<#
.SYNOPSIS
  Paralel calisma oturumu icin izole bir git worktree acar.

.DESCRIPTION
  NEDEN VAR
  ---------
  Ayni calisma agacinda birden fazla oturum (Claude Code penceresi, IDE,
  terminal) calistiginda isler birbirine giriyor. 2026-08-12'de 20 dakika
  icinde uc kaza yasandi:

    1. `git commit -a` baska bir oturumun dosyalarini da commit'ledi.
    2. Editorde acik kalmis ESKI tampon kaydedilince, baska bir oturumun
       246 satirlik degisikligi commit ile GERI ALINDI.
    3. `git reset` iki commit'i daldan dusurdu (biri baska oturumun).

  Ucunun de tek sebebi var: paylasilan tek calisma agaci. Disiplinle
  azaltilir, yapisal olarak cozulmez. Her oturum kendi worktree'sinde
  calisirsa `-A` ile commit etse bile digerinin dosyasina DOKUNAMAZ.

  KONUM: worktree'ler `.claude/worktrees/` altinda acilir.
    * `.gitignore`da zaten var,
    * Docker derleme baglamlari uygulama basina (`./apps/...`) oldugu icin
      imaja SIZMAZ,
    * Claude Code oturumu `EnterWorktree` ile dogrudan bu yola gecebilir.

  PORT: her worktree'ye ayri port cifti verilir (frontend 5173+N, backend
  8000+N). Frontend'in API adresi 5173 portuna BAGLI bir varsayimla
  cozuluyor (bkz. shared/api.ts `API_BASE_URL`); baska portta calisirken
  `VITE_API_BASE_URL` yazilmazsa arayuz backend'i bulamaz. Script bu
  dosyayi kendisi yazar — en cok zaman kaybettiren ayrinti buydu.

.PARAMETER Konu
  Isin kisa adi: `analiz`, `gateway`, `rapor`. Dal adi ve dizin bundan turer.

.PARAMETER Dal
  Dal adi. Varsayilan: `feat/<konu>`.

.PARAMETER Temel
  Dalin baslayacagi ref. Varsayilan `origin/main` (once fetch edilir):
  baska bir oturumun yarim kalmis yerel commit'lerini devralmamak icin.

.PARAMETER TamKurulum
  `node_modules` icin baglanti (junction) yerine GERCEK `npm install`.
  Yalnizca o worktree'de bagimlilik degistiriyorsan gerekir.

.PARAMETER Aciklama
  Isin bir cumlelik tarifi. Defterde ("kim ne yapiyor") ve diger oturumlarin
  SessionStart tablosunda gorunur.

.PARAMETER VSCode
  Worktree'yi AYRI bir VSCode penceresinde acar.
  NEDEN GEREKLI: VSCode eklentisinde her sekme bir Claude oturumu ama hepsi
  AYNI workspace klasorunu gosterir. Claude `EnterWorktree` ile worktree'ye
  gecse bile EDITOR ana agacta kalir; acik bir tampon kaydedildiginde
  degisiklik ana agaca yazilir. 246 satirlik kayip tam bu ayrimdan cikti.
  Ayri pencere, editor ile oturumu ayni dizinde bulusturur.

.PARAMETER Sessiz
  Yalnizca worktree yolunu stdout'a basar, diger her seyi stderr'e. Bu bicimi
  WorktreeCreate hook'u kullanir: Claude Code o olayda stdout'u worktree yolu
  olarak okur, fazladan tek satir ciktiy akisi bozar.

.EXAMPLE
  .\tools\oturum-ac.ps1 -Konu analiz -VSCode
  .\tools\oturum-ac.ps1 -Konu gateway -Temel main -TamKurulum
#>
[CmdletBinding()]
param(
  [Parameter(Mandatory = $true)][string]$Konu,
  [string]$Dal = "",
  [string]$Temel = "origin/main",
  [string]$Aciklama = "",
  [switch]$TamKurulum,
  [switch]$VSCode,
  [switch]$Sessiz
)

$ErrorActionPreference = "Stop"

# Sessiz kipte TUM anlatim stderr'e gider; stdout yalnizca worktree yolunu
# tasir (WorktreeCreate hook sozlesmesi).
function Yaz($metin, $renk = "Gray") {
  if ($Sessiz) { [Console]::Error.WriteLine($metin) } else { Write-Host $metin -ForegroundColor $renk }
}

# Git'i cagirmanin GUVENLI yolu.
#
# NEDEN: git normal ilerleme mesajlarini ("Preparing worktree...") STDERR'e
# yazar. `$ErrorActionPreference = "Stop"` altinda, cagiran taraf ciktiyi
# yonlendirirse (`2>&1`, boru hatti, CI) PowerShell bu satirlari ErrorRecord'a
# cevirir ve script BASARILI git komutunda oluverir. Basari olcusu tek sey:
# cikis kodu.
function Git-Calistir {
  param([Parameter(ValueFromRemainingArguments = $true)][string[]]$Arg)
  $eski = $ErrorActionPreference
  $ErrorActionPreference = "Continue"
  try {
    # stderr satirlari DUZ METNE cevrilir. Aksi halde PowerShell onlari
    # kirmizi ErrorRecord olarak basar ve basarili bir kurulum ekranda
    # "hata almis" gibi gorunur — kullanici bosuna geri alir.
    & git @Arg 2>&1 | ForEach-Object {
      if ($_ -is [System.Management.Automation.ErrorRecord]) {
        Yaz $_.Exception.Message "DarkGray"
      } else {
        Yaz "$_"
      }
    }
  } finally { $ErrorActionPreference = $eski }
  if ($LASTEXITCODE -ne 0) { throw "git $($Arg -join ' ') basarisiz (kod $LASTEXITCODE)." }
}

# --- Depo kokunu bul: script nereden cagrilirsa cagrilsin dogru yer -------
$kok = (git rev-parse --show-toplevel 2>$null)
if (-not $kok) { throw "Git deposu bulunamadi. Depo icinden calistirin." }
$kok = $kok.Trim() -replace "/", "\"

# Ana agacta miyiz? Worktree icinden yeni worktree acmak kafa karistirir.
$gitDir = (git rev-parse --git-dir).Trim()
if ($gitDir -match "worktrees") {
  throw "Zaten bir worktree icindesiniz. Ana agaca donup tekrar deneyin: $kok"
}

if (-not ($Konu -match '^[a-z0-9][a-z0-9\-_]{0,30}$')) {
  throw "Konu adi kucuk harf/rakam/tire olmali (ornek: analiz, gateway-surum)."
}
if ([string]::IsNullOrWhiteSpace($Dal)) { $Dal = "feat/$Konu" }

$worktreeKok = Join-Path $kok ".claude\worktrees"
$hedef = Join-Path $worktreeKok $Konu
if (Test-Path $hedef) { throw "Bu konu zaten acik: $hedef" }

# --- Ortak defter --------------------------------------------------------
# Slot/port dagitimi ve "kim ne yapiyor" kaydi buradan yurur.
. (Join-Path $PSScriptRoot "oturum-ortak.ps1")

Yaz "Depo      : $kok"
Yaz "Konu/dal  : $Konu  ->  $Dal"
Yaz "Temel ref : $Temel"
Yaz ""

# --- Worktree ------------------------------------------------------------
if ($Temel -like "origin/*") {
  Yaz "origin fetch ediliyor..." "DarkGray"
  Git-Calistir fetch origin --quiet
}
Yaz "Worktree aciliyor..." "DarkGray"
Git-Calistir worktree add -b $Dal $hedef $Temel

# --- Port cifti: DEFTERDEN ilk bos slot ----------------------------------
# Slot 0 ANA AGACIN: 5173/8000 orada kalsin, alisilmis akis bozulmasin.
#
# ESKI HESAP YANLISTI: slot = "dizin sayisi + 1". Bir oturum kapatilip yenisi
# acildiginda ayni slot ikinci kez dagitiliyordu; iki uvicorn ayni portta
# ikincisi sessizce baslamiyor, kullanici da "backend neden cevap vermiyor"
# diye frontend'de ariyordu. Defter ilk BOS tam sayiyi verir ve kilit
# altinda calisir -- iki oturum ayni anda acilsa da ayni slotu almaz.
$kayit = Add-Oturum -Konu $Konu -Yol $hedef -Dal $Dal -Aciklama $Aciklama
if ($kayit) {
  $slot = [int]$kayit.slot
  $frontPort = [int]$kayit.frontendPort
  $backPort = [int]$kayit.backendPort
} else {
  # Defter yazilamadiysa is DURMASIN; kaba ama calisir bir port ver.
  $slot = (@(Get-ChildItem $worktreeKok -Directory -ErrorAction SilentlyContinue)).Count
  $frontPort = 5173 + $slot
  $backPort = 8000 + $slot
  $kayit = [pscustomobject]@{
    konu = $Konu; dal = $Dal; yol = $hedef; slot = $slot
    frontendPort = $frontPort; backendPort = $backPort; aciklama = $Aciklama
  }
  Yaz "UYARI: defter yazilamadi; port kaba hesapla verildi." "Yellow"
}
Yaz "Portlar   : frontend $frontPort / backend $backPort" "DarkGray"

# --- Kurulum: .env'ler, node_modules, OTURUM.md -------------------------
# Ortak kitaplikta (oturum-ortak.ps1 > Copy-OturumKurulumu). NEDEN ORADA:
# yerlesik `--worktree` akisi da ayni kurulumdan gecmeli, yoksa oradan
# acilan worktree .env'siz kalir ve hic calismaz.
Copy-OturumKurulumu -Hedef $hedef -Kayit $kayit -Temel $Temel -TamKurulum:$TamKurulum -Bildir { param($m, $r) Yaz $m $r }
# --- VSCode penceresi ----------------------------------------------------
# VSCode eklentisinde sekmeler AYNI workspace klasorunu paylasir. Claude
# `EnterWorktree` ile worktree'ye gecse bile EDITOR ana agacta kalir: acik
# bir tampon kaydedildiginde degisiklik ana agaca yazilir. 246 satirlik kayip
# tam bu ayrimdan cikti. Ayri pencere ikisini ayni dizinde bulusturur.
if ($VSCode) {
  $kod = Get-Command code -ErrorAction SilentlyContinue
  if ($kod) {
    Yaz "VSCode yeni pencerede aciliyor..." "DarkGray"
    # cmd uzerinden: `code` bir .cmd sarmalayicisi, dogrudan cagrilinca
    # PowerShell bazi kurulumlarda pencereyi acip donmuyor.
    Start-Process -FilePath "cmd.exe" -ArgumentList "/c", "code", "-n", "`"$hedef`"" -WindowStyle Hidden
  } else {
    Yaz "UYARI: 'code' PATH'te yok; VSCode elle acilmali." "Yellow"
    Yaz "  VSCode > Command Palette > 'Shell Command: Install code command in PATH'" "Yellow"
  }
}

Yaz ""
Yaz "HAZIR: $hedef" "Green"
Yaz ""
Yaz "Sonraki adimlar:" "White"
if ($VSCode) {
  Yaz "  1) Acilan YENI VSCode penceresinde Claude sekmesi baslat." "White"
  Yaz "     (Bu pencerede baslatirsan editor ana agacta kalir.)" "DarkGray"
} else {
  Yaz "  1) Bu oturumu oraya tasi (Claude Code):" "White"
  Yaz "       EnterWorktree  path: $hedef"
  Yaz "     ya da terminalde:  cd '$hedef'"
  Yaz "     VSCode kullaniyorsan -VSCode ile ayri pencere acmak DAHA GUVENLI." "DarkGray"
}
Yaz "  2) Calistirma komutlari: $hedef\OTURUM.md"
Yaz "  3) Is bitince ana agactan:  tools\oturum-kapat.ps1 -Konu $Konu"

# WorktreeCreate hook sozlesmesi: stdout YALNIZCA worktree yolu.
if ($Sessiz) { [Console]::Out.WriteLine($hedef) }
