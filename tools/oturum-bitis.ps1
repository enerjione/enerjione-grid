<#
.SYNOPSIS
  SessionEnd hook'u: oturumu defterden dusurur, commit'lenmemis is varsa uyarir.

.DESCRIPTION
  NEDEN
  -----
  Kayiplarin yasandigi an, oturumun kapandigi andir: bir sekme kapaniyor,
  isi commit'lenmemis kaliyor, ertesi gun baska bir oturum ayni dosyalari
  farkli yonde degistiriyor. Worktree diskte durdugu icin is kaybolmus
  gorunmuyor -- ta ki biri `oturum-kapat -Zorla` diyene kadar.

  Bu hook kapanista iki sey yapar:
    1. Defterdeki canli pencere kaydini siler (tablo olu sekmelerle dolmasin).
    2. Bu agacta commit'lenmemis is varsa EKRANA yazar.

  CIKTI KURALI: SessionEnd'in stdout'u gosterilmez; yalnizca CIKIS KODU 2 ile
  yazilan stderr kullaniciya ulasir. Bu yuzden uyari stderr'e gider ve script
  2 ile cikar. Temiz agacta 0 ile cikar -- her kapanista ekrana bir sey
  basmak, ikinci gun okunmayan bir uyari uretirdi.
#>
$ErrorActionPreference = "Stop"

$oturumId = ""
$cwd = ""
try {
  $ham = [Console]::In.ReadToEnd()
  if (-not [string]::IsNullOrWhiteSpace($ham)) {
    $girdi = $ham | ConvertFrom-Json
    $oturumId = [string]$girdi.session_id
    $cwd = [string]$girdi.cwd
  }
} catch { }

try {
  . (Join-Path $PSScriptRoot "oturum-ortak.ps1")
} catch { exit 0 }

try {
  if ($oturumId) { Remove-Pencere -Id $oturumId }

  if ([string]::IsNullOrWhiteSpace($cwd)) { $cwd = (Get-Location).Path }
  $kok = Invoke-GitOku -C (ConvertTo-WindowsYol $cwd) rev-parse --show-toplevel
  if (-not $kok) { exit 0 }
  $kok = ConvertTo-WindowsYol ($kok -join "")

  $durum = Invoke-GitOku -C $kok status --porcelain
  $kirli = New-Object System.Collections.ArrayList
  foreach ($satir in @($durum)) {
    if ([string]::IsNullOrWhiteSpace($satir)) { continue }
    $yol = $satir.Substring(2).Trim()
    if ($yol -match '^"(.*)"$') { $yol = $Matches[1] }
    if ($yol -match '\s->\s(.+)$') { $yol = $Matches[1].Trim('"') }
    if (Test-AltyapiDosyasi $yol) { continue }
    [void]$kirli.Add($yol)
  }
  if ($kirli.Count -eq 0) { exit 0 }

  $dal = ""
  $d = Invoke-GitOku -C $kok rev-parse --abbrev-ref HEAD
  if ($d) { $dal = ($d -join "").Trim() }

  $mesaj = @(
    "",
    "OTURUM KAPANIYOR -- COMMIT'LENMEMIS IS VAR ($($kirli.Count) dosya)",
    "  Dizin: $kok",
    "  Dal  : $dal"
  )
  foreach ($f in (@($kirli) | Select-Object -First 10)) { $mesaj += "    $f" }
  if ($kirli.Count -gt 10) { $mesaj += "    ... ve $($kirli.Count - 10) dosya daha" }
  $mesaj += @(
    "",
    "  Bu dosyalar diskte duruyor, kaybolmadi. Ama baska bir oturum ayni",
    "  dosyalari farkli yonde degistirirse geri donmesi zorlasir.",
    "  Yapilacak: dalinizi commit'leyip push edin."
  )

  [Console]::Error.WriteLine(($mesaj -join "`n"))
  exit 2
} catch {
  exit 0
}
