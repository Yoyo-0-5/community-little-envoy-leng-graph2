from sqlalchemy import create_engine

from app.config import DATABASE_URL


def _normalize_url(url: str) -> str:
    """URL 以 postgresql:// 開頭時，明確指定 psycopg3 驅動。"""
    if url.startswith("postgresql://"):
        return url.replace("postgresql://", "postgresql+psycopg://", 1)
    return url


engine = create_engine(_normalize_url(DATABASE_URL), pool_pre_ping=True)