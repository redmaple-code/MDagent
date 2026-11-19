"""Closed preview metadata shared with the browser and control host."""
from __future__ import annotations
import json
import math
from pathlib import Path
import re
from ..errors import BioError, canonical, require

CONTRACT = json.loads((Path(__file__).resolve().parents[3] / "visualization/contracts.json").read_text())
LIMITS = CONTRACT["limits"]
IDENTIFIER = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,127}$")
HASH = re.compile(r"^[a-f0-9]{64}$")


def integer(value, low=0, high=1000000):
    return type(value) is int and low <= value <= high


def identifier(value):
    return type(value) is str and IDENTIFIER.fullmatch(value) is not None


def keys(value, names):
    return type(value) is dict and set(value) == set(names.split())


def validate_source(value, code="INVALID_PREVIEW_REQUEST"):
    require(keys(value, "kind sourceId sourceSha256 workflowId revision stageId attemptId"), code)
    require(value["kind"] in {"input", "accepted"} and identifier(value["sourceId"])
            and type(value["sourceSha256"]) is str and HASH.fullmatch(value["sourceSha256"]), code)
    if value["kind"] == "input":
        require(all(value[k] is None for k in ("workflowId", "revision", "stageId", "attemptId")), code)
    else:
        require(all(identifier(value[k]) for k in ("workflowId", "stageId", "attemptId"))
                and integer(value["revision"], 1), code)
    return value


def validate_preview_request(value):
    code = "INVALID_PREVIEW_REQUEST"
    require(keys(value, "source profile atomSet startFrame endFrame stride") or
            keys(value, "source profile atomSet startFrame endFrame stride pbc"), code)
    require(type(value.get('pbc','raw')) is str and value.get('pbc','raw') in {'raw','unwrap'},code)
    validate_source(value["source"], code)
    require(value["profile"] in {"overview", "detail"} and value["atomSet"] in {"overview", "solute-heavy", "all"}, code)
    require(integer(value["startFrame"]) and integer(value["endFrame"]) and value["endFrame"] >= value["startFrame"]
            and integer(value["stride"], 1), code)
    if value["source"]["kind"] == "input":
        require(value["startFrame"] == value["endFrame"] == 0, code)
    if value["profile"] == "detail":
        require((value["endFrame"] - value["startFrame"]) // value["stride"] + 1 <= LIMITS["windowFrames"], code)
    require(len(canonical(value)) <= 8192, code)
    return value


def validate_processing(value,code='INVALID_PREVIEW_MANIFEST'):
    require(keys(value,'mode makeWhole makeWholeVersion unwrap unwrapVersion runtimeSha256'),code)
    require(value['mode']=='unwrap' and value['makeWhole']=='MDAnalysis'
            and value['unwrap']=='pymol.cmd.pbc_unwrap' and type(value['runtimeSha256']) is str
            and HASH.fullmatch(value['runtimeSha256']),code)
    require(all(type(value[k]) is str and 0<len(value[k])<=128 and not any(ord(c)<32 or ord(c)==127 for c in value[k])
                for k in ('makeWholeVersion','unwrapVersion')),code)
    return value


def validate_manifest(value):
    code = "INVALID_PREVIEW_MANIFEST"
    fields="schemaVersion previewId source atomCount sourceFrameCount atomMapFile topologyFile frames files precision previewOnly"
    require(keys(value,fields) or keys(value,fields+' processing'),code)
    if 'processing' in value:validate_processing(value['processing'],code)
    validate_source(value["source"], code)
    require(value["schemaVersion"] == "bio.preview.v1" and identifier(value["previewId"])
            and integer(value["atomCount"], 1) and integer(value["sourceFrameCount"], 1)
            and type(value["precision"]) is int and value["precision"] == CONTRACT["precision"]
            and value["previewOnly"] is True, code)
    frames, files = value["frames"], value["files"]
    require(type(frames) is list and 1 <= len(frames) <= LIMITS["overviewFrames"]
            and type(files) is list and 2 <= len(files) <= LIMITS["overviewFrames"] + 2, code)
    by_id = {}
    for file in files:
        require(keys(file, "fileId byteSize sha256 kind previewFrameStart previewFrameCount"), code)
        require(identifier(file["fileId"]) and file["fileId"] not in by_id and integer(file["byteSize"], 1, LIMITS["cacheBytes"])
                and type(file["sha256"]) is str and HASH.fullmatch(file["sha256"]) and file["kind"] in {"topology", "atom-map", "xtc"}, code)
        if file["kind"] == "xtc":
            require(integer(file["previewFrameStart"], 0, len(frames) - 1)
                    and integer(file["previewFrameCount"], 1, LIMITS["windowFrames"])
                    and file["byteSize"] <= LIMITS["httpMaxBytes"]
                    and file["previewFrameCount"] * value["atomCount"] * 12 <= LIMITS["windowBytes"], code)
        else:
            require(file["previewFrameStart"] is None and file["previewFrameCount"] is None, code)
        by_id[file["fileId"]] = file
    require(by_id.get(value["topologyFile"], {}).get("kind") == "topology"
            and by_id.get(value["atomMapFile"], {}).get("kind") == "atom-map", code)
    previous_frame, previous_time = -1, -math.inf
    for index, frame in enumerate(frames):
        require(keys(frame, "sourceFrameIndex segmentId segmentFrameIndex timePs"), code)
        segment = by_id.get(frame["segmentId"], {}) if type(frame["segmentId"]) is str else {}
        require(integer(frame["sourceFrameIndex"], 0, value["sourceFrameCount"] - 1)
                and frame["sourceFrameIndex"] > previous_frame and segment.get("kind") == "xtc"
                and integer(frame["segmentFrameIndex"], 0, segment.get("previewFrameCount", 0) - 1)
                and index == segment["previewFrameStart"] + frame["segmentFrameIndex"]
                and type(frame["timePs"]) in {int, float} and math.isfinite(frame["timePs"])
                and frame["timePs"] >= 0 and frame["timePs"] > previous_time, code)
        previous_frame, previous_time = frame["sourceFrameIndex"], frame["timePs"]
    for file in files:
        if file["kind"] == "xtc":
            require(sum(f["segmentId"] == file["fileId"] for f in frames) == file["previewFrameCount"], code)
    if value["source"]["kind"] == "input": require(len(frames) == value["sourceFrameCount"] == 1, code)
    require(len(canonical(value)) <= 120 * 1024, code)
    return value
