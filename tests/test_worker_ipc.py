"""The child-process boundary, tested against fake workers.

No speech model needed: these drive WorkerLink with small scripts that emit the same
JSON Lines a real worker does, including the crash that started all of this.
"""

from __future__ import annotations

import sys
import threading
from pathlib import Path

import pytest

import worker_ipc


def run_link(script: str, tmp_path: Path, timeout: float = 60, **kwargs):
    """Run a fake worker script and collect everything the parent saw."""
    path = tmp_path / "fake_worker.py"
    path.write_text(script, encoding="utf-8")

    messages: list[dict] = []
    errors: list[str] = []
    done = threading.Event()

    def on_message(msg: dict) -> bool:
        messages.append(msg)
        if msg.get("type") in ("done", "error"):
            done.set()
            return True
        return False

    def on_error(message: str) -> None:
        errors.append(message)
        done.set()

    link = worker_ipc.WorkerLink(
        [sys.executable, str(path)], on_message=on_message, on_error=on_error, **kwargs
    )
    link.start()
    finished = done.wait(timeout=timeout)
    return link, messages, errors, finished


def test_messages_arrive_and_done_ends_the_stream(tmp_path):
    script = (
        "import json, sys\n"
        "for i in range(3):\n"
        "    print(json.dumps({'type': 'segment', 'text': f'line {i}'}), flush=True)\n"
        "print(json.dumps({'type': 'done'}), flush=True)\n"
    )
    _link, messages, errors, finished = run_link(script, tmp_path)
    assert finished and not errors
    assert [m["text"] for m in messages if m["type"] == "segment"] == \
        ["line 0", "line 1", "line 2"]


def test_messages_stream_rather_than_arriving_all_at_once(tmp_path):
    """A batching parent would break the progress bar and lose partial output."""
    script = (
        "import json, sys, time\n"
        "for i in range(3):\n"
        "    print(json.dumps({'type': 'segment', 'text': str(i)}), flush=True)\n"
        "    time.sleep(0.6)\n"
        "print(json.dumps({'type': 'done'}), flush=True)\n"
    )
    seen_at: list[float] = []
    import time
    t0 = time.time()
    done = threading.Event()

    def on_message(msg):
        if msg.get("type") == "segment":
            seen_at.append(time.time() - t0)
        if msg.get("type") == "done":
            done.set()
            return True
        return False

    path = tmp_path / "w.py"
    path.write_text(script, encoding="utf-8")
    worker_ipc.WorkerLink([sys.executable, str(path)],
                          on_message=on_message, on_error=lambda m: done.set()).start()
    assert done.wait(timeout=60)
    assert len(seen_at) == 3
    # The last must arrive clearly after the first, not bunched at the end.
    assert seen_at[-1] - seen_at[0] > 0.8, f"arrival times {seen_at} look batched"


def test_non_ascii_survives_the_pipe(tmp_path):
    """Frozen builds ignore PYTHONIOENCODING; emit() escapes to ASCII to compensate.
    This is what stopped non-English transcripts being destroyed."""
    script = (
        "import sys\n"
        f"sys.path.insert(0, {str(Path(worker_ipc.__file__).parent)!r})\n"
        "import worker_ipc\n"
        "worker_ipc.emit({'type': 'segment', 'text': 'கூ नोट café'})\n"
        "worker_ipc.emit({'type': 'done'})\n"
    )
    _link, messages, errors, finished = run_link(script, tmp_path)
    assert finished and not errors
    assert messages[0]["text"] == "கூ नोट café"


def test_emit_writes_pure_ascii():
    """The wire itself must be ASCII, so no encoding mismatch can corrupt it."""
    import json
    line = json.dumps({"text": "கூ"}, ensure_ascii=True)
    line.encode("ascii")            # would raise if not
    assert json.loads(line)["text"] == "கூ"


def test_garbage_lines_are_ignored(tmp_path):
    """Native libraries print to stdout uninvited; it must not derail the stream."""
    script = (
        "import json\n"
        "print('some library banner', flush=True)\n"
        "print('', flush=True)\n"
        "print(json.dumps({'type': 'segment', 'text': 'real'}), flush=True)\n"
        "print('not json at all {', flush=True)\n"
        "print(json.dumps({'type': 'done'}), flush=True)\n"
    )
    _link, messages, errors, finished = run_link(script, tmp_path)
    assert finished and not errors
    assert [m["text"] for m in messages if m["type"] == "segment"] == ["real"]


def test_an_access_violation_becomes_actionable_advice(tmp_path):
    """The whole reason this boundary exists. 0xC0000005 must explain the pin."""
    script = "import sys\nsys.exit(-1073741819)\n"
    _link, _messages, errors, finished = run_link(script, tmp_path)
    assert finished
    assert errors, "a crashing worker must report something"
    assert "ctranslate2==4.4.0" in errors[0]
    assert "access violation" in errors[0].lower()


def test_other_exit_codes_report_the_code(tmp_path):
    script = "import sys\nsys.exit(42)\n"
    _link, _messages, errors, finished = run_link(script, tmp_path)
    assert finished and errors
    assert "42" in errors[0]


def test_a_silent_clean_exit_uses_the_empty_hint(tmp_path):
    script = "pass\n"
    _link, _messages, errors, finished = run_link(
        script, tmp_path, empty_hint="Nothing was transcribed."
    )
    assert finished and errors
    assert "Nothing was transcribed." in errors[0]


def test_stderr_is_surfaced_in_the_explanation(tmp_path):
    script = (
        "import sys\n"
        "print('a native library complained', file=sys.stderr, flush=True)\n"
        "sys.exit(3)\n"
    )
    _link, _messages, errors, finished = run_link(script, tmp_path)
    assert finished and errors
    assert "a native library complained" in errors[0]


def test_cancel_stops_the_worker_and_stays_quiet(tmp_path):
    """Stopping on purpose is not a failure, so it must not raise an error at the user."""
    script = (
        "import json, time\n"
        "print(json.dumps({'type': 'status', 'message': 'started'}), flush=True)\n"
        "time.sleep(120)\n"
    )
    path = tmp_path / "w.py"
    path.write_text(script, encoding="utf-8")
    started, errors = threading.Event(), []

    def on_message(msg):
        started.set()
        return False

    link = worker_ipc.WorkerLink([sys.executable, str(path)],
                                 on_message=on_message, on_error=errors.append)
    link.start()
    assert started.wait(timeout=60), "worker never produced its first message"
    link.cancel()
    for _ in range(100):
        if not link.is_running():
            break
        import time
        time.sleep(0.1)
    assert not link.is_running(), "cancel left the worker running"
    assert link.cancelled
    assert errors == [], f"cancelling reported a spurious error: {errors}"


def test_a_missing_program_reports_rather_than_raising():
    errors = []
    done = threading.Event()
    link = worker_ipc.WorkerLink(
        ["definitely-not-a-real-program-xyz"],
        on_message=lambda m: False,
        on_error=lambda m: (errors.append(m), done.set()),
    )
    link.start()
    assert done.wait(timeout=30)
    assert "Could not start" in errors[0]


# --- argv construction ------------------------------------------------------

def test_worker_argv_uses_an_interpreter_and_the_script(tmp_path):
    argv = worker_ipc.worker_argv("transcribe", tmp_path / "mod.py", ["--model", "tiny"])
    assert argv[1].endswith("mod.py")
    assert argv[2:] == [worker_ipc.WORKER_ARG, "transcribe", "--model", "tiny"]


def test_frozen_builds_re_run_the_app_itself(monkeypatch):
    """A frozen exe has no python to call, so it must re-execute sys.executable."""
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", r"C:\App\Cue.exe", raising=False)
    argv = worker_ipc.worker_argv("listen", "ignored.py", ["--model", "tiny"])
    assert argv == [r"C:\App\Cue.exe", worker_ipc.WORKER_ARG, "listen", "--model", "tiny"]


def test_maybe_run_worker_ignores_a_normal_launch():
    assert worker_ipc.maybe_run_worker([]) is None
    assert worker_ipc.maybe_run_worker(["note.opus", "--model", "tiny"]) is None


def test_maybe_run_worker_rejects_an_unknown_name(capsys):
    assert worker_ipc.maybe_run_worker([worker_ipc.WORKER_ARG, "nope"]) == 2
    assert "Unknown worker" in capsys.readouterr().out
