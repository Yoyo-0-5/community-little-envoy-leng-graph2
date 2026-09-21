from langchain_ollama import OllamaEmbeddings

from app.config import OLLAMA_BASE_URL

_embeddings = OllamaEmbeddings(model="bge-m3", base_url=OLLAMA_BASE_URL)


def embed_text(text: str) -> list[float]:
    """用 bge-m3 把描述文字轉成 1024 維向量，寫進 cases.embedding。"""
    return _embeddings.embed_query(text)