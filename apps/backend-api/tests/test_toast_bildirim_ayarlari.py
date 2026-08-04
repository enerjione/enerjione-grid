"""Toast bildirim ayarlari: varsayilanlar, dogrulama, denetim, public GET.

NEDEN BU TESTLER
----------------
Ayar KURULUM GENELIDIR: bir INSTALLER susturmayi acinca TUM kullanicilarin
alarm baloncugu susar. Uc sey yanlis giderse sonuc agirdir:

  1. VARSAYILAN KAYARSA — guncelleyen sahada kimse bir sey degistirmemisken
     davranis degisir (or. bildirimler kendiliginden susar). Kolonlar NULL
     kalmali ve NULL "mevcut davranis" demeli.
  2. DENETIM IZI DUSERSE — "alarm bildirimlerini kim kapatti" sorusunun
     cevabi kalmaz.
  3. PUBLIC GET SIZDIRIRSA — `GET /project-settings` bilincli olarak
     auth'suz. Oraya eklenen her alan anonim bir cagirana acilir.
"""

from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

import app.models  # noqa: F401  (model kayitlari Base.metadata'ya girsin)
from app.api.project_settings import get_project_settings, update_project_settings
from app.db.base import Base
from app.models.project_settings import ProjectSettings
from app.models.system_event import SystemEvent
from app.schemas.project_settings import ProjectSettingsRead, ProjectSettingsUpdate

GECERLI_KONUMLAR = ["bottom-right", "bottom-left", "top-right", "top-left"]


@pytest.fixture()
def db():
    eng = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(eng)
    Session = sessionmaker(bind=eng, autoflush=True)
    s = Session()
    try:
        yield s
    finally:
        s.close()
        eng.dispose()


def _kurulumcu():
    # Router fonksiyonu dogrudan cagriliyor; Depends degerlendirilmiyor.
    # Yetki kisiti `require_role(UserRole.INSTALLER)` ile router imzasinda
    # duruyor ve test_route_auth_boundary tarafindan ayrica dogrulaniyor.
    return SimpleNamespace(username="kurulumcu")


# ---------------------------------------------------------------------------
# 1. Varsayilanlar mevcut davranisi AYNEN korur
# ---------------------------------------------------------------------------


def test_yeni_satirda_toast_alanlari_NULL(db):
    """Guncelleyen sahada hicbir sey degismemeli."""
    satir = ProjectSettings(id=1)
    db.add(satir)
    db.flush()
    assert satir.toast_position is None
    assert satir.toast_muted is None


def test_bos_ayar_public_GET_ile_NULL_doner(db):
    """Satir hic yokken de alanlar NULL — frontend bunu 'mevcut davranis' okur."""
    okunan = get_project_settings(db=db)
    assert okunan.toast_position is None
    assert okunan.toast_muted is None


def test_baska_alan_kaydedilince_toast_ayarlari_BOZULMAZ(db):
    """`exclude_unset`: gonderilmeyen alan silinmemeli.

    Susturma acikken biri logo degistirdiginde susturma kendiliginden
    kapansaydi, ayar 'tutmuyor' gibi gorunurdu.
    """
    update_project_settings(
        payload=ProjectSettingsUpdate(toast_muted=True, toast_position="top-left"),
        current_user=_kurulumcu(),
        db=db,
    )
    update_project_settings(
        payload=ProjectSettingsUpdate(project_name="Aras"),
        current_user=_kurulumcu(),
        db=db,
    )
    satir = db.get(ProjectSettings, 1)
    assert satir.toast_muted is True
    assert satir.toast_position == "top-left"
    assert satir.project_name == "Aras"


# ---------------------------------------------------------------------------
# 2. Dogrulama — gecersiz konum CSS'te karsiligi olmayan sinif uretirdi
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("konum", GECERLI_KONUMLAR)
def test_gecerli_konumlar_kabul_edilir(konum):
    assert ProjectSettingsUpdate(toast_position=konum).toast_position == konum


@pytest.mark.parametrize("konum", ["orta", "BOTTOM-RIGHT", "bottom right", "", "center-top"])
def test_gecersiz_konum_reddedilir(konum):
    with pytest.raises(ValidationError):
        ProjectSettingsUpdate(toast_position=konum)


def test_konum_sozlesmesi_frontend_ile_ayni():
    """Backend Literal <-> frontend `ToastPosition` <-> CSS sinifi ayni metin."""
    tipler = ProjectSettingsUpdate.model_fields["toast_position"].annotation
    metinler = {
        v
        for arg in getattr(tipler, "__args__", ())
        for v in getattr(arg, "__args__", ())
        if isinstance(v, str)
    }
    assert metinler == set(GECERLI_KONUMLAR)


def test_susturma_bayragi_bool(db):
    assert ProjectSettingsUpdate(toast_muted=True).toast_muted is True
    assert ProjectSettingsUpdate(toast_muted=False).toast_muted is False
    assert ProjectSettingsUpdate().toast_muted is None


# ---------------------------------------------------------------------------
# 3. Denetim izi — susturmayi kim actiysa kayitta gorunsun
# ---------------------------------------------------------------------------


def test_susturma_denetim_kaydina_DEGERIYLE_duser(db):
    update_project_settings(
        payload=ProjectSettingsUpdate(toast_muted=True, toast_position="top-right"),
        current_user=_kurulumcu(),
        db=db,
    )
    olay = db.execute(
        select(SystemEvent).where(SystemEvent.event_type == "project_settings_updated")
    ).scalars().one()
    assert olay.actor_username == "kurulumcu"
    # Yalnizca alan ADI degil DEGERI de: "kapatildi mi acildi mi" kayitta olmali.
    assert "toast_muted=True" in olay.message
    assert "toast_position=top-right" in olay.message
    assert "toast_muted" in (olay.metadata_json or "")


def test_susturmayi_KAPATMA_da_kayda_duser(db):
    update_project_settings(
        payload=ProjectSettingsUpdate(toast_muted=True),
        current_user=_kurulumcu(),
        db=db,
    )
    update_project_settings(
        payload=ProjectSettingsUpdate(toast_muted=False),
        current_user=_kurulumcu(),
        db=db,
    )
    mesajlar = [
        e.message
        for e in db.execute(select(SystemEvent)).scalars().all()
    ]
    assert any("toast_muted=True" in m for m in mesajlar)
    assert any("toast_muted=False" in m for m in mesajlar)


# ---------------------------------------------------------------------------
# 4. Auth'suz GET ne sizdiriyor
# ---------------------------------------------------------------------------
# `GET /project-settings` bilincli olarak halka acik (login ekrani ve header
# oturum yokken logoyu ceker). Oraya eklenen HER alan anonim bir cagirana
# acilir; bu yuzden liste elle gozden gecirilir.


PUBLIC_ALANLAR = {
    "project_name": "marka; login ekraninda zaten gorunur",
    "customer_name": "marka; login ekraninda zaten gorunur",
    "customer_logo": "login ekraninda gosterilen gorsel",
    "customer_logo_light": "header'da gosterilen gorsel",
    "battery_voltage_low": "goruntuleme esigi; cihaz/olcum verisi degil",
    "battery_voltage_full": "goruntuleme esigi; cihaz/olcum verisi degil",
    "site_title": "tarayici sekme basligi",
    "favicon": "tarayici ikonu",
    "login_image": "login ekrani dekoratif gorseli",
    "toast_position": "UI tercihi: baloncugun kosesi. PII/ag/kimlik bilgisi yok",
    "toast_muted": "UI tercihi: baloncuk susturuldu mu. Alarm uretimi ve "
    "e-posta/SMS/Telegram/push yollari ETKILENMEZ",
}


def test_public_GET_yalnizca_gozden_gecirilmis_alanlari_doner():
    alanlar = set(ProjectSettingsRead.model_fields)
    eklenen = alanlar - set(PUBLIC_ALANLAR)
    assert not eklenen, (
        f"auth'suz GET'e gozden gecirilmemis alan eklenmis: {sorted(eklenen)}. "
        "Hassas degilse PUBLIC_ALANLAR'a gerekcesiyle ekleyin."
    )
    assert not set(PUBLIC_ALANLAR) - alanlar, "PUBLIC_ALANLAR'da olu girdi var"


def test_toast_alanlari_gizli_veri_TASIMAZ(db):
    """Sizan degerler sabit bir kucuk kumeden geliyor; serbest metin degil."""
    update_project_settings(
        payload=ProjectSettingsUpdate(toast_muted=True, toast_position="top-left"),
        current_user=_kurulumcu(),
        db=db,
    )
    okunan = ProjectSettingsRead.model_validate(get_project_settings(db=db))
    assert okunan.toast_position in GECERLI_KONUMLAR
    assert isinstance(okunan.toast_muted, bool)


# ---------------------------------------------------------------------------
# 5. Migration — tek head + kolon zaten varsa patlamamali
# ---------------------------------------------------------------------------

_VERSIYONLAR = Path(__file__).resolve().parents[1] / "alembic_migrations/versions"
_MIG = _VERSIYONLAR / "2026_08_03_0002-0038_project_settings_toast.py"


def _sabit(agac: ast.AST, ad: str):
    for d in ast.walk(agac):
        if isinstance(d, ast.AnnAssign) and getattr(d.target, "id", None) == ad:
            return ast.literal_eval(d.value)
        if isinstance(d, ast.Assign) and any(
            getattr(t, "id", None) == ad for t in d.targets
        ):
            return ast.literal_eval(d.value)
    raise AssertionError(f"{ad} bulunamadi")


def test_migration_zinciri_TEK_head():
    revizyonlar: dict[str, str | None] = {}
    for dosya in _VERSIYONLAR.glob("*.py"):
        agac = ast.parse(dosya.read_text(encoding="utf-8"))
        revizyonlar[_sabit(agac, "revision")] = _sabit(agac, "down_revision")
    assert revizyonlar.get("0038") == "0037", "0038 zincire baglanmamis"
    ustler = set(revizyonlar) - {d for d in revizyonlar.values() if d}
    assert ustler == {"0038"}, f"tek head olmali, bulunan: {sorted(ustler)}"


def test_add_column_KOSULA_bagli():
    """Kolon zaten varsa DuplicateColumn ile backend acilamaz.

    `app/main.py` acilista `Base.metadata.create_all()` cagiriyor ve alembic
    ondan SONRA kosuyor; temiz veritabaninda kolonlar zaten olusmus oluyor.
    0025 tam bu yuzden cihazlari kalici olarak kilitlemisti. Metin aramasi
    yetmez (yorumda da "if" gecer) — AST ile kosul altinda oldugu dogrulanir.
    """
    agac = ast.parse(_MIG.read_text(encoding="utf-8"))
    for fn_adi, cagri_adi in (("upgrade", "add_column"), ("downgrade", "drop_column")):
        fn = next(
            d for d in ast.walk(agac) if isinstance(d, ast.FunctionDef) and d.name == fn_adi
        )
        cagrilar = [
            d
            for d in ast.walk(fn)
            if isinstance(d, ast.Call) and getattr(d.func, "attr", None) == cagri_adi
        ]
        assert len(cagrilar) == 2, f"{fn_adi}: iki {cagri_adi} bekleniyordu"
        kosul_icinde = [
            c
            for d in ast.walk(fn)
            if isinstance(d, ast.If)
            for c in ast.walk(d)
            if isinstance(c, ast.Call) and getattr(c.func, "attr", None) == cagri_adi
        ]
        assert len(kosul_icinde) == len(cagrilar), (
            f"{fn_adi}: kosulsuz {cagri_adi} var — kolon zaten varsa backend acilmaz"
        )


def test_migration_server_default_VERMEZ():
    """server_default eklemek mevcut sahada davranisi degistirirdi.

    NULL = "mevcut davranis" sozlesmesi model ve frontend tarafinda da bu
    sekilde okunuyor; migration'da sabit bir varsayilan yazmak sozlesmeyi
    ikiye bolerdi.
    """
    kaynak = _MIG.read_text(encoding="utf-8")
    agac = ast.parse(kaynak)
    for d in ast.walk(agac):
        if isinstance(d, ast.Call) and getattr(d.func, "attr", None) == "Column":
            adlar = {k.arg for k in d.keywords}
            assert "server_default" not in adlar, "toast kolonlarina server_default konmus"
            assert d.keywords and any(
                k.arg == "nullable" and ast.literal_eval(k.value) is True for k in d.keywords
            ), "toast kolonlari nullable olmali"
