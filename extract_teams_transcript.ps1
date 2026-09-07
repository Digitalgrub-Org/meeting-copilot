# Extract text from a meeting window via Windows UI Automation.
#
# Two modes, both ending in "walk an element's accessibility tree and print its text":
#
#   -WindowTitle "<substring>"    read the top-level window whose title matches.
#                                 This is what the "Pick a window..." source uses.
#   -TeamsMode                    find Teams however it presents itself today, then
#                                 narrow to the captions pane if one is exposed.
#
# Why -TeamsMode exists: this script used to look for a top-level window titled
# "Captions". The current Teams client (ms-teams) has no such window -- captions are a
# pane inside the meeting window -- so the lookup always failed with "No window found"
# and the Teams source could never have worked. Classic Teams did pop out a Captions
# window, hence the original assumption.
#
# Usage:
#   powershell -ExecutionPolicy Bypass -File extract_teams_transcript.ps1 -TeamsMode
#   powershell -ExecutionPolicy Bypass -File extract_teams_transcript.ps1 -WindowTitle "Zoom Meeting"

param(
    [string]$OutPath = "",
    # Substring of the target window's title. Ignored when -TeamsMode finds Teams.
    [string]$WindowTitle = "Captions",
    # Locate Teams by process/title rather than requiring a "Captions" window.
    [switch]$TeamsMode,
    # Processes that count as Teams. ms-teams is the current client, Teams the classic one.
    [string[]]$TeamsProcess = @('ms-teams', 'Teams'),
    # Scroll the pane from top to bottom, reading at each step, and write every pass.
    # For a recording's Transcript pane, which is virtualized: only the entries on
    # screen exist in the accessibility tree, so one read gets ~2 minutes of a 2-hour
    # meeting. Progress is reported as JSON Lines on stdout for the parent to show.
    [switch]$Collect,
    [int]$MaxPasses = 400,
    [int]$SettleMs = 650,
    [int]$WheelNotches = 5
)

Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes
Add-Type -AssemblyName System.Windows.Forms

$Win32Sig = @'
[DllImport("user32.dll")] public static extern void mouse_event(uint dwFlags, int dx, int dy, int dwData, int dwExtraInfo);
[DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr hWnd);
[DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr hWnd, int nCmdShow);
'@
$Win32 = Add-Type -MemberDefinition $Win32Sig -Name NativeInput -Namespace CueExtract -PassThru

function Emit([hashtable]$obj) {
    # One JSON object per line; ConvertTo-Json escapes non-ASCII, so the pipe is safe.
    Write-Output ($obj | ConvertTo-Json -Compress)
}

$ErrorActionPreference = 'Stop'

# Names/automation ids that mark the captions region inside a meeting window.
$CaptionHints = @('caption', 'live caption', 'closed caption', 'transcript')

function Get-AllText {
    param([System.Windows.Automation.AutomationElement]$Root)

    $results = [System.Collections.Generic.List[string]]::new()
    $walker  = [System.Windows.Automation.TreeWalker]::ControlViewWalker
    $stack   = [System.Collections.Generic.Stack[System.Windows.Automation.AutomationElement]]::new()
    $stack.Push($Root)

    while ($stack.Count -gt 0) {
        $el = $stack.Pop()
        try {
            $name = $el.Current.Name
            $type = $el.Current.ControlType.LocalizedControlType
        } catch {
            continue
        }

        if ($name -and $name.Trim().Length -gt 0) {
            # Skip generic UI chrome
            if ($type -notin @('button','menu item','menu bar','tab','tab item','title bar','window','custom','pane','group','image','separator','tool bar','split button','check box','combo box','edit','spin button','progress bar','list','tree','tree item','header','header item','status bar','thumb','scroll bar')) {
                $results.Add("[$type] $name") | Out-Null
            } elseif ($type -in @('text','document','edit')) {
                $results.Add($name) | Out-Null
            }
        }

        # A stack pops last-in first, so pushing children first-to-last walked every
        # sibling group backwards: transcript entries came out newest-first and live
        # captions were scrambled within each poll. Push in reverse to restore document
        # order, which is reading order.
        $kids = [System.Collections.Generic.List[System.Windows.Automation.AutomationElement]]::new()
        $child = $walker.GetFirstChild($el)
        while ($child) {
            $kids.Add($child) | Out-Null
            $child = $walker.GetNextSibling($child)
        }
        for ($i = $kids.Count - 1; $i -ge 0; $i--) { $stack.Push($kids[$i]) }
    }

    return $results
}

function Count-TextDescendants {
    # How many text controls sit under $El. Distinguishes a content pane from a label
    # or tab that merely has "Transcript" as its name.
    param([System.Windows.Automation.AutomationElement]$El)
    try {
        $cond = New-Object System.Windows.Automation.PropertyCondition(
            [System.Windows.Automation.AutomationElement]::ControlTypeProperty,
            [System.Windows.Automation.ControlType]::Text)
        return $El.FindAll([System.Windows.Automation.TreeScope]::Descendants, $cond).Count
    } catch {
        return 0
    }
}

function Find-CaptionsRegion {
    <#
      Narrow a meeting window down to just its captions or transcript pane, when one
      is exposed. Returns $null if nothing qualifies, so the caller falls back to the
      whole window and lets downstream filtering deal with the sidebar.

      First version matched the first element *named* like captions and returned it.
      Against a real recording that was the "Transcript" tab button: zero text under
      it, so the capture came back empty. A region has to contain text to count.
    #>
    param([System.Windows.Automation.AutomationElement]$Window)

    $notRegions = @('button','tab item','hyperlink','menu item','check box','radio button','list item')

    try {
        $all = $Window.FindAll(
            [System.Windows.Automation.TreeScope]::Descendants,
            (New-Object System.Windows.Automation.PropertyCondition(
                [System.Windows.Automation.AutomationElement]::IsControlElementProperty, $true)))
    } catch {
        return $null
    }

    $best = $null
    $bestCount = 0
    foreach ($el in $all) {
        try {
            $name = "$($el.Current.Name)".ToLower()
            $autoId = "$($el.Current.AutomationId)".ToLower()
            $type = "$($el.Current.ControlType.LocalizedControlType)".ToLower()
        } catch { continue }
        if ($type -in $notRegions) { continue }

        $hit = $false
        foreach ($hint in $CaptionHints) {
            if ($name -eq $hint -or $autoId.Contains($hint) -or
                ($name.Contains($hint) -and $name.Length -lt 40)) { $hit = $true; break }
        }
        if (-not $hit) { continue }

        $n = Count-TextDescendants -El $el
        # Prefer the richest candidate: a pane with many lines beats a header with one.
        if ($n -ge 3 -and $n -gt $bestCount) { $best = $el; $bestCount = $n }
    }
    return $best
}

$root = [System.Windows.Automation.AutomationElement]::RootElement
$cond = New-Object System.Windows.Automation.PropertyCondition(
    [System.Windows.Automation.AutomationElement]::ControlTypeProperty,
    [System.Windows.Automation.ControlType]::Window)
$windows = $root.FindAll([System.Windows.Automation.TreeScope]::Children, $cond)

$target = $null
$strategy = ""

# 1. Title match. Still first, so an actual Captions window (classic Teams, or a
#    third-party captioner) wins when one exists.
$titleLower = $WindowTitle.ToLower()
foreach ($w in $windows) {
    $title = $w.Current.Name
    if (-not $title) { continue }
    if ($title.ToLower().Contains($titleLower)) {
        if ($title -match 'Captions') { $target = $w; $strategy = "title:captions"; break }
        if (-not $target) { $target = $w; $strategy = "title" }
    }
}

# 2. Teams fallback: find the app by process, then by title. Only when asked, so
#    "Pick a window..." never silently reads something the user did not choose.
if (-not $target -and $TeamsMode) {
    $teamsPids = @()
    foreach ($p in $TeamsProcess) {
        $teamsPids += (Get-Process -Name $p -ErrorAction SilentlyContinue |
                       Where-Object { $_.MainWindowHandle -ne 0 }).Id
    }
    foreach ($w in $windows) {
        try { $wpid = $w.Current.ProcessId } catch { continue }
        if ($teamsPids -contains $wpid) { $target = $w; $strategy = "process"; break }
    }
    if (-not $target) {
        foreach ($w in $windows) {
            $title = "$($w.Current.Name)"
            if ($title -and $title.ToLower().Contains('microsoft teams')) {
                $target = $w; $strategy = "title:teams"; break
            }
        }
    }
}

if (-not $target) {
    if ($TeamsMode) {
        Write-Error "Teams doesn't appear to be running with a visible window. Open your meeting, then start capturing."
    } else {
        Write-Error "No window found whose title contains '$WindowTitle'. Make sure that window is open and visible."
    }
    exit 1
}

# 3. Narrow to the captions pane inside the window when it is exposed. Without this a
#    Teams capture is the whole app: sidebar, chat list, timestamps and all.
$scope = $target
$region = Find-CaptionsRegion -Window $target
if ($region) {
    $scope = $region
    $strategy += "+captions-pane"
}

if ([string]::IsNullOrWhiteSpace($OutPath)) {
    $OutPath = Join-Path $PSScriptRoot 'teams_extracted_raw.txt'
}

# ---------------------------------------------------------------------------
# -Collect: scroll the pane top to bottom, reading at every step
# ---------------------------------------------------------------------------
if ($Collect) {
    if (-not $region) {
        Emit @{ type = "error"; message = ("Cue found Teams but no transcript pane inside it. Open the " +
            "recording, click the Transcript tab so the text is showing, then try again.") }
        exit 1
    }

    # Wheel input goes to whatever is under the cursor, so Teams has to be in front.
    try {
        $hwnd = [IntPtr]$target.Current.NativeWindowHandle
        $null = $Win32::ShowWindow($hwnd, 9)          # SW_RESTORE
        $null = $Win32::SetForegroundWindow($hwnd)
        Start-Sleep -Milliseconds 400
    } catch {}

    $textCond = New-Object System.Windows.Automation.PropertyCondition(
        [System.Windows.Automation.AutomationElement]::ControlTypeProperty,
        [System.Windows.Automation.ControlType]::Text)
    # Entry header: "Name 0 minutes 03 seconds", optionally prefixed by the [type] tag.
    $HDR = [regex]'^(?:\[[^\]]+\]\s*)?(?<name>[A-Z][^\d]{1,60}?)\s*(?:(?<h>\d+)\s*hours?\s*)?(?<m>\d+)\s*minutes?\s*(?<s>\d+)\s*seconds?\.?$'

    function Wheel($el, [int]$notches) {
        # Negative notches scroll down.
        $r = $el.Current.BoundingRectangle
        [System.Windows.Forms.Cursor]::Position = New-Object System.Drawing.Point([int]($r.X + $r.Width / 2), [int]($r.Y + $r.Height / 2))
        $delta = if ($notches -lt 0) { -120 } else { 120 }
        for ($i = 0; $i -lt [math]::Abs($notches); $i++) { $Win32::mouse_event(0x0800, 0, 0, $delta, 0); Start-Sleep -Milliseconds 40 }
    }
    function Keys([System.Windows.Automation.AutomationElement]$el, [string]$keys) {
        try { $el.SetFocus() } catch {}
        Start-Sleep -Milliseconds 120
        [System.Windows.Forms.SendKeys]::SendWait($keys)
    }
    function HeaderKeys([string[]]$lines) {
        $keys = New-Object 'System.Collections.Generic.HashSet[string]'
        foreach ($l in $lines) {
            $m = $HDR.Match($l)
            if (-not $m.Success) { continue }
            $h = 0; if ($m.Groups['h'].Success) { $h = [int]$m.Groups['h'].Value }
            $secs = $h * 3600 + [int]$m.Groups['m'].Value * 60 + [int]$m.Groups['s'].Value
            $null = $keys.Add("$($m.Groups['name'].Value.Trim().ToLower())|$secs")
        }
        return $keys
    }

    # Start from the top, two ways, because either alone can fail to take.
    Wheel $region 60; Start-Sleep -Milliseconds $SettleMs
    Keys $region "^{HOME}"; Start-Sleep -Milliseconds $SettleMs

    $seen = New-Object 'System.Collections.Generic.HashSet[string]'
    $all = New-Object 'System.Collections.Generic.List[string]'
    $stall = 0; $method = "wheel"; $maxSec = 0; $p = 0
    for ($p = 1; $p -le $MaxPasses; $p++) {
        # Elements go stale as the list virtualizes; re-find the pane every pass.
        $scopeNow = Find-CaptionsRegion -Window $target
        if (-not $scopeNow) { $scopeNow = $region }
        try { $lines = Get-AllText -Root $scopeNow } catch { $lines = @() }

        $all.Add("--- pass $p ---") | Out-Null
        foreach ($l in $lines) { $all.Add($l) | Out-Null }

        $new = 0
        foreach ($k in HeaderKeys $lines) {
            if ($seen.Add($k)) { $new++; $s = [int]($k.Split('|')[1]); if ($s -gt $maxSec) { $maxSec = $s } }
        }
        Emit @{ type = "progress"; pass = $p; entries = $seen.Count; new = $new; max_seconds = $maxSec; method = $method }

        if ($new -eq 0) { $stall++ } else { $stall = 0 }
        if ($stall -ge 4) { break }
        # Escalate when the current method stops turning up entries. Reaching the true
        # end looks the same as being unable to scroll, so try each before giving up.
        if ($stall -eq 1 -and $method -eq "wheel") { $method = "scrollintoview" }
        elseif ($stall -eq 2 -and $method -eq "scrollintoview") { $method = "pagedown" }

        switch ($method) {
            "wheel" { Wheel $scopeNow (-$WheelNotches) }
            "scrollintoview" {
                $texts = $scopeNow.FindAll([System.Windows.Automation.TreeScope]::Descendants, $textCond)
                $moved = $false
                if ($texts.Count -gt 0) {
                    try {
                        $sip = $texts[$texts.Count - 1].GetCurrentPattern([System.Windows.Automation.ScrollItemPattern]::Pattern)
                        $sip.ScrollIntoView(); $moved = $true
                    } catch {}
                }
                if (-not $moved) { Wheel $scopeNow (-$WheelNotches) }
            }
            "pagedown" { Keys $scopeNow "{PGDN}" }
        }
        Start-Sleep -Milliseconds $SettleMs
    }

    $all -join "`n" | Out-File -FilePath $OutPath -Encoding utf8
    Emit @{ type = "done"; passes = $p; entries = $seen.Count; max_seconds = $maxSec; path = $OutPath }
    exit 0
}

# ---------------------------------------------------------------------------
# Single read (live caption polling)
# ---------------------------------------------------------------------------
Write-Host "Found window: $($target.Current.Name) [$strategy]" -ForegroundColor Cyan
Write-Host "Walking accessibility tree (this can take a few seconds)..." -ForegroundColor Cyan

$lines = Get-AllText -Root $scope
Write-Host "Captured $($lines.Count) text elements." -ForegroundColor Green

$lines -join "`n" | Out-File -FilePath $OutPath -Encoding utf8
Write-Host "Saved raw extract to: $OutPath" -ForegroundColor Green
