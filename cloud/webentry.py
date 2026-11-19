"""Authenticated, single-use browser handoff into a tenant dsh instance."""
import hashlib
import hmac
import secrets
import threading
import time
import os
from urllib.parse import urlencode

from fastapi import HTTPException, Request
from fastapi.responses import RedirectResponse
import accounts

WEB_ORIGIN = os.environ.get('MDAGENT_WEB_ORIGIN', '').rstrip('/')
TICKET_TTL = 120
SESSION_TTL = 8 * 3600
MAX_TICKETS = 512
MAX_SESSIONS = 16
_tickets = {}
_lock = threading.Lock()

def _hash(value):
    return hashlib.sha256(value.encode()).hexdigest()

def _valid_source(user, record):
    field = record.get('credential_field')
    if field not in ('token_sha256', 'external_api_token_sha256'):
        return False
    current = (user or {}).get(field) or ''
    return bool(user and user.get('status') == 'active' and current
                and hmac.compare_digest(current, record.get('credential_hash') or ''))

def issue(token):
    user = accounts.by_token(token)
    if not user:
        raise HTTPException(401, '需要有效接口 Token')
    field = 'external_api_token_sha256' if user.get('token_kind') == 'external-api' else 'token_sha256'
    now = time.time()
    ticket = secrets.token_urlsafe(32)
    record = {'username':user['username'], 'credential_field':field,
              'credential_hash':_hash(token), 'expires':now+TICKET_TTL}
    with _lock:
        for key in list(_tickets):
            if _tickets[key]['expires'] <= now:
                del _tickets[key]
        if len(_tickets) >= MAX_TICKETS:
            raise HTTPException(429, '进入链接过多，请稍后重试')
        _tickets[_hash(ticket)] = record
    return {'url':WEB_ORIGIN+'/mdagent/web/open?'+urlencode({'ticket':ticket}),
            'expires_in':TICKET_TTL, 'one_time':True}

def consume(ticket):
    if not ticket or len(ticket) > 128:
        return None
    with _lock:
        record = _tickets.pop(_hash(ticket), None)
    now = time.time()
    if not record or record['expires'] <= now:
        return None
    session = 'mdsw_' + secrets.token_urlsafe(32)
    def mutate(db):
        user = accounts._find(db,record['username'])
        if not _valid_source(user,record):
            return None
        saved = [r for r in user.get('integration_sessions', [])
                 if r.get('expires',0) > now and _valid_source(user,r)]
        saved.append({'session_hash':_hash(session), 'expires':now+SESSION_TTL,
                      'credential_field':record['credential_field'],
                      'credential_hash':record['credential_hash']})
        user['integration_sessions'] = saved[-MAX_SESSIONS:]
        return session
    return accounts._txn(mutate)

def browser_user(session):
    if not session or not session.startswith('mdsw_') or len(session) > 128:
        return None
    digest = _hash(session)
    now = time.time()
    for user in accounts._load()['users']:
        for record in user.get('integration_sessions', []):
            if (record.get('expires',0) > now and _valid_source(user,record)
                    and hmac.compare_digest(record.get('session_hash') or '',digest)):
                view = dict(user)
                view['role'] = 'user'
                view['token_kind'] = 'web-entry'
                return view
    return None

def mount(app):
    @app.post('/v1/web/entry', status_code=201)
    def web_entry(request: Request):
        auth = request.headers.get('authorization','')
        token = auth[7:].strip() if auth.lower().startswith('bearer ') else None
        result = issue(token)
        from fastapi.responses import JSONResponse
        return JSONResponse(result,status_code=201,headers={
            'Cache-Control':'no-store', 'Referrer-Policy':'no-referrer'})

    @app.get('/web/open')
    def web_open(ticket: str = ''):
        session = consume(ticket)
        if not session:
            raise HTTPException(403, '进入链接已失效，请从你的网站重新点击进入')
        response = RedirectResponse('/',status_code=303,headers={
            'Cache-Control':'no-store', 'Referrer-Policy':'no-referrer'})
        response.set_cookie('mdexp_web_session',session,max_age=SESSION_TTL,
                            secure=True,httponly=True,samesite='lax',path='/')
        return response
