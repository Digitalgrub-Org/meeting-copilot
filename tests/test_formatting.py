"""Transcript shaping and cleanup. Pure logic, no model, fast."""

from __future__ import annotations

import audio_transcribe as at


def seg(start: float, end: float, text: str) -> at.Segment:
    return at.Segment(start=start, end=end, text=text)


# --- paragraphs -------------------------------------------------------------

def test_short_gaps_join_into_one_paragraph():
    out = at.paragraphs([seg(0, 2, "First bit."), seg(2.1, 4, "Same paragraph.")])
    assert len(out) == 1
    assert out[0].text == "First bit. Same paragraph."
    assert (out[0].start, out[0].end) == (0, 4)


def test_a_pause_starts_a_new_paragraph():
    out = at.paragraphs([seg(0, 2, "Before the pause."), seg(9, 11, "After it.")])
    assert [p.text for p in out] == ["Before the pause.", "After it."]


def test_pause_threshold_is_the_boundary():
    gap = at.PARAGRAPH_PAUSE_GAP
    assert len(at.paragraphs([seg(0, 1, "a."), seg(1 + gap - 0.1, 2, "b.")])) == 1
    assert len(at.paragraphs([seg(0, 1, "a."), seg(1 + gap + 0.1, 2, "b.")])) == 2


def test_empty_segments_are_dropped():
    out = at.paragraphs([seg(0, 1, "Real."), seg(1, 2, "   "), seg(2, 3, "Also real.")])
    assert len(out) == 1
    assert out[0].text == "Real. Also real."


def test_no_segments_yields_nothing():
    assert at.paragraphs([]) == []
    assert at.to_plain([]) == ""


# --- output formats ---------------------------------------------------------

def test_plain_separates_paragraphs_with_a_blank_line():
    text = at.to_plain([seg(0, 2, "One."), seg(9, 11, "Two.")])
    assert text == "One.\n\nTwo."


def test_timestamps_prefix_each_paragraph_with_its_start():
    text = at.to_timestamped([seg(0, 2, "One."), seg(65, 67, "Two.")])
    assert text.startswith("[00:00]  One.")
    assert "[01:05]  Two." in text


def test_hours_appear_only_when_needed():
    assert at.format_duration(59) == "00:59"
    assert at.format_duration(600) == "10:00"
    assert at.format_duration(3661) == "1:01:01"


def test_srt_is_numbered_with_comma_milliseconds():
    out = at.to_srt([seg(0, 1.5, "Hello.")]).splitlines()
    assert out[0] == "1"
    assert out[1] == "00:00:00,000 --> 00:00:01,500"
    assert out[2] == "Hello."


def test_vtt_has_a_header_and_dot_milliseconds():
    out = at.to_vtt([seg(0, 1.5, "Hello.")]).splitlines()
    assert out[0] == "WEBVTT"
    assert "00:00:00.000 --> 00:00:01.500" in out


def test_markdown_carries_the_metadata_and_the_body():
    result = at.Result(
        segments=[seg(0, 2, "Hello.")], language="en", language_probability=0.99,
        duration=2.0, model="small", device="cpu", source="note.opus",
    )
    md = at.to_markdown(result)
    assert md.startswith("# Transcript — note.opus")
    assert "**Language:** English (99% confident)" in md
    assert "whisper small (cpu)" in md
    assert "\n\nHello.\n" in md   # blank line between metadata and body


def test_every_named_format_produces_something():
    result = at.Result(segments=[seg(0, 1, "Hi.")], source="a.opus")
    for name, fn in at.FORMATTERS.items():
        assert fn(result).strip(), f"formatter {name!r} produced nothing"


# --- initial prompt ---------------------------------------------------------

def test_empty_hints_mean_no_prompt():
    assert at.build_initial_prompt("") is None
    assert at.build_initial_prompt("   ") is None


def test_a_term_list_is_wrapped_into_a_punctuated_sentence():
    """Whisper copies its prompt's style: a bare list suppresses all punctuation."""
    out = at.build_initial_prompt("Contoso, Northwind, Atlas API")
    assert out.endswith(".")
    assert "Contoso, Northwind, Atlas API" in out


def test_trailing_list_punctuation_is_tidied():
    assert at.build_initial_prompt("a, b,").endswith("a, b.")


def test_prose_is_left_alone():
    prose = "We discussed the Q3 roadmap."
    assert at.build_initial_prompt(prose) == prose


# --- cleanup ----------------------------------------------------------------

def test_canned_phrase_over_silence_is_dropped():
    out = list(at.clean_segments([(0, 1, "Thanks for watching!", 0.9)]))
    assert out == []


def test_the_same_phrase_as_real_speech_is_kept():
    out = list(at.clean_segments([(0, 1, "Thanks for watching!", 0.1)]))
    assert len(out) == 1


def test_repetition_loops_are_collapsed():
    raw = [(i, i + 1, "Loop.", 0.1) for i in range(8)]
    assert len(list(at.clean_segments(raw))) == 2   # max_repeats default


def test_repetition_counter_resets_on_new_text():
    raw = [(0, 1, "A.", 0.1), (1, 2, "A.", 0.1), (2, 3, "B.", 0.1), (3, 4, "A.", 0.1)]
    assert [s.text for s in at.clean_segments(raw)] == ["A.", "A.", "B.", "A."]


def test_whitespace_is_normalised():
    out = list(at.clean_segments([(0, 1, "  spaced   out  ", 0.1)]))
    assert out[0].text == "spaced out"


def test_clean_segments_streams_rather_than_collecting():
    """It must be lazy: building a list would delay every segment until the end,
    which breaks the progress bar and loses partial output on cancel."""
    import inspect
    assert inspect.isgeneratorfunction(at.clean_segments)


# --- presets and helpers ----------------------------------------------------

def test_quality_presets_round_trip():
    for label in at.QUALITY_PRESETS:
        assert at.quality_for_model(at.model_for_quality(label)) == label


def test_unknown_quality_falls_back_to_the_default():
    assert at.model_for_quality("nonsense") == at.model_for_quality(at.DEFAULT_QUALITY)


def test_whatsapp_voice_notes_are_a_supported_extension():
    assert at.is_supported("note.opus")
    assert at.is_supported("VIDEO.MP4")      # case-insensitive
    assert not at.is_supported("notes.txt")


def test_auto_is_the_first_language_option():
    assert at.LANGUAGES[0][0] == "auto"
