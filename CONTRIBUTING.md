# Katkı Rehberi

## Dal modeli

Tek gövde: **`main`**. Her an release edilebilir durumda tutulur.

```
main ──●──●──●──●──●──●──►
        \        /   │
         feat/xyz    └─ v2.25.0 (tag) ──► release CI ──► GHCR + VDS
```

- İş dalı: `feat/<kısa-ad>`, `fix/<kısa-ad>`, `chore/<kısa-ad>`
- `main`'e **doğrudan push yok** — PR ile girilir, CI yeşil olmalı
- Ayrı bir "deploy dalı" **yoktur**. Deploy tag'den tetiklenir.

## Commit mesajı

Conventional Commits, açıklama Türkçe:

```
feat(alarm): coklu ariza bolgesi hesabi
fix(install): rabbitmq hazir olmadan kurulum kesilmesin
chore(ci): shellcheck kapisi eklendi
```

Scope, etkilenen alandır: `backend`, `frontend`, `alarm`, `iec104`, `install`,
`ci`, `dashboard`…

Kod yorumları ASCII kullanır (`ariza`, `kullanici`); kullanıcıya görünen
metinler Türkçedir ve `t(...)` üzerinden geçer.

## Değişiklik yapmadan önce

`CLAUDE.md` proje kılavuzudur — katman akışı, migration kuralı, scope/audit
gereklilikleri orada. Özet:

1. Model'e dokunduysan **migration üret** (`alembic revision --autogenerate`)
2. Şemayı backend ↔ frontend **birlikte** güncelle
3. Kalıcı state değişimi → `record_event(...)`
4. Değişikliği **gerçekten çalıştır**, sadece derleme/test yeterli değil
5. En küçük diff

## Yerel doğrulama

```bash
# Backend
cd apps/backend-api
pip install -r requirements.txt -r requirements-dev.txt
ruff check . && pytest -q

# Frontend
cd apps/frontend-web
npm ci && npm run build

# Kurulum scriptleri
bash -n install.sh update.sh uninstall.sh infra/scripts/linux/_lib.sh

# Compose
cp .env.example .env && docker compose config --quiet
```

CI aynılarını çalıştırır; yerelde geçen PR'da da geçer.

## Bağımlılık ekleme

- **Python (runtime):** `requirements.in` düzenle → `pip-compile --generate-hashes`
  ile `requirements.txt` üret. `requirements.txt` **elle düzenlenmez**.
- **Python (sadece geliştirme/CI):** `requirements-dev.txt`.
- **Node:** `npm install <paket>` → `package-lock.json` da commit edilir.

Yeni bağımlılık eklemeden önce mevcut stdlib/bağımlılıkla çözülüp
çözülmediğine bakın.

## Sürüm çıkarma

`docs/CI-CD.md` → "Yayın çıkarma" bölümü. Kısaca:

```bash
# VERSION + package.json birlikte artır, commit et, sonra:
git tag v2.25.0 && git push origin v2.25.0
```

CI imajları derler, GHCR'a basar, Release oluşturur ve VDS'e deploy eder.
