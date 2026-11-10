param(
    [ValidatePattern('^[A-Za-z0-9_-]{1,64}$')][string]$AccountName = 'main',
    [ValidatePattern('^([01]\d|2[0-3]):[0-5]\d$')][string]$At = '09:17'
)
$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
$pythonPath = Join-Path $repoRoot '.venv\Scripts\python.exe'
$pythonwPath = Join-Path $repoRoot '.venv\Scripts\pythonw.exe'
if (-not (Test-Path -LiteralPath $pythonPath)) { throw 'Install dependencies first: uv sync' }
$statusJson = & $pythonPath (Join-Path $repoRoot 'agentrouter_github.py') status --name $AccountName
if ($LASTEXITCODE -ne 0 -or -not ($statusJson | ConvertFrom-Json).initialized) {
    throw 'Complete bootstrap before installing the scheduled task.'
}
$taskName = "AgentRouter-GitHub-$AccountName"
$runnerPath = Join-Path $PSScriptRoot 'run_github_checkin.py'
$arguments = '"{0}" "{1}"' -f $runnerPath, $AccountName
$existingTask = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
if ($existingTask -and ($existingTask.Actions.Arguments -notcontains $arguments)) {
    throw "An unrelated task already uses $taskName; it was not changed."
}
$identity = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
$action = New-ScheduledTaskAction -Execute $pythonwPath -Argument $arguments -WorkingDirectory $repoRoot
$daily = New-ScheduledTaskTrigger -Daily -At $At
$logon = New-ScheduledTaskTrigger -AtLogOn -User $identity
$principal = New-ScheduledTaskPrincipal -UserId $identity -LogonType Interactive -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 6) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
Register-ScheduledTask -TaskName $taskName -Action $action -Trigger @($daily, $logon) `
    -Principal $principal -Settings $settings -Description 'AgentRouter daily GitHub OAuth login; local profile only.' `
    -Force | Select-Object TaskName, State
Get-ScheduledTaskInfo -TaskName $taskName | Select-Object NextRunTime, LastTaskResult
