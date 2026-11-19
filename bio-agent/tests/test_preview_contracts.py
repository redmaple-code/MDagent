import copy
import json
from pathlib import Path
import unittest
from bioagent.errors import BioError
from bioagent.visualization.contracts import LIMITS, validate_preview_request, validate_manifest

ROOT = Path(__file__).resolve().parents[1]
SOURCE = dict(kind="accepted", sourceId="trajectory", sourceSha256="a" * 64,
              workflowId="wf", revision=1, stageId="production", attemptId="attempt")
REQUEST = dict(source=SOURCE, profile="overview", atomSet="overview", startFrame=0, endFrame=202, stride=1)


class PreviewContractsTest(unittest.TestCase):
    def test_pbc_mode_is_closed_and_legacy_requests_remain_valid(self):
        self.assertEqual(validate_preview_request(REQUEST), REQUEST)
        for mode in ('raw', 'unwrap'):
            value={**REQUEST,'pbc':mode}
            try: actual=validate_preview_request(value)
            except BioError as error: self.fail(f'valid PBC mode rejected: {error.code}')
            self.assertEqual(actual,value)
        for mode in (None, True, 'wrap', 'exec()', {'mode':'unwrap'}):
            with self.assertRaisesRegex(BioError,'INVALID_PREVIEW_REQUEST'):
                validate_preview_request({**REQUEST,'pbc':mode})
        with self.assertRaisesRegex(BioError,'INVALID_PREVIEW_REQUEST'):
            validate_preview_request({**REQUEST,'pbc':'unwrap','python':'/foreign/python'})

    def test_closed_request_rejects_path_and_script(self):
        for patch in ({"path": "/foreign/trajectory.dcd"}, {"python": "exec()"},
                      {"startFrame": -1}, {"startFrame": True}, {"endFrame": 0.5}, {"stride": 0}):
            with self.subTest(patch=patch), self.assertRaisesRegex(BioError, "INVALID_PREVIEW_REQUEST"):
                validate_preview_request({**REQUEST, **patch})
        for digest in (None, "a", "A" * 64):
            with self.assertRaises(BioError):
                validate_preview_request({**REQUEST, "source": {**SOURCE, "sourceSha256": digest}})

    def test_input_is_static_only(self):
        source = {**SOURCE, "kind": "input", **dict.fromkeys(("workflowId", "revision", "stageId", "attemptId"))}
        static = {**REQUEST, "source": source, "startFrame": 0, "endFrame": 0}
        self.assertEqual(validate_preview_request(static), static)
        for patch in ({"endFrame": 1}, {"source": {**source, "attemptId": "a"}}):
            with self.assertRaises(BioError): validate_preview_request({**static, **patch})

    def test_limits_are_identical(self):
        self.assertEqual(LIMITS["overviewFrames"], 200)
        self.assertEqual(LIMITS["windowFrames"], 64)
        self.assertEqual(LIMITS["windowBytes"], 32 * 1024**2)
        self.assertEqual(LIMITS["cacheBytes"], 64 * 1024**2)
        self.assertEqual(LIMITS["httpMaxBytes"], 1024**2)
        self.assertEqual((LIMITS["minFps"], LIMITS["maxFps"]), (1, 20))

    def test_shared_cases(self):
        for case in json.loads((ROOT / "visualization/contract-cases.json").read_text()):
            with self.subTest(case=case["name"]):
                if case["valid"]: validate_preview_request(case["request"])
                else:
                    with self.assertRaises(BioError): validate_preview_request(case["request"])

    def test_manifest_rejects_duplicate_frames_and_missing_file(self):
        file = lambda fid, kind, start=None, count=None: dict(fileId=fid, kind=kind, byteSize=10, sha256="b" * 64,
                                                            previewFrameStart=start, previewFrameCount=count)
        value = dict(schemaVersion="bio.preview.v1", previewId="preview", source=SOURCE, atomCount=2,
                     sourceFrameCount=3, atomMapFile="map", topologyFile="top", precision=3, previewOnly=True,
                     frames=[dict(sourceFrameIndex=0, segmentId="seg", segmentFrameIndex=0, timePs=0),
                             dict(sourceFrameIndex=2, segmentId="seg", segmentFrameIndex=1, timePs=5)],
                     files=[file("map", "atom-map"), file("top", "topology"), file("seg", "xtc", 0, 2)])
        validate_manifest(value)
        for field, replacement in (("sourceFrameIndex", 0), ("segmentFrameIndex", 0), ("timePs", float("nan")), ("segmentId", "missing")):
            changed = copy.deepcopy(value); changed["frames"][1][field] = replacement
            with self.assertRaisesRegex(BioError, "INVALID_PREVIEW_MANIFEST"): validate_manifest(changed)


if __name__ == "__main__": unittest.main()
