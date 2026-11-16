from threading import Lock
import sqlite3
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, ConfigDict, Field
from starlette.responses import JSONResponse
from .answer import answer
from .config import SETTINGS
from .ollama import ModelError, Ollama
from .embeddings import ManagedOllama


class Question(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    question: str = Field(min_length=3, max_length=600)


def create_app(settings=SETTINGS, model=None):
    app = FastAPI(title="Local Support / evidence only", docs_url=None, redoc_url=None)
    model = model or (ManagedOllama(settings) if settings.managed_embeddings else Ollama(settings))
    lock = Lock()
    app.add_middleware(CORSMiddleware, allow_origins=["http://127.0.0.1:5173", "http://localhost:5173"],
                       allow_methods=["GET", "POST"], allow_headers=["Content-Type"])

    @app.middleware("http")
    async def bound_requests(request: Request, call_next):
        host = request.headers.get("host", "").split(":")[0]
        if host not in {"127.0.0.1", "localhost", "testserver"}:
            return JSONResponse({"detail": "Loopback only"}, 400)
        origin = request.headers.get("origin")
        if origin and origin not in {"http://127.0.0.1:5173", "http://localhost:5173"}:
            return JSONResponse({"detail": "Origin denied"}, 403)
        body = bytearray()
        async for part in request.stream():
            body.extend(part)
            if len(body) > 4096:
                return JSONResponse({"detail": "Body too large"}, 413)
        request._body = bytes(body)
        return await call_next(request)

    @app.get("/api/health")
    def health():
        return {"status": "ok", "indexed": settings.db_path.is_file(), "mode": "local-extractive"}

    @app.post("/api/ask")
    def ask(payload: Question):
        if not lock.acquire(blocking=False):
            raise HTTPException(429, "One local inference at a time; try again shortly")
        try:
            return answer(payload.question, settings, model)
        except (sqlite3.Error, ModelError, OSError, ValueError) as exc:
            raise HTTPException(503, "Local index or model unavailable. Run ingestion and check Ollama.") from exc
        finally:
            lock.release()

    return app


app = create_app()
