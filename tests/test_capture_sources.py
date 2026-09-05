"""How each capture source targets a window.

The Teams source was broken from the start: it looked for a top-level window whose
title contains "Captions", and the current Teams client has no such window, because
captions are a pane inside the meeting window. Every poll failed with "No window found
whose title contains 'Captions'". Confirmed against a running Teams client.

It now finds Teams by process instead. The important boundary is that this fallback is
Teams-only: when someone picks a window by name, reading a different one instead would
be worse than reading nothing.
"""

from __future__ import annotations

import pytest

import live_capture as lc

pytestmark = pytest.mark.usefixtures("tk_root")


@pytest.fixture
def app(tk_root):
    a = lc.LiveCapture(tk_root)
    yield a
    try:
        a.assist_panel.stop()
    except Exception:
        pass


def test_teams_source_asks_the_extractor_to_find_teams(app):
    app.source_var.set("Teams desktop")
    app.teams_mode = True
    assert "-TeamsMode" in app._extract_argv()


def test_a_picked_window_never_falls_back(app):
    """If the user named a window, read that window or nothing."""
    app.source_var.set("Pick a window…")
    app.teams_mode = False
    app.capture_window_title = "Zoom Meeting"
    argv = app._extract_argv()
    assert "-TeamsMode" not in argv
    assert argv[argv.index("-WindowTitle") + 1] == "Zoom Meeting"


def test_argv_always_carries_script_and_output_path(app):
    argv = app._extract_argv()
    assert argv[0] == "powershell"
    assert "-ExecutionPolicy" in argv and "Bypass" in argv
    assert str(lc.EXTRACT_PS1) in argv
    assert str(lc.RAW_TXT) in argv[argv.index("-OutPath") + 1]


def test_teams_mode_defaults_on(app):
    """Teams desktop is the first entry in the dropdown, so the default matters."""
    assert app.teams_mode is True


def test_the_extractor_script_is_present():
    assert lc.EXTRACT_PS1.exists(), f"missing {lc.EXTRACT_PS1}"


def test_the_script_supports_the_teams_mode_switch():
    """Guards against the Python side passing a flag the script would reject."""
    src = lc.EXTRACT_PS1.read_text(encoding="utf-8", errors="replace")
    assert "[switch]$TeamsMode" in src
    assert "$TeamsProcess" in src
    assert "ms-teams" in src, "the current Teams client's process name must be recognised"


def test_the_script_still_prefers_a_real_captions_window():
    """Classic Teams and third-party captioners do expose one; title match stays first."""
    src = lc.EXTRACT_PS1.read_text(encoding="utf-8", errors="replace")
    title_match = src.index("title:captions")
    process_match = src.index('$strategy = "process"')
    assert title_match < process_match, "title lookup must be attempted before the fallback"
