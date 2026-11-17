"""Bounded ASGI boundary around unchanged /api/ask. No administrative routes."""
import asyncio
import json
import logging
import re
import secrets
import time
from starlette.responses import JSONResponse
from starlette.concurrency import run_in_threadpool
from ..api import create_app
from .config import Rejected
from .storage import Guard
from .model import DEADLINE,DisabledModel,SocketModel

LOG=logging.getLogger('rag.production')
PATHS={'/api/ask','/api/health','/health/live','/health/ready'}

def emit(event,**fields): LOG.info(json.dumps({'event':event,**fields},sort_keys=True,separators=(',',':')))

class Production:
    def __init__(self,config,*,model=None,permissions=True):
        self.config=config; self.guard=Guard(config,permissions=permissions)
        self.inner=create_app(config.settings(),model if model is not None else SocketModel(config,self.guard) if config.mode=='uds' else DisabledModel())
        self.closing=False; self.active=0

    async def __call__(self,scope,receive,send):
        if scope['type']=='lifespan':
            async def lifecycle():
                event=await receive()
                if event['type']=='lifespan.shutdown': self.closing=True
                return event
            return await self.inner(scope,lifecycle,send)
        if scope['type']!='http': return
        rid=secrets.token_hex(16); started=time.monotonic(); status=500; response_started=False
        route=scope.get('path',''); method=scope.get('method','')
        async def response(code,reason):
            await JSONResponse({'detail':reason},code)(scope,receive,wrapped_send)
        async def wrapped_send(message):
            nonlocal status,response_started
            if message['type']=='http.response.start':
                response_started=True; status=message['status']; message={**message,'headers':list(message.get('headers',[]))+[(b'x-request-id',rid.encode()),(b'cache-control',b'no-store')]}
            await send(message)
        token=None; admitted=False
        try:
            headers=scope.get('headers',[])
            if len(headers)>64 or sum(len(k)+len(v) for k,v in headers)>8192: return await response(431,'Headers too large')
            hosts=[v.decode('latin1') for k,v in headers if k==b'host']
            origins=[v.decode('latin1') for k,v in headers if k==b'origin']
            if len(hosts)!=1 or not re.fullmatch(r'(?:127\.0\.0\.1|localhost)(?::(?:5173|8000|8080))?',hosts[0]): return await response(400,'Loopback only')
            if len(origins)>1 or origins and origins[0] not in {'http://127.0.0.1:5173','http://localhost:5173'}: return await response(403,'Origin denied')
            if route not in PATHS: return await response(404,'Not found')
            if method not in {'GET','POST'} or method=='POST' and route!='/api/ask': return await response(405,'Method denied')
            if self.closing: return await response(503,'Shutting down')
            if self.active>=16: return await response(503,'Request capacity reached')
            self.active+=1; admitted=True
            token=DEADLINE.set(started+self.config.request_seconds)
            lengths=[v for k,v in headers if k==b'content-length']
            if len(lengths)>1 or lengths and (not lengths[0].isdigit() or int(lengths[0])>4096): return await response(413,'Body too large')
            body=bytearray()
            async with asyncio.timeout(self.config.body_seconds):
                while True:
                    message=await receive()
                    if message['type']=='http.disconnect': return
                    body.extend(message.get('body',b''))
                    if len(body)>4096: return await response(413,'Body too large')
                    if not message.get('more_body',False): break
            if lengths and int(lengths[0])!=len(body): return await response(400,'Invalid body length')
            if route=='/health/live': return await response(200,'alive')
            if route in {'/health/ready','/api/ask'}:
                ready,reason=await run_in_threadpool(self.guard.check)
                if not ready: return await response(503,reason)
                if route=='/health/ready': return await response(200,'ready')
            sent=False
            async def buffered_receive():
                nonlocal sent
                if not sent:
                    sent=True; return {'type':'http.request','body':bytes(body),'more_body':False}
                return await receive()
            remaining=DEADLINE.get()-time.monotonic()
            async with asyncio.timeout(max(0,remaining)):
                await self.inner(scope,buffered_receive,wrapped_send)
        except TimeoutError:
            if not response_started: await response(408,'Request deadline exceeded')
        except asyncio.CancelledError: raise
        except Exception:
            if not response_started: await response(500,'Local request failed')
        finally:
            if token is not None: DEADLINE.reset(token)
            if admitted: self.active-=1
            emit('http',request_id=rid,route=route if route in PATHS else 'other',method=method if method in {'GET','POST'} else 'other',status=status,elapsed_ms=round((time.monotonic()-started)*1000))
