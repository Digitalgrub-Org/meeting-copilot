"""End-to-end transcription and the file-transcription window.

Marked slow: these load a real speech model, which downloads a few hundred MB the
first time. Run the fast suite with `-m "not slow"`.

Tkinter is invisible to Windows UI Automation, so the UI tests construct real widgets
and call the same methods the buttons call.
"""

from __future__ import annotations

import threading

import pytest

import audio_transcribe as at

pytestmark = pytest.mark.slow

# Words the fixture clip actually contains. Kept loose: exact wording varies with the
# model, and asserting a full string would make this a transcription-accuracy test.
EXPECTED_WORDS = ("update", "knowledge", "retrieval", "friday", "installer")


def transcribe(path, model_root, **kwargs) -> at.Result:
    """Run a real transcription through the worker and wait for it."""
    done = threading.Event()
    out: dict = {}
    job = at.TranscribeJob(
        path, model_size="small", download_root=model_root,
        on_done=lambda r: (out.update(result=r), done.set()),
        on_error=lambda m: (out.update(error=m), done.set()),
        **kwargs,
    )
    job.start()
    assert done.wait(timeout=900), "transcription timed out"
    if "error" in out:
        pytest.fail(f"worker reported: {out['error']}")
    return out["result"]


def test_transcribes_a_whatsapp_voice_note(sample_audio, model_root):
    result = transcribe(sample_audio, model_root)
    text = result.text.lower()
    assert result.segments, "no segments produced"
    hits = [w for w in EXPECTED_WORDS if w in text]
    assert len(hits) >= 3, f"only matched {hits} in: {text[:200]}"


def test_detects_the_language_and_duration(sample_audio, model_root):
    result = transcribe(sample_audio, model_root)
    assert result.language == "en"
    assert result.language_probability > 0.5
    assert 15 < result.duration < 30, f"unexpected duration {result.duration}"
    assert result.language_label == "English"


def test_output_is_punctuated(sample_audio, model_root):
    """Guards the initial_prompt style trap: hints must not suppress punctuation."""
    result = transcribe(sample_audio, model_root,
                        initial_prompt="Contoso, Northwind, Atlas API")
    assert result.text.count(".") >= 3, f"punctuation missing: {result.text[:200]}"


def test_segments_stay_in_order_and_within_the_audio(sample_audio, model_root):
    result = transcribe(sample_audio, model_root)
    starts = [s.start for s in result.segments]
    assert starts == sorted(starts), "segments arrived out of order"
    assert result.segments[-1].end <= result.duration + 1


def test_a_missing_file_is_reported_not_raised(model_root, tmp_path):
    done = threading.Event()
    errors: list[str] = []
    job = at.TranscribeJob(
        tmp_path / "nope.opus", model_size="tiny", download_root=model_root,
        on_error=lambda m: (errors.append(m), done.set()),
        on_done=lambda r: done.set(),
    )
    job.start()
    assert done.wait(timeout=300)
    assert errors and "No such file" in errors[0]


# --- the window -------------------------------------------------------------

def drive_window(tk_root, path, model_root, timeout=900):
    """Open the real TranscribeWindow, run it, and return it once finished."""
    from transcribe_window import TranscribeWindow

    win = TranscribeWindow(tk_root, initial_path=str(path))
    win.v_prompt.set("Contoso, Northwind")
    finished = {"value": False}

    def poll(n=0):
        if win._result is not None or (n > 6 and win._job is None):
            finished["value"] = True
            tk_root.quit()
            return
        if n * 0.5 > timeout:
            tk_root.quit()
            return
        tk_root.after(500, poll, n + 1)

    tk_root.after(1500, poll)
    tk_root.mainloop()
    assert finished["value"], f"window never finished; status: {win.v_status.get()!r}"
    return win


def test_window_transcribes_and_enables_the_actions(tk_root, sample_audio, model_root):
    win = drive_window(tk_root, sample_audio, model_root)
    text = win.text.get("1.0", "end-1c").strip()
    assert text, "the window produced no text"
    assert "Done" in win.v_status.get()
    assert int(win.progress["value"]) == 1000, "progress bar did not finish full"
    states = {str(b["text"]): str(b["state"]) for b in win.action_buttons}
    assert all(s == "normal" for s in states.values()), states
    win.destroy()


def test_window_timestamp_toggle_re_renders(tk_root, sample_audio, model_root):
    win = drive_window(tk_root, sample_audio, model_root)
    assert not win.text.get("1.0", "end-1c").startswith("[")
    win.v_timestamps.set(True)
    win._rerender()
    assert win.text.get("1.0", "end-1c").startswith("[00:00]")
    win.v_timestamps.set(False)
    win._rerender()
    assert not win.text.get("1.0", "end-1c").startswith("[")
    win.destroy()


@pytest.mark.parametrize("suffix,marker", [
    (".txt", "update"), (".srt", "-->"), (".vtt", "WEBVTT"), (".md", "# Transcript"),
])
def test_window_saves_every_format(tk_root, sample_audio, model_root, tmp_path,
                                   suffix, marker):
    from tkinter import filedialog

    win = drive_window(tk_root, sample_audio, model_root)
    target = tmp_path / f"out{suffix}"
    original = filedialog.asksaveasfilename
    filedialog.asksaveasfilename = lambda **_kw: str(target)
    try:
        win.save_as()
    finally:
        filedialog.asksaveasfilename = original
    body = target.read_text(encoding="utf-8")
    assert marker.lower() in body.lower(), f"{suffix} output missing {marker!r}"
    win.destroy()


def test_window_hands_text_to_the_app(tk_root, sample_audio, model_root):
    """Summarize and Send to live transcript pass the text back to live_capture."""
    from transcribe_window import TranscribeWindow

    summarized: list[str] = []
    inserted: list[str] = []
    win = TranscribeWindow(tk_root, initial_path=None,
                           on_summarize=summarized.append, on_insert=inserted.append)
    win.text.insert("1.0", "Some transcribed text.")
    win.summarize()
    win.send_to_transcript()
    assert summarized == ["Some transcribed text."]
    assert inserted == ["Some transcribed text."]
    win.destroy()
