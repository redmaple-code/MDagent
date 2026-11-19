#!/usr/bin/env python3
"""Build a transferable Ubuntu source bundle, excluding all runtime material.

The companion kit is checked statically, then its allowed original code bytes
are packaged. The recipient's installer prepares that code for their paths.
No kit module, model request, service or scientific job is executed here.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import subprocess
import sys
import tarfile
import tempfile

import prepare_kit


ROOT_NAME = "MDagent-Ubuntu"
MANIFEST_NAME = ROOT_NAME + "/install-bundle-manifest.json"
REPOSITORY_ASSET_LIMIT = 80 * 1024 * 1024
PUBLIC_CORPUS_FILES = {"external_corpus/gromacs_answered.jsonl", "external_corpus/lammps_answered.jsonl"}
REQUIRED_REPO_FILES = {
    "install.sh", "requirements-install.txt", "package.json", "pnpm-lock.yaml",
    "pnpm-workspace.yaml", "run-dsh.sh", "scripts/setup.sh",
    "scripts/prepare_kit.py", "scripts/install_runtime.sh", "scripts/install_service.py",
    "scripts/install_config.py", "scripts/install_check.py",
    "scripts/read_dsh_defaults.py", "scripts/.credentials.yaml.example",
}
FORBIDDEN_PARTS = {".git", ".ssh", ".aws", "node_modules", "__pycache__", ".venv", "venv", "runtime",
                   "logs", "sessions", "conversation", "conversations", "state", "scratch",
                   "projects", "mdsim-data", "credentials", "secrets", "keys"}
FORBIDDEN_NAMES = {"md_config.json", "install.json", "install.env", "status_bus.json",
                   "bus_journal.jsonl", "gpt2_transcript.txt", "sim_config.json"}


class BundleError(Exception):
    pass


def checked_relative(value: str) -> str:
    """Validate the unnormalized Git path, before any filesystem read."""
    if not value or "\\" in value or "\x00" in value or any(c in value for c in "\r\n"):
        raise BundleError("UNSAFE_REPOSITORY_PATH")
    raw_parts = value.split("/")
    if any(part in {"", ".", ".."} or ":" in part for part in raw_parts) or PurePosixPath(value).is_absolute():
        raise BundleError("UNSAFE_REPOSITORY_PATH")
    return value


def repository_files(repository: Path) -> list[str]:
    try:
        result = subprocess.run(["git", "-C", str(repository), "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
                                capture_output=True, check=False)
    except OSError:
        raise BundleError("GIT_FILE_LIST_UNAVAILABLE") from None
    if result.returncode:
        raise BundleError("GIT_FILE_LIST_FAILED")
    try:
        return sorted({checked_relative(value.decode("utf-8")) for value in result.stdout.split(b"\x00") if value})
    except UnicodeError:
        raise BundleError("REPOSITORY_FILENAME_NOT_UTF8") from None


def allowed_repository_file(relative: str) -> bool:
    parts = PurePosixPath(relative).parts
    lowered = [part.lower() for part in parts]
    name = lowered[-1]
    if any(part in FORBIDDEN_PARTS for part in lowered):
        return False
    if name in FORBIDDEN_NAMES or name.startswith(".mdagent-bundle-"):
        return False
    # Only the known blank credential template is part of the source contract.
    if relative == "scripts/.credentials.yaml.example":
        return True
    if name == ".env" or name.startswith(".env.") or name.startswith(".credentials"):
        return False
    if name.startswith((".v3.", ".routine_", ".token_", ".task_")):
        return False
    if relative in PUBLIC_CORPUS_FILES:
        return True
    if any(marker in name for marker in ("transcript", "conversation", "strategy_db", ".bak", ".backup")):
        return False
    if name in {"id_rsa", "id_dsa", "id_ecdsa", "id_ed25519"}:
        return False
    if name.endswith((".pyc", ".pyo", ".log", ".out", ".tmp", ".jsonl", ".db", ".sqlite", ".sqlite3",
                      ".tar", ".tar.gz", ".tgz", ".zip", ".7z",
                      ".key", ".pem", ".p12", ".pfx", ".jks")):
        return False
    return True


def collect_repository(repository: Path, *, include_public_corpus: bool = True) -> dict[str, bytes]:
    prepare_kit.reject_links(repository)
    files = {}
    for relative in repository_files(repository):
        relative = checked_relative(relative)
        if relative in PUBLIC_CORPUS_FILES and not include_public_corpus:
            continue
        if allowed_repository_file(relative):
            data = prepare_kit.read_code(repository / relative, relative, limit=REPOSITORY_ASSET_LIMIT)
            if relative in PUBLIC_CORPUS_FILES:
                prepare_kit.check_credentials(data.decode("utf-8-sig"), relative)
            files[relative] = data
    missing = sorted(REQUIRED_REPO_FILES - files.keys())
    if missing:
        raise BundleError("MISSING_INSTALLER_SOURCE: " + missing[0])
    # Never distribute a locally filled credential template, even if tracked.
    template = files["scripts/.credentials.yaml.example"].decode("utf-8-sig")
    for line in template.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        key, separator, value = stripped.partition(":")
        value = value.split("#", 1)[0].strip()
        if not separator or value not in {"", "''", '""', "null", "~"}:
            raise BundleError("CREDENTIAL_TEMPLATE_NOT_BLANK: scripts/.credentials.yaml.example")
    return files


def assemble(repository: Path, kit_source: Path, *, include_public_corpus: bool = True) -> tuple[dict[str, bytes], dict]:
    repository = Path(os.path.abspath(repository))
    kit_source = Path(os.path.abspath(kit_source))
    repo_files = collect_repository(repository, include_public_corpus=include_public_corpus)
    # Validate the kit against a hypothetical recipient layout, without writing
    # anything and without using its source configuration/state as input.
    _, kit_check = prepare_kit.build_prepared(kit_source, Path("/mdagent-bundle-check/runtime/kit"),
                                            Path("/mdagent-bundle-check/projects"))
    kit_files = prepare_kit.collect_source(kit_source)
    expected_source = {record["path"]: record["source_sha256"] for record in kit_check["files"]}
    if expected_source != {relative: prepare_kit.digest(data) for relative, data in kit_files.items()}:
        raise BundleError("KIT_SOURCE_CHANGED_DURING_CHECK")
    members = {}
    for prefix, files in ((ROOT_NAME + "/MDagent/", repo_files),
                          (ROOT_NAME + "/agent_deploy_kit/", kit_files)):
        for relative, data in sorted(files.items()):
            if relative.endswith(".sh"):
                # Windows working trees may use CRLF; bash scripts must retain
                # executable Unix line endings after transfer to Ubuntu.
                data = data.replace(b"\r\n", b"\n").replace(b"\r", b"\n")
            members[prefix + checked_relative(relative)] = data
    instructions = ("# Ubuntu installation source bundle\n\n"
                    "After extracting, run `cd MDagent-Ubuntu/MDagent` and `bash install.sh`. "
                    "The installer uses the sibling `agent_deploy_kit` by default.\n\n"
                    "This bundle carries code and blank installation templates. Credentials, "
                    "previous sessions, agent state and simulation results are not included. "
                    "Configure a model and verify the selected simulation engine on the recipient machine.\n\n")
    if include_public_corpus:
        instructions += ("Whitelisted public error-reference corpora are included when present in the repository. "
                         "Preserve their URLs, authors and `external_corpus/README.md` license terms. "
                         "The records are retrieval references and remain unverified for a recipient's simulation case.\n")
    else:
        instructions += ("Public JSONL error-reference corpora were explicitly omitted with `--without-public-corpus`. "
                         "The repository's `external_corpus/corpus_lookup.py` and licensing README remain included. "
                         "Corpus search is optional and requires separately supplied, safely distributable "
                         "`gromacs_answered.jsonl` / `lammps_answered.jsonl` in `MDagent/external_corpus/`, "
                         "beside `corpus_lookup.py`; preserve upstream URL/author attribution and license terms.\n")
    members[ROOT_NAME + "/INSTALL-BUNDLE.md"] = instructions.encode("utf-8")
    records = [{"path": name, "sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)}
               for name, data in sorted(members.items())]
    manifest = {"format": "mdagent.ubuntu-code-bundle.v1", "files": records,
                "installer": ROOT_NAME + "/MDagent/install.sh",
                "transforms": {"shell_scripts": "CRLF/CR normalized to LF"},
                "omitted_public_corpus": sorted(PUBLIC_CORPUS_FILES) if not include_public_corpus else [],
                "kit_static_checks": ["metadata-no-links", "credential-literals", "local-module-and-asset-dependencies",
                                      "claude-interface-cli", "python-compile"],
                "scope": "source code, installation templates and whitelisted public reference corpus; no user runtime data or external service validation"}
    members[MANIFEST_NAME] = (json.dumps(manifest, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    return members, manifest


def build(repository: Path, kit_source: Path, output: Path, *, check_only: bool = False,
          include_public_corpus: bool = True) -> dict:
    output = Path(os.path.abspath(output))
    prepare_kit.reject_links(output)
    if output.exists():
        raise BundleError("OUTPUT_EXISTS_CHOOSE_A_NEW_FILENAME")
    if not output.name.endswith(".tar.gz"):
        raise BundleError("OUTPUT_MUST_END_IN_TAR_GZ")
    members, manifest = assemble(repository, kit_source, include_public_corpus=include_public_corpus)
    report = {"status": "checked" if check_only else "built", "files": len(manifest["files"]),
              "kit_files": sum(name.startswith(ROOT_NAME + "/agent_deploy_kit/") for name in members)}
    if check_only:
        return report
    output.parent.mkdir(parents=True, exist_ok=True)
    prepare_kit.reject_links(output.parent)
    descriptor, temporary = tempfile.mkstemp(prefix=".mdagent-bundle-", suffix=".tmp", dir=output.parent)
    try:
        with os.fdopen(descriptor, "wb") as raw:
            with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as compressed:
                with tarfile.open(mode="w", fileobj=compressed, format=tarfile.PAX_FORMAT) as archive:
                    for name, data in sorted(members.items()):
                        info = tarfile.TarInfo(name)
                        info.size = len(data)
                        info.mode = 0o755 if name.endswith(".sh") else 0o644
                        info.mtime = info.uid = info.gid = 0
                        info.uname = info.gname = ""
                        archive.addfile(info, io.BytesIO(data))
            raw.flush()
            os.fsync(raw.fileno())
        # A hard link publishes the complete archive atomically and fails if the
        # requested output appeared meanwhile. Existing outputs are never replaced.
        os.link(temporary, output)
    except FileExistsError:
        raise BundleError("OUTPUT_EXISTS_CHOOSE_A_NEW_FILENAME") from None
    finally:
        os.unlink(temporary)
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--kit", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--check-only", action="store_true")
    parser.add_argument("--without-public-corpus", action="store_true",
                        help="Explicitly omit public JSONL corpora; retain the query program and licensing documentation")
    args = parser.parse_args(argv)
    try:
        result = build(Path(__file__).resolve().parents[1], args.kit, args.output, check_only=args.check_only,
                       include_public_corpus=not args.without_public_corpus)
    except (BundleError, prepare_kit.PreparationError) as error:
        print("install-bundle: " + str(error), file=sys.stderr)
        return 2
    except (OSError, ValueError, UnicodeError):
        print("install-bundle: FILESYSTEM_OR_ENCODING_ERROR", file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
