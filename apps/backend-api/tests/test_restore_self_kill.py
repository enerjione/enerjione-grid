"""Geri yukleme KENDI baglantisini oldurmemeli (denetim A1).

YASANAN HATA (bu testler yazilmadan once CANLIYDI)
--------------------------------------------------
`PGAPPNAME` atamasi `subprocess.Popen(env=env)` cagrisindan SONRA yapiliyordu:

    proc = subprocess.Popen(cmd, env=env, ...)   # <-- kopya cocuga gitti
    ...
    env["PGAPPNAME"] = "e1_restore_session"      # <-- cocuga HIC ulasmadi

Popen, env sozlugunun bir KOPYASINI cocuga gecirir. Sonradan yapilan atama
calisan surece etki etmez.

ZINCIR
------
    pg_restore baslar -> application_name libpq varsayilani ('pg_restore')
    -> terminate dongusunun `e1_%` desenine UYMAZ
    -> 1.5 sn sonra dongu ilk turunu atar
    -> --jobs=4 ile acilmis BES baglantinin hepsi pg_terminate_backend edilir
    -> pg_restore "server closed the connection unexpectedly" ile duser

`--single-transaction` KULLANILAMIYOR (--jobs ile birlikte gecersiz), yani
geri alma yok: veritabani `--clean` asamasinda DROP edilmis tablolarla YARIM
kalir. Her deneme ayni yerde patlar; sistem geri yuklenemez ve mevcut veri de
gitmistir — yedekten donmenin en cok gerektigi anda.

Bu testler hem ortami hem SIRALAMAYI kilitler.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

from app.services import backup_service

KAYNAK = Path(backup_service.__file__).read_text(encoding="utf-8")


# ------------------------------------------------------------------- ortam


def test_restore_ortami_application_name_TASIR():
    env = backup_service._build_restore_env()
    assert env.get("PGAPPNAME") == backup_service.RESTORE_APP_NAME


def test_application_name_terminate_FILTRESINE_uyar():
    """Ad `e1_` ile baslamazsa dongu restore'u oldurur.

    Filtre: `application_name NOT LIKE 'e1_%'` olanlar kesilir.
    """
    assert backup_service.RESTORE_APP_NAME.startswith("e1_")


def test_PGOPTIONS_ikinci_kat_koruma_saglar():
    """PGAPPNAME tasinmazsa (farkli libpq, sarmalayici script) bu tutar."""
    env = backup_service._build_restore_env()
    pgoptions = env.get("PGOPTIONS", "")
    assert f"application_name={backup_service.RESTORE_APP_NAME}" in pgoptions
    # Mevcut korumalar kaybolmamali.
    assert "statement_timeout" in pgoptions
    assert "lock_timeout" in pgoptions


def test_restore_parolasi_KORUNUYOR():
    """Yeni ortam kurulumu POSTGRES_RESTORE_PASSWORD davranisini bozmamali."""
    import os

    onceki = os.environ.get("POSTGRES_RESTORE_PASSWORD")
    os.environ["POSTGRES_RESTORE_PASSWORD"] = "ozel-parola"
    try:
        env = backup_service._build_restore_env()
        assert env["PGPASSWORD"] == "ozel-parola"
    finally:
        if onceki is None:
            os.environ.pop("POSTGRES_RESTORE_PASSWORD", None)
        else:
            os.environ["POSTGRES_RESTORE_PASSWORD"] = onceki


# ----------------------------------------------------------------- siralama


def _restore_fonksiyonu() -> ast.FunctionDef:
    agac = ast.parse(KAYNAK)
    for dugum in ast.walk(agac):
        if isinstance(dugum, ast.FunctionDef) and "Popen" in ast.dump(dugum):
            for alt in ast.walk(dugum):
                if (
                    isinstance(alt, ast.Attribute)
                    and alt.attr == "Popen"
                ):
                    return dugum
    raise AssertionError("Popen cagiran fonksiyon bulunamadi")


def test_ortam_Popen_dan_ONCE_hazirlanir():
    """En kritik yapisal koruma.

    Biri ileride env'i Popen'dan sonra degistirirse hata AYNEN geri gelir ve
    yalnizca gercek bir geri yukleme denemesinde — yani en kotu anda — fark
    edilir.
    """
    fn = _restore_fonksiyonu()

    popen_satiri = min(
        alt.lineno
        for alt in ast.walk(fn)
        if isinstance(alt, ast.Attribute) and alt.attr == "Popen"
    )
    env_yazma_satirlari = [
        alt.lineno
        for alt in ast.walk(fn)
        if isinstance(alt, ast.Subscript)
        and isinstance(alt.value, ast.Name)
        and alt.value.id == "env"
        and isinstance(alt.ctx, ast.Store)
    ]

    gec_kalanlar = [s for s in env_yazma_satirlari if s > popen_satiri]
    assert not gec_kalanlar, (
        f"env, Popen'dan SONRA degistiriliyor (satir {gec_kalanlar}). "
        "Popen env'in kopyasini gecirir; bu atamalar cocuk surece ULASMAZ."
    )


def test_terminate_dongusu_psql_i_COZUMLER():
    """`os.getenv('PSQL', 'psql')` Windows/native kurulumda sessizce bosa gider.

    Dongu hicbir sey yapmazsa worker baglantilari kesilmez, pg_restore kilit
    bekler ve lock_timeout ile duser. Yine geri yukleme basarisiz.
    """
    # YORUMLARA DEGIL KODA bak: bu dosyanin yorumlari hatanin kendisini
    # anlatiyor ve metin aramasi onlari da yakalardi.
    agac = ast.parse(KAYNAK)
    getenv_psql = [
        d
        for d in ast.walk(agac)
        if isinstance(d, ast.Call)
        and isinstance(d.func, ast.Attribute)
        and d.func.attr == "getenv"
        and d.args
        and isinstance(d.args[0], ast.Constant)
        and d.args[0].value == "PSQL"
    ]
    assert not getenv_psql, (
        "psql PATH varsayimiyla araniyor (os.getenv('PSQL')); "
        "resolve_pg_binary kullanilmali"
    )
    assert re.search(r'psql_path\s*=\s*resolve_pg_binary\("psql"\)', KAYNAK), (
        "psql_path resolve_pg_binary ile cozumlenmiyor"
    )
