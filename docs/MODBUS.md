# Modbus TCP Outbound

Sahadaki sinyalleri Modbus TCP ile dis SCADA'ya yayinlar. IEC 104 gibi
**ayri bir servis** olarak calisir (`modbus-outbound`): SCADA'nin tarama hizi
backend'i veya telemetri akisini etkilemez.

```
tag-engine → NATS (telemetry.normalized) → modbus-outbound (bellek) → SCADA okumasi
                                                ▲
                            backend /internal/modbus-plans   (adres plani)
                                   /internal/modbus-values   (son bilinen degerler)
```

SCADA istedigi hizda okur; her okuma bellekten cevaplanir, DB'ye veya
backend'e hic dokunulmaz.

**Iki besleme kanali var.** Canli akis (NATS) yalnizca cihaz yeni olcum
yayinladikca akar; degismeyen sinyaller icin bu yeterli DEGILDIR (bkz.
[6. Son bilinen deger tazelemesi](#6-son-bilinen-deger-tazelemesi)).

---

## 1. Kapasite — kac cihaz sigar?

Horstmann SN2 katalogu (193 sinyal/cihaz) uzerinden gercek olculer:

| Veri tipi | Adet | Modbus alani | Yer |
|---|---|---|---|
| analog | 75 | holding + input register | 75 word (int16) · 150 word (float32) |
| counter | 6 | register (32-bit) | 12 word |
| binary | 64 | discrete input | 64 bit |
| binary_output | 18 | coil (salt okunur) | 18 bit |
| string | 30 | — | **dahil edilmez** |

**Cihaz basina:** 87 word (int16) veya 162 word (float32) + 82 bit.

| Mod | Blok | Kapasite | Not |
|---|---|---|---|
| block · int16 | 100 register | **655 cihaz** | Cihaz basina TEK okuma istegi (FC3/4 limiti 125) |
| block · int16 | 128 register | 512 cihaz | Rahat pay |
| block · float32 | 200 register | 327 cihaz | Cihaz basina 2 istek |
| unit | — | 247 cihaz | Modbus unit id araligi (port basina) |

Bit alani ayri bir 65536'lik uzaydir; cihaz basina 100 bit ile orada da 655
cihaz sigar. **Gercek darbogaz adres degil, master'in tarama hizidir**
(655 cihaz x 1 istek ~ 100 istek/sn'de 7 saniye/tur).

247 cihazi asan kurulumlar icin: ikinci bir Modbus hedefi acilir (farkli port).

---

## 2. Iki adresleme modu

Hedef olustururken secilir; sonradan degistirilebilir.

### block — tek yayin, adres bloklari (varsayilan)

Butun cihazlar **tek unit id**'de; her cihaza kendi adres blogu verilir.

```
unit 1
  0…99     DEV-001     (0=sayac, 4=modem_rssi, 5=test_point_level, …)
  100…199  DEV-002     (100=sayac, 104=modem_rssi, …)
  200…299  DEV-003
```

Adres = `base_address + slot_index × stride + sinyal_offset`

### unit — cihaz basina unit ID

Her cihaz kendi **Modbus slave (unit) adresinde**; adres duzeni hepsinde ayni.

```
unit 1 (DEV-001)     unit 2 (DEV-002)     unit 3 (DEV-003)
  0  sayac             0  sayac             0  sayac
  4  modem_rssi        4  modem_rssi        4  modem_rssi
```

SCADA'da her cihaz ayri bir slave gibi gorunur; adres tablosu tek sayfadir.

### Adresler neden kaymaz

Cihaz → (unit_id, blok baslangici) eslemesi `outbound_modbus_slots` tablosunda
**kalici** tutulur. Adresler cihaz listesinden her seferinde yeniden
turetilseydi, aradan bir cihaz silindiginde sonraki tum cihazlarin adresi kayar
ve SCADA'daki butun etiketler sessizce yanlis noktayi gosterirdi. Slot bir kez
atanir; silinen cihazin yeri bosalir ve yeni cihaza verilir.

---

## 3. Veri alanlari

| Sinyal tipi | Fonksiyon | Not |
|---|---|---|
| analog | **FC3** (holding) + **FC4** (input) | Ayni icerik iki alanda birden. SCADA'lar hangisini okuyacagi konusunda ikiye bolunur; ayna yayin "yanlis alan" sorununu bastan kaldirir. |
| counter | FC3 + FC4 | Her zaman 32-bit (2 register), formattan bagimsiz |
| binary | FC2 (discrete input) | |
| binary_output | FC1 (coil) | **Salt okunur** |
| string | — | Modbus metin tasimaya uygun degil |

### Deger formati

- **int16 (varsayilan):** `raw = (deger − offset) / scale`. SCADA tarafinda ayni
  katsayilar girilir — adres tablosunda ve CSV'de her sinyal icin yazar.
  Tasma durumunda deger **kirpilir** (sarma yok): 40000 A'lik hatali bir okuma
  SCADA'da −25536 gibi inandirici ama tamamen yanlis bir degere donusmez.
- **float32:** IEEE 754, 2 register. Olcek yok, muhendislik birimi dogrudan
  okunur. Word sirasi `big` (ABCD, standart) veya `little` (CDAB, word-swap
  bekleyen PLC'ler icin) secilebilir.

### Kalite (quality)

Modbus'ta kalite biti yoktur. Bu yuzden davranis **Canlı Değerler ekraniyla
birebir aynidir**: o an gelen deger, kalitesi ne olursa olsun yazilir. Kalite
yalnizca teshis icin sayilir (`bad_quality_count`), yazmayi **engellemez**.

> Onceden "bozuk kaliteli olcumu atla, son iyi degeri koru" davranisi vardi.
> Bir sinyal HIC iyi kaliteli gelmezse (sahada goruldu: bir hedefte 10K+
> mesajin 6K+'si surekli `bad`, bir kez bile `good` degil) register hicbir
> zaman yazilmiyor ve SCADA sonsuza dek varsayilan **0** goruyordu — sistemin
> geri kalani gercek degeri gosterirken. Yani "koruma" yaniltici olani
> koruyordu.

---

## 4. Guvenlik

**Kanal salt okunurdur.** FC1/2/3/4 disindaki her sey, ozellikle yazma
fonksiyonlari (FC5/6/15/16), `Illegal Function` ile reddedilir.

Modbus'ta **kimlik dogrulama yoktur** — ne parola ne sertifika. Yazmaya izin
vermek, aga erisen herkese cihaz komutu verme yetkisi vermek olurdu.

Veriyi kimin okuyabilecegini sinirlayan tek mekanizma **IP allowlist**'tir
(hedef formunda). Bos birakilirsa agdaki herkes baglanip okuyabilir.

---

## 5. Kullanim

### Hedef olusturma

Muhendislik ▸ Entegrasyonlar ▸ **Outbound** ▸ Yeni hedef ▸ Protokol: **Modbus TCP**

1. Adresleme modunu sec (block / unit) — kart uzerinde ornek adresler gorunur
2. Port: **502** (varsayilan), 5020 veya 5021
3. Deger formati: int16 (kompakt) veya float32
4. IP allowlist: SCADA sunucusunun IP'sini ekle

Form altinda **"Bu ayarlarla N cihaz sigar"** yazar; secim degistikce guncellenir.

### Adres listesini alma

Hedef satirinda **"Adres planı"** butonu:

- Kapasite ozeti (mod, cihaz/kapasite, cihaz basina word/bit, toplam nokta)
- Tam adres tablosu: cihaz, unit, fonksiyon, adres, **Modicon** gosterimi
  (40001 tarzi), sinyal, olcek
- **CSV indir** — SCADA'ya toplu tag girisi icin

Plan backend'de uretilir ve `modbus-outbound` **ayni plani** uygular; yani
ekranda gordugunuz adres, sahada yayinlanan adresin ta kendisidir.

### Portlar

`docker-compose.yml` 502, 5020 ve 5021'i yayinlar. Baska port gerekiyorsa
compose'daki `ports` listesine eklenmeli (IEC104'teki 2404-2406 mantigi).

Container non-root (uid 10001) calisir; 502 gibi <1024 portu baglayabilmesi
icin `net.ipv4.ip_unprivileged_port_start=0` sysctl'i verilir. Bu ayar
container namespace'ine ozeldir, **host'un port politikasini degistirmez**.

---

## 6. Son bilinen deger tazelemesi

Modbus'ta **"deger henuz gelmedi" diye bir hal yoktur**: SCADA ne sorarsa
depoda ne varsa onu okur ve yazilmamis adres **0** doner. Canli telemetri
akisi ise yalnizca cihaz **yeni olcum yayinladikca** akar. Bu ikisi bir arada
sessiz bir ariza uretir:

| Durum | Canli akista ne olur | Register |
|---|---|---|
| Sinyal degismiyor (ariza bayragi, nominal degerler, konum) | gunlerce mesaj gelmez | **0** kalir |
| Servis/konteyner yeniden basladi | tuketici `DeliverPolicy.NEW` — gecmis oynatilmaz | **0** kalir |
| Yeni hedef / yeni cihaz plana girdi | ilk olcume kadar bos | **0** kalir |

Yani SCADA "gerilim 0", "ariza yok" okur; ekran ise gercek degeri gosterir.

**Cozum:** worker her `MODBUS_SNAPSHOT_REFRESH_SEC` saniyede (varsayilan **30**)
`/internal/modbus-values` ucundan **son bilinen degerleri** ceker ve eksik
register'lari doldurur. Kaynak backend'in `telemetry_latest` tablosudur —
**Canli Degerler ekraninin okudugu ayni satirlar**. Boylece "ekranda var,
SCADA'da yok" ayrismasi yapisal olarak ortadan kalkar.

Tazeleme canli akisin yerine gecmez, **boslugu doldurur**:

- Her (cihaz, sinyal) icin register'a yazilan son degerin **kaynak damgasi**
  tutulur. DB'den gelen **bayat** bir satir, daha yeni bir canli degeri
  **ezemez** — aksi halde SCADA'da gorunur bir geri sicrama olurdu.
- Plan degistiginde (yeni cihaz/hedef/adres) tazeleme **beklemeden** tetiklenir,
  yeni adresler bir sonraki periyodu beklemez.
- `MODBUS_SNAPSHOT_REFRESH_SEC=0` tazelemeyi kapatir. Kapatilirsa degismeyen
  sinyaller SCADA'da yeniden 0 gorunur; bilerek yapilmadikca dokunmayin.

### Cekim artimlidir

600 cihaz x 193 sinyal = **~115.000 satir**. Bunu her 30 saniyede tam cekmek,
`/signals/live` ucunda backend'i OOM'a goturen desenin aynisi olurdu. Bu yuzden:

| Tur | Ne cekilir |
|---|---|
| Ilk tur (servis basladi) | **tam liste** — tohumlama |
| Plan degisti | **tam liste** — adresler kaymis olabilir |
| Her 20. tur (~10 dk) | **tam liste** — saat geri alinmasina karsi kendini onarim |
| Diger turlar | yalnizca `updated_at >= since` satirlari (genelde birkac yuz) |

Esik worker'in kendi saatinden DEGIL, backend'in yanitindaki
`max_updated_at`ten gelir; iki taraf arasindaki saat kaymasi satir
kaybettirmez. `/health` icindeki `snapshot_full_refreshes` kacinin tam tur
oldugunu soyler — her tur tam cikiyorsa esik ilerlemiyor demektir.

Sayaclar `/health` ve **SCADA Çıkışları ▸ Modbus Yayın Durumu** ekraninda:
`snapshot_seeded` (ilk kez yazilan nokta — asil kazanc), `snapshot_refreshed`
(canli akisin kacirdigi, DB'si daha yeni), `snapshot_stale_skipped` (canli
deger daha taze, dokunulmadi), `snapshot_unmapped` (planda yok — string
sinyaller icin normal).

---

## 7. Tanilama

```bash
# Servis sagligi + hedef bazinda runtime (bagli SCADA, istek sayisi)
curl -s http://localhost:8017/health | python3 -m json.tool

# Tek hedefin durumu
curl -s http://localhost:8017/runtime/3

# Loglar
docker compose logs -f modbus-outbound

# Backend'in urettigi plan (worker'in gordugu ile ayni)
curl -s -H "X-Service-Token: $INTERNAL_SERVICE_TOKEN" \
     http://localhost:8000/api/v1/internal/modbus-plans | python3 -m json.tool

# Worker'in tazelemede kullandigi son bilinen degerler
curl -s -H "X-Service-Token: $INTERNAL_SERVICE_TOKEN" \
     http://localhost:8000/api/v1/internal/modbus-values | python3 -m json.tool

# Elle Modbus okumasi (mbpoll varsa)
mbpoll -m tcp -a 1 -r 1 -c 10 -t 4 <host>
```

`/health` alanlari: `active_servers`, `deployed_targets`, `messages_processed`,
`points_written`, `bad_quality_count`, `snapshot_enabled` / `snapshot_refreshes`
/ `snapshot_seeded` / `snapshot_refreshed`, hedef bazinda `connected_clients` /
`requests_served` / `rejected_peers` / `updates_applied` / `updates_unmapped`.

**"SCADA sifir goruyor" karar agaci**

| Belirti | Anlami |
|---|---|
| `messages_processed = 0` | NATS'tan hic telemetri gelmiyor (gateway/NATS/abonelik) |
| `updates_unmapped` artiyor, `updates_applied = 0` | telemetri geliyor ama plandaki (cihaz, sinyal) anahtarlariyla eslesmiyor — plan eski ya da cihaz kodlari farkli |
| `updates_uncoercible` artiyor | eslesme var, deger sayiya/bit'e cevrilemiyor |
| `updates_applied = 0`, `snapshot_seeded > 0` | canli akis susuyor, register'lar son bilinen degerlerle besleniyor — veri **var** ama tazelenmiyor |
| ikisi de 0, `snapshot_enabled = false` | tazeleme kapali; degismeyen sinyaller 0 kalir (`MODBUS_SNAPSHOT_REFRESH_SEC`) |
| `updates_applied` artiyor, `requests_served = 0` | degerler yaziliyor, SCADA hic okumuyor (port/baglanti) |
| ikisi de artiyor ama SCADA 0 gosteriyor | istemci tarafi: FC3/FC4, unit id, 40001 taban kaymasi, int16/float32 |

### Guncelleme

```bash
sudo bash update.sh modbus
```

---

## 8. Sinirlar

- **Salt okunur.** SCADA'dan komut gonderme yok.
- **String sinyaller disarida** (30 adet: seri no, firmware surumu vb.) —
  REST/MQTT kanallarindan alinabilir.
- Plan degisikligi worker'a **30 saniyede** yansir (`MODBUS_CATALOG_REFRESH_SEC`).
  Bu sirada yayin kesilmez; yeni plan hazir olunca sunucu tek seferde yenilenir
  ve mevcut degerler yeni registry'ye tasinir.
- Degismeyen sinyaller **en fazla `MODBUS_SNAPSHOT_REFRESH_SEC`** kadar gecikmeyle
  register'a duser (varsayilan 30 sn). Canli akan sinyaller bundan etkilenmez;
  onlar geldigi an yazilir.
- Ayni anda bir cihaz **tek slot** alir; iki farkli Modbus hedefinde ayni cihaz
  farkli adreslerde yayinlanabilir (her hedefin kendi slot tablosu var).
