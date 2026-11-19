"""Allowlisted source-only server bundle; no environments, credentials or results."""
from pathlib import Path
import datetime
import hashlib
import io
import json
import re
import tarfile

ROOT = Path(__file__).resolve().parents[1]


def contains_credential(content: bytes) -> bool:
    # "task-..." provenance identifiers are words, not standalone API keys.
    return re.search(
        rb"(?<![A-Za-z0-9_])sk-[A-Za-z0-9_-]{24,}|-----BEGIN (?:RSA |OPENSSH |EC )?PRIVATE KEY-----",
        content,
    ) is not None


def build() -> dict:
    paths = [p.relative_to(ROOT).as_posix() for p in sorted((ROOT / "src/bioagent").glob("*.py"))]
    snapshot = json.loads((ROOT / "provenance/source-snapshot.json").read_text())
    paths += ["runtime/ksh_openmm/" + item["path"] for item in snapshot["runtimeFiles"]]
    paths += ["run_biobridge.py", "run_bioagent.py", "run_biogateway.py", "run_biotarget.py", "provenance/source-snapshot.json",
              "validation/cases/protein-benzene.json", "validation/chat-cases/intents/water.json",
              "agent/bridge-client.mjs", "agent/remote-bridge.mjs", "agent/node-world-bridge.mjs", "agent/execution-target.mjs", "agent/client-receipts.mjs", "agent/tools.mjs",
              "agent/contracts.json", "agent/sdk.mjs", "agent/domain-prompt.md",
              "web/runtime/bio-plugin.mjs", "web/runtime/bio-runtime.mjs", "web/runtime/server-plugin.mjs", "web/runtime/server-apply.mjs", "web/runtime/server-config.mjs", "web/runtime/wire-contracts.mjs",
              "web/runtime/tool-presentation.mjs", "web/runtime/human-api.mjs", "web/runtime/execution-gate.mjs", "web/runtime/platform-gate.mjs", "web/runtime/frozen-web.mjs", "web/runtime/profile-watch.mjs",
              "web/runtime/ui-cards/package.json", "web/runtime/ui-cards/index.mjs",
              "web/runtime/ui-cards/client.js", "web/runtime/ui-cards/client.source.js",
              "deploy/compiler.mjs", "deploy/server-launch.mjs", "deploy/verify_release.py", "deploy/smoke.mjs",
              "deploy/tenant-profile.mjs", "deploy/README.md", "deploy/connection.example.json", "deploy/execution-target.example.json", "deploy/platform-binding.example.json",
              "docs/remote-workflow.md"]
    paths += ["run_biopreview.py", "run_biopreview_direct.py", "visualization/direct-contracts.json", "visualization/contracts.json", "provenance/visualization-source-snapshot.json",
              "provenance/visualization-notices.txt", "docs/web-visualization.md"]
    paths += ["src/bioagent/visualization/" + name + ".py" for name in (
        "__init__", "contracts", "sources", "preview_plan", "preview_files", "pbc", "pymol_pbc_worker", "jobs", "worker", "service", "direct_contracts", "published_reader", "direct_permits", "direct_reader")]
    paths += ["web/runtime/visualization/" + name for name in (
        "appearance.mjs", "style-templates.mjs", "contracts.mjs", "direct-contracts.mjs", "preview-api.mjs", "preview-relay.mjs", "view-api.mjs", "view-runtime.mjs", "view-state.mjs", "view-contracts.json")]
    paths += ["web/visualization/" + name for name in ("package.json", "pnpm-lock.yaml", "vite.config.mjs")]
    paths += ["web/visualization/src/" + name for name in (
        "renderer.mjs", "appearance.mjs", "theme.mjs", "floating-panel.mjs", "player.mjs", "transport.mjs", "frame-binding.mjs", "dock.mjs", "view-bridge.mjs", "index.mjs", "style.css", "local-connector.mjs", "direct-provider.mjs", "relay-provider.mjs", "source-catalog.mjs", "compute-progress.mjs", "compute-progress-panel.mjs")]
    paths += ["local-connector/" + name for name in ("config.mjs", "ssh-protocol.mjs", "ssh-reader.mjs", "pairing.mjs", "scheduler.mjs", "server.mjs", "run.mjs", "config.example.json")]
    paths += ["docs/local-preview-connector.md"]
    extracted = json.loads((ROOT / "provenance/visualization-source-snapshot.json").read_text())
    for record in extracted["files"]:paths += [record["originalPath"], record["patchPath"]]
    assets = json.loads((ROOT / "web/visualization/dist/asset-manifest.json").read_text())
    paths += ["web/visualization/dist/asset-manifest.json"]
    for asset in assets["assets"]:
        name=asset["path"]
        if not re.fullmatch(r"[a-zA-Z0-9_.-]+\.(js|css)",name):raise RuntimeError("INVALID_VISUALIZATION_ASSET")
        paths += ["web/visualization/dist/" + name + suffix for suffix in ("", ".gz", ".br")]
    for item in snapshot["patches"]:
        paths += [item["originalPath"], item["patchPath"]]
    paths = sorted(paths)
    if len(set(paths)) != len(paths):
        raise RuntimeError("DUPLICATE_BUNDLE_PATH")
    data, records = {}, []
    for name in paths:
        path = ROOT / name
        if path.is_symlink() or path.resolve() != path.absolute():
            raise RuntimeError("BUNDLE_SOURCE_SYMLINK")
        content = path.read_bytes()
        if contains_credential(content):
            raise RuntimeError("BUNDLE_REQUIRES_CREDENTIAL_REDACTION")
        data[name] = content
        records.append({"path": name, "sha256": hashlib.sha256(content).hexdigest(), "byteSize": len(content)})
    digest = hashlib.sha256(json.dumps(records, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    release_id = "bio-" + datetime.date.today().strftime("%Y%m%d") + "-" + digest[:12]
    manifest = {"schemaVersion": "bio.server-release.v1", "releaseId": release_id,
                "contentSha256": digest, "files": records, "target": "DSH control host; existing remote-world/SSH compute host; TypeBox 1.1.38",
                "deploymentStatus": "CANDIDATE_NOT_DEPLOYED", "remoteTransportAcceptance": "OFFLINE_ONLY",
                "containsCredentials": False, "containsEnvironment": False, "containsScientificResults": False,
                "scientificExecutionAuthorized": False}
    data["release.json"] = (json.dumps(manifest, indent=2, ensure_ascii=False) + "\n").encode()
    directory = ROOT / "deploy/dist"
    directory.mkdir(parents=True, exist_ok=True)
    archive = directory / (release_id + ".tar.gz")
    with archive.open("xb") as output:
        with tarfile.open(fileobj=output, mode="w:gz") as tar:
            for name, content in sorted(data.items()):
                entry = tarfile.TarInfo(name)
                entry.size, entry.mode, entry.mtime = len(content), 0o644, 0
                entry.uid = entry.gid = 0
                tar.addfile(entry, io.BytesIO(content))
    receipt = {"releaseId": release_id, "archive": str(archive), "archiveSha256": hashlib.sha256(archive.read_bytes()).hexdigest(),
               "archiveBytes": archive.stat().st_size, "sourceBytes": sum(len(value) for value in data.values()),
               "contentSha256": digest, "fileCount": len(records), "remoteMutations": 0}
    with (directory / (release_id + ".json")).open("x") as output:
        output.write(json.dumps(receipt, indent=2) + "\n")
    return receipt


if __name__ == "__main__":
    print(json.dumps(build()))
