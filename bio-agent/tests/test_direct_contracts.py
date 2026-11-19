import copy
import json
from pathlib import Path
import unittest

from bioagent.errors import BioError
from bioagent.visualization.direct_contracts import (
    LIMITS, validate_direct_request, validate_channel, validate_permit, manifest_digest,
)

CASES = json.loads((Path(__file__).resolve().parents[1] / 'visualization/direct-contract-cases.json').read_text())


class DirectContractsTest(unittest.TestCase):
    def test_closed_direct_fields_and_boolean_ranges(self):
        for case in CASES['validRequests']:
            self.assertEqual(validate_direct_request(case['action'], case['value']), case['value'])
        for case in CASES['invalidRequests']:
            with self.subTest(case=case), self.assertRaisesRegex(BioError, 'INVALID_DIRECT_REQUEST'):
                validate_direct_request(case['action'], case['value'])

    def test_channel_cannot_choose_target_or_path(self):
        validate_channel(CASES['channel'])
        for patch in ({'path': '/tmp'}, {'projectKey': '../other'}, {'executionTargetSha256': 'a' * 64}, {'readerVersion': 'unknown'}):
            with self.assertRaises(BioError): validate_channel({**CASES['channel'], **patch})
        changed = copy.deepcopy(CASES['channel'])
        changed['source']['revision'] = True
        with self.assertRaises(BioError): validate_channel(changed)

    def test_permit_cannot_exceed_file_range_or_lie_about_types(self):
        validate_permit(CASES['permit'])
        for patch in ({'limit': 262145}, {'offset': True}, {'offset': 524288}, {'expiresAtMs': float('nan')}, {'handle': 'short'}):
            with self.assertRaises(BioError): validate_permit({**CASES['permit'], **patch})

    def test_manifest_digest_matches_across_integer_and_fractional_times(self):
        original = CASES['manifest']
        self.assertEqual(manifest_digest(original), CASES['manifestDigest'])
        changed = copy.deepcopy(original)
        changed['frames'][0]['timePs'] = -0.0
        self.assertEqual(manifest_digest(changed), CASES['manifestDigest'])
        for mutate in (lambda m: m['source'].update(sourceSha256='b' * 64), lambda m: m['frames'][1].update(timePs=2.5), lambda m: m['files'][0].update(sha256='b' * 64)):
            changed = copy.deepcopy(original); mutate(changed)
            self.assertNotEqual(manifest_digest(changed), CASES['manifestDigest'])

    def test_limits_enforce_range_boundaries(self):
        request = copy.deepcopy(CASES['validRequests'][1]['value'])
        request['limit'] = LIMITS['permitBytes']; validate_direct_request('direct-permit', request)
        request['limit'] += 1
        with self.assertRaises(BioError): validate_direct_request('direct-permit', request)
