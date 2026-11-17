"""Only a configured Unix socket reaches the fixed host-loopback relay."""
import asyncio
from contextvars import ContextVar
import json
import time
import httpx
from ..embeddings import ManagedOllama,PINNED_MODEL,PINNED_DIGEST
from ..ollama import ModelError,unit

DEADLINE=ContextVar('production_deadline',default=0.0)
QWEN_MODEL='qwen3:4b'
QWEN_DIGEST='359d7dd4bcdab3d86b87d73ac27966f4dbb9f5efdfcc75d34a8764a09474fae7'

class DisabledModel:
    def embed(self,*args,**kwargs): raise ModelError('Model route disabled')
    def select(self,*args,**kwargs): raise ModelError('Model route disabled')

class SocketModel(ManagedOllama):
    def __init__(self,config,guard):
        super().__init__(config.settings())
        self.config,self.guard=config,guard

    def _request(self,method,endpoint,payload=None):
        ready,_=self.guard.route()
        if not ready: raise ModelError('Model route unavailable')
        remaining=min(90,DEADLINE.get()-time.monotonic())
        if remaining<=0: raise ModelError('Request deadline exceeded')
        async def send():
            async with asyncio.timeout(remaining):
                transport=httpx.AsyncHTTPTransport(uds=str(self.config.socket),retries=0)
                async with httpx.AsyncClient(transport=transport,trust_env=False,follow_redirects=False,
                                             timeout=httpx.Timeout(remaining,connect=min(3,remaining))) as client:
                    async with client.stream(method,'http://127.0.0.1:11434'+endpoint,**({'json':payload} if payload is not None else {})) as response:
                        response.raise_for_status(); raw=bytearray()
                        async for part in response.aiter_bytes():
                            raw.extend(part)
                            if len(raw)>2_000_000: raise ModelError('Model response limit')
            body=json.loads(raw)
            if not isinstance(body,dict): raise ModelError('Invalid response')
            return body
        try: return asyncio.run(send())
        except (httpx.HTTPError,ValueError,RecursionError,TimeoutError,OSError) as exc: raise ModelError('Local model route failed') from exc

    def _identity(self,model,digest):
        body=self._request('GET','/api/tags'); models=body.get('models')
        if not isinstance(models,list) or any(not isinstance(m,dict) for m in models): raise ModelError('Invalid model identity')
        matches=[m for m in models if m.get('name')==model]
        if len(matches)!=1 or matches[0].get('digest')!=digest or matches[0].get('remote_host') or matches[0].get('remote_model'):
            raise ModelError('Pinned local model mismatch')

    def _verify_installed(self): self._identity(PINNED_MODEL,PINNED_DIGEST)

    def _post(self,endpoint,payload):
        if endpoint=='/api/chat':
            if payload.get('model')!=QWEN_MODEL: raise ModelError('Generation model mismatch')
            self._identity(QWEN_MODEL,QWEN_DIGEST)
        elif endpoint!='/api/embed' or payload.get('model')!=PINNED_MODEL:
            raise ModelError('Forbidden endpoint')
        body=self._request('POST',endpoint,payload)
        if body.get('model')!=payload['model'] or body.get('remote_host') or body.get('remote_model'): raise ModelError('Response identity mismatch')
        if endpoint=='/api/embed':
            if any(len(unit(v))!=768 for v in body.get('embeddings',[])): raise ModelError('Embedding dimension mismatch')
        else: self._identity(QWEN_MODEL,QWEN_DIGEST)
        return body
