# ===========================================================================
# EnerjiOne Grid - Update (PowerShell)
# ===========================================================================
# Yeni image versiyonuna gec. Once postgres backup alir, sonra pull + up.
#
# Kullanim:
#   .\update.ps1                  # Mevcut E1_VERSION ile yeniden pull
#   .\update.ps1 -Version 0.5.0   # Belirli versiyona gec
# ===========================================================================

[CmdletBinding()]
param(
    [string]$InstallDir = "C:\enerjione",
    [string]$Version = ""
)

$ErrorActionPreference = 'Stop'

function Write-Step($msg) {
    Write-Host ""
    Write-Host "==> $msg" -ForegroundColor Cyan
}
function Write-Ok($msg) { Write-Host "  [OK] $msg" -ForegroundColor Green }
function Write-Warn2($msg) { Write-Host "  [WARN] $msg" -ForegroundColor Yellow }

if (-not (Test-Path $InstallDir)) {
    Write-Host "[HATA] InstallDir bulunamadi: $InstallDir" -ForegroundColor Red
    exit 1
}
Set-Location $InstallDir

# ---- Pre-update backup ----
Write-Step "Pre-update Postgres backup"
$backupDir = Join-Path $InstallDir "backups"
if (-not (Test-Path $backupDir)) {
    New-Item -ItemType Directory -Path $backupDir -Force | Out-Null
}
$ts = (Get-Date).ToString("yyyyMMdd-HHmmss")
$backupFile = Join-Path $backupDir "auto-pre-update-$ts.dump"

try {
    & docker compose exec -T postgres pg_dump -U enerjione -d enerjione -F c -f /tmp/pre-update.dump
    if ($LASTEXITCODE -ne 0) { throw "pg_dump basarisiz" }
    & docker compose cp postgres:/tmp/pre-update.dump $backupFile
    & docker compose exec -T postgres rm /tmp/pre-update.dump
    Write-Ok "Backup: $backupFile"
} catch {
    Write-Warn2 "Backup alinamadi: $_"
    Write-Host "Devam etmek icin Enter, iptal icin Ctrl-C..."
    Read-Host | Out-Null
}

# ---- Version guncelleme ----
if ($Version) {
    Write-Step ".env'de E1_VERSION guncelleniyor: $Version"
    $envPath = Join-Path $InstallDir ".env"
    $content = Get-Content $envPath -Raw -Encoding utf8
    $content = [regex]::Replace($content, 'E1_VERSION=[^\r\n]*', "E1_VERSION=$Version")
    Set-Content -Path $envPath -Value $content -Encoding utf8 -NoNewline
    Write-Ok "E1_VERSION=$Version"
}

# ---- Pull + up ----
Write-Step "Yeni image'lar cekiliyor"
& docker compose pull
if ($LASTEXITCODE -ne 0) {
    Write-Host "[HATA] docker compose pull basarisiz" -ForegroundColor Red
    exit 1
}
Write-Ok "Image'lar cekildi"

Write-Step "Servisler yeniden baslatiliyor"
& docker compose up -d
if ($LASTEXITCODE -ne 0) {
    Write-Host "[HATA] docker compose up basarisiz" -ForegroundColor Red
    Write-Host "Rollback icin: .env'de E1_VERSION'i eski degere alip tekrar calistirin"
    exit 1
}
Write-Ok "Update tamamlandi"

# Eski image'lari temizle (opsiyonel; sadece dangling olanlar)
Write-Step "Dangling image temizligi"
& docker image prune -f | Out-Null
Write-Ok "Tamam"
