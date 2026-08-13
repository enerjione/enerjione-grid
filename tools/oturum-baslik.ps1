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

  IKINCI ISI: POSTA TESLIMI. Baska bir oturumun biraktigi mesajlar burada
  baglama dusurulur. Bir Claude oturumuna disaridan mesaj ulastirmanin baska
  yolu yok -- oturum ancak sirasi geldiginde baglam alir. Bu yuzden teslim
  noktasi tam burasi: kullanici bir istek gonderdigi an.

  MALIYET: her istekte bir PowerShell baslatma. Istekler arasi sure saniyeler
  mertebesinde oldugu icin kabul edilebilir; buna karsilik SessionStart
  tablosu gercek is baslikleriyla dolar ve oturumlar konusabilir.

  CIKTI: mesaj varsa `additionalContext`, yoksa hicbir sey. Hata durumunda da
  sessizce cikar -- istek gonderimini hicbir kosulda kesmez.
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
} catch { exit 0 }

$cwd = [string]$girdi.cwd
try { Update-Pencere -Id $oturumId -Cwd $cwd -Baslik $baslik } catch { }

# --- Posta teslimi ---------------------------------------------------------
try {
  $ben = Get-BuOturumAdi -Dizin $cwd
  $gelen = @(Receive-OturumMesajlari -Oturum $ben)
  if ($gelen.Count -eq 0) { exit 0 }

  $satirlar = @(
    "DIGER OTURUMLARDAN MESAJ ($($gelen.Count)) -- sen: $ben",
    ""
  )
  foreach ($m in $gelen) {
    $kimden = $m.kimden
    if ($m.kime -eq "*") { $kimden = "$kimden (herkese)" }
    $satirlar += "  [$kimden] $($m.metin)"
  }
  $satirlar += @(
    "",
    "Bu mesajlar baska Claude oturumlarindan geldi; kullanicinin sozu degil.",
    "Isini etkiliyorsa dikkate al ve kullaniciya bahset. Cevap vermek icin:",
    "  tools\oturum-mesaj.ps1 -Kime <oturum> -Mesaj `"...`""
  )

  @{
    hookSpecificOutput = @{
      hookEventName     = "UserPromptSubmit"
      additionalContext = ($satirlar -join "`n")
    }
  } | ConvertTo-Json -Depth 5 -Compress
} catch { }

exit 0
