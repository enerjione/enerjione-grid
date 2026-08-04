"""IKI HAT — dijital/durum sinyalleri analog selinin arkasinda beklemesin.

NEDEN BU TESTLER VAR
--------------------
Butun olcumler TEK bogazdan gectigi surece, yuz binlerce analog olcumun
arasindaki ARIZA BAYRAGI da ayni sirada bekliyordu. Ariza gostergesinde
gecikme konfor degil DOGRULUK meselesi: 10 dakika gec gorulen ariza
operasyonel olarak KACIRILMISTIR.

Gercek NATS uzerinde olculen davranis (36.500 analog olcum bekliyorken
ariza bayragi yayinlandi):
    tek hat (onceki davranis) : 30.703 ms
    iki hat  (bu degisiklik)  :     15 ms
Analog hatti HIZLANDIRILMADI — backlog'u aynen 36.500'de kaldi. Veri
atilmiyor, SIRA veriliyor.

Buradaki testler o davranisin dayandigi KURALLARI kilitler; NATS
gerektirmezler.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from app.core.config import settings
from app.services import telemetry_consumer as tc

KOK = Path(__file__).resolve().parents[3]

# tag-engine'in siniflandirma modulu — yalnizca stdlib kullanir, guvenle
# import edilir. Siniflandirma kurallarini KOPYALAMIYORUZ; kopya, iki
# tarafin sessizce ayrisabilecegi anlamina gelirdi.
sys.path.insert(0, str(KOK / "apps" / "tag-engine"))
from tag_engine import katalog  # noqa: E402


def _katalog_kayitlari() -> list[dict]:
    return json.loads(
        (KOK / "apps/backend-api/app/data/horstmann_sn2_signals.json").read_text("utf-8")
    )


@pytest.fixture(autouse=True)
def _temiz_sayaclar():
    tc._hat_stats.clear()
    tc._kayip_isareti.clear()
    tc._son_kayip_denetimi.clear()
    tc._last_backlog_warn_at.clear()
    yield


# ---------------------------------------------------------------- siniflandirma


def test_ariza_belirleyen_sinyaller_ONCELIKLI_hatta():
    """Ariza-gecis gostergesinin ariza belirleyen sinyalleri toplu hatta dusemez.

    Liste burada bir BEKLENTIDIR, siniflandirma kaynagi DEGIL: kural
    katalogdan (`data_type` + `dnp3_class`) cikar. Bu test kuralin o
    beklentiyi karsiladigini dogrular.
    """
    kayitlar = {k["key"]: k for k in _katalog_kayitlari()}
    # EK olarak eslenir (`endswith`), ICERIK olarak DEGIL. Ayrim onemli:
    # `conductor_temperature_alarm` bir ariza DURUMUDUR (binary, Class 1),
    # `conductor_temperature_alarm_threshold` ise o alarmin AYAR DEGERIDIR
    # (analog, Class 2) ve toplu hatta ait. Icerik eslemesi ikisini
    # karistirirdi.
    ariza_ekleri = (
        "permanent_fault",
        "momentary_fault",
        "overcurrent_tripped",
        "delta_i_delta_t_tripped",
        "delta_i_delta_t_pick_up",
        "fault_direction_green_a",
        "fault_direction_red_b",
        "voltage_loss",
        "voltage_loss_all_units",
        "current_loss",
        "communication_failure",
        "battery_status",
        "tamper_detection",
        "conductor_temperature_alarm",
        "momentary_fault_counter",
        "permanent_fault_counter",
        # ANALOG ama ariza belirleyici — asil tuzak burasi.
        "fault_current",
        "fault_duration",
        "last_good_known_current",
    )
    eslesen = [a for a in kayitlar if any(a.endswith(ek) for ek in ariza_ekleri)]
    assert len(eslesen) >= 40, f"beklenen kadar ariza sinyali eslesmedi: {len(eslesen)}"
    yanlis = [
        a for a in eslesen
        if katalog.sinif_hesapla(kayitlar[a]) != katalog.SINIF_ONCELIKLI
    ]
    assert not yanlis, (
        f"ariza belirleyen sinyaller TOPLU hatta dustu: {yanlis} — "
        "ariza gecisi analog selinin arkasinda bekler"
    )


def test_alarm_ESIGI_ile_alarm_DURUMU_karistirilmiyor():
    """`..._alarm` durumdur (oncelikli); `..._alarm_threshold` ayardir (toplu).

    Ayari oncelikli hatta koymak, hattin degerini seyreltirdi: oncelikli hat
    ancak ICINDE yalnizca gercekten acil olan varsa hizli kalir.
    """
    kayitlar = {k["key"]: k for k in _katalog_kayitlari()}
    assert (
        katalog.sinif_hesapla(kayitlar["master.conductor_temperature_alarm"])
        == katalog.SINIF_ONCELIKLI
    )
    assert (
        katalog.sinif_hesapla(kayitlar["master.conductor_temperature_alarm_threshold"])
        == katalog.SINIF_TOPLU
    )


def test_class1_ANALOGLAR_oncelikli_hatta():
    """`fault_current` ANALOG'dur. Saf `data_type` ile bolen bir kural onu
    toplu hatta koyardi: ekranda "ariza var ama akim bilinmiyor" olusurdu."""
    kayitlar = [k for k in _katalog_kayitlari() if k["data_type"] == "analog"]
    class1 = [k for k in kayitlar if "1" in str(k.get("dnp3_class"))]
    assert class1, "katalogda Class 1 analog yok — test anlamsiz"
    for kayit in class1:
        assert katalog.sinif_hesapla(kayit) == katalog.SINIF_ONCELIKLI, (
            f"{kayit['key']} Class 1 ANALOG ama toplu hatta dustu"
        )


def test_yalnizca_class2_ANALOG_toplu_hatta():
    for kayit in _katalog_kayitlari():
        sinif = katalog.sinif_hesapla(kayit)
        if sinif == katalog.SINIF_TOPLU:
            assert kayit["data_type"] == "analog", (
                f"{kayit['key']} analog degil ama toplu hatta dustu"
            )
            assert "2" in str(kayit["dnp3_class"]), (
                f"{kayit['key']} Class 2 degil ama toplu hatta dustu"
            )


def test_BILINMEYEN_her_zaman_oncelikli_hatta():
    """Siniflandiramadigimiz bir sinyali GECIKTIRMEK, gereksiz
    hizlandirmaktan risklidir. Guvenli yon tek yondur."""
    assert katalog.sinif_hesapla(None) == katalog.SINIF_ONCELIKLI
    assert katalog.sinif_hesapla({}) == katalog.SINIF_ONCELIKLI
    # Tipi taninmayan / gelecekte eklenen bir tip
    assert (
        katalog.sinif_hesapla({"data_type": "analog_output", "dnp3_class": "Class 2"})
        == katalog.SINIF_ONCELIKLI
    )
    # dnp3_class okunamiyor ("-" gibi)
    assert (
        katalog.sinif_hesapla({"data_type": "analog", "dnp3_class": "-"})
        == katalog.SINIF_ONCELIKLI
    )
    assert (
        katalog.sinif_hesapla({"data_type": "analog", "dnp3_class": None})
        == katalog.SINIF_ONCELIKLI
    )


def test_KATALOG_YOKKEN_her_sey_oncelikli_hatta():
    """Backend'e ulasilamiyorsa davranis ESKIYE, yani tek hatta duser.

    Bos bir tablo ile toplu hatta dusen bir sinyal olsaydi, backend'in kisa
    bir yeniden baslatmasinda ariza bayraklari sinifsizlasip gecikirdi.
    """
    onbellek = katalog.KatalogOnbellegi(
        base_url="http://127.0.0.1:1", service_token="x", yenileme_sn=60, zaman_asimi_sn=0.1
    )
    assert onbellek.yuklendi is False
    assert onbellek.sinif("master.permanent_fault") == katalog.SINIF_ONCELIKLI
    assert onbellek.sinif("master.current_phase_a") == katalog.SINIF_ONCELIKLI
    assert onbellek.sinif(None) == katalog.SINIF_ONCELIKLI


def test_cekim_basarisiz_olunca_MEVCUT_tablo_KORUNUR():
    """Basarisizlikta tabloyu bosaltmak, tum analog selini oncelikli hatta
    yikmak olurdu — tam da onlemeye calistigimiz tikanma."""
    onbellek = katalog.KatalogOnbellegi(
        base_url="http://127.0.0.1:1", service_token="x", yenileme_sn=60, zaman_asimi_sn=0.1
    )
    onbellek._tablo = {"master.current_phase_a": katalog.SINIF_TOPLU}
    onbellek._yuklendi = True
    assert onbellek.yenile() is False
    assert onbellek.sinif("master.current_phase_a") == katalog.SINIF_TOPLU


def test_siniflandirma_KODA_GOMULU_liste_kullanmiyor():
    """Kuralin kaynagi katalog olmali; sinyal adi listesi olmamali.

    Katalog kullanici tarafindan degistirilebiliyor (Sinyal Katalogu
    ekrani); kodda sabit bir liste, o ekrandan yapilan degisikligi sessizce
    yok sayardi.
    """
    kaynak = (KOK / "apps/tag-engine/tag_engine/katalog.py").read_text("utf-8")
    kod = "\n".join(
        satir for satir in kaynak.splitlines()
        if not satir.lstrip().startswith("#")
    )
    # Docstring'lerde ornek olarak gecebilir; KOD kisminda gecmemeli.
    govde = kod.split('"""')
    calisan_kod = "".join(govde[i] for i in range(0, len(govde), 2))
    for yasak in ("permanent_fault", "fault_current", "momentary_fault"):
        assert yasak not in calisan_kod, (
            f"siniflandirmada koda gomulu sinyal adi var: {yasak}"
        )


# ------------------------------------------------------------------- konular


def test_tag_engine_konuya_SINIF_TOKENI_ekliyor():
    kaynak = (KOK / "apps/tag-engine/tag_engine/main.py").read_text("utf-8")
    assert "KATALOG.sinif(" in kaynak, "tag-engine siniflandirmayi cagirmiyor"
    assert "{processed['source_gateway']}.{sinif}" in kaynak, (
        "konuya sinif token'i eklenmiyor — hat ayrimi calismaz"
    )


def test_oncelikli_filtre_SINIFSIZ_eski_konuyu_da_tasiyor():
    """Guncelleme penceresinde eski tag-engine 4 token'li konuya basar.

    Yalnizca `*.prio` dinlenseydi o mesajlar hicbir filtreye uymaz ve
    SESSIZCE kaybolurdu.
    """
    filtreler = tc._oncelikli_filtreler()
    assert settings.nats_subject_telemetry_normalized_prio in filtreler
    assert settings.nats_subject_telemetry_normalized_legacy in filtreler


def test_filtreler_CAKISMIYOR():
    """Cakisan filtreler ayni mesajin IKI KEZ islenmesi demekti.

    Ayrim token SAYISINDAN geliyor: sinifli konu 5, sinifsiz konu 4 token.
    """
    def token(desen: str) -> int:
        return len(desen.split("."))

    prio = settings.nats_subject_telemetry_normalized_prio
    bulk = settings.nats_subject_telemetry_normalized_bulk
    eski = settings.nats_subject_telemetry_normalized_legacy
    assert token(prio) == token(bulk) == 5
    assert token(eski) == 4
    assert prio != bulk
    # Ayni token sayisinda son token SABIT olmali (wildcard degil).
    assert prio.endswith(".prio") and bulk.endswith(".bulk")
    # Hepsi stream'in subject'i altinda kalmali; aksi halde consumer
    # olusturulamaz ve NATS izin listesi de ayni kok altinda.
    kok = settings.nats_subject_telemetry_normalized.rstrip(">")
    for desen in (prio, bulk, eski):
        assert desen.startswith(kok), f"{desen} stream subject'i disinda"


def test_hat_durablelari_AYRI():
    assert (
        settings.nats_consumer_telemetry_persist_prio
        != settings.nats_consumer_telemetry_persist_bulk
    )
    # Tek hat donemi durable'i ile de karismamali.
    assert settings.nats_consumer_telemetry_persist_normalized not in (
        settings.nats_consumer_telemetry_persist_prio,
        settings.nats_consumer_telemetry_persist_bulk,
    )


def test_idempotency_defteri_ANAHTARI_hatlara_gore_DEGISMIYOR():
    """`CONSUMER_NAME` iki hatta da AYNI kalmali.

    Hat basina ayri bir defter anahtari kullanilsaydi, gecis penceresinde
    geri sarilan bir olcum digger hattin defterinde bulunmaz ve IKINCI KEZ
    yazilirdi. Tek anahtar + `uq_processed_message_consumer_msg` bilesik
    UNIQUE index'i, bir yaris olsa bile ikinci yazimi DB'de reddettirir.
    """
    kaynak = (
        KOK / "apps/backend-api/app/services/telemetry_consumer.py"
    ).read_text("utf-8")
    assert kaynak.count('CONSUMER_NAME = "backend-api.telemetry-persister"') == 1
    assert "consumer_name=CONSUMER_NAME" in kaynak
    # Hat sinifina gore degisen bir consumer_name olmamali.
    assert "consumer_name=f" not in kaynak


# ----------------------------------------------------- tampon tasmasi (sessiz degil)


class _SahteDurum:
    def __init__(self, first_seq: int) -> None:
        self.first_seq = first_seq


class _SahteStreamBilgi:
    def __init__(self, first_seq: int) -> None:
        self.state = _SahteDurum(first_seq)


class _SahteAckFloor:
    def __init__(self, stream_seq: int) -> None:
        self.stream_seq = stream_seq


class _SahteTuketiciBilgi:
    def __init__(self, ack_floor: int) -> None:
        self.ack_floor = _SahteAckFloor(ack_floor)


class _SahteJS:
    def __init__(self, first_seq: int, ack_floor: int) -> None:
        self._first_seq = first_seq
        self._ack_floor = ack_floor

    async def stream_info(self, _ad):
        return _SahteStreamBilgi(self._first_seq)

    async def consumer_info(self, _stream, _durable):
        return _SahteTuketiciBilgi(self._ack_floor)


def _hat(sinif: str) -> tc.Hat:
    return tc.Hat(sinif=sinif, stream="S", durable="d", psub=None)


def _olaylari_yakala(monkeypatch) -> list[dict]:
    olaylar: list[dict] = []
    monkeypatch.setattr(tc, "_olay_yaz", lambda **kw: olaylar.append(kw))
    return olaylar


@pytest.mark.asyncio
async def test_tasma_SESSIZ_KALMAZ_ve_SINIF_yazilir(monkeypatch):
    olaylar = _olaylari_yakala(monkeypatch)
    # Stream'in en eskisi 5001; hat ancak 1000'e kadar ack'lemis ->
    # 4000 mesaj hat onlari GORMEDEN silinmis.
    js = _SahteJS(first_seq=5001, ack_floor=1000)

    await tc._dusen_mesaj_denetimi(js, _hat(tc.SINIF_ONCELIKLI))

    assert len(olaylar) == 1, "tampon tasmasi SESSIZ kaldi"
    olay = olaylar[0]
    assert olay["event_type"] == "telemetry_tampon_tasmasi"
    assert olay["metadata"]["sinif"] == tc.SINIF_ONCELIKLI, "hangi sinif dustu yazilmadi"
    assert olay["metadata"]["dusen_ust_sinir"] == 4000
    assert olay["severity"] == "critical", (
        "ariza belirleyen sinif dustu ama olay uyari seviyesinde kaldi"
    )
    assert "ARIZA BELIRLEYEN" in olay["message"]


@pytest.mark.asyncio
async def test_toplu_hat_tasmasi_UYARI_seviyesinde(monkeypatch):
    """Analoglar da kaydedilir ama ariza sinifi ile ayni agirlikta degil."""
    olaylar = _olaylari_yakala(monkeypatch)
    js = _SahteJS(first_seq=5001, ack_floor=1000)

    await tc._dusen_mesaj_denetimi(js, _hat(tc.SINIF_TOPLU))

    assert len(olaylar) == 1
    assert olaylar[0]["severity"] == "warning"
    assert olaylar[0]["metadata"]["sinif"] == tc.SINIF_TOPLU


@pytest.mark.asyncio
async def test_YETISEN_hatta_yanlis_pozitif_yok(monkeypatch):
    """Yanlis bir "veri kaybi" olayi, gercek olani da inandiriciliktan dusurur."""
    olaylar = _olaylari_yakala(monkeypatch)
    js = _SahteJS(first_seq=1, ack_floor=4000)

    await tc._dusen_mesaj_denetimi(js, _hat(tc.SINIF_ONCELIKLI))

    assert olaylar == []


@pytest.mark.asyncio
async def test_ayni_kayip_IKI_KEZ_raporlanmaz(monkeypatch):
    olaylar = _olaylari_yakala(monkeypatch)
    js = _SahteJS(first_seq=5001, ack_floor=1000)
    hat = _hat(tc.SINIF_ONCELIKLI)

    await tc._dusen_mesaj_denetimi(js, hat)
    tc._son_kayip_denetimi.clear()  # aralik kisitini atla
    await tc._dusen_mesaj_denetimi(js, hat)

    assert len(olaylar) == 1, "ayni kayip tekrar raporlandi — sayac siser"


@pytest.mark.asyncio
async def test_bilgi_okunamazsa_KARAR_VERILMEZ(monkeypatch):
    olaylar = _olaylari_yakala(monkeypatch)

    class _Patlayan:
        async def stream_info(self, _ad):
            raise RuntimeError("ag yok")

        async def consumer_info(self, _s, _d):
            raise RuntimeError("ag yok")

    await tc._dusen_mesaj_denetimi(_Patlayan(), _hat(tc.SINIF_ONCELIKLI))

    assert olaylar == []


# ------------------------------------------------- hat basina backlog uyarisi


def test_backlog_uyarisi_HAT_BASINA_ayri(monkeypatch):
    """Toplu hattin geride kalmasi kabul edilebilir, oncelikli hattin DEGIL.

    Tek birlesik rate-limit olsaydi, toplu hattin gurultusu oncelikli hattin
    uyarisini yutardi.
    """
    monkeypatch.setattr(settings, "telemetry_backlog_warn_threshold", 1_000)
    monkeypatch.setattr(settings, "telemetry_backlog_warn_interval_sec", 300)
    olaylar = _olaylari_yakala(monkeypatch)

    tc._warn_if_backlog_high(tc.SINIF_TOPLU, 50_000)
    tc._warn_if_backlog_high(tc.SINIF_ONCELIKLI, 50_000)

    siniflar = [o["metadata"]["sinif"] for o in olaylar]
    assert siniflar == [tc.SINIF_TOPLU, tc.SINIF_ONCELIKLI], (
        "hatlardan biri digerinin rate-limit'ine takildi"
    )
    seviyeler = {o["metadata"]["sinif"]: o["severity"] for o in olaylar}
    assert seviyeler[tc.SINIF_ONCELIKLI] == "critical"
    assert seviyeler[tc.SINIF_TOPLU] == "warning"


def test_ust_seviye_backlog_EN_KOTU_hatti_gosterir():
    """Sistem Durumu ekranini besleyen alan; ortalama degil EN KOTU olmali."""
    tc._stats_record_batch(sinif=tc.SINIF_TOPLU, size=10, duration=0.1, backlog=90_000, bad=0)
    tc._stats_record_batch(sinif=tc.SINIF_ONCELIKLI, size=10, duration=0.1, backlog=3, bad=0)

    s = tc.get_stats()
    assert s["backlog"] == 90_000
    assert s["hatlar"][tc.SINIF_ONCELIKLI]["backlog"] == 3
    assert s["hatlar"][tc.SINIF_TOPLU]["backlog"] == 90_000
