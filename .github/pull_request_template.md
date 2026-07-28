## Ne değişti

<!-- Bir-iki cümle. "Ne yaptım" değil, "kullanıcı/sistem için ne değişti". -->

## Neden

<!-- Sorunun kendisi. Varsa issue: Closes #123 -->

## Nasıl doğrulandı

<!-- Sadece "testler geçti" yetmez — akışı gerçekten sürdüğünüzü yazın.
     Örn: "VDS'te alarm üretip Telegram bildirimini aldım" -->

- [ ] `npx tsc --noEmit` / `npm run build` (frontend değiştiyse)
- [ ] `pytest` (backend değiştiyse)
- [ ] Değişiklik gerçekten çalıştırıldı, sadece derlenmedi

## Kontrol listesi

- [ ] **Migration**: model değiştiyse alembic revision üretildi, `down_revision` zinciri tek head
- [ ] **Şema senkron**: backend `schemas/` ↔ frontend `types.ts` ↔ `api.ts` birlikte güncellendi
- [ ] **Yetki/scope**: operator görünürlüğü etkilendiyse `scope_service` kontrol edildi
- [ ] **Audit**: kalıcı state değişimi varsa `record_event(...)` eklendi
- [ ] **i18n**: yeni kullanıcı metni `t(...)` ile, `tr.json` + `en.json` güncel
- [ ] **Secret yok**: token/parola/anahtar koda gömülmedi (`.env` veya vault)
- [ ] **Sürüm**: yayına çıkacaksa `VERSION` + `apps/frontend-web/package.json` birlikte artırıldı

## Saha etkisi

<!-- Mevcut kurulumlarda elle bir adım gerekiyor mu? (.env alanı, migration
     süresi, servis yeniden başlatma, geri alma yolu) Yoksa "yok" yazın. -->
