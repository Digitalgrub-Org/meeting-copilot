# PyInstaller spec for Cue (by Digitalgrub) — Windows core build.
#
# Build:   pyinstaller Cue.spec --noconfirm
# Output:  dist/Cue/Cue.exe  (onedir — ship the whole dist/Cue folder, or zip it)
#
# This is the CORE build: Teams capture, "Pick a window" capture, knowledge base,
# Ollama + Claude, and the live-assist panel. It deliberately EXCLUDES the
# Whisper system-audio source (faster-whisper / ctranslate2 / av / soundcard),
# which is heavy and native; that source stays available in the Python version.

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

datas = []
# Bundle the UI-Automation helper script + app icon at the bundle root.
datas += [("extract_teams_transcript.ps1", "."), ("cue.ico", ".")]
# sv-ttk ships .tcl theme files that must travel with the app.
datas += collect_data_files("sv_ttk")

hiddenimports = []
# These are imported lazily / dynamically, so PyInstaller can't see them statically.
hiddenimports += ["docx", "pypdf", "tiktoken_ext", "tiktoken_ext.openai_public"]

# Keep the heavy / unused stacks out of the build.
# TF-IDF is now pure-python, so scikit-learn + scipy are no longer needed (huge size cut).
# numpy stays — only the optional OpenAI-embeddings path uses it.
excludes = [
    "faster_whisper", "ctranslate2", "av", "soundcard",   # Whisper source (Python-only)
    "torch", "transformers", "sentence_transformers",     # abandoned embedding stack
    "chromadb", "onnxruntime",                            # abandoned vector DB
    "sklearn", "scikit-learn", "scipy",                   # replaced by pure-python TF-IDF
    "matplotlib", "tensorflow", "IPython", "notebook",    # never used by Cue
    "pandas",
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
