"""Parse teams_extracted_raw.txt into Speaker: text format, save, and copy to clipboard."""

import re
import subprocess
from pathlib import Path
from transcript_cleaner import clean_transcript

HERE = Path(__file__).parent
raw_path = HERE / "teams_extracted_raw.txt"
out_path = HERE / "teams_transcript_cleaned.txt"

raw = raw_path.read_text(encoding="utf-8")
lines = [ln.rstrip() for ln in raw.splitlines()]

# Strip the `[type] ` prefix the extractor adds
stripped: list[str] = []
for ln in lines:
    m = re.match(r"^\[([^\]]+)\]\s*(.*)$", ln)
    label_type = m.group(1) if m else None
    content = m.group(2) if m else ln
    content = content.strip()
    if not content:
        continue
    # Drop window chrome / room labels
    if label_type == "document":
        continue
    if content in {"ACME Tower 7", "Untitled"}:
        continue
    stripped.append(content)

# Pair speaker labels with their following caption text
SPEAKER_RE = re.compile(r"^[A-Z][\w'’\-\.]+(?:\s+[A-Z][\w'’\-\.]+)*$")

paired: list[str] = []
i = 0
while i < len(stripped):
    cur = stripped[i]
    # Heuristic: a short (<= 4 words) capitalized line followed by a longer line = speaker + caption
    is_speaker = (
        SPEAKER_RE.match(cur)
        and len(cur.split()) <= 4
        and i + 1 < len(stripped)
        and len(stripped[i + 1]) > len(cur)
    )
    if is_speaker:
        paired.append(f"{cur}: {stripped[i + 1]}")
        i += 2
    else:
        paired.append(cur)
        i += 1

joined = "\n\n".join(paired)
cleaned = clean_transcript(joined)

out_path.write_text(cleaned, encoding="utf-8")
print(f"Saved cleaned transcript ({len(cleaned):,} chars) -> {out_path}")

# Copy to Windows clipboard
proc = subprocess.run(
    ["powershell", "-NoProfile", "-Command", "$input | Set-Clipboard"],
    input=cleaned,
    text=True,
    encoding="utf-8",
)
if proc.returncode == 0:
    print("Copied to clipboard. Paste anywhere with Ctrl+V.")
else:
    print(f"Clipboard copy failed with exit code {proc.returncode}")

print("\n--- First 600 chars preview ---")
print(cleaned[:600])
