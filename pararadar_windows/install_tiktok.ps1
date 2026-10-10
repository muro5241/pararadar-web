param([string]$ProjectPath = 'C:\Users\murat\OneDrive\Masaüstü\kripto_borsa_bot')
$ErrorActionPreference = 'Stop'
$Target = Join-Path $ProjectPath 'pararadar_service'
$Python = Join-Path $Target '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $Python)) { throw 'Existing ParaRadar installation not found. Production was not changed.' }
$Stamp = Get-Date -Format 'yyyyMMdd_HHmmss_fff'
$Backup = Join-Path $ProjectPath "pararadar_yedek\tiktok_$Stamp"
New-Item -ItemType Directory -Path $Backup -Force | Out-Null
foreach ($Name in @('tiktok_windows.py','tiktok_backend')) {
  $Previous = Join-Path $Target $Name
  if (Test-Path -LiteralPath $Previous) { Copy-Item -LiteralPath $Previous -Destination $Backup -Recurse }
}
# A separate environment avoids upgrading any video-production dependencies.
$TikTokVenv = Join-Path $Target '.tiktok-venv'
if (-not (Test-Path (Join-Path $TikTokVenv 'Scripts\python.exe'))) {
  & $Python -m venv $TikTokVenv
  if ($LASTEXITCODE -ne 0) { throw 'TikTok environment creation failed' }
}
Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'tiktok_windows.py') -Destination $Target -Force
$BackendTarget = Join-Path $Target 'tiktok_backend'
New-Item -ItemType Directory -Path $BackendTarget -Force | Out-Null
Copy-Item -Path (Join-Path $PSScriptRoot 'tiktok_backend\*') -Destination $BackendTarget -Recurse -Force
$TikTokPython = Join-Path $TikTokVenv 'Scripts\python.exe'
& $TikTokPython -m pip install -r (Join-Path $BackendTarget 'requirements.txt')
if ($LASTEXITCODE -ne 0) { throw 'TikTok dependency installation failed; production environment unchanged' }
& $TikTokPython (Join-Path $Target 'tiktok_windows.py') configure
if ($LASTEXITCODE -ne 0) { throw 'TikTok setup incomplete; publishing remains disabled' }
# Only this separate task is touched. Existing Worker/Daily10 tasks are preserved.
$TaskName = 'ParaRadar-TikTok'
if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) { Stop-ScheduledTask -TaskName $TaskName }
$Identity = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
$Principal = New-ScheduledTaskPrincipal -UserId $Identity -LogonType Interactive -RunLevel Limited
$Action = New-ScheduledTaskAction -Execute (Join-Path $TikTokVenv 'Scripts\pythonw.exe') -Argument ('"' + (Join-Path $Target 'tiktok_windows.py') + '" serve') -WorkingDirectory $Target
$Trigger = New-ScheduledTaskTrigger -AtLogOn -User $Identity
$Settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew -ExecutionTimeLimit ([TimeSpan]::Zero)
Register-ScheduledTask -TaskName $TaskName -Action $Action -Trigger $Trigger -Principal $Principal -Settings $Settings -Force | Out-Null
Start-ScheduledTask -TaskName $TaskName
Write-Host 'TikTok panel: http://127.0.0.1:8766/ | Click Connect, preview one MP4 and select Only Me.'
Write-Host 'No video is posted without app approval, OAuth video.publish and explicit consent.'
