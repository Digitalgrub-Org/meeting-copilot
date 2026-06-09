"""Right-side panel that auto-generates a running brief and suggested questions
from the live transcript at configurable cadences.

LiveAssistPanel is a ttk.Frame you embed in a PanedWindow. It does its own
background polling — call .start() once attached, .stop() to halt.
"""

from __future__ import annotations

import threading
import tkinter as tk
from datetime import datetime
from tkinter import ttk
from typing import Callable

from md_render import render_markdown
from summarize import generate_brief, generate_questions, generate_talking_points

# Cadences in seconds (default — user can adjust via Spinbox)
DEFAULT_BRIEF_INTERVAL = 120
DEFAULT_QUESTIONS_INTERVAL = 180
DEFAULT_POINTS_INTERVAL = 150
MIN_NEW_CHARS = 200  # don't bother updating if fewer than this many new chars since last call


class LiveAssistPanel(ttk.Frame):
    def __init__(
        self,
        parent: tk.Misc,
        *,
        get_transcript: Callable[[], str],
        resolve_backend: Callable[[], tuple[str, str | None] | None],
    ) -> None:
        super().__init__(parent)
        self.get_transcript = get_transcript
        self.resolve_backend = resolve_backend

        # State for incremental brief / questions / points (resume from where we left off)
        self.last_brief_pos = 0
        self.prev_brief = ""
        self.last_questions_pos = 0
        self.prev_questions = ""
        self.last_points_pos = 0
        self.prev_points = ""

        self.brief_after_id: str | None = None
        self.questions_after_id: str | None = None
        self.points_after_id: str | None = None
        self.brief_in_flight = False
        self.questions_in_flight = False
        self.points_in_flight = False
        self.paused = tk.BooleanVar(value=False)

        self.brief_interval = tk.IntVar(value=DEFAULT_BRIEF_INTERVAL)
        self.questions_interval = tk.IntVar(value=DEFAULT_QUESTIONS_INTERVAL)
        self.points_interval = tk.IntVar(value=DEFAULT_POINTS_INTERVAL)

        # Notebook tab bookkeeping for "updated" badges
        self.nb: ttk.Notebook | None = None
        self._tab_base: dict = {}

        self._build_ui()

    def _build_ui(self) -> None:
        try:
            style = ttk.Style()
            style.configure("Assist.Section.TLabel", font=("Segoe UI Semibold", 11))
            style.configure("Assist.Hint.TLabel", foreground="#7a7a7a")
        except tk.TclError:
            pass

        # Header
        ctrl = ttk.Frame(self, padding=(12, 12, 12, 6))
        ctrl.pack(fill="x")
        ttk.Label(ctrl, text="Live assist", font=("Segoe UI Semibold", 13)).pack(side="left")
        ttk.Checkbutton(ctrl, text="Pause", variable=self.paused).pack(side="right")

        self.brief_status = tk.StringVar(value="Waiting for first capture…")
        self.questions_status = tk.StringVar(value="Waiting for first capture…")
        self.points_status = tk.StringVar(value="Waiting for first capture…")

        self.nb = ttk.Notebook(self)
        self.nb.pack(fill="both", expand=True, padx=6, pady=(0, 6))

        brief_tab, self.brief_text = self._build_section(
            self.nb, title="Running brief", interval_var=self.brief_interval,
            status_var=self.brief_status, on_refresh=lambda: self._kick_brief(force=True))
        self._add_tab(brief_tab, "📋 Brief")

        q_tab, self.questions_text = self._build_section(
            self.nb, title="Questions to ask", interval_var=self.questions_interval,
            status_var=self.questions_status, on_refresh=lambda: self._kick_questions(force=True))
        self._add_tab(q_tab, "❓ Questions")

        p_tab, self.points_text = self._build_section(
            self.nb, title="What you could say right now", interval_var=self.points_interval,
            status_var=self.points_status, on_refresh=lambda: self._kick_points(force=True))
        self._add_tab(p_tab, "💬 Chip in")

        self.nb.bind("<<NotebookTabChanged>>", self._on_tab_changed)

    def _add_tab(self, frame: ttk.Frame, label: str) -> None:
        self.nb.add(frame, text=label)
        self._tab_base[str(frame)] = label

    def _build_section(self, parent, *, title, interval_var, status_var, on_refresh):
        """One tab: title row, cadence+refresh row, status line, then text area."""
        wrap = ttk.Frame(parent, padding=(12, 10, 12, 10))

        ttk.Label(wrap, text=title, style="Assist.Section.TLabel").pack(anchor="w")

        controls = ttk.Frame(wrap)
        controls.pack(fill="x", pady=(2, 4))
        ttk.Label(controls, text="every", style="Assist.Hint.TLabel").pack(side="left")
        ttk.Spinbox(controls, from_=2, to=900, increment=2, width=5,
                    textvariable=interval_var).pack(side="left", padx=(4, 2))
        ttk.Label(controls, text="sec", style="Assist.Hint.TLabel").pack(side="left")
        ttk.Button(controls, text="↻ Refresh", command=on_refresh).pack(side="right")

        ttk.Label(wrap, textvariable=status_var, style="Assist.Hint.TLabel").pack(anchor="w")

        body = ttk.Frame(wrap)
        body.pack(fill="both", expand=True, pady=(4, 0))
        txt = tk.Text(body, wrap="word", font=("Segoe UI", 10), undo=True,
                      relief="flat", borderwidth=1, padx=8, pady=6)
        sb = ttk.Scrollbar(body, orient="vertical", command=txt.yview)
        txt.configure(yscrollcommand=sb.set)
        txt.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        return wrap, txt

    # --- Tab "updated" badges ---
    def _on_tab_changed(self, _event=None) -> None:
        if not self.nb:
            return
        cur = self.nb.select()
        base = self._tab_base.get(cur)
        if base:
            self.nb.tab(cur, text=base)  # clear badge on the now-visible tab

    def _mark_tab_updated(self, text_widget: tk.Text) -> None:
        """Put a • badge on the tab containing text_widget if it isn't the visible one."""
        if not self.nb:
            return
        # find the tab frame that is an ancestor of text_widget
        w = text_widget
        while w is not None and str(w) not in self._tab_base:
            w = w.master
        if w is None:
            return
        frame_id = str(w)
        if self.nb.select() != frame_id:
            base = self._tab_base.get(frame_id, "")
            if base and not base.startswith("• "):
                self.nb.tab(frame_id, text="• " + base)

    # --- Lifecycle ---
    def start(self) -> None:
        self._schedule_brief()
        self._schedule_questions()
        self._schedule_points()

    def stop(self) -> None:
        for attr in ("brief_after_id", "questions_after_id", "points_after_id"):
            aid = getattr(self, attr)
            if aid:
                try:
                    self.after_cancel(aid)
                except tk.TclError:
                    pass
                setattr(self, attr, None)

    def reset(self) -> None:
        """Called when transcript is cleared."""
        self.last_brief_pos = 0
        self.prev_brief = ""
        self.last_questions_pos = 0
        self.prev_questions = ""
        self.last_points_pos = 0
        self.prev_points = ""
        self.brief_text.delete("1.0", "end")
        self.questions_text.delete("1.0", "end")
        self.points_text.delete("1.0", "end")
        self.brief_status.set("Reset.")
        self.questions_status.set("Reset.")
        self.points_status.set("Reset.")

    # --- Brief loop ---
    def _schedule_brief(self) -> None:
        interval_ms = max(2, self.brief_interval.get()) * 1000
        self.brief_after_id = self.after(interval_ms, self._kick_brief)

    def _kick_brief(self, *, force: bool = False) -> None:
        # Re-schedule the next tick first so cadence stays consistent
        self._schedule_brief()
        if self.paused.get() and not force:
            return
        if self.brief_in_flight:
            return
        transcript = self.get_transcript()
        new_part = transcript[self.last_brief_pos:]
        if not force and len(new_part) < MIN_NEW_CHARS and self.prev_brief:
            self.brief_status.set(f"Skipped — only {len(new_part)} new chars since last update.")
            return
        if not new_part.strip() and not self.prev_brief:
            return
        resolved = self.resolve_backend()
        if not resolved:
            self.brief_status.set("No backend (set Claude key or start Ollama).")
            return
        backend, model = resolved
        self.brief_in_flight = True
        self.brief_status.set(f"Updating via {backend}…")
        cap_len = len(transcript)

        def work() -> None:
            try:
                result = generate_brief(
                    new_part,
                    prev_brief=self.prev_brief or None,
                    backend=backend,
                    ollama_model=model,
                )
                self.after(0, lambda r=result, n=cap_len: self._on_brief_done(r, n))
            except Exception as e:
                msg = f"{type(e).__name__}: {e}"
                self.after(0, lambda m=msg: self._on_brief_error(m))

        threading.Thread(target=work, daemon=True).start()

    def _on_brief_done(self, brief: str, new_pos: int) -> None:
        self.brief_in_flight = False
        if brief.strip():
            self.prev_brief = brief
            self.last_brief_pos = new_pos
            render_markdown(self.brief_text, brief)
            self._mark_tab_updated(self.brief_text)
            self.brief_status.set(f"Updated at {datetime.now().strftime('%H:%M:%S')}.")
        else:
            self.brief_status.set("Got empty response.")

    def _on_brief_error(self, msg: str) -> None:
        self.brief_in_flight = False
        self.brief_status.set(f"Error: {msg[:100]}")

    # --- Questions loop ---
    def _schedule_questions(self) -> None:
        interval_ms = max(2, self.questions_interval.get()) * 1000
        self.questions_after_id = self.after(interval_ms, self._kick_questions)

    def _kick_questions(self, *, force: bool = False) -> None:
        self._schedule_questions()
        if self.paused.get() and not force:
            return
        if self.questions_in_flight:
            return
        transcript = self.get_transcript()
        new_part = transcript[self.last_questions_pos:]
        if not force and len(new_part) < MIN_NEW_CHARS and self.prev_questions:
            self.questions_status.set(f"Skipped — only {len(new_part)} new chars since last update.")
            return
        if not new_part.strip() and not self.prev_questions:
            return
        resolved = self.resolve_backend()
        if not resolved:
            self.questions_status.set("No backend.")
            return
        backend, model = resolved
        self.questions_in_flight = True
        self.questions_status.set(f"Generating via {backend}…")
        cur_len = len(transcript)

        def work() -> None:
            try:
                result = generate_questions(
                    new_part,
                    prev_questions=self.prev_questions or None,
                    backend=backend,
                    ollama_model=model,
                )
                self.after(0, lambda r=result, n=cur_len: self._on_questions_done(r, n))
            except Exception as e:
                msg = f"{type(e).__name__}: {e}"
                self.after(0, lambda m=msg: self._on_questions_error(m))

        threading.Thread(target=work, daemon=True).start()

    def _on_questions_done(self, questions: str, new_pos: int) -> None:
        self.questions_in_flight = False
        if questions.strip():
            self.last_questions_pos = new_pos
            self.prev_questions = questions
            render_markdown(self.questions_text, questions)
            self._mark_tab_updated(self.questions_text)
            self.questions_status.set(f"Updated at {datetime.now().strftime('%H:%M:%S')}.")
        else:
            self.questions_status.set("Got empty response.")

    def _on_questions_error(self, msg: str) -> None:
        self.questions_in_flight = False
        self.questions_status.set(f"Error: {msg[:100]}")

    # --- Points ("Chip in") loop ---
    def _schedule_points(self) -> None:
        interval_ms = max(2, self.points_interval.get()) * 1000
        self.points_after_id = self.after(interval_ms, self._kick_points)

    def _kick_points(self, *, force: bool = False) -> None:
        self._schedule_points()
        if self.paused.get() and not force:
            return
        if self.points_in_flight:
            return
        transcript = self.get_transcript()
        new_part = transcript[self.last_points_pos:]
        if not force and len(new_part) < MIN_NEW_CHARS and self.prev_points:
            self.points_status.set(f"Skipped — only {len(new_part)} new chars since last update.")
            return
        if not new_part.strip() and not self.prev_points:
            return
        resolved = self.resolve_backend()
        if not resolved:
            self.points_status.set("No backend.")
            return
        backend, model = resolved
        self.points_in_flight = True
        self.points_status.set(f"Thinking of what you could say via {backend}…")
        cur_len = len(transcript)

        def work() -> None:
            try:
                result = generate_talking_points(
                    new_part,
                    prev_points=self.prev_points or None,
                    backend=backend,
                    ollama_model=model,
                )
                self.after(0, lambda r=result, n=cur_len: self._on_points_done(r, n))
            except Exception as e:
                msg = f"{type(e).__name__}: {e}"
                self.after(0, lambda m=msg: self._on_points_error(m))

        threading.Thread(target=work, daemon=True).start()

    def _on_points_done(self, points: str, new_pos: int) -> None:
        self.points_in_flight = False
        if points.strip():
            self.last_points_pos = new_pos
            self.prev_points = points
            render_markdown(self.points_text, points)
            self._mark_tab_updated(self.points_text)
            self.points_status.set(f"Updated at {datetime.now().strftime('%H:%M:%S')}.")
        else:
            self.points_status.set("Got empty response.")

    def _on_points_error(self, msg: str) -> None:
        self.points_in_flight = False
        self.points_status.set(f"Error: {msg[:100]}")
