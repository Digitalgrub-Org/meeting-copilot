"""The recording-import window, driven the way its buttons drive it.

Nothing here touches Teams. It checks the window wires up, renders entries, and hands
text to the app, using synthetic entries.
"""

from __future__ import annotations

import pytest

import live_capture as lc
import teams_recording as trec
from recording_window import RecordingImportWindow

pytestmark = pytest.mark.usefixtures("tk_root")


def _entries():
    a = trec.Entry("Ada Lovelace", 3); a.lines.append("Let me pull up the deck.")
    b = trec.Entry("Grace Hopper", 66); b.lines.append("Okay, a short intro first.")
    return [a, b]


def test_tools_menu_offers_the_import(tk_root):
    app = lc.LiveCapture(tk_root)
    try:
        menubar = tk_root.nametowidget(tk_root["menu"])
        labels = []
        for i in range(menubar.index("end") + 1):
            if menubar.type(i) != "cascade":      # index 0 is the tearoff entry
                continue
            sub = tk_root.nametowidget(menubar.entrycget(i, "menu"))
            for j in range(sub.index("end") + 1):
                if sub.type(j) == "command":
                    labels.append(sub.entrycget(j, "label"))
        assert "Import Teams recording transcript…" in labels
        assert callable(app.import_recording)
    finally:
        app.assist_panel.stop()


def test_done_renders_with_and_without_timestamps(tk_root):
    win = RecordingImportWindow(tk_root)
    win._on_done(_entries(), {"passes": 4})
    text = win.text.get("1.0", "end-1c")
    assert text.startswith("[0:03] Ada Lovelace: Let me pull up the deck.")
    assert "[1:06] Grace Hopper:" in text
    assert "Done — 2 entries" in win.v_status.get()
    win.v_timestamps.set(False)
    win._render()
    assert win.text.get("1.0", "end-1c").startswith("Ada Lovelace: Let me")
    win.destroy()


def test_done_enables_the_actions(tk_root):
    win = RecordingImportWindow(tk_root, on_insert=lambda t: None)
    assert all(str(b["state"]) == "disabled" for b in win.action_buttons)
    win._on_done(_entries(), {})
    assert all(str(b["state"]) == "normal" for b in win.action_buttons)
    win.destroy()


def test_no_entries_explains_rather_than_celebrating(tk_root):
    win = RecordingImportWindow(tk_root)
    win._on_done([], {})
    assert "no transcript entries" in win.v_status.get()
    assert all(str(b["state"]) == "disabled" for b in win.action_buttons)
    win.destroy()


def test_hand_offs_use_the_speaker_text_shape(tk_root):
    got = {}
    win = RecordingImportWindow(tk_root, on_insert=lambda t: got.__setitem__("ins", t),
                                on_summarize=lambda t: got.__setitem__("sum", t))
    win._on_done(_entries(), {})
    win.send_to_transcript()
    win.summarize()
    assert got["ins"].startswith("Ada Lovelace: Let me pull up the deck.")
    assert "[0:03]" not in got["ins"], "Cue's transcript box uses Speaker: text, no stamps"
    assert got["sum"] == got["ins"]
    win.destroy()


def test_progress_is_readable(tk_root):
    win = RecordingImportWindow(tk_root)
    win._on_progress({"pass": 7, "entries": 42, "max_seconds": 754})
    assert win.v_progress.get() == "pass 7 · 42 entries · reached 12:34"
    win.destroy()


def test_error_resets_the_run_controls(tk_root, monkeypatch):
    from tkinter import messagebox
    monkeypatch.setattr(messagebox, "showerror", lambda *a, **k: None)
    win = RecordingImportWindow(tk_root)
    win.start_btn.configure(state="disabled"); win.cancel_btn.configure(state="normal")
    win._on_error("boom")
    assert str(win.start_btn["state"]) == "normal"
    assert str(win.cancel_btn["state"]) == "disabled"
    win.destroy()
