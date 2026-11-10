# Stage 0.6 probe helper (Windows PowerShell 5.1): finds the microphone permission prompt of an
# Edge window through UI Automation, saves a screenshot of that window and presses the button that
# grants the permission persistently. Used only by scripts/probe_edge.py, never by the app.
param(
    [int]$TimeoutSec = 15,
    [string]$Screenshot = ''
)
Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes
Add-Type -AssemblyName System.Drawing

$root = [System.Windows.Automation.AutomationElement]::RootElement
$patterns = @('Allow while visiting*', 'Разрешить при посещении*', 'Allow', 'Разрешить')
$deadline = (Get-Date).AddSeconds($TimeoutSec)
$buttonType = [System.Windows.Automation.ControlType]::Button
$cond = New-Object System.Windows.Automation.PropertyCondition(
    [System.Windows.Automation.AutomationElement]::ControlTypeProperty, $buttonType)

while ((Get-Date) -lt $deadline) {
    $edgeWindows = $root.FindAll([System.Windows.Automation.TreeScope]::Children,
        [System.Windows.Automation.Condition]::TrueCondition) | Where-Object {
        try { (Get-Process -Id $_.Current.ProcessId).ProcessName -eq 'msedge' } catch { $false }
    }
    foreach ($win in $edgeWindows) {
        $buttons = $win.FindAll([System.Windows.Automation.TreeScope]::Descendants, $cond)
        foreach ($p in $patterns) {
            $btn = $buttons | Where-Object { $_.Current.Name -like $p } | Select-Object -First 1
            if ($btn) {
                if ($Screenshot) {
                    $r = $win.Current.BoundingRectangle
                    $bmp = New-Object System.Drawing.Bitmap([int]$r.Width, [int]$r.Height)
                    $g = [System.Drawing.Graphics]::FromImage($bmp)
                    $g.CopyFromScreen([int]$r.X, [int]$r.Y, 0, 0, $bmp.Size)
                    $bmp.Save($Screenshot)
                    $g.Dispose(); $bmp.Dispose()
                }
                $names = ($buttons | ForEach-Object { $_.Current.Name }) -join ' | '
                $inv = $btn.GetCurrentPattern([System.Windows.Automation.InvokePattern]::Pattern)
                $inv.Invoke()
                Write-Output "clicked: $($btn.Current.Name); window: $($win.Current.Name); buttons: $names"
                exit 0
            }
        }
    }
    Start-Sleep -Milliseconds 300
}
Write-Output 'prompt not found'
exit 1
