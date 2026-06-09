"""Lightweight Markdown → tk.Text renderer.

Handles the subset our LLM output uses: #/##/### headings, - and * bullets,
1. numbered lists, **bold** inline, and blank-line spacing. Not a full Markdown
parser — just enough to make briefs and questions read nicely without a webview.

Usage:
    from md_render import render_markdown
    render_markdown(text_widget, markdown_string)   # clears + re-renders
"""

from __future__ import annotations

import re
import tkinter as tk

_BOLD_RE = re.compile(r"\*\*(.+?)\*\*")
_NUM_RE = re.compile(r"^(\d+)\.\s+(.*)$")
_BULLET_RE = re.compile(r"^[-*]\s+(.*)$")
_HEADING_RE = re.compile(r"^(#{1,3})\s+(.*)$")


def _configure_tags(w: tk.Text) -> None:
    base = "Segoe UI"
    w.tag_configure("h1", font=(base, 15, "bold"), spacing1=8, spacing3=4)
    w.tag_configure("h2", font=(base, 12, "bold"), spacing1=8, spacing3=3)
    w.tag_configure("h3", font=(base, 11, "bold"), spacing1=6, spacing3=2)
    w.tag_configure("body", font=(base, 10), spacing3=2, lmargin1=2, lmargin2=2)
    w.tag_configure("bold", font=(base, 10, "bold"))
    w.tag_configure("bold_body", font=(base, 10, "bold"))
    # Bullets / numbered items get a hanging indent so wrapped lines align.
    w.tag_configure("li", font=(base, 10), lmargin1=14, lmargin2=28, spacing3=2)
    w.tag_configure("li_bold", font=(base, 10, "bold"), lmargin1=14, lmargin2=28)


def _insert_inline(w: tk.Text, text: str, base_tag: str, bold_tag: str) -> None:
    """Insert a line, applying bold to **...** spans."""
    pos = 0
    for m in _BOLD_RE.finditer(text):
        if m.start() > pos:
            w.insert("end", text[pos:m.start()], base_tag)
        w.insert("end", m.group(1), bold_tag)
        pos = m.end()
    if pos < len(text):
        w.insert("end", text[pos:], base_tag)
    w.insert("end", "\n", base_tag)


def render_markdown(w: tk.Text, md: str) -> None:
    """Clear the widget and render `md` with simple Markdown styling."""
    was_disabled = str(w.cget("state")) == "disabled"
    if was_disabled:
        w.configure(state="normal")
    _configure_tags(w)
    w.delete("1.0", "end")

    for raw in (md or "").split("\n"):
        line = raw.rstrip()
        if not line.strip():
            w.insert("end", "\n", "body")
            continue

        m = _HEADING_RE.match(line)
        if m:
            level = len(m.group(1))
            tag = {1: "h1", 2: "h2", 3: "h3"}[level]
            w.insert("end", m.group(2) + "\n", tag)
            continue

        m = _BULLET_RE.match(line)
        if m:
            w.insert("end", "•  ", "li")
            _insert_inline(w, m.group(1), "li", "li_bold")
            continue

        m = _NUM_RE.match(line)
        if m:
            w.insert("end", f"{m.group(1)}.  ", "li_bold")
            _insert_inline(w, m.group(2), "li", "li_bold")
            continue

        _insert_inline(w, line, "body", "bold_body")

    if was_disabled:
        w.configure(state="disabled")
