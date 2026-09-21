import json
import random
import time

from sqlalchemy import text

from app.db import engine
from app.embedding import embed_text

_CASE_COLUMNS = (
    "cases_id, description, category_id, images, status, created_at, "
    "author_account, address, reject_reason"
)


def create_case(
    description: str,
    category_id: int,
    images: list[str] | None,
    address: str | None,
    author_account: str,
) -> dict:
    """依 cases 表欄位建立新案件，embedding 由後端產生，前端不送。"""
    created_at = int(time.time() * 1000)
    # 與 13 位數毫秒時間戳同級的流水號 (例: 1717000000011)
    cases_id = int(created_at * 10 + random.randint(0, 9))
    embedding = embed_text(description)
    vector_literal = "[" + ",".join(repr(v) for v in embedding) + "]"

    with engine.begin() as connection:
        row = connection.execute(
            text(
                "INSERT INTO cases "
                "(cases_id, description, embedding, category_id, images, "
                " status, created_at, author_account, address) "
                "VALUES "
                "(:cases_id, :description, CAST(:embedding AS vector), :category_id, "
                " CAST(:images AS jsonb), 'pending', :created_at, :author_account, :address) "
                "RETURNING " + _CASE_COLUMNS
            ),
            {
                "cases_id": cases_id,
                "description": description,
                "embedding": vector_literal,
                "category_id": category_id,
                "images": json.dumps(images, ensure_ascii=False) if images else None,
                "created_at": created_at,
                "author_account": author_account,
                "address": address or None,
            },
        ).mappings().first()
    return dict(row)


def list_cases_by_account(author_account: str) -> list[dict]:
    with engine.connect() as connection:
        rows = connection.execute(
            text(
                f"SELECT {_CASE_COLUMNS} FROM cases "
                "WHERE author_account = :account "
                "ORDER BY created_at DESC LIMIT 50"
            ),
            {"account": author_account},
        ).mappings().all()
    return [dict(r) for r in rows]