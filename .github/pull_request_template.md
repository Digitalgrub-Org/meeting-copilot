<!--
Thanks for contributing. Keep this short; the useful part is "how you verified it".
-->

## What this changes

<!-- One or two sentences. Link an issue if there is one. -->

## How you verified it

<!--
For UI work, saying what you clicked is fine. For anything else, the commands you ran.

    python -m pytest tests -m "not slow" -q     # fast suite
    python -m pytest tests -q                   # includes real speech models
-->

## Checklist

- [ ] `python -m pytest tests -m "not slow"` passes
- [ ] If this touches the worker boundary (`worker_ipc`, `audio_transcribe`,
      `whisper_capture`), the full suite passes including `slow`
- [ ] No new module-level import of native code in a parent half — `test_purity.py`
      catches this, and it exists because a violation makes the app vanish rather
      than raise
- [ ] No new runtime dependency, or the PR explains why it is worth it
- [ ] Docs updated if behaviour changed, and `CHANGELOG.md` under Unreleased
