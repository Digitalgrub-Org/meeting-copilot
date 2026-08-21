# PyInstaller spec for Cue (by Digitalgrub) — Windows core build.
#
# Build:   pyinstaller Cue.spec --noconfirm
# Output:  dist/Cue/Cue.exe  (onedir — ship the whole dist/Cue folder, or zip it)
#
# FULL build: Teams capture, "Pick a window" capture, system-audio capture via
# Whisper, file transcription, knowledge base, Ollama + Claude, and the live-assist
# panel.
#
# Speech is INCLUDED here (faster-whisper / ctranslate2 / av / soundcard) because a
# Store or installer user has no Python to fall back on. Both speech features run in
# a child process, which a frozen build starts by re-running Cue.exe with
# --cue-worker; see worker_ipc.worker_argv. Models are not bundled — they download
# on first use into the data directory.
#
# Pin ctranslate2==4.4.0 in the BUILD environment. 4.5+ crashes on model load, and
# PyInstaller bundles whatever is installed, so a bad pin ships a broken app.

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

datas = []
# Bundle the UI-Automation helper script + app icon at the bundle root.
datas += [("extract_teams_transcript.ps1", "."), ("cue.ico", ".")]
# faster-whisper ships the Silero VAD models as package data, and ctranslate2 /
# onnxruntime / av carry native libraries PyInstaller won't find on its own.
datas += collect_data_files("faster_whisper")
# sv-ttk ships .tcl theme files that must travel with the app.
datas += collect_data_files("sv_ttk")

hiddenimports = []
# These are imported lazily / dynamically, so PyInstaller can't see them statically.
hiddenimports += ["docx", "pypdf", "tiktoken_ext", "tiktoken_ext.openai_public"]
# The speech workers are reached through worker_ipc's importlib dispatch, which
# PyInstaller cannot follow statically.
hiddenimports += ["audio_transcribe", "whisper_capture", "worker_ipc"]
hiddenimports += ["faster_whisper", "ctranslate2", "av", "soundcard", "onnxruntime"]
hiddenimports += collect_submodules("faster_whisper")

# Keep the heavy / unused stacks out of the build.
# TF-IDF is pure-python, so scikit-learn + scipy are not needed (huge size cut).
# numpy stays: the OpenAI-embeddings path and the audio pipeline both use it.
excludes = [
    "torch", "transformers", "sentence_transformers",     # abandoned embedding stack
    "chromadb",                                           # abandoned vector DB
    "sklearn", "scikit-learn", "scipy",                   # replaced by pure-python TF-IDF
    "matplotlib", "tensorflow", "IPython", "notebook",    # never used by Cue
    "pandas", "mypy", "zmq", "jedi", "pytest",            # pulled in by the base env
]

a = Analysis(
    ["live_capture.py"],
    pathex=[],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Cue",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,            # windowed app, no console
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon="cue.ico",
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="Cue",
)
