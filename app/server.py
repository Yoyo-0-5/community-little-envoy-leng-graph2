import json
import time
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from app.agent.state import DB_EXPERT, MEMBERS, RESEARCHER
from app.analyze import analyze_images, list_categories
from app.agent.graph import build_agent
from app.cases import create_case, list_cases_by_account
from app.config import LOCAL_UPLOAD_DIR, STORAGE_BACKEND
from app.storage import save_upload

BASE_DIR = Path(__file__).resolve().parent.parent
TEST_DIR = BASE_DIR / "test"

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


LOCAL_UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/uploads", StaticFiles(directory=LOCAL_UPLOAD_DIR), name="uploads")

app.mount("/test", StaticFiles(directory=TEST_DIR), name="test")


@app.get("/")
def index():
    return RedirectResponse(url="/test/report-case.html")