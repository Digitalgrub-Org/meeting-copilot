"""Download a transcript as a file: Markdown or plain text, into Downloads by default.

One implementation for every window that shows a transcript (the main window, a Teams
recording import), so the ⬇ Download button behaves the same everywhere.
"""

from __future__ import annotations

import ctypes
import re
import sys
import uuid
from pathlib import Path
from tkinter import filedialog

import transcript_cleaner as tc

FILE_TYPES = [("Markdown", "*.md"), ("Text", "*.txt")]
MARKDOWN_SUFFIXES = {".md", ".markdown"}

# Windows' Downloads folder. It can be moved or redirected (OneDrive, another drive),
# so ask the shell instead of assuming %USERPROFILE%\Downloads.
_FOLDERID_DOWNLOADS = "374DE290-123F-4565-9164-39C4925E467B"

_BAD_FILENAME_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_STAMP = re.compile(r"^(?P<stamp>\[\d{1,2}:\d{2}(?::\d{2})?\])\s+(?P<rest>.*)$", re.DOTALL)
_ORDERED_MARKER = re.compile(r"^(\d+)([.)])(\s)")
_BLOCK_MARKER = re.compile(r"^([#>]|[-+*](?=\s))")


class _GUID(ctypes.Structure):
    _fields_ = [("Data1", ctypes.c_ulong), ("Data2", ctypes.c_ushort),
                ("Data3", ctypes.c_ushort), ("Data4", ctypes.c_ubyte * 8)]


def _known_folder(guid: str) -> Path | None:
    u = uuid.UUID(guid)
    g = _GUID(u.fields[0], u.fields[1], u.fields[2],
              (ctypes.c_ubyte * 8).from_buffer_copy(u.bytes[8:]))
    out = ctypes.c_wchar_p()
    hr = ctypes.windll.shell32.SHGetKnownFolderPath(ctypes.byref(g), 0, None, ctypes.byref(out))
    try:
        return Path(out.value) if hr == 0 and out.value else None
    finally:
        ctypes.windll.ole32.CoTaskMemFree(out)


def downloads_dir() -> Path:
    """The user's Downloads folder, wherever Windows keeps it."""
    if sys.platform == "win32":
        try:
            found = _known_folder(_FOLDERID_DOWNLOADS)
            if found and found.is_dir():
                return found
        except (OSError, AttributeError, ValueError):
            pass
    fallback = Path.home() / "Downloads"
    return fallback if fallback.is_dir() else Path.home()


def safe_filename(name: str, fallback: str = "Transcript") -> str:
    """A file name Windows accepts: no reserved characters, no trailing dots or spaces."""
    cleaned = " ".join(_BAD_FILENAME_CHARS.sub(" ", name).split()).strip(" .")
    return cleaned[:120] or fallback


def _escape_block(line: str) -> str:
    """Stop a transcript line from turning into a heading, quote or list in Markdown."""
    line = _ORDERED_MARKER.sub(r"\1\\\2\3", line)
    return _BLOCK_MARKER.sub(r"\\\1", line)


def to_markdown(text: str, title: str | None = None) -> str:
    """The transcript as Markdown: a heading, then one paragraph per entry.

    The timestamp and speaker of each entry are bold. Line breaks inside an entry are
    kept as hard breaks, which is what keeps Zoom's one-caption-per-line files readable
    once rendered.
    """
    out: list[str] = [f"# {title}"] if title else []
    for para in re.split(r"\n[ \t]*\n", text.strip()):
        para = para.strip()
        if not para:
            continue
        head, body = "", para
        m = _STAMP.match(body)
        if m:
            head, body = m.group("stamp"), m.group("rest")
        first, _, more = body.partition("\n")
        name, words = tc.split_speaker(first)
        if name:
            head = f"{head} {name}:".strip()
            body = words + (f"\n{more}" if more else "")
        lines = "  \n".join(_escape_block(ln.strip()) for ln in body.split("\n"))
        out.append(f"**{head}** {lines}" if head else lines)
    return "\n\n".join(out) + "\n"


def write_transcript(path: str | Path, text: str, *, title: str | None = None) -> Path:
    """Write Markdown for a .md name and plain text for anything else."""
    p = Path(path)
    if p.suffix.lower() in MARKDOWN_SUFFIXES:
        content = to_markdown(text, title)
    else:
        content = text.strip() + "\n"
    p.write_text(content, encoding="utf-8")
    return p


def save_transcript(parent, text: str, *, filename: str, title: str | None = None) -> Path | None:
    """Ask where to save, offering Downloads and Markdown first, then write the file.

    Returns the saved path, or None when there is nothing to save or the user cancels.
    """
    if not text.strip():
        return None
    path = filedialog.asksaveasfilename(
        parent=parent,
        title="Download transcript",
        initialdir=str(downloads_dir()),
        initialfile=f"{safe_filename(filename)}.md",
        defaultextension=".md",
        filetypes=FILE_TYPES,
    )
    if not path:
        return None
    return write_transcript(path, text, title=title)
