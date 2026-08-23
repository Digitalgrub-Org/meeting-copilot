"""Right-side panel that auto-generates a running brief and suggested questions
from the live transcript at configurable cadences.

LiveAssistPanel is a ttk.Frame you embed in a PanedWindow. It does its own
background polling — call .start() once attached, .stop() to halt.
"""

from __future__ import annotations

import threading
import time
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


def _short_error(msg: str) -> str:
    """Compress a backend failure into a status line worth reading.

    The raw text is a wall of exception, and truncating it mid-sentence tells the user
    nothing. These are the failures that actually happen; say what to do about them.
    """
    low = msg.lower()
    if "ollama" in low and ("connect" in low or "refused" in low):
        return "Ollama isn't running — Tools → Start Ollama engine"
    if "model" in low and ("not found" in low or "no such" in low):
        return "That model isn't installed — try ollama pull"
    if any(k in low for k in ("api key", "authentication", "401", "invalid_api_key")):
        return "API key rejected — check Settings → API keys"
    if "rate" in low and "limit" in low:
        return "Rate limited by the API — it'll retry"
    if any(k in low for k in ("timed out", "timeout", "connection")):
        return "Can't reach the AI backend"
    return msg.split("\n")[0][:70]

# Cadence used *before* a panel has produced anything. The configured intervals are
# 120s and 180s, which as a cold start means staring at an empty panel for two minutes
# with no clue whether it is working. Tested in a real Meet call and the panel simply
# stayed blank, which reads as broken. So until a panel has output, retry quickly.
FIRST_RUN_DELAY_SEC = 20
MIN_FIRST_CHARS = 150   # ...but wait for this much speech, so the first brief has substance
STATUS_TICK_MS = 2000   # how often the idle countdown refreshes


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
        self.status_after_id: str | None = None
        # When each loop next fires, so the idle status can count down to it.
        self.next_brief_at: float | None = None
        self.next_questions_at: float | None = None
        self.next_points_at: float | None = None
        # Last failure per loop, kept so the countdown can't bury it. "Ollama is not
        # running" is the single most useful thing the panel ever has to say.
        self.brief_error: str | None = None
        self.questions_error: str | None = None
        self.points_error: str | None = None
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
        self._tick_status()

    def _tick_status(self) -> None:
        """Keep the waiting message honest until a panel has produced something.

        A blank panel with a stale "Waiting for first capture…" is indistinguishable
        from a broken app, which is exactly how it read in a real Meet call. Show what
        is being waited for and when the next attempt lands. Only touches panels that
        have never produced output, so real statuses and errors are never clobbered.
        """
        self.status_after_id = self.after(STATUS_TICK_MS, self._tick_status)
        if self.paused.get():
            return
        transcript = self.get_transcript()
        for prev, pos, in_flight, next_at, var, err in (
            (self.prev_brief, self.last_brief_pos, self.brief_in_flight,
             self.next_brief_at, self.brief_status, self.brief_error),
            (self.prev_questions, self.last_questions_pos, self.questions_in_flight,
             self.next_questions_at, self.questions_status, self.questions_error),
            (self.prev_points, self.last_points_pos, self.points_in_flight,
             self.next_points_at, self.points_status, self.points_error),
        ):
            if prev or in_flight or next_at is None:
                continue
            retry_in = max(0, int(next_at - time.monotonic()))
            if err:
                # Keep the failure visible; only the countdown moves.
                var.set(f"{err} · retrying in {retry_in}s")
                continue
            new_chars = len(transcript[pos:].strip())
            if new_chars < MIN_FIRST_CHARS:
                var.set(f"Listening… {new_chars}/{MIN_FIRST_CHARS} characters of speech")
            else:
                var.set(f"{new_chars:,} characters captured · first update in {retry_in}s")

    def stop(self) -> None:
        if self.status_after_id:
            try:
                self.after_cancel(self.status_after_id)
            except tk.TclError:
                pass
            self.status_after_id = None
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
        seconds = FIRST_RUN_DELAY_SEC if not self.prev_brief else max(2, self.brief_interval.get())
        self.next_brief_at = time.monotonic() + seconds
        self.brief_after_id = self.after(int(seconds * 1000), self._kick_brief)

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
        # Nothing said yet, or barely anything. The status ticker explains the wait.
        if not force and not self.prev_brief and len(new_part.strip()) < MIN_FIRST_CHARS:
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
        self.brief_error = None
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
        self.brief_error = _short_error(msg)
        self.brief_status.set(self.brief_error)

    # --- Questions loop ---
    def _schedule_questions(self) -> None:
        seconds = (FIRST_RUN_DELAY_SEC if not self.prev_questions
                   else max(2, self.questions_interval.get()))
        self.next_questions_at = time.monotonic() + seconds
        self.questions_after_id = self.after(int(seconds * 1000), self._kick_questions)

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
        if not force and not self.prev_questions and len(new_part.strip()) < MIN_FIRST_CHARS:
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
        self.questions_error = None
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
        self.questions_error = _short_error(msg)
        self.questions_status.set(self.questions_error)

    # --- Points ("Chip in") loop ---
    def _schedule_points(self) -> None:
        seconds = (FIRST_RUN_DELAY_SEC if not self.prev_points
                   else max(2, self.points_interval.get()))
        self.next_points_at = time.monotonic() + seconds
        self.points_after_id = self.after(int(seconds * 1000), self._kick_points)

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
        if not force and not self.prev_points and len(new_part.strip()) < MIN_FIRST_CHARS:
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
        self.points_error = None
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
        self.points_error = _short_error(msg)
        self.points_status.set(self.points_error)
