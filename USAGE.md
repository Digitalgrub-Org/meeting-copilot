# How to use Cue

A friendly, step-by-step guide. No coding needed once it's installed.

---

## What Cue does for you

You're in a meeting. Cue listens to the captions and, on the right side of the window, keeps three things up to date automatically:

- **📋 Brief** — a short running summary so you can catch up at a glance
- **❓ Questions** — smart questions you could ask
- **💬 Chip in** — actual sentences you could *say* out loud to contribute

If you've added your own documents (a spec, a proposal, past notes), Cue uses them so its suggestions reflect *your* knowledge.

---

## What you need first

- **Windows 10 or 11** — Cue is Windows-only.
- **Ollama** (free local AI) — install from <https://ollama.com>, open it once so it sits in your tray, and pull a small model: `ollama pull llama3.2:1b`.
- That's the only requirement. (Claude and OpenAI keys are optional, for higher quality — add them later in Settings.)

## First-time setup (once)

1. **Install Ollama** from <https://ollama.com> and open it once — it lives in your system tray and auto-starts on login.
2. **Get a small model.** Open a terminal and run:
   ```
   ollama pull llama3.2:1b
   ```
   That's a ~1.3 GB model that runs on most laptops. (Cue works on small models on purpose.)
3. **Start Cue** — either:
   - **Packaged app:** double-click **`Cue.exe`** inside the `Cue` folder, or
   - **From source:** `python live_capture.py`

   The window titled **"Cue · by Digitalgrub"** opens.

> If you got the packaged `.exe`, the **System audio (Whisper)** source isn't included — use **Teams desktop** or **Pick a window…** instead. (Whisper is available in the Python version.)

> If the AI ever says it can't connect, click **Tools → Start Ollama engine** in Cue.

---

## Using it in a meeting

### Step 1 — turn on captions in your meeting
- **Teams:** in the meeting, click **More (⋯) → Language and speech → Turn on live captions.**

### Step 2 — pick how Cue should listen
Top-left **Source** dropdown:
- **Teams desktop** — reads Teams' own caption panel. Best if you're on Teams desktop.
- **Pick a window…** — reads text from any window you choose. A second dropdown appears listing your open windows; pick the one showing captions/subtitles/a transcript. Great for caption tools or browser captions. (Won't work if the subtitles are part of a video image rather than real text.)
- **System audio (Whisper)** — transcribes whatever you hear. Use this for **Google Meet, Zoom, or Teams in a browser**.

### Step 3 — start
Click **▶ Start capturing**. A red **● Recording** dot appears, and captions begin filling the left side within a couple of seconds.

### Step 4 — use the right-hand tabs
- **📋 Brief** updates every couple of minutes.
- **❓ Questions** gives you things to ask.
- **💬 Chip in** gives you things to say.
- A small **• dot** on a tab means it has new content since you last looked.
- Each tab has a **↻ Refresh** button if you want an update right now.

### Step 5 — get a full summary anytime
Click **✨ Summarize** for a complete, structured summary of the whole meeting in a pop-up window (with a Copy button).

### Step 6 — stop
Click **■ Stop capturing** when you're done. Cue will offer to save the transcript so future meetings can learn from it.

---

## Add your own documents (highly recommended)

This is what makes Cue's suggestions genuinely useful.

1. **Knowledge Base → Add document…**
2. Pick a PDF, Word (.docx), text, or markdown file.
3. That's it — from now on, the Brief / Questions / Chip-in will pull relevant points from your document when they're relevant to the discussion.

Manage what you've added under **Knowledge Base → Manage**.

---

## Adjusting things (⚙ Tools → Settings)

You rarely need to, but everything's here:

| If you want to… | Go to |
|---|---|
| Use a different / smaller / larger AI model | **LLM** tab |
| Use Claude instead of local Ollama | **LLM** tab → Backend = Claude (+ add key in **API keys**) |
| Make the brief / questions update more or less often | **Cadence** tab |
| Turn captions into text faster | **Cadence** tab → Caption poll |
| Let Cue use your *past* meetings too | **Context** tab → "Include past meeting transcripts" |
| Switch light/dark look | **View → Theme** |

---

## Tips

- **Keep the meeting's caption panel open** while capturing with "Teams desktop" — Cue reads from it.
- **Small model feels slow or basic?** Pull `llama3.1` (bigger) or switch to Claude in Settings.
- **Sensitive meeting?** Stay on the default (Ollama + local search) — nothing leaves your computer.
- **Your own voice isn't in the transcript** when using the Whisper source — that mode hears your speakers, not your mic.

---

## Troubleshooting

| Problem | Fix |
|---|---|
| "Could not reach Ollama" | **Tools → Start Ollama engine**, or open the Ollama app |
| No captions appearing | Make sure live captions are ON in the meeting, and Source matches where the meeting is |
| Suggestions are empty | Wait for ~200 characters of new speech, or click **↻ Refresh** |
| Want it on Zoom/Meet | Set Source = **System audio (Whisper)** |
