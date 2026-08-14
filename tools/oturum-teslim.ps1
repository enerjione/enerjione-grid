<#
.SYNOPSIS
  Bir oturumun isini DOGRULAR ve main'e alir. "Bitti" demenin tek yolu.

.DESCRIPTION
  NEDEN VAR
  ---------
  Izolasyon (`oturum-ac`) vardi, guncelleme (`oturum-birlestir`) vardi, TESLIM
  yoktu. Sonuc olculebilir: bu script yazilirken 16 acik oturum vardi, 13'unun
  dali main'e girmisti ama worktree'si hala aciktik; 44 olu dal duruyordu; ve
  main, origin'in 4 commit onundeydi -- yani yapilan is push bile edilmemisti.
  "Tek seferde tag'a cikayim" diyen birinin neyin eksik oldugunu tek tek
  aramasi gerekiyordu.

  Bu script bir oturumun isini SU SIRAYLA main'e tasir; herhangi bir adim
  duserse HICBIR SEY degismez (rebase geri alinir, merge yapilmaz):

    1. Commit'lenmemis is var mi          -> varsa dur, listele
    2. main'i origin'den tazele            -> ayrisma varsa dur
    3. Dali guncel main uzerine rebase      -> cakisirsa geri al, dur
    4. Migration borcu var mi               -> model degisti + migration yok = dur
    5. Migration zinciri tek basli mi       -> iki head = dur
    6. Testler (degisen alana gore)         -> pytest / tsc / npm test
    7. main'e merge (--no-ff, gecmis kalsin)
    8. origin/main'e push
    9. Ozet + worktree kapatma onerisi

  4. ADIM NEDEN ENGEL: bu script yazilirken acik duran bir dalin commit mesaji
  aynen soyleydi: "wip(alarm): cihaz silinince alarm gecmisi kalsin --
  MIGRATION EKSIK". Bunu bilen tek sey commit mesajiydi; kimse okumuyordu.

  7. ADIM NEDEN --no-ff: depo gecmisi zaten boyle (`merge: ... (feat/x)`).
  Merge commit'i "bu is hangi oturumdan geldi" bilgisini korur; tag notunu
  yazarken ise yarayan tek sey bu.

.PARAMETER Konu
  Oturum adi. Kendi worktree'nizin ICINDEN calistiriyorsaniz gerekmez --
  dizinden okunur.

.PARAMETER Mesaj
  Merge commit'inin ozeti. Verilmezse daldaki son commit'in basligi kullanilir.

.PARAMETER Prova
  Hicbir seyi degistirme: 1-6 arasi kontrolleri kosur, raporlar, cikar.

.PARAMETER TestAtla
  6. adimi atla. Testler baska yerde kosuldugunda ise yarar; ATLANDIGI
  ciktida ve merge mesajinda yazar.

.PARAMETER MigrationGerekmiyor
  4. adimi gecersiz kilar. Model dosyasina dokunuldu ama sema degismedi
  (yorum, tip ipucu, docstring) durumu icin.

.PARAMETER PushYok
  main'e merge et ama origin'e push etme.

.PARAMETER Kapat
  Teslim basariliysa worktree'yi de kapat (`oturum-kapat.ps1`).

.PARAMETER Zorla
  `-Konu` ile disaridan teslim ederken "o oturum su an calisiyor" korumasini
  gecersiz kilar. Rebase karsi tarafin commit'lerini yeniden yazar; ancak
  oturumun kapali oldugundan eminken kullanilir.

.EXAMPLE
  .\tools\oturum-teslim.ps1 -Prova
  .\tools\oturum-teslim.ps1
  .\tools\oturum-teslim.ps1 -Konu gw-silme -Mesaj "gateway silinince container da kalkiyor" -Kapat
#>
[CmdletBinding()]
param(
  [string]$Konu = "",
  [string]$Mesaj = "",
  [switch]$Prova,
  [switch]$TestAtla,
  [switch]$MigrationGerekmiyor,
  [switch]$PushYok,
  [switch]$Kapat,
  [switch]$Zorla
)

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "oturum-ortak.ps1")

function Yaz($metin, $renk = "Gray") { Write-Host $metin -ForegroundColor $renk }
function Baslik($n, $metin) { Write-Host ""; Write-Host "[$n] $metin" -ForegroundColor White }
function Tamam($metin) { Write-Host "    OK  $metin" -ForegroundColor Green }
function Engel($metin) { Write-Host "    !!  $metin" -ForegroundColor Red }
function Bilgi($metin) { Write-Host "    ..  $metin" -ForegroundColor DarkGray }

# Git'i CALISTIRIR (okumaz): ciktisi ekrana duz metin olarak akar, donen deger
# cikis kodudur. Invoke-GitOku ciktiyi yutar; yazma islemlerinde ne oldugunu
# gormek gerekiyor.
function Invoke-GitYaz {
  param([Parameter(ValueFromRemainingArguments = $true)][string[]]$Arg)
  $eski = $ErrorActionPreference
  $ErrorActionPreference = "Continue"
  try {
    & git @Arg 2>&1 | ForEach-Object {
      $s = if ($_ -is [System.Management.Automation.ErrorRecord]) { $_.Exception.Message } else { "$_" }
      Write-Host "        $s" -ForegroundColor DarkGray
    }
    return $LASTEXITCODE
  } finally { $ErrorActionPreference = $eski }
}

# Git'i SESSIZ ve HATASIZ calistirir; yalnizca cikis kodu doner.
#
# NEDEN: `& git ... 2>$null` YETMEZ. `$ErrorActionPreference = "Stop"` altinda
# native bir komutun stderr'i terminating error'a donusur ve script komple
# duser -- ilk calistirmada `git fetch` agsizken tam bunu yapti. Fetch'in
# dusmesi teslimi durdurmamali: yerel main hala gecerlidir, push zaten kendi
# hatasini raporlar.
function Invoke-GitSessiz {
  param([Parameter(ValueFromRemainingArguments = $true)][string[]]$Arg)
  $eski = $ErrorActionPreference
  $ErrorActionPreference = "Continue"
  try {
    & git @Arg 2>&1 | Out-Null
    return $LASTEXITCODE
  } catch {
    return 1
  } finally { $ErrorActionPreference = $eski }
}

function Dur($mesaj) {
  Write-Host ""
  Write-Host "TESLIM EDILMEDI." -ForegroundColor Red
  Write-Host $mesaj -ForegroundColor Yellow
  Write-Host ""
  Write-Host "Hicbir sey degismedi: dal oldugu yerde, main'e dokunulmadi." -ForegroundColor DarkGray
  exit 1
}

# ---------------------------------------------------------------------------
# 0. Nerede calisiyoruz
# ---------------------------------------------------------------------------
$anaKok = Get-AnaAgacKok
if (-not $anaKok) { throw "Git deposu bulunamadi." }

if ([string]::IsNullOrWhiteSpace($Konu)) {
  # Worktree icinden calistirilmis olmali; dizin adi oturum adidir.
  $burasi = Invoke-GitOku rev-parse --show-toplevel
  if (-not $burasi) { throw "Git deposu bulunamadi." }
  $burasi = ConvertTo-WindowsYol ($burasi -join "")
  if ($burasi -eq $anaKok) {
    Dur @"
Ana agactasiniz ve -Konu verilmedi.

Hangi oturumun isi teslim edilecek? Ya oturumun kendi penceresinden
calistirin ya da adini verin:
    tools\oturum-teslim.ps1 -Konu <ad>
Acik oturumlar: tools\oturum-kayit.ps1
"@
  }
  $yol = $burasi
  $Konu = Split-Path -Leaf $yol
} else {
  $yol = Join-Path $anaKok ".claude\worktrees\$Konu"
  if (-not (Test-Path $yol)) { Dur "Boyle bir oturum yok: $yol" }

  # BASKASININ AGACINDA MIYIZ: teslim rebase yapar, yani o dalin commit'lerini
  # YENIDEN YAZAR. Karsi oturum o sirada calisiyorsa HEAD'i ayagindan cekilir.
  # Bu yuzden -Konu ile disaridan teslim, ancak agac SESSIZSE yapilir.
  # (Kendi penceresinden calistirilan teslim bu kontrole hic girmez.)
  $canli = @(Get-Pencereler | Where-Object {
    $_ -and $_.cwd -and (ConvertTo-WindowsYol $_.cwd).StartsWith((ConvertTo-WindowsYol $yol), [System.StringComparison]::OrdinalIgnoreCase)
  })
  $taze = @($canli | Where-Object {
    try { ((Get-Date).ToUniversalTime() - [datetime]::Parse($_.gorulen).ToUniversalTime()).TotalMinutes -lt 30 } catch { $false }
  })
  if ($taze.Count -gt 0 -and -not $Zorla -and -not $Prova) {
    $ne = @($taze | ForEach-Object { if ($_.baslik) { $_.baslik } else { "(baslik yok)" } }) -join " | "
    Dur @"
'$Konu' oturumu SU AN CALISIYOR (son 30 dk icinde hareket var):
    $ne

Teslim rebase yapar -- calisan bir oturumun commit'lerini yeniden yazmak
onun HEAD'ini ayagindan ceker. Once o oturuma haber verin:
    tools\oturum-mesaj.ps1 -Kime $Konu -Mesaj "tag hazirligi: /teslim calistir"

Oturumun kapali oldugundan eminseniz: -Zorla
Yalnizca durumu gormek icin: -Prova
"@
  }
}

$dal = Invoke-GitOku -C $yol rev-parse --abbrev-ref HEAD
if (-not $dal) { Dur "Dal okunamadi: $yol" }
$dal = ($dal -join "").Trim()
if ($dal -eq "main" -or $dal -eq "HEAD") {
  Dur "Oturum '$Konu' main uzerinde (ya da detached). Teslim edilecek bir dal yok."
}

Write-Host ""
Write-Host "TESLIM  $Konu  [$dal]" -ForegroundColor Cyan
if ($Prova) { Write-Host "        (PROVA -- hicbir sey degistirilmeyecek)" -ForegroundColor DarkGray }

# ---------------------------------------------------------------------------
# 1. Commit'lenmemis is
# ---------------------------------------------------------------------------
Baslik 1 "Commit'lenmemis is var mi"
$durum = Invoke-GitOku -C $yol status --porcelain
$kirli = @(@($durum) | Where-Object {
  $_ -and -not (Test-AltyapiDosyasi ($_.Substring(2).Trim().Trim('"')))
})
if ($kirli.Count -gt 0) {
  foreach ($k in ($kirli | Select-Object -First 20)) { Engel $k }
  Dur @"
Once bu dosyalari commit'leyin (kendi worktree'nizde genis komut serbest):
    git -C "$yol" add -A
    git -C "$yol" commit -m "feat(...): ..."
Teslim edilmemis bir dosya, tag'a girmeyen bir dosyadir.
"@
}
Tamam "agac temiz"

# ---------------------------------------------------------------------------
# 2. main'i tazele
# ---------------------------------------------------------------------------
Baslik 2 "main guncel mi"
Bilgi "origin fetch ediliyor"
$fetchKod = Invoke-GitSessiz -C $yol fetch origin --quiet
$cevrimdisi = ($fetchKod -ne 0)
if ($cevrimdisi) {
  Bilgi "origin'e ULASILAMADI -- yerel main ile devam ediliyor"
}

$uzakVar = (-not $cevrimdisi) -and ($null -ne (Invoke-GitOku -C $anaKok rev-parse --verify --quiet "origin/main"))
if ($uzakVar) {
  $sayim = Invoke-GitOku -C $anaKok rev-list --left-right --count "origin/main...main"
  $uzakOnde = 0; $yerelOnde = 0
  if ($sayim) {
    $p = (($sayim -join "") -split "\s+") | Where-Object { $_ -match '^\d+$' }
    if ($p.Count -ge 2) { $uzakOnde = [int]$p[0]; $yerelOnde = [int]$p[1] }
  }
  if ($uzakOnde -gt 0 -and $yerelOnde -gt 0) {
    Dur @"
Yerel main ile origin/main AYRISMIS ($yerelOnde yerel / $uzakOnde uzak commit).
Bunu teslim akisi kendiliginden cozmez -- yanlis cozum baskasinin isini
dusurur. Once ana agacta durumu netlestirin:
    git -C "$anaKok" log --oneline --graph origin/main...main
"@
  }
  if ($uzakOnde -gt 0) {
    if ($Prova) {
      Bilgi "main $uzakOnde commit geride (prova: ileri sarilmadi)"
    } else {
      Bilgi "main $uzakOnde commit geride, ileri sariliyor"
      $kod = Invoke-GitYaz -C $anaKok merge --ff-only origin/main
      if ($kod -ne 0) { Dur "main ileri sarilamadi. Ana agacta commit'lenmemis is olabilir." }
    }
  }
  if ($yerelOnde -gt 0) { Bilgi "yerel main origin'in $yerelOnde commit onunde (push edilmemis is)" }
}
Tamam "main hazir"

# ---------------------------------------------------------------------------
# 3. Rebase
# ---------------------------------------------------------------------------
Baslik 3 "Dal guncel main uzerine tasiniyor"
$geride = 0
$sayim2 = Invoke-GitOku -C $yol rev-list --left-right --count "main...HEAD"
$ileride = 0
if ($sayim2) {
  $p2 = (($sayim2 -join "") -split "\s+") | Where-Object { $_ -match '^\d+$' }
  if ($p2.Count -ge 2) { $geride = [int]$p2[0]; $ileride = [int]$p2[1] }
}
if ($ileride -eq 0) {
  Write-Host ""
  Write-Host "Bu dalda main'de OLMAYAN commit yok -- teslim edilecek is bulunmuyor." -ForegroundColor Yellow
  Write-Host "Oturum bosa acik duruyor olabilir:" -ForegroundColor White
  Write-Host "    tools\oturum-kapat.ps1 -Konu $Konu"
  exit 0
}
Bilgi "$ileride commit teslim edilecek, dal $geride commit geride"

if ($geride -gt 0) {
  # Once SANAL birlestirme: `git rebase` cakisirsa agaci yarim birakir.
  # SANAL birlestirme: diske dokunmadan "cakisir mi" sorusunu cevaplar.
  #
  # `Invoke-GitOku` BURADA KULLANILMAZ: `merge-tree` cakisma bulunca cikis kodu
  # 1 doner, Invoke-GitOku ise sifirdan farkli kodda $null verir -- yani tam
  # cakisma varken "cakisma yok" denirdi. (Ilk kosuda oldu; ayni hata
  # oturum-birlestir.ps1'de de vardi.) Cikis kodu: 0 temiz, 1 cakisma,
  # digerleri "prova yapilamadi".
  #
  # Degisken adi `$prova` OLAMAZ: PowerShell degisken adlari buyuk/kucuk harf
  # duyarsizdir ve `-Prova` switch'ini ezip "SwitchParameter'a Object[]
  # atanamaz" hatasi verir (bu da ilk kosuda oldu).
  $eskiTercih = $ErrorActionPreference
  $ErrorActionPreference = "Continue"
  try {
    $provaCikti = & git -C $yol merge-tree --write-tree --name-only main HEAD 2>&1
    $provaKod = $LASTEXITCODE
  } finally { $ErrorActionPreference = $eskiTercih }

  if ($provaKod -gt 1) {
    Bilgi "prova yapilamadi (git merge-tree desteklemiyor olabilir); rebase dogrudan denenecek"
  }
  if ($provaKod -eq 1) {
    # --name-only ciktisinda ilk satir agac OID'i, kalanlar cakisan dosyalar.
    $catisan = @(@($provaCikti) | Select-Object -Skip 1 | Where-Object { $_ -and "$_" -notmatch '^(Auto-merging|hint:)' })
    foreach ($c in ($catisan | Select-Object -First 10)) { Engel "$c" }
    Dur @"
Dal main ile CAKISIYOR. Cakismayi ancak isi yazan cozebilir:
    cd "$yol"
    git rebase main          # cakismalari coz
    git rebase --continue
    tools\oturum-teslim.ps1  # sonra tekrar
"@
  }
  if ($Prova) {
    Bilgi "prova: rebase yapilmadi (cakisma yok, temiz gecerdi)"
  } else {
    $kod = Invoke-GitYaz -C $yol rebase main
    if ($kod -ne 0) {
      & git -C $yol rebase --abort 2>$null | Out-Null
      Dur "Rebase dustu; agac yarim birakilmadi, geri alindi."
    }
    Tamam "dal artik main ustunde"
  }
} else {
  Tamam "dal zaten main ustunde"
}

# ---------------------------------------------------------------------------
# 4-5. Migration borcu ve zincir
# ---------------------------------------------------------------------------
# Once degiskene, SONRA filtre: Invoke-GitOku diziyi `,@(...)` ile donduruyor;
# dogrudan pipe'a verilirse karsi tarafa tek nesne olarak butun dizi gider.
$hamDegisen = Invoke-GitOku -C $yol diff --name-only "main...HEAD"
$degisen = @(@($hamDegisen) | Where-Object { $_ })

Baslik 4 "Migration borcu"
$modelDegisti = @($degisen | Where-Object { $_ -like "apps/backend-api/app/models/*" })
$migrationVar = @($degisen | Where-Object { $_ -like "*alembic_migrations/versions/*" })
if ($modelDegisti.Count -gt 0 -and $migrationVar.Count -eq 0 -and -not $MigrationGerekmiyor) {
  foreach ($m in ($modelDegisti | Select-Object -First 10)) { Engel $m }
  Dur @"
Model degismis ama yeni migration YOK. Yukseltilen bir sahada bu tablo/kolon
hic olusmaz; belirtisi de sema hatasi degil, sessiz yanlis veridir.

    cd "$yol\apps\backend-api"
    alembic revision --autogenerate -m "aciklama"   # sonra gozden gecirin

Sema gercekten degismediyse (yorum/tip ipucu): -MigrationGerekmiyor
"@
}
if ($modelDegisti.Count -eq 0) { Tamam "model dosyasi degismemis" }
elseif ($MigrationGerekmiyor) { Tamam "model degismis, sema degismedigi BEYAN EDILDI (-MigrationGerekmiyor)" }
else { Tamam "$($migrationVar.Count) yeni migration dosyasi var" }

Baslik 5 "Migration zinciri"
$vDizin = Join-Path $yol "apps\backend-api\alembic_migrations\versions"
if (Test-Path $vDizin) {
  # Zincirin BASI: hicbir dosyanin down_revision'i olarak gecmeyen revision.
  # Iki bas = iki dal ayni ata uzerine migration yazmis; `alembic upgrade head`
  # o noktadan sonra HANGI kolu surecegini bilemez ve tek Postgres'te
  # `alembic_version` HERKES icin bozulur (bkz. CLAUDE.md > Bilinen sinir).
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
    foreach ($b in $basliklar) { Engel "head: $b" }
    Dur @"
Migration zinciri $($basliklar.Count) BASLI. Iki oturum ayni ata uzerine
migration yazmis. Yenisinin down_revision'ini digerinin revision'ina baglayin,
dosya adindaki sirayi da duzeltin; sonra tekrar teslim edin.
"@
  }
  if ($basliklar.Count -eq 1) { Tamam "tek basli ($($basliklar[0]))" }
  else { Bilgi "zincir okunamadi ($($rev.Count) revision) -- atlaniyor" }
} else {
  Bilgi "versions dizini yok -- atlaniyor"
}

# ---------------------------------------------------------------------------
# 6. Testler
# ---------------------------------------------------------------------------
Baslik 6 "Testler"
$backendDegisti = @($degisen | Where-Object { $_ -like "apps/backend-api/*" }).Count -gt 0
$frontDegisti = @($degisen | Where-Object { $_ -like "apps/frontend-web/*" }).Count -gt 0
$testOzeti = New-Object System.Collections.ArrayList

if ($TestAtla) {
  Bilgi "-TestAtla verildi, atlandi"
  [void]$testOzeti.Add("ATLANDI (-TestAtla)")
} elseif (-not $backendDegisti -and -not $frontDegisti) {
  Bilgi "apps/ altinda degisiklik yok"
  [void]$testOzeti.Add("gerekmedi")
} else {
  # Worktree'de .venv YOK (kopyalanmiyor); ana agacinki kullanilir. Kod yolu
  # cwd'den cozuldugu icin testler DOGRU agacta kosar.
  if ($backendDegisti) {
    $py = Join-Path $anaKok "apps\backend-api\.venv\Scripts\python.exe"
    if (-not (Test-Path $py)) { $py = "python" }
    Bilgi "pytest"
    Push-Location (Join-Path $yol "apps\backend-api")
    try {
      $cikti = & $py -m pytest -q 2>&1
      $kod = $LASTEXITCODE
    } finally { Pop-Location }
    $son = @($cikti | Select-Object -Last 12)
    foreach ($s in $son) { Write-Host "        $s" -ForegroundColor DarkGray }
    if ($kod -ne 0) { Dur "pytest DUSTU. Once testleri gecirin." }
    Tamam "pytest gecti"
    [void]$testOzeti.Add("pytest")
  }
  if ($frontDegisti) {
    # `npx tsc -b` -- `--noEmit` DEGIL: kok tsconfig solution-style (files: []),
    # --noEmit hicbir dosyayi kontrol etmez ve HER ZAMAN 0 doner.
    Bilgi "npx tsc -b"
    Push-Location (Join-Path $yol "apps\frontend-web")
    try {
      $cikti = & npx tsc -b 2>&1
      $kod = $LASTEXITCODE
      if ($kod -eq 0) {
        Bilgi "npm test"
        $cikti2 = & npm test 2>&1
        $kod2 = $LASTEXITCODE
      }
    } finally { Pop-Location }
    if ($kod -ne 0) {
      foreach ($s in @($cikti | Select-Object -Last 15)) { Write-Host "        $s" -ForegroundColor DarkGray }
      Dur "Tip kontrolu DUSTU."
    }
    Tamam "tsc -b gecti"
    if ($kod2 -ne 0) {
      foreach ($s in @($cikti2 | Select-Object -Last 15)) { Write-Host "        $s" -ForegroundColor DarkGray }
      Dur "npm test DUSTU."
    }
    Tamam "npm test gecti"
    [void]$testOzeti.Add("tsc + npm test")
  }
}

# ---------------------------------------------------------------------------
# Prova burada biter
# ---------------------------------------------------------------------------
if ($Prova) {
  Write-Host ""
  Write-Host "PROVA TEMIZ -- teslim edilebilir." -ForegroundColor Green
  Write-Host "Gercekten teslim etmek icin: tools\oturum-teslim.ps1 -Konu $Konu" -ForegroundColor White
  exit 0
}

# ---------------------------------------------------------------------------
# 7. main'e merge
# ---------------------------------------------------------------------------
Baslik 7 "main'e aliniyor"
$anaDal = Invoke-GitOku -C $anaKok rev-parse --abbrev-ref HEAD
$anaDal = ($anaDal -join "").Trim()
if ($anaDal -ne "main") {
  Dur "Ana agac '$anaDal' dalinda, main'de degil. Teslim main'e yapilir; once ana agaci main'e alin."
}

if ([string]::IsNullOrWhiteSpace($Mesaj)) {
  $son = Invoke-GitOku -C $yol log -1 --pretty=%s
  $Mesaj = ($son -join "").Trim()
  # "wip(...)" / "fix(...)" onekini merge basliginda tasimanin anlami yok.
  $Mesaj = $Mesaj -replace '^\s*(wip|feat|fix|chore|refactor|docs|test)(\([^)]*\))?:\s*', ''
}
$merceMesaj = "merge: $Mesaj ($dal)"
if ($TestAtla) { $merceMesaj = "$merceMesaj [testler atlandi]" }

$kod = Invoke-GitYaz -C $anaKok merge --no-ff $dal -m $merceMesaj
if ($kod -ne 0) {
  Dur @"
Merge dustu. En olasi sebep: ANA AGACTA commit'lenmemis dosya var ve merge
onlara dokunuyor. Ana agaci temizleyin, sonra tekrar deneyin.
    git -C "$anaKok" status
"@
}
Tamam $merceMesaj

# ---------------------------------------------------------------------------
# 8. Push
# ---------------------------------------------------------------------------
Baslik 8 "origin/main"
if ($PushYok) {
  Bilgi "-PushYok verildi, push edilmedi"
  Yaz "        DIKKAT: diger oturumlar origin/main'e gore rebase oluyor;" "Yellow"
  Yaz "        push edilmeyen is onlara gorunmez." "Yellow"
} elseif ($cevrimdisi) {
  Yaz "        origin'e ULASILAMIYOR. Is main'de, origin'de DEGIL:" "Yellow"
  Yaz "        ag gelince: git -C `"$anaKok`" push origin main" "White"
} elseif (-not $uzakVar) {
  Bilgi "origin/main yok (uzak tanimli degil) -- push atlandi"
} else {
  $kod = Invoke-GitYaz -C $anaKok push origin main
  if ($kod -ne 0) {
    Write-Host ""
    Yaz "UYARI: merge YAPILDI ama push DUSTU. Is main'de, origin'de degil:" "Yellow"
    Yaz "    git -C `"$anaKok`" push origin main" "White"
  } else {
    Tamam "push edildi"
  }
}

# ---------------------------------------------------------------------------
# 9. Ozet
# ---------------------------------------------------------------------------
Write-Host ""
Write-Host "TESLIM EDILDI: $Konu -> main" -ForegroundColor Green
Write-Host "  $ileride commit  |  testler: $(@($testOzeti) -join ', ')" -ForegroundColor DarkGray

if ($Kapat) {
  Write-Host ""
  Bilgi "worktree kapatiliyor"
  # Kapatma ANA AGACTAN kosulmali (script kendi dizinini silemez).
  & powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $anaKok "tools\oturum-kapat.ps1") -Konu $Konu
} else {
  Write-Host ""
  Write-Host "Sirada:" -ForegroundColor White
  Write-Host "    tools\oturum-kapat.ps1 -Konu $Konu    # worktree'yi kapat (ANA AGACTAN)"
  Write-Host "    tools\surum-hazir.ps1                 # tag'a hazir miyiz"
}
