"""Owner-started Linux UDS -> fixed loopback Ollama relay. Never a TCP listener."""
import argparse
import asyncio
import json
import os
from pathlib import Path
import signal
import socket
import stat
import struct
import httpx
from ..embeddings import PINNED_MODEL
from .config import Rejected
from .model import QWEN_MODEL
from .storage import directory,identity

def payload_ok(method,path,body):
    if method=='GET': return path=='/api/tags' and not body
    if method!='POST' or path not in {'/api/embed','/api/chat'}: return False
    try: value=json.loads(body.decode('utf-8'))
    except (ValueError,UnicodeError,RecursionError): return False
    if not isinstance(value,dict): return False
    if path=='/api/embed':
        return (set(value)=={'model','input','truncate','keep_alive','options'} and value['model']==PINNED_MODEL
                and value['truncate'] is False and value['keep_alive']=='5m' and value['options']=={'num_ctx':2048}
                and isinstance(value['input'],list) and 1<=len(value['input'])<=16
                and all(isinstance(t,str) and len(t)<=1217 and t.startswith(('search_query: ','search_document: ')) for t in value['input']))
    return (set(value)=={'model','stream','think','format','keep_alive','options','messages'} and value['model']==QWEN_MODEL
            and value['stream'] is False and value['think'] is False and isinstance(value['format'],dict)
            and value['keep_alive']=='5m' and value['options']=={'temperature':0,'seed':42,'num_predict':384,'num_ctx':4096}
            and isinstance(value['messages'],list) and len(value['messages'])==2
            and all(isinstance(m,dict) and set(m)=={'role','content'} and isinstance(m['content'],str) for m in value['messages'])
            and [m['role'] for m in value['messages']]==['system','user'])

async def read_request(reader):
    raw=await reader.readuntil(b'\r\n\r\n')
    if len(raw)>8192: raise Rejected('relay_header_limit')
    lines=raw[:-4].decode('ascii').split('\r\n')
    first=lines.pop(0).split(' ')
    if len(first)!=3 or first[2]!='HTTP/1.1': raise Rejected('relay_protocol')
    method,path,_=first
    headers={}
    for line in lines:
        key,sep,value=line.partition(':')
        if not sep or key.lower() in headers or key!=key.strip(): raise Rejected('relay_headers')
        headers[key.lower()]=value.strip()
    if headers.get('host')!='127.0.0.1:11434' or any(k in headers for k in ('transfer-encoding','authorization','proxy-authorization','upgrade')):
        raise Rejected('relay_headers')
    length=headers.get('content-length','0')
    if not length.isascii() or not length.isdigit() or int(length)>64000: raise Rejected('relay_body_limit')
    body=await reader.readexactly(int(length))
    if not payload_ok(method,path,body): raise Rejected('relay_operation_denied')
    return method,path,body

async def forward(method,path,body):
    async with asyncio.timeout(90):
        async with httpx.AsyncClient(trust_env=False,follow_redirects=False,timeout=httpx.Timeout(90,connect=3),
                                     transport=httpx.AsyncHTTPTransport(retries=0)) as client:
            async with client.stream(method,'http://127.0.0.1:11434'+path,content=body,
                                      headers={'Content-Type':'application/json'}) as response:
                raw=bytearray()
                async for block in response.aiter_bytes():
                    raw.extend(block)
                    if len(raw)>2_000_000: raise Rejected('relay_response_limit')
                return response.status_code,bytes(raw)

class Relay:
    def __init__(self,uid=10001,*,sender=forward):
        self.uid=uid; self.sender=sender; self.busy=False; self.clients=set()

    async def handle(self,reader,writer):
        task=asyncio.current_task()
        try:
            credentials=writer.get_extra_info('socket').getsockopt(socket.SOL_SOCKET,socket.SO_PEERCRED,struct.calcsize('3i'))
            _,uid,_=struct.unpack('3i',credentials)
            if uid!=self.uid or self.busy: return
            self.busy=True; self.clients.add(task)
            try:
                async with asyncio.timeout(5): method,path,body=await read_request(reader)
                code,raw=await self.sender(method,path,body)
                if type(code) is not int or not 100<=code<=599 or len(raw)>2_000_000: raise Rejected('relay_response')
                writer.write(f'HTTP/1.1 {code} Local\r\nContent-Type: application/json\r\nContent-Length: {len(raw)}\r\nConnection: close\r\n\r\n'.encode()+raw)
                async with asyncio.timeout(3): await writer.drain()
            finally:
                self.busy=False; self.clients.discard(task)
        except (OSError,ValueError,TimeoutError,struct.error,httpx.HTTPError,asyncio.IncompleteReadError,asyncio.LimitOverrunError,Rejected):
            pass # No request bodies, URLs or raw exception strings in logs.
        finally:
            writer.close()
            try:
                async with asyncio.timeout(1): await writer.wait_closed()
            except (OSError,TimeoutError):
                transport=getattr(writer,'transport',None)
                if transport is not None: transport.abort()

async def serve(path):
    if not hasattr(socket,'SO_PEERCRED') or os.getuid()!=10001 or os.getgid()!=10001: raise Rejected('native_linux_uid_required')
    directory(path,10001,10001)
    target=path/'ollama.sock'
    if target.exists() or target.is_symlink(): raise Rejected('existing_socket_refused')
    relay=Relay(); os.umask(0o077)
    server=await asyncio.start_unix_server(relay.handle,path=target,limit=8192,backlog=8)
    os.chmod(target,0o600); pinned=identity(target)
    stopped=asyncio.Event(); loop=asyncio.get_running_loop()
    for sig in (signal.SIGINT,signal.SIGTERM): loop.add_signal_handler(sig,stopped.set)
    try:
        async with server: await stopped.wait()
    finally:
        server.close(); await server.wait_closed()
        pending=list(relay.clients)
        for task in pending: task.cancel()
        if pending: await asyncio.gather(*pending,return_exceptions=True)
        if target.exists() and identity(target)==pinned: target.unlink()

def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--socket-dir',type=Path,required=True)
    args=parser.parse_args(argv)
    try: asyncio.run(serve(args.socket_dir))
    except (Rejected,OSError,ValueError): parser.exit(2,'Local relay configuration rejected.\n')

if __name__=='__main__': main()
