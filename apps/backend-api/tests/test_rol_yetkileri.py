"""ROL YETKI SINIRLARI — guvenlik duvari ve kullanici yonetimi.

Rol kontrolu bu depoda HIYERARSIK DEGIL: `require_roles` tam-eslesme yapar
(app/api/deps.py). Yani "installer her seyi yapar" gibi ortulu bir kural yok,
her uc kendi listesini yazar. Liste sessizce genisleyince kimse fark etmez —
ekran acilir, calisir, yalnizca yetki sinirini asmis olur. Bu dosya iki
sinirin butun kenarlarini sabitler.
"""

from __future__ import annotations

import pytest

from app.api import firewall as firewall_api
from app.api import users as users_api
from app.models.enums import UserRole


ROLLER = [UserRole.INSTALLER, UserRole.ENGINEER, UserRole.OPS_MANAGER, UserRole.OPERATOR]


# --------------------------------------------------------------- guvenlik duvari
def test_guvenlik_duvarini_YALNIZCA_installer_gorur():
    """Kural listesi cihazin ag yuzeyi: hangi port disariya acik, hangi adres
    gecebiliyor. Tek basina bir kesif haritasi — "sadece bakiyor" diye
    dagitilmaz. Ag Ayarlari da yalnizca installer'da."""
    assert firewall_api._VIEW_ROLES == [UserRole.INSTALLER]


def test_guvenlik_duvarini_YALNIZCA_installer_degistirir():
    assert firewall_api._MANAGE_ROLES == [UserRole.INSTALLER]


@pytest.mark.parametrize("rol", [r for r in ROLLER if r != UserRole.INSTALLER])
def test_installer_DISI_roller_guvenlik_duvarina_giremez(rol):
    assert rol not in firewall_api._VIEW_ROLES
    assert rol not in firewall_api._MANAGE_ROLES


# ------------------------------------------------------------ kullanici yonetimi
def test_ops_manager_kullanici_yonetimine_ERISIR():
    """Operasyon Yoneticisinin isi ekip yonetimi; operator hesaplarini
    gorup olusturabilmeli."""
    assert UserRole.OPS_MANAGER in users_api._USER_MGMT_ROLES


def test_operator_kullanici_yonetimine_GIREMEZ():
    assert UserRole.OPERATOR not in users_api._USER_MGMT_ROLES


class _Sahte:
    """`require_roles`tan gecmis bir kullanicinin yerine gecen en kucuk nesne."""

    def __init__(self, role: UserRole) -> None:
        self.role = role


def test_ops_manager_OPERATOR_disi_rol_ATAYAMAZ():
    """Kendine engineer/installer acmasin — yetki yukseltme yolu budur."""
    for rol in (UserRole.ENGINEER, UserRole.INSTALLER, UserRole.OPS_MANAGER):
        with pytest.raises(Exception) as hata:
            users_api._assert_ops_manager_only_operator(
                _Sahte(UserRole.OPS_MANAGER), target=None, new_role=rol
            )
        assert getattr(hata.value, "status_code", None) == 403


def test_ops_manager_OPERATOR_rolu_atayabilir():
    users_api._assert_ops_manager_only_operator(
        _Sahte(UserRole.OPS_MANAGER), target=None, new_role=UserRole.OPERATOR
    )


def test_ops_manager_OPERATOR_disi_hesaba_DOKUNAMAZ():
    for rol in (UserRole.ENGINEER, UserRole.INSTALLER, UserRole.OPS_MANAGER):
        with pytest.raises(Exception) as hata:
            users_api._assert_ops_manager_only_operator(
                _Sahte(UserRole.OPS_MANAGER), target=_Sahte(rol), new_role=None
            )
        assert getattr(hata.value, "status_code", None) == 403


def test_ops_manager_OPERATOR_hesabina_dokunabilir():
    users_api._assert_ops_manager_only_operator(
        _Sahte(UserRole.OPS_MANAGER), target=_Sahte(UserRole.OPERATOR), new_role=None
    )


def test_kural_YALNIZCA_ops_manager_icin_isler():
    """Installer/engineer bu kapidan gecmemeli; onlarin sinirlari ayri
    (bkz. `_assert_engineer_may_use_installer_role`)."""
    for rol in (UserRole.INSTALLER, UserRole.ENGINEER):
        users_api._assert_ops_manager_only_operator(
            _Sahte(rol), target=_Sahte(UserRole.INSTALLER), new_role=UserRole.INSTALLER
        )
