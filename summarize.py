"""Claude-powered meeting summarizer for the Teams transcript app.

Exposes:
  - get_api_key()  -> str | None       : env var or saved key file
  - set_api_key(s) -> None              : persist key to local config
  - SummaryWindow(parent, transcript)   : Tkinter popup that streams a summary
"""

from __future__ import annotations

import json
import os
import threading
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, simpledialog, ttk

import anthropic

import config as cfg_mod
import knowledge_base as kb

CONFIG_DIR = cfg_mod.DATA_DIR
CONFIG_PATH = CONFIG_DIR / "config.json"

# Token counting (approximation; cl100k is OpenAI/GPT-4's tokenizer and close enough for Claude)
_tiktoken_enc = None


def _count_tokens(text: str) -> int:
    global _tiktoken_enc
    if not text:
        return 0
    try:
        if _tiktoken_enc is None:
            import tiktoken
            _tiktoken_enc = tiktoken.get_encoding("cl100k_base")
        return len(_tiktoken_enc.encode(text))
    except Exception:
        return len(text) // 4  # fallback heuristic

DEFAULT_PROMPT = """You are reading a transcript captured live from a Microsoft Teams meeting.

Important context about the transcript:
- It was captured from the live Captions panel and may have transcription errors.
- Speaker attribution is imperfect — many lines are labeled with whoever was the active speaker at the time, even if someone else said it.
- The transcript may be incomplete (only captions visible during capture were recorded).

If a "Reference material" section is provided below, those are excerpts from the user's own knowledge base (uploaded docs, past meeting notes, project specs) retrieved as relevant to what's being discussed. Use them to ground your analysis — quote source filenames when you reference a specific doc.

Produce a concise meeting brief with these sections:

## Topics discussed
- 3-7 bullets covering the main topics, in the order they came up

## Key points & decisions
- Specific claims, decisions, or positions taken — quote a short phrase when useful

## Connections to reference material
- Where the discussion intersects (or conflicts with) the reference material. Cite source filenames. Skip this section if no reference material was provided or nothing connects.

## Action items
- Anything that sounds like a commitment or next step. Mark "(unclear who)" if attribution is ambiguous.

## Open questions / unresolved
- Anything that was raised but not concluded

## Suggested questions for you to ask
- 3-5 sharp questions the user should ask if they're called on. Pull from the reference material when possible (e.g. "the spec mentions X — has that been addressed?"). These should help the user contribute meaningfully.

Keep it tight. Don't pad with filler. If a section has nothing real, write "(none)" rather than inventing items.

"""


def _format_kb_section(chunks: list, *, stuffed: bool = False) -> str:
    if not chunks:
        return ""
    label = "Reference material (full KB — stuffed)" if stuffed else "Reference material (retrieved from your knowledge base)"
    parts = [f"\n--- {label} ---\n"]
    for c in chunks:
        kind_tag = "transcript" if getattr(c, "kind", "document") == "transcript" else "doc"
        parts.append(f"\n[{kind_tag}: {c.source} · chunk {c.chunk_index}]\n{c.text}\n")
    return "".join(parts)


def _include_past_transcripts() -> bool:
    return bool(cfg_mod.get_config()["context"].get("include_past_transcripts", False))


def _kb_chunks_for_query(query: str, k: int) -> list:
    # kind=None -> all chunks (docs + transcripts); kind="document" -> docs only.
    kind = None if _include_past_transcripts() else "document"
    try:
        return kb.retrieve(query, k=k, kind=kind)
    except Exception:
        return []


def _all_kb_chunks_as_objects() -> list:
    """Every retrievable chunk in the KB (used by stuff-everything strategy).
    Excludes meeting transcripts unless include_past_transcripts is on."""
    try:
        chunks = kb.list_chunks()
    except Exception:
        return []
    if _include_past_transcripts():
        return chunks
    return [c for c in chunks if getattr(c, "kind", "document") == "document"]


def build_kb_context(query: str, transcript: str) -> tuple[list, str]:
    """Returns (chunks_used, strategy_used).

    The current meeting's live transcript is always passed to the LLM in full by
    the caller — this only governs what *extra* KB context is attached. By default
    that's uploaded documents only (no other meetings' transcripts).

    strategy_used is one of 'stuff', 'rag', 'none'."""
    cfg = cfg_mod.get_config()
    strategy = cfg["context"].get("strategy", "auto")
    budget = int(cfg["context"].get("stuff_budget_tokens", 150000))
    top_k = int(cfg["context"].get("rag_top_k", 6))

    if strategy == "rag":
        return _kb_chunks_for_query(query, top_k), "rag"

    if strategy == "stuff":
        return _all_kb_chunks_as_objects(), "stuff"

    # auto
    all_chunks = _all_kb_chunks_as_objects()
    if not all_chunks:
        return [], "none"
    total_text = "".join(c.text for c in all_chunks)
    total_tokens = _count_tokens(total_text) + _count_tokens(transcript)
    if total_tokens <= budget:
        return all_chunks, "stuff"
    return _kb_chunks_for_query(query, top_k), "rag"


INCREMENTAL_BRIEF_PROMPT = """You are tracking a live Microsoft Teams meeting and maintaining a concise running brief. The transcript was captured from live captions, may contain errors, and speaker labels are imperfect.

Output the brief in markdown with these sections (each gets 2-5 short bullets, or "(none yet)" if empty):

## Topics
## Decisions / key points
## Action items
## Open questions

{prev_section}
{kb_section}
--- NEW TRANSCRIPT (most recent portion) ---
{new_transcript}

Output ONLY the updated brief in markdown. No preamble, no commentary, no closing remarks.
"""


QUESTIONS_PROMPT = """You are helping a meeting participant stay engaged in a live Microsoft Teams meeting. They need sharp questions they can ask if called on, based on what's happening RIGHT NOW.

You are given only the NEW discussion since the last update (plus, if available, the questions you suggested last time). Refresh the list: keep any prior question still worth asking, DROP ones that have now been answered or are stale, and ADD new ones for the new discussion.

Generate 3-5 questions that:
- Reference specific topics or claims from the new discussion below
- Surface tension, unresolved points, or things worth probing
- Bring in relevant context from the reference material when applicable (cite the source)
- Are ONE sentence each — concise, direct, sounds natural spoken aloud

Output format (no preamble, no commentary, just the numbered list):
1. First question?
2. Second question?
3. Third question?

{prev_section}
{kb_section}
--- NEW DISCUSSION (since last update) ---
{new_transcript}
"""


TALKING_POINTS_PROMPT = """You are a meeting copilot for a participant who doesn't want to sit idle — they want to contribute and sound engaged. Based on the live discussion (and the reference material, which is THEIR own documents and expertise), suggest specific things THEY could say right now to add value.

You are given only the NEW discussion since the last update, plus the suggestions you gave last time. Refresh the list: keep any still worth saying, DROP ones already said or now stale, ADD new ones for the new discussion.

Each suggestion is a ready-to-say line — first person, natural spoken English, 1-2 sentences — that the participant could say out loud. Ground it in the reference material when relevant (mention the source briefly). After each line, add a short italic cue in parentheses explaining why it lands.

Output 2-4 suggestions as a markdown numbered list, nothing else:
1. "The actual sentence they can say." *(why this lands)*
2. "Another sentence." *(why)*

No preamble, no commentary, no closing remarks.

{prev_section}
{kb_section}
--- NEW DISCUSSION (since last update) ---
{new_transcript}
"""


def generate_talking_points(
    new_transcript: str,
    *,
    prev_points: str | None = None,
    backend: str = "claude",
    ollama_model: str | None = None,
) -> str:
    """Suggest 2-4 things the user could SAY right now. Incremental like the brief."""
    if not new_transcript.strip() and not prev_points:
        return ""
    cfg = cfg_mod.get_config()
    tail = int(cfg["context"].get("rag_query_tail_chars", 4000))
    query = new_transcript[-tail:] or (prev_points or "")
    chunks, strategy = build_kb_context(query, new_transcript)
    prev_section = (
        f"\n--- SUGGESTIONS FROM LAST UPDATE (refresh; drop any already said) ---\n{prev_points}\n"
        if prev_points else ""
    )
    prompt = TALKING_POINTS_PROMPT.format(
        prev_section=prev_section,
        kb_section=_format_kb_section(chunks, stuffed=(strategy == "stuff")),
        new_transcript=new_transcript or "(no new discussion since last update)",
    )
    return _call_llm(prompt, backend=backend, ollama_model=ollama_model, max_tokens=1500)


def _call_claude_blocking(api_key: str, prompt: str, max_tokens: int = 4000) -> str:
    cfg = cfg_mod.get_config()
    model = cfg["llm"].get("claude_model", "claude-opus-4-7")
    effort = cfg["llm"].get("claude_effort", "medium")
    client = anthropic.Anthropic(api_key=api_key)
    resp = client.messages.create(
        model=model,
        max_tokens=max_tokens,
        thinking={"type": "adaptive"},
        output_config={"effort": effort},
        messages=[{"role": "user", "content": prompt}],
    )
    parts = [b.text for b in resp.content if b.type == "text"]
    return "".join(parts).strip()


def _call_ollama_blocking(model: str, prompt: str) -> str:
    import ollama
    client = ollama.Client(host="http://localhost:11434")
    resp = client.chat(
        model=model,
        messages=[{"role": "user", "content": prompt}],
        options={"temperature": 0.3, "num_ctx": 16384},
    )
    msg = getattr(resp, "message", None)
    return (getattr(msg, "content", "") or "").strip() if msg else ""


COMPACT_DIRECTIVE = (
    "You are a small local model. Follow the requested format EXACTLY. Be concise. "
    "Output ONLY what is asked — no preamble, no explanation, no extra commentary.\n\n"
)


def _call_llm(prompt: str, *, backend: str, ollama_model: str | None, max_tokens: int = 4000) -> str:
    if backend == "ollama":
        if not ollama_model:
            raise RuntimeError("Ollama backend requires a model name")
        # Small models follow long structured prompts poorly — prepend a tight directive.
        if _is_lightweight_model(ollama_model):
            prompt = COMPACT_DIRECTIVE + prompt
        return _call_ollama_blocking(ollama_model, prompt)
    key = get_api_key()
    if not key:
        raise RuntimeError("No Anthropic API key configured")
    return _call_claude_blocking(key, prompt, max_tokens=max_tokens)


def generate_brief(
    new_transcript: str,
    *,
    prev_brief: str | None = None,
    backend: str = "claude",
    ollama_model: str | None = None,
) -> str:
    """Generate or incrementally update a running meeting brief."""
    if not new_transcript.strip() and not prev_brief:
        return ""
    cfg = cfg_mod.get_config()
    tail = int(cfg["context"].get("rag_query_tail_chars", 4000))
    query_for_kb = new_transcript[-tail:] or (prev_brief or "")
    chunks, strategy = build_kb_context(query_for_kb, new_transcript)
    prev_section = (
        f"\n--- PREVIOUS BRIEF (update this with the new transcript) ---\n{prev_brief}\n"
        if prev_brief else ""
    )
    prompt = INCREMENTAL_BRIEF_PROMPT.format(
        prev_section=prev_section,
        kb_section=_format_kb_section(chunks, stuffed=(strategy == "stuff")),
        new_transcript=new_transcript or "(no new captions since last update)",
    )
    return _call_llm(prompt, backend=backend, ollama_model=ollama_model, max_tokens=3000)


def generate_questions(
    new_transcript: str,
    *,
    prev_questions: str | None = None,
    backend: str = "claude",
    ollama_model: str | None = None,
) -> str:
    """Generate 3-5 questions the user could ask right now.

    Incremental: pass only the NEW transcript since the last call plus the
    previously-suggested questions — not the whole meeting transcript."""
    if not new_transcript.strip() and not prev_questions:
        return ""
    cfg = cfg_mod.get_config()
    tail = int(cfg["context"].get("rag_query_tail_chars", 4000))
    query = new_transcript[-tail:] or (prev_questions or "")
    chunks, strategy = build_kb_context(query, new_transcript)
    prev_section = (
        f"\n--- QUESTIONS FROM LAST UPDATE (refresh; drop any now answered) ---\n{prev_questions}\n"
        if prev_questions else ""
    )
    prompt = QUESTIONS_PROMPT.format(
        prev_section=prev_section,
        kb_section=_format_kb_section(chunks, stuffed=(strategy == "stuff")),
        new_transcript=new_transcript or "(no new discussion since last update)",
    )
    return _call_llm(prompt, backend=backend, ollama_model=ollama_model, max_tokens=1500)


def _build_prompt(transcript: str, focus_note: str) -> tuple[str, list]:
    """Returns (prompt, kb_chunks)."""
    cfg = cfg_mod.get_config()
    tail = int(cfg["context"].get("rag_query_tail_chars", 4000))
    query = transcript[-tail:] if len(transcript) > tail else transcript
    chunks, strategy = build_kb_context(query, transcript)

    parts: list[str] = [DEFAULT_PROMPT]
    if focus_note.strip():
        parts.append(f"[Focus / context from user]: {focus_note.strip()}\n\n")
    parts.append(_format_kb_section(chunks, stuffed=(strategy == "stuff")))
    parts.append("\n--- TRANSCRIPT ---\n")
    parts.append(transcript)
    return "".join(parts), chunks


def get_api_key() -> str | None:
    """Anthropic API key — delegates to config module."""
    return cfg_mod.get_anthropic_key()


def set_api_key(key: str) -> None:
    """Save Anthropic API key — delegates to config module."""
    cfg_mod.set_anthropic_key(key)


def prompt_for_api_key(parent: tk.Misc) -> str | None:
    key = simpledialog.askstring(
        "Anthropic API key needed",
        "Paste your Anthropic API key (starts with sk-ant-…).\n"
        "It will be saved locally at ~/.meeting_workflow/config.json.",
        parent=parent,
        show="*",
    )
    if not key:
        return None
    key = key.strip()
    if not key.startswith("sk-ant-"):
        messagebox.showerror("Invalid key", "Key should start with 'sk-ant-'.")
        return None
    set_api_key(key)
    return key


def list_ollama_models() -> list[str]:
    try:
        import ollama
        resp = ollama.list()
        names = []
        for m in getattr(resp, "models", []) or []:
            n = getattr(m, "model", None) or getattr(m, "name", None)
            if n:
                names.append(n)
        return names
    except Exception:
        return []


# Substrings that mark a model as lightweight (small param count / quantized small).
LIGHTWEIGHT_MARKERS = (
    "phi", "tinyllama", "tiny", "gemma2:2b", "gemma:2b",
    ":0.5b", ":1b", ":1.5b", ":2b", ":3b", "qwen2.5:0.5b", "qwen2.5:1.5b",
    "llama3.2:1b", "llama3.2:3b", "smollm", "moondream", "orca-mini",
)

# Preference order when the user wants the SMALLEST capable model (the default,
# to honor "should work with a minimal model"). Smallest first.
SMALL_FIRST = [
    "qwen2.5:0.5b", "qwen2.5:1.5b", "gemma2:2b", "llama3.2:1b", "llama3.2:3b",
    "phi3", "phi", "tinyllama", "llama3.2", "qwen2.5", "gemma2", "mistral",
    "llama3.1", "llama3", "llama2",
]

# Preference order when the user wants the most capable model. Largest first.
LARGE_FIRST = ["llama3.3", "llama3.2", "llama3.1", "qwen", "mistral", "gemma", "llama3", "phi", "llama2"]


def _is_lightweight_model(model: str | None) -> bool:
    if not model:
        return False
    m = model.lower()
    return any(marker in m for marker in LIGHTWEIGHT_MARKERS)


def pick_default_ollama_model(models: list[str]) -> str | None:
    """Pick a default model. Honors config llm.prefer_small_model (default True):
    when True, prefer the smallest capable model so Cue works on minimal hardware."""
    if not models:
        return None
    prefer_small = bool(cfg_mod.get_config()["llm"].get("prefer_small_model", True))
    order = SMALL_FIRST if prefer_small else LARGE_FIRST
    for pref in order:
        for m in models:
            if m.lower().startswith(pref):
                return m
    # Nothing matched the preference list — if preferring small, return the
    # model with the smallest-looking tag; else just the first.
    if prefer_small:
        lightweight = [m for m in models if _is_lightweight_model(m)]
        if lightweight:
            return lightweight[0]
    return models[0]


class SummaryWindow(tk.Toplevel):
    """Popup that streams a summary of the transcript.

    backend: 'claude' (default) uses Anthropic API; 'ollama' uses local Ollama.
    """

    def __init__(
        self,
        parent: tk.Misc,
        transcript: str,
        *,
        focus_note: str = "",
        backend: str = "claude",
        ollama_model: str | None = None,
    ) -> None:
        super().__init__(parent)
        self.backend = backend
        self.ollama_model = ollama_model
        label = "Claude (claude-opus-4-7)" if backend == "claude" else f"Ollama ({ollama_model})"
        self.title(f"Meeting summary — {label}")
        self.geometry("900x700")
        self.minsize(600, 400)

        self.transcript = transcript
        self.focus_note = focus_note
        self._stop_flag = threading.Event()

        top = ttk.Frame(self, padding=(8, 8, 8, 0))
        top.pack(fill="x")
        ttk.Label(top, text=f"Summary streaming from {label}…").pack(side="left")
        self.status = tk.StringVar(value="Connecting…")
        ttk.Label(top, textvariable=self.status, foreground="#666").pack(side="right")

        body = ttk.Frame(self, padding=8)
        body.pack(fill="both", expand=True)
        self.text = tk.Text(body, wrap="word", font=("Segoe UI", 11), undo=True)
        scroll = ttk.Scrollbar(body, orient="vertical", command=self.text.yview)
        self.text.configure(yscrollcommand=scroll.set)
        self.text.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")

        bottom = ttk.Frame(self, padding=(8, 0, 8, 8))
        bottom.pack(fill="x")
        ttk.Button(bottom, text="Copy", command=self.copy).pack(side="left")
        ttk.Button(bottom, text="Stop", command=self.stop).pack(side="left", padx=(6, 0))
        ttk.Button(bottom, text="Close", command=self.destroy).pack(side="right")

        self.protocol("WM_DELETE_WINDOW", self._on_close)

        threading.Thread(target=self._stream, daemon=True).start()

    def _append(self, text: str) -> None:
        self.text.insert("end", text)
        self.text.see("end")

    def _set_status(self, msg: str) -> None:
        self.status.set(msg)

    def _stream(self) -> None:
        prompt, chunks = _build_prompt(self.transcript, self.focus_note)
        if chunks:
            sources = sorted({c.source for c in chunks})
            note = f"Using {len(chunks)} KB chunks from: {', '.join(sources)}\n\n"
            self.after(0, lambda n=note: self._append(n))

        if self.backend == "ollama":
            self._stream_ollama(prompt)
            return

        api_key = get_api_key()
        if not api_key:
            self.after(0, lambda: self._set_status("No API key"))
            self.after(0, lambda: self._append("\n[ERROR] No Anthropic API key configured.\n"))
            return

        try:
            cfg = cfg_mod.get_config()
            model = cfg["llm"].get("claude_model", "claude-opus-4-7")
            effort = cfg["llm"].get("claude_effort_summary", "high")
            client = anthropic.Anthropic(api_key=api_key)
            self.after(0, lambda: self._set_status(f"Streaming ({model}, effort={effort})…"))
            with client.messages.stream(
                model=model,
                max_tokens=8000,
                thinking={"type": "adaptive"},
                output_config={"effort": effort},
                messages=[{"role": "user", "content": prompt}],
            ) as stream:
                for event in stream:
                    if self._stop_flag.is_set():
                        break
                    if event.type == "content_block_delta" and event.delta.type == "text_delta":
                        chunk = event.delta.text
                        self.after(0, lambda c=chunk: self._append(c))
                if not self._stop_flag.is_set():
                    final = stream.get_final_message()
                    usage = final.usage
                    self.after(0, lambda: self._set_status(
                        f"Done · in {usage.input_tokens:,} / out {usage.output_tokens:,} tokens"
                    ))
                else:
                    self.after(0, lambda: self._set_status("Stopped"))
        except anthropic.AuthenticationError:
            self.after(0, lambda: self._set_status("Auth failed"))
            self.after(0, lambda: self._append(
                "\n[ERROR] Anthropic rejected the API key. Use Settings → Set API key to update it.\n"
            ))
        except anthropic.RateLimitError:
            self.after(0, lambda: self._set_status("Rate limited"))
            self.after(0, lambda: self._append("\n[ERROR] Rate limited. Wait a moment and retry.\n"))
        except anthropic.APIConnectionError as e:
            msg = f"\n[ERROR] Connection error: {e}\n"
            self.after(0, lambda: self._set_status("Network error"))
            self.after(0, lambda m=msg: self._append(m))
        except anthropic.APIStatusError as e:
            status = e.status_code
            msg = f"\n[ERROR] API error {status}: {getattr(e, 'message', str(e))}\n"
            try:
                msg += f"  Body: {e.response.text[:1000]}\n"
            except Exception:
                pass
            self.after(0, lambda s=status: self._set_status(f"API error {s}"))
            self.after(0, lambda m=msg: self._append(m))
        except Exception as e:
            msg = f"\n[ERROR] {type(e).__name__}: {e}\n"
            self.after(0, lambda: self._set_status("Error"))
            self.after(0, lambda m=msg: self._append(m))

    def _stream_ollama(self, prompt: str) -> None:
        if not self.ollama_model:
            self.after(0, lambda: self._set_status("No model"))
            self.after(0, lambda: self._append("\n[ERROR] No Ollama model selected.\n"))
            return
        try:
            import ollama
        except ImportError:
            self.after(0, lambda: self._set_status("Missing dep"))
            self.after(0, lambda: self._append(
                "\n[ERROR] Ollama python package missing. Run: pip install ollama\n"
            ))
            return

        try:
            self.after(0, lambda: self._set_status(f"Streaming from {self.ollama_model}…"))
            client = ollama.Client(host="http://localhost:11434")
            stream = client.chat(
                model=self.ollama_model,
                messages=[{"role": "user", "content": prompt}],
                stream=True,
                options={"temperature": 0.3, "num_ctx": 16384},
            )
            total_in = total_out = 0
            for chunk in stream:
                if self._stop_flag.is_set():
                    break
                text = ""
                msg = getattr(chunk, "message", None)
                if msg is not None:
                    text = getattr(msg, "content", "") or ""
                if text:
                    self.after(0, lambda c=text: self._append(c))
                done = getattr(chunk, "done", False)
                if done:
                    total_in = getattr(chunk, "prompt_eval_count", 0) or 0
                    total_out = getattr(chunk, "eval_count", 0) or 0
            if self._stop_flag.is_set():
                self.after(0, lambda: self._set_status("Stopped"))
            else:
                self.after(0, lambda: self._set_status(
                    f"Done · in {total_in:,} / out {total_out:,} tokens (local)"
                ))
        except Exception as e:
            text = f"{type(e).__name__}: {e}".lower()
            is_conn = isinstance(e, ConnectionError) or any(
                kw in text for kw in ("connection", "connect", "refused", "max retries", "10061")
            )
            if is_conn:
                self.after(0, lambda: self._set_status("Ollama not running"))
                self.after(0, lambda: self._append(
                    "\n[ERROR] Could not reach Ollama at http://localhost:11434.\n"
                ))
                self.after(0, self._offer_start_ollama)
            else:
                msg = f"\n[ERROR] {type(e).__name__}: {e}\n"
                self.after(0, lambda: self._set_status("Ollama error"))
                self.after(0, lambda m=msg: self._append(m))

    def _offer_start_ollama(self) -> None:
        from tkinter import messagebox
        import ollama_util
        if not messagebox.askyesno(
            "Ollama not running",
            "The local Ollama engine isn't running.\n\nStart it now and retry?",
            parent=self,
        ):
            return
        self._set_status("Starting Ollama…")

        def work() -> None:
            ok = ollama_util.launch_ollama()
            def done() -> None:
                if ok:
                    self._set_status("Ollama started — retrying…")
                    self.text.delete("1.0", "end")
                    self._stop_flag.clear()
                    threading.Thread(target=self._stream, daemon=True).start()
                else:
                    self._set_status("Could not start Ollama.")
                    self._append(
                        "\n[ERROR] Ollama isn't installed or isn't on PATH.\n"
                        "Install from https://ollama.com, then: ollama pull llama3.1\n"
                    )
            self.after(0, done)

        threading.Thread(target=work, daemon=True).start()

    def copy(self) -> None:
        txt = self.text.get("1.0", "end-1c").strip()
        if not txt:
            return
        self.clipboard_clear()
        self.clipboard_append(txt)
        self.update()
        self._set_status("Copied to clipboard")

    def stop(self) -> None:
        self._stop_flag.set()

    def _on_close(self) -> None:
        self._stop_flag.set()
        self.destroy()
