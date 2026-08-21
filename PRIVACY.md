# Cue — Privacy Policy

**Effective date:** 21 August 2026
**Product:** Cue, a live meeting copilot
**Publisher:** Digitalgrub

Cue is a desktop application that runs on your own computer. This policy describes
what it does with your data. It is written to be checked against the source code,
which is public and MIT licensed.

---

## The short version

Cue collects nothing. There is no telemetry, no analytics, no crash reporting, and
no account. Digitalgrub operates no servers for Cue and receives no data from it.

Everything Cue captures stays in a folder on your computer, **unless you choose to
turn on a cloud AI provider**. If you do, the meeting text is sent to that provider
so it can answer. That choice is off by default and described below.

---

## What Cue processes

Cue exists to read what is being said in a meeting, so it necessarily handles the
contents of your meetings, which may include other people's speech.

| Data | How it is obtained | Where it goes |
|---|---|---|
| Meeting captions | Read from the Teams desktop caption panel, or another window you pick, using Windows UI Automation | Your computer |
| Meeting audio | Captured from your system audio output (what you hear) when you select the Whisper source | Transcribed on your computer, never stored as audio |
| Audio and video files | Only files you explicitly choose in **Transcribe a file** | Transcribed on your computer |
| Documents you add | Only files you explicitly add to the knowledge base | Your computer |
| Your settings and API keys | Entered by you | Your computer |

Cue does not capture your microphone unless you select the microphone source. It
does not take screenshots, read your files unprompted, or scan other applications
beyond the single window you select for caption capture.

## Where it is stored

Everything lives in Cue's data directory, which is `%USERPROFILE%\.meeting_workflow`
by default, or wherever you point the `CUE_DATA_DIR` environment variable:

- `kb.jsonl` — documents you added and meeting transcripts you archived, as text
- `kb_openai_vecs.npy` — search vectors, only if you enable OpenAI embeddings
- `config.json` — your settings, including any API keys, written with file mode 0600
- `whisper-models/` — downloaded speech models

Nothing is encrypted at rest beyond the file permissions your operating system
applies. Treat this folder as sensitive, because it contains meeting transcripts.

You can delete indexed content at any time from **Knowledge Base → Manage knowledge
base**, or by deleting the data directory. Uninstalling Cue does not delete it, so
that your data is not destroyed by an update; remove it yourself if you want it gone.

## When data leaves your computer

By default it does not. Cue ships configured for local processing: the language
model is [Ollama](https://ollama.com) running on your machine, retrieval is a local
keyword index, and speech recognition is local Whisper.

Data leaves your computer **only** if you enable one of these yourself in Settings:

- **Claude (Anthropic API).** If you set an Anthropic API key and select the Claude
  backend, the meeting transcript and relevant excerpts from your documents are sent
  to Anthropic to generate the brief, questions and summaries. Anthropic's handling
  of that data is governed by their own privacy policy and terms.
- **OpenAI embeddings.** If you set an OpenAI API key and select the OpenAI
  embeddings backend, the text of your documents and transcripts is sent to OpenAI
  to compute search vectors. OpenAI's own privacy policy applies.
- **Model downloads.** The first time a speech model is used, it is downloaded from
  Hugging Face. This transfers no meeting data; it only fetches the model.

Cue contacts no other network destinations. There is no Digitalgrub endpoint.

## Recording other people, and consent

This is the part that matters most, and it is your responsibility rather than
something software can settle for you.

Cue transcribes what other participants say. In many places, recording or
transcribing a conversation without the consent of the people in it is unlawful.
Rules differ widely: some jurisdictions require only your own consent, others
require everyone's, and workplace and sector rules may be stricter still.

Before you capture a meeting, tell the other participants and get their agreement.
Follow your employer's policy and the rules that apply where you and they are. Cue
gives you no permission you do not already have, and Digitalgrub accepts no
responsibility for how you use it.

## Children

Cue is a workplace productivity tool and is not directed at children.

## Your rights

Because Digitalgrub holds none of your data, there is nothing for us to disclose,
export or erase. You have direct control: the files are on your disk. If you used a
cloud provider, direct any request about data they hold to that provider.

## Changes

Material changes to this policy will be published in this file in the project
repository, with the effective date above updated.

## Contact

Questions about this policy: open an issue in the Cue repository, or use the support
contact on the Cue Microsoft Store listing.
