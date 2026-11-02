"""Configuration loading for Fluister."""
import copy
import shutil
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "config.yaml"
EXAMPLE_CONFIG_PATH = ROOT / "config.example.yaml"
HISTORY_PATH = ROOT / "history.jsonl"
LEARNED_CORRECTIONS_PATH = ROOT / "learned_corrections.json"

DEFAULTS = {
    "hotkey": {"mode": "pynput", "key": "alt_r"},
    "audio": {
        "min_seconds": 0.35,
        "always_on": False,
        "preroll_ms": 300,
        "tail_ms": 250,
    },
    "vad": {
        "enabled": True,
        "min_speech_seconds": 0.3,
        "hallucination_max_speech_s": 1.2,
        "hallucinations": [
            "you", "thank you", "thanks", "thank you for watching",
            "thanks for watching", "bye", "mm-hmm", "hmm", "uh", "um",
            "oh", "whoa", "yeah",
        ],
    },
    "feedback": {
        "sounds": False,
        "start_sound": "Tink",
        "stop_sound": "Pop",
        "volume": 0.3,
        "mute_while_recording": False,
    },
    "injection": {
        "ax_insert": True,
        "trailing_space": False,
        "auto_submit_apps": [],
        "remote_apps": [],
        "remote_paste_modifier": "ctrl",
    },
    "dictionary_learning": {
        "enabled": True,
        "threshold": 2,
        "check_delay_seconds": 4,
    },
    "asr": {
        "backend": "fasterwhisper",
        "model": "large-v3-turbo",
        "compute_type": "int8",
        "language": None,
    },
    "llm": {
        "backend": "ollama",
        "model": "qwen3:4b-instruct",
        "base_url": "http://localhost:11434/v1",
        "api_key_env": "",
        "temperature": 0.2,
        "timeout": 20,
    },
    "dictionary": [],
    "replacements": {},
    "app_styles": {},
    "history": {"enabled": True},
}


def _merge(base: dict, override: dict) -> dict:
    out = copy.deepcopy(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _merge(out[key], value)
        else:
            out[key] = value
    return out


def load() -> dict:
    # First run: start from the shipped example so there is a config.yaml
    # to edit. The personal copy is never committed.
    if not CONFIG_PATH.exists() and EXAMPLE_CONFIG_PATH.exists():
        shutil.copyfile(EXAMPLE_CONFIG_PATH, CONFIG_PATH)
    user = {}
    if CONFIG_PATH.exists():
        user = yaml.safe_load(CONFIG_PATH.read_text()) or {}
    return _merge(DEFAULTS, user)


def save_asr_language(language):
    """Persist asr.language in config.yaml by editing only that line, so the
    user's comments and spacing survive. language is None, "nl" or "en".
    Works with any indentation and adds the key (or the whole asr block)
    when it is missing."""
    import re

    value = language or "null"
    lines = CONFIG_PATH.read_text(encoding="utf-8").splitlines(keepends=True)

    asr_idx = next(
        (i for i, line in enumerate(lines) if re.match(r"^asr:[ \t]*(#.*)?$", line)),
        None,
    )
    if asr_idx is None:
        prefix = "" if not lines or lines[-1].endswith("\n") else "\n"
        lines.append(f"{prefix}asr:\n  language: {value}\n")
        CONFIG_PATH.write_text("".join(lines), encoding="utf-8")
        return

    child_indent = None
    for i in range(asr_idx + 1, len(lines)):
        line = lines[i]
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        indent = len(line) - len(line.lstrip())
        if indent == 0:
            break
        if child_indent is None:
            child_indent = indent
        if indent != child_indent:
            continue  # nested deeper: not a direct child of asr
        match = re.match(
            r"^([ \t]+language:)[ \t]*([^#\r\n]*?)([ \t]*(?:#.*)?)(\r?\n?)$", line
        )
        if match:
            lines[i] = f"{match.group(1)} {value}{match.group(3)}{match.group(4)}"
            CONFIG_PATH.write_text("".join(lines), encoding="utf-8")
            return

    indent = " " * (child_indent or 2)
    lines.insert(asr_idx + 1, f"{indent}language: {value}\n")
    CONFIG_PATH.write_text("".join(lines), encoding="utf-8")
