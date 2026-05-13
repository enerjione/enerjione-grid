# ===========================================================================
# EnerjiOne Grid - Uninstall (PowerShell)
# ===========================================================================
# Container'lari durdurur ve isteyene gore volume + dizin siler.
#
# Kullanim:
#   .\uninstall.ps1              # Container stop, veri korunur (varsayilan)
#   .\uninstall.ps1 -DeleteData  # Volume + dizin SIL (geri donus YOK)
# ===========================================================================

[CmdletBinding()]
param(
    [string]$InstallDir = "C:\enerjione",
    [switch]$DeleteData
)

$ErrorActionPreference = 'Stop'

if (-not (Test-Path $InstallDir)) {
    Write-Host "InstallDir bulunamadi: $InstallDir" -ForegroundColor Yellow
    exit 0
}
Set-Location $InstallDir

if ($DeleteData) {
    Write-Host ""
    Write-Host "  UYARI: -DeleteData aktif - tum DB ve dosyalar SILINECEK." -ForegroundColor Red
    Write-Host "  Bu islem GERI ALINAMAZ. Onaylamak icin EXACTLY 'evet sil' yazin:" -ForegroundColor Red
    $confirm = Read-Host
    if ($confirm -ne "evet sil") {
        Write-Host "Iptal edildi."
        exit 0
    }

    Write-Host "==> Container + volume siliniyor" -ForegroundColor Cyan
    & docker compose down -v
    Write-Host "==> Dizin siliniyor: $InstallDir" -ForegroundColor Cyan
    Set-Location C:\
    Remove-Item $InstallDir -Recurse -Force
    Write-Host "[OK] Tamamen kaldirildi" -ForegroundColor Green
} else {
    Write-Host "==> Container'lar durduruluyor (veri korunur)" -ForegroundColor Cyan
    & docker compose down
    Write-Host "[OK] Container'lar durduruldu" -ForegroundColor Green
    Write-Host ""
    Write-Host "Veriyi tamamen silmek icin: .\uninstall.ps1 -DeleteData"
    Write-Host "Tekrar baslatmak icin     : docker compose up -d"
}
