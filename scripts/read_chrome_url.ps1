Add-Type -AssemblyName UIAutomationClient
$root = [System.Windows.Automation.AutomationElement]::RootElement
$wins = $root.FindAll(
    [System.Windows.Automation.TreeScope]::Children,
    [System.Windows.Automation.Condition]::TrueCondition
)
foreach ($w in $wins) {
    $n = $w.Current.Name
    if ($n -notmatch "Chrome|Edge") { continue }
    Write-Output "WINDOW:$n"
    $editCondition = New-Object System.Windows.Automation.PropertyCondition(
        [System.Windows.Automation.AutomationElement]::ControlTypeProperty,
        [System.Windows.Automation.ControlType]::Edit
    )
    $edits = $w.FindAll([System.Windows.Automation.TreeScope]::Descendants, $editCondition)
    foreach ($e in $edits) {
        try {
            $val = $e.GetCurrentPropertyValue(
                [System.Windows.Automation.ValuePattern]::ValueProperty
            )
            if ($val) {
                Write-Output ("EDIT:" + $e.Current.Name + " => " + $val)
            }
        } catch {
        }
    }
}
