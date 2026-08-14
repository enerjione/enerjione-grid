<#
.SYNOPSIS
  "Tag'a hazir miyiz?" -- tek ekranda, tek cevap. Isteyen tag'i da atar.

.DESCRIPTION
  NEDEN VAR
  ---------
  Saha cihazlari main'i degil TAG'i takip eder. Yani tag anindaki eksik,
  sahaya cikan eksiktir. Bu scripten once "her sey main'de mi" sorusunun
  cevabi yoktu: acik oturumlarin dallarina, worktree'lerdeki commit'lenmemis
  dosyalara, main'in origin'e gore durumuna ve dort ayri yerdeki surum
  numarasina TEK TEK bakmak gerekiyordu. Yazildigi gun bu tarama sunlari
  buldu: origin'e push edilmemis 4 commit, dalinda bekleyen "MIGRATION EKSIK"
  etiketli bir is, ve ayni duzeltmeyi tasiyan iki ayri dal.

  ENGEL / UYARI AYRIMI
    ENGEL  tag atilirsa sahaya EKSIK ya da BOZUK cikar. `-Tag` calismaz.
    UYARI  duzen sorunu (olu dal, acik worktree). Tag'i durdurmaz.

  KONTROLLER
    1. Ana agac main'de mi, temiz mi
    2. main <-> origin/main
    3. Teslim edilmemis is: main'de olmayan commit tasiyan HER yerel dal
       + acik worktree'lerdeki commit'lenmemis dosyalar
    4. Migration zinciri tek basli mi
    5. Surum numarasi bes kaynakta ayni mi, tag zaten var mi
    6. (-Test) main uzerinde pytest + tsc + npm test

.PARAMETER Test
  6. adimi da kosur. Yavastir; tag oncesi son turda ise yarar.

.PARAMETER Tag
  Her sey yesilse tag'i atar ve push eder. `-Ozet` ile birlikte verilir.

.PARAMETER Ozet
  Tag notunun basligi: "v2.87.0 (uzun tire) <ozet>". Depodaki tag'lar boyle
  yazilmis; uzun tire koda ASCII kacisiyla gomulu (bkz. $TIRE).

.EXAMPLE
  .\tools\surum-hazir.ps1
  .\tools\surum-hazir.ps1 -Test
  .\tools\surum-hazir.ps1 -Tag -Ozet "ariza cozumu, alarm gecmisi, PDF rapor"
#>
[CmdletBinding()]
param(
  [switch]$Test,
  [switch]$Tag,
  [string]$Ozet = ""
)

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "oturum-ortak.ps1")

$script:engeller = New-Object System.Collections.ArrayList
$script:uyarilar = New-Object System.Collections.ArrayList

# Depodaki tag notlari uzun tire kullaniyor ("v2.86.0 <tire> Cihaz Durum
# Raporu"). Dosyaya ASCII disi karakter YAZILMAZ: PowerShell 5.1 BOM'suz .ps1
# dosyasini ANSI okur ve tek bir uzun tire scripti ayristirilamaz hale getirir
# (bu script ilk calistirmada tam bundan dustu).
$TIRE = [char]0x2014

function Baslik($n, $metin) { Write-Host ""; Write-Host "[$n] $metin" -ForegroundColor White }
function Tamam($metin) { Write-Host "    OK  $metin" -ForegroundColor Green }
function Bilgi($metin) { Write-Host "    ..  $metin" -ForegroundColor DarkGray }
function Ekle-Engel($metin) {
  Write-Host "    !!  $metin" -ForegroundColor Red
  [void]$script:engeller.Add($metin)
}
function Ekle-Uyari($metin) {
  Write-Host "    ~   $metin" -ForegroundColor Yellow
  [void]$script:uyarilar.Add($metin)
}

$anaKok = Get-AnaAgacKok
if (-not $anaKok) { throw "Git deposu bulunamadi." }

Write-Host ""
Write-Host "SURUM HAZIRLIK  --  $anaKok" -ForegroundColor Cyan

# `& git ... 2>$null` YETMEZ: `$ErrorActionPreference = "Stop"` altinda native
# komutun stderr'i terminating error olur ve script agsizken komple duser
# (oturum-teslim.ps1'de tam bu yasandi). Fetch dusebilir; kontroller yerel
# bilgiyle devam eder, sadece origin karsilastirmasi atlanir.
$eskiTercih = $ErrorActionPreference
$ErrorActionPreference = "Continue"
try { & git -C $anaKok fetch origin --quiet 2>&1 | Out-Null; $fetchKod = $LASTEXITCODE }
catch { $fetchKod = 1 }
finally { $ErrorActionPreference = $eskiTercih }
$cevrimdisi = ($fetchKod -ne 0)
$uzakVar = (-not $cevrimdisi) -and ($null -ne (Invoke-GitOku -C $anaKok rev-parse --verify --quiet "origin/main"))

# ---------------------------------------------------------------------------
# 1. Ana agac
# ---------------------------------------------------------------------------
Baslik 1 "Ana agac"
$anaDal = (( Invoke-GitOku -C $anaKok rev-parse --abbrev-ref HEAD) -join "").Trim()
if ($anaDal -ne "main") { Ekle-Engel "ana agac '$anaDal' dalinda; tag main'den atilir" }
else { Tamam "main dalinda" }

$anaDurum = Invoke-GitOku -C $anaKok status --porcelain
$anaKirli = @(@($anaDurum) | Where-Object {
  $_ -and $_.Substring(0, 2) -ne "??" -and -not (Test-AltyapiDosyasi ($_.Substring(2).Trim().Trim('"')))
})
$anaIzlenmeyen = @(@($anaDurum) | Where-Object {
  $_ -and $_.Substring(0, 2) -eq "??" -and -not (Test-AltyapiDosyasi ($_.Substring(2).Trim().Trim('"')))
})
if ($anaKirli.Count -gt 0) {
  Ekle-Engel "ana agacta commit'lenmemis $($anaKirli.Count) dosya"
  foreach ($k in ($anaKirli | Select-Object -First 10)) { Write-Host "          $k" -ForegroundColor DarkGray }
} else { Tamam "izlenen dosyalarda degisiklik yok" }
if ($anaIzlenmeyen.Count -gt 0) {
  Ekle-Uyari "ana agacta izlenmeyen $($anaIzlenmeyen.Count) dosya (tag'a girmez)"
  foreach ($k in ($anaIzlenmeyen | Select-Object -First 5)) { Write-Host "          $k" -ForegroundColor DarkGray }
}

# ---------------------------------------------------------------------------
# 2. main <-> origin/main
# ---------------------------------------------------------------------------
Baslik 2 "origin/main"
if ($cevrimdisi) {
  # Sessizce gecilmez: tag uzaga push edilir. Uzakla karsilastirma
  # yapilamadiysa "her sey main'de" ifadesi yalnizca YEREL icin dogrudur.
  Ekle-Uyari "origin'e ulasilamadi -- uzak karsilastirmasi YAPILAMADI"
} elseif (-not $uzakVar) {
  Bilgi "origin/main yok -- atlaniyor"
} else {
  $sayim = Invoke-GitOku -C $anaKok rev-list --left-right --count "origin/main...main"
  $uzakOnde = 0; $yerelOnde = 0
  if ($sayim) {
    $p = (($sayim -join "") -split "\s+") | Where-Object { $_ -match '^\d+$' }
    if ($p.Count -ge 2) { $uzakOnde = [int]$p[0]; $yerelOnde = [int]$p[1] }
  }
  if ($yerelOnde -gt 0 -and $uzakOnde -gt 0) {
    Ekle-Engel "main ile origin/main AYRISMIS ($yerelOnde yerel / $uzakOnde uzak)"
  } elseif ($yerelOnde -gt 0) {
    # Tag'i uzaga push edeceksiniz; isaret ettigi commit uzakta yoksa tag
    # bos bir ref olur ve deploy o commit'i cekemez.
    Ekle-Engel "main, origin'in $yerelOnde commit ONUNDE -- push edilmemis is var"
    Write-Host "          git -C `"$anaKok`" push origin main" -ForegroundColor DarkGray
  } elseif ($uzakOnde -gt 0) {
    Ekle-Engel "main, origin'in $uzakOnde commit GERISINDE"
    Write-Host "          git -C `"$anaKok`" merge --ff-only origin/main" -ForegroundColor DarkGray
  } else {
    Tamam "senkron"
  }
}

# ---------------------------------------------------------------------------
# 3. Teslim edilmemis is
# ---------------------------------------------------------------------------
Baslik 3 "Teslim edilmemis is"

# 3a. main'de olmayan commit tasiyan yerel dallar.
#
# DEFTERE DEGIL GIT'E bakilir: defter yalnizca acik worktree'leri bilir, oysa
# is worktree'si kapatilmis bir dalda da bekliyor olabilir (yazildigi gun
# `chore/release-2.80.1` tam boyleydi). Tek dogru kaynak commit grafigidir.
# DIKKAT: `Invoke-GitOku ... | Where-Object` YAZILMAZ. Fonksiyon diziyi
# korumak icin `,@($cikti)` donduruyor; dogrudan pipe'a verilince PowerShell
# dis sarmali acar ve karsi tarafa TEK nesne olarak butun dizi gider. Ilk
# surumde tam bu oldu: 84 dal "1 dal" gorundu ve filtre calismadigi icin
# `main` de listeye girdi. Once degiskene al, sonra filtrele.
$hamDallar = Invoke-GitOku -C $anaKok for-each-ref --format="%(refname:short)" refs/heads/
$dallar = @(@($hamDallar) | Where-Object { $_ -and $_ -ne "main" })
$bekleyen = New-Object System.Collections.ArrayList
$olu = New-Object System.Collections.ArrayList
foreach ($d in $dallar) {
  $n = Invoke-GitOku -C $anaKok rev-list --count "main..$d"
  $sayi = 0
  if ($n) { try { $sayi = [int](($n -join "").Trim()) } catch { $sayi = 0 } }
  if ($sayi -gt 0) { [void]$bekleyen.Add([pscustomobject]@{ Dal = $d; Sayi = $sayi }) }
  else { [void]$olu.Add($d) }
}

if ($bekleyen.Count -eq 0) {
  Tamam "main'de olmayan commit tasiyan dal yok"
} else {
  foreach ($b in $bekleyen) {
    Ekle-Engel "$($b.Dal): main'de olmayan $($b.Sayi) commit"
    $satirlar = Invoke-GitOku -C $anaKok log --oneline "main..$($b.Dal)"
    foreach ($c in (@($satirlar) | Select-Object -First 5)) {
      Write-Host "          $c" -ForegroundColor DarkGray
    }
  }
  Write-Host "          -> tools\oturum-teslim.ps1 -Konu <ad>   (dogrular, main'e alir)" -ForegroundColor DarkGray
}

# 3b. Acik worktree'lerdeki commit'lenmemis dosyalar.
$worktreeler = @(Get-WorktreeListesi | Where-Object { -not $_.AnaMi })
$kirliAgac = 0
foreach ($w in $worktreeler) {
  if (-not (Test-Path $w.Yol)) { continue }
  $d = Invoke-GitOku -C $w.Yol status --porcelain
  $k = @(@($d) | Where-Object { $_ -and -not (Test-AltyapiDosyasi ($_.Substring(2).Trim().Trim('"'))) })
  if ($k.Count -gt 0) {
    $kirliAgac++
    Ekle-Engel "$(Split-Path -Leaf $w.Yol) [$($w.Dal)]: commit'lenmemis $($k.Count) dosya"
    foreach ($s in ($k | Select-Object -First 5)) { Write-Host "          $s" -ForegroundColor DarkGray }
  }
}
if ($worktreeler.Count -gt 0 -and $kirliAgac -eq 0) { Tamam "$($worktreeler.Count) acik worktree'nin hepsi temiz" }

if ($olu.Count -gt 0) {
  Ekle-Uyari "$($olu.Count) dal main'e girmis ama duruyor (tag'i engellemez)"
  foreach ($o in ($olu | Select-Object -First 3)) { Write-Host "          $o" -ForegroundColor DarkGray }
  if ($olu.Count -gt 3) { Write-Host "          ... ve $($olu.Count - 3) dal daha" -ForegroundColor DarkGray }
  Write-Host "          tools\dal-temizle.ps1        # birlesmis dallari toplu siler" -ForegroundColor DarkGray
}
$bosAgac = @($worktreeler | Where-Object {
  $dd = $_.Dal
  ($dd) -and (@($olu) -contains $dd)
})
if ($bosAgac.Count -gt 0) {
  Ekle-Uyari "$($bosAgac.Count) worktree isi main'e girdigi halde acik"
  foreach ($b in ($bosAgac | Select-Object -First 5)) {
    Write-Host "          tools\oturum-kapat.ps1 -Konu $(Split-Path -Leaf $b.Yol)" -ForegroundColor DarkGray
  }
}

# ---------------------------------------------------------------------------
# 4. Migration zinciri
# ---------------------------------------------------------------------------
Baslik 4 "Migration zinciri"
$vDizin = Join-Path $anaKok "apps\backend-api\alembic_migrations\versions"
if (Test-Path $vDizin) {
  $rev = New-Object System.Collections.Generic.HashSet[string]
  $alt = New-Object System.Collections.Generic.HashSet[string]
  foreach ($f in (Get-ChildItem $vDizin -Filter *.py -File)) {
    foreach ($satir in (Get-Content $f.FullName -Encoding UTF8)) {
      if ($satir -match '^\s*revision\s*(?::[^=]+)?=\s*[''"]([^''"]+)[''"]') { [void]$rev.Add($Matches[1]) }
      elseif ($satir -match '^\s*down_revision\s*(?::[^=]+)?=\s*[''"]([^''"]+)[''"]') { [void]$alt.Add($Matches[1]) }
    }
  }
  $basliklar = @($rev | Where-Object { -not $alt.Contains($_) })
  if ($basliklar.Count -gt 1) {
    Ekle-Engel "zincir $($basliklar.Count) BASLI: $($basliklar -join ', ')"
    Write-Host "          Tek Postgres var; iki basli zincir alembic_version'i HERKES icin bozar." -ForegroundColor DarkGray
  } elseif ($basliklar.Count -eq 1) {
    Tamam "tek basli ($($basliklar[0])) -- $($rev.Count) migration"
  } else {
    Bilgi "zincir okunamadi"
  }
} else { Bilgi "versions dizini yok" }

# ---------------------------------------------------------------------------
# 5. Surum numarasi
# ---------------------------------------------------------------------------
Baslik 5 "Surum numarasi"

# Surum BES yerde yaziyor. Biri unutulursa arayuz bir surum, imaj tag'i baska
# bir surum gosterir -- "hangi surum sahada" sorusu cevapsiz kalir.
$kaynaklar = @(
  @{ Ad = "VERSION";           Yol = "VERSION";                              Desen = '^\s*([0-9]+\.[0-9]+\.[0-9]+)\s*$' },
  @{ Ad = "package.json";      Yol = "apps\frontend-web\package.json";       Desen = '"version"\s*:\s*"([0-9]+\.[0-9]+\.[0-9]+)"' },
  @{ Ad = "config.py";         Yol = "apps\backend-api\app\core\config.py";  Desen = '_FALLBACK_APP_VERSION\s*=\s*"([0-9]+\.[0-9]+\.[0-9]+)"' },
  # `S.r.m`: dosyada "Surum" da "Surum" (Turkce u ile) de yazabilir. Desene
  # ASCII disi karakter KOYULMAZ -- bkz. $TIRE aciklamasi.
  @{ Ad = "CLAUDE.md";         Yol = "CLAUDE.md";                            Desen = '(?i)\*\*S.r.m:?\*\*\s*([0-9]+\.[0-9]+\.[0-9]+)' },
  @{ Ad = "CHANGELOG.md";      Yol = "CHANGELOG.md";                         Desen = '^##\s*\[([0-9]+\.[0-9]+\.[0-9]+)\]' }
)

$bulunan = @{}
foreach ($k in $kaynaklar) {
  $tam = Join-Path $anaKok $k.Yol
  if (-not (Test-Path $tam)) { Ekle-Uyari "$($k.Ad) bulunamadi"; continue }
  $ham = Get-Content $tam -Raw -Encoding UTF8
  # CHANGELOG'da ILK surum basligi en yeni surumdur; digerlerinde tek eslesme.
  $m = [regex]::Match($ham, $k.Desen, [System.Text.RegularExpressions.RegexOptions]::Multiline)
  if ($m.Success) { $bulunan[$k.Ad] = $m.Groups[1].Value }
  else { Ekle-Uyari "$($k.Ad) icinde surum numarasi okunamadi" }
}

$farkli = @($bulunan.Values | Sort-Object -Unique)
foreach ($ad in $bulunan.Keys) { Bilgi "$($ad.PadRight(14)) $($bulunan[$ad])" }
if ($farkli.Count -gt 1) {
  Ekle-Engel "surum numaralari AYRISIK: $($farkli -join ' / ')"
} elseif ($farkli.Count -eq 1) {
  Tamam "bes kaynakta da $($farkli[0])"
}

$surum = ""
if ($farkli.Count -ge 1) { $surum = $farkli[0] }
if ($bulunan.ContainsKey("VERSION")) { $surum = $bulunan["VERSION"] }

$tagAdi = ""
if ($surum) {
  $tagAdi = "v$surum"
  $varMi = Invoke-GitOku -C $anaKok rev-parse --verify --quiet "refs/tags/$tagAdi"
  if ($varMi) {
    $tagCommit = (($varMi -join "").Trim())
    $tagHedef = (( Invoke-GitOku -C $anaKok rev-list -1 $tagAdi) -join "").Trim()
    $mainHead = (( Invoke-GitOku -C $anaKok rev-parse main) -join "").Trim()
    if ($tagHedef -eq $mainHead) {
      Ekle-Uyari "$tagAdi ZATEN VAR ve main'i gosteriyor -- yeni is yoksa cikacak bir sey yok"
    } else {
      Ekle-Engel "$tagAdi ZATEN VAR ama baska bir commit'i gosteriyor"
      Write-Host "          tag: $tagHedef" -ForegroundColor DarkGray
      Write-Host "          main: $mainHead" -ForegroundColor DarkGray
      Write-Host "          Surumu yukseltin (bes kaynakta birden) ya da tag'i tasiyin." -ForegroundColor DarkGray
    }
  } else {
    Tamam "$tagAdi henuz atilmamis"
  }
}

# ---------------------------------------------------------------------------
# 6. Testler (istege bagli)
# ---------------------------------------------------------------------------
if ($Test) {
  Baslik 6 "Testler (main uzerinde)"
  $py = Join-Path $anaKok "apps\backend-api\.venv\Scripts\python.exe"
  if (-not (Test-Path $py)) { $py = "python" }
  Bilgi "pytest"
  Push-Location (Join-Path $anaKok "apps\backend-api")
  try { $cikti = & $py -m pytest -q 2>&1; $kod = $LASTEXITCODE } finally { Pop-Location }
  if ($kod -ne 0) {
    Ekle-Engel "pytest DUSTU"
    foreach ($s in @($cikti | Select-Object -Last 12)) { Write-Host "          $s" -ForegroundColor DarkGray }
  } else { Tamam "pytest gecti" }

  Bilgi "npx tsc -b + npm test"
  Push-Location (Join-Path $anaKok "apps\frontend-web")
  try {
    $c1 = & npx tsc -b 2>&1; $k1 = $LASTEXITCODE
    $c2 = & npm test 2>&1;   $k2 = $LASTEXITCODE
  } finally { Pop-Location }
  if ($k1 -ne 0) {
    Ekle-Engel "tsc -b DUSTU"
    foreach ($s in @($c1 | Select-Object -Last 12)) { Write-Host "          $s" -ForegroundColor DarkGray }
  } else { Tamam "tsc -b gecti" }
  if ($k2 -ne 0) {
    Ekle-Engel "npm test DUSTU"
    foreach ($s in @($c2 | Select-Object -Last 12)) { Write-Host "          $s" -ForegroundColor DarkGray }
  } else { Tamam "npm test gecti" }
} else {
  Baslik 6 "Testler"
  Bilgi "atlandi -- kosmak icin: -Test"
}

# ---------------------------------------------------------------------------
# Sonuc
# ---------------------------------------------------------------------------
Write-Host ""
Write-Host ("-" * 68) -ForegroundColor DarkGray
if ($script:engeller.Count -gt 0) {
  Write-Host "TAG'A HAZIR DEGIL -- $($script:engeller.Count) engel" -ForegroundColor Red
  $i = 1
  foreach ($e in $script:engeller) { Write-Host "  $i. $e" -ForegroundColor Red; $i++ }
  if ($script:uyarilar.Count -gt 0) {
    Write-Host ""
    Write-Host "Ayrica $($script:uyarilar.Count) uyari (tag'i engellemez):" -ForegroundColor Yellow
    foreach ($u in $script:uyarilar) { Write-Host "  - $u" -ForegroundColor DarkYellow }
  }
  exit 1
}

Write-Host "TAG'A HAZIR" -ForegroundColor Green
if ($surum) { Write-Host "  Surum: $surum   Tag: $tagAdi" -ForegroundColor White }
if (-not $Test) { Write-Host "  (testler kosulmadi -- son tur icin: -Test)" -ForegroundColor DarkGray }
if ($script:uyarilar.Count -gt 0) {
  Write-Host ""
  Write-Host "$($script:uyarilar.Count) uyari:" -ForegroundColor Yellow
  foreach ($u in $script:uyarilar) { Write-Host "  - $u" -ForegroundColor DarkYellow }
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
  Write-Host "-Tag icin -Ozet gerekli. Depodaki tag'lar '$tagAdi $TIRE <ozet>' bicimindedir." -ForegroundColor Yellow
  exit 1
}
Write-Host ""
Write-Host "Tag atiliyor: $tagAdi" -ForegroundColor Cyan
& git -C $anaKok tag -a $tagAdi -m "$tagAdi $TIRE $Ozet"
if ($LASTEXITCODE -ne 0) { Write-Host "Tag atilamadi." -ForegroundColor Red; exit 1 }
& git -C $anaKok push origin $tagAdi
if ($LASTEXITCODE -ne 0) {
  Write-Host "Tag YERELDE atildi ama push DUSTU:" -ForegroundColor Yellow
  Write-Host "  git -C `"$anaKok`" push origin $tagAdi" -ForegroundColor White
  exit 1
}
Write-Host ""
Write-Host "$tagAdi atildi ve push edildi. Deploy tag'den tetiklenir." -ForegroundColor Green
