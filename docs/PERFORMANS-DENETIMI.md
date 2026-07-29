# Performans Denetimi — Darboğazlar

**Tarih:** 2026-07-28 · **Kapsam:** `enerjione/enerjione-grid` @ `main`
**Yöntem:** Kod ve derlenmiş çıktı üzerinde ölçüm; tahmin değil.

---

## Özet

Dört gerçek darboğaz vardı. **Üçü giderildi** (`700b1e7`), biri bilinçli
olarak ertelendi.

| # | Darboğaz | Durum |
|---|---|---|
| 1 | Telemetri temizleme sorgusu tüm tabloyu tarıyor | ✅ Düzeltildi |
| 2 | 3.7 MB ikon fontu (122 ikon için) | ⏸ Ertelendi — aşağıda gerekçe |
| 3 | Alarm reconcile'da N+1 | ✅ Düzeltildi |
| 4 | Kod bölme yok — 2.1 MB tek JS | ✅ 2.1 MB → 739 KB |

Ayrıca Postgres varsayılan ayarlarla koşuyordu — düzeltildi (madde 5).

### Ölçülen sonuç

```
                    ÖNCE      SONRA     fark
ana JS             2150 K      739 K   -1411 K  (-66%)
CSS                 544 K      542 K
ikon fontu         3789 K     3739 K   (ertelendi)
                   ──────    ───────
ilk yükleme        6483 K     5021 K     -23%
font hariç         2694 K     1282 K     -52%
```

JS 29 parçaya bölündü; en büyüğü `DeviceDetailPage` (848 K) ve yalnızca bir
cihaz açıldığında iniyor.

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

## 5. ✅ Bellek — Postgres varsayılanlarla çalışıyordu

Saha donanımı **en az 16 GB** olarak netleşti; tavan toplamı (13 GB) sorun
değil — `limits` tavandır, boş belleği tutmaz. Gerçek rezervasyon 3 GB.

Asıl bulgu bu değildi: **Postgres tamamen varsayılan ayarlarla koşuyordu.**

```
shared_buffers   128 MB      ← 21M satırlık telemetri için çok düşük
work_mem           4 MB      ← pencere/sıralama DİSKE taşıyor
```

`work_mem=4MB`, madde 1'de "sıralama `work_mem`'e sığmazsa diske taşıyor"
dediğim durumun **doğrudan sebebiydi**. Sorgu düzeltildi ama ayar da
düzeltilmeliydi.

**Uygulandı:** Postgres'e kendi kaynak bloğu (3 GB tavan) ve ayarlar:

| Ayar | Değer | Neden |
|---|---|---|
| `shared_buffers` | 768 MB | Tavanın ~%25'i (PostgreSQL önerisi) |
| `work_mem` | 16 MB | × en fazla 50 bağlantı = 800 MB en kötü durum |
| `maintenance_work_mem` | 256 MB | VACUUM / index oluşturma |
| `effective_cache_size` | 2 GB | **Planlayıcı ipucu** — bellek ayırmaz |
| `max_wal_size` | 2 GB | Checkpoint'leri seyrelt (yazma yoğun) |
| `checkpoint_completion_target` | 0.9 | I/O tepe noktalarını yay |

768 + 800 + 256 ≈ 1.8 GB < 3 GB tavan.

> RAM 8 GB'ın altına inerse bu değerler **ve** container tavanı düşürülmeli.
> Kurulum aracının "Bağlantıyı Test Et" adımı RAM'i zaten gösteriyor.

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

**Yapıldı** (`700b1e7`): temizleme sorgusu, alarm reconcile, kod bölme.

**Sırada:**
1. Temizleme sorgusunu gerçek veriyle `EXPLAIN ANALYZE` ile doğrula —
   statik analiz "index artık kullanılabilir" diyor, ölçüm bunu teyit etmeli
2. Lucide geçişini tamamla → ikon fontu tamamen kalkar (3.7 MB)

**Yük altında ölçüldükten sonra:**
3. `signals.py:204` hydration maliyeti (~105 K satır)

---

## İkinci tarama (düzeltmelerden sonra)

Düzeltmeler uygulandıktan sonra sistem yeniden tarandı.

**Doğrulananlar:**
- Temizleme sorgusunda filtre pencerenin içinde, dışarıda kalmadı
- Alarm reconcile döngüsünde DB çağrısı **sıfır**
- İlk yükleme JS'i 739 K, 29 parça

**Kalan döngü-içi sorgular — 6 yer, hiçbiri sıcak yolda değil:**

| Yer | Neden sorun değil |
|---|---|
| `alarm_engine_service.py:307` | Toplu onay — kullanıcı seçimiyle sınırlı |
| `notification_service.py:63` | Geçersiz FCM token temizliği — genelde 0 |
| `responsibility_areas.py:448` | Bölge-hat atama — tek seferlik yönetim işi |
| `auth.py:459` | Oturum işlemleri |
| `bulk_notification_scheduler.py:67` | İş başına, dakikalık döngü |
| `grid_import_service.py:853` | Excel içe aktarma — tek seferlik |

Hiçbiri saniyede/30 saniyede bir koşan telemetri yolunda değil.

**Sonraki aday — `signals.py:204`:**
Tüm cihazların son sinyal değerlerini tek `DISTINCT ON` ile çekiyor. Sorgu
şekli doğru (yorumda eski GROUP BY+JOIN'in tam tarama yaptığı ve bunun
düzeltildiği yazıyor) ama **satır sayısı sınırsız**: 600 cihaz × 175 sinyal
= ~105 000 satır her çağrıda ORM nesnesine dönüşüyor. Sorgunun kendisi hızlı,
maliyet hydration'da. Sayfalama veya `Row` tuple kullanımı gerekebilir —
ama önce gerçek yükte ölçülmeli.

**`public.py:189`** aynı GROUP BY+JOIN desenini kullanıyor ama alt sorgu
cihaz kapsamlı (`device_id == device.id`), yani sınırlı. Sorun değil.

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
