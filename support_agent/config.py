from dataclasses import dataclass
from pathlib import Path
import os


@dataclass(frozen=True)
class Settings:
    db_path: Path = Path(os.environ.get("RAG_DB", "data/support.sqlite3"))
    ollama_url: str = "http://127.0.0.1:11434"
    embed_model: str = "nomic-embed-text:latest"
    chat_model: str = "qwen3:4b"
    max_file_bytes: int = 64_000
    max_documents: int = 50
    max_chunks: int = 500
    max_db_bytes: int = 64_000_000
    chunk_chars: int = 800
    max_context_chars: int = 4_800
    top_k: int = 6
    min_similarity: float = 0.40
    timeout_seconds: float = 90
    managed_embeddings: bool = os.environ.get("RAG_MANAGED_EMBEDDINGS") == "1"


SETTINGS = Settings()
