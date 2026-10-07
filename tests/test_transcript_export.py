"""⬇ Download: a transcript saved as Markdown or plain text, Downloads by default."""

from __future__ import annotations

import pytest

import teams_recording as trec
import transcript_export as tx
from recording_window import RecordingImportWindow


# --- Markdown -------------------------------------------------------------------

def test_markdown_has_a_heading_and_bold_speakers():
    text = "[0:03] Ada Lovelace: Let me pull up the deck.\n\n[1:06] Grace Hopper: Go ahead."
    assert tx.to_markdown(text, "KPI review transcript") == (
        "# KPI review transcript\n\n"
        "**[0:03] Ada Lovelace:** Let me pull up the deck.\n\n"
        "**[1:06] Grace Hopper:** Go ahead.\n"
    )


def test_markdown_without_timestamps_or_title():
    assert tx.to_markdown("Ada Lovelace: Hello.") == "**Ada Lovelace:** Hello.\n"


def test_markdown_keeps_one_caption_per_line():
    """Zoom's saved captions have no speakers, one cue per line. Markdown would run
    them together into one paragraph without hard breaks."""
    assert tx.to_markdown("Yes.\nShall we start?\nGo ahead.") == (
        "Yes.  \nShall we start?  \nGo ahead.\n"
    )


def test_markdown_does_not_turn_speech_into_structure():
    md = tx.to_markdown("# not a heading\n- not a list\n1. not a list\n> not a quote")
    assert md == "\\# not a heading  \n\\- not a list  \n1\\. not a list  \n\\> not a quote\n"


# --- writing ---------------------------------------------------------------------

def test_the_extension_picks_the_format(tmp_path):
    text = "[0:03] Ada Lovelace: Hello."
    md = tx.write_transcript(tmp_path / "t.md", text, title="Standup")
    txt = tx.write_transcript(tmp_path / "t.txt", text, title="Standup")
    assert md.read_text(encoding="utf-8").startswith("# Standup\n\n**[0:03] Ada Lovelace:**")
    assert txt.read_text(encoding="utf-8") == text + "\n"


def test_save_offers_downloads_and_markdown_first(tmp_path, monkeypatch):
    seen = {}

    def fake_dialog(**kw):
        seen.update(kw)
        return str(tmp_path / "picked.md")

    monkeypatch.setattr(tx.filedialog, "asksaveasfilename", fake_dialog)
    path = tx.save_transcript(None, "Ada Lovelace: Hi.", filename='KPI: Q3 "review"',
                              title="KPI review")
    assert path == tmp_path / "picked.md" and path.exists()
    assert seen["initialdir"] == str(tx.downloads_dir())
    assert seen["initialfile"] == "KPI Q3 review.md", "reserved characters must not reach Windows"
    assert seen["filetypes"][0] == ("Markdown", "*.md")
    assert ("Text", "*.txt") in seen["filetypes"]


def test_cancelling_or_nothing_to_save_writes_nothing(monkeypatch):
    monkeypatch.setattr(tx.filedialog, "asksaveasfilename", lambda **kw: "")
    assert tx.save_transcript(None, "Ada Lovelace: Hi.", filename="x") is None
    monkeypatch.setattr(tx.filedialog, "asksaveasfilename",
                        lambda **kw: pytest.fail("no dialog for an empty transcript"))
    assert tx.save_transcript(None, "   ", filename="x") is None


def test_downloads_dir_exists():
    assert tx.downloads_dir().is_dir()


@pytest.mark.parametrize("raw, expected", [
    ('a<b>c:d"e/f\\g|h?i*j', "a b c d e f g h i j"),
    ("  trailing dots... ", "trailing dots"),
    ("", "Transcript"),
])
def test_safe_filename(raw, expected):
    assert tx.safe_filename(raw) == expected


# --- naming the file after the meeting --------------------------------------------

@pytest.mark.parametrize("title, expected", [
    ("KPI review | Contoso | ada@contoso.com | Microsoft Teams", "KPI review"),
    ("Chat | KPI review | Contoso | ada@contoso.com | Microsoft Teams", "KPI review"),
    ("Microsoft Teams", ""),
    ("", ""),
])
def test_meeting_name_from_a_teams_window_title(title, expected):
    assert trec.meeting_name(title) == expected


def test_the_extractor_reports_which_window_it_read():
    src = trec.extractor_script().read_text(encoding="utf-8", errors="replace")
    done = next(ln for ln in src.splitlines() if 'type = "done"' in ln)
    assert "window = $windowTitle" in done


# --- the import window ---------------------------------------------------------------

def _entries():
    a = trec.Entry("Ada Lovelace", 3); a.lines.append("Let me pull up the deck.")
    return [a]


@pytest.mark.usefixtures("tk_root")
def test_import_window_downloads_markdown_named_after_the_meeting(tk_root, tmp_path, monkeypatch):
    seen = {}

    def fake_dialog(**kw):
        seen.update(kw)
        return str(tmp_path / kw["initialfile"])

    monkeypatch.setattr(tx.filedialog, "asksaveasfilename", fake_dialog)
    win = RecordingImportWindow(tk_root)
    try:
        win._on_done(_entries(), {"window": "KPI review | Contoso | ada@contoso.com | Microsoft Teams"})
        assert str(win.download_btn["state"]) == "normal"
        win.download()
        saved = tmp_path / "KPI review transcript.md"
        assert seen["initialfile"] == saved.name
        assert saved.read_text(encoding="utf-8") == (
            "# KPI review transcript\n\n**[0:03] Ada Lovelace:** Let me pull up the deck.\n"
        )
        assert str(saved) in win.v_status.get()
    finally:
        win.destroy()


@pytest.mark.usefixtures("tk_root")
def test_import_window_names_the_file_by_date_when_teams_does_not_say(tk_root):
    win = RecordingImportWindow(tk_root)
    try:
        win._on_done(_entries(), {})
        assert win.download_name().startswith("Teams transcript 20")
    finally:
        win.destroy()
