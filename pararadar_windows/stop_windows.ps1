$ErrorActionPreference = 'Stop'
foreach ($Name in @('ParaRadar-Worker','ParaRadar-Daily10')) {
  if (Get-ScheduledTask -TaskName $Name -ErrorAction SilentlyContinue) {
    Stop-ScheduledTask -TaskName $Name
    Disable-ScheduledTask -TaskName $Name | Out-Null
  }
}
Write-Host 'ParaRadar görevleri durduruldu ve devre dışı bırakıldı. Videolar korundu.'
