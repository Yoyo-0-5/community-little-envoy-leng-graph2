import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

TAVILY_API_KEY = os.getenv("TAVILY_API_KEY", "")
GOOGLE_MAPS_API_KEY = os.getenv("GOOGLE_MAPS_API_KEY", "")
DATABASE_URL = os.getenv("DATABASE_URL", "")
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "qwen2.5:7b")

GCS_BUCKET_NAME = os.getenv("GCS_BUCKET_NAME", "")
# gcs | local（gcs 優先，失敗自動退 local，方便沒有 GCS 憑證時測試）
STORAGE_BACKEND = os.getenv("STORAGE_BACKEND", "gcs")
LOCAL_UPLOAD_DIR = Path(
    os.getenv("LOCAL_UPLOAD_DIR", str(Path(__file__).resolve().parent.parent / "data" / "uploads"))
)