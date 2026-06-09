"""Universal meeting transcript capture via system audio + Whisper STT.

Works for any meeting platform (Teams / Meet / Zoom / phone) because it captures
the OS audio output (loopback) — whatever you hear, gets transcribed.

Pipeline:
  WASAPI loopback (soundcard) -> 8-sec audio chunks -> faster-whisper -> text -> callback

Threading:
  - One background thread runs the capture+transcribe loop
  - Each transcribed sentence is delivered via the on_text(speaker, text) callback
  - on_text is called from the worker thread; callers must marshal to UI via .after()

Defaults:
  - model: base.en (faster-whisper, ~150MB download on first use)
  - device: cpu, int8 quant
  - chunk: 8s with 1s overlap
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable

import numpy as np

try:
    import soundcard as sc
except ImportError as e:
    raise ImportError("soundcard package required: pip install soundcard") from e

try:
    from faster_whisper import WhisperModel
except ImportError as e:
    raise ImportError("faster-whisper required: pip install faster-whisper") from e


SAMPLE_RATE = 16000     # Whisper expects 16kHz
CHUNK_SECONDS = 8       # audio buffered per transcription pass
OVERLAP_SECONDS = 1     # overlap with prior chunk to catch boundary words
MIN_RMS = 0.003         # below this, skip transcription (silence)


class WhisperCapture:
    def __init__(
        self,
        on_text: Callable[[str, str], None],
        *,
        model_size: str = "base.en",
        language: str | None = "en",
        source: str = "loopback",  # 'loopback' (system audio) | 'mic'
        device: str = "cpu",
        compute_type: str = "int8",
        on_status: Callable[[str], None] | None = None,
    ) -> None:
        self.on_text = on_text
        self.on_status = on_status or (lambda _: None)
        self.model_size = model_size
        self.language = language
        self.source = source
        self.device = device
        self.compute_type = compute_type

        self._model: WhisperModel | None = None
        self._thread: threading.Thread | None = None
        self._stop_flag = threading.Event()

    # ---- Lifecycle ----
    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop_flag.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop_flag.set()
        if self._thread:
            self._thread.join(timeout=2.0)
        self._thread = None

    def is_running(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    # ---- Audio source ----
    def _open_recorder(self):
        """Returns a context-manageable recorder for the chosen source."""
        if self.source == "mic":
            mic = sc.default_microphone()
            return mic.recorder(samplerate=SAMPLE_RATE, channels=1, blocksize=2048)
        # Default: WASAPI loopback of the default speaker (captures system audio)
        speaker = sc.default_speaker()
        loopback = sc.get_microphone(id=str(speaker.name), include_loopback=True)
        return loopback.recorder(samplerate=SAMPLE_RATE, channels=1, blocksize=2048)

    # ---- Main loop ----
    def _run(self) -> None:
        try:
            self.on_status(f"Loading Whisper model '{self.model_size}'…")
            # Keep model downloads on the configured data drive (off C by default).
            try:
                import config as _cfg
                download_root = str((_cfg.DATA_DIR / "whisper-models"))
            except Exception:
                download_root = None
            self._model = WhisperModel(
                self.model_size,
                device=self.device,
                compute_type=self.compute_type,
                download_root=download_root,
            )
        except Exception as e:
            self.on_status(f"Whisper model load failed: {type(e).__name__}: {e}")
            return

        try:
            recorder_cm = self._open_recorder()
        except Exception as e:
            self.on_status(
                f"Audio source open failed: {type(e).__name__}: {e}\n"
                "Tip: ensure your default speaker exists and isn't muted."
            )
            return

        self.on_status(f"Listening (source={self.source}, model={self.model_size}).")
        overlap_samples = OVERLAP_SECONDS * SAMPLE_RATE
        chunk_samples = CHUNK_SECONDS * SAMPLE_RATE
        carry = np.zeros(0, dtype=np.float32)

        with recorder_cm as rec:
            while not self._stop_flag.is_set():
                try:
                    needed = chunk_samples - len(carry)
                    data = rec.record(numframes=needed)
                    if data.ndim > 1:
                        data = data.mean(axis=1)
                    audio = np.concatenate([carry, data.astype(np.float32)])
                except Exception as e:
                    self.on_status(f"Audio read error: {type(e).__name__}: {e}")
                    time.sleep(0.5)
                    continue

                if len(audio) < chunk_samples:
                    continue

                # Skip mostly-silent windows (saves cycles)
                rms = float(np.sqrt(np.mean(audio[:chunk_samples] ** 2)))
                if rms < MIN_RMS:
                    carry = audio[-overlap_samples:].copy()
                    continue

                try:
                    segments, _info = self._model.transcribe(
                        audio[:chunk_samples],
                        language=self.language,
                        beam_size=1,
                        condition_on_previous_text=False,
                        vad_filter=True,
                        vad_parameters={"min_silence_duration_ms": 500},
                    )
                    for seg in segments:
                        text = (seg.text or "").strip()
                        if text:
                            self.on_text("", text)
                except Exception as e:
                    self.on_status(f"Transcribe error: {type(e).__name__}: {e}")

                carry = audio[-overlap_samples:].copy()

        self.on_status("Stopped.")
