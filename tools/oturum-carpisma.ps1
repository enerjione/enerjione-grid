<#
.SYNOPSIS
  PreToolUse (Edit/Write) hook'u: PAYLASIMLI bir dosyaya dokunulurken baska
  oturumlarin ayni dosyadaki isini bildirir.

.DESCRIPTION
  NEDEN
  -----
  Worktree izolasyonu dosya EZILMESINI cozdu, CARPISMAYI cozmedi. Dort oturum
  ayni `types.ts`i ayri agaclarda mutlu mesut duzenleyebilir; hepsi yesil,
  hepsi test geciyor. Fatura merge'de kesiliyor -- ve CLAUDE.md'nin kendi
  ifadesiyle "carpismalarin hepsi bu dosyalarda oldu": types.ts, App.tsx,
  i18n, styles.css, models/, alembic versions/.

  Bu hook o dosyalara dokunuldugu ANDA "senden once iki kisi daha var" der.
  Engellemez -- karar modelin/kullanicinin: ya once onlarin isini bekler, ya
  degisikligi kucuk tutar, ya da bilerek devam eder.

  KAPSAM: settings.json'daki `if` kurallari hook'u yalnizca paylasimli
  yollarda calistirir. Her Edit'e PowerShell baslatma maliyeti bindirmenin
  karsiligi yoktu (ayni gerekce: oturum-koruma.ps1).

  IKI TUR CARPISMA RAPORLANIR:
    1. Commit'lenmemis  -- karsi agacta su an duzenleniyor.
    2. Dalda commit'li  -- karsi dal main'e girmemis bir degisiklik tasiyor.
  Ikincisi daha sinsi: karsi agac `git status`ta TERTEMIZ gorunur.

  CIKTI: yalnizca `additionalContext`. Bilerek `permissionDecision`
  YAZILMIYOR -- "allow" demek kullanicinin kendi izin kurallarini es gecmek
  olurdu; bu hook'un isi bilgilendirmek, yetki dagitmak degil.
#>

$ErrorActionPreference = "Stop"

function Cik-Sessiz { exit 0 }

# --- Girdi ---------------------------------------------------------------
$ham = [Console]::In.ReadToEnd()
if ([string]::IsNullOrWhiteSpace($ham)) { Cik-Sessiz }
try { $girdi = $ham | ConvertFrom-Json } catch { Cik-Sessiz }

$dosyaYolu = [string]$girdi.tool_input.file_path
if ([string]::IsNullOrWhiteSpace($dosyaYolu)) { Cik-Sessiz }

try {
  . (Join-Path $PSScriptRoot "oturum-ortak.ps1")
} catch {
  Cik-Sessiz   # Kitaplik yuklenemiyorsa aracin onunu kesmeyiz.
}

try {
  $gorele = Get-DepoGoreliYol -Yol $dosyaYolu
  if (-not $gorele) { Cik-Sessiz }

  # Kendi agacimiz: bizim degisikligimiz carpisma degil.
  $bizimKok = Invoke-GitOku rev-parse --show-toplevel
  if (-not $bizimKok) { Cik-Sessiz }
  $bizimKok = ConvertTo-WindowsYol ($bizimKok -join "")

  $harita = Get-KirliHarita
  $carpisan = New-Object System.Collections.ArrayList

  foreach ($agac in $harita) {
    if ((ConvertTo-WindowsYol $agac.yol) -eq $bizimKok) { continue }

    $kirliMi = (@($agac.dosyalar) -contains $gorele)
    $daldaMi = (@($agac.dalDosyalari) -contains $gorele)
    if (-not $kirliMi -and -not $daldaMi) { continue }

    $ad = $agac.konu
    if ($agac.dal) { $ad = "$ad ($($agac.dal))" }
    if ($kirliMi -and $daldaMi) {
      [void]$carpisan.Add("  - $ad : hem dalinda commit'li hem SU AN duzenliyor")
    } elseif ($kirliMi) {
      [void]$carpisan.Add("  - $ad : SU AN duzenliyor (commit'lenmemis)")
    } else {
      [void]$carpisan.Add("  - $ad : dalinda commit'lemis, main'e girmemis")
    }
  }

  if ($carpisan.Count -eq 0) { Cik-Sessiz }

  $satirlar = @(
    "CARPISMA UYARISI -- $gorele",
    "",
    "Bu dosyada baska oturum(lar) da calisiyor:"
  ) + @($carpisan) + @(
    "",
    "Bu bir engel degil, bilgi. Yapabileceklerin:",
    "  - Degisikligi DAR tut: ayni dosyada genis yeniden duzenlemeden kacin,",
    "    boylece merge cakismasi satir seviyesinde kalir.",
    "  - Once ilerlemek gerekiyorsa: tools\oturum-birlestir.ps1 -Konu <ad> ile",
    "    karsi tarafin main'e girmis isini alip ustune yaz.",
    "  - Isin gercekten ayni yere dokunuyorsa kullaniciya sor: iki oturumdan",
    "    hangisi bu dosyanin sahibi olsun?"
  )

  @{
    hookSpecificOutput = @{
      hookEventName     = "PreToolUse"
      additionalContext = ($satirlar -join "`n")
    }
  } | ConvertTo-Json -Depth 5 -Compress
  exit 0
} catch {
  Cik-Sessiz
}
