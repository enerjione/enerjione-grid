# Modbus TCP Outbound

Sahadaki sinyalleri Modbus TCP ile dis SCADA'ya yayinlar. IEC 104 gibi
**ayri bir servis** olarak calisir (`modbus-outbound`): SCADA'nin tarama hizi
backend'i veya telemetri akisini etkilemez.

```
tag-engine → NATS (telemetry.normalized) → modbus-outbound (bellek) → SCADA okumasi
                                                ▲
                                    backend /internal/modbus-plans (adres plani)
```

SCADA istedigi hizda okur; her okuma bellekten cevaplanir, DB'ye veya
backend'e hic dokunulmaz.

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

Modbus'ta kalite biti yoktur. Bozuk kaliteli olcum geldiginde **son iyi deger
korunur**; 0 yazmak SCADA'da "gerilim sifira dustu" gibi gercek bir olay gibi
gorunur ve yanlis alarm uretir.

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

## 6. Tanilama

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

# Elle Modbus okumasi (mbpoll varsa)
mbpoll -m tcp -a 1 -r 1 -c 10 -t 4 <host>
```

`/health` alanlari: `active_servers`, `deployed_targets`, `messages_processed`,
`points_written`, `skipped_bad_quality`, hedef bazinda `connected_clients` /
`requests_served` / `rejected_peers` / `updates_unmapped`.

`updates_unmapped` surekli artiyorsa: telemetri geliyor ama planda karsiligi
yok — cihaz plana girmemis olabilir (kapasite dolu) veya sinyal Modbus'a dahil
edilmeyen bir tip (string).

### Guncelleme

```bash
sudo bash update.sh modbus
```

---

## 7. Sinirlar

- **Salt okunur.** SCADA'dan komut gonderme yok.
- **String sinyaller disarida** (30 adet: seri no, firmware surumu vb.) —
  REST/MQTT kanallarindan alinabilir.
- Plan degisikligi worker'a **30 saniyede** yansir (`MODBUS_CATALOG_REFRESH_SEC`).
  Bu sirada yayin kesilmez; yeni plan hazir olunca sunucu tek seferde yenilenir
  ve mevcut degerler yeni registry'ye tasinir.
- Ayni anda bir cihaz **tek slot** alir; iki farkli Modbus hedefinde ayni cihaz
  farkli adreslerde yayinlanabilir (her hedefin kendi slot tablosu var).
