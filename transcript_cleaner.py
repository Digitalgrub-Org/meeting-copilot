"""Teams Transcript Cleaner — paste or load a transcript, get clean copyable text."""

import re
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from pathlib import Path


TIMESTAMP_LINE = re.compile(
    r"^\s*\d{1,2}:\d{2}(?::\d{2})?(?:[.,]\d{1,3})?\s*-->\s*"
    r"\d{1,2}:\d{2}(?::\d{2})?(?:[.,]\d{1,3})?.*$"
)
CUE_ID_LINE = re.compile(r"^\s*([0-9]+|[0-9a-fA-F-]{8,})\s*$")
SPEAKER_TAG = re.compile(r"<v\s+([^>]+)>(.*?)</v>", re.DOTALL)
ANY_TAG = re.compile(r"<[^>]+>")
TEAMS_PASTE_SPEAKER = re.compile(
    r"^([A-Z][\w'’\-\.]*(?:\s+[A-Z][\w'’\-\.]*)*)\s+\d{1,2}:\d{2}(?::\d{2})?\s*$"
)
INLINE_TIMESTAMP = re.compile(r"\[\d{1,2}:\d{2}(?::\d{2})?\]")


def clean_transcript(raw: str) -> str:
    if not raw.strip():
        return ""

    text = raw.replace("\r\n", "\n").replace("\r", "\n")
    text = SPEAKER_TAG.sub(lambda m: f"{m.group(1).strip()}: {m.group(2).strip()}", text)
    text = ANY_TAG.sub("", text)

    lines = text.split("\n")
    cleaned: list[str] = []
    skip_block = False
    pending_speaker: str | None = None

    for line in lines:
        stripped = line.strip()

        if not stripped:
            if pending_speaker is not None:
                continue
            skip_block = False
            if cleaned and cleaned[-1] != "":
                cleaned.append("")
            continue

        if stripped.upper().startswith("WEBVTT"):
            continue
        if stripped.startswith("NOTE"):
            skip_block = True
            continue
        if skip_block:
            continue

        if TIMESTAMP_LINE.match(stripped):
            continue
        if CUE_ID_LINE.match(stripped):
            continue

        stripped = INLINE_TIMESTAMP.sub("", stripped).strip()
        if not stripped:
            continue

        m = TEAMS_PASTE_SPEAKER.match(stripped)
        if m:
            pending_speaker = m.group(1).strip()
            continue

        if pending_speaker is not None:
            if cleaned and cleaned[-1] != "":
                cleaned.append("")
            cleaned.append(f"{pending_speaker}: {stripped}")
            pending_speaker = None
            continue

        cleaned.append(stripped)

    out: list[str] = []
    blank = False
    for line in cleaned:
        if line == "":
            if not blank and out:
                out.append("")
            blank = True
        else:
            out.append(line)
            blank = False

    while out and out[-1] == "":
        out.pop()

    return "\n".join(out)


def load_file(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix == ".docx":
        try:
            from docx import Document
        except ImportError as e:
            raise RuntimeError(
                "Reading .docx needs the 'python-docx' package.\n"
                "Install it with:  pip install python-docx"
            ) from e
        doc = Document(str(path))
        return "\n".join(p.text for p in doc.paragraphs)

    for encoding in ("utf-8-sig", "utf-8", "utf-16", "cp1252", "latin-1"):
        try:
            return path.read_text(encoding=encoding)
        except UnicodeDecodeError:
            continue
    return path.read_text(encoding="utf-8", errors="replace")


class App:
    def __init__(self, root: tk.Tk):
        self.root = root
        root.title("Teams Transcript Cleaner")
        root.geometry("1100x720")
        root.minsize(700, 500)

        toolbar = ttk.Frame(root, padding=(8, 8, 8, 0))
        toolbar.pack(fill="x")

        ttk.Button(toolbar, text="Open file…", command=self.open_file).pack(side="left")
        ttk.Button(toolbar, text="Paste from clipboard", command=self.paste).pack(side="left", padx=(6, 0))
        ttk.Button(toolbar, text="Clean →", command=self.clean).pack(side="left", padx=(6, 0))
        ttk.Separator(toolbar, orient="vertical").pack(side="left", fill="y", padx=8)
        ttk.Button(toolbar, text="Copy result", command=self.copy_result).pack(side="left")
        ttk.Button(toolbar, text="Save as…", command=self.save_result).pack(side="left", padx=(6, 0))
        ttk.Separator(toolbar, orient="vertical").pack(side="left", fill="y", padx=8)
        ttk.Button(toolbar, text="Clear", command=self.clear_all).pack(side="left")

        paned = ttk.PanedWindow(root, orient="horizontal")
        paned.pack(fill="both", expand=True, padx=8, pady=8)

        left = ttk.Frame(paned)
        ttk.Label(left, text="Raw transcript (paste here or use Open file)").pack(anchor="w")
        self.input_text = tk.Text(left, wrap="word", undo=True, font=("Consolas", 10))
        in_scroll = ttk.Scrollbar(left, orient="vertical", command=self.input_text.yview)
        self.input_text.configure(yscrollcommand=in_scroll.set)
        self.input_text.pack(side="left", fill="both", expand=True)
        in_scroll.pack(side="right", fill="y")
        paned.add(left, weight=1)

        right = ttk.Frame(paned)
        ttk.Label(right, text="Cleaned text (editable — copy when ready)").pack(anchor="w")
        self.output_text = tk.Text(right, wrap="word", undo=True, font=("Segoe UI", 11))
        out_scroll = ttk.Scrollbar(right, orient="vertical", command=self.output_text.yview)
        self.output_text.configure(yscrollcommand=out_scroll.set)
        self.output_text.pack(side="left", fill="both", expand=True)
        out_scroll.pack(side="right", fill="y")
        paned.add(right, weight=1)

        self.status = tk.StringVar(value="Ready. Paste a transcript or open a .vtt / .txt / .srt / .docx file.")
        ttk.Label(root, textvariable=self.status, anchor="w", relief="sunken", padding=(8, 2)).pack(fill="x", side="bottom")

        root.bind_all("<Control-Return>", lambda e: self.clean())
        root.bind_all("<Control-o>", lambda e: self.open_file())
        root.bind_all("<Control-l>", lambda e: self.clear_all())

    def set_status(self, msg: str) -> None:
        self.status.set(msg)

    def open_file(self) -> None:
        path = filedialog.askopenfilename(
            title="Open transcript",
            filetypes=[
                ("Transcript files", "*.vtt *.txt *.srt *.docx"),
                ("WebVTT", "*.vtt"),
                ("Text", "*.txt"),
                ("SubRip", "*.srt"),
                ("Word", "*.docx"),
                ("All files", "*.*"),
            ],
        )
        if not path:
            return
        try:
            content = load_file(Path(path))
        except Exception as e:
            messagebox.showerror("Could not open file", str(e))
            return
        self.input_text.delete("1.0", "end")
        self.input_text.insert("1.0", content)
        self.set_status(f"Loaded {Path(path).name} ({len(content):,} chars). Press Clean →.")
        self.clean()

    def paste(self) -> None:
        try:
            data = self.root.clipboard_get()
        except tk.TclError:
            messagebox.showinfo("Clipboard empty", "There's nothing on the clipboard to paste.")
            return
        self.input_text.delete("1.0", "end")
        self.input_text.insert("1.0", data)
        self.set_status(f"Pasted {len(data):,} chars from clipboard.")
        self.clean()

    def clean(self) -> None:
        raw = self.input_text.get("1.0", "end-1c")
        result = clean_transcript(raw)
        self.output_text.delete("1.0", "end")
        self.output_text.insert("1.0", result)
        if not result:
            self.set_status("Nothing to clean — input is empty.")
        else:
            lines = result.count("\n") + 1
            self.set_status(f"Cleaned: {len(result):,} chars, {lines:,} lines. Edit if needed, then Copy result.")

    def copy_result(self) -> None:
        text = self.output_text.get("1.0", "end-1c")
        if not text.strip():
            messagebox.showinfo("Nothing to copy", "The cleaned text area is empty.")
            return
        self.root.clipboard_clear()
        self.root.clipboard_append(text)
        self.root.update()
        self.set_status(f"Copied {len(text):,} chars to clipboard.")

    def save_result(self) -> None:
        text = self.output_text.get("1.0", "end-1c")
        if not text.strip():
            messagebox.showinfo("Nothing to save", "The cleaned text area is empty.")
            return
        path = filedialog.asksaveasfilename(
            title="Save cleaned transcript",
            defaultextension=".txt",
            filetypes=[("Text", "*.txt"), ("All files", "*.*")],
        )
        if not path:
            return
        Path(path).write_text(text, encoding="utf-8")
        self.set_status(f"Saved to {path}")

    def clear_all(self) -> None:
        self.input_text.delete("1.0", "end")
        self.output_text.delete("1.0", "end")
        self.set_status("Cleared.")


def main() -> None:
    root = tk.Tk()
    try:
        ttk.Style().theme_use("vista")
    except tk.TclError:
        pass
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
