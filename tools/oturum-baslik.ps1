<#
.SYNOPSIS
  UserPromptSubmit hook'u: oturumun NE ISLE mesgul oldugunu deftere yazar.

.DESCRIPTION
  NEDEN
  -----
  Kullanici dort Claude sekmesini ayni anda yurutuyor ve sekme basliklarindan
  ("PDF rapor tasarimi", "Ariza sayfasi", "map marker") hangisinin ne yaptigini
  okuyor. Defterde bu bilgi yoktu: SessionStart "baska oturum var" diyebiliyor
  ama "o oturum PDF raporu yapiyor, senin isinle alakasi yok" diyemiyordu.

  ILK istek oturumun konusudur. Sonrakiler ayni isin devamidir; baslik
  ustune yazilmaz (bkz. oturum-ortak.ps1 > Update-Pencere). Boylece tablo
  oturumun ISINI gosterir, son cumlesini degil.

  MALIYET: her istekte bir PowerShell baslatma. Istekler arasi sure saniyeler
  mertebesinde oldugu icin kabul edilebilir; buna karsilik SessionStart
  tablosu gercek is baslikleriyla dolar.

  CIKTI YOK: bu hook bilgi TOPLAR, bilgi vermez. Sessizce cikar; hata
  durumunda da sessizce cikar -- istek gonderimini hicbir kosulda kesmez.
#>
$ErrorActionPreference = "Stop"

try {
  $ham = [Console]::In.ReadToEnd()
  if ([string]::IsNullOrWhiteSpace($ham)) { exit 0 }
  $girdi = $ham | ConvertFrom-Json
} catch { exit 0 }

$oturumId = [string]$girdi.session_id
if ([string]::IsNullOrWhiteSpace($oturumId)) { exit 0 }

# Baslik: ilk istegin ilk satiri, kisaltilmis. Cok satirli istekte gerisi
# ayrinti olur; tabloya sigmasi ve okunmasi gereken tek sey ilk cumledir.
$baslik = ""
try {
  $istek = [string]$girdi.prompt
  if ($istek) {
    $ilk = ($istek -split "`r?`n" | Where-Object { $_.Trim() } | Select-Object -First 1)
    if ($ilk) {
      $baslik = $ilk.Trim()
      # Slash komutu ise ("/check", "/oturum liste") oldugu gibi anlamli.
      if ($baslik.Length -gt 70) { $baslik = $baslik.Substring(0, 67) + "..." }
    }
  }
} catch { }

try {
  . (Join-Path $PSScriptRoot "oturum-ortak.ps1")
  $cwd = [string]$girdi.cwd
  Update-Pencere -Id $oturumId -Cwd $cwd -Baslik $baslik
} catch { }

exit 0
