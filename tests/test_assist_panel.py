"""The assist panel's cold start.

Tested in a real Google Meet call: captions flowed into the transcript, and the
right-hand panel stayed completely blank. The configured cadences are 120s and 180s,
so the first attempt was still minutes away, and the status line said "Waiting for
first capture…" the whole time even though capture was clearly working. An empty panel
with a stale message is indistinguishable from a broken app.

These tests pin the cold-start behaviour: retry quickly until there is output, wait for
enough speech to be worth summarising, and always say what is being waited for.
"""

from __future__ import annotations

import pytest

import auto_assist
from auto_assist import LiveAssistPanel


@pytest.fixture
def panel(tk_root):
    """A real panel whose transcript and backend we control."""
    state = {"transcript": "", "backend": ("ollama", "test-model")}
    p = LiveAssistPanel(
        tk_root,
        get_transcript=lambda: state["transcript"],
        resolve_backend=lambda: state["backend"],
    )
    p.state = state
    yield p
    p.stop()


def test_first_attempt_is_soon_not_a_full_cadence(panel):
    """The whole bug: a cold panel must not wait two minutes before trying."""
    assert auto_assist.FIRST_RUN_DELAY_SEC < 60
    assert auto_assist.FIRST_RUN_DELAY_SEC < panel.brief_interval.get()


def test_warming_loops_retry_at_the_short_delay(panel):
    """Until a panel has output, every reschedule uses the short delay, so launching
    the app before joining the call cannot strand it on a long timer."""
    import time
    panel.prev_brief = ""
    panel._schedule_brief()
    assert panel.next_brief_at is not None
    assert panel.next_brief_at - time.monotonic() <= auto_assist.FIRST_RUN_DELAY_SEC + 1


def test_settled_loops_use_the_configured_cadence(panel):
    import time
    panel.prev_brief = "## Topics\n* something"
    panel.brief_interval.set(120)
    panel._schedule_brief()
    assert panel.next_brief_at - time.monotonic() > 60


def test_it_waits_for_enough_speech_before_the_first_call(panel):
    """A brief built from four words is worse than no brief."""
    calls = []
    panel.resolve_backend = lambda: (calls.append("resolved"), ("ollama", "m"))[1]
    panel.state["transcript"] = "Hello."
    panel._kick_brief()
    assert calls == [], "should not have called the backend on a near-empty transcript"


def test_force_overrides_the_minimum(panel):
    """The Refresh button must work even with barely any transcript."""
    resolved = []
    panel.resolve_backend = lambda: (resolved.append(1), None)[1]
    panel.state["transcript"] = "Short."
    panel._kick_brief(force=True)
    assert resolved, "force should reach backend resolution"


def test_status_reports_progress_toward_the_first_update(panel):
    panel.state["transcript"] = "word " * 10          # ~50 chars, under the minimum
    panel._schedule_brief()
    panel._tick_status()
    status = panel.brief_status.get()
    assert "Listening" in status
    assert str(auto_assist.MIN_FIRST_CHARS) in status


def test_status_counts_down_once_there_is_enough_speech(panel):
    panel.state["transcript"] = "x" * (auto_assist.MIN_FIRST_CHARS + 50)
    panel._schedule_brief()
    panel._tick_status()
    status = panel.brief_status.get()
    assert "characters captured" in status
    assert "first update in" in status


def test_status_never_says_waiting_for_first_capture_once_captions_arrive(panel):
    """The exact misleading message from the Meet test."""
    panel.state["transcript"] = "Someone is definitely talking now. " * 3
    panel._schedule_brief()
    panel._schedule_questions()
    panel._schedule_points()
    panel._tick_status()
    for var in (panel.brief_status, panel.questions_status, panel.points_status):
        assert "Waiting for first capture" not in var.get()


def test_real_statuses_are_not_clobbered_by_the_ticker(panel):
    """Once a panel has output, the ticker must leave its status alone."""
    panel.prev_brief = "## Topics\n* shipped"
    panel.brief_status.set("Updated at 14:32:01.")
    panel.state["transcript"] = "more speech here"
    panel._tick_status()
    assert panel.brief_status.get() == "Updated at 14:32:01."


def test_errors_are_not_clobbered_by_the_ticker(panel):
    panel.prev_questions = "1. something"
    panel.questions_status.set("Error: ConnectionError: refused")
    panel._tick_status()
    assert panel.questions_status.get().startswith("Error:")


def test_a_failure_stays_visible_while_warming(panel):
    """The countdown must not bury the reason nothing is appearing. Before this, an
    Ollama connection error showed for two seconds and was then overwritten."""
    panel.state["transcript"] = "x" * 500
    panel._schedule_brief()
    panel._on_brief_error("ConnectionError: Failed to connect to Ollama at 11434")
    assert "Ollama isn't running" in panel.brief_status.get()
    panel._tick_status()
    status = panel.brief_status.get()
    assert "Ollama isn't running" in status, f"error was buried: {status!r}"
    assert "retrying in" in status


def test_a_success_clears_the_remembered_failure(panel):
    panel._on_brief_error("ConnectionError: refused")
    assert panel.brief_error is not None
    panel._on_brief_done("## Topics\n* something", 100)
    assert panel.brief_error is None


@pytest.mark.parametrize("raw,expected", [
    ("ConnectionError: Failed to connect to Ollama", "Ollama isn't running"),
    ("ResponseError: model 'phi' not found, try pulling it", "isn't installed"),
    ("AuthenticationError: invalid_api_key provided", "API key rejected"),
    ("RateLimitError: rate limit exceeded", "Rate limited"),
    ("ReadTimeout: request timed out", "Can't reach the AI backend"),
])
def test_known_failures_become_actionable_text(raw, expected):
    assert expected in auto_assist._short_error(raw)


def test_an_unknown_failure_still_says_something():
    out = auto_assist._short_error("WeirdError: something nobody predicted\nline two")
    assert out.startswith("WeirdError")
    assert "\n" not in out


def test_a_missing_backend_is_reported_not_silent(panel):
    """If Ollama isn't running, say so rather than sitting blank."""
    panel.resolve_backend = lambda: None
    panel.state["transcript"] = "x" * (auto_assist.MIN_FIRST_CHARS + 10)
    panel._kick_brief()
    assert "backend" in panel.brief_status.get().lower()


def test_stop_cancels_the_status_ticker(panel):
    panel.start()
    assert panel.status_after_id is not None
    panel.stop()
    assert panel.status_after_id is None


def test_paused_panel_does_not_rewrite_statuses(panel):
    panel.paused.set(True)
    panel.brief_status.set("untouched")
    panel.state["transcript"] = "lots of speech " * 20
    panel._tick_status()
    assert panel.brief_status.get() == "untouched"
