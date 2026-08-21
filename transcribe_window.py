"""Transcribe-a-file window — drop in a voice note, get text out.

The companion UI to audio_transcribe.py. Everything heavy happens in a child
process, so this module imports nothing but Tk and stdlib: the window stays
responsive while a model runs, and a native crash in the speech engine surfaces
as an error message instead of killing Cue.
"""

from __future__ import annotations

import tkinter as tk
from collections.abc import Callable
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

import audio_transcribe as at
import config as cfg_mod
import knowledge_base as kb

SAVE_FORMATS = [
    ("Text", "*.txt"),
    ("Markdown", "*.md"),
    ("SubRip subtitles", "*.srt"),
    ("WebVTT subtitles", "*.vtt"),
    ("All files", "*.*"),
]


class TranscribeWindow(tk.Toplevel):
    """Pick a file, transcribe it, then send the text wherever it's useful."""

    def __init__(
        self,
        parent: tk.Misc,
        *,
        initial_path: str | None = None,
        on_summarize: Callable[[str], None] | None = None,
        on_insert: Callable[[str], None] | None = None,
    ) -> None:
        super().__init__(parent)
        self.title("Transcribe a file  ·  Cue")
        self.geometry("880x680")
        self.minsize(720, 540)
        self.transient(parent)

        self._on_summarize = on_summarize
        self._on_insert = on_insert

        saved = cfg_mod.get_config().get("transcribe", {}) or {}
        self.v_path = tk.StringVar(value=initial_path or "")
        self.v_quality = tk.StringVar(
            value=at.quality_for_model(saved.get("model", "small"))
        )
        self.v_language = tk.StringVar(
            value=at.LANGUAGE_NAMES.get(saved.get("language", "auto"), "Auto-detect")
        )
        self.v_prompt = tk.StringVar(value=saved.get("initial_prompt", ""))
        self.v_timestamps = tk.BooleanVar(value=bool(saved.get("timestamps", False)))
        self.v_status = tk.StringVar(value="Pick a file, then press Transcribe.")
        self.v_progress = tk.StringVar(value="")
        self.v_quality_note = tk.StringVar(value="")

        self._job: at.TranscribeJob | None = None
        self._result: at.Result | None = None
        self._duration = 0.0
        self._processed = 0.0
        self._started_at = 0.0

        self._build()
        self._on_quality_changed()
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.bind("<Escape>", lambda _e: self._on_close())
        if initial_path:
            self.after(120, self.start)

    # ---- layout ----
    def _build(self) -> None:
        head = ttk.Frame(self, padding=(16, 14, 16, 6))
        head.pack(fill="x")
        ttk.Label(head, text="Transcribe a file", style="Section.TLabel").pack(anchor="w")
        ttk.Label(
            head,
            text=("A WhatsApp voice note, a phone recording, a meeting export — anything "
                  "with speech in it. Runs locally; the audio never leaves your machine."),
            style="Hint.TLabel",
            wraplength=780,
            justify="left",
        ).pack(anchor="w", pady=(2, 0))

        form = ttk.Frame(self, padding=(16, 8, 16, 8))
        form.pack(fill="x")
        form.columnconfigure(1, weight=1)

        # File
        ttk.Label(form, text="File").grid(row=0, column=0, sticky="w", pady=4)
        file_row = ttk.Frame(form)
        file_row.grid(row=0, column=1, columnspan=2, sticky="ew", pady=4)
        file_row.columnconfigure(0, weight=1)
        self.path_entry = ttk.Entry(file_row, textvariable=self.v_path)
        self.path_entry.grid(row=0, column=0, sticky="ew")
        ttk.Button(file_row, text="Browse…", command=self.browse, width=11).grid(
            row=0, column=1, padx=(6, 0)
        )

        # Quality
        ttk.Label(form, text="Quality").grid(row=1, column=0, sticky="w", pady=4)
        self.quality_box = ttk.Combobox(
            form, textvariable=self.v_quality, state="readonly",
            values=list(at.QUALITY_PRESETS), width=20,
        )
        self.quality_box.grid(row=1, column=1, sticky="w", pady=4)
        self.quality_box.bind("<<ComboboxSelected>>", lambda _e: self._on_quality_changed())
        ttk.Label(form, textvariable=self.v_quality_note, style="Hint.TLabel").grid(
            row=1, column=2, sticky="w", padx=(10, 0)
        )

        # Language + timestamps
        ttk.Label(form, text="Language").grid(row=2, column=0, sticky="w", pady=4)
        self.lang_box = ttk.Combobox(
            form, textvariable=self.v_language, state="readonly",
            values=[label for _code, label in at.LANGUAGES], width=20,
        )
        self.lang_box.grid(row=2, column=1, sticky="w", pady=4)
        self.ts_check = ttk.Checkbutton(
            form, text="Show timestamps", variable=self.v_timestamps,
            command=self._rerender,
        )
        self.ts_check.grid(row=2, column=2, sticky="w", padx=(10, 0))

        # Vocabulary hints
        ttk.Label(form, text="Names / jargon").grid(row=3, column=0, sticky="w", pady=4)
        self.prompt_entry = ttk.Entry(form, textvariable=self.v_prompt)
        self.prompt_entry.grid(row=3, column=1, columnspan=2, sticky="ew", pady=4)
        ttk.Label(
            form,
            text="Optional — people, products and acronyms you expect, so they get spelled right.",
            style="Hint.TLabel",
        ).grid(row=4, column=1, columnspan=2, sticky="w")

        # Run controls
        run = ttk.Frame(self, padding=(16, 8, 16, 4))
        run.pack(fill="x")
        self.run_btn = ttk.Button(run, text="▶  Transcribe", command=self.start,
                                  style="Accent.TButton", width=16)
        self.run_btn.pack(side="left")
        self.cancel_btn = ttk.Button(run, text="Cancel", command=self.cancel,
                                     width=10, state="disabled")
        self.cancel_btn.pack(side="left", padx=(8, 0))
        ttk.Label(run, textvariable=self.v_progress, style="Hint.TLabel").pack(
            side="right"
        )

        self.progress = ttk.Progressbar(self, mode="determinate", maximum=1000)
        self.progress.pack(fill="x", padx=16, pady=(0, 8))

        # Transcript
        body = ttk.Frame(self, padding=(16, 0, 16, 8))
        body.pack(fill="both", expand=True)
        self.text = tk.Text(body, wrap="word", undo=True, font=("Segoe UI", 11),
                            relief="flat", borderwidth=1, padx=10, pady=8)
        scroll = ttk.Scrollbar(body, orient="vertical", command=self.text.yview)
        self.text.configure(yscrollcommand=scroll.set)
        self.text.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")

        # Actions
        actions = ttk.Frame(self, padding=(16, 0, 16, 8))
        actions.pack(fill="x")
        self.action_buttons: list[ttk.Button] = []
        for label, command in (
            ("Copy", self.copy),
            ("Save as…", self.save_as),
            ("Add to knowledge base", self.add_to_kb),
        ):
            b = ttk.Button(actions, text=label, command=command, state="disabled")
            b.pack(side="left", padx=(0, 6))
            self.action_buttons.append(b)
        if self._on_insert:
            b = ttk.Button(actions, text="Send to live transcript",
                           command=self.send_to_transcript, state="disabled")
            b.pack(side="right")
            self.action_buttons.append(b)
        if self._on_summarize:
            b = ttk.Button(actions, text="✨ Summarize", command=self.summarize,
                           state="disabled")
            b.pack(side="right", padx=(0, 6))
            self.action_buttons.append(b)

        status = ttk.Frame(self, padding=(16, 2, 16, 8))
        status.pack(fill="x")
        ttk.Label(status, textvariable=self.v_status, style="Hint.TLabel",
                  wraplength=820, justify="left").pack(anchor="w")

    # ---- small helpers ----
    def _on_quality_changed(self) -> None:
        preset = at.QUALITY_PRESETS.get(self.v_quality.get())
        self.v_quality_note.set(preset[1] if preset else "")

    def _language_code(self) -> str:
        label = self.v_language.get()
        for code, name in at.LANGUAGES:
            if name == label:
                return code
        return "auto"

    def _set_inputs_enabled(self, enabled: bool) -> None:
        state = "normal" if enabled else "disabled"
        combo_state = "readonly" if enabled else "disabled"
        self.path_entry.configure(state=state)
        self.prompt_entry.configure(state=state)
        self.quality_box.configure(state=combo_state)
        self.lang_box.configure(state=combo_state)
        self.run_btn.configure(state=state)
        self.cancel_btn.configure(state="disabled" if enabled else "normal")

    def _set_actions_enabled(self, enabled: bool) -> None:
        for b in self.action_buttons:
            b.configure(state="normal" if enabled else "disabled")

    def _current_text(self) -> str:
        return self.text.get("1.0", "end-1c").strip()

    def _save_choices(self) -> None:
        cfg = cfg_mod.get_config()
        cfg.setdefault("transcribe", {})
        cfg["transcribe"]["model"] = at.model_for_quality(self.v_quality.get())
        cfg["transcribe"]["language"] = self._language_code()
        cfg["transcribe"]["initial_prompt"] = self.v_prompt.get().strip()
        cfg["transcribe"]["timestamps"] = bool(self.v_timestamps.get())
        cfg_mod.save_config(cfg)

    # ---- actions ----
    def browse(self) -> None:
        path = filedialog.askopenfilename(
            parent=self, title="Choose an audio or video file", filetypes=at.FILETYPES
        )
        if path:
            self.v_path.set(path)
            self.v_status.set(f"{Path(path).name} selected. Press Transcribe.")

    def start(self) -> None:
        if self._job and self._job.is_running():
            return
        raw = self.v_path.get().strip().strip('"')
        if not raw:
            messagebox.showinfo("Pick a file", "Choose an audio or video file first.",
                                parent=self)
            return
        src = Path(raw)
        if not src.exists():
            messagebox.showerror("File not found", f"No such file:\n{src}", parent=self)
            return
        if not at.is_supported(src):
            if not messagebox.askyesno(
                "Unusual file type",
                f"“{src.suffix or 'no extension'}” isn't a format Cue recognises.\n\n"
                "Try it anyway? It works if ffmpeg can decode the file.",
                parent=self,
            ):
                return

        self._save_choices()
        self.text.delete("1.0", "end")
        self._result = None
        self._duration = 0.0
        self._processed = 0.0
        # Nothing to measure until the model is loaded and the audio decoded, which is
        # the longest wait of the whole run — keep the bar moving so it doesn't look hung.
        self.progress.configure(mode="indeterminate", value=0)
        self.progress.start(18)
        self.v_progress.set("")
        self._set_inputs_enabled(False)
        self._set_actions_enabled(False)
        self.v_status.set("Starting the speech engine… the first run downloads the model.")

        import time
        self._started_at = time.time()

        self._job = at.TranscribeJob(
            src,
            model_size=at.model_for_quality(self.v_quality.get()),
            language=self._language_code(),
            initial_prompt=self.v_prompt.get().strip(),
            download_root=at.default_download_root(),
            on_status=lambda m: self.after(0, self._handle_status, m),
            on_info=lambda d: self.after(0, self._handle_info, d),
            on_segment=lambda s: self.after(0, self._handle_segment, s),
            on_done=lambda r: self.after(0, self._handle_done, r),
            on_error=lambda m: self.after(0, self._handle_error, m),
        )
        self._job.start()

    def cancel(self) -> None:
        if not self._job:
            return
        self.v_status.set("Stopping…")
        job = self._job          # _finish_run clears the attribute
        job.cancel()
        # Whatever came back before the stop is still worth keeping: someone who
        # cancels 40 minutes into a 45-minute recording wants those 40 minutes.
        if job.result.segments:
            self._result = job.result
            self._rerender()
        self._finish_run(
            f"Stopped after {at.format_duration(self._processed)} — "
            "keeping what was transcribed so far."
        )
        if self._current_text():
            self._set_actions_enabled(True)

    # ---- worker callbacks (already marshalled onto the Tk thread) ----
    def _handle_status(self, message: str) -> None:
        self.v_status.set(message)

    def _handle_info(self, info: dict) -> None:
        self._duration = float(info.get("duration") or 0.0)
        # Length is known now, so switch from "working…" to real progress.
        self.progress.stop()
        self.progress.configure(mode="determinate", value=0)
        lang = str(info.get("language") or "")
        prob = float(info.get("language_probability") or 0.0)
        name = at.LANGUAGE_NAMES.get(lang, lang or "unknown")
        detected = (
            f"Detected {name} ({prob:.0%} confident)"
            if self._language_code() == "auto" else f"Language: {name}"
        )
        self.v_status.set(
            f"{detected} · {at.format_duration(self._duration)} of audio · "
            f"whisper {info.get('model', '')}"
        )

    def _handle_segment(self, seg: at.Segment) -> None:
        # A cancelled worker can still have a segment or two queued behind it; those
        # would land after the text has been re-flowed, so drop them.
        if self._job is None:
            return
        # Stream raw segments for immediate feedback; the text is re-flowed into
        # paragraphs once the run finishes.
        at_end = self.text.yview()[1] > 0.98
        self.text.insert("end", seg.text.strip() + " ")
        if at_end:
            self.text.see("end")
        self._processed = max(self._processed, seg.end)
        self._update_progress()

    def _update_progress(self) -> None:
        if self._duration <= 0:
            return
        frac = min(1.0, self._processed / self._duration)
        self.progress.configure(value=int(frac * 1000))
        label = (
            f"{at.format_duration(self._processed)} / "
            f"{at.format_duration(self._duration)} · {frac:.0%}"
        )
        eta = self._estimate_remaining(frac)
        self.v_progress.set(f"{label}{eta}")

    def _estimate_remaining(self, frac: float) -> str:
        import time
        if frac <= 0.02 or frac >= 0.999 or not self._started_at:
            return ""
        elapsed = time.time() - self._started_at
        remaining = elapsed / frac - elapsed
        if remaining < 5:
            return ""
        if remaining < 90:
            return f"  ·  about {int(remaining)}s left"
        return f"  ·  about {int(round(remaining / 60))} min left"

    def _handle_done(self, result: at.Result) -> None:
        self._result = result
        self._processed = self._duration
        self.progress.configure(value=1000)
        self.v_progress.set("")
        self._rerender()
        if not result.segments:
            self._finish_run(
                "No speech found in that file. If it's a video, check that it has an "
                "audio track; if it's very quiet, try turning off silence trimming."
            )
            return
        words = len(result.text.split())
        speed = (
            f" ({result.duration / result.elapsed:.1f}x faster than real time)"
            if result.elapsed > 0 and result.duration > 0 else ""
        )
        self._finish_run(
            f"Done — {words:,} words from {at.format_duration(result.duration)} of "
            f"{result.language_label} audio in {result.elapsed:.0f}s{speed}."
        )
        self._set_actions_enabled(True)

    def _handle_error(self, message: str) -> None:
        self._finish_run("Transcription failed.")
        if self._current_text():
            self._set_actions_enabled(True)
        messagebox.showerror("Transcription failed", message, parent=self)

    def _finish_run(self, status: str) -> None:
        # stop() zeroes the bar, so only call it while it's still animating —
        # otherwise a completed run would snap back from 100% to empty.
        if str(self.progress.cget("mode")) == "indeterminate":
            self.progress.stop()
            self.progress.configure(mode="determinate", value=0)
        self._set_inputs_enabled(True)
        self.v_status.set(status)
        self._job = None

    def _rerender(self) -> None:
        """Redraw the finished transcript, honouring the timestamps toggle."""
        if not self._result or not self._result.segments:
            return
        text = (
            at.to_timestamped(self._result.segments)
            if self.v_timestamps.get()
            else at.to_plain(self._result.segments)
        )
        self.text.delete("1.0", "end")
        self.text.insert("1.0", text)
        self.text.see("1.0")

    # ---- what to do with the transcript ----
    def copy(self) -> None:
        text = self._current_text()
        if not text:
            return
        self.clipboard_clear()
        self.clipboard_append(text)
        self.update()
        self.v_status.set(f"Copied {len(text):,} characters to the clipboard.")

    def save_as(self) -> None:
        text = self._current_text()
        if not text:
            return
        stem = Path(self.v_path.get()).stem or "transcript"
        path = filedialog.asksaveasfilename(
            parent=self, title="Save transcript", defaultextension=".txt",
            initialfile=f"{stem}-transcript.txt", filetypes=SAVE_FORMATS,
        )
        if not path:
            return
        out = Path(path)
        fmt = out.suffix.lower().lstrip(".")
        if self._result and fmt in ("srt", "vtt", "md"):
            # Subtitles and Markdown are rebuilt from the segments — the text box has
            # only the flowed paragraphs, which have lost the per-segment timings.
            body = at.FORMATTERS[fmt](self._result)
        else:
            body = text + "\n"
        try:
            out.write_text(body, encoding="utf-8")
        except OSError as e:
            messagebox.showerror("Could not save", str(e), parent=self)
            return
        self.v_status.set(f"Saved to {out}")

    def add_to_kb(self) -> None:
        text = self._current_text()
        if not text:
            return
        title = Path(self.v_path.get()).name or "Transcribed audio"
        try:
            info = kb.index_transcript(text, title=title)
        except Exception as e:
            messagebox.showerror("Could not index", f"{type(e).__name__}: {e}", parent=self)
            return
        if info.get("already_indexed"):
            self.v_status.set(f"“{title}” is already in the knowledge base.")
        else:
            self.v_status.set(
                f"Added “{title}” to the knowledge base ({info.get('chunks', 0)} chunks)."
            )

    def summarize(self) -> None:
        text = self._current_text()
        if text and self._on_summarize:
            self._on_summarize(text)

    def send_to_transcript(self) -> None:
        text = self._current_text()
        if not text or not self._on_insert:
            return
        self._on_insert(text)
        self.v_status.set("Added to the live transcript — the assist panel will pick it up.")

    # ---- teardown ----
    def _on_close(self) -> None:
        if self._job and self._job.is_running():
            if not messagebox.askyesno(
                "Stop transcribing?",
                "A transcription is still running. Stop it and close this window?",
                parent=self,
            ):
                return
            self._job.cancel()
        self.destroy()
