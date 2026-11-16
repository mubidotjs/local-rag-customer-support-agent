"""Synthetic tests: no model quality claims are derived from these fixtures."""
from dataclasses import replace
from pathlib import Path
import pytest
from support_agent.config import Settings
from support_agent.store import ingest
from support_agent.text import sentences


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
