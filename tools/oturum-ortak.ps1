<#
.SYNOPSIS
  Paralel oturum araclarinin ORTAK kitapligi. Dot-source edilir, kendi
  basina is yapmaz.

.DESCRIPTION
  NEDEN VAR
  ---------
  Worktree izolasyonu (bkz. oturum-ac.ps1) oturumlarin birbirinin dosyasini
  EZMESINI onledi ama birbirini GORMESINI saglamadi. Dort oturum ayni anda
  `types.ts`e dokunabiliyor, hepsi ayri agacta mutlu mesut calisiyor, fatura
  merge sirasinda kesiliyor.

  Bu kitaplik ortak defteri tutar: hangi oturum acik, hangi dalda, hangi
  portta, su an hangi dosyalari kirletmis. Hook'lar (oturum-durum,
  oturum-carpisma, oturum-bitis) ve scriptler (oturum-ac, oturum-kapat,
  oturum-birlestir) hep buradan okur.

  DEFTERIN YERI: <ana-agac>\.claude\oturumlar.json
  Worktree'lerin her birinde ayri bir `.claude/` var; defter TEK olmali,
  o yuzden her zaman ANA AGACIN yolu hesaplanir (Get-AnaAgacKok). Dosya
  `.gitignore`da: yerel makine durumu, commit edilmez.

  KURAL: buradaki hicbir fonksiyon cagirani BLOKLAMAZ. Git yoksa, defter
  bozuksa, kilit alinamazsa bos/kismi sonuc doner. Bir hook'un kullanicinin
  onunu kesmesi, korumanin kendisinden daha pahaliya mal olur.
#>

# StrictMode 1.0: degisken yazim hatasini yakalar ama OLMAYAN OZELLIK erisimine
# karismaz. Defter elle duzenlenmis ya da eski semadan kalmis olabilir; eksik
# alan $null donmeli, script'i patlatmamali.
Set-StrictMode -Version 1.0

# Defter surumu: sema degisirse artir, eski dosya sifirdan kurulur.
$script:KAYIT_SURUM = 1

# Portlar: slot 0 ANA AGACIN (5173/8000), worktree'ler 1'den baslar.
$script:FRONT_TABAN = 5173
$script:BACK_TABAN = 8000

# Kirli-dosya haritasi onbellek suresi (saniye). Carpisma hook'u her Edit'te
# calisir; her seferinde 5 worktree'de `git status` cagirmak duzenlemeye
# yarim saniye eklerdi. 20 sn pencere: baska oturumun degisikligini en gec
# 20 sn gecikmeyle gorursunuz, ki bu uyari icin fazlasiyla yeterli.
$script:ONBELLEK_SANIYE = 20

# ---------------------------------------------------------------------------
# Git yardimcilari
# ---------------------------------------------------------------------------

# Git'i SESSIZ cagirir: cikti satirlari doner, hata durumunda $null.
# stderr yutulur -- git ilerleme mesajlarini oraya yazar ve cagiran taraf
# `$ErrorActionPreference = "Stop"` altindaysa basarili komut hataya donusur
# (bkz. oturum-ac.ps1 > Git-Calistir).
function Invoke-GitOku {
  param([Parameter(ValueFromRemainingArguments = $true)][string[]]$Arg)
  $eski = $ErrorActionPreference
  $ErrorActionPreference = "Continue"
  try {
    $cikti = & git @Arg 2>$null
    if ($LASTEXITCODE -ne 0) { return $null }
    # Virgul operatoru SART: `return @($x)` tek elemanli diziyi acar ve cagiran
    # taraf string alir. `$g[0]` o zaman ILK HARFI dondurur -- sessiz ve tuhaf
    # bir hata sinifi (once tam bunu yasadik).
    return ,@($cikti)
  } catch {
    return $null
  } finally {
    $ErrorActionPreference = $eski
  }
}

function ConvertTo-WindowsYol([string]$yol) {
  if ([string]::IsNullOrWhiteSpace($yol)) { return "" }
  return ($yol.Trim() -replace "/", "\").TrimEnd("\")
}

<#
.SYNOPSIS
  ANA calisma agacinin kokunu dondurur (worktree icinden cagrilsa bile).
.DESCRIPTION
  `git worktree list --porcelain` ciktisinin ILK kaydi her zaman ana agactir
  (git belgelenmis davranisi). Worktree icinde `git rev-parse --show-toplevel`
  worktree'nin kendisini verir; defterin yerini bulmak icin ise ana agac lazim.
  Depo degilsek $null doner.
#>
function Get-AnaAgacKok {
  $satirlar = Invoke-GitOku worktree list --porcelain
  if (-not $satirlar) { return $null }
  foreach ($s in $satirlar) {
    if ($s -match '^worktree\s+(.+)$') { return (ConvertTo-WindowsYol $Matches[1]) }
  }
  return $null
}

<#
.SYNOPSIS
  Kayitli tum worktree'leri dondurur: @{ Yol; Dal; AnaMi }.
#>
function Get-WorktreeListesi {
  $satirlar = Invoke-GitOku worktree list --porcelain
  if (-not $satirlar) { return @() }

  $sonuc = New-Object System.Collections.ArrayList
  $suanki = $null
  foreach ($s in $satirlar) {
    if ($s -match '^worktree\s+(.+)$') {
      if ($suanki) { [void]$sonuc.Add($suanki) }
      $suanki = [pscustomobject]@{ Yol = (ConvertTo-WindowsYol $Matches[1]); Dal = ""; AnaMi = $false }
    } elseif ($s -match '^branch\s+refs/heads/(.+)$' -and $suanki) {
      $suanki.Dal = $Matches[1].Trim()
    } elseif ($s -match '^detached' -and $suanki) {
      $suanki.Dal = "(detached)"
    }
  }
  if ($suanki) { [void]$sonuc.Add($suanki) }

  if ($sonuc.Count -gt 0) { $sonuc[0].AnaMi = $true }
  return @($sonuc)
}

<#
.SYNOPSIS
  Bu oturum kendi worktree'sinde mi? (ana agacta ise $false)
#>
function Test-WorktreeIcinde {
  $gitDir = Invoke-GitOku rev-parse --git-dir
  if (-not $gitDir) { return $false }
  return (($gitDir -join "`n") -match "worktrees")
}

# ---------------------------------------------------------------------------
# Defter (registry)
# ---------------------------------------------------------------------------

function Get-KayitYolu {
  $kok = Get-AnaAgacKok
  if (-not $kok) { return $null }
  return (Join-Path $kok ".claude\oturumlar.json")
}

<#
.SYNOPSIS
  Defteri kilitleyip $Blok'u calistirir, sonucu geri yazar.
.DESCRIPTION
  NEDEN KILIT: iki oturum ayni anda `oturum-ac` calistirdiginda ikisi de ayni
  bos slotu gorup ayni portu dagitiyordu. Kilit `CreateNew` ile atomik olarak
  alinir (dosya varsa hata -> baskasi tutuyor).

  KILIT ALINAMAZSA yine de devam edilir: koruma ugruna kullaniciyi bekletmek
  yanlis takas. En kotu ihtimalle iki oturum ayni portu alir, bu da gorulur
  ve duzeltilebilir bir hatadir -- takilan bir script degildir.

  $Blok imzasi: [scriptblock] param($liste)  -> yeni liste dondurmeli.
  $Blok $null dondururse defter DEGISTIRILMEZ (salt-okuma kullanim).
#>
function Invoke-KayitKilitli {
  param([Parameter(Mandatory = $true)][scriptblock]$Blok)

  $kayit = Get-KayitYolu
  if (-not $kayit) { return $null }
  $kilit = "$kayit.lock"
  $akis = $null

  for ($i = 0; $i -lt 40; $i++) {
    try {
      $akis = New-Object System.IO.FileStream(
        $kilit, [System.IO.FileMode]::CreateNew, [System.IO.FileAccess]::Write,
        [System.IO.FileShare]::None, 8, [System.IO.FileOptions]::DeleteOnClose)
      break
    } catch {
      Start-Sleep -Milliseconds 50
    }
  }
  # $akis hala $null ise kilitsiz devam ediyoruz (yukaridaki gerekce).

  try {
    $defter = Read-Defter
    $yeni = & $Blok $defter
    if ($null -ne $yeni) { Write-Defter $yeni }
    return $yeni
  } finally {
    if ($akis) { $akis.Dispose() }
  }
}

<#
.SYNOPSIS
  Defteri HAM okur (senkronlamadan). Bozuksa bos defter doner.
.DESCRIPTION
  Defterin IKI bolumu var:
    oturumlar  -- worktree'ler: dal, slot, port. Diskteki dizinle eslesir.
    pencereler -- CANLI Claude oturumlari: hangi sekme, hangi dizinde, ne
                  isle mesgul. Kullanicinin kurulumunda dort sekme AYNI
                  VSCode klasorunde acik; worktree listesi tek basina
                  "kim ne yapiyor" sorusunu cevaplamiyor, bu bolum cevapliyor.
#>
function Read-Defter {
  $bos = [pscustomobject]@{ oturumlar = @(); pencereler = @() }
  $kayit = Get-KayitYolu
  if (-not $kayit -or -not (Test-Path $kayit)) { return $bos }
  try {
    $ham = Get-Content $kayit -Raw -Encoding UTF8
    if ([string]::IsNullOrWhiteSpace($ham)) { return $bos }
    $veri = $ham | ConvertFrom-Json
    if ($veri.surum -ne $script:KAYIT_SURUM) { return $bos }
    return [pscustomobject]@{
      oturumlar  = @($veri.oturumlar  | Where-Object { $_ })
      pencereler = @($veri.pencereler | Where-Object { $_ })
    }
  } catch {
    # Bozuk defter = defter yok. Senkron zaten worktree'lerden yeniden kurar.
    return $bos
  }
}

function Write-Defter {
  param([Parameter(Mandatory = $true)]$Defter)
  $kayit = Get-KayitYolu
  if (-not $kayit) { return }
  $dizin = Split-Path -Parent $kayit
  if (-not (Test-Path $dizin)) { New-Item -ItemType Directory -Path $dizin -Force | Out-Null }

  $veri = [pscustomobject]@{
    surum      = $script:KAYIT_SURUM
    guncelleme = (Get-Date).ToUniversalTime().ToString("s") + "Z"
    oturumlar  = @($Defter.oturumlar)
    pencereler = @($Defter.pencereler)
  }
  # Atomik yazma: once yan dosya, sonra tasi. Yarim yazilmis JSON, defteri
  # okuyan bir hook'u her seferinde sifirlatirdi.
  $gecici = "$kayit.tmp"
  ($veri | ConvertTo-Json -Depth 6) | Set-Content -Path $gecici -Encoding UTF8
  Move-Item -Path $gecici -Destination $kayit -Force
}

<#
.SYNOPSIS
  Defteri diskteki GERCEK worktree'lerle esitler ve dondurur.
.DESCRIPTION
  Defter tek dogruluk kaynagi DEGIL; git'in worktree listesi oyle. Elle
  `git worktree add` yapilmis ya da yerlesik `--worktree` akisiyla acilmis
  oturumlar deftere burada girer (slot/port atanir), silinmis olanlar duser.
  Boylece defter hicbir zaman "gercek disi" kalmaz.
#>
<#
.SYNOPSIS
  Kullanilanlar disindaki ILK bos slot numarasini dondurur (1'den baslar).
.DESCRIPTION
  Slot 0 ana agacin (5173/8000). Ayri fonksiyon olmasinin sebebi test
  edilebilirlik: eski hesap ("dizin sayisi + 1") bir oturum kapatilip yenisi
  acildiginda ayni portu ikinci kez dagitiyordu ve bunu fark etmek zordu --
  iki uvicorn ayni portta, ikincisi sessizce baslamiyor.
#>
function Get-BosSlot {
  param([int[]]$Kullanilan = @())
  $kume = New-Object System.Collections.Generic.HashSet[int]
  foreach ($k in @($Kullanilan)) { [void]$kume.Add([int]$k) }
  $slot = 1
  while ($kume.Contains($slot)) { $slot++ }
  return $slot
}

function Sync-Oturumlar {
  param([object[]]$Liste)

  $worktreeler = @(Get-WorktreeListesi | Where-Object { -not $_.AnaMi })
  $gecerliYollar = @($worktreeler | ForEach-Object { $_.Yol })

  # 1) Artik olmayanlari dusur.
  $kalan = New-Object System.Collections.ArrayList
  foreach ($o in @($Liste)) {
    if (-not $o) { continue }
    if ($gecerliYollar -contains (ConvertTo-WindowsYol $o.yol)) { [void]$kalan.Add($o) }
  }

  # 2) Kullanilan slotlar.
  $kullanilan = New-Object System.Collections.Generic.HashSet[int]
  foreach ($o in $kalan) {
    try { [void]$kullanilan.Add([int]$o.slot) } catch { }
  }

  # 3) Defterde olmayan worktree'leri ekle.
  #
  # SIRA ONEMLI: once diskte YAZILI portu olanlar (.env'den okunur) kendi
  # slotunu kapsin, sonra kalanlara bos slot dagitilsin. Defter bu altyapidan
  # sonra dogdu; hali hazirda calisan oturumlarin portu .env'de yaziyor ve
  # uvicorn/vite o portta ayakta. Defter onlara baska bir slot verirse
  # "hangi port dogru" sorusunu defterin kendisi uretmis olur.
  $mevcutYollar = @($kalan | ForEach-Object { ConvertTo-WindowsYol $_.yol })
  $yeniler = @($worktreeler | Where-Object { $mevcutYollar -notcontains $_.Yol })

  $portsuz = New-Object System.Collections.ArrayList
  foreach ($w in $yeniler) {
    $slot = Get-YaziliSlot -WorktreeYolu $w.Yol
    if ($slot -and -not $kullanilan.Contains($slot)) {
      [void]$kullanilan.Add($slot)
      [void]$kalan.Add((New-OturumKaydi -Konu (Split-Path -Leaf $w.Yol) -Dal $w.Dal -Yol $w.Yol -Slot $slot))
    } else {
      [void]$portsuz.Add($w)
    }
  }
  foreach ($w in $portsuz) {
    $slot = Get-BosSlot -Kullanilan @($kullanilan)
    [void]$kullanilan.Add($slot)
    [void]$kalan.Add((New-OturumKaydi -Konu (Split-Path -Leaf $w.Yol) -Dal $w.Dal -Yol $w.Yol -Slot $slot))
  }

  return @($kalan | Sort-Object { [int]$_.slot })
}

<#
.SYNOPSIS
  Worktree'nin DISKTE yazili backend portundan slot numarasini cikarir.
.DESCRIPTION
  Kaynak: `apps/frontend-web/.env` icindeki VITE_API_BASE_URL. Bu dosyayi
  oturum-ac.ps1 uretiyor ve calisan frontend backend'i oradan buluyor --
  yani gercekten kullanilan port bu. Dosya yoksa (yerlesik `--worktree`
  akisiyla acilmis bir oturum) $null doner, cagiran bos slot dagitir.
#>
function Get-YaziliSlot {
  param([Parameter(Mandatory = $true)][string]$WorktreeYolu)
  $env = Join-Path $WorktreeYolu "apps\frontend-web\.env"
  if (-not (Test-Path $env)) { return $null }
  try {
    foreach ($satir in (Get-Content $env -Encoding UTF8)) {
      if ($satir -match 'VITE_API_BASE_URL\s*=\s*https?://[^:/]+:(\d+)') {
        $slot = [int]$Matches[1] - $script:BACK_TABAN
        if ($slot -ge 1 -and $slot -le 200) { return $slot }
      }
    }
  } catch { }
  return $null
}

function New-OturumKaydi {
  param(
    [Parameter(Mandatory = $true)][string]$Konu,
    [string]$Dal = "",
    [Parameter(Mandatory = $true)][string]$Yol,
    [Parameter(Mandatory = $true)][int]$Slot,
    [string]$Aciklama = ""
  )
  return [pscustomobject]@{
    konu         = $Konu
    dal          = $Dal
    yol          = (ConvertTo-WindowsYol $Yol)
    slot         = $Slot
    frontendPort = $script:FRONT_TABAN + $Slot
    backendPort  = $script:BACK_TABAN + $Slot
    aciklama     = $Aciklama
    acilis       = (Get-Date).ToUniversalTime().ToString("s") + "Z"
  }
}

<#
.SYNOPSIS
  Esitlenmis defteri dondurur (ve diske yazar).
#>
function Get-Oturumlar {
  $defter = Invoke-KayitKilitli {
    param($d)
    $d.oturumlar = @(Sync-Oturumlar -Liste $d.oturumlar)
    $d.pencereler = @(Select-CanliPencereler -Liste $d.pencereler)
    return $d
  }
  if ($null -eq $defter) { return @() }
  return @($defter.oturumlar)
}

<#
.SYNOPSIS
  Bos bir slot ayirir ve deftere yazar; olusan kaydi dondurur.
.DESCRIPTION
  Slot = 1'den baslayarak ILK BOS tam sayi. Eski surum slotu "dizin sayisi+1"
  diye hesapliyordu; bir oturum kapatilip yenisi acilinca ayni port ikinci kez
  dagitiliyordu (iki uvicorn ayni portta -> ikincisi sessizce baslamiyor).
#>
function Add-Oturum {
  param(
    [Parameter(Mandatory = $true)][string]$Konu,
    [Parameter(Mandatory = $true)][string]$Yol,
    [string]$Dal = "",
    [string]$Aciklama = ""
  )
  $hedef = ConvertTo-WindowsYol $Yol
  $defter = Invoke-KayitKilitli {
    param($d)
    $guncel = @(Sync-Oturumlar -Liste $d.oturumlar)

    # Zaten varsa (senkron eklemistir) alanlarini tazele, ikinci kayit acma.
    $var = $guncel | Where-Object { (ConvertTo-WindowsYol $_.yol) -eq $hedef } | Select-Object -First 1
    if ($var) {
      if ($Dal) { $var.dal = $Dal }
      if ($Aciklama) { $var.aciklama = $Aciklama }
      $d.oturumlar = $guncel
      return $d
    }

    $kullanilan = New-Object System.Collections.Generic.HashSet[int]
    foreach ($o in $guncel) { try { [void]$kullanilan.Add([int]$o.slot) } catch { } }
    $slot = Get-BosSlot -Kullanilan @($kullanilan)

    $d.oturumlar = @($guncel + (New-OturumKaydi -Konu $Konu -Dal $Dal -Yol $Yol -Slot $slot -Aciklama $Aciklama))
    return $d
  }
  # Kaydi listeden GERI OKU: kilitli blogun icinden disariya degisken tasimak
  # (script scope) dot-source edilen kitaplikta guvenilir degil.
  if ($null -eq $defter) { return $null }
  return (@($defter.oturumlar) | Where-Object { $_ -and (ConvertTo-WindowsYol $_.yol) -eq $hedef } | Select-Object -First 1)
}

function Remove-Oturum {
  param([Parameter(Mandatory = $true)][string]$Yol)
  $hedef = ConvertTo-WindowsYol $Yol
  Invoke-KayitKilitli {
    param($d)
    $d.oturumlar = @(@($d.oturumlar) | Where-Object { $_ -and (ConvertTo-WindowsYol $_.yol) -ne $hedef })
    return $d
  } | Out-Null
}

# ---------------------------------------------------------------------------
# Canli pencereler (acik Claude Code oturumlari)
# ---------------------------------------------------------------------------

# Bir pencere bu suredir haber vermediyse kapanmis sayilir. SessionEnd hook'u
# kaydi duzgun siliyor ama cokme/kapatma her zaman hook calistirmaz; sureli
# temizlik olmadan defter olu sekmelerle dolar ve kimse ona bakmaz.
$script:PENCERE_OMRU_SAAT = 8

function Select-CanliPencereler {
  param([object[]]$Liste)
  $simdi = (Get-Date).ToUniversalTime()
  $canli = New-Object System.Collections.ArrayList
  foreach ($p in @($Liste)) {
    if (-not $p -or -not $p.id) { continue }
    try {
      $son = [datetime]::Parse($p.gorulen).ToUniversalTime()
      if (($simdi - $son).TotalHours -gt $script:PENCERE_OMRU_SAAT) { continue }
    } catch { continue }
    [void]$canli.Add($p)
  }
  return @($canli)
}

<#
.SYNOPSIS
  Canli oturumu deftere yazar/tazeler.
.PARAMETER Baslik
  Oturumun ne isle mesgul oldugu. Ilk kullanici istegi buraya yazilir --
  kullanicinin VSCode sekme basliklarinin ("PDF rapor tasarimi", "Ariza
  sayfasi") defterdeki karsiligi. Bos gecilirse mevcut baslik KORUNUR:
  her istekte ustune yazsaydi baslik surekli degisir, "kim ne yapiyor"
  tablosu son cumleyi gosterirdi, isi degil.
#>
function Update-Pencere {
  param(
    [Parameter(Mandatory = $true)][string]$Id,
    [string]$Cwd = "",
    [string]$Baslik = ""
  )
  if ([string]::IsNullOrWhiteSpace($Cwd)) { $Cwd = (Get-Location).Path }
  $cwdTam = ConvertTo-WindowsYol $Cwd
  $dalAdi = ""
  $d = Invoke-GitOku -C $cwdTam rev-parse --abbrev-ref HEAD
  if ($d) { $dalAdi = ($d -join "").Trim() }
  $simdi = (Get-Date).ToUniversalTime().ToString("s") + "Z"

  Invoke-KayitKilitli {
    param($defter)
    $liste = @(Select-CanliPencereler -Liste $defter.pencereler)
    $var = $liste | Where-Object { $_.id -eq $Id } | Select-Object -First 1
    if ($var) {
      $var.cwd = $cwdTam
      $var.dal = $dalAdi
      $var.gorulen = $simdi
      if ($Baslik) { $var.baslik = $Baslik }
      $defter.pencereler = $liste
    } else {
      $yeni = [pscustomobject]@{
        id      = $Id
        cwd     = $cwdTam
        dal     = $dalAdi
        baslik  = $Baslik
        acilis  = $simdi
        gorulen = $simdi
      }
      $defter.pencereler = @($liste + $yeni)
    }
    return $defter
  } | Out-Null
}

function Remove-Pencere {
  param([Parameter(Mandatory = $true)][string]$Id)
  Invoke-KayitKilitli {
    param($d)
    $d.pencereler = @(@(Select-CanliPencereler -Liste $d.pencereler) | Where-Object { $_.id -ne $Id })
    return $d
  } | Out-Null
}

function Get-Pencereler {
  $defter = Invoke-KayitKilitli {
    param($d)
    $d.pencereler = @(Select-CanliPencereler -Liste $d.pencereler)
    return $d
  }
  if ($null -eq $defter) { return @() }
  return @($defter.pencereler)
}

# ---------------------------------------------------------------------------
# Kirli dosya haritasi (onbellekli)
# ---------------------------------------------------------------------------

<#
.SYNOPSIS
  Bu dosya, oturum altyapisinin KENDI urettigi bir dosya mi?
.DESCRIPTION
  OTURUM.md ve kopyalanan .env'ler her worktree'de "izlenmeyen dosya" olarak
  durur. Sayilsalardi her agac surekli kirli gorunur, "kim ne yapiyor" tablosu
  da carpisma uyarisi da anlamsiz gurultuye bogulurdu -- ve gurultulu bir
  uyariyi kimse ikinci gun okumaz.
#>
function Test-AltyapiDosyasi {
  param([string]$Yol)
  if ([string]::IsNullOrWhiteSpace($Yol)) { return $true }
  $y = $Yol.Trim() -replace "\\", "/"
  if ($y -eq "OTURUM.md") { return $true }
  if ($y -like "*/.env" -or $y -eq ".env") { return $true }
  if ($y -like ".claude/oturumlar*.json*") { return $true }
  return $false
}

function Get-OnbellekYolu {
  $kok = Get-AnaAgacKok
  if (-not $kok) { return $null }
  return (Join-Path $kok ".claude\oturumlar.cache.json")
}

<#
.SYNOPSIS
  Her agacin (ana agac dahil) commit'lenmemis dosyalarini dondurur.
.OUTPUTS
  @( @{ konu; dal; yol; anaMi; dosyalar = @("apps/.../types.ts", ...) } )
  Yollar DEPO GORELI ve ileri-slash'li (git formati).
.DESCRIPTION
  Onbellek: ONBELLEK_SANIYE icinde tekrar cagrilirsa diskten okur. Carpisma
  hook'u her paylasimli dosya duzenlemesinde calisiyor; onbelleksiz her
  duzenlemeye 5 x `git status` bineceki.
#>
function Get-KirliHarita {
  param([switch]$Tazele)

  $onbellek = Get-OnbellekYolu
  if (-not $Tazele -and $onbellek -and (Test-Path $onbellek)) {
    try {
      $yas = ((Get-Date) - (Get-Item $onbellek).LastWriteTime).TotalSeconds
      if ($yas -lt $script:ONBELLEK_SANIYE) {
        $ham = Get-Content $onbellek -Raw -Encoding UTF8
        if (-not [string]::IsNullOrWhiteSpace($ham)) { return @(($ham | ConvertFrom-Json)) }
      }
    } catch { }   # Bozuk onbellek: asagida yeniden uretilir.
  }

  $oturumlar = Get-Oturumlar
  $agaclar = New-Object System.Collections.ArrayList
  foreach ($w in Get-WorktreeListesi) {
    $konu = Split-Path -Leaf $w.Yol
    $aciklama = ""
    if ($w.AnaMi) {
      $konu = "(ana agac)"
    } else {
      $kayit = $oturumlar | Where-Object { (ConvertTo-WindowsYol $_.yol) -eq $w.Yol } | Select-Object -First 1
      if ($kayit) {
        $konu = $kayit.konu
        $aciklama = [string]$kayit.aciklama
      }
    }
    $durum = Invoke-GitOku -C $w.Yol status --porcelain
    $dosyalar = New-Object System.Collections.ArrayList
    foreach ($satir in @($durum)) {
      if ([string]::IsNullOrWhiteSpace($satir)) { continue }
      # Format: "XY <yol>" ya da yeniden adlandirmada "XY <eski> -> <yeni>".
      $yol = $satir.Substring(2).Trim()
      if ($yol -match '^"(.*)"$') { $yol = $Matches[1] }
      if ($yol -match '\s->\s(.+)$') { $yol = $Matches[1].Trim('"') }
      if (Test-AltyapiDosyasi $yol) { continue }
      [void]$dosyalar.Add($yol)
    }
    # Dalda COMMIT'LENMIS ama main'e girmemis dosyalar. Carpisma yalnizca
    # "ikimiz de su an duzenliyoruz" degil; "o commit'ledi, ben yeni
    # basliyorum" da ayni merge kavgasini uretir -- hatta daha sinsi olani,
    # cunku karsi tarafin calisma agaci TERTEMIZ gorunur.
    $dalDosyalari = @()
    $geride = 0
    $ileride = 0
    if (-not $w.AnaMi -and $w.Dal -and $w.Dal -ne "(detached)") {
      $fark = Invoke-GitOku -C $w.Yol diff --name-only "main...HEAD"
      if ($fark) { $dalDosyalari = @($fark | Where-Object { $_ -and $_.Trim() }) }
      # "main geride kalmis mi": cok geriden gelen bir dal merge'de en cok
      # kavga cikaran daldir. Sayiyi burada uretip tabloda gosteriyoruz ki
      # kimse 12 commit geriden gelen bir dali fark etmeden buyutmesin.
      $sayim = Invoke-GitOku -C $w.Yol rev-list --left-right --count "main...HEAD"
      if ($sayim) {
        $parca = (($sayim -join "") -split "\s+") | Where-Object { $_ -match '^\d+$' }
        if ($parca.Count -ge 2) { $geride = [int]$parca[0]; $ileride = [int]$parca[1] }
      }
    }

    [void]$agaclar.Add([pscustomobject]@{
      konu         = $konu
      dal          = $w.Dal
      yol          = $w.Yol
      anaMi        = $w.AnaMi
      aciklama     = $aciklama
      geride       = $geride
      ileride      = $ileride
      dosyalar     = @($dosyalar)
      dalDosyalari = @($dalDosyalari)
    })
  }

  if ($onbellek) {
    try {
      $gecici = "$onbellek.tmp"
      (@($agaclar) | ConvertTo-Json -Depth 6) | Set-Content -Path $gecici -Encoding UTF8
      Move-Item -Path $gecici -Destination $onbellek -Force
    } catch { }   # Onbellek yazilamazsa is yine yurur, sadece yavaslar.
  }
  return @($agaclar)
}

# ---------------------------------------------------------------------------
# Oturumlar arasi mesajlasma
# ---------------------------------------------------------------------------

<#
  POSTA KUTUSU: <ana-agac>\.claude\oturum-mesajlar.json (gitignore'da)

  NEDEN VAR
  ---------
  Oturumlar ortak bir amaca calisiyor ama birbirlerine tek kelime
  edemiyorlardi. "types.ts'e dokunuyorum, 10 dakika bekle" ya da "migration'i
  once ben alayim" demenin yolu, kullanicinin dort sekme arasinda mesaji elle
  tasimasiydi.

  TESLIM MODELI -- dogrudan sunu bilerek soyluyoruz: bu ANLIK bir bildirim
  DEGIL. Bir Claude oturumu yalnizca sirasi geldiginde (kullanici bir istek
  gonderdiginde) baglam alir. Mesaj, hedef oturumun BIR SONRAKI adiminda
  UserPromptSubmit hook'u ile baglamina duser. Oturum bos bekliyorsa mesaj
  posta kutusunda durur.

  Bu sinir teknik bir eksiklik degil, mimarinin kendisi: disaridan bir
  oturumun dusunce akisina girmek mumkun degil. Panel okunmamis mesajlari
  gosterir, boylece kullanici "ulasmadi mi" diye merak etmez.
#>

$script:MESAJ_SURUM = 1
# Posta kutusu sinirsiz buyumesin: en son bu kadar mesaj tutulur.
$script:MESAJ_TAVAN = 200

function Get-PostaYolu {
  $kok = Get-AnaAgacKok
  if (-not $kok) { return $null }
  return (Join-Path $kok ".claude\oturum-mesajlar.json")
}

function Read-Posta {
  $yol = Get-PostaYolu
  if (-not $yol -or -not (Test-Path $yol)) { return @() }
  try {
    $ham = Get-Content $yol -Raw -Encoding UTF8
    if ([string]::IsNullOrWhiteSpace($ham)) { return @() }
    $veri = $ham | ConvertFrom-Json
    if ($veri.surum -ne $script:MESAJ_SURUM) { return @() }
    return @($veri.mesajlar | Where-Object { $_ })
  } catch { return @() }
}

function Write-Posta {
  param([object[]]$Mesajlar)
  $yol = Get-PostaYolu
  if (-not $yol) { return }
  $dizin = Split-Path -Parent $yol
  if (-not (Test-Path $dizin)) { New-Item -ItemType Directory -Path $dizin -Force | Out-Null }

  $liste = @($Mesajlar)
  if ($liste.Count -gt $script:MESAJ_TAVAN) {
    $liste = $liste[($liste.Count - $script:MESAJ_TAVAN)..($liste.Count - 1)]
  }
  $veri = [pscustomobject]@{ surum = $script:MESAJ_SURUM; mesajlar = @($liste) }
  $gecici = "$yol.tmp"
  ($veri | ConvertTo-Json -Depth 6) | Set-Content -Path $gecici -Encoding UTF8
  Move-Item -Path $gecici -Destination $yol -Force
}

<#
.SYNOPSIS
  Posta kutusunu kilitleyip $Blok ile gunceller (defter kilidiyle ayni desen).
#>
function Invoke-PostaKilitli {
  param([Parameter(Mandatory = $true)][scriptblock]$Blok)
  $yol = Get-PostaYolu
  if (-not $yol) { return $null }
  $kilit = "$yol.lock"
  $akis = $null
  for ($i = 0; $i -lt 40; $i++) {
    try {
      $akis = New-Object System.IO.FileStream(
        $kilit, [System.IO.FileMode]::CreateNew, [System.IO.FileAccess]::Write,
        [System.IO.FileShare]::None, 8, [System.IO.FileOptions]::DeleteOnClose)
      break
    } catch { Start-Sleep -Milliseconds 50 }
  }
  try {
    $yeni = & $Blok (Read-Posta)
    if ($null -ne $yeni) { Write-Posta @($yeni) }
    return $yeni
  } finally { if ($akis) { $akis.Dispose() } }
}

<#
.SYNOPSIS
  Bir oturumdan digerine mesaj birakir.
.PARAMETER Kime
  Hedef oturum adi (worktree klasor adi), ya da herkese icin "*".
.PARAMETER Kimden
  Gonderen oturum adi. Bos birakilirsa bulundugun dizinden turer.
#>
function Send-OturumMesaji {
  param(
    [Parameter(Mandatory = $true)][string]$Kime,
    [Parameter(Mandatory = $true)][string]$Metin,
    [string]$Kimden = ""
  )
  if ([string]::IsNullOrWhiteSpace($Kimden)) { $Kimden = Get-BuOturumAdi }
  $mesaj = [pscustomobject]@{
    id      = [System.Guid]::NewGuid().ToString("N").Substring(0, 12)
    kimden  = $Kimden
    kime    = $Kime.Trim()
    metin   = $Metin.Trim()
    zaman   = (Get-Date).ToUniversalTime().ToString("s") + "Z"
    okuyan  = @()
  }
  Invoke-PostaKilitli { param($liste) return @(@($liste) + $mesaj) } | Out-Null
  return $mesaj
}

<#
.SYNOPSIS
  Bu calisma agacinin oturum adi (worktree klasor adi; ana agacta "ana").
#>
function Get-BuOturumAdi {
  param([string]$Dizin = "")
  if ([string]::IsNullOrWhiteSpace($Dizin)) {
    $k = Invoke-GitOku rev-parse --show-toplevel
    if (-not $k) { return "bilinmiyor" }
    $Dizin = ($k -join "")
  }
  $Dizin = ConvertTo-WindowsYol $Dizin
  $ana = Get-AnaAgacKok
  if ($ana -and $Dizin -eq $ana) { return "ana" }
  return (Split-Path -Leaf $Dizin)
}

<#
.SYNOPSIS
  Bu oturuma gelmis OKUNMAMIS mesajlari dondurur ve okundu isaretler.
.PARAMETER SadeceBak
  Okundu isaretleme (panel icin).
#>
function Receive-OturumMesajlari {
  param(
    [string]$Oturum = "",
    [switch]$SadeceBak
  )
  if ([string]::IsNullOrWhiteSpace($Oturum)) { $Oturum = Get-BuOturumAdi }

  $benim = @(Read-Posta | Where-Object {
    $_ -and ($_.kime -eq $Oturum -or $_.kime -eq "*") -and
    ($_.kimden -ne $Oturum) -and (@($_.okuyan) -notcontains $Oturum)
  })
  if ($SadeceBak -or $benim.Count -eq 0) { return $benim }

  $idler = @($benim | ForEach-Object { $_.id })
  Invoke-PostaKilitli {
    param($liste)
    foreach ($m in @($liste)) {
      if ($idler -contains $m.id) { $m.okuyan = @(@($m.okuyan) + $Oturum | Sort-Object -Unique) }
    }
    return $liste
  } | Out-Null

  return $benim
}

# ---------------------------------------------------------------------------
# Oturum izi: Claude transkriptinden "ne yapiyor, ne kadar kaldi"
# ---------------------------------------------------------------------------

<#
.SYNOPSIS
  Bir calisma dizinine ait Claude transkript dosyasini (.jsonl) bulur.
.DESCRIPTION
  Claude Code her oturumun kaydini `~/.claude/projects/<slug>/<id>.jsonl`
  altinda tutar. Slug, calisma dizininin yolundan turer: `:`, `\`, bosluk ve
  `.` karakterleri `-` olur, surucu harfi kucuktur.

  NEDEN TRANSKRIPT: defterdeki "ne isle mesgul" bilgisini UserPromptSubmit
  hook'u yaziyor -- ama hook yalnizca AYARLAR YUKLENDIKTEN sonra calisir ve
  ayarlar main'e girene kadar diger oturumlarda yok. Transkript ise zaten
  diskte: panel hicbir sey merge edilmeden, bugun, dort sekmenin de ne
  yaptigini gosterebiliyor. Hook ile transkript birbirinin yedegi.
#>
function Get-TranskriptBilgisi {
  param([Parameter(Mandatory = $true)][string]$CalismaDizini)

  $bos = [pscustomobject]@{ Yol = $null; OturumSayisi = 0 }
  $kok = Join-Path $env:USERPROFILE ".claude\projects"
  if (-not (Test-Path $kok)) { return $bos }

  $slug = (ConvertTo-WindowsYol $CalismaDizini) -replace '[:\\ .]', '-'
  if ($slug.Length -gt 0) { $slug = $slug.Substring(0, 1).ToLowerInvariant() + $slug.Substring(1) }
  $aday = Join-Path $kok $slug

  if (-not (Test-Path $aday)) {
    # Slug kurali surumle degisebilir. Yedek: dizin adinin SONU worktree
    # klasoru ile bitsin. Tahmin etmektense diskte arariz.
    $yaprak = Split-Path -Leaf $CalismaDizini
    $bulunan = Get-ChildItem $kok -Directory -ErrorAction SilentlyContinue |
      Where-Object { $_.Name -like "*worktrees-$yaprak" } | Select-Object -First 1
    if (-not $bulunan) { return $bos }
    $aday = $bulunan.FullName
  }

  $dosyalar = @(Get-ChildItem $aday -Filter *.jsonl -File -ErrorAction SilentlyContinue |
    Sort-Object LastWriteTime -Descending)
  if ($dosyalar.Count -eq 0) { return $bos }

  # Bir dizinde BIRDEN FAZLA oturum acik olabilir (kullanicinin kurulumunda
  # VSCode sekmeleri ayni klasoru paylasiyor). Her .jsonl bir oturum; son 24
  # saatte yazilmis olanlari sayariz, ayrintiyi en yenisinden okuruz.
  $esik = (Get-Date).AddHours(-24)
  $taze = @($dosyalar | Where-Object { $_.LastWriteTime -gt $esik })

  return [pscustomobject]@{
    Yol          = $dosyalar[0].FullName
    OturumSayisi = [Math]::Max(1, $taze.Count)
  }
}

<#
.SYNOPSIS
  Transkriptin SONUNDAN oturumun guncel durumunu cikarir.
.OUTPUTS
  @{ sonIstek; suAnArac; suAnHedef; todoTamam; todoToplam; todoSuAn; sonHareket }
.DESCRIPTION
  Dosyanin tamami okunmaz -- transkriptler 50 MB'a cikiyor ve panel her birkac
  saniyede bir yenileniyor. Yalnizca SON parca (varsayilan 900 KB) okunur ve
  satirlar SONDAN basa taranir; aranan uc sey bulununca durulur.

  Dosya, Claude o anda yaziyorken acilir; bu yuzden FileShare.ReadWrite sart
  (aksi halde "another process" hatasi alinir ve panel canli oturumu tam da
  en aktif oldugu anda gosteremez).
#>
function Read-TranskriptOzeti {
  param(
    [Parameter(Mandatory = $true)][string]$Yol,
    [int]$KacBayt = 921600
  )

  $bos = [pscustomobject]@{
    sonIstek = ""; suAnArac = ""; suAnHedef = ""
    todoTamam = 0; todoToplam = 0; todoSuAn = ""; sonHareket = $null
  }
  if (-not (Test-Path $Yol)) { return $bos }

  try { $bos.sonHareket = (Get-Item $Yol).LastWriteTimeUtc } catch { }

  $metin = ""
  try {
    $fs = [System.IO.File]::Open($Yol, [System.IO.FileMode]::Open,
      [System.IO.FileAccess]::Read, [System.IO.FileShare]::ReadWrite)
    try {
      $uzunluk = $fs.Length
      $oku = [Math]::Min($KacBayt, $uzunluk)
      [void]$fs.Seek($uzunluk - $oku, [System.IO.SeekOrigin]::Begin)
      $tampon = New-Object byte[] $oku
      $alindi = $fs.Read($tampon, 0, $oku)
      $metin = [System.Text.Encoding]::UTF8.GetString($tampon, 0, $alindi)
    } finally { $fs.Dispose() }
  } catch {
    return $bos
  }

  $satirlar = $metin -split "`n"
  # Ilk satir yarim kalmis olabilir (parcanin ortasindan basladik).
  if ($satirlar.Count -gt 1) { $satirlar = $satirlar[1..($satirlar.Count - 1)] }

  $istekTamam = $false; $aracTamam = $false; $todoTamam = $false
  for ($i = $satirlar.Count - 1; $i -ge 0; $i--) {
    if ($istekTamam -and $aracTamam -and $todoTamam) { break }
    $s = $satirlar[$i]
    if ([string]::IsNullOrWhiteSpace($s) -or $s.Length -lt 20) { continue }

    # Once UCUZ metin kontrolu; ConvertFrom-Json yalnizca aday satirlarda
    # calisir. Satirlar 100 KB'a cikabiliyor, hepsini cozmek panel'i durdurur.
    $todoAdayi = (-not $todoTamam) -and $s.Contains('"TodoWrite"') -and $s.Contains('"tool_use"')
    $aracAdayi = (-not $aracTamam) -and $s.Contains('"type":"assistant"') -and $s.Contains('"tool_use"')
    $istekAdayi = (-not $istekTamam) -and $s.Contains('"type":"user"') -and (-not $s.Contains('"tool_use_id"'))
    if (-not ($todoAdayi -or $aracAdayi -or $istekAdayi)) { continue }

    $o = $null
    try { $o = $s | ConvertFrom-Json } catch { continue }
    if (-not $o -or -not $o.message) { continue }

    if ($istekAdayi -and $o.type -eq "user") {
      $icerik = $o.message.content
      $yazi = ""
      if ($icerik -is [string]) {
        $yazi = $icerik
      } else {
        foreach ($p in @($icerik)) { if ($p.type -eq "text" -and $p.text) { $yazi = [string]$p.text; break } }
      }
      $yazi = $yazi.Trim()
      # Sistem enjeksiyonlarini ele: <system-reminder>, <command-name>,
      # "Caveat:" ile baslayan girisler kullanicinin istegi DEGIL.
      if ($yazi -and -not $yazi.StartsWith("<") -and -not $yazi.StartsWith("Caveat:")) {
        $ilk = ($yazi -split "`r?`n" | Where-Object { $_.Trim() } | Select-Object -First 1)
        if ($ilk) {
          $ilk = $ilk.Trim()
          if ($ilk.Length -gt 110) { $ilk = $ilk.Substring(0, 107) + "..." }
          $bos.sonIstek = $ilk
          $istekTamam = $true
        }
      }
      continue
    }

    if ($o.type -ne "assistant") { continue }
    foreach ($p in @($o.message.content)) {
      if ($p.type -ne "tool_use") { continue }

      if ((-not $todoTamam) -and $p.name -eq "TodoWrite" -and $p.input.todos) {
        $liste = @($p.input.todos)
        $bos.todoToplam = $liste.Count
        $bos.todoTamam = @($liste | Where-Object { $_.status -eq "completed" }).Count
        $suan = $liste | Where-Object { $_.status -eq "in_progress" } | Select-Object -First 1
        if ($suan) { $bos.todoSuAn = [string]$suan.content }
        $todoTamam = $true
      }

      if (-not $aracTamam) {
        $bos.suAnArac = [string]$p.name
        $hedef = ""
        foreach ($alan in @("file_path", "command", "pattern", "description", "prompt")) {
          $d = $p.input.$alan
          if ($d) { $hedef = [string]$d; break }
        }
        if ($hedef) {
          $hedef = ($hedef -split "`r?`n")[0].Trim()
          # Dosya yollarini kisalt: tam yol karti tasirir, son iki parca yeter.
          if ($hedef -match '[\\/]') {
            $parca = $hedef -split '[\\/]'
            if ($parca.Count -gt 2) { $hedef = ".../" + ($parca[-2..-1] -join "/") }
          }
          if ($hedef.Length -gt 60) { $hedef = $hedef.Substring(0, 57) + "..." }
        }
        $bos.suAnHedef = $hedef
        $aracTamam = $true
      }
    }
  }

  return $bos
}

<#
.SYNOPSIS
  Tum agaclar icin transkript ozetini dondurur (onbellekli).
.DESCRIPTION
  Onbellek anahtari dosya UZUNLUGU: transkript buyumediyse yeniden taranmaz.
  Panel dort saniyede bir yenileniyor; her yenilemede alti dosyayi bastan
  taramanin anlami yok, hicbiri degismemis olabilir.
#>
function Get-OturumIzleri {
  $onbellekYolu = $null
  $kok = Get-AnaAgacKok
  if ($kok) { $onbellekYolu = Join-Path $kok ".claude\oturumlar.iz.json" }

  $eski = @{}
  if ($onbellekYolu -and (Test-Path $onbellekYolu)) {
    try {
      $ham = Get-Content $onbellekYolu -Raw -Encoding UTF8
      if (-not [string]::IsNullOrWhiteSpace($ham)) {
        foreach ($k in ($ham | ConvertFrom-Json).PSObject.Properties) { $eski[$k.Name] = $k.Value }
      }
    } catch { }
  }

  $yeni = @{}
  foreach ($w in Get-WorktreeListesi) {
    $bilgi = Get-TranskriptBilgisi -CalismaDizini $w.Yol
    if (-not $bilgi.Yol) { continue }
    $uzunluk = 0
    try { $uzunluk = (Get-Item $bilgi.Yol).Length } catch { continue }

    $anahtar = $w.Yol
    $onceki = $eski[$anahtar]
    if ($onceki -and [int64]$onceki.uzunluk -eq $uzunluk) {
      # Onbellekteki kaydin USTUNE YAZMA, YENIDEN KUR. Eski surumden kalmis
      # bir onbellek dosyasinda yeni alan bulunmuyor ve `$onceki.yeniAlan = x`
      # "property cannot be found" ile patliyor -- panel de sessizce durup
      # eski veriyi gostermeye devam ediyordu.
      $yeni[$anahtar] = [pscustomobject]@{
        uzunluk      = $uzunluk
        oturumSayisi = $bilgi.OturumSayisi   # transkript buyumeden de degisir
        sonIstek     = [string]$onceki.sonIstek
        suAnArac     = [string]$onceki.suAnArac
        suAnHedef    = [string]$onceki.suAnHedef
        todoTamam    = [int]$onceki.todoTamam
        todoToplam   = [int]$onceki.todoToplam
        todoSuAn     = [string]$onceki.todoSuAn
        sonHareket   = $onceki.sonHareket
      }
      continue
    }

    $ozet = Read-TranskriptOzeti -Yol $bilgi.Yol
    $yeni[$anahtar] = [pscustomobject]@{
      uzunluk      = $uzunluk
      oturumSayisi = $bilgi.OturumSayisi
      sonIstek     = $ozet.sonIstek
      suAnArac     = $ozet.suAnArac
      suAnHedef    = $ozet.suAnHedef
      todoTamam    = $ozet.todoTamam
      todoToplam   = $ozet.todoToplam
      todoSuAn     = $ozet.todoSuAn
      sonHareket   = $(if ($ozet.sonHareket) { $ozet.sonHareket.ToString("o") } else { $null })
    }
  }

  if ($onbellekYolu) {
    try {
      $gecici = "$onbellekYolu.tmp"
      ([pscustomobject]$yeni | ConvertTo-Json -Depth 6) | Set-Content -Path $gecici -Encoding UTF8
      Move-Item -Path $gecici -Destination $onbellekYolu -Force
    } catch { }
  }
  return $yeni
}

# ---------------------------------------------------------------------------
# Worktree kurulumu
# ---------------------------------------------------------------------------

<#
.SYNOPSIS
  Bir worktree'yi CALISIR hale getirir: .env'ler, node_modules, OTURUM.md.
.DESCRIPTION
  NEDEN ORTAK KITAPLIKTA
  ----------------------
  Worktree acmanin iki yolu var (oturum-ac.ps1 ve yerlesik `--worktree`) ve
  kurulum yalnizca birincisinde yapiliyordu. Ikinci yoldan acilan worktree
  CALISMIYOR: backend .env olmadan hic acilmaz, frontend de 5173 disinda bir
  portta backend'i bulamaz. Depoda bunun canli ornegi vardi.

  Kurulum tek yerde durursa iki yol da ayni sonucu verir. WorktreeCreate
  hook'u (oturum-worktree-hook.ps1) da bunu cagirir.

.PARAMETER Hedef  Worktree dizini.
.PARAMETER Kayit  Defterdeki oturum kaydi (port bilgisi buradan gelir).
.PARAMETER Temel  OTURUM.md'ye yazilacak temel ref bilgisi.
.PARAMETER TamKurulum  node_modules icin junction yerine gercek `npm install`.
.PARAMETER Bildir  Ilerleme mesajlarini basacak scriptblock: param($metin,$renk)
#>
function Copy-OturumKurulumu {
  param(
    [Parameter(Mandatory = $true)][string]$Hedef,
    [Parameter(Mandatory = $true)]$Kayit,
    [string]$Temel = "origin/main",
    [switch]$TamKurulum,
    [scriptblock]$Bildir = $null
  )

  function Duyur([string]$m, [string]$r = "DarkGray") {
    if ($Bildir) { & $Bildir $m $r }
  }

  $kok = Get-AnaAgacKok
  $konu = [string]$Kayit.konu
  $dal = [string]$Kayit.dal
  $frontPort = [int]$Kayit.frontendPort
  $backPort = [int]$Kayit.backendPort

  # --- Gitignore'daki yerel dosyalar: worktree'ye GELMEZLER ----------------
  # Backend .env olmadan uygulama hic acilmaz; kopyalanmasi sart.
  $kaynakEnv = Join-Path $kok "apps\backend-api\.env"
  $hedefEnv = Join-Path $Hedef "apps\backend-api\.env"
  if (Test-Path $kaynakEnv) {
    if (-not (Test-Path $hedefEnv)) {
      Copy-Item $kaynakEnv $hedefEnv
      Duyur "backend .env kopyalandi"
    }
  } else {
    Duyur "UYARI: apps/backend-api/.env yok; .env.example'dan uretin." "Yellow"
  }

  # Claude Code kisisel ayarlari da gitignore'da. Kopyalanmazsa worktree'de
  # `additionalDirectories` (ornegin DNP3 gateway kaynagi) ve kisisel izinler
  # kaybolur; oturum ayni depoda calistigi halde farkli davranir.
  $kaynakYerel = Join-Path $kok ".claude\settings.local.json"
  $hedefYerel = Join-Path $Hedef ".claude\settings.local.json"
  if ((Test-Path $kaynakYerel) -and -not (Test-Path $hedefYerel)) {
    $yerelDizin = Split-Path -Parent $hedefYerel
    if (-not (Test-Path $yerelDizin)) { New-Item -ItemType Directory -Path $yerelDizin -Force | Out-Null }
    Copy-Item $kaynakYerel $hedefYerel
    Duyur "settings.local.json kopyalandi"
  }

  # Frontend'in API adresi: 5173 DISINDA bir portta calisirken zorunlu.
  $frontEnv = Join-Path $Hedef "apps\frontend-web\.env"
  @(
    "# Bu dosyayi oturum altyapisi uretti (paralel oturum: $konu).",
    "# 5173 DISINDA bir portta calisiyoruz; api.ts'teki 5173 varsayimi",
    "# devreye girmedigi icin backend adresi burada ACIKCA verilmeli.",
    "VITE_API_BASE_URL=http://localhost:$backPort/api/v1"
  ) | Set-Content -Path $frontEnv -Encoding utf8
  Duyur "frontend .env yazildi (VITE_API_BASE_URL -> :$backPort)"

  # --- node_modules --------------------------------------------------------
  $anaNode = Join-Path $kok "apps\frontend-web\node_modules"
  $yeniNode = Join-Path $Hedef "apps\frontend-web\node_modules"
  if (Test-Path $yeniNode) {
    Duyur "node_modules zaten var; dokunulmadi"
  } elseif ($TamKurulum) {
    Duyur "npm install calisiyor (tam kurulum)..."
    Push-Location (Join-Path $Hedef "apps\frontend-web")
    try { npm install } finally { Pop-Location }
  } elseif (Test-Path $anaNode) {
    # Junction: yonetici hakki gerektirmez, vite/tsc sorunsuz izler.
    # BAGIMLILIK DEGISTIRIRSEN bu paylasim yaniltir -- o durumda -TamKurulum.
    New-Item -ItemType Junction -Path $yeniNode -Target $anaNode | Out-Null
    Duyur "node_modules ana agaca baglandi (junction)"
  } else {
    Duyur "UYARI: ana agacta node_modules yok; npm install gerekiyor." "Yellow"
  }

  # --- Oturum notu: dizini acan herkes ne oldugunu gorsun ------------------
  $not = @(
    "# Oturum: $konu",
    "",
    "Bu dizin PARALEL BIR CALISMA OTURUMU icin acilmis bir git worktree'sidir.",
    "Ana agac: $kok",
    "",
    "| Ne | Deger |",
    "| --- | --- |",
    "| Dal | ``$dal`` |",
    "| Temel | ``$Temel`` |",
    "| Frontend portu | $frontPort |",
    "| Backend portu | $backPort |",
    "",
    "## Calistirma",
    "",
    '```powershell',
    "# Backend (venv ANA AGACTAN kullanilir; ayri kurulum gerekmez)",
    "& '$kok\apps\backend-api\.venv\Scripts\Activate.ps1'",
    "cd '$Hedef\apps\backend-api'",
    "python -m uvicorn app.main:app --reload --port $backPort",
    "",
    "# Frontend (ayri pencerede)",
    "cd '$Hedef\apps\frontend-web'",
    "npm run dev -- --port $frontPort",
    '```',
    "",
    "## Diger oturumlar",
    "",
    '```powershell',
    "cd '$kok'",
    ".\tools\oturum-kayit.ps1              # kim ne yapiyor",
    ".\tools\oturum-birlestir.ps1 -Konu $konu   # main'i uzerine al",
    '```',
    "",
    "## Bitince",
    "",
    '```powershell',
    "cd '$kok'",
    ".\tools\oturum-kapat.ps1 -Konu $konu",
    '```',
    "",
    "> Duz ``git worktree remove`` KULLANMA: node_modules ana agaca junction",
    "> ile bagli, baglantinin icine giren bir silme ANA AGACIN node_modules'unu",
    "> goturur. Kapatma scripti once baglantiyi tek basina kaldirir.",
    "",
    "> ``node_modules`` ana agaca junction ile bagli. Bu oturumda BAGIMLILIK",
    "> degistirirsen once ``npm install`` ile bagimsizlastir, yoksa ana agaci",
    "> da etkilersin."
  )
  Set-Content -Path (Join-Path $Hedef "OTURUM.md") -Value $not -Encoding utf8
}

<#
.SYNOPSIS
  Mutlak bir dosya yolunu depo-goreli (ileri slash) hale getirir.
.DESCRIPTION
  Yol hangi agacta olursa olsun ayni gorele donusur: ana agactaki
  `...\EnerjiOne Grid\apps\x.ts` ile worktree'deki
  `...\worktrees\analiz\apps\x.ts` ikisi de `apps/x.ts` olur. Karsilastirma
  ancak boyle anlamli.
#>
function Get-DepoGoreliYol {
  param([Parameter(Mandatory = $true)][string]$Yol)

  $tam = ConvertTo-WindowsYol $Yol
  if ([string]::IsNullOrWhiteSpace($tam)) { return $null }

  $adaylar = New-Object System.Collections.ArrayList
  foreach ($w in Get-WorktreeListesi) { [void]$adaylar.Add($w.Yol) }
  # En UZUN eslesen kok kazanir: worktree'ler ana agacin ICINDE
  # (.claude\worktrees\...), kisa kok once eslesirse gorele yol
  # ".claude/worktrees/analiz/apps/x.ts" cikardi ve hicbir seyle eslesmezdi.
  $en = $null
  foreach ($k in ($adaylar | Sort-Object { $_.Length } -Descending)) {
    if ($tam.StartsWith($k, [System.StringComparison]::OrdinalIgnoreCase)) { $en = $k; break }
  }
  if (-not $en) { return $null }
  return ($tam.Substring($en.Length).TrimStart("\") -replace "\\", "/")
}
