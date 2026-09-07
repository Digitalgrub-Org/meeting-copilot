"""Import the transcript of a Teams meeting recording from its Transcript pane.

Teams shows a transcript beside a recording, but whether you may *download* it is a
tenant policy decision, and often the answer is no. The text is still on screen, and
the Teams desktop client exposes it through Windows UI Automation, so Cue can read it
the same way it reads live captions.

Two problems make this more than "run the extractor once":

1. **The pane is virtualized.** Only the entries currently scrolled into view exist in
   the accessibility tree. A two-hour meeting shows about two and a half minutes at a
   time. Getting the whole transcript means scrolling and re-reading repeatedly, then
   merging the passes. That scrolling lives in the PowerShell side; this module is the
   merge.

2. **Entries arrive as fragments.** Each entry is a header, then the spoken text. The
   header is the *screen-reader* form of the timestamp, so it reads
   `Ada Lovelace 0 minutes 03 seconds` rather than `0:03`, and Teams tends to expose
   it twice with slightly different spacing. Speaker detection built for live captions
   does not recognise that shape, so this module has its own parser.

Parent-side only, standard library only: safe to import from Tk.
"""

from __future__ import annotations

import re
import sys
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path

import worker_ipc

# "Ada Lovelace 0 minutes 03 seconds", "Ada Lovelace1 hours 2 minutes 5 seconds", with or
# without the space before the digits, optional trailing period. Case-insensitive so
# a localized capital "Minutes" still matches.
HEADER_RE = re.compile(
    r"^(?P<name>[A-Z][^\d]{1,60}?)\s*"
    r"(?:(?P<h>\d+)\s*hours?\s*)?"
    r"(?P<m>\d+)\s*minutes?\s*"
    r"(?P<s>\d+)\s*seconds?\.?$",
    re.IGNORECASE,
)

# The extractor prefixes lines with their control type; strip it.
_TYPE_PREFIX_RE = re.compile(r"^\[[^\]]+\]\s*")
_WS_RE = re.compile(r"\s+")

# Interface text that appears inside the transcript pane itself.
_PANE_CHROME = frozenset({
    "transcript", "notes", "ai summary", "download", "search", "sync to video",
    "ai-generated content may be incorrect", "is this transcript useful?",
    "started transcription", "stopped transcription", "shared files",
    "no files were shared.", "speakers", "show more", "show less",
})


@dataclass
class Entry:
    speaker: str
    seconds: int
    lines: list[str] = field(default_factory=list)

    @property
    def text(self) -> str:
        return " ".join(l.strip() for l in self.lines if l.strip())

    @property
    def stamp(self) -> str:
        h, rem = divmod(self.seconds, 3600)
        m, s = divmod(rem, 60)
        return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"

    @property
    def key(self) -> tuple[str, int]:
        return (self.speaker.lower(), self.seconds)


def parse_header(line: str) -> tuple[str, int] | None:
    """(speaker, seconds) if the line is an entry header, else None."""
    m = HEADER_RE.match(line.strip())
    if not m:
        return None
    name = _WS_RE.sub(" ", m.group("name")).strip(" .,:-")
    if not name:
        return None
    secs = int(m.group("h") or 0) * 3600 + int(m.group("m")) * 60 + int(m.group("s"))
    return name, secs


def _clean(line: str) -> str:
    return _WS_RE.sub(" ", _TYPE_PREFIX_RE.sub("", line)).strip()


def parse_pane(raw: str) -> list[Entry]:
    """Turn one extractor dump of the pane into ordered entries.

    A header opens an entry; following non-header lines are its speech. The doubled
    header Teams exposes collapses onto the same entry because the key matches.
    Text before any header is dropped, since it can only be interface chrome.
    """
    entries: list[Entry] = []
    by_key: dict[tuple[str, int], Entry] = {}
    current: Entry | None = None

    for raw_line in raw.splitlines():
        line = _clean(raw_line)
        if not line or line.lower() in _PANE_CHROME:
            continue
        header = parse_header(line)
        if header:
            name, secs = header
            key = (name.lower(), secs)
            current = by_key.get(key)
            if current is None:
                current = Entry(speaker=name, seconds=secs)
                by_key[key] = current
                entries.append(current)
            continue
        if current is None:
            continue
        # The same fragment can be exposed twice too; don't double the speech.
        if line not in current.lines:
            current.lines.append(line)

    # Chronological regardless of the order the tree was walked in. Attribution does
    # not depend on this (each fragment binds to the header before it), coverage does.
    return sorted((e for e in entries if e.lines), key=lambda e: (e.seconds, e.speaker.lower()))


def merge(passes: Iterable[list[Entry]]) -> list[Entry]:
    """Combine entries from successive scroll positions into one transcript.

    Passes overlap heavily, which is what makes the merge safe: identical
    (speaker, timestamp) keys are the same entry seen twice. When the same entry was
    seen with more text in one pass than another, keep the fuller version, since a
    partially rendered entry at the edge of the viewport is the usual cause.
    """
    best: dict[tuple[str, int], Entry] = {}
    for entries in passes:
        for e in entries:
            have = best.get(e.key)
            if have is None or len(e.text) > len(have.text):
                best[e.key] = e
    return sorted(best.values(), key=lambda e: (e.seconds, e.speaker.lower()))


def coverage(entries: list[Entry]) -> tuple[int, int]:
    """(first, last) timestamp in seconds, or (0, 0) for nothing."""
    if not entries:
        return (0, 0)
    return (entries[0].seconds, entries[-1].seconds)


def to_text(entries: list[Entry], *, timestamps: bool = True) -> str:
    """Speaker-attributed transcript, one entry per paragraph."""
    out = []
    for e in entries:
        head = f"[{e.stamp}] {e.speaker}" if timestamps else e.speaker
        out.append(f"{head}: {e.text}")
    return "\n\n".join(out)


def to_cue_transcript(entries: list[Entry]) -> str:
    """The `Speaker: text` shape Cue's live transcript and knowledge base expect."""
    return to_text(entries, timestamps=False)


# ---------------------------------------------------------------------------
# Multi-pass dumps from the extractor's -Collect mode
# ---------------------------------------------------------------------------

_PASS_MARKER_RE = re.compile(r"^---\s*pass\s+\d+\s*---\s*$")


def parse_passes(raw: str) -> list[list[Entry]]:
    """Split a -Collect dump on its pass markers and parse each pass separately.

    Kept as separate passes, rather than one big dump, so merge() can prefer the
    fullest single rendering of each entry instead of stitching fragments together in
    whatever order they scrolled past.
    """
    chunks: list[list[str]] = []
    current: list[str] = []
    for line in raw.splitlines():
        if _PASS_MARKER_RE.match(line.strip()):
            if current:
                chunks.append(current)
            current = []
        else:
            current.append(line)
    if current:
        chunks.append(current)
    return [parse_pane("\n".join(c)) for c in chunks if any(l.strip() for l in c)]


def extractor_script() -> Path:
    """The UI Automation script, wherever this build keeps it."""
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    return base / "extract_teams_transcript.ps1"


def import_argv(out_path: str | Path) -> list[str]:
    return [
        "powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
        "-File", str(extractor_script()),
        "-OutPath", str(out_path),
        "-TeamsMode", "-Collect",
    ]


class RecordingImport:
    """Drive the extractor's -Collect mode and hand back a merged transcript.

    Runs through worker_ipc.WorkerLink, so progress streams in as the pane scrolls
    and a stuck or crashed script becomes a message rather than a hang. Callbacks
    fire on the reader thread; a Tk caller marshals them with `after(0, ...)`.
    """

    def __init__(
        self,
        out_path: str | Path,
        *,
        on_progress: Callable[[dict], None] | None = None,
        on_done: Callable[[list[Entry], dict], None] | None = None,
        on_error: Callable[[str], None] | None = None,
    ) -> None:
        self.out_path = Path(out_path)
        self.on_progress = on_progress or (lambda _d: None)
        self.on_done = on_done or (lambda _e, _d: None)
        self.on_error = on_error or (lambda _m: None)
        self._link = worker_ipc.WorkerLink(
            import_argv(self.out_path),
            on_message=self._handle,
            on_error=self.on_error,
            crash_hint=("The transcript reader stopped unexpectedly. Make sure the "
                        "recording is open in Teams with the Transcript tab showing."),
            empty_hint=("The transcript reader finished without reporting anything. "
                        "Is the Transcript tab open in Teams?"),
        )

    def start(self) -> None:
        self._link.start()

    def cancel(self) -> None:
        self._link.cancel()

    def is_running(self) -> bool:
        return self._link.is_running()

    def _handle(self, msg: dict) -> bool:
        kind = msg.get("type")
        if kind == "progress":
            self.on_progress(msg)
            return False
        if kind == "done":
            try:
                raw = self.out_path.read_text(encoding="utf-8", errors="replace")
                entries = merge(parse_passes(raw))
            except Exception as e:  # noqa: BLE001 - surfaced to the user
                self.on_error(f"Read the pane but could not parse it: {type(e).__name__}: {e}")
                return True
            self.on_done(entries, msg)
            return True
        if kind == "error":
            self.on_error(str(msg.get("message", "Unknown error reading the transcript.")))
            return True
        return False
