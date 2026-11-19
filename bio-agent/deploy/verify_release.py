"""Read-only release integrity and scientific provenance check."""
from pathlib import Path
import hashlib
import json
import sys

ROOT = Path(__file__).resolve().parents[1]


def verify(root: Path = ROOT) -> dict:
    manifest = json.loads((root / "release.json").read_text())
    records = manifest["files"]
    if manifest["schemaVersion"] != "bio.server-release.v1" or len({r["path"] for r in records}) != len(records):
        raise RuntimeError("INVALID_RELEASE_MANIFEST")
    for record in records:
        path = root / record["path"]
        if path.is_symlink() or path.resolve() != path.absolute() or not path.is_file():
            raise RuntimeError("INVALID_RELEASE_PATH")
        path.resolve().relative_to(root.resolve())
        data = path.read_bytes()
        if len(data) != record["byteSize"] or hashlib.sha256(data).hexdigest() != record["sha256"]:
            raise RuntimeError("RELEASE_CHANGED")
    encoded = json.dumps(records, sort_keys=True, separators=(",", ":")).encode()
    if hashlib.sha256(encoded).hexdigest() != manifest["contentSha256"]:
        raise RuntimeError("INVALID_RELEASE_MANIFEST")
    sys.path.insert(0, str(root / "src"))
    from bioagent.provenance import verify_runtime
    scientific = verify_runtime(root)
    extracted=json.loads((root/"provenance/visualization-source-snapshot.json").read_text())
    for record in extracted['files']:
        for field,digest in (('originalPath','sourceSha256'),('patchPath','patchSha256'),('derivedPath','derivedSha256')):
            path=root/record[field]
            if path.is_symlink() or hashlib.sha256(path.read_bytes()).hexdigest()!=record[digest]:raise RuntimeError('VISUALIZATION_PROVENANCE_CHANGED')
    packages=json.loads((root/'web/visualization/package.json').read_text())
    if packages['dependencies']['molstar']!='5.13.1' or packages['devDependencies']['vite']!='8.2.2':raise RuntimeError('VISUALIZATION_VERSION_CHANGED')
    required={'run_biopreview_direct.py','visualization/direct-contracts.json','src/bioagent/visualization/direct_reader.py',
              'src/bioagent/visualization/direct_permits.py','src/bioagent/visualization/published_reader.py',
              'local-connector/run.mjs','local-connector/server.mjs','web/visualization/src/direct-provider.mjs','web/visualization/src/source-catalog.mjs',
              'web/runtime/visualization/appearance.mjs','web/runtime/visualization/style-templates.mjs','web/visualization/src/appearance.mjs'}
    if not required <= {r['path'] for r in records}:raise RuntimeError('DIRECT_COMPONENT_MISSING')
    assets=json.loads((root/'web/visualization/dist/asset-manifest.json').read_text())
    for asset in assets['assets']:
        path=root/'web/visualization/dist'/asset['path']
        if path.is_symlink() or path.resolve().parent != (root/'web/visualization/dist').resolve() or hashlib.sha256(path.read_bytes()).hexdigest()!=asset['sha256']:raise RuntimeError('VISUALIZATION_ASSET_CHANGED')
    return {"releaseId": manifest["releaseId"], "fileCount": len(records),
            "contentSha256": manifest["contentSha256"], "scientificRuntimeSha256": scientific["runtimeSha256"],
            "integrity": "PASS", "simulationExecuted": False}


if __name__ == "__main__":
    print(json.dumps(verify(), sort_keys=True))
