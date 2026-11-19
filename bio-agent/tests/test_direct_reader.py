import hashlib
import io
import json
import os
from pathlib import Path
import struct
import sys
import tempfile
import unittest
from unittest.mock import patch

from bioagent.errors import BioError, sha256_file
from bioagent.store import WorkflowStore
from bioagent.visualization.direct_reader import run_reader, decode_record, encode_record
from bioagent.visualization.direct_permits import DirectPermitStore
from bioagent.visualization.published_reader import PublishedPreviewReader
from tests.direct_fixture import make_direct_fixture, direct_binding


class DirectReaderTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='bio-direct-reader-', dir=os.environ.get('BIO_DIRECT_TEST_TMPDIR'))
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)/hashlib.sha256(b'session-test').hexdigest()
        self.f = make_direct_fixture(self.root)
        self.reader = PublishedPreviewReader(self.root, WorkflowStore(self.root, read_only=True))
        self.permits = DirectPermitStore(self.root, reader=self.reader)
        self.channel = self.permits.open_channel(direct_binding(self.f))

    def test_partial_prefix_and_eof_fail_closed(self):
        for value in (b'\0', struct.pack('>II', 32769, 0), struct.pack('>II', 1, 65537), struct.pack('>II', 2, 0)+b'{'):
            with self.assertRaisesRegex(BioError, 'DIRECT_PROTOCOL_INVALID'): decode_record(io.BytesIO(value))

    def test_read_consumes_exact_permit_and_no_extra_stdout(self):
        file_id = self.f['manifest']['topologyFile']
        permit = self.permits.issue(self.channel['channelId'], file_id, 0, 16)
        inputs = io.BytesIO(encode_record(dict(kind='hello', requestId='hello-test', channel=self.channel))+
                            encode_record(dict(kind='read', requestId='read-test', channelId=self.channel['channelId'], handle=permit['handle'])))
        out = io.BytesIO(); before = sha256_file(self.f['output']/'trajectory.dcd')
        with patch('bioagent.controller.Controller.__init__', side_effect=AssertionError('reader must not construct Controller')):
            self.assertEqual(run_reader(self.root.parent, Path(self.f['target']['python']), inputs, out), 0)
        out.seek(0); hello, binary = decode_record(out); data, binary = decode_record(out); done, tail = decode_record(out)
        self.assertEqual(hello['uid'], os.getuid()); self.assertEqual(hello['channelId'], self.channel['channelId'])
        expected = (self.root/'previews/files'/self.f['manifest']['previewId']/file_id).read_bytes()[:16]
        self.assertEqual(binary, expected); self.assertEqual(data['nextOffset'], 16); self.assertEqual(done['kind'], 'done')
        self.assertIsNone(decode_record(out)); self.assertEqual(sha256_file(self.f['output']/'trajectory.dcd'), before)
        with self.assertRaisesRegex(BioError, 'DIRECT_REPLAY'): self.permits.claim(permit['channelId'], permit['handle'])

    def test_wrong_host_uid_target_or_project_rejected(self):
        for patch in ({'executionTargetSha256': 'a'*64}, {'projectKey': 'a'*64}, {'uid': os.getuid()+1}, {'hostIdentitySha256': 'a'*64}):
            inputs = io.BytesIO(encode_record(dict(kind='hello', requestId='hello-test', channel={**self.channel, **patch})))
            out = io.BytesIO(); self.assertEqual(run_reader(self.root.parent, Path(self.f['target']['python']), inputs, out), 1)
            out.seek(0); header, binary = decode_record(out)
            self.assertEqual(header['kind'], 'error'); self.assertEqual(binary, b'')

    def test_same_project_rebind_consumes_each_clients_own_permit(self):
        other = self.permits.open_channel(direct_binding(self.f, client='second-client'))
        file_id = self.f['manifest']['topologyFile']
        records = []
        for index, channel in enumerate((self.channel, other)):
            permit = self.permits.issue(channel['channelId'], file_id, index, 1)
            records += [encode_record(dict(kind='hello', requestId=f'hello-{index}', channel=channel)),
                        encode_record(dict(kind='read', requestId=f'read-{index}', channelId=channel['channelId'], handle=permit['handle']))]
        output = io.BytesIO()
        self.assertEqual(run_reader(self.root.parent, Path(self.f['target']['python']), io.BytesIO(b''.join(records)), output), 0)
        output.seek(0); observed = []
        while (record := decode_record(output)) is not None:
            if record[0]['kind'] == 'data': observed.append(record[1])
        expected = (self.root/'previews/files'/self.f['manifest']['previewId']/file_id).read_bytes()
        self.assertEqual(observed, [expected[:1], expected[1:2]])

    def test_expired_after_first_frame_stops(self):
        # Expiration is tested between frames using a stream that advances the issuer clock.
        now = [100.0]; permits = DirectPermitStore(self.root, reader=self.reader, clock=lambda: now[0])
        channel = permits.open_channel(direct_binding(self.f, client='expiry-client'))
        permit = permits.issue(channel['channelId'], self.f['manifest']['topologyFile'], 0, 20)
        claim = permits.claim(channel['channelId'], permit['handle']); permits.check(claim, 0, 10)
        now[0] += 5.001
        with self.assertRaisesRegex(BioError, 'DIRECT_PERMIT_EXPIRED'): permits.check(claim, 10, 10)

    def test_target_marker_change_after_first_data_stops_next_frame(self):
        from bioagent.execution_target import target_sha256
        from bioagent.visualization.direct_contracts import LIMITS
        permit=self.permits.issue(self.channel['channelId'],self.f['manifest']['topologyFile'],0,16)
        marker=self.root/'execution-target.json'
        class ChangingOutput(io.BytesIO):
            changed=False
            def flush(output):
                if not output.changed and b'"kind":"data"' in output.getvalue():
                    value=json.loads(marker.read_text());value['target']['targetRevision']+=1;value['executionTargetSha256']=target_sha256(value['target']);marker.write_text(json.dumps(value));output.changed=True
        output=ChangingOutput();inputs=io.BytesIO(encode_record(dict(kind='hello',requestId='h',channel=self.channel))+encode_record(dict(kind='read',requestId='r',channelId=self.channel['channelId'],handle=permit['handle'])))
        with patch.dict(LIMITS,{'frameBytes':8}):
            self.assertEqual(run_reader(self.root.parent,Path(sys.executable),inputs,output),1)
        output.seek(0);seen=[]
        while (record:=decode_record(output)) is not None:seen.append(record[0])
        self.assertEqual([v['kind'] for v in seen],['hello','data','error']);self.assertEqual(seen[-1]['code'],'DIRECT_BINDING_CHANGED')
