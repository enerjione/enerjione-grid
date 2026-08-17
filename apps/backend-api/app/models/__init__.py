"""Tum ORM modelleri — `Base.metadata` icin TEK KAYNAK.

BU DOSYA EKSIK KALIRSA SAHA CIHAZI SESSIZCE BOZULUR
---------------------------------------------------
`scripts/migrate_db.py` semayi IKI ayri yoldan kurar:

  * TEMIZ kurulum (`alembic_version` yok): `Base.metadata.create_all()` +
    `alembic stamp head`. Migration'lar KOSMAZ.
  * MEVCUT kurulum: `alembic upgrade head`.

Yani temiz kurulumda bir tablonun olusmasinin TEK yolu, modelinin bu dosya
uzerinden `Base.metadata`'ya kayitli olmasidir. Burada eksik kalan bir model:

  * `create_all` tarafindan kurulmaz VE onu kuran migration da `stamp head`
    yuzunden kosmaz  ->  tablo sifirdan kurulan cihazda HIC OLUSMAZ,
  * `alembic revision --autogenerate` tarafindan "DB'de var, modelde yok"
    sanilir  ->  uretilen migration icin `op.drop_table` yazar.

Ikisi de hata vermez: gelistirici makinesi ve CI zaten YUKSELTME yolundan
gectigi icin yesil kalir. 2026-08-13 denetiminde `gateway_health`,
`device_purge_jobs`, `ftp_settings` ve `device_model_settings` tam boyle
kaybolmustu; `gateway_health`'in yoklugu staleness watchdog'u her turda
dusurup susmus gateway'in cihazlarini haritada ONLINE takili birakiyordu.

KURAL: `app/models/` altina yeni bir model dosyasi ekleyen, onu BURAYA da
ekler. `migrate_db.py` ve `alembic_migrations/env.py` artik kendi listelerini
tutmaz, yalnizca `import app.models` der — tek nokta burasi.
Bekcisi: `tests/test_model_kayit_tekligi.py`.
"""

from app.models.alarm import AlarmComment, AlarmDailyCount, AlarmEvent
from app.models.alarm_rule import AlarmRule
from app.models.api_key import ApiKey
from app.models.backup import BackupJob, BackupSchedule
from app.models.bulk_notification_job import BulkNotificationJob
from app.models.bulk_notification_template import BulkNotificationTemplate
from app.models.device import Device
from app.models.device_command import DeviceCommand
from app.models.device_config import DeviceConfigTemplate, DeviceConfigVersion
from app.models.device_model_settings import DeviceModelSettings
from app.models.device_purge_job import DevicePurgeJob
from app.models.fault import FaultComment, FaultEvent
from app.models.ftp_settings import FtpSettings
from app.models.gateway import Gateway
from app.models.gateway_health import GatewayHealth
from app.models.infra_notification import InfraNotificationState
from app.models.gateway_ingest_batch import GatewayIngestBatch
from app.models.grid_topology import Line, LineSegment, Pole, Region
from app.models.notification import Notification
from app.models.notification_settings import NotificationSettings
from app.models.outbound_target import (
    OutboundModbusSlot,
    OutboundTarget,
    OutboundTopicMapping,
)
from app.models.outbox_event import OutboxEvent
from app.models.processed_message import ProcessedMessage
from app.models.project_settings import ProjectSettings
from app.models.responsibility_area import ResponsibilityArea
from app.models.signal_catalog import SignalCatalog
from app.models.system_event import SystemEvent
from app.models.telemetry import Telemetry
from app.models.telemetry_history import TelemetryHistory
from app.models.telemetry_latest import TelemetryLatest
from app.models.unknown_device_telemetry import UnknownDeviceTelemetry
from app.models.user import User
from app.models.user_fcm_token import UserFcmToken
from app.models.user_notification_preference import UserNotificationPreference
from app.models.user_session import UserSession
from app.models.ws_ticket import WsTicket

__all__ = [
    "AlarmComment",
    "AlarmDailyCount",
    "AlarmEvent",
    "AlarmRule",
    "ApiKey",
    "BackupJob",
    "BackupSchedule",
    "BulkNotificationJob",
    "BulkNotificationTemplate",
    "Device",
    "DeviceCommand",
    "DeviceConfigTemplate",
    "DeviceConfigVersion",
    "DeviceModelSettings",
    "DevicePurgeJob",
    "FaultComment",
    "FaultEvent",
    "FtpSettings",
    "Gateway",
    "GatewayHealth",
    "InfraNotificationState",
    "GatewayIngestBatch",
    "Line",
    "LineSegment",
    "Notification",
    "NotificationSettings",
    "OutboundModbusSlot",
    "OutboundTarget",
    "OutboundTopicMapping",
    "OutboxEvent",
    "Pole",
    "ProcessedMessage",
    "ProjectSettings",
    "Region",
    "ResponsibilityArea",
    "SignalCatalog",
    "SystemEvent",
    "Telemetry",
    "TelemetryHistory",
    "TelemetryLatest",
    "UnknownDeviceTelemetry",
    "User",
    "UserFcmToken",
    "UserNotificationPreference",
    "UserSession",
    "WsTicket",
]
