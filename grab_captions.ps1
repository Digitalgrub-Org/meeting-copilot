# One-shot: extract Teams live captions, clean them, copy to clipboard.
# Usage:  powershell -ExecutionPolicy Bypass -File grab_captions.ps1

$here = Split-Path -Parent $MyInvocation.MyCommand.Path
& powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $here 'extract_teams_transcript.ps1')
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
& python (Join-Path $here 'process_extract.py')
