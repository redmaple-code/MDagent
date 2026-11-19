#!/usr/bin/env python3
"""Prepare the companion kit without importing it or copying runtime data.

This is a code preparation check, not a scientific execution check. Launchers
must persist the environment named in the manifest. Existing prepared targets
are verified and reused; a changed source requires an explicitly new target.
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import sys
import tempfile


MANIFEST = ".mdagent-kit-manifest.json"
FORMAT = "mdagent.prepared-kit.v1"
TRANSFORM_VERSION = 1
OLD_PARENT = "/home/kkk/bak1/Students/Master/2025-PJM"
OLD_BASES = tuple(OLD_PARENT + "/" + name for name in
                  ("agent_deploy_kit_20260630", "20260523-智能体", "智能体设计-5.7"))
OLD_CASES = OLD_PARENT + "/程序运行-case"
OLD_LMP_DIR = "/home/kkk/bak1/Students/Master/lammps-2Aug2023/src"
OLD_MPI_DIR = "/opt/oneapi/2024.0/mpi/2021.11/bin"
OLD_MPI_VARS = "/opt/oneapi/2024.0/mpi/2021.11/env/vars.sh"
RUNTIME = "agents/runtime/v3.3/"
REQUIRED_AGENTS = {
    "orchestrator", "claude_interface", "preflight_agent", "preflight_agent_v2",
    "launch_agent", "monitor_agent", "monitor_daemon", "recover_agent",
    "rebuild_agent", "validate_agent", "research_agent", "analyze_agent",
    "specialist_rules",
}
REQUIRED_CORE = {"__init__", "agent_base", "bus", "config", "observability"}
REQUIRED_SCHEMAS = {"status_bus.schema.json", "sim_config.schema.json"}
ASSET_SUFFIXES = {".html", ".css", ".js", ".svg", ".png", ".jpg", ".jpeg", ".gif", ".woff", ".woff2", ".ico"}
EXTERNAL_MODULES = {"jsonschema"}
RUNTIME_ENV = ["AGENT_BASE", "AGENTS_DIR", "RUN_CASE_BASE", "LMP", "MPIRUN", "MPI_VARS",
               "MDSIM_DATA_ROOT", "MDSIM_REGISTER_SCRIPT"]
SECRET_NAME = re.compile(r"(?:api[_-]?key|access[_-]?token|auth[_-]?token|(?:^|[_-])token(?:$|[_-])|secret|password|passwd|credential|webhook)", re.I)
SECRET_VALUE = re.compile(r"(?:\bsk-[A-Za-z0-9_-]{16,}|\bgh[pousr]_[A-Za-z0-9]{20,}|\bAKIA[A-Z0-9]{16}\b|-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----|https?://[^\s\"']*(?:open-apis/bot/v2/hook/|api/send\?[^\s\"']*key=)[^\s\"']+|https?://[^\s/\"']+:[^\s/\"']+@)")


class PreparationError(Exception):
    """Safe diagnostic: callers must not append source contents or values."""


def fail(code: str, relative: str = "") -> None:
    raise PreparationError(code + (": " + relative if relative else ""))


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def reject_links(path: Path) -> None:
    """lstat every existing ancestor; Windows junctions are links too."""
    for item in reversed((path, *path.parents)):
        try:
            info = item.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400):
            fail("SYMLINK_OR_REPARSE_POINT", item.name)


def read_code(path: Path, relative: str, limit: int = 10 * 1024 * 1024) -> bytes:
    reject_links(path)
    try:
        before = path.lstat()
        if not stat.S_ISREG(before.st_mode):
            fail("NOT_A_REGULAR_FILE", relative)
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        with os.fdopen(fd, "rb") as stream:
            opened = os.fstat(stream.fileno())
            if (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino) or not stat.S_ISREG(opened.st_mode):
                fail("FILE_CHANGED_DURING_READ", relative)
            data = stream.read(limit + 1)
        if len(data) > limit:
            fail("CODE_FILE_TOO_LARGE", relative)
        return data
    except OSError:
        fail("CODE_FILE_UNREADABLE", relative)


def parse_python(text: str, relative: str) -> ast.Module:
    try:
        tree = ast.parse(text, filename=relative)
        compile(tree, relative, "exec")  # Compile only; never execute kit code.
        return tree
    except (SyntaxError, ValueError, TypeError):
        fail("PYTHON_COMPILE_FAILED", relative)


def check_credentials(text: str, relative: str, tree: ast.Module | None = None) -> None:
    if SECRET_VALUE.search(text):
        fail("SUSPECTED_EMBEDDED_CREDENTIAL", relative)
    if tree is not None:
        pairs = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                pairs.extend((target.id, node.value) for target in node.targets if isinstance(target, ast.Name))
            elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                pairs.append((node.target.id, node.value))
            elif isinstance(node, ast.Dict):
                pairs.extend((key.value, value) for key, value in zip(node.keys, node.values)
                             if isinstance(key, ast.Constant) and isinstance(key.value, str))
        for name, value in pairs:
            if SECRET_NAME.search(name) and isinstance(value, ast.Constant) and isinstance(value.value, str):
                literal = value.value.strip()
                if literal and not re.fullmatch(r"(?:example|placeholder|changeme|your[_ -].*|fixture.*|synthetic.*|<.*>)", literal, re.I):
                    # File paths and labels in credential-reading code are not credentials.
                    path_name = re.search(r"(?:file|path|log)", name, re.I)
                    if not (path_name and (literal.endswith((".yaml", ".json", ".jsonl", ".log")) or literal.startswith(("/", "logs/")))):
                        fail("SUSPECTED_EMBEDDED_CREDENTIAL", relative)
    elif re.search(r"(?:api[_-]?key|access[_-]?token|password|secret)\s*[:=]\s*[\"'][^\"']{8,}[\"']", text, re.I):
        fail("SUSPECTED_EMBEDDED_CREDENTIAL", relative)


def collect_source(source: Path) -> dict[str, bytes]:
    """Read only the explicit code surface, never state, logs or transcripts."""
    reject_links(source)
    if not source.is_dir():
        fail("SOURCE_NOT_A_DIRECTORY")
    paths = set()
    for folder in (RUNTIME.rstrip("/"), "agents/runtime/v3.4", "harness/core"):
        directory = source / folder
        reject_links(directory)
        if not directory.is_dir():
            fail("MISSING_CODE_DIRECTORY", folder)
        paths.update(path.relative_to(source).as_posix() for path in directory.glob("*.py"))
    for name in REQUIRED_AGENTS:
        if RUNTIME + name + ".py" not in paths:
            fail("MISSING_AGENT_MODULE", RUNTIME + name + ".py")
    for name in REQUIRED_CORE:
        relative = "harness/core/" + name + ".py"
        if relative not in paths:
            fail("MISSING_HARNESS_MODULE", relative)
    if "agents/runtime/v3.4/web_monitor.py" not in paths:
        fail("MISSING_AGENT_MODULE", "agents/runtime/v3.4/web_monitor.py")
    files = {relative: read_code(source / relative, relative) for relative in sorted(paths)}
    schema_queue = list(REQUIRED_SCHEMAS)
    while schema_queue:
        name = schema_queue.pop()
        relative = "harness/schemas/" + name
        if relative in files:
            continue
        data = read_code(source / relative, relative)
        try:
            schema = json.loads(data)
            if not isinstance(schema, dict):
                fail("INVALID_JSON_SCHEMA", relative)
        except (ValueError, UnicodeError):
            fail("INVALID_JSON_SCHEMA", relative)
        files[relative] = data
        for ref in re.findall(r'"\$ref"\s*:\s*"([^"#]+)(?:#[^"]*)?"', data.decode("utf-8")):
            path = PurePosixPath(ref)
            if path.is_absolute() or len(path.parts) != 1 or not ref.endswith(".schema.json"):
                fail("UNVERIFIABLE_SCHEMA_DEPENDENCY", relative)
            schema_queue.append(ref)
    for version in ("v3.3", "v3.4"):
        folder = source / "agents/runtime" / version / "static"
        reject_links(folder)
        if not folder.is_dir():
            fail("MISSING_STATIC_DIRECTORY", "agents/runtime/" + version + "/static")
        for path in folder.rglob("*"):
            reject_links(path)
            if path.is_dir():
                continue
            if path.suffix.lower() in ASSET_SUFFIXES:
                relative = path.relative_to(source).as_posix()
                if any(SECRET_NAME.search(part) or part.lower() in {"conversation", "sessions", "logs", "backup"} for part in path.relative_to(folder).parts):
                    fail("UNEXPECTED_STATIC_ASSET", relative)
                files[relative] = read_code(path, relative)
    for relative in ("agents/runtime/v3.3/static/index.html", "agents/runtime/v3.4/static/index.html", "agents/runtime/v3.4/static/simple.html"):
        if relative not in files:
            fail("MISSING_STATIC_ASSET", relative)
    return files


def defined_names(tree: ast.Module) -> set[str]:
    names = set()
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            names.update(target.id for target in targets if isinstance(target, ast.Name))
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            names.update(alias.asname or alias.name.split(".")[0] for alias in node.names)
    return names


def local_module(relative: str, module: str, level: int, files: dict[str, bytes]) -> str | None:
    base = PurePosixPath(relative).parent
    if level:
        for _ in range(level - 1):
            base = base.parent
        roots = [base]
    elif module.startswith("core"):
        roots = [PurePosixPath("harness")]
    else:
        roots = [base]
    for root in roots:
        stem = root.joinpath(*module.split(".")) if module else root
        for path in (str(stem) + ".py", str(stem / "__init__.py")):
            if path in files:
                return path
    return None


def validate_dependencies(files: dict[str, bytes], trees: dict[str, ast.Module]) -> None:
    stdlib = set(sys.stdlib_module_names)
    for relative, tree in trees.items():
        for node in ast.walk(tree):
            if not isinstance(node, (ast.Import, ast.ImportFrom)):
                continue
            entries = [(node.module or "", node.level, node.names)] if isinstance(node, ast.ImportFrom) else [(alias.name, 0, []) for alias in node.names]
            for module, level, symbols in entries:
                found = local_module(relative, module, level, files)
                if found:
                    if symbols and any(alias.name != "*" and alias.name not in defined_names(trees[found]) for alias in symbols):
                        fail("MISSING_LOCAL_IMPORT_SYMBOL", relative)
                elif level or module.split(".")[0] not in stdlib | EXTERNAL_MODULES:
                    fail("UNVERIFIED_MODULE_DEPENDENCY", relative)
        if relative.startswith(RUNTIME):
            for node in ast.walk(tree):
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "_run_agent" and node.args and isinstance(node.args[0], ast.Constant):
                    target = RUNTIME + str(node.args[0].value) + ".py"
                    if target not in files:
                        fail("MISSING_DYNAMIC_AGENT_MODULE", relative)
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                value = node.value
                if value.startswith("harness/schemas/") and value.endswith(".schema.json") and value not in files:
                    fail("MISSING_SCHEMA_DEPENDENCY", relative)
    for relative, data in files.items():
        if not relative.endswith((".html", ".css", ".js", ".svg")):
            continue
        text = data.decode("utf-8")
        for ref in re.findall(r'(?:src|href)\s*=\s*["\']([^"\']+)["\']', text):
            if ref.startswith(("http:", "https:", "//", "data:", "#", "/")) or "${" in ref:
                continue
            value = ref.split("?", 1)[0].split("#", 1)[0]
            if not value or value.startswith(("javascript:", "mailto:")):
                continue
            candidate = (PurePosixPath(relative).parent / value).as_posix()
            if ".." in PurePosixPath(value).parts or candidate not in files:
                fail("MISSING_STATIC_ASSET_DEPENDENCY", relative)


def check_cli_contract(tree: ast.Module, relative: str) -> None:
    functions = {node.name: node for node in tree.body if isinstance(node, ast.FunctionDef)}
    for name, expected in {"get_context_for_claude": [], "execute_shell": ["cmd", "description", "cwd"], "execute_bg": ["cmd", "description", "cwd", "log"], "main": []}.items():
        if name not in functions or [arg.arg for arg in functions[name].args.args] != expected:
            fail("CLAUDE_INTERFACE_CONTRACT_MISMATCH", relative)
    main = functions["main"]
    branches = {}
    for node in ast.walk(main):
        if isinstance(node, ast.If) and isinstance(node.test, ast.Compare) and isinstance(node.test.left, ast.Name) and node.test.left.id == "cmd" and len(node.test.comparators) == 1 and isinstance(node.test.comparators[0], ast.Constant):
            branches[node.test.comparators[0].value] = node.body
    for command, function, positions in (("context", "get_context_for_claude", set()), ("exec", "execute_shell", {2, 3, 4}), ("exec_bg", "execute_bg", {2, 3, 4, 5})):
        body = ast.Module(body=branches.get(command, []), type_ignores=[])
        nodes = list(ast.walk(body))
        calls = [node for node in nodes if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == function]
        indices = {node.slice.value for node in nodes if isinstance(node, ast.Subscript) and isinstance(node.value, ast.Attribute) and isinstance(node.value.value, ast.Name) and node.value.value.id == "sys" and node.value.attr == "argv" and isinstance(node.slice, ast.Constant)}
        json_output = any(isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Name) and node.func.value.id == "json" and node.func.attr == "dumps" for node in nodes)
        if len(calls) != 1 or not positions <= indices or not json_output:
            fail("CLAUDE_INTERFACE_CONTRACT_MISMATCH", relative)
    for name in ("execute_shell", "execute_bg"):
        calls = [node for node in ast.walk(functions[name]) if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Name) and node.func.value.id == "subprocess" and node.func.attr in {"run", "Popen"}]
        if not calls or any(not any(keyword.arg == "shell" and isinstance(keyword.value, ast.Constant) and keyword.value.value is False for keyword in call.keywords) for call in calls):
            fail("CLAUDE_INTERFACE_SHELL_CONTRACT_MISMATCH", relative)
    for name, fields in {"get_context_for_claude": {"current_phase", "task", "recent_operations"},
                         "execute_shell": {"returncode", "stdout", "stderr"},
                         "execute_bg": {"returncode", "background", "pid", "log"}}.items():
        dictionaries = [{key.value for key in node.keys if isinstance(key, ast.Constant)}
                        for node in ast.walk(functions[name]) if isinstance(node, ast.Dict)]
        if not any(fields <= keys for keys in dictionaries):
            fail("CLAUDE_INTERFACE_JSON_CONTRACT_MISMATCH", relative)


def transform(text: str, relative: str, destination: str, run_case_base: str) -> tuple[str, list[dict]]:
    counts = {}
    replacements = [("legacy-kit-root", old, destination) for old in OLD_BASES]
    replacements += [("legacy-case-root", OLD_CASES, run_case_base),
                     ("legacy-lmp-binary", OLD_LMP_DIR + "/lmp_mpi", "lmp_mpi"),
                     ("legacy-mpi-binary", OLD_MPI_DIR + "/mpirun", "mpirun"),
                     ("legacy-mpi-vars", OLD_MPI_VARS, "")]
    def replace_value(value):
        for rule, old, new in replacements:
            count = value.count(old)
            if count:
                value = value.replace(old, new)
                counts[rule] = counts.get(rule, 0) + count
        return value

    if relative.endswith(".py"):
        tree = parse_python(text, relative)
        class Paths(ast.NodeTransformer):
            def visit_Assign(self, node):
                self.generic_visit(node)
                if len(node.targets) == 1 and isinstance(node.targets[0], ast.Name) and node.targets[0].id in {"BASE", "RUN_CASE_BASE"}:
                    name = node.targets[0].id
                    env = "AGENT_BASE" if name == "BASE" else "RUN_CASE_BASE"
                    fallback = destination if name == "BASE" else run_case_base
                    node.value = ast.parse("os.environ.get(%r, %r)" % (env, fallback), mode="eval").body
                    counts["runtime-environment-paths"] = counts.get("runtime-environment-paths", 0) + 1
                return node

            def visit_Constant(self, node):
                if not isinstance(node.value, str):
                    return node
                if node.value in (OLD_LMP_DIR, OLD_MPI_DIR):
                    env = "LMP" if node.value == OLD_LMP_DIR else "MPIRUN"
                    counts["runtime-binary-directories"] = counts.get("runtime-binary-directories", 0) + 1
                    return ast.parse("os.path.dirname(os.environ.get(%r, ''))" % env, mode="eval").body
                node.value = replace_value(node.value)
                return node

            def visit_JoinedStr(self, node):
                values = []
                for value in node.values:
                    visited = self.visit(value)
                    if isinstance(value, ast.Constant) and not isinstance(visited, ast.Constant):
                        visited = ast.FormattedValue(value=visited, conversion=-1)
                    values.append(visited)
                node.values = values
                return node

        tree = ast.fix_missing_locations(Paths().visit(tree))
        # AST rendering quotes paths correctly, even when destination has spaces,
        # quotes or Windows backslashes. It does not evaluate any kit statement.
        text = ast.unparse(tree) + "\n"
        parse_python(text, relative)
    else:
        text = replace_value(text)
    if any(old in text for old in (*OLD_BASES, OLD_CASES, OLD_LMP_DIR, OLD_MPI_DIR, OLD_MPI_VARS)) or "/home/kkk/" in text or "/opt/oneapi/" in text:
        fail("UNRESOLVED_LEGACY_PATH", relative)
    return text, [{"rule": rule, "count": count} for rule, count in sorted(counts.items())]


def build_prepared(source: Path, destination: Path, run_case_base: Path) -> tuple[dict[str, bytes], dict]:
    files = collect_source(source)
    trees = {}
    texts = {}
    for relative, data in files.items():
        if relative.endswith((".py", ".json", ".html", ".css", ".js", ".svg")):
            try:
                text = data.decode("utf-8-sig")
            except UnicodeError:
                fail("INVALID_UTF8_CODE", relative)
            tree = parse_python(text, relative) if relative.endswith(".py") else None
            check_credentials(text, relative, tree)
            texts[relative] = text
            if tree:
                trees[relative] = tree
    validate_dependencies(files, trees)
    ci = RUNTIME + "claude_interface.py"
    check_cli_contract(trees[ci], ci)
    prepared, records = {}, []
    for relative, original in sorted(files.items()):
        rules = []
        if relative in texts:
            text, rules = transform(texts[relative], relative, str(destination), str(run_case_base))
            data = text.encode("utf-8")
        else:
            data = original
        prepared[relative] = data
        records.append({"path": relative, "source_sha256": digest(original), "prepared_sha256": digest(data), "transforms": rules})
    check_cli_contract(parse_python(prepared[ci].decode("utf-8"), ci), ci)
    manifest = {"format": FORMAT, "transform_version": TRANSFORM_VERSION,
                "destination": str(destination), "run_case_base": str(run_case_base),
                "files": records, "runtime_environment": RUNTIME_ENV,
                "external_python_dependencies": sorted(EXTERNAL_MODULES),
                "checks": ["metadata-no-links", "credential-literals", "local-module-and-asset-dependencies", "claude-interface-cli", "python-compile"],
                "scope": "code-only; runtime state, case data, engine compatibility and scientific execution are not verified"}
    return prepared, manifest


def verify_existing(destination: Path, expected: dict) -> None:
    path = destination / MANIFEST
    if not path.is_file():
        fail("DESTINATION_NOT_A_PREPARED_KIT")
    try:
        actual = json.loads(read_code(path, MANIFEST, 1024 * 1024))
    except (ValueError, UnicodeError):
        fail("INVALID_PREPARED_KIT_MANIFEST")
    if actual != expected:
        fail("SOURCE_OR_CONFIGURATION_CHANGED_USE_NEW_DESTINATION")
    for record in expected["files"]:
        relative = record["path"]
        if digest(read_code(destination / relative, relative)) != record["prepared_sha256"]:
            fail("PREPARED_CODE_MODIFIED_USE_NEW_DESTINATION", relative)


def prepare(source: Path, destination: Path, run_case_base: Path, *, check_only: bool = False) -> dict:
    source, destination, run_case_base = (Path(os.path.abspath(path)) for path in (source, destination, run_case_base))
    reject_links(destination)
    reject_links(run_case_base)
    if source == destination or source in destination.parents or destination in source.parents:
        fail("SOURCE_AND_DESTINATION_MUST_NOT_OVERLAP")
    prepared, manifest = build_prepared(source, destination, run_case_base)
    if destination.exists():
        if not destination.is_dir():
            fail("DESTINATION_NOT_A_DIRECTORY")
        verify_existing(destination, manifest)
        return {"status": "checked" if check_only else "reused", "files": len(prepared)}
    if check_only:
        return {"status": "checked", "files": len(prepared)}
    destination.parent.mkdir(parents=True, exist_ok=True)
    reject_links(destination.parent)
    staging = Path(tempfile.mkdtemp(prefix=".mdagent-kit-", dir=destination.parent))
    try:
        for relative, data in prepared.items():
            path = staging / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("xb") as stream:
                stream.write(data)
            path.chmod(0o644)
        with (staging / MANIFEST).open("x", encoding="utf-8", newline="\n") as stream:
            json.dump(manifest, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
        # Destination may have appeared while staging: never replace its contents.
        if destination.exists():
            fail("DESTINATION_APPEARED_DURING_PREPARATION")
        staging.rename(destination)
    finally:
        if staging.exists():
            reject_links(staging)
            if staging.parent.resolve() == destination.parent.resolve() and staging.name.startswith(".mdagent-kit-"):
                shutil.rmtree(staging)
    return {"status": "prepared", "files": len(prepared)}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--destination", required=True, type=Path)
    parser.add_argument("--run-case-base", required=True, type=Path)
    parser.add_argument("--check-only", action="store_true")
    args = parser.parse_args(argv)
    try:
        result = prepare(args.source, args.destination, args.run_case_base, check_only=args.check_only)
    except PreparationError as error:
        print("prepare-kit: " + str(error), file=sys.stderr)
        return 2
    except (OSError, UnicodeError, ValueError):
        print("prepare-kit: FILESYSTEM_OR_ENCODING_ERROR", file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
