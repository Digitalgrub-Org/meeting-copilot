# Third-party notices

Cue itself is MIT licensed (see [LICENSE](LICENSE)). The packaged build
(`CueSetup.exe` and the `dist/Cue` folder) also contains third-party components,
listed here with their licenses.

**Running from source distributes nothing**, so these obligations apply only to the
packaged binary. If you build and hand out `CueSetup.exe`, ship this file with it.

> ⚠️ **Read [The FFmpeg question](#the-ffmpeg-question) before distributing binaries.**
> One bundled component has a licensing status that needs resolving first. It does not
> affect using Cue yourself, or sharing the source.

---

## The FFmpeg question

Cue decodes audio through [PyAV](https://github.com/PyAV-Org/PyAV), whose wheels embed
a prebuilt FFmpeg. Querying `avutil_configuration()` on the exact DLL we ship
(`avutil-60`, FFmpeg 8.x, PyAV 18.1.0) reports it was configured with:

```
--enable-version3 --enable-libdav1d --enable-libmp3lame --enable-libopencore-amrnb
--enable-libopencore-amrwb --enable-libopus --enable-libsvtav1 --enable-libvpx
--enable-libwebp --enable-libx264 --enable-libx265 --enable-nvenc --enable-nvdec
--enable-amf --enable-libvpl --enable-mediafoundation --enable-shared
```

Two things follow, and one is unresolved.

**Settled: this FFmpeg is at least LGPL v3, not LGPL v2.1.** `--enable-version3`
upgrades the license, which this build needs because it includes `libopencore-amr`.
LGPL v3 lets you ship it inside a differently-licensed application, provided you say
you are using it, supply the license text, and leave the user able to replace the
library with their own build. Shipping FFmpeg as separate `.dll` files in
`dist/Cue/_internal` satisfies the replaceability part on its own.

**Resolved, and it is GPL.** `libx264` and `libx265` are GPL, and upstream FFmpeg will
not enable them without `--enable-gpl`. That flag is absent from PyAV 18.1.0's
recorded configure line, so we checked older wheels. Every PyAV wheel from 12 through
18 bundles both encoders, and **12.3.0 and 13.1.0 record `--enable-gpl` explicitly**:

| PyAV wheel | libavutil | libx264 / libx265 | `--enable-gpl` recorded |
|---|---|---|---|
| 12.3.0 | 58.29 | yes | **yes** |
| 13.1.0 | 59.8 | yes | **yes** |
| 15.1.0 | 59.39 | yes | not in string |
| 16.1.0 | 60.8 | yes | not in string |
| 17.1.0 | 60.26 | yes | not in string |
| 18.1.0 | 60.26 | yes | not in string |

The newer builds dropped the literal flag from the recorded string but still ship the
same GPL encoders. Treat the bundled FFmpeg as **GPL v3** (GPL, raised to v3 by
`--enable-version3`).

**So pinning an older PyAV does not help.** There is no LGPL-only wheel to fall back
to.

It matters because if that FFmpeg is effectively GPL, then distributing Cue's binary
means distributing a GPL work, and the whole distribution has to meet GPL terms.
MIT source combines into GPL fine, but the *binary you hand out* would carry GPL
obligations, which is almost certainly not what you want and would complicate a
Microsoft Store submission.

### Cue does not need any of this

x264, x265, SVT-AV1, VPX, dav1d, WebP and NVENC are **video** codecs. Cue only ever
decodes an audio track. Those components are dead weight: roughly 29 MB of DLLs
providing capability the app never calls.

Two things block the obvious workarounds, both verified rather than assumed:

- **They cannot be deleted from the bundle.** `avcodec` links them at build time, so
  removing the DLLs breaks `import av` outright with `DLL load failed while importing
  _core`.
- **PyAV cannot be dropped either.** `faster_whisper/audio.py` imports `av` at module
  level, so `import faster_whisper` fails without it. Even if Cue decoded audio some
  other way, the DLLs would still be loaded and shipped.

**The options, honestly costed:**

1. **Ship the installer under GPL terms.** The least work by a distance, and awkwardly
   the most practical. GPL wants the corresponding source available, and Cue's source
   is public and FFmpeg's is upstream, so compliance is mostly a labelling exercise:
   the *binary* is offered under GPL v3, while the source stays MIT. Cue's own code
   does not change license. **The catch is the Microsoft Store**, whose terms have
   historically sat badly with GPL v3, so this may close that route. Fine for a GitHub
   release, questionable for the Store.
2. **Build a custom audio-only FFmpeg** and compile PyAV against it, configured without
   `--enable-libx264 --enable-libx265 --enable-libsvtav1 --enable-libvpx
   --enable-libwebp --enable-nvenc`, keeping `--enable-libopus`. Gives an unambiguously
   LGPL result, a smaller installer, and keeps the Store route open. Costs a full MSYS2
   and nasm toolchain, and means maintaining a custom media stack you have to rebuild
   for every security update. Do not undertake this casually.
3. **Replace the audio path and patch faster-whisper** so `av` is never imported,
   decoding via a separate `ffmpeg.exe` subprocess instead. A separate process is not
   linking, so nothing propagates into Cue's binary. Needs a fork or an upstream patch
   of faster-whisper, and either bundling `ffmpeg.exe` (still GPL, but separable, so
   only that file carries the obligation) or requiring the user to install it.
4. **Do not ship binaries.** Source distribution carries no obligation at all. Costs
   you every user who will not install Python.

**Recommendation:** option 1 for a GitHub release now, option 2 only if the Microsoft
Store matters enough to justify maintaining a custom FFmpeg.

*None of this is legal advice. It is what the binaries actually report, and what the
licenses say on their face.*

---

## Native components

| Component | Version | License | Notes |
|---|---|---|---|
| [FFmpeg](https://ffmpeg.org/) (libavcodec, libavformat, libavutil, libavfilter, libavdevice, libswscale, libswresample) | 8.x, via PyAV 18.1.0 | LGPL v3 or later, plus the open question above | Audio and video decoding |
| [libopus](https://opus-codec.org/) | 1.x | BSD 3-Clause | Decodes WhatsApp `.opus` voice notes |
| [libopencore-amr](https://sourceforge.net/projects/opencore-amr/) | — | Apache 2.0 | Why FFmpeg here is version3 |
| [libx264](https://www.videolan.org/developers/x264.html) | 165 | GPL v2 or later (see above) | **Unused by Cue** |
| [libx265](https://www.x265.org/) | — | GPL v2 or later (see above) | **Unused by Cue** |
| [SVT-AV1](https://gitlab.com/AOMediaCodec/SVT-AV1), [libvpx](https://chromium.googlesource.com/webm/libvpx), [dav1d](https://code.videolan.org/videolan/dav1d), [libwebp](https://chromium.googlesource.com/webm/libwebp), [libmp3lame](https://lame.sourceforge.io/) | — | BSD 3-Clause / BSD / LGPL | **Unused by Cue** |
| [CTranslate2](https://github.com/OpenNMT/CTranslate2) | 4.4.0 | MIT | Runs the Whisper model |
| [Intel OpenMP runtime](https://www.openmprtl.org/) (`libiomp5md.dll`) | — | Apache 2.0 with LLVM exception | Shipped by CTranslate2 |
| [NVIDIA cuDNN](https://developer.nvidia.com/cudnn) (`cudnn64_8.dll`) | 8 | NVIDIA Software License Agreement | Shipped by CTranslate2. **Unused** — Cue runs on CPU. Redistribution carries NVIDIA's own terms; worth removing. |
| [ONNX Runtime](https://onnxruntime.ai/) | 1.18.1 | MIT | Silero VAD for silence trimming |
| [OpenBLAS](https://www.openblas.net/) | 0.3.23 | BSD 3-Clause | Shipped by NumPy |
| [Tcl/Tk](https://www.tcl.tk/) | 8.6 | Tcl/Tk License (BSD-style) | The GUI toolkit |
| [SQLite](https://sqlite.org/) | 3.x | Public domain | Python standard library |
| [CPython](https://www.python.org/) | 3.11 | Python Software Foundation License 2.0 | The interpreter |
| [libstdc++](https://gcc.gnu.org/onlinedocs/libstdc++/), libiconv | — | GPL v3 with Runtime Library Exception; LGPL | MinGW runtime from the FFmpeg build. The Runtime Library Exception exists precisely so this does not impose GPL terms. |

## Python packages

All permissive. Full license texts ship inside each package's `dist-info` directory in
`dist/Cue/_internal`.

| Package | Version | License |
|---|---|---|
| anthropic | 1.0.0 | MIT |
| av (PyAV) | 18.1.0 | BSD 3-Clause *(the bundled FFmpeg is separate — see above)* |
| certifi | 2026.7.22 | MPL 2.0 |
| cffi | 2.1.1 | MIT |
| charset-normalizer | 3.5.1 | MIT |
| click | 8.4.2 | BSD 3-Clause |
| colorama | 0.4.6 | BSD |
| coloredlogs | 15.0.1 | MIT |
| ctranslate2 | 4.4.0 | MIT |
| faster-whisper | 1.2.1 | MIT |
| filelock | 3.32.3 | Unlicense |
| flatbuffers | 25.12.19 | Apache 2.0 |
| fsspec | 2026.7.0 | BSD 3-Clause |
| h11 | 0.16.0 | MIT |
| hf-xet | 1.6.0 | Apache 2.0 |
| httpcore / httpx | 1.0.9 / 0.28.1 | BSD 3-Clause |
| huggingface_hub | 1.28.0 | Apache 2.0 |
| humanfriendly | 10.0 | MIT |
| idna | 3.19 | BSD 3-Clause |
| jiter | 0.16.0 | MIT |
| lxml | 6.1.2 | BSD 3-Clause |
| mpmath | 1.3.0 | BSD 3-Clause |
| numpy | 1.26.4 | BSD 3-Clause |
| ollama | 0.6.2 | MIT |
| onnxruntime | 1.18.1 | MIT |
| openai | 3.3.1 | Apache 2.0 |
| packaging | 26.3 | Apache 2.0 / BSD 2-Clause |
| protobuf | 7.36.0 | BSD 3-Clause |
| pycparser | 3.0 | BSD 3-Clause |
| pydantic / pydantic-core | 2.13.4 / 2.46.4 | MIT |
| pypdf | 6.16.1 | BSD 3-Clause |
| pyreadline3 | 3.5.6 | BSD 3-Clause |
| python-docx | 1.2.0 | MIT |
| PyYAML | 6.0.3 | MIT |
| regex | 2026.7.19 | Apache 2.0 |
| requests | 2.34.2 | Apache 2.0 |
| sniffio | 1.3.1 | MIT or Apache 2.0 |
| SoundCard | 0.4.6 | BSD 3-Clause |
| sv-ttk | 2.6.1 | MIT |
| sympy | 1.14.0 | BSD 3-Clause |
| tiktoken | 0.14.0 | MIT |
| tokenizers | 0.23.1 | Apache 2.0 |
| tqdm | 4.70.0 | MPL 2.0 and MIT |
| typing_extensions | 4.16.0 | PSF 2.0 |
| urllib3 | 2.7.0 | MIT |

## Build tooling, not shipped

[PyInstaller](https://pyinstaller.org/) is GPL v2, but carries an explicit exception
allowing the applications it freezes to be distributed under any license. Nothing of
PyInstaller's own licensed code ends up in the output beyond its bootloader, which the
exception covers. [Inno Setup](https://jrsoftware.org/isinfo.php) builds the installer
and is separately licensed; none of it is redistributed by Cue.

## Speech models

Whisper models are **not bundled**. They download on first use from Hugging Face
([Systran/faster-whisper-*](https://huggingface.co/Systran)), converted from OpenAI's
Whisper, MIT licensed. The Silero VAD model ships inside the `faster-whisper` package,
MIT licensed.

## Assets

The Cue icon, the Digitalgrub logo and the promotional material in `promo/` are
© 2026 Digitalgrub, all rights reserved. They are not covered by the MIT license on
the source code.
