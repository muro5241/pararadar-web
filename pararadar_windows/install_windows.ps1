param(
  [string]$ProjectPath = 'C:\Users\murat\OneDrive\Masaüstü\kripto_borsa_bot',
  [string]$DailyTime = '10:00'
)
$ErrorActionPreference = 'Stop'
$Source = $PSScriptRoot
if (-not (Test-Path -LiteralPath $ProjectPath -PathType Container)) { throw "Proje klasörü bulunamadı: $ProjectPath" }
$Stamp = Get-Date -Format 'yyyyMMdd_HHmmss_fff'
$Backup = Join-Path $ProjectPath "pararadar_yedek\$Stamp"
New-Item -ItemType Directory -Path $Backup -Force | Out-Null
foreach ($Name in @('main.py','pararadar_hizli.py')) {
  $Old = Join-Path $ProjectPath $Name
  if (Test-Path -LiteralPath $Old) { Copy-Item -LiteralPath $Old -Destination $Backup }
}
$Target = Join-Path $ProjectPath 'pararadar_service'
if (Test-Path -LiteralPath $Target) {
  $PreviousCode = Join-Path $Backup 'pararadar_service'
  New-Item -ItemType Directory -Path $PreviousCode -Force | Out-Null
  Get-ChildItem -LiteralPath $Target -File | Copy-Item -Destination $PreviousCode
}
foreach ($Name in @('ParaRadar-Worker','ParaRadar-Daily10')) {
  if (Get-ScheduledTask -TaskName $Name -ErrorAction SilentlyContinue) {
    Stop-ScheduledTask -TaskName $Name
    Disable-ScheduledTask -TaskName $Name | Out-Null
  }
}
New-Item -ItemType Directory -Path $Target -Force | Out-Null
foreach ($Name in @('pararadar.py','dashboard.html','requirements.txt','.env.example','.gitignore','README_TR.md','stop_windows.ps1','test_regression.py','configure_key.py','test_key_discovery.py')) {
  Copy-Item -LiteralPath (Join-Path $Source $Name) -Destination $Target -Force
}
$Venv = Join-Path $Target '.venv'
if (-not (Test-Path (Join-Path $Venv 'Scripts\python.exe'))) {
  if (Get-Command py -ErrorAction SilentlyContinue) { & py -3 -m venv $Venv }
  elseif (Get-Command python -ErrorAction SilentlyContinue) { & python -m venv $Venv }
  else { throw 'Python 3.11 veya üzeri kurulu olmalı.' }
  if ($LASTEXITCODE -ne 0) { throw 'Python sanal ortamı oluşturulamadı' }
}
$Python = Join-Path $Venv 'Scripts\python.exe'
& $Python -c "import sys;assert sys.version_info >= (3,11), 'Python 3.11 veya üzeri gerekli'"
if ($LASTEXITCODE -ne 0) { throw 'Python sürümü uygun değil' }
& $Python -m pip install -r (Join-Path $Target 'requirements.txt')
if ($LASTEXITCODE -ne 0) { throw 'Python bağımlılıkları kurulamadı' }
$EnvFile = Join-Path $Target '.env'
if (-not (Test-Path -LiteralPath $EnvFile)) {
  Copy-Item -LiteralPath (Join-Path $Target '.env.example') -Destination $EnvFile
  # Preserve existing credentials without displaying or copying unrelated variables.
  $ExistingEnv = Join-Path $ProjectPath '.env'
  if (Test-Path -LiteralPath $ExistingEnv) {
    $Bindings = Get-Content -LiteralPath $ExistingEnv | Where-Object { $_ -match '^\s*(NVIDIA_API_KEY|ESPEAK_BIN|PARARADAR_FONT)\s*=' }
    if ($Bindings) { Add-Content -LiteralPath $EnvFile -Value $Bindings -Encoding utf8 }
  }
}
# Search only the user's selected project and desktop folders; never print keys.
$KeyArgs = @((Join-Path $Target 'configure_key.py'), '--env-file', $EnvFile, '--folder', $ProjectPath)
$DesktopFolders = @([Environment]::GetFolderPath('Desktop'), (Split-Path $ProjectPath -Parent))
foreach ($Root in @($env:OneDrive, $env:OneDriveConsumer, $env:USERPROFILE)) {
  if ($Root) { $DesktopFolders += (Join-Path $Root 'Desktop'); $DesktopFolders += (Join-Path $Root 'Masaüstü') }
}
foreach ($Folder in ($DesktopFolders | Where-Object { $_ } | Select-Object -Unique)) {
  $KeyArgs += @('--folder', $Folder)
}
& $Python @KeyArgs
if ($LASTEXITCODE -ne 0) { throw 'Yerel NVIDIA anahtarı seçilemedi; otomatik görevler başlatılmadı.' }
$Tools = Join-Path $Target 'tools'
New-Item -ItemType Directory -Path $Tools -Force | Out-Null
# Fetch from the upstream release repositories, without browser automation.
if (-not (Get-Command ffmpeg -ErrorAction SilentlyContinue)) {
  $FfmpegRoot = Join-Path $Tools 'ffmpeg'
  if (-not (Get-ChildItem -LiteralPath $FfmpegRoot -Filter ffmpeg.exe -Recurse -ErrorAction SilentlyContinue)) {
    $FfmpegZip = Join-Path $Tools 'ffmpeg.zip'
    Invoke-WebRequest -Uri 'https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-win64-gpl.zip' -OutFile $FfmpegZip -UseBasicParsing
    Expand-Archive -LiteralPath $FfmpegZip -DestinationPath $FfmpegRoot -Force
  }
  $FfmpegExe = Get-ChildItem -LiteralPath $FfmpegRoot -Filter ffmpeg.exe -Recurse | Select-Object -First 1
  if (-not $FfmpegExe) { throw 'FFmpeg çıkarılamadı' }
  $FfmpegDir = $FfmpegExe.DirectoryName
} else { $FfmpegDir = Split-Path (Get-Command ffmpeg).Source -Parent }
$env:PATH = $FfmpegDir + ';' + $env:PATH
Add-Content -LiteralPath $EnvFile -Value ("FFMPEG_DIR='" + $FfmpegDir.Replace('\','/') + "'") -Encoding utf8
$EspeakCommand = Get-Command espeak-ng -ErrorAction SilentlyContinue
if ($EspeakCommand) { $EspeakExePath = $EspeakCommand.Source }
else {
  $EspeakRoot = Join-Path $Tools 'espeak'
  $EspeakExe = Get-ChildItem -LiteralPath $EspeakRoot -Filter espeak-ng.exe -Recurse -ErrorAction SilentlyContinue | Select-Object -First 1
  if (-not $EspeakExe) {
    $Msi = Join-Path $Tools 'espeak-ng.msi'
    Invoke-WebRequest -Uri 'https://github.com/espeak-ng/espeak-ng/releases/download/1.52.0/espeak-ng.msi' -OutFile $Msi -UseBasicParsing
    $Extraction = Start-Process -FilePath 'msiexec.exe' -ArgumentList ('/a "' + $Msi + '" /qn TARGETDIR="' + $EspeakRoot + '"') -Wait -PassThru
    if ($Extraction.ExitCode -notin @(0,3010)) { throw 'eSpeak NG paketi çıkarılamadı' }
    $EspeakExe = Get-ChildItem -LiteralPath $EspeakRoot -Filter espeak-ng.exe -Recurse | Select-Object -First 1
  }
  if (-not $EspeakExe) { throw 'Türkçe ses motoru bulunamadı' }
  $EspeakExePath = $EspeakExe.FullName
}
Add-Content -LiteralPath $EnvFile -Value ("ESPEAK_BIN='" + $EspeakExePath.Replace('\','/') + "'") -Encoding utf8
$EspeakData = Get-ChildItem -LiteralPath (Split-Path $EspeakExePath -Parent) -Directory -Filter 'espeak-ng-data' -Recurse -ErrorAction SilentlyContinue | Select-Object -First 1
if (-not $EspeakData -and (Test-Path (Join-Path $Tools 'espeak'))) {
  $EspeakData = Get-ChildItem -LiteralPath (Join-Path $Tools 'espeak') -Directory -Filter 'espeak-ng-data' -Recurse | Select-Object -First 1
}
if ($EspeakData) {
  $DataParent = Split-Path $EspeakData.FullName -Parent
  Add-Content -LiteralPath $EnvFile -Value ("ESPEAK_DATA='" + $DataParent.Replace('\','/') + "'") -Encoding utf8
}
# Check real dependencies and AI availability before registering automatic jobs.
& $Python -c "import sys;sys.path.insert(0,sys.argv[1]);import pararadar;pararadar.dependencies();assert pararadar.os.getenv('NVIDIA_API_KEY'), 'NVIDIA_API_KEY eksik: pararadar_service/.env dosyasına ekleyin'" $Target
if ($LASTEXITCODE -ne 0) { throw 'FFmpeg, eSpeak NG veya NVIDIA anahtarı eksik. Görevler kaydedilmedi.' }
& $Python (Join-Path $Target 'pararadar.py') run --count 1
if ($LASTEXITCODE -ne 0) { throw 'İlk video testi başarısız; arka plan görevleri etkinleştirilmedi. data/worker.log dosyasını kontrol edin.' }
$WorkerTask = 'ParaRadar-Worker'
$DailyTask = 'ParaRadar-Daily10'
$Identity = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
$Principal = New-ScheduledTaskPrincipal -UserId $Identity -LogonType Interactive -RunLevel Limited
$Settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) -ExecutionTimeLimit ([TimeSpan]::Zero)
$WorkerAction = New-ScheduledTaskAction -Execute (Join-Path $Venv 'Scripts\pythonw.exe') -Argument ('"' + (Join-Path $Target 'pararadar.py') + '" serve') -WorkingDirectory $Target
$WorkerTrigger = New-ScheduledTaskTrigger -AtLogOn -User $Identity
Register-ScheduledTask -TaskName $WorkerTask -Action $WorkerAction -Trigger $WorkerTrigger -Settings $Settings -Principal $Principal -Force | Out-Null
$DailyAction = New-ScheduledTaskAction -Execute (Join-Path $Venv 'Scripts\pythonw.exe') -Argument ('"' + (Join-Path $Target 'pararadar.py') + '" enqueue --count 10') -WorkingDirectory $Target
$DailyTrigger = New-ScheduledTaskTrigger -Daily -At $DailyTime
Register-ScheduledTask -TaskName $DailyTask -Action $DailyAction -Trigger $DailyTrigger -Settings $Settings -Principal $Principal -Force | Out-Null
Start-ScheduledTask -TaskName $WorkerTask
& $Python (Join-Path $Target 'pararadar.py') enqueue --count 10
if ($LASTEXITCODE -ne 0) { throw 'İlk 10 video kuyruğa eklenemedi' }
$Ready = $false
for ($Attempt = 0; $Attempt -lt 20; $Attempt++) {
  try {
    Invoke-WebRequest -Uri 'http://127.0.0.1:8765/api/status' -UseBasicParsing -TimeoutSec 2 | Out-Null
    $Ready = $true
    break
  } catch { Start-Sleep -Milliseconds 500 }
}
if (-not $Ready) { throw 'Görev kaydedildi ancak panel açılmadı; Windows Görev Zamanlayıcı ve data/worker.log dosyasını kontrol edin.' }
Start-Process 'http://127.0.0.1:8765'
Write-Host "Kurulum tamamlandı. Yedek: $Backup"
Write-Host "Panel: http://127.0.0.1:8765 | Günlük üretim: $DailyTime (Windows yerel saati)"
Write-Host "Videolar: $Target\data\videos | Kayıt: $Target\data\worker.log"
Write-Host 'Windows oturumu açık kalmalı. Uyku/kapalı bilgisayarda üretim çalışmaz.'
