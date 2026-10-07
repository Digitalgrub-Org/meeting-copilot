"""Opening a finished meeting's transcript file (File → Open transcript file…).

The shapes below are the ones meetings leave behind: Zoom's saved captions next to a
local recording, Zoom's cloud transcript, a Teams .vtt or .docx download, and what Cue
writes itself. Names are placeholders; the structure is what matters.
"""

from __future__ import annotations

import pytest

import live_capture as lc
import transcript_cleaner as tc


# --- Zoom ---------------------------------------------------------------------

ZOOM_LOCAL_CAPTIONS = """WEBVTT


00:00:01.490 --> 00:00:03.490
Yes.

00:00:16.584 --> 00:00:20.584
I'm going to stop sharing for a second.

00:00:20.731 --> 00:00:22.731
Uh, something else?
"""


def test_zoom_local_captions_stay_one_line_per_cue():
    """No speakers in this file, so cue boundaries are the only turn-taking left.
    Keep them, but single-spaced, so a meeting is a block and not 400 paragraphs."""
    assert tc.prepare_for_cue(ZOOM_LOCAL_CAPTIONS) == (
        "Yes.\nI'm going to stop sharing for a second.\nUh, something else?"
    )


def test_zoom_cloud_transcript_is_attributed_and_merged():
    raw = (
        "WEBVTT\n\n"
        "1\n00:00:01.000 --> 00:00:02.000\nAda Lovelace: Shall we start?\n\n"
        "2\n00:00:02.500 --> 00:00:04.000\nAda Lovelace: The numbers are in.\n\n"
        "3\n00:00:04.500 --> 00:00:05.000\nGrace Hopper: Go ahead.\n"
    )
    assert tc.prepare_for_cue(raw) == (
        "Ada Lovelace: Shall we start? The numbers are in.\n\n"
        "Grace Hopper: Go ahead."
    )


# --- Teams ------------------------------------------------------------------------

TEAMS_VTT = """WEBVTT

0d4f4d4c-8b6a-4c4f-9d7b-6a2c0b9d3a71/13-0
00:00:03.159 --> 00:00:05.539
<v Ada Lovelace>We should ship on Friday.</v>

0d4f4d4c-8b6a-4c4f-9d7b-6a2c0b9d3a71/13-1
00:00:05.700 --> 00:00:07.100
<v Ada Lovelace>Before the review.</v>

7c1e22a0-1f2b-4d3e-8a9b-0c1d2e3f4a5b/14-0
00:00:08.000 --> 00:00:09.000
<v Grace Hopper>Agreed.</v>
"""


def test_teams_cue_ids_do_not_leak_into_the_text():
    """Teams writes cue ids as "<guid>/<n>-<m>", which the id pattern never matched."""
    out = tc.prepare_for_cue(TEAMS_VTT)
    assert "0d4f4d4c" not in out and "/13-" not in out
    assert out == "Ada Lovelace: We should ship on Friday. Before the review.\n\nGrace Hopper: Agreed."


def test_an_unclosed_voice_tag_still_names_the_speaker():
    raw = "WEBVTT\n\n00:00:01.000 --> 00:00:02.000\n<v Ada Lovelace>Hello there.\n"
    assert tc.prepare_for_cue(raw) == "Ada Lovelace: Hello there."


def test_old_teams_timings_are_dropped():
    raw = "0:0:3.120 --> 0:0:5.430\n<v Ada Lovelace>Morning.</v>\n"
    assert tc.prepare_for_cue(raw) == "Ada Lovelace: Morning."


def test_teams_docx_headers_with_real_world_name_shapes():
    """Particles, "Last, First" and "(Guest)" all occur in real tenants. A missed name
    used to leave its "Name   0:03" header in the transcript as speech."""
    raw = (
        "Ludwig van Beethoven   0:03\nWe start with the regions.\n"
        "Lovelace, Ada   0:09\nAgreed.\n"
        "Alan Turing (Guest)   0:15\nThanks for having me.\n"
    )
    assert tc.prepare_for_cue(raw) == (
        "Ludwig van Beethoven: We start with the regions.\n\n"
        "Lovelace, Ada: Agreed.\n\n"
        "Alan Turing (Guest): Thanks for having me."
    )


def test_a_teams_docx_file_loads(tmp_path):
    docx = pytest.importorskip("docx")
    doc = docx.Document()
    for para in ("KPI review", "Ada Lovelace   0:03", "Revenue is up.",
                 "Grace Hopper   0:07", "By how much?"):
        doc.add_paragraph(para)
    path = tmp_path / "meeting.docx"
    doc.save(str(path))
    assert tc.prepare_for_cue(tc.load_file(path)) == (
        "KPI review\n\nAda Lovelace: Revenue is up.\n\nGrace Hopper: By how much?"
    )


# --- Cue's own files and plain text ----------------------------------------------

def test_cues_recording_export_round_trips():
    raw = "[0:03] Ada Lovelace: One.\n\n[0:06] Ada Lovelace: Two.\n\n[1:02:09] Grace Hopper: Three."
    assert tc.prepare_for_cue(raw) == "Ada Lovelace: One. Two.\n\nGrace Hopper: Three."


def test_single_spaced_lines_from_different_speakers_stay_apart():
    assert tc.prepare_for_cue("Ada Lovelace: One.\nGrace Hopper: Two.") == (
        "Ada Lovelace: One.\n\nGrace Hopper: Two."
    )


def test_a_sentence_with_a_colon_is_not_a_speaker():
    assert tc.split_speaker("the plan for Friday: ship it") == (None, "the plan for Friday: ship it")
    assert tc.split_speaker("One Two Three Four Five Six Seven: words")[0] is None


def test_a_long_turn_is_split_into_paragraphs():
    sentence = "This is one sentence of a long monologue."
    raw = "\n\n".join(f"Ada Lovelace: {sentence}" for _ in range(60))
    blocks = tc.prepare_for_cue(raw).split("\n\n")
    assert len(blocks) > 1
    assert all(b.startswith("Ada Lovelace: ") for b in blocks)
    assert all(len(b) <= tc.MAX_TURN_CHARS + len("Ada Lovelace: ") for b in blocks)


def test_an_empty_file_gives_nothing():
    assert tc.prepare_for_cue("WEBVTT\n\n") == ""


# --- the app ---------------------------------------------------------------------

pytest_tk = pytest.mark.usefixtures("tk_root")


@pytest.fixture
def app(tk_root):
    a = lc.LiveCapture(tk_root)
    yield a
    try:
        a.assist_panel.stop()
    except Exception:
        pass


@pytest.fixture
def vtt(tmp_path):
    path = tmp_path / "zoom.vtt"
    path.write_text(ZOOM_LOCAL_CAPTIONS, encoding="utf-8")
    return path


def _body(app) -> str:
    return app.text.get("1.0", "end-1c").strip()


@pytest_tk
def test_opening_a_file_fills_an_empty_transcript(app, vtt):
    app.open_transcript_file(str(vtt))
    assert _body(app) == tc.prepare_for_cue(ZOOM_LOCAL_CAPTIONS)
    assert app.captured, "the assist panel reads captured, not the text widget"


@pytest_tk
@pytest.mark.parametrize("answer, expect_old, expect_new", [
    (True, False, True),     # Yes: replace
    (False, True, True),     # No: add after
    (None, True, False),     # Cancel: leave alone
])
def test_opening_over_an_existing_transcript_asks_first(app, vtt, monkeypatch,
                                                       answer, expect_old, expect_new):
    app.append_transcript("Earlier meeting text.")
    monkeypatch.setattr(lc.messagebox, "askyesnocancel", lambda *a, **k: answer)
    app.open_transcript_file(str(vtt))
    body = _body(app)
    assert ("Earlier meeting text." in body) is expect_old
    assert ("something else?" in body) is expect_new


@pytest_tk
def test_an_unreadable_file_reports_instead_of_raising(app, tmp_path, monkeypatch):
    shown = []
    monkeypatch.setattr(lc.messagebox, "showerror", lambda title, msg, **k: shown.append(msg))
    app.open_transcript_file(str(tmp_path / "missing.vtt"))
    assert shown and _body(app) == ""
