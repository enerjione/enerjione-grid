---
description: Backend'e yeni bir API endpoint ekle (katman akışını koruyarak)
---

`$ARGUMENTS` için yeni endpoint ekle. Bu projenin backend konvansiyonlarına uy:

1. **Router** (`app/api/<kaynak>.py`): docstring'e endpoint + yetki notu ekle.
   `Depends(get_current_user)` / `require_roles(...)`, `Depends(get_db)`.
2. **Service** (`app/services/`): iş mantığını buraya yaz, router ince kalsın.
3. **Schema** (`app/schemas/`): request/response pydantic modelleri.
4. **Model** değiştiyse → Alembic migration üret + `down_revision` zincirini koru.
5. **Scope:** operator görünürlüğü etkileniyorsa `scope_service` filtresi uygula.
6. **Audit:** kalıcı state değişimi varsa `event_service.record_event(...)`.
7. Router'ı `app/api/__init__.py`'ye kaydet (gerekiyorsa).
8. Frontend'e lazımsa `src/shared/api.ts` + `types.ts` güncelle.

Zaman UTC-aware (`datetime.now(timezone.utc)`), SQLAlchemy 2 style (`select`).
Bitince değişikliği çalıştırıp doğrula.
