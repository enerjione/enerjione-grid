<#
.SYNOPSIS
  Oturum defterini gosterir/onarir: kim acik, hangi dalda, hangi portta,
  ne isle mesgul, nerede carpisiyor.

.DESCRIPTION
  Defter `.claude/oturumlar.json` (ana agacta, gitignore'da). Hook'lar onu
  otomatik yazar; bu script insan icin okunur hale getirir ve bozulursa
  onarir.

  KIPLER
    (varsayilan)  Tablo: oturumlar, canli Claude pencereleri, carpisan dosyalar.
    -Onar         Defteri diskteki gercek worktree'lerle sifirdan esitler.
    -Json         Ham JSON basar (baska araclara borulamak icin).

.PARAMETER Onar
  Defteri sil ve worktree listesinden yeniden kur. Bozuk/elle duzenlenmis
  defter icin.

.PARAMETER Json
  Insan tablosu yerine ham JSON.

.EXAMPLE
  .\tools\oturum-kayit.ps1
  .\tools\oturum-kayit.ps1 -Onar
#>
[CmdletBinding()]
param(
  [switch]$Onar,
  [switch]$Json
)

$ErrorActionPreference = "Stop"
function Yaz($metin, $renk = "Gray") { Write-Host $metin -ForegroundColor $renk }

. (Join-Path $PSScriptRoot "oturum-ortak.ps1")

$kayitYolu = Get-KayitYolu
if (-not $kayitYolu) { throw "Git deposu bulunamadi. Depo icinden calistirin." }

if ($Onar) {
  # Onbellegi de sil: eski kirli-dosya haritasi yeni defterle celisirse
  # kullanici duzelttigi seyin duzelmedigini sanir.
  Remove-Item $kayitYolu -ErrorAction SilentlyContinue
  Remove-Item (Get-OnbellekYolu) -ErrorAction SilentlyContinue
  Remove-Item "$kayitYolu.lock" -ErrorAction SilentlyContinue
  Yaz "Defter silindi; worktree listesinden yeniden kuruluyor..." "DarkGray"
}

$oturumlar = Get-Oturumlar
$pencereler = Get-Pencereler
$harita = Get-KirliHarita -Tazele

if ($Json) {
  [pscustomobject]@{
    oturumlar  = $oturumlar
    pencereler = $pencereler
    agaclar    = $harita
  } | ConvertTo-Json -Depth 6
  exit 0
}

Yaz ""
Yaz "OTURUM DEFTERI  ($kayitYolu)" "White"
Yaz ""

# --- Agaclar --------------------------------------------------------------
Yaz "CALISMA AGACLARI" "Cyan"
foreach ($a in $harita) {
  $kayit = $oturumlar | Where-Object { (ConvertTo-WindowsYol $_.yol) -eq (ConvertTo-WindowsYol $a.yol) } | Select-Object -First 1
  $port = "  -"
  if ($kayit) { $port = "  :$($kayit.backendPort)/:$($kayit.frontendPort)" }

  $sapma = ""
  if ($a.ileride -gt 0 -or $a.geride -gt 0) { $sapma = "  +$($a.ileride)/-$($a.geride)" }

  $renk = "Gray"
  if ($a.geride -gt 10) { $renk = "Yellow" }   # cok geriden gelen dal = merge kavgasi

  Yaz ("  {0,-26} {1,-34}{2}{3}" -f $a.konu, "[$($a.dal)]", $port, $sapma) $renk

  $p = $pencereler | Where-Object { (ConvertTo-WindowsYol $_.cwd) -eq (ConvertTo-WindowsYol $a.yol) }
  foreach ($x in @($p)) {
    $b = $x.baslik
    if (-not $b) { $b = "(henuz istek gelmedi)" }
    Yaz "        acik Claude oturumu: $b" "DarkCyan"
  }
  if ($a.dosyalar.Count -gt 0) {
    Yaz "        commit'lenmemis ($($a.dosyalar.Count)): $((@($a.dosyalar) | Select-Object -First 3) -join ', ')" "DarkGray"
  }
}

# --- Sahipsiz pencereler ---------------------------------------------------
# Depo disinda ya da silinmis bir dizinde acilmis Claude oturumlari.
$bilinen = @($harita | ForEach-Object { ConvertTo-WindowsYol $_.yol })
$oksuz = @($pencereler | Where-Object { $bilinen -notcontains (ConvertTo-WindowsYol $_.cwd) })
if ($oksuz.Count -gt 0) {
  Yaz ""
  Yaz "BASKA DIZINDEKI OTURUMLAR" "Cyan"
  foreach ($x in $oksuz) { Yaz "  $($x.cwd)  -- $($x.baslik)" "DarkGray" }
}

# --- Carpisan dosyalar -----------------------------------------------------
$sayac = @{}
foreach ($a in $harita) {
  foreach ($f in (@($a.dosyalar) + @($a.dalDosyalari) | Sort-Object -Unique)) {
    if (-not $f) { continue }
    if (-not $sayac.ContainsKey($f)) { $sayac[$f] = New-Object System.Collections.ArrayList }
    [void]$sayac[$f].Add($a.konu)
  }
}
$cakisan = @($sayac.Keys | Where-Object { $sayac[$_].Count -gt 1 } | Sort-Object)

Yaz ""
if ($cakisan.Count -eq 0) {
  Yaz "CARPISMA: yok -- hicbir dosyada iki oturum birden calismiyor." "Green"
} else {
  Yaz "CARPISAN DOSYALAR ($($cakisan.Count))" "Yellow"
  foreach ($f in $cakisan) {
    Yaz ("  {0}" -f $f) "Yellow"
    Yaz ("      {0}" -f ((@($sayac[$f]) | Sort-Object -Unique) -join ", ")) "DarkGray"
  }
}

Yaz ""
Yaz "Komutlar:" "White"
Yaz "  tools\oturum-ac.ps1 -Konu <ad> -VSCode        yeni oturum (ayri pencere)"
Yaz "  tools\oturum-birlestir.ps1 -Hepsi             main'e gore durum"
Yaz "  tools\oturum-birlestir.ps1 -Konu <ad> -Uygula rebase et"
Yaz "  tools\oturum-panel.ps1                        canli gorsel panel"
Yaz "  tools\oturum-kapat.ps1 -Konu <ad>             oturumu kapat"
Yaz ""
