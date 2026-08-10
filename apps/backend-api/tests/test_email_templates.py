"""E-posta sablonlari — govde SAGLAM ve girdi KACISLI mi?

Mail sessiz bir yuzey: bozuk cikan HTML kimseye hata dondurmez, sahadaki
kisi sadece okunmaz bir mesaj gorur ve kimse haber vermez. Sablonlar
`email_components` primitiflerinden kuruldugu icin bir primitifteki
degisiklik ucunu birden etkiler. Burada dort risk kilitleniyor:

  1. HAM HTML SIZINTISI — cihaz adi / kural adi kullanici girdisidir.
     Kacislanmazsa mail govdesine isaretleme enjekte edilir.
  2. EKSIK VERIYLE COKME — alanlarin cogu opsiyonel; hepsi None iken de
     mail gonderilebilir olmali (alarm her kosulda cikacak).
  3. ISLENMEMIS SABLON ARTIGI — `{...}` kalintisi ya da "None" dizgisi
     govdeye basilirsa alici anlamsiz metin gorur.
  4. HARITA SLOTU — inline harita `cid:` referansiyla SABLONUN icinde
     durmali; eskiden `</body>` string degistirmesiyle kartin ve
     altbilginin ALTINA dusuyordu.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.services.email_templates import (
    PRODUCT_NAME,
    render_alarm_email,
    render_assignment_email,
    render_fault_email,
)

ZAMAN = datetime(2026, 8, 10, 11, 0, 0, tzinfo=timezone.utc)

#: Girdi alanlarina konulan zararli yuk — govdede HAM halde bulunmamali.
ZARARLI = '<script>alert(1)</script><img src=x onerror=1>"'


def _alarm(**kwargs):
    """Tum alanlari None olan alarm maili; test ilgilendigini gecer."""
    alanlar = dict(
        project_title=None, rule_name="Kural", description=None, level=None,
        device_name=None, device_code=None, signal_source=None, line_name=None,
        region_name=None, value=None, value_string=None, threshold=None,
        operator=None, occurred_at=None,
    )
    alanlar.update(kwargs)
    return render_alarm_email(**alanlar)


def _ariza(**kwargs):
    alanlar = dict(
        project_title=None, line_name="Hat", line_code=None, region_name=None,
        last_red_device_name=None, last_red_device_code=None,
        first_green_device_name=None, first_green_device_code=None,
        from_pole_seq=None, to_pole_seq=None, latitude=None, longitude=None,
        opened_at=None, fault_link=None, assigned_to=None,
    )
    alanlar.update(kwargs)
    return render_fault_email(**alanlar)


def _atama(**kwargs):
    alanlar = dict(
        project_title=None, kind="alarm", recipient_full_name="Ad Soyad",
        title="Baslik", description=None, level=None, actor_username=None,
        link_path=None,
    )
    alanlar.update(kwargs)
    return render_assignment_email(**alanlar)


# --------- 1. Ham HTML sizintisi ---------------------------------------

@pytest.mark.parametrize(
    "uret",
    [
        lambda: _alarm(rule_name=ZARARLI, device_name=ZARARLI, description=ZARARLI,
                       value_string=ZARARLI, line_name=ZARARLI, region_name=ZARARLI,
                       project_title=ZARARLI),
        lambda: _ariza(line_name=ZARARLI, line_code=ZARARLI, region_name=ZARARLI,
                       last_red_device_name=ZARARLI, first_green_device_name=ZARARLI,
                       assigned_to=ZARARLI, project_title=ZARARLI),
        lambda: _atama(title=ZARARLI, description=ZARARLI, actor_username=ZARARLI,
                       recipient_full_name=ZARARLI, project_title=ZARARLI),
    ],
    ids=["alarm", "ariza", "atama"],
)
def test_kullanici_girdisi_HAM_HTML_olarak_govdeye_girmez(uret):
    _, govde = uret()
    # Yuk KACISSIZ haliyle hic bulunmamali. "onerror" gibi tek kelimeler
    # kacislanmis metnin ICINDE de gecer (escape yalnizca < > & " degistirir)
    # — o yuzden acilan ETIKETI ariyoruz, kelimeyi degil.
    assert ZARARLI not in govde
    assert "<script" not in govde
    assert "onerror=1>" not in govde
    # Kacislanmis hali bulunmali — yoksa test yanlis yerden gecmis olur.
    assert "&lt;script&gt;" in govde


def test_alarm_konusu_ham_kalir_ama_govde_kacislidir():
    """Konu satiri HTML degil: SMTP basligi olarak gider, kacislanmaz.

    Kacislanirsa alici kutusunda `&lt;` gibi artiklar gorunur."""
    konu, govde = _alarm(rule_name="A & B", device_name="C<D")
    assert konu == "[Bilgi] A & B — C<D"
    assert "C&lt;D" in govde


# --------- 2. Eksik veriyle cokme -------------------------------------

def test_alanlarin_hepsi_None_iken_de_mail_uretilir():
    for uret in (_alarm, _ariza, _atama):
        konu, govde = uret()
        assert konu
        assert govde.strip().startswith("<!DOCTYPE html")
        assert govde.rstrip().endswith("</html>")


def test_proje_adi_yoksa_urun_adi_yazilir():
    _, govde = _alarm()
    assert PRODUCT_NAME in govde


def test_bozuk_koordinat_arizayi_patlatmaz_mail_haritasiz_gider():
    """Konum alanlari veritabanindan geliyor; bozuk deger mailin tamamini
    dusurmemeli."""
    _, govde = _ariza(latitude="abc", longitude="def")
    assert "staticmap" not in govde
    assert "Hat Arızası" in govde


# --------- 3. Islenmemis sablon artigi --------------------------------

@pytest.mark.parametrize(
    "uret",
    [
        lambda: _alarm(level="critical", device_name="DM-1", value=612.4, threshold=500,
                       operator="gt", signal_source="sat07", line_name="Hat 1",
                       region_name="Merkez", occurred_at=ZAMAN,
                       description="Aciklama", alarm_link="https://x/1"),
        lambda: _ariza(line_code="BF-03", region_name="Merkez", from_pole_seq=12,
                       to_pole_seq=13, latitude=39.9, longitude=32.8,
                       opened_at=ZAMAN, fault_link="https://x/2", assigned_to="Ekip"),
        lambda: _atama(kind="fault", level="critical", actor_username="u",
                       description="d", link_path="/faults/1"),
    ],
    ids=["alarm", "ariza", "atama"],
)
def test_govdede_islenmemis_yer_tutucu_ya_da_None_kalmaz(uret):
    _, govde = uret()
    assert "PALETTE[" not in govde
    assert ">None<" not in govde
    assert "None;" not in govde  # stil degeri olarak sizmis None
    # `{` yalnizca <style> bloklarinda olabilir (biri de MSO kosullusu);
    # SON </style>'dan sonra kalmamali.
    _, _, sonrasi = govde.rpartition("</style>")
    assert "{" not in sonrasi


def test_zaman_yerel_saate_cevrilir():
    """UTC 11:00 postada 11:00 yazmamali (bkz. services/local_time.py)."""
    _, govde = _alarm(occurred_at=ZAMAN)
    assert "10.08.2026 14:00:00" in govde


# --------- 4. Harita slotu --------------------------------------------

def test_inline_harita_sablonun_ICINDE_render_edilir():
    _, govde = _alarm(map_cid="fault-map-7")
    assert 'src="cid:fault-map-7"' in govde
    # Altbilginin ONUNDE olmali: kartin disina dusmus olmasin.
    assert govde.index("cid:fault-map-7") < govde.index("Bu e-posta")


def test_harita_verilmezse_cid_referansi_hic_basilmaz():
    _, govde = _alarm()
    assert "cid:" not in govde


# --------- Onizleme kumesi hala render ediliyor mu ---------------------

def test_onizleme_ornekleri_render_edilebilir():
    """`scripts/preview_emails.py` tasarim gozden gecirmenin tek pratik
    yolu; sablon imzasi degisince sessizce bozulmasin."""
    from scripts.preview_emails import _samples

    ornekler = _samples()
    assert len(ornekler) >= 5
    for ad, konu, govde in ornekler:
        assert konu, ad
        assert govde.rstrip().endswith("</html>"), ad
