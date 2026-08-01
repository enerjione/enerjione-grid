"""Gateway token rotasyonu hem calismali hem ESKISINI IPTAL ETMELI (Faz 2-14).

YASANAN ARIZA
-------------
`PATCH /gateways/{code}` tum alanlari `setattr(row, key, value)` ile yaziyordu
ama `token_hash` DOKUNULMUYORDU. Create yolunda hash hesaplaniyor
(`gateways.py` -> `hash_gateway_token`), update yolunda hesaplanmiyordu.

`validate_gateway_token` `token_hash` DOLUYSA yalnizca ona bakar; plaintext
karsilastirma sadece hash bosken devreye giren legacy yoldur. Sonuc iki
yonlu bozuktu:

  * YENI token 401 alir -> operator "gateway baglanmiyor" der ve sebebi
    aglarda/sertifikada arar,
  * ESKI token CALISMAYA DEVAM EDER -> rotasyon IPTAL ETMEZ.

Token'i degistirmenin tek amaci genelde "sizdi, gecersiz kilalim"dir; bu
haliyle sizan token gecerli kalmaya devam ediyordu.
"""

from __future__ import annotations

import ast
import inspect

from app.services.ingest_service import hash_gateway_token


def _update_fn() -> ast.FunctionDef:
    from app.api import gateways

    return next(
        d
        for d in ast.walk(ast.parse(inspect.getsource(gateways)))
        if isinstance(d, ast.FunctionDef) and d.name == "update_gateway"
    )


def _atanan_alanlar(fn: ast.FunctionDef) -> set[str]:
    return {
        t.attr
        for n in ast.walk(fn)
        if isinstance(n, ast.Assign)
        for t in n.targets
        if isinstance(t, ast.Attribute)
    }


def test_update_token_hash_i_AYARLIYOR():
    assert "token_hash" in _atanan_alanlar(_update_fn()), (
        "PATCH token'i degistiriyor ama hash'i guncellemiyor — yeni token 401 "
        "alir, ESKI token calismaya devam eder (rotasyon iptal etmez)"
    )


def test_hash_yalnizca_token_DEGISTIYSE_hesaplaniyor():
    """Baska bir alan guncellenirken token_hash'e dokunulmamali.

    Kosulsuz hesaplanirsa `changes` icinde token yokken `None` hash'i
    yazilir ve gateway'in kimligi SILINIR — calisan tum gateway'ler aninda
    401 almaya baslar.
    """
    fn = _update_fn()
    kosullu = False
    for dal in ast.walk(fn):
        if not isinstance(dal, ast.If):
            continue
        if any(
            isinstance(n, ast.Attribute) and n.attr == "token_hash"
            for n in ast.walk(dal)
        ):
            kosullu = True
            break
    assert kosullu, "token_hash kosulsuz yaziliyor — token disi guncellemeler kimligi siler"


def test_create_ile_ayni_hash_fonksiyonu():
    """Iki yol ayri hash uretirse rotasyon sonrasi token yine gecersiz olur."""
    from app.api import gateways

    kaynak = inspect.getsource(gateways)
    assert kaynak.count("hash_gateway_token") >= 2, (
        "update yolu create ile ayni hash fonksiyonunu kullanmiyor"
    )


def test_hash_deterministik_ve_farkli_tokenlar_FARKLI():
    """Dogrulama hash karsilastirmasina dayaniyor; temel ozellik sabitlensin."""
    assert hash_gateway_token("abc") == hash_gateway_token("abc")
    assert hash_gateway_token("abc") != hash_gateway_token("abd")


def test_dogrulama_hash_DOLUYSA_plaintext_e_dusmuyor():
    """Legacy plaintext yolu yalnizca hash BOSKEN devreye girmeli.

    Aksi halde eski token (plaintext kolonda duruyor) rotasyondan sonra da
    kabul edilirdi — yani duzeltme etkisiz kalirdi.
    """
    from app.services import ingest_service

    fn = next(
        d
        for d in ast.walk(ast.parse(inspect.getsource(ingest_service)))
        if isinstance(d, ast.FunctionDef) and d.name == "validate_gateway_token"
    )
    # `if gateway.token_hash:` ... `elif gateway.token:` yapisi korunmali
    dallar = [
        n
        for n in ast.walk(fn)
        if isinstance(n, ast.If)
        and isinstance(n.test, ast.Attribute)
        and n.test.attr == "token_hash"
    ]
    assert dallar, "token_hash dali yok"
    assert dallar[0].orelse, (
        "plaintext yolu ayri bir dal degil — hash doluyken de calisabilir"
    )
