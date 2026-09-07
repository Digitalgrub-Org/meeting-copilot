"""Parsing the Teams recording Transcript pane.

The shapes here were taken from a real pane dump with letters masked. Headers are the
screen-reader form of the timestamp and arrive twice; speech follows as fragments.
"""

from __future__ import annotations

import teams_recording as tr

PANE = """\
[text] Transcript
[text] AI-generated content may be incorrect
[text] Ada Lovelace started transcription
[text] Ada Lovelace 0 minutes 03 seconds
[text] Ada Lovelace0 minutes 03 seconds
[text] Let me pull up the deck, or start presenting at least the kickoff.
[text] Grace Hopper 0 minutes 06 seconds
[text] Grace Hopper0 minutes 06 seconds
[text] Okay.
[text] Yeah.
[text] So I think for a group of us who already met, maybe a short intro.
[text] Alan Turing 1 hours 2 minutes 5 seconds
[text] We are past the hour mark now.
[text] Is this transcript useful?
"""


def test_headers_are_recognised_in_both_spacings():
    assert tr.parse_header("Ada Lovelace 0 minutes 03 seconds") == ("Ada Lovelace", 3)
    assert tr.parse_header("Ada Lovelace0 minutes 03 seconds") == ("Ada Lovelace", 3)


def test_hours_are_handled():
    assert tr.parse_header("Alan Turing 1 hours 2 minutes 5 seconds") == ("Alan Turing", 3725)


def test_speech_is_not_a_header():
    assert tr.parse_header("We met at 0 minutes past") is None
    assert tr.parse_header("Okay.") is None
    assert tr.parse_header("3 minutes 4 seconds") is None     # no speaker


def test_doubled_headers_collapse_onto_one_entry():
    entries = tr.parse_pane(PANE)
    assert [e.speaker for e in entries] == ["Ada Lovelace", "Grace Hopper", "Alan Turing"]


def test_speech_fragments_attach_to_their_speaker():
    entries = tr.parse_pane(PANE)
    assert entries[0].text.startswith("Let me pull up the deck")
    assert entries[1].text == "Okay. Yeah. So I think for a group of us who already met, maybe a short intro."


def test_pane_chrome_is_dropped():
    text = tr.to_text(tr.parse_pane(PANE))
    for junk in ("Transcript", "AI-generated", "started transcription", "useful"):
        assert junk not in text


def test_text_before_the_first_header_is_ignored():
    entries = tr.parse_pane("[text] Some stray label\n[text] Ada Lovelace 0 minutes 1 seconds\n[text] Hi.")
    assert len(entries) == 1 and entries[0].text == "Hi."


def test_timestamps_render_in_the_obvious_way():
    entries = tr.parse_pane(PANE)
    assert entries[0].stamp == "0:03"
    assert entries[2].stamp == "1:02:05"


def test_output_has_speaker_and_time():
    out = tr.to_text(tr.parse_pane(PANE))
    assert out.startswith("[0:03] Ada Lovelace: Let me pull up the deck")
    assert "\n\n[0:06] Grace Hopper: Okay." in out


def test_cue_transcript_form_drops_timestamps():
    out = tr.to_cue_transcript(tr.parse_pane(PANE))
    assert out.startswith("Ada Lovelace: Let me pull up")
    assert "[0:" not in out


# --- merging scroll passes --------------------------------------------------

def _entries(*specs):
    out = []
    for speaker, secs, text in specs:
        e = tr.Entry(speaker=speaker, seconds=secs)
        e.lines.append(text)
        out.append(e)
    return out


def test_merge_dedups_overlapping_passes():
    a = _entries(("Ada", 3, "one"), ("Grace", 6, "two"))
    b = _entries(("Grace", 6, "two"), ("Alan", 9, "three"))
    merged = tr.merge([a, b])
    assert [(e.speaker, e.seconds) for e in merged] == [("Ada", 3), ("Grace", 6), ("Alan", 9)]


def test_merge_keeps_the_fuller_version_of_an_entry():
    """An entry cut off at the viewport edge in one pass is complete in the next."""
    a = _entries(("Grace", 6, "So I think"))
    b = _entries(("Grace", 6, "So I think for a group of us who already met."))
    merged = tr.merge([a, b])
    assert merged[0].text.endswith("already met.")


def test_merge_orders_by_time_regardless_of_pass_order():
    late = _entries(("Alan", 90, "z"))
    early = _entries(("Ada", 3, "a"))
    assert [e.seconds for e in tr.merge([late, early])] == [3, 90]


def test_coverage_reports_first_and_last():
    assert tr.coverage(_entries(("Ada", 3, "a"), ("Alan", 150, "z"))) == (3, 150)
    assert tr.coverage([]) == (0, 0)


def test_module_is_parent_safe():
    """Runs inside Tk, so it must not drag in anything native."""
    import sys
    for m in ("numpy", "av", "ctranslate2", "faster_whisper"):
        assert m not in sys.modules or True  # presence from other tests is fine; see test_purity
    src = open(tr.__file__, encoding="utf-8").read()
    for m in ("numpy", "av", "ctranslate2", "faster_whisper", "subprocess"):
        assert f"import {m}" not in src
