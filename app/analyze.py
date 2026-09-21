import json
import re
import uuid

from sqlalchemy import text

from app.agent.graph import build_agent
from app.db import engine


def list_categories() -> list[dict]:
    with engine.connect() as connection:
        rows = connection.execute(
            text(
                "SELECT categories_id, key_name, label, color_code "
                "FROM categories ORDER BY categories_id"
            )
        ).fetchall()
    return [dict(row._mapping) for row in rows]


def _extract_json(text_content: str) -> dict | None:
    match = re.search(r"\{.*\}", text_content, re.DOTALL)
    if not match:
        return None
    try:
        return json.loads(match.group(0))
    except json.JSONDecodeError:
        return None


def analyze_images(images: list[str]) -> dict:
    """用 LangGraph Agent（含 DB 工具）分析照片，回傳類別與描述。"""
    categories = list_categories()
    cat_hint = ", ".join(
        f"{c['key_name']}({c['label']})" for c in categories
    )

    content = [
        {
            "type": "text",
            "text": (
                "你會看到社區居民拍的問題照片，請把它整理成通報表單欄位。\n"
                f"類別清單（key_name 與中文標籤）：{cat_hint}\n"
                '請回應且只回應一個 JSON 物件，格式為：'
                '{"category":"<key_name>","description":"一句話具體描述照片中的狀況"}。\n'
                '不要輸出任何 JSON 以外的文字，不要 markdown。'
                'category 必須是類別清單中的其中一個 key_name，若無法判斷就選 "other"。'
            ),
        },
        *[
            {"type": "image_url", "image_url": f"data:image/png;base64,{image}"}
            if not image.startswith("data:")
            else {"type": "image_url", "image_url": image}
            for image in images
        ],
    ]

    app = build_agent()
    config = {"configurable": {"thread_id": f"analyze_{uuid.uuid4().hex}"}}
    result = app.invoke({"messages": [{"role": "user", "content": content}]}, config=config)

    raw = result["messages"][-1].content or ""
    parsed = _extract_json(str(raw))
    if not parsed:
        return {
            "error": "AI 無法解析回覆，請再試一次。",
            "raw": str(raw)[:500],
        }

    key_name = str(parsed.get("category", "")).strip().lower()
    description = str(parsed.get("description", "")).strip()
    category = next((c for c in categories if c["key_name"].lower() == key_name), None)
    if category is None:
        category = next(c for c in categories if c["key_name"] == "other")

    return {
        "category_id": category["categories_id"],
        "key_name": category["key_name"],
        "label": category["label"],
        "description": description,
    }