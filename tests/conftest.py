"""Synthetic tests: no model quality claims are derived from these fixtures."""
from dataclasses import replace
from pathlib import Path
import pytest
import httpx
import os
from support_agent.config import Settings
from support_agent.store import ingest
from support_agent.text import sentences


@pytest.fixture(autouse=True)
def no_unmocked_http_in_offline_tests(monkeypatch, request):
    if request.node.get_closest_marker("real") and os.environ.get("RUN_REAL_OLLAMA") == "1":
        return
    sync_send, async_send = httpx.Client.send, httpx.AsyncClient.send
    def offline_send(client, *args, **kwargs):
        if isinstance(client._transport, httpx.HTTPTransport):
            raise AssertionError("Offline tests forbid real HTTP; use an explicit synthetic transport")
        return sync_send(client, *args, **kwargs)
    async def offline_async_send(client, *args, **kwargs):
        if isinstance(client._transport, httpx.AsyncHTTPTransport):
            raise AssertionError("Offline tests forbid real HTTP; use an explicit synthetic transport")
        return await async_send(client, *args, **kwargs)
    monkeypatch.setattr(httpx.Client, "send", offline_send)
    monkeypatch.setattr(httpx.AsyncClient, "send", offline_async_send)


class SyntheticModel:
    def __init__(self):
        self.generation_calls = 0
        self.embedding_calls = 0

    def embed(self, texts, *, query=False):
        self.embedding_calls += 1
        # Deliberately simplistic vectors for orchestration/security tests only.
        return [[1.0, 0.0, 0.0] for _ in texts]

    def select(self, question, passages):
        self.generation_calls += 1
        passage = next((p for p in passages if p["chunk_id"].startswith("returns:")), passages[0])
        quote = next((s for s in sentences(passage["text"]) if "30 calendar" in s), sentences(passage["text"])[0])
        return {"abstain": False, "claims": [{"chunk_id": passage["chunk_id"], "quote": quote}]}


@pytest.fixture
def indexed(tmp_path):
    settings = replace(Settings(), db_path=tmp_path / "test.sqlite3")
    model = SyntheticModel()
    ingest(Path("corpus"), settings, model)
    return settings, model
