# Cue

**Your cue to speak.** · *by Digitalgrub*

Cue is a Windows desktop copilot for live meetings. It captures the meeting's captions and, in a side panel, continuously gives you:

- 📋 **a running brief** of what's been discussed,
- ❓ **questions you could ask**, and
- 💬 **what you could say** — ready-to-speak lines so you're never sitting idle.

Everything is grounded in **your own uploaded documents** (specs, notes, past meetings). It runs **fully local and free by default** (Ollama + keyword search), and works on a **minimal model** out of the box. Optional paid upgrades (Claude API, OpenAI embeddings) raise quality.

> New here? Read **[USAGE.md](USAGE.md)** for a step-by-step, non-technical walkthrough.

---

## ⚠️ Before you start — what you need & what to expect

**Platform: Windows 10/11 only.** Cue's capture relies on Windows UI Automation; it does **not** run on macOS or Linux.

**Required**
- **Windows 10 or 11** (64-bit)
- **[Ollama](https://ollama.com)** installed and running, with at least one model pulled — this is the free local AI engine and is **not bundled** (install it separately, once). A **small model** like `llama3.2:1b` is enough; Cue defaults to the smallest one you have.
- ~4 GB RAM free for a small model (more for larger models)

**Optional**
- **Anthropic API key** — to use Claude instead of local Ollama (higher quality, ~$0.50–2/meeting)
- **OpenAI API key** — for semantic document search instead of keyword search
- **Python 3.10+** — only if you run from source or want the **Whisper** audio-capture source (see below)

**What you can expect**
- Live capture from **Teams desktop**, **any window showing real caption/subtitle text** (incl. browser/YouTube CC), and — in the Python version — **system audio via Whisper**.
- A side panel that auto-updates a **brief**, **questions to ask**, and **what you could say**.
- On a small local model, output is quick but basic; it gets sharper with a larger model or Claude.
- **All your data stays on your machine.** No telemetry.

**The packaged `.exe` (no Python needed)** includes everything **except the Whisper system-audio source** (that stack is heavy and native — use the Python version for Whisper). Teams capture and "Pick a window" both work in the `.exe`.

---

## Option A — Install with the setup (no Python) — recommended

1. Install **[Ollama](https://ollama.com)** and pull a small model once: `ollama pull llama3.2:1b`. Open the Ollama app so it runs in the tray.
2. Run **`CueSetup.exe`** and follow the prompts. It installs Cue, adds Start-menu / desktop shortcuts, and registers an uninstaller. No admin needed (per-user install).

That's it — no Python, no `pip`. This build includes Teams capture, "Pick a window" capture, the knowledge base, Ollama/Claude, and the assist panel. *(The Whisper system-audio source is only in the Python version below.)*

> **Portable alternative:** instead of the installer, you can run the app directly from the `dist\Cue` folder by double-clicking `Cue.exe` (zip and share the whole folder — the `.exe` alone won't run).

## Option B — Run from source (Python) — full features incl. Whisper

**1. Python 3.10+** — check with `python --version`.

**2. Clone + install dependencies**
```bash
git clone <your-repo-url>
cd meeting_workflow
pip install -r requirements.txt
```

**3. Install Ollama (the free local AI engine)** — download from <https://ollama.com>, then pull a **small** model so Cue runs on minimal hardware:
```bash
ollama pull llama3.2:1b        # ~1.3 GB, fast, runs on most laptops
# or even smaller:
ollama pull qwen2.5:1.5b       # ~1 GB
# or, if you have the RAM and want higher quality:
ollama pull llama3.1           # ~4.7 GB
```
Open the Ollama app once so it stays running in the tray (auto-starts on login). Cue can also start it for you via **Tools → Start Ollama engine**.

**4. Run Cue**
```bash
python live_capture.py
```

---

## Use it (30-second version)

1. In your meeting (Teams), turn on **live captions**.
2. In Cue: pick a **Source** → click **▶ Start capturing**.
3. Watch the three tabs on the right fill in: **Brief**, **Questions**, **Chip in**.
4. Hit **✨ Summarize** anytime for a full summary.
5. Add reference docs with **Knowledge Base → Add document** so the AI draws on your material.

Full walkthrough with what each control does: **[USAGE.md](USAGE.md)**.

---

## Capture sources

| Source | How it works | Notes |
|---|---|---|
| **Teams desktop** | Reads the live Captions panel via Windows UI Automation | Includes everyone (you too). Turn on captions in Teams first. |
| **Pick a window…** | Reads text from **any window you choose** via UI Automation | Generalizes Teams capture — point it at any app showing a captions/subtitle/transcript pane (browser captions, captioning tools, etc.). A dropdown lists your open windows. Works when the app exposes real text (most do); subtitles *painted as pixels* — burned-in video subs, GPU overlays — aren't readable this way (OCR mode is planned). |
| **System audio (Whisper)** | Transcribes whatever plays through your speakers, locally | Works for **Teams / Meet / Zoom / anything**. Captures others' voices (not your own mic). First run downloads a ~150 MB model. |

---

## AI engines & backends (all in ⚙ Settings)

| Use case | LLM | Embeddings | Cost |
|---|---|---|---|
| **Default — fully local, minimal** | Ollama (small model) | TF-IDF | $0 |
| Higher-quality output | Claude API | TF-IDF | ~$0.50–2 / meeting |
| Semantic document search | Ollama | OpenAI embeddings | <$0.01 / indexing |
| Best quality | Claude API | OpenAI embeddings | ~$0.50–2 / meeting |

---

## Minimal-model setup

Cue is designed to work on a **small local model**:

- By default it **auto-picks the smallest capable Ollama model** you have installed (toggle in **Settings → LLM → "Prefer a small / lightweight model"**).
- When a lightweight model is detected (e.g. `llama3.2:1b`, `qwen2.5:1.5b`, `phi3`, `gemma2:2b`), Cue automatically uses **tighter prompts** so the smaller model follows the format reliably.
- Recommended minimal models: **`llama3.2:1b`** (best balance), `qwen2.5:1.5b`, `gemma2:2b`. For very low RAM, `qwen2.5:0.5b`.
- Expect shorter, simpler output than a large model — good enough for live briefs/questions/talking-points. Bump to `llama3.1` or Claude when you want more depth.

Whisper similarly defaults to the small `base.en` model; you can drop to `tiny.en` for an even lighter footprint.

---

## Settings (⚙ Tools → Settings)

A 6-tab window — almost everything is configurable without touching code:

- **LLM** — backend (Ollama / Claude / Auto), models, effort levels, prefer-small-model
- **Embeddings** — TF-IDF (local) vs OpenAI (semantic), model
- **Context** — stuff-everything vs RAG retrieval, token budget, include-past-meetings
- **Cadence** — how often the brief / questions / chip-in / capture refresh
- **API keys** — Anthropic + OpenAI (stored locally, mode 0600)
- **Archive** — what to do with the transcript when you close

Theme (light/dark) is under **View → Theme**.

---

## Your data stays local

Everything lives under `~/.meeting_workflow/`:
- `kb.jsonl` — your uploaded docs + archived meeting transcripts (chunked text)
- `kb_openai_vecs.npy` — semantic vectors (only if OpenAI embeddings enabled)
- `config.json` — your settings (file permissions 0600)

No telemetry, no analytics. Manage the knowledge base via **Knowledge Base → Manage**.

---

## Requirements

- Windows 10/11 (Teams-desktop capture uses Windows UI Automation)
- Python 3.10+
- [Ollama](https://ollama.com) with at least one model pulled
- *(Optional)* Anthropic API key — for Claude
- *(Optional)* OpenAI API key — for semantic embeddings

---

## Known limits

- Teams-desktop capture is Windows-only; the **System audio (Whisper)** source covers other platforms.
- The Whisper source captures others' voices, not your own microphone.
- Speaker attribution from captions is approximate.
- Local semantic embeddings (sentence-transformers/Chroma) are disabled on Anaconda+Tk setups due to a native-library crash — Cue uses TF-IDF locally, or OpenAI embeddings if you add a key. See [TECHNICAL.md](TECHNICAL.md).
- For the authoritative full transcript, use Teams' built-in transcript download after the meeting.

## License

MIT — see [LICENSE](LICENSE).
