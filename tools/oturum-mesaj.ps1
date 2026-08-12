<#
.SYNOPSIS
  Oturumlar arasi mesaj gonderir / posta kutusunu gosterir.

.DESCRIPTION
  Oturumlar ortak bir amaca calisiyor ama birbirlerine tek kelime
  edemiyorlardi. "types.ts'e dokunuyorum, 10 dakika bekle" ya da "migration'i
  once ben alayim" demenin yolu, kullanicinin dort sekme arasinda mesaji elle
  tasimasiydi.

  TESLIM -- bunu bilerek acikca soyluyoruz: mesaj ANLIK ULASMAZ. Bir Claude
  oturumu ancak sirasi geldiginde (kullanici ona bir istek gonderdiginde)
  baglam alir; mesaj o anda UserPromptSubmit hook'u ile baglamina duser.
  Hedef oturum bos bekliyorsa mesaj posta kutusunda durur. Panel okunmamis
  mesajlari gosterir, boylece "ulasti mi" sorusu ortada kalmaz.

.PARAMETER Kime
  Hedef oturum adi (worktree klasor adi). Ana agac icin `ana`.
  Herkese gondermek icin `*`.

.PARAMETER Mesaj
  Gonderilecek metin.

.PARAMETER Oku
  Bu oturuma gelen okunmamis mesajlari goster ve OKUNDU isaretle.

.PARAMETER Tumu
  Butun posta kutusunu goster (okunmuslar dahil), hicbir sey isaretleme.

.EXAMPLE
  .\tools\oturum-mesaj.ps1 -Kime alarm-gecmisi -Mesaj "types.ts'e dokunuyorum, 10 dk"
  .\tools\oturum-mesaj.ps1 -Kime * -Mesaj "migration 0059'u ben aliyorum"
  .\tools\oturum-mesaj.ps1 -Oku
  .\tools\oturum-mesaj.ps1 -Tumu
#>
[CmdletBinding(DefaultParameterSetName = "Gonder")]
param(
  [Parameter(ParameterSetName = "Gonder", Mandatory = $true, Position = 0)][string]$Kime,
  [Parameter(ParameterSetName = "Gonder", Mandatory = $true, Position = 1)][string]$Mesaj,
  [Parameter(ParameterSetName = "Oku")][switch]$Oku,
  [Parameter(ParameterSetName = "Tumu")][switch]$Tumu
)

$ErrorActionPreference = "Stop"
function Yaz($metin, $renk = "Gray") { Write-Host $metin -ForegroundColor $renk }

. (Join-Path $PSScriptRoot "oturum-ortak.ps1")

$ben = Get-BuOturumAdi

if ($Tumu) {
  $hepsi = @(Read-Posta)
  Yaz ""
  Yaz "POSTA KUTUSU ($($hepsi.Count) mesaj) -- bu oturum: $ben" "White"
  Yaz ""
  if ($hepsi.Count -eq 0) { Yaz "  (bos)" "DarkGray"; exit 0 }
  foreach ($m in $hepsi) {
    $okundu = if (@($m.okuyan).Count -gt 0) { "okundu: $((@($m.okuyan)) -join ', ')" } else { "OKUNMADI" }
    $renk = if (@($m.okuyan).Count -gt 0) { "DarkGray" } else { "Yellow" }
    Yaz ("  {0}  {1} -> {2}" -f $m.zaman.Substring(11, 5), $m.kimden, $m.kime) $renk
    Yaz ("      {0}" -f $m.metin)
    Yaz ("      {0}" -f $okundu) "DarkGray"
  }
  exit 0
}

if ($Oku) {
  $gelen = @(Receive-OturumMesajlari -Oturum $ben)
  if ($gelen.Count -eq 0) { Yaz "Yeni mesaj yok ($ben)." "DarkGray"; exit 0 }
  Yaz ""
  Yaz "YENI MESAJ ($($gelen.Count)) -- $ben" "Cyan"
  foreach ($m in $gelen) {
    Yaz ("  {0} diyor ki:" -f $m.kimden) "White"
    Yaz ("      {0}" -f $m.metin)
  }
  Yaz ""
  exit 0
}

# --- Gonder ---------------------------------------------------------------
# Hedefi dogrula: yanlis yazilan bir ad mesaji sessizce kaybettirir.
$gecerli = @("*", "ana") + @(Get-Oturumlar | ForEach-Object { $_.konu })
if ($gecerli -notcontains $Kime) {
  Yaz "Boyle bir oturum yok: $Kime" "Red"
  Yaz "Gecerli hedefler: $((@($gecerli) | Sort-Object -Unique) -join ', ')" "White"
  exit 1
}

$m = Send-OturumMesaji -Kime $Kime -Metin $Mesaj -Kimden $ben
Yaz "Gonderildi: $ben -> $($m.kime)" "Green"
Yaz "  $($m.metin)" "DarkGray"
Yaz ""
Yaz "Not: mesaj hedef oturumun BIR SONRAKI adiminda baglamina duser." "DarkGray"
Yaz "Oturum bos bekliyorsa orada bir istek gonderilene kadar posta kutusunda kalir." "DarkGray"
