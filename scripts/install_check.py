#!/usr/bin/env python3
"""Read-only local readiness checks. Never request a model answer or run physics."""
import argparse
import importlib.util
import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import urllib.error
import urllib.request


def kit_integrity(kit):
    try:
        manifest = kit / ".mdagent-kit-manifest.json"
        if manifest.is_symlink():
            return False
        records = json.loads(manifest.read_text(encoding="utf-8"))
        if records.get("format") != "mdagent.prepared-kit.v1" or records.get("destination") != str(kit):
            return False
        files = records.get("files")
        if not isinstance(files, list) or not files:
            return False
        for record in files:
            relative = record["path"]
            if not isinstance(relative, str) or "\\" in relative or ":" in relative:
                return False
            parts = relative.split("/")
            if any(part in ("", ".", "..") for part in parts):
                return False
            path = kit.joinpath(*parts)
            if any(part.is_symlink() for part in (path, *path.parents)):
                return False
            if hashlib.sha256(path.read_bytes()).hexdigest() != record["prepared_sha256"]:
                return False
        return True
    except (OSError, ValueError, KeyError, TypeError):
        return False


def command_ok(argv, env, timeout=15, contains=None):
    try:
        result = subprocess.run(argv, env=env, capture_output=True, text=True, timeout=timeout)
        return result.returncode == 0 and (contains is None or contains in (result.stdout + result.stderr))
    except (OSError, subprocess.TimeoutExpired):
        return False


def http_ok(url):
    # Ignore proxy environment variables for localhost readiness probes.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(url, timeout=1) as response:
            return response.status == 200
    except (OSError, urllib.error.URLError):
        return False


def inspect(data, services=False):
    data = Path(data).expanduser().absolute()
    report = {"checks": {}, "model_response_verified": False,
              "scientific_job_verified": False, "ssh_submission_verified": False}
    checks = report["checks"]
    metadata_path = data / "install.json"
    if metadata_path.is_symlink() or not metadata_path.is_file():
        checks["installation_metadata"] = False
        return report
    try:
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        code, kit = Path(metadata["code_root"]), Path(metadata["kit"])
        runtime = data / "runtime"
        env = dict(os.environ)
        env.update(MDSIM_DATA_ROOT=str(data), DSH_HOME=str(data / "dsh-home"),
                   MDSIM_NODE_BIN=str(runtime / "node/bin"), MDSIM_PYTHON=str(runtime / "venv/bin/python"),
                   MDSIM_KIT_BASE=str(kit), AGENT_BASE=str(kit),
                   RUN_CASE_BASE=metadata["run_case_base"],
                   MDSIM_REGISTER_SCRIPT=str(code / "scripts/register_job.py"))
        env["PATH"] = os.pathsep.join(str(p) for p in (data / "bin", runtime / "node/bin", runtime / "npm-tools/bin", runtime / "venv/bin")) + os.pathsep + env.get("PATH", "")
        checks["installation_metadata"] = metadata.get("format") == 1 and metadata.get("data_root") == str(data)
        checks["node_22"] = command_ok([str(runtime / "node/bin/node"), "--version"], env, contains="v22.")
        checks["pnpm"] = command_ok([str(runtime / "npm-tools/bin/pnpm"), "--version"], env)
        checks["python_venv"] = command_ok([str(runtime / "venv/bin/python"), "-c", "import sys; sys.exit(0 if sys.version_info >= (3,10) and sys.prefix != sys.base_prefix else 1)"], env)
        checks["python_libraries"] = command_ok([str(runtime / "venv/bin/python"), "-c", "import yaml,pypdf,pdfplumber,docx,numpy,jsonschema"], env)
        checks["moltemplate"] = (runtime / "venv/bin/moltemplate.sh").is_file()
        checks["dsh_entry"] = (code / "node_modules/.bin/dsh").exists()
        checks["dsh_cli"] = command_ok(["bash", str(code / "run-dsh.sh"), "--help"], env, timeout=30)
        checks["dsh_profiles"] = all(
            all((data / "dsh-home/profiles" / profile / name).exists()
                for name in ("package.json", "cordis.yml", "cordis.patch.yml", "node_modules"))
            for profile in ("web", "headless", "tui"))
        checks["kit_manifest"] = kit_integrity(kit)
        checks["kit_runtime"] = all((kit / "agents/runtime/v3.3" / (name + ".py")).is_file()
                                    for name in ("orchestrator", "claude_interface", "monitor_agent"))
        import yaml
        settings_path = data / "dsh-home/settings.yaml"
        credentials_path = data / "dsh-home/.credentials.yaml"
        if settings_path.is_symlink() or credentials_path.is_symlink():
            checks["model_configuration"] = False
        else:
            settings = yaml.safe_load(settings_path.read_text(encoding="utf-8")) or {}
            credentials = yaml.safe_load(credentials_path.read_text(encoding="utf-8")) or {}
            default = settings.get("agent-default-model") or {}
            provider = ((settings.get("llm-pi-ai") or {}).get("providers") or {}).get(default.get("provider"), {})
            key_name = provider.get("apiKeyEnv")
            value = credentials.get(key_name, "") if key_name else ""
            checks["model_configuration"] = isinstance(value, str) and bool(value.strip()) and bool(default.get("model"))
            checks["credential_permissions"] = os.name != "posix" or stat.S_IMODE(credentials_path.stat().st_mode) == 0o600
        cfg = json.loads((code / "mcp/md_config.json").read_text(encoding="utf-8"))
        checks["local_execution_paths"] = (cfg.get("mode") == "local" and cfg.get("local_base") == str(kit)
                                            and cfg.get("data_root") == str(data)
                                            and not (cfg.get("exec_bridge") or {}).get("sudo_user")
                                            and not (cfg.get("hpc") or {}).get("enabled")
                                            and not (cfg.get("scnet") or {}).get("enabled"))
        if metadata["engine"] != "none":
            lmp = str(data / "bin/lmp_mpi")
            checks["lammps_reaxff"] = command_ok([lmp, "-help"], env, contains="reaxff")
            checks["lammps_qeq"] = command_ok([lmp, "-help"], env, contains="qeq/reaxff")
        if metadata["engine"] == "both":
            checks["gromacs"] = command_ok(["gmx", "--version"], env)
        if services:
            checks["user_services"] = command_ok(["systemctl", "--user", "is-active", "--quiet", *metadata["services"]], env, timeout=3)
            checks["chat_http"] = http_ok("http://127.0.0.1:3091/")
            checks["dashboard_http"] = http_ok("http://127.0.0.1:8080/workbench")
            bus = json.loads((kit / "status_bus.json").read_text(encoding="utf-8"))
            import jsonschema
            schema = json.loads((kit / "harness/schemas/status_bus.schema.json").read_text(encoding="utf-8"))
            checks["state_bus_schema"] = jsonschema.Draft7Validator(schema).is_valid(bus)
    except (OSError, ValueError, KeyError, ImportError, TypeError):
        checks["configuration_readable"] = False
    # A configured key is not evidence that the provider accepted it. The research
    # council has a separate provider/search contract and is not tested here.
    report["research_council_verified"] = False
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-root", required=True)
    parser.add_argument("--services", action="store_true")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args()
    report = inspect(args.data_root, args.services)
    ok = bool(report["checks"]) and all(report["checks"].values())
    if not args.quiet:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
