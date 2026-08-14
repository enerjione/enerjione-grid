<#
.SYNOPSIS
  `tools/oturum-koruma.ps1` hook'unun davranis testi.

.DESCRIPTION
  NEDEN DOSYADA: yasakli komut METINLERI burada durur. Bash komut satirina
  yazilirsa hook'un kendisi test komutunu engelliyor — gelistirme sirasinda
  iki kez yasandi, cunku hook komutu kabuk ayiraclarindan parcalayip her
  parcanin basina bakiyor ve test yuku de bir "parca" oluyor.

  NE KILITLENIYOR:
    * Genis komutlar ANA AGACTA engellenir (kaza mekanizmalari).
    * Dar komutlar (acik dosya yolu, -m ile commit, status) SERBEST.
    * Metin icinde gecen kalip YANLIS ENGELLENMEZ. Yanlis engelleme
      korumaya olan guveni goturur; kullanici ilk isten sonra kapatir.

.EXAMPLE
  .\tools\oturum-koruma-test.ps1
#>
$ErrorActionPreference = "Stop"

$kok = (git rev-parse --show-toplevel).Trim() -replace "/", "\"
$script = Join-Path $kok "tools\oturum-koruma.ps1"
if (-not (Test-Path $script)) { throw "Hook scripti bulunamadi: $script" }

# Hook yalnizca ANA AGACTA engeller; worktree icinde her sey serbest oldugu
# icin testin ana agactan kosmasi gerekiyor.
if ((git rev-parse --git-dir).Trim() -match "worktrees") {
  Write-Host "Bu test ANA AGACTAN kosulmali (worktree'de hook zaten serbest)." -ForegroundColor Yellow
  exit 1
}

$durumlar = @(
  @{ Ad = "git add -A";                      Komut = "git add -A";                       Beklenen = "ENGEL" },
  @{ Ad = "git add .";                       Komut = "git add .";                        Beklenen = "ENGEL" },
  @{ Ad = "zincirde commit -am";             Komut = "cd x && git commit -am hop";       Beklenen = "ENGEL" },
  @{ Ad = "git reset --hard";                Komut = "git reset --hard HEAD~1";          Beklenen = "ENGEL" },
  @{ Ad = "git clean -fd";                   Komut = "git clean -fd";                    Beklenen = "ENGEL" },
  @{ Ad = "git checkout -- .";               Komut = "git checkout -- .";                Beklenen = "ENGEL" },
  @{ Ad = "git stash";                       Komut = "git stash";                        Beklenen = "ENGEL" },
  @{ Ad = "git add <dosya>";                 Komut = "git add tools/x.ps1";              Beklenen = "IZIN" },
  @{ Ad = "git commit -m";                   Komut = "git commit -m 'mesaj'";            Beklenen = "IZIN" },
  @{ Ad = "git status";                      Komut = "git status";                       Beklenen = "IZIN" },
  @{ Ad = "git stash list";                  Komut = "git stash list";                   Beklenen = "IZIN" },
  @{ Ad = "git stash show (salt-okunur)";    Komut = "git stash show -p stash@{0}";      Beklenen = "IZIN" },
  @{ Ad = "git stash pop";                   Komut = "git stash pop";                    Beklenen = "ENGEL" },
  @{ Ad = "git stash drop";                  Komut = "git stash drop stash@{1}";         Beklenen = "ENGEL" },
  @{ Ad = "metinde gecen kalip (echo)";      Komut = "echo 'git add -A yapmayin'";       Beklenen = "IZIN" },
  @{ Ad = "metinde gecen kalip (grep)";      Komut = "grep -r 'git reset --hard' docs";  Beklenen = "IZIN" }
)

$hata = 0
foreach ($d in $durumlar) {
  $yuk = @{ tool_name = "Bash"; tool_input = @{ command = $d.Komut } } | ConvertTo-Json -Compress
  $cikti = $yuk | & powershell -NoProfile -ExecutionPolicy Bypass -File $script
  $sonuc = if ([string]::IsNullOrWhiteSpace($cikti)) { "IZIN" } else { "ENGEL" }
  if ($sonuc -eq $d.Beklenen) {
    Write-Host ("OK    {0,-28} {1}" -f $d.Ad, $sonuc)
  } else {
    Write-Host ("HATA  {0,-28} beklenen={1} sonuc={2}" -f $d.Ad, $d.Beklenen, $sonuc) -ForegroundColor Red
    $hata++
  }
}

Write-Host ""
if ($hata -eq 0) {
  Write-Host "TUM DURUMLAR GECTI ($($durumlar.Count) durum)" -ForegroundColor Green
} else {
  Write-Host "$hata durum BASARISIZ" -ForegroundColor Red
  exit 1
}
