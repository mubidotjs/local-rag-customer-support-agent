"""Static packaging and fake-only relay tests. Never executes Docker containers."""
import asyncio
import json
import os
from pathlib import Path
import shutil
import signal
import socket
import subprocess
import tempfile
import time
import urllib.request
import sys
import pytest
from evaluation import container_offline as audit
from support_agent.production.config import Rejected
from support_agent.production.relay import payload_ok,read_request,Relay
from support_agent.production.model import QWEN_MODEL
from support_agent.embeddings import PINNED_MODEL

def test_all_predecessor_files_claims_and_new_live_evidence_unchanged():
    result=audit.preserved()
    assert result['protected_files']==367 and result['verified_available']+len(result['skipped_optional'])==367
    if not result['skipped_optional']: assert audit.preserved(True)['verified_available']==367

def test_build_context_excludes_historical_data_and_runtime_tools():
    ignore=Path('.dockerignore').read_text()
    assert ignore.splitlines()[1]=='**'
    assert not any('!'+s in ignore for s in ('reports','corpus','.local','data','.env','.git'))
    runtime=Path('containers/requirements-runtime.lock').read_text()
    assert '--hash=sha256:' in runtime and all('=='+line.split('==')[1].split()[0] in Path('requirements.lock').read_text() for line in runtime.splitlines() if '==' in line)
    assert all(x not in runtime for x in ('pytest==','coverage==','langgraph==','mcp=='))
    assert 'host.docker.internal' not in Path('compose.yaml').read_text()
    assert '--require-hashes' in Path('containers/backend.Dockerfile').read_text()
    assert 'proxy_next_upstream off' in Path('containers/nginx.conf').read_text()

@pytest.mark.skipif(shutil.which('docker') is None,reason='Docker CLI unavailable; dedicated packaging CI requires Compose')
def test_compose_static_security_and_uds_override(tmp_path):
    env={**os.environ,'RAG_DATA_DIR':str(tmp_path.absolute()),'RAG_RELAY_DIR':str(tmp_path.absolute())}
    for args in ([],['-f','compose.yaml','-f','containers/compose.uds.yaml']):
        raw=subprocess.check_output(['docker','compose',*args,'--profile','maintenance','config','--format','json'],env=env,timeout=30)
        value=json.loads(raw)
        assert audit.compose_invariants(value)['model_requests']==0
        mode=value['services']['backend']['environment']['RAG_MODEL_MODE']
        assert mode==('uds' if args else 'disabled')
        assert len(value['services']['backend']['volumes'])==(2 if args else 1)

@pytest.mark.parametrize('method,path,body',[('GET','/api/pull',b''),('POST','/api/tags',b'{}'),
    ('GET','http://external.invalid',b''),('GET','/api/tags?extra=1',b''),('GET','/api/tags',b'{}'),
    ('POST','/api/embed',b'invalid'),('POST','/api/embed',b'[]'),
    ('POST','/api/embed',b'['*2000+b'0'+b']'*2000),
    ('POST','/api/embed',b'{"model":"remote"}'),('POST','/api/chat',b'{"tools":[{}]}')])
def test_relay_never_allows_downloads_external_paths_or_arbitrary_models(method,path,body):
    assert not payload_ok(method,path,body)

def test_relay_accepts_only_existing_bounded_embedding_and_generation_shapes():
    payload={'model':PINNED_MODEL,'input':['search_query: invented question'],'truncate':False,'keep_alive':'5m','options':{'num_ctx':2048}}
    assert payload_ok('POST','/api/embed',json.dumps(payload).encode())
    payload['input']=['arbitrary']; assert not payload_ok('POST','/api/embed',json.dumps(payload).encode())
    payload={'model':QWEN_MODEL,'stream':False,'think':False,'format':{'type':'object'},'keep_alive':'5m',
        'options':{'temperature':0,'seed':42,'num_predict':384,'num_ctx':4096},
        'messages':[{'role':'system','content':'finite policy'},{'role':'user','content':'invented question'}]}
    assert payload_ok('POST','/api/chat',json.dumps(payload).encode())
    payload['tools']=[]; assert not payload_ok('POST','/api/chat',json.dumps(payload).encode())

@pytest.mark.parametrize('header',[b'GET /api/tags HTTP/1.1\r\nHost: external.invalid\r\n\r\n',
    b'GET /api/tags HTTP/1.1\r\nHost: 127.0.0.1:11434\r\nHost: duplicate\r\n\r\n',
    b'GET /api/tags HTTP/1.1\r\nHost: 127.0.0.1:11434\r\nTransfer-Encoding: chunked\r\n\r\n',
    b'GET /api/tags HTTP/1.1\r\nHost: 127.0.0.1:11434\r\nContent-Length: 64001\r\n\r\n',
    b'GET /api/tags HTTP/1.0\r\n\r\n',b'GET /api/tags HTTP/1.1\r\nBadHeader\r\n\r\n'])
def test_relay_rejects_http_smuggling_and_oversized_headers(header):
    async def check():
        reader=asyncio.StreamReader(limit=8192); reader.feed_data(header); reader.feed_eof()
        with pytest.raises((Rejected,ValueError)): await read_request(reader)
    asyncio.run(check())

@pytest.mark.skipif(not hasattr(socket,'SO_PEERCRED'),reason='Real Unix peer credentials tested on Linux CI only')
def test_native_unix_relay_is_peer_bound_without_any_ollama_requests():
    async def check(path):
        calls=[]
        async def fake(method,route,body): calls.append((method,route,body)); return 200,b'{"models":[]}'
        relay=Relay(os.getuid(),sender=fake)
        server=await asyncio.start_unix_server(relay.handle,path=path,limit=8192)
        async with server:
            reader,writer=await asyncio.open_unix_connection(path)
            writer.write(b'GET /api/tags HTTP/1.1\r\nHost: 127.0.0.1:11434\r\n\r\n'); await writer.drain()
            response=await reader.read(); writer.close(); await writer.wait_closed()
            assert b'200 Local' in response and calls==[('GET','/api/tags',b'')]
            relay.uid=os.getuid()+1
            reader,writer=await asyncio.open_unix_connection(path)
            writer.write(b'GET /api/tags HTTP/1.1\r\nHost: 127.0.0.1:11434\r\n\r\n'); await writer.drain()
            try: assert not await reader.read()
            except ConnectionResetError: pass
            writer.close()
            try: await writer.wait_closed()
            except ConnectionResetError: pass
            assert len(calls)==1
    with tempfile.TemporaryDirectory(prefix='rag-') as root: asyncio.run(check(str(Path(root)/'relay.sock')))

def test_main_uses_one_bounded_worker_and_rejects_invalid_startup(monkeypatch,tmp_path):
    from support_agent.production import __main__ as main
    from support_agent.production.config import Config
    monkeypatch.setattr(main.os,'umask',lambda _:None)
    monkeypatch.setattr(main.Config,'environment',lambda:(_ for _ in ()).throw(Rejected('bad')))
    assert main.main()==2
    c=Config(data_dir=tmp_path)
    monkeypatch.setattr(main.Config,'environment',lambda:c)
    class Fake:
        config=c
    monkeypatch.setattr(main,'Production',lambda _:Fake())
    calls=[]; monkeypatch.setattr(main.uvicorn,'run',lambda *a,**k:calls.append(k))
    assert main.main()==0
    assert calls[0]['host']=='127.0.0.1' and calls[0]['workers']==1 and calls[0]['timeout_graceful_shutdown']==10
    assert calls[0]['access_log'] is False and calls[0]['limit_concurrency']==16

@pytest.mark.skipif(os.name!='posix',reason='Native SIGTERM handling is Linux runtime behavior')
def test_native_server_sigterm_is_clean_without_model_service(tmp_path):
    data=tmp_path/'data'; data.mkdir(mode=0o700)
    listener=socket.socket(); listener.bind(('127.0.0.1',0)); port=listener.getsockname()[1]; listener.close()
    program="from pathlib import Path; import uvicorn,os; from support_agent.production.config import Config; from support_agent.production.app import Production; app=Production(Config(data_dir=Path("+repr(str(data))+"),uid=os.getuid(),gid=os.getgid())); uvicorn.run(app,host='127.0.0.1',port="+str(port)+",access_log=False,timeout_graceful_shutdown=2)"
    log=tmp_path/'server.log'
    with log.open('wb') as stream:
        process=subprocess.Popen([sys.executable,'-c',program],stdout=stream,stderr=stream)
        try:
            deadline=time.monotonic()+10
            opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
            while True:
                try:
                    request=urllib.request.Request(f'http://127.0.0.1:{port}/health/live',headers={'Host':'127.0.0.1:8000'})
                    with opener.open(request,timeout=0.2) as response:
                        assert response.status==200
                    break
                except OSError:
                    if process.poll() is not None or time.monotonic()>deadline: raise AssertionError('Native test server failed startup')
                    time.sleep(0.05)
            process.terminate(); assert process.wait(timeout=5) in {0,-signal.SIGTERM}
        finally:
            if process.poll() is None: process.kill(); process.wait(timeout=5)
    assert not list(data.iterdir())
    assert 'Application shutdown complete' in log.read_text()
