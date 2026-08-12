<#
.SYNOPSIS
  Paralel oturum altyapisinin davranis testi (defter, slot, carpisma, hook'lar).

.DESCRIPTION
  KAPSAM
    * Slot/port dagitimi -- bir oturum kapatilip yenisi acilinca ayni portun
      ikinci kez dagitilmamasi (eski hesabin somut hatasi).
    * Yol donusumu -- worktree ana agacin ICINDE oldugu icin "en uzun kok
      kazanir" kurali; yanlis kok, gorele yolu bozar ve carpisma hic gorunmez.
    * Altyapi dosyasi filtresi -- OTURUM.md/.env her agacta kirli gorunur,
      sayilsalardi tablo gurultuye bogulurdu.
    * Defter turu -- canli pencere ekle/tazele/sil; bos baslikla tazeleyince
      mevcut basligin KORUNMASI.
    * Hook'larin gercekten calismasi -- carpisma, oturum-durum ve oturum-baslik
      hook'lari asil hook yuku ile beslenip ciktilari dogrulanir.

  YAN ETKI YOK: testler gecici bir kimlikle ("test-oturum-*") calisir ve
  kendi kayitlarini siler. Gercek defter icerigine dokunulmaz.

  Hook engelleme testleri ayri dosyada: tools/oturum-koruma-test.ps1
  (o test ANA AGACTAN kosulmali; bu dosya her yerden kosar).

.EXAMPLE
  .\tools\oturum-test.ps1
#>
$ErrorActionPreference = "Stop"

. (Join-Path $PSScriptRoot "oturum-ortak.ps1")

$script:gecen = 0
$script:kalan = 0

function Sina([string]$ad, [scriptblock]$blok) {
  try {
    $sonuc = & $blok
    if ($sonuc) {
      Write-Host ("OK    {0}" -f $ad)
      $script:gecen++
    } else {
      Write-Host ("HATA  {0}" -f $ad) -ForegroundColor Red
      $script:kalan++
    }
  } catch {
    Write-Host ("HATA  {0}  -- {1}" -f $ad, $_.Exception.Message) -ForegroundColor Red
    $script:kalan++
  }
}

Write-Host ""
Write-Host "PARALEL OTURUM ALTYAPISI -- DAVRANIS TESTI" -ForegroundColor White
Write-Host ""

# --- Slot dagitimi ---------------------------------------------------------
Write-Host "slot/port dagitimi" -ForegroundColor Cyan
Sina "bos listede ilk slot 1" { (Get-BosSlot -Kullanilan @()) -eq 1 }
Sina "1,2,3 doluyken 4" { (Get-BosSlot -Kullanilan @(1, 2, 3)) -eq 4 }
Sina "ORTADAKI bosluk yeniden kullanilir (eski hatanin dogru davranisi)" {
  # Eski hesap "dizin sayisi + 1" idi: 1 ve 3 acikken 3 dondururdu ve
  # calisan bir oturumun portunu ikinci kez dagitirdi.
  (Get-BosSlot -Kullanilan @(1, 3)) -eq 2
}
Sina "sirasiz girdi onemsiz" { (Get-BosSlot -Kullanilan @(5, 1, 2)) -eq 3 }

# --- Altyapi dosyasi filtresi ---------------------------------------------
Write-Host ""
Write-Host "altyapi dosyasi filtresi" -ForegroundColor Cyan
Sina "OTURUM.md elenir" { Test-AltyapiDosyasi "OTURUM.md" }
Sina "apps/backend-api/.env elenir" { Test-AltyapiDosyasi "apps/backend-api/.env" }
Sina "defterin kendisi elenir" { Test-AltyapiDosyasi ".claude/oturumlar.json" }
Sina "gercek kaynak dosyasi ELENMEZ" { -not (Test-AltyapiDosyasi "apps/frontend-web/src/shared/types.ts") }
Sina ".env.example ELENMEZ (izlenen dosya)" { -not (Test-AltyapiDosyasi "apps/backend-api/.env.example") }

# --- Yol donusumu ----------------------------------------------------------
Write-Host ""
Write-Host "depo-goreli yol" -ForegroundColor Cyan
$anaKok = Get-AnaAgacKok
Sina "ana agac kokunu bulur" { -not [string]::IsNullOrWhiteSpace($anaKok) }
Sina "ana agactaki dosya gorele olur" {
  (Get-DepoGoreliYol -Yol (Join-Path $anaKok "apps\frontend-web\src\shared\types.ts")) -eq "apps/frontend-web/src/shared/types.ts"
}
Sina "WORKTREE icindeki dosya AYNI goreleyi verir (en uzun kok kazanir)" {
  # Worktree'ler ana agacin icinde (.claude\worktrees\...). Kisa kok once
  # eslesirse gorele yol ".claude/worktrees/x/apps/..." cikar ve hicbir seyle
  # eslesmez -- carpisma sessizce hic gorunmez.
  $wt = @(Get-WorktreeListesi | Where-Object { -not $_.AnaMi }) | Select-Object -First 1
  if (-not $wt) { return $true }   # worktree yoksa test konusuz
  (Get-DepoGoreliYol -Yol (Join-Path $wt.Yol "apps\frontend-web\src\shared\types.ts")) -eq "apps/frontend-web/src/shared/types.ts"
}
Sina "depo disindaki yol icin null" {
  $null -eq (Get-DepoGoreliYol -Yol "C:\Windows\System32\drivers\etc\hosts")
}

# --- .env'den slot okuma ---------------------------------------------------
Write-Host ""
Write-Host "yazili porttan slot" -ForegroundColor Cyan
$gecici = Join-Path ([System.IO.Path]::GetTempPath()) ("oturum-test-" + [System.Guid]::NewGuid().ToString("N").Substring(0, 8))
New-Item -ItemType Directory -Path (Join-Path $gecici "apps\frontend-web") -Force | Out-Null
Sina ".env yoksa null" { $null -eq (Get-YaziliSlot -WorktreeYolu $gecici) }
Sina "VITE_API_BASE_URL'den slot cikar" {
  "VITE_API_BASE_URL=http://localhost:8003/api/v1" |
    Set-Content (Join-Path $gecici "apps\frontend-web\.env") -Encoding UTF8
  (Get-YaziliSlot -WorktreeYolu $gecici) -eq 3
}
Sina "sacma port reddedilir" {
  "VITE_API_BASE_URL=http://localhost:443/api/v1" |
    Set-Content (Join-Path $gecici "apps\frontend-web\.env") -Encoding UTF8
  $null -eq (Get-YaziliSlot -WorktreeYolu $gecici)
}
Remove-Item $gecici -Recurse -Force -ErrorAction SilentlyContinue

# --- Defter: canli pencereler ---------------------------------------------
Write-Host ""
Write-Host "defter -- canli pencereler" -ForegroundColor Cyan
$testId = "test-oturum-" + [System.Guid]::NewGuid().ToString("N").Substring(0, 8)
try {
  Sina "pencere eklenir ve baslik yazilir" {
    Update-Pencere -Id $testId -Baslik "test islemi"
    $p = Get-Pencereler | Where-Object { $_.id -eq $testId } | Select-Object -First 1
    $p -and $p.baslik -eq "test islemi"
  }
  Sina "BOS baslikla tazeleme mevcut basligi KORUR" {
    # Aksi halde her istekte baslik ustune yazilir ve tablo oturumun isini
    # degil son cumlesini gosterir.
    Update-Pencere -Id $testId -Baslik ""
    $p = Get-Pencereler | Where-Object { $_.id -eq $testId } | Select-Object -First 1
    $p -and $p.baslik -eq "test islemi"
  }
  Sina "yeni baslik ustune yazar" {
    Update-Pencere -Id $testId -Baslik "ikinci is"
    $p = Get-Pencereler | Where-Object { $_.id -eq $testId } | Select-Object -First 1
    $p.baslik -eq "ikinci is"
  }
  Sina "pencere silinir" {
    Remove-Pencere -Id $testId
    -not (Get-Pencereler | Where-Object { $_.id -eq $testId })
  }
} finally {
  Remove-Pencere -Id $testId
}

# --- Kirli harita ----------------------------------------------------------
Write-Host ""
Write-Host "kirli dosya haritasi" -ForegroundColor Cyan
$harita = Get-KirliHarita -Tazele
Sina "her agac icin kayit uretir" { @($harita).Count -ge 1 }
Sina "ana agac isaretli" { @($harita | Where-Object { $_.anaMi }).Count -eq 1 }
Sina "beklenen alanlar var" {
  $a = @($harita)[0]
  ($null -ne $a.dosyalar) -and ($null -ne $a.dalDosyalari) -and ($null -ne $a.geride)
}
Sina "onbellek ikinci cagride hizli doner (<1sn)" {
  $s = [System.Diagnostics.Stopwatch]::StartNew()
  Get-KirliHarita | Out-Null
  $s.Stop()
  $s.Elapsed.TotalSeconds -lt 1
}

# --- Hook'lar: gercek yukle -------------------------------------------------
Write-Host ""
Write-Host "hook'lar" -ForegroundColor Cyan

function Hook-Calistir([string]$dosya, [string]$json) {
  $yol = Join-Path $PSScriptRoot $dosya
  return ($json | & powershell -NoProfile -ExecutionPolicy Bypass -File $yol) -join "`n"
}

Sina "oturum-durum.ps1 gecerli JSON ve durum metni doner" {
  $c = Hook-Calistir "oturum-durum.ps1" '{"session_id":"test-durum","hook_event_name":"SessionStart"}'
  $v = $c | ConvertFrom-Json
  $v.hookSpecificOutput.additionalContext -match "PARALEL OTURUM DURUMU"
}
Sina "oturum-baslik.ps1 sessiz calisir" {
  $c = Hook-Calistir "oturum-baslik.ps1" '{"session_id":"test-baslik-gecici","prompt":"birinci satir\nikinci","cwd":""}'
  [string]::IsNullOrWhiteSpace($c)
}
Sina "oturum-baslik.ps1 basligi ILK SATIRDAN alir" {
  $p = Get-Pencereler | Where-Object { $_.id -eq "test-baslik-gecici" } | Select-Object -First 1
  $sonuc = $p -and $p.baslik -eq "birinci satir"
  Remove-Pencere -Id "test-baslik-gecici"
  $sonuc
}
Sina "oturum-carpisma.ps1 ILGISIZ dosyada sessiz" {
  $sahte = (Join-Path $anaKok "apps\boyle-bir-dosya-yok-12345.ts") -replace '\\', '\\'
  $c = Hook-Calistir "oturum-carpisma.ps1" ('{"tool_input":{"file_path":"' + $sahte + '"}}')
  [string]::IsNullOrWhiteSpace($c)
}
Sina "oturum-carpisma.ps1 depo DISI yolda sessiz" {
  $c = Hook-Calistir "oturum-carpisma.ps1" '{"tool_input":{"file_path":"C:\\Windows\\notepad.exe"}}'
  [string]::IsNullOrWhiteSpace($c)
}
Sina "oturum-carpisma.ps1 bos girdide sessiz (araci bloklamaz)" {
  [string]::IsNullOrWhiteSpace((Hook-Calistir "oturum-carpisma.ps1" ""))
}
Sina "oturum-carpisma.ps1 GERCEK carpismayi bildirir" {
  # Baska bir agacta degismis bir dosya bul; yoksa test konusuz.
  $buKok = ConvertTo-WindowsYol ((Invoke-GitOku rev-parse --show-toplevel) -join "")
  $aday = $null
  foreach ($a in $harita) {
    if ((ConvertTo-WindowsYol $a.yol) -eq $buKok) { continue }
    $d = @(@($a.dosyalar) + @($a.dalDosyalari)) | Select-Object -First 1
    if ($d) { $aday = $d; break }
  }
  if (-not $aday) { return $true }
  $tam = (Join-Path $buKok ($aday -replace '/', '\')) -replace '\\', '\\'
  $c = Hook-Calistir "oturum-carpisma.ps1" ('{"tool_input":{"file_path":"' + $tam + '"}}')
  ($c | ConvertFrom-Json).hookSpecificOutput.additionalContext -match "CARPISMA UYARISI"
}

# --- Mesajlasma -------------------------------------------------------------
Write-Host ""
Write-Host "oturumlar arasi mesajlasma" -ForegroundColor Cyan
$mA = "test-ajan-a"; $mB = "test-ajan-b"
$temizle = {
  $idler = @(Read-Posta | Where-Object { $_.kimden -like "test-ajan-*" } | ForEach-Object { $_.id })
  if ($idler.Count -gt 0) {
    Invoke-PostaKilitli { param($l) return @(@($l) | Where-Object { $idler -notcontains $_.id }) } | Out-Null
  }
}
& $temizle
try {
  Sina "mesaj gonderilir ve hedefe dusor" {
    Send-OturumMesaji -Kime $mB -Metin "types.ts'e dokunuyorum" -Kimden $mA | Out-Null
    $g = @(Receive-OturumMesajlari -Oturum $mB -SadeceBak)
    @($g | Where-Object { $_.metin -eq "types.ts'e dokunuyorum" }).Count -eq 1
  }
  Sina "okununca BIR DAHA teslim edilmez" {
    Receive-OturumMesajlari -Oturum $mB | Out-Null
    @(Receive-OturumMesajlari -Oturum $mB -SadeceBak).Count -eq 0
  }
  Sina "gonderen kendi mesajini almaz" {
    Send-OturumMesaji -Kime "*" -Metin "herkese duyuru" -Kimden $mA | Out-Null
    @(Receive-OturumMesajlari -Oturum $mA -SadeceBak | Where-Object { $_.metin -eq "herkese duyuru" }).Count -eq 0
  }
  Sina "'*' mesaji BASKA oturuma dusor" {
    @(Receive-OturumMesajlari -Oturum $mB -SadeceBak | Where-Object { $_.metin -eq "herkese duyuru" }).Count -eq 1
  }
  Sina "bir oturumun okumasi digerini etkilemez" {
    Receive-OturumMesajlari -Oturum $mB | Out-Null
    @(Receive-OturumMesajlari -Oturum "test-ajan-c" -SadeceBak | Where-Object { $_.metin -eq "herkese duyuru" }).Count -eq 1
  }
  Sina "hook mesaji baglama enjekte eder" {
    Send-OturumMesaji -Kime (Get-BuOturumAdi) -Metin "hook teslim denemesi" -Kimden $mA | Out-Null
    $c = Hook-Calistir "oturum-baslik.ps1" ('{"session_id":"test-teslim","prompt":"devam","cwd":"' +
         ((Get-Location).Path -replace '\\', '\\') + '"}')
    ($c | ConvertFrom-Json).hookSpecificOutput.additionalContext -match "hook teslim denemesi"
  }
} finally {
  & $temizle
  Remove-Pencere -Id "test-teslim"
}

# --- Transkript izi ---------------------------------------------------------
Write-Host ""
Write-Host "oturum izi (transkript)" -ForegroundColor Cyan
Sina "bu oturumun transkripti bulunur" {
  $b = Get-TranskriptBilgisi -CalismaDizini (Get-Location).Path
  $b.Yol -and (Test-Path $b.Yol)
}
Sina "olmayan dizin icin bos doner" {
  (Get-TranskriptBilgisi -CalismaDizini "C:\boyle-bir-yer-yok-98765").Yol -eq $null
}
Sina "ozet bu oturumun son istegini okur" {
  $b = Get-TranskriptBilgisi -CalismaDizini (Get-Location).Path
  $o = Read-TranskriptOzeti -Yol $b.Yol
  -not [string]::IsNullOrWhiteSpace($o.sonIstek)
}
Sina "izler onbellekten hizli doner (<2sn)" {
  Get-OturumIzleri | Out-Null
  $s = [System.Diagnostics.Stopwatch]::StartNew()
  $iz = Get-OturumIzleri
  $s.Stop()
  ($s.Elapsed.TotalSeconds -lt 2) -and (@($iz.Keys).Count -ge 1)
}

# --- settings.json tutarliligi ---------------------------------------------
Write-Host ""
Write-Host "yapilandirma" -ForegroundColor Cyan
$ayarYolu = Join-Path (Split-Path -Parent $PSScriptRoot) ".claude\settings.json"
Sina "settings.json cozulebilir JSON" { $null -ne ((Get-Content $ayarYolu -Raw -Encoding UTF8) | ConvertFrom-Json) }
Sina "her hook komutu var olan bir dosyaya isaret ediyor" {
  $ayar = (Get-Content $ayarYolu -Raw -Encoding UTF8) | ConvertFrom-Json
  $eksik = @()
  foreach ($olay in $ayar.hooks.PSObject.Properties) {
    foreach ($grup in @($olay.Value)) {
      foreach ($h in @($grup.hooks)) {
        if ($h.command -match 'tools/([a-z0-9\-]+\.ps1)') {
          $d = Join-Path $PSScriptRoot $Matches[1]
          if (-not (Test-Path $d)) { $eksik += $Matches[1] }
        }
      }
    }
  }
  if ($eksik.Count -gt 0) { Write-Host "      eksik: $($eksik -join ', ')" -ForegroundColor Red }
  $eksik.Count -eq 0
}
Sina "panel sayfasi yaninda duruyor" { Test-Path (Join-Path $PSScriptRoot "oturum-panel.html") }
Sina "defter dosyalari .gitignore'da (yerel makine durumu)" {
  $gi = Get-Content (Join-Path (Split-Path -Parent $PSScriptRoot) ".gitignore") -Raw -Encoding UTF8
  ($gi -match '\.claude/oturumlar\.json') -and ($gi -match '\.claude/oturumlar\.cache\.json')
}

Write-Host ""
if ($script:kalan -eq 0) {
  Write-Host "TUM DURUMLAR GECTI ($($script:gecen) durum)" -ForegroundColor Green
  exit 0
} else {
  Write-Host "$($script:kalan) durum BASARISIZ ($($script:gecen) gecti)" -ForegroundColor Red
  exit 1
}
