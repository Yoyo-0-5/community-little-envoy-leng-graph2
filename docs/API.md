# 社區小尖兵 — 網頁 API 文件

本文件列出 `test/report-case.html`（社區通報網頁）實際「使用到」的所有 API。
後端為 FastAPI，入口在 `app/server.py`。

## 共通資訊

| 項目 | 值 |
|------|-----|
| Base URL | `http://<server-ip>:8000`（網頁與 API 同源，前端用相對路徑 `/api`） |
| 格式 | request body 一律 `application/json`（上傳除外），回傳 `application/json` |
| 錯誤格式 | 統一為 `{ "detail": "<錯誤訊息>" }`，配合 HTTP 狀態碼 |
| CORS | 已開啟（`allow_origins=["*"]`），跨網域也可呼叫 |
| 啟動 | `python server.py`（背景服務，`/tmp/server.log`） |

## 端點總覽

| Method | Path | 說明 | 網頁來源 |
|--------|------|------|----------|
| GET | `/api/categories` | 讀取通報類別字典表 | 類別選單 |
| POST | `/api/ai-analyze` | AI 分析照片，回傳類別＋描述 | 「用 AI 分析並帶入表單」按鈕 |
| POST | `/api/uploads` | 上傳照片（multipart） | 照片選取後送出 |
| POST | `/api/cases` | 建立通報案件 | 表單送出 |
| GET | `/api/cases?author_account=` | 查詢某帳號的案件 | 「我的通報」清單 |
| POST | `/api/chat` | 社區小尖兵 AI 聊天（Supervisor 多智能體，與 `main.py` 同一個 Agent） | — |
| POST | `/api/chat/stream` | 同上，但以 SSE 逐步送進度事件 | 右下角浮動對話框 |

另有 `POST /api/login` 已在網頁原始碼預留語法，但**尚未接上表單、後端也未實作**（暫以 mock 回傳）。

---

## 1. GET /api/categories

讀取申報類別字典表（對應資料庫 `categories` 表）。

- **網頁對應**：`apiGetCategories()`（`report-case.html` 🔌 1）
- **後端**：`app/server.py:70` → `app/analyze.py:list_categories()`

**回傳 200**：類別陣列

```json
[
  { "categories_id": 1, "key_name": "road_damage",     "label": "路面損壞",     "color_code": "#FFA500" },
  { "categories_id": 2, "key_name": "street_light",    "label": "路燈故障",     "color_code": "#F59E0B" },
  { "categories_id": 3, "key_name": "waterlogging",    "label": "積水",         "color_code": "#3B82F6" },
  { "categories_id": 4, "key_name": "garbage",         "label": "垃圾",         "color_code": "#10B981" },
  { "categories_id": 5, "key_name": "illegal_dump",    "label": "違規堆放",     "color_code": "#8B5CF6" },
  { "categories_id": 6, "key_name": "facility_damage", "label": "公共設施損壞", "color_code": "#EC4899" },
  { "categories_id": 7, "key_name": "fallen_tree",     "label": "樹木倒塌",     "color_code": "#84CC16" },
  { "categories_id": 8, "key_name": "other",           "label": "其他",         "color_code": "#6B7280" }
]
```

| 欄位 | 型別 | 說明 |
|------|------|------|
| `categories_id` | int | 類別主鍵（寫案件時用這個） |
| `key_name` | string | 類別英文代號（AI 分析回傳這種） |
| `label` | string | 中文標籤 |
| `color_code` | string | 前端顯示用的顏色 |

**錯誤**：`500 { "detail": "資料庫查詢失敗: ..." }`

```bash
curl http://localhost:8000/api/categories
```

---

## 2. POST /api/ai-analyze

把照片交給後端 Ollama 視覺模型，自動選類別＋寫描述。

- **網頁對應**：`apiAnalyze()`（🔌 1b）、「用 AI 分析並帶入表單」
- **後端**：`app/server.py:78` → `app/analyze.py:analyze_images()`

**Request**（JSON）

```json
{
  "images": [
    "data:image/jpeg;base64,/9j/4AAQSkZJRg=="
  ]
}
```

| 欄位 | 型別 | 說明 |
|------|------|------|
| `images` | string[] | base64 的 data URI，至少 1 張 |

**回傳 200**：

```json
{
  "category_id": 2,
  "key_name": "street_light",
  "label": "路燈故障",
  "description": "夜間路燈燈頭未亮，疑似路燈故障"
}
```

| 欄位 | 型別 | 說明 |
|------|------|------|
| `category_id` | int | 對應 `categories.categories_id` |
| `key_name` | string | 對應 `categories.key_name` |
| `label` | string | 中文類別名 |
| `description` | string | 一句話具體描述 |

> 例外：若 AI 無法解析回覆，會回 **200** 但內容是 `{ "error": "AI 無法解析回覆，請再試一次。", "raw": "..." }`。

**錯誤**：
- `400 { "detail": "至少需要一張照片，AI 才能分析。" }`
- `500 { "detail": "AI 分析失敗: ..." }`

```bash
curl -X POST http://localhost:8000/api/ai-analyze \
  -H 'Content-Type: application/json' \
  -d '{"images": ["data:image/png;base64,AAAA"]}'
```

---

## 3. POST /api/uploads

上傳照片，回傳物件路徑陣列（檔名由後端決定）。

- **網頁對應**：`apiUploadImages()`（🔌 2）
- **後端**：`app/server.py:111` → `app/storage.py:save_upload()`
- **儲存**：`.env` 的 `STORAGE_BACKEND` — `gcs` 存到 GCS bucket `my-report-image`；`local` 暫存本機 `data/uploads/`。GCS 失敗會自動退回本機。

**Request**（`multipart/form-data`，欄位名 `files`，可重複多個檔案）

```
files=@20260921路燈.jpg;type=image/jpeg
```

**回傳 200**：

```json
{
  "paths": [
    "uploads/202609/4a16b777b5494731b017495e3f6b0288.png"
  ]
}
```

`paths` 為物件路徑陣列，會原封不動寫進案件的 `images` 欄位。

**錯誤**：
- `400 { "detail": "沒有收到照片。" }`
- `500 { "detail": "圖片上傳失敗: ..." }`（例如 GCS 連線失敗又無法退本機）

```bash
curl -X POST http://localhost:8000/api/uploads -F "files=@photo.jpg"
```

---

## 4. POST /api/cases

建立通報案件（`cases_id`、`created_at`、`status`、`embedding` 都由後端產生）。

- **網頁對應**：`apiCreateCase()`（🔌 3）、「送出通報」
- **後端**：`app/server.py:126` → `app/cases.py:create_case()`

**Request**（JSON）

```json
{
  "description": "夜間路燈燈頭未亮，疑似路燈故障",
  "category_id": 2,
  "images": ["uploads/202609/f3af886b21de4cffb36df6b44e3dba65.jpg"],
  "address": "23.57454, 119.58092",
  "author_account": "test1"
}
```

| 欄位 | 型別 | 必填 | 說明 |
|------|------|------|------|
| `description` | string | ✅ | 問題描述，至少 10 個字 |
| `category_id` | int | ✅ | 見 `/api/categories`，需 > 0 |
| `images` | string[] | 否 | `/api/uploads` 回傳的物件路徑，最多 5 張 |
| `address` | string | 否 | 地址或坐標（`23.57454, 119.58092`） |
| `author_account` | string | ✅ | 發布者帳號（英文＋數字） |

**回傳 200**（完整案件，`created_at` 為 13 位毫秒）：

```json
{
  "cases_id": 17899204544044,
  "description": "夜間路燈燈頭未亮，疑似路燈故障",
  "category_id": 2,
  "images": ["uploads/202609/f3af886b21de4cffb36df6b44e3dba65.jpg"],
  "status": "pending",
  "created_at": 1789920454404,
  "author_account": "test1",
  "address": "23.57454, 119.58092",
  "reject_reason": null
}
```

> 注意：`created_at` 換算日期要用 `created_at / 1000.0`（毫秒），不是直接當秒。

**錯誤**（`400`）：
- `帳號不能空白。`
- `描述至少要 10 個字。`
- `請選擇有效類別。`
- `最多 5 張照片。`
- `建立案件失敗: ...`（例如 FK 不存在、資料庫錯誤）

```bash
curl -X POST http://localhost:8000/api/cases \
  -H 'Content-Type: application/json' \
  -d '{"description":"巷口路燈已三天沒亮","category_id":2,"images":[],"address":"中正路 12 巷","author_account":"test1"}'
```

---

## 5. GET /api/cases

查詢某帳號的通報案件（`author_account` 必填）。

- **網頁對應**：`apiGetMyCases()`（🔌 4）、「我的通報」
- **後端**：`app/server.py:148` → `app/cases.py:list_cases_by_account()`
- **SQL**：`WHERE author_account = :account ORDER BY created_at DESC LIMIT 50`，不 SELECT embedding 欄位

**Query 參數**

| 名稱 | 必填 | 說明 |
|------|------|------|
| `author_account` | ✅ | 帳號，如 `test1` |

**回傳 200**：案件陣列（每筆結構同「建立案件」的回傳）

```json
[
  {
    "cases_id": 17899204544044,
    "description": "夜間路燈燈頭未亮，疑似路燈故障",
    "category_id": 2,
    "images": ["uploads/202609/f3af886b21de4cffb36df6b44e3dba65.jpg"],
    "status": "pending",
    "created_at": 1789920454404,
    "author_account": "test1",
    "address": "23.57454, 119.58092",
    "reject_reason": null
  }
]
```

**錯誤**：`500 { "detail": "案件查詢失敗: ..." }`

```bash
curl 'http://localhost:8000/api/cases?author_account=test1'
```

---

## 6. POST /api/chat

社區小尖兵 AI 聊天。就是 `main.py` 的那個 LangGraph Agent，搬到網頁上。同一個 `thread_id` 的多輪問題會串接對話記憶（存在伺服器程序中）。

架構是 **Supervisor 多智能體**（`app/agent/graph.py`）：`supervisor` 節點不動手查資料，只決定要喚醒哪位專家；專家做完回報，`supervisor` 再決定下一步，全部完成才 `FINISH` 結束。

| 節點 | 工具 | 負責 |
|------|------|------|
| `supervisor` | 無 | 只做路由，輸出 `plan`（本輪需要哪些專家）與 `next`（下一位） |
| `DB_Expert` | `query_community_database` | 寫 SQL 查 `cases` / `users` / `categories` |
| `Researcher` | `tavily_tool` | Tavily 聯網搜尋外部即時資訊 |

路由規則：派工清單由程式端排隊（`plan` 內還沒回報的專家依序執行），單輪最多派 4 次防迴圈；一輪最多回報 2 位專家。
回覆內容是本輪所有專家回報的合併（多位專家以 `---` 分隔）。
`/api/ai-analyze` 的照片辨識走另一條路徑 `build_vision_agent()`（單一 react agent，會看到圖片），不經過 supervisor。

- **網頁對應**：右下角浮動「AI」按鈕 → `sendChat()`
- **後端**：`app/server.py:119`（網頁實際走的是 `POST /api/chat/stream`，見第 7 節）

**Request**（JSON）

```json
{
  "message": "幫我查一下 test1 這個帳號目前有哪些通報案件？",
  "thread_id": "web_1789920981_a1b2c3"
}
```

| 欄位 | 型別 | 必填 | 說明 |
|------|------|------|------|
| `message` | string | ✅ | 要問的問題（前後空白會被去除） |
| `thread_id` | string | 否 | 對話 id；相同就共用記憶，預設 `web_session_01` |

**回傳 200**：

```json
{
  "reply": "查到了！`test1` 帳號目前共有 3 件通報案件…"
}
```

**錯誤**：
- `400 { "detail": "請輸入訊息。" }`
- `500 { "detail": "AI 回覆失敗: ..." }`（Ollama/工具呼叫錯誤）
- `500 { "detail": "AI 沒有回覆內容，請再試一次。" }`

```bash
curl -X POST http://localhost:8000/api/chat \
  -H 'Content-Type: application/json' \
  -d '{"message":"路燈壞了要怎麼通報？","thread_id":"my_thread"}'
```

> 首次呼叫需載入 Ollama 模型，可能等幾秒到十幾秒。多輪對話務必沿用同一個 `thread_id`。

---

## 7. POST /api/chat/stream

跟 `/api/chat` 是同一個 Agent、同一組參數，差別在回應格式：**Server-Sent Events**，先送進度事件，再逐字送專家正文，最後補一次完整回覆。

Supervisor 一次問題會呼叫模型多次（路由 → 專家 → 路由），整段跑完常要 20 秒以上。若等到最後才回應，畫面就是一個「思考中」停住；只送節點進度也不夠，因為最慢的一段（生答案）完全包在單一節點裡面，所以正文也一起串流。

- **網頁對應**：`sendChat()` → `parseChatFrame()` / `chatHttpError()`
- **後端**：`app/server.py:140`

**Request**（JSON）：同 `/api/chat`

**回應**：`text/event-stream`，每個事件形如 `event: <名稱>\ndata: <JSON>\n\n`

| event | data | 觸發時機 |
|-------|------|----------|
| `status` | `{"text": "正在思考…"}` | 請求剛進來 |
| `status` | `{"text": "正在查資料庫…"}` / `"正在搜尋外部資料…"` | supervisor 決定派給哪位專家（依 `next` 欄位判斷） |
| `status` | 同上 | 專家決定要去呼叫工具（ReAct 決策期沒有任何可顯示的字） |
| `delta` | `{"expert": "Researcher", "text": "以下是路"}` | 專家正文逐字，每幀約 0.08 秒／10～15 字 |
| `status` | `{"text": "正在整理回覆…"}` | 專家回報完成，supervisor 收尾中 |
| `reset` | `{"expert": "DB_Expert"}` | 該專家第一輪回空字、準備重試，叫前端清掉已送出的半截 |
| `done` | `{"reply": "…markdown…"}` | 全部完成，回覆已合併（多位專家以 `---` 分隔） |
| `error` | `{"message": "AI 回覆失敗: …"}` | 節點或工具例外 |

`reply` 是權威答案，前端在 `done` 時用它覆蓋串流中已顯示的內容，因此中途過濾或重送都不會殘留錯字。

空白 `message` 仍在串流開始前就回 `400 { "detail": "請輸入訊息。" }`（非 SSE）。

回應頭另帶 `Cache-Control: no-cache` 與 `X-Accel-Buffering: no`：nginx 預設會緩衝 SSE，沒有後者會讓事件又變回一次整段送出。

```bash
curl -N -X POST http://localhost:8000/api/chat/stream \
  -H 'Content-Type: application/json' \
  -H 'Accept: text/event-stream' \
  -d '{"message":"路燈壞了要怎麼通報？","thread_id":"my_thread"}'
```

前端因是 POST，不能用 `EventSource`，改用 `fetch` + `response.body.getReader()` 讀 `ReadableStream`，再以空行切 frame（frame 可能被切成兩次 chunk 讀到，要保留殘段）。收到 `delta` 就 append 文字，用 `requestAnimationFrame` 節流後重跑 `marked.parse`（每個字重解析一次會卡）。

### 為什麼要改專家節點

`app/agent/graph.py` 的專家節點原本呼叫 `agent.invoke()`。**同步 `invoke()` 不會向 Ollama 要求串流**，token 只會在整段跑完後一次湧出（實測 5 個 chunk 同時抵達）；改成 `agent.stream(..., stream_mode=["messages", "values"])` 才會逐字送出。`values` 用來取得 sub-graph 的最終 state，寫進記憶體的仍是權威訊息，串流只供前端顯示。

過濾規則：sub-graph 內部只有 `agent`（模型）與 `tools`（工具）兩種節點，只送 `agent` 的文字，工具的 JSON 回傳與 ReAct 決策期不顯示。

沒有串流監聽時（`main.py`、`/api/chat`）writer 會丟棄事件，行為與改動前相同。

### 已知延遲

模型是遠端 Ollama 的 `qwen3.8-16k`（約 17GB VRAM），實測一次「路燈壞了要怎麼通報？」：

| 階段 | 耗時 |
|------|------|
| supervisor 路由 | 2.9s |
| 專家決定要搜尋 | 2.6s |
| 首次關鍵詞延遲 | 7.2s |
| 輸出 826 字（打字機） | 8.3s |

首字前無法再壓低（模型要先 decide 要不要搜、prefill 長 prompt）；首字之後幾乎沒有停頓。要縮短首字時間只能換小模型，見 `OLLAMA_MODEL`。

---


## 附錄 A：頁面自己受用的靜態路徑

| Path | 說明 |
|------|------|
| `GET /` | 302 轉到 `/test/report-case.html` |
| `GET /test/*` | 網頁本體（`test/report-case.html`）與靜態檔 |
| `GET /uploads/{object_path}` | `STORAGE_BACKEND=local` 時，回傳上傳的圖片本體 |
| `https://storage.googleapis.com/my-report-image/{object_path}` | `STORAGE_BACKEND=gcs` 時的圖片公開網址 |

## 附錄 B：網頁已定義但未接上的 API

| Method | Path | 狀態 |
|--------|------|------|
| `POST` | `/api/login` | 網頁 JS 有 `apiLogin()` 語法，未接到表單；後端**未實作**（資料表 `users` 有，要做再加） |

---

*產生時間：2026-09-21。以 `app/server.py` 現況為準，改後端要同步更新這裡。*