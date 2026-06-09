"""Centralized settings for the meeting workflow app.

Settings live at ~/.meeting_workflow/config.json. get_config() reads the file each
call (cached briefly) so updates from the Settings window propagate without restart.

Schema is a flat-ish nested dict — see DEFAULTS below for the canonical structure.
Missing keys are filled from DEFAULTS, so older configs upgrade cleanly.
"""

from __future__ import annotations

import copy
import json
import os
import threading
from pathlib import Path
from typing import Any

# All Cue data (config, knowledge base, model caches) lives under DATA_DIR.
# Override with the CUE_DATA_DIR env var to keep it off the system drive
# (e.g. CUE_DATA_DIR=D:\CueData). Defaults to ~/.meeting_workflow for portability.
DATA_DIR = Path(os.environ.get("CUE_DATA_DIR") or (Path.home() / ".meeting_workflow"))
try:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
except OSError:
    DATA_DIR = Path.home() / ".meeting_workflow"
    DATA_DIR.mkdir(parents=True, exist_ok=True)

CONFIG_DIR = DATA_DIR  # back-compat alias
CONFIG_PATH = DATA_DIR / "config.json"

DEFAULTS: dict[str, Any] = {
    "llm": {
        "backend": "ollama",                 # ollama | claude | auto  (default: free, local)
        "claude_model": "claude-opus-4-7",   # claude model ID
        "claude_effort": "medium",           # low | medium | high | max (for brief/questions)
        "claude_effort_summary": "high",     # effort used for explicit Summarize action
        "ollama_model": "",                  # empty = auto-pick from installed models
        # Default True: auto-pick the SMALLEST capable Ollama model so Cue runs on
        # minimal hardware. Set False to prefer the most capable installed model.
        "prefer_small_model": True,
    },
    "embeddings": {
        "backend": "tfidf",                   # tfidf | openai
        "openai_model": "text-embedding-3-small",
    },
    "context": {
        "strategy": "auto",                   # auto | stuff | rag
        "stuff_budget_tokens": 150000,        # if total fits under this, stuff everything
        "rag_top_k": 6,
        "rag_query_tail_chars": 4000,
        # When False (default): retrieval uses ONLY uploaded documents, never
        # meeting transcripts. The current meeting's transcript is always passed
        # to the LLM in full, so this keeps each meeting isolated — past meetings
        # don't bleed into the current brief/questions.
        # When True: past meeting transcripts in the KB are also retrievable
        # (cross-meeting memory).
        "include_past_transcripts": False,
    },
    "cadence": {
        "brief_interval_sec": 120,
        "questions_interval_sec": 180,
        "points_interval_sec": 150,        # "what you could say" suggestions
        "auto_index_interval_sec": 600,
        "caption_poll_sec": 4,
    },
    "archive": {
        "on_close": "ask",                    # ask | always | never
    },
    "ui": {
        "theme": "light",                     # light | dark
    },
    "api_keys": {
        # API keys are also read from env vars (ANTHROPIC_API_KEY, OPENAI_API_KEY)
        # at use sites — config values override env if set.
        "anthropic": "",
        "openai": "",
    },
}

_LOCK = threading.RLock()
_listeners: list = []
_cache: dict | None = None
_cache_mtime: float = 0.0


def _deep_merge(base: dict, override: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def _migrate(raw: dict) -> dict:
    # Older config used top-level "anthropic_api_key"; move it under api_keys.
    if "anthropic_api_key" in raw and isinstance(raw.get("anthropic_api_key"), str):
        raw.setdefault("api_keys", {})
        if not raw["api_keys"].get("anthropic"):
            raw["api_keys"]["anthropic"] = raw["anthropic_api_key"]
        raw.pop("anthropic_api_key", None)
    return raw


def get_config() -> dict:
    """Returns a deep copy of current config (defaults overlaid with on-disk values)."""
    global _cache, _cache_mtime
    with _LOCK:
        if CONFIG_PATH.exists():
            mtime = CONFIG_PATH.stat().st_mtime
            if _cache is None or mtime != _cache_mtime:
                try:
                    raw = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
                except Exception:
                    raw = {}
                raw = _migrate(raw)
                _cache = _deep_merge(DEFAULTS, raw)
                _cache_mtime = mtime
        elif _cache is None:
            _cache = copy.deepcopy(DEFAULTS)
        return copy.deepcopy(_cache)


def save_config(new_config: dict) -> None:
    """Save the config dict to disk and notify listeners."""
    global _cache, _cache_mtime
    with _LOCK:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        merged = _deep_merge(DEFAULTS, new_config)
        tmp = CONFIG_PATH.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(merged, indent=2), encoding="utf-8")
        tmp.replace(CONFIG_PATH)
        try:
            os.chmod(CONFIG_PATH, 0o600)
        except OSError:
            pass
        _cache = merged
        _cache_mtime = CONFIG_PATH.stat().st_mtime
        callbacks = list(_listeners)
    for cb in callbacks:
        try:
            cb(copy.deepcopy(merged))
        except Exception:
            pass


def on_change(callback) -> None:
    """Register a callback fired after save_config(). callback(new_config)."""
    with _LOCK:
        _listeners.append(callback)


def get_anthropic_key() -> str | None:
    cfg = get_config()
    key = cfg["api_keys"].get("anthropic") or os.environ.get("ANTHROPIC_API_KEY")
    return key.strip() if key else None


def get_openai_key() -> str | None:
    cfg = get_config()
    key = cfg["api_keys"].get("openai") or os.environ.get("OPENAI_API_KEY")
    return key.strip() if key else None


def set_anthropic_key(key: str) -> None:
    cfg = get_config()
    cfg["api_keys"]["anthropic"] = key.strip()
    save_config(cfg)


def set_openai_key(key: str) -> None:
    cfg = get_config()
    cfg["api_keys"]["openai"] = key.strip()
    save_config(cfg)
