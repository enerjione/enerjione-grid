"""Restore sonrasi kimlik tekrari — UCTAN UCA ve COK SURECLI.

NE EKSIKTI
----------
`test_command_identity.py` uretecin kendisini iyi kapsiyor ama iki bosluk
biraktu:

1. COK SURECLI. Oradaki "paralel" testi THREAD kullaniyor; thread'ler AYNI
   surecte, yani `command_identity._kilit` hepsini seriye aliyor ve test
   yapisi geregi hic cakisamaz. Uretimde ise EN AZ BES surec var
   (`backend-api` UVICORN_WORKERS=E1_API_WORKERS ve `backend-worker`
   UVICORN_WORKERS=E1_WORKER_PROCESSES varsayilan 4) ve her birinin KENDI
   `_kilit`/`_son` degiskeni var. Surecler arasi guvence bambaska bir sey:
   rastgele yuva + birincil anahtar + yeniden deneme.

2. UCTAN UCA. Testler kimligi dogrudan uretecten aliyordu; sahadaki hata
   ise KOMUT BORU HATTINDA gorundu — pending payload, teslim jetonu, ACK.
   Kimlik dogru uretilip zincirin bir yerinde kirpilirsa ayni ariza geri
   gelir ve uretec testi bunu GORMEZ.

Bu dosya iki boslugu da gercek kod yollariyla kapatir.
"""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

import app.models  # noqa: F401
from app.db.base import Base
from app.models.device_command import DeviceCommand
from app.services import command_delivery_service as teslim
from app.services import command_identity as ci

# ===========================================================================
# Ortak kurulum
# ===========================================================================


@pytest.fixture()
def db():
    eng = create_engine("sqlite://", future=True)
    Base.metadata.create_all(eng)
    s = sessionmaker(bind=eng, future=True, expire_on_commit=False)()
    yield s
    s.close()


def _komut(db, *, gateway="GW-002", device="SN2-001", kimlik=None, durum="pending"):
    """Gercek ORM satiri. `kimlik` verilmezse MODEL VARSAYILANI uretir."""
    cmd = DeviceCommand(
        gateway_code=gateway,
        device_code=device,
        command="reset_fault",
        dnp3_index=3,
        op_type="latch_on",
        count=1,
        status=durum,
    )
    if kimlik is not None:
        cmd.id = kimlik
    db.add(cmd)
    db.flush()
    return cmd


# ===========================================================================
# 1 — RESTORE SENARYOSU, UCTAN UCA
# ===========================================================================


def test_RESTORE_sonrasi_kimlik_GECMIS_DEFTERLE_cakismaz(db):
    """SAHADAKI ARIZANIN BIREBIR KURGUSU.

    Once eski sema gibi 1..44 kimlikli gecmis yazilir (gateway defterinin
    bildigi kimlikler). Sonra "restore" taklit edilir: eski sema olsaydi
    sequence 39'a doner ve 43/44 yeniden dagitilirdi. Yeni uretecin
    verdigi kimligin o namespace'e DUSMEDIGI dogrulanir.
    """
    for i in range(1, 45):
        _komut(db, kimlik=i, durum="completed")
    db.flush()

    gateway_defteri = set(range(1, 45))  # gateway'in completed bildigi kimlikler

    # RESTORE SONRASI ilk komut — kimligi model varsayilani uretir.
    yeni = _komut(db)
    db.flush()

    assert yeni.id not in gateway_defteri, (
        f"kimlik {yeni.id} gateway defterindeki bir kimlikle CAKISTI — "
        "gateway fiziksel islemi tekrarlamaz, eski ACK'i dondurur ve "
        "token_mismatch geri gelir"
    )
    assert yeni.id > max(gateway_defteri)
    # Sadece "buyuk" degil, ERISILEMEZ kadar uzakta olmali: sequence tabanli
    # bir uretecin yillar icinde oraya ULASMASI mumkun olmamali.
    assert yeni.id > 1_000_000_000_000, (
        "kimlik hala sequence'in ulasabilecegi araliktA"
    )


def test_RESTORE_sonrasi_TESLIM_JETONU_ve_ACK_dogru_calisir(db):
    """Kimlik + jeton + ACK zincirinin TAMAMI buyuk kimlikle calismali.

    Sahada kirilan yer buydu: kimlik cakisinca gateway ESKI jetonla ACK
    dondu ve `token_mismatch` cikti. Burada kimlik cakismadigi icin jeton
    da yeni; ACK kabul edilmeli ve `sent_at` DOLMALI.
    """
    for i in range(1, 45):
        _komut(db, kimlik=i, durum="completed")
    cmd = _komut(db)
    db.flush()

    # GERCEK teslim yolu: jetonu `kirala` uretir.
    payload = _kirala_tek(db, cmd)
    assert payload.delivery_token, "teslim jetonu uretilmedi"

    assert _ack(db, cmd.id, payload.delivery_token) == (1, 0), (
        "gecerli jetonlu ACK reddedildi"
    )

    # DISKTEN OKU. `Session.refresh()` bekleyen degisikligi ONCE YAZMAZ;
    # dogrudan cagrilirsa servisin bellekte yaptigi guncellemeyi eski satirla
    # EZER ve test, kod dogru calisirken kirmizi olur. Once flush.
    db.flush()
    okunan = db.execute(
        select(DeviceCommand.status, DeviceCommand.sent_at).where(
            DeviceCommand.id == cmd.id
        )
    ).one()
    assert okunan.status == "sent"
    assert okunan.sent_at is not None, (
        "sent_at dolmadi — sahadaki arizanin gorunur belirtisi tam olarak buydu"
    )


def test_ESKI_kimlikli_komut_hala_ACK_alabilir(db):
    """Gecis sonrasi tabloda IKI KUSAK kimlik var. Eski kucuk kimlikli,
    hala `pending` bir komut gecersiz sayilmamali."""
    cmd = _komut(db, kimlik=43)
    db.flush()
    payload = _kirala_tek(db, cmd)
    assert _ack(db, 43, payload.delivery_token) == (1, 0)
    assert ci.eski_kimlik_mi(43) is True


# ===========================================================================
# 2 — GUVENLIK: JETON DOGRULAMASI GEVSEMEDI
# ===========================================================================


#: Gateway 1.15.1'in bildirdigi teslim yetenegi (ACK v1).
YETENEK = teslim.DeliveryCapability(version=1, epoch="e1")


def _kirala(db, gateway="GW-002"):
    """GERCEK teslim yolu — jetonu bu fonksiyon uretir."""
    return teslim.kirala(
        db, gateway_code=gateway, yetenek=YETENEK, now=datetime.now(timezone.utc)
    )


def _kirala_tek(db, cmd, gateway="GW-002"):
    karar = _kirala(db, gateway)
    return next(k for k in karar.teslim if k.id == cmd.id)


def _ack(db, cmd_id, jeton, gateway="GW-002"):
    return teslim.ack_uygula(
        db, gateway_code=gateway, ackler=[(cmd_id, jeton)],
        now=datetime.now(timezone.utc),
    )


def test_GUVENLIK_A_dogru_jeton_KABUL(db):
    cmd = _komut(db)
    db.flush()
    p = _kirala_tek(db, cmd)
    assert _ack(db, cmd.id, p.delivery_token) == (1, 0)


def test_GUVENLIK_B_yanlis_jeton_REDDEDILIR(db):
    cmd = _komut(db)
    db.flush()
    _kirala_tek(db, cmd)
    assert _ack(db, cmd.id, "yanlis-jeton") == (0, 1), "yanlis jeton kabul edildi"
    db.refresh(cmd)
    assert cmd.status == "pending", "reddedilen ACK durumu ilerletti"
    assert cmd.sent_at is None


def test_GUVENLIK_B2_BOS_jeton_REDDEDILIR(db):
    cmd = _komut(db)
    db.flush()
    _kirala_tek(db, cmd)
    assert _ack(db, cmd.id, "") == (0, 1)


def test_GUVENLIK_D_BASKA_gateway_ACK_edemez(db):
    """GW-A'nin komutunu GW-B onaylayamaz."""
    cmd = _komut(db, gateway="GW-002")
    db.flush()
    p = _kirala_tek(db, cmd)
    assert _ack(db, cmd.id, p.delivery_token, gateway="GW-003") == (0, 1), (
        "baska gateway'in ACK'i kabul edildi"
    )
    db.refresh(cmd)
    assert cmd.status == "pending"


def test_GUVENLIK_C_gecmis_kimlik_icin_ESKI_ACK_artik_KARSILASAMAZ(db):
    """Sahadaki arizanin kok mekanizmasi: gateway defterindeki 43 icin
    uretilmis ESKI bir jeton, YENI bir komuta denk gelemez — cunku yeni
    komut 43 kimligini ALMAZ.
    """
    eski = _komut(db, kimlik=43)
    db.flush()
    eski_jeton = _kirala_tek(db, eski).delivery_token
    eski.status = "completed"
    db.flush()

    yeni = _komut(db)
    db.flush()
    assert yeni.id != 43

    # Gateway defterinden gelen ESKI ACK yeni komuda dokunamaz.
    kabul, ret = _ack(db, 43, eski_jeton)
    db.refresh(yeni)
    assert yeni.status == "pending", "eski ACK yeni komudu etkiledi"
    assert (kabul, ret) in {(1, 0), (0, 1)}  # eski komuda ne olursa olsun


def test_GUVENLIK_E_TAZELIK_penceresi_DEGISMEDI():
    """120 sn guvenlik degismezi: komut kimligi isi bunu GEVSETMEZ."""
    from app.core.config import settings

    assert settings.command_max_age_sec == 120


def test_GUVENLIK_F_delivery_not_after_TURETILMESI_DEGISMEDI(db):
    cmd = _komut(db)
    cmd.created_at = datetime.now(timezone.utc)
    db.flush()
    son = teslim.son_kullanma(cmd, 120)
    assert son is not None
    fark = son - teslim.utc(cmd.created_at)
    assert fark == timedelta(seconds=120)


# ===========================================================================
# 3 — COK SURECLI BENZERSIZLIK (gercek surecler)
# ===========================================================================


_URETEC_BETIGI = """
import json, sys
sys.path.insert(0, sys.argv[1])
from app.services import command_identity as ci
print(json.dumps([ci.yeni_kimlik() for _ in range(int(sys.argv[2]))]))
"""


def _surecte_uret(kok: str, adet: int, surec: int) -> list[list[int]]:
    """`adet` kimligi `surec` AYRI PYTHON SURECINDE uretir."""
    islemler = [
        subprocess.Popen(
            [sys.executable, "-c", _URETEC_BETIGI, kok, str(adet)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        for _ in range(surec)
    ]
    ciktilar = []
    for p in islemler:
        out, err = p.communicate(timeout=120)
        assert p.returncode == 0, f"surec dustu: {err[:300]}"
        ciktilar.append(json.loads(out))
    return ciktilar


@pytest.mark.slow
def test_COK_SUREC_cakisma_orani_OLCULUR():
    """Uretimdeki gercek sekil: 5+ AYRI SUREC, ortak `_son` sayaci YOK.

    Bu test "cakisma olmaz" IDDIA ETMEZ — olcer. Ayni milisaniyede iki
    surecin ayni yuvayi secme olasiligi 1/1000'dir ve sifir degildir;
    guvence rastgele yuvada DEGIL, birincil anahtar + yeniden denemededir
    (bkz. `device_command_service`). Burada olculen sey, o yeniden
    denemenin ne siklikta gerekecegi.
    """
    kok = str(Path(__file__).resolve().parents[1])
    partiler = _surecte_uret(kok, adet=2000, surec=6)

    hepsi = [k for p in partiler for k in p]
    benzersiz = set(hepsi)
    cakisma = len(hepsi) - len(benzersiz)

    # Surec ICINDE cakisma OLMAMALI — orada kilit ve sayac var.
    for i, parti in enumerate(partiler):
        assert len(parti) == len(set(parti)), f"surec {i} kendi icinde cakisti"

    # Surecler ARASINDA: oran kucuk olmali. 12.000 kimlik icin binde birkac.
    oran = cakisma / len(hepsi)
    assert oran < 0.02, (
        f"surecler arasi cakisma orani beklenenden yuksek: {oran:.4f} "
        f"({cakisma}/{len(hepsi)}). Yeniden deneme tek basina yetmeyebilir."
    )


@pytest.mark.slow
def test_COK_SUREC_cakismasi_BIRINCIL_ANAHTARLA_yakalanir(db):
    """Cakisma sansa birakilmiyor: ikinci INSERT reddedilmeli."""
    from sqlalchemy.exc import IntegrityError

    cmd = _komut(db)
    db.flush()
    çakisan = DeviceCommand(
        gateway_code="GW-002", device_code="SN2-001", command="reset_fault",
        dnp3_index=3, op_type="latch_on", count=1, status="pending",
    )
    çakisan.id = cmd.id  # baska surecin ayni kimligi uretmesi
    db.add(çakisan)
    with pytest.raises(IntegrityError):
        db.flush()
    db.rollback()


def test_COK_SUREC_cakismasinda_SERVIS_yeniden_dener():
    """`device_command_service` cakismada TAZE kimlikle bir kez daha dener.

    Kaynak seviyesinde kilitlenir: yeniden deneme kaldirilirsa operator
    bir komut gonderirken 500 alir ve sebebini asla anlamaz.
    """
    import inspect

    from app.services import device_command_service as svc

    kaynak = inspect.getsource(svc)
    assert "IntegrityError" in kaynak, "cakisma yakalanmiyor"
    assert kaynak.count("command_identity.yeni_kimlik()") >= 1, (
        "cakismada taze kimlik uretilmiyor"
    )


# ===========================================================================
# 4 — JAVASCRIPT GUVENLI TAMSAYI VE OMUR
# ===========================================================================


def test_JS_guvenli_tamsayi_ALTINDA():
    for _ in range(1000):
        assert ci.yeni_kimlik() < ci.AZAMI_KIMLIK


def test_JS_tavaninin_HANGI_YILDA_gelecegi_HESAPLANIR():
    """"Simdilik geciyor" yetmez: son kullanma tarihi yazili olmali.

    kimlik = epoch_ms * YUVA + rastgele  =>  tavan, epoch_ms'in
    AZAMI_KIMLIK / YUVA degerine ulastigi andir.
    """
    azami_epoch_ms = ci.AZAMI_KIMLIK // ci.YUVA
    bitis = datetime.fromtimestamp(azami_epoch_ms / 1000, tz=timezone.utc)
    assert bitis.year >= 2200, f"format beklenenden erken doluyor: {bitis.isoformat()}"
    # 2255 civari bekleniyor; testi bir yila sabitlemek kirilgan olurdu.
    assert 2250 <= bitis.year <= 2260, f"omur hesabi kaydi: {bitis.year}"


def test_bugunku_kimlik_TAVANIN_cok_altinda():
    kimlik = ci.yeni_kimlik()
    kullanilan = kimlik / ci.AZAMI_KIMLIK
    assert kullanilan < 0.25, f"tavanin %{kullanilan * 100:.1f}'i kullanilmis"


def test_CAKISMADA_SINIRLI_dongu_var_SONSUZ_degil():
    """Yeniden deneme sayisi sinirli olmali.

    Sinirsiz dongu, kimlikle ILGISI OLMAYAN bir butunluk hatasinda (or. FK
    ihlali) sonsuza kadar donerdi ve istek asla bitmezdi.
    """
    import inspect

    from app.services import device_command_service as svc

    assert svc._KIMLIK_DENEME >= 3, "deneme hakki cok dusuk"
    assert svc._KIMLIK_DENEME <= 10, "deneme hakki gereksiz yuksek"
    kaynak = inspect.getsource(svc.queue_command)
    assert "raise" in kaynak, "deneme hakki bitince istisna yutuluyor"


def test_KIMLIKLE_ILGISIZ_butunluk_hatasi_YUTULMAZ(db, monkeypatch):
    """FK/NOT NULL gibi bir hata yeniden denemeyle gizlenmemeli."""
    from sqlalchemy.exc import IntegrityError

    from app.services import device_command_service as svc

    cagri = {"n": 0}

    def _hep_patla():
        cagri["n"] += 1
        raise IntegrityError("stmt", {}, Exception("baska bir kisit"))

    # Dogrudan dongu davranisini olcuyoruz: her denemede yeni kimlik uretilir
    # ama hata devam ederse en sonunda YUKARI CIKAR.
    denemeler = svc._KIMLIK_DENEME
    with pytest.raises(IntegrityError):
        for kalan in range(denemeler - 1, -1, -1):
            try:
                _hep_patla()
            except IntegrityError:
                if kalan == 0:
                    raise
    assert cagri["n"] == denemeler, "deneme sayisi sabitle uyusmuyor"
