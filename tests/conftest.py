"""Shared fixtures. Keeps the repo root importable and locates the audio fixture."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

FIXTURES = Path(__file__).resolve().parent / "fixtures"


@pytest.fixture(scope="session")
def sample_audio() -> Path:
    """A short synthetic English voice note, encoded like a WhatsApp one."""
    path = FIXTURES / "sample_en.opus"
    if not path.exists():
        pytest.skip(f"missing audio fixture: {path}")
    return path


@pytest.fixture(scope="session")
def model_root(tmp_path_factory) -> str:
    """Where slow tests may download speech models.

    Reuses Cue's own model directory when it exists, so a developer who has already
    pulled a model does not download it again.
    """
    try:
        import config as cfg_mod
        existing = cfg_mod.DATA_DIR / "whisper-models"
        if existing.exists():
            return str(existing)
    except Exception:
        pass
    return str(tmp_path_factory.mktemp("whisper-models"))


@pytest.fixture(scope="session")
def _tk_session():
    """One Tk root for the whole run.

    Deliberately session-scoped. Creating and destroying a root per test
    intermittently failed to initialise Tk on a machine whose Anaconda Tk is missing
    `ttk/winTheme.tcl`, which showed up as tests skipping at random. One root sidesteps
    that entirely and is faster.
    """
    tk = pytest.importorskip("tkinter")
    try:
        root = tk.Tk()
    except tk.TclError as e:
        pytest.skip(f"Tk unavailable: {e}")
    root.withdraw()
    # live_capture defines these app-wide; widgets reference them by name.
    from tkinter import ttk
    try:
        style = ttk.Style()
        style.configure("Section.TLabel", font=("Segoe UI Semibold", 11))
        style.configure("Hint.TLabel", foreground="#7a7a7a")
    except Exception:
        pass
    try:
        yield root
    finally:
        try:
            root.destroy()
        except Exception:
            pass


@pytest.fixture
def tk_root(_tk_session):
    """The shared root, with anything a test created cleaned up afterwards."""
    before = set(_tk_session.winfo_children())
    yield _tk_session
    for child in set(_tk_session.winfo_children()) - before:
        try:
            child.destroy()
        except Exception:
            pass
