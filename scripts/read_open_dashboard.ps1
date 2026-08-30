Add-Type -AssemblyName UIAutomationClient

$root = [System.Windows.Automation.AutomationElement]::RootElement
$wins = $root.FindAll(
    [System.Windows.Automation.TreeScope]::Children,
    [System.Windows.Automation.Condition]::TrueCondition
)

$url = ""
$title = ""
$text = ""

foreach ($w in $wins) {
    $name = $w.Current.Name
    if ($name -notmatch "Chrome|Edge|Brave") { continue }

    $editCondition = New-Object System.Windows.Automation.PropertyCondition(
        [System.Windows.Automation.AutomationElement]::ControlTypeProperty,
        [System.Windows.Automation.ControlType]::Edit
    )
    $windowUrl = ""
    foreach ($edit in $w.FindAll([System.Windows.Automation.TreeScope]::Descendants, $editCondition)) {
        try {
            $val = [string]$edit.GetCurrentPropertyValue(
                [System.Windows.Automation.ValuePattern]::ValueProperty
            )
            if ($val -match "transaction|admin|as6868|/\#") {
                $windowUrl = $val
                break
            }
            if (-not $windowUrl -and $val -match "\.") {
                $windowUrl = $val
            }
        } catch {
        }
    }

    $docCondition = New-Object System.Windows.Automation.PropertyCondition(
        [System.Windows.Automation.AutomationElement]::ControlTypeProperty,
        [System.Windows.Automation.ControlType]::Document
    )
    $bestText = ""
    foreach ($doc in $w.FindAll([System.Windows.Automation.TreeScope]::Descendants, $docCondition)) {
        $docName = $doc.Current.Name
        if ($docName -match "DevTools") { continue }
        try {
            $pattern = $doc.GetCurrentPattern([System.Windows.Automation.TextPattern]::Pattern)
            $docText = $pattern.DocumentRange.GetText(80000)
            if ($docText -match "Username:" -and $docText.Length -gt $bestText.Length) {
                $bestText = $docText
            }
        } catch {
        }
    }

    $isAdmin = ($name -match "Admin") -or ($windowUrl -match "transaction") -or ($bestText -match "Username:")
    if ($isAdmin -and $bestText) {
        $url = $windowUrl
        $title = $name
        $text = $bestText
        break
    }
}

if ($url -and $url -notmatch "^https?://") {
    $url = "https://" + $url
}

[pscustomobject]@{
    url   = $url
    title = $title
    text  = $text
} | ConvertTo-Json -Compress -Depth 5
