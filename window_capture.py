"""List visible top-level windows on Windows (for the 'Pick a window' source).

Uses user32 via ctypes — no extra dependencies. Returns window titles the user
can choose from; the chosen title is passed to the UI Automation extractor,
which reads accessible text from that window.
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes

_user32 = ctypes.windll.user32

_EnumWindowsProc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

# Titles we never want to offer as a capture source (our own app, shell chrome).
_EXCLUDE_SUBSTRINGS = (
    "Cue  ·  by Digitalgrub",
    "Default IME",
    "MSCTFIME UI",
    "Program Manager",
    "Windows Input Experience",
    "Settings",
)


def list_windows() -> list[str]:
    """Return de-duplicated titles of visible, titled top-level windows."""
    titles: list[str] = []
    seen: set[str] = set()

    def _cb(hwnd, _lparam):
        if not _user32.IsWindowVisible(hwnd):
            return True
        length = _user32.GetWindowTextLengthW(hwnd)
        if length <= 0:
            return True
        buf = ctypes.create_unicode_buffer(length + 1)
        _user32.GetWindowTextW(hwnd, buf, length + 1)
        title = buf.value.strip()
        if not title:
            return True
        if any(ex.lower() in title.lower() for ex in _EXCLUDE_SUBSTRINGS):
            return True
        if title in seen:
            return True
        seen.add(title)
        titles.append(title)
        return True

    _user32.EnumWindows(_EnumWindowsProc(_cb), 0)
    titles.sort(key=str.lower)
    return titles


if __name__ == "__main__":
    ws = list_windows()
    print(f"{len(ws)} windows")
    for t in ws:
        print(t.encode("ascii", "replace").decode())
