"""Import the transcript of a Teams recording you can see but can't download.

Companion UI to teams_recording.py. The heavy lifting (scrolling the pane and reading
it through UI Automation) runs in a child PowerShell process; this window shows
progress and then offers the usual places to send the text.
"""

from __future__ import annotations

import tkinter as tk
from collections.abc import Callable
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

import config as cfg_mod
import knowledge_base as kb
import teams_recording as trec


def _stamp(seconds: int) -> str:
    h, rem = divmod(int(seconds), 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


class RecordingImportWindow(tk.Toplevel):
    def __init__(
        self,
        parent: tk.Misc,
        *,
        on_insert: Callable[[str], None] | None = None,
        on_summarize: Callable[[str], None] | None = None,
    ) -> None:
        super().__init__(parent)
        self.title("Import a Teams recording transcript  ·  Cue")
        self.geometry("820x640")
        self.minsize(680, 520)
        self.transient(parent)

        self._on_insert = on_insert
        self._on_summarize = on_summarize
        self._job: trec.RecordingImport | None = None
        self._entries: list[trec.Entry] = []
        self.v_status = tk.StringVar(
            value="Open the recording in Teams and click its Transcript tab, then press Start."
        )
        self.v_progress = tk.StringVar(value="")
        self.v_timestamps = tk.BooleanVar(value=True)

        self._build()
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.bind("<Escape>", lambda _e: self._on_close())

    # ---- layout ----
    def _build(self) -> None:
        head = ttk.Frame(self, padding=(16, 14, 16, 6))
        head.pack(fill="x")
        ttk.Label(head, text="Import a Teams recording transcript",
                  style="Section.TLabel").pack(anchor="w")
        ttk.Label(
            head,
            text=("Teams shows the transcript beside a recording even when your tenant "
                  "won't let you download it. Cue reads it off the screen the same way it "
                  "reads live captions, scrolling the pane from top to bottom.\n\n"
                  "1. Open the recording in the Teams desktop app.\n"
                  "2. Click the Transcript tab so the text is visible.\n"
                  "3. Press Start and leave Teams alone until it finishes — it needs the "
                  "mouse for scrolling."),
            style="Hint.TLabel", wraplength=760, justify="left",
        ).pack(anchor="w", pady=(4, 0))

        run = ttk.Frame(self, padding=(16, 8, 16, 4))
        run.pack(fill="x")
        self.start_btn = ttk.Button(run, text="▶  Start", command=self.start,
                                    style="Accent.TButton", width=12)
        self.start_btn.pack(side="left")
        self.cancel_btn = ttk.Button(run, text="Cancel", command=self.cancel,
                                     width=10, state="disabled")
        self.cancel_btn.pack(side="left", padx=(8, 0))
        ttk.Checkbutton(run, text="Show timestamps", variable=self.v_timestamps,
                        command=self._render).pack(side="left", padx=(16, 0))
        ttk.Label(run, textvariable=self.v_progress, style="Hint.TLabel").pack(side="right")

        self.progress = ttk.Progressbar(self, mode="indeterminate")
        self.progress.pack(fill="x", padx=16, pady=(0, 8))

        body = ttk.Frame(self, padding=(16, 0, 16, 8))
        body.pack(fill="both", expand=True)
        self.text = tk.Text(body, wrap="word", undo=True, font=("Segoe UI", 11),
                            relief="flat", borderwidth=1, padx=10, pady=8)
        scroll = ttk.Scrollbar(body, orient="vertical", command=self.text.yview)
        self.text.configure(yscrollcommand=scroll.set)
        self.text.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")

        actions = ttk.Frame(self, padding=(16, 0, 16, 8))
        actions.pack(fill="x")
        self.action_buttons: list[ttk.Button] = []
        for label, cmd in (("Copy", self.copy), ("Save as…", self.save_as),
                           ("Add to knowledge base", self.add_to_kb)):
            b = ttk.Button(actions, text=label, command=cmd, state="disabled")
            b.pack(side="left", padx=(0, 6))
            self.action_buttons.append(b)
        if self._on_insert:
            b = ttk.Button(actions, text="Send to live transcript",
                           command=self.send_to_transcript, state="disabled")
            b.pack(side="right")
            self.action_buttons.append(b)
        if self._on_summarize:
            b = ttk.Button(actions, text="✨ Summarize", command=self.summarize, state="disabled")
            b.pack(side="right", padx=(0, 6))
            self.action_buttons.append(b)

        status = ttk.Frame(self, padding=(16, 2, 16, 8))
        status.pack(fill="x")
        ttk.Label(status, textvariable=self.v_status, style="Hint.TLabel",
                  wraplength=780, justify="left").pack(anchor="w")

    # ---- run ----
    def start(self) -> None:
        if self._job and self._job.is_running():
            return
        out = cfg_mod.DATA_DIR / "teams_recording_raw.txt"
        self._entries = []
        self.text.delete("1.0", "end")
        self._set_actions(False)
        self.start_btn.configure(state="disabled")
        self.cancel_btn.configure(state="normal")
        self.progress.start(18)
        self.v_status.set("Finding the Transcript pane in Teams…")
        self.v_progress.set("")
        self._job = trec.RecordingImport(
            out,
            on_progress=lambda d: self.after(0, self._on_progress, d),
            on_done=lambda e, d: self.after(0, self._on_done, e, d),
            on_error=lambda m: self.after(0, self._on_error, m),
        )
        self._job.start()

    def cancel(self) -> None:
        if self._job:
            self._job.cancel()
        self._finish("Stopped.")

    def _on_progress(self, d: dict) -> None:
        entries = int(d.get("entries") or 0)
        covered = _stamp(int(d.get("max_seconds") or 0))
        self.v_progress.set(f"pass {d.get('pass', '?')} · {entries} entries · reached {covered}")
        self.v_status.set("Scrolling the transcript… leave Teams alone until this finishes.")

    def _on_done(self, entries: list[trec.Entry], d: dict) -> None:
        self._entries = entries
        self._render()
        if not entries:
            self._finish("Read the pane but found no transcript entries. Is the Transcript "
                         "tab showing, and does this recording have a transcript?")
            return
        first, last = trec.coverage(entries)
        words = sum(len(e.text.split()) for e in entries)
        self._finish(f"Done — {len(entries)} entries, {words:,} words, "
                     f"{_stamp(first)} to {_stamp(last)}, in {d.get('passes', '?')} passes.")
        self._set_actions(True)

    def _on_error(self, message: str) -> None:
        self._finish("Import failed.")
        messagebox.showerror("Import failed", message, parent=self)

    def _finish(self, status: str) -> None:
        self.progress.stop()
        self.start_btn.configure(state="normal")
        self.cancel_btn.configure(state="disabled")
        self.v_status.set(status)
        self._job = None

    def _render(self) -> None:
        if not self._entries:
            return
        self.text.delete("1.0", "end")
        self.text.insert("1.0", trec.to_text(self._entries, timestamps=self.v_timestamps.get()))
        self.text.see("1.0")

    def _set_actions(self, on: bool) -> None:
        for b in self.action_buttons:
            b.configure(state="normal" if on else "disabled")

    def _current_text(self) -> str:
        return self.text.get("1.0", "end-1c").strip()

    # ---- actions ----
    def copy(self) -> None:
        text = self._current_text()
        if text:
            self.clipboard_clear()
            self.clipboard_append(text)
            self.update()
            self.v_status.set(f"Copied {len(text):,} characters.")

    def save_as(self) -> None:
        text = self._current_text()
        if not text:
            return
        path = filedialog.asksaveasfilename(
            parent=self, title="Save transcript", defaultextension=".txt",
            initialfile="teams-recording-transcript.txt",
            filetypes=[("Text", "*.txt"), ("Markdown", "*.md"), ("All files", "*.*")],
        )
        if path:
            Path(path).write_text(text + "\n", encoding="utf-8")
            self.v_status.set(f"Saved to {path}")

    def add_to_kb(self) -> None:
        if not self._entries:
            return
        try:
            info = kb.index_transcript(trec.to_cue_transcript(self._entries),
                                       title="Teams recording transcript")
        except Exception as e:  # noqa: BLE001
            messagebox.showerror("Could not index", f"{type(e).__name__}: {e}", parent=self)
            return
        self.v_status.set("Already in the knowledge base." if info.get("already_indexed")
                          else f"Added to the knowledge base ({info.get('chunks', 0)} chunks).")

    def summarize(self) -> None:
        if self._entries and self._on_summarize:
            self._on_summarize(trec.to_cue_transcript(self._entries))

    def send_to_transcript(self) -> None:
        if self._entries and self._on_insert:
            self._on_insert(trec.to_cue_transcript(self._entries))
            self.v_status.set("Sent to the live transcript.")

    def _on_close(self) -> None:
        if self._job and self._job.is_running():
            if not messagebox.askyesno("Stop importing?", "Still scrolling. Stop and close?",
                                       parent=self):
                return
            self._job.cancel()
        self.destroy()
