from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.models.alarm import AlarmComment, AlarmEvent
from app.models.device import Device
from app.models.telemetry import Telemetry
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
        self.db.commit()
        self.db.refresh(device)
        return device

    def update(self, device: Device, payload: DeviceUpdate) -> Device:
        for key, value in payload.model_dump(exclude_none=True).items():
            setattr(device, key, value)
        self.db.commit()
        self.db.refresh(device)
        return device

    def _delete_telemetry_and_alarms_for_device(self, device_id: int) -> None:
        event_ids = list(
            self.db.scalars(select(AlarmEvent.id).where(AlarmEvent.device_id == device_id)).all()
        )
        if event_ids:
            self.db.execute(delete(AlarmComment).where(AlarmComment.alarm_event_id.in_(event_ids)))
        self.db.execute(delete(AlarmEvent).where(AlarmEvent.device_id == device_id))
        self.db.execute(delete(Telemetry).where(Telemetry.device_id == device_id))

    def delete(self, device: Device) -> None:
        self._delete_telemetry_and_alarms_for_device(device.id)
        self.db.delete(device)
        self.db.commit()

    def delete_all_for_gateway(self, gateway_code: str) -> int:
        """Gateway silinmeden önce: tüm cihazlar, telemetri, alarm/ yorum.

        Eskiden cihaz başına 3 ayrı DELETE çalışıyordu (N cihaz × 3 = 3N RTT).
        Tek IN(...) ile toplu silmeye çevirdik; çok cihaz/çok satır olduğunda
        ciddi farkla hızlanır. Commit yok — caller commit eder.
        """
        device_ids = list(
            self.db.scalars(
                select(Device.id).where(Device.gateway_code == gateway_code)
            ).all()
        )
        if not device_ids:
            return 0
        # Önce alarm yorumları (FK), sonra alarm event'leri ve telemetri,
        # en son cihazların kendisi.
        event_ids = list(
            self.db.scalars(
                select(AlarmEvent.id).where(AlarmEvent.device_id.in_(device_ids))
            ).all()
        )
        if event_ids:
            self.db.execute(
                delete(AlarmComment).where(AlarmComment.alarm_event_id.in_(event_ids))
            )
        self.db.execute(delete(AlarmEvent).where(AlarmEvent.device_id.in_(device_ids)))
        self.db.execute(delete(Telemetry).where(Telemetry.device_id.in_(device_ids)))
        self.db.execute(delete(Device).where(Device.id.in_(device_ids)))
        return len(device_ids)
