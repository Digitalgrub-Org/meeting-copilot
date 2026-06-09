from __future__ import annotations

import argparse
import subprocess
import textwrap
from dataclasses import dataclass
from pathlib import Path


WIDTH = 1080
HEIGHT = 1920
FPS = 30
REGULAR_FONT = Path(r"C:\Windows\Fonts\segoeui.ttf")
BOLD_FONT = Path(r"C:\Windows\Fonts\segoeuib.ttf")
DEFAULT_VOICE = "Microsoft Zira Desktop"


@dataclass
class Scene:
    heading: str
    body: str
    subtitle: str
    bg_color: str
    panel_color: str
    accent_color: str
    min_duration: float = 3.8
    duration: float = 0.0


SCENES = [
    Scene(
        heading="Meet Cue",
        body="A Windows copilot for live meetings.",
        subtitle="Meet Cue, a Windows copilot for live meetings.",
        bg_color="0x0F172A",
        panel_color="0x14213A",
        accent_color="0x38BDF8",
    ),
    Scene(
        heading="Listens Live",
        body="Reads Teams captions, browser subtitles, or system audio.",
        subtitle="Cue reads Teams captions, browser subtitles, or system audio.",
        bg_color="0x101827",
        panel_color="0x1A2942",
        accent_color="0xF59E0B",
    ),
    Scene(
        heading="Keeps You Current",
        body="Builds a running brief while the meeting is still happening.",
        subtitle="It builds a running brief while the meeting is still happening.",
        bg_color="0x102A43",
        panel_color="0x18385A",
        accent_color="0x34D399",
    ),
    Scene(
        heading="Helps You Speak",
        body="Suggests smart questions and ready-to-say lines so you can jump in fast.",
        subtitle="Cue suggests smart questions and ready-to-say lines so you can jump in fast.",
        bg_color="0x1F2937",
        panel_color="0x2F3C4F",
        accent_color="0xFB7185",
    ),
    Scene(
        heading="Grounded In Your Docs",
        body="Add specs, notes, and past meetings so the answers stay relevant.",
        subtitle="Add specs, notes, and past meetings so the answers stay relevant.",
        bg_color="0x14261C",
        panel_color="0x223628",
        accent_color="0xA3E635",
    ),
    Scene(
        heading="Private By Default",
        body="Runs locally with Ollama. No telemetry. Your cue to speak.",
        subtitle="Runs locally with Ollama, no telemetry. Cue by Digitalgrub. Your cue to speak.",
        bg_color="0x2B1A12",
        panel_color="0x3A2418",
        accent_color="0xFDBA74",
    ),
]


def run(command: list[str], *, cwd: Path | None = None) -> None:
    subprocess.run(command, check=True, cwd=cwd)


def capture(command: list[str]) -> str:
    return subprocess.check_output(command, text=True).strip()


def escape_filter_path(path: Path) -> str:
    return path.resolve().as_posix().replace(":", r"\:")


def to_ps_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def wrap_text(value: str, width: int) -> str:
    return textwrap.fill(value, width=width)


def write_plain_text(path: Path, value: str) -> None:
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(value)


def ensure_tools() -> None:
    run(["ffmpeg", "-version"])
    run(["ffprobe", "-version"])
    if not REGULAR_FONT.exists() or not BOLD_FONT.exists():
        raise FileNotFoundError("Expected Segoe UI fonts were not found in C:/Windows/Fonts.")


def synthesize_scene_audio(text_path: Path, wav_path: Path, voice: str, rate: int) -> None:
    voices_script = (
        "Add-Type -AssemblyName System.Speech; "
        "$synth = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
        "$synth.GetInstalledVoices() | ForEach-Object { $_.VoiceInfo.Name }"
    )
    available_voices = capture(["pwsh", "-NoProfile", "-Command", voices_script]).splitlines()
    voice_name = voice if voice in available_voices else ""

    script = "\n".join(
        [
            "Add-Type -AssemblyName System.Speech",
            "$synth = New-Object System.Speech.Synthesis.SpeechSynthesizer",
            f"$voiceName = {to_ps_literal(voice_name)}",
            "if ($voiceName) { $synth.SelectVoice($voiceName) }",
            f"$synth.Rate = {rate}",
            f"$text = [System.IO.File]::ReadAllText({to_ps_literal(str(text_path.resolve()))})",
            f"$synth.SetOutputToWaveFile({to_ps_literal(str(wav_path.resolve()))})",
            "$synth.Speak($text)",
            "$synth.Dispose()",
        ]
    )
    run(["pwsh", "-NoProfile", "-Command", script])


def probe_duration(path: Path) -> float:
    output = capture(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(path),
        ]
    )
    return float(output)


def format_timestamp(total_seconds: float) -> str:
    milliseconds = round(total_seconds * 1000)
    hours, milliseconds = divmod(milliseconds, 3_600_000)
    minutes, milliseconds = divmod(milliseconds, 60_000)
    seconds, milliseconds = divmod(milliseconds, 1000)
    return f"{hours:02}:{minutes:02}:{seconds:02},{milliseconds:03}"


def write_scene_files(scene: Scene, scene_dir: Path) -> dict[str, Path]:
    heading_path = scene_dir / "heading.txt"
    body_path = scene_dir / "body.txt"
    subtitle_path = scene_dir / "subtitle.txt"
    narration_path = scene_dir / "narration.txt"

    write_plain_text(heading_path, wrap_text(scene.heading, 18))
    write_plain_text(body_path, wrap_text(scene.body, 30))
    write_plain_text(subtitle_path, wrap_text(scene.subtitle, 38))
    write_plain_text(narration_path, scene.subtitle)

    return {
        "heading": heading_path,
        "body": body_path,
        "subtitle": subtitle_path,
        "narration": narration_path,
    }


def build_filter(scene: Scene, text_files: dict[str, Path]) -> str:
    regular_font = escape_filter_path(REGULAR_FONT)
    bold_font = escape_filter_path(BOLD_FONT)
    heading = escape_filter_path(text_files["heading"])
    body = escape_filter_path(text_files["body"])
    subtitle = escape_filter_path(text_files["subtitle"])
    fade_start = max(scene.duration - 0.35, 0)

    return ",".join(
        [
            "drawgrid=w=180:h=180:t=1:c=white@0.05",
            f"drawbox=x=54:y=120:w=972:h=1660:color={scene.panel_color}@0.86:t=fill",
            f"drawbox=x=54:y=120:w=972:h=14:color={scene.accent_color}@1.0:t=fill",
            f"drawbox=x=760:y=1480:w=240:h=240:color={scene.accent_color}@0.14:t=fill",
            "drawbox=x=90:y=1430:w=900:h=330:color=black@0.30:t=fill",
            f"drawtext=fontfile='{bold_font}':text='DIGITALGRUB':fontcolor={scene.accent_color}:fontsize=28:x=84:y=128",
            f"drawtext=fontfile='{bold_font}':textfile='{heading}':fontcolor=white:fontsize=88:line_spacing=10:x=84:y=195",
            f"drawtext=fontfile='{regular_font}':textfile='{body}':fontcolor=0xE8F1F8:fontsize=56:line_spacing=18:x=84:y=470",
            f"drawtext=fontfile='{regular_font}':textfile='{subtitle}':fontcolor=white:fontsize=48:line_spacing=14:x=108:y=h-312",
            "fade=t=in:st=0:d=0.35",
            f"fade=t=out:st={fade_start:.2f}:d=0.35",
        ]
    )


def render_scene(scene: Scene, scene_dir: Path, voice: str, rate: int) -> Path:
    text_files = write_scene_files(scene, scene_dir)
    audio_path = scene_dir / "narration.wav"
    synthesize_scene_audio(text_files["narration"], audio_path, voice, rate)

    audio_duration = probe_duration(audio_path)
    scene.duration = max(scene.min_duration, audio_duration + 0.45)
    output_path = scene_dir / "clip.mp4"
    video_filter = build_filter(scene, text_files)

    run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"color=c={scene.bg_color}:s={WIDTH}x{HEIGHT}:r={FPS}:d={scene.duration:.3f}",
            "-i",
            str(audio_path),
            "-vf",
            video_filter,
            "-af",
            f"apad=whole_dur={scene.duration:.3f}",
            "-c:v",
            "libx264",
            "-preset",
            "medium",
            "-profile:v",
            "high",
            "-pix_fmt",
            "yuv420p",
            "-r",
            str(FPS),
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            "-ar",
            "48000",
            "-movflags",
            "+faststart",
            "-t",
            f"{scene.duration:.3f}",
            str(output_path),
        ]
    )
    return output_path


def write_srt(scenes: list[Scene], output_path: Path) -> None:
    current = 0.0
    blocks: list[str] = []
    for index, scene in enumerate(scenes, start=1):
        start = current
        end = current + scene.duration
        subtitle = wrap_text(scene.subtitle, 40)
        blocks.append(
            "\n".join(
                [
                    str(index),
                    f"{format_timestamp(start)} --> {format_timestamp(end)}",
                    subtitle,
                ]
            )
        )
        current = end
    output_path.write_text("\n\n".join(blocks) + "\n", encoding="utf-8")


def concat_clips(clips: list[Path], concat_file: Path, output_path: Path) -> None:
    concat_lines = [f"file '{clip.resolve().as_posix()}'" for clip in clips]
    concat_file.write_text("\n".join(concat_lines) + "\n", encoding="utf-8")
    run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(concat_file),
            "-c:v",
            "libx264",
            "-preset",
            "medium",
            "-profile:v",
            "high",
            "-pix_fmt",
            "yuv420p",
            "-r",
            str(FPS),
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            "-ar",
            "48000",
            "-movflags",
            "+faststart",
            str(output_path),
        ]
    )


def verify_output(video_path: Path) -> str:
    return capture(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=width,height,r_frame_rate",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1",
            str(video_path),
        ]
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Render a vertical Instagram promo reel for Cue.")
    parser.add_argument(
        "--output-dir",
        default="promo/output",
        help="Directory where rendered assets will be written.",
    )
    parser.add_argument(
        "--voice",
        default=DEFAULT_VOICE,
        help="Installed Windows voice to use for narration.",
    )
    parser.add_argument(
        "--rate",
        type=int,
        default=0,
        help="Speech rate for the local narrator, between -10 and 10.",
    )
    args = parser.parse_args()

    ensure_tools()

    output_dir = Path(args.output_dir)
    build_dir = output_dir / "build"
    build_dir.mkdir(parents=True, exist_ok=True)

    clips: list[Path] = []
    for index, scene in enumerate(SCENES, start=1):
        scene_dir = build_dir / f"scene_{index:02}"
        scene_dir.mkdir(parents=True, exist_ok=True)
        clips.append(render_scene(scene, scene_dir, args.voice, args.rate))

    video_path = output_dir / "cue_instagram_reel.mp4"
    srt_path = output_dir / "cue_instagram_reel.srt"
    concat_file = build_dir / "concat.txt"

    write_srt(SCENES, srt_path)
    concat_clips(clips, concat_file, video_path)

    print(video_path.resolve())
    print(srt_path.resolve())
    print(verify_output(video_path))


if __name__ == "__main__":
    main()