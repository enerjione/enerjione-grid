# ===========================================================================
# EnerjiOne Grid - Windows Installer (PowerShell)
# ===========================================================================
# Local-LAN deploy icin Windows muadili. Docker Desktop yuklu varsayilir.
#
# Kullanim (admin PowerShell):
#   powershell -ExecutionPolicy Bypass -File install.ps1
#
# Parametreler:
#   -InstallDir  Hedef dizin (default: C:\enerjione)
#   -Version     E1_VERSION secimi (default: latest)
#   -SkipPull    Image cekme adimini atla (offline kurulumda image'lar
#                'docker load' ile onceden yuklenmis olmali)
#
# Idempotent: tekrar calistirilabilir; mevcut .env korunur, eksik
# secret'lar rastgele uretilir.
# ===========================================================================

[CmdletBinding()]
param(
    [string]$InstallDir = "C:\enerjione",
    [string]$Version = "latest",
    [switch]$SkipPull
)

$ErrorActionPreference = 'Stop'

function Write-Step($msg) {
    Write-Host ""
    Write-Host "==> $msg" -ForegroundColor Cyan
}

function Write-Ok($msg) {
    Write-Host "  [OK] $msg" -ForegroundColor Green
}

function Write-Warn2($msg) {
    Write-Host "  [WARN] $msg" -ForegroundColor Yellow
}

function Write-Err($msg) {
    Write-Host "  [HATA] $msg" -ForegroundColor Red
}

function New-RandomHex {
    param([int]$Bytes = 32)
    $b = New-Object byte[] $Bytes
    [System.Security.Cryptography.RandomNumberGenerator]::Fill($b)
    return -join ($b | ForEach-Object { $_.ToString("x2") })
}

# ---- Banner ----
Clear-Host
Write-Host ""
Write-Host "  EnerjiOne Grid - Windows Installer" -ForegroundColor White
Write-Host "  -----------------------------------" -ForegroundColor DarkGray
Write-Host "  Hedef dizin : $InstallDir"
Write-Host "  Version     : $Version"
Write-Host ""

# ---- Admin kontrolu ----
$isAdmin = ([Security.Principal.WindowsPrincipal] `
    [Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
    [Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) {
    Write-Err "Bu script administrator yetkisi gerektirir. PowerShell'i sag tikla -> 'Run as administrator' ile acin."
    exit 1
}

# ---- Docker Desktop kontrolu ----
Write-Step "Docker Desktop kontrolu"
try {
    $dockerVersion = & docker --version 2>$null
    if ($LASTEXITCODE -ne 0) { throw "docker --version basarisiz" }
    Write-Ok "Docker: $dockerVersion"
} catch {
    Write-Err "Docker bulunamadi veya calismiyor. Docker Desktop yukleyin:"
    Write-Host "    https://www.docker.com/products/docker-desktop"
    exit 1
}
try {
    & docker info 2>&1 | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "Docker daemon yanit vermiyor" }
    Write-Ok "Docker daemon calisiyor"
} catch {
    Write-Err "Docker daemon calismiyor. Docker Desktop'i baslatin ve tekrar deneyin."
    exit 1
}

# ---- Hedef dizin ----
Write-Step "Kurulum dizini hazirligi: $InstallDir"
if (-not (Test-Path $InstallDir)) {
    New-Item -ItemType Directory -Path $InstallDir -Force | Out-Null
    Write-Ok "Dizin olusturuldu"
} else {
    Write-Ok "Dizin mevcut (uzerine yazma yapilmayacak)"
}

# Compose dosyasini ve sablonu kopyala
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$composeSrc = Join-Path $scriptDir "docker-compose.yml"
$envExampleSrc = Join-Path $scriptDir ".env.example"

if (-not (Test-Path $composeSrc)) {
    Write-Err "docker-compose.yml bulunamadi: $composeSrc"
    Write-Host "Bu script'i repo kokunden calistirin."
    exit 1
}

Copy-Item $composeSrc (Join-Path $InstallDir "docker-compose.yml") -Force
Write-Ok "docker-compose.yml kopyalandi"

# infra/ klasoru (nats config vs.) varsa onu da kopyala
$infraSrc = Join-Path $scriptDir "infra"
if (Test-Path $infraSrc) {
    $infraDst = Join-Path $InstallDir "infra"
    if (Test-Path $infraDst) { Remove-Item $infraDst -Recurse -Force }
    Copy-Item $infraSrc $infraDst -Recurse -Force
    Write-Ok "infra/ kopyalandi"
}

# ---- .env hazirligi ----
Write-Step ".env hazirligi"
$envPath = Join-Path $InstallDir ".env"
if (-not (Test-Path $envPath)) {
    if (-not (Test-Path $envExampleSrc)) {
        Write-Err ".env.example bulunamadi: $envExampleSrc"
        exit 1
    }
    Copy-Item $envExampleSrc $envPath
    Write-Ok ".env olusturuldu (sablon: .env.example)"

    # Rastgele secret'larla doldur (placeholder'lari override et)
    $secret1 = New-RandomHex 32
    $secret2 = New-RandomHex 32
    $pgPass = New-RandomHex 16
    $rabbitPass = New-RandomHex 16

    $content = Get-Content $envPath -Raw -Encoding utf8
    $content = $content -replace 'SECRET_KEY=please-change-me-32-bytes-hex', "SECRET_KEY=$secret1"
    $content = $content -replace 'INTERNAL_SERVICE_TOKEN=please-change-me-internal-32-bytes-hex', "INTERNAL_SERVICE_TOKEN=$secret2"
    $content = $content -replace 'POSTGRES_PASSWORD=change-me-strong-password', "POSTGRES_PASSWORD=$pgPass"
    $content = $content -replace 'RABBITMQ_PASSWORD=change-me-strong-password', "RABBITMQ_PASSWORD=$rabbitPass"
    $content = $content -replace 'E1_VERSION=latest', "E1_VERSION=$Version"
    # APP_ENV varsayilan production yap
    if ($content -match 'APP_ENV=') {
        $content = $content -replace 'APP_ENV=development', 'APP_ENV=production'
    }

    Set-Content -Path $envPath -Value $content -Encoding utf8 -NoNewline
    Write-Ok "Rastgele secret'lar uretildi ve .env'e yazildi"
    Write-Warn2 "GUVENLIK: .env dosyasi hassas secret'lar iceriyor — paylasma."
} else {
    Write-Ok ".env mevcut, korundu (eksik alanlari elle kontrol edin)"
}

# .env dosyasini sadece kuran kullanicinin okuyabilecegi sekilde sinirla
try {
    $acl = Get-Acl $envPath
    $acl.SetAccessRuleProtection($true, $false)  # inheritance disable
    $acl.Access | ForEach-Object { $acl.RemoveAccessRule($_) | Out-Null }
    $admin = New-Object System.Security.AccessControl.FileSystemAccessRule(
        [System.Security.Principal.SecurityIdentifier]::new('S-1-5-32-544'),  # BUILTIN\Administrators
        'FullControl', 'Allow')
    $sys = New-Object System.Security.AccessControl.FileSystemAccessRule(
        [System.Security.Principal.SecurityIdentifier]::new('S-1-5-18'),  # SYSTEM
        'FullControl', 'Allow')
    $acl.AddAccessRule($admin)
    $acl.AddAccessRule($sys)
    Set-Acl -Path $envPath -AclObject $acl
    Write-Ok ".env izinleri kisitlandi (sadece Administrators + SYSTEM)"
} catch {
    Write-Warn2 ".env izin kisitlama atlandi: $_"
}

# ---- FCM placeholder ----
Write-Step "FCM service account placeholder"
$fcmPath = Join-Path $InstallDir "fcm-service-account.json"
if (-not (Test-Path $fcmPath)) {
    $placeholder = @{
        type = "service_account"
        project_id = "DISABLED-no-fcm-configured"
        comment = "Bu placeholder dosya - mobil push gondermek icin Firebase Admin SDK JSON dosyasini buraya kopyalayin"
    } | ConvertTo-Json
    Set-Content -Path $fcmPath -Value $placeholder -Encoding utf8
    Write-Warn2 "FCM placeholder olusturuldu. MOBIL PUSH CALISMAZ — gercek JSON yukleyin."
} else {
    Write-Ok "fcm-service-account.json mevcut"
}

# ---- Image pull veya skip ----
Set-Location $InstallDir
if (-not $SkipPull) {
    Write-Step "Image'lar cekiliyor"
    & docker compose pull
    if ($LASTEXITCODE -ne 0) {
        Write-Err "docker compose pull basarisiz oldu."
        Write-Host "Offline kurulumda image'lari onceden 'docker load' ile yukleyip"
        Write-Host "tekrar calistirin: install.ps1 -SkipPull"
        exit 1
    }
    Write-Ok "Image'lar guncel"
} else {
    Write-Warn2 "SkipPull aktif - image'larin onceden yuklenmis olmasi gerekir"
}

# ---- Servisleri ayaga kaldir ----
Write-Step "Servisler baslatiliyor"
& docker compose up -d
if ($LASTEXITCODE -ne 0) {
    Write-Err "docker compose up basarisiz oldu. Loglar icin:"
    Write-Host "    cd $InstallDir; docker compose logs -f"
    exit 1
}
Write-Ok "Servisler baslatildi"

# ---- Saglik kontrolu (kisa bekleme) ----
Write-Step "Saglik kontrolu (30 sn)"
Start-Sleep -Seconds 5
$retries = 0
$maxRetries = 6
$healthy = $false
while ($retries -lt $maxRetries) {
    try {
        $resp = Invoke-WebRequest -Uri "http://127.0.0.1:8000/api/v1/health" -TimeoutSec 5 -UseBasicParsing 2>$null
        if ($resp.StatusCode -eq 200) {
            $healthy = $true
            break
        }
    } catch {
        # bekle
    }
    Start-Sleep -Seconds 5
    $retries++
    Write-Host "  ... ($retries/$maxRetries)" -ForegroundColor DarkGray
}

if ($healthy) {
    Write-Ok "Backend saglikli (http://127.0.0.1:8000/api/v1/health)"
} else {
    Write-Warn2 "Backend henuz hazir degil. Birkac dakika sonra elle kontrol edin:"
    Write-Host "    Invoke-WebRequest http://127.0.0.1:8000/api/v1/health"
}

# ---- Ozet ----
Write-Host ""
Write-Host "============================================================" -ForegroundColor Green
Write-Host "  Kurulum tamamlandi" -ForegroundColor Green
Write-Host "============================================================" -ForegroundColor Green
Write-Host ""
Write-Host "  Dizin       : $InstallDir"
Write-Host "  Frontend    : http://localhost"
Write-Host "  Backend API : http://localhost:8000/api/v1"
Write-Host "  Default admin: admin / ChangeMe123!  (ILK GIRISTE DEGISTIR)"
Write-Host ""
Write-Host "Komutlar:"
Write-Host "  cd $InstallDir"
Write-Host "  docker compose logs -f         # canli log"
Write-Host "  docker compose ps              # servis durumu"
Write-Host "  docker compose restart         # tum servisleri yeniden basla"
Write-Host "  .\update.ps1                   # yeni image versiyonuna gec"
Write-Host "  .\uninstall.ps1                # kaldirma"
Write-Host ""
