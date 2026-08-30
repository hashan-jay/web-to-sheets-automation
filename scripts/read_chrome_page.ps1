Add-Type -AssemblyName UIAutomationClient
$root = [System.Windows.Automation.AutomationElement]::RootElement
$wins = $root.FindAll(
    [System.Windows.Automation.TreeScope]::Children,
    [System.Windows.Automation.Condition]::TrueCondition
)
foreach ($w in $wins) {
    $n = $w.Current.Name
    if ($n -notmatch "Admin - Google Chrome") { continue }
    $docCondition = New-Object System.Windows.Automation.PropertyCondition(
        [System.Windows.Automation.AutomationElement]::ControlTypeProperty,
        [System.Windows.Automation.ControlType]::Document
    )
    $docs = $w.FindAll([System.Windows.Automation.TreeScope]::Descendants, $docCondition)
    foreach ($d in $docs) {
        Write-Output ("DOC:" + $d.Current.Name)
        try {
            $tp = $d.GetCurrentPattern([System.Windows.Automation.TextPattern]::Pattern)
            $text = $tp.DocumentRange.GetText(20000)
            if ($text) { Write-Output "TEXTSTART"; Write-Output $text; Write-Output "TEXTEND" }
        } catch {
            Write-Output ("DOC_NO_TEXT:" + $_.Exception.Message)
        }
    }
    $texts = $w.FindAll(
        [System.Windows.Automation.TreeScope]::Descendants,
        (New-Object System.Windows.Automation.PropertyCondition(
            [System.Windows.Automation.AutomationElement]::ControlTypeProperty,
            [System.Windows.Automation.ControlType]::Text
        ))
    )
    $count = 0
    foreach ($t in $texts) {
        $name = $t.Current.Name
        if ($name) {
            Write-Output ("T:" + $name)
            $count++
            if ($count -ge 80) { break }
        }
    }
}
