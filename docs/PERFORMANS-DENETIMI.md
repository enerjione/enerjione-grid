# Performans Denetimi — Darboğazlar

**Tarih:** 2026-07-28 · **Kapsam:** `enerjione/enerjione-grid` @ `main`
**Yöntem:** Kod ve derlenmiş çıktı üzerinde ölçüm; tahmin değil.

---

## Özet

Dört gerçek darboğaz var. İkisi **ölçek büyüdükçe sistemi durdurur**, ikisi
kullanıcı deneyimini bozar.

| # | Darboğaz | Etki | Efor |
|---|---|---|---|
| 1 | Telemetri temizleme sorgusu tüm tabloyu tarıyor | 🔴 Ölçekte DB'yi kilitler | 1 saat |
| 2 | 3.7 MB ikon fontu (122 ikon için) | 🔴 İlk açılış | 2 saat |
| 3 | Alarm reconcile'da N+1 | 🟡 Alarm çokken gecikme | 2 saat |
| 4 | Kod bölme yok — 2.1 MB tek JS | 🟡 İlk açılış | 4 saat |

Ayrıca **bellek limitleri saha donanımıyla uyumsuz** (aşağıda).

---

## 1. 🔴 Telemetri temizleme sorgusu — en kritik

`app/services/telemetry_retention.py:170`

```sql
DELETE FROM telemetry t USING (
  SELECT id FROM (
    SELECT id, source_timestamp,
           ROW_NUMBER() OVER (PARTITION BY device_id, signal_key ORDER BY id DESC) AS rn
    FROM telemetry                    -- ← WHERE YOK
  ) sub
  WHERE rn > 1 AND source_timestamp < :cutoff
) old WHERE t.id = old.id
```

**Sorun:** İç sorgu `telemetry` tablosunun **tamamını** okuyor ve
`ROW_NUMBER()` için `(device_id, signal_key)` bazında **sıralıyor**. Filtre
(`source_timestamp < cutoff`) pencere fonksiyonundan **sonra** uygulandığı
için `idx_telemetry_source_timestamp` bu sorguda **kullanılamıyor**.

`app/main.py:270`'teki yorum "index olmadan full table scan" diyor — index
eklenmiş ama sorgu şekli onu devre dışı bırakıyor. Yani koruma var sanılıyor,
gerçekte yok.

**Ölçek:** Tablo 30 dakikalık veri tutuyor. 600 cihaz × ~20 sinyal ×
saniyede 1 okuma ≈ **21 milyon satır**. Bu sorgu **5 dakikada bir** o 21
milyon satırı tarayıp sıralıyor. Sıralama `work_mem`'e sığmazsa diske
taşıyor; DELETE süresince satır kilitleri birikiyor ve ingest yavaşlıyor.

**Çözüm** — filtreyi pencere fonksiyonunun içine indir:

```sql
DELETE FROM telemetry t USING (
  SELECT id FROM (
    SELECT id, ROW_NUMBER() OVER (PARTITION BY device_id, signal_key ORDER BY id DESC) AS rn
    FROM telemetry
    WHERE source_timestamp < :cutoff       -- ← index burada devreye girer
  ) sub WHERE rn > 1
) old WHERE t.id = old.id
```

Böylece yalnızca eski satırlar taranır. Semantik farkı: veri göndermeyi
bırakmış bir cihaz için "son bilinen değer" yine korunur (amaç buydu); aktif
cihazlarda cihaz+sinyal başına bir fazladan eski satır kalır — sonraki turda
temizlenir, zararsız.

> Doğrulamadan uygulamayın: gerçek veriyle `EXPLAIN ANALYZE` alıp önce/sonra
> karşılaştırın. Bu sorgu veri siliyor.

---

## 2. 🔴 İkon fontu — 3.7 MB

```
dist/assets/material-symbols-outlined-*.woff2   3.7 MB   ← en büyük varlık
dist/assets/index-*.js                          2.1 MB
dist/assets/index-*.css                         544 KB
                                        toplam  8.8 MB
```

Kodda **122 farklı ikon** kullanılıyor; font ise tüm Material Symbols
setini (binlerce glif) taşıyor. Fontun kendisi JS bundle'ından büyük.

**Etki:** Saha mini PC'sinde WiFi AP üzerinden ilk açılış. LAN'da tolere
edilir, ama uzaktan bakımda (VPN/internet) belirgin gecikme.

**Çözüm seçenekleri:**

| Yol | Kazanç | Efor |
|---|---|---|
| Fontu kullanılan 122 glife indir (`glyphhanger`/`pyftsubset`) | 3.7 MB → ~30 KB | 2 saat |
| Lucide'a tamamen geç (zaten kısmen kullanılıyor) | Font tamamen kalkar | 1-2 gün |

Kısa vadede **subsetting** doğru seçim: tek build adımı, davranış değişmiyor.

---

## 3. 🟡 Alarm reconcile — N+1

`app/services/alarm_reconciliation.py:296` — **30 saniyede bir** çalışan
döngü, **her açık alarm için ayrı bir sorgu** atıyor:

```python
for alarm in open_alarms:
    last = db.scalar(select(Telemetry).where(device_id==...).where(signal_key==...) ...)
```

Sorgular indeksli ve hızlı, ama N tur gidiş-dönüş. Bir hat arızasında 100+
alarm açıksa 30 saniyede bir 100+ sorgu.

**Çözüm:** Tek sorguda `DISTINCT ON (device_id, signal_key)` ile ilgili tüm
son değerleri çekip bellekte eşleştirmek. 100 sorgu → 1 sorgu.

---

## 4. 🟡 Kod bölme yok

`App.tsx`'te `React.lazy` **sıfır**. 16 mühendislik sayfasının tamamı ilk
yüklemede geliyor → 2.1 MB tek JS. Kullanıcı yalnızca panoya baksa bile
Modbus plan ekranı, harita, grafik kütüphanesi hepsi iniyor.

**Çözüm:** Rota bazlı `React.lazy` + `Suspense`. Vite kod bölmeyi kendisi
yapar; ilk yükleme muhtemelen yarıya iner.

---

## 5. 🟡 Bellek limitleri saha donanımıyla uyumsuz

```
heavy  4 × 2 GB   = 8 GB    (postgres, rabbitmq, nats, backend-api)
worker 8 × 512 MB = 4 GB
                    ─────
toplam limit        12 GB
```

Limit tavandır, hepsi aynı anda dolmaz — ama **4 GB RAM'li bir mini PC'de**
Postgres tek başına 2 GB'a kadar şişebilir ve OOM killer devreye girer.

**Yapılacak:** Saha donanımının gerçek RAM'ini netleştirin. 8 GB altındaysa
`x-heavy-resources` limitlerini düşürün (Postgres 1 GB, backend 768 MB gibi)
ve `shared_buffers`'ı buna göre ayarlayın. Kurulum aracının "Bağlantıyı Test
Et" adımı RAM'i zaten gösteriyor — 4 GB altında uyarı verilmeli.

---

## İyi durumda olanlar

Denetimde doğruladığım, doğru kurulmuş kısımlar:

- **`telemetry_history` hypertable** + `(device_id, signal_key, source_timestamp)`
  bileşik indeksi — grafik sorguları için doğru şekil.
- **`telemetry` bileşik indeksi** `(device_id, signal_key, source_timestamp DESC)`
  — canlı değer sorgusu doğrudan index seek.
- **Connection pool** 30+20: 600 cihaz ölçeğinde makul; varsayılan 5+10 ile
  kalınsaydı bağlantı açlığı yaşanırdı.
- **Canlı değerler WebSocket ile** — polling değil. Doğru tercih.
- **Yoklama sıklıkları makul**: 1 sn'likler yalnızca sayaç güncelliyor
  (ağ isteği yok), gerçek istekler 5-30 sn aralıklarında.
- **Telemetri 30 dakika DB'de**, gerisi hypertable'da — sıcak tablo küçük
  kalıyor (temizleme sorgusu düzeltilirse).

---

## Plan

**Önce (yarım gün):**
1. Temizleme sorgusunu düzelt — `EXPLAIN ANALYZE` ile önce/sonra ölç
2. Fontu subset'le — 3.7 MB → ~30 KB

**Sonra (bir gün):**
3. Alarm reconcile'ı tek sorguya indir
4. Saha RAM'ini netleştir, limitleri hizala

**Fırsat oldukça:**
5. Rota bazlı kod bölme

---

## Ölçemediklerim

Bu denetim **statik**. Aşağıdakiler ancak yük altında görülür ve gerçek
kararlar için gereklidir:

- Gerçek satır sayıları ve sorgu süreleri (`pg_stat_statements`)
- NATS JetStream tüketici gecikmesi (consumer lag)
- 600 cihaz altında ingest hızı ve batch davranışı
- Container'ların gerçek bellek kullanımı (`docker stats`)

Sahada bir kurulum yük alır almaz bu dördünü ölçmek, buradaki tahminleri
gerçeğe çevirir.
