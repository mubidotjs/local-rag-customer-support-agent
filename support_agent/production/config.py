from dataclasses import dataclass, replace
from pathlib import Path
import os
from ..config import Settings

class Rejected(RuntimeError):
    """Static reason codes only; never interpolate operator data or exceptions."""

@dataclass(frozen=True)
class Config:
    data_dir: Path = Path('/var/lib/rag')
    relay_dir: Path = Path('/run/ollama-relay')
    mode: str = 'disabled'
    uid: int = 10001
    gid: int = 10001
    request_seconds: float = 180.0
    body_seconds: float = 5.0

    @classmethod
    def environment(cls, env=None):
        env = os.environ if env is None else env
        if any((k.startswith('RAG_') and k != 'RAG_MODEL_MODE') or k.startswith('OLLAMA_') for k in env):
            raise Rejected('unsupported_environment')
        if any(v and k.startswith(('LANGSMITH_','LANGCHAIN_','OTEL_')) for k,v in env.items()):
            raise Rejected('external_tracing_forbidden')
        mode = env.get('RAG_MODEL_MODE', 'disabled')
        if mode not in {'disabled', 'uds'}: raise Rejected('invalid_model_mode')
        return cls(mode=mode)

    @property
    def database(self): return self.data_dir/'support.sqlite3'

    @property
    def socket(self): return self.relay_dir/'ollama.sock'

    def settings(self):
        return replace(Settings(), db_path=self.database, managed_embeddings=True)
