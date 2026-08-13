<#
.SYNOPSIS
  Bir oturum dalini guncel main uzerine tasir (rebase) ve carpismayi
  ONCEDEN raporlar.

.DESCRIPTION
  NEDEN VAR
  ---------
  Izolasyon vardi, birlestirme yolu yoktu. Sonuc olculebilir: bu altyapi
  yazilirken acik dort dalin dordu de main'in 11-20 commit GERISINDEYDI ve
  hicbirinde guncelleme araci yoktu. Geriden gelen dal buyudukce merge
  kavgasi buyur; kimse de "bugun rebase edeyim" diye hatirlamaz.

  Script uc kipte calisir:

    (varsayilan)  Once RAPOR: dal ne kadar geride, hangi dosyalar hem main'de
                  hem dalda degismis, prova rebase cakisiyor mu. Hicbir sey
                  degistirmez.
    -Uygula       Raporu gecerse gercek rebase'i yapar.
    -Hepsi        Tum acik oturumlari sirayla raporlar (toplu bakis).

  PROVA (dry-run) NEDEN: `git rebase` cakisirsa calisma agacini yarim
  birakir; paralel oturumda yarim kalmis bir rebase, sahibi olmayan bir
  agacta en kotu durumdur. Once `git merge-tree` ile SANAL birlestirme
  yapilir -- diske hic dokunmadan cakisma var mi gorunur.

.PARAMETER Konu
  Oturum adi (worktree dizin adi). -Hepsi ile birlikte verilmez.

.PARAMETER Uygula
  Raporla yetinme, rebase'i gercekten yap.

.PARAMETER Hepsi
  Tum acik oturumlari raporla.

.PARAMETER Hedef
  Uzerine tasinacak ref. Varsayilan `origin/main` (once fetch edilir).

.EXAMPLE
  .\tools\oturum-birlestir.ps1 -Hepsi
  .\tools\oturum-birlestir.ps1 -Konu analiz-erisim
  .\tools\oturum-birlestir.ps1 -Konu analiz-erisim -Uygula
#>
[CmdletBinding()]
param(
  [string]$Konu = "",
  [switch]$Uygula,
  [switch]$Hepsi,
  [string]$Hedef = "origin/main"
)

$ErrorActionPreference = "Stop"
function Yaz($metin, $renk = "Gray") { Write-Host $metin -ForegroundColor $renk }

. (Join-Path $PSScriptRoot "oturum-ortak.ps1")

if (-not $Hepsi -and [string]::IsNullOrWhiteSpace($Konu)) {
  throw "-Konu <ad> ya da -Hepsi verin. Acik oturumlar icin: tools\oturum-kayit.ps1"
}
if ($Hepsi -and $Uygula) {
  throw "-Hepsi yalnizca RAPOR kipidir. Rebase'i oturum oturum yapin (-Konu <ad> -Uygula)."
}

$oturumlar = Get-Oturumlar
if ($oturumlar.Count -eq 0) { Yaz "Acik oturum yok." "Yellow"; exit 0 }

# --- Hedefi tazele --------------------------------------------------------
if ($Hedef -like "origin/*") {
  Yaz "origin fetch ediliyor..." "DarkGray"
  & git fetch origin --quiet 2>$null | Out-Null
}

<#
  Tek bir oturumu inceler. Cikti: rapor ekrana, donen deger cakisan dosya
  sayisi (0 = temiz).
#>
function Incele($kayit) {
  $yol = ConvertTo-WindowsYol $kayit.yol
  $dal = [string]$kayit.dal

  Yaz ""
  Yaz "=== $($kayit.konu)  [$dal]" "Cyan"

  if (-not (Test-Path $yol)) {
    Yaz "  Dizin yok; defter bir sonraki acilista esitler." "Yellow"
    return -1
  }

  # --- Ne kadar geride/ileride -------------------------------------------
  $sayim = Invoke-GitOku -C $yol rev-list --left-right --count "$Hedef...HEAD"
  $geride = 0; $ileride = 0
  if ($sayim) {
    $p = (($sayim -join "") -split "\s+") | Where-Object { $_ -match '^\d+$' }
    if ($p.Count -ge 2) { $geride = [int]$p[0]; $ileride = [int]$p[1] }
  }
  Yaz "  $Hedef'e gore: $ileride commit ileride, $geride commit geride"

  if ($ileride -eq 0 -and $geride -eq 0) { Yaz "  Guncel, yapacak bir sey yok." "Green"; return 0 }

  # --- Calisma agaci temiz mi --------------------------------------------
  # Kirli agacta rebase yapilmaz. Uyariyi rapor kipinde de veriyoruz:
  # kullanici -Uygula demeden once neyi commit'lemesi gerektigini bilsin.
  $durum = Invoke-GitOku -C $yol status --porcelain
  $kirli = @(@($durum) | Where-Object {
    $_ -and -not (Test-AltyapiDosyasi ($_.Substring(2).Trim()))
  })
  if ($kirli.Count -gt 0) {
    Yaz "  UYARI: commit'lenmemis $($kirli.Count) dosya var. Rebase icin once commit'leyin:" "Yellow"
    foreach ($k in ($kirli | Select-Object -First 5)) { Yaz "      $k" "DarkGray" }
  }

  if ($ileride -eq 0) {
    Yaz "  Dalda kendi commit'iniz yok; rebase yerine ileri sarma yeter." "DarkGray"
  }

  # --- Iki tarafta da degismis dosyalar -----------------------------------
  # Asil carpisma riski burada. `git rebase`in patlayacagi yeri diske
  # dokunmadan gostermek, patladiktan sonra temizlemekten ucuz.
  $ortakAta = Invoke-GitOku -C $yol merge-base $Hedef HEAD
  $cakisan = @()
  if ($ortakAta) {
    $ata = ($ortakAta -join "").Trim()
    $bizim = @(Invoke-GitOku -C $yol diff --name-only "$ata..HEAD")
    $onlarin = @(Invoke-GitOku -C $yol diff --name-only "$ata..$Hedef")
    $cakisan = @($bizim | Where-Object { $_ -and ($onlarin -contains $_) } | Sort-Object -Unique)
  }

  if ($cakisan.Count -eq 0) {
    Yaz "  Ayni dosyaya iki taraf da dokunmamis: rebase temiz gecmeli." "Green"
  } else {
    Yaz "  IKI TARAFTA DA DEGISEN $($cakisan.Count) dosya:" "Yellow"
    foreach ($c in ($cakisan | Select-Object -First 15)) { Yaz "      $c" }
    if ($cakisan.Count -gt 15) { Yaz "      ... ve $($cakisan.Count - 15) dosya daha" }

    # --- Prova birlestirme: GERCEKTEN cakisiyor mu --------------------------
    # Ayni dosyaya dokunmak cakisma DEMEK DEGIL; farkli yerlere dokunulmus
    # olabilir. `merge-tree --write-tree` sanal birlestirme yapar, diske
    # dokunmaz; ciktisinda "CONFLICT" varsa is gercekten elle cozulecek.
    $prova = Invoke-GitOku -C $yol merge-tree --write-tree --name-only $Hedef HEAD
    if ($prova) {
      $conflictli = @(@($prova) | Where-Object { $_ -match 'CONFLICT' })
      if ($conflictli.Count -gt 0) {
        Yaz "  PROVA: gercek cakisma var, elle cozulecek." "Red"
        foreach ($c in ($conflictli | Select-Object -First 8)) { Yaz "      $c" "Red" }
      } else {
        Yaz "  PROVA: dosyalar ortak ama satirlar cakismiyor; otomatik birlesir." "Green"
      }
    }
  }

  # --- Uygula --------------------------------------------------------------
  if (-not $Uygula) {
    Yaz "  (rapor kipi -- hicbir sey degismedi. Uygulamak icin: -Uygula)" "DarkGray"
    return $cakisan.Count
  }
  if ($kirli.Count -gt 0) {
    Yaz "  UYGULANMADI: once commit'lenmemis isi halledin." "Red"
    return $cakisan.Count
  }

  Yaz "  Rebase calisiyor: $dal  ->  $Hedef ustune" "White"
  $eski = $ErrorActionPreference
  $ErrorActionPreference = "Continue"
  try {
    & git -C $yol rebase $Hedef 2>&1 | ForEach-Object { Yaz "      $_" "DarkGray" }
    $kod = $LASTEXITCODE
  } finally { $ErrorActionPreference = $eski }

  if ($kod -eq 0) {
    Yaz "  TAMAM: dal artik $Hedef ustunde." "Green"
    Yaz "  Sonraki: git -C '$yol' push --force-with-lease" "White"
  } else {
    # --force DEGIL --abort: yarim rebase birakmak, paylasilan depoda en
    # kotu durum. Kullanici bilerek devam etmek isterse elle surdurur.
    Yaz "  REBASE CAKISTI. Agac yarim birakilmadi, geri alindi." "Red"
    & git -C $yol rebase --abort 2>$null | Out-Null
    Yaz "  Elle cozmek icin:" "White"
    Yaz "      cd '$yol'"
    Yaz "      git rebase $Hedef        # cakismalari coz"
    Yaz "      git rebase --continue"
  }
  return $cakisan.Count
}

if ($Hepsi) {
  Yaz "TUM OTURUMLAR -- $Hedef'e gore durum" "White"
  $toplam = 0
  foreach ($o in $oturumlar) { $s = Incele $o; if ($s -gt 0) { $toplam += $s } }
  Yaz ""
  if ($toplam -gt 0) {
    Yaz "Ozet: $toplam dosya-carpismasi noktasi var." "Yellow"
    Yaz "Once EN AZ carpisani main'e alin; kalanlar onun ustune rebase etsin." "White"
  } else {
    Yaz "Ozet: carpisma yok." "Green"
  }
  exit 0
}

$secili = $oturumlar | Where-Object { $_.konu -eq $Konu } | Select-Object -First 1
if (-not $secili) {
  Yaz "Boyle bir oturum yok: $Konu" "Red"
  Yaz "Acik oturumlar: $((@($oturumlar | ForEach-Object { $_.konu })) -join ', ')"
  exit 1
}
Incele $secili | Out-Null
