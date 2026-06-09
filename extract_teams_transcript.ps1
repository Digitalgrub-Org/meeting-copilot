# Extract text from the Teams "Captions" window via Windows UI Automation.
# Targets the window whose title contains "Captions" (the transcript pane).
# Usage:  powershell -ExecutionPolicy Bypass -File extract_teams_transcript.ps1 [-OutPath <file>]
param(
    [string]$OutPath = "",
    # Substring of the target window's title to read text from.
    # Default "Captions" preserves the original Teams-captions behavior.
    [string]$WindowTitle = "Captions"
)

Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes

$ErrorActionPreference = 'Stop'

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

# Find the Teams Captions window
$root = [System.Windows.Automation.AutomationElement]::RootElement
$cond = New-Object System.Windows.Automation.PropertyCondition(
    [System.Windows.Automation.AutomationElement]::ControlTypeProperty,
    [System.Windows.Automation.ControlType]::Window)
$windows = $root.FindAll([System.Windows.Automation.TreeScope]::Children, $cond)

$target = $null
$titleLower = $WindowTitle.ToLower()
foreach ($w in $windows) {
    $title = $w.Current.Name
    if (-not $title) { continue }
    if ($title.ToLower().Contains($titleLower)) {
        # Prefer an exact-ish caption window over a generic app shell.
        if ($title -match 'Captions') { $target = $w; break }
        if (-not $target) { $target = $w }
    }
}

if (-not $target) {
    Write-Error "No window found whose title contains '$WindowTitle'. Make sure that window is open and visible."
    exit 1
}

Write-Host "Found window: $($target.Current.Name)" -ForegroundColor Cyan
Write-Host "Walking accessibility tree (this can take a few seconds)..." -ForegroundColor Cyan

$lines = Get-AllText -Root $target
Write-Host "Captured $($lines.Count) text elements." -ForegroundColor Green

if ([string]::IsNullOrWhiteSpace($OutPath)) {
    $OutPath = Join-Path $PSScriptRoot 'teams_extracted_raw.txt'
}
$lines -join "`n" | Out-File -FilePath $OutPath -Encoding utf8
Write-Host "Saved raw extract to: $OutPath" -ForegroundColor Green
