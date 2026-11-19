"""Read-only probes through the existing selected scientific interpreter."""
from pathlib import Path
from .errors import read_json, require
from .preflight import probe_environment
from .provenance import PRODUCT_ROOT
from .intent import PROFILES


def probe_capabilities(python: Path, *, cache_path: Path | None = None) -> dict:
    protocol = read_json(PRODUCT_ROOT / "validation/cases/protein-benzene.json")
    result = probe_environment(python, protocol, cache_path=cache_path)
    require(type(result.get("environment")) is dict and type(result.get("capabilities")) is dict,
            "ENVIRONMENT_PROBE_FAILED")
    return {**result, "supportedProfiles": sorted(PROFILES),
            "toolchainSha256": result["environment"].get("toolchainSha256"),
            "scope": "Read-only capability inventory; no Context or simulation created."}
