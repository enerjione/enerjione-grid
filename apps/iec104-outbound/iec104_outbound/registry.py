"""IEC 104 nokta (point) kayit defteri.

Yeni model:
  - Her **cihaz** kendi ASDU Common Address'ine sahip olabilir
    (`device.iec104_common_address`). NULL ise outbound target'in default
    CA'si (`outbound_target.iec104_common_address`) kullanilir.
  - Her **sinyal** ait oldugu mutlak IOA'ya sahiptir (`signal.iec104_ioa`).
    Eski deploylar `iec104_ioa_offset` doldurmus olabilir; yeni alan dolu
    degilse offset mutlak IOA olarak yorumlanir.

Sonuc: tek TCP oturumu icinde farkli CA'lara ait ASDU'lar yayinlanir;
SCADA tarafi general interrogation'i CA bazli (veya broadcast=0xFFFF)
calistirabilir.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Iterable, Mapping

from iec104_outbound import runtime_health

logger = logging.getLogger(__name__)

# IEC 60870-5-101: 0xFFFF rezerve edilmis; "broadcast" anlami tasir, cihaza
# atanmasi onerilmez.
BROADCAST_COMMON_ADDRESS = 0xFFFF

# IEC 104 ile yayinlanan IZLEME tipleri — URUN KAPSAMI.
#
#   1  M_SP_NA_1  tek nokta (binary)      -> ariza/durum bitleri
#   13 M_ME_NC_1  kisa kayan nokta        -> olcumler
#   15 M_IT_NA_1  sayac
#
# Zaman etiketli karsiliklari (30 M_SP_TB_1, 36 M_ME_TF_1) sunucu tarafinda
# `with_timestamp` bayragina gore TUREETILIR; katalogda temel tip durur.
#
# KAPSAM DISI (bilincli urun karari):
#   * STRING (DNP3 Group 110 / octet string) — IEC 104 ile GONDERILMEZ.
#     Bu sinyaller REST/MQTT kanallarindan alinir.
#   * ANALOG CIKIS / setpoint (C_SE_*) — yayinlanmaz.
#   * BINARY OUTPUT — izleme noktasi olarak yayinlanmaz.
SUPPORTED_MONITORING_TYPES = frozenset({1, 13, 15})

# KONTROL YONU — kapsam KASITLI olarak dar.
#
# Urun karari: IEC 104 uzerinden kabul edilen TEK kontrol komutu ariza
# gostergesi RESET'idir. Analog cikis / setpoint (C_SE_NA/NB/NC) ve diger
# kontrol tipleri kapsam disidir.
#
# NEDEN AYRICA SLUG ALLOWLIST'I VAR: tip filtresi tek basina yetmez.
# `PATCH /signals/{key}` `iec104_type_id` alanini duzenlenebilir yapiyor;
# biri `firmware_update` ya da `config_update` gibi bir binary_output'a 45
# verirse, SADECE tipe bakan bir kontrol o komutu SCADA'ya acardi. Yani
# yanlis bir katalog duzenlemesi, uzaktan firmware tetiklemeye donusurdu.
# Slug allowlist'i bu yolu kapatiyor: tip 45 olsa bile listede olmayan
# komut registry'ye GIRMEZ.
SUPPORTED_COMMAND_TYPES = frozenset({45})
ALLOWED_COMMAND_SLUGS = frozenset({"reset_all_fcis"})


@dataclass(frozen=True)
class CommandAddress:
    """Bir kontrol noktasi: hangi (CA, IOA) hangi cihazin hangi komutu.

    `signal_key` katalogdaki tam anahtar ("master.reset_all_fcis"),
    `command_slug` ise backend'in bekledigi kisa ad ("reset_all_fcis").
    """

    device_code: str
    signal_key: str
    command_slug: str
    type_id: int
    common_address: int
    ioa: int


@dataclass(frozen=True)
class PointAddress:
    """Bir veri noktasi: hangi cihazin hangi sinyali, hangi (CA, IOA) ile yayinlanir."""

    device_code: str
    signal_key: str
    type_id: int
    common_address: int
    ioa: int
    # Sinyal IEC 104 mesajinda CP56Time2a zaman etiketi tasiyacak mi?
    # True ise server, type_id 1 yerine 30 (M_SP_TB_1), 13 yerine 36
    # (M_ME_TF_1) versiyonunu kullanir. Counter (15) icin henuz timestamp
    # destegi eklenmedi (M_IT_TB_1 = 37) — gerekirse genisletilebilir.
    with_timestamp: bool = False


@dataclass(frozen=True)
class PointRegistry:
    """Bir outbound target'in tam point listesi.

    `default_common_address`: cihaz icin ozel CA tanimlanmamissa kullanilan
    fallback. Interrogation broadcast'inde "varsa hangi CA'lar var" sorgusu
    `unique_common_addresses` ile karsilanir.
    """

    target_id: int
    default_common_address: int
    points: tuple[PointAddress, ...]
    #: Kontrol noktalari. Izleme noktalarindan AYRI tutuluyor: ikisi ayni
    #: (CA, IOA) uzayini paylassa da anlamlari zit yonde ve karistirilmasi
    #: bir izleme noktasini yazilabilir gostermek demek olurdu.
    commands: tuple[CommandAddress, ...] = ()

    def command_by_address(self) -> dict[tuple[int, int], CommandAddress]:
        """(common_address, ioa) -> CommandAddress.

        Carpisma UYARI degil HATA seviyesinde: yanlis cihaza reset gonderir.
        """
        result: dict[tuple[int, int], CommandAddress] = {}
        for c in self.commands:
            key = (c.common_address, c.ioa)
            if key in result:
                logger.error(
                    "iec104_komut_adres_carpismasi ca=%d ioa=%d prev=%s new=%s — "
                    "ayni adres iki cihaza bakiyor, reset YANLIS cihaza gidebilir",
                    c.common_address, c.ioa, result[key].device_code, c.device_code,
                )
            result[key] = c
        return result

    def by_key(self) -> dict[tuple[str, str], PointAddress]:
        return {(p.device_code, p.signal_key): p for p in self.points}

    def by_address(self) -> dict[tuple[int, int], PointAddress]:
        """(common_address, ioa) -> PointAddress. Carismalari log'lar (last-wins)."""
        result: dict[tuple[int, int], PointAddress] = {}
        for p in self.points:
            key = (p.common_address, p.ioa)
            if key in result:
                logger.warning(
                    "iec104_registry_collision ca=%d ioa=%d prev=(%s,%s) new=(%s,%s)",
                    p.common_address, p.ioa,
                    result[key].device_code, result[key].signal_key,
                    p.device_code, p.signal_key,
                )
            result[key] = p
        return result

    def unique_common_addresses(self) -> tuple[int, ...]:
        return tuple(sorted({p.common_address for p in self.points}))


def signal_key_slug(key: str) -> str:
    """Katalog anahtarindan komut slug'i: "master.reset_all_fcis" -> "reset_all_fcis".

    Backend'in komut ucu kaynak onekini beklemiyor; ayrica yalnizca `master.`
    onekli binary_output'lar komut olabiliyor (uydularin kendi kontrol
    noktasi yok).
    """
    return key.split(".", 1)[1] if "." in key else key


def _resolve_signal_ioa(signal: Mapping) -> int | None:
    """Yeni `iec104_ioa` doluysa onu; degilse eski `iec104_ioa_offset`'i mutlak IOA olarak kullanir."""
    raw = signal.get("iec104_ioa")
    if raw is None:
        raw = signal.get("iec104_ioa_offset")
    if raw is None:
        return None
    try:
        ioa = int(raw)
    except (TypeError, ValueError):
        return None
    if not 0 <= ioa <= 0xFFFFFF:
        return None
    return ioa


def _resolve_device_ca(device: Mapping, *, default: int) -> int:
    raw = device.get("iec104_common_address")
    if raw is None:
        return default
    try:
        ca = int(raw)
    except (TypeError, ValueError):
        return default
    if not 0 <= ca <= 0xFFFE:
        return default
    return ca


def build_point_registry(
    *,
    target_id: int,
    default_common_address: int,
    devices: Iterable[Mapping],
    signals: Iterable[Mapping],
) -> PointRegistry:
    """Cihazlar + sinyal kataloguna gore deterministik bir point listesi uretir.

    - Sadece `is_active=True` cihazlar (default True).
    - Sadece `iec104_type_id` + (`iec104_ioa` ya da legacy `iec104_ioa_offset`)
      dolu olan sinyaller.
    - (CA, IOA) carismasi durumu log'lanir, son ekleme kazanir.
    """
    if not 0 <= default_common_address <= 0xFFFE:
        raise ValueError(f"default_common_address {default_common_address} out of range")

    active_devices = sorted(
        (d for d in devices if d.get("is_active", True)),
        key=lambda d: str(d.get("code") or ""),
    )
    mapped_signals: list[tuple[Mapping, int, int, bool]] = []
    komut_adaylari: list[tuple[Mapping, int, int, str]] = []
    desteklenmeyen: list[str] = []
    reddedilen_komut: list[str] = []
    for s in signals:
        if not s.get("is_active", True):
            continue
        # SINYAL BAZINDA YAYIN KAPATMA — backend registry'si bunu filtreliyordu
        # ama BU servis (SCADA'nin gercekten konustugu servis) alani HIC
        # OKUMUYORDU. Operator arayuzden bir sinyalin IEC 104 yayinini
        # kapatiyor, arayuz ve CSV disa aktarim "kapali" gosteriyor, veri ise
        # SCADA'ya AKMAYA DEVAM EDIYORDU.
        if s.get("iec104_enabled", True) is False:
            continue
        # Katalogta duran ama o cihaz kaydinda HIC saklanmayan nokta
        # (Pole Master Kit'in uydu satirlari — telemetrileri SETLERE
        # yonlendirilir) adres tablosuna girmemeli; girerse SCADA'ya hicbir
        # zaman veri gelmeyen yuzlerce adres bildirilir. Karar backend'de
        # tek yerde uretilir: SignalCatalog.outbound_eligible.
        if s.get("outbound_eligible", True) is False:
            continue
        type_id_raw = s.get("iec104_type_id")
        if type_id_raw is None:
            continue
        ioa = _resolve_signal_ioa(s)
        if ioa is None:
            continue
        try:
            type_id = int(type_id_raw)
        except (TypeError, ValueError):
            continue

        # KAPSAM: IEC 104 ile YALNIZCA izleme tipleri yayinlanir.
        #
        #   1  M_SP_NA_1  tek nokta (binary)
        #   13 M_ME_NC_1  kisa kayan nokta (analog)
        #   15 M_IT_NA_1  sayac
        #
        # STRING YOK: DNP3 Group 110 (octet string) sinyalleri IEC 104 ile
        # gonderilmiyor (urun karari). ANALOG CIKIS YOK: setpoint/analog
        # output tipleri kapsam disi.
        #
        # Bu filtre NEDEN GEREKLI: katalogda string ve binary_output sinyalleri
        # `iec104_type_id = NULL` ile geliyor, yani veri zaten uygun. Ama
        # `PATCH /signals/{key}` bu alani DUZENLENEBILIR yapiyor; biri string
        # bir sinyale type id verirse nokta registry'ye girer, sonra
        # `_encode_single_value` onu tanimayip None doner ve bildirim HER
        # SEFERINDE SESSIZCE duser. Yani yanlis yapilandirma calisir gorunur.
        # Politikayi burada, tek yerde sabitliyoruz.
        if type_id in SUPPORTED_COMMAND_TYPES:
            # KONTROL YONU. Izleme noktasi degil; ayri listeye alinir ve
            # slug allowlist'inden gecmesi gerekir (bkz. ALLOWED_COMMAND_SLUGS).
            slug = signal_key_slug(str(s.get("key") or ""))
            if slug in ALLOWED_COMMAND_SLUGS:
                komut_adaylari.append((s, type_id, ioa, slug))
            else:
                reddedilen_komut.append(f"{s.get('key')}(type={type_id})")
            continue
        if type_id not in SUPPORTED_MONITORING_TYPES:
            desteklenmeyen.append(f"{s.get('key')}(type={type_id})")
            continue
        # Zaman etiketi flag'i: backend SignalCatalog.iec104_with_timestamp.
        # default: dijital (binary) sinyallerde True, analog (float/counter)'de
        # False. Backend default'lari katalog seed'inde set ediyor; eski
        # kayitlarda da is_digital fallback ile dolduruluyor.
        with_ts = bool(s.get("iec104_with_timestamp", False))
        mapped_signals.append((s, type_id, ioa, with_ts))

    if desteklenmeyen:
        # Operator bir sinyale kapsam disi bir tip vermis. Sessiz kalirsak
        # nokta yayinlanmaz ve sebebi hicbir yerde gorunmez; SCADA tarafinda
        # "adres listesinde var ama veri gelmiyor" olarak yasanir.
        logger.warning(
            "iec104_kapsamdisi_tip target=%s adet=%d ornekler=%s — IEC 104 ile "
            "yalnizca izleme tipleri (1/13/15) yayinlanir; string ve analog "
            "cikis kapsam disidir",
            target_id,
            len(desteklenmeyen),
            ",".join(sorted(desteklenmeyen)[:10]),
        )

    if reddedilen_komut:
        # Katalogda kontrol tipi verilmis ama allowlist'te olmayan bir komut.
        # Sessiz kalmak TEHLIKELI olurdu: operator SCADA'dan komut
        # gonderebilecegini sanir, komut hicbir zaman islenmez ve sebebi
        # hicbir yerde gorunmez. ERROR, cunku bu genelde yanlis bir katalog
        # duzenlemesidir (or. firmware_update'e tip 45 verilmesi).
        logger.error(
            "iec104_izinsiz_komut target=%s adet=%d ornekler=%s — IEC 104 ile "
            "yalnizca %s komutlari kabul edilir",
            target_id, len(reddedilen_komut),
            ",".join(sorted(reddedilen_komut)[:10]),
            ",".join(sorted(ALLOWED_COMMAND_SLUGS)),
        )

    # MODEL FILTRESI — ZORUNLU.
    #
    # Nokta uretimi cihaz x sinyal KARTEZYEN carpimidir. Katalog tek modelli
    # oldugu surece bu tesadufen dogru calisiyordu. Ikinci bir model
    # eklendiginde SN2 cihazlarina Pole Master Kit sinyalleri (ve tersi)
    # yapisir: SCADA'ya hicbir zaman veri gelmeyen yuzlerce nokta bildirilir
    # ve ayni (CA, IOA) cifti iki farkli sinyale duserek carpisir. Carpisma
    # bu serviste HIC loglanmiyor (`by_address` uretim kodunda cagrilmiyor),
    # yani belirti tamamen sessiz olurdu.
    signals_by_model: dict[str, list] = {}
    for eslesme in mapped_signals:
        signals_by_model.setdefault(str(eslesme[0].get("model") or ""), []).append(eslesme)

    commands_by_model: dict[str, list] = {}
    for eslesme in komut_adaylari:
        commands_by_model.setdefault(str(eslesme[0].get("model") or ""), []).append(eslesme)

    points: list[PointAddress] = []
    commands: list[CommandAddress] = []
    # Adresi olmayan cihazlar — hepsi varsayilan CA'ya duser ve BIRBIRININ
    # UZERINE yazar. Sessiz kalmamali (bkz. asagidaki uyari).
    adressiz: list[str] = []
    for device in active_devices:
        device_code = str(device.get("code") or "")
        if not device_code:
            continue
        if device.get("iec104_common_address") is None:
            adressiz.append(device_code)
        ca = _resolve_device_ca(device, default=default_common_address)
        device_model = str(device.get("model") or "")

        # SISTEM NOKTALARI — her cihazda, CIHAZ BASINA AYRI IOA.
        #
        # Calisma-zamani sagligi bir SAHA OLCUMU degil; gateway'in cihazla
        # olan oturumu hakkinda. Katalogda DNP3 sinyali olarak durmuyor
        # (CSV/katalog disa aktarimlarinda gercek bir cihaz noktasiymis gibi
        # gorunurdu) ama ADRESLEME ayni yoldan gecer.
        #
        # IOA `device_id`den turer: `iec104_common_address` NULLABLE ve
        # UNIQUE KISITI YOK, dolayisiyla iki cihaz ayni CA'yi paylasabilir.
        # Sabit bir IOA kullanmak o durumda CAKISMA GARANTISI olurdu —
        # sistem noktalari HER cihazda bulundugu icin.
        device_id = device.get("id")
        if device_id is None:
            # Kimliksiz cihaza deterministik adres uretilemez. Sessizce
            # sabit bir IOA vermek cakismaya doner; nokta URETILMEZ ve
            # sebep loglanir.
            logger.warning(
                "iec104_sistem_noktasi_atlandi target=%s device=%s — cihaz "
                "kimligi yok, deterministik IOA uretilemiyor",
                target_id, device_code,
            )
        else:
            for sistem in runtime_health.system_signals_for_device(
                int(device_id), device_model
            ):
                points.append(
                    PointAddress(
                        device_code=device_code,
                        signal_key=str(sistem["key"]),
                        type_id=int(sistem["iec104_type_id"]),
                        common_address=ca,
                        ioa=int(sistem["iec104_ioa"]),
                        with_timestamp=True,
                    )
                )

        for signal, type_id, ioa, with_ts in signals_by_model.get(device_model, ()):
            signal_key = str(signal.get("key") or "")
            if not signal_key:
                continue
            points.append(
                PointAddress(
                    device_code=device_code,
                    signal_key=signal_key,
                    type_id=type_id,
                    common_address=ca,
                    ioa=ioa,
                    with_timestamp=with_ts,
                )
            )
        for signal, type_id, ioa, slug in commands_by_model.get(device_model, ()):
            signal_key = str(signal.get("key") or "")
            if not signal_key:
                continue
            commands.append(
                CommandAddress(
                    device_code=device_code,
                    signal_key=signal_key,
                    command_slug=slug,
                    type_id=type_id,
                    common_address=ca,
                    ioa=ioa,
                )
            )

    # CARPISMA UYARISI — eskiden HIC basilmiyordu.
    #
    # `iec104_common_address` cihazda NULL ise varsayilan CA'ya duselir; IOA
    # ise sinyalden gelir ve tum cihazlarda AYNIDIR. Yani adressiz iki cihaz
    # ayni (CA, IOA) ciftine biner ve SCADA'da TEK cihaza coker: hangi fiderin
    # arizalandigi anlasilmaz, son yazan deger digerini ezer.
    #
    # Backend artik cihaz olustururken adres atiyor ve migration 0029 mevcut
    # kayitlari dolduruyor; buraya dusulmesi eski bir kayit ya da elle
    # NULL'a cekilmis bir cihaz demektir. Bu yuzden ERROR seviyesinde.
    if len(adressiz) > 1:
        logger.error(
            "iec104_ca_carpismasi target=%s adressiz_cihaz=%d ornekler=%s — "
            "hepsi ayni (CA=%d, IOA) ciftine biniyor ve SCADA'da TEK cihaza "
            "cokuyor. Cihazlara ayri Common Address atayin.",
            target_id,
            len(adressiz),
            ",".join(sorted(adressiz)[:10]),
            default_common_address,
        )
    elif adressiz:
        logger.warning(
            "iec104_ca_yok target=%s cihaz=%s — varsayilan CA=%d kullaniliyor",
            target_id, adressiz[0], default_common_address,
        )

    return PointRegistry(
        target_id=target_id,
        default_common_address=default_common_address,
        points=tuple(points),
        commands=tuple(commands),
    )
