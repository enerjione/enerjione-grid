<#
.SYNOPSIS
  PreToolUse (Bash) hook'u: ANA AGACTA genis git komutlarini engeller.

.DESCRIPTION
  NEDEN
  -----
  Paralel oturumlarda veri kaybinin MEKANIZMASI belliydi: bir oturum
  `git commit -a` / `git add -A` yaptiginda baska bir oturumun o an
  duzenledigi dosyalari da commit'liyor; `git reset --hard` / `checkout -- .`
  ise baskasinin calismasini geri aliyor. Kural CLAUDE.md'de yaziyordu ama
  kural hatirlanmak zorundadir — hook hatirlamak zorunda degildir.

  KAPSAM: yalnizca PAYLASILAN ana agac. Kendi worktree'sinde her sey serbest;
  orada `-A` ile commit etmek zaten guvenli (agac size ait) ve akisi
  yavaslatmanin karsiligi yok.

  Hook `if: "Bash(git *)"` ile filtreli cagrilir; git disi komutlarda hic
  calismaz (her Bash cagrisina 300 ms eklemek kabul edilemezdi).

  CIKTI: engellenecekse `permissionDecision: "deny"` + sebep. Aksi halde hic
  cikti yok (izin ver).
#>
$ErrorActionPreference = "Stop"

# --- Girdi: stdin'de hook JSON'u -----------------------------------------
$ham = [Console]::In.ReadToEnd()
if ([string]::IsNullOrWhiteSpace($ham)) { exit 0 }
try {
  $girdi = $ham | ConvertFrom-Json
} catch {
  exit 0   # Anlamadigimiz girdi yuzunden aracin onunu KESMEYIZ.
}
$komut = [string]$girdi.tool_input.command
if ([string]::IsNullOrWhiteSpace($komut)) { exit 0 }

# --- Kendi worktree'sinde miyiz? -----------------------------------------
try {
  $gitDir = (git rev-parse --git-dir 2>$null)
} catch {
  exit 0
}
if (-not $gitDir) { exit 0 }
if ($gitDir.Trim() -match "worktrees") { exit 0 }   # kendi agacin: serbest

# --- Yasakli kaliplar ----------------------------------------------------
# Her kalip GERCEKTEN yasanmis bir kazanin mekanizmasi. Aciklamalar deny
# gerekcesinde kullaniciya/modele aynen gider.
$kaliplar = @(
  @{ Desen = '^git\s+add\s+(-A|--all|\.(\s|$))';                   Ad = 'git add -A / git add .' },
  @{ Desen = '^git\s+commit\s+(-[a-zA-Z]*a|--all)';                Ad = 'git commit -a' },
  @{ Desen = '^git\s+reset\s+--hard';                              Ad = 'git reset --hard' },
  @{ Desen = '^git\s+checkout\s+--\s+\.';                          Ad = 'git checkout -- .' },
  @{ Desen = '^git\s+restore\s+(--\s+)?\.(\s|$)';                  Ad = 'git restore .' },
  @{ Desen = '^git\s+clean\s+-[a-zA-Z]*f';                         Ad = 'git clean -f' },
  # `list` ve `show` SALT-OKUNUR: stash'in ne oldugunu gormek icin kullanilir
  # ve hicbir seyi degistirmez. Ilk surum yalnizca `list`i muaf tutuyordu;
  # `git stash show` engellenince "hangi is nerede kaldi" sorusuna bakan
  # komutun onu kesilmis oluyordu. Yanlis engelleme korumaya olan guveni
  # goturur -- kullanici ilk isten sonra hook'u kapatir.
  @{ Desen = '^git\s+stash(\s|$)(?!\s*(list|show)\b)';             Ad = 'git stash' }
)

# YALNIZCA KOMUT KONUMUNDAKI git cagrilari.
#
# Ham metinde arama YAPILMAZ: `echo "git add -A yapmayin"` ya da bu kalibi
# iceren bir grep/heredoc da engelleniyordu (ilk surumde tam bu yasandi —
# hook kendi test komutunu blokladi). Yanlis engelleme korumaya olan guveni
# goturur; kullanici ilk isten sonra hook'u kapatir.
#
# Bu yuzden komut kabuk ayiraclarindan (&& || ; | yenisatir) parcalanir ve
# her parcanin BASI test edilir. `x && git add -A` yakalanir; metin icinde
# gecen ayni dizi yakalanmaz.
$parcalar = [regex]::Split($komut, '(\|\||&&|;|\||\r?\n)') |
  ForEach-Object { $_.Trim() } |
  Where-Object { $_ -ne "" }

foreach ($k in $kaliplar) {
  $eslesen = $parcalar | Where-Object { $_ -match $k.Desen }
  if (-not $eslesen) { continue }
  $sebep = @(
    "ENGELLENDI: '$($k.Ad)' PAYLASILAN ana agacta kullanilamaz.",
    "",
    "Bu depoda birden fazla oturum ayni anda calisiyor ve bu komut, o an",
    "baska bir oturumun duzenledigi dosyalari da kapsar. 2026-08-12'de tam",
    "boyle bir commit baska bir oturumun 246 satirlik duzeltmesini geri aldi.",
    "",
    "Yapilacak:",
    "  - Commit edeceksen ACIK DOSYA YOLU ver: git add <dosya1> <dosya2>",
    "  - Genis komutlara ihtiyacin varsa kendi worktree'nde calis:",
    "      tools\oturum-ac.ps1 -Konu <kisa-ad>",
    "    (orada bu komutlarin hepsi serbest)",
    "  - Gercekten ana agacta ve bilerek yapmak istiyorsan komutu kullaniciya",
    "    sor; onay verirse hook'u gecici olarak /hooks ekranindan kapatabilir."
  ) -join "`n"

  @{
    hookSpecificOutput = @{
      hookEventName            = "PreToolUse"
      permissionDecision       = "deny"
      permissionDecisionReason = $sebep
    }
  } | ConvertTo-Json -Depth 5 -Compress
  exit 0
}

exit 0
