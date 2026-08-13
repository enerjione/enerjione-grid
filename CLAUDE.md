# EnerjiOne Grid — Proje Kılavuzu

Endüstriyel akıllı şebeke izleme platformu. Horstmann Smart Navigator 2.0 arıza-geçiş
göstergesi cihazlarını izler/yönetir. Event-driven mikroservis mimarisi, Docker + systemd
ile deploy edilir.

- **Sürüm:** 2.81.1
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

### Paralel oturumlar — AYNI AĞAÇTA ÇALIŞMA

Bu depoda aynı anda birden fazla oturum (Claude Code penceresi, IDE, terminal)
çalışıyor. **Aynı çalışma ağacını paylaşmak veri kaybettiriyor** — 2026-08-12'de
20 dakika içinde üç kaza oldu: `commit -a` başka oturumun dosyalarını aldı,
editörde kalmış eski tampon 246 satırlık bir düzeltmeyi commit ile geri aldı,
`git reset` iki commit'i daldan düşürdü.

**Kural: her iş kendi worktree'sinde.** Bu artık *otomatik* — hatırlamaya
bağlı değil (`.claude/settings.json` > `hooks`).

### Günlük akış

```powershell
.\tools\oturum-kayit.ps1                         # kim ne yapıyor, nerede çarpışıyoruz
.\tools\oturum-panel.ps1 -Ac                     # canlı görsel panel (:7373)
.\tools\oturum-ac.ps1  -Konu analiz -VSCode      # worktree + dal + .env + port + AYRI pencere
.\tools\oturum-mesaj.ps1 -Kime analiz -Mesaj "types.ts bende, 10 dk"
.\tools\oturum-birlestir.ps1 -Hepsi              # dallar main'e göre nerede
.\tools\oturum-birlestir.ps1 -Konu analiz -Uygula  # güncel main üstüne rebase
.\tools\oturum-kapat.ps1 -Konu analiz            # güvenli kapatma (junction'a dikkat)
```

Claude içinden hepsi tek komut: **`/oturum`** (durum · ac · mesaj · birlestir · panel · kapat).

### Oturumlar arası mesajlaşma

Posta kutusu `.claude/oturum-mesajlar.json`. Hedef `-Kime <oturum-adı>` ya da
herkes için `-Kime *`. **Mesaj anlık değildir**: bir Claude oturumu ancak sırası
geldiğinde bağlam alır, mesaj hedefin *bir sonraki adımında* `UserPromptSubmit`
hook'uyla bağlamına düşer. Oturum boş bekliyorsa posta kutusunda kalır — panel
okunmamışları sarı kenarla gösterir, "ulaştı mı" sorusu ortada kalmaz.

Paylaşımlı bir dosyada geniş değişikliğe **başlarken** ilgili oturuma bir satır
yaz; çarpışma hook'u zaten kimin orada olduğunu söylüyor.

### Panel ne gösterir

Her oturum bir pixel-art coworker: dalı, portu, **son isteği**, **şu an hangi
aracı hangi dosyada** çalıştırdığı, **görev ilerleme çubuğu** (`3/7 · 4 kaldı` +
o an yürüyen görev), son hareketten bu yana geçen süre, `+ileride/-geride`,
açık dosya sayısı. Altta mesaj akışı ve çarpışma tahtası; her karttan o oturuma
mesaj yollanabiliyor.

Veri kaynağı iki katmanlı: defter (hook'lar yazar) **ve** Claude'un kendi
transkriptleri (`~/.claude/projects/<slug>/*.jsonl`). İkincisi hook'lardan
bağımsız çalışır — panel hiçbir ayar yüklenmemiş bir oturumu bile görebilir.

### Ortak defter

`.claude/oturumlar.json` (ana ağaçta, gitignore'da) — açık oturumlar, dalları,
port çiftleri ve **canlı Claude pencerelerinin ne işle meşgul olduğu**. Hook'lar
yazar; `tools/oturum-ortak.ps1` okur. Bozulursa `oturum-kayit.ps1 -Onar`
worktree listesinden yeniden kurar. Port slotu defterden **ilk boş sayı** olarak
verilir — bir oturum kapanınca portu serbest kalır.

### Hook'lar

| Olay | Script | Ne yapar |
| --- | --- | --- |
| SessionStart | `oturum-durum.ps1` | Nerede olduğun, **açık oturumlar + ne yaptıkları**, dalların kaç commit geride kaldığı, aynı dosyada birden fazla oturum, migration zinciri çakışma riski |
| UserPromptSubmit | `oturum-baslik.ps1` | İlk isteği oturumun "işi" olarak deftere yazar; **diğer oturumlardan gelen mesajları bağlama düşürür** |
| PreToolUse `Bash(git *)` | `oturum-koruma.ps1` | Ana ağaçta `add -A`, `commit -a`, `reset --hard`, `clean -f`, `checkout -- .`, `stash` **engellenir**. Kendi worktree'nde serbest |
| PreToolUse `Edit/Write` | `oturum-carpisma.ps1` | Paylaşımlı dosyalarda (`src/shared/`, `src/app/`, `styles.css`, `app/models/`, `alembic_migrations/versions/`) "bu dosyada 2 oturum daha var" uyarısı. Engellemez |
| SessionEnd | `oturum-bitis.ps1` | Defterden düşer; commit'lenmemiş iş varsa ekrana yazar |
| WorktreeCreate | `oturum-worktree-hook.ps1` | Yerleşik `--worktree` / `EnterWorktree` akışını da `oturum-ac.ps1`'den geçirir |

Testler: `tools/oturum-test.ps1` (34 durum — defter, slot, yol, hook'lar) ve
`tools/oturum-koruma-test.ps1` (13 durum — engelleme; **ana ağaçtan** koşulur).

### VSCode kullanıyorsan

Eklentide sekmeler **aynı workspace klasörünü paylaşır**. Claude `EnterWorktree`
ile worktree'ye geçse bile **editör ana ağaçta kalır**; açık bir tampon
kaydedildiğinde değişiklik ana ağaca yazılır — 246 satırlık kayıp tam bu
ayrımdan çıktı. Bu yüzden `oturum-ac.ps1 -Konu <ad> **-VSCode**` kullan: ayrı
pencere açar, editörle oturumu aynı dizinde buluşturur.

### Bilinen sınır: tek Postgres

Portlar ayrı, veritabanı ortak (`enerjione_grid`). İki dal farklı migration
zinciriyle `alembic upgrade head` çalıştırırsa `alembic_version` **herkes için**
bozulur. SessionStart bunu tespit edip uyarır; sırayı konuşun — önce biri
main'e girsin, diğeri üstüne rebase etsin.

### Ana ağaçta kalmak zorundaysan

Commit'i **açık dosya yoluyla** yap, `reset` / `checkout --` öncesi
`git log --oneline -5` ile ne düşeceğine bak (düşen commit başkasının olabilir;
reflog'dan kurtarılır ama önce fark etmek gerekir), `types.ts` / `App.tsx` /
i18n gibi **ortak dosyalara** dokunduysan hemen commit'le — çarpışmaların hepsi
bu dosyalarda oldu.

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
