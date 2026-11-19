"""Offline public/private boundaries and one shared egress clock."""
import asyncio
import base64
import hashlib
from functools import wraps
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from preview_budget import GlobalPreviewBudget
from bio_preview import install_preview_routes, authenticate_browser
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient


def isolated_loop(function):
    @wraps(function)
    def run(self):
        loop=asyncio.new_event_loop()
        try:return loop.run_until_complete(function(self))
        finally:loop.close()
    return run


class SharedBudgetTest(unittest.TestCase):
    @isolated_loop
    async def test_send_permit_stays_owned_through_async_scope_check_and_write(self):
        async def sleep(seconds):await asyncio.sleep(0)
        budget=GlobalPreviewBudget(sleep=sleep)
        release=await budget.wait('first',16,'foreground',hold=True)
        other=asyncio.create_task(budget.wait('second',16,'foreground'));await asyncio.sleep(0)
        self.assertFalse(other.done());release();await other

    @isolated_loop
    async def test_three_tenants_share_four_mbps(self):
        now=[0.0];order=[]
        async def sleep(seconds):now[0]+=seconds;await asyncio.sleep(0)
        budget=GlobalPreviewBudget(clock=lambda:now[0],sleep=sleep)
        async def send(owner):
            for _ in range(3):await budget.wait(owner,16384,'foreground');order.append(owner)
        await asyncio.gather(*(send(owner) for owner in ('alice','bob','carol')))
        self.assertAlmostEqual(now[0],9*16384*8/4000000)
        self.assertEqual(order[:3],['alice','bob','carol']);self.assertEqual(set(order),{'alice','bob','carol'})

    @isolated_loop
    async def test_priority_and_cancel(self):
        gate=asyncio.Event();order=[]
        async def sleep(seconds):await gate.wait();await asyncio.sleep(0)
        budget=GlobalPreviewBudget(sleep=sleep)
        first=asyncio.create_task(budget.wait('first',1,'prefetch'));await asyncio.sleep(0)
        background=asyncio.create_task(budget.wait('background',1,'prefetch'))
        foreground=asyncio.create_task(budget.wait('foreground',1,'foreground'))
        canceled=asyncio.create_task(budget.wait('canceled',1,'foreground'));await asyncio.sleep(0);canceled.cancel()
        gate.set();await first;await foreground;self.assertFalse(background.done());await background
        with self.assertRaises(asyncio.CancelledError):await canceled

    @isolated_loop
    async def test_multi_worker_without_shared_backend_disabled(self):
        with patch.dict('os.environ',{'WEB_CONCURRENCY':'2'}):self.assertFalse(GlobalPreviewBudget().enabled)


class PreviewRoutesTest(unittest.TestCase):
    def test_legacy_read_permission_cannot_emit_coordinates(self):
        app=FastAPI();body=b'preview';digest=hashlib.sha256(body).hexdigest();seen=[]
        async def private(transport,action,payload):
            seen.append(payload['limit']);offset=payload['offset'];data=body[offset:offset+payload['limit']]
            return dict(previewId='preview-test',fileId='segment.xtc',offset=offset,nextOffset=offset+len(data),
                        byteSize=len(body),sourceSha256='a'*64,fileSha256=digest,manifestSha256='b'*64,sha256=hashlib.sha256(data).hexdigest(),data=base64.b64encode(data).decode())
        install_preview_routes(app,lambda request:dict(username='alice',status='active'),lambda *a:dict(port=21000,world_token='test-only'),GlobalPreviewBudget())
        config=dict(allowedActions=['preview:read'],executionTarget=dict(nodeId='n_test'))
        with patch('bio_preview.bio.load_binding',return_value=config),patch('bio_preview.private_request',side_effect=private),TestClient(app) as client:
            response=client.get('/v1/bio/preview/preview-test/files/segment.xtc?offset=0&limit=7',headers={'x-bio-session':'s','x-bio-viewport':'main','x-bio-source-sha256':'a'*64})
            self.assertEqual(response.status_code,403);self.assertEqual(response.json()['detail'],'PREVIEW_RELAY_DISABLED')
        self.assertEqual(seen,[])

    def test_visualization_reuses_existing_web_entry_cookie_without_expanding_account_auth(self):
        from types import SimpleNamespace
        browser=dict(username='alice',status='active');request=SimpleNamespace(cookies={'mdexp_web_session':'test-only'})
        with patch('bio_preview.webentry.browser_user',return_value=browser):
            self.assertEqual(authenticate_browser(request,lambda r:None),browser)

    def test_unauthorized_request_does_not_lookup_or_spend(self):
        app=FastAPI();seen=[]
        def authenticate(request):raise HTTPException(401,'unauthorized')
        install_preview_routes(app,authenticate,lambda *a:seen.append(a),GlobalPreviewBudget())
        with TestClient(app) as client:self.assertEqual(client.post('/v1/bio/preview/status',json={}).status_code,401)
        self.assertEqual(seen,[])
