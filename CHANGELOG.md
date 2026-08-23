# Changelog

Notable changes to Cue. Format loosely follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); this project does not yet
publish versioned releases, so dates carry the meaning.

## [Unreleased]

### Added

- **Transcribe a file** (`File → Transcribe a file…`, `Ctrl+O`). Turns an audio or
  video file into text: WhatsApp `.opus` voice notes, `.m4a`, `.mp3`, `.wav`, `.amr`,
  and video containers. Streams text as it decodes, cancellable while keeping what has
  already been transcribed, with export to `.txt`, `.md`, `.srt` and `.vtt`. Language
  is auto-detected, and a names/jargon box biases spelling of proper nouns.
- **Settings → Transcribe** tab for the defaults, including an interpreter override so
  the speech engine can run in a separate environment.
- **`worker_ipc`**: the shared child-process boundary both speech features use.
- **First-run capture consent notice**, and [PRIVACY.md](PRIVACY.md).
- **[STORE.md](STORE.md)**: Microsoft Store submission requirements and current status.
- **Test suite** (`pytest tests`): 56 tests, including one that asserts no native code
  loads in the Tk process.
- **[CONTRIBUTING.md](CONTRIBUTING.md)** and **[SECURITY.md](SECURITY.md)**.

### Changed

- **Speech no longer runs in Cue's own process.** `ctranslate2` can end a process with
  a Windows access violation rather than raising, so the **System audio (Whisper)**
  source used to make the whole window vanish with no error. Both speech features now
  load their model in a child process and stream results back. A crash becomes a
  message naming the fix.
- **The packaged build now includes speech.** Previously `Cue.spec` excluded
  `faster-whisper`, so an installer user got no speech features at all. Frozen builds
  re-run themselves as workers, needing no Python on the machine. The build also
  dropped `mypy`, `zmq`, `jedi` and `pytest`, which had been leaking in from the
  development environment, so the installer grew only ~4 MB despite gaining the engine.
- **Audio device opening is bounded.** Building a WASAPI loopback recorder took under a
  second on Bluetooth headphones, 30 to 95 seconds on HDMI audio, and blocked
  indefinitely on an output with no active sink. It now reports progress and gives up
  after 150 seconds with instructions.
- `requirements.txt` pins `ctranslate2==4.4.0` and `onnxruntime==1.18.1`.
- README corrected: it claimed all data stays local, which stopped being true the
  moment a cloud backend was enabled. The exception is now stated plainly.
- `installer/Cue.iss` resolves the repo root relative to the script, so the installer
  builds from any checkout instead of one hardcoded path.

### Fixed

- **The assist panel showed nothing for the first two minutes, and looked broken.**
  Found by testing in a real Google Meet with captions on: the transcript filled
  correctly and the right-hand panel stayed completely blank. Two causes. The loops
  only ever fired on their configured cadence, so the first attempt at a brief was
  120 seconds after launch and questions 180 seconds, and that clock started at app
  launch rather than when capture began. Meanwhile the status line still read
  "Waiting for first capture…" even though captions were plainly arriving. Until a
  panel has produced output it now retries every 20 seconds, waits for 150 characters
  of speech so the first brief has substance, and shows what it is waiting for:
  `Listening… 79/150 characters of speech`, then `360 characters captured · first
  update in 3s`. Measured: first brief in **37 seconds** instead of 120 or more.
- **Backend failures were being hidden by that same countdown.** An Ollama connection
  error appeared for two seconds and was then overwritten by the next status tick, so
  the one thing worth reading vanished. Failures now persist until the next success and
  read as advice rather than a truncated traceback: `Ollama isn't running — Tools →
  Start Ollama engine` instead of `Error: ConnectionError: Failed to connect to Ollama.
  Please check that Ollama is downloaded, running and ac`.
- **One-word utterances were being treated as speaker names.** Teams puts a speaker's
  name alone on the line above their caption, so "a short capitalised line" was the
  only signal available — and `Thanks` has exactly that shape. Replaying a real
  capture, `Thanks` became a speaker and swallowed the following line as its caption.
  Shape cannot separate the two, so common utterances are now excluded by name.
- **The transcript picked up the app's own interface.** UI Automation returns every
  string a window exposes, so captures included `Type a message`, message timestamps
  and the entire chat sidebar, all of which went into the LLM prompt as if someone had
  said it. Whole-line matches for unambiguous chrome, timestamps and presence rows are
  now dropped. On the replayed capture this removed 74 junk lines and 8 false speakers.
  Deliberately conservative: bare words like `Chats` are kept, because a caption line
  can legitimately be one word and losing real speech is worse than keeping noise.
- **Non-English transcripts were being destroyed in packaged builds.** A frozen exe
  ignores `PYTHONIOENCODING`, so worker output fell back to the console code page and
  silently replaced every character it could not encode. Tamil, Hindi and anything
  else non-ASCII came back as `?`. The wire is now ASCII-escaped JSON.
- **Vocabulary hints suppressed all punctuation.** Whisper copies the *style* of its
  initial prompt: a bare comma list of names spelled the names correctly and then
  produced a transcript with no sentence punctuation at all. Hints are now wrapped in
  a punctuated sentence, which keeps both.
- **Transcripts arrived all at once instead of streaming.** The cleanup stage built a
  list, draining the whole transcription before the caller saw its first segment. That
  froze the progress bar and threw away everything on cancel. It is now a generator.
- A finished progress bar snapped back to empty, because `ttk`'s `stop()` zeroes the
  value.
- Cancelling a transcription left the partial text on screen but the Copy and Save
  buttons disabled.
- Removed a duplicated `_warm_kb` definition in `live_capture.py`, where the first
  copy was dead code.

## 2026-06-10 — Initial commit

First public snapshot: Teams desktop and Pick a window caption capture, system-audio
capture via Whisper, pure-Python TF-IDF knowledge base with optional OpenAI
embeddings, live assist panel (brief, questions, chip-in), Ollama and Claude backends,
tabbed settings, Sun Valley theme with light and dark, PyInstaller build and Inno Setup
installer.
