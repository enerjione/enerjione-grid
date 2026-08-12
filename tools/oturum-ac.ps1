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

.EXAMPLE
  .\tools\oturum-ac.ps1 -Konu analiz
  .\tools\oturum-ac.ps1 -Konu gateway -Temel main -TamKurulum
#>
[CmdletBinding()]
param(
  [Parameter(Mandatory = $true)][string]$Konu,
  [string]$Dal = "",
  [string]$Temel = "origin/main",
  [switch]$TamKurulum
)

$ErrorActionPreference = "Stop"

function Yaz($metin, $renk = "Gray") { Write-Host $metin -ForegroundColor $renk }

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

# --- Port cifti: mevcut worktree sayisina gore bir sonraki slot ----------
# Slot 0 ANA AGACIN: 5173/8000 orada kalsin, alisilmis akis bozulmasin.
$mevcut = @()
if (Test-Path $worktreeKok) {
  $mevcut = @(Get-ChildItem $worktreeKok -Directory -ErrorAction SilentlyContinue)
}
$slot = $mevcut.Count + 1
$frontPort = 5173 + $slot
$backPort = 8000 + $slot

Yaz "Depo      : $kok"
Yaz "Konu/dal  : $Konu  ->  $Dal"
Yaz "Temel ref : $Temel"
Yaz "Portlar   : frontend $frontPort / backend $backPort"
Yaz ""

# --- Worktree ------------------------------------------------------------
if ($Temel -like "origin/*") {
  Yaz "origin fetch ediliyor..." "DarkGray"
  git fetch origin --quiet
}
Yaz "Worktree aciliyor..." "DarkGray"
git worktree add -b $Dal $hedef $Temel
if ($LASTEXITCODE -ne 0) { throw "git worktree add basarisiz." }

# --- Gitignore'daki yerel dosyalar: worktree'ye GELMEZLER ----------------
# Backend .env olmadan uygulama hic acilmaz; kopyalanmasi sart.
$kaynakEnv = Join-Path $kok "apps\backend-api\.env"
$hedefEnv = Join-Path $hedef "apps\backend-api\.env"
if (Test-Path $kaynakEnv) {
  Copy-Item $kaynakEnv $hedefEnv
  Yaz "backend .env kopyalandi" "DarkGray"
} else {
  Yaz "UYARI: apps/backend-api/.env yok; .env.example'dan uretin." "Yellow"
}

# Frontend'in API adresi: 5173 DISINDA bir portta calisirken zorunlu.
$frontEnv = Join-Path $hedef "apps\frontend-web\.env"
@(
  "# Bu dosyayi tools/oturum-ac.ps1 uretti (paralel oturum: $Konu).",
  "# 5173 DISINDA bir portta calisiyoruz; api.ts'teki 5173 varsayimi",
  "# devreye girmedigi icin backend adresi burada ACIKCA verilmeli.",
  "VITE_API_BASE_URL=http://localhost:$backPort/api/v1"
) | Set-Content -Path $frontEnv -Encoding utf8
Yaz "frontend .env yazildi (VITE_API_BASE_URL -> :$backPort)" "DarkGray"

# --- node_modules --------------------------------------------------------
$anaNode = Join-Path $kok "apps\frontend-web\node_modules"
$yeniNode = Join-Path $hedef "apps\frontend-web\node_modules"
if ($TamKurulum) {
  Yaz "npm install calisiyor (tam kurulum)..." "DarkGray"
  Push-Location (Join-Path $hedef "apps\frontend-web")
  npm install
  Pop-Location
} elseif (Test-Path $anaNode) {
  # Junction: yonetici hakki gerektirmez, vite/tsc sorunsuz izler.
  # BAGIMLILIK DEGISTIRIRSEN bu paylasim yaniltir — o durumda -TamKurulum.
  New-Item -ItemType Junction -Path $yeniNode -Target $anaNode | Out-Null
  Yaz "node_modules ana agaca baglandi (junction)" "DarkGray"
} else {
  Yaz "UYARI: ana agacta node_modules yok; `npm install` gerekiyor." "Yellow"
}

# --- Oturum notu: dizini acan herkes ne oldugunu gorsun ------------------
$not = @(
  "# Oturum: $Konu",
  "",
  "Bu dizin PARALEL BIR CALISMA OTURUMU icin acilmis bir git worktree'sidir.",
  "Ana agac: $kok",
  "",
  "| Ne | Deger |",
  "| --- | --- |",
  "| Dal | ``$Dal`` |",
  "| Temel | ``$Temel`` |",
  "| Frontend portu | $frontPort |",
  "| Backend portu | $backPort |",
  "",
  "## Calistirma",
  "",
  '```powershell',
  "# Backend (venv ANA AGACTAN kullanilir; ayri kurulum gerekmez)",
  "& '$kok\apps\backend-api\.venv\Scripts\Activate.ps1'",
  "cd '$hedef\apps\backend-api'",
  "python -m uvicorn app.main:app --reload --port $backPort",
  "",
  "# Frontend (ayri pencerede)",
  "cd '$hedef\apps\frontend-web'",
  "npm run dev -- --port $frontPort",
  '```',
  "",
  "## Bitince",
  "",
  '```powershell',
  "cd '$kok'",
  ".\tools\oturum-kapat.ps1 -Konu $Konu",
  '```',
  "",
  "> Duz ``git worktree remove`` KULLANMA: node_modules ana agaca junction",
  "> ile bagli, baglantinin icine giren bir silme ANA AGACIN node_modules'unu",
  "> goturur. Kapatma scripti once baglantiyi tek basina kaldirir.",
  "",
  "> `node_modules` ana agaca junction ile bagli. Bu oturumda BAGIMLILIK",
  "> degistirirsen once `npm install` ile bagimsizlastir, yoksa ana agaci",
  "> da etkilersin."
)
Set-Content -Path (Join-Path $hedef "OTURUM.md") -Value $not -Encoding utf8

Yaz ""
Yaz "HAZIR: $hedef" "Green"
Yaz ""
Yaz "Sonraki adimlar:" "White"
Yaz "  1) Bu oturumu oraya tasi (Claude Code):" "White"
Yaz "       EnterWorktree  path: $hedef"
Yaz "     ya da terminalde:  cd '$hedef'"
Yaz "  2) Calistirma komutlari: $hedef\OTURUM.md"
Yaz "  3) Is bitince ana agactan:  tools\oturum-kapat.ps1 -Konu $Konu"
