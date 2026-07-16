---
description: Model değişikliği için Alembic migration üret ve doğrula
allowed-tools: Bash(alembic:*), Read, Edit, Grep, Glob
---

`$ARGUMENTS` değişikliği için migration üret:

1. `apps/backend-api/app/models/` içindeki değişikliği gözden geçir.
2. `cd apps/backend-api && alembic revision --autogenerate -m "<ascii-slug>"`.
3. Üretilen dosyayı **elle incele** — autogenerate her zaman doğru değildir
   (index, server_default, enum değişimi, veri taşıma).
4. Dosya adı formatı: `YYYY_MM_DD_NNNN-<rev>_<slug>.py`. `down_revision` bir
   önceki head'e işaret etmeli (mevcut zincir: baseline → ops_manager → produces_fault).
5. `upgrade()` **ve** `downgrade()` ikisi de dolu ve tersine çalışır olmalı.
6. Veri kaybı riski varsa uyar, kullanıcıya sor.
