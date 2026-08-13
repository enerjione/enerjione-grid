"""modbus-outbound duman testleri — harici bagimlilik olmadan calisir.

`python -m tests.test_smoke` ile dogrudan kosturulabilir (pytest sart degil);
CI/gelistirici makinesinde pymodbus/nats kurulu olmasa da calisir.

Kapsam:
  * codec: int16 olcekleme/kirpma, float32 word sirasi, uint32 sayac
  * registry: plan -> store yerlestirme, bit/register guncelleme
  * server: PDU seviyesinde FC1/2/3/4 + hata kodlari
  * uctan uca: gercek TCP soketi uzerinden Modbus istegi/cevabi
"""

from __future__ import annotations

import asyncio
import socket
import struct
import sys
from types import SimpleNamespace

from modbus_outbound import codec
from modbus_outbound.catalog import SnapshotSyncer
from modbus_outbound.consumer import TelemetryConsumer
from modbus_outbound.registry import build_registry_from_plan
from modbus_outbound.server import (
    EXC_GATEWAY_TARGET_FAILED,
    EXC_ILLEGAL_DATA_ADDRESS,
    EXC_ILLEGAL_DATA_VALUE,
    EXC_ILLEGAL_FUNCTION,
    ModbusServerManager,
    ModbusTargetServer,
    handle_pdu,
)

def check(label: str, condition: bool, extra: str = "") -> None:
    """Kontrolu dogrular — BASARISIZLIKTA HATA FIRLATIR.

    Eskiden bu fonksiyon yalnizca bir listeye not dusuyordu ve `main()` sonda
    bakiyordu. Dosya pytest kesfine girince bu sessiz bir tuzaga donustu:
    hicbir test fonksiyonu ASSERT ETMEDIGI icin `test_codec`, `test_registry`
    ve `test_pdu` — icindeki kontrollerin hepsi patlasa bile — pytest'te
    HER ZAMAN YESIL kaliyordu. Yani "3 test geciyor" ciktisi tamamen
    aldaticiydi; regresyon koruma degeri SIFIRDI.
    """
    print(f"  [{'OK  ' if condition else 'FAIL'}] {label} {extra}")
    assert condition, f"{label} {extra}".strip()


# --- Ornek plan (backend'in urettigi formatta) ------------------------------
PLAN = {
    "target_id": 1,
    "target_name": "SCADA-A",
    "mode": "block",
    "value_format": "int16",
    "word_order": "big",
    "listen_host": "127.0.0.1",
    "listen_port": 0,
    "is_active": True,
    "allowed_peers": [],
    "devices": [
        {"device_id": 1, "device_code": "DEV-001", "device_name": "Fider 1",
         "slot_index": 0, "unit_id": 1, "block_start": 0},
        {"device_id": 2, "device_code": "DEV-002", "device_name": "Fider 2",
         "slot_index": 1, "unit_id": 1, "block_start": 100},
    ],
    "points": [
        {"device_code": "DEV-001", "device_name": "Fider 1", "signal_key": "master.actual_voltage",
         "label": "Gerilim", "source": "master", "data_type": "analog", "unit": "V",
         "unit_id": 1, "function": 3, "address": 0, "word_count": 1,
         "scale": 0.1, "offset": 0.0, "manual": False},
        {"device_code": "DEV-001", "device_name": "Fider 1", "signal_key": "master.fault_counter",
         "label": "Sayac", "source": "master", "data_type": "counter", "unit": "",
         "unit_id": 1, "function": 3, "address": 10, "word_count": 2,
         "scale": 1.0, "offset": 0.0, "manual": False},
        {"device_code": "DEV-001", "device_name": "Fider 1", "signal_key": "master.fault_flag",
         "label": "Ariza", "source": "master", "data_type": "binary", "unit": "",
         "unit_id": 1, "function": 2, "address": 3, "word_count": 0,
         "scale": 1.0, "offset": 0.0, "manual": False},
        {"device_code": "DEV-001", "device_name": "Fider 1", "signal_key": "master.reset_cmd",
         "label": "Reset", "source": "master", "data_type": "binary_output", "unit": "",
         "unit_id": 1, "function": 1, "address": 2, "word_count": 0,
         "scale": 1.0, "offset": 0.0, "manual": False},
        {"device_code": "DEV-002", "device_name": "Fider 2", "signal_key": "master.actual_voltage",
         "label": "Gerilim", "source": "master", "data_type": "analog", "unit": "V",
         "unit_id": 1, "function": 3, "address": 100, "word_count": 1,
         "scale": 0.1, "offset": 0.0, "manual": False},
    ],
}


def test_codec() -> None:
    print("\n1) codec")
    check("int16 olcekleme 230.5V scale=0.1 -> 2305",
          codec.encode_int16(230.5, scale=0.1, offset=0.0) == [2305],
          str(codec.encode_int16(230.5, scale=0.1, offset=0.0)))
    check("negatif deger two's complement",
          codec.encode_int16(-5, scale=1.0, offset=0.0) == [0xFFFB],
          hex(codec.encode_int16(-5, scale=1.0, offset=0.0)[0]))
    check("ust sinirda kirpilir (sarma yok)",
          codec.encode_int16(999999, scale=1.0, offset=0.0) == [32767])
    check("alt sinirda kirpilir",
          codec.encode_int16(-999999, scale=1.0, offset=0.0) == [0x8000])
    check("NaN -> 0", codec.encode_int16(float("nan"), scale=1.0, offset=0.0) == [0])
    check("offset uygulanir (real=raw*scale+offset)",
          codec.encode_int16(120.0, scale=1.0, offset=100.0) == [20])

    big = codec.encode_float32(1.0, word_order="big")
    little = codec.encode_float32(1.0, word_order="little")
    check("float32 big = [0x3F80, 0x0000]", big == [0x3F80, 0x0000], str([hex(x) for x in big]))
    check("float32 little word-swap", little == [0x0000, 0x3F80], str([hex(x) for x in little]))
    check("float32 geri cozulur",
          struct.unpack(">f", struct.pack(">HH", *big))[0] == 1.0)

    check("uint32 sayac 70000 -> [1, 4464]",
          codec.encode_uint32(70000) == [1, 4464], str(codec.encode_uint32(70000)))
    check("negatif sayac 0'a kilitlenir", codec.encode_uint32(-5) == [0, 0])

    check("bool 'true' metni", codec.coerce_bool("true") is True)
    check("bool 'OFF' metni", codec.coerce_bool("OFF") is False)
    check("bool 1", codec.coerce_bool(1) is True)
    check("sayi cevrilemezse None", codec.coerce_number("abc") is None)


def test_registry() -> None:
    print("\n2) registry")
    reg = build_registry_from_plan(PLAN)
    check("5 nokta yuklendi", reg.point_count == 5, str(reg.point_count))
    check("tek unit (block modu)", reg.unit_ids() == [1], str(reg.unit_ids()))

    written = reg.update("DEV-001", "master.actual_voltage", 230.5)
    check("analog yazildi", written == 1)
    check("register 0 = 2305", reg.read(1, 3, 0, 1) == [2305], str(reg.read(1, 3, 0, 1)))
    check("FC4 ayni degeri verir (ayna)", reg.read(1, 4, 0, 1) == [2305])

    reg.update("DEV-001", "master.fault_counter", 70000)
    check("sayac 2 register", reg.read(1, 3, 10, 2) == [1, 4464], str(reg.read(1, 3, 10, 2)))

    reg.update("DEV-001", "master.fault_flag", True)
    check("discrete input yazildi", reg.read(1, 2, 3, 1) == [True])
    reg.update("DEV-001", "master.reset_cmd", "on")
    check("coil yazildi", reg.read(1, 1, 2, 1) == [True])

    reg.update("DEV-002", "master.actual_voltage", 400.0)
    check("2. cihaz kendi blogunda", reg.read(1, 3, 100, 1) == [4000], str(reg.read(1, 3, 100, 1)))
    check("1. cihazin degeri bozulmadi", reg.read(1, 3, 0, 1) == [2305])

    before = reg.updates_unmapped
    reg.update("DEV-999", "yok.olmayan", 1)
    check("planda olmayan sinyal sayilir", reg.updates_unmapped == before + 1)

    check("bos adres 0 doner", reg.read(1, 3, 5000, 2) == [0, 0])
    check("bilinmeyen unit -> None", reg.read(99, 3, 0, 1) is None)

    before_unc = reg.updates_uncoercible
    reg.update("DEV-001", "master.actual_voltage", "cevrilemez-metin")
    check("cevrilemeyen deger SAYILIR (eskiden sessizce kayboluyordu)",
          reg.updates_uncoercible == before_unc + 1, str(reg.updates_uncoercible))
    check("cevrilemeyen deger register'i BOZMAZ (onceki deger kalir)",
          reg.read(1, 3, 0, 1) == [2305], str(reg.read(1, 3, 0, 1)))


def test_consumer_kalite_ve_deger_cozumu() -> None:
    """2026-08-07 olayi: kullanici sahada 'Modbus'ta kalite yok, o an okunan
    ne ise o yazilmali, degerler hala gozukmuyor' dedi. Sayaclar da bunu
    dogruladi: binlerce mesaj islendi, 'kotu kalite' diye binlercesi
    atlaniyordu, KALAN iyi-kaliteli olcumlerden BILE hicbiri yazilmamisti.
    Iki ayri kok neden vardi, bu test ikisini de kilitler:
      1) kalite kotu diye yazma ATLANIYORDU (Modbus'ta kalite biti yok —
         Canli Degerler ekrani neyi gosteriyorsa Modbus da onu yazmali).
      2) `value` alani bos, gercek deger `value_string`'te olan mesajlar
         hicbir zaman register'a donmuyordu (sessiz dusme, sayilmiyordu bile).
    """
    print("\n6) TelemetryConsumer._handle_payload")

    reg = build_registry_from_plan(PLAN)
    mgr = ModbusServerManager()
    mgr._servers[1] = SimpleNamespace(registry=reg)  # noqa: SLF001
    consumer = TelemetryConsumer(settings=object(), manager=mgr)  # type: ignore[arg-type]

    consumer._handle_payload({  # noqa: SLF001
        "device_code": "DEV-001", "signal_key": "master.actual_voltage",
        "quality": "bad", "value": 12.5,
    })
    check("kotu kalite ARTIK yazmayi engellemiyor",
          reg.read(1, 3, 0, 1) == [125], str(reg.read(1, 3, 0, 1)))
    check("kotu kalite yine de SAYILIR (teshis icin)", consumer.bad_quality_count == 1)

    reg2 = build_registry_from_plan(PLAN)
    mgr2 = ModbusServerManager()
    mgr2._servers[1] = SimpleNamespace(registry=reg2)  # noqa: SLF001
    consumer2 = TelemetryConsumer(settings=object(), manager=mgr2)  # type: ignore[arg-type]
    consumer2._handle_payload({  # noqa: SLF001
        "device_code": "DEV-002", "signal_key": "master.actual_voltage",
        "quality": "good", "value": None, "value_string": "45.0",
    })
    check("value bos, value_string dolu ise fallback ile yazilir",
          reg2.read(1, 3, 100, 1) == [450], str(reg2.read(1, 3, 100, 1)))


def test_snapshot_tazeleme() -> None:
    """DEGISMEYEN SINYAL: son bilinen deger register'a yazilmali.

    YASANAN SORUN (2026-08-13): SCADA Modbus'tan hicbir deger alamiyordu.
    Worker'in tek besleme kanali canli NATS akisiydi ve o akis ancak cihaz
    YENI OLCUM yayinladiginda akar. Modbus'ta "deger henuz gelmedi" hali
    olmadigi icin yazilmamis her adres 0 doner ve SCADA bunu gercek bir olcum
    sanar. Degismeyen sinyaller (ariza bayragi, nominal degerler), yeniden
    baslatilmis servis (`DeliverPolicy.NEW` -> gecmis oynatilmaz) ve yeni
    kurulan hedefler bu yuzden sonsuza dek 0 gorunuyordu.

    Bu test tazelemenin dort davranisini kilitler:
      1. hic yazilmamis nokta DB'deki son degerle DOLDURULUR (asil kazanc)
      2. daha TAZE canli deger bayat DB satiriyla EZILMEZ
      3. DB satiri gercekten daha yeniyse yazilir (canli akis kacirmis)
      4. planda olmayan satirlar canli akis teshisini (updates_unmapped)
         KIRLETMEZ — ayri sayilir
    """
    print("\n7) Son bilinen deger tazelemesi")
    reg = build_registry_from_plan(PLAN)

    # 1) Hic yazilmamis nokta: DB'deki son deger yazilir.
    sonuc = reg.apply_snapshot([
        {"device_code": "DEV-001", "signal_key": "master.actual_voltage",
         "value": 231.0, "quality": "good",
         "source_timestamp": "2026-08-13T10:00:00+00:00"},
        {"device_code": "DEV-001", "signal_key": "master.fault_flag",
         "value": 1.0, "quality": "good",
         "source_timestamp": "2026-08-13T10:00:00+00:00"},
    ])
    check("ilk tazelemede iki nokta yazildi", sonuc["seeded"] == 2, str(sonuc))
    check("degismeyen analog register'a dustu",
          reg.read(1, 3, 0, 1) == [2310], str(reg.read(1, 3, 0, 1)))
    check("degismeyen bit discrete input'a dustu", reg.read(1, 2, 3, 1) == [True])

    # 2) Canli akis daha YENI bir deger yazdi -> bayat DB satiri ezmemeli.
    reg.update("DEV-001", "master.actual_voltage", 240.0,
               "2026-08-13T10:05:00+00:00")
    sonuc = reg.apply_snapshot([
        {"device_code": "DEV-001", "signal_key": "master.actual_voltage",
         "value": 231.0, "quality": "good",
         "source_timestamp": "2026-08-13T10:00:00+00:00"},
    ])
    check("bayat DB satiri atlandi", sonuc["stale"] == 1, str(sonuc))
    check("taze canli deger korundu",
          reg.read(1, 3, 0, 1) == [2400], str(reg.read(1, 3, 0, 1)))

    # 3) DB daha yeni (canli akis o olcumu kacirmis) -> yazilir.
    sonuc = reg.apply_snapshot([
        {"device_code": "DEV-001", "signal_key": "master.actual_voltage",
         "value": 250.0, "quality": "good",
         "source_timestamp": "2026-08-13T10:09:00+00:00"},
    ])
    check("daha yeni DB satiri yazildi", sonuc["refreshed"] == 1, str(sonuc))
    check("register yeni degeri gosteriyor",
          reg.read(1, 3, 0, 1) == [2500], str(reg.read(1, 3, 0, 1)))

    # 4) Planda olmayan satir canli akis teshisini kirletmemeli.
    unmapped_once = reg.updates_unmapped
    sonuc = reg.apply_snapshot([
        {"device_code": "DEV-001", "signal_key": "master.serial_number",
         "value": None, "value_string": "SN-42", "quality": "good",
         "source_timestamp": "2026-08-13T10:10:00+00:00"},
    ])
    check("planda olmayan satir ayri sayilir", sonuc["unmapped"] == 1, str(sonuc))
    check("updates_unmapped (canli akis teshisi) BOZULMADI",
          reg.updates_unmapped == unmapped_once, str(reg.updates_unmapped))

    # Damgasiz canli yazim da korunmali: "su an gorduk" kabul edilir, DB'den
    # gelen eski bir satir uzerine yazamaz.
    reg2 = build_registry_from_plan(PLAN)
    reg2.update("DEV-002", "master.actual_voltage", 400.0)  # damga YOK
    sonuc = reg2.apply_snapshot([
        {"device_code": "DEV-002", "signal_key": "master.actual_voltage",
         "value": 100.0, "quality": "good",
         "source_timestamp": "2020-01-01T00:00:00Z"},
    ])
    check("damgasiz canli deger de bayat satirla ezilmez",
          reg2.read(1, 3, 100, 1) == [4000], str(reg2.read(1, 3, 100, 1)))
    check("bozuk/eksik damga tazelemeyi dusurmuyor", sonuc["stale"] == 1, str(sonuc))

    # value ve value_string ikisi de bos -> 0 YAZILMAZ ("olcum sifirlandi"
    # gibi okunurdu).
    reg3 = build_registry_from_plan(PLAN)
    sonuc = reg3.apply_snapshot([
        {"device_code": "DEV-001", "signal_key": "master.actual_voltage",
         "value": None, "value_string": None, "quality": "bad",
         "source_timestamp": "2026-08-13T10:00:00+00:00"},
    ])
    check("bos deger icin register'a 0 yazilmaz", sonuc["seeded"] == 0, str(sonuc))

    # Manager seviyesi: birden fazla hedefe ayni satirlar uygulanir.
    mgr = ModbusServerManager()
    mgr._servers[1] = SimpleNamespace(registry=build_registry_from_plan(PLAN))  # noqa: SLF001
    mgr._servers[2] = SimpleNamespace(registry=build_registry_from_plan(PLAN))  # noqa: SLF001
    toplam = mgr.apply_snapshot([
        {"device_code": "DEV-001", "signal_key": "master.actual_voltage",
         "value": 231.0, "quality": "good",
         "source_timestamp": "2026-08-13T10:00:00+00:00"},
    ])
    check("iki hedefin ikisine de uygulandi",
          toplam["targets"] == 2 and toplam["seeded"] == 2, str(toplam))


def test_snapshot_syncer_artimli_cekim() -> None:
    """Tazeleme dongusu: ilk tur TAM, sonrasi ARTIMLI, periyodik TAM tur.

    NEDEN ONEMLI: 600 cihazda tam liste ~115.000 satirdir. Her 30 saniyede
    tam cekmek, `/signals/live` ucunda backend'i OOM'a goturen desenin
    (istek basina yuz megabaytlik yanit) aynisi olurdu. Ama artimli cekim de
    tek basina yeterli degil: esik `updated_at`e dayanir ve sunucu saati
    GERI alinirsa (NTP adimi) esigin altinda kalan satirlar bir daha hic
    gelmez — bu yuzden periyodik tam tur sart.
    """
    print("\n8) SnapshotSyncer artimli cekim")

    cagrilar: list[str | None] = []

    class _Sahte:
        """CatalogClient yerine: hangi `since` ile cagrildigini kaydeder."""

        def fetch_values(self, since=None):  # noqa: ANN001
            cagrilar.append(since)
            return {
                "values": [
                    {"device_code": "DEV-001",
                     "signal_key": "master.actual_voltage",
                     "value": 231.0, "quality": "good",
                     "source_timestamp": "2026-08-13T10:00:00+00:00"},
                ],
                "max_updated_at": f"2026-08-13T10:00:{len(cagrilar):02d}+00:00",
                "full": since is None,
            }

    mgr = ModbusServerManager()
    mgr._servers[1] = SimpleNamespace(registry=build_registry_from_plan(PLAN))  # noqa: SLF001
    syncer = SnapshotSyncer(catalog=_Sahte(), manager=mgr, refresh_sec=30)

    async def _turlar(adet: int) -> None:
        for _ in range(adet):
            await syncer.tick()

    asyncio.run(_turlar(3))
    check("ilk tur TAM (since yok)", cagrilar[0] is None, str(cagrilar[0]))
    check("ikinci tur ARTIMLI (backend'in verdigi esikle)",
          cagrilar[1] == "2026-08-13T10:00:01+00:00", str(cagrilar[1]))
    check("esik her turda ilerliyor",
          cagrilar[2] == "2026-08-13T10:00:02+00:00", str(cagrilar[2]))
    check("tam tur sayaci yalnizca ilk turu saydi",
          syncer.full_refreshes == 1, str(syncer.full_refreshes))

    # TAM_TUR_PERIYODU'na gelince esik sifirlanmali (saat geri alinmasi
    # senaryosunun kendiliginden onarimi).
    asyncio.run(_turlar(SnapshotSyncer.TAM_TUR_PERIYODU - 3 + 1))
    check("periyot dolunca yeniden TAM tur cekildi",
          syncer.full_refreshes == 2, str(syncer.full_refreshes))

    # Plan degisimi: esik sifirlanir, cunku adresler kaymis olabilir ve
    # butun noktalarin yeniden yazilmasi gerekir.
    syncer.request_refresh()
    asyncio.run(_turlar(1))
    check("plan degisiminde esik sifirlandi (TAM tur)",
          cagrilar[-1] is None, str(cagrilar[-1]))

    # Hedef ayaga kalkmadiysa hic cekim yapilmamali (bos yere on binlerce
    # satir tasima yok).
    bos_mgr = ModbusServerManager()
    bos_cagri: list[str | None] = []

    class _Sayan:
        def fetch_values(self, since=None):  # noqa: ANN001
            bos_cagri.append(since)
            return {"values": [], "max_updated_at": None, "full": True}

    bos = SnapshotSyncer(catalog=_Sayan(), manager=bos_mgr, refresh_sec=30)
    asyncio.run(bos.tick())
    check("hedef yokken deger cekilmiyor", bos_cagri == [], str(bos_cagri))

    # refresh_sec=0 -> dongu hic kurulmaz.
    kapali = SnapshotSyncer(catalog=_Sayan(), manager=mgr, refresh_sec=0)
    check("refresh_sec=0 tazelemeyi kapatir", kapali.enabled is False)
    asyncio.run(kapali.run_forever())
    check("kapali dongu hic cekim yapmadan doner", bos_cagri == [], str(bos_cagri))


def test_pdu() -> None:
    print("\n3) PDU isleme")
    reg = build_registry_from_plan(PLAN)
    reg.update("DEV-001", "master.actual_voltage", 230.5)
    reg.update("DEV-001", "master.fault_flag", True)

    # FC3: 1 register oku
    resp = handle_pdu(reg, 1, struct.pack(">BHH", 3, 0, 1))
    check("FC3 cevap formati", resp == struct.pack(">BBH", 3, 2, 2305), resp.hex())

    # FC4: ayni icerik
    resp4 = handle_pdu(reg, 1, struct.pack(">BHH", 4, 0, 1))
    check("FC4 ayna", resp4 == struct.pack(">BBH", 4, 2, 2305), resp4.hex())

    # FC2: discrete input, adres 3 -> 4 bit oku (0,1,2,3) -> son bit set
    resp2 = handle_pdu(reg, 1, struct.pack(">BHH", 2, 0, 4))
    check("FC2 bit paketleme", resp2 == bytes([2, 1, 0b1000]), resp2.hex())

    # FC1: coil adres 2
    resp1 = handle_pdu(reg, 1, struct.pack(">BHH", 1, 0, 3))
    check("FC1 coil (yazilmamis -> 0)", resp1 == bytes([1, 1, 0b000]), resp1.hex())

    # Yazma reddi
    for fc in (5, 6, 15, 16):
        r = handle_pdu(reg, 1, struct.pack(">BHH", fc, 0, 1))
        check(f"FC{fc} yazma reddedildi",
              r == bytes([fc | 0x80, EXC_ILLEGAL_FUNCTION]), r.hex())

    # Bilinmeyen fonksiyon
    r = handle_pdu(reg, 1, struct.pack(">BHH", 99, 0, 1))
    check("bilinmeyen FC -> illegal function", r == bytes([99 | 0x80, EXC_ILLEGAL_FUNCTION]))

    # Sayi sinirlari
    r = handle_pdu(reg, 1, struct.pack(">BHH", 3, 0, 126))
    check("FC3 126 register -> illegal data value",
          r == bytes([3 | 0x80, EXC_ILLEGAL_DATA_VALUE]))
    r = handle_pdu(reg, 1, struct.pack(">BHH", 3, 0, 0))
    check("FC3 0 register -> illegal data value",
          r == bytes([3 | 0x80, EXC_ILLEGAL_DATA_VALUE]))
    r = handle_pdu(reg, 1, struct.pack(">BHH", 1, 0, 2001))
    check("FC1 2001 bit -> illegal data value",
          r == bytes([1 | 0x80, EXC_ILLEGAL_DATA_VALUE]))
    check("FC3 125 register kabul", handle_pdu(reg, 1, struct.pack(">BHH", 3, 0, 125))[0] == 3)
    check("FC1 2000 bit kabul", handle_pdu(reg, 1, struct.pack(">BHH", 1, 0, 2000))[0] == 1)

    # Adres tasmasi
    r = handle_pdu(reg, 1, struct.pack(">BHH", 3, 65535, 2))
    check("adres uzayi tasmasi -> illegal data address",
          r == bytes([3 | 0x80, EXC_ILLEGAL_DATA_ADDRESS]))

    # Bilinmeyen unit
    r = handle_pdu(reg, 77, struct.pack(">BHH", 3, 0, 1))
    check("bilinmeyen unit -> gateway hatasi",
          r == bytes([3 | 0x80, EXC_GATEWAY_TARGET_FAILED]))

    # Kisa PDU
    r = handle_pdu(reg, 1, bytes([3, 0]))
    check("eksik PDU -> illegal data value", r == bytes([3 | 0x80, EXC_ILLEGAL_DATA_VALUE]))


def _modbus_request(sock: socket.socket, unit: int, pdu: bytes, tid: int = 1) -> bytes:
    frame = struct.pack(">HHHB", tid, 0, len(pdu) + 1, unit) + pdu
    sock.sendall(frame)
    header = sock.recv(7)
    length = struct.unpack(">H", header[4:6])[0]
    body = b""
    while len(body) < length - 1:
        body += sock.recv(length - 1 - len(body))
    return header + body


def test_tcp_end_to_end() -> None:
    """Dosyadaki EN DEGERLI test — ve pytest altinda HIC KOSMUYORDU.

    `async def` idi; pakette pytest-asyncio olmadigi icin pytest bunu
    "async def functions are not natively supported" ile BASARISIZ sayiyordu
    (yani gercek TCP yolu, blok okuma, yazma reddi ve IP allowlist hicbir
    zaman dogrulanmiyordu). Yeni bir bagimlilik eklemek yerine dongusu
    burada aciliyor — dosyanin geri kalani zaten stdlib.
    """
    asyncio.run(_tcp_end_to_end())


async def _tcp_end_to_end() -> None:
    print("\n4) Gercek TCP uzerinden uctan uca")
    reg = build_registry_from_plan(PLAN)
    reg.update("DEV-001", "master.actual_voltage", 230.5)
    reg.update("DEV-002", "master.actual_voltage", 400.0)

    # Port 0 = isletim sistemi bos port versin.
    server = ModbusTargetServer(
        target_id=1, name="test", host="127.0.0.1", port=0, registry=reg,
    )
    await server.start()
    port = server._server.sockets[0].getsockname()[1]  # noqa: SLF001
    print(f"     test sunucusu 127.0.0.1:{port}")

    loop = asyncio.get_running_loop()

    def _client_calls() -> dict:
        out = {}
        with socket.create_connection(("127.0.0.1", port), timeout=5) as sock:
            resp = _modbus_request(sock, 1, struct.pack(">BHH", 3, 0, 1), tid=0x1234)
            out["voltage"] = resp
            # Ayni baglantida ikinci istek (persistent connection)
            resp2 = _modbus_request(sock, 1, struct.pack(">BHH", 3, 100, 1), tid=0x1235)
            out["dev2"] = resp2
            # Blok okuma: cihaz 1'in tum blogu tek istekte
            resp3 = _modbus_request(sock, 1, struct.pack(">BHH", 3, 0, 100), tid=0x1236)
            out["block"] = resp3
            # Yazma denemesi
            resp4 = _modbus_request(sock, 1, struct.pack(">BHH", 6, 0, 1), tid=0x1237)
            out["write"] = resp4
        return out

    result = await loop.run_in_executor(None, _client_calls)

    tid, pid, length, unit = struct.unpack(">HHHB", result["voltage"][:7])
    check("transaction id aynen doner", tid == 0x1234, hex(tid))
    check("protocol id 0", pid == 0)
    check("unit id aynen doner", unit == 1)
    check("uzunluk alani dogru", length == len(result["voltage"]) - 6, str(length))
    check("gerilim degeri dogru", result["voltage"][7:] == struct.pack(">BBH", 3, 2, 2305),
          result["voltage"][7:].hex())
    check("2. cihaz ayni baglantidan okundu",
          result["dev2"][7:] == struct.pack(">BBH", 3, 2, 4000), result["dev2"][7:].hex())
    check("100 register blok okumasi", len(result["block"]) == 7 + 2 + 200,
          str(len(result["block"])))
    check("blok icinde dogru deger", result["block"][9:11] == struct.pack(">H", 2305))
    check("yazma TCP uzerinden de reddedildi",
          result["write"][7:] == bytes([6 | 0x80, EXC_ILLEGAL_FUNCTION]),
          result["write"][7:].hex())
    check("istek sayaci arttI", server.requests_served == 4, str(server.requests_served))

    await server.stop()

    print("\n5) IP allowlist")
    reg2 = build_registry_from_plan(PLAN)
    guarded = ModbusTargetServer(
        target_id=2, name="guarded", host="127.0.0.1", port=0, registry=reg2,
        allowed_peers=("10.0.0.5",),
    )
    await guarded.start()
    gport = guarded._server.sockets[0].getsockname()[1]  # noqa: SLF001

    def _blocked_call() -> bool:
        try:
            with socket.create_connection(("127.0.0.1", gport), timeout=3) as sock:
                sock.sendall(struct.pack(">HHHB", 1, 0, 6, 1) + struct.pack(">BHH", 3, 0, 1))
                return sock.recv(16) == b""  # baglanti kapatildi -> bos okuma
        except (ConnectionError, OSError):
            return True

    blocked = await loop.run_in_executor(None, _blocked_call)
    check("listede olmayan IP reddedildi", blocked)
    check("red sayaci arttI", guarded.rejected_peers == 1, str(guarded.rejected_peers))
    await guarded.stop()


def main() -> int:
    """pytest'siz calistirma yolu (`python -m tests.test_smoke`).

    `check` artik ilk basarisizlikta hata firlattigi icin burada yakalayip
    anlamli bir cikis kodu donuyoruz.
    """
    try:
        test_codec()
        test_registry()
        test_consumer_kalite_ve_deger_cozumu()
        test_snapshot_tazeleme()
        test_snapshot_syncer_artimli_cekim()
        test_pdu()
        test_tcp_end_to_end()
    except AssertionError as exc:
        print(f"\n!! BASARISIZ: {exc}")
        return 1
    print("\nTum kontroller basarili.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
