from typing import Literal

from pydantic import BaseModel, Field

IpEndpointType = Literal["initiating", "listening"]


class Dnp3ExtendedSettings(BaseModel):
    """Uç birimdeki (gateway/collector) DNP3 oturum parametreleri; merkez sadece saklar ve gösterir.

    MASTER_ADDRESS NEDEN OPSIYONEL (2026-08-07 saha arizasi)
    --------------------------------------------------------
    Bu alan once `default=100` idi. Operator cihaz kaydinda ILGISIZ bir alani
    (orn. TCP portu) degistirip kaydettiginde bile `merge_dnp3_extended` tum
    alanlari somutlastirdigi icin master_address diske 100 olarak yaziliyordu
    ve gateway o cihaza artik 100 adresiyle konusuyordu. Gateway'in kendi
    varsayilani ise DNP3_LOCAL_ADDRESS=1.

    DNP3 outstation'lari BEKLEMEDIKLERI master adresinden gelen istekleri
    SESSIZCE ATAR: TCP baglantisi kurulur, uygulama katmani hic cevap vermez.
    Sahada tam olarak bu gorundu (link_open -> 15sn fresh frame yok -> lost ->
    forced_relink dongusu). Ayirt edici kanit: ayni gateway'deki SIMULATOR
    cihazlari master=100 ile sorunsuz calisiyordu — simulator master adresini
    dogrulamiyor, gercek outstation doguluyor. Yani bu hata simulasyon
    testlerinde GORUNMEZ.

    None = "gateway kendi DNP3_LOCAL_ADDRESS varsayilanini kullansin".
    Merkezi bir varsayilani saha cihazinin uzerine yazmak DOGRU DEGIL.
    """

    ip_endpoint_type: IpEndpointType = "listening"
    master_ip_address: str = ""
    master_ip_port: int = Field(default=20002, ge=1, le=65535)
    #: None birakilmali; bkz. sinif docstring'i. Deger girilirse saha
    #: cihazinin BEKLEDIGI adresle bire bir ayni olmali.
    master_address: int | None = Field(default=None, ge=0, le=65535)
    unsolicited_reporting: bool = True
    unsolicited_on_startup: bool = True
    unsolicited_class_mask_id: int = Field(default=7, ge=0, le=255)
    link_status_period_min: int = Field(default=0, ge=0)
    enable_self_address: bool = False
    validate_source_address: bool = False
    session_timeout_listening_sec: int = Field(default=60, ge=1, le=86400)
    socket_listening_timeout_sec: int = Field(default=600, ge=1, le=86400)


def merge_dnp3_extended(stored: dict | None) -> Dnp3ExtendedSettings:
    """Kayitli sozlugu GORUNTULEME icin tamamlar (eksik alanlara varsayilan).

    Yalnizca okuma/gosterim yolunda kullanilir. YAZMA yolunda
    `dnp3_extended_to_store` kullanilir — orada eksik alan TAMAMLANMAZ,
    yoksa her kayit islemi dokunulmamis alanlari diske sabitler.
    """
    base = Dnp3ExtendedSettings().model_dump()
    if not stored:
        return Dnp3ExtendedSettings.model_validate(base)
    if not isinstance(stored, dict):
        return Dnp3ExtendedSettings.model_validate(base)
    clean = {k: v for k, v in stored.items() if k not in ("tls_dnp3",)}
    base.update({k: v for k, v in clean.items() if k in base})
    return Dnp3ExtendedSettings.model_validate(base)


def dnp3_extended_to_store(value: object) -> dict | None:
    """Diske YAZILACAK sozluk — istemcinin ACIKCA gonderdigi alanlar.

    NEDEN (2026-08-07): yazma yolunda tum alanlari somutlastirmak, operatorun
    hic dokunmadigi ayarlari merkezi varsayilanlarla SABITLIYOR. master_address
    ornegi haberlesmeyi tamamen kesti (bkz. Dnp3ExtendedSettings docstring'i);
    ayni risk unsolicited_*, validate_source_address, session_timeout_* icin de
    gecerli. Pydantic `model_fields_set` istemcinin gercekten gonderdigi
    anahtarlari tutar; yalnizca onlar yazilir.
    """
    if value is None:
        return None
    if isinstance(value, Dnp3ExtendedSettings):
        return value.model_dump(exclude_unset=True)
    if isinstance(value, dict):
        return {k: v for k, v in value.items() if k not in ("tls_dnp3",)}
    return None
