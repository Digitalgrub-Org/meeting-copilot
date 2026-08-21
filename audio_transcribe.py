"""File transcription for Cue — turn an audio/video file into a clean transcript.

Built for the "I got a voice note on WhatsApp, just transcribe it" case: pick a
file, get readable text. Handles anything ffmpeg can decode (.opus voice notes,
.m4a, .mp3, .wav, .mp4, …), auto-detects the spoken language, and shapes Whisper's
subtitle-sized fragments into paragraphs you can actually read.

WHY THIS RUNS IN A SUBPROCESS
-----------------------------
faster-whisper's native backend (ctranslate2) can hard-crash the host process on
Windows/Anaconda setups — an access violation, not a Python exception, so there is
nothing to catch and the whole app dies with it. Cue therefore never loads the
model in its own process: `TranscribeJob` spawns a worker and streams results back
over stdout as JSON Lines. A crash in the worker becomes an error message instead
of a dead app. See worker_ipc for the shared plumbing.

That split is what the two halves of this module are:

  Parent side (safe to import from Tk — stdlib only at module level)
    TranscribeJob        spawn/stream/cancel a worker
    Segment, Result      plain data
    to_plain / to_timestamped / to_srt / to_vtt / to_markdown
    paragraphs()         merge fragments into readable blocks
    QUALITY_PRESETS, LANGUAGES, SUPPORTED_EXTS, FILETYPES

  Worker side (imports faster_whisper — only ever inside the child process)
    transcribe_file()    the actual model call
    run_worker()         JSON-Lines protocol, dispatched by worker_ipc

CLI:
    python audio_transcribe.py note.opus                  # print the transcript
    python audio_transcribe.py note.opus -o out.txt       # ...or write it
    python audio_transcribe.py note.opus --format srt     # plain|timestamps|srt|vtt|md
"""

from __future__ import annotations

import re
import sys
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass, field
from pathlib import Path

import worker_ipc

# ---------------------------------------------------------------------------
# Shared constants (parent + worker)
# ---------------------------------------------------------------------------

# Anything ffmpeg/PyAV can decode. WhatsApp voice notes are .opus; iOS shares
# .m4a; forwarded video notes come as .mp4.
SUPPORTED_EXTS = (
    ".opus", ".ogg", ".oga", ".m4a", ".mp3", ".wav", ".aac", ".flac", ".wma",
    ".amr", ".3gp", ".3gpp", ".aiff", ".aif", ".caf", ".mp4", ".m4v", ".mov",
    ".mkv", ".webm", ".avi", ".wmv", ".flv", ".mpg", ".mpeg", ".ts",
)

FILETYPES = [
    ("Audio & video", " ".join(f"*{e}" for e in SUPPORTED_EXTS)),
    ("WhatsApp voice note", "*.opus *.ogg *.m4a *.mp4"),
    ("Audio", "*.opus *.ogg *.oga *.m4a *.mp3 *.wav *.aac *.flac *.wma *.amr"),
    ("Video", "*.mp4 *.m4v *.mov *.mkv *.webm *.avi *.wmv"),
    ("All files", "*.*"),
]

# label -> (whisper model, note shown in the UI)
QUALITY_PRESETS: dict[str, tuple[str, str]] = {
    "Fast (tiny)":       ("tiny",     "~75 MB · roughly real-time · rough draft"),
    "Quick (base)":      ("base",     "~145 MB · ~2x faster than audio"),
    "Balanced (small)":  ("small",    "~480 MB · the sweet spot — recommended"),
    "Accurate (medium)": ("medium",   "~1.5 GB · noticeably better on accents"),
    "Best (large-v3)":   ("large-v3", "~3 GB · slowest, most accurate"),
}
DEFAULT_QUALITY = "Balanced (small)"

# (code, label). "auto" lets Whisper detect — the right default for mixed-language
# voice notes. The rest are ordered for this app's users.
LANGUAGES: list[tuple[str, str]] = [
    ("auto", "Auto-detect"),
    ("en", "English"),
    ("ta", "Tamil"),
    ("hi", "Hindi"),
    ("te", "Telugu"),
    ("ml", "Malayalam"),
    ("kn", "Kannada"),
    ("mr", "Marathi"),
    ("bn", "Bengali"),
    ("gu", "Gujarati"),
    ("ur", "Urdu"),
    ("ar", "Arabic"),
    ("es", "Spanish"),
    ("fr", "French"),
    ("de", "German"),
    ("pt", "Portuguese"),
    ("it", "Italian"),
    ("nl", "Dutch"),
    ("ru", "Russian"),
    ("zh", "Chinese"),
    ("ja", "Japanese"),
    ("ko", "Korean"),
]

LANGUAGE_NAMES = dict(LANGUAGES)

# Whisper invents these over silence and music — safe to drop when the segment
# also looks like non-speech. Matched case-insensitively, punctuation stripped.
_HALLUCINATION_PHRASES = frozenset((
    "thank you", "thanks for watching", "thank you for watching",
    "subscribe", "please subscribe", "like and subscribe",
    "subtitles by the amaraorg community", "amaraorg", "www.amara.org",
    "transcription by castingwordscom", "subtitles by castingwords",
    "bye", "bye bye", "okay", "ok", "you", "yeah", "hmm", "mm", "uh",
    "music", "applause", "laughter", "silence", "outro music", "intro music",
))
_PUNCT_RE = re.compile(r"[^\w\s]+")
_WS_RE = re.compile(r"\s+")


def _norm(text: str) -> str:
    return _WS_RE.sub(" ", _PUNCT_RE.sub("", text.lower())).strip()


_PROMPT_LEAD = "The following recording may mention:"


def build_initial_prompt(hints: str) -> str | None:
    """Turn the user's list of names and jargon into a prompt Whisper handles well.

    Whisper copies the *style* of its initial prompt, not just the vocabulary. Handing
    it a bare comma list ("Contoso, Northwind, Atlas API") makes it spell those words
    correctly and then drop sentence punctuation for the whole transcript — measurably
    worse output. Wrapping the same words in one properly punctuated sentence keeps
    the vocabulary bias and gives the model a correct style to copy.
    """
    hints = _WS_RE.sub(" ", (hints or "")).strip()
    if not hints:
        return None
    # Already written as prose? Leave the wording alone, just close the sentence.
    if re.search(r"[.!?][\s\"')\]]*$", hints):
        return hints
    return f"{_PROMPT_LEAD} {hints.rstrip(',;:')}."


# ---------------------------------------------------------------------------
# Data
# ---------------------------------------------------------------------------

@dataclass
class Segment:
    start: float
    end: float
    text: str

    @property
    def duration(self) -> float:
        return max(0.0, self.end - self.start)


@dataclass
class Result:
    segments: list[Segment] = field(default_factory=list)
    language: str = ""
    language_probability: float = 0.0
    duration: float = 0.0          # audio length in seconds
    model: str = ""
    device: str = ""
    elapsed: float = 0.0           # wall-clock seconds spent transcribing
    source: str = ""               # original filename

    @property
    def text(self) -> str:
        return to_plain(self.segments)

    @property
    def language_label(self) -> str:
        return LANGUAGE_NAMES.get(self.language, self.language or "unknown")


# ---------------------------------------------------------------------------
# Formatting — Whisper fragments into something readable
# ---------------------------------------------------------------------------

PARAGRAPH_MAX_CHARS = 500   # a comfortable block of prose to read on screen
PARAGRAPH_PAUSE_GAP = 1.2   # seconds of silence that starts a new paragraph


def paragraphs(
    segments: Iterable[Segment],
    *,
    max_chars: int = PARAGRAPH_MAX_CHARS,
    pause_gap: float = PARAGRAPH_PAUSE_GAP,
) -> list[Segment]:
    """Merge subtitle-sized segments into paragraph-sized ones.

    Whisper emits ~5-second chunks, which read like subtitles rather than prose.
    A new paragraph starts on a noticeable pause, or once the current one has run
    long and we're at a sentence boundary.
    """
    out: list[Segment] = []
    cur: Segment | None = None
    for seg in segments:
        text = seg.text.strip()
        if not text:
            continue
        if cur is None:
            cur = Segment(seg.start, seg.end, text)
            continue
        gap = seg.start - cur.end
        ends_sentence = cur.text.endswith((".", "!", "?", "…", "。", "؟"))
        too_long = len(cur.text) >= max_chars
        if gap >= pause_gap or (too_long and ends_sentence) or len(cur.text) >= max_chars * 2:
            out.append(cur)
            cur = Segment(seg.start, seg.end, text)
        else:
            cur = Segment(cur.start, seg.end, f"{cur.text} {text}")
    if cur is not None:
        out.append(cur)
    return out


def _stamp(seconds: float) -> str:
    seconds = max(0.0, seconds)
    h, rem = divmod(int(seconds), 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def _srt_stamp(seconds: float) -> str:
    seconds = max(0.0, seconds)
    ms = int(round((seconds - int(seconds)) * 1000))
    h, rem = divmod(int(seconds), 3600)
    m, s = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def _vtt_stamp(seconds: float) -> str:
    return _srt_stamp(seconds).replace(",", ".")


def to_plain(segments: Iterable[Segment]) -> str:
    """Readable prose — paragraphs separated by a blank line, no timestamps."""
    return "\n\n".join(p.text for p in paragraphs(segments))


def to_timestamped(segments: Iterable[Segment]) -> str:
    """Paragraphs prefixed with the time they start — good for skimming."""
    return "\n\n".join(f"[{_stamp(p.start)}]  {p.text}" for p in paragraphs(segments))


def to_srt(segments: Iterable[Segment]) -> str:
    lines: list[str] = []
    for i, seg in enumerate(segments, 1):
        lines.append(str(i))
        lines.append(f"{_srt_stamp(seg.start)} --> {_srt_stamp(seg.end)}")
        lines.append(seg.text.strip())
        lines.append("")
    return "\n".join(lines)


def to_vtt(segments: Iterable[Segment]) -> str:
    lines = ["WEBVTT", ""]
    for seg in segments:
        lines.append(f"{_vtt_stamp(seg.start)} --> {_vtt_stamp(seg.end)}")
        lines.append(seg.text.strip())
        lines.append("")
    return "\n".join(lines)


def to_markdown(result: Result) -> str:
    head = [f"# Transcript — {result.source or 'audio'}", ""]
    meta = []
    if result.duration:
        meta.append(f"**Length:** {_stamp(result.duration)}")
    if result.language:
        pct = f" ({result.language_probability:.0%} confident)" if result.language_probability else ""
        meta.append(f"**Language:** {result.language_label}{pct}")
    if result.model:
        meta.append(f"**Model:** whisper {result.model} ({result.device or 'cpu'})")
    if meta:
        head.append(" · ".join(meta))
    return "\n".join(head) + "\n\n" + to_plain(result.segments) + "\n"


FORMATTERS: dict[str, Callable[[Result], str]] = {
    "plain": lambda r: to_plain(r.segments),
    "timestamps": lambda r: to_timestamped(r.segments),
    "srt": lambda r: to_srt(r.segments),
    "vtt": lambda r: to_vtt(r.segments),
    "md": to_markdown,
}


# ---------------------------------------------------------------------------
# Cleanup — Whisper's known failure modes on phone audio
# ---------------------------------------------------------------------------

def clean_segments(
    raw: Iterable[tuple[float, float, str, float]],
    *,
    max_repeats: int = 2,
) -> Iterator[Segment]:
    """Drop hallucinated filler and collapse repetition loops, lazily.

    Each input item is (start, end, text, no_speech_prob). Two things go wrong when
    Whisper meets compressed phone audio: it narrates silence ("Thanks for
    watching!") and it gets stuck repeating a line. Both are cheap to spot here and
    impossible to un-see in the final transcript.

    This is a generator on purpose. Building a list here would drain the whole
    transcription before the caller saw its first segment, which kills both the
    progress bar and the ability to keep partial output after a cancel.
    """
    repeat_key: str | None = None
    repeat_count = 0
    for start, end, text, no_speech in raw:
        text = _WS_RE.sub(" ", (text or "").strip())
        if not text:
            continue
        key = _norm(text)
        if not key:
            continue
        # Canned phrase over probable silence -> hallucination, not speech.
        if key in _HALLUCINATION_PHRASES and no_speech >= 0.5:
            continue
        if key == repeat_key:
            repeat_count += 1
            if repeat_count > max_repeats:
                continue
        else:
            repeat_key, repeat_count = key, 1
        yield Segment(start=float(start), end=float(end), text=text)


# ---------------------------------------------------------------------------
# Worker side — the only place faster_whisper is imported
# ---------------------------------------------------------------------------

def _is_vad_failure(exc: BaseException) -> bool:
    """Is this exception the VAD stack (onnxruntime) failing to load, not a real error?"""
    blob = " ".join(str(e).lower() for e in (exc, getattr(exc, "__cause__", "") or ""))
    return "onnxruntime" in blob or "vad" in blob


def transcribe_file(
    path: str | Path,
    *,
    model_size: str = "small",
    language: str | None = None,
    device: str = "cpu",
    compute_type: str = "int8",
    beam_size: int = 5,
    initial_prompt: str | None = None,
    condition_on_previous_text: bool = True,
    vad_filter: bool = True,
    download_root: str | None = None,
    on_status: Callable[[str], None] | None = None,
    on_info: Callable[[dict], None] | None = None,
    on_segment: Callable[[Segment], None] | None = None,
    should_cancel: Callable[[], bool] | None = None,
) -> Result:
    """Transcribe one file. Runs in the worker process — never call this from Tk.

    Callbacks fire as work completes so the caller can stream progress. Raises on
    a recoverable failure; a native crash takes the process down instead, which is
    exactly why this runs isolated.
    """
    import time

    from faster_whisper import WhisperModel

    src = Path(path)
    if not src.exists():
        raise FileNotFoundError(f"No such file: {src}")

    say = on_status or (lambda _m: None)
    say(f"Loading the {model_size} model…")
    started = time.time()
    model = WhisperModel(
        model_size,
        device=device,
        compute_type=compute_type,
        download_root=download_root,
    )
    say(f"Model ready in {time.time() - started:.0f}s. Decoding audio…")

    started = time.time()
    options = dict(
        language=None if language in (None, "", "auto") else language,
        beam_size=beam_size,
        best_of=beam_size,
        initial_prompt=build_initial_prompt(initial_prompt or ""),
        condition_on_previous_text=condition_on_previous_text,
        vad_filter=vad_filter,
        vad_parameters={"min_silence_duration_ms": 500},
        # Accuracy knobs: it's a file, not a live stream, so spend the cycles.
        temperature=[0.0, 0.2, 0.4, 0.6, 0.8, 1.0],
        compression_ratio_threshold=2.4,
        log_prob_threshold=-1.0,
        no_speech_threshold=0.6,
    )
    try:
        segments_iter, info = model.transcribe(str(src), **options)
    except Exception as e:
        # Silence trimming runs a Silero VAD through onnxruntime, whose native library
        # is a common casualty of a mismatched runtime. A transcript without VAD is
        # still a good transcript, so degrade instead of failing outright.
        if not (vad_filter and _is_vad_failure(e)):
            raise
        say(f"Silence trimming unavailable ({type(e).__name__}); transcribing without it.")
        options["vad_filter"] = False
        options.pop("vad_parameters", None)
        segments_iter, info = model.transcribe(str(src), **options)

    result = Result(
        language=info.language or "",
        language_probability=float(info.language_probability or 0.0),
        duration=float(info.duration or 0.0),
        model=model_size,
        device=device,
        source=src.name,
    )
    if on_info:
        on_info({
            "duration": result.duration,
            "language": result.language,
            "language_probability": result.language_probability,
            "model": model_size,
            "device": device,
        })
    say(
        f"Transcribing {_stamp(result.duration)} of "
        f"{LANGUAGE_NAMES.get(result.language, result.language or 'speech')}…"
    )

    def _stream() -> Iterator[tuple[float, float, str, float]]:
        for seg in segments_iter:
            if should_cancel and should_cancel():
                break
            yield (
                float(seg.start),
                float(seg.end),
                seg.text or "",
                float(getattr(seg, "no_speech_prob", 0.0) or 0.0),
            )

    kept: list[Segment] = []
    for seg in clean_segments(_stream()):
        kept.append(seg)
        if on_segment:
            on_segment(seg)

    result.segments = kept
    result.elapsed = time.time() - started
    return result


# ---------------------------------------------------------------------------
# Parent side — spawn a worker, stream its output, allow cancel
# ---------------------------------------------------------------------------

NO_SPEECH_HINT = (
    "The speech engine finished without producing any text. "
    "The file may contain no speech."
)


class TranscribeJob:
    """Runs a transcription in a child process and streams results back.

    Callbacks fire on the reader thread — a Tk caller must marshal them with
    `root.after(0, ...)`. Every callback is optional. The spawn, cancel and
    crash-explanation plumbing lives in worker_ipc, shared with live capture.
    """

    def __init__(
        self,
        path: str | Path,
        *,
        model_size: str = "small",
        language: str = "auto",
        device: str = "cpu",
        compute_type: str = "int8",
        beam_size: int = 5,
        initial_prompt: str = "",
        condition_on_previous_text: bool = True,
        vad_filter: bool = True,
        download_root: str | None = None,
        on_status: Callable[[str], None] | None = None,
        on_info: Callable[[dict], None] | None = None,
        on_segment: Callable[[Segment], None] | None = None,
        on_done: Callable[[Result], None] | None = None,
        on_error: Callable[[str], None] | None = None,
    ) -> None:
        self.path = Path(path)
        self.model_size = model_size
        self.language = language or "auto"
        self.device = device
        self.compute_type = compute_type
        self.beam_size = beam_size
        self.initial_prompt = initial_prompt or ""
        self.condition_on_previous_text = condition_on_previous_text
        self.vad_filter = vad_filter
        self.download_root = download_root

        self.on_status = on_status or (lambda _m: None)
        self.on_info = on_info or (lambda _d: None)
        self.on_segment = on_segment or (lambda _s: None)
        self.on_done = on_done or (lambda _r: None)
        self.on_error = on_error or (lambda _m: None)

        self.result = Result(source=self.path.name, model=model_size, device=device)
        self._link = worker_ipc.WorkerLink(
            self._argv(),
            on_message=self._handle,
            on_error=self.on_error,
            empty_hint=NO_SPEECH_HINT,
        )

    # ---- lifecycle ----
    def start(self) -> None:
        self._link.start()

    def cancel(self) -> None:
        self._link.cancel()

    def is_running(self) -> bool:
        return self._link.is_running()

    @property
    def cancelled(self) -> bool:
        return self._link.cancelled

    # ---- internals ----
    def _argv(self) -> list[str]:
        return worker_ipc.worker_argv("transcribe", Path(__file__).resolve(), [
            str(self.path),
            "--model", self.model_size,
            "--language", self.language,
            "--device", self.device,
            "--compute-type", self.compute_type,
            "--beam-size", str(self.beam_size),
            *(("--prompt", self.initial_prompt) if self.initial_prompt else ()),
            *(() if self.condition_on_previous_text else ("--no-context",)),
            *(() if self.vad_filter else ("--no-vad",)),
            *(("--download-root", self.download_root) if self.download_root else ()),
        ])

    def _handle(self, msg: dict) -> bool:
        """Dispatch one worker message. Returns True once the job is finished."""
        kind = msg.get("type")
        if kind == "status":
            self.on_status(str(msg.get("message", "")))
        elif kind == "info":
            self.result.duration = float(msg.get("duration") or 0.0)
            self.result.language = str(msg.get("language") or "")
            self.result.language_probability = float(msg.get("language_probability") or 0.0)
            self.result.model = str(msg.get("model") or self.result.model)
            self.result.device = str(msg.get("device") or self.result.device)
            self.on_info(msg)
        elif kind == "segment":
            seg = Segment(
                start=float(msg.get("start") or 0.0),
                end=float(msg.get("end") or 0.0),
                text=str(msg.get("text") or ""),
            )
            self.result.segments.append(seg)
            self.on_segment(seg)
        elif kind == "done":
            self.result.elapsed = float(msg.get("elapsed") or 0.0)
            self.on_done(self.result)
            return True
        elif kind == "error":
            self.on_error(str(msg.get("message", "Unknown transcription error.")))
            return True
        return False


def default_download_root() -> str | None:
    """Keep model downloads next to the rest of Cue's data, off the system drive."""
    try:
        import config as cfg_mod
        return str(cfg_mod.DATA_DIR / "whisper-models")
    except Exception:
        return None


def model_for_quality(label: str) -> str:
    return QUALITY_PRESETS.get(label, QUALITY_PRESETS[DEFAULT_QUALITY])[0]


def quality_for_model(model: str) -> str:
    for label, (name, _note) in QUALITY_PRESETS.items():
        if name == model:
            return label
    return DEFAULT_QUALITY


def is_supported(path: str | Path) -> bool:
    return Path(path).suffix.lower() in SUPPORTED_EXTS


def format_duration(seconds: float) -> str:
    """Public alias — the UI shows the same timestamps this module writes."""
    return _stamp(seconds)


# ---------------------------------------------------------------------------
# Entry point — worker protocol and a human-friendly CLI
# ---------------------------------------------------------------------------

_emit = worker_ipc.emit


def run_worker(argv: list[str]) -> int:
    """Worker entry point, dispatched by worker_ipc.maybe_run_worker()."""
    return _run_worker(_build_parser().parse_args(argv))


def _run_worker(args) -> int:
    """JSON-Lines protocol on stdout. One object per line, see module docstring."""
    try:
        result = transcribe_file(
            args.path,
            model_size=args.model,
            language=args.language,
            device=args.device,
            compute_type=args.compute_type,
            beam_size=args.beam_size,
            initial_prompt=args.prompt,
            condition_on_previous_text=not args.no_context,
            vad_filter=not args.no_vad,
            download_root=args.download_root,
            on_status=lambda m: _emit({"type": "status", "message": m}),
            on_info=lambda d: _emit({"type": "info", **d}),
            on_segment=lambda s: _emit(
                {"type": "segment", "start": s.start, "end": s.end, "text": s.text}
            ),
        )
    except ImportError as e:
        _emit({
            "type": "error",
            "message": (
                "Speech recognition isn't installed in this environment.\n\n"
                "    pip install faster-whisper\n\n"
                f"({e})"
            ),
        })
        return 1
    except FileNotFoundError as e:
        _emit({"type": "error", "message": str(e)})
        return 1
    except Exception as e:
        import traceback
        traceback.print_exc(file=sys.stderr)
        _emit({"type": "error", "message": f"{type(e).__name__}: {e}"})
        return 1

    _emit({"type": "done", "elapsed": result.elapsed, "segments": len(result.segments)})
    return 0


def _run_cli(args) -> int:
    def status(msg: str) -> None:
        print(f"… {msg}", file=sys.stderr, flush=True)

    result = transcribe_file(
        args.path,
        model_size=args.model,
        language=args.language,
        device=args.device,
        compute_type=args.compute_type,
        beam_size=args.beam_size,
        initial_prompt=args.prompt,
        condition_on_previous_text=not args.no_context,
        vad_filter=not args.no_vad,
        download_root=args.download_root or default_download_root(),
        on_status=status,
    )
    text = FORMATTERS.get(args.format, FORMATTERS["plain"])(result)
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
        print(
            f"Wrote {args.out} — {len(result.segments)} segments, "
            f"{result.language_label}, {_stamp(result.duration)} of audio "
            f"in {result.elapsed:.0f}s.",
            file=sys.stderr,
        )
    else:
        print(text)
    return 0


def _build_parser():
    import argparse

    p = argparse.ArgumentParser(
        prog="audio_transcribe",
        description="Transcribe an audio or video file to text (local Whisper).",
    )
    p.add_argument("path", help="audio/video file to transcribe")
    p.add_argument("-o", "--out", help="write the transcript here instead of stdout")
    p.add_argument("--format", default="plain", choices=sorted(FORMATTERS),
                   help="output format (default: plain)")
    p.add_argument("--model", default="small", help="whisper model (default: small)")
    p.add_argument("--language", default="auto",
                   help="language code, or 'auto' to detect (default: auto)")
    p.add_argument("--device", default="cpu", choices=["cpu", "cuda", "auto"])
    p.add_argument("--compute-type", dest="compute_type", default="int8")
    p.add_argument("--beam-size", dest="beam_size", type=int, default=5)
    p.add_argument("--prompt", default="", help="names/jargon to bias spelling")
    p.add_argument("--no-context", action="store_true",
                   help="don't feed previous text back in (breaks repetition loops)")
    p.add_argument("--no-vad", action="store_true", help="disable silence trimming")
    p.add_argument("--download-root", dest="download_root", default=None)
    return p


def main(argv: list[str] | None = None) -> int:
    # Cue launches this file as `--cue-worker transcribe …`; that has to be handled
    # before the normal CLI, which knows nothing about the JSON-Lines protocol.
    code = worker_ipc.maybe_run_worker(argv)
    if code is not None:
        return code
    args = _build_parser().parse_args(argv)
    try:
        return _run_cli(args)
    except KeyboardInterrupt:
        print("\nCancelled.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
