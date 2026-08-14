<#
.SYNOPSIS
  main'e girmis yerel dallari toplu siler. Dal listesini tekrar okunur yapar.

.DESCRIPTION
  NEDEN VAR
  ---------
  Yazildigi gun depoda 84 yerel dal vardi; 81'inin isi main'e girmisti ve
  44'u yerlesik `--worktree` akisinin urettigi `worktree-agent-<hex>`
  dallariydi. Bunun bedeli teorik degil: "hangi is teslim edilmedi?" sorusuna
  bakan her arac (panel, surum-hazir, `git branch`) bu yiginin icinden
  gercekten bekleyen uc dali ayirmak zorunda kaliyordu. Okunmayan liste,
  olmayan listeyle ayni ise yarar.

  GUVENLIK: silme `git branch -d` iledir (`-D` DEGIL). Git, main'e girmemis
  bir dali `-d` ile SILMEZ; yani bu script tanim geregi is kaybettiremez.
  Worktree'si acik olan dallara da dokunulmaz -- onlar calisan oturumlardir.

.PARAMETER Uygula
  Raporla yetinme, dallari gercekten sil.

.PARAMETER Hepsi
  `worktree-agent-*` disindaki birlesmis dallari da sil. Varsayilan yalnizca
  otomatik uretilmis `worktree-agent-*` dallarini hedefler -- elle acilmis
  `feat/...` dallari isim tasimasa da baglam tasir.

.PARAMETER Hedef
  Birlesme olcutu. Varsayilan `main`.

.EXAMPLE
  .\tools\dal-temizle.ps1
  .\tools\dal-temizle.ps1 -Uygula
  .\tools\dal-temizle.ps1 -Hepsi -Uygula
#>
[CmdletBinding()]
param(
  [switch]$Uygula,
  [switch]$Hepsi,
  [string]$Hedef = "main"
)

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "oturum-ortak.ps1")

function Yaz($metin, $renk = "Gray") { Write-Host $metin -ForegroundColor $renk }

$anaKok = Get-AnaAgacKok
if (-not $anaKok) { throw "Git deposu bulunamadi." }

# Kaydi dusmus worktree'ler dallari "kullanimda" gosterir; once temizle.
& git -C $anaKok worktree prune 2>$null | Out-Null

# Worktree'de checkout edilmis dallar dokunulmazdir: silinemezler ve zaten
# calisan bir oturuma aittirler.
$mesgul = New-Object System.Collections.Generic.HashSet[string]
foreach ($w in Get-WorktreeListesi) {
  if ($w.Dal) { [void]$mesgul.Add($w.Dal) }
}

# DIKKAT: Invoke-GitOku ciktisi dogrudan pipe'a verilmez (bkz. surum-hazir.ps1).
$ham = Invoke-GitOku -C $anaKok for-each-ref --format="%(refname:short)" refs/heads/
$dallar = @(@($ham) | Where-Object { $_ -and $_ -ne $Hedef })

$silinecek = New-Object System.Collections.ArrayList
$bekleyen = New-Object System.Collections.ArrayList
$atlanan = New-Object System.Collections.ArrayList

foreach ($d in $dallar) {
  $n = Invoke-GitOku -C $anaKok rev-list --count "$Hedef..$d"
  $sayi = 0
  if ($n) { try { $sayi = [int](($n -join "").Trim()) } catch { $sayi = 0 } }

  if ($sayi -gt 0) { [void]$bekleyen.Add([pscustomobject]@{ Dal = $d; Sayi = $sayi }); continue }
  if ($mesgul.Contains($d)) { [void]$atlanan.Add($d); continue }
  if (-not $Hepsi -and $d -notlike "worktree-agent-*") { [void]$atlanan.Add($d); continue }
  [void]$silinecek.Add($d)
}

Write-Host ""
Write-Host "DAL TEMIZLIGI  --  olcut: $Hedef" -ForegroundColor Cyan
Write-Host "  toplam $($dallar.Count) yerel dal" -ForegroundColor DarkGray

if ($bekleyen.Count -gt 0) {
  Write-Host ""
  Yaz "TESLIM EDILMEMIS ($($bekleyen.Count)) -- bunlara DOKUNULMAZ:" "Yellow"
  foreach ($b in $bekleyen) { Yaz ("  {0,-42} +{1} commit" -f $b.Dal, $b.Sayi) }
  Yaz "  -> tools\oturum-teslim.ps1 -Konu <ad>" "DarkGray"
}

if ($atlanan.Count -gt 0) {
  Write-Host ""
  $sebep = "worktree'si acik ya da elle acilmis dal"
  if ($Hepsi) { $sebep = "worktree'si acik" }
  Yaz "ATLANAN ($($atlanan.Count)) -- $sebep" "DarkGray"
  foreach ($a in ($atlanan | Select-Object -First 8)) { Yaz "  $a" "DarkGray" }
  if ($atlanan.Count -gt 8) { Yaz "  ... ve $($atlanan.Count - 8) dal daha" "DarkGray" }
  if (-not $Hepsi) { Yaz "  (elle acilmis birlesmis dallari da silmek icin: -Hepsi)" "DarkGray" }
}

Write-Host ""
if ($silinecek.Count -eq 0) {
  Yaz "Silinecek dal yok." "Green"
  exit 0
}

Yaz "SILINECEK ($($silinecek.Count)) -- hepsi $Hedef'e girmis:" "White"
foreach ($s in ($silinecek | Select-Object -First 10)) { Yaz "  $s" }
if ($silinecek.Count -gt 10) { Yaz "  ... ve $($silinecek.Count - 10) dal daha" }

if (-not $Uygula) {
  Write-Host ""
  Yaz "(rapor kipi -- hicbir sey silinmedi. Silmek icin: -Uygula)" "DarkGray"
  exit 0
}

Write-Host ""
$basarili = 0
$dusen = New-Object System.Collections.ArrayList
foreach ($s in $silinecek) {
  # `-d`: birlesmemis dali git zaten reddeder. Cikti yutulur, sayilir.
  & git -C $anaKok branch -d $s 2>$null | Out-Null
  if ($LASTEXITCODE -eq 0) { $basarili++ } else { [void]$dusen.Add($s) }
}

Yaz "$basarili dal silindi." "Green"
if ($dusen.Count -gt 0) {
  Yaz "$($dusen.Count) dal silinemedi (git birlesmemis sayiyor -- is kaybi riski, dokunulmadi):" "Yellow"
  foreach ($d in ($dusen | Select-Object -First 10)) { Yaz "  $d" "DarkGray" }
}
