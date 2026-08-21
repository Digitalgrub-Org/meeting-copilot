# Test fixtures

## `sample_en.opus`

A ~20 second English clip, encoded the way WhatsApp encodes voice notes: Opus in Ogg,
mono, 16 kHz, 16 kbps.

**It is synthesised speech, not a recording of a person.** Generated with the Windows
built-in speech synthesiser, so there is no voice to consent to and nothing personal in
it. Please keep it that way: never commit a real recording to this repository, and do
not attach one to an issue.

To regenerate it, or to make a longer or noisier variant:

```powershell
Add-Type -AssemblyName System.Speech
$s = New-Object System.Speech.Synthesis.SpeechSynthesizer
$s.SetOutputToWaveFile("sample.wav")
$s.Speak("Hey, quick update on the meeting workflow project. I finished the knowledge base indexing and the retrieval is working well now. Can you review the pull request before Friday? Also, the client asked whether we can ship the installer next week. Let me know what you think. Thanks.")
$s.Dispose()
ffmpeg -y -i sample.wav -c:a libopus -b:a 16k -ac 1 -ar 16000 sample_en.opus
```

`tests/test_transcribe.py` asserts on a handful of words from that script rather than
the full text, because exact wording varies between models. If you change the script,
update `EXPECTED_WORDS`.

To simulate a poor-quality recording, mix in noise and drop the bitrate:

```powershell
ffmpeg -y -i sample.wav -f lavfi -i "anoisesrc=color=brown:amplitude=0.035" `
  -filter_complex "[0:a]volume=1.0[a];[1:a]volume=1.0[n];[a][n]amix=inputs=2:duration=first" `
  -c:a libopus -b:a 12k -ac 1 -ar 16000 noisy.opus
```
