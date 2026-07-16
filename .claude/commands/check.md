---
description: Değişikliği commit öncesi doğrula (typecheck + test + migration senk.)
allowed-tools: Bash(npx tsc:*), Bash(pytest:*), Bash(git status), Bash(git diff:*), Bash(alembic:*), Read, Grep, Glob
---

Mevcut değişikliği commit öncesi doğrula:

1. `git status` + `git diff` — neyin değiştiğini gör.
2. Backend değiştiyse: `cd apps/backend-api && pytest`.
3. Model değiştiyse: yeni migration var mı kontrol et — yoksa uyar.
4. Frontend değiştiyse: `cd apps/frontend-web && npx tsc --noEmit`.
5. Backend schema ↔ frontend `types.ts`/`api.ts` senkron mu bak.
6. Yeni env değişkeni varsa `.env.example`'a eklenmiş mi kontrol et.
7. Sonucu dürüstçe raporla — geçen/kalan/atlanan ayrı ayrı.
