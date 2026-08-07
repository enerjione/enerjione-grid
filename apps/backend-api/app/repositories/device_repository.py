from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import Session

from app.models.alarm import AlarmComment, AlarmEvent
from app.models.alarm_rule import AlarmRule
from app.models.device import Device
from app.models.device_command import DeviceCommand
from app.models.telemetry import Telemetry
from app.models.telemetry_history import TelemetryHistory
from app.schemas.device import DeviceCreate, DeviceUpdate
from app.schemas.dnp3_extended import dnp3_extended_to_store

# IEC 60870-5-104: 65535 yayin (broadcast) adresidir, cihaza atanamaz.
BROADCAST_COMMON_ADDRESS = 0xFFFF


class DeviceRepository:
    def __init__(self, db: Session):
        self.db = db

    def list_devices(self) -> list[Device]:
        stmt = select(Device).order_by(Device.name.asc())
        return list(self.db.scalars(stmt).all())

    def list_devices_by_gateway(self, gateway_code: str) -> list[Device]:
        stmt = select(Device).where(Device.gateway_code == gateway_code).order_by(Device.name.asc())
        return list(self.db.scalars(stmt).all())

    def get_by_code(self, code: str) -> Device | None:
        stmt = select(Device).where(Device.code == code)
        return self.db.scalar(stmt)

    def create(self, payload: DeviceCreate) -> Device:
        alanlar = payload.model_dump()
        # DNP3 ek ayarlari: yalnizca ISTEMCININ GONDERDIGI anahtarlar yazilir.
        # Tum alanlari somutlastirmak, operatorun dokunmadigi ayarlari merkezi
        # varsayilanlarla sabitler (2026-08-07: master_address=100 boyle
        # yazildi ve gercek cihazin haberlesmesini kesti — bkz.
        # schemas/dnp3_extended.py docstring'i).
        alanlar["dnp3_extended"] = dnp3_extended_to_store(payload.dnp3_extended)
        device = Device(**alanlar)
        if device.iec104_common_address is None:
            device.iec104_common_address = self.next_free_iec104_ca()
        self.db.add(device)
        self.db.flush()
        self.db.refresh(device)
        return device

    def next_free_iec104_ca(self) -> int:
        """Bir sonraki BOS IEC 104 Common Address'i doner.

        NEDEN OTOMATIK ATANIYOR
        -----------------------
        `iec104_common_address` nullable ve varsayilansizdi. IEC104 kayit
        defteri (`registry._resolve_device_ca`) deger yoksa target'in
        varsayilan CA'sina duser; IOA ise SINYALDEN gelir ve tum cihazlarda
        AYNIDIR. Yani her cihaza elle ayri CA atanmadikca:

            600 cihazin TAMAMI ayni (CA, IOA) ciftlerine biner.

        SCADA tarafinda 600 cihaz TEK sanal cihaza cokuyor; hangi fiderin
        arizalandigi anlasilamiyor ve son yazan deger digerlerini eziyor.
        Ustelik bu sessiz: hicbir yerde carpisma uyarisi basilmiyordu.

        CA 16 bit (1..65534; 65535 broadcast). 600 cihaz rahat sigar.
        """
        mevcut = self.db.scalar(select(func.max(Device.iec104_common_address)))
        # 1'den baslar; 0 bazi master'larda "tanimsiz" anlamina gelir.
        sonraki = int(mevcut or 0) + 1
        if sonraki >= BROADCAST_COMMON_ADDRESS:
            # Pratikte ulasilmaz (65534 cihaz); yine de sessiz sarmalama olmasin.
            raise ValueError(
                "IEC 104 Common Address alani doldu (65534). Kullanilmayan "
                "cihazlari silin ya da adresleri elle yeniden duzenleyin."
            )
        return sonraki

    def update(self, device: Device, payload: DeviceUpdate) -> Device:
        for key, value in payload.model_dump(exclude_unset=True).items():
            if key == "dnp3_extended":
                # Gonderilen anahtarlar MEVCUT kaydin uzerine biner; gonderilmeyen
                # alan OLDUGU GIBI kalir. Tam sozluk yazmak, dokunulmamis alanlari
                # merkezi varsayilanlarla ezerdi (bkz. create()).
                gelen = dnp3_extended_to_store(payload.dnp3_extended)
                if gelen is None:
                    setattr(device, key, None)
                else:
                    mevcut = dict(device.dnp3_extended or {})
                    mevcut.update(gelen)
                    setattr(device, key, mevcut)
                continue
            setattr(device, key, value)
        self.db.flush()
        return device

    def _remove_device_from_rule_filters(self, device_code: str) -> int:
        changed = 0
        rules = list(
            self.db.scalars(
                select(AlarmRule).where(AlarmRule.device_code_filter.is_not(None))
            ).all()
        )
        for rule in rules:
            codes = [code.strip() for code in (rule.device_code_filter or "").split(",") if code.strip()]
            remaining = [code for code in codes if code != device_code]
            if len(remaining) == len(codes):
                continue
            rule.device_code_filter = ",".join(remaining) or None
            if not remaining:
                rule.is_active = False
            changed += 1
        return changed

    def _delete_telemetry_and_alarms_for_device(self, device_id: int, device_code: str) -> dict[str, int]:
        event_ids = list(
            self.db.scalars(select(AlarmEvent.id).where(AlarmEvent.device_id == device_id)).all()
        )
        comments = 0
        if event_ids:
            result = self.db.execute(
                delete(AlarmComment).where(AlarmComment.alarm_event_id.in_(event_ids))
            )
            comments = int(result.rowcount or 0)
        alarm_result = self.db.execute(delete(AlarmEvent).where(AlarmEvent.device_id == device_id))
        telemetry_result = self.db.execute(delete(Telemetry).where(Telemetry.device_id == device_id))
        history_result = self.db.execute(
            delete(TelemetryHistory).where(TelemetryHistory.device_id == device_id)
        )
        command_result = self.db.execute(
            update(DeviceCommand)
            .where(
                DeviceCommand.device_code == device_code,
                DeviceCommand.status.in_(("pending", "sent")),
            )
            .values(status="cancelled", result_error="Cihaz silindi")
        )
        return {
            "alarm_comments": comments,
            "alarm_events": int(alarm_result.rowcount or 0),
            "telemetry": int(telemetry_result.rowcount or 0),
            "telemetry_history": int(history_result.rowcount or 0),
            "commands_cancelled": int(command_result.rowcount or 0),
            "alarm_rules_updated": self._remove_device_from_rule_filters(device_code),
        }

    def delete(self, device: Device) -> dict[str, int]:
        counts = self._delete_telemetry_and_alarms_for_device(device.id, device.code)
        self.db.delete(device)
        self.db.flush()
        return counts

    def delete_all_for_gateway(self, gateway_code: str) -> tuple[list[str], dict[str, int]]:
        """Gateway cihazlarini ayni transaction icinde temizle; commit caller'da."""
        devices = list(
            self.db.scalars(
                select(Device).where(Device.gateway_code == gateway_code).order_by(Device.id.asc())
            ).all()
        )
        total: dict[str, int] = {}
        for device in devices:
            counts = self._delete_telemetry_and_alarms_for_device(device.id, device.code)
            for key, value in counts.items():
                total[key] = total.get(key, 0) + value
            self.db.delete(device)
        self.db.flush()
        return [device.code for device in devices], total
