# Contributing to Cue

Thanks for looking. This document exists mostly to save you an afternoon: Cue sits on
top of some native libraries that fail in unusual ways on Windows, and the failures
do not look like failures. Read [The traps](#the-traps) before you debug anything.

---

## Getting set up

```bash
git clone https://github.com/Digitalgrub-Org/meeting-copilot.git
cd meeting-copilot
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python live_capture.py
```

You will also want [Ollama](https://ollama.com) running with a small model
(`ollama pull llama3.2:1b`) so the assist panel has a backend.

**Use a fresh virtual environment, not a working Anaconda base.** Anaconda ships its
own OpenMP and MKL runtimes, which is the root of most of what follows.

---

## The traps

### 1. ctranslate2 4.5+ crashes on model load

Not an exception — a Windows access violation (`0xC0000005`, exit code
`-1073741819`). The process dies with no traceback. Symptoms look like a hang, a
silent exit, or a corrupt audio file.

Verified on Python 3.11 / Windows 11: **4.7.2 crashes, 4.4.0 works** on identical
input. Ruled out along the way: model file corruption, `KMP_DUPLICATE_LIB_OK`,
`OMP_NUM_THREADS`, CUDA visibility, and `cpu_threads`. The environment had three
competing copies of `libiomp5md.dll`.

`requirements.txt` pins `ctranslate2==4.4.0`. Leave it pinned. If you bump it, test
an actual transcription, not just an import: `import ctranslate2` succeeds fine on the
broken version.

### 2. Nothing native may run in the Tk process

Because of the above, no speech code runs in Cue's own process. Both speech features
spawn a child and stream JSON Lines back over stdout. [worker_ipc.py](worker_ipc.py)
owns that boundary.

**The rule:** the parent half of a feature imports standard library only. If you add
an `import numpy` at the top of [audio_transcribe.py](audio_transcribe.py) or
[whisper_capture.py](whisper_capture.py), you have reintroduced the crash. There is a
test for this; keep it passing.

The same applies to the abandoned embedding stack: sentence-transformers, ChromaDB and
onnxruntime-backed embedders all segfaulted inside Tk. That is why retrieval is a
pure-Python TF-IDF index rather than something semantic. Do not reintroduce a local
PyTorch embedder without a clean, non-Anaconda environment.

### 3. Frozen builds ignore PYTHONIOENCODING

A PyInstaller exe does not honour the parent's `PYTHONIOENCODING`, so worker stdout
comes up on the console code page and silently replaces characters it cannot encode.
This destroyed non-English transcripts entirely.

Fixed two ways in `worker_ipc`: stdout is reconfigured to UTF-8, and `emit()` uses
`ensure_ascii=True` so the wire is pure ASCII escapes regardless. If you add a new
message channel, use `worker_ipc.emit`, not `print`.

### 4. Whisper copies its prompt's punctuation style

Passing a bare comma list of names as `initial_prompt` makes Whisper spell those names
correctly and then strip sentence punctuation from the **entire** transcript. Measured
on a fixed clip: bare list gave correct names and zero periods; the same words wrapped
in a punctuated sentence gave both.

`build_initial_prompt()` does that wrapping. Do not pass raw user input straight to
`initial_prompt`.

### 5. Audio device opening is unbounded

Building a WASAPI loopback recorder took under a second on Bluetooth headphones, 30 to
95 seconds on an HDMI audio device, and blocked indefinitely against an output with no
active sink. `_open_recorder_bounded()` runs it on an abandonable daemon thread with
progress reporting and a 150-second ceiling. Keep any new device work behind it.

### 6. Tkinter is invisible to UI Automation

You cannot drive Cue's own buttons with Windows UI Automation, which rules out
end-to-end GUI click automation. The tests work around this by constructing real
widgets and calling the same methods the buttons call.

---

## Project layout

| File | Role |
|---|---|
| `live_capture.py` | Main Tk app: layout, menus, caption polling, worker dispatch |
| `auto_assist.py` | Right-side panel — brief, questions, chip-in |
| `summarize.py` | Backend-agnostic LLM calls, RAG vs stuff-everything |
| `knowledge_base.py` | Pure-Python TF-IDF index, optional OpenAI vectors |
| `worker_ipc.py` | Child-process boundary: spawn, stream, cancel, explain crashes |
| `audio_transcribe.py` | File transcription — parent half and worker half |
| `whisper_capture.py` | Live system-audio capture — parent half and worker half |
| `transcribe_window.py` | File-transcription UI |
| `settings_window.py` | Tabbed settings |
| `config.py` | JSON config with deep-merged defaults and change listeners |
| `window_capture.py` | Window enumeration for the "Pick a window" source |
| `extract_teams_transcript.ps1` | PowerShell UI Automation caption scraper |
| `md_render.py` | Minimal Markdown to `tk.Text` renderer |

Each speech module is split into a **parent half** (safe to import from Tk) and a
**worker half** (imports the native stack, only ever runs in the child). The module
docstrings mark the boundary. Respect it.

---

## Running the tests

```bash
python -m pytest tests -v
```

Tests that need a real speech model are marked `slow` and download a few hundred MB on
first run:

```bash
python -m pytest tests -v -m "not slow"     # fast subset
```

`tests/test_purity.py` is the one that protects the crash fix — it asserts that
importing the parent halves pulls in no native modules. If it fails, something grew an
import it should not have.

Transcription timing is variable, so avoid wall-clock assertions. Whisper decodes in
roughly 30-second windows, which means segments arrive in batches, not smoothly; a
test that cancels on a fixed timer will race the first batch and flake. Wait for
observable output instead.

---

## Building a release

Build from a **pinned** environment, never from a working Anaconda install.
PyInstaller bundles whatever is installed, so a bad `ctranslate2` ships a crash that
no end user can repair.

```bash
python -m venv build-venv
build-venv\Scripts\python.exe -m pip install -r requirements.txt pyinstaller
build-venv\Scripts\python.exe -m PyInstaller Cue.spec --noconfirm
```

Verify the frozen speech worker before packaging. A frozen build has no interpreter to
call, so it re-runs itself:

```bash
dist\Cue\Cue.exe --cue-worker transcribe some-audio.m4a --model tiny
```

If that prints JSON Lines ending in `{"type": "done"...}`, the dispatch is intact.
Then build the installer:

```bash
"C:\Program Files (x86)\Inno Setup 6\ISCC.exe" installer\Cue.iss
```

Store submission requirements, including code signing, are in [STORE.md](STORE.md).

---

## Style

Match the surrounding code. Practically:

- Type hints on public functions, `from __future__ import annotations` at the top.
- Comments explain **why**, not what. The interesting comments in this codebase are
  the ones recording a measurement or a failure mode.
- Keep UI strings plain and specific. Error messages should say what to do next, and
  where a fix is known, include the exact command.
- No new runtime dependencies without a good reason. This project has deliberately
  removed scikit-learn and scipy in favour of ~80 lines of pure Python.

## Pull requests

Say what you changed and how you verified it. "Tested manually" is fine for UI work if
you say what you clicked. If you touched anything near the worker boundary, run the
full test suite and say so.
