<!-- VENDOR EDILMIS BOLUM — KAYNAK: enerjione-grid-dnp3-gateway

     Branch  : fix/health-delta-clock-observability-1.15.1
     Commit  : 34d9ee44b34f74f3b441a4b89d3a4c0f604505f8
     Yol     : docs/HORSTMANN_SMART_MODE.md  (bolum 6b)
     Alindi  : 2026-08-21

     NEDEN VENDOR EDILDI: Grid, Horstmann gateway'lerine
     `DNP3_TIME_SYNC=nonlan` yaziyor. Bu karar bir tercih DEGIL, olculmus
     bir uyumluluk gerekcesine dayaniyor (FC=23 + G50V1 profilde ilan
     edilmis; FC=24 + G50V3 EDILMEMIS). Gerekce Grid deposunda durmazsa,
     ileride "neden lan degil" sorusu cevapsiz kalir ve deger sessizce
     geri alinabilir.
-->

## 6b. Zaman senkronizasyonu — `DNP3_TIME_SYNC=nonlan` (1.15.1)

### Ozet

Horstmann filosunda calisan gateway'lerde **`DNP3_TIME_SYNC=nonlan`** verin.

```yaml
# docker/compose.yml
environment:
  DNP3_TIME_SYNC: "nonlan"
```

### Neden — profil ne ilan ediyor

Resmi *DNP V3.0 Device Profile Document* (Smart Navigator 2.0 / Pole Master)
Implementation Table'inda:

| Ilan edilen | Ilan EDILMEYEN |
|---|---|
| **FC = 23** DELAY MEASUREMENT | FC = 24 RECORD CURRENT TIME |
| **G50V1** Time And Date (FC=2 WRITE, qualifier 07, quantity 1) | G50V3 Last Recorded Time |

### Neden — kutuphane ne gonderiyor (OLCULDU)

`yadnp3 3.2.1.1` ile gercek bir outstation'a karsi olculmustur
(`tests/test_horstmann_conformance.py`, AB/AC testleri):

| `DNP3_TIME_SYNC` | Tel uzerinde gorulen |
|---|---|
| `lan` | **FC=24** RECORD_CURRENT_TIME → WRITE **G50V3** |
| `nonlan` | **FC=23** DELAY_MEASUREMENT → WRITE **G50V1** |
| `none` | (saat hic yazilmaz) |

Yani `lan` seciliyken gateway, cihazin ilan **etmedigi** bir nesneyi
yaziyordu. Cihaz G50V3'u desteklemiyorsa NEED_TIME asserted olsa **bile**
senkronizasyon basarisiz olur ve saat yanlis kalir.

**Asimetri (kararin dayanagi):** G50V1 profilde **acikca destekleniyor**,
G50V3 ise **ilan edilmemis**. Dolayisiyla `nonlan` her iki varsayim altinda
da guvenlidir; `lan` yalnizca dogrulanmamis bir varsayim altinda guvenlidir.

### Neden varsayilan degismedi

Varsayilan **`lan` olarak kaldi**. Varsayilani degistirmek, Horstmann
olmayan **her** kurulumun prosedurunu degistirirdi — bu bir bakim
paketinin isi degildir. Secim acik yapilandirmayla yapilir.

### Neden model bazli otomatik secim YOK

`DeviceConfig`te kanonik bir `model` alani **yoktur**. Ona en yakin alan
`signal_profile`tir ve sozlesmesi acikca *"gateway bu string'i sadece
tasir, anlamlandirmaz"* der. Ona bakip prosedur secmek bir **string
heuristic'i** olurdu: backend bir gun profil adini degistirse gateway
**sessizce** yanlis prosedure gecerdi. Bu yuzden secim **acik
yapilandirmadir**.

### Fail-closed davranis

* Gecersiz deger (`DNP3_TIME_SYNC=nonlann` gibi) → **gateway acilmaz**.
  1.15.0'a kadar taninmayan **her** deger sessizce `lan` sayiliyordu.
* Istenen prosedur binding'de yoksa → **ERROR** loglanir ve saat
  senkronizasyonu **kapatilir**; baska bir prosedure **dusulmez**. Yanlis
  prosedurle saat yazmak, hic yazmamaktan kotudur: operator "senkronize"
  sanir.
* `off` / `disabled` takma adlari geriye uyumluluk icin `none`a normalize
  edilir.

### Degismeyenler

* **Zorla senkronizasyon YOK.** Master yalnizca cihaz **IIN1.4 (NEED_TIME)**
  assert ettiginde saat yazar. Saat yanlis olup NEED_TIME gelmiyorsa
  gateway yalnizca **gorunur kilar** (`device_clock_status = "invalid"`).
* **ClockGuard degismedi.** Gateway kendi saatinden emin degilse (RTC/NTP
  dogrulanmamis) istense bile yazmaz.
* **Smart sessizlik degismezi degismedi** (§5c): periyodik tarama yok,
  60sn link keepalive yok, modem uyandirma yok.

### Saha dogrulamasi — HENUZ YAPILMADI

Fiziksel Horstmann uzerinde **dogrulanmamistir**. Kabul icin PCAP'te
sunlarin **hepsi** gorulmelidir:

1. Cihazdan `IIN1.4 = 1` (NEED_TIME),
2. Gateway'den **FC=23** DELAY MEASUREMENT,
3. Gateway'den **G50V1** WRITE,
4. Cihaz RTC'sinin duzelmesi (or. 2066 → 2026),
5. Sonraki yanitlarda `IIN1.4 = 0`.

---

