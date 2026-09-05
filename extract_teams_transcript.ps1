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
    [string[]]$TeamsProcess = @('ms-teams', 'Teams')
)

Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes

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

        $child = $walker.GetFirstChild($el)
        while ($child) {
            $stack.Push($child)
            $child = $walker.GetNextSibling($child)
        }
    }

    return $results
}

function Find-CaptionsRegion {
    <#
      Narrow a meeting window down to just its captions pane, when one is exposed.
      Returns $null if nothing looks like captions, so the caller falls back to the
      whole window and lets downstream filtering deal with the sidebar.
    #>
    param([System.Windows.Automation.AutomationElement]$Window)

    try {
        $all = $Window.FindAll(
            [System.Windows.Automation.TreeScope]::Descendants,
            (New-Object System.Windows.Automation.PropertyCondition(
                [System.Windows.Automation.AutomationElement]::IsControlElementProperty, $true)))
    } catch {
        return $null
    }

    foreach ($el in $all) {
        try {
            $name = "$($el.Current.Name)".ToLower()
            $autoId = "$($el.Current.AutomationId)".ToLower()
        } catch { continue }
        foreach ($hint in $CaptionHints) {
            if ($name -eq $hint -or $autoId.Contains($hint) -or
                ($name.Contains($hint) -and $name.Length -lt 40)) {
                return $el
            }
        }
    }
    return $null
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

Write-Host "Found window: $($target.Current.Name) [$strategy]" -ForegroundColor Cyan
Write-Host "Walking accessibility tree (this can take a few seconds)..." -ForegroundColor Cyan

$lines = Get-AllText -Root $scope
Write-Host "Captured $($lines.Count) text elements." -ForegroundColor Green

if ([string]::IsNullOrWhiteSpace($OutPath)) {
    $OutPath = Join-Path $PSScriptRoot 'teams_extracted_raw.txt'
}
$lines -join "`n" | Out-File -FilePath $OutPath -Encoding utf8
Write-Host "Saved raw extract to: $OutPath" -ForegroundColor Green
