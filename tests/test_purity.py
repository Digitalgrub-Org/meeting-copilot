"""The test that protects the crash fix.

ctranslate2 can end a process with a Windows access violation instead of raising, so
nothing native may load inside Cue's Tk process. Each speech module is split into a
parent half that Tk imports and a worker half that only ever runs in a child.

If someone adds a module-level `import numpy` to one of the parent halves, that split
is silently gone and the app is one bad dependency away from vanishing mid-meeting.
This catches that in a second, without needing a speech model.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# Anything that loads native code we have seen crash, or that drags one in.
FORBIDDEN = (
    "faster_whisper",
    "ctranslate2",
    "av",
    "soundcard",
    "onnxruntime",
    "torch",
    "sentence_transformers",
    "chromadb",
)

PARENT_HALVES = ("worker_ipc", "audio_transcribe", "whisper_capture", "transcribe_window")


def _modules_after_importing(names: tuple[str, ...]) -> set[str]:
    """Import `names` in a clean interpreter and report which forbidden ones loaded."""
    script = (
        "import sys\n"
        f"sys.path.insert(0, {str(REPO_ROOT)!r})\n"
        f"import {', '.join(names)}\n"
        f"forbidden = {FORBIDDEN!r}\n"
        "print(' '.join(m for m in forbidden if m in sys.modules))\n"
    )
    out = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True, text=True, cwd=str(REPO_ROOT), timeout=120,
    )
    assert out.returncode == 0, f"importing {names} failed:\n{out.stderr}"
    return set(out.stdout.split())


def test_parent_halves_import_no_native_code():
    """Importing everything Tk touches must not pull in the speech stack."""
    loaded = _modules_after_importing(PARENT_HALVES)
    assert not loaded, (
        f"these native modules loaded at import time: {sorted(loaded)}. "
        "Move the import inside the worker function; see CONTRIBUTING.md."
    )


def test_main_app_imports_no_native_code():
    """The same must hold for the app module itself."""
    loaded = _modules_after_importing(("live_capture",))
    assert not loaded, f"live_capture pulled in {sorted(loaded)} at import time"


def test_worker_halves_are_reachable():
    """worker_ipc dispatches by name; a typo would only show up at runtime."""
    import importlib

    import worker_ipc

    for name, (module_name, func_name) in worker_ipc._WORKERS.items():
        module = importlib.import_module(module_name)
        assert callable(getattr(module, func_name, None)), (
            f"worker {name!r} points at {module_name}.{func_name}, which is not callable"
        )
