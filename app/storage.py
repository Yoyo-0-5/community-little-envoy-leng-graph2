import logging
import uuid
from datetime import datetime
from pathlib import Path

from app.config import GCS_BUCKET_NAME, LOCAL_UPLOAD_DIR, STORAGE_BACKEND

logger = logging.getLogger(__name__)


def _object_name(filename: str, extension: str = "") -> str:
    ext = Path(filename or "").suffix or extension
    return f"uploads/{datetime.now().strftime('%Y%m')}/{uuid.uuid4().hex}{ext}"


def _save_local(object_path: str, data: bytes) -> None:
    dest = LOCAL_UPLOAD_DIR / object_path
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(data)


def _gcs_blob(object_path: str):
    from google.cloud import storage

    client = storage.Client()
    return client.bucket(GCS_BUCKET_NAME).blob(object_path)


def save_upload(data: bytes, filename: str, content_type: str | None) -> str:
    """儲存一張圖片，回傳物件路徑（例: uploads/202609/xxxx.jpg）。

    backend=gcs 時，若 GCS 驗證／上傳失敗（例如 service account 金鑰
    失效），會自動退回本機儲存，避免上傳整個失敗；哪天 GCS 正常後
    優先走 GCS。
    """
    object_path = _object_name(filename)

    if STORAGE_BACKEND == "local":
        _save_local(object_path, data)
        return object_path

    try:
        blob = _gcs_blob(object_path)
        blob.upload_from_string(data, content_type=content_type or "application/octet-stream")
        return object_path
    except Exception as e:
        logger.warning("GCS 上傳失敗，退回本機儲存 (%s): %s", object_path, e)
        _save_local(object_path, data)
        return object_path


def object_url(object_path: str) -> str:
    """把物件路徑組回可存取的網址（GCS 公開網址或本機靜態網址）。"""
    if STORAGE_BACKEND == "local":
        return f"/uploads/{object_path}"
    return f"https://storage.googleapis.com/{GCS_BUCKET_NAME}/{object_path}"