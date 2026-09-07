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

> The packaged `.exe` now includes speech recognition, so **System audio (Whisper)** and **Transcribe a file** both work without installing Python.

> If the AI ever says it can't connect, click **Tools → Start Ollama engine** in Cue.

---

## Using it in a meeting

### Step 1 — turn on captions in your meeting
- **Teams:** in the meeting, click **More (⋯) → Language and speech → Turn on live captions.**

### Step 2 — pick how Cue should listen
Top-left **Source** dropdown:
- **Teams desktop** — reads Teams' own caption pane. Best if you're on Teams desktop. Turn captions on and leave the pane open; Cue finds Teams itself, you don't need to arrange any windows.
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

## Transcribe a recording you already have

For when there's no live meeting — someone sent you a **WhatsApp voice note**, or you
have a phone recording or a meeting export sitting in a folder.

1. **File → Transcribe a file…** (or press **Ctrl+O**, or click **Transcribe a file…**
   in the top bar).
2. **Browse…** and pick the file. WhatsApp voice notes are `.opus`; also works with
   `.m4a`, `.mp3`, `.wav`, `.amr`, and videos like `.mp4` or `.mov`.
3. Click **▶ Transcribe**.

The first run downloads the speech model (a few hundred MB, once). After that you'll see
a progress bar and the text appearing as it goes. A 5-minute note takes a couple of
minutes on a normal laptop.

When it's finished, pick what to do with it:

| Button | What it does |
|---|---|
| **Copy** | Puts the text on your clipboard |
| **Save as…** | Writes a `.txt`, `.md`, or subtitle file (`.srt` / `.vtt`) |
| **Add to knowledge base** | Stores it so future meetings can draw on it |
| **✨ Summarize** | Gives you the summary instead of the full text |
| **Send to live transcript** | Drops it into the main window so Brief / Questions / Chip-in work on it |

**To get a better transcript:**

- **Type the names.** The **Names / jargon** box is the single biggest improvement. Put
  in the people, products and acronyms you expect — `Contoso, Northwind, Atlas API` — and
  they'll be spelled properly instead of guessed at phonetically.
- **Heavy accent, or noisy recording?** Move **Quality** up to *Accurate (medium)* or
  *Best (large-v3)*. Slower, clearly better.
- **Short or unclear clip?** Set **Language** instead of leaving it on Auto-detect —
  auto-detection can guess wrong on a few seconds of audio.
- **Want to know when something was said?** Tick **Show timestamps**. You can toggle it
  after the fact; it just re-renders.

Nothing is uploaded — the audio is transcribed on your own machine.

---

## Get the transcript of a Teams recording you can't download

Some organisations turn off transcript download in Teams. The transcript is still shown
next to the recording, and Cue can read it off the screen.

1. In the **Teams desktop app**, open the meeting recording.
2. Click the **Transcript** tab on the right so the text is showing.
3. In Cue: **Tools → Import Teams recording transcript…**, then **▶ Start**.
4. **Leave Teams alone until it finishes.** Cue scrolls the transcript pane itself and
   needs the mouse to do it. You'll see `pass 12 · 87 entries · reached 14:20` ticking
   up. A two-hour meeting takes a few minutes.
5. When it's done, **Copy**, **Save as…**, **Add to knowledge base**, **✨ Summarize**,
   or **Send to live transcript**.

If it says it found Teams but no transcript pane, the Transcript tab isn't showing.
Click it and try again.

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
| Change the default quality / language for file transcription | **Transcribe** tab |
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
| "Still opening \<device\>…" then it gives up | Windows can block on an output with no active sound, like an HDMI monitor that's asleep. Set the output you actually listen through as the Windows default, play any sound to wake it, then start again. **Teams desktop** and **Pick a window…** don't use audio at all. |
| "The speech engine crashed… (access violation)" | A known Windows library clash, not your file. Run `pip install "ctranslate2==4.4.0"` and try again. |
| "Speech recognition isn't installed" | Run `pip install faster-whisper "ctranslate2==4.4.0"` |
| Names come out spelled wrong | Put them in the **Names / jargon** box and transcribe again |
| Transcript has no punctuation | Update Cue — older builds passed your name list to the model in a way that suppressed punctuation |
