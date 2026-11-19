"""Authenticated same-origin binary facade. Compute data remains on its node."""
from __future__ import annotations
import hashlib
import json
import re
import httpx
from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
import bio
import webentry

ID=re.compile(r'^[a-z0-9][a-z0-9_.-]{0,127}$')


def authenticate_browser(request,fallback):
    """Same cookie precedence as the existing web proxy, only for display routes."""
    user=webentry.browser_user(request.cookies.get('mdexp_web_session'))
    return user if user else fallback(request)


async def private_request(transport,action,payload):
    async with httpx.AsyncClient(timeout=8 if action.startswith('direct-') else 130,follow_redirects=False,trust_env=False) as client:
        async with client.stream('POST',f"http://127.0.0.1:{transport['port']}/_bio/preview/{action}",headers={'x-world-token':transport['world_token']},json=payload) as response:
            if response.status_code!=200:raise HTTPException(403,'PREVIEW_ACCESS_DENIED')
            parts=[];size=0
            async for part in response.aiter_bytes():
                size+=len(part)
                if size>128*1024:raise HTTPException(502,'PREVIEW_RESPONSE_TOO_LARGE')
                parts.append(part)
            try:return json.loads(b''.join(parts))
            except ValueError:raise HTTPException(502,'PREVIEW_RESPONSE_INVALID')


def install_preview_routes(app,authenticate,transport_lookup,budget):
    def json_response(value,owner,check):
        data=json.dumps(value,allow_nan=False,separators=(',',':')).encode()
        if len(data)>128*1024:raise HTTPException(502,'PREVIEW_RESPONSE_TOO_LARGE')
        async def stream():
            for start in range(0,len(data),16384):
                part=data[start:start+16384];release=await budget.wait(owner,len(part),'foreground',hold=True)
                try:check();yield part
                finally:release()
        return StreamingResponse(stream(),media_type='application/json',headers={'cache-control':'no-store','content-length':str(len(data))})

    def assets_access(request):
        user=authenticate(request)
        if user.get('status')!='active':raise HTTPException(403,'BIO_TENANT_ACCESS_DENIED')
        config=bio.load_binding(user['username'])
        if not config or not bio.PREVIEW_CONTROL_ACTIONS<=set(config['allowedActions']):raise HTTPException(403,'PREVIEW_NOT_ENABLED')
        if not budget.enabled or budget.pid!=__import__('os').getpid():raise HTTPException(503,'PREVIEW_SHARED_BUDGET_UNAVAILABLE')
        if request.query_params:raise HTTPException(400,'PREVIEW_ASSET_INVALID')
        return user['username']

    def asset_manifest():
        from bioagent.errors import read_json, safe_file
        root=bio.BIO_ROOT/'web/visualization/dist'
        try:value=read_json(safe_file(root,'asset-manifest.json'))
        except Exception:raise HTTPException(503,'PREVIEW_ASSETS_NOT_BUILT')
        return root,value

    @app.get('/v1/bio/visualization/manifest')
    async def manifest(request: Request):
        owner=assets_access(request);_,value=asset_manifest()
        return json_response({'entry':value['entry'],'styles':value['styles']},owner,lambda:assets_access(request))

    @app.get('/v1/bio/visualization/assets/{asset_id}')
    async def asset(asset_id: str,request: Request):
        owner=assets_access(request);root,value=asset_manifest()
        records=[v for v in value['assets'] if v['assetId']==asset_id and re.fullmatch(r'[a-zA-Z0-9_.-]+\.(js|css)',asset_id)]
        if len(records)!=1:raise HTTPException(404,'PREVIEW_ASSET_NOT_FOUND')
        from bioagent.errors import safe_file
        path=safe_file(root,records[0]['path']);data=path.read_bytes()
        if hashlib.sha256(data).hexdigest()!=records[0]['sha256']:raise HTTPException(503,'PREVIEW_ASSET_CHANGED')
        encoding=None
        accepted=request.headers.get('accept-encoding','')
        if 'br' in accepted:data=safe_file(root,records[0]['path']+'.br').read_bytes();encoding='br'
        elif 'gzip' in accepted:data=safe_file(root,records[0]['path']+'.gz').read_bytes();encoding='gzip'
        async def stream():
            for start in range(0,len(data),16384):
                if await request.is_disconnected():return
                part=data[start:start+16384];release=await budget.wait(owner,len(part),'foreground',hold=True)
                try:assets_access(request);yield part
                finally:release()
        headers={'cache-control':'private, max-age=31536000, immutable','vary':'accept-encoding','content-length':str(len(data))}
        if encoding:headers['content-encoding']=encoding
        return StreamingResponse(stream(),media_type='text/javascript' if asset_id.endswith('.js') else 'text/css',headers=headers)

    def authorize(request,action):
        user=authenticate(request)
        if user.get('status')!='active':raise HTTPException(403,'BIO_TENANT_ACCESS_DENIED')
        username=user['username'];config=bio.load_binding(username)
        if config is None or 'preview:'+action not in config['allowedActions']:raise HTTPException(403,'PREVIEW_NOT_ENABLED')
        if action.startswith('direct-') and not (bio.PREVIEW_CONTROL_ACTIONS|bio.DIRECT_ACTIONS)<=set(config['allowedActions']):raise HTTPException(403,'DIRECT_NOT_CONFIGURED')
        if not budget.enabled or budget.pid!=__import__('os').getpid():raise HTTPException(503,'PREVIEW_SHARED_BUDGET_UNAVAILABLE')
        session=request.headers.get('x-bio-session','');viewport=request.headers.get('x-bio-viewport','')
        if not 1<=len(session)<=256 or any(ord(c)<32 or ord(c)==127 for c in session) or not ID.fullmatch(viewport):raise HTTPException(400,'INVALID_PREVIEW_REQUEST')
        transport=transport_lookup(username,config['executionTarget']['nodeId'])
        return username,transport,dict(sessionId=session,viewportId=viewport)

    @app.post('/v1/bio/preview/{action}')
    async def control(action: str,request: Request):
        if action not in {'describe','prepare','status','window','cancel','direct-open','direct-permit','direct-close'}:raise HTTPException(404,'PREVIEW_ACTION_NOT_FOUND')
        owner,transport,binding=authorize(request,action)
        parts=[];size=0
        async for chunk in request.stream():
            size+=len(chunk)
            if size>32768:raise HTTPException(413,'INVALID_PREVIEW_REQUEST')
            parts.append(chunk)
        try:payload=json.loads(b''.join(parts))
        except ValueError:raise HTTPException(400,'INVALID_PREVIEW_REQUEST')
        if not isinstance(payload,dict) or {'sessionId','viewportId'}&payload.keys():raise HTTPException(400,'INVALID_PREVIEW_REQUEST')
        try:result=await private_request(transport,action,{**binding,**payload})
        except httpx.TimeoutException:raise HTTPException(504,'DIRECT_TRANSFER_TIMEOUT' if action.startswith('direct-') else 'PREVIEW_TRANSFER_TIMEOUT') from None
        authorize(request,action)
        def check():
            _,current,_=authorize(request,action)
            if current!=transport:raise HTTPException(403,'PREVIEW_TRANSPORT_CHANGED')
        return json_response(result,owner,check)

    @app.get('/v1/bio/preview/{preview_id}/files/{file_id}')
    async def read(preview_id: str,file_id: str,request: Request):
        # Authenticate first, but never resolve a compute transport or read bytes.
        user=authenticate(request)
        if user.get('status')!='active':raise HTTPException(403,'BIO_TENANT_ACCESS_DENIED')
        raise HTTPException(403,'PREVIEW_RELAY_DISABLED')
