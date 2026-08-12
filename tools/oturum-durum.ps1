<#
.SYNOPSIS
  SessionStart hook'u: oturumun NEREDE oldugunu modele bildirir.

.DESCRIPTION
  NEDEN HOOK
  ----------
  Paralel oturum kurali CLAUDE.md'de yaziyor ama bir oturumun PAYLASILAN ana
  agacta mi kendi worktree'sinde mi oldugunu bilmesi icin her seferinde git'e
  bakmasi gerekiyordu — ve bakmadigi zaman kazalar oldu (bkz. oturum-ac.ps1).
  Bu hook cevabi oturum acilirken BAGLAMA koyar; kimsenin hatirlamasi gerekmez.

  CIKTI: `hookSpecificOutput.additionalContext` ile model baglamina giren tek
  bir JSON. Kullaniciya gosterilecek bir sey yok, o yuzden `suppressOutput`.

  SESSIZ BASARISIZLIK YOK: git yoksa ya da depo degilse bos additionalContext
  doner (hook oturum acilisini bloklamamali).
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

try {
  $gitDir = (git rev-parse --git-dir 2>$null)
  if (-not $gitDir) { JsonYaz ""; exit 0 }
  $gitDir = $gitDir.Trim()
  $kok = (git rev-parse --show-toplevel).Trim() -replace "/", "\"
  $dal = (git rev-parse --abbrev-ref HEAD 2>$null)
  if ($dal) { $dal = $dal.Trim() }

  # Acik oturumlar: her worktree bir is. Model kiminle ayni depoda oldugunu
  # bilsin ki ortak dosyalara dokunurken tedbirli olsun.
  $digerleri = @(
    (git worktree list --porcelain) -match '^worktree ' | ForEach-Object {
      ($_ -replace '^worktree ', '').Trim() -replace '/', '\'
    }
  ) | Where-Object { $_ -ne $kok }

  if ($gitDir -match "worktrees") {
    $satirlar = @(
      "PARALEL OTURUM DURUMU: Kendi worktree'nizdesiniz.",
      "  Dizin: $kok",
      "  Dal  : $dal",
      'Bu agac size ait; genis git komutlari (add -A, commit -a) burada SERBEST.',
      "Bitince ana agactan: tools\oturum-kapat.ps1 -Konu <ad>"
    )
  } else {
    $satirlar = @(
      "PARALEL OTURUM DURUMU: PAYLASILAN ANA AGACTASINIZ (dal: $dal).",
      "Bu depoda birden fazla oturum ayni anda calisiyor. Ayni agaci",
      "paylasmak veri kaybettirdi (bkz. CLAUDE.md > Paralel oturumlar):",
      "commit -a baska oturumun dosyalarini aldi, eski editor tamponu 246",
      "satirlik bir duzeltmeyi geri aldi, git reset iki commit'i dusurdu.",
      "",
      "KURAL: kod yazacaksan ONCE kendi worktree'ni ac:",
      "  tools\oturum-ac.ps1 -Konu <kisa-ad>",
      "sonra o dizine gec (EnterWorktree path: <cikan yol>).",
      "",
      'Ana agacta kalirsan: git add -A, git commit -a, git reset --hard',
      'gibi GENIS komutlar hook ile ENGELLENIR (tools\oturum-koruma.ps1).',
      "Commit'lerini acik dosya yoluyla yap: git add <dosya1> <dosya2>"
    )
    if ($digerleri.Count -gt 0) {
      $satirlar += ""
      $satirlar += "Su an acik olan diger oturum worktree'leri:"
      foreach ($d in $digerleri) { $satirlar += "  $d" }
    }
  }
  JsonYaz ($satirlar -join "`n")
} catch {
  JsonYaz ""
}
