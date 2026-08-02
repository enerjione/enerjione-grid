"""Filo seviyesi kopma uyarisi.

NE KORUNUYOR
------------
1. YANLIS ALARM URETMEMEK. Bu kural yanlis calisirsa her gateway
   guncellemesinde ve her kucuk filoda uyari yagar; bildirim merkezi
   kullanilamaz hale gelir ve GERCEK uyarilar bu yiginda kaybolur. Testlerin
   cogu "tetiklenMEmeli" tarafinda.

2. TEK BILDIRIM. Tarama periyodik; her turda gondermek saha ekibi sorunu
   cozene kadar dakikada bir bildirim demekti.

3. CIHAZ DURUMUNU ETKILEMEMEK. Uyari kurali bir kolayliktir; cihaz
   haberlesme durumu SCADA'nin kendisidir. Uyaridaki bir hata onu
   dusurmemeli.
"""

from __future__ import annotations

import app.services.gateway_fleet_alarm as fa


class _SahteSatir:
    def __init__(self, kod: str, toplam: int | None, kayip: int | None) -> None:
        self.gateway_code = kod
        self.devices_total = toplam
        self.devices_lost = kayip


# --------------------------------------------------------------------------
# Esik karari — asil deger burada
# --------------------------------------------------------------------------


def test_yarisindan_fazlasi_kopukse_bozuk_sayilir() -> None:
    assert fa.degraded(10, 6, 0.5) is True


def test_tam_yarisi_kopukse_bozuk_SAYILMAZ() -> None:
    """Esik `>` ile karsilastiriliyor, `>=` ile degil.

    10 cihazin 5'i kopuk olmasi sik gorulen bir durum (yarim saha bakimda).
    `>=` olsaydi bu da uyari uretirdi.
    """
    assert fa.degraded(10, 5, 0.5) is False


def test_kucuk_filo_disarida() -> None:
    """2 cihazli gateway'de tek kopma %50 eder — "filo coktu" demek degil."""
    assert fa.degraded(2, 2, 0.5) is False
    assert fa.degraded(3, 3, 0.5) is False
    # Esik filo boyutuna ulasinca kural devreye girer.
    assert fa.degraded(fa.MIN_FLEET_SIZE, fa.MIN_FLEET_SIZE, 0.5) is True


def test_hic_kopuk_yoksa_bozuk_degil() -> None:
    assert fa.degraded(100, 0, 0.5) is False
    assert fa.degraded(100, None, 0.5) is False


def test_toplam_bilinmiyorsa_bozuk_degil() -> None:
    """Sayim gelmemisse tahmin YURUTULMEZ.

    `total=None` iken "kopuk" demek, veri yoklugunu ariza sanmaktir.
    """
    assert fa.degraded(None, 50, 0.5) is False
    assert fa.degraded(0, 0, 0.5) is False


def test_esik_ayarlanabilir() -> None:
    assert fa.degraded(10, 8, 0.9) is False
    assert fa.degraded(10, 10, 0.9) is True


# --------------------------------------------------------------------------
# Ayar okuma — bozuk env sistemi dusurmemeli
# --------------------------------------------------------------------------


def test_bozuk_oran_ayari_varsayilana_duser(monkeypatch) -> None:
    for bozuk in ("abc", "0", "-1", "1.5", ""):
        monkeypatch.setenv("GATEWAY_FLEET_LOST_RATIO", bozuk)
        assert fa._lost_ratio() == fa.DEFAULT_LOST_RATIO, bozuk


def test_gecerli_oran_ayari_okunur(monkeypatch) -> None:
    monkeypatch.setenv("GATEWAY_FLEET_LOST_RATIO", "0.8")
    assert fa._lost_ratio() == 0.8


def test_sure_ayarinin_alt_siniri_var(monkeypatch) -> None:
    """Cok kisa sure, gateway yeniden baslatmalarinda yanlis alarm uretirdi."""
    monkeypatch.setenv("GATEWAY_FLEET_SUSTAIN_SEC", "5")
    assert fa._sustain_sec() >= 60.0


# --------------------------------------------------------------------------
# Sure sarti ve tek-bildirim
# --------------------------------------------------------------------------


class _SahteDB:
    """check_once'in dokundugu kadarini taklit eder."""

    def __init__(self, satirlar, notified: bool = False) -> None:
        self._satirlar = satirlar
        self.notified = notified
        self.commit_sayisi = 0
        self.silinen = 0

    def scalars(self, _stmt):
        db = self

        class _S:
            def all(self_inner):
                return db._satirlar

            def unique(self_inner):
                return self_inner

        return _S()

    def scalar(self, _stmt):
        return 1 if self.notified else None

    def delete(self, _obj):
        self.silinen += 1

    def commit(self):
        self.commit_sayisi += 1


def test_esik_asilir_asilmaz_UYARI_GITMEZ(monkeypatch) -> None:
    """Ilk turda sure sarti dolmadigi icin bildirim olmamali.

    Gateway yeniden baslatildiginda ya da toplu config yenilemesinde cihazlar
    kisa sure `lost` gorunur; anlik esik asimi uyari uretseydi her
    guncellemede yanlis alarm giderdi.
    """
    fa._degraded_since.clear()
    monkeypatch.setattr(fa, "_hedef_kullanicilar", lambda db: ["muh"])
    cagrildi = []
    monkeypatch.setattr(fa, "create_notification_hedefli", lambda *a, **k: cagrildi.append(1))

    db = _SahteDB([_SahteSatir("GW-1", 10, 9)])
    assert fa.check_once(db) == 0
    assert cagrildi == []


def test_sure_dolunca_uyari_gider_ve_SADECE_BIR_KEZ(monkeypatch) -> None:
    fa._degraded_since.clear()
    monkeypatch.setattr(fa, "_hedef_kullanicilar", lambda db: ["muh"])
    monkeypatch.setattr(fa, "record_event", lambda *a, **k: None)
    cagrildi = []
    monkeypatch.setattr(
        fa, "create_notification_hedefli", lambda db, **k: cagrildi.append(k)
    )

    # Bozulma 10 dakika once baslamis gibi davran.
    sahte_saat = [1000.0]

    class _SahteSaat:
        @staticmethod
        def monotonic() -> float:
            return sahte_saat[0]

    monkeypatch.setattr(fa, "time", _SahteSaat)

    db = _SahteDB([_SahteSatir("GW-1", 10, 9)])
    assert fa.check_once(db) == 0  # sayac basladi

    sahte_saat[0] += 600.0
    assert fa.check_once(db) == 1
    assert len(cagrildi) == 1
    assert cagrildi[0]["oran"] == 90

    # Ikinci turda `system_events` isareti var -> tekrar gitmez.
    db.notified = True
    assert fa.check_once(db) == 0
    assert len(cagrildi) == 1


def test_filo_duzelince_isaret_temizlenir_yeni_donem_uyarabilir(monkeypatch) -> None:
    """Bozulma-duzelme-bozulma: ikinci donem YENIDEN uyarmali.

    Isaret temizlenmeseydi bir gateway omru boyunca yalnizca bir kez uyarirdi.
    """
    fa._degraded_since.clear()
    monkeypatch.setattr(fa, "_hedef_kullanicilar", lambda db: ["muh"])

    db = _SahteDB([_SahteSatir("GW-1", 10, 0)], notified=True)
    fa.check_once(db)
    assert db.silinen == 1, "duzelmis filoda 'gonderildi' isareti silinmeli"
    assert "GW-1" not in fa._degraded_since


def test_saglik_satiri_kaybolan_gateway_sayaci_sizdirmaz(monkeypatch) -> None:
    """Gateway silinirse sureç içi sayaç da temizlenmeli."""
    fa._degraded_since.clear()
    fa._degraded_since["ESKI-GW"] = 1.0
    monkeypatch.setattr(fa, "_hedef_kullanicilar", lambda db: ["muh"])

    fa.check_once(_SahteDB([_SahteSatir("GW-1", 10, 0)]))
    assert "ESKI-GW" not in fa._degraded_since


# --------------------------------------------------------------------------
# Watchdog'a bagli — uyari cihaz durumunu DUSURMEMELI
# --------------------------------------------------------------------------


def test_uyari_hatasi_cihaz_durumunu_etkilemez() -> None:
    """`check_once` patlarsa `apply_link_states`/`sweep_once` yine kosmali.

    Ayni try blogunda olsalardi bir bildirim hatasi cihaz durumu
    guncellemesini de atlatirdi — yani bir kolaylik ozelligi yuzunden
    cihazlar yanlis renkte kalirdi. AST ile cagrinin KENDI try'inda oldugu
    dogrulaniyor; metin aramasi bunu ayirt edemez.
    """
    import ast
    import inspect
    import textwrap

    from app.services import gateway_staleness_watchdog as w

    # dedent SART: metot kaynagi sinif icinden girintili gelir, ast.parse
    # bunu IndentationError ile reddeder.
    agac = ast.parse(textwrap.dedent(inspect.getsource(w.GatewayStalenessWatchdog._run)))

    def _fleet_cagrisi_iceriyor(dugumler) -> bool:
        """`dugumler` bir LISTE (govde ya da handler listesi); ast.walk dugum ister."""
        return any(
            isinstance(d, ast.Call)
            and isinstance(d.func, ast.Attribute)
            and d.func.attr == "check_once"
            for kok in dugumler
            for d in ast.walk(kok)
        )

    korumali = [
        t
        for t in ast.walk(agac)
        if isinstance(t, ast.Try)
        and _fleet_cagrisi_iceriyor(t.body)
        and not _fleet_cagrisi_iceriyor(t.handlers)
    ]
    assert korumali, "fleet alarm cagrisi kendi try/except'inde degil"

    # O try'in govdesinde SADECE fleet cagrisi olmali; cihaz durumu
    # guncellemeleri disarida kalmali.
    en_ic = min(korumali, key=lambda t: len(ast.dump(t)))
    govde_metni = " ".join(ast.dump(d) for d in en_ic.body)
    assert "apply_link_states" not in govde_metni
    assert "sweep_once" not in govde_metni
