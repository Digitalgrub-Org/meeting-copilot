#!/usr/bin/env bash
# Build a 9:16 Instagram promo reel for Cue (by Digitalgrub).
# Requires: ImageMagick (magick) + ffmpeg on PATH.
set -e
cd "$(dirname "$0")"

W=1080; H=1920
BG="#0E1116"
PANEL="#161B22"
WHITE="#F2F5F8"
GRAY="#9AA4B2"
ACCENT="#F7B500"    # Digitalgrub amber
BOLD="C:/Windows/Fonts/segoeuib.ttf"
SEMI="C:/Windows/Fonts/seguisb.ttf"
LIGHT="C:/Windows/Fonts/segoeuil.ttf"

mkdir -p clips

# make_slide OUT HEAD HEAD_COLOR SUB [FOOT]
make_slide() {
  local out="$1" head="$2" hcol="$3" sub="$4" foot="$5"
  local cmd=(magick -size ${W}x${H} xc:"$BG"
    \( -background none -fill "$hcol" -font "$BOLD" -pointsize 100 -size 820x -gravity center caption:"$head" \)
      -gravity center -geometry +0-120 -composite
    \( -background none -fill "$GRAY" -font "$LIGHT" -pointsize 54 -size 840x -gravity center caption:"$sub" \)
      -gravity center -geometry +0+170 -composite )
  if [ -n "$foot" ]; then
    cmd+=( \( -background none -fill "$ACCENT" -font "$SEMI" -pointsize 38 -size 900x -gravity center caption:"$foot" \)
      -gravity south -geometry +0+110 -composite )
  fi
  cmd+=( "$out" )
  "${cmd[@]}"
}

# 1 — hook
make_slide clips/s1.png "Zoned out in the meeting?" "$WHITE" \
  "Then someone asks for YOUR input."

# 2 — stakes
make_slide clips/s2.png "Don't freeze." "$ACCENT" \
  "There's a better way to stay in the room."

# 3 — reveal
magick -size ${W}x${H} xc:"$BG" \
  \( -background none -fill "$ACCENT" -font "$BOLD" -pointsize 240 -size 1000x -gravity center caption:"Cue" \) \
    -gravity center -geometry +0-150 -composite \
  \( -background none -fill "$WHITE" -font "$LIGHT" -pointsize 64 -size 900x -gravity center caption:"your cue to speak" \) \
    -gravity center -geometry +0+80 -composite \
  \( -background none -fill "$GRAY" -font "$SEMI" -pointsize 40 -size 900x -gravity center caption:"a live meeting copilot" \) \
    -gravity center -geometry +0+185 -composite \
  clips/s3.png

# 4 — real product screenshot, framed
magick cue_ui.png -resize 1000x -bordercolor "$PANEL" -border 6 clips/shot.png
magick -size ${W}x${H} xc:"$BG" \
  \( -background none -fill "$WHITE" -font "$BOLD" -pointsize 72 -size 940x -gravity center caption:"It listens to your meeting, live" \) \
    -gravity north -geometry +0+220 -composite \
  clips/shot.png -gravity center -geometry +0+120 -composite \
  \( -background none -fill "$GRAY" -font "$LIGHT" -pointsize 44 -size 940x -gravity center caption:"Teams, Meet, Zoom, or any window with captions" \) \
    -gravity south -geometry +0+150 -composite \
  clips/s4.png

# 5 — three things
magick -size ${W}x${H} xc:"$BG" \
  \( -background none -fill "$WHITE" -font "$BOLD" -pointsize 64 -size 900x -gravity center caption:"In a side panel:" \) \
    -gravity north -geometry +0+300 -composite \
  \( -background none -fill "$ACCENT" -font "$BOLD" -pointsize 78 -size 920x -gravity center caption:"A running brief" \) \
    -gravity center -geometry +0-230 -composite \
  \( -background none -fill "$ACCENT" -font "$BOLD" -pointsize 78 -size 920x -gravity center caption:"Questions to ask" \) \
    -gravity center -geometry +0-30 -composite \
  \( -background none -fill "$ACCENT" -font "$BOLD" -pointsize 78 -size 920x -gravity center caption:"What you could say" \) \
    -gravity center -geometry +0+170 -composite \
  \( -background none -fill "$GRAY" -font "$LIGHT" -pointsize 46 -size 900x -gravity center caption:"grounded in YOUR own documents" \) \
    -gravity south -geometry +0+190 -composite \
  clips/s5.png

# 6 — local & private
make_slide clips/s6.png "Free and local." "$WHITE" \
  "Runs on your machine. Your meeting never leaves your laptop."

# logo, rounded a touch, for branding
magick dg_logo.png -resize x150 clips/logo_small.png

# 7 — CTA (with real Digitalgrub logo)
magick -size ${W}x${H} xc:"$BG" \
  clips/logo_small.png -gravity north -geometry +0+360 -composite \
  \( -background none -fill "$ACCENT" -font "$BOLD" -pointsize 210 -size 1000x -gravity center caption:"Cue" \) \
    -gravity center -geometry +0-40 -composite \
  \( -background none -fill "$WHITE" -font "$SEMI" -pointsize 56 -size 900x -gravity center caption:"your cue to speak" \) \
    -gravity center -geometry +0+150 -composite \
  \( -background none -fill "$GRAY" -font "$LIGHT" -pointsize 46 -size 900x -gravity center caption:"by Digitalgrub  ·  free & open source" \) \
    -gravity south -geometry +0+200 -composite \
  clips/s7.png

DURS=(3.6 3.0 3.6 5.2 5.0 4.2 4.0)
SLIDES=(s1 s2 s3 s4 s5 s6 s7)

: > clips/list.txt
i=0
for s in "${SLIDES[@]}"; do
  d=${DURS[$i]}
  fo=$(awk "BEGIN{printf \"%.2f\", $d-0.35}")
  ffmpeg -y -loglevel error -loop 1 -t "$d" -i "clips/$s.png" \
    -f lavfi -t "$d" -i anullsrc=channel_layout=stereo:sample_rate=44100 \
    -vf "scale=${W}:${H},fade=t=in:st=0:d=0.35,fade=t=out:st=${fo}:d=0.35,format=yuv420p" \
    -c:v libx264 -preset medium -crf 20 -r 30 -c:a aac -b:a 128k -shortest "clips/$s.mp4"
  echo "file '$s.mp4'" >> clips/list.txt
  i=$((i+1))
done

ffmpeg -y -loglevel error -f concat -safe 0 -i clips/list.txt -c copy Cue_reel.mp4
echo "DONE -> $(pwd)/Cue_reel.mp4"
ffprobe -v error -show_entries format=duration -of default=noprint_wrappers=1:nokey=1 Cue_reel.mp4
