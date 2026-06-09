# Cue — Technical overview

*by Digitalgrub · MIT*


## Architecture

```
+-----------------------------------------------------------------+
|  live_capture.py  (main Tkinter app, layout + caption polling)  |
|    ├── transcript_cleaner.py   (file-import cleaner, standalone)|
|    ├── extract_teams_transcript.ps1 (PowerShell UIAutomation)   |
|    ├── auto_assist.py     (right-side panel: brief + questions) |
|    ├── summarize.py       (Claude/Ollama calls, RAG vs stuff)   |
|    ├── knowledge_base.py  (TF-IDF + optional OpenAI vectors)    |
|    ├── settings_window.py (config UI)                           |
|    └── config.py          (JSON config + on_change listeners)   |
+-----------------------------------------------------------------+
```

## How each piece works

### 1. Caption capture (Windows UI Automation)

`extract_teams_transcript.ps1` walks the accessibility tree of the Teams desktop window whose title contains "Captions". It emits each visible text element to `teams_extracted_raw.txt`. `live_capture.py` polls this script every 4 sec (configurable), parses speaker-label/caption pairs, dedupes against a normalized-text seen-set, and handles progressive caption revisions (Teams retypes captions as the speaker continues).

### 2. Knowledge base

**Storage:** Plain JSONL at `~/.meeting_workflow/kb.jsonl`. One line per chunk with `{doc_id, source, kind, chunk_index, added_at, text}`. Chunks are ~450 tokens with 50-token overlap, split by paragraph then sentence.

**Retrieval — two backends:**

- **TF-IDF (default)** — scikit-learn `TfidfVectorizer(ngram_range=(1,2), sublinear_tf=True)`. Matrix rebuilt in-memory on first query after any add/delete. Pros: zero setup, zero cost, no API calls. Cons: keyword overlap only, misses synonyms.
- **OpenAI** — `text-embedding-3-small` (1536-dim). Vectors persisted at `~/.meeting_workflow/kb_openai_vecs.npy` (numpy float32 matrix, row-aligned with `kb.jsonl`). New chunks embedded on next retrieve. Pros: real semantic similarity. Cons: needs API key, ~$0.02/1M tokens.

**Why not ChromaDB?** Tried it first. ChromaDB pulls in ONNX Runtime + PyTorch (via sentence-transformers default), which conflicts with Tkinter+OpenMP on this Anaconda environment. Repeatable segfaults at startup. TF-IDF + optional OpenAI sidesteps both native deps.

### 3. Context strategy (RAG vs stuff)

Configured in `config.context.strategy`:
- **`auto`** (default) — counts tokens; if `transcript + all_kb_chunks ≤ stuff_budget_tokens` (default 150k), include the whole KB in the prompt. Else fall back to RAG retrieval. Works well because Claude Opus 4.7 has a 1M context window.
- **`stuff`** — always include everything in the KB. Highest quality, highest cost.
- **`rag`** — always retrieve top-K relevant chunks. Cheapest, scales to huge KBs.

Token counting uses `tiktoken` (cl100k_base) which is close enough for Claude planning.

### 4. LLM calls (Claude + Ollama)

`summarize.py` exposes three entry points — `generate_brief()`, `generate_questions()`, and `SummaryWindow` (streaming popup) — all backend-agnostic:

- **Claude** — Anthropic SDK with adaptive thinking (`thinking={"type": "adaptive"}`) and effort levels from config. Streaming for the Summarize popup, non-streaming for background brief/questions.
- **Ollama** — Local HTTP at `localhost:11434`. Same prompts. Streaming used for the popup.

Briefs are **incremental**: each call sends the previous brief + only new captions since the last update, keeping per-call cost roughly flat regardless of meeting length.

### 5. Background polling (right-side panel)

`auto_assist.py` runs two independent timer loops via `tk.after()`:
- Brief: every `cadence.brief_interval_sec` (default 120s)
- Questions: every `cadence.questions_interval_sec` (default 180s)

Each tick spawns a worker thread that does the LLM call, then posts the result back to the UI via `root.after(0, ...)`. The panel skips a tick if fewer than `MIN_NEW_CHARS` (200) have been added since the last call — avoids burning tokens on no-op updates.

### 6. Auto-archive

Every `cadence.auto_index_interval_sec` (default 600s = 10 min), the current transcript is re-indexed into the KB with `kind="transcript"` and a stable `meeting_id` based on session start time. Each re-index deletes the prior version of the same meeting first, so the KB grows by one entry per meeting (not one per snapshot). On app close, `config.archive.on_close` controls behavior (ask / always / never).

### 7. Settings

`config.py` reads/writes `~/.meeting_workflow/config.json` (chmod 600). Components register via `cfg_mod.on_change(callback)` to react to changes without restart. The `SettingsWindow` is a notebook with 6 tabs (LLM / Embeddings / Context / Cadence / API keys / Archive).

## Future platform support

| Platform | Approach | Effort |
|---|---|---|
| Teams web | DOM scraping via browser automation (Selenium / Playwright / [Claude in Chrome](https://claude.com/chrome)) | Medium |
| Google Meet | DOM scraping (browser) — captions are in a transient overlay div | Medium |
| Zoom desktop | UI Automation, different accessibility tree from Teams | Medium |
| Zoom web | DOM scraping (browser) | Medium |
| **Universal fallback** | System audio capture + local Whisper STT (faster-whisper or whisper.cpp). Works for any app. Heavier setup. | Higher |

The universal Whisper-based path is the most robust long-term: it doesn't care about UI changes in Teams/Meet/Zoom, works on every platform, and gives you a real audio-derived transcript instead of relying on the host's caption feature.

## Open source notes

- License: MIT
- No telemetry, no analytics
- All data lives locally in `~/.meeting_workflow/`
- API keys stored with mode 0600
- Built with Tkinter (stdlib) for portability — no Electron, no extra runtime
