#!/usr/bin/env python3
"""Decode dsh defaults for the shell launcher without logging settings contents."""
import argparse
import json
from pathlib import Path
import re
import sys


def _scalar(text):
    """Fallback for this installer's JSON-quoted and simple YAML string scalars."""
    text = text.strip()
    if text.startswith('"'):
        decoder = json.JSONDecoder()
        value, end = decoder.raw_decode(text)
        tail = text[end:].strip()
        if tail and not tail.startswith("#"):
            raise ValueError("Invalid scalar")
        return value
    if text.startswith("'"):
        match = re.fullmatch(r"'((?:[^']|'')*)'\s*(?:#.*)?", text)
        if not match:
            raise ValueError("Invalid scalar")
        return match.group(1).replace("''", "'")
    text = re.split(r"\s+#", text, maxsplit=1)[0].rstrip()
    if text in ("", "~", "null", "Null", "NULL", "true", "false", "True", "False"):
        return None
    if text[0] in "[{>|&*!":
        raise ValueError("Complex YAML requires PyYAML")
    return text


def _fallback(text):
    # Read only the two small sections used by the launcher. Other plugin
    # sections may contain expressions and are deliberately left untouched.
    result, section = {}, None
    for line in text.splitlines():
        root = re.match(r"^([A-Za-z0-9_-]+):\s*(?:#.*)?$", line)
        if root:
            section = root.group(1)
            if section in ("agent-default-model", "agent-presets"):
                if section in result:
                    raise ValueError("Duplicate default section")
                result[section] = {}
            continue
        if line.strip() and not line[0].isspace() and not line.startswith("#"):
            section = None
        if section in result:
            entry = re.match(r"^  ([A-Za-z0-9_-]+):\s*(.*)$", line)
            if entry:
                name = entry.group(1)
                if name in result[section]:
                    raise ValueError("Duplicate default value")
                result[section][name] = _scalar(entry.group(2))
    return result


def read_defaults(path):
    text = Path(path).read_text(encoding="utf-8")
    try:
        import yaml
    except ImportError:
        settings = _fallback(text)
    else:
        # The managed installer provides PyYAML; use its full YAML decoder.
        settings = yaml.safe_load(text)
    if not isinstance(settings, dict):
        raise ValueError("Settings must be a mapping")
    model = settings.get("agent-default-model") or {}
    presets = settings.get("agent-presets") or {}
    if not isinstance(model, dict) or not isinstance(presets, dict):
        raise ValueError("Default section must be a mapping")
    return {"provider": model.get("provider"), "model": model.get("model"),
            "preset": presets.get("default")}


def default_value(path, field):
    values = read_defaults(path)
    names = ("provider", "model") if field == "model" else ("preset",)
    parts = []
    for name in names:
        value = values[name]
        if (not isinstance(value, str) or not value.strip()
                or any(ord(char) < 32 or ord(char) == 127 for char in value)):
            raise ValueError("Missing or invalid default")
        parts.append(value)
    return "/".join(parts) if field == "model" else parts[0]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("settings")
    parser.add_argument("field", choices=("model", "preset"))
    args = parser.parse_args(argv)
    try:
        value = default_value(args.settings, args.field)
    except Exception:
        # Never print the YAML document or parser's source excerpts: the settings
        # file may include other services' inline credentials on an old machine.
        print("Unable to read dsh defaults; check settings.yaml", file=sys.stderr)
        return 1
    print(value)
    return 0


if __name__ == "__main__":
    sys.exit(main())
