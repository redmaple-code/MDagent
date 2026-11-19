import json
import hashlib
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import uuid
from types import SimpleNamespace
from unittest.mock import patch

from bioagent.errors import BioError, sha256_file
from bioagent.store import WorkflowStore
from bioagent.visualization.published_reader import PublishedPreviewReader
from bioagent.visualization.direct_permits import DirectPermitStore
from tests.direct_fixture import make_direct_fixture, direct_binding


class DirectPermitsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='bio-direct-permits-', dir=os.environ.get('BIO_DIRECT_TEST_TMPDIR'))
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)/hashlib.sha256(b'session-test').hexdigest()
        self.f = make_direct_fixture(self.root)
        self.now = [100.0]
        self.reader = PublishedPreviewReader(self.root, WorkflowStore(self.root, read_only=True))
        self.permits = DirectPermitStore(self.root, reader=self.reader, clock=lambda: self.now[0])
        self.channel = self.permits.open_channel(direct_binding(self.f))
        self.file_id = self.f['manifest']['topologyFile']

    def issue(self, limit=2):
        return self.permits.issue(self.channel['channelId'], self.file_id, 0, limit)

    def test_changed_host_after_open_blocks_new_permit(self):
        from bioagent.visualization.service import PreviewService
        from bioagent.visualization.direct_permits import actor_for
        service = PreviewService(SimpleNamespace(project=self.root, target=self.f['target'], python=Path(sys.executable), store=self.f['store']))
        service.direct = self.permits
        with patch('bioagent.execution_target.host_identity',return_value='0'*64):
            with self.assertRaisesRegex(BioError,'COMPUTE_IDENTITY_MISMATCH'):
                service.direct_permit(self.channel['channelId'],self.file_id,0,1,actor_for(direct_binding(self.f)))
        self.assertTrue(json.loads(self.permits.path.read_text())['channels'][self.channel['channelId']]['closed'])

    def test_expiry_and_reserved_bytes_cannot_replay(self):
        permit = self.issue(); claim = self.permits.claim(permit['channelId'], permit['handle'])
        with self.assertRaisesRegex(BioError, 'DIRECT_REPLAY'): self.permits.claim(permit['channelId'], permit['handle'])
        self.now[0] += 5.001
        with self.assertRaisesRegex(BioError, 'DIRECT_PERMIT_EXPIRED'): self.permits.check(claim, 0, 1)

    def test_close_blocks_next_frame(self):
        permit = self.issue(); claim = self.permits.claim(permit['channelId'], permit['handle'])
        self.permits.check(claim, 0, 1); self.permits.close(permit['channelId'])
        with self.assertRaises(BioError): self.permits.check(claim, 1, 1)

    def test_source_change_blocks_issued_permit(self):
        permit = self.issue()
        (self.f['output']/'trajectory.times.csv').write_text('changed')
        with self.assertRaisesRegex(BioError, 'PREVIEW_SOURCE_CHANGED'):
            self.permits.claim(permit['channelId'], permit['handle'])

    def test_two_slots_refuse_third_without_overwriting_live_nonce(self):
        first = self.issue(); self.issue()
        with self.assertRaisesRegex(BioError, 'DIRECT_SLOT_LIMIT'): self.issue()
        claim = self.permits.claim(first['channelId'], first['handle'])
        self.permits.check(claim, 0, 2)
        self.issue()
        with self.assertRaises(BioError): self.permits.check(claim, 0, 1)

    def test_channel_owner_epoch_and_project_cannot_be_forged(self):
        for patch in ({'tenantId': 'foreign'}, {'agentEpoch': 'foreign-epoch'}, {'sessionId': 'different'}):
            with self.assertRaisesRegex(BioError, 'DIRECT_BINDING_CHANGED'):
                self.permits.open_channel({**direct_binding(self.f), **patch})

    def test_unknown_schema_and_symlink_preserve_original_record(self):
        original = self.permits.path.read_bytes()
        self.permits.path.write_text('{"schemaVersion":"unknown","channels":{}}')
        with self.assertRaises(BioError): self.issue()
        self.assertIn('unknown', self.permits.path.read_text())
        self.permits.path.write_bytes(original)
        external = Path(self.tmp.name)/'foreign.json'; external.write_text('protected')
        self.permits.path.rename(self.permits.path.with_suffix('.saved'))
        self.permits.path.symlink_to(external)
        with self.assertRaises(BioError): self.issue()
        self.assertEqual(external.read_text(), 'protected')

    def test_channel_limit_and_close_allow_owned_runtime_reuse(self):
        for i in range(63): self.permits.open_channel(direct_binding(self.f, client='client-'+str(i)))
        with self.assertRaisesRegex(BioError, 'DIRECT_SLOT_LIMIT'):
            self.permits.open_channel(direct_binding(self.f, client='extra'))
        self.permits.close(self.channel['channelId'])
        self.permits.open_channel(direct_binding(self.f, client='extra'))

    def test_changed_revision_denies_cached_reader(self):
        self.reader.resolve(self.f['manifest']['previewId'])
        with self.f['store']._connection(write=True) as db:
            db.execute('INSERT INTO workflows VALUES (?,?,?,?,?)', ('wf', 2, 'c'*64, 'COMPLETED', '{}'))
        with self.assertRaises(BioError): self.reader.read_bytes(self.f['manifest']['previewId'], self.file_id, 0, 1)

    def test_ready_record_replacement_denies_cached_reader(self):
        from bioagent.errors import atomic_json
        self.reader.resolve(self.f['manifest']['previewId'])
        atomic_json(self.root/'previews/jobs/job-direct-fixture/status.json', dict(state='CANCELED', previewId=self.f['manifest']['previewId']))
        with self.assertRaises(BioError): self.reader.read_bytes(self.f['manifest']['previewId'], self.file_id, 0, 1)

    def test_readonly_missing_db_creates_nothing(self):
        absent = Path(self.tmp.name)/'absent'
        with self.assertRaises(BioError): WorkflowStore(absent, read_only=True)
        self.assertFalse(absent.exists())
        before = sha256_file(self.root/'workflow.sqlite3')
        with self.assertRaisesRegex(BioError, 'STORE_READ_ONLY'):
            with WorkflowStore(self.root, read_only=True)._connection(write=True): pass
        self.assertEqual(sha256_file(self.root/'workflow.sqlite3'), before)

    def test_direct_read_never_initializes_resource_pool(self):
        before = sha256_file(self.f['output']/'trajectory.dcd')
        with patch('bioagent.controller.Controller.__init__', side_effect=AssertionError('no Controller')), patch('bioagent.resource_pool.ResourcePool.__init__', side_effect=AssertionError('no pool')):
            reader = PublishedPreviewReader(self.root, WorkflowStore(self.root, read_only=True))
            block = reader.read_bytes(self.f['manifest']['previewId'], self.file_id, 0, 10)
            self.assertEqual(len(block['data']), 10)
            self.assertEqual(block['nextOffset'], 10)
        self.assertEqual(sha256_file(self.f['output']/'trajectory.dcd'), before)

    def test_bridge_producer_enforces_fixed_actions_and_actor(self):
        from bioagent.bridge import Bridge
        from bioagent.visualization.service import PreviewService
        from bioagent.visualization.direct_permits import actor_for
        bridge = object.__new__(Bridge)
        bridge.previews = PreviewService(SimpleNamespace(project=self.root, python=Path(sys.executable), target=self.f['target'], store=self.f['store']))
        def call(tool, args):
            return bridge.handle_host(dict(schemaVersion='bio.host.v1', requestId=str(uuid.uuid4()), tool=tool, arguments=args))
        binding = direct_binding(self.f, client='bridge-client')
        opened = call('preview-direct-open', {'binding': binding})
        self.assertTrue(opened['ok'])
        channel_id = opened['result']['channelId']; actor = actor_for(binding)
        permit = call('preview-direct-permit', dict(channelId=channel_id, fileId=self.file_id, offset=0, limit=1, actor=actor))
        self.assertTrue(permit['ok'])
        denied = call('preview-direct-permit', dict(channelId=channel_id, fileId=self.file_id, offset=0, limit=1, actor={**actor, 'tenantId': 'foreign'}))
        self.assertFalse(denied['ok']); self.assertEqual(denied['error']['code'], 'DIRECT_ACCESS_DENIED')
        self.assertFalse(call('preview-direct-permit', dict(channelId=channel_id, fileId=self.file_id, offset=0, limit=1, actor=actor, path='/foreign'))['ok'])

    def test_single_claim_across_processes(self):
        real = DirectPermitStore(self.root, reader=self.reader)
        channel = real.open_channel(direct_binding(self.f, client='real-clock-client'))
        permit = real.issue(channel['channelId'], self.file_id, 0, 2)
        program = '''import json,sys
from pathlib import Path
from bioagent.store import WorkflowStore
from bioagent.visualization.published_reader import PublishedPreviewReader
from bioagent.visualization.direct_permits import DirectPermitStore
from bioagent.errors import BioError
v=json.load(sys.stdin);p=Path(v['root'])
s=DirectPermitStore(p,reader=PublishedPreviewReader(p,WorkflowStore(p,read_only=True)),create=False)
try:s.claim(v['channelId'],v['handle']);print('CLAIMED')
except BioError as e:print(e.code)
'''
        processes = [subprocess.Popen([sys.executable, '-B', '-c', program], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) for _ in range(2)]
        payload = json.dumps(dict(root=str(self.root), channelId=permit['channelId'], handle=permit['handle']))
        for child in processes: child.stdin.write(payload); child.stdin.close(); child.stdin = None
        results = [child.communicate(timeout=10)[0].strip() for child in processes]
        self.assertEqual(sorted(results), ['CLAIMED', 'DIRECT_REPLAY'])
