from langchain_core.tools import tool
from sqlalchemy import text

from app.db import engine

_MAX_ROWS = 50
_MAX_TEXT_LEN = 12000


@tool
def query_community_database(sql_query: str) -> str:
    """
    查詢 community_envoys 資料庫的工具。
    資料庫包含 cases (案件紀錄) 與 users (使用者) 等資料表。
    請傳入標準的 PostgreSQL SQL 語法進行查詢。
    傳回結果中不會包含 embedding 欄位（1024 維向量對回答沒幫助）。
    """
    try:
        with engine.connect() as connection:
            result = connection.execute(text(sql_query))
            rows = [dict(row._mapping) for row in result.fetchall()]

        # 有 embedding 欄位時，grep rules: 對 LLM 無意義且會撐爆 context，直接拿掉
        for row in rows:
            row.pop("embedding", None)

        if not rows:
            return "查詢成功，但沒有找到符合的資料。"

        lines = [str(row) for row in rows]
        text_out = "\n".join(lines)

        over = len(rows) - _MAX_ROWS
        if over > 0:
            text_out = "\n".join(lines[: _MAX_ROWS])
            text_out += f"\n……（其餘 {over} 筆省略）"
        if len(text_out) > _MAX_TEXT_LEN:
            text_out = text_out[: _MAX_TEXT_LEN] + "\n……（結果過長，已截斷）"
        return text_out
    except Exception as e:
        return f"資料庫查詢發生錯誤: {str(e)}"