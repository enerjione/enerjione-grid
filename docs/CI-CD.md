# CI/CD ve Yayın Süreci

## Genel akış

```
  feat/xyz dalı
      │  PR
      ▼
  ┌─────────────────────────────────────────────┐
  │ CI (.github/workflows/ci.yml)               │
  │  frontend build · backend ruff+pytest       │
  │  alembic tek-head · shellcheck · compose    │
  │  sürüm tutarlılığı                          │
  └─────────────────────────────────────────────┘
      │  merge (CI yeşilse)
      ▼
    main ────────────────────────────────────────►
      │
      │  git tag v2.25.0 && git push origin v2.25.0
      ▼
  ┌─────────────────────────────────────────────┐
  │ Release (.github/workflows/release.yml)     │
  │  1. doğrula: tag == VERSION == package.json │
  │  2. 9 servis imajı → ghcr.io                │
  │  3. GitHub Release + notlar                 │
  │  4. VDS'e otomatik deploy (ssh)             │
  └─────────────────────────────────────────────┘
      │
      ├──► VDS      : otomatik
      └──► Saha PC  : sudo bash update.sh (istenildiğinde)
```

Ayrı bir "deploy dalı" yoktur. Deploy **tag**'den tetiklenir; `main`'e giren
her commit sahaya gitmez.

## İmajlar nerede üretilir

**CI'da, cihazda değil.** Saha PC'si `docker compose pull` ile hazır imajı
indirir. Kazanç:

- Kurulum 3–8 dakikalık derlemeden ~1 dakikalık indirmeye iner
- Her cihazda **bit-bit aynı imaj** çalışır; "bu makinede derlenmiyor" sınıfı
  hata ortadan kalkar
- Mini PC'nin CPU'su ve diski derleme yükü altına girmez

İmaj adresi: `ghcr.io/enerjione/enerjione-grid/<servis>:<sürüm>`

Etiketler: `2.25.0` (tam sürüm), `2.25` (minor), `latest`.
**Üretimde her zaman tam sürüm kullanılır** — `.env`'deki `E1_VERSION`'ı
`update.sh` checkout edilen sürümden otomatik yazar.

İnternet kısıtlı veya token yoksa kurulum **otomatik olarak yerel derlemeye
düşer**; `--build` ile de zorlanabilir.

## Yayın çıkarma

```bash
# 1. Sürümü artır (iki dosya birlikte — CI aksini reddeder)
echo "2.25.0" > VERSION
npm --prefix apps/frontend-web version 2.25.0 --no-git-tag-version

# 2. CHANGELOG.md'de [Yayınlanmamış] başlığını yeni sürüme taşı

# 3. Commit + PR + merge
git add VERSION apps/frontend-web/package.json CHANGELOG.md
git commit -m "chore(release): 2.25.0"

# 4. main'de tag at
git tag v2.25.0
git push origin v2.25.0
```

Gerisi otomatik. İlerlemeyi GitHub → Actions sekmesinden izleyin.

## Geri alma (rollback)

Sorunlu bir sürüm çıktıysa yeni sürüm beklemeye gerek yok — önceki imaj
zaten GHCR'da ve büyük ihtimalle cihazın diskinde:

```bash
cd /opt/enerjione-grid
sudo bash update.sh --version 2.24.4
```

Bu komut hem kodu hem imajı o sürüme geri alır. **Uyarı:** araya bir DB
migration girdiyse geri alma şemayı geri almaz; migration'ların geriye dönük
uyumlu olması bu yüzden önemlidir.

## Gerekli GitHub ayarları

Repo → Settings altında bir kez yapılır.

### Secrets (Settings → Secrets and variables → Actions)

| Ad | Zorunlu | Ne için |
|---|---|---|
| `VDS_HOST` | deploy için | VDS IP veya alan adı |
| `VDS_USER` | deploy için | SSH kullanıcısı (parolasız sudo yetkili) |
| `VDS_SSH_KEY` | deploy için | Deploy anahtarının **özel** kısmı |
| `VDS_HOST_KEY` | önerilir | `ssh-keyscan <VDS_IP>` çıktısı — MITM'e karşı |

`GITHUB_TOKEN` otomatik verilir; GHCR'a basmak için ayrı token gerekmez.

Secret'lardan biri yoksa deploy adımı **hata vermez, atlar** — imaj yine basılır.

### Variables

| Ad | Varsayılan |
|---|---|
| `VDS_PATH` | `/opt/enerjione-grid` |

### Environment

`production` adında bir environment oluşturun. İsterseniz "Required reviewers"
ekleyin — o zaman VDS deploy'u bir kişi onaylamadan çalışmaz.

### Branch protection (`main`)

- Require a pull request before merging
- Require status checks to pass: `Frontend — typecheck + build`,
  `Backend — ruff + pytest`, `Alembic — tek head kontrolu`,
  `Kurulum scriptleri — syntax + shellcheck`, `docker-compose dogrulama`,
  `Surum tutarliligi`
- Require branches to be up to date before merging
- Require review from Code Owners (`.github/CODEOWNERS`)
- Do not allow bypassing the above settings

## Saha cihazının GHCR erişimi

Depo private olduğu için imajlar da private. Cihaz salt-okunur bir token ile
`ghcr.io`'ya giriş yapar.

Token üretimi: GitHub → Settings → Developer settings → Personal access tokens
→ **Fine-grained** → yalnızca `enerjione/enerjione-grid` deposu, izin:
**Packages: Read-only**. Depoya yazma yetkisi olan bir token asla sahaya
dağıtılmaz.

Kuruluma verme yolları:

```bash
# 1. Kurulum sırasında sorulur (hiçbir şey yapmanıza gerek yok)

# 2. Ortam değişkeni ile (otomatik kurulum)
curl -fsSL https://raw.githubusercontent.com/enerjione/enerjione-grid/main/install.sh \
  | sudo E1_GHCR_TOKEN=ghp_xxx ASSUME_YES=1 bash

# 3. Sonradan .env'e
GHCR_TOKEN=ghp_xxx
GHCR_USERNAME=x-access-token
```

Token verilmezse kurulum imajları cihazda derler — çalışır, sadece yavaştır.

## CI kapıları ne yakalar

| İş | Yakaladığı hata |
|---|---|
| Frontend build | TS tip hatası, kırık import, derlenmeyen JSX |
| Backend ruff | Tanımsız isim, syntax hatası, anlamsız karşılaştırma |
| Backend pytest | Servis/encoder/repository regresyonları |
| Alembic tek head | İki dalın paralel migration üretip zinciri çatallaması |
| shellcheck | `install.sh`/`update.sh`'te sahayı kilitleyecek kabuk hatası |
| compose config | `.env.example`'da eksik zorunlu değişken, bozuk YAML |
| Sürüm tutarlılığı | `VERSION` ile `package.json`'ın ayrışması |

Ruff bilerek **dar** bir kural setiyle çalışır (`ruff.toml`): amaç stil polisi
olmak değil, çalışma zamanında patlayacak şeyleri yakalamak. Kural eklemek
kolaydır, ama önce ilgili dizini temizleyip sonra açın.
