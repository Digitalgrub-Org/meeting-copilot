"""Lightweight Markdown → tk.Text renderer.

Handles the subset our LLM output uses: #/##/### headings, - and * bullets,
1. numbered lists, **bold** and *italic* inline, and blank-line spacing. Not a full
Markdown parser — just enough to make briefs and questions read nicely without a
webview.

Usage:
    from md_render import render_markdown
    render_markdown(text_widget, markdown_string)   # clears + re-renders
"""

from __future__ import annotations

import re
import tkinter as tk

# **bold**, then *italic*. Italic was missing, and the Chip in prompt asks for an italic
# "*(why this lands)*" after every suggestion, so each one showed raw asterisks. The
# italic form needs a non-space just inside each asterisk and no word character or
# asterisk just outside, so "5 * 3", "a*b" and the halves of ** are left alone.
_INLINE_RE = re.compile(
    r"\*\*(?P<bold>.+?)\*\*"
    r"|(?<![\w*])\*(?P<italic>[^\s*](?:.*?[^\s*])?)\*(?![\w*])"
)
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
    w.tag_configure("italic_body", font=(base, 10, "italic"))
    # Bullets / numbered items get a hanging indent so wrapped lines align.
    w.tag_configure("li", font=(base, 10), lmargin1=14, lmargin2=28, spacing3=2)
    w.tag_configure("li_bold", font=(base, 10, "bold"), lmargin1=14, lmargin2=28)
    w.tag_configure("li_italic", font=(base, 10, "italic"), lmargin1=14, lmargin2=28)


def _insert_inline(w: tk.Text, text: str, base_tag: str) -> None:
    """Insert a line, applying bold to **...** and italic to *...* spans."""
    bold_tag, italic_tag = {
        "body": ("bold_body", "italic_body"),
        "li": ("li_bold", "li_italic"),
    }[base_tag]
    pos = 0
    for m in _INLINE_RE.finditer(text):
        if m.start() > pos:
            w.insert("end", text[pos:m.start()], base_tag)
        if m.group("bold") is not None:
            w.insert("end", m.group("bold"), bold_tag)
        else:
            w.insert("end", m.group("italic"), italic_tag)
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
            _insert_inline(w, m.group(1), "li")
            continue

        m = _NUM_RE.match(line)
        if m:
            w.insert("end", f"{m.group(1)}.  ", "li_bold")
            _insert_inline(w, m.group(2), "li")
            continue

        _insert_inline(w, line, "body")

    if was_disabled:
        w.configure(state="disabled")
