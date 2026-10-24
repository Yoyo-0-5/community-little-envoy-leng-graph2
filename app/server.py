import json
import time
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from sqlalchemy.exc import SQLAlchemyError

from app.agent.state import DB_EXPERT, MEMBERS, RESEARCHER
from app.analyze import analyze_images, list_categories
from app.agent.graph import build_agent
from app.cases import create_case, list_cases_by_account
from app.config import LOCAL_UPLOAD_DIR, STORAGE_BACKEND
from app.events import get_event, list_events
from app.image_location import ImageMetadataError
from app.places import PlacesProviderError, reverse_geocode, search_places
from app.storage import save_upload
from app.welfare import WelfareSearchError, search_welfare
from app.weather import WeatherProviderError, get_weather

BASE_DIR = Path(__file__).resolve().parent.parent
TEST_DIR = BASE_DIR / "test"
# 整合後的 Vue 前端（monorepo 結構：backend/ 的上一層是 frontend/）。
FRONTEND_DIST = BASE_DIR.parent / "frontend" / "dist"

app = FastAPI(title="社區小尖兵 AI Agent")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


class AnalyzeRequest(BaseModel):
    images: list[str] = []


class CreateCaseRequest(BaseModel):
    description: str
    category_id: int
    images: list[str] | None = None
    address: str | None = None
    author_account: str


class ChatRequest(BaseModel):
    message: str
    thread_id: str | None = None


_chat_agent = None


def _get_chat_agent():
    """共用同一個 Agent（MemorySaver 保留在程序中），聊天才有跨輪記憶。"""
    global _chat_agent
    if _chat_agent is None:
        _chat_agent = build_agent()
    return _chat_agent


def _last_ai_text(messages) -> str:
    for m in reversed(messages):
        if getattr(m, "type", "") != "ai":
            continue
        content = m.content
        if isinstance(content, list):
            return "".join(
                b.get("text", "") for b in content
                if isinstance(b, dict) and b.get("type") == "text"
            ).strip()
        return str(content or "").strip()
    return ""


def _turn_reply(messages) -> str:
    """本輪 supervisor 可能派了多位專家，把他們的回報依序合併成一段回覆。"""
    reports: list[str] = []
    for m in reversed(messages):
        if getattr(m, "type", "") == "human":
            break
        if getattr(m, "type", "") == "ai" and getattr(m, "name", None):
            text = str(m.content or "").strip()
            if text:
                reports.append(text)
    reports.reverse()
    if not reports:
        return _last_ai_text(messages)
    return "\n\n---\n\n".join(reports)


# 專家節點開始跑之前就先把進度講出來，不用等整段回覆生成完。
_EXPERT_STATUS = {
    DB_EXPERT: "正在查資料庫…",
    RESEARCHER: "正在搜尋外部資料…",
}
# 串流幀的最短間隔：一個中文字一個 SSE 事件太碎，前端重繪也划不來。
_STREAM_FLUSH_SECONDS = 0.08


def _sse(event: str, **data) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


@app.get("/api/categories")
def get_categories():
    try:
        return list_categories()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"資料庫查詢失敗: {e}")


@app.post("/api/ai-analyze")
def ai_analyze(req: AnalyzeRequest):
    if not req.images:
        raise HTTPException(status_code=400, detail="至少需要一張照片，AI 才能分析。")
    try:
        return analyze_images(req.images)
    except ImageMetadataError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"AI 分析失敗: {e}")


@app.post("/api/chat")
def chat(req: ChatRequest):
    """跟 main.py 同一個 LangGraph Agent 對話，thread_id 相同就能多輪記憶。"""
    message = req.message.strip()
    if not message:
        raise HTTPException(status_code=400, detail="請輸入訊息。")
    try:
        result = _get_chat_agent().invoke(
            {"messages": [("user", message)]},
            config={"configurable": {"thread_id": req.thread_id or "web_session_01"}},
        )
        reply = _turn_reply(result["messages"])
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"AI 回覆失敗: {e}")
    if not reply:
        raise HTTPException(status_code=500, detail="AI 沒有回覆內容，請再試一次。")
    return {"reply": reply}


@app.post("/api/chat/stream")
def chat_stream(req: ChatRequest):
    """跟 /api/chat 同一個 Agent，但用 SSE 送「進度 + 正文 token」。

    Supervisor 一次問題會呼叫模型多次（路由 → 專家 → 路由），整段跑完要十幾秒；
    光有進度事件不夠，因為最慢的一段（生答案的十幾秒）完全包在一個節點裡面。
    這裡同時送兩種事件：status（節點邊界）與 delta（專家正文逐字），
    token 由 app/agent/graph.py 的專家節點用 stream() 取，再用自訂事件往外轉。
    """
    message = req.message.strip()
    if not message:
        raise HTTPException(status_code=400, detail="請輸入訊息。")

    agent = _get_chat_agent()
    config = {"configurable": {"thread_id": req.thread_id or "web_session_01"}}

    def events():
        yield _sse("status", text="正在思考…")
        # token 一個中文字一個事件太碎，累積到一定長度或一定時間再送一幀。
        pending, last_flush = "", time.monotonic()
        try:
            for mode, item in agent.stream(
                {"messages": [("user", message)]},
                config=config,
                stream_mode=["updates", "custom"],
            ):
                now = time.monotonic()
                if mode == "custom":
                    if item.get("status"):
                        if pending:                      # 順序不能顛倒，先把已累積的字送出去
                            yield _sse("delta", expert="", text=pending)
                            pending, last_flush = "", now
                        yield _sse("status", text=item["status"])
                    elif item.get("reset"):
                        yield _sse("reset", expert=item.get("expert", ""))
                    elif item.get("text"):
                        pending += item["text"]
                    if not pending or now - last_flush < _STREAM_FLUSH_SECONDS:
                        continue
                    yield _sse("delta", expert=item.get("expert", ""), text=pending)
                    pending, last_flush = "", now
                    continue
                for node, patch in (item or {}).items():
                    if node == "supervisor":
                        upcoming = (patch or {}).get("next")
                        if upcoming in _EXPERT_STATUS:
                            yield _sse("status", text=_EXPERT_STATUS[upcoming])
                    elif node in MEMBERS:
                        yield _sse("status", text="正在整理回覆…")
            if pending:
                yield _sse("delta", expert="", text=pending)
            messages = agent.get_state(config).values.get("messages", [])
            reply = _turn_reply(messages)
        except Exception as e:
            if pending:
                yield _sse("delta", expert="", text=pending)
            yield _sse("error", message=f"AI 回覆失敗: {e}")
            return
        if not reply:
            yield _sse("error", message="AI 沒有回覆內容，請再試一次。")
            return
        yield _sse("done", reply=reply)

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        # X-Accel-Buffering: nginx 預設會緩衝 SSE，沒這行就又變成一次整段送出。
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.post("/api/uploads")
async def upload_images(files: list[UploadFile] = File(...)):
    """存到 GCS（或本機模擬），回傳物件路徑陣列。檔名由後端決定。"""
    if not files:
        raise HTTPException(status_code=400, detail="沒有收到照片。")
    paths = []
    try:
        for f in files:
            data = await f.read()
            paths.append(save_upload(data, f.filename or "", f.content_type))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"圖片上傳失敗: {e}")
    return {"paths": paths}


@app.post("/api/cases")
def create_case_endpoint(req: CreateCaseRequest):
    if not req.author_account.strip():
        raise HTTPException(status_code=400, detail="帳號不能空白。")
    if not req.description.strip() or len(req.description.strip()) < 10:
        raise HTTPException(status_code=400, detail="描述至少要 10 個字。")
    if req.category_id <= 0:
        raise HTTPException(status_code=400, detail="請選擇有效類別。")
    if req.images and len(req.images) > 5:
        raise HTTPException(status_code=400, detail="最多 5 張照片。")
    try:
        return create_case(
            description=req.description.strip(),
            category_id=req.category_id,
            images=req.images,
            address=req.address.strip() if req.address else None,
            author_account=req.author_account.strip(),
        )
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"建立案件失敗: {e}")


@app.get("/api/cases")
def get_my_cases(author_account: str):
    try:
        return list_cases_by_account(author_account)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"案件查詢失敗: {e}")


@app.get("/api/events")
def get_events(
    time: str = Query(default="month", pattern="^(today|week|month|all)$"),
    event_type: str | None = Query(default=None, alias="type"),
    status: str | None = None,
    region: str | None = None,
    filter_region: str | None = Query(default=None, alias="filterRegion"),
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
):
    """列出社區通報事件，回傳前端 Event 清單格式。"""
    try:
        return list_events(
            time_filter=time,
            event_type=event_type,
            status=status,
            region=region,
            filter_region=filter_region,
            limit=limit,
            offset=offset,
        )
    except SQLAlchemyError as e:
        raise HTTPException(status_code=500, detail=f"事件查詢失敗: {e}")


@app.get("/api/places")
def get_places(q: str = Query(default="")):
    """以文字搜尋台灣地點，供申報位置挑選使用。"""
    if len(q) > 200:
        raise HTTPException(status_code=422, detail="地點搜尋文字不可超過 200 個字。")
    try:
        return search_places(q)
    except PlacesProviderError as e:
        raise HTTPException(status_code=e.status_code, detail=str(e))


@app.get("/api/places/reverse")
def get_place_address(
    lat: float = Query(ge=-90, le=90),
    lng: float = Query(ge=-180, le=180),
):
    """將地圖座標轉成地址文字。"""
    try:
        return {"address": reverse_geocode(lat, lng)}
    except PlacesProviderError as e:
        raise HTTPException(status_code=e.status_code, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))


@app.get("/api/events/{event_id}")
def get_event_detail(event_id: str):
    """取得一筆社區事件；未知或已拒絕案件回傳 null。"""
    try:
        return get_event(event_id)
    except SQLAlchemyError as e:
        raise HTTPException(status_code=500, detail=f"事件查詢失敗: {e}")


@app.get("/api/welfare")
def get_welfare(
    event_type: str | None = Query(default=None, alias="type"),
    target: str | None = None,
    time: str | None = Query(default=None, pattern="^(today|week|month|all)$"),
    region: str | None = None,
):
    """搜尋中央／地方政府及社區發布的福利與活動資訊。"""
    try:
        return search_welfare(
            event_type=event_type,
            target=target,
            time=time,
            region=region,
        )
    except WelfareSearchError as e:
        raise HTTPException(status_code=e.status_code, detail=str(e))


@app.get("/api/weather")
def get_current_weather(
    lat: float = Query(ge=-90, le=90),
    lng: float = Query(ge=-180, le=180),
):
    """依瀏覽器提供的 GPS 座標查詢 Open-Meteo 目前天氣。"""
    try:
        return get_weather(lat, lng)
    except WeatherProviderError as e:
        raise HTTPException(status_code=502, detail=str(e))


LOCAL_UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/uploads", StaticFiles(directory=LOCAL_UPLOAD_DIR), name="uploads")

app.mount("/test", StaticFiles(directory=TEST_DIR), name="test")


@app.get("/")
def index():
    """整合後根路徑直接服務 Vue 前端；測試頁仍保留在 /test/。"""
    index_file = FRONTEND_DIST / "index.html"
    if index_file.is_file():
        return FileResponse(index_file)
    return RedirectResponse(url="/test/report-case.html")


@app.get("/{full_path:path}", include_in_schema=False)
def spa_fallback(full_path: str):
    """Vue history 路由 fallback：認得的靜態檔直接回，其餘回 index.html。

    註冊在所有 route / mount 之後（/api、/uploads、/test），才不會蓋掉後端路由。
    frontend/dist 未建置時回 404，後端仍可單獨啟動。
    """
    if full_path == "api" or full_path.startswith("api/"):
        return JSONResponse({"detail": "API endpoint not found"}, status_code=404)
    if FRONTEND_DIST.is_dir():
        candidate = FRONTEND_DIST / full_path
        if candidate.is_file():
            return FileResponse(candidate)
        index_file = FRONTEND_DIST / "index.html"
        if index_file.is_file():
            return FileResponse(index_file)
    return JSONResponse({"detail": "前端尚未建置：請先 cd frontend && npm run build"}, status_code=404)