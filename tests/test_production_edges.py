"""Deterministic failure/termination tests; no external model transport."""
import asyncio
from dataclasses import replace
import json
import os
from pathlib import Path
import socket
import struct
import time
import httpx
import pytest
from support_agent.production import relay,volume
from support_agent.production.config import Config,Rejected
from support_agent.production.storage import Guard,directory
from support_agent.production.model import SocketModel,DEADLINE,PINNED_MODEL,PINNED_DIGEST,QWEN_MODEL,QWEN_DIGEST

@pytest.mark.parametrize('mode',['ok','wrong-peer','busy','sender-failed','bad-request','oversize-response'])
def test_relay_peer_failure_busy_and_bounded_responses_are_closed(monkeypatch,mode):
    monkeypatch.setattr(socket,'SO_PEERCRED',17,raising=False)
    calls=[]
    class Sock:
        def getsockopt(self,*args): return struct.pack('3i',1,10002 if mode=='wrong-peer' else 10001,10001)
    class Writer:
        closed=False; data=b''
        def get_extra_info(self,*args): return Sock()
        def write(self,data): self.data+=data
        async def drain(self): pass
        def close(self): self.closed=True
        async def wait_closed(self): pass
    async def sender(*args):
        calls.append(args)
        if mode=='sender-failed': raise httpx.ConnectError('must not be logged')
        return 200,b'x'*2_000_001 if mode=='oversize-response' else b'{}'
    async def check():
        reader=asyncio.StreamReader(); reader.feed_data(b'GET '+(b'/api/pull' if mode=='bad-request' else b'/api/tags')+b' HTTP/1.1\r\nHost: 127.0.0.1:11434\r\n\r\n'); reader.feed_eof()
        handler=relay.Relay(sender=sender); handler.busy=mode=='busy'; writer=Writer()
        await handler.handle(reader,writer)
        assert writer.closed
        if mode=='ok': assert writer.data.startswith(b'HTTP/1.1 200')
        else: assert not writer.data
        if mode in {'wrong-peer','busy','bad-request'}: assert not calls
    asyncio.run(check())

def test_relay_start_stop_signal_cleanup_is_fixed_and_nonroot(tmp_path,monkeypatch):
    monkeypatch.setattr(socket,'SO_PEERCRED',17,raising=False)
    monkeypatch.setattr(os,'getuid',lambda:10001,raising=False)
    monkeypatch.setattr(os,'getgid',lambda:10001,raising=False)
    monkeypatch.setattr(os,'umask',lambda _:None)
    monkeypatch.setattr(relay,'directory',lambda *args:None)
    events=[]
    class Server:
        async def __aenter__(self): return self
        async def __aexit__(self,*args): pass
        def close(self): events.append('closed')
        async def wait_closed(self): pass
    async def start(handler,*,path,limit,backlog):
        assert limit==8192 and backlog==8
        path.write_bytes(b'fake socket inode'); return Server()
    monkeypatch.setattr(asyncio,'start_unix_server',start,raising=False)
    async def check():
        loop=asyncio.get_running_loop()
        monkeypatch.setattr(loop,'add_signal_handler',lambda sig,callback:callback())
        await relay.serve(tmp_path)
    asyncio.run(check())
    assert events==['closed'] and not (tmp_path/'ollama.sock').exists()
    (tmp_path/'ollama.sock').write_bytes(b'stale')
    with pytest.raises(Rejected,match='existing_socket'): asyncio.run(relay.serve(tmp_path))

def test_volume_cli_requires_confirmation_and_never_exposes_raw_errors(tmp_path,monkeypatch,capsys):
    result={'sha256':'a'*64,'chunks':1,'dimension':768}; calls=[]
    monkeypatch.setattr(volume,'inspect',lambda c:result)
    monkeypatch.setattr(volume,'backup',lambda c,p:calls.append('backup') or result)
    monkeypatch.setattr(volume,'restore',lambda c,p,s:calls.append(s) or result)
    volume.main(['check']); assert json.loads(capsys.readouterr().out)['status']=='PASS'
    volume.main(['backup','--backup-directory',str(tmp_path),'--confirm','WRITERS-STOPPED'])
    volume.main(['restore','--backup-directory',str(tmp_path),'--confirm','REPLACE-STOPPED-INDEX'])
    assert calls==['backup','REPLACE-STOPPED-INDEX']
    for args in (['backup'],['backup','--backup-directory',str(tmp_path)]):
        with pytest.raises(SystemExit): volume.main(args)
    monkeypatch.setattr(volume,'inspect',lambda _:(_ for _ in ()).throw(OSError('private path or secret')))
    with pytest.raises(SystemExit): volume.main(['check'])
    assert 'private path or secret' not in capsys.readouterr().err

def test_volume_backup_uses_validated_private_stopped_index_only(tmp_path,monkeypatch):
    c=Config(data_dir=tmp_path); (tmp_path/'support.sqlite3').write_bytes(b'invented data')
    destination=tmp_path/'backup'; destination.mkdir()
    result={'sha256':volume.sha(c.database),'chunks':1,'dimension':768}
    monkeypatch.setattr(volume,'directory',lambda *a:None)
    monkeypatch.setattr(volume,'inspect',lambda _:result)
    assert volume.backup(c,destination)==result
    assert (destination/'support.sqlite3').read_bytes()==b'invented data'
    with pytest.raises(Rejected): volume.backup(c,destination)
    with pytest.raises(Rejected): volume.restore(c,destination,'wrong')
    (destination/'backup.json').write_text('{}')
    with pytest.raises(Rejected,match='backup_hash'): volume.restore(c,destination,'REPLACE-STOPPED-INDEX')

def test_storage_missing_new_file_requires_restart_and_busy_is_bounded(tmp_path):
    c=Config(data_dir=tmp_path); guard=Guard(c,permissions=False)
    assert guard.check()==(False,'index_missing_restart_required')
    guard.lock.acquire()
    try: assert guard.check()==(False,'index_check_busy')
    finally: guard.lock.release()
    with pytest.raises(Rejected): directory(Path('relative'),10001,10001,permissions=False)
    with pytest.raises((Rejected,ValueError,OSError)): Guard(Config(data_dir=tmp_path/'missing'),permissions=False)
    if os.name!='posix':
        with pytest.raises(Rejected): Guard(c)

@pytest.mark.parametrize('failure',['wrong-model','wrong-dimension','bad-tags','remote','network'])
def test_socket_model_rejects_inconsistent_response_without_retry(tmp_path,monkeypatch,failure):
    class FakeGuard:
        def route(self): return True,'ready'
    model=SocketModel(Config(data_dir=tmp_path,mode='uds'),FakeGuard()); calls=[]
    def request(method,path,payload=None):
        calls.append((method,path))
        if failure=='network': raise RuntimeError('fake network failure')
        if path=='/api/tags':
            if failure=='bad-tags': return {'models':{}}
            return {'models':[{'name':PINNED_MODEL,'digest':PINNED_DIGEST,'remote_host':'evil' if failure=='remote' else ''}]}
        return {'model':'other' if failure=='wrong-model' else PINNED_MODEL,'embeddings':[[1.0] if failure=='wrong-dimension' else [1.0]+[0.0]*767]}
    monkeypatch.setattr(model,'_request',request)
    with pytest.raises(RuntimeError): model.embed(['invented'],query=True)
    assert len(calls)<=2

def test_socket_generation_identity_is_checked_around_exact_existing_payload(tmp_path,monkeypatch):
    class FakeGuard:
        def route(self): return True,'ready'
    model=SocketModel(Config(data_dir=tmp_path,mode='uds'),FakeGuard()); calls=[]
    def request(method,path,payload=None):
        calls.append((method,path))
        if path=='/api/tags': return {'models':[{'name':QWEN_MODEL,'digest':QWEN_DIGEST}]}
        return {'model':QWEN_MODEL,'done':True,'message':{'content':'{}'}}
    monkeypatch.setattr(model,'_request',request)
    model._post('/api/chat',{'model':QWEN_MODEL})
    assert calls==[('GET','/api/tags'),('POST','/api/chat'),('GET','/api/tags')]
    with pytest.raises(RuntimeError): model._post('/api/chat',{'model':'remote'})

@pytest.mark.parametrize('oversize',[False,True])
def test_relay_upstream_is_fixed_loopback_without_proxies_redirects_or_retries(monkeypatch,oversize):
    original=httpx.AsyncClient; calls=[]
    def respond(request):
        calls.append(request)
        assert str(request.url)=='http://127.0.0.1:11434/api/tags'
        return httpx.Response(200,content=b'x'*2_000_001 if oversize else b'{"models":[]}')
    def client(**kwargs):
        assert kwargs['trust_env'] is False and kwargs['follow_redirects'] is False
        kwargs['transport']=httpx.MockTransport(respond); return original(**kwargs)
    monkeypatch.setattr(httpx,'AsyncClient',client)
    if oversize:
        with pytest.raises(Rejected,match='response_limit'): asyncio.run(relay.forward('GET','/api/tags',b''))
    else: assert asyncio.run(relay.forward('GET','/api/tags',b''))==(200,b'{"models":[]}')
    assert len(calls)==1

def test_maintenance_role_requires_fixed_identity_and_no_live_embedding_flag(monkeypatch,tmp_path):
    from support_agent.production import maintenance
    calls=[]
    for args in ([],['--db','other'],['apply','--allow-local-embedding'],['apply','--allow-local-emb']):
        with pytest.raises(SystemExit): maintenance.main(args)
    monkeypatch.setattr(maintenance.Config,'environment',lambda:Config(data_dir=tmp_path))
    monkeypatch.setattr(maintenance.os,'name','posix')
    monkeypatch.setattr(maintenance.os,'getuid',lambda:10001,raising=False)
    monkeypatch.setattr(maintenance.os,'getgid',lambda:10001,raising=False)
    monkeypatch.setattr(maintenance.os,'umask',lambda _:None)
    monkeypatch.setattr(maintenance,'directory',lambda *args:None)
    monkeypatch.setattr(maintenance,'documents',lambda args:calls.append(args))
    maintenance.main(['status'])
    assert calls==[['documents','--db',str(tmp_path/'support.sqlite3'),'status']]
