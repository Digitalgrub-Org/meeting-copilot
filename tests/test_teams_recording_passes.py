"""Merging the multi-pass dump the -Collect mode writes."""

from __future__ import annotations

import teams_recording as tr

DUMP = """\
--- pass 1 ---
[text] Ada Lovelace 0 minutes 03 seconds
[text] Let me pull up the deck.
[text] Grace Hopper 0 minutes 06 seconds
[text] Okay.
--- pass 2 ---
[text] Grace Hopper 0 minutes 06 seconds
[text] Okay.
[text] Yeah, so a short intro first.
[text] Alan Turing 0 minutes 40 seconds
[text] We are past the intro now.
--- pass 3 ---
[text] Alan Turing 0 minutes 40 seconds
[text] We are past the intro now.
"""


def test_passes_are_split_on_the_marker():
    passes = tr.parse_passes(DUMP)
    assert len(passes) == 3
    assert [len(p) for p in passes] == [2, 2, 1]


def test_merging_passes_yields_one_ordered_transcript():
    entries = tr.merge(tr.parse_passes(DUMP))
    assert [(e.speaker, e.seconds) for e in entries] == [
        ("Ada Lovelace", 3), ("Grace Hopper", 6), ("Alan Turing", 40),
    ]


def test_the_fuller_rendering_of_an_edge_entry_wins():
    """Grace's entry was cut off at the bottom of pass 1 and complete in pass 2."""
    entries = tr.merge(tr.parse_passes(DUMP))
    grace = next(e for e in entries if e.speaker == "Grace Hopper")
    assert grace.text == "Okay. Yeah, so a short intro first."


def test_a_dump_without_markers_is_one_pass():
    passes = tr.parse_passes("[text] Ada Lovelace 0 minutes 1 seconds\n[text] Hi.")
    assert len(passes) == 1 and passes[0][0].text == "Hi."


def test_import_argv_targets_the_extractor_in_collect_mode():
    argv = tr.import_argv(r"C:\tmp\out.txt")
    assert argv[0] == "powershell"
    assert "-Collect" in argv and "-TeamsMode" in argv
    assert argv[argv.index("-OutPath") + 1] == r"C:\tmp\out.txt"
    assert argv[argv.index("-File") + 1].endswith("extract_teams_transcript.ps1")


# --- how -Collect scrolls (read from the script) -------------------------------------

def _collect_block() -> str:
    src = tr.extractor_script().read_text(encoding="utf-8", errors="replace")
    return src[src.index("if ($Collect) {\n    if (-not $region)"):]


def test_collect_moves_by_bringing_the_last_transcript_line_into_view():
    """In the current Teams the lines are list items and the only text elements are the
    pane's header notes. Bringing the last *text* element into view jumped back to the
    top, and a 1 h 45 min import ended at 34:06 reporting itself done."""
    block = _collect_block()
    assert '$method = "lastline"' in block
    lastline = block[block.index('"lastline" {'):block.index('"wheel" {')]
    assert lastline.index("$items") < lastline.index("$textCond"), (
        "list items first; a text element only for older Teams with no list items")


def test_collect_starts_at_the_top_without_the_mouse():
    block = _collect_block()
    assert "Bring-IntoView $firstItems[0]" in block


def test_collect_checks_the_numbered_lines_for_gaps():
    block = _collect_block()
    assert "sub-entry|itemHeader" in block
    assert "$seenIdx.Contains($i)" in block and "Step back over the gap" in block


def test_collect_tries_every_way_of_moving_before_it_stops():
    block = _collect_block()
    assert 'if ($stall -eq 2) { $method = "wheel" }' in block
    assert 'elseif ($stall -eq 3) { $method = "pagedown" }' in block
    assert "if ($stall -ge 4) { break }" in block


def test_teams_is_brought_forward_only_for_mouse_or_keys():
    block = _collect_block()
    assert block.count("Ensure-Front") >= 3  # defined, used by Wheel, used by Keys
    wheel = block[block.index("function Wheel"):block.index("function Keys")]
    keys = block[block.index("function Keys"):block.index("function HeaderKeys")]
    assert "Ensure-Front" in wheel and "Ensure-Front" in keys
