"""Alarm kalite kapisi — "baglanti kopunca ariza gecti" hatasinin testi.

YASANAN HATA (bu testler yazilmadan once CANLIYDI)
--------------------------------------------------
Haberlesmesi kopan bir cihaz icin gateway `comm_lost` kalitesiyle 0.0 basiyor.
Alarm kapatma yollarinin HICBIRI kaliteye bakmiyordu:

    alarm_reconciliation.py : `if last is None or last.value is None: continue`
    alarm-service/main.py   : `if signal_key is None or value_raw is None: return`

Sonuc zinciri:
    cihazla baglanti kopar
    -> gateway comm_lost + 0.0 yayinlar
    -> _evaluate_rule(rule, 0.0) "esik artik saglanmiyor" der
    -> ACIK ARIZA ALARMI KAPANIR
    -> fault_recompute tetiklenir, HARITA YESILE DONER
    -> alarm onaylanmissa KALICI SILINIR

Yani sistem, bir cihazla baglantiyi kaybettigi anda "ariza gecti" diyordu.
Sebekede bu tehlikelidir: SCADA doktrini veri kaybinda SON BILINEN DURUMU
korumaktir.

Bu testler kapiyi kilitler. Kaldirilirsa kirmizi olurlar.
"""

from __future__ import annotations

import pytest

from app.services.tag_engine_service import quality_blocks_alarm


@pytest.mark.parametrize(
    "quality",
    ["comm_lost", "bad", "offline", "invalid", "restart"],
)
def test_bozuk_kaliteler_alarm_degerlendirmesini_ENGELLER(quality):
    assert quality_blocks_alarm(quality) is True, (
        f"'{quality}' engellemiyor — baglanti kopunca acik alarm KAPANIR"
    )


def test_comm_lost_ozellikle_engellenir():
    """Gateway'in haberlesme kopunca bastigi kalite TAM OLARAK budur.

    Ayri bir test cunku eski kara liste {bad, offline, invalid} idi ve
    `comm_lost` YOKTU — yani en sik gerceklesen senaryo kapsanmiyordu.
    """
    assert quality_blocks_alarm("comm_lost") is True


@pytest.mark.parametrize("quality", ["good", "GOOD", " good ", "no_change"])
def test_saglam_kaliteler_engellemez(quality):
    assert quality_blocks_alarm(quality) is False


def test_bilinmeyen_token_ENGELLEMEZ():
    """KARA LISTE semantigi — beyaz liste DEGIL.

    Beyaz liste olsaydi gateway ileride yeni bir kalite adi yayinladiginda
    (orn. B1 ile gelecek `forced`) tum alarm motoru SESSIZCE durabilirdi.
    Bilinmeyen token'da degerlendirme devam etmeli: gurultulu hata, sessiz
    korlukten iyidir.
    """
    assert quality_blocks_alarm("bilinmeyen_yeni_kalite") is False


def test_none_engellemez():
    """Kalite alani hic gelmezse eski davranis korunmali."""
    assert quality_blocks_alarm(None) is False


def test_alarm_service_kara_listesi_backend_ile_AYNI():
    """Iki servis ayri paket; kume elle kopyalandi ve SENKRON kalmali.

    Biri digerinden ayrisirsa alarm bir yolda kapanir digerinde kapanmaz —
    teshisi cok zor, tutarsiz bir davranis olusur.
    """
    import pathlib
    import re

    kok = pathlib.Path(__file__).resolve().parents[3]
    kaynak = (kok / "apps" / "alarm-service" / "alarm_service" / "main.py").read_text(
        encoding="utf-8"
    )
    m = re.search(
        r"_ALARM_BLOCKING_QUALITIES\s*=\s*frozenset\(\s*\{([^}]*)\}", kaynak, re.S
    )
    assert m, "alarm-service icinde _ALARM_BLOCKING_QUALITIES bulunamadi"
    alarm_servisi = {t.strip().strip('"').strip("'") for t in m.group(1).split(",") if t.strip()}

    from app.services.tag_engine_service import ALARM_BLOCKING_QUALITIES

    assert alarm_servisi == set(ALARM_BLOCKING_QUALITIES), (
        f"kumeler ayrismis: alarm-service={sorted(alarm_servisi)} "
        f"backend={sorted(ALARM_BLOCKING_QUALITIES)}"
    )


def test_alarm_service_kapiyi_GERCEKTEN_cagiriyor():
    """`_quality_is_bad` bir zamanlar tanimliydi ama HIC CAGRILMIYORDU.

    Olu kod olarak durmasi, "kalite kontrolu var" izlenimi verip kimsenin
    bakmamasina yol aciyordu. Bu test cagrinin varligini kilitler.
    """
    import pathlib

    kok = pathlib.Path(__file__).resolve().parents[3]
    kaynak = (kok / "apps" / "alarm-service" / "alarm_service" / "main.py").read_text(
        encoding="utf-8"
    )
    assert "if _quality_is_bad(payload):" in kaynak, (
        "_quality_is_bad cagrilmiyor — kalite kapisi olu kod durumunda"
    )
