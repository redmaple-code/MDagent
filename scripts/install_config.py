#!/usr/bin/env python3
"""Configure a single-user, local MDagent installation without contacting services.

The llm-pi-ai mapping follows @deepseek-ai/dsh-llm-pi-ai 0.1.0-rc.6:
packages/llm/llm-pi-ai/README.md (declared routes) and lib/types/config.d.ts.
An unlisted route requires api, baseURL and models; apiKeyEnv references the
separate credentials file. DeepSeek defaults follow its official API docs:
https://api-docs.deepseek.com/quick_start/pricing/ (checked 2026-10-12).
"""
import argparse
import copy
import getpass
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import tempfile
from urllib.parse import urlsplit

REPO = Path(__file__).resolve().parent.parent
STATE_FILE = ".mdagent-install.json"
DEFAULT_MODEL = "deepseek-flash"
DEFAULT_BASE_URL = "https://api.deepseek.com"
REQUIRED_KIT_FILES = tuple(
    "agents/runtime/v3.3/" + name + ".py"
    for name in ("claude_interface", "preflight_agent", "launch_agent",
                 "monitor_agent", "recover_agent", "analyze_agent", "research_agent")
)
SUPPORTED_APIS = ("openai-completions", "openai-responses", "anthropic-messages")


class ConfigError(Exception):
    """A diagnostic that contains no API key or credential document."""


def absolute(path):
    return Path(path).expanduser().resolve()


def check_kit(kit):
    kit = absolute(kit)
    missing = [name for name in REQUIRED_KIT_FILES
               if not (kit / name).is_file() or (kit / name).stat().st_size == 0]
    if missing:
        raise ConfigError("kit v3.3 不完整，缺少非空运行文件: " + ", ".join(missing))
    return kit


def _read_json(path):
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise ConfigError("无法读取 JSON 配置，请先修复该文件: " + str(path)) from None
    if not isinstance(value, dict):
        raise ConfigError("配置必须是 JSON 对象: " + str(path))
    return value


def _write(path, text, mode=0o600):
    """Create a private temporary file, then replace atomically; never follow links."""
    if path.is_symlink():
        raise ConfigError("安装配置不能是符号链接: " + str(path))
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".mdagent-", dir=str(path.parent))
    try:
        os.chmod(temporary, mode)
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(text)
        os.replace(temporary, path)
        os.chmod(path, mode)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _write_json(path, value):
    _write(path, json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def _digest(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _within(path, root):
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def prepare(kit, data_root, run_case_base=None, repo=REPO):
    """Seed a fresh local installation; retain existing model and user config."""
    kit, data_root, repo = check_kit(kit), absolute(data_root), absolute(repo)
    run_case_base = absolute(run_case_base or kit.parent / "程序运行-case")
    if _within(data_root, kit) or _within(run_case_base, kit):
        raise ConfigError("数据根和任务目录必须位于 kit 源码目录之外")
    home = data_root / "dsh-home"
    state_path = data_root / STATE_FILE
    state = _read_json(state_path) if state_path.exists() else {}
    existing = bool(state.get("model_configured"))
    for name, seed_field in (("settings.yaml", "settings_seed_sha256"),
                             (".credentials.yaml", "credentials_seed_sha256")):
        path = home / name
        if path.exists() and state.get(seed_field) != _digest(path.read_text(encoding="utf-8")):
            existing = True
    config_path = repo / "mcp" / "md_config.json"
    if not existing or not config_path.exists():
        # Start from the export template, never from a previous machine's config.
        config = copy.deepcopy(_read_json(repo / "mcp" / "md_config.json.example"))
        config.update(mode="local", local_base=str(kit), data_root=str(data_root),
                      run_case_base=str(run_case_base), monitor_url="http://127.0.0.1:8080")
        config["ssh"] = {"host": "", "user": "", "password": "", "key_file": "",
                         "remote_base": str(kit)}
        config["exec_bridge"] = {}
        for name in ("hpc", "scnet"):
            block = config.get(name)
            config[name] = block if isinstance(block, dict) else {}
            config[name]["enabled"] = False
            if name == "scnet" or "auto_submit" in config[name]:
                config[name]["auto_submit"] = False
        _write_json(config_path, config)
    home.mkdir(parents=True, exist_ok=True)
    os.chmod(home, 0o700)
    run_case_base.mkdir(parents=True, exist_ok=True)
    settings_path = home / "settings.yaml"
    if not settings_path.exists():
        text = (repo / "scripts" / "settings.yaml.example").read_text(encoding="utf-8")
        _write(settings_path, text)
        state["settings_seed_sha256"] = _digest(text)
    credential_path = home / ".credentials.yaml"
    if not credential_path.exists():
        text = '# 模型凭据；请勿提交或分享。\nDEEPSEEK_API_KEY: ""\n'
        _write(credential_path, text)
        state["credentials_seed_sha256"] = _digest(text)
    else:
        if credential_path.is_symlink():
            raise ConfigError("凭据文件不能是符号链接")
        os.chmod(credential_path, 0o600)
    state.setdefault("version", 1)
    state.setdefault("model_configured", existing)
    _write_json(state_path, state)
    return {"existing": existing, "data_root": str(data_root), "kit": str(kit)}


def _scalar(value):
    # JSON string syntax is a valid YAML scalar, including escaping and Unicode.
    return json.dumps(value, ensure_ascii=False)


def _clean_text(value, label):
    value = str(value).strip()
    if not value or any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise ConfigError(label + " 不能为空或包含控制字符")
    return value


def model_sections(provider, model=None, base_url=None, api="openai-completions",
                   context_window=None, max_tokens=8192):
    if provider not in ("deepseek", "openai-compatible", "custom"):
        raise ConfigError("provider 须为 deepseek、openai-compatible 或 custom")
    model = _clean_text(model or (DEFAULT_MODEL if provider == "deepseek" else ""), "模型 ID")
    base_url = _clean_text(base_url or (DEFAULT_BASE_URL if provider == "deepseek" else ""), "API 地址")
    parsed = urlsplit(base_url)
    if (parsed.scheme not in ("https", "http") or not parsed.hostname or parsed.username
            or parsed.password or parsed.query or parsed.fragment):
        raise ConfigError("API 地址须为无用户名、密码、查询参数或片段的 http(s) 地址")
    if api not in SUPPORTED_APIS:
        raise ConfigError("不支持该 API 协议")
    context_window = context_window if context_window is not None else (
        1048576 if provider == "deepseek" else 32768)
    if context_window <= 0 or max_tokens <= 0 or max_tokens > context_window:
        raise ConfigError("上下文和输出预算须为正整数，输出预算不能大于上下文")
    route = "deepseek" if provider == "deepseek" else "mdagent-" + provider
    reference = "DEEPSEEK_API_KEY" if provider == "deepseek" else "MDAGENT_MODEL_API_KEY"
    llm = ("llm-pi-ai:\n  providers:\n    " + route + ":\n"
           + "      apiKeyEnv: " + reference + "\n"
           + "      api: " + api + "\n"
           + "      baseURL: " + _scalar(base_url.rstrip("/")) + "\n"
           + "      models:\n        - id: " + _scalar(model) + "\n"
           + "          contextWindow: " + str(context_window) + "\n"
           + "          maxTokens: " + str(max_tokens) + "\n")
    if provider == "deepseek" and api == "openai-completions":
        # The wire switch is documented by both pi-ai and DeepSeek, rather than
        # relying on an old catalog recognising the current model's new ID.
        llm += ("          reasoningEfforts:\n            off: null\n"
                "            low: low\n            high: high\n            max: max\n"
                "      compat:\n        thinkingFormat: deepseek\n")
    default = "agent-default-model:\n  provider: " + route + "\n  model: " + _scalar(model) + "\n"
    return {"llm-pi-ai": llm, "agent-default-model": default}, reference


def _replace_model_sections(text, sections):
    """Retain unrelated top-level settings verbatim, without needing PyYAML."""
    lines = text.splitlines(keepends=True)
    output, replaced, index = [], set(), 0
    while index < len(lines):
        match = re.match(r"^([A-Za-z0-9_-]+):(?:\s|$)", lines[index])
        name = match.group(1) if match else None
        if name not in sections:
            output.append(lines[index])
            index += 1
            continue
        if name in replaced:
            raise ConfigError("模型配置存在重复顶层段，请先修复 settings.yaml")
        output.append(sections[name])
        replaced.add(name)
        index += 1
        while index < len(lines):
            line = lines[index]
            if line.strip() and not line[0].isspace() and not line.startswith("#"):
                break
            # Preserve section-level comments and blank lines following its values.
            if line.startswith("#"):
                output.append(line)
            index += 1
    for name in sections:
        if name not in replaced:
            if output and not output[-1].endswith("\n"):
                output.append("\n")
            output.append(sections[name])
    return "".join(output)


def _credential_value(text, reference):
    match = re.search(r"^" + re.escape(reference) + r"\s*:\s*(.*?)\s*$", text, re.M)
    if not match:
        return ""
    value = match.group(1).strip()
    if value.startswith('"'):
        try:
            return json.loads(value)
        except ValueError:
            return ""
    if value.startswith("'") and value.endswith("'"):
        return value[1:-1].replace("''", "'")
    return value.split(" #", 1)[0].strip()


def _set_credential(text, reference, key):
    line = reference + ": " + _scalar(key) + "\n"
    pattern = r"^" + re.escape(reference) + r"\s*:.*(?:\n|$)"
    if len(re.findall(pattern, text, re.M)) > 1:
        raise ConfigError("凭据文件中该模型的引用重复，请先修复该文件")
    if re.search(pattern, text, re.M):
        return re.sub(pattern, lambda _: line, text, count=1, flags=re.M)
    return text + ("\n" if text and not text.endswith("\n") else "") + line


def _validate_key(key):
    key = key.strip()
    if not key or any(ord(char) < 33 or ord(char) > 126 for char in key):
        raise ConfigError("API Key 不能为空，且须为可用作 HTTP 凭据的 ASCII 字符")
    return key


def configure_model(data_root, provider=None, model=None, base_url=None,
                    non_interactive=False, reconfigure=False, api="openai-completions",
                    context_window=None, max_tokens=8192, environ=None):
    data_root = absolute(data_root)
    home = data_root / "dsh-home"
    settings_path, credential_path = home / "settings.yaml", home / ".credentials.yaml"
    state_path = data_root / STATE_FILE
    state = _read_json(state_path) if state_path.exists() else {}
    settings = settings_path.read_text(encoding="utf-8") if settings_path.exists() else ""
    credentials = credential_path.read_text(encoding="utf-8") if credential_path.exists() else ""
    settings_seeded = state.get("settings_seed_sha256") == _digest(settings)
    credentials_seeded = state.get("credentials_seed_sha256") == _digest(credentials)
    existing = (state.get("model_configured")
                or (settings and not settings_seeded)
                or (credentials and not credentials_seeded))
    if not reconfigure and existing:
        if credential_path.exists():
            if credential_path.is_symlink():
                raise ConfigError("凭据文件不能是符号链接")
            os.chmod(credential_path, 0o600)
        return {"preserved": True}
    if provider is None:
        provider = "deepseek" if non_interactive else (
            input("模型服务 [deepseek/openai-compatible/custom，默认 deepseek]: ").strip() or "deepseek")
    if not non_interactive:
        model = model or input("模型 ID [" + (DEFAULT_MODEL if provider == "deepseek" else "必填") + "]: ").strip()
        base_url = base_url or input("API 地址 [" + (DEFAULT_BASE_URL if provider == "deepseek" else "必填") + "]: ").strip()
    sections, reference = model_sections(provider, model, base_url, api, context_window, max_tokens)
    environ = os.environ if environ is None else environ
    key = environ.get("MDAGENT_MODEL_API_KEY", "")
    if not key and not non_interactive:
        # getpass refuses an echoing fallback: missing a real terminal is an error.
        import warnings
        with warnings.catch_warnings():
            warnings.simplefilter("error", getpass.GetPassWarning)
            try:
                key = getpass.getpass("API Key（不回显）: ")
            except getpass.GetPassWarning:
                raise ConfigError("无法隐藏 API Key 输入；请使用真实终端或环境变量") from None
    if not key:
        raise ConfigError("非交互配置需要 MDAGENT_MODEL_API_KEY 环境变量")
    key = _validate_key(key)
    new_settings = _replace_model_sections(settings, sections)
    new_credentials = _set_credential(credentials, reference, key)
    home.mkdir(parents=True, exist_ok=True)
    os.chmod(home, 0o700)
    _write(credential_path, new_credentials)
    _write(settings_path, new_settings)
    state.update(version=1, model_configured=True)
    state.pop("settings_seed_sha256", None)
    state.pop("credentials_seed_sha256", None)
    _write_json(state_path, state)
    return {"preserved": False, "provider": provider}


def main(argv=None):
    parser = argparse.ArgumentParser(description="单用户本机 MDagent 安装配置；不连接 LLM/SSH")
    subparsers = parser.add_subparsers(dest="command", required=True)
    for name in ("prepare", "check-kit", "configure-model"):
        command = subparsers.add_parser(name)
        command.add_argument("--kit", required=name != "configure-model")
        command.add_argument("--data-root", default=str(REPO.parent / "mdsim-data"))
        command.add_argument("--run-case-base")
        if name == "configure-model":
            command.add_argument("--provider", choices=("deepseek", "openai-compatible", "custom"))
            command.add_argument("--model")
            command.add_argument("--base-url")
            command.add_argument("--api", choices=SUPPORTED_APIS, default="openai-completions")
            command.add_argument("--context-window", type=int)
            command.add_argument("--max-tokens", type=int, default=8192)
            command.add_argument("--non-interactive", action="store_true")
            command.add_argument("--reconfigure", action="store_true", help="显式更换已有模型配置与 Key")
    args = parser.parse_args(argv)
    try:
        if args.command == "check-kit":
            check_kit(args.kit)
            print("[install] kit v3.3 运行文件检查通过（未执行模拟）")
        elif args.command == "prepare":
            result = prepare(args.kit, args.data_root, args.run_case_base)
            print("[install] 已保留现有用户配置" if result["existing"] else "[install] 已准备单用户本机配置")
        else:
            result = configure_model(args.data_root, args.provider, args.model, args.base_url,
                                     args.non_interactive, args.reconfigure, args.api,
                                     args.context_window, args.max_tokens)
            print("[install] 已保留现有模型和 Key" if result["preserved"] else "[install] 模型配置已保存，凭据权限 600")
    except (ConfigError, OSError, EOFError, KeyboardInterrupt):
        # Never include raw OS paths/errors or input in the model path, which may
        # contain a secret supplied accidentally; CLI diagnostics stay generic.
        if args.command == "configure-model":
            print("[install] 模型配置失败；请检查模型 ID、API 地址及 Key 输入，已有文件未因参数校验而改变", file=sys.stderr)
        else:
            print("[install] 配置失败；请检查 kit 文件、目录权限和配置文件", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
