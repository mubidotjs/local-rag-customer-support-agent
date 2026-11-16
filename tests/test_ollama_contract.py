"""Synthetic transport validation; these do not prove local inference quality."""
import asyncio
from dataclasses import replace
import httpx
import pytest
from support_agent.config import Settings
from support_agent.ollama import ModelError, Ollama


@pytest.mark.parametrize("payload", [{}, {"embeddings": None}, {"embeddings": [[0, 0]]}, {"embeddings": [[1], [2]]}])
def test_reject_bad_embedding_responses(monkeypatch, payload):
    model = Ollama(Settings())
    monkeypatch.setattr(model, "_post", lambda *args: payload)
    with pytest.raises(ModelError):
        model.embed(["test"])


def test_embedding_prefixes_and_truncation(monkeypatch):
    model = Ollama(Settings())
    calls = []
    def post(endpoint, payload):
        calls.append((endpoint, payload))
        return {"embeddings": [[3, 4]]}
    monkeypatch.setattr(model, "_post", post)
    assert model.embed(["passage"]) == [[.6, .8]]
    model.embed(["question"], query=True)
    assert calls[0][1]["input"] == ["search_document: passage"]
    assert calls[1][1]["input"] == ["search_query: question"]
    assert calls[0][1]["truncate"] is False


def test_generation_schema_contains_only_evidence_choices(monkeypatch):
    model = Ollama(Settings())
    calls = []
    def post(endpoint, payload):
        calls.append(payload)
        return {"done": True, "message": {"content": '{"abstain":true,"claims":[]}'}}
    monkeypatch.setattr(model, "_post", post)
    model.select("How to reset?", [{"chunk_id": "reset:c000", "text": "Hold the reset button for 10 seconds."}])
    payload = calls[0]
    assert payload["think"] is False
    assert payload["options"]["num_predict"] == 384
    props = payload["format"]["properties"]["claims"]["items"]["properties"]
    assert props["chunk_id"]["enum"] == ["reset:c000"]
    assert props["quote"]["enum"] == ["Hold the reset button for 10 seconds."]


def test_total_deadline_cancels_request(monkeypatch):
    original = httpx.AsyncClient
    async def slow(request):
        await asyncio.sleep(1)
        return httpx.Response(200, json={"embeddings": [[1, 0]]})
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: original(**kw, transport=httpx.MockTransport(slow)))
    model = Ollama(replace(Settings(), timeout_seconds=.02))
    with pytest.raises(ModelError, match="request failed"):
        model.embed(["test"])


def test_local_transport_no_proxy_redirects_and_response_bound(monkeypatch):
    original = httpx.AsyncClient
    calls = []
    def client(**kwargs):
        calls.append(kwargs)
        return original(**kwargs, transport=httpx.MockTransport(lambda request: httpx.Response(200, content=b" " * 2_000_001)))
    monkeypatch.setattr(httpx, "AsyncClient", client)
    with pytest.raises(ModelError, match="size limit"):
        Ollama(Settings()).embed(["test"])
    assert calls[0]["trust_env"] is False and calls[0]["follow_redirects"] is False
