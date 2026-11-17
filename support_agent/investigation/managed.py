"""Explicit async ManagedOllama query capability; construction is network-free."""
import asyncio
import json
import time
import httpx
from ..embeddings import ManagedOllama, PINNED_MODEL, PINNED_DIGEST, config_digest
from ..ollama import ModelError, unit


class ManagedQuery(ManagedOllama):
    """Same binding/prefix/options as ManagedOllama, with cancellable async I/O.

    No worker threads, synthetic fallback, generation, discovery or downloads.
    HTTPX transport injection belongs to trusted test/runner code only.
    """
    def __init__(self, settings, *, max_queries=1, clock=time.monotonic):
        super().__init__(settings)
        if settings.ollama_url != "http://127.0.0.1:11434" or type(max_queries) is not int or not 1 <= max_queries <= 2:
            raise ValueError("Explicit fixed-loopback query capability required")
        self.max_queries, self.clock = max_queries, clock
        self.queries_attempted = 0
        self.counts = {"embedding_posts": 0, "metadata_gets": 0}
        self.failed = False
        self.active = False

    def embed(self, *args, **kwargs):
        raise ModelError("Use the bounded async query-only capability")

    def select(self, *args, **kwargs):
        raise ModelError("Generation is forbidden for a query-only capability")

    def compatible(self, settings, meta, dimension):
        self.binding.validate(settings)
        if (self.settings != settings or self.binding.model != PINNED_MODEL or self.binding.digest != PINNED_DIGEST
            or self.binding.config != config_digest(settings) or dimension != 768
            or meta.get("embed_model") != PINNED_MODEL or meta.get("embed_digest") != PINNED_DIGEST
            or meta.get("embed_config") != self.binding.config):
            raise ModelError("Managed query/index identity mismatch")

    async def _request(self, method, endpoint, deadline, payload=None):
        remaining = min(self.settings.timeout_seconds, deadline-self.clock())
        if remaining <= 0: raise TimeoutError("query_deadline")
        kind = "metadata_gets" if method == "GET" else "embedding_posts"
        self.counts[kind] += 1  # Includes failed sends; never retried.
        async with asyncio.timeout(remaining):
            async with httpx.AsyncClient(timeout=httpx.Timeout(remaining, connect=min(3, remaining)),
                                         trust_env=False, follow_redirects=False) as client:
                async with client.stream(method, self.settings.ollama_url+endpoint, **({"json":payload} if payload is not None else {})) as response:
                    response.raise_for_status()
                    raw = bytearray()
                    async for block in response.aiter_bytes():
                        raw.extend(block)
                        if len(raw) > 2_000_000: raise ModelError("Model response exceeded size limit")
        body = json.loads(raw)
        if not isinstance(body, dict): raise ModelError("Invalid model response")
        return body

    async def _identity(self, deadline):
        body = await self._request("GET", "/api/tags", deadline)
        models = body.get("models")
        if not isinstance(models, list) or any(not isinstance(m, dict) for m in models):
            raise ModelError("Invalid model metadata")
        matches = [m for m in models if m.get("name") == PINNED_MODEL]
        if (len(matches) != 1 or matches[0].get("digest") != PINNED_DIGEST
            or matches[0].get("remote_host") or matches[0].get("remote_model")):
            raise ModelError("Pinned local model identity mismatch")

    async def embed_query(self, question, *, deadline):
        if self.failed or self.active or self.queries_attempted >= self.max_queries:
            raise ModelError("Query capability exhausted")
        self.queries_attempted += 1
        self.active = True
        try:
            self.binding.validate(self.settings)
            if (self.settings.embed_model != PINNED_MODEL or self.binding.digest != PINNED_DIGEST
                or self.settings.ollama_url != "http://127.0.0.1:11434"
                or not isinstance(question, str) or not 3 <= len(question.strip()) <= 600):
                raise ModelError("Invalid managed query")
            await self._identity(deadline)
            body = await self._request("POST", "/api/embed", deadline, {
                "model": PINNED_MODEL, "input": ["search_query: "+question], "truncate": False,
                "keep_alive": "5m", "options": {"num_ctx": 2048}})
            vectors = body.get("embeddings")
            if (body.get("model") != PINNED_MODEL or body.get("remote_host") or body.get("remote_model")
                or not isinstance(vectors, list) or len(vectors) != 1):
                raise ModelError("Invalid query embedding response")
            vector = unit(vectors[0])
            if len(vector) != 768: raise ModelError("Query dimension mismatch")
            await self._identity(deadline)
            if self.clock() >= deadline: raise TimeoutError("query_deadline")
            return vector
        except BaseException as exc:
            self.failed = True
            if isinstance(exc, (httpx.HTTPError, ValueError, TypeError)):
                raise ModelError("Managed query response or transport failed") from exc
            raise
        finally:
            self.active = False
