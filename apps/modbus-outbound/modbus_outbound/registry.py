"""Adres plani -> calisma zamani veri deposu + hizli arama.

Backend'in urettigi plan (`/internal/modbus-plans`) burada iki yapiya donusur:

  stores  : unit_id -> UnitStore   (SCADA'nin okudugu register/bit degerleri)
  lookup  : (device_code, signal_key) -> [Placement, ...]

Telemetri geldiginde lookup ile O(1) yerlestirme bulunur ve deger kodlanip
store'a yazilir. Adres hesabi BURADA YAPILMAZ — backend'den geldigi gibi
kullanilir; boylece web arayuzundeki adres tablosu ile sahada yayinlanan
adres arasinda ayrisma imkansizdir.

Depolama sparse (dict): 65536'lik dizi ayirmak yerine sadece dolu adresler
tutulur. Okunmamis adres 0 doner (SCADA blok halinde okurken bosluklar
hataya degil sifira denk gelsin).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from threading import RLock

from modbus_outbound.codec import coerce_bool, coerce_number, encode_registers

FC_COIL = 1
FC_DISCRETE_INPUT = 2
FC_HOLDING_REGISTER = 3
FC_INPUT_REGISTER = 4

MODBUS_ADDRESS_SPACE = 65_536


@dataclass(frozen=True)
class Placement:
    """Bir sinyalin tek bir hedefteki somut yeri."""

    unit_id: int
    function: int
    address: int
    word_count: int
    data_type: str
    scale: float
    offset: float


@dataclass
class UnitStore:
    """Tek bir Modbus unit (slave) icin veri alanlari.

    holding ve input register AYNI sozlugu paylasir: ayni icerik hem FC3 hem
    FC4 ile okunabilir. Sebep: SCADA'lar hangi alani okuyacagi konusunda ikiye
    bolunur ve "yanlis alani okuma" entegrasyonda en sik yasanan sorundur.
    """

    registers: dict[int, int] = field(default_factory=dict)
    discrete_inputs: dict[int, bool] = field(default_factory=dict)
    coils: dict[int, bool] = field(default_factory=dict)

    def read_registers(self, address: int, count: int) -> list[int]:
        return [self.registers.get(address + i, 0) for i in range(count)]

    def read_discrete(self, address: int, count: int) -> list[bool]:
        return [self.discrete_inputs.get(address + i, False) for i in range(count)]

    def read_coils(self, address: int, count: int) -> list[bool]:
        return [self.coils.get(address + i, False) for i in range(count)]


class PointRegistry:
    """Bir Modbus hedefinin tum unit'leri + sinyal yerlesimi.

    Thread-safety: telemetri consumer'i kendi thread'inde yazar, Modbus server
    asyncio loop'unda okur. Yazma/okuma tek bir RLock ile korunur; degerler
    kucuk oldugu icin kilit suresi ihmal edilebilir.
    """

    def __init__(
        self,
        *,
        target_id: int,
        value_format: str = "int16",
        word_order: str = "big",
    ) -> None:
        self.target_id = target_id
        self.value_format = value_format
        self.word_order = word_order
        self.stores: dict[int, UnitStore] = {}
        self.lookup: dict[tuple[str, str], list[Placement]] = {}
        self._lock = RLock()
        self.updates_applied = 0
        self.updates_unmapped = 0
        # Eslesme + kalite sorunu YOK ama deger sayiya cevrilemedi (bos,
        # metin, vb.) — eskiden burada SESSIZCE `continue` vardi ve
        # "yazilan=0" teshis edilemiyordu (bkz. consumer.py docstring'i).
        self.updates_uncoercible = 0

    # ---- Kurulum ----------------------------------------------------------
    def add_point(
        self,
        *,
        device_code: str,
        signal_key: str,
        unit_id: int,
        function: int,
        address: int,
        word_count: int,
        data_type: str,
        scale: float,
        offset: float,
    ) -> None:
        if not (0 <= address < MODBUS_ADDRESS_SPACE):
            # Plan bozuksa sessizce yanlis adrese yazmaktansa noktayi atla.
            return
        placement = Placement(
            unit_id=unit_id, function=function, address=address,
            word_count=word_count, data_type=data_type,
            scale=scale, offset=offset,
        )
        self.lookup.setdefault((device_code, signal_key), []).append(placement)
        self.stores.setdefault(unit_id, UnitStore())

    @property
    def point_count(self) -> int:
        return sum(len(v) for v in self.lookup.values())

    def unit_ids(self) -> list[int]:
        return sorted(self.stores.keys())

    # ---- Calisma zamani ---------------------------------------------------
    def update(self, device_code: str, signal_key: str, value) -> int:
        """Bir sinyal degerini ilgili tum adreslere yaz. Yazilan nokta sayisi doner."""
        placements = self.lookup.get((device_code, signal_key))
        if not placements:
            with self._lock:
                self.updates_unmapped += 1
            return 0

        written = 0
        with self._lock:
            for p in placements:
                store = self.stores.get(p.unit_id)
                if store is None:
                    continue
                if p.function == FC_DISCRETE_INPUT:
                    store.discrete_inputs[p.address] = coerce_bool(value)
                    written += 1
                elif p.function == FC_COIL:
                    store.coils[p.address] = coerce_bool(value)
                    written += 1
                else:
                    number = coerce_number(value)
                    if number is None:
                        self.updates_uncoercible += 1
                        continue
                    words = encode_registers(
                        number,
                        data_type=p.data_type,
                        value_format=self.value_format,
                        word_order=self.word_order,
                        scale=p.scale,
                        offset=p.offset,
                    )
                    for i, word in enumerate(words[: p.word_count or len(words)]):
                        store.registers[p.address + i] = word
                    written += 1
            self.updates_applied += written
        return written

    # ---- Okuma (Modbus server tarafi) -------------------------------------
    def read(self, unit_id: int, function: int, address: int, count: int):
        """Server icin okuma. Bilinmeyen unit -> None (gateway hatasi dondurulur)."""
        with self._lock:
            store = self.stores.get(unit_id)
            if store is None:
                return None
            if function in (FC_HOLDING_REGISTER, FC_INPUT_REGISTER):
                return store.read_registers(address, count)
            if function == FC_DISCRETE_INPUT:
                return store.read_discrete(address, count)
            if function == FC_COIL:
                return store.read_coils(address, count)
            return None


def build_registry_from_plan(plan: dict) -> PointRegistry:
    """Backend'den gelen plan sozlugunu PointRegistry'ye cevir."""
    registry = PointRegistry(
        target_id=int(plan.get("target_id") or 0),
        value_format=str(plan.get("value_format") or "int16"),
        word_order=str(plan.get("word_order") or "big"),
    )
    for point in plan.get("points") or []:
        try:
            registry.add_point(
                device_code=str(point["device_code"]),
                signal_key=str(point["signal_key"]),
                unit_id=int(point["unit_id"]),
                function=int(point["function"]),
                address=int(point["address"]),
                word_count=int(point.get("word_count") or 0),
                data_type=str(point.get("data_type") or "analog"),
                scale=float(point.get("scale") if point.get("scale") is not None else 1.0),
                offset=float(point.get("offset") if point.get("offset") is not None else 0.0),
            )
        except (KeyError, TypeError, ValueError):
            continue
    # Plan'da cihazi olup noktasi olmayan unit'ler de bos store almali ki
    # SCADA o unit'i sordugunda "gateway yok" degil, sifir dolu cevap alsin.
    for device in plan.get("devices") or []:
        try:
            registry.stores.setdefault(int(device["unit_id"]), UnitStore())
        except (KeyError, TypeError, ValueError):
            continue
    return registry
