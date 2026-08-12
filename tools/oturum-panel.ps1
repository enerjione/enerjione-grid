<#
.SYNOPSIS
  Paralel oturumlari CANLI gosteren pixel-art panel (yerel web sunucusu).

.DESCRIPTION
  NE GOSTERIR
  -----------
  Her oturum bir "coworker": kendi masasi, kendi ekrani, kendi durumu.
    * yaziyor   -- commit'lenmemis degisiklik var, oturum canli
    * bekliyor  -- oturum canli, agac temiz
    * uykuda    -- acik Claude penceresi yok (worktree duruyor)
  Ustune: dal, port, ne isle mesgul, main'e gore +ileride/-geride, ve
  ikisinin ayni dosyada calistigi durumlarda masalar arasi KIRMIZI bag.

  NEDEN AYRI SUNUCU
  -----------------
  Tarayici yerel dosyayi fetch edemez (file:// icin CORS). En kucuk cozum
  bir yerel HTTP sunucusu. `HttpListener` bazi Windows kurulumlarinda URL
  rezervasyonu (yonetici hakki) ister; `TcpListener` istemez -- bu yuzden
  HTTP cevabi elle yaziliyor. Ek bagimlilik yok.

  VERI KAYNAGI: tools/oturum-ortak.ps1 defteri ve kirli-dosya haritasi;
  yani hook'larin zaten tuttugu bilgi. Panel yeni bir dogruluk kaynagi
  URETMEZ, olani gosterir.

.PARAMETER Port
  Dinlenecek port. Varsayilan 7373 (uygulama portlariyla -- 5173+/8000+ --
  cakismamasi icin bilerek uzak secildi).

.PARAMETER Ac
  Panel hazir olunca tarayiciyi da ac.

.EXAMPLE
  .\tools\oturum-panel.ps1 -Ac
#>
[CmdletBinding()]
param(
  [int]$Port = 7373,
  [switch]$Ac
)

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "oturum-ortak.ps1")

$sayfaYolu = Join-Path $PSScriptRoot "oturum-panel.html"
if (-not (Test-Path $sayfaYolu)) { throw "Panel sayfasi bulunamadi: $sayfaYolu" }

<#
  Panelin tek veri ucu. Defteri ve kirli-dosya haritasini panelin
  anlayacagi bicime cevirir.
#>
function Get-PanelVerisi {
  $oturumlar = Get-Oturumlar
  $harita = Get-KirliHarita -Tazele
  $izler = Get-OturumIzleri
  $posta = @(Read-Posta)
  $simdi = (Get-Date).ToUniversalTime()

  $ajanlar = New-Object System.Collections.ArrayList
  foreach ($a in $harita) {
    $yol = ConvertTo-WindowsYol $a.yol
    $kayit = $oturumlar | Where-Object { (ConvertTo-WindowsYol $_.yol) -eq $yol } | Select-Object -First 1
    $iz = $izler[$yol]

    # CANLILIK OLCUSU: transkriptin son yazilma zamani. Defterdeki pencere
    # kaydi da var ama o yalnizca hook'lar yuklendiginde doluyor; transkript
    # her oturumda, hemen, kosulsuz var. Ikisinden GERCEK olani bu.
    $saniye = -1
    if ($iz -and $iz.sonHareket) {
      try { $saniye = [int]($simdi - [datetime]::Parse($iz.sonHareket).ToUniversalTime()).TotalSeconds } catch { }
    }
    $kirli = @($a.dosyalar).Count
    if ($saniye -ge 0 -and $saniye -lt 90) { $durum = "yaziyor" }
    elseif ($saniye -ge 0 -and $saniye -lt 1800) { $durum = "bekliyor" }
    else { $durum = "uykuda" }

    $ad = [string]$a.konu
    if ($a.anaMi) { $ad = "ana" }

    [void]$ajanlar.Add([pscustomobject]@{
      konu       = $ad
      dal        = [string]$a.dal
      anaMi      = [bool]$a.anaMi
      port       = $(if ($kayit) { [int]$kayit.backendPort } else { 0 })
      frontPort  = $(if ($kayit) { [int]$kayit.frontendPort } else { 0 })
      baslik     = $(if ($iz) { [string]$iz.sonIstek } else { "" })
      suAnArac   = $(if ($iz) { [string]$iz.suAnArac } else { "" })
      suAnHedef  = $(if ($iz) { [string]$iz.suAnHedef } else { "" })
      todoTamam  = $(if ($iz) { [int]$iz.todoTamam } else { 0 })
      todoToplam = $(if ($iz) { [int]$iz.todoToplam } else { 0 })
      todoSuAn   = $(if ($iz) { [string]$iz.todoSuAn } else { "" })
      saniye     = $saniye
      durum      = $durum
      oturumSayisi = $(if ($iz) { [int]$iz.oturumSayisi } else { 0 })
      dizin      = $yol
      ileride    = [int]$a.ileride
      geride     = [int]$a.geride
      kirli      = $kirli
      dosyalar   = @(@($a.dosyalar) | Select-Object -First 8)
      okunmamis  = @($posta | Where-Object {
                      ($_.kime -eq $ad -or $_.kime -eq "*") -and
                      ($_.kimden -ne $ad) -and (@($_.okuyan) -notcontains $ad)
                    }).Count
    })
  }

  # Carpismalar: ayni dosyaya birden fazla agacin dokunmus olmasi.
  $sayac = @{}
  foreach ($a in $harita) {
    foreach ($f in (@($a.dosyalar) + @($a.dalDosyalari) | Sort-Object -Unique)) {
      if (-not $f) { continue }
      if (-not $sayac.ContainsKey($f)) { $sayac[$f] = New-Object System.Collections.ArrayList }
      [void]$sayac[$f].Add([string]$a.konu)
    }
  }
  $carpismalar = New-Object System.Collections.ArrayList
  foreach ($f in ($sayac.Keys | Sort-Object)) {
    $kimler = @(@($sayac[$f]) | Sort-Object -Unique)
    if ($kimler.Count -lt 2) { continue }
    [void]$carpismalar.Add([pscustomobject]@{ dosya = $f; kimler = $kimler })
  }

  # Son mesajlar: panelin sohbet akisi.
  $mesajlar = New-Object System.Collections.ArrayList
  foreach ($m in (@($posta) | Select-Object -Last 30)) {
    [void]$mesajlar.Add([pscustomobject]@{
      kimden  = [string]$m.kimden
      kime    = [string]$m.kime
      metin   = [string]$m.metin
      saat    = $(try { [datetime]::Parse($m.zaman).ToLocalTime().ToString("HH:mm") } catch { "" })
      okuyan  = @($m.okuyan)
    })
  }

  $anaKok = Get-AnaAgacKok
  return [pscustomobject]@{
    guncelleme  = (Get-Date).ToString("HH:mm:ss")
    proje       = $(if ($anaKok) { Split-Path -Leaf $anaKok } else { "?" })
    projeYolu   = [string]$anaKok
    ajanlar     = @($ajanlar)
    carpismalar = @($carpismalar)
    mesajlar    = @($mesajlar)
  }
}

<#
  Panelden mesaj gonderme. Gonderen "panel" olarak kaydedilir -- kullanicinin
  kendisi yaziyor, bir oturum degil; alici tarafta bunun ayirt edilmesi onemli.
#>
function Invoke-PanelGonder {
  param([string]$Sorgu)
  $ayrik = @{}
  foreach ($p in ($Sorgu -split "&")) {
    $es = $p.IndexOf("=")
    if ($es -lt 1) { continue }
    $ad = $p.Substring(0, $es)
    $deger = [System.Uri]::UnescapeDataString($p.Substring($es + 1).Replace("+", " "))
    $ayrik[$ad] = $deger
  }
  $kime = [string]$ayrik["kime"]
  $metin = [string]$ayrik["metin"]
  if ([string]::IsNullOrWhiteSpace($kime) -or [string]::IsNullOrWhiteSpace($metin)) {
    return [pscustomobject]@{ tamam = $false; hata = "kime/metin bos" }
  }
  Send-OturumMesaji -Kime $kime -Metin $metin -Kimden "panel" | Out-Null
  return [pscustomobject]@{ tamam = $true }
}

# --- Minimal HTTP -----------------------------------------------------------
function Send-Cevap {
  param($Akis, [string]$Tur, [byte[]]$Govde, [int]$Kod = 200)
  $basliklar = @(
    "HTTP/1.1 $Kod OK",
    "Content-Type: $Tur",
    "Content-Length: $($Govde.Length)",
    "Cache-Control: no-store",
    "Connection: close",
    "", ""
  ) -join "`r`n"
  $bas = [System.Text.Encoding]::ASCII.GetBytes($basliklar)
  $Akis.Write($bas, 0, $bas.Length)
  $Akis.Write($Govde, 0, $Govde.Length)
  $Akis.Flush()
}

$dinleyici = New-Object System.Net.Sockets.TcpListener([System.Net.IPAddress]::Loopback, $Port)
try {
  $dinleyici.Start()
} catch {
  throw "Port $Port dinlenemedi (baska bir panel acik olabilir): $($_.Exception.Message)"
}

Write-Host ""
Write-Host "  OTURUM PANELI calisiyor:  http://localhost:$Port/" -ForegroundColor Green
Write-Host "  Durdurmak icin: Ctrl+C" -ForegroundColor DarkGray
Write-Host ""

if ($Ac) { Start-Process "http://localhost:$Port/" | Out-Null }

try {
  while ($true) {
    $istemci = $dinleyici.AcceptTcpClient()
    try {
      $akis = $istemci.GetStream()
      $akis.ReadTimeout = 3000

      # Istek satirini oku. Govde ile ilgilenmiyoruz (yalnizca GET var).
      $tampon = New-Object byte[] 2048
      $okunan = $akis.Read($tampon, 0, $tampon.Length)
      if ($okunan -le 0) { continue }
      $istek = [System.Text.Encoding]::ASCII.GetString($tampon, 0, $okunan)
      $ilk = ($istek -split "`r?`n")[0]
      $yol = "/"
      if ($ilk -match '^\w+\s+(\S+)') { $yol = $Matches[1] }

      if ($yol -like "/durum*") {
        $json = (Get-PanelVerisi | ConvertTo-Json -Depth 6 -Compress)
        Send-Cevap $akis "application/json; charset=utf-8" ([System.Text.Encoding]::UTF8.GetBytes($json))
      } elseif ($yol -like "/gonder?*") {
        $sonuc = Invoke-PanelGonder -Sorgu ($yol.Substring($yol.IndexOf("?") + 1))
        $json = ($sonuc | ConvertTo-Json -Compress)
        Send-Cevap $akis "application/json; charset=utf-8" ([System.Text.Encoding]::UTF8.GetBytes($json))
      } elseif ($yol -eq "/" -or $yol -like "/index*") {
        $html = Get-Content $sayfaYolu -Raw -Encoding UTF8
        Send-Cevap $akis "text/html; charset=utf-8" ([System.Text.Encoding]::UTF8.GetBytes($html))
      } else {
        Send-Cevap $akis "text/plain; charset=utf-8" ([System.Text.Encoding]::UTF8.GetBytes("yok")) 404
      }
    } catch {
      # Tek bir istegin hatasi sunucuyu dusurmez: tarayici sekmesini
      # kapatinca yarim kalan baglantilar normaldir.
    } finally {
      $istemci.Close()
    }
  }
} finally {
  $dinleyici.Stop()
  Write-Host "Panel durduruldu." -ForegroundColor DarkGray
}
