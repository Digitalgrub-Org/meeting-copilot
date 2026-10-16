"""The small Markdown renderer behind the Brief, Questions and Chip in tabs."""

from __future__ import annotations

import tkinter as tk

import pytest

from md_render import render_markdown

pytestmark = pytest.mark.usefixtures("tk_root")


@pytest.fixture
def text(tk_root):
    w = tk.Text(tk_root)
    yield w
    w.destroy()


def _shown(w: tk.Text) -> str:
    return w.get("1.0", "end-1c").rstrip("\n")


def _tagged(w: tk.Text, tag: str) -> list[str]:
    ranges = w.tag_ranges(tag)
    return [w.get(ranges[i], ranges[i + 1]) for i in range(0, len(ranges), 2)]


def test_chip_in_reasons_render_as_italics_not_asterisks(text):
    """The Chip in prompt asks for '*(why this lands)*' after every line; the renderer
    knew only bold, so every suggestion showed raw asterisks."""
    render_markdown(text, '1. "I can take the renewal bug." *(shows ownership)*')
    shown = _shown(text)
    assert "*" not in shown
    assert shown == '1.  "I can take the renewal bug." (shows ownership)'
    assert _tagged(text, "li_italic") == ["(shows ownership)"]


def test_bold_still_works_and_is_not_read_as_italics(text):
    render_markdown(text, "Plan: **fix by Wednesday**, test *Thursday*.")
    assert _shown(text) == "Plan: fix by Wednesday, test Thursday."
    assert _tagged(text, "bold_body") == ["fix by Wednesday"]
    assert _tagged(text, "italic_body") == ["Thursday"]


@pytest.mark.parametrize("line", ["5 * 3 = 15", "a*b*c", "a lone * star", "** not bold"])
def test_asterisks_that_are_not_emphasis_stay(text, line):
    render_markdown(text, line)
    assert _shown(text) == line
    assert not _tagged(text, "italic_body")


def test_a_star_bullet_is_still_a_bullet(text):
    render_markdown(text, "* Support gets a note today *(owner: Saravana)*")
    assert _shown(text) == "•  Support gets a note today (owner: Saravana)"
    assert _tagged(text, "li_italic") == ["(owner: Saravana)"]
