<#
.SYNOPSIS
  "Tag'a hazir miyiz?" -- tek ekranda, tek cevap. Tag'de NE CIKTIGINI da yazar.

.DESCRIPTION
  NEDEN VAR
  ---------
  Saha cihazlari main'i degil TAG'i takip eder. Yani tag anindaki eksik,
  sahaya cikan eksiktir. Bu scripten once "her sey main'de mi" sorusunun
  cevabi yoktu: acik oturumlarin dallarina, worktree'lerdeki commit'lenmemis
  dosyalara, main'in origin'e gore durumuna ve bes ayri yerdeki surum
  numarasina TEK TEK bakmak gerekiyordu. Yazildigi gun bu tarama sunlari
  buldu: origin'e push edilmemis 4 commit, dalinda bekleyen "MIGRATION EKSIK"
  etiketli bir is, ve ayni duzeltmeyi tasiyan iki ayri dal.

  HESAP BURADA DEGIL: butun kontroller `surum-durum.ps1` kitapligindadir.
  Panel de ayni kitapligi cagirir -- iki yuzun ayni soruya farkli cevap
  vermesi boylece imkansiz.

  ENGEL / UYARI AYRIMI
    ENGEL  tag atilirsa sahaya EKSIK ya da BOZUK cikar. `-Tag` calismaz.
    UYARI  duzen sorunu (olu dal, acik worktree). Tag'i durdurmaz.

.PARAMETER Test
  main uzerinde pytest + tsc + npm test kosar. Yavastir; son turda ise yarar.

.PARAMETER Notlar
  Yalnizca "bu tag'de ne cikiyor" listesini yazdir, kontrolleri atla.

.PARAMETER Tag
  Her sey yesilse tag'i atar ve push eder. Tag notunun govdesine Eklendi/
  Duzeltildi listesi yazilir.

.PARAMETER Ozet
  Tag basligi: "v2.88.0 (uzun tire) <ozet>". Depodaki tag'lar boyle yazilmis;
  uzun tire koda ASCII kacisiyla gomulu (bkz. $TIRE).

.EXAMPLE
  .\tools\surum-hazir.ps1
  .\tools\surum-hazir.ps1 -Notlar
  .\tools\surum-hazir.ps1 -Test -Tag -Ozet "ariza cozumu, alarm gecmisi, PDF rapor"
#>
[CmdletBinding()]
param(
  [switch]$Test,
  [switch]$Notlar,
  [switch]$Tag,
  [string]$Ozet = ""
)

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "surum-durum.ps1")

# Depodaki tag notlari uzun tire kullaniyor ("v2.86.0 <tire> Cihaz Durum
# Raporu"). Dosyaya ASCII disi karakter YAZILMAZ: PowerShell 5.1 BOM'suz .ps1
# dosyasini ANSI okur ve tek bir uzun tire scripti ayristirilamaz hale getirir
# (bu script ilk calistirmada tam bundan dustu).
$TIRE = [char]0x2014

function Baslik($n, $metin) { Write-Host ""; Write-Host "[$n] $metin" -ForegroundColor White }
function Tamam($metin) { Write-Host "    OK  $metin" -ForegroundColor Green }
function Bilgi($metin) { Write-Host "    ..  $metin" -ForegroundColor DarkGray }
function Kotu($metin)  { Write-Host "    !!  $metin" -ForegroundColor Red }
function Sari($metin)  { Write-Host "    ~   $metin" -ForegroundColor Yellow }
function Ayrinti($metin) { Write-Host "          $metin" -ForegroundColor DarkGray }

$kok = Get-AnaAgacKok
if (-not $kok) { throw "Git deposu bulunamadi." }

# ---------------------------------------------------------------------------
# Bu tag'de ne cikiyor
# ---------------------------------------------------------------------------
function Yaz-Notlar($n) {
  Write-Host ""
  $nereden = if ($n.sonTag) { "$($n.sonTag) sonrasi" } else { "depo baslangicindan beri" }
  Write-Host "BU TAG'DE NE CIKIYOR  ($nereden, $($n.toplam) is)" -ForegroundColor Cyan

  if ($n.toplam -eq 0) {
    Bilgi "main'e $nereden yeni bir is girmemis."
    return
  }
  if ($n.eklendi.Count -gt 0) {
    Write-Host ""
    Write-Host "  EKLENDI ($($n.eklendi.Count))" -ForegroundColor Green
    foreach ($e in $n.eklendi) {
      Write-Host "    + $($e.metin)" -ForegroundColor Gray
      if ($e.dal) { Write-Host "      $($e.sha)  $($e.dal)" -ForegroundColor DarkGray }
      else { Write-Host "      $($e.sha)" -ForegroundColor DarkGray }
    }
  }
  if ($n.duzeltildi.Count -gt 0) {
    Write-Host ""
    Write-Host "  DUZELTILDI ($($n.duzeltildi.Count))" -ForegroundColor Yellow
    foreach ($e in $n.duzeltildi) {
      Write-Host "    * $($e.metin)" -ForegroundColor Gray
      if ($e.dal) { Write-Host "      $($e.sha)  $($e.dal)" -ForegroundColor DarkGray }
      else { Write-Host "      $($e.sha)" -ForegroundColor DarkGray }
    }
  }
  if ($n.diger.Count -gt 0) {
    Write-Host ""
    Write-Host "  DIGER ($($n.diger.Count))" -ForegroundColor DarkGray
    foreach ($e in $n.diger) { Write-Host "    - $($e.metin)  ($($e.sha))" -ForegroundColor DarkGray }
  }
  if ($n.yayinlanmamis) {
    Write-Host ""
    Write-Host "  CHANGELOG > [Yayinlanmamis] BOS DEGIL:" -ForegroundColor Yellow
    foreach ($s in (($n.yayinlanmamis -split "`n") | Select-Object -First 8)) { Ayrinti $s }
    Ayrinti "Tag'den once bu maddeler yeni surum basligina tasinmali."
  }
}

# Tag notu govdesi: ekranda gordugunuz listenin duz metin hali.
function Yap-TagGovdesi($n, $ozet, $tagAdi) {
  $satir = New-Object System.Collections.ArrayList
  [void]$satir.Add("$tagAdi $TIRE $ozet")
  [void]$satir.Add("")
  if ($n.eklendi.Count -gt 0) {
    [void]$satir.Add("Eklendi")
    foreach ($e in $n.eklendi) { [void]$satir.Add("- $($e.metin) ($($e.sha))") }
    [void]$satir.Add("")
  }
  if ($n.duzeltildi.Count -gt 0) {
    [void]$satir.Add("Duzeltildi")
    foreach ($e in $n.duzeltildi) { [void]$satir.Add("- $($e.metin) ($($e.sha))") }
    [void]$satir.Add("")
  }
  if ($n.diger.Count -gt 0) {
    [void]$satir.Add("Diger")
    foreach ($e in $n.diger) { [void]$satir.Add("- $($e.metin) ($($e.sha))") }
  }
  return (($satir -join "`n").Trim())
}

if ($Notlar) {
  $n = Get-SurumNotlari -Kok $kok
  Yaz-Notlar $n
  Write-Host ""
  exit 0
}

# ---------------------------------------------------------------------------
# Kontroller
# ---------------------------------------------------------------------------
Write-Host ""
Write-Host "SURUM HAZIRLIK  --  $kok" -ForegroundColor Cyan
Bilgi "origin fetch ediliyor"
$d = Get-SurumDurumu -Fetch -Tazele
if (-not $d) { throw "Durum hesaplanamadi." }

Baslik 1 "Ana agac"
if ($d.anaDal -ne "main") { Kotu "ana agac '$($d.anaDal)' dalinda; tag main'den atilir" }
else { Tamam "main dalinda" }
if ($d.anaKirli.Count -gt 0) {
  Kotu "commit'lenmemis $($d.anaKirli.Count) dosya"
  foreach ($k in ($d.anaKirli | Select-Object -First 10)) { Ayrinti $k }
} else { Tamam "izlenen dosyalarda degisiklik yok" }
if ($d.anaIzlenmeyen.Count -gt 0) {
  Sari "izlenmeyen $($d.anaIzlenmeyen.Count) dosya (tag'a girmez)"
  foreach ($k in ($d.anaIzlenmeyen | Select-Object -First 5)) { Ayrinti $k }
}

Baslik 2 "origin/main"
if ($d.cevrimdisi) { Sari "origin'e ulasilamadi -- uzak karsilastirmasi yapilamadi" }
elseif (-not $d.uzakVar) { Bilgi "origin/main yok -- atlaniyor" }
elseif ($d.yerelOnde -gt 0 -and $d.uzakOnde -gt 0) { Kotu "AYRISMIS ($($d.yerelOnde) yerel / $($d.uzakOnde) uzak)" }
elseif ($d.yerelOnde -gt 0) {
  Kotu "main, origin'in $($d.yerelOnde) commit ONUNDE -- push edilmemis is var"
  Ayrinti "git -C `"$kok`" push origin main"
} elseif ($d.uzakOnde -gt 0) {
  Kotu "main, origin'in $($d.uzakOnde) commit GERISINDE"
  Ayrinti "git -C `"$kok`" merge --ff-only origin/main"
} else { Tamam "senkron" }

Baslik 3 "Teslim edilmemis is"
if ($d.bekleyen.Count -eq 0) { Tamam "main'de olmayan commit tasiyan dal yok" }
else {
  foreach ($b in $d.bekleyen) {
    $kim = if ($b.konu) { "$($b.konu) [$($b.dal)]" } else { "$($b.dal) (worktree'si kapali)" }
    Kotu "$kim : main'de olmayan $($b.sayi) commit"
    Ayrinti $b.son
  }
  Ayrinti "-> tools\oturum-teslim.ps1 -Hepsi     (hazir olanlarin hepsini main'e alir)"
}
if ($d.kirliAgac.Count -eq 0) { Tamam "acik worktree'lerin hepsi temiz" }
else {
  foreach ($a in $d.kirliAgac) {
    Kotu "$($a.konu) [$($a.dal)]: commit'lenmemis $($a.sayi) dosya"
    foreach ($f in $a.dosyalar) { Ayrinti $f }
  }
}
if ($d.stash.Count -gt 0) {
  Sari "$($d.stash.Count) stash -- hicbir dalda, hicbir tag'de gorunmez"
  foreach ($s in ($d.stash | Select-Object -First 5)) { Ayrinti $s }
  Ayrinti "icerigi: git stash show -p stash@{0}   |   geri al: git stash pop"
}
if ($d.olu.Count -gt 0) {
  Sari "$($d.olu.Count) dal main'e girmis ama duruyor"
  Ayrinti "tools\dal-temizle.ps1 -Uygula"
}
if ($d.bosAgac.Count -gt 0) {
  Sari "$($d.bosAgac.Count) worktree isi main'e girdigi halde acik"
  foreach ($b in ($d.bosAgac | Select-Object -First 5)) { Ayrinti "tools\oturum-kapat.ps1 -Konu $b" }
}

Baslik 4 "Migration zinciri"
if ($d.migrationBas.Count -gt 1) {
  Kotu "zincir $($d.migrationBas.Count) BASLI: $($d.migrationBas -join ', ')"
  Ayrinti "Tek Postgres var; iki basli zincir alembic_version'i HERKES icin bozar."
} elseif ($d.migrationBas.Count -eq 1) {
  Tamam "tek basli ($($d.migrationBas[0])) -- $($d.migrationSayi) migration"
} else { Bilgi "zincir okunamadi" }

Baslik 5 "Surum numarasi"
foreach ($ad in $d.surumler.Keys) { Bilgi "$($ad.PadRight(14)) $($d.surumler[$ad])" }
$farkli = @($d.surumler.Values | Sort-Object -Unique)
if ($farkli.Count -gt 1) { Kotu "AYRISIK: $($farkli -join ' / ')" }
elseif ($farkli.Count -eq 1) { Tamam "bes kaynakta da $($farkli[0])" }
if ($d.tagAdi) {
  if (-not $d.tagVar) { Tamam "$($d.tagAdi) henuz atilmamis" }
  elseif ($d.tagMaindeMi) { Sari "$($d.tagAdi) zaten var ve main'i gosteriyor" }
  else {
    Kotu "$($d.tagAdi) ZATEN VAR ama baska bir commit'i gosteriyor"
    Ayrinti "Surumu yukseltin (bes kaynakta birden) ya da tag'i tasiyin."
  }
}

# ---------------------------------------------------------------------------
# 6. Testler
# ---------------------------------------------------------------------------
$testEngeli = New-Object System.Collections.ArrayList
if ($Test) {
  Baslik 6 "Testler (main uzerinde)"
  $py = Join-Path $kok "apps\backend-api\.venv\Scripts\python.exe"
  if (-not (Test-Path $py)) { $py = "python" }
  Bilgi "pytest"
  Push-Location (Join-Path $kok "apps\backend-api")
  try { $cikti = & $py -m pytest -q 2>&1; $kod = $LASTEXITCODE } finally { Pop-Location }
  if ($kod -ne 0) {
    Kotu "pytest DUSTU"; [void]$testEngeli.Add("pytest DUSTU")
    foreach ($s in @($cikti | Select-Object -Last 12)) { Ayrinti $s }
  } else { Tamam "pytest gecti" }

  Bilgi "npx tsc -b + npm test"
  Push-Location (Join-Path $kok "apps\frontend-web")
  try {
    $c1 = & npx tsc -b 2>&1; $k1 = $LASTEXITCODE
    $c2 = & npm test 2>&1;   $k2 = $LASTEXITCODE
  } finally { Pop-Location }
  if ($k1 -ne 0) {
    Kotu "tsc -b DUSTU"; [void]$testEngeli.Add("tsc -b DUSTU")
    foreach ($s in @($c1 | Select-Object -Last 12)) { Ayrinti $s }
  } else { Tamam "tsc -b gecti" }
  if ($k2 -ne 0) {
    Kotu "npm test DUSTU"; [void]$testEngeli.Add("npm test DUSTU")
    foreach ($s in @($c2 | Select-Object -Last 12)) { Ayrinti $s }
  } else { Tamam "npm test gecti" }
} else {
  Baslik 6 "Testler"
  Bilgi "atlandi -- kosmak icin: -Test"
}

# ---------------------------------------------------------------------------
# Ne cikiyor + sonuc
# ---------------------------------------------------------------------------
# Degisken adi `$notlar` OLAMAZ: PowerShell degisken adlari buyuk/kucuk harf
# duyarsizdir ve `-Notlar` switch'ini ezip "SwitchParameter'a PSCustomObject
# atanamaz" hatasi verir. Script tam da sonuc bolumune varmadan duserdi --
# yani "TAG'A HAZIR MI" satiri hic yazilmazdi.
$surumNotlari = Get-SurumNotlari -Kok $kok
Yaz-Notlar $surumNotlari

$engeller = @(@($d.engeller) + @($testEngeli))
Write-Host ""
Write-Host ("-" * 68) -ForegroundColor DarkGray
if ($engeller.Count -gt 0) {
  Write-Host "TAG'A HAZIR DEGIL -- $($engeller.Count) engel" -ForegroundColor Red
  $i = 1
  foreach ($e in $engeller) { Write-Host "  $i. $e" -ForegroundColor Red; $i++ }
  if ($d.uyarilar.Count -gt 0) {
    Write-Host ""
    Write-Host "Ayrica $($d.uyarilar.Count) uyari (tag'i engellemez):" -ForegroundColor Yellow
    foreach ($u in $d.uyarilar) { Write-Host "  - $u" -ForegroundColor DarkYellow }
  }
  exit 1
}

Write-Host "TAG'A HAZIR" -ForegroundColor Green
if ($d.surum) { Write-Host "  Surum: $($d.surum)   Tag: $($d.tagAdi)" -ForegroundColor White }
if (-not $Test) { Write-Host "  (testler kosulmadi -- son tur icin: -Test)" -ForegroundColor DarkGray }
if ($d.uyarilar.Count -gt 0) {
  Write-Host ""
  Write-Host "$($d.uyarilar.Count) uyari:" -ForegroundColor Yellow
  foreach ($u in $d.uyarilar) { Write-Host "  - $u" -ForegroundColor DarkYellow }
}

if (-not $Tag) {
  Write-Host ""
  Write-Host "Tag atmak icin:" -ForegroundColor White
  Write-Host "  tools\surum-hazir.ps1 -Test -Tag -Ozet `"<bir cumlelik ozet>`""
  exit 0
}

# ---------------------------------------------------------------------------
# Tag
# ---------------------------------------------------------------------------
if ([string]::IsNullOrWhiteSpace($Ozet)) {
  Write-Host ""
  Write-Host "-Tag icin -Ozet gerekli. Depodaki tag'lar '$($d.tagAdi) $TIRE <ozet>' bicimindedir." -ForegroundColor Yellow
  exit 1
}

$govde = Yap-TagGovdesi $surumNotlari $Ozet $d.tagAdi
Write-Host ""
Write-Host "Tag notu:" -ForegroundColor Cyan
foreach ($s in ($govde -split "`n")) { Write-Host "  $s" -ForegroundColor DarkGray }

Write-Host ""
Write-Host "Tag atiliyor: $($d.tagAdi)" -ForegroundColor Cyan
& git -C $kok tag -a $d.tagAdi -m $govde
if ($LASTEXITCODE -ne 0) { Write-Host "Tag atilamadi." -ForegroundColor Red; exit 1 }
& git -C $kok push origin $d.tagAdi
if ($LASTEXITCODE -ne 0) {
  Write-Host "Tag YERELDE atildi ama push DUSTU:" -ForegroundColor Yellow
  Write-Host "  git -C `"$kok`" push origin $($d.tagAdi)" -ForegroundColor White
  exit 1
}
Write-Host ""
Write-Host "$($d.tagAdi) atildi ve push edildi. Deploy tag'den tetiklenir." -ForegroundColor Green
