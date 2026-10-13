"""Transcript only (AI off): just the transcript, nothing that talks to a model.

Off has to mean off, not hidden: the assist panel stops polling, the AI controls go,
Summarize refuses, background indexing pauses, and a launch in this mode never asks
Ollama for its models. The setting is remembered. Config writes are captured here so
the tests never touch the real settings file.
"""

from __future__ import annotations

import copy
import tkinter as tk

import pytest

import live_capture as lc

pytestmark = pytest.mark.usefixtures("tk_root")


@pytest.fixture
def saved(monkeypatch):
    """Capture config saves instead of writing the user's config.json."""
    out: list[dict] = []
    monkeypatch.setattr(lc.cfg_mod, "save_config", lambda cfg: out.append(copy.deepcopy(cfg)))
    return out


def _make(tk_root):
    top = tk.Toplevel(tk_root)
    app = lc.LiveCapture(top)
    return top, app


def _close(top, app):
    try:
        app.assist_panel.stop()
    except Exception:
        pass
    top.destroy()


def _ai_hidden(app) -> bool:
    return all(w.winfo_manager() == "" for w, _ in app._ai_controls)


def _ai_shown(app) -> bool:
    return all(w.winfo_manager() == "pack" for w, _ in app._ai_controls)


def _panel_shown(app) -> bool:
    return str(app.assist_panel) in [str(p) for p in app.main_paned.panes()]


def test_turning_it_on_removes_the_panel_and_the_ai_controls(tk_root, saved):
    top, app = _make(tk_root)
    try:
        assert _panel_shown(app) and _ai_shown(app)
        app.transcript_only.set(True)
        app._apply_transcript_only()
        assert not _panel_shown(app)
        assert _ai_hidden(app)
        assert app.download_btn.winfo_manager() == "pack", "the transcript keeps its Download"
        assert app.transcript_only_check.winfo_manager() == "pack", "the switch must stay reachable"
        assert "AI: off" in app.stats.get()
        assert saved and saved[-1]["ui"]["transcript_only"] is True
    finally:
        _close(top, app)


def test_turning_it_off_brings_everything_back(tk_root, saved, monkeypatch):
    top, app = _make(tk_root)
    try:
        app.transcript_only.set(True)
        app._apply_transcript_only()
        started = []
        monkeypatch.setattr(app.assist_panel, "start", lambda: started.append(True))
        app._kb_ready = True
        app.transcript_only.set(False)
        app._apply_transcript_only()
        assert _panel_shown(app) and _ai_shown(app)
        assert started, "the panel must resume polling"
        assert saved[-1]["ui"]["transcript_only"] is False
    finally:
        _close(top, app)


def test_a_launch_in_transcript_only_never_contacts_ollama(tk_root, saved, monkeypatch):
    cfg = copy.deepcopy(lc.cfg_mod.get_config())
    cfg["ui"]["transcript_only"] = True
    monkeypatch.setattr(lc.cfg_mod, "get_config", lambda: copy.deepcopy(cfg))
    asked = []
    monkeypatch.setattr(lc, "list_ollama_models", lambda: asked.append(True) or [])
    monkeypatch.setattr(lc.kb, "warm_up", lambda: None)
    top, app = _make(tk_root)
    try:
        started = []
        monkeypatch.setattr(app.assist_panel, "start", lambda: started.append(True))
        app._warm_kb()
        assert not asked, "Ollama was queried with AI off"
        assert not started, "the assist panel started with AI off"
        assert not _panel_shown(app) and _ai_hidden(app)
        assert not saved, "applying the remembered setting should not rewrite it"
    finally:
        _close(top, app)


def test_summarize_refuses_with_ai_off(tk_root, saved, monkeypatch):
    top, app = _make(tk_root)
    try:
        app.transcript_only.set(True)
        app._apply_transcript_only()
        shown = []
        monkeypatch.setattr(lc.messagebox, "showinfo", lambda *a, **k: shown.append(a))
        monkeypatch.setattr(lc, "SummaryWindow", lambda *a, **k: pytest.fail("summary opened"))
        monkeypatch.setattr(app, "_resolve_backend", lambda: pytest.fail("backend resolved"))
        app.summarize_text("Ada Lovelace: we should ship on Friday.")
        assert shown, "the user should be told why nothing happened"
    finally:
        _close(top, app)


def test_background_indexing_pauses_with_ai_off(tk_root, saved, monkeypatch):
    top, app = _make(tk_root)
    try:
        app.transcript_only.set(True)
        app._apply_transcript_only()
        app.text.insert("end", "Ada Lovelace: " + "we should ship on Friday. " * 100)
        monkeypatch.setattr(app, "_schedule_auto_index", lambda: None)
        threads = []
        monkeypatch.setattr(lc.threading, "Thread", lambda *a, **k: threads.append(k) or pytest.fail("indexed"))
        app._auto_index_tick()
        assert not threads
    finally:
        _close(top, app)


def test_the_view_menu_offers_the_switch(tk_root, saved):
    top, app = _make(tk_root)
    try:
        menubar = top.nametowidget(top["menu"])
        labels = []
        for i in range(menubar.index("end") + 1):
            if menubar.type(i) != "cascade":
                continue
            sub = top.nametowidget(menubar.entrycget(i, "menu"))
            for j in range(sub.index("end") + 1):
                if sub.type(j) in ("command", "checkbutton"):
                    labels.append(sub.entrycget(j, "label"))
        assert "Transcript only (AI off)" in labels
    finally:
        _close(top, app)
