<#
.SYNOPSIS
  `tools/oturum-ac.ps1` ile acilmis bir worktree'yi GUVENLE kapatir.

.DESCRIPTION
  NEDEN AYRI BIR SCRIPT
  ---------------------
  Duz `git worktree remove` bu kurulumda RISKLI: worktree'nin
  `node_modules` dizini ana agaca JUNCTION ile bagli. Junction'i "icine
  girerek" silen bir arac, hedefteki gercek dizini — yani ANA AGACIN
  node_modules'unu — silebilir. Once baglanti tek basina kaldirilir
  (`Directory.Delete`, hedefe DOKUNMAZ), sonra worktree silinir.

  Ayrica commit'lenmemis is varsa uyarir: paralel oturumda "hangi
  dizindeydim" karisikligi zaten yasandi, silmeden once ne kaybedilecegi
  ekranda yazsin.

.PARAMETER Konu
  `oturum-ac.ps1 -Konu <ad>` ile verilen ad.

.PARAMETER Zorla
  Commit'lenmemis/izlenmeyen dosya olsa da sil.

.EXAMPLE
  .\tools\oturum-kapat.ps1 -Konu analiz
#>
[CmdletBinding()]
param(
  [Parameter(Mandatory = $true)][string]$Konu,
  [switch]$Zorla
)

$ErrorActionPreference = "Stop"
function Yaz($metin, $renk = "Gray") { Write-Host $metin -ForegroundColor $renk }

# Bkz. oturum-ac.ps1: git ilerleme mesajlarini stderr'e yazar; ciktisi
# yonlendirilen bir cagride bu, BASARILI komutu hataya cevirir.
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
        Write-Host $_.Exception.Message -ForegroundColor DarkGray
      } else {
        Write-Host $_
      }
    }
  } finally { $ErrorActionPreference = $eski }
  if ($LASTEXITCODE -ne 0) { throw "git $($Arg -join ' ') basarisiz (kod $LASTEXITCODE)." }
}

$kok = (git rev-parse --show-toplevel 2>$null)
if (-not $kok) { throw "Git deposu bulunamadi." }
$kok = $kok.Trim() -replace "/", "\"

$gitDir = (git rev-parse --git-dir).Trim()
if ($gitDir -match "worktrees") {
  throw "Silinecek worktree'nin ICINDESINIZ. Once ana agaca gecin: $kok"
}

$hedef = Join-Path $kok ".claude\worktrees\$Konu"
if (-not (Test-Path $hedef)) { throw "Boyle bir oturum yok: $hedef" }

# --- Dizin GERCEKTEN kayitli bir worktree mi -----------------------------
# SART: worktree kaydi dusmus ama dizin diskte kalmis olabilir (yarim kalan
# bir silme). O durumda `git -C <dizin> status` sessizce ANA DEPOYU raporlar
# — dizin ana deponun icinde oldugu icin git yukari yuruyup onu bulur. Kontrol
# olmadan script "bu oturumda commit'lenmemis is var" diye ANA AGACIN
# degisikliklerini listeliyor, kullanici da yanlis agaci temizlemeye kalkiyor.
$kayitli = (git worktree list --porcelain) -match '^worktree ' | ForEach-Object {
  ($_ -replace '^worktree ', '').Trim() -replace '/', '\'
}
if ($kayitli -notcontains $hedef) {
  Yaz "Dizin var ama KAYITLI BIR WORKTREE DEGIL (yarim kalmis silme olabilir):" "Yellow"
  Yaz "  $hedef"
  Yaz ""
  Yaz "Elle temizlik:" "White"
  Yaz "  git worktree prune"
  Yaz "  Remove-Item -Recurse -Force '$hedef'   # once node_modules junction'ini kaldirin"
  exit 1
}

# --- Commit'lenmemis is var mi -------------------------------------------
# Scriptin KENDI urettigi dosyalar sayilmaz (OTURUM.md, kopyalanan .env'ler).
# Onlar da "kirli" sayilsaydi her kapatma -Zorla ister, kullanici da refleksle
# hep -Zorla yazardi: koruma ilk gunde islevsizlesirdi.
$uretilen = @("OTURUM.md", "apps/frontend-web/.env", "apps/backend-api/.env")
$kirli = @(
  git -C $hedef status --porcelain | Where-Object {
    $yol = ($_ -replace '^..\s*', '').Trim('"')
    $uretilen -notcontains $yol
  }
)
if ($kirli.Count -gt 0 -and -not $Zorla) {
  Yaz "Bu oturumda commit'lenmemis degisiklik var:" "Yellow"
  $kirli | Select-Object -First 15 | ForEach-Object { Yaz "  $_" }
  Yaz ""
  Yaz "Once commit/push edin ya da -Zorla ile silin." "Yellow"
  exit 1
}

# --- Junction'i TEK BASINA kaldir (hedefe dokunmadan) --------------------
# Directory.Delete($path, $false): baglantinin kendisini siler, isaret
# ettigi dizine GIRMEZ. `Remove-Item -Recurse` bazi PowerShell surumlerinde
# junction'in ICINE girer — ana agacin node_modules'u gider.
$junction = Join-Path $hedef "apps\frontend-web\node_modules"
if (Test-Path $junction) {
  $item = Get-Item $junction -Force
  if ($item.LinkType -eq "Junction" -or $item.LinkType -eq "SymbolicLink") {
    [System.IO.Directory]::Delete($item.FullName, $false)
    Yaz "node_modules baglantisi kaldirildi (ana agac etkilenmedi)" "DarkGray"
  } else {
    Yaz "node_modules gercek dizin (tam kurulum yapilmisti); birlikte silinecek." "DarkGray"
  }
}

# --- Worktree ------------------------------------------------------------
# `--force`: .env ve OTURUM.md izlenmeyen dosyalar, onlarsiz git reddeder.
try {
  Git-Calistir worktree remove --force $hedef
} catch {
  # Windows'ta en sik sebep: bir kabuk/editor HALA o dizinin icinde. Acik
  # dizin silinemez ve git'in "Permission denied"i bunu soylemez. Yarim kalan
  # silme worktree kaydini dusurur, dizin diskte kalir — bir sonraki denemede
  # script ana agaci raporlamaya baslar (bkz. yukaridaki kayit kontrolu).
  Yaz ""
  Yaz "Worktree silinemedi. En olasi sebep: BIR KABUK/EDITOR bu dizinin icinde." "Yellow"
  Yaz "  $hedef"
  Yaz ""
  Yaz "Yapilacak: o pencereleri kapatin ya da baska dizine gecin, sonra:" "White"
  Yaz "  git worktree prune"
  Yaz "  .\tools\oturum-kapat.ps1 -Konu $Konu"
  throw
}
Yaz "Worktree silindi: $hedef" "Green"

# --- Dal ------------------------------------------------------------------
# Dal SILINMEZ: is birlestirilmemis olabilir. Ne yapilacagi ekranda dursun.
$dal = "feat/$Konu"
$varMi = git show-ref --verify --quiet "refs/heads/$dal"; $dalVar = ($LASTEXITCODE -eq 0)
if ($dalVar) {
  $birlesmis = git branch --merged origin/main | Select-String -SimpleMatch $dal
  if ($birlesmis) {
    Yaz "Dal '$dal' origin/main'e birlesmis; silebilirsiniz:" "White"
    Yaz "  git branch -d $dal"
  } else {
    Yaz "Dal '$dal' DURUYOR (henuz birlesmemis). Isi kaybetmeyin:" "Yellow"
    Yaz "  git log origin/main..$dal --oneline"
  }
}
