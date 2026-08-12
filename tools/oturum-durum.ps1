<#
.SYNOPSIS
  SessionStart hook'u: oturumu deftere yazar ve TUM tablonun resmini
  modelin baglamina koyar.

.DESCRIPTION
  NEDEN HOOK
  ----------
  Paralel oturum kurali CLAUDE.md'de yaziyor ama bir oturumun PAYLASILAN ana
  agacta mi kendi worktree'sinde mi oldugunu bilmesi icin her seferinde git'e
  bakmasi gerekiyordu -- ve bakmadigi zaman kazalar oldu (bkz. oturum-ac.ps1).

  ILK SURUMUN EKSIGI: yalnizca worktree YOLLARINI listeliyordu. "Baska
  oturumlar var" demek, "su oturum su dosyada calisiyor" demek degil. Dort
  sekme ayni anda `types.ts`e dokunabiliyordu ve hicbiri otekini gormuyordu.
  Bu surum defteri (tools/oturum-ortak.ps1) okur ve su dordunu bildirir:

    1. Neredeyim, hangi dalda, hangi portta.
    2. Acik oturumlar: konu, dal, port, ne isle mesgul, kac commit geride.
    3. AYNI dosyaya birden fazla oturumun dokunmus olmasi.
    4. Birden fazla oturumda YENI MIGRATION olmasi (down_revision zinciri
       tek govdeli; iki dal ayni ataya baglanirsa merge'de zincir kirilir).

  CIKTI: `hookSpecificOutput.additionalContext`. Kullaniciya gosterilecek bir
  sey yok, o yuzden `suppressOutput`.

  SESSIZ BASARISIZLIK YOK, ENGEL DE YOK: git yoksa/depo degilse bos
  additionalContext doner. Bir hook oturum acilisini bloklamamali.
#>
$ErrorActionPreference = "Stop"

function JsonYaz([string]$baglam) {
  $cikti = @{
    suppressOutput     = $true
    hookSpecificOutput = @{
      hookEventName     = "SessionStart"
      additionalContext = $baglam
    }
  }
  $cikti | ConvertTo-Json -Depth 5 -Compress
}

# Girdi hook JSON'u: session_id lazim (defterdeki pencere kaydinin anahtari).
$oturumId = ""
try {
  $ham = [Console]::In.ReadToEnd()
  if (-not [string]::IsNullOrWhiteSpace($ham)) {
    $oturumId = [string]($ham | ConvertFrom-Json).session_id
  }
} catch { }

try {
  . (Join-Path $PSScriptRoot "oturum-ortak.ps1")
} catch {
  JsonYaz ""; exit 0
}

try {
  $kok = Get-AnaAgacKok
  if (-not $kok) { JsonYaz ""; exit 0 }

  $buKok = ConvertTo-WindowsYol ((Invoke-GitOku rev-parse --show-toplevel) -join "")
  $dal = ""
  $d = Invoke-GitOku rev-parse --abbrev-ref HEAD
  if ($d) { $dal = ($d -join "").Trim() }
  $worktreeDe = Test-WorktreeIcinde

  if ($oturumId) { Update-Pencere -Id $oturumId -Cwd $buKok }

  $oturumlar = Get-Oturumlar
  $pencereler = Get-Pencereler
  $harita = Get-KirliHarita -Tazele

  $satirlar = New-Object System.Collections.ArrayList
  function Ekle($s) { [void]$satirlar.Add($s) }

  # --- 1) Neredeyim ------------------------------------------------------
  $benim = $oturumlar | Where-Object { (ConvertTo-WindowsYol $_.yol) -eq $buKok } | Select-Object -First 1
  if ($worktreeDe) {
    Ekle "PARALEL OTURUM DURUMU: Kendi worktree'nizdesiniz."
    Ekle "  Dizin: $buKok"
    Ekle "  Dal  : $dal"
    if ($benim) { Ekle "  Port : frontend $($benim.frontendPort) / backend $($benim.backendPort)" }
    Ekle 'Bu agac size ait; genis git komutlari (add -A, commit -a) burada SERBEST.'
    Ekle "Bitince ana agactan: tools\oturum-kapat.ps1 -Konu <ad>"
  } else {
    Ekle "PARALEL OTURUM DURUMU: PAYLASILAN ANA AGACTASINIZ (dal: $dal)."
    Ekle "Bu depoda birden fazla oturum ayni anda calisiyor. Ayni agaci"
    Ekle "paylasmak veri kaybettirdi (bkz. CLAUDE.md > Paralel oturumlar):"
    Ekle "commit -a baska oturumun dosyalarini aldi, eski editor tamponu 246"
    Ekle "satirlik bir duzeltmeyi geri aldi, git reset iki commit'i dusurdu."
    Ekle ""
    Ekle "KURAL: kod yazacaksan ONCE kendi worktree'ni ac:"
    Ekle "  tools\oturum-ac.ps1 -Konu <kisa-ad> -VSCode"
    Ekle "(-VSCode worktree'yi AYRI bir pencerede acar. VSCode eklentisinde"
    Ekle " sekmeler ayni klasoru paylasir; editor ana agacta kalirsa acik"
    Ekle " tamponlar oraya kaydedilir -- 246 satirlik kayip tam boyle oldu.)"
    Ekle ""
    Ekle 'Ana agacta kalirsan: git add -A, git commit -a, git reset --hard'
    Ekle 'gibi GENIS komutlar hook ile ENGELLENIR (tools\oturum-koruma.ps1).'
    Ekle "Commit'lerini acik dosya yoluyla yap: git add <dosya1> <dosya2>"
  }

  # --- 1b) Bekleyen mesajlar ----------------------------------------------
  # Oturum kapaliyken gelen mesajlar posta kutusunda birikir; acilista
  # teslim edilmezse kullanici "gonderdim ama gormedi" der.
  $ben = Get-BuOturumAdi -Dizin $buKok
  $gelen = @(Receive-OturumMesajlari -Oturum $ben)
  if ($gelen.Count -gt 0) {
    Ekle ""
    Ekle "DIGER OTURUMLARDAN MESAJ ($($gelen.Count)) -- sen: $ben"
    foreach ($m in $gelen) {
      $kimden = $m.kimden
      if ($m.kime -eq "*") { $kimden = "$kimden (herkese)" }
      Ekle "  [$kimden] $($m.metin)"
    }
    Ekle "  Cevap: tools\oturum-mesaj.ps1 -Kime <oturum> -Mesaj `"...`""
  }

  # --- 2) Acik oturumlar --------------------------------------------------
  $digerAgac = @($harita | Where-Object { (ConvertTo-WindowsYol $_.yol) -ne $buKok })
  if ($digerAgac.Count -gt 0) {
    Ekle ""
    Ekle "ACIK OTURUMLAR (defter: .claude/oturumlar.json)"
    foreach ($a in $digerAgac) {
      $kayit = $oturumlar | Where-Object { (ConvertTo-WindowsYol $_.yol) -eq (ConvertTo-WindowsYol $a.yol) } | Select-Object -First 1
      $port = ""
      if ($kayit) { $port = "  port $($kayit.backendPort)" }

      $sapma = ""
      if ($a.ileride -gt 0 -or $a.geride -gt 0) { $sapma = "  [main'e gore +$($a.ileride)/-$($a.geride)]" }

      # Bu agacta acik bir Claude penceresi var mi, ne isle mesgul?
      $p = $pencereler | Where-Object { (ConvertTo-WindowsYol $_.cwd) -eq (ConvertTo-WindowsYol $a.yol) } | Select-Object -First 1
      $is = ""
      if ($p -and $p.baslik) { $is = "  is: $($p.baslik)" }

      Ekle "  * $($a.konu) [$($a.dal)]$port$sapma$is"
      if ($a.dosyalar.Count -gt 0) {
        Ekle "      commit'lenmemis: $($a.dosyalar.Count) dosya -> $((@($a.dosyalar) | Select-Object -First 4) -join ', ')"
      }
    }
    Ekle "  Uyari: '-N' geride demek. Cok geriden gelen dal merge'de en cok"
    Ekle "  kavga cikarandir: tools\oturum-birlestir.ps1 -Konu <ad> ile guncelle."
  }

  # --- 3) Ayni dosyaya dokunan birden fazla oturum ------------------------
  $sayac = @{}
  foreach ($a in $harita) {
    $hepsi = @($a.dosyalar) + @($a.dalDosyalari) | Sort-Object -Unique
    foreach ($f in $hepsi) {
      if (-not $f) { continue }
      if (-not $sayac.ContainsKey($f)) { $sayac[$f] = New-Object System.Collections.ArrayList }
      [void]$sayac[$f].Add($a.konu)
    }
  }
  $cakisan = @($sayac.Keys | Where-Object { $sayac[$_].Count -gt 1 } | Sort-Object)
  if ($cakisan.Count -gt 0) {
    Ekle ""
    Ekle "AYNI DOSYADA BIRDEN FAZLA OTURUM ($($cakisan.Count) dosya)"
    foreach ($f in ($cakisan | Select-Object -First 12)) {
      Ekle "  ! $f  <- $((@($sayac[$f]) | Sort-Object -Unique) -join ', ')"
    }
    if ($cakisan.Count -gt 12) { Ekle "  ... ve $($cakisan.Count - 12) dosya daha" }
    Ekle "  Bu dosyalara dokunmadan once karsi tarafin ne yaptigina bak."
  }

  # --- 4) Migration zinciri -----------------------------------------------
  # down_revision TEK govdeli bir zincir. Iki dal ayni atadan yeni bir surum
  # turetirse merge'den sonra iki bas olusur ve `alembic upgrade head` patlar.
  # Bunu merge aninda degil, ISE BASLARKEN gormek gerekir.
  $migrasyonlu = New-Object System.Collections.ArrayList
  foreach ($a in $harita) {
    $m = @(@($a.dosyalar) + @($a.dalDosyalari) | Where-Object { $_ -like "*alembic_migrations/versions/*" })
    if ($m.Count -gt 0) { [void]$migrasyonlu.Add("$($a.konu) ($($m.Count))") }
  }
  if ($migrasyonlu.Count -gt 1) {
    Ekle ""
    Ekle "MIGRATION CAKISMASI RISKI: $($migrasyonlu.Count) oturumda yeni migration var"
    Ekle "  $(($migrasyonlu) -join ' | ')"
    Ekle "  down_revision zinciri tek govdeli. Iki dal ayni ataya baglanirsa"
    Ekle "  merge sonrasi iki bas olusur ve 'alembic upgrade head' patlar."
    Ekle "  Sirayi konus: once biri main'e girsin, digeri uzerine rebase etsin."
    Ekle "  Ayrica dikkat: tum oturumlar AYNI Postgres'i (enerjione_grid)"
    Ekle "  kullaniyor; baska bir dalin migration'ini calistirmak herkesi etkiler."
  }

  JsonYaz (($satirlar) -join "`n")
} catch {
  JsonYaz ""
}
