<#
.SYNOPSIS
  WorktreeCreate / WorktreeRemove hook adaptoru: yerlesik worktree akisini
  bu projenin kurulumundan gecirir.

.DESCRIPTION
  NEDEN
  -----
  Worktree acmanin IKI yolu var ve ikisi ayni sonucu vermiyordu:

    1. tools\oturum-ac.ps1  -> .env kopyalanir, VITE_API_BASE_URL yazilir,
                               node_modules baglanir, port ayrilir, OTURUM.md
                               olusur, defter guncellenir.
    2. Yerlesik akis (EnterWorktree / `claude --worktree`)
                            -> yalin `git worktree add`. Baska hicbir sey yok.

  Ikinci yoldan acilan bir oturum calismaz: backend .env olmadan hic acilmaz,
  frontend de 5173 disinda bir portta backend'i bulamaz. Depoda bunun canli
  ornegi vardi -- `ariza-bolgesi-tek-kural` worktree'sinde ne .env ne OTURUM.md
  ne de atanmis bir port vardi.

  WorktreeCreate hook'u bu ayrimi kaldirir: yerlesik akis da oturum-ac.ps1'i
  cagirir. Artik hangi yoldan acilirsa acilsin kurulum ayni.

  SOZLESME (Claude Code, WorktreeCreate):
    - Girdi (stdin JSON): worktree_path -- olusturulacagi YER.
    - Cikti (stdout): worktree'nin yolu, BASKA HICBIR SEY. Bu yuzden
      oturum-ac.ps1 -Sessiz ile cagrilir (anlatim stderr'e gider).
    - Sifir disi cikis kodu worktree olusturmayi IPTAL eder.

.PARAMETER Olay
  `create` ya da `remove`.
#>
[CmdletBinding()]
param(
  [Parameter(Mandatory = $true)][ValidateSet("create", "remove")][string]$Olay
)

$ErrorActionPreference = "Stop"

try {
  $ham = [Console]::In.ReadToEnd()
  $girdi = $null
  if (-not [string]::IsNullOrWhiteSpace($ham)) { $girdi = $ham | ConvertFrom-Json }
} catch {
  [Console]::Error.WriteLine("Hook girdisi cozulemedi.")
  exit 1
}

$yol = ""
if ($girdi) { $yol = [string]$girdi.worktree_path }

if ($Olay -eq "remove") {
  # Silme: yalnizca defteri temizle. Dizini silmek bu hook'un isi degil --
  # node_modules junction'i yuzunden guvenli silme oturum-kapat.ps1'de.
  try {
    . (Join-Path $PSScriptRoot "oturum-ortak.ps1")
    if ($yol) { Remove-Oturum -Yol $yol }
  } catch { }
  exit 0
}

# --- create ---------------------------------------------------------------
if ([string]::IsNullOrWhiteSpace($yol)) {
  [Console]::Error.WriteLine("WorktreeCreate: worktree_path bos geldi.")
  exit 1
}

$konu = Split-Path -Leaf $yol

# Konu adi oturum-ac.ps1'in kaliplarina uymayabilir (yerlesik akis rastgele ad
# uretebiliyor). Uymuyorsa temizle; tamamen bosalirsa reddetmek yerine
# anlasilir bir ad uret -- worktree acilisini iptal etmek kullaniciya pahaliya
# mal olur, kotu bir dizin adindan cok daha pahaliya.
$temiz = ($konu.ToLowerInvariant() -replace '[^a-z0-9\-_]', '-').Trim('-')
if ($temiz.Length -gt 31) { $temiz = $temiz.Substring(0, 31).Trim('-') }
if ([string]::IsNullOrWhiteSpace($temiz)) { $temiz = "oturum" }

$acici = Join-Path $PSScriptRoot "oturum-ac.ps1"

# Hedef dizin ZATEN varsa (yerlesik akis onceden olusturmus olabilir)
# oturum-ac.ps1 hata verir. O durumda kurulumu var olan dizine uygulariz.
if (Test-Path $yol) {
  try {
    . (Join-Path $PSScriptRoot "oturum-ortak.ps1")
    $kayit = Add-Oturum -Konu $temiz -Yol $yol
    if ($kayit) { Copy-OturumKurulumu -Hedef $yol -Kayit $kayit }
  } catch {
    [Console]::Error.WriteLine("Var olan worktree'ye kurulum uygulanamadi: $($_.Exception.Message)")
  }
  [Console]::Out.WriteLine((Resolve-Path $yol).Path)
  exit 0
}

& powershell -NoProfile -ExecutionPolicy Bypass -File $acici -Konu $temiz -Sessiz
if ($LASTEXITCODE -ne 0) {
  [Console]::Error.WriteLine("oturum-ac.ps1 basarisiz (kod $LASTEXITCODE); worktree acilmadi.")
  exit $LASTEXITCODE
}
exit 0
