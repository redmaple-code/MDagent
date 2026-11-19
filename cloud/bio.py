"""Optional Bio tenant composition and bounded access gate; no compute on this host."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys

from fastapi import HTTPException, Request

import accounts
import nodes
import paths

BIO_ROOT = Path(__file__).resolve().parents[1] / "bio-agent"
sys.path.insert(0, str(BIO_ROOT / "src"))
from bioagent.errors import canonical  # noqa: E402
from bioagent.execution_target import validate_target, target_sha256  # noqa: E402

MODEL_ACTIONS = frozenset(json.loads((BIO_ROOT / "agent/contracts.json").read_text())["tools"])
HUMAN_ACTIONS = frozenset("human:" + name for name in (
    "context", "inputs", "capabilities", "list", "get", "review", "approve", "submit",
    "cancel", "recover", "upload-begin", "upload-chunk", "upload-finish", "artifacts",
    "download-begin", "download-chunk", "download-finish", "resource-preference"))
PREVIEW_CONTROL_ACTIONS = frozenset('preview:'+name for name in ('describe','prepare','status','window','cancel'))
# Legacy bindings remain parseable; the preview facade never emits relay coordinates.
PREVIEW_ACTIONS = PREVIEW_CONTROL_ACTIONS | {'preview:read'}
VIEW_ACTIONS = frozenset({'bio_view','bio_observe',*('view:'+name for name in ('attach','poll','ack','detach'))})
DIRECT_ACTIONS = frozenset('preview:'+name for name in ('direct-open','direct-permit','direct-close'))
ACTIONS = MODEL_ACTIONS | HUMAN_ACTIONS | PREVIEW_ACTIONS | VIEW_ACTIONS | DIRECT_ACTIONS | {"connect", "advance"}
_ID = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,127}$")
_USER = re.compile(r"^[a-z][a-z0-9_-]{2,15}$")


def _absolute(value):
    return (isinstance(value, str) and 0 < len(value) <= 1024
            and Path(value).is_absolute() and str(Path(value)) == value
            and ".." not in Path(value).parts and value != "/"
            and not any(ord(c) < 32 or ord(c) == 127 for c in value))


def _root():
    return Path(os.environ.get("MDAGENT_BIO_CONFIG_DIR",
                               str(Path(paths.accounts_dir()) / "bio-targets")))


def load_binding(username):
    """Read an operator-owned registry, never a tenant-supplied target path."""
    if not isinstance(username, str) or not _USER.fullmatch(username):
        raise ValueError("BIO_TENANT_INVALID")
    root = _root()
    if not _absolute(str(root)):
        raise ValueError("BIO_CONFIGURATION_INVALID")
    if not root.exists():
        return None
    try:
        # Registry parents must be owned by the service and never writable by a tenant.
        for parent in [root, *root.parents]:
            observed = parent.lstat()
            if (not stat.S_ISDIR(observed.st_mode) or parent.resolve() != parent
                    or observed.st_uid != os.geteuid() or observed.st_mode & 0o022):
                raise ValueError("BIO_CONFIGURATION_INVALID")
            if parent == Path(paths.accounts_dir()).resolve() or parent == Path("/"):
                break
        file = root / (username + ".json")
        try:
            descriptor = os.open(file, os.O_RDONLY | os.O_NOFOLLOW)
        except FileNotFoundError:
            return None
        with os.fdopen(descriptor, "rb") as source:
            observed = os.fstat(source.fileno())
            if (not stat.S_ISREG(observed.st_mode) or observed.st_uid != os.geteuid()
                    or observed.st_mode & 0o077 or observed.st_size > 65536):
                raise ValueError("BIO_CONFIGURATION_INVALID")
            config = json.loads(source.read(65537))
        fields = {"schemaVersion", "executionTarget", "mode", "allowedActions"}
        if isinstance(config, dict) and config.get("mode") == "node-ssh":
            fields |= {"gatewayPython", "gatewayReleaseRoot", "gatewayConnectionFile"}
        if (not isinstance(config, dict) or set(config) - fields - {"rosterRoots"}
                or not fields <= set(config) or config["schemaVersion"] != "bio.platform-binding.v1"
                or config["mode"] not in {"node-world", "node-ssh"}):
            raise ValueError("BIO_CONFIGURATION_INVALID")
        target = validate_target(config["executionTarget"])
        if (target["tenantId"] != username or not re.fullmatch(r"n_[a-z0-9_-]{1,100}", target["nodeId"])
                or not isinstance(config["allowedActions"], list)
                or any(not isinstance(item, str) or item not in ACTIONS for item in config["allowedActions"])
                or len(set(config["allowedActions"])) != len(config["allowedActions"])):
            raise ValueError("BIO_CONFIGURATION_INVALID")
        if config["mode"] == "node-ssh" and any(not _absolute(config[key]) for key in (
                "gatewayPython", "gatewayReleaseRoot", "gatewayConnectionFile")):
            raise ValueError("BIO_CONFIGURATION_INVALID")
        if not isinstance(config.get("rosterRoots", []), list):
            raise ValueError("BIO_CONFIGURATION_INVALID")
        for root_ in config.get("rosterRoots", []):
            if (not isinstance(root_, dict) or set(root_) != {"path", "trust"}
                    or not _absolute(root_["path"]) or root_["trust"] not in {"system", "user"}):
                raise ValueError("BIO_CONFIGURATION_INVALID")
        return config
    except Exception as error:
        raise ValueError("BIO_CONFIGURATION_INVALID") from error


def signature(username):
    config = load_binding(username)
    return hashlib.sha256(canonical(config)).hexdigest() if config is not None else None


def preferred_node(username, online):
    config = load_binding(username)
    if config and config["executionTarget"]["nodeId"] in online:
        return config["executionTarget"]["nodeId"]
    return None


def authorize(value, world_token, relay):
    if not world_token:
        raise HTTPException(401, "BIO_WORLD_TOKEN_REQUIRED")
    record = accounts.verify_world_token(world_token)
    if not record:
        raise HTTPException(401, "BIO_WORLD_TOKEN_REQUIRED")
    username, node_id = record["username"], record["node_id"]
    user = accounts.get(username)
    if not user or user.get("status") != "active":
        raise HTTPException(403, "BIO_TENANT_ACCESS_DENIED")
    node = nodes.get(node_id)
    if not node or node.get("username") != username or node.get("revoked"):
        raise HTTPException(403, "BIO_NODE_ACCESS_DENIED")
    try:
        config = load_binding(username)
    except ValueError:
        raise HTTPException(503, "BIO_CONFIGURATION_INVALID")
    if config is None:
        raise HTTPException(403, "BIO_NOT_ENABLED")
    if not isinstance(value, dict):
        raise HTTPException(400, "BIO_REQUEST_INVALID")
    fields = {"schemaVersion", "tenantId", "nodeId", "executionTargetSha256", "action", "sessionId"}
    if (not fields <= set(value) or set(value) - fields - {"workflowId", "revision"}
            or value["schemaVersion"] != "bio.platform-access.v1"
            or not isinstance(value["action"], str) or value["action"] not in ACTIONS
            or not isinstance(value["sessionId"], str) or not 1 <= len(value["sessionId"]) <= 256
            or any(ord(c) < 32 or ord(c) == 127 for c in value["sessionId"])
            or "workflowId" in value and (not isinstance(value["workflowId"], str) or not _ID.fullmatch(value["workflowId"]))
            or "revision" in value and (type(value["revision"]) is not int or value["revision"] < 1)):
        raise HTTPException(400, "BIO_REQUEST_INVALID")
    target = config["executionTarget"]
    if (value["tenantId"] != username or value["nodeId"] != node_id or target["nodeId"] != node_id
            or value["executionTargetSha256"] != target_sha256(target)
            or value["action"] not in config["allowedActions"]):
        raise HTTPException(403, "BIO_PLATFORM_ACCESS_DENIED")
    if not relay.status(node_id)["online"]:
        raise HTTPException(503, "BIO_NODE_OFFLINE")
    accounts.touch_world_token(world_token)
    return {**value, "allowed": True}


def mount(app, relay):
    @app.post("/v1/bio/access")
    async def bio_access(request: Request):
        chunks, size = [], 0
        async for chunk in request.stream():
            size += len(chunk)
            if size > 8192:
                raise HTTPException(413, "BIO_REQUEST_TOO_LARGE")
            chunks.append(chunk)
        raw = b"".join(chunks)
        try:
            value = json.loads(raw)
        except ValueError:
            raise HTTPException(400, "BIO_REQUEST_INVALID")
        return authorize(value, request.headers.get("x-world-token") or "", relay)


def _public_write(path, value):
    """Immutable public configuration only; root-owned parents stay outside tenant homes."""
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o755)
    content = canonical(value) + b"\n"
    if path.exists():
        if path.is_symlink() or path.read_bytes() != content:
            raise ValueError("BIO_PROFILE_CONFLICT")
        return
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o644)
    with os.fdopen(fd, "wb") as stream:
        stream.write(content)


def overlay_entries(username, node_id):
    """Generate a source-only preset for an explicitly registered matching target."""
    config = load_binding(username)
    if config is None or config["executionTarget"]["nodeId"] != node_id:
        return []
    node = nodes.get(node_id)
    if not node or node.get("username") != username or node.get("revoked"):
        raise ValueError("BIO_NODE_ACCESS_DENIED")
    digest = hashlib.sha256(canonical(config)).hexdigest()
    base = Path(paths.accounts_dir()).parent / "bio-profiles" / username / digest
    base.mkdir(parents=True, exist_ok=True, mode=0o755)
    if any(parent.is_symlink() or parent.stat().st_uid != os.geteuid() or parent.stat().st_mode & 0o022
           for parent in [base, base.parent, base.parent.parent]):
        raise ValueError("BIO_PROFILE_DIRECTORY_INVALID")
    target_file = base / "target.json"
    _public_write(target_file, config["executionTarget"])
    preset_config = {"projectsRoot": str(Path(paths.user_data_dir(username)) / "bio-control"),
                     "mode": config["mode"], "nodeId": node_id, "executionTargetFile": str(target_file),
                     "gateMode": "platform", "platformGateModule": str(BIO_ROOT / "web/runtime/platform-gate.mjs")}
    preset_config['previewEnabled']=PREVIEW_CONTROL_ACTIONS <= set(config['allowedActions'])
    preset_config['directEnabled']=preset_config['previewEnabled'] and DIRECT_ACTIONS <= set(config['allowedActions'])
    preset_config['viewEnabled']=preset_config['previewEnabled'] and VIEW_ACTIONS <= set(config['allowedActions'])
    if config["mode"] == "node-ssh":
        preset_config.update({key: config[key] for key in ("gatewayPython", "gatewayReleaseRoot", "gatewayConnectionFile")})
    presets = base / "presets"
    _public_write(presets / "bio/preset.yml", {"name": "Bio 生物分子模拟", "description": "在固定的 SSH 计算目标构建、生成拓扑并运行 OpenMM。"})
    _public_write(presets / "bio/agent.cordis.yml", [{"id": "bio-tools", "name": "dsh-bio-agent", "config": preset_config}])
    # Preset module resolution also works outside the tenant's writable DSH_HOME.
    modules = base / "node_modules"
    modules.mkdir(exist_ok=True, mode=0o755)
    for name, folder in (("dsh-bio-agent", "dsh-bio-agent"), ("bio-web-tool-cards", "dsh-bio-ui")):
        destination = Path(paths.code_dir()) / "dsh-plugins" / folder
        if not destination.is_dir():
            raise ValueError("BIO_PLUGIN_SOURCE_REQUIRED")
        link = modules / name
        if not os.path.lexists(link):
            link.symlink_to(destination)
        elif not link.is_symlink() or link.resolve() != destination.resolve():
            raise ValueError("BIO_PROFILE_CONFLICT")
    # Do not override the existing default agent or model/provider settings.
    return [{"id": "agent-presets", "config": {"roots": [*config.get("rosterRoots", []),
                                                              {"path": str(presets), "trust": "system"}]}},
            {"insert": [{"id": "bio-web-tool-cards", "name": "bio-web-tool-cards"}]}]
