# Security Policy

## Reporting a vulnerability

Please report security issues privately rather than opening a public issue.

Use GitHub's [private vulnerability reporting](https://github.com/Digitalgrub-Org/meeting-copilot/security/advisories/new)
on this repository. If that is unavailable to you, open an issue titled
"Security contact request" with no details, and we will arrange a private channel.

Please include what you need to demonstrate the problem: affected version, steps to
reproduce, and what an attacker gains. We will acknowledge within a week.

Cue is a small project maintained in spare time. There is no bounty programme, and
fixes are best-effort.

## What is in scope

Cue is a local desktop application with no backend, so the interesting surface is
narrow. These are worth reporting:

- **Leakage of captured meeting content** anywhere it should not go: to a network
  destination, to a world-readable file, or into logs.
- **API key exposure.** Keys live in `config.json` at file mode 0600. Anything that
  copies them elsewhere, prints them, or transmits them is a bug.
- **Code execution** through a file Cue ingests: a crafted audio, video, PDF or DOCX
  file that leads to execution rather than a clean error.
- **Command injection** through a filename, window title, or configured interpreter
  path. Cue spawns child processes and runs a PowerShell helper.
- **Path traversal** in transcript or document export.

## What is not a vulnerability

- **Cue transcribes other people.** That is the product. The legal and consent
  obligations are covered in [PRIVACY.md](PRIVACY.md); they are the user's
  responsibility, not a software defect.
- **Meeting text is sent to Anthropic or OpenAI** when the user enables those
  backends and supplies their own key. This is opt-in, off by default, and documented.
- **`config.json` is readable by the account that owns it.** It is a local user file,
  not encrypted at rest. Treat the data directory as sensitive.
- **The knowledge base survives uninstall.** Deliberate, so an update does not destroy
  your transcripts. Delete the data directory yourself.
- **`ctranslate2` crashing with an access violation.** A known upstream dependency bug,
  documented in [CONTRIBUTING.md](CONTRIBUTING.md), and isolated to a child process.
  Pin `ctranslate2==4.4.0`.
- **Anything requiring an attacker to already control the machine.** If they can write
  to your `config.json`, they can already read your files.

## Handling of your data

Cue has no servers and no telemetry, so a report will never involve data we hold.
Please do not send us meeting recordings or transcripts. If you need to share a
reproducing file, synthesise one; the audio fixture in `tests/fixtures/` is generated
speech and is a reasonable model to follow.
