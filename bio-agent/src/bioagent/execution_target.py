"""Operator-owned final compute identity; model protocols never select a host."""
from __future__ import annotations

import hashlib
import fcntl
import os
from pathlib import Path
import platform
import socket

from .errors import canonical, read_json, require, safe_file
from .provenance import PRODUCT_ROOT
from .schema import valid_hash, valid_id

FIELDS = {"schemaVersion", "tenantId", "nodeId", "targetId", "targetRevision", "hostIdentitySha256",
          "uid", "python", "projectsRoot", "releaseRoot", "resourcePolicy"}


def host_identity() -> str:
    for filename in ("/etc/machine-id", "/var/lib/dbus/machine-id"):
        path = Path(filename)
        if path.is_file():
            value = path.read_bytes().strip()
            if value:
                return hashlib.sha256(b"machine-id:" + value).hexdigest()
    return hashlib.sha256(canonical({"hostname": socket.gethostname(), "system": platform.system(),
                                     "machine": platform.machine()})).hexdigest()


def validate_target(value: dict) -> dict:
    require(type(value) is dict and set(value) == FIELDS and value["schemaVersion"] == "bio.execution-target.v1",
            "EXECUTION_TARGET_INVALID")
    require(all(valid_id(value[k]) for k in ("tenantId", "nodeId", "targetId"))
            and type(value["targetRevision"]) is int and value["targetRevision"] >= 1
            and valid_hash(value["hostIdentitySha256"]) and type(value["uid"]) is int and value["uid"] >= 0,
            "EXECUTION_TARGET_INVALID")
    for key in ("python", "projectsRoot", "releaseRoot"):
        item = value[key]
        require(type(item) is str and 0 < len(item) <= 1024 and Path(item).is_absolute()
                and str(Path(item)) == item and ".." not in Path(item).parts
                and not any(ord(c) < 32 or ord(c) == 127 for c in item) and item != "/", "EXECUTION_TARGET_PATH_INVALID")
    policy = value["resourcePolicy"]
    require(type(policy) is dict and set(policy) == {"quotaRoot", "cpuThreads", "maxConcurrentJobs", "gpuDevices", "maxOutputBytes"},
            "RESOURCE_POLICY_INVALID")
    require(type(policy["quotaRoot"]) is str and Path(policy["quotaRoot"]).is_absolute()
            and str(Path(policy["quotaRoot"])) == policy["quotaRoot"] and ".." not in Path(policy["quotaRoot"]).parts
            and len(policy["quotaRoot"]) <= 1024 and policy["quotaRoot"] != "/"
            and not any(ord(c) < 32 or ord(c) == 127 for c in policy["quotaRoot"]), "RESOURCE_POLICY_INVALID")
    require(type(policy["cpuThreads"]) is int and 1 <= policy["cpuThreads"] <= 4096
            and type(policy["maxConcurrentJobs"]) is int and 1 <= policy["maxConcurrentJobs"] <= 256
            and type(policy["maxOutputBytes"]) is int and 1048576 <= policy["maxOutputBytes"] <= 10995116277760,
            "RESOURCE_POLICY_INVALID")
    require(type(policy["gpuDevices"]) is list and len(policy["gpuDevices"]) <= 64
            and all(type(i) is int and 0 <= i <= 63 for i in policy["gpuDevices"])
            and len(set(policy["gpuDevices"])) == len(policy["gpuDevices"]), "RESOURCE_POLICY_INVALID")
    return value


def target_sha256(value: dict) -> str:
    return hashlib.sha256(canonical(validate_target(value))).hexdigest()


def local_target(project: Path, python: Path) -> dict:
    return validate_target({"schemaVersion": "bio.execution-target.v1", "tenantId": "local",
        "nodeId": "local", "targetId": "local-preview", "targetRevision": 1,
        "hostIdentitySha256": host_identity(), "uid": os.getuid(), "python": str(python.absolute()),
        "projectsRoot": str(project.parent.resolve()), "releaseRoot": str(PRODUCT_ROOT.resolve()),
        "resourcePolicy": {"quotaRoot": str(project.parent.resolve() / ".bio-resource-pool"),
            "cpuThreads": os.cpu_count() or 1, "maxConcurrentJobs": 1, "gpuDevices": [],
            "maxOutputBytes": 10995116277760}})


def verify_target(value: dict, project: Path, python: Path) -> dict:
    validate_target(value)
    require(value["hostIdentitySha256"] == host_identity() and value["uid"] == os.getuid(), "COMPUTE_IDENTITY_MISMATCH")
    require(Path(value["python"]).resolve() == python.resolve()
            and Path(value["releaseRoot"]).resolve() == PRODUCT_ROOT.resolve(), "COMPUTE_RUNTIME_MISMATCH")
    require(project.resolve().is_relative_to(Path(value["projectsRoot"]).resolve())
            and project.resolve() != Path(value["projectsRoot"]).resolve(), "COMPUTE_PROJECT_MISMATCH")
    return {"target": value, "executionTargetSha256": target_sha256(value), "identityVerified": True}


def bind_project(value: dict, project: Path, python: Path) -> dict:
    observed = verify_target(value, project, python)
    project.mkdir(parents=True, exist_ok=True, mode=0o700)
    marker = safe_file(project, "execution-target.json")
    descriptor = os.open(safe_file(project, "execution-target.lock"), os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        if marker.exists():
            require(read_json(marker) == observed, "PROJECT_TARGET_CHANGED")
        else:
            output_fd = os.open(marker, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
            with os.fdopen(output_fd, "wb") as output:
                output.write(canonical(observed)); output.flush(); os.fsync(output.fileno())
    finally:
        fcntl.flock(descriptor, fcntl.LOCK_UN); os.close(descriptor)
    return observed


def check_resources(target: dict, protocol: dict) -> None:
    policy = target["resourcePolicy"]
    for stage in protocol["stages"]:
        resources = stage["resources"]
        require(resources["cpuThreads"] <= policy["cpuThreads"]
                and resources.get("maxOutputBytes", 0) <= policy["maxOutputBytes"], "RESOURCE_POLICY_EXCEEDED")
        if resources["platform"] == "CUDA":
            require(resources["deviceIndex"] in policy["gpuDevices"], "GPU_NOT_AUTHORIZED")
