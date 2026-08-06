# EnerjiOne Grid — Proje Kılavuzu

Endüstriyel akıllı şebeke izleme platformu. Horstmann Smart Navigator 2.0 arıza-geçiş
göstergesi cihazlarını izler/yönetir. Event-driven mikroservis mimarisi, Docker + systemd
ile deploy edilir.

- **Sürüm:** 2.53.11
- **Ana dal:** `main` (tek gövde, her an release edilebilir). Sürümler `v2.25.0` gibi
  **tag** ile çıkar; deploy tag'den tetiklenir, daldan değil. İş dalları: `feat/...`, `fix/...`.
- **Dil:** Kod yorumları ve UI **Türkçe**. Kod tabanında ASCII-only yorum tercih edilir
  (bazı dosyalarda Türkçe karakter var, yeni yorumda ASCII kullan: "ariza" gibi).

---

## Mimari

```
Frontend (Vite+React+TS, nginx:80/8080)
    │ /api/v1/...
Backend API (FastAPI + SQLAlchemy 2, uvicorn:8000)
    ├─ Postgres 16        (enerjione_grid DB)
    ├─ RabbitMQ           (management)
    ├─ NATS JetStream     (gateway ↔ backend ↔ worker)
    └─ Workers:
        ├─ tag-engine           (sinyal → tag işleme)
        ├─ alarm-service        (alarm kural motoru)
        ├─ notification-worker  (SMTP/SMS/Telegram/FCM dispatch)
        └─ iec104-outbound      (IEC104 SCADA çıkışı)
```

Container namespace: `e1-grid-<service>`. Solar yan yana çalışırsa `e1s-*` kullanır.

## Repo Yapısı

```
apps/
  backend-api/        FastAPI. app/{api,services,models,schemas,repositories,core,db,data,workers}
  frontend-web/       React. src/{app,components,features,shared}
  tag-engine/         Worker: tag_engine/
  alarm-service/      Worker: alarm_service/ (main.py + rules.py)
  notification-worker/ Worker: notification_service/
  iec104-outbound/    Worker: iec104_outbound/ (encoder, server, registry, consumer...)
packages/
  shared-contracts/   telemetry-contract.json (servisler arası sözleşme)
infra/
  host-nginx/         multi-domain reverse proxy setup
  nats/               NATS config
  systemd/            setup-systemd.sh
  scripts/
docs/
  DEPLOYMENT.md, security-roadmap.md, event-driven-validation.md
install.sh update.sh uninstall.sh (+ .ps1 karşılıkları)  docker-compose.yml
```

---

## Backend konvansiyonları (`apps/backend-api`)

**Katman akışı:** `api/` (router, HTTP) → `services/` (iş mantığı) → `models/` + `repositories/`
(ORM/persistence) → `schemas/` (pydantic I/O). Router içine iş mantığı gömme; service'e taşı.

- **Router dosyası:** üstte docstring ile endpoint listesi + yetki notu yaz (bkz `app/api/faults.py`).
- **Import sırası:** stdlib → 3rd party → `app.*`. Absolute import (`from app.models.user import User`).
- **DB session:** `Depends(get_db)` → `Session`. SQLAlchemy 2 style: `select(...)`, `db.get(...)`,
  `db.execute(...).scalars()`. Legacy `Query` API kullanma.
- **Auth/yetki:** `Depends(get_current_user)`, `Depends(require_roles(...))`. Scope filtresi
  operator için `scope_service.get_visible_line_ids(...)` üzerinden.
- **Roller:** `installer` > `engineer` > `ops_manager` > `operator` (bkz `app/models/enums.py` UserRole).
- **Olay kaydı:** kalıcı aksiyonlarda `event_service.record_event(...)` ile audit log.
- **Zaman:** her yerde UTC-aware — `datetime.now(timezone.utc)`. Naive datetime kullanma.
- **Config:** `from app.core.config import settings`. Yeni env değişkeni → `config.py` + `.env.example`.
- **Idempotency / outbox:** dış sisteme yayında `outbox_service` + `processed_message` pattern'i var,
  bozma. Event yayını `event_bus` / `jetstream_bus` üzerinden.
- **Bağımlılık ekleme:** `requirements.in` düzenle → `pip-compile --generate-hashes` ile
  `requirements.txt` üret (hash-locked, elle txt düzenleme yok). Yeni dep eklemeden önce
  mevcut stdlib/dep ile çözülebiliyor mu bak.

## Migration (Alembic)

- Konum: `apps/backend-api/alembic_migrations/versions/`. Format: `YYYY_MM_DD_NNNN-<rev>_<slug>.py`.
- Model değiştirince **her zaman** migration üret. `down_revision` zincirini koru.
- Mevcut zincir: baseline → ops_manager_role → produces_fault.
- Üretim: `cd apps/backend-api && alembic revision --autogenerate -m "aciklama"` → gözden geçir → düzelt.

## Frontend konvansiyonları (`apps/frontend-web`)

- **TS strict açık.** `any` kaçınılmaz değilse kullanma. Tipler `src/shared/types.ts`.
- **Feature-based:** her sayfa `src/features/<feature>/` altında (`*Page.tsx`, `*Modal.tsx`).
- **API çağrısı:** hepsi `src/shared/api.ts` üzerinden — component içine `fetch` yazma.
  Base URL resolution zaten orada (same-origin `/api/v1` / dev `:8000`).
- **i18n:** kullanıcıya görünen metin `react-i18next` ile — hardcode Türkçe string koyma
  (mevcut kodda istisna var ama yeni kodda `t(...)` kullan). Çeviriler `src/shared/i18n`.
- **Harita:** Leaflet + react-leaflet. Live veri `src/shared/useLiveValuesSocket.ts` (WS).
- **Icon:** `material-symbols`.

---

## Geliştirme

```bash
# Backend (Python 3.11+)
cd apps/backend-api && python -m venv .venv && .venv\Scripts\activate
pip install -r requirements.txt && cp .env.example .env
python -m uvicorn app.main:app --reload --port 8000

# Frontend (Node 20+)
cd apps/frontend-web && npm install && npm run dev   # :5173, API'yi :8000'e proxy

# Testler
cd apps/backend-api && pytest
cd apps/frontend-web && npx tsc -b          # type check (--noEmit DEGIL: kok
#                                            tsconfig solution-style, files:[] --
#                                            --noEmit hicbir dosyayi kontrol etmez ve
#                                            her zaman 0 doner)
```

## Deploy / Docker

- `docker-compose.yml` tüm stack. Image: `e1-grid/<service>:${E1_VERSION:-latest}`.
- Güncelleme: `sudo bash update.sh [backend|frontend|alarm|tag|notification|iec]`.
- systemd: `sudo systemctl {start,stop,restart,status} enerjione-grid`.

---

## Değişiklik yaparken

1. **Migration gerekiyor mu?** Model'e dokunduysan evet.
2. **Şema senkron mu?** Backend `schemas/` ↔ frontend `types.ts` ↔ `api.ts` birlikte güncelle.
3. **Yetki/scope etkilendi mi?** Operator görünürlüğü `scope_service`'ten geçer, kontrol et.
4. **Audit gerekli mi?** Kalıcı state değişimi → `record_event`.
5. **Değişikliği çalıştırıp doğrula** — sadece typecheck/test değil, akışı gerçekten sür.
6. **En küçük diff.** Yeni soyutlama/dosya isteniyor mu emin ol; stdlib/mevcut dep önce.
7. **Türkçe UI + Türkçe commit mesajı** (bkz git log: `feat(alarm/fault): ...`).

## Commit

Conventional commit, Türkçe açıklama: `feat(scope): ...`, `fix(dashboard): ...`.
Kullanıcı istemeden commit/push yapma. İş dalı aç (`feat/...`, `fix/...`), `main`'e PR ile gir.

---

## Kurulu Skill'ler (`.claude/skills/`)

Bu skill'ler ilgili işte **otomatik tetiklenir**; ayrıca `/code-reviewer` gibi elle de çağrılabilir.

- **code-reviewer** — PR/diff incelemesi (Python + TS/JS destekli). Kalıcı değişiklikten önce
  bu projenin katman/scope/audit kurallarına göre gözden geçir. Referans: `references/`, script: `scripts/`.
  Yerleşik `/code-review` skill'i ile birlikte kullanılabilir.
- **frontend-design** — yeni UI/component tasarımı. `src/features/` altında yeni ekran yaparken;
  ama bu projenin mevcut stiline (Leaflet, material-symbols, i18n) **uyumlu** kal, jenerik değil.
- **docx** — Word (.docx) üret/oku/düzenle. Rapor/mektup/şablon çıktısı istenirse. PDF/Excel için değil.

### Notlar
- Ortam **Windows / PowerShell**. Skill script'leri Python; `python` PATH'te olmalı.
- Güvenlik taraması için ayrı hook **yok** — secret koruması `.gitignore` (fcm/env/pem/key) +
  `.claude/settings.json` deny listesi ile sağlanıyor. Yeni secret'ı asla koda gömme,
  `.env` / vault (`secrets_vault.py`) kullan.
