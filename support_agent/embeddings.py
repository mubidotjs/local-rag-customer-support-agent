"""Explicit identities for incremental reuse; construction never contacts a model."""
from dataclasses import dataclass
import asyncio
import hashlib
import json
import re
import httpx
from .ollama import ModelError, Ollama, unit


PINNED_MODEL = "nomic-embed-text:latest"
PINNED_DIGEST = "0a109f422b47e3a30ba2b10eca18548e944e8a23073ee3f3e947efcf3c45e59f"


def config_digest(settings):
    config = {"normalize": "bom-crlf-tabs-v1", "chunks": "character-boundaries-v1",
              "chunk_chars": settings.chunk_chars, "document_prefix": "search_document: ",
              "query_prefix": "search_query: ", "num_ctx": 2048, "truncate": False}
    return hashlib.sha256(json.dumps(config, sort_keys=True).encode()).hexdigest()


@dataclass(frozen=True)
class Binding:
    model: str
    digest: str
    config: str

    def validate(self, settings):
        if not all(isinstance(v,str) for v in (self.model,self.digest,self.config)) or self.model != settings.embed_model or len(self.model) > 120 or not re.fullmatch(r"[0-9a-f]{64}", self.digest) or self.config != config_digest(settings):
            raise ValueError("Incompatible embedding model identity or preprocessing configuration")
        return self


def adapter_binding(adapter, settings):
    binding = getattr(adapter, "binding", None)
    if not isinstance(binding, Binding):
        raise ValueError("An explicitly identified embedding adapter is required")
    return binding.validate(settings)


def embed_batches(texts, adapter, settings, dimension=None):
    binding = adapter_binding(adapter, settings)
    vectors = []
    for start in range(0, len(texts), 16):
        batch = texts[start:start+16]
        result = adapter.embed(batch)
        if adapter_binding(adapter, settings) != binding or not isinstance(result, list) or len(result) != len(batch):
            raise ValueError("Embedding identity or count changed")
        for vector in result:
            vector = unit(vector)
            if dimension is None:
                dimension = len(vector)
            if len(vector) != dimension:
                raise ValueError("Embedding dimension mismatch")
            encoded = json.dumps(vector, allow_nan=False)
            if len(encoded) > 120000:
                raise ValueError("Embedding storage limit exceeded")
            vectors.append(encoded)
    return vectors, dimension


class SyntheticEmbeddings:
    """Deterministic test vectors, never evidence of AI acceptance."""
    def __init__(self, settings):
        self.binding = Binding(settings.embed_model, hashlib.sha256(b"synthetic-sha256-8-v1").hexdigest(), config_digest(settings))
        self.calls = 0
        self.texts = []

    def embed(self, texts, *, query=False):
        self.calls += 1
        self.texts.extend(texts)
        return [unit([b+1 for b in hashlib.sha256(t.encode()).digest()[:8]]) for t in texts]


class ManagedOllama(Ollama):
    """Future authorized local use only; verifies pinned tags around each batch.

    No downloads, redirects, proxies or retries. The local owner must not retag
    models during use; tag verification cannot authenticate a hostile daemon.
    """
    def __init__(self, settings):
        super().__init__(settings)
        self.binding = Binding(PINNED_MODEL, PINNED_DIGEST, config_digest(settings)).validate(settings)

    def _verify_installed(self):
        # Bounded GET, only when embed() is explicitly invoked, never at preview.
        async def read():
            async with asyncio.timeout(self.settings.timeout_seconds):
                async with httpx.AsyncClient(timeout=httpx.Timeout(self.settings.timeout_seconds, connect=3), trust_env=False, follow_redirects=False) as client:
                    async with client.stream("GET", self.settings.ollama_url + "/api/tags") as response:
                        response.raise_for_status()
                        raw = bytearray()
                        async for block in response.aiter_bytes():
                            raw.extend(block)
                            if len(raw) > 2_000_000:
                                raise ModelError("Model metadata exceeds limit")
                        return json.loads(raw)
        try:
            body=asyncio.run(read())
        except (httpx.HTTPError,ValueError,TimeoutError) as exc:
            raise ModelError("Pinned model metadata verification failed") from exc
        models = body.get("models") if isinstance(body,dict) else None
        if not isinstance(models,list) or any(not isinstance(m,dict) for m in models):
            raise ModelError("Invalid model metadata")
        matches = [m for m in models if m.get("name") == self.binding.model]
        if len(matches) != 1 or matches[0].get("digest") != self.binding.digest:
            raise ModelError("Pinned embedding model digest mismatch")

    def embed(self, texts, *, query=False):
        self._verify_installed()
        result = super().embed(texts, query=query)
        self._verify_installed()
        return result

    def _post(self, endpoint, payload):
        result=super()._post(endpoint,payload)
        if endpoint=="/api/embed" and result.get("model")!=self.binding.model:
            raise ModelError("Embedding response model identity mismatch")
        return result
