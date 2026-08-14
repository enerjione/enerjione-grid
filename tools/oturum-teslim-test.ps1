<#
.SYNOPSIS
  `tools/oturum-teslim.ps1` akisinin uctan uca testi -- GECICI bir depoda.

.DESCRIPTION
  NEDEN GECICI DEPO: teslimin son adimi main'e merge etmektir. Gercek depoda
  test etmek, her kosuda main'e cop commit atmak demekti. Script $env:TEMP
  altinda kucuk bir depo kurar (ana agac + bir worktree), senaryolari orada
  kosar, sonunda siler. Gercek main'e HIC dokunulmaz.

  NE KILITLENIYOR
    * Eksik olan hicbir sey main'e GIRMEZ: commit'lenmemis dosya, migration
      borcu, iki basli zincir, cakisma -- her biri teslimi durdurur.
    * Durduran her senaryoda agac DEGISMEZ (dal yerinde, main'e dokunulmaz).
    * Temiz senaryoda is GERCEKTEN main'e girer ve merge commit'i olusur.

  Testler `-TestAtla` ile kosar: gecici depoda pytest/tsc calistirmanin
  anlami yok, dogrulanan sey teslim AKISI.

.EXAMPLE
  .\tools\oturum-teslim-test.ps1
#>
$ErrorActionPreference = "Stop"

$kaynakTools = $PSScriptRoot
$kok = Join-Path $env:TEMP "e1-teslim-test-$PID"
$wtYol = Join-Path $kok ".claude\worktrees\ornek"

$script:hata = 0
$script:sira = 0

# param() BLOGU YOK, bilerek: adlandirilmis bir `[string[]]$Arg` parametresi
# olsaydi PowerShell'in on-ek eslestirmesi `git add -A` cagrisindaki `-A`yi
# `-Arg` parametresi sanip "argument eksik" derdi (ilk kosuda tam bu oldu).
# $args her seyi oldugu gibi tasir.
function Git-Sessiz {
  $eski = $ErrorActionPreference
  $ErrorActionPreference = "Continue"
  try { & git @args 2>&1 | Out-Null; return $LASTEXITCODE } finally { $ErrorActionPreference = $eski }
}

function Yaz-Dosya($yol, $icerik) {
  $dizin = Split-Path -Parent $yol
  if (-not (Test-Path $dizin)) { New-Item -ItemType Directory -Force $dizin | Out-Null }
  Set-Content -Path $yol -Value $icerik -Encoding UTF8
}

<#
  Bir senaryoyu kosar. $Beklenen: "DUR" (teslim edilmemeli) ya da "GEC".
  $Icerik: metinde aranacak parca (bos ise bakilmaz).
#>
function Senaryo {
  param(
    [string]$Ad,
    [string[]]$Arg,
    [ValidateSet("DUR", "GEC")][string]$Beklenen,
    [string]$Icerik = ""
  )
  $script:sira++
  $oncekiMain = (& git -C $kok rev-parse main).Trim()

  Push-Location $kok
  try {
    $cikti = & powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $kok "tools\oturum-teslim.ps1") @Arg 2>&1
    $kod = $LASTEXITCODE
  } finally { Pop-Location }

  $metin = ($cikti | ForEach-Object { "$_" }) -join "`n"
  $sonrakiMain = (& git -C $kok rev-parse main).Trim()
  $mainDegisti = ($oncekiMain -ne $sonrakiMain)

  $sonuc = if ($kod -eq 0) { "GEC" } else { "DUR" }
  $tamam = ($sonuc -eq $Beklenen)

  # DUR bekleniyorsa main'in DEGISMEMIS olmasi da sarttir: "durdu ama yarim
  # is birakti" en kotu sonuc olurdu.
  if ($Beklenen -eq "DUR" -and $mainDegisti) { $tamam = $false; $metin += "`n[main DEGISTI -- olmamaliydi]" }
  if ($Beklenen -eq "GEC" -and -not $mainDegisti) { $tamam = $false; $metin += "`n[main DEGISMEDI -- degismeliydi]" }
  if ($tamam -and $Icerik -and ($metin -notmatch [regex]::Escape($Icerik))) {
    $tamam = $false; $metin += "`n[beklenen metin yok: $Icerik]"
  }

  if ($tamam) {
    Write-Host ("OK    {0,-2} {1,-42} {2}" -f $script:sira, $Ad, $sonuc)
  } else {
    Write-Host ("HATA  {0,-2} {1,-42} beklenen={2} sonuc={3}" -f $script:sira, $Ad, $Beklenen, $sonuc) -ForegroundColor Red
    foreach ($s in ($metin -split "`n" | Select-Object -Last 12)) { Write-Host "          $s" -ForegroundColor DarkGray }
    $script:hata++
  }
}

# ---------------------------------------------------------------------------
# Kurulum
# ---------------------------------------------------------------------------
if (Test-Path $kok) { Remove-Item -Recurse -Force $kok }
New-Item -ItemType Directory -Force $kok | Out-Null

Write-Host "Gecici depo: $kok" -ForegroundColor DarkGray
Git-Sessiz -C $kok init | Out-Null
Git-Sessiz -C $kok symbolic-ref HEAD refs/heads/main | Out-Null
Git-Sessiz -C $kok config user.email "test@ornek" | Out-Null
Git-Sessiz -C $kok config user.name "Teslim Testi" | Out-Null
# Commit imzalama global olarak acik olabilir; gecici depoda anahtar yok.
Git-Sessiz -C $kok config commit.gpgsign false | Out-Null

Yaz-Dosya (Join-Path $kok "apps\backend-api\app\models\alarm.py") "class Alarm:`n    pass`n"
Yaz-Dosya (Join-Path $kok "apps\backend-api\alembic_migrations\versions\0001_taban.py") @"
revision = "0001"
down_revision = None
"@
Yaz-Dosya (Join-Path $kok "apps\frontend-web\src\x.ts") "export const x = 1;`n"
Yaz-Dosya (Join-Path $kok "docs\not.md") "not`n"

# Teslim scripti kitapligi yaninda arar; ikisini de kopyala.
New-Item -ItemType Directory -Force (Join-Path $kok "tools") | Out-Null
foreach ($d in @("oturum-teslim.ps1", "oturum-ortak.ps1")) {
  Copy-Item (Join-Path $kaynakTools $d) (Join-Path $kok "tools\$d") -Force
}

Git-Sessiz -C $kok add -A | Out-Null
Git-Sessiz -C $kok commit -m "taban" | Out-Null
Git-Sessiz -C $kok worktree add $wtYol -b feat/ornek | Out-Null

Write-Host ""

# ---------------------------------------------------------------------------
# 1. Teslim edilecek is yok
# ---------------------------------------------------------------------------
# main ile ayni noktadayken teslim, "yapacak bir sey yok" deyip 0 donmeli --
# hata degil, bilgi. (main degismedigi icin GEC beklentisi kullanilamaz.)
Push-Location $kok
try {
  $c = & powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $kok "tools\oturum-teslim.ps1") -Konu ornek -TestAtla 2>&1
  $k = $LASTEXITCODE
} finally { Pop-Location }
$script:sira++
if ($k -eq 0 -and (($c -join "`n") -match "teslim edilecek is bulunmuyor")) {
  Write-Host ("OK    {0,-2} {1,-42} {2}" -f $script:sira, "is yok: uyarir, hata vermez", "GEC")
} else {
  Write-Host ("HATA  {0,-2} {1,-42} kod={2}" -f $script:sira, "is yok: uyarir, hata vermez", $k) -ForegroundColor Red
  $script:hata++
}

# ---------------------------------------------------------------------------
# 2. Commit'lenmemis is teslimi durdurur
# ---------------------------------------------------------------------------
Yaz-Dosya (Join-Path $wtYol "docs\not.md") "kaydedilmemis degisiklik`n"
Senaryo -Ad "kirli agac durdurur" -Arg @("-Konu", "ornek", "-TestAtla") -Beklenen "DUR" -Icerik "Commit'lenmemis"

Git-Sessiz -C $wtYol add -A | Out-Null
Git-Sessiz -C $wtYol commit -m "docs: not" | Out-Null

# ---------------------------------------------------------------------------
# 3. apps/ disi degisiklik: test gerekmez, main'e girer
# ---------------------------------------------------------------------------
Senaryo -Ad "docs degisikligi main'e girer" -Arg @("-Konu", "ornek") -Beklenen "GEC" -Icerik "TESLIM EDILDI"

# ---------------------------------------------------------------------------
# 4. Model degisti, migration yok -> durur
# ---------------------------------------------------------------------------
Git-Sessiz -C $wtYol checkout -b feat/model | Out-Null
Yaz-Dosya (Join-Path $wtYol "apps\backend-api\app\models\alarm.py") "class Alarm:`n    yeni_kolon = 1`n"
Git-Sessiz -C $wtYol add -A | Out-Null
Git-Sessiz -C $wtYol commit -m "feat: yeni kolon" | Out-Null
Senaryo -Ad "migration borcu durdurur" -Arg @("-Konu", "ornek", "-TestAtla") -Beklenen "DUR" -Icerik "migration YOK"

# ---------------------------------------------------------------------------
# 5. Ayni durum, -MigrationGerekmiyor ile gecer
# ---------------------------------------------------------------------------
Senaryo -Ad "-MigrationGerekmiyor gecirir" -Arg @("-Konu", "ornek", "-TestAtla", "-MigrationGerekmiyor") -Beklenen "GEC" -Icerik "TESLIM EDILDI"

# ---------------------------------------------------------------------------
# 6. Model + migration birlikte -> gecer
# ---------------------------------------------------------------------------
Git-Sessiz -C $wtYol checkout -b feat/migration | Out-Null
Yaz-Dosya (Join-Path $wtYol "apps\backend-api\app\models\alarm.py") "class Alarm:`n    ikinci_kolon = 2`n"
Yaz-Dosya (Join-Path $wtYol "apps\backend-api\alembic_migrations\versions\0002_ikinci.py") @"
revision = "0002"
down_revision = "0001"
"@
Git-Sessiz -C $wtYol add -A | Out-Null
Git-Sessiz -C $wtYol commit -m "feat: ikinci kolon + migration" | Out-Null
Senaryo -Ad "model + migration gecer" -Arg @("-Konu", "ornek", "-TestAtla") -Beklenen "GEC" -Icerik "TESLIM EDILDI"

# ---------------------------------------------------------------------------
# 7. Iki basli migration zinciri -> durur
# ---------------------------------------------------------------------------
# Ayni ataya (0001) baglanan ikinci bir migration: alembic hangi kolu
# surecegini bilemez ve tek Postgres'te alembic_version HERKES icin bozulur.
Git-Sessiz -C $wtYol checkout -b feat/ikibas | Out-Null
Yaz-Dosya (Join-Path $wtYol "apps\backend-api\alembic_migrations\versions\0003_paralel.py") @"
revision = "0003"
down_revision = "0001"
"@
Git-Sessiz -C $wtYol add -A | Out-Null
Git-Sessiz -C $wtYol commit -m "feat: paralel migration" | Out-Null
Senaryo -Ad "iki basli zincir durdurur" -Arg @("-Konu", "ornek", "-TestAtla") -Beklenen "DUR" -Icerik "BASLI"

# ---------------------------------------------------------------------------
# 8. Cakisma -> prova yakalar, agac yarim kalmaz
# ---------------------------------------------------------------------------
Git-Sessiz -C $wtYol checkout -b feat/cakisma main | Out-Null
Yaz-Dosya (Join-Path $wtYol "docs\not.md") "dal tarafi`n"
Git-Sessiz -C $wtYol add -A | Out-Null
Git-Sessiz -C $wtYol commit -m "docs: dal tarafi" | Out-Null
# main'de ayni satiri baska turlu degistir.
Yaz-Dosya (Join-Path $kok "docs\not.md") "main tarafi`n"
Git-Sessiz -C $kok add docs/not.md | Out-Null
Git-Sessiz -C $kok commit -m "docs: main tarafi" | Out-Null
Senaryo -Ad "cakisma durdurur, rebase yarim kalmaz" -Arg @("-Konu", "ornek", "-TestAtla") -Beklenen "DUR" -Icerik "CAKISIYOR"

# Cakisma senaryosundan sonra worktree'nin SAGLAM olmasi sart: yarim rebase
# birakilmis olsaydi status "rebase in progress" derdi.
$script:sira++
$rebaseVar = (Test-Path (Join-Path $kok ".git\worktrees\ornek\rebase-merge")) -or
             (Test-Path (Join-Path $kok ".git\worktrees\ornek\rebase-apply"))
if (-not $rebaseVar) {
  Write-Host ("OK    {0,-2} {1,-42} {2}" -f $script:sira, "cakismadan sonra agac saglam", "TEMIZ")
} else {
  Write-Host ("HATA  {0,-2} {1,-42} yarim rebase kalmis" -f $script:sira, "cakismadan sonra agac saglam") -ForegroundColor Red
  $script:hata++
}

# ---------------------------------------------------------------------------
# Temizlik
# ---------------------------------------------------------------------------
Write-Host ""
try {
  Git-Sessiz -C $kok worktree remove --force $wtYol | Out-Null
  Remove-Item -Recurse -Force $kok
  Write-Host "Gecici depo silindi." -ForegroundColor DarkGray
} catch {
  Write-Host "Gecici depo silinemedi: $kok" -ForegroundColor Yellow
}

Write-Host ""
if ($script:hata -eq 0) {
  Write-Host "TUM SENARYOLAR GECTI ($script:sira senaryo)" -ForegroundColor Green
} else {
  Write-Host "$script:hata senaryo BASARISIZ" -ForegroundColor Red
  exit 1
}
