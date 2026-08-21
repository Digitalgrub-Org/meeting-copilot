"""Live Teams Captions capture — polls the Teams Captions window and appends
new caption lines as they arrive. Captions panel only shows ~20 lines at a time,
so this needs to keep up while the meeting is running.

Run:  python live_capture.py
"""

import os
# Avoid OpenMP duplicate-library segfaults on Windows + Anaconda.
# Must be set BEFORE any import that pulls in numpy/torch.
os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")
os.environ.setdefault("OMP_NUM_THREADS", "1")

import queue
import re
import subprocess
import sys
import threading
import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

import config as cfg_mod
import knowledge_base as kb
import window_capture
import worker_ipc
from auto_assist import LiveAssistPanel
from settings_window import SettingsWindow
from summarize import (
    SummaryWindow,
    get_api_key,
    list_ollama_models,
    pick_default_ollama_model,
    prompt_for_api_key,
    set_api_key,
)


def _resource_dir() -> Path:
    """Where bundled read-only resources live. Handles PyInstaller (frozen) and source runs."""
    if getattr(sys, "frozen", False):
        # PyInstaller: onefile extracts to _MEIPASS; onedir puts data under the exe dir.
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    return Path(__file__).parent


# Read-only bundled resources
EXTRACT_PS1 = _resource_dir() / "extract_teams_transcript.ps1"
ICON_PATH = _resource_dir() / "cue.ico"
# Writable scratch file — never inside the (possibly read-only) bundle. On DATA_DIR (off-C when configured).
WRITABLE_DIR = cfg_mod.DATA_DIR
RAW_TXT = WRITABLE_DIR / "teams_extracted_raw.txt"
SPEAKER_RE = re.compile(r"^[A-Z][\w'’\-\.]+(?:\s+[A-Z][\w'’\-\.]+)*$")
LABEL_RE = re.compile(r"^\[([^\]]+)\]\s*(.*)$")
ROOM_LABELS = {"ACME Tower 7", "Untitled"}

# Teams puts a speaker's name alone on the line above their caption, so "a short
# capitalised line" is the only shape we have to go on. Trouble is a one-word
# utterance looks identical: replaying a real capture, "Thanks" became a speaker and
# swallowed the next line as its caption. Shape can't separate these, so exclude the
# common utterances by name.
NOT_SPEAKER = frozenset("""
thanks thank ok okay yes no yeah yep nope right sure hello hi hey sorry please
exactly correct perfect great good nice cool wow true false maybe well so and but
actually anyway alright absolutely agreed understood indeed same congratulations
bye goodbye welcome morning afternoon evening night oh ah um uh hmm mhm yay oops
""".split())

# Multi-word phrases that pass the capitalised-words shape test but are speech.
NOT_SPEAKER_PHRASES = frozenset({
    "thank you", "thanks a lot", "thank you so much", "good morning",
    "good afternoon", "good evening", "good night", "see you", "talk soon",
    "got it", "makes sense", "fair enough", "no worries", "no problem",
    "sounds good", "will do", "hold on", "go ahead", "one moment", "let me see",
    "sorry about that", "excuse me", "over to you", "you too", "same here",
})


# UI Automation reads whatever text a window exposes, which includes the app's own
# furniture. Replaying a real capture, the transcript picked up "Type a message",
# message timestamps and the whole chat sidebar, all of which then went into the LLM
# prompt as if someone had said it.
#
# Deliberately conservative: only whole-line matches for phrases that are unambiguously
# chrome. Bare words like "Chats" or "You" are left alone, because a caption line can
# legitimately be one word and dropping real speech is worse than keeping some noise.
UI_CHROME = frozenset({
    "type a message", "type a new message", "more options", "show more", "show less",
    "raise hand", "meeting chat", "live captions", "turn off live captions",
    "new message", "unread", "sent", "delivered", "edited", "seen",
    "drafts", "favorites", "type a message to reply", "reply in thread",
})

# A line that is only a timestamp: "Yesterday at 11:55 PM.", "11:26 PM", "Today 09:03".
TIMESTAMP_ONLY_RE = re.compile(
    r"^(?:yesterday|today|tomorrow|mon|tue|wed|thu|fri|sat|sun\w*)?\s*"
    r"(?:at\s+)?\d{1,2}:\d{2}(?::\d{2})?\s*(?:am|pm)?\s*\.?$",
    re.IGNORECASE,
)

# A contact row in the chat list, e.g. "Chat Ada Lovelace Offline".
PRESENCE_ROW_RE = re.compile(
    r"^chat\b.*\b(available|offline|busy|away|do not disturb|be right back)\s*$",
    re.IGNORECASE,
)


def is_ui_chrome(line: str) -> bool:
    """Is this line part of the app's interface rather than something said?"""
    bare = " ".join(line.split())
    if not bare:
        return True
    if bare.lower().rstrip(".") in UI_CHROME:
        return True
    return bool(TIMESTAMP_ONLY_RE.match(bare) or PRESENCE_ROW_RE.match(bare))


def looks_like_speaker(line: str) -> bool:
    """Is this line a speaker label rather than something someone said?"""
    if not SPEAKER_RE.match(line) or len(line.split()) > 4:
        return False
    # Trailing punctuation means it's a sentence, not a name. Keep "J.R." working by
    # only stripping what ends the line.
    bare = " ".join(line.split()).rstrip(".!?,;:")
    if not bare:
        return False
    low = bare.lower()
    return low not in NOT_SPEAKER and low not in NOT_SPEAKER_PHRASES


def parse_raw(raw: str) -> list[tuple[str | None, str]]:
    """Turn the raw extractor output into a list of (speaker, text) pairs.
    speaker is None for un-attributed lines (e.g. live partial captions)."""
    items: list[tuple[str | None, str]] = []
    stripped: list[str] = []
    for ln in raw.splitlines():
        m = LABEL_RE.match(ln)
        label_type = m.group(1) if m else None
        content = (m.group(2) if m else ln).strip()
        if not content:
            continue
        if label_type == "document":
            continue
        if content in ROOM_LABELS:
            continue
        if is_ui_chrome(content):
            continue
        stripped.append(content)

    i = 0
    while i < len(stripped):
        cur = stripped[i]
        is_speaker = (
            looks_like_speaker(cur)
            and i + 1 < len(stripped)
            and len(stripped[i + 1]) > len(cur)
        )
        if is_speaker:
            items.append((cur, stripped[i + 1]))
            i += 2
        else:
            items.append((None, cur))
            i += 1
    return items


def normalize_text(t: str) -> str:
    """Loose key for dedup — collapse whitespace, lowercase, strip punctuation."""
    return re.sub(r"[^a-z0-9 ]+", "", re.sub(r"\s+", " ", t.lower())).strip()


def is_revision_of(prev_text: str, new_text: str) -> bool:
    """True if new_text looks like a continuation/revision of prev_text
    (Teams often types out captions progressively)."""
    a, b = normalize_text(prev_text), normalize_text(new_text)
    if not a or not b:
        return False
    if a == b:
        return True
    if b.startswith(a):
        return True
    if a.startswith(b):
        return True
    # last 30 chars of prev appear at the start of new (overlap revision)
    if len(a) >= 30 and a[-30:] in b[:60]:
        return True
    return False


class LiveCapture:
    def __init__(self, root: tk.Tk):
        self.root = root
        root.title("Cue  ·  by Digitalgrub")
        root.geometry("1240x800")
        root.minsize(960, 600)

        cfg = cfg_mod.get_config()
        self.poll_seconds = tk.IntVar(value=cfg["cadence"].get("caption_poll_sec", 4))
        self.running = False
        self.poll_after_id: str | None = None
        self.queue: queue.Queue = queue.Queue()
        self.captured: list[tuple[str | None, str]] = []  # ordered final list
        self.seen_norm: set[str] = set()  # normalized texts already captured
        self.last_poll_time: datetime | None = None
        self.error_count = 0
        self.whisper = None  # lazy-initialized Whisper capture
        self.capture_window_title = "Captions"  # which window the UIA poller reads

        # Auto-indexing of the in-progress transcript into the KB
        self.meeting_id: str | None = None
        self.last_indexed_doc_id: str | None = None
        self.auto_index_interval_sec = int(cfg["cadence"].get("auto_index_interval_sec", 600))
        self.auto_index_after_id: str | None = None
        self.auto_index_min_chars = 800

        # Shared state vars referenced across the UI
        self.source_var = tk.StringVar(value="Teams desktop")
        initial_backend = cfg["llm"].get("backend", "ollama").capitalize()
        self.backend_var = tk.StringVar(
            value=initial_backend if initial_backend in ("Auto", "Claude", "Ollama") else "Ollama"
        )
        self.ollama_model_var = tk.StringVar(value="")
        self._ollama_models_cache: list[str] = []
        self.assist_visible = tk.BooleanVar(value=True)
        self.theme_var = tk.StringVar(value=cfg["ui"].get("theme", "light"))
        self.stats = tk.StringVar(value="0 captions")

        # React to settings changes
        cfg_mod.on_change(self._on_config_change)

        self._setup_styles()
        self._build_menubar()
        self._build_capture_bar(root)
        self._build_body(root)
        self._build_statusbar(root)

        root.protocol("WM_DELETE_WINDOW", self._on_close)
        root.after(150, self._drain_queue)
        self._schedule_auto_index()
        # Position the split once the window has a real width.
        root.after(80, self._set_initial_sash)
        # Warm up the embedding model on the MAIN thread shortly after the UI shows.
        # Loading off the main thread segfaults on Windows + Anaconda.
        self.set_status("Loading knowledge-base engine… (one-time, ~5s)")
        root.after(200, self._warm_kb)

    # ---- Styles ----
    def _setup_styles(self) -> None:
        style = ttk.Style()
        # An accent style for the primary Start button (sv-ttk ships 'Accent.TButton').
        try:
            style.configure("Section.TLabel", font=("Segoe UI Semibold", 11))
            style.configure("Hint.TLabel", foreground="#7a7a7a")
            style.configure("CaptureBar.TFrame")
        except tk.TclError:
            pass

    # ---- Menu bar ----
    def _build_menubar(self) -> None:
        menubar = tk.Menu(self.root)

        m_file = tk.Menu(menubar, tearoff=0)
        m_file.add_command(label="Transcribe a file…", accelerator="Ctrl+O",
                           command=self.transcribe_file)
        m_file.add_separator()
        m_file.add_command(label="Copy transcript", accelerator="Ctrl+C", command=self.copy_all)
        m_file.add_command(label="Save transcript as…", accelerator="Ctrl+S", command=self.save_as)
        m_file.add_separator()
        m_file.add_command(label="Clear transcript", command=self.clear)
        m_file.add_separator()
        m_file.add_command(label="Exit", command=self._on_close)
        menubar.add_cascade(label="File", menu=m_file)

        m_kb = tk.Menu(menubar, tearoff=0)
        m_kb.add_command(label="Add document…", command=self.add_doc)
        m_kb.add_command(label="Archive current meeting", command=self.archive_now)
        m_kb.add_separator()
        m_kb.add_command(label="Manage knowledge base…", command=self.manage_kb)
        menubar.add_cascade(label="Knowledge Base", menu=m_kb)

        m_tools = tk.Menu(menubar, tearoff=0)
        m_tools.add_command(label="Summarize now", accelerator="Ctrl+Enter", command=self.summarize)
        m_tools.add_command(label="Transcribe a file…", command=self.transcribe_file)
        m_tools.add_command(label="Start Ollama engine", command=self.start_ollama)
        m_tools.add_separator()
        m_tools.add_command(label="Settings…", command=self.open_settings)
        menubar.add_cascade(label="Tools", menu=m_tools)

        m_view = tk.Menu(menubar, tearoff=0)
        m_view.add_checkbutton(label="Live assist panel", variable=self.assist_visible,
                               command=self._toggle_assist_panel)
        m_theme = tk.Menu(m_view, tearoff=0)
        m_theme.add_radiobutton(label="Light", value="light", variable=self.theme_var,
                                command=lambda: self._set_theme("light"))
        m_theme.add_radiobutton(label="Dark", value="dark", variable=self.theme_var,
                                command=lambda: self._set_theme("dark"))
        m_view.add_cascade(label="Theme", menu=m_theme)
        menubar.add_cascade(label="View", menu=m_view)

        m_help = tk.Menu(menubar, tearoff=0)
        m_help.add_command(label="About Cue", command=self._show_about)
        menubar.add_cascade(label="Help", menu=m_help)

        self.root.config(menu=menubar)

        # Keyboard shortcuts
        self.root.bind_all("<Control-Return>", lambda e: self.summarize())
        self.root.bind_all("<Control-s>", lambda e: self.save_as())
        self.root.bind_all("<Control-o>", lambda e: self.transcribe_file())

    # ---- Capture bar ----
    def _build_capture_bar(self, root: tk.Misc) -> None:
        bar = ttk.Frame(root, padding=(14, 12, 14, 12), style="CaptureBar.TFrame")
        bar.pack(fill="x")

        # Primary action
        self.start_btn = ttk.Button(bar, text="▶  Start capturing", command=self.toggle,
                                     style="Accent.TButton", width=18)
        self.start_btn.pack(side="left")

        ttk.Label(bar, text="Source").pack(side="left", padx=(16, 6))
        self.source_box = ttk.Combobox(
            bar, textvariable=self.source_var, state="readonly",
            values=["Teams desktop", "Pick a window…", "System audio (Whisper)"], width=20
        )
        self.source_box.pack(side="left")
        self.source_box.bind("<<ComboboxSelected>>", lambda e: self._on_source_changed())

        # Window picker — only visible when Source = "Pick a window…"
        self.window_title_var = tk.StringVar(value="")
        self.window_box = ttk.Combobox(
            bar, textvariable=self.window_title_var, values=[], width=34, state="readonly"
        )
        self.window_box.configure(postcommand=self._refresh_window_list)

        # Recording indicator
        self.rec_var = tk.StringVar(value="○ Idle")
        self.rec_label = ttk.Label(bar, textvariable=self.rec_var, foreground="#9a9a9a")
        self.rec_label.pack(side="left", padx=(16, 0))
        self._rec_blink_on = False
        self._rec_after_id: str | None = None

        # The offline path: a file you already have (voice note, recording, meeting
        # export) rather than audio happening right now.
        ttk.Button(bar, text="Transcribe a file…", command=self.transcribe_file).pack(
            side="left", padx=(16, 0)
        )

        # AI engine group, right-aligned
        ttk.Button(bar, text="✨ Summarize", command=self.summarize).pack(side="right")
        self.ollama_model_box = ttk.Combobox(
            bar, textvariable=self.ollama_model_var, values=[], width=16, state="readonly"
        )
        self.ollama_model_box.pack(side="right", padx=(6, 12))
        self.backend_box = ttk.Combobox(
            bar, textvariable=self.backend_var, values=["Auto", "Claude", "Ollama"],
            width=8, state="readonly"
        )
        self.backend_box.pack(side="right", padx=(0, 0))
        ttk.Label(bar, text="AI engine").pack(side="right", padx=(0, 6))
        self._refresh_ollama_models()

        ttk.Separator(root, orient="horizontal").pack(fill="x")

    # ---- Body (split) ----
    def _build_body(self, root: tk.Misc) -> None:
        self.main_paned = ttk.PanedWindow(root, orient="horizontal")
        self.main_paned.pack(fill="both", expand=True, padx=12, pady=12)

        # Left: transcript
        left = ttk.Frame(self.main_paned, padding=(0, 0, 6, 0))
        head = ttk.Frame(left)
        head.pack(fill="x", pady=(0, 6))
        ttk.Label(head, text="Live transcript", style="Section.TLabel").pack(side="left")
        ttk.Label(head, text="editable — captions append as they arrive",
                  style="Hint.TLabel").pack(side="left", padx=(10, 0))
        text_body = ttk.Frame(left)
        text_body.pack(fill="both", expand=True)
        self.text = tk.Text(text_body, wrap="word", undo=True, font=("Segoe UI", 11),
                            relief="flat", borderwidth=1, padx=10, pady=8)
        scroll = ttk.Scrollbar(text_body, orient="vertical", command=self.text.yview)
        self.text.configure(yscrollcommand=scroll.set)
        self.text.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        self.main_paned.add(left, weight=3)

        # Right: assist panel
        self.assist_panel = LiveAssistPanel(
            self.main_paned,
            get_transcript=lambda: self.text.get("1.0", "end-1c"),
            resolve_backend=self._resolve_backend_silent,
        )
        # Apply configured cadence at startup (panel defaults are only a fallback).
        cfg = cfg_mod.get_config()
        self.assist_panel.brief_interval.set(int(cfg["cadence"].get("brief_interval_sec", 120)))
        self.assist_panel.questions_interval.set(int(cfg["cadence"].get("questions_interval_sec", 180)))
        self.assist_panel.points_interval.set(int(cfg["cadence"].get("points_interval_sec", 150)))
        self.main_paned.add(self.assist_panel, weight=2)
        # Polling starts after kb.warm_up() finishes (see _warm_kb).

    # ---- Status bar ----
    def _build_statusbar(self, root: tk.Misc) -> None:
        bar = ttk.Frame(root, padding=(12, 4, 12, 4))
        bar.pack(fill="x", side="bottom")
        self.status = tk.StringVar(value="Ready — choose a source and click Start capturing.")
        ttk.Label(bar, textvariable=self.status, style="Hint.TLabel").pack(side="left")
        ttk.Label(bar, textvariable=self.stats, style="Hint.TLabel").pack(side="right")
        self._refresh_stats()

    def _refresh_stats(self) -> None:
        """Update the bottom-right summary: captions · KB docs · backend."""
        try:
            docs = len(kb.list_documents())
        except Exception:
            docs = 0
        backend = self.backend_var.get()
        self.stats.set(f"{len(self.captured)} captions  ·  KB: {docs} items  ·  AI: {backend}")

    def _set_initial_sash(self) -> None:
        try:
            self.root.update_idletasks()
            width = self.main_paned.winfo_width()
            if width > 200:
                self.main_paned.sashpos(0, int(width * 0.60))
        except tk.TclError:
            pass

    def _set_theme(self, mode: str) -> None:
        try:
            import sv_ttk
            sv_ttk.set_theme(mode)
        except Exception:
            return
        self.theme_var.set(mode)
        cfg = cfg_mod.get_config()
        cfg["ui"]["theme"] = mode
        cfg_mod.save_config(cfg)
        self.set_status(f"Theme: {mode}.")

    def _show_about(self) -> None:
        messagebox.showinfo(
            "About Cue",
            "Cue  ·  by Digitalgrub\n\n"
            "Your cue to speak. Live meeting captions + an AI running brief,\n"
            "suggested questions to ask, and on-demand summaries — grounded in\n"
            "your own documents and past transcripts.\n\n"
            "Capture: Teams desktop (UI Automation), System audio (Whisper), or an\n"
            "audio/video file. AI: Ollama (local, free) or Claude API.\n"
            "Retrieval: TF-IDF or OpenAI embeddings.\n\n"
            "Your data stays on this computer unless you enable a cloud AI engine\n"
            "in Settings. No telemetry. See PRIVACY.md.\n\n"
            "Cue transcribes everyone in the meeting — get their consent first.\n\n"
            "MIT licensed."
        )

    def _warm_kb(self) -> None:
        try:
            kb.warm_up()
            self.set_status("Ready — choose a source and click Start capturing.")
        except Exception as e:
            self.set_status(f"KB warm-up failed: {type(e).__name__}: {e}")
        # Safe to start the assist panel polling now that the model is loaded.
        if hasattr(self, "assist_panel"):
            self.assist_panel.start()

    def set_status(self, msg: str) -> None:
        self.status.set(msg)

    def _on_source_changed(self) -> None:
        """Show the window picker only for the 'Pick a window…' source."""
        if self.source_var.get() == "Pick a window…":
            if not self.window_box.winfo_ismapped():
                self.window_box.pack(side="left", padx=(8, 0), after=self.source_box)
            self._refresh_window_list()
        else:
            try:
                self.window_box.pack_forget()
            except tk.TclError:
                pass

    def _refresh_window_list(self) -> None:
        try:
            wins = window_capture.list_windows()
        except Exception as e:
            wins = []
            self.set_status(f"Could not list windows: {e}")
        self.window_box["values"] = wins
        if wins and self.window_title_var.get() not in wins:
            self.window_title_var.set(wins[0])

    def toggle(self) -> None:
        if self.running:
            self.stop()
        else:
            self.start()

    def _confirm_capture_notice(self) -> bool:
        """Show the consent notice once. False means don't start capturing.

        Cue transcribes other people, and in many places that needs their consent.
        Saying so before the first capture is both a Store requirement for products
        handling personal information and simply the honest thing to do.
        """
        cfg = cfg_mod.get_config()
        if cfg["ui"].get("capture_notice_ack", False):
            return True
        agreed = messagebox.askokcancel(
            "Before you capture",
            "Cue transcribes what everyone in the meeting says, not just you.\n\n"
            "In many places, recording or transcribing people without their consent "
            "is against the law or against your employer's policy, and the rules "
            "differ by country and state.\n\n"
            "Please tell the other participants and get their agreement first.\n\n"
            "Everything stays on this computer unless you switch on a cloud AI "
            "engine in Settings. See PRIVACY.md for the details.\n\n"
            "OK to continue. This notice won't appear again.",
            icon="warning",
            default="cancel",
        )
        if not agreed:
            self.set_status("Capture cancelled.")
            return False
        cfg["ui"]["capture_notice_ack"] = True
        cfg_mod.save_config(cfg)
        return True

    def start(self) -> None:
        if not self._confirm_capture_notice():
            return
        source = self.source_var.get()
        if source == "System audio (Whisper)":
            self.running = True
            self.start_btn.configure(text="■  Stop capturing")
            self._start_rec_blink()
            self._start_whisper()
            return

        # UI-Automation sources (Teams desktop / a user-picked window)
        if source == "Pick a window…":
            title = self.window_title_var.get().strip()
            if not title:
                messagebox.showinfo("Pick a window", "Choose a window to read from first.")
                return
            self.capture_window_title = title
            label = title if len(title) < 40 else title[:37] + "…"
            self.status.set(f"Capturing… reading text from “{label}”.")
        else:  # Teams desktop
            self.capture_window_title = "Captions"
            self.status.set("Capturing… polling the Teams Captions window.")

        self.running = True
        self.start_btn.configure(text="■  Stop capturing")
        self._start_rec_blink()
        self._poll()

    def stop(self) -> None:
        self.running = False
        if self.poll_after_id:
            try:
                self.root.after_cancel(self.poll_after_id)
            except tk.TclError:
                pass
            self.poll_after_id = None
        if self.whisper is not None:
            try:
                self.whisper.stop()
            except Exception:
                pass
            self.whisper = None
        self._stop_rec_blink()
        self.start_btn.configure(text="▶  Start capturing")
        self.status.set(f"Stopped. {len(self.captured)} captions captured.")

    # ---- Recording indicator ----
    def _start_rec_blink(self) -> None:
        self._rec_blink_on = True
        self._blink_rec()

    def _stop_rec_blink(self) -> None:
        self._rec_blink_on = False
        if self._rec_after_id:
            try:
                self.root.after_cancel(self._rec_after_id)
            except tk.TclError:
                pass
            self._rec_after_id = None
        self.rec_var.set("○ Idle")
        try:
            self.rec_label.configure(foreground="#9a9a9a")
        except tk.TclError:
            pass

    def _blink_rec(self) -> None:
        if not self._rec_blink_on:
            return
        # Alternate between solid red dot and dim, ~1s cycle
        if self.rec_var.get().startswith("●"):
            self.rec_var.set("○ Recording")
            color = "#d98a8a"
        else:
            self.rec_var.set("● Recording")
            color = "#e01b1b"
        try:
            self.rec_label.configure(foreground=color)
        except tk.TclError:
            pass
        self._rec_after_id = self.root.after(700, self._blink_rec)

    def _start_whisper(self) -> None:
        # whisper_capture only imports stdlib here; the model and the audio device
        # live in a child process, so a missing package or a native crash comes back
        # as a status message rather than taking Cue down.
        from whisper_capture import WhisperCapture

        self.status.set("Starting the speech engine — first run downloads ~150MB…")

        def on_text(_speaker: str, text: str) -> None:
            self.root.after(0, lambda t=text: self._append_whisper(t))

        def on_status(msg: str) -> None:
            self.root.after(0, lambda m=msg: self.set_status(f"Whisper · {m.splitlines()[0]}"))

        def on_error(msg: str) -> None:
            self.root.after(0, lambda m=msg: self._whisper_failed(m))

        self.whisper = WhisperCapture(
            on_text=on_text,
            on_status=on_status,
            on_error=on_error,
            model_size="base.en",
            source="loopback",
        )
        self.whisper.start()

    def _whisper_failed(self, message: str) -> None:
        """The capture worker died. Reset the UI and show the whole explanation."""
        self.stop()
        self.set_status("Audio capture failed.")
        messagebox.showerror("Audio capture failed", message)

    def _append_whisper(self, text: str) -> None:
        # Treat Whisper output as unattributed captions
        self.captured.append((None, text))
        self.seen_norm.add(normalize_text(text))
        at_end = self.text.yview()[1] > 0.98
        self.text.insert("end", text + "\n\n")
        if at_end:
            self.text.see("end")
        self._refresh_stats()

    def _poll(self) -> None:
        if not self.running:
            return
        threading.Thread(target=self._do_extract, daemon=True).start()
        self.poll_after_id = self.root.after(max(2, self.poll_seconds.get()) * 1000, self._poll)

    def _do_extract(self) -> None:
        try:
            subprocess.run(
                [
                    "powershell",
                    "-NoProfile",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-File",
                    str(EXTRACT_PS1),
                    "-OutPath",
                    str(RAW_TXT),
                    "-WindowTitle",
                    self.capture_window_title,
                ],
                capture_output=True,
                timeout=20,
                check=True,
                # Don't flash a console window when running as a packaged (windowed) .exe
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            raw = RAW_TXT.read_text(encoding="utf-8")
            self.queue.put(("ok", raw))
        except subprocess.CalledProcessError as e:
            self.queue.put(("error", e.stderr.decode("utf-8", errors="replace") or str(e)))
        except Exception as e:
            self.queue.put(("error", str(e)))

    def _drain_queue(self) -> None:
        try:
            while True:
                kind, payload = self.queue.get_nowait()
                if kind == "ok":
                    self._handle_extract(payload)
                else:
                    self.error_count += 1
                    self.status.set(f"Extractor error ({self.error_count}): {payload.splitlines()[0] if payload else 'unknown'}")
        except queue.Empty:
            pass
        self.root.after(200, self._drain_queue)

    def _handle_extract(self, raw: str) -> None:
        self.last_poll_time = datetime.now()
        items = parse_raw(raw)
        added = 0
        for speaker, text in items:
            norm = normalize_text(text)
            if not norm:
                continue
            if norm in self.seen_norm:
                continue
            # Check if this revises the previous line from the same speaker
            if self.captured:
                prev_speaker, prev_text = self.captured[-1]
                if prev_speaker == speaker and is_revision_of(prev_text, text):
                    # Replace prev with the longer/revised version
                    self.seen_norm.discard(normalize_text(prev_text))
                    self.captured[-1] = (speaker, text)
                    self.seen_norm.add(norm)
                    self._rerender_last()
                    continue
            self.captured.append((speaker, text))
            self.seen_norm.add(norm)
            self._append(speaker, text)
            added += 1

        self._refresh_stats()
        stamp = self.last_poll_time.strftime("%H:%M:%S")
        if added:
            self.status.set(f"Last poll {stamp} — added {added} new caption(s).")
        else:
            self.status.set(f"Last poll {stamp} — no new captions.")

    def _append(self, speaker: str | None, text: str) -> None:
        at_end = self.text.yview()[1] > 0.98
        line = f"{speaker}: {text}\n\n" if speaker else f"{text}\n\n"
        self.text.insert("end", line)
        if at_end:
            self.text.see("end")

    def _rerender_last(self) -> None:
        # Re-render the last caption block — find last non-empty block and replace
        if not self.captured:
            return
        speaker, text = self.captured[-1]
        full = self.text.get("1.0", "end-1c")
        blocks = full.split("\n\n")
        # drop trailing empty blocks
        while blocks and blocks[-1] == "":
            blocks.pop()
        if blocks:
            blocks[-1] = f"{speaker}: {text}" if speaker else text
        new_full = "\n\n".join(blocks) + "\n\n"
        at_end = self.text.yview()[1] > 0.98
        self.text.delete("1.0", "end")
        self.text.insert("1.0", new_full)
        if at_end:
            self.text.see("end")

    def copy_all(self) -> None:
        text = self.text.get("1.0", "end-1c").strip()
        if not text:
            messagebox.showinfo("Nothing to copy", "No captions captured yet.")
            return
        self.root.clipboard_clear()
        self.root.clipboard_append(text)
        self.root.update()
        self.status.set(f"Copied {len(text):,} chars to clipboard.")

    def save_as(self) -> None:
        text = self.text.get("1.0", "end-1c").strip()
        if not text:
            messagebox.showinfo("Nothing to save", "No captions captured yet.")
            return
        path = filedialog.asksaveasfilename(
            title="Save live transcript",
            defaultextension=".txt",
            filetypes=[("Text", "*.txt"), ("All files", "*.*")],
        )
        if not path:
            return
        Path(path).write_text(text, encoding="utf-8")
        self.status.set(f"Saved to {path}")

    def clear(self) -> None:
        if self.captured and not messagebox.askyesno(
            "Clear captured?", "Discard all captured captions?"
        ):
            return
        self.captured.clear()
        self.seen_norm.clear()
        self.text.delete("1.0", "end")
        self._refresh_stats()
        self.status.set("Cleared.")
        self.meeting_id = None
        self.last_indexed_doc_id = None
        if hasattr(self, "assist_panel"):
            self.assist_panel.reset()

    def _refresh_ollama_models(self) -> None:
        models = list_ollama_models()
        self._ollama_models_cache = models
        self.ollama_model_box["values"] = models
        if models and not self.ollama_model_var.get():
            self.ollama_model_var.set(pick_default_ollama_model(models) or models[0])

    def _resolve_backend_silent(self) -> tuple[str, str | None] | None:
        """Backend resolution for background polling — no dialogs, no prompts.
        Auto mode now prefers Ollama (free, local) over Claude (paid)."""
        choice = self.backend_var.get()
        has_key = bool(get_api_key())
        if choice == "Claude":
            return ("claude", None) if has_key else None
        if choice == "Ollama":
            if not self._ollama_models_cache:
                self._refresh_ollama_models()
            model = self.ollama_model_var.get() or (
                self._ollama_models_cache[0] if self._ollama_models_cache else None
            )
            return ("ollama", model) if model else None
        # Auto: prefer Ollama if available (free), else Claude
        if not self._ollama_models_cache:
            self._refresh_ollama_models()
        if self._ollama_models_cache:
            model = self.ollama_model_var.get() or pick_default_ollama_model(self._ollama_models_cache)
            return ("ollama", model)
        if has_key:
            return ("claude", None)
        return None

    def _toggle_assist_panel(self) -> None:
        if self.assist_visible.get():
            if self.assist_panel not in self.main_paned.panes():
                self.main_paned.add(self.assist_panel, weight=1)
            self.assist_panel.start()
        else:
            self.assist_panel.stop()
            try:
                self.main_paned.forget(self.assist_panel)
            except tk.TclError:
                pass

    def _resolve_backend(self) -> tuple[str, str | None] | None:
        """Returns (backend, ollama_model) or None if user cancelled."""
        choice = self.backend_var.get()
        has_key = bool(get_api_key())

        if choice == "Claude":
            if not has_key:
                key = prompt_for_api_key(self.root)
                if not key:
                    return None
            return ("claude", None)

        if choice == "Ollama":
            self._refresh_ollama_models()
            model = self.ollama_model_var.get() or (self._ollama_models_cache[0]
                                                     if self._ollama_models_cache else None)
            if not model:
                messagebox.showerror(
                    "No Ollama models",
                    "No Ollama models found. Open Ollama and run e.g. `ollama pull llama3.1`."
                )
                return None
            return ("ollama", model)

        # Auto: prefer Ollama (free), fall back to Claude
        self._refresh_ollama_models()
        if self._ollama_models_cache:
            model = self.ollama_model_var.get() or pick_default_ollama_model(self._ollama_models_cache)
            return ("ollama", model)
        if has_key:
            return ("claude", None)
        # nothing available — offer to set Claude key
        if messagebox.askyesno(
            "No backend configured",
            "No Anthropic API key and no Ollama models found.\n\nSet up an Anthropic API key now?"
        ):
            key = prompt_for_api_key(self.root)
            if key:
                return ("claude", None)
        return None

    def summarize(self) -> None:
        text = self.text.get("1.0", "end-1c").strip()
        if not text:
            messagebox.showinfo("Nothing to summarize", "Capture some transcript first.")
            return
        self.summarize_text(text)

    def summarize_text(self, text: str) -> None:
        """Summarize arbitrary text — the live transcript, or an imported one."""
        resolved = self._resolve_backend()
        if not resolved:
            return
        backend, model = resolved
        SummaryWindow(self.root, transcript=text, backend=backend, ollama_model=model)

    def transcribe_file(self, path: str | None = None) -> None:
        """Transcribe an audio/video file you already have (voice note, recording).

        Separate from the capture sources, which listen to audio happening now.
        """
        try:
            from transcribe_window import TranscribeWindow
        except ImportError as e:
            messagebox.showerror(
                "Transcribe unavailable",
                f"The file-transcription window couldn't be loaded.\n\n{e}"
            )
            return
        TranscribeWindow(
            self.root,
            initial_path=path,
            on_summarize=self.summarize_text,
            on_insert=self.append_transcript,
        )

    def append_transcript(self, text: str) -> None:
        """Append imported text to the live transcript so the assist panel picks it up."""
        block = text.strip()
        if not block:
            return
        self.captured.append((None, block))
        self.seen_norm.add(normalize_text(block))
        self.text.insert("end", block + "\n\n")
        self.text.see("end")
        self._refresh_stats()
        self.set_status("Imported transcript added — brief and questions will use it.")

    def set_key(self) -> None:
        prompt_for_api_key(self.root)

    def open_settings(self) -> None:
        SettingsWindow(self.root)

    def start_ollama(self) -> None:
        import ollama_util
        if ollama_util.is_ollama_up():
            self.set_status("Ollama is already running.")
            self._refresh_ollama_models()
            return
        self.set_status("Starting Ollama engine…")

        def work() -> None:
            ok = ollama_util.launch_ollama()
            def done() -> None:
                if ok:
                    self.set_status("Ollama engine started.")
                    self._refresh_ollama_models()
                else:
                    messagebox.showwarning(
                        "Couldn't start Ollama",
                        "Ollama isn't installed or isn't on PATH.\n\n"
                        "Install it from https://ollama.com, then run a model once:\n"
                        "    ollama pull llama3.1\n\n"
                        "Tip: opening the Ollama app from the Start menu keeps it "
                        "running in the tray and auto-starts on login."
                    )
                    self.set_status("Ollama not available.")
            self.root.after(0, done)

        threading.Thread(target=work, daemon=True).start()

    def _on_config_change(self, new_cfg: dict) -> None:
        """Live update runtime values when settings are saved."""
        try:
            self.auto_index_interval_sec = int(new_cfg["cadence"].get("auto_index_interval_sec", 600))
            self.poll_seconds.set(int(new_cfg["cadence"].get("caption_poll_sec", 4)))
            if hasattr(self, "assist_panel"):
                self.assist_panel.brief_interval.set(
                    int(new_cfg["cadence"].get("brief_interval_sec", 120))
                )
                self.assist_panel.questions_interval.set(
                    int(new_cfg["cadence"].get("questions_interval_sec", 180))
                )
                self.assist_panel.points_interval.set(
                    int(new_cfg["cadence"].get("points_interval_sec", 150))
                )
            self.set_status("Settings updated.")
        except Exception as e:
            self.set_status(f"Settings change error: {e}")

    def _schedule_auto_index(self) -> None:
        self.auto_index_after_id = self.root.after(
            self.auto_index_interval_sec * 1000, self._auto_index_tick
        )

    def _auto_index_tick(self) -> None:
        self._schedule_auto_index()
        transcript = self.text.get("1.0", "end-1c")
        if len(transcript) < self.auto_index_min_chars:
            return
        if not self.meeting_id:
            self.meeting_id = f"Meeting {datetime.now().strftime('%Y-%m-%d %H:%M')}"
        threading.Thread(target=self._do_index, args=(transcript, False), daemon=True).start()

    def _do_index(self, transcript: str, notify: bool) -> None:
        try:
            if self.last_indexed_doc_id:
                kb.delete_document(self.last_indexed_doc_id)
            info = kb.index_transcript(transcript, title=self.meeting_id)
            self.last_indexed_doc_id = info.get("doc_id") or None
            chunks = info.get("chunks", 0)
            self.root.after(0, lambda c=chunks: self.set_status(
                f"Transcript indexed into KB · {c} chunks · meeting '{self.meeting_id}'"
            ))
            if notify:
                self.root.after(0, lambda c=chunks: messagebox.showinfo(
                    "Archived",
                    f"Current transcript indexed into the knowledge base as '{self.meeting_id}' ({c} chunks)."
                ))
        except Exception as e:
            err = e
            self.root.after(0, lambda er=err: self.set_status(
                f"Auto-index error: {type(er).__name__}: {er}"
            ))

    def archive_now(self) -> None:
        transcript = self.text.get("1.0", "end-1c")
        if not transcript.strip():
            messagebox.showinfo("Nothing to archive", "Capture some transcript first.")
            return
        if not self.meeting_id:
            self.meeting_id = f"Meeting {datetime.now().strftime('%Y-%m-%d %H:%M')}"
        threading.Thread(target=self._do_index, args=(transcript, True), daemon=True).start()

    def add_doc(self) -> None:
        paths = filedialog.askopenfilenames(
            title="Add document(s) to knowledge base",
            filetypes=[
                ("Supported", "*.pdf *.docx *.txt *.md *.markdown *.vtt *.srt"),
                ("PDF", "*.pdf"),
                ("Word", "*.docx"),
                ("Text/Markdown", "*.txt *.md *.markdown"),
                ("All files", "*.*"),
            ],
        )
        if not paths:
            return
        self.set_status(f"Indexing {len(paths)} file(s) — first run downloads ~90MB embed model…")
        self.root.update_idletasks()

        def work() -> None:
            results = []
            for p in paths:
                try:
                    results.append(("ok", kb.add_document(p)))
                except Exception as e:
                    results.append(("err", f"{Path(p).name}: {type(e).__name__}: {e}"))
            self.root.after(0, lambda: self._after_index(results))

        threading.Thread(target=work, daemon=True).start()

    def _after_index(self, results: list[tuple[str, object]]) -> None:
        lines = []
        added_chunks = 0
        for status, info in results:
            if status == "ok":
                if info["already_indexed"]:
                    lines.append(f"• {info['source']} — already in KB (skipped)")
                else:
                    lines.append(f"• {info['source']} — {info['chunks']} chunks indexed")
                    added_chunks += info["chunks"]
            else:
                lines.append(f"✗ {info}")
        messagebox.showinfo("Knowledge base updated", "\n".join(lines))
        self.set_status(f"KB updated · {added_chunks} new chunks indexed.")

    def manage_kb(self) -> None:
        ManageKBWindow(self.root)

    def _maybe_archive_on_close(self) -> bool:
        """Per config.archive.on_close: ask/always/never. Returns True if we should
        proceed to close, False if the user cancelled."""
        transcript = self.text.get("1.0", "end-1c").strip()
        if not transcript:
            return True
        mode = cfg_mod.get_config()["archive"].get("on_close", "ask")
        if mode == "never":
            return True
        if mode == "ask":
            ans = messagebox.askyesnocancel(
                "Archive transcript?",
                "Save the current meeting transcript into the knowledge base "
                "before closing?\n\n(Yes = archive, No = discard, Cancel = stay open)"
            )
            if ans is None:
                return False
            if not ans:
                return True
        # mode == "always" or user said yes
        try:
            if not self.meeting_id:
                self.meeting_id = f"Meeting {datetime.now().strftime('%Y-%m-%d %H:%M')}"
            if self.last_indexed_doc_id:
                kb.delete_document(self.last_indexed_doc_id)
            kb.index_transcript(transcript, title=self.meeting_id)
        except Exception as e:
            messagebox.showerror("Archive failed", f"{type(e).__name__}: {e}")
        return True

    def _on_close(self) -> None:
        if not self._maybe_archive_on_close():
            return
        self.running = False
        if self.auto_index_after_id:
            try:
                self.root.after_cancel(self.auto_index_after_id)
            except tk.TclError:
                pass
        if self.whisper is not None:
            try:
                self.whisper.stop()
            except Exception:
                pass
        if hasattr(self, "assist_panel"):
            self.assist_panel.stop()
        self.root.destroy()


class ManageKBWindow(tk.Toplevel):
    """List documents in the knowledge base with delete support."""

    def __init__(self, parent: tk.Misc) -> None:
        super().__init__(parent)
        self.title("Knowledge base")
        self.geometry("720x440")
        self.minsize(500, 300)

        top = ttk.Frame(self, padding=(8, 8, 8, 0))
        top.pack(fill="x")
        self.summary = tk.StringVar(value="")
        ttk.Label(top, textvariable=self.summary).pack(side="left")
        ttk.Button(top, text="Refresh", command=self.refresh).pack(side="right")
        ttk.Button(top, text="Delete selected", command=self.delete_selected).pack(side="right", padx=(0, 6))

        body = ttk.Frame(self, padding=8)
        body.pack(fill="both", expand=True)
        cols = ("source", "chunks", "added_at", "doc_id")
        self.tree = ttk.Treeview(body, columns=cols, show="headings", selectmode="extended")
        for c, w in zip(cols, (320, 70, 160, 130)):
            self.tree.heading(c, text=c.replace("_", " ").title())
            self.tree.column(c, width=w, anchor="w")
        scroll = ttk.Scrollbar(body, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")

        self.refresh()

    def refresh(self) -> None:
        for row in self.tree.get_children():
            self.tree.delete(row)
        docs = kb.list_documents()
        total_chunks = sum(d["chunks"] for d in docs)
        self.summary.set(f"{len(docs)} document(s) · {total_chunks} chunks total")
        for d in docs:
            self.tree.insert("", "end", iid=d["doc_id"],
                             values=(d["source"], d["chunks"], d["added_at"], d["doc_id"]))

    def delete_selected(self) -> None:
        sel = list(self.tree.selection())
        if not sel:
            return
        if not messagebox.askyesno("Delete?", f"Remove {len(sel)} document(s) from the knowledge base?"):
            return
        removed = 0
        for doc_id in sel:
            removed += kb.delete_document(doc_id)
        messagebox.showinfo("Deleted", f"Removed {removed} chunks.")
        self.refresh()


def main() -> None:
    # A frozen build has no interpreter to call, so speech workers are launched by
    # re-running Cue.exe with --cue-worker. Catch that before any UI exists.
    code = worker_ipc.maybe_run_worker()
    if code is not None:
        raise SystemExit(code)
    if not EXTRACT_PS1.exists():
        raise SystemExit(f"Missing required script: {EXTRACT_PS1}")
    root = tk.Tk()
    # Modern Windows 11 / macOS-style theme (Sun Valley), honoring saved preference.
    try:
        import sv_ttk
        theme = cfg_mod.get_config()["ui"].get("theme", "light")
        sv_ttk.set_theme(theme if theme in ("light", "dark") else "light")
    except Exception:
        try:
            ttk.Style().theme_use("vista")
        except tk.TclError:
            pass
    try:
        if ICON_PATH.exists():
            root.iconbitmap(default=str(ICON_PATH))
    except tk.TclError:
        pass
    LiveCapture(root)
    root.mainloop()


if __name__ == "__main__":
    main()
