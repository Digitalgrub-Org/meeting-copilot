"""Live meeting capture via system audio + Whisper, isolated in a worker process.

Works for any meeting platform (Teams / Meet / Zoom / phone) because it captures
the OS audio output (loopback) — whatever you hear gets transcribed.

Pipeline:
  WASAPI loopback (soundcard) -> 8s audio chunks -> faster-whisper -> text -> callback

WHY THIS RUNS IN A SUBPROCESS
-----------------------------
This used to load the model on a background thread inside Cue's own process. That
is not survivable: ctranslate2 can end the process with a Windows access violation
instead of an exception, so a bad native stack didn't produce an error message, it
made the whole window vanish mid-meeting. The model now loads in a child process
that streams transcribed lines back over stdout as JSON Lines, so the worst case is
a message in the status bar. worker_ipc holds the plumbing, shared with file
transcription.

Two halves, same as audio_transcribe:

  Parent side (safe to import from Tk — stdlib only at module level)
    WhisperCapture       start/stop, delivers text through on_text
  Worker side (imports faster_whisper + soundcard, only in the child)
    run_worker()         capture loop, dispatched by worker_ipc
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import worker_ipc

SAMPLE_RATE = 16000     # Whisper expects 16kHz
CHUNK_SECONDS = 8       # audio buffered per transcription pass
OVERLAP_SECONDS = 1     # overlap with prior chunk to catch boundary words
MIN_RMS = 0.003         # below this, skip transcription (silence)

MISSING_DEPS_HINT = (
    "Live audio capture needs two extra packages:\n\n"
    '    pip install faster-whisper "ctranslate2==4.4.0" soundcard\n\n'
    "Or point Cue at an environment that has them, in "
    "Settings → Transcribe → Python for transcription."
)


class WhisperCapture:
    """Transcribes system audio in a child process and streams lines back.

    Callbacks fire on a reader thread, so a Tk caller must marshal them with
    `root.after(0, ...)`. The public surface is unchanged from the in-process
    version, so callers only had to keep calling start() and stop().
    """

    def __init__(
        self,
        on_text: Callable[[str, str], None],
        *,
        model_size: str = "base.en",
        language: str | None = "en",
        source: str = "loopback",   # 'loopback' (system audio) | 'mic'
        device: str = "cpu",
        compute_type: str = "int8",
        download_root: str | None = None,
        on_status: Callable[[str], None] | None = None,
        on_error: Callable[[str], None] | None = None,
    ) -> None:
        self.on_text = on_text
        self.on_status = on_status or (lambda _m: None)
        # Failures are multi-line and actionable, so they get their own channel
        # rather than being crammed into a one-line status bar.
        self.on_error = on_error or self.on_status
        self.model_size = model_size
        self.language = language
        self.source = source
        self.device = device
        self.compute_type = compute_type
        self.download_root = download_root or _default_download_root()

        self._link = worker_ipc.WorkerLink(
            self._argv(),
            on_message=self._handle,
            on_error=self._on_error,
            empty_hint=(
                "Audio capture stopped without transcribing anything. Check that "
                "sound is actually playing through your default output device."
            ),
        )

    # ---- lifecycle ----
    def start(self) -> None:
        self._link.start()

    def stop(self) -> None:
        self._link.cancel()

    def is_running(self) -> bool:
        return self._link.is_running()

    # ---- internals ----
    def _argv(self) -> list[str]:
        return worker_ipc.worker_argv("listen", Path(__file__).resolve(), [
            "--model", self.model_size,
            "--language", self.language or "auto",
            "--source", self.source,
            "--device", self.device,
            "--compute-type", self.compute_type,
            *(("--download-root", self.download_root) if self.download_root else ()),
        ])

    def _handle(self, msg: dict) -> bool:
        kind = msg.get("type")
        if kind == "text":
            text = str(msg.get("text") or "").strip()
            if text:
                self.on_text(str(msg.get("speaker") or ""), text)
        elif kind == "status":
            self.on_status(str(msg.get("message", "")))
        elif kind == "error":
            self.on_error(str(msg.get("message", "Unknown capture error.")))
            return True
        return False

    def _on_error(self, message: str) -> None:
        self.on_error(message)


def _default_download_root() -> str | None:
    """Keep model downloads with the rest of Cue's data, off the system drive."""
    try:
        import config as cfg_mod
        return str(cfg_mod.DATA_DIR / "whisper-models")
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Worker side — only ever runs in the child process
# ---------------------------------------------------------------------------

def run_worker(argv: list[str]) -> int:
    """Capture system audio and transcribe it until the parent kills us."""
    import argparse

    p = argparse.ArgumentParser(prog="whisper_capture")
    p.add_argument("--model", default="base.en")
    p.add_argument("--language", default="en")
    p.add_argument("--source", default="loopback", choices=["loopback", "mic"])
    p.add_argument("--device", default="cpu")
    p.add_argument("--compute-type", dest="compute_type", default="int8")
    p.add_argument("--download-root", dest="download_root", default=None)
    args = p.parse_args(argv)

    try:
        import numpy as np
        import soundcard as sc
        from faster_whisper import WhisperModel
    except ImportError as e:
        worker_ipc.emit_error(f"{MISSING_DEPS_HINT}\n\n({e})")
        return 1

    worker_ipc.emit_status(f"Loading the {args.model} model…")
    try:
        model = WhisperModel(
            args.model,
            device=args.device,
            compute_type=args.compute_type,
            download_root=args.download_root,
        )
    except Exception as e:
        worker_ipc.emit_error(f"Could not load the speech model: {type(e).__name__}: {e}")
        return 1

    try:
        target = "your microphone" if args.source == "mic" else str(sc.default_speaker().name)
    except Exception:
        target = "the default audio device"

    recorder = _open_recorder_bounded(sc, args.source, target)
    if recorder is None:
        return 1

    worker_ipc.emit_status(f"Listening (source={args.source}, model={args.model}).")
    language = None if args.language in ("", "auto") else args.language
    overlap_samples = OVERLAP_SECONDS * SAMPLE_RATE
    chunk_samples = CHUNK_SECONDS * SAMPLE_RATE
    carry = np.zeros(0, dtype=np.float32)

    try:
        with recorder as rec:
            while True:
                try:
                    data = rec.record(numframes=chunk_samples - len(carry))
                    if data.ndim > 1:
                        data = data.mean(axis=1)
                    audio = np.concatenate([carry, data.astype(np.float32)])
                except Exception as e:
                    worker_ipc.emit_status(f"Audio read error: {type(e).__name__}: {e}")
                    continue

                if len(audio) < chunk_samples:
                    continue

                window = audio[:chunk_samples]
                carry = audio[-overlap_samples:].copy()

                # Skip near-silent windows: saves cycles and stops Whisper
                # inventing speech where there is none.
                if float(np.sqrt(np.mean(window ** 2))) < MIN_RMS:
                    continue

                try:
                    segments, _info = model.transcribe(
                        window,
                        language=language,
                        beam_size=1,          # live: latency beats accuracy
                        condition_on_previous_text=False,
                        vad_filter=True,
                        vad_parameters={"min_silence_duration_ms": 500},
                    )
                    for seg in segments:
                        text = (seg.text or "").strip()
                        if text:
                            worker_ipc.emit({"type": "text", "speaker": "", "text": text})
                except Exception as e:
                    worker_ipc.emit_status(f"Transcribe error: {type(e).__name__}: {e}")
    except KeyboardInterrupt:
        pass
    except Exception as e:
        worker_ipc.emit_error(f"Capture stopped: {type(e).__name__}: {e}")
        return 1
    return 0


DEVICE_OPEN_TIMEOUT = 150   # seconds before we give up on the audio device
DEVICE_OPEN_TICK = 15       # how often to reassure the user we're still trying


def _open_recorder_bounded(sc, source: str, target: str):
    """Open the audio device, but never block forever. None means give up.

    Building a WASAPI loopback recorder is wildly variable: under a second on
    Bluetooth headphones, 30 to 95 seconds on an HDMI audio device, and it can block
    indefinitely against an output with no active sink, such as a monitor that has
    gone to sleep. Waiting silently on that looks identical to a frozen app, so the
    open runs on a daemon thread we are willing to abandon: returning lets the
    process exit and takes the stuck call with it.
    """
    import threading
    import time

    out: dict = {}

    def open_it() -> None:
        try:
            out["recorder"] = _open_recorder(sc, source)
        except Exception as e:  # noqa: BLE001 - reported to the user verbatim
            out["error"] = e

    worker_ipc.emit_status(f"Opening {target}… this can take a minute on some devices.")
    thread = threading.Thread(target=open_it, daemon=True)
    thread.start()

    waited = 0
    while thread.is_alive() and waited < DEVICE_OPEN_TIMEOUT:
        thread.join(timeout=DEVICE_OPEN_TICK)
        waited += DEVICE_OPEN_TICK
        if thread.is_alive():
            worker_ipc.emit_status(f"Still opening {target}… {waited}s so far.")

    if "recorder" in out:
        return out["recorder"]

    if "error" in out:
        e = out["error"]
        worker_ipc.emit_error(
            f"Could not open {target}: {type(e).__name__}: {e}\n\n"
            "Check that your default playback device exists and isn't muted."
        )
    else:
        worker_ipc.emit_error(
            f"Gave up waiting for {target} after {DEVICE_OPEN_TIMEOUT} seconds.\n\n"
            "Windows can block indefinitely on an output that has no active sound, "
            "for example an HDMI monitor that has gone to sleep.\n\n"
            "Try this:\n"
            "  1. Set the output you actually listen through (headphones, speakers) "
            "as the Windows default playback device.\n"
            "  2. Play any sound to wake it up.\n"
            "  3. Start capturing again.\n\n"
            "The Teams desktop and Pick a window sources don't touch audio at all, "
            "so they work regardless."
        )
    return None


def _open_recorder(sc, source: str):
    """A context-manageable recorder for the chosen source."""
    if source == "mic":
        return sc.default_microphone().recorder(
            samplerate=SAMPLE_RATE, channels=1, blocksize=2048
        )
    # Default: WASAPI loopback of the default speaker, i.e. whatever you can hear.
    speaker = sc.default_speaker()
    loopback = sc.get_microphone(id=str(speaker.name), include_loopback=True)
    return loopback.recorder(samplerate=SAMPLE_RATE, channels=1, blocksize=2048)


def main(argv: list[str] | None = None) -> int:
    code = worker_ipc.maybe_run_worker(argv)
    if code is not None:
        return code
    print(__doc__)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
