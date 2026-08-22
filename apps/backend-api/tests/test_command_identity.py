"""KOMUT KIMLIGI — veritabani restore'u kimligi TEKRAR KULLANDIRAMAZ.

YASANAN OLAY
------------
Gateway GW-002'nin defterinde ve backend `device_commands` tablosunda AYNI
tamsayi kimlik (39-42) FARKLI TARIHLI, FARKLI komutlar icin tekrar
kullanildi:

    gateway defteri : id 39 -> eski komut, completed/acked
    backend         : id 39 -> YENI komut, farkli teslim jetonu

Gateway DOGRU davrandi: defterinde o kimligi gorup fiziksel islemi
TEKRARLAMADI ve eski dayanikli ACK'i yeniden gonderdi. Backend ise yeni
komut icin baska bir jeton bekledigi icin `token_mismatch` uretti;
`sent_at` dolmadi, 120 sn tazelik penceresi doldu, komut `failed` oldu.

Iki taraf da sozlesmeye uydu. HATALI OLAN KIMLIGIN KENDISIYDI: kimlik
kaynagi veritabaninin ICINDE yasayan bir sequence'ti ve veritabani daha
eski bir ana alindiginda kimlikler yeniden dagitildi.

BU DOSYANIN KILITLEDIGI SEY
---------------------------
Restore SIMULE EDILIR ve yeni kimligin eski kimliklerle CAKISMADIGI
gosterilir. Ayrica komut guvenlik invaryantlarinin (120 sn, jeton
dogrulama) DEGISMEDIGI dogrulanir — kimligi duzeltmek, korumalari
gevsetmek DEGILDIR.
"""

from __future__ import annotations

import threading

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

import app.models  # noqa: F401
from app.db.base import Base
from app.models.device import Device
from app.models.device_command import DeviceCommand
from app.models.gateway import Gateway
from app.models.signal_catalog import SignalCatalog
from app.services import command_identity as ci
from app.services import device_command_service as svc

MODEL = "horstmann_sn_2_0"
SLUG = "reset_all_fcis"


@pytest.fixture()
def db():
    eng = create_engine("sqlite://", future=True)
    Base.metadata.create_all(eng)
    s = sessionmaker(bind=eng, future=True, expire_on_commit=False)()
    s.add(Gateway(code="GW-1", name="G", host="10.0.0.1", listen_port=20000,
                  token="t" * 20, is_active=True))
    s.commit()
    s.add(Device(code="DEV-1", name="D", gateway_code="GW-1", model=MODEL,
                 ip_address="10.0.0.10", latitude=39.0, longitude=35.0))
    s.add(SignalCatalog(key=f"master.{SLUG}", model=MODEL, label="FCI reset",
                        data_type="binary_output", dnp3_index=7, is_active=True))
    s.commit()
    yield s
    s.close()


def _cihaz(db):  # noqa: ANN001
    return db.scalar(select(Device).where(Device.code == "DEV-1"))


def _kuyrukla(db):  # noqa: ANN001
    return svc.queue_command(
        db, device=_cihaz(db), slug=SLUG, actor="tester", origin="ui"
    )


# ===========================================================================
# A) Benzersizlik ve temel ozellikler
# ===========================================================================


def test_A_her_komut_BENZERSIZ_kimlik_alir(db):
    kimlikler = {_kuyrukla(db).id for _ in range(50)}
    db.commit()
    assert len(kimlikler) == 50


def test_A2_kimlik_SEQUENCE_ten_GELMEZ(db):
    """Kucuk sirali sayilar (1,2,3...) artik uretilmemeli."""
    k = _kuyrukla(db).id
    assert k > 1_000_000_000_000, (
        f"kimlik {k} — sequence'ten geliyor gibi gorunuyor"
    )
    assert not ci.eski_kimlik_mi(k)


def test_A3_model_VARSAYILANI_uretir(db):
    """Kimlik uretimi TEK YERDE: modelde.

    Cagiran tarafa birakilsaydi, ileride eklenen bir ekleme yolu sessizce
    kimliksiz satir yazmaya calisirdi.
    """
    cmd = DeviceCommand(
        gateway_code="GW-1", device_code="DEV-1", command="x",
        dnp3_index=0, status="pending",
    )
    db.add(cmd)
    db.flush()
    assert cmd.id is not None and cmd.id > 1_000_000_000_000


# ===========================================================================
# B) 10000 uretim — cakisma yok
# ===========================================================================


def test_B_10000_uretimde_CAKISMA_YOK():
    kume = {ci.yeni_kimlik() for _ in range(10_000)}
    assert len(kume) == 10_000


# ===========================================================================
# C) Paralel uretim
# ===========================================================================


def test_C_PARALEL_uretimde_CAKISMA_YOK():
    """Birden fazla uvicorn worker'i AYRI SURECTIR; bu test surec ici
    esszamanliligi olcer. Surecler arasi guvence birincil anahtardir."""
    sonuc: list[int] = []
    kilit = threading.Lock()

    def uret():
        yerel = [ci.yeni_kimlik() for _ in range(500)]
        with kilit:
            sonuc.extend(yerel)

    isler = [threading.Thread(target=uret) for _ in range(8)]
    for t in isler:
        t.start()
    for t in isler:
        t.join()
    assert len(sonuc) == 4000
    assert len(set(sonuc)) == 4000, "paralel uretimde cakisma"


def test_C2_kimlikler_MONOTON_artar():
    """`GET /devices/{code}/commands` `order_by(id.desc()).limit(N)` ile
    "en yeni once" diyor. Sirasiz kimlikte en yeni komut listeye HIC
    girmeyebilirdi — operator gonderdigi komutu goremezdi."""
    diz = [ci.yeni_kimlik() for _ in range(200)]
    assert diz == sorted(diz), "kimlikler monoton degil"


# ===========================================================================
# D) Surec yeniden baslamasi
# ===========================================================================


def test_D_RESTART_sonrasi_cakisma_yok(db, monkeypatch):  # noqa: ANN001
    """Yeniden baslamada surec sayaci sifirlanir — uretimdeki gibi kurulur.

    Acilista `taban_yukselt(max(id))` cagriliyor (bkz. `main.py`). Bu, AYNI
    MILISANIYEDE yeniden baslamis bir surecin, o milisaniyede zaten
    dagitilmis bir yuvayi yeniden secmesini engeller.
    """
    once = [_kuyrukla(db).id for _ in range(20)]
    db.commit()

    # "Restart": surec sayaci sifirlanir...
    monkeypatch.setattr(ci, "_son", 0, raising=False)
    # ...ve acilis kancasi tabani DB'den yukseltir.
    ci.taban_yukselt(db.scalar(select(func.max(DeviceCommand.id))))

    sonra = [_kuyrukla(db).id for _ in range(20)]
    db.commit()
    assert not (set(once) & set(sonra)), "restart sonrasi kimlik tekrarlandi"
    assert min(sonra) > max(once), "restart sonrasi kimlik geriye gitti"


def test_D2_taban_yukseltme_AYNI_MS_cakismasini_onler():
    """Sayac sifirken taban DB'den yukseltilirse yuva tekrarlanamaz."""
    ilk = ci.yeni_kimlik()
    ci.taban_yukselt(None)          # None guvenli: hicbir sey yapmaz
    ci.taban_yukselt(ilk)           # bilinen en yuksek
    assert ci.yeni_kimlik() > ilk


# ===========================================================================
# E) SIMULE EDILMIS RESTORE — bu isin ASIL testi
# ===========================================================================


def test_E_RESTORE_sonrasi_kimlik_TEKRAR_KULLANILMAZ(db):
    """Uretim olayinin BIREBIR simulasyonu.

    A,B,C uret -> "yedek al" -> D,E,F uret (gateway bunlari GORDU) ->
    veritabanini yedege geri al -> yeni komut uret.

    BEKLENEN: yeni kimlik D/E/F ile CAKISMAZ. Eski semada cakisirdi, cunku
    sequence de yedegin icindeydi ve geri donerdi.
    """
    A_B_C = [_kuyrukla(db).id for _ in range(3)]
    db.commit()

    # --- YEDEK ALINDI: bu andaki satirlar ve sayac degeri ---------------
    yedek_kimlikler = list(A_B_C)

    D_E_F = [_kuyrukla(db).id for _ in range(3)]
    db.commit()
    # Gateway bunlari defterine yazdi (fiziksel olarak isledi).
    gateway_defteri = set(A_B_C) | set(D_E_F)

    # --- RESTORE: veritabani yedege geri alindi -------------------------
    # Yedekten SONRAKI satirlar (D,E,F) kaybolur. Eski semada sequence de
    # geriye donerdi; yeni semada kimligin kaynagi DB'de DEGIL.
    for kid in D_E_F:
        db.delete(db.get(DeviceCommand, kid))
    db.commit()
    assert db.scalar(select(func.count()).select_from(DeviceCommand)) == 3

    # Acilis kancasinin yaptigi sey: taban RESTORE EDILMIS max(id)'ye
    # yukseltilir. Bu deger de geriye donmustur — TEK BASINA yetmez.
    ci.taban_yukselt(db.scalar(select(func.max(DeviceCommand.id))))

    # --- RESTORE SONRASI YENI KOMUT -------------------------------------
    yeni = [_kuyrukla(db).id for _ in range(5)]
    db.commit()

    cakisan = set(yeni) & gateway_defteri
    assert not cakisan, (
        f"restore sonrasi kimlik TEKRAR KULLANILDI: {sorted(cakisan)} — "
        "gateway bu id'leri defterinde gorup fiziksel islemi atlar ve "
        "token_mismatch dongusu geri doner"
    )
    assert min(yeni) > max(D_E_F), "yeni kimlikler yedek sonrasi araliga dusmus"
    assert not (set(yeni) & set(yedek_kimlikler))


def test_E2_ESKI_kimlikli_satirlar_OKUNMAYA_devam_eder(db):
    """Gecis oncesi kucuk kimlikler (39,40...) bozulmamali."""
    eski = DeviceCommand(
        id=39, gateway_code="GW-1", device_code="DEV-1", command="x",
        dnp3_index=0, status="ok",
    )
    db.add(eski)
    db.commit()
    okunan = db.get(DeviceCommand, 39)
    assert okunan is not None and okunan.id == 39
    assert ci.eski_kimlik_mi(39)
    # Yeni komut eskisiyle cakismaz.
    assert _kuyrukla(db).id != 39


# ===========================================================================
# F) JavaScript ve veritabani siniri
# ===========================================================================


def test_F_kimlik_JAVASCRIPT_te_KAYIPSIZ():
    """Arayuz kimligi `number` tasiyor; JS 2^53 ustunde tamsayi
    hassasiyetini KAYBEDER. Tam 63-bit rastgele kimlik tarayicida SESSIZCE
    bozulurdu."""
    for _ in range(1000):
        k = ci.yeni_kimlik()
        assert 0 < k <= ci.AZAMI_KIMLIK
        # float'a cevirip geri almak degeri DEGISTIRMEMELI (JS'in yaptigi).
        assert int(float(k)) == k, f"{k} JS'te bozulur"


def test_F2_kolon_BIGINT():
    """int4 tavani 2.147.483.647; uretilen deger ~1.79e15."""
    from sqlalchemy.dialects import postgresql

    from app.models.device_config_application import DeviceConfigApplication

    assert (
        DeviceCommand.__table__.columns["id"].type.compile(postgresql.dialect())
        == "BIGINT"
    )
    assert (
        DeviceConfigApplication.__table__.columns["command_id"].type.compile(
            postgresql.dialect()
        )
        == "BIGINT"
    ), "FK genisletilmemis — yeni kimlikli komuta baglanmak patlar"
    assert DeviceCommand.__table__.columns["id"].autoincrement is False


# ===========================================================================
# G/H/I/J) GUVENLIK INVARYANTLARI DEGISMEDI
# ===========================================================================


def test_G_120sn_TAZELIK_DEGISMEDI():
    from app.core.config import settings

    assert int(settings.command_max_age_sec) == 120


def test_H_jeton_dogrulamasi_KALDIRILMADI():
    """ACK ucu teslim jetonunu DOGRULAMAYA devam etmeli.

    `token_mismatch` bu olayda DOGRU davranisti: kimligi duzeltmek,
    korumayi gevsetmek DEGILDIR.
    """
    import pathlib

    kaynak = (
        pathlib.Path(__file__).resolve().parents[1]
        / "app/services/command_delivery_service.py"
    ).read_text(encoding="utf-8")
    assert "reason=token_mismatch" in kaynak, "jeton uyusmazligi kontrolu kaldirilmis"
    # Reddetme YOLU da durmali: jeton eslesmiyorsa ACK kabul EDILMEZ.
    assert "def ack_uygula(" in kaynak


def test_I_delivery_not_after_DEGISMEDI():
    """Mutlak son kullanma `created_at + TTL`; kimlik semasi ona dokunmaz."""
    import pathlib

    kaynak = (
        pathlib.Path(__file__).resolve().parents[1]
        / "app/services/command_delivery_service.py"
    ).read_text(encoding="utf-8")
    assert "def son_kullanma(" in kaynak
    assert "command_identity" not in kaynak, (
        "teslim katmani kimlik uretimine baglanmis — ayri kalmali"
    )


def test_J_kimlik_uretici_KOMUT_BORU_HATTINA_dokunmaz():
    """Kimlik modulu teslim/kira/ACK mantigina bagimli OLMAMALI."""
    import pathlib

    kaynak = (
        pathlib.Path(__file__).resolve().parents[1]
        / "app/services/command_identity.py"
    ).read_text(encoding="utf-8")
    for yasak in ("delivery_token", "lease", "kirala", "ack_uygula"):
        assert yasak not in kaynak, f"kimlik modulu {yasak} ile baglanmis"


# ===========================================================================
# K) 10(b) config apply ile regresyon
# ===========================================================================


def test_K_config_apply_YENI_kimlik_kullanir(db):
    """Uyanma sonrasi uretilen `config_update` da restore-guvenli kimlik alir.

    Ayni yol `queue_command`'dan gectigi icin bu otomatik saglanir; test
    o bagin KOPMADIGINI kilitler.
    """
    db.add(SignalCatalog(key="master.config_update", model=MODEL,
                         label="Config", data_type="binary_output",
                         dnp3_index=0, is_active=True))
    db.commit()
    kuyruk = svc.queue_command(
        db, device=_cihaz(db), slug="config_update",
        actor="muh", origin="config_apply",
    )
    db.commit()
    assert kuyruk.id > 1_000_000_000_000
    assert not ci.eski_kimlik_mi(kuyruk.id)


# ===========================================================================
# L) COK SURECLI CAKISMA — GERCEK SERVIS YOLUNDA YENIDEN DENEME
# ===========================================================================
#
# B ve C testleri uretecin kendisini olcuyor. Sahada onemli olan ise SERVISIN
# cakismayi ATLATIP atlatamadigi: iki backend sureci (uretimde en az bes var)
# ayni milisaniyede ayni yuvayi secerse birincil anahtar ikinci INSERT'i
# reddeder ve `queue_command` TAZE bir kimlikle yeniden denemelidir.
#
# Cakisma OLCULDU (6 surec, ard arda uretim): binde 6. Gercek komut hizinda
# cok daha nadir, ama sifir degil.


#: Cakismayi TEMIZ BIR SURECTE zorlayan cocuk betik.
#:
#: NEDEN AYRI SUREC
#: ----------------
#: `DeviceCommand.id` varsayilani (`default=command_identity.yeni_kimlik`)
#: SQLAlchemy mapper'i tarafindan ILK KULLANIMDA cozulur ve orada kalir;
#: sonradan `Column.default`i ya da `command_identity.yeni_kimlik`i
#: yamalamak ILK INSERT'i ARTIK ETKILEMEZ (olculdu: yamali degere ragmen
#: gercek uretec calisti).
#:
#: Bu, uretim icin ISTENEN bir ozelliktir — kimlik uretimi calisma aninda
#: degistirilemez. Ama testte cakismayi zorlamak icin yama, o sinif hic
#: flush edilmeden ONCE konmalidir. Tek guvenilir yol taze bir surectir.
_CAKISMA_BETIGI = """
import json, sys
sys.path.insert(0, sys.argv[1])

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.sql.schema import ColumnDefault

import app.models  # noqa: F401
from app.db.base import Base
from app.models.device import Device
from app.models.device_command import DeviceCommand
from app.models.gateway import Gateway
from app.models.signal_catalog import SignalCatalog
from app.services import command_identity as ci
from app.services import device_command_service as svc

CAKISAN = 777000111222333
tekrar = int(sys.argv[2])          # kac kez cakisan deger uretilsin
sira = [CAKISAN] * (tekrar + 1)    # +1: ilk INSERT de cakissin
sayac = {"n": 0}


def sahte():
    sayac["n"] += 1
    return sira.pop(0) if sira else ci_gercek()


ci_gercek = ci.yeni_kimlik
# YAMA, SINIF HIC FLUSH EDILMEDEN ONCE konur.
DeviceCommand.__table__.c.id.default = ColumnDefault(sahte)
ci.yeni_kimlik = sahte

eng = create_engine("sqlite://", future=True)
Base.metadata.create_all(eng)
db = sessionmaker(bind=eng, future=True)()
db.add(Gateway(code="GW-1", name="G", host="10.0.0.1", listen_port=20000,
               token="t" * 20, is_active=True))
db.commit()
db.add(Device(code="DEV-1", name="D", serial_number="DEV-1", gateway_code="GW-1",
              model="horstmann_sn_2_0", ip_address="10.0.0.9",
              latitude=39.0, longitude=35.0))
db.add(SignalCatalog(key="master.reset_fault", model="horstmann_sn_2_0", label="R",
                     data_type="binary_output", dnp3_index=3, is_active=True))
db.commit()
dev = db.scalar(select(Device).where(Device.code == "DEV-1"))

# 1) Cakisacak kimligi tasiyan satir olusur.
ilk = svc.queue_command(db, device=dev, slug="reset_fault", actor="m", origin="ui")
db.commit()

# 2) Ayni kimlik yeniden uretilir -> birincil anahtar reddeder -> yeniden dene.
sonuc = {"ilk_id": ilk.id}
try:
    ikinci = svc.queue_command(db, device=dev, slug="reset_fault", actor="m", origin="ui")
    db.commit()
    sonuc["ikinci_id"] = ikinci.id
    sonuc["hata"] = None
except Exception as exc:                       # noqa: BLE001
    db.rollback()
    sonuc["ikinci_id"] = None
    sonuc["hata"] = type(exc).__name__
sonuc["uretim_sayisi"] = sayac["n"]
sonuc["satir"] = db.scalar(select(func.count()).select_from(DeviceCommand))
sonuc["deneme_hakki"] = svc._KIMLIK_DENEME
print(json.dumps(sonuc))
"""


def _cakisma_kos(tekrar: int) -> dict:
    import json
    import subprocess
    import sys
    from pathlib import Path

    kok = str(Path(__file__).resolve().parents[1])
    p = subprocess.run(
        [sys.executable, "-c", _CAKISMA_BETIGI, kok, str(tekrar)],
        capture_output=True, text=True, timeout=180,
    )
    assert p.returncode == 0, f"cocuk surec dustu: {p.stderr[-400:]}"
    return json.loads(p.stdout.strip().splitlines()[-1])


def test_L_CAKISMADA_servis_TAZE_kimlikle_devam_eder():
    """GERCEK cakisma, GERCEK servis yolu: komut yine de kuyruga girer.

    Yeniden deneme calismasaydi operator komutu gonderemedigini gorur ve
    sebebini asla anlamazdi (500).
    """
    s = _cakisma_kos(tekrar=1)
    assert s["hata"] is None, f"cakisma atlatilamadi: {s['hata']}"
    assert s["ikinci_id"] is not None
    assert s["ikinci_id"] != s["ilk_id"], "cakisan kimlik ezildi"
    assert s["satir"] == 2
    assert s["uretim_sayisi"] >= 3, "cakisma hic yasanmadi — kurgu bosa dondu"


def test_L2_deneme_hakki_TUKENIRSE_hata_YUKARI_cikar():
    """Yeniden deneme bir KACIS DELIGI degil.

    Kimlik hep ayni gelirse servis sessizce basarili gorunmemeli; hak
    bitince istisna cagirana gider ve yarim satir yazilmaz.
    """
    s = _cakisma_kos(tekrar=50)
    assert s["hata"] == "IntegrityError", f"beklenen istisna gelmedi: {s['hata']}"
    assert s["satir"] == 1, "hata sonrasi yarim satir kaldi"


def test_L3_deneme_sayisi_SINIRLI():
    """Uretim sayisi `_KIMLIK_DENEME` ile SINIRLI olmali.

    Tam esitlik ARANMAZ: model varsayilani ilk degeri uretir ve basarisiz
    bir flush sonrasinda SQLAlchemy nesneyi yeniden bekleyen duruma
    aldiginda varsayilan bir kez daha degerlendirilebilir. Onemli olan
    ustten sinirli olmasi — sinirsiz dongu, kimlikle ilgisiz bir butunluk
    hatasinda istegi hic bitirmezdi.
    """
    s = _cakisma_kos(tekrar=50)
    hak = s["deneme_hakki"]
    assert hak <= s["uretim_sayisi"] <= hak + 1, (
        f"kimlik {s['uretim_sayisi']} kez uretildi; {hak}..{hak + 1} bekleniyordu"
    )


def test_L4_kimlik_uretimi_CALISMA_ANINDA_degistirilemez(db):
    """Uretim ozelligi: varsayilan mapper'da sabitlenir.

    `command_identity.yeni_kimlik` sonradan yamalanirsa ILK INSERT
    ETKILENMEZ. Yani calisma aninda kimlik uretimini degistirebilecek bir
    yol yok — bu testler bu yuzden ayri surecte kosuyor.
    """
    svc.queue_command(db, device=_cihaz(db), slug=SLUG, actor="muh", origin="ui")
    db.commit()

    import app.services.command_identity as canli

    eski = canli.yeni_kimlik
    try:
        canli.yeni_kimlik = lambda: 42
        yeni = svc.queue_command(
            db, device=_cihaz(db), slug=SLUG, actor="muh", origin="ui"
        )
        db.commit()
        assert yeni.id != 42, "kimlik uretimi calisma aninda degistirilebiliyor"
        assert yeni.id > 1_000_000_000_000
    finally:
        canli.yeni_kimlik = eski
