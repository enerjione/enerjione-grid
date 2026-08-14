<#
.SYNOPSIS
  PreToolUse (Edit/Write) hook'u: PAYLASILAN ana agacta kod dosyasi
  duzenlemeyi ENGELLER. Is kendi worktree'sinde yapilir.

.DESCRIPTION
  NEDEN ENGEL, NEDEN UYARI DEGIL
  ------------------------------
  "Her is kendi worktree'sinde" kurali CLAUDE.md'de yaziyordu, SessionStart
  hook'u her acilista hatirlatiyordu, yine de ana agacta calisildi: 2026-08-12'de
  20 dakika icinde uc kayip. Hatirlatilan kural, hatirlamayi gerektirdigi
  surece bir kez unutulur. Bu hook unutmayi imkansiz kilar.

  KARAR DOSYA YOLUNA GORE VERILIR, oturumun calisma dizinine gore DEGIL.
  Sebep VSCode: eklentide sekmeler ayni klasoru paylasir, oturum worktree'ye
  gecse bile editorun acik tamponu ANA AGACA kayit yapar -- 246 satirlik kayip
  tam bu ayrimdan cikti. Yolun kendisine bakmak o kapiyi da kapatir.

  SERBEST KALANLAR
    * Kendi worktree'ndeki her dosya (yolda `.claude\worktrees\` gecer).
    * Depo disindaki her dosya (scratchpad, baska proje).
    * Oturum altyapisinin KENDISI: `tools\`, `.claude\`, `OTURUM.md`.
      Bu dosyalar bilerek muaf: hook'lar ve defter ANA AGACTAN okunur, bir
      worktree'de duzenlenirlerse merge edilene kadar hicbir etkileri olmaz --
      yani altyapiyi duzeltmek icin altyapiyi devre disi birakmak gerekirdi.

  ACIL DURUM KAPISI
    `$env:E1_ANA_AGAC_SERBEST = "1"` -> hook hicbir seyi engellemez. Tek
    oturumlu, paralel calismanin olmadigi bir makinede kurali kapatmanin yolu
    settings.json'i duzenlemek olmasin diye var.

  MALIYET: her Edit/Write'a bir PowerShell baslatma (~200 ms) biner. Bilerek
  kabul edildi -- bu hook'un onledigi tek kaza, omur boyu birikecek gecikmeden
  pahaliydi. Git CAGRILMAZ (yol karsilastirmasi saf metin islemidir); tek
  maliyet surecin kendisi.

  CIKTI: engellenecekse `permissionDecision: "deny"` + gerekce. Aksi halde
  cikti yok (izin ver).
#>
$ErrorActionPreference = "Stop"

function Cik-Sessiz { exit 0 }

if ($env:E1_ANA_AGAC_SERBEST -eq "1") { Cik-Sessiz }

# --- Girdi ---------------------------------------------------------------
$ham = [Console]::In.ReadToEnd()
if ([string]::IsNullOrWhiteSpace($ham)) { Cik-Sessiz }
try { $girdi = $ham | ConvertFrom-Json } catch { Cik-Sessiz }

$dosyaYolu = [string]$girdi.tool_input.file_path
if ([string]::IsNullOrWhiteSpace($dosyaYolu)) { Cik-Sessiz }

# --- Yol normalizasyonu ---------------------------------------------------
# Kod yollari hem `/` hem `\` ile gelebiliyor; karsilastirma oncesi tek bicime
# cevrilir. Buyuk/kucuk harf Windows'ta onemsiz -- karsilastirmalar
# OrdinalIgnoreCase.
function Duzelt([string]$y) {
  if ([string]::IsNullOrWhiteSpace($y)) { return "" }
  return ($y.Trim() -replace "/", "\").TrimEnd("\")
}

$hedef = Duzelt $dosyaYolu

# --- Worktree icindeki dosya: serbest ------------------------------------
# Worktree'ler ana agacin ICINDE (`<kok>\.claude\worktrees\<ad>`), yani ana
# kok prefiksi ikisiyle de eslesir. Bu yuzden worktree kontrolu ONCE gelir.
$AYRAC = "\.claude\worktrees\"
if ($hedef -like "*$AYRAC*") { Cik-Sessiz }

# --- Ana agac koku --------------------------------------------------------
# `git rev-parse` CAGRILMIYOR: bu hook her duzenlemede calisir ve git cagrisi
# maliyetin buyuk kismi olurdu. Script kendi yerini biliyor:
#   ana agacta   -> <kok>\tools
#   worktree'de  -> <kok>\.claude\worktrees\<ad>\tools
# Ikinci durumda ayractan onceki parca ana koku verir.
$kendiKok = Duzelt (Split-Path -Parent $PSScriptRoot)
$anaKok = $kendiKok
$i = $kendiKok.IndexOf($AYRAC, [System.StringComparison]::OrdinalIgnoreCase)
if ($i -ge 0) { $anaKok = $kendiKok.Substring(0, $i) }

if (-not $hedef.StartsWith($anaKok, [System.StringComparison]::OrdinalIgnoreCase)) {
  Cik-Sessiz   # Depo disi: bizi ilgilendirmez.
}

$gorele = $hedef.Substring($anaKok.Length).TrimStart("\")
if ([string]::IsNullOrWhiteSpace($gorele)) { Cik-Sessiz }

# --- Altyapi muafiyeti ----------------------------------------------------
$muaf = @("tools\", ".claude\")
foreach ($m in $muaf) {
  if ($gorele.StartsWith($m, [System.StringComparison]::OrdinalIgnoreCase)) { Cik-Sessiz }
}
if ($gorele -eq "OTURUM.md") { Cik-Sessiz }

# --- Engelle --------------------------------------------------------------
# Oturum adi onerisi: dosyanin bulundugu alandan uretilir. Modelin "-Konu ne
# yazayim" diye durmasi yerine calistirabilecegi bir komut versin.
$oneri = "degisiklik"
$parcalar = $gorele -split "\\"
if ($parcalar.Count -ge 2 -and $parcalar[0] -eq "apps") { $oneri = $parcalar[1] -replace "^(backend-api|frontend-web)$", "kod" }
elseif ($parcalar.Count -ge 1) { $oneri = ($parcalar[0] -replace "\.[^.]+$", "") }
if ([string]::IsNullOrWhiteSpace($oneri)) { $oneri = "degisiklik" }

$sebep = @(
  "ENGELLENDI: $gorele PAYLASILAN ana agacta.",
  "",
  "Bu depoda birden fazla oturum ayni anda calisiyor. Ana agactaki bir",
  "duzenleme baska oturumlarin isine karisir; 2026-08-12'de tam boyle bir",
  "kayit baska bir oturumun 246 satirlik duzeltmesini geri aldi.",
  "",
  "YAPILACAK -- once kendi worktree'ni ac, sonra duzenlemeyi orada yap:",
  "",
  "    tools\oturum-ac.ps1 -Konu $oneri -VSCode -Aciklama `"<isin bir cumlelik tarifi>`"",
  "",
  "-VSCode ayri bir pencere acar; editor ile oturum ayni dizinde bulusur.",
  "Acilan pencerede Claude'u baslat ve ise ORADA devam et.",
  "Isin bitince: tools\oturum-teslim.ps1  (dogrular, main'e alir)",
  "",
  "Serbest olanlar: kendi worktree'n, depo disi dosyalar, tools\ ve .claude\.",
  "Paralel calismanin olmadigi bir makinedeysen: `$env:E1_ANA_AGAC_SERBEST=`"1`""
) -join "`n"

@{
  hookSpecificOutput = @{
    hookEventName            = "PreToolUse"
    permissionDecision       = "deny"
    permissionDecisionReason = $sebep
  }
} | ConvertTo-Json -Depth 5 -Compress
exit 0
