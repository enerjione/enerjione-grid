"""Ariza sebebi cikarimi — cihazin alarm imzasindan.

Bu kurallar analiz katmaninin temeli. Yanlis bir kural sessizdir: SCADA'da
hicbir sey patlamaz, yalnizca ariza istatistigi yanlis birikir ve aylar sonra
"agac temasi %40" gibi UYDURMA bir bulguya donusur. O yuzden her kural
burada ayri ayri kilitleniyor.
"""

from __future__ import annotations

from app.data.fault_causes import CAUSE_CODES, FAULT_KIND_CODES
from app.services.fault_inference import infer, strip_source


def test_kaynak_oneki_soyulur():
    """Ayni ariza master/sat01/sat02'den gelebilir; kural kaynaktan bagimsiz."""
    assert strip_source("master.overcurrent_tripped") == "overcurrent_tripped"
    assert strip_source("sat01.current_loss") == "current_loss"
    assert strip_source("onexsiz") == "onexsiz"


# ---- Kalicilik CIHAZDAN okunur --------------------------------------------

def test_kalici_ariza_cihazdan_okunur():
    r = infer(active_signals=["master.permanent_fault", "master.overcurrent_tripped"])
    assert r.fault_kind == "permanent"


def test_gecici_ariza_cihazdan_okunur():
    r = infer(active_signals=["master.momentary_fault", "master.overcurrent_tripped"])
    assert r.fault_kind == "transient"


def test_ikisi_birden_aktifse_KALICI_kazanir():
    """Cihaz once gecici gorup tekrar kapama denemis, tutmayinca kalici
    isaretlemistir — sirali olayin SON hali baglayicidir."""
    r = infer(active_signals=["master.momentary_fault", "master.permanent_fault"])
    assert r.fault_kind == "permanent"


def test_bilgi_yoksa_tur_UYDURULMAZ():
    r = infer(active_signals=["master.overcurrent_tripped"])
    assert r.fault_kind is None, "olculmemis bir seye deger atandi"


# ---- Sebep kurallari -------------------------------------------------------

def test_kurcalama_ucuncu_sahis():
    r = infer(active_signals=["master.tamper_detection"])
    assert r.auto_cause_code == "third_party"
    assert "tamper_detection" in r.matched


def test_akim_kaybi_asiri_akim_YOKSA_iletken_kopmasi():
    r = infer(active_signals=["master.current_loss", "master.voltage_loss"])
    assert r.auto_cause_code == "conductor_break"


def test_akim_kaybi_asiri_akim_VARSA_iletken_kopmasi_DEGIL():
    """Kisa devrede once akim FIRLAR; kopmada firlama olmadan kesilir."""
    r = infer(active_signals=["master.current_loss", "master.overcurrent_tripped"])
    assert r.auto_cause_code != "conductor_break"


def test_sicaklik_alarmi_asiri_akim_YOKSA_asiri_yuk():
    r = infer(active_signals=["master.conductor_temperature_alarm"])
    assert r.auto_cause_code == "overload"


def test_sicaklik_alarmi_asiri_akim_VARSA_asiri_yuk_DEGIL():
    """Asiri akim varsa sicaklik SONUCTUR, sebep degil."""
    r = infer(
        active_signals=[
            "master.conductor_temperature_alarm",
            "master.overcurrent_tripped",
        ]
    )
    assert r.auto_cause_code != "overload"


def test_didt_asiri_akim_YOKSA_yuksek_empedans_agac():
    """Akim yavas ve sinirli artar, koruma esigine ulasmaz — tipik agac temasi."""
    r = infer(active_signals=["sat01.delta_i_delta_t_tripped"])
    assert r.auto_cause_code == "tree_contact"
    assert "dogrulanmali" in (r.reason or ""), "oneri kesinmis gibi sunuluyor"


def test_asiri_akimda_sebep_UYDURULMAZ():
    """Yildirim, hayvan, izolator, ucuncu sahis — hepsi ayni sonucu verir.
    Birini secmek analiz katmanini yanlis egitirdi."""
    r = infer(active_signals=["master.overcurrent_tripped"], fault_current_a=820.0)
    assert r.auto_cause_code is None
    assert r.reason and "ayirt edilemez" in r.reason


def test_yalnizca_gerilim_kaybi_hat_arizasi_saymaz():
    r = infer(active_signals=["master.voltage_loss_all_units"])
    assert r.auto_cause_code is None
    assert r.reason and "ust kademe" in r.reason


def test_bos_imza_hicbir_sey_uretmez():
    r = infer(active_signals=[])
    assert r.auto_cause_code is None
    assert r.fault_kind is None
    assert r.fault_direction is None


# ---- Yon -------------------------------------------------------------------

def test_yon_tek_tarafliysa_okunur():
    assert infer(
        active_signals=["master.overcurrent_fault_direction_green_a"]
    ).fault_direction == "green_a"
    assert infer(
        active_signals=["master.delta_i_delta_t_fault_direction_red_b"]
    ).fault_direction == "red_b"


def test_yon_celiskiliyse_NULL():
    """Iki yon birden: bilgi yok. 'unknown' yazmak da bilgi tasimaz."""
    r = infer(
        active_signals=[
            "master.overcurrent_fault_direction_green_a",
            "master.overcurrent_fault_direction_red_b",
        ]
    )
    assert r.fault_direction is None


# ---- Sozlesme --------------------------------------------------------------

def test_uretilen_kodlar_KATALOGDA_var():
    """Katalogda olmayan bir kod uretmek, arayuzde bos etiket demek."""
    imzalar = [
        ["master.tamper_detection"],
        ["master.current_loss"],
        ["master.conductor_temperature_alarm"],
        ["master.delta_i_delta_t_tripped"],
        ["master.overcurrent_tripped"],
        ["master.voltage_loss"],
    ]
    for imza in imzalar:
        r = infer(active_signals=imza)
        if r.auto_cause_code is not None:
            assert r.auto_cause_code in CAUSE_CODES, f"{imza} -> {r.auto_cause_code}"
        if r.fault_kind is not None:
            assert r.fault_kind in FAULT_KIND_CODES


def test_her_oneri_GEREKCE_tasir():
    """Aciklanamayan oneriye operator guvenmez."""
    for imza in (["master.tamper_detection"], ["master.current_loss"]):
        r = infer(active_signals=imza)
        assert r.reason, f"{imza} icin gerekce yok"
        assert r.matched, f"{imza} icin katkida bulunan sinyal listelenmemis"


# ---- FAZ: hangi unite gorduyse o faz ---------------------------------------
#
# Horstmann SN2'nin uc unitesi (master/sat01/sat02) hatta UC AYRI FAZA
# kelepcelenir. Yani kaynak oneki "hangi unite" degil, HANGI FAZ demektir.
# Ilk yazimda onek kural eslestirmesi icin soyuluyordu ve faz bilgisi bu
# sirada tamamen kayboluyordu — bu testler o kaybi engelliyor.

def test_tek_unite_goruyorsa_TEK_FAZ():
    r = infer(active_signals=["sat01.overcurrent_tripped"])
    assert r.phase == "b", "sat01 -> B fazi"
    assert r.faulted_sources == ("sat01",)


def test_uc_unite_de_goruyorsa_UC_FAZ():
    r = infer(
        active_signals=[
            "master.overcurrent_tripped",
            "sat01.overcurrent_tripped",
            "sat02.overcurrent_tripped",
        ]
    )
    assert r.phase == "abc"
    assert r.faulted_sources == ("master", "sat01", "sat02")


def test_iki_unite_KARARLI_siralanir():
    """'ac' her zaman 'ac' olmali — 'ca' olarak birikirse gruplama bolunur."""
    r = infer(
        active_signals=["sat02.current_loss", "master.current_loss"]
    )
    assert r.phase == "ac"


def test_isletme_bayraklari_FAZI_ARIZALI_gostermez():
    """`battery_status` bir ariza degil; o unitenin fazini isaretlememeli."""
    r = infer(active_signals=["sat02.battery_status", "master.overcurrent_tripped"])
    assert r.phase == "a", f"sat02 isletme bayragi faza karisti: {r.phase}"
    assert "sat02" not in r.faulted_sources


def test_faz_esleme_KURULUMA_gore_degistirilebilir():
    """Kelepceyi hangi faza takacagina kurulumcu karar verir; varsayilani
    sabit kabul etmek faz etiketlerini sessizce yanlis biriktirirdi."""
    r = infer(
        active_signals=["master.overcurrent_tripped"],
        source_phase={"master": "c", "sat01": "a", "sat02": "b"},
    )
    assert r.phase == "c"


def test_ariza_sinyali_yoksa_faz_YOK():
    r = infer(active_signals=["master.config_update"])
    assert r.phase is None
    assert r.faulted_sources == ()


def test_tek_faz_gerekcede_DIS_ETKEN_olasiligi_yazar():
    r = infer(active_signals=["sat01.overcurrent_tripped"])
    assert r.reason and "Tek faz" in r.reason and "dis etken" in r.reason


def test_uc_faz_gerekcede_EKIPMAN_olasiligi_yazar():
    r = infer(
        active_signals=[
            "master.overcurrent_tripped",
            "sat01.overcurrent_tripped",
            "sat02.overcurrent_tripped",
        ]
    )
    assert r.reason and "Uc faz" in r.reason


def test_faz_uretilirse_KATALOG_kumesinde():
    from app.data.fault_causes import PHASES

    for imza in (
        ["master.overcurrent_tripped"],
        ["master.current_loss", "sat01.current_loss"],
        ["master.momentary_fault", "sat01.momentary_fault", "sat02.momentary_fault"],
    ):
        r = infer(active_signals=imza)
        if r.phase is not None:
            assert r.phase in PHASES, f"{imza} -> {r.phase} katalogda yok"
