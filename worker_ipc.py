"""Running crash-prone native work in a child process, and talking to it.

Both of Cue's speech features load ctranslate2, whose model loading can end a
process with a Windows access violation rather than an exception. There is nothing
to catch, so nothing that touches it may run inside Cue's Tk process: each feature
has a worker half that runs in a child and reports back over stdout as JSON Lines,
one object per line, each with a "type".

This module owns what both halves need:

  Worker side   emit()             write one message to the parent
  Parent side   WorkerLink         spawn, stream, cancel, explain a crash
  Both          worker_argv()      build the command line, frozen build included
                maybe_run_worker() dispatch when we ARE the worker

Frozen builds have no interpreter to call, so the app re-executes itself with
`--cue-worker <name>`; `maybe_run_worker()` intercepts that before any UI starts.
"""

from __future__ import annotations

import importlib
import json
import os
import subprocess
import sys
import threading
from collections.abc import Callable
from pathlib import Path

WORKER_ARG = "--cue-worker"
KILL_GRACE_SECONDS = 5
STDERR_TAIL_LINES = 40

# worker name -> (module, callable taking the remaining argv and returning an exit code)
_WORKERS: dict[str, tuple[str, str]] = {
    "transcribe": ("audio_transcribe", "run_worker"),
    "listen": ("whisper_capture", "run_worker"),
}

# 0xC0000005. Windows reports it unsigned from some APIs and negative from others.
ACCESS_VIOLATION_CODES = (-1073741819, 3221225477)

CTRANSLATE2_FIX = (
    "The speech engine crashed while loading the model (access violation).\n\n"
    "This is a known ctranslate2 / Windows library clash, not a problem with your "
    "audio. Fix it with:\n\n"
    '    pip install "ctranslate2==4.4.0"\n\n'
    "Versions 4.5 and newer crash on model load in some environments, Anaconda "
    "especially. If it still crashes, point Cue at a clean virtual environment in "
    "Settings → Transcribe → Python for transcription."
)


# ---------------------------------------------------------------------------
# Worker side
# ---------------------------------------------------------------------------

def _use_utf8_io() -> None:
    """Force this process's stdout/stderr to UTF-8.

    A frozen build does not honour PYTHONUNBUFFERED/PYTHONIOENCODING from the
    parent's environment, so stdout comes up on the console code page instead. That
    silently replaced characters it could not encode, which would have wrecked every
    non-English transcript. Belt to the ensure_ascii braces in emit().
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass


def emit(obj: dict) -> None:
    """Send one message to the parent. Flushed, because the parent reads live.

    ensure_ascii escapes every non-ASCII character to \\uXXXX, so the wire is pure
    ASCII and no encoding mismatch between parent and child can corrupt a Tamil or
    Hindi transcript. json.loads restores the real characters on the other side.
    """
    sys.stdout.write(json.dumps(obj, ensure_ascii=True) + "\n")
    sys.stdout.flush()


def emit_error(message: str) -> None:
    emit({"type": "error", "message": message})


def emit_status(message: str) -> None:
    emit({"type": "status", "message": message})


# ---------------------------------------------------------------------------
# Deciding how to launch a worker
# ---------------------------------------------------------------------------

def resolve_python() -> str:
    """Interpreter to run workers under.

    A configured override wins (Settings → Transcribe → Python for transcription),
    so someone whose main environment has a broken native stack can point at a
    clean virtual environment instead of repairing it.
    """
    try:
        import config as cfg_mod
        override = (cfg_mod.get_config().get("transcribe", {}) or {}).get("python", "")
        if override and Path(override).exists():
            return str(override)
    except Exception:
        pass
    return sys.executable


def worker_argv(name: str, script: str | Path, args: list[str]) -> list[str]:
    """Command line that runs worker `name` in a fresh process."""
    if getattr(sys, "frozen", False):
        # No interpreter to call in a frozen build, so re-run the app itself.
        return [sys.executable, WORKER_ARG, name, *args]
    return [resolve_python(), str(script), WORKER_ARG, name, *args]


def maybe_run_worker(argv: list[str] | None = None) -> int | None:
    """If this process was started as a worker, run it and return its exit code.

    Returns None when this is a normal launch, so callers can carry on. Import the
    target module lazily: pulling in a worker's heavy dependencies just to find out
    we aren't that worker would defeat the whole point of the split.
    """
    argv = list(sys.argv[1:] if argv is None else argv)
    if len(argv) < 2 or argv[0] != WORKER_ARG:
        return None
    _use_utf8_io()
    # A frozen worker re-runs the app, whose module import clamps OpenMP to one
    # thread to keep Tk safe. Nothing here draws a UI, and the clamp would make
    # transcription several times slower, so drop it before the model loads.
    os.environ.pop("OMP_NUM_THREADS", None)
    name, rest = argv[1], argv[2:]
    target = _WORKERS.get(name)
    if target is None:
        emit_error(f"Unknown worker '{name}'.")
        return 2
    module_name, func_name = target
    try:
        module = importlib.import_module(module_name)
        return int(getattr(module, func_name)(rest))
    except Exception as e:
        import traceback
        traceback.print_exc(file=sys.stderr)
        emit_error(f"{type(e).__name__}: {e}")
        return 1


# ---------------------------------------------------------------------------
# Parent side
# ---------------------------------------------------------------------------

class WorkerLink:
    """A running worker process and the thread reading its messages.

    `on_message` is called with each decoded object and returns True once the
    stream is logically finished, which is how we tell a clean end from a crash.
    Both callbacks fire on the reader thread, so a Tk caller must marshal them
    with `root.after(0, ...)`.
    """

    def __init__(
        self,
        argv: list[str],
        *,
        on_message: Callable[[dict], bool],
        on_error: Callable[[str], None],
        cwd: str | Path | None = None,
        crash_hint: str = CTRANSLATE2_FIX,
        empty_hint: str = "",
    ) -> None:
        self.argv = argv
        self.on_message = on_message
        self.on_error = on_error
        self.cwd = str(cwd) if cwd else str(Path(__file__).resolve().parent)
        self.crash_hint = crash_hint
        self.empty_hint = empty_hint

        self._proc: subprocess.Popen | None = None
        self._thread: threading.Thread | None = None
        self._cancelled = threading.Event()
        self._finished = False
        self._stderr: list[str] = []

    # ---- lifecycle ----
    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._cancelled.clear()
        self._finished = False
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def cancel(self) -> None:
        self._cancelled.set()
        proc = self._proc
        if proc and proc.poll() is None:
            try:
                proc.terminate()
                try:
                    proc.wait(timeout=KILL_GRACE_SECONDS)
                except subprocess.TimeoutExpired:
                    proc.kill()
            except Exception:
                pass

    def is_running(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    @property
    def cancelled(self) -> bool:
        return self._cancelled.is_set()

    # ---- internals ----
    def _child_env(self) -> dict:
        env = dict(os.environ)
        # The worker owns its own native threads. Inheriting Cue's single-threaded
        # OpenMP clamp would make it needlessly slow.
        env.pop("OMP_NUM_THREADS", None)
        env["PYTHONUNBUFFERED"] = "1"
        env["PYTHONIOENCODING"] = "utf-8"
        return env

    def _run(self) -> None:
        try:
            self._proc = subprocess.Popen(
                self.argv,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                stdin=subprocess.DEVNULL,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                env=self._child_env(),
                cwd=self.cwd,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except Exception as e:
            self.on_error(f"Could not start the speech engine: {type(e).__name__}: {e}")
            return

        drain = threading.Thread(target=self._drain_stderr, daemon=True)
        drain.start()

        try:
            assert self._proc.stdout is not None
            for line in self._proc.stdout:
                line = line.strip()
                if not line or not line.startswith("{"):
                    continue
                try:
                    message = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if self.on_message(message):
                    self._finished = True
        except Exception as e:
            self.on_error(f"Lost contact with the speech engine: {type(e).__name__}: {e}")
            return

        code = self._proc.wait()
        drain.join(timeout=2)
        if self._cancelled.is_set() or self._finished:
            return
        self.on_error(self.explain_exit(code))

    def _drain_stderr(self) -> None:
        proc = self._proc
        if not proc or proc.stderr is None:
            return
        try:
            for line in proc.stderr:
                line = line.rstrip()
                if line:
                    self._stderr.append(line)
                    del self._stderr[:-STDERR_TAIL_LINES]
        except Exception:
            pass

    def stderr_tail(self, lines: int = 8) -> str:
        return "\n".join(self._stderr[-lines:]).strip()

    def explain_exit(self, code: int) -> str:
        """Turn a bare exit code into something the user can act on."""
        tail = self.stderr_tail()
        suffix = f"\n\nEngine output:\n{tail}" if tail else ""
        if code in ACCESS_VIOLATION_CODES:
            return self.crash_hint + suffix
        if code == 0 and self.empty_hint:
            return self.empty_hint + suffix
        return f"The speech engine stopped unexpectedly (exit code {code})." + suffix
