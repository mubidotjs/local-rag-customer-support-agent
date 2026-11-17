"""No live model traffic: injected fake models/transports and invented indexes."""
import asyncio
from dataclasses import replace
import json
import logging
import os
from pathlib import Path
import re
import socket
import sqlite3
import time
import httpx
import pytest
from starlette.testclient import TestClient
from support_agent.config import Settings
from support_agent.embeddings import Binding,PINNED_MODEL,PINNED_DIGEST,config_digest
from support_agent.store import ingest,connect
from support_agent.production.app import Production
from support_agent.production.config import Config,Rejected
from support_agent.production.model import DisabledModel,SocketModel,DEADLINE,QWEN_MODEL,QWEN_DIGEST
from support_agent.production.storage import Guard

@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    async def blocked(*args,**kwargs): raise AssertionError('Live transport forbidden in production tests')
    monkeypatch.setattr(httpx.AsyncHTTPTransport,'handle_async_request',blocked)

@pytest.fixture
def index(tmp_path):
    data=tmp_path/'data'; data.mkdir(mode=0o700)
    sources=tmp_path/'invented'; sources.mkdir()
    (sources/'maple.md').write_text('---\nid: maple\ntitle: Maple Guide\nversion: 1\ntopic: maple\n---\nThe maple access token remains valid for 27 minutes.\n',encoding='utf-8',newline='\n')
    uid=os.getuid() if hasattr(os,'getuid') else 10001; gid=os.getgid() if hasattr(os,'getgid') else 10001
    config=Config(data_dir=data,uid=uid,gid=gid)
    settings=config.settings()
    class Fake:
        binding=Binding(PINNED_MODEL,PINNED_DIGEST,config_digest(settings))
        def embed(self,texts,**kwargs): return [[1.0]+[0.0]*767 for _ in texts]
        def select(self,*args): return {'abstain':False,'claims':[{'chunk_id':'maple:c000','quote':'The maple access token remains valid for 27 minutes.'}]}
    fake=Fake(); ingest(sources,settings,fake); os.chmod(config.database,0o600)
    return config,fake

@pytest.mark.parametrize('env',[{'RAG_DB':'/etc/passwd'},{'RAG_MODEL_MODE':'host'},{'OLLAMA_HOST':'0.0.0.0'},
    {'RAG_UNKNOWN':'secret'},{'LANGSMITH_API_KEY':'secret'},{'OTEL_EXPORTER_OTLP_ENDPOINT':'https://external.invalid'}])
def test_config_rejects_unsafe_or_unknown_environment(env):
    with pytest.raises(Rejected): Config.environment(env)
    assert Config.environment({}).mode=='disabled'
    assert Config.environment({'RAG_MODEL_MODE':'uds'}).mode=='uds'

def test_health_has_zero_model_calls_and_preserves_index(index,monkeypatch):
    c,fake=index; before=c.database.read_bytes(); calls=[]
    def forbidden(*args,**kwargs): calls.append(1); raise AssertionError('Probe must not call a model')
    monkeypatch.setattr(fake,'embed',forbidden); monkeypatch.setattr(fake,'select',forbidden)
    app=Production(c,model=fake,permissions=False)
    with TestClient(app,base_url='http://127.0.0.1:5173') as client:
        assert client.get('/health/live').status_code==200
        assert client.get('/health/ready').status_code==503
        assert client.get('/api/health').status_code==200
        assert client.post('/api/ask',json={'question':'How long does the maple token remain valid?'}).status_code==503
        assert client.get('/openapi.json').status_code==404
    assert app.closing and not calls and before==c.database.read_bytes()

def test_same_origin_backward_compatible_grounded_answer_and_safe_logs(index,monkeypatch,caplog):
    c,fake=index; app=Production(c,model=fake,permissions=False)
    monkeypatch.setattr(app.guard,'route',lambda:(True,'ready'))
    caplog.set_level(logging.INFO,logger='rag.production')
    secret='secret-private-input'
    with TestClient(app,base_url='http://localhost:5173') as client:
        value=client.post('/api/ask',json={'question':'How long does the maple access token remain valid?'},headers={'Origin':'http://localhost:5173','X-Request-ID':secret})
        assert value.status_code==200 and not value.json()['abstained']
        assert value.json()['citations'][0]['chunk_id']=='maple:c000'
        assert re.fullmatch('[0-9a-f]{32}',value.headers['x-request-id'])
        assert value.headers['cache-control']=='no-store'
        assert client.get('/'+secret+'?secret=private').status_code==404
        assert client.get('/health/live',headers={'Host':'evil.invalid'}).status_code==400
        assert client.get('/health/live',headers={'Origin':'https://evil.invalid'}).status_code==403
    entries=[json.loads(r.message) for r in caplog.records if r.name=='rag.production']
    assert entries and secret not in json.dumps(entries) and 'maple' not in json.dumps(entries)

@pytest.mark.parametrize('change',['corrupt','dimension','journal','replacement','missing'])
def test_bad_index_or_replacement_fails_readiness_without_writes(index,change):
    c,_=index; guard=Guard(c,permissions=False)
    if change=='corrupt':
        with sqlite3.connect(c.database) as db: db.execute('UPDATE chunks SET source_end=source_end+1')
    elif change=='dimension':
        with sqlite3.connect(c.database) as db: db.execute("UPDATE metadata SET value='8' WHERE key='dimension'")
    elif change=='journal': Path(str(c.database)+'-journal').write_bytes(b'invented unfinished update')
    elif change=='replacement':
        p=c.data_dir/'new'; p.write_bytes(c.database.read_bytes()); os.replace(p,c.database)
    else: c.database.unlink()
    before=c.database.read_bytes() if c.database.exists() else None
    assert not guard.check()[0]
    assert (c.database.read_bytes() if c.database.exists() else None)==before

def test_bounded_body_headers_concurrency_and_operational_failures(index,monkeypatch):
    c,fake=index; app=Production(c,model=fake,permissions=False)
    with TestClient(app,base_url='http://127.0.0.1:5173') as client:
        assert client.post('/api/ask',content='x'*4097).status_code==413
        assert client.get('/health/live',headers={'X-Large':'x'*8200}).status_code==431
        app.active=16; assert client.get('/health/live').status_code==503; app.active=0
        monkeypatch.setattr(app.guard,'route',lambda:(True,'ready'))
        monkeypatch.setattr(fake,'embed',lambda *a,**k:(_ for _ in ()).throw(RuntimeError('private prompt')))
        response=client.post('/api/ask',json={'question':'How long does the maple access token remain valid?'})
        assert response.status_code==500 and 'private' not in response.text
        app.closing=True; assert client.get('/health/live').status_code==503

def test_slow_or_disconnected_body_is_bounded(index):
    c,fake=index; app=Production(replace(c,body_seconds=0.01),model=fake,permissions=False)
    async def call(disconnect):
        sent=[]
        async def receive():
            if disconnect: return {'type':'http.disconnect'}
            await asyncio.sleep(1)
        async def send(v): sent.append(v)
        await app({'type':'http','path':'/api/ask','method':'POST','headers':[(b'host',b'localhost:5173')]},receive,send)
        return sent
    assert asyncio.run(call(False))[0]['status']==408
    assert asyncio.run(call(True))==[] and app.active==0

def test_existing_single_inference_lock_rejects_parallel_asks(index,monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    c,fake=index; app=Production(c,model=fake,permissions=False)
    monkeypatch.setattr(app.guard,'route',lambda:(True,'ready'))
    entered,release=Event(),Event(); original=fake.embed
    def wait(*args,**kwargs):
        entered.set(); assert release.wait(3)
        return original(*args,**kwargs)
    monkeypatch.setattr(fake,'embed',wait)
    with TestClient(app,base_url='http://localhost:5173') as client,ThreadPoolExecutor(max_workers=1) as executor:
        first=executor.submit(client.post,'/api/ask',json={'question':'How long does the maple access token remain valid?'})
        try:
            assert entered.wait(2)
            assert client.get('/health/live').status_code==200
            assert client.post('/api/ask',json={'question':'How long does the maple access token remain valid?'}).status_code==429
        finally: release.set()
        assert first.result(timeout=3).status_code==200

@pytest.mark.skipif(os.name!='posix',reason='POSIX ownership/permissions are tested in Linux CI')
def test_private_mount_permissions_and_backup_restore(index,tmp_path):
    from support_agent.production.volume import backup,restore,inspect
    c,_=index; guard=Guard(c)
    assert guard.file()==guard.pinned
    backup_dir=tmp_path/'backup'; backup_dir.mkdir(mode=0o700)
    original=backup(c,backup_dir)
    with pytest.raises(Rejected): restore(c,backup_dir,'wrong')
    with pytest.raises(Rejected): backup(c,backup_dir)
    with sqlite3.connect(c.database) as db: db.execute("UPDATE metadata SET value='2' WHERE key='index_revision'")
    assert restore(c,backup_dir,'REPLACE-STOPPED-INDEX')==original
    assert not guard.check()[0] # restored inode requires serving process restart
    os.chmod(c.database,0o644)
    with pytest.raises(Rejected): Guard(c)
    os.chmod(c.database,0o600); os.chmod(c.data_dir,0o777)
    with pytest.raises(Rejected): Guard(c)

@pytest.mark.skipif(os.name!='posix',reason='Linux filesystem defenses')
def test_symlink_hardlink_and_socket_mounts(index,tmp_path):
    c,_=index; link=tmp_path/'link'; link.symlink_to(c.data_dir,target_is_directory=True)
    with pytest.raises(ValueError): Guard(replace(c,data_dir=link))
    os.link(c.database,c.data_dir/'duplicate')
    with pytest.raises(Rejected): Guard(c)
    (c.data_dir/'duplicate').unlink()
    relay=tmp_path/'relay'; relay.mkdir(mode=0o700)
    config=replace(c,mode='uds',relay_dir=relay); guard=Guard(config)
    assert not guard.check()[0]
    config.socket.write_bytes(b'not a Unix socket'); os.chmod(config.socket,0o600)
    assert not guard.check()[0]
    config.socket.unlink()
    sock=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM)
    sock.bind(str(config.socket)); os.chmod(config.socket,0o600)
    try:
        assert guard.check()==(True,'ready')
        with connect(c.database) as db:
            assert db.execute('PRAGMA query_only').fetchone()[0]==1
            with pytest.raises(sqlite3.OperationalError): db.execute('DELETE FROM documents')
    finally: sock.close()

def test_model_socket_transport_identity_dimensions_and_deadline(index,monkeypatch):
    c,_=index; c=replace(c,mode='uds'); guard=Guard(replace(c,mode='disabled'),permissions=False)
    monkeypatch.setattr(guard,'route',lambda:(True,'ready'))
    model=SocketModel(c,guard); sent=[]; client=httpx.AsyncClient
    def respond(request):
        sent.append(request)
        if request.method=='GET': return httpx.Response(200,json={'models':[{'name':PINNED_MODEL,'digest':PINNED_DIGEST},{'name':QWEN_MODEL,'digest':QWEN_DIGEST}]})
        return httpx.Response(200,json={'model':PINNED_MODEL,'embeddings':[[1.0]+[0.0]*767]})
    def fake_client(**kwargs):
        assert isinstance(kwargs['transport'],httpx.AsyncHTTPTransport)
        assert kwargs['trust_env'] is False and kwargs['follow_redirects'] is False
        kwargs['transport']=httpx.MockTransport(respond); return client(**kwargs)
    monkeypatch.setattr(httpx,'AsyncClient',fake_client)
    token=DEADLINE.set(time.monotonic()+2)
    try:
        assert len(model.embed(['invented'],query=True)[0])==768 and [r.method for r in sent]==['GET','POST','GET']
        assert json.loads(sent[1].content)['input']==['search_query: invented']
        with pytest.raises(RuntimeError): model._post('/api/pull',{})
        monkeypatch.setattr(model,'_request',lambda *a,**k:{'models':[{'name':PINNED_MODEL,'digest':'0'*64}]})
        with pytest.raises(RuntimeError): model._verify_installed()
    finally: DEADLINE.reset(token)
    with pytest.raises(RuntimeError): SocketModel(c,guard)._request('GET','/api/tags')
    with pytest.raises(RuntimeError): DisabledModel().embed(['invented'])
