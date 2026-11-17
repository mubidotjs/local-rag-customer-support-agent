"""Base API remains independent of optional investigation SDKs; synthetic data only."""
import builtins
from dataclasses import replace
import pytest
from fastapi.testclient import TestClient
from support_agent.config import Settings
from support_agent.store import ingest


def test_base_ask_remains_functional_when_optional_imports_are_denied(tmp_path,monkeypatch):
    real_import=builtins.__import__
    def blocked(name,*args,**kwargs):
        if name.split(".")[0] in {"langgraph","mcp"}: raise ImportError("optional SDK deliberately unavailable")
        return real_import(name,*args,**kwargs)
    monkeypatch.setattr(builtins,"__import__",blocked)
    from support_agent.api import create_app
    sources=tmp_path/"invented"; sources.mkdir()
    quote="The junco token remains valid for 24 minutes."
    (sources/"junco.md").write_text("---\nid: junco\ntitle: Junco Guide\nversion: 1\ntopic: junco\n---\n"+quote+"\n",encoding="utf-8",newline="\n")
    class FakeModel:
        def embed(self,texts,**kwargs): return [[1.0,0.0,0.0] for _ in texts]
        def select(self,*args): return {"abstain":False,"claims":[{"chunk_id":"junco:c000","quote":quote}]}
    settings=replace(Settings(),db_path=tmp_path/"synthetic.sqlite3")
    model=FakeModel(); ingest(sources,settings,model)
    before=settings.db_path.read_bytes()
    with TestClient(create_app(settings,model)) as client:
        response=client.post("/api/ask",json={"question":"How long does the junco token remain valid?"})
        assert response.status_code==200 and not response.json()["abstained"]
        assert response.json()["citations"][0]["quote"]==quote
        assert client.post("/api/investigate",json={"question":"test"}).status_code==404
    assert settings.db_path.read_bytes()==before


def test_investigation_cli_reports_missing_optional_sdk_without_creating_a_demo(monkeypatch,capsys):
    from support_agent.investigation.__main__ import main
    real_import=builtins.__import__
    def blocked(name,globals=None,*args,**kwargs):
        if name=="workflow" and globals and globals.get("__package__")=="support_agent.investigation":
            raise ImportError("optional SDK deliberately unavailable")
        return real_import(name,globals,*args,**kwargs)
    monkeypatch.setattr(builtins,"__import__",blocked)
    with pytest.raises(SystemExit): main(["--demo"])
    assert "dependencies unavailable" in capsys.readouterr().err
