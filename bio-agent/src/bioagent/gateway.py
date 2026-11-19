"""Fixed SSH byte relay on an ingress node; imports no scientific packages."""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import shlex
import sys
from .errors import read_json, require
from .execution_target import validate_target


def command(connection: dict, project: Path, target: dict) -> list[str]:
    fields = {"schemaVersion", "hostAlias", "configFile", "knownHosts", "projectsRoot", "releaseRoot", "python"}
    require(type(connection) is dict and set(connection) == fields
            and connection["schemaVersion"] == "bio.ssh-connection.v1", "GATEWAY_CONNECTION_INVALID")
    import re
    require(type(connection["hostAlias"]) is str and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", connection["hostAlias"]) is not None,
            "GATEWAY_ALIAS_INVALID")
    validate_target(target)
    for key in ("projectsRoot", "releaseRoot", "python"):
        require(connection[key] == target[key], "GATEWAY_TARGET_MISMATCH")
    require(project.is_relative_to(Path(connection["projectsRoot"])), "GATEWAY_PROJECT_INVALID")
    relative = project.relative_to(Path(connection["projectsRoot"]))
    require(len(relative.parts) == 1 and re.fullmatch(r"[a-f0-9]{64}", str(relative)) is not None, "GATEWAY_PROJECT_INVALID")
    for key in ("configFile", "knownHosts"):
        path = Path(connection[key])
        require(path.is_absolute() and path.resolve() == path and not path.is_symlink() and path.is_file(), "GATEWAY_AUTH_FILE_INVALID")
        stat = path.stat()
        require(stat.st_uid in {0, os.getuid()} and 0 < stat.st_size <= 1048576
                and stat.st_mode & (0o077 if key == "configFile" else 0o022) == 0, "GATEWAY_AUTH_FILE_INVALID")
    argv = [connection["python"], "-I", "-B", str(Path(connection["releaseRoot"]) / "run_biobridge.py"),
            "--project", str(project), "--python", connection["python"], "--execution-target", json.dumps(target)]
    return ["/usr/bin/ssh", "-F", connection["configFile"], "-T", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=yes",
        "-o", "UserKnownHostsFile=" + connection["knownHosts"], "-o", "GlobalKnownHostsFile=/dev/null",
        "-o", "ForwardAgent=no", "-o", "IdentityAgent=none", "-o", "ClearAllForwardings=yes", "-o", "ConnectTimeout=8",
        "--", connection["hostAlias"], shlex.join(argv)]


def main() -> int:
    parser = argparse.ArgumentParser(description="Fixed Bio SSH ingress relay")
    parser.add_argument("--connection", type=Path, required=True)
    parser.add_argument("--project", type=Path, required=True)
    parser.add_argument("--execution-target", required=True)
    args = parser.parse_args()
    try:
        require(args.connection.is_absolute() and args.connection.resolve() == args.connection and args.connection.is_file(), "GATEWAY_CONNECTION_INVALID")
        metadata = args.connection.stat()
        require(metadata.st_uid in {0, os.getuid()} and metadata.st_mode & 0o022 == 0
                and 0 < metadata.st_size <= 65536, "GATEWAY_CONNECTION_INVALID")
        argv = command(read_json(args.connection), args.project, json.loads(args.execution_target))
        # SSH owns stdin/stdout from here. The node cannot interpret or execute MD.
        os.execve(argv[0], argv, {"PATH": "/usr/bin:/bin"})
    except Exception:
        sys.stderr.write("BIO_GATEWAY_UNAVAILABLE\n")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
