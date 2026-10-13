"""Every window's bottom row must be on screen at the window's own size.

Tk's pack geometry manager hands out space in packing order. The windows packed their
transcript box, which asks for 24 lines, before the row of buttons under it, so at the
default size there was nothing left: the row got no height and Tk unmapped it. Someone
importing a Teams transcript found no Save button anywhere. These tests map each window
off-screen at its default size and check that the bottom widgets are really on it.
"""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk

import pytest

import live_capture as lc
import summarize
from recording_window import RecordingImportWindow
from settings_window import SettingsWindow
from transcribe_window import TranscribeWindow

pytestmark = pytest.mark.usefixtures("tk_root")


def _show_offscreen(win: tk.Misc) -> None:
    """Map a window where nobody sees it, keeping its default size."""
    try:
        win.wm_transient("")  # a transient of the withdrawn test root would stay unmapped
    except tk.TclError:
        pass
    win.geometry("+-20000+-20000")
    win.deiconify()
    win.update()


def _buttons(root: tk.Misc, *labels: str) -> list[ttk.Button]:
    found = []
    stack = [root]
    while stack:
        w = stack.pop()
        if isinstance(w, ttk.Button) and str(w.cget("text")) in labels:
            found.append(w)
        stack.extend(w.winfo_children())
    assert len(found) == len(labels), f"expected {labels}, found {[b.cget('text') for b in found]}"
    return found


def _assert_visible(win: tk.Misc, widget: tk.Misc) -> None:
    name = widget.cget("text") if "text" in widget.keys() else str(widget)
    assert widget.winfo_ismapped(), f"{name!r} was squeezed out of the window"
    assert widget.winfo_height() >= widget.winfo_reqheight() - 2, f"{name!r} is cut short"
    bottom = widget.winfo_rooty() + widget.winfo_height()
    assert bottom <= win.winfo_rooty() + win.winfo_height(), f"{name!r} hangs off the bottom"


def test_recording_import_buttons_are_visible(tk_root):
    win = RecordingImportWindow(tk_root, on_insert=lambda t: None, on_summarize=lambda t: None)
    try:
        _show_offscreen(win)
        assert win.action_buttons
        for b in win.action_buttons:
            _assert_visible(win, b)
        _assert_visible(win, win.download_btn)
    finally:
        win.destroy()


def test_transcribe_window_buttons_are_visible(tk_root):
    win = TranscribeWindow(tk_root, on_insert=lambda t: None, on_summarize=lambda t: None)
    try:
        _show_offscreen(win)
        assert win.action_buttons
        for b in win.action_buttons:
            _assert_visible(win, b)
    finally:
        win.destroy()


def test_summary_window_buttons_are_visible(tk_root, monkeypatch):
    monkeypatch.setattr(summarize.SummaryWindow, "_stream", lambda self: None)
    win = summarize.SummaryWindow(tk_root, "Ada Lovelace: hello.", backend="ollama",
                                  ollama_model="none")
    try:
        _show_offscreen(win)
        for b in _buttons(win, "Copy", "Stop", "Close"):
            _assert_visible(win, b)
    finally:
        win.destroy()


def test_settings_window_buttons_are_visible(tk_root):
    win = SettingsWindow(tk_root)
    try:
        _show_offscreen(win)
        for b in _buttons(win, "OK", "Cancel", "Apply"):
            _assert_visible(win, b)
    finally:
        win.destroy()


def test_main_window_status_bar_and_download_are_visible(tk_root):
    top = tk.Toplevel(tk_root)
    app = lc.LiveCapture(top)
    try:
        _show_offscreen(top)
        status_labels = []
        stack = [top]
        while stack:
            w = stack.pop()
            if isinstance(w, ttk.Label) and str(w.cget("textvariable")) == str(app.status):
                status_labels.append(w)
            stack.extend(w.winfo_children())
        assert status_labels, "status bar label not found"
        _assert_visible(top, status_labels[0])
        _assert_visible(top, app.download_btn)
        _assert_visible(top, app.transcript_only_check)
    finally:
        try:
            app.assist_panel.stop()
        except Exception:
            pass
        top.destroy()
