<#
.SYNOPSIS
  "Tag'a hazir miyiz" sorusunun TEK hesaplama yeri. Kitaplik; kendi basina
  bir sey yazdirmaz.

.DESCRIPTION
  NEDEN AYRI DOSYA
  ----------------
  Ayni soruyu iki yuz soruyor: `surum-hazir.ps1` (terminal raporu) ve
  `oturum-panel.ps1` (canli panel bandi). Hesabi iki yere kopyalamak, iki
  cevabin bir gun ayrismasi demekti -- ve ayrisan iki cevaptan hangisinin
  dogru oldugunu kimse bilemez. Ikisi de burayi cagirir.

  HIZ: panel 4 saniyede bir tazeliyor. Bu yuzden
    * Dal taramasi TEK git cagrisiyla yapilir (`branch --no-merged`), dal
      basina rev-list DEGIL.
    * Dosyadan okunan seyler (surum numaralari, migration zinciri) surec
      icinde $script:ONBELLEK_SN saniye hatirlanir.
    * `git fetch` YALNIZCA -Fetch verilirse yapilir. Panelin her turda aga
      cikmasi kabul edilemezdi (agsiz makinede 4 saniyede bir donma).

  DONEN NESNE
    anaDal, anaKirli, anaIzlenmeyen
    cevrimdisi, uzakVar, yerelOnde, uzakOnde
    bekleyen[]  -- main'de olmayan commit tasiyan dallar (+ varsa worktree adi)
    olu[]       -- main'e girmis ama duran dallar
    kirliAgac[] -- commit'lenmemis dosyasi olan worktree'ler
    bosAgac[]   -- isi main'e girdigi halde acik duran worktree'ler
    migrationBas[], migrationSayi
    surumler{}, surum, tagAdi, tagVar, tagMaindeMi
    engeller[], uyarilar[]
#>

. (Join-Path $PSScriptRoot "oturum-ortak.ps1")

$script:SURUM_ONBELLEK_SN = 30
$script:surumOnbellek = $null
$script:surumOnbellekZaman = [datetime]::MinValue
$script:notOnbellek = $null
$script:notOnbellekAnahtar = ""

<#
  Migration zincirinin BASLARI: hicbir dosyanin down_revision'i olarak
  gecmeyen revision'lar. Birden fazlaysa iki dal ayni ata uzerine migration
  yazmis demektir; tek Postgres'te `alembic_version` HERKES icin bozulur.
#>
function Get-MigrationZinciri {
  param([Parameter(Mandatory = $true)][string]$Dizin)
  if (-not (Test-Path $Dizin)) { return [pscustomobject]@{ baslar = @(); sayi = 0 } }

  $rev = New-Object System.Collections.Generic.HashSet[string]
  $alt = New-Object System.Collections.Generic.HashSet[string]
  foreach ($f in (Get-ChildItem $Dizin -Filter *.py -File)) {
    foreach ($satir in (Get-Content $f.FullName -Encoding UTF8)) {
      if ($satir -match '^\s*revision\s*(?::[^=]+)?=\s*[''"]([^''"]+)[''"]') { [void]$rev.Add($Matches[1]) }
      elseif ($satir -match '^\s*down_revision\s*(?::[^=]+)?=\s*[''"]([^''"]+)[''"]') { [void]$alt.Add($Matches[1]) }
    }
  }
  return [pscustomobject]@{
    baslar = @($rev | Where-Object { -not $alt.Contains($_) })
    sayi   = $rev.Count
  }
}

<#
  Surum numarasi BES yerde yaziyor. Biri unutulursa arayuz bir surum, imaj
  tag'i baska bir surum gosterir -- "hangi surum sahada" sorusu cevapsiz kalir.
#>
function Get-SurumKaynaklari {
  param([Parameter(Mandatory = $true)][string]$Kok)

  # `S.r.m`: dosyada "Surum" da Turkce u ile "Surum" de yazabilir. Desene
  # ASCII disi karakter KOYULMAZ (PowerShell 5.1 BOM'suz .ps1'i ANSI okur).
  $kaynaklar = @(
    @{ Ad = "VERSION";      Yol = "VERSION";                             Desen = '^\s*([0-9]+\.[0-9]+\.[0-9]+)\s*$' },
    @{ Ad = "package.json"; Yol = "apps\frontend-web\package.json";      Desen = '"version"\s*:\s*"([0-9]+\.[0-9]+\.[0-9]+)"' },
    @{ Ad = "config.py";    Yol = "apps\backend-api\app\core\config.py"; Desen = '_FALLBACK_APP_VERSION\s*=\s*"([0-9]+\.[0-9]+\.[0-9]+)"' },
    @{ Ad = "CLAUDE.md";    Yol = "CLAUDE.md";                           Desen = '(?i)\*\*S.r.m:?\*\*\s*([0-9]+\.[0-9]+\.[0-9]+)' },
    @{ Ad = "CHANGELOG.md"; Yol = "CHANGELOG.md";                        Desen = '^##\s*\[([0-9]+\.[0-9]+\.[0-9]+)\]' }
  )

  $bulunan = [ordered]@{}
  $eksik = New-Object System.Collections.ArrayList
  foreach ($k in $kaynaklar) {
    $tam = Join-Path $Kok $k.Yol
    if (-not (Test-Path $tam)) { [void]$eksik.Add("$($k.Ad) bulunamadi"); continue }
    $ham = Get-Content $tam -Raw -Encoding UTF8
    # CHANGELOG'da ILK surum basligi en yeni surumdur; digerlerinde tek eslesme.
    $m = [regex]::Match($ham, $k.Desen, [System.Text.RegularExpressions.RegexOptions]::Multiline)
    if ($m.Success) { $bulunan[$k.Ad] = $m.Groups[1].Value }
    else { [void]$eksik.Add("$($k.Ad) icinde surum numarasi okunamadi") }
  }
  return [pscustomobject]@{ bulunan = $bulunan; eksik = @($eksik) }
}

<#
.SYNOPSIS
  Bu tag'de NE CIKIYOR: son tag'den bu yana main'e giren is, "Eklendi" ve
  "Duzeltildi" diye ayrilmis.

.DESCRIPTION
  KAYNAK: `git log --first-parent <sonTag>..main`.

  `--first-parent` SART: teslim akisi her isi `--no-ff` merge ile aliyor, yani
  main'de her oturum icin BIR merge commit'i var. First-parent olmadan o
  merge'in icindeki tum ara commit'ler de listeye girer ve "ne cikti" sorusu
  40 satirlik bir commit dokumune donusur -- kimse okumaz. Bu haliyle her
  satir bir ISTIR.

  SINIFLANDIRMA: merge basligindaki dal adi (`merge: ... (feat/x)`) ya da
  conventional commit oneki. Dal adi once gelir: teslim mesaji ozeti tasir,
  onek ise dalin kendi ic commit'inden kalmis olabilir.

  CHANGELOG [Yayinlanmamis] bolumu ayrica dondurulur -- insanin yazdigi metin
  her zaman commit basligindan iyidir; ikisi birlikte gosterilir.
#>
function Get-SurumNotlari {
  param(
    [Parameter(Mandatory = $true)][string]$Kok,
    [string]$SonTag = ""
  )

  if ([string]::IsNullOrWhiteSpace($SonTag)) {
    $t = Invoke-GitOku -C $Kok describe --tags --abbrev=0 main
    if ($t) { $SonTag = (($t -join "").Trim()) }
  }

  # ONBELLEK ANAHTARI main'in SHA'si: panel 4 saniyede bir cagiriyor ve her
  # merge icin ayrica git'e sorulacak. Zamana degil ICERIGE bagli anahtar
  # sectik -- main degismedikce sonuc da degismez, bayatlama ihtimali yok.
  $mainSha = ((Invoke-GitOku -C $Kok rev-parse main) -join "").Trim()
  $anahtar = "$Kok|$SonTag|$mainSha"
  if ($script:notOnbellekAnahtar -eq $anahtar -and $script:notOnbellek) {
    return $script:notOnbellek
  }

  $aralik = "main"
  if ($SonTag) { $aralik = "$SonTag..main" }

  # Ayirac U+001F (unit separator): commit basliginda gecemeyecek tek karakter.
  # `[char]0x1F` YAZILIR, "`u{1f}" DEGIL: o kacis PowerShell 6 ile geldi, 5.1
  # onu duz metin sayar ve boyle bir ayirac hicbir zaman eslesmez -- liste
  # sessizce BOS doner (ilk kosuda tam bu oldu, hata da vermedi).
  $AYIRAC = [char]0x1F
  $ham = Invoke-GitOku -C $Kok log --first-parent --pretty=format:"%h%x1f%s" $aralik
  $eklendi = New-Object System.Collections.ArrayList
  $duzeltildi = New-Object System.Collections.ArrayList
  $diger = New-Object System.Collections.ArrayList

  foreach ($satir in @($ham)) {
    if (-not $satir) { continue }
    $parca = "$satir".Split($AYIRAC)
    if ($parca.Count -lt 2) { continue }
    $sha = $parca[0]; $konu = $parca[1]

    # Surum yukseltme commit'leri "ne cikti" listesinde yer tutmaz.
    if ($konu -match '^\s*chore\(release\)') { continue }

    # Teslim, merge basligina koseli parantezli not ekleyebiliyor
    # ("[testler atlandi]"). Bu ek ONCE atilmali: dal adi desenini sona
    # bagladigimiz icin, ekli bir baslikta dal HIC bulunamiyor ve is
    # siniflandirilamayip "Diger"e dusuyordu.
    $temizKonu = $konu -replace '\s*\[[^\]]*\]\s*$', ''

    $dal = ""
    if ($temizKonu -match '\(([^)]*/[^)]*)\)\s*$') { $dal = $Matches[1] }

    # Merge basligindan "merge: " onekini ve sondaki dal parantezini at:
    # geriye isin kendi cumlesi kalir.
    $metin = $temizKonu -replace '^\s*merge:\s*', ''
    $metin = $metin -replace '\s*\([^)]*/[^)]*\)\s*$', ''
    $metin = $metin -replace '^\s*(feat|fix|wip|chore|refactor|docs|test|style|perf)(\([^)]*\))?:\s*', ''
    $metin = $metin.Trim()
    if (-not $metin) { $metin = $konu }

    $kayit = [pscustomobject]@{ sha = $sha; metin = $metin; dal = $dal }

    # TIP: dal adi tek basina YETMEZ. `feat/denetim-duzeltme` dalinda tasinan
    # is aslinda `fix(sema): ...` idi; dal adina bakan ilk surum onu "Eklendi"
    # diye yazdi.
    #
    # Merge ise DALIN TEPE COMMIT'ine bakilir (`<sha>^2`). Iki sebeple:
    #   1. Teslim, merge ozetini zaten o commit'in basligindan uretiyor --
    #      yani listede okudugunuz cumle ile tip ayni yerden gelir.
    #   2. Merge'in ICINDEKI butun commit'leri saymak yaniltiyor: rebase
    #      edilmeden birlestirilmis eski dallar, eski main'den devraldiklari
    #      ilgisiz commit'leri de tasiyor (denetim-duzeltme merge'inde uc
    #      tane vardi ve biri `feat` idi).
    $tip = ""
    if ($dal) {
      $tepe = Invoke-GitOku -C $Kok log -1 --pretty=format:"%s" "$sha^2"
      if ($tepe) {
        $tepeMetin = ($tepe -join "").Trim()
        if ($tepeMetin -match '^\s*feat(\(|:)') { $tip = "feat" }
        elseif ($tepeMetin -match '^\s*fix(\(|:)') { $tip = "fix" }
      }
    }
    if (-not $tip) {
      if ($konu -match '^\s*feat(\(|:)') { $tip = "feat" }
      elseif ($konu -match '^\s*fix(\(|:)') { $tip = "fix" }
      elseif ($dal -like "feat/*") { $tip = "feat" }
      elseif ($dal -like "fix/*") { $tip = "fix" }
    }

    if ($tip -eq "feat") { [void]$eklendi.Add($kayit) }
    elseif ($tip -eq "fix") { [void]$duzeltildi.Add($kayit) }
    else { [void]$diger.Add($kayit) }
  }

  # CHANGELOG'un [Yayinlanmamis] bolumu: bir sonraki basliga kadar olan kisim.
  $yayinlanmamis = ""
  $clYol = Join-Path $Kok "CHANGELOG.md"
  if (Test-Path $clYol) {
    $cl = Get-Content $clYol -Raw -Encoding UTF8
    $m = [regex]::Match($cl, '(?ms)^##\s*\[Yay.nlanmam..\]\s*\r?\n(.*?)(?=^##\s*\[)')
    if ($m.Success) {
      $yayinlanmamis = ($m.Groups[1].Value -replace '(?m)^\s*---\s*$', '').Trim()
    }
  }

  $sonuc = [pscustomobject]@{
    sonTag        = $SonTag
    eklendi       = @($eklendi)
    duzeltildi    = @($duzeltildi)
    diger         = @($diger)
    toplam        = $eklendi.Count + $duzeltildi.Count + $diger.Count
    yayinlanmamis = $yayinlanmamis
  }
  $script:notOnbellekAnahtar = $anahtar
  $script:notOnbellek = $sonuc
  return $sonuc
}

<#
.SYNOPSIS
  Butun kontrolleri kosar ve yapisal sonucu dondurur.
.PARAMETER Fetch
  origin'i tazele. Terminal raporu evet der, panel HAYIR (bkz. HIZ).
.PARAMETER Tazele
  Dosyadan okunan bolumun onbellegini atla.
#>
function Get-SurumDurumu {
  param([switch]$Fetch, [switch]$Tazele)

  $kok = Get-AnaAgacKok
  if (-not $kok) { return $null }

  $engeller = New-Object System.Collections.ArrayList
  $uyarilar = New-Object System.Collections.ArrayList

  # --- 1. Ana agac ---------------------------------------------------------
  $anaDal = ((Invoke-GitOku -C $kok rev-parse --abbrev-ref HEAD) -join "").Trim()
  $anaDurum = Invoke-GitOku -C $kok status --porcelain
  $anaKirli = @(@($anaDurum) | Where-Object {
    $_ -and $_.Substring(0, 2) -ne "??" -and -not (Test-AltyapiDosyasi ($_.Substring(2).Trim().Trim('"')))
  })
  $anaIzlenmeyen = @(@($anaDurum) | Where-Object {
    $_ -and $_.Substring(0, 2) -eq "??" -and -not (Test-AltyapiDosyasi ($_.Substring(2).Trim().Trim('"')))
  })

  if ($anaDal -ne "main") { [void]$engeller.Add("ana agac '$anaDal' dalinda; tag main'den atilir") }
  if ($anaKirli.Count -gt 0) { [void]$engeller.Add("ana agacta commit'lenmemis $($anaKirli.Count) dosya") }
  if ($anaIzlenmeyen.Count -gt 0) { [void]$uyarilar.Add("ana agacta izlenmeyen $($anaIzlenmeyen.Count) dosya (tag'a girmez)") }

  # --- 2. origin -----------------------------------------------------------
  # `& git ... 2>$null` YETMEZ: "Stop" tercihi altinda native komutun stderr'i
  # terminating error olur ve script agsizken komple duser.
  $cevrimdisi = $false
  if ($Fetch) {
    $eskiTercih = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try { & git -C $kok fetch origin --quiet 2>&1 | Out-Null; $kod = $LASTEXITCODE }
    catch { $kod = 1 }
    finally { $ErrorActionPreference = $eskiTercih }
    $cevrimdisi = ($kod -ne 0)
  }

  $uzakVar = $null -ne (Invoke-GitOku -C $kok rev-parse --verify --quiet "origin/main")
  $yerelOnde = 0; $uzakOnde = 0
  if ($uzakVar) {
    $sayim = Invoke-GitOku -C $kok rev-list --left-right --count "origin/main...main"
    if ($sayim) {
      $p = (($sayim -join "") -split "\s+") | Where-Object { $_ -match '^\d+$' }
      if ($p.Count -ge 2) { $uzakOnde = [int]$p[0]; $yerelOnde = [int]$p[1] }
    }
    if ($yerelOnde -gt 0 -and $uzakOnde -gt 0) {
      [void]$engeller.Add("main ile origin/main AYRISMIS ($yerelOnde yerel / $uzakOnde uzak)")
    } elseif ($yerelOnde -gt 0) {
      # Tag uzaga push edilir; isaret ettigi commit uzakta yoksa deploy onu cekemez.
      [void]$engeller.Add("main, origin'in $yerelOnde commit ONUNDE -- push edilmemis is var")
    } elseif ($uzakOnde -gt 0) {
      [void]$engeller.Add("main, origin'in $uzakOnde commit GERISINDE")
    }
  }
  if ($cevrimdisi) { [void]$uyarilar.Add("origin'e ulasilamadi -- uzak karsilastirmasi yapilamadi") }

  # --- 3. Teslim edilmemis is ----------------------------------------------
  # DEFTERE DEGIL GIT'E bakilir: is, worktree'si kapatilmis bir dalda da
  # bekliyor olabilir. Tek dogru kaynak commit grafigidir.
  #
  # Dal basina `rev-list` YAPILMAZ: `branch --no-merged` tek cagriyla ayni
  # ayrimi verir. Panel 4 saniyede bir cagiriyor.
  $hamAcik = Invoke-GitOku -C $kok branch --no-merged main --format="%(refname:short)"
  $acikDallar = @(@($hamAcik) | Where-Object { $_ -and $_.Trim() -ne "" } | ForEach-Object { $_.Trim() })
  $hamOlu = Invoke-GitOku -C $kok branch --merged main --format="%(refname:short)"
  $olu = @(@($hamOlu) | Where-Object { $_ -and $_.Trim() -ne "" -and $_.Trim() -ne "main" } | ForEach-Object { $_.Trim() })

  $worktreeler = @(Get-WorktreeListesi | Where-Object { -not $_.AnaMi })
  $dalKonu = @{}
  foreach ($w in $worktreeler) { if ($w.Dal) { $dalKonu[$w.Dal] = (Split-Path -Leaf $w.Yol) } }

  $bekleyen = New-Object System.Collections.ArrayList
  foreach ($d in $acikDallar) {
    $n = Invoke-GitOku -C $kok rev-list --count "main..$d"
    $sayi = 0
    if ($n) { try { $sayi = [int](($n -join "").Trim()) } catch { $sayi = 0 } }
    if ($sayi -le 0) { continue }
    $ilk = Invoke-GitOku -C $kok log -1 --pretty=%s $d
    [void]$bekleyen.Add([pscustomobject]@{
      dal  = $d
      sayi = $sayi
      konu = $(if ($dalKonu.ContainsKey($d)) { $dalKonu[$d] } else { "" })
      son  = (($ilk -join "").Trim())
    })
    [void]$engeller.Add("$($d): main'de olmayan $sayi commit")
  }

  # Worktree'lerdeki commit'lenmemis dosyalar.
  $kirliAgac = New-Object System.Collections.ArrayList
  foreach ($w in $worktreeler) {
    if (-not (Test-Path $w.Yol)) { continue }
    $d = Invoke-GitOku -C $w.Yol status --porcelain
    $k = @(@($d) | Where-Object { $_ -and -not (Test-AltyapiDosyasi ($_.Substring(2).Trim().Trim('"'))) })
    if ($k.Count -eq 0) { continue }
    $konu = Split-Path -Leaf $w.Yol
    [void]$kirliAgac.Add([pscustomobject]@{
      konu     = $konu
      dal      = [string]$w.Dal
      sayi     = $k.Count
      dosyalar = @($k | Select-Object -First 5 | ForEach-Object { $_.Substring(2).Trim() })
    })
    [void]$engeller.Add("$konu [$($w.Dal)]: commit'lenmemis $($k.Count) dosya")
  }

  # --- Stash: hicbir kontrolun gormedigi is --------------------------------
  # Stash ne dalda ne agacta gorunur; `git status` temiz der, panel temiz der,
  # dal taramasi temiz der. Sonra biri "hani su duzeltme?" diye sorar. Depoda
  # bu kontrol yazilirken iki stash duruyordu, biri aylar oncesinden.
  # ENGEL DEGIL UYARI: stash cogu zaman bilerek birakilmis muswedde.
  $hamStash = Invoke-GitOku -C $kok stash list
  $stash = @(@($hamStash) | Where-Object { $_ -and "$_".Trim() } | ForEach-Object { "$_".Trim() })
  if ($stash.Count -gt 0) {
    [void]$uyarilar.Add("$($stash.Count) stash var -- hicbir dalda, hicbir tag'de gorunmez")
  }

  # Isi main'e girdigi halde acik duran agaclar: duzen sorunu, engel degil.
  $bosAgac = @($worktreeler | Where-Object { $_.Dal -and (@($olu) -contains $_.Dal) } |
                ForEach-Object { Split-Path -Leaf $_.Yol })
  if ($olu.Count -gt 0) { [void]$uyarilar.Add("$($olu.Count) dal main'e girmis ama duruyor") }
  if ($bosAgac.Count -gt 0) { [void]$uyarilar.Add("$($bosAgac.Count) worktree isi main'e girdigi halde acik") }

  # --- 4-5. Dosyadan okunanlar (onbellekli) --------------------------------
  $simdi = (Get-Date)
  $tazeGerek = $Tazele -or ($null -eq $script:surumOnbellek) -or
               (($simdi - $script:surumOnbellekZaman).TotalSeconds -gt $script:SURUM_ONBELLEK_SN)
  if ($tazeGerek) {
    $script:surumOnbellek = [pscustomobject]@{
      zincir   = Get-MigrationZinciri -Dizin (Join-Path $kok "apps\backend-api\alembic_migrations\versions")
      kaynak   = Get-SurumKaynaklari -Kok $kok
    }
    $script:surumOnbellekZaman = $simdi
  }
  $zincir = $script:surumOnbellek.zincir
  $kaynak = $script:surumOnbellek.kaynak

  if ($zincir.baslar.Count -gt 1) {
    [void]$engeller.Add("migration zinciri $($zincir.baslar.Count) BASLI: $($zincir.baslar -join ', ')")
  }
  foreach ($e in $kaynak.eksik) { [void]$uyarilar.Add($e) }

  $farkli = @($kaynak.bulunan.Values | Sort-Object -Unique)
  if ($farkli.Count -gt 1) { [void]$engeller.Add("surum numaralari AYRISIK: $($farkli -join ' / ')") }

  $surum = ""
  if ($farkli.Count -ge 1) { $surum = $farkli[0] }
  if ($kaynak.bulunan.Contains("VERSION")) { $surum = $kaynak.bulunan["VERSION"] }

  $tagAdi = ""; $tagVar = $false; $tagMaindeMi = $false
  if ($surum) {
    $tagAdi = "v$surum"
    $tagVar = $null -ne (Invoke-GitOku -C $kok rev-parse --verify --quiet "refs/tags/$tagAdi")
    if ($tagVar) {
      $tagHedef = ((Invoke-GitOku -C $kok rev-list -1 $tagAdi) -join "").Trim()
      $mainHead = ((Invoke-GitOku -C $kok rev-parse main) -join "").Trim()
      $tagMaindeMi = ($tagHedef -eq $mainHead)
      if ($tagMaindeMi) {
        [void]$uyarilar.Add("$tagAdi zaten var ve main'i gosteriyor -- yeni is yoksa cikacak bir sey yok")
      } else {
        [void]$engeller.Add("$tagAdi ZATEN VAR ama baska bir commit'i gosteriyor")
      }
    }
  }

  return [pscustomobject]@{
    kok            = $kok
    anaDal         = $anaDal
    anaKirli       = @($anaKirli | ForEach-Object { $_.Substring(2).Trim() })
    anaIzlenmeyen  = @($anaIzlenmeyen | ForEach-Object { $_.Substring(2).Trim() })
    cevrimdisi     = $cevrimdisi
    uzakVar        = $uzakVar
    yerelOnde      = $yerelOnde
    uzakOnde       = $uzakOnde
    bekleyen       = @($bekleyen)
    stash          = @($stash)
    olu            = @($olu)
    kirliAgac      = @($kirliAgac)
    bosAgac        = @($bosAgac)
    migrationBas   = @($zincir.baslar)
    migrationSayi  = $zincir.sayi
    surumler       = $kaynak.bulunan
    surum          = $surum
    tagAdi         = $tagAdi
    tagVar         = $tagVar
    tagMaindeMi    = $tagMaindeMi
    engeller       = @($engeller)
    uyarilar       = @($uyarilar)
  }
}
