"""Caption parsing: speaker attribution, UI-chrome filtering, progressive revisions.

This is the oldest and least glamorous part of Cue, and until now the least tested.
Every case below came from replaying a real Teams UI Automation capture.
"""

from __future__ import annotations

import live_capture as lc


# --- speaker detection ------------------------------------------------------

def test_a_name_above_a_longer_line_is_a_speaker():
    items = lc.parse_raw("Ada Lovelace\nI think we should ship on Friday instead.")
    assert items == [("Ada Lovelace", "I think we should ship on Friday instead.")]


def test_a_one_word_utterance_is_not_a_speaker():
    """Replaying a real capture, "Thanks" became a speaker and swallowed the next
    line as its caption. Shape alone cannot tell them apart."""
    items = lc.parse_raw("Thanks\nThat covers everything I wanted to raise.")
    assert items == [
        (None, "Thanks"),
        (None, "That covers everything I wanted to raise."),
    ]


def test_real_names_are_still_accepted():
    for name in ("Ada", "Grace Hopper", "J.R. Example", "Michael O'Hara",
                 "Jean-Luc Picard", "Ada Lovelace"):
        assert lc.looks_like_speaker(name), name


def test_common_utterances_are_rejected():
    for word in ("Thanks", "Thank You", "Okay", "Yes", "No", "Sure", "Exactly",
                 "Good Morning", "Hold On", "Wow", "Alright", "Perfect"):
        assert not lc.looks_like_speaker(word), word


def test_a_sentence_is_not_a_speaker():
    assert not lc.looks_like_speaker("We should ship on Friday.")
    assert not lc.looks_like_speaker("Really?")


def test_lowercase_lines_are_not_speakers():
    assert not lc.looks_like_speaker("ada lovelace")


def test_a_very_long_name_is_rejected():
    assert not lc.looks_like_speaker("One Two Three Four Five")


def test_a_speaker_needs_something_to_attribute():
    """A trailing name with nothing after it stays unattributed rather than
    swallowing nothing."""
    assert lc.parse_raw("Some caption text here.\nAda Lovelace") == [
        (None, "Some caption text here."),
        (None, "Ada Lovelace"),
    ]


def test_a_speaker_needs_a_longer_following_line():
    # "Hi." is shorter than the candidate, so this is two utterances, not attribution.
    assert lc.parse_raw("Ada Lovelace\nHi.") == [(None, "Ada Lovelace"), (None, "Hi.")]


# --- UI chrome --------------------------------------------------------------

def test_message_box_placeholder_is_dropped():
    assert lc.is_ui_chrome("Type a message")
    assert "Type a message" not in [t for _s, t in lc.parse_raw("Type a message\nReal speech here.")]


def test_timestamps_are_dropped():
    for stamp in ("11:26 PM", "Yesterday at 11:55 PM.", "Yesterday 11:55 PM",
                  "Today 09:03", "9:15", "14:07:22"):
        assert lc.is_ui_chrome(stamp), stamp


def test_presence_rows_are_dropped():
    for row in ("Chat Ada Lovelace Offline", "Chat Grace Hopper Available",
                "Chat Alan Turing Busy"):
        assert lc.is_ui_chrome(row), row


def test_real_speech_is_not_mistaken_for_chrome():
    """The filter must stay conservative: dropping speech is worse than keeping noise."""
    for line in ("Thanks", "Chats", "You", "We met at 11:26 PM to discuss it",
                 "Let's move the deadline", "Sent the file over this morning",
                 "I will share my screen"):
        assert not lc.is_ui_chrome(line), line


def test_document_labels_and_room_names_are_dropped():
    raw = "[document] Calendar | Microsoft Teams\n[text] Untitled\n[text] Actual speech."
    assert lc.parse_raw(raw) == [(None, "Actual speech.")]


def test_element_type_labels_are_stripped():
    items = lc.parse_raw("[text] Ada Lovelace\n[text] A longer caption line follows.")
    assert items == [("Ada Lovelace", "A longer caption line follows.")]


def test_blank_and_whitespace_lines_are_skipped():
    assert lc.parse_raw("\n   \nReal line.\n\n") == [(None, "Real line.")]


def test_empty_input_yields_nothing():
    assert lc.parse_raw("") == []


# --- normalisation and revisions --------------------------------------------

def test_normalize_ignores_case_punctuation_and_spacing():
    assert lc.normalize_text("Hello,   World!") == lc.normalize_text("hello world")


def test_identical_text_is_a_revision():
    assert lc.is_revision_of("we should ship", "we should ship")


def test_a_growing_caption_is_a_revision():
    """Teams types captions out progressively; each poll sees a longer version."""
    assert lc.is_revision_of("We should ship", "We should ship on Friday")


def test_a_shrinking_caption_is_a_revision():
    assert lc.is_revision_of("We should ship on Friday", "We should ship")


def test_an_overlapping_tail_is_a_revision():
    prev = "the migration failed validation on about two percent of records"
    new = "on about two percent of records, mostly missing postal codes"
    assert lc.is_revision_of(prev, new)


def test_unrelated_lines_are_not_revisions():
    assert not lc.is_revision_of("We should ship on Friday",
                                 "The budget came in under by eleven thousand")


def test_empty_text_is_never_a_revision():
    assert not lc.is_revision_of("", "anything")
    assert not lc.is_revision_of("anything", "")
