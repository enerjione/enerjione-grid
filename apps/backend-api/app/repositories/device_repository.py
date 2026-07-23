from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from app.models.alarm import AlarmComment, AlarmEvent
from app.models.alarm_rule import AlarmRule
from app.models.device import Device
from app.models.device_command import DeviceCommand
from app.models.telemetry import Telemetry
from app.models.telemetry_history import TelemetryHistory
from app.schemas.device import DeviceCreate, DeviceUpdate


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
        device = Device(**payload.model_dump())
        self.db.add(device)
        self.db.flush()
        self.db.refresh(device)
        return device

    def update(self, device: Device, payload: DeviceUpdate) -> Device:
        for key, value in payload.model_dump(exclude_unset=True).items():
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
