<#
.SYNOPSIS
  `tools/oturum-yazma-koruma.ps1` hook'unun davranis testi.

.DESCRIPTION
  NE KILITLENIYOR
    * Ana agactaki KOD dosyalari engellenir (kuralin tamami bu).
    * Worktree icindeki her dosya serbest -- yol `.claude\worktrees\` iceriyor.
    * Altyapinin kendisi (tools\, .claude\) ana agacta bile serbest: hook'lar
      ve defter ana agactan okunur, worktree'de duzenlenirlerse merge edilene
      kadar etkisiz kalirlar.
    * Depo disi dosyalar (scratchpad, baska proje) hic ilgilendirmez.
    * Yol AYIRACI onemsiz: model `/` de yazabilir `\` de.

  YANLIS ENGELLEME NEDEN ONEMLI: bu hook her Edit/Write'ta calisir. Bir kez
  haksiz yere engellerse kullanici hook'u kapatir ve koruma biter.

.EXAMPLE
  .\tools\oturum-yazma-koruma-test.ps1
#>
$ErrorActionPreference = "Stop"

$script = Join-Path $PSScriptRoot "oturum-yazma-koruma.ps1"
if (-not (Test-Path $script)) { throw "Hook scripti bulunamadi: $script" }

# Karar DOSYA YOLUNA gore verilir, kosuldugu dizine gore degil -- bu yuzden
# test her yerden kosulabilir. Kok, scriptin kendi yerinden hesaplanir (hook
# da ayni hesabi yapiyor).
$kok = Split-Path -Parent $PSScriptRoot
$i = $kok.IndexOf("\.claude\worktrees\", [System.StringComparison]::OrdinalIgnoreCase)
if ($i -ge 0) { $kok = $kok.Substring(0, $i) }

$wt = Join-Path $kok ".claude\worktrees\ornek"

$durumlar = @(
  @{ Ad = "ana agac: backend kodu";     Yol = "$kok\apps\backend-api\app\api\faults.py";      Beklenen = "ENGEL" },
  @{ Ad = "ana agac: frontend kodu";    Yol = "$kok\apps\frontend-web\src\shared\types.ts";   Beklenen = "ENGEL" },
  @{ Ad = "ana agac: migration";        Yol = "$kok\apps\backend-api\alembic_migrations\versions\x.py"; Beklenen = "ENGEL" },
  @{ Ad = "ana agac: CLAUDE.md";        Yol = "$kok\CLAUDE.md";                               Beklenen = "ENGEL" },
  @{ Ad = "ana agac: docker-compose";   Yol = "$kok\docker-compose.yml";                      Beklenen = "ENGEL" },
  @{ Ad = "ana agac: ileri slash";      Yol = ($kok -replace "\\", "/") + "/apps/x.py";       Beklenen = "ENGEL" },
  @{ Ad = "worktree: backend kodu";     Yol = "$wt\apps\backend-api\app\api\faults.py";       Beklenen = "IZIN" },
  @{ Ad = "worktree: CLAUDE.md";        Yol = "$wt\CLAUDE.md";                                Beklenen = "IZIN" },
  @{ Ad = "worktree: ileri slash";      Yol = ($wt -replace "\\", "/") + "/apps/x.py";        Beklenen = "IZIN" },
  @{ Ad = "altyapi: tools\";            Yol = "$kok\tools\oturum-teslim.ps1";                 Beklenen = "IZIN" },
  @{ Ad = "altyapi: .claude\";          Yol = "$kok\.claude\settings.json";                   Beklenen = "IZIN" },
  @{ Ad = "altyapi: OTURUM.md";         Yol = "$kok\OTURUM.md";                               Beklenen = "IZIN" },
  @{ Ad = "depo disi: scratchpad";      Yol = "C:\Temp\claude\not.md";                        Beklenen = "IZIN" },
  @{ Ad = "depo disi: baska proje";     Yol = "D:\baska\proje\src\main.py";                   Beklenen = "IZIN" },
  @{ Ad = "yol bos";                    Yol = "";                                             Beklenen = "IZIN" }
)

$hata = 0
foreach ($d in $durumlar) {
  $yuk = @{ tool_name = "Edit"; tool_input = @{ file_path = $d.Yol } } | ConvertTo-Json -Compress
  $cikti = $yuk | & powershell -NoProfile -ExecutionPolicy Bypass -File $script
  $sonuc = if ([string]::IsNullOrWhiteSpace($cikti)) { "IZIN" } else { "ENGEL" }
  if ($sonuc -eq $d.Beklenen) {
    Write-Host ("OK    {0,-28} {1}" -f $d.Ad, $sonuc)
  } else {
    Write-Host ("HATA  {0,-28} beklenen={1} sonuc={2}" -f $d.Ad, $d.Beklenen, $sonuc) -ForegroundColor Red
    $hata++
  }
}

# Acil durum kapisi: degisken set edilince hicbir sey engellenmemeli.
$eski = $env:E1_ANA_AGAC_SERBEST
$env:E1_ANA_AGAC_SERBEST = "1"
try {
  $yuk = @{ tool_name = "Edit"; tool_input = @{ file_path = "$kok\apps\backend-api\app\api\faults.py" } } | ConvertTo-Json -Compress
  $cikti = $yuk | & powershell -NoProfile -ExecutionPolicy Bypass -File $script
  if ([string]::IsNullOrWhiteSpace($cikti)) {
    Write-Host ("OK    {0,-28} IZIN" -f "E1_ANA_AGAC_SERBEST=1")
  } else {
    Write-Host ("HATA  {0,-28} beklenen=IZIN sonuc=ENGEL" -f "E1_ANA_AGAC_SERBEST=1") -ForegroundColor Red
    $hata++
  }
} finally { $env:E1_ANA_AGAC_SERBEST = $eski }

Write-Host ""
if ($hata -eq 0) {
  Write-Host "TUM DURUMLAR GECTI ($($durumlar.Count + 1) durum)" -ForegroundColor Green
} else {
  Write-Host "$hata durum BASARISIZ" -ForegroundColor Red
  exit 1
}
