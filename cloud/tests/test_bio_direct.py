import json
import unittest
from unittest.mock import patch
from fastapi import FastAPI
from fastapi.testclient import TestClient
import bio
from bio_preview import install_preview_routes
from preview_budget import GlobalPreviewBudget

class DirectRoutesTest(unittest.TestCase):
    def fixture(self,actions):
        app=FastAPI();calls=[];budget=GlobalPreviewBudget()
        install_preview_routes(app,lambda r:dict(username='alice',status='active'),lambda *a:dict(port=21000,world_token='synthetic'),budget)
        config=dict(allowedActions=list(actions),executionTarget=dict(nodeId='n_test'))
        async def private(t,a,p):calls.append((a,p));return dict(schemaVersion='synthetic.control',ack=True)
        return app,config,calls,private

    def test_default_binding_rejects_before_transport(self):
        app,config,calls,private=self.fixture(bio.PREVIEW_ACTIONS)
        with patch('bio_preview.bio.load_binding',return_value=config),patch('bio_preview.private_request',side_effect=private),TestClient(app) as c:
            self.assertEqual(c.post('/v1/bio/preview/direct-open',json={},headers={'x-bio-session':'s','x-bio-viewport':'main'}).status_code,403)
        self.assertEqual(calls,[])

    def test_direct_control_is_budgeted_json_and_cannot_choose_session(self):
        app,config,calls,private=self.fixture(bio.PREVIEW_ACTIONS|bio.DIRECT_ACTIONS)
        with patch('bio_preview.bio.load_binding',return_value=config),patch('bio_preview.private_request',side_effect=private),patch.object(GlobalPreviewBudget,'wait',autospec=True,return_value=lambda:None) as wait,TestClient(app) as c:
            headers={'x-bio-session':'owned-session','x-bio-viewport':'main'}
            r=c.post('/v1/bio/preview/direct-open',json={'previewId':'preview-synthetic'},headers=headers)
            self.assertEqual(r.status_code,200);self.assertTrue(r.json()['ack']);self.assertNotIn('data',r.json());self.assertTrue(wait.called)
            self.assertEqual(calls[0][1]['sessionId'],'owned-session')
            self.assertEqual(c.post('/v1/bio/preview/direct-open',json={'sessionId':'foreign'},headers=headers).status_code,400)
        self.assertEqual(len(calls),1)

    def test_closed_action_set_includes_only_three_direct_actions(self):
        self.assertEqual(bio.DIRECT_ACTIONS,frozenset({'preview:direct-open','preview:direct-permit','preview:direct-close'}))

    def test_direct_without_read_permission_is_enabled(self):
        actions=(bio.PREVIEW_ACTIONS-{'preview:read'})|bio.DIRECT_ACTIONS
        app,config,calls,private=self.fixture(actions)
        with patch('bio_preview.bio.load_binding',return_value=config),patch('bio_preview.private_request',side_effect=private),TestClient(app) as client:
            reply=client.post('/v1/bio/preview/direct-open',json={},headers={'x-bio-session':'s','x-bio-viewport':'main'})
            self.assertEqual(reply.status_code,200)
        self.assertEqual(len(calls),1)

if __name__=='__main__':unittest.main()
