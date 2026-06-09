"""Helpers to detect and launch the local Ollama daemon."""

from __future__ import annotations

import subprocess
import time
import urllib.request

OLLAMA_TAGS_URL = "http://localhost:11434/api/tags"


def is_ollama_up(timeout: float = 1.0) -> bool:
    try:
        with urllib.request.urlopen(OLLAMA_TAGS_URL, timeout=timeout):
            return True
    except Exception:
        return False


def launch_ollama(wait_seconds: int = 15) -> bool:
    """Start `ollama serve` detached if it isn't already up. Blocks up to
    wait_seconds polling for readiness — call from a worker thread, not the UI thread.
    Returns True if Ollama is reachable afterward."""
    if is_ollama_up():
        return True
    try:
        flags = 0
        # Detach so the daemon outlives our app.
        for name in ("DETACHED_PROCESS", "CREATE_NEW_PROCESS_GROUP"):
            flags |= getattr(subprocess, name, 0)
        subprocess.Popen(
            ["ollama", "serve"],
            creationflags=flags,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
        )
    except FileNotFoundError:
        return False  # ollama not installed / not on PATH
    except Exception:
        return False

    for _ in range(max(1, wait_seconds)):
        if is_ollama_up():
            return True
        time.sleep(1)
    return is_ollama_up()
