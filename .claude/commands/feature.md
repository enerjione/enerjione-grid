---
description: Frontend'e yeni bir feature sayfası/modal ekle
---

`$ARGUMENTS` için frontend feature ekle. Konvansiyonlar:

1. `src/features/<feature>/` klasörü aç. Sayfa `<Ad>Page.tsx`, modal `<Ad>Modal.tsx`.
2. API çağrıları **sadece** `src/shared/api.ts` üzerinden — component'e `fetch` yazma.
   Yeni endpoint çağrısı gerekiyorsa önce `api.ts` + `types.ts`'e ekle.
3. Kullanıcıya görünen tüm metin `t(...)` (react-i18next). Çeviri `src/shared/i18n`.
4. Tip güvenli — TS strict açık, `any` kullanma. Tipler `src/shared/types.ts`.
5. Rota/menü `src/app/App.tsx` içinde; yetki gerekiyorsa role kontrolü ekle.
6. Icon: `material-symbols`. Harita gerekiyorsa Leaflet + react-leaflet.
7. Canlı veri lazımsa `useLiveValuesSocket.ts` pattern'ini kullan.

Bitince `npx tsc --noEmit` ile type check, sonra çalıştırıp doğrula.
