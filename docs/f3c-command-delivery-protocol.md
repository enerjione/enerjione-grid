# F3C — Komut Teslim Protokolu v1 (`command_delivery_ack_v1`)

Bu belge **iki repo arasindaki tel sozlesmesidir**. Backend yarisi
`enerjione-grid` icinde uygulanmistir; gateway yarisi
`enerjione-grid-dnp3-gateway` icinde uygulanir. Iki taraf da bu belgeye uyar;
tek tarafli degisiklik sessiz bir uyumsuzluk demektir.

---

## 1. Kapatilan ariza

Backend `/pending` yanitini uretirken komutu `sent` isaretliyordu. Iki pencere
aciktı:

* **A** — backend `sent` COMMIT etti, HTTP yaniti gateway'e ULASMADI.
* **B** — gateway yaniti bellege aldi, dayanikli `start_dispatch` yazimindan
  ONCE oldu.

Her ikisinde de sonuc ayni: backend `sent`, gateway defteri bos, cihaz komutu
hic almadi, komut sonsuza kadar `sent` kaliyor. Kayip SESSIZ.

C–H pencereleri (defter yazildiktan sonraki tum cokme noktalari) gateway
defteri sayesinde ZATEN guvenli; bu protokol onlari DEGISTIRMEZ.

---

## 2. Degismez guvenlik kurali

```
Backend -> Gateway  TESLIM       : yeniden denenebilir
Gateway -> Cihaz    CALISTIRMA   : ASLA otomatik yeniden denenmez
Gateway -> Backend  SONUC        : yeniden denenebilir
```

Ayni `command_id` icin ikinci bir fiziksel DNP3 CROB uretilemez. Bunu saglayan
tek mekanizma gateway defterinin `INSERT OR IGNORE` idempotency'sidir; bu
protokol o mekanizmaya DAYANIR, onu degistirmez.

---

## 3. Yetenek bildirimi

Gateway, teslim protokolunu destekledigini **her `/pending` isteginde** su
baslikla bildirir:

```
X-E1-Delivery: <base64url( {"v":1,"epoch":"<uuid>"} )>
```

* `v` — protokol surumu (tamsayi). Su an `1`.
* `epoch` — **defter kimligi**; asagida §6.
* base64url, padding opsiyonel (backend tamamlar). Compact JSON
  (`separators=(",", ":")`).
* Baslik **2048 bayti** asarsa backend onu YOK SAYAR (eski `X-E1-Gateway-Health`
  ile ayni sinir). Govde ~60 bayt olmali.

**Baslik yoksa** gateway "eski" kabul edilir (bkz. §8).

### Neden her istekte, neden saglik basligina binmiyor

`epoch` bayat olamaz. Defter T aninda sifirlanir, saglik ozeti 30 saniyede bir
gider; aradaki pencerede backend ESKI epoch'a guvenerek komut kiralar, gateway
ise bos defterle o komutu YENI sanip **CROB'u tekrarlar**. Yani epoch'un
tazeligi bir performans tercihi degil, cift-calistirma korumasinin ta kendisi.

Ayrica gateway saglik ozetine gozlemlenebilirlik icin sunlari EKLER (zorunlu
degil, kirici degil — yayindaki backend tanimadigi anahtarlari `raw_json`'e
yazar):

```json
{"capabilities": ["command_delivery_ack_v1"], "ledger_epoch": "<uuid>"}
```

---

## 4. Komut teslimi

`GET /gateways/{gateway_code}/pending` yanitindaki her komut, protokol v1
gateway'e sunuldugunda **iki ek alan** tasir:

```json
{
  "id": 1234,
  "device_code": "CIHAZ-A",
  "command": "fault_reset",
  "dnp3_index": 3,
  "op_type": "latch_on",
  "count": 1,
  "on_time_ms": 0,
  "off_time_ms": 0,
  "created_at": "2026-08-14T10:00:00+00:00",
  "delivery_token": "<opak, ~43 karakter, url-safe>",
  "delivery_not_after": "2026-08-14T10:02:00+00:00"
}
```

* `delivery_token` — bu komutun teslim kimligi. **Opak**tir; gateway icerigini
  yorumlamaz. **Loglanmaz.**
* `delivery_not_after` — backend'in turettigi **degismez** son kullanma ani
  (`created_at + COMMAND_MAX_AGE_SEC`), timezone-aware ISO-8601.

**KRITIK:** komut artik `sent` DEGIL, `pending` kalir. `sent`'e ancak ACK
alininca gecer.

### Jeton dondurulmez

Ayni komut icin `delivery_token` **ilk kiralamada uretilir ve ACK gelene kadar
DEGISMEZ**. Kira suresi yenilenebilir, deneme sayaci artabilir; teslim kimligi
sabittir. Boylece gateway defterindeki jeton her zaman gecerli kalir ve
mukerrer ACK sadelesir.

---

## 5. Gateway'in uygulamasi gereken sira

```
HER KOMUT-POLL DONGUSU:
  1. BEKLEYEN ACK'LERI GONDER          <-- ONCE. Bkz. asagidaki not.
  2. GET /pending  (X-E1-Delivery ile)
  3. her komut icin:
       a. command_id defterde VAR MI?
            EVET -> FIZIKSEL CALISTIRMA YOK. Bu mukerrer teslimdir.
                    ACK'i yeniden uret (dayanikli), gerekiyorsa sonucu
                    yeniden teslim et. `delivery_not_after` gecmis olsa BILE
                    yalnizca ACK/sonuc kurtarmasi yapilir.
            HAYIR -> b'ye gec
       b. now > delivery_not_after ?
            EVET -> FIZIKSEL CALISTIRMA YOK. Bayat sonucu DAYANIKLI kaydet
                    ve backend'e bildir (`status="expired"`).
            HAYIR -> c'ye gec
       c. ledger.start_dispatch(command_id, delivery_token)  [SQLite COMMIT]
       d. COMMIT BASARILI ise ACK-bekliyor kaydi olusur
       e. fiziksel DNP3 CROB
       f. record_result
  4. BEKLEYEN SONUCLARI GONDER (mevcut davranis)
```

**Neden ACK once gonderiliyor:** ayni dongude once poll edilirse, gateway'in
kabul ettigi ama ACK'i henuz gitmemis bir komut backend tarafinda hala
`pending` gorunur ve TTL doldugunda sonlandirilabilir. ACK'i one almak bu
yarisi ortadan kaldirir. (Backend yine de bu durumu guvenli sekilde ele alir —
bkz. §7 — ama sirali akis yarisi hic yasatmamak icin gerekli.)

**Kritik degismez:** ACK ancak `start_dispatch` SQLite COMMIT'i tamamlandiktan
SONRA uretilebilir. ACK teslimi basarisiz olursa kayit defterde KALIR ve proses
yeniden baslatildiktan sonra tekrar gonderilir.

---

## 6. Defter kimligi (`epoch`)

`epoch`, gateway defterinin **dayaniklilik kimligidir**: defter dosyasi
yaratildiginda uretilen bir UUID'dir ve defterle ayni omru paylasir.

* Defter bozulup karantinaya alinirsa (mevcut `journal_reset_at` yolu) yeni
  dosya yeni bir `epoch` alir.
* Defter yerinde durdugu surece — proses restart, container restart, container
  recreate, elektrik kesintisi — `epoch` DEGISMEZ.

Backend bir komutu ilk kiralarken o andaki `epoch`'u saklar. Yeniden teslim
degerlendirmesinde epoch farkliysa **otomatik teslim yapilmaz**; komut
`failed` / `result_status="delivery_state_lost"` ile sonlandirilir ve operator
incelemesine birakilir.

Zaman damgasi bu isi yapamazdi: bellekte tutuluyor, restart'ta kayboluyor ve
saat geri alinirsa iki farkli defter ayni damgayi tasiyabiliyor. UUID'de bu
yaris yok.

---

## 7. ACK ucu

```
POST /gateways/{gateway_code}/command-delivery-acks
X-Gateway-Token: <mevcut gateway token'i>
Content-Type: application/json

{"acks": [{"command_id": 1234, "delivery_token": "..."}]}
```

Yanit:

```json
{"accepted": 1, "rejected": 0}
```

* **Yeni auth sistemi YOK** — mevcut `X-Gateway-Token` ve gateway sahiplik
  dogrulamasi kullanilir.
* Jeton `secrets.compare_digest` ile karsilastirilir.
* Baska gateway'e ait `command_id` -> reddedilir (IDOR koruma).
* Yanlis jeton -> reddedilir.
* Mukerrer ACK -> **idempotent no-op**, `accepted` sayilir (gateway kuyrugu
  ilerleyebilsin diye).
* Jeton HICBIR log satirinda yer almaz.
* Parti sinirlari: en fazla 500 ACK / istek.

Basarili ACK: `status = "sent"`, `sent_at = now`. Bu andan itibaren `sent`
**"gateway komutu dayanikli olarak kabul etti"** anlamina gelir.

`4xx` kalici, `5xx`/ag hatasi gecici sayilir (mevcut `command-results`
siniflandirmasiyla ayni).

---

## 8. Eski gateway

`X-E1-Delivery` gonderMEYEN gateway "eski"dir. Backend davranisi
`COMMAND_DELIVERY_ACK_REQUIRED` ile belirlenir:

| Deger | Davranis |
|---|---|
| `true` (varsayilan) | Komut teslim EDILMEZ. `pending` kalir, TTL dolunca `expired` olur. Fail-closed. Hiz sinirli `warning` + `system_event`. |
| `false` | Eski v2.96 davranisi: `pending -> sent` aninda. Gorunur `warning` + `system_event` uretir; sessiz DEGILDIR. |

Saha gecisi icin `false` kullanilabilir; ama bu bir gecis kaldiraci olarak
tasarlandi, kalici bir yapilandirma degil.

---

## 9. Yayin sirasi

```
1. gateway  : yetenek + ACK destegi surumu cikar
2. saha     : gateway'ler yukseltilir
3. dogrulama: gateway_health uzerinden yetenek gorulur
4. backend  : lease/ACK surumu cikar
5. dogrulama: COMMAND_DELIVERY_ACK_REQUIRED=true
```

**Yeni gateway + eski backend calisir:** eski backend `X-E1-Delivery` basligini
tanimaz ve YOK SAYAR (FastAPI bilinmeyen istek basliklarini gormezden gelir),
`delivery_token` gondermez. Gateway jeton gelmeyince ACK uretmez ve bugunku
davranisla calisir.

**Eski gateway + yeni backend calisir:** §8.

---

## 10. Sahada karisik filo

2026-08-14 itibariyla: `GW-001` = 1.6.2, `GW-002` = 1.7.2. Flag-day KABUL
EDILEMEZ; her iki yon de yukaridaki kurallarla calisir.
