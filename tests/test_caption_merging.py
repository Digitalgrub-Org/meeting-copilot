"""How live captions become transcript entries when the app keeps rewriting them.

Zoom shows its last two or three captions and keeps rewriting them: a caption grows
while its speaker talks, its words get corrected ("10 see it" becomes "10 seats"), and
the rewrite can land after someone else's reply. Cue compared each caption with the last
entry only, so corrections and late rewrites were kept twice, wrong version included.
Every replay below is a sequence of polls of the kind Zoom's overlay produced.
"""

from __future__ import annotations

import subprocess
import tkinter as tk

import pytest

import live_capture as lc

pytestmark = pytest.mark.usefixtures("tk_root")


def poll(*items: tuple[str, str]) -> str:
    return "\n".join(f"[speaker] {who}\n[text] {words}" for who, words in items)


@pytest.fixture
def app(tk_root, monkeypatch):
    monkeypatch.setattr(lc.cfg_mod, "save_config", lambda cfg: None)
    top = tk.Toplevel(tk_root)
    a = lc.LiveCapture(top)
    yield a
    a.running = False
    try:
        a.assist_panel.stop()
    except Exception:
        pass
    top.destroy()


def lines(app) -> list[str]:
    return [b for b in app.text.get("1.0", "end-1c").strip().split("\n\n") if b]


def replay(app, *polls: str) -> list[str]:
    for raw in polls:
        app._handle_extract(raw)
    return lines(app)


# --- merge_caption ------------------------------------------------------------------

def test_a_growing_caption_replaces_its_earlier_version():
    assert lc.merge_caption("We said Friday", "We said Friday, so I want") == "We said Friday, so I want"


def test_a_corrected_caption_replaces_the_wrong_words():
    assert lc.merge_caption("If they purchase like 10 see it",
                            "If they purchase, like, 10 seats, the price tier changes.") == (
        "If they purchase, like, 10 seats, the price tier changes.")


def test_a_stale_shorter_copy_keeps_the_full_caption():
    assert lc.merge_caption("We said Friday, so I want to know", "We said Friday") == (
        "We said Friday, so I want to know")


def test_a_rolling_caption_is_joined_not_replaced():
    """is_revision_of replaced the earlier line here and lost its beginning."""
    prev = "OK so what we are trying to do is build a tool but the challenge we face is"
    new = "but the challenge we face is that right now we copy everything by hand."
    assert lc.merge_caption(prev, new) == (
        "OK so what we are trying to do is build a tool but the challenge we face is "
        "that right now we copy everything by hand.")


@pytest.mark.parametrize("prev, new", [
    ("I think so.", "I think we should ship on Friday anyway."),          # short: exact rules only
    ("We need the gateway logs to know.", "We need to test the payment flow properly."),
    ("Thanks everyone for joining today.", "Okay, thanks everyone for joining today."),  # first word differs
    ("so what we are trying to", "are trying to finish it today"),       # overlap under five words
])
def test_different_things_said_are_not_merged(prev, new):
    assert lc.merge_caption(prev, new) is None


def test_empty_captions_are_never_merged():
    assert lc.merge_caption("", "anything") is None
    assert lc.merge_caption("anything", "") is None


# --- the capture loop -----------------------------------------------------------------

def test_a_correction_leaves_one_entry_with_the_right_words(app):
    assert replay(app,
                  poll(("Meera", "If they purchase like 10 see it")),
                  poll(("Meera", "If they purchase, like, 10 seats, the price tier changes."))) == [
        "Meera: If they purchase, like, 10 seats, the price tier changes."]


def test_a_rewrite_after_someone_elses_reply_lands_in_place(app):
    assert replay(app,
                  poll(("Priya", "We said Friday")),
                  poll(("Priya", "We said Friday, so I want"), ("Daniel", "Yes.")),
                  poll(("Priya", "We said Friday, so I want to know if Friday is still real."),
                       ("Daniel", "Yes."))) == [
        "Priya: We said Friday, so I want to know if Friday is still real.",
        "Daniel: Yes.",
    ]


def test_a_stale_copy_does_not_cut_an_entry_back(app):
    assert replay(app,
                  poll(("Priya", "We said Friday, so I want to know if Friday is still real.")),
                  poll(("Priya", "We said Friday, so I want"))) == [
        "Priya: We said Friday, so I want to know if Friday is still real."]


def test_a_stale_copy_does_not_return_once_out_of_reach(app):
    polls = [poll(("Priya", "We said Friday")), poll(("Priya", "We said Friday, so I want to know."))]
    polls += [poll((f"P{i}", f"Unrelated remark number {i} about something else.")) for i in range(8)]
    polls.append(poll(("Priya", "We said Friday")))
    assert sum(1 for line in replay(app, *polls) if line.startswith("Priya:")) == 1


def test_different_sentences_from_one_speaker_all_stay(app):
    assert len(replay(app,
                      poll(("Priya", "I think so.")),
                      poll(("Priya", "I think we should ship on Friday anyway.")),
                      poll(("Priya", "We need the gateway logs to know.")),
                      poll(("Priya", "We need to test the payment flow properly.")))) == 4


def test_an_edit_to_an_earlier_entry_survives_a_later_revision(app):
    """The old code rebuilt the whole widget to change the last entry."""
    replay(app, poll(("Priya", "Okay let's start.")), poll(("Daniel", "The payment retry")))
    start = app.text.search("Okay", "1.0")
    app.text.insert(start, "[edited] ")
    replay(app, poll(("Daniel", "The payment retry fix is merged.")))
    assert lines(app) == ["Priya: [edited] Okay let's start.", "Daniel: The payment retry fix is merged."]


def test_an_imported_transcript_is_never_revised_by_a_caption(app):
    app.append_transcript("Ada Lovelace: We should ship on Friday because the review is done.")
    replay(app, poll(("Ada Lovelace", "We should ship on Friday")))
    assert len(app.captured) == 2


def test_a_revision_of_text_the_user_deleted_goes_to_the_end(app):
    replay(app, poll(("Priya", "We said Friday")), poll(("Daniel", "Yes.")))
    app.text.delete("1.0", "3.0")  # the user removes Priya's line
    replay(app, poll(("Priya", "We said Friday, so I want to know.")))
    assert lines(app)[-1] == "Priya: We said Friday, so I want to know."


def test_clearing_resets_the_entry_tags(app):
    replay(app, poll(("Priya", "We said Friday")))
    app._reset_transcript()
    assert app.captured == [] and app._entry_tags == []
    assert replay(app, poll(("Priya", "We said Friday"))) == ["Priya: We said Friday"]


# --- polling ------------------------------------------------------------------------

class _NoThread:
    started: list = []

    def __init__(self, target=None, daemon=None):
        self.target = target

    def start(self):
        _NoThread.started.append(self.target)


def _poll_once(app, monkeypatch) -> int:
    """Run one poll and return the interval it scheduled the next one for."""
    scheduled = []
    monkeypatch.setattr(app.root, "after", lambda ms, fn: scheduled.append(ms) or "id")
    app._poll()
    return scheduled[-1]


def test_zoom_is_checked_every_second(app, monkeypatch):
    monkeypatch.setattr(lc.threading, "Thread", _NoThread)
    app.running, app.zoom_mode, app.teams_mode = True, True, False
    assert _poll_once(app, monkeypatch) == lc.ZOOM_POLL_MS == 1000


def test_teams_keeps_the_configured_interval(app, monkeypatch):
    monkeypatch.setattr(lc.threading, "Thread", _NoThread)
    app.running, app.zoom_mode = True, False
    app.poll_seconds.set(4)
    assert _poll_once(app, monkeypatch) == 4000


def test_a_poll_never_starts_a_second_extractor_run(app, monkeypatch):
    _NoThread.started = []
    monkeypatch.setattr(lc.threading, "Thread", _NoThread)
    app.running = True
    _poll_once(app, monkeypatch)
    _poll_once(app, monkeypatch)  # the first run has not finished
    assert len(_NoThread.started) == 1


def test_the_busy_flag_clears_even_when_the_run_fails(app, monkeypatch):
    def boom(*a, **k):
        raise OSError("powershell missing")
    monkeypatch.setattr(lc.subprocess, "run", boom)
    app._extract_busy = True
    app._do_extract()
    assert app._extract_busy is False
    assert app.queue.get_nowait()[0] == "error"


def test_exit_code_2_is_a_status_message_not_an_error(app, monkeypatch):
    advice = "Teams is open, but no live captions are showing."
    def nothing_to_read(*a, **k):
        raise subprocess.CalledProcessError(2, "powershell", stderr=advice.encode())
    monkeypatch.setattr(lc.subprocess, "run", nothing_to_read)
    app._do_extract()
    kind, payload = app.queue.get_nowait()
    assert (kind, payload) == ("waiting", advice)
    app.queue.put((kind, payload))
    errors_before = app.error_count
    app._drain_queue()
    assert app.status.get() == advice
    assert app.error_count == errors_before


# --- the extractor script -----------------------------------------------------------

SRC = lc.EXTRACT_PS1.read_text(encoding="utf-8", errors="replace")


def test_teams_live_capture_reads_captions_or_nothing():
    """It used to read the whole main window, chat list and all, when captions were off."""
    assert "no live captions are showing" in SRC
    i = SRC.index("no live captions are showing")
    guard = SRC[SRC.rindex("if (", 0, i):i]
    assert "$TeamsMode" in guard and "-not $Collect" in guard and "-not $region" in guard
    assert '"title:captions"' in guard, "a real Captions window found by title is still read"


def test_nothing_to_read_exits_2_with_a_plain_line():
    body = SRC[SRC.index("function Stop-NothingToRead"):]
    body = body[:body.index("\n}\n")]
    assert "[Console]::Error.WriteLine($Message)" in body and "exit 2" in body
    assert not any(line.strip().startswith("Write-Error") for line in SRC.splitlines()), (
        "Write-Error prefixes the script path and wraps, which cut the advice off")


def test_mouse_and_forms_load_only_for_the_recording_import():
    collect = SRC.index("if ($Collect) {\n    if (-not $region)")
    assert SRC.index("Add-Type -AssemblyName System.Windows.Forms") > collect
    assert SRC.index("$Win32 = Add-Type -MemberDefinition") > collect
