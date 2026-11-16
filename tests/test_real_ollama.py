"""Opt-in actual inference; never run by ordinary CI."""
import os
from pathlib import Path
from dataclasses import replace
import pytest
from support_agent.config import Settings
from support_agent.store import ingest
from support_agent.ollama import Ollama
from support_agent.answer import answer


@pytest.mark.real
@pytest.mark.skipif(os.environ.get("RUN_REAL_OLLAMA") != "1", reason="Requires explicit local inference opt-in")
def test_actual_local_models(tmp_path):
    settings = replace(Settings(), db_path=tmp_path / "real.sqlite3")
    model = Ollama(settings)
    assert len(model.embed(["LumaDock returns are accepted within 30 days."])[0]) == 768
    ingest(Path("corpus"), settings, model)
    result = answer("How do I factory reset LumaDock Home?", settings, model)
    assert result["diagnostics"]["generation_called"]
    assert not result["abstained"]
    assert any("10 seconds" in c["quote"] for c in result["citations"])
