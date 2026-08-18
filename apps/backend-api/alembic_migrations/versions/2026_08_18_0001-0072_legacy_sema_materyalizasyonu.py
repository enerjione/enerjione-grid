"""0072 — legacy sema materyalizasyonu (Alembic sema otoritesi)

NEDEN VAR
---------
Bu surume kadar TEMIZ KURULUM semayi Alembic'ten DEGIL,
`Base.metadata.create_all()` + `alembic stamp head` ikilisinden aliyordu.
Sebep olculdu: 0001 baseline'i BOS (`pass`) ve 50 model tablosunun 39'unu
hicbir migration kurmuyordu (`users`, `devices`, `gateways`, `alarm_events`
dahil). Bos bir veritabaninda `alembic upgrade head` 71 revizyonun 52'sinde
"UndefinedTable" ile kiriliyor ve sonunda 8 tablo birakiyordu.

Yani sema otoritesi fiilen SQLAlchemy modelleriydi; Alembic yalnizca
sonradan gelen degisiklikleri tasiyordu.

BU MIGRATION NE YAPAR
---------------------
Guncel uygulama semasinin TAMAMINI explicit Alembic operasyonlariyla kurar.
Boylece sema, model dosyalarindan degil, VERSIYONLANMIS bir migration'dan
uretilebilir hale gelir.

IKI YOL, TEK SONUC
------------------
  * TEMIZ KURULUM : bos DB -> `alembic stamp 0071` -> `upgrade head`
    0072 kosar ve semayi bastan kurar. `create_all` KULLANILMAZ.
  * MEVCUT KURULUM: sema zaten 0071'de ve tablolar mevcut. 0072 hicbir
    tabloyu yeniden kurmaz; yalnizca `alembic_version` ilerler (NO-OP).

NEDEN TABLO DUZEYINDE GUARD
---------------------------
Guard'lar tablo VARLIGINA bakar, index/kolon duzeyine INMEZ. Bu bilincli:
0020 ve 0022 hot-path index'lerini bilerek tasiyip DUSURUYOR. Mevcut bir
tabloda model index'lerini "eksik" sanip yeniden kurmak, o migration'larin
kararini SESSIZCE GERI ALIRDI. Tablo varsa ona hic dokunulmaz.

NEDEN `create_all` DEGIL
------------------------
Migration govdesinde `Base.metadata.create_all()` cagirmak bagimliligi
yalnizca baska bir dosyaya tasirdi: sema yine modelden uretilirdi ve
migration gecmisi semayi TARIF ETMEZDI. Operasyonlar bu yuzden explicit.

0001-0071 DEGISTIRILMEDI. Bu migration tarihi borcu tek seferde Alembic
otoritesine devreder.
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0072"
down_revision: Union[str, None] = "0071"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _mevcut_tablolar() -> set[str]:
    return set(sa.inspect(op.get_bind()).get_table_names())


def upgrade() -> None:
    mevcut = _mevcut_tablolar()

    if "alarm_rules" not in mevcut:
        op.create_table('alarm_rules',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('signal_key', sa.String(length=80), nullable=False),
        sa.Column('name', sa.String(length=160), nullable=False),
        sa.Column('description', sa.String(length=500), nullable=True),
        sa.Column('level', sa.String(length=20), nullable=False),
        sa.Column('rule_kind', sa.String(length=20), nullable=False),
        sa.Column('expression_json', sa.Text(), nullable=True),
        sa.Column('comparator', sa.String(length=20), nullable=False),
        sa.Column('threshold', sa.Float(), nullable=False),
        sa.Column('threshold_high', sa.Float(), nullable=True),
        sa.Column('hysteresis', sa.Float(), nullable=False),
        sa.Column('debounce_sec', sa.Integer(), nullable=False),
        sa.Column('device_code_filter', sa.String(length=500), nullable=True),
        sa.Column('device_model_filter', sa.String(length=500), nullable=True),
        sa.Column('is_active', sa.Boolean(), nullable=False),
        sa.Column('notify_email', sa.Boolean(), nullable=False),
        sa.Column('notify_sms', sa.Boolean(), nullable=False),
        sa.Column('notify_telegram', sa.Boolean(), nullable=False),
        sa.Column('notify_whatsapp_web', sa.Boolean(), nullable=False),
        sa.Column('produces_fault', sa.Boolean(), nullable=False),
        sa.PrimaryKeyConstraint('id')
        )
        op.create_index(op.f('ix_alarm_rules_id'), 'alarm_rules', ['id'], unique=False)
        op.create_index(op.f('ix_alarm_rules_signal_key'), 'alarm_rules', ['signal_key'], unique=False)

    if "backup_jobs" not in mevcut:
        op.create_table('backup_jobs',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('job_type', sa.String(length=20), nullable=False),
        sa.Column('status', sa.String(length=20), nullable=False),
        sa.Column('file_path', sa.String(length=500), nullable=True),
        sa.Column('size_bytes', sa.Integer(), nullable=True),
        sa.Column('error_message', sa.String(length=2000), nullable=True),
        sa.Column('created_by_username', sa.String(length=120), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint('id')
        )
        op.create_index(op.f('ix_backup_jobs_created_at'), 'backup_jobs', ['created_at'], unique=False)
        op.create_index(op.f('ix_backup_jobs_job_type'), 'backup_jobs', ['job_type'], unique=False)
        op.create_index(op.f('ix_backup_jobs_status'), 'backup_jobs', ['status'], unique=False)

    if "backup_schedule" not in mevcut:
        op.create_table('backup_schedule',
        sa.Column('id', sa.Integer(), autoincrement=False, nullable=False),
        sa.Column('enabled', sa.Boolean(), nullable=False),
        sa.Column('interval_hours', sa.Integer(), nullable=False),
        sa.Column('retention_count', sa.Integer(), nullable=False),
        sa.Column('last_run_at', sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint('id')
        )

    if "device_commands" not in mevcut:
        op.create_table('device_commands',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('gateway_code', sa.String(length=50), nullable=False),
        sa.Column('device_code', sa.String(length=50), nullable=False),
        sa.Column('command', sa.String(length=80), nullable=False),
        sa.Column('dnp3_index', sa.Integer(), nullable=False),
        sa.Column('op_type', sa.String(length=20), nullable=False),
        sa.Column('count', sa.Integer(), nullable=False),
        sa.Column('on_time_ms', sa.Integer(), nullable=False),
        sa.Column('off_time_ms', sa.Integer(), nullable=False),
        sa.Column('status', sa.String(length=20), nullable=False),
        sa.Column('result_status', sa.String(length=40), nullable=True),
        sa.Column('result_error', sa.String(length=500), nullable=True),
        sa.Column('actor_username', sa.String(length=150), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('sent_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('delivery_token', sa.String(length=64), nullable=True),
        sa.Column('delivery_lease_until', sa.DateTime(timezone=True), nullable=True),
        sa.Column('delivery_attempt', sa.Integer(), server_default='0', nullable=False),
        sa.Column('delivery_gateway_epoch', sa.String(length=64), nullable=True),
        sa.PrimaryKeyConstraint('id')
        )
        op.create_index(op.f('ix_device_commands_created_at'), 'device_commands', ['created_at'], unique=False)
        op.create_index('ix_device_commands_delivery_lease', 'device_commands', ['gateway_code', 'status', 'delivery_lease_until'], unique=False)
        op.create_index(op.f('ix_device_commands_device_code'), 'device_commands', ['device_code'], unique=False)
        op.create_index(op.f('ix_device_commands_gateway_code'), 'device_commands', ['gateway_code'], unique=False)
        op.create_index(op.f('ix_device_commands_id'), 'device_commands', ['id'], unique=False)
        op.create_index(op.f('ix_device_commands_status'), 'device_commands', ['status'], unique=False)

    if "device_config_templates" not in mevcut:
        op.create_table('device_config_templates',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(length=120), nullable=False),
        sa.Column('device_model', sa.String(length=80), nullable=False),
        sa.Column('raw', sa.LargeBinary(), nullable=False),
        sa.Column('source_filename', sa.String(length=200), nullable=True),
        sa.Column('note', sa.Text(), nullable=True),
        sa.Column('is_default', sa.Boolean(), nullable=False),
        sa.Column('created_by', sa.String(length=80), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('id')
        )
        op.create_index(op.f('ix_device_config_templates_device_model'), 'device_config_templates', ['device_model'], unique=False)
        op.create_index(op.f('ix_device_config_templates_id'), 'device_config_templates', ['id'], unique=False)

    if "device_model_settings" not in mevcut:
        op.create_table('device_model_settings',
        sa.Column('model', sa.String(length=80), nullable=False),
        sa.Column('battery_voltage_low', sa.Float(), nullable=True),
        sa.Column('battery_voltage_full', sa.Float(), nullable=True),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint('model')
        )

    if "device_purge_jobs" not in mevcut:
        op.create_table('device_purge_jobs',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('device_id', sa.Integer(), nullable=False),
        sa.Column('device_code', sa.String(length=50), nullable=False),
        sa.Column('device_name', sa.String(length=255), nullable=True),
        sa.Column('requested_by', sa.String(length=120), nullable=True),
        sa.Column('requested_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('state', sa.String(length=20), nullable=False),
        sa.Column('rows_deleted', sa.Integer(), nullable=False),
        sa.Column('attempts', sa.Integer(), nullable=False),
        sa.Column('last_error', sa.Text(), nullable=True),
        sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint('id')
        )
        op.create_index(op.f('ix_device_purge_jobs_device_id'), 'device_purge_jobs', ['device_id'], unique=False)
        op.create_index(op.f('ix_device_purge_jobs_requested_at'), 'device_purge_jobs', ['requested_at'], unique=False)
        op.create_index(op.f('ix_device_purge_jobs_state'), 'device_purge_jobs', ['state'], unique=False)

    if "ftp_settings" not in mevcut:
        op.create_table('ftp_settings',
        sa.Column('id', sa.Integer(), autoincrement=False, nullable=False),
        sa.Column('mode', sa.String(length=10), nullable=False),
        sa.Column('embedded_username', sa.String(length=30), nullable=False),
        sa.Column('embedded_password_enc', sa.Text(), nullable=True),
        sa.Column('embedded_host', sa.String(length=200), nullable=True),
        sa.Column('host', sa.String(length=200), nullable=True),
        sa.Column('port', sa.Integer(), nullable=False),
        sa.Column('username', sa.String(length=30), nullable=False),
        sa.Column('password_enc', sa.Text(), nullable=True),
        sa.Column('directory', sa.String(length=200), nullable=False),
        sa.Column('poll_interval_sec', sa.Integer(), nullable=False),
        sa.Column('updated_by', sa.String(length=80), nullable=True),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint('id')
        )

    if "gateway_health" not in mevcut:
        op.create_table('gateway_health',
        sa.Column('gateway_code', sa.String(length=50), nullable=False),
        sa.Column('status', sa.String(length=20), nullable=False),
        sa.Column('issues', sa.String(length=1000), nullable=True),
        sa.Column('outbox_pending', sa.Integer(), nullable=True),
        sa.Column('outbox_dead_letter', sa.Integer(), nullable=True),
        sa.Column('devices_total', sa.Integer(), nullable=True),
        sa.Column('devices_online', sa.Integer(), nullable=True),
        sa.Column('devices_recovering', sa.Integer(), nullable=True),
        sa.Column('devices_lost', sa.Integer(), nullable=True),
        sa.Column('uptime_sec', sa.Integer(), nullable=True),
        sa.Column('gateway_version', sa.String(length=40), nullable=True),
        sa.Column('raw_json', sa.Text(), nullable=True),
        sa.Column('reported_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('gateway_code')
        )
        op.create_index(op.f('ix_gateway_health_reported_at'), 'gateway_health', ['reported_at'], unique=False)

    if "gateways" not in mevcut:
        op.create_table('gateways',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('code', sa.String(length=50), nullable=False),
        sa.Column('name', sa.String(length=255), nullable=False),
        sa.Column('host', sa.String(length=120), nullable=False),
        sa.Column('listen_port', sa.Integer(), nullable=False),
        sa.Column('upstream_url', sa.String(length=500), nullable=False),
        sa.Column('batch_interval_sec', sa.Integer(), nullable=False),
        sa.Column('max_devices', sa.Integer(), nullable=False),
        sa.Column('device_code_prefix', sa.String(length=80), nullable=True),
        sa.Column('token', sa.String(length=255), nullable=False),
        sa.Column('token_hash', sa.String(length=128), nullable=True),
        sa.Column('publish_dnp3_quality', sa.Boolean(), server_default='false', nullable=False),
        sa.Column('is_active', sa.Boolean(), nullable=False),
        sa.Column('last_seen_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('control_host', sa.String(length=255), nullable=False),
        sa.Column('control_port', sa.Integer(), nullable=False),
        sa.Column('command_token', sa.String(length=255), nullable=True),
        sa.Column('command_delivery_token', sa.String(length=255), nullable=True),
        sa.Column('rabbitmq_username', sa.String(length=120), nullable=True),
        sa.Column('rabbitmq_password', sa.String(length=255), nullable=True),
        sa.Column('initiating_port_base', sa.Integer(), nullable=False),
        sa.Column('initiating_port_count', sa.Integer(), nullable=False),
        sa.Column('refresh_nonce', sa.Integer(), nullable=False),
        sa.Column('config_nonce', sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint('id')
        )
        op.create_index(op.f('ix_gateways_code'), 'gateways', ['code'], unique=True)
        op.create_index(op.f('ix_gateways_last_seen_at'), 'gateways', ['last_seen_at'], unique=False)
        op.create_index(op.f('ix_gateways_token'), 'gateways', ['token'], unique=False)
        op.create_index(op.f('ix_gateways_token_hash'), 'gateways', ['token_hash'], unique=False)

    if "infra_notification_state" not in mevcut:
        op.create_table('infra_notification_state',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('event_type', sa.String(length=120), nullable=False),
        sa.Column('resource_key', sa.String(length=200), server_default='', nullable=False),
        sa.Column('last_notified_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('last_event_id', sa.Integer(), nullable=True),
        sa.Column('suppressed_count', sa.Integer(), server_default='0', nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('event_type', 'resource_key', name='uq_infra_notification_key')
        )
        op.create_index(op.f('ix_infra_notification_state_event_type'), 'infra_notification_state', ['event_type'], unique=False)

    if "notification_settings" not in mevcut:
        op.create_table('notification_settings',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('smtp_enabled', sa.Boolean(), nullable=False),
        sa.Column('smtp_host', sa.String(length=255), nullable=False),
        sa.Column('smtp_port', sa.Integer(), nullable=False),
        sa.Column('smtp_username', sa.String(length=255), nullable=False),
        sa.Column('smtp_password', sa.String(length=255), nullable=False),
        sa.Column('smtp_from_email', sa.String(length=255), nullable=False),
        sa.Column('smtp_from_name', sa.String(length=200), nullable=False),
        sa.Column('sms_enabled', sa.Boolean(), nullable=False),
        sa.Column('sms_provider', sa.String(length=80), nullable=False),
        sa.Column('sms_api_url', sa.String(length=500), nullable=False),
        sa.Column('sms_api_key', sa.String(length=255), nullable=False),
        sa.Column('sms_account_sid', sa.String(length=120), nullable=False),
        sa.Column('sms_from_number', sa.String(length=40), nullable=False),
        sa.Column('telegram_enabled', sa.Boolean(), nullable=False),
        sa.Column('telegram_bot_token', sa.String(length=255), nullable=False),
        sa.Column('telegram_chat_ids', sa.String(length=2000), nullable=False),
        sa.Column('whatsapp_web_enabled', sa.Boolean(), nullable=False),
        sa.Column('whatsapp_web_group_jids', sa.String(length=2000), nullable=False),
        sa.Column('whatsapp_web_group_mode', sa.Boolean(), nullable=False),
        sa.PrimaryKeyConstraint('id')
        )

    if "notifications" not in mevcut:
        op.create_table('notifications',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('recipient_username', sa.String(length=120), nullable=True),
        sa.Column('category', sa.String(length=40), nullable=False),
        sa.Column('severity', sa.String(length=20), nullable=False),
        sa.Column('title', sa.String(length=200), nullable=False),
        sa.Column('body', sa.Text(), nullable=True),
        sa.Column('actor_username', sa.String(length=120), nullable=True),
        sa.Column('link', sa.String(length=500), nullable=True),
        sa.Column('metadata_json', sa.Text(), nullable=True),
        sa.Column('is_read', sa.Boolean(), nullable=False),
        sa.Column('read_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('id')
        )
        op.create_index('idx_notif_recipient_unread', 'notifications', ['recipient_username', 'is_read', 'created_at'], unique=False)
        op.create_index(op.f('ix_notifications_category'), 'notifications', ['category'], unique=False)
        op.create_index(op.f('ix_notifications_created_at'), 'notifications', ['created_at'], unique=False)
        op.create_index(op.f('ix_notifications_recipient_username'), 'notifications', ['recipient_username'], unique=False)

    if "outbound_targets" not in mevcut:
        op.create_table('outbound_targets',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(length=120), nullable=False),
        sa.Column('protocol', sa.String(length=20), nullable=False),
        sa.Column('endpoint', sa.String(length=500), nullable=False),
        sa.Column('topic', sa.String(length=255), nullable=True),
        sa.Column('event_filter', sa.String(length=40), nullable=False),
        sa.Column('auth_header', sa.String(length=255), nullable=True),
        sa.Column('auth_token', sa.String(length=255), nullable=True),
        sa.Column('qos', sa.Integer(), nullable=False),
        sa.Column('retain', sa.Boolean(), nullable=False),
        sa.Column('is_active', sa.Boolean(), nullable=False),
        sa.Column('mqtt_port', sa.Integer(), nullable=True),
        sa.Column('mqtt_username', sa.String(length=255), nullable=True),
        sa.Column('mqtt_password', sa.String(length=500), nullable=True),
        sa.Column('mqtt_client_id', sa.String(length=255), nullable=True),
        sa.Column('mqtt_tls_enabled', sa.Boolean(), nullable=False),
        sa.Column('mqtt_tls_insecure', sa.Boolean(), nullable=False),
        sa.Column('mqtt_tls_ca_path', sa.String(length=500), nullable=True),
        sa.Column('mqtt_tls_cert_path', sa.String(length=500), nullable=True),
        sa.Column('mqtt_tls_key_path', sa.String(length=500), nullable=True),
        sa.Column('mqtt_keepalive_sec', sa.Integer(), nullable=False),
        sa.Column('mqtt_connect_timeout_sec', sa.Integer(), nullable=False),
        sa.Column('mqtt_publish_interval_sec', sa.Integer(), nullable=False),
        sa.Column('mqtt_topic_template', sa.String(length=500), nullable=True),
        sa.Column('mqtt_topic_prefix', sa.String(length=60), nullable=False),
        sa.Column('mqtt_customer_id', sa.String(length=120), nullable=True),
        sa.Column('listen_host', sa.String(length=255), nullable=True),
        sa.Column('listen_port', sa.Integer(), nullable=True),
        sa.Column('iec104_common_address', sa.Integer(), nullable=True),
        sa.Column('iec104_ioa_device_stride', sa.Integer(), nullable=True),
        sa.Column('iec104_allowed_peers', sa.String(length=2000), nullable=True),
        sa.Column('modbus_mode', sa.String(length=10), nullable=False),
        sa.Column('modbus_unit_id', sa.Integer(), nullable=False),
        sa.Column('modbus_value_format', sa.String(length=10), nullable=False),
        sa.Column('modbus_word_order', sa.String(length=10), nullable=False),
        sa.Column('modbus_block_stride', sa.Integer(), nullable=True),
        sa.Column('modbus_base_address', sa.Integer(), nullable=False),
        sa.Column('modbus_allowed_peers', sa.String(length=2000), nullable=True),
        sa.PrimaryKeyConstraint('id')
        )
        op.create_index(op.f('ix_outbound_targets_event_filter'), 'outbound_targets', ['event_filter'], unique=False)
        op.create_index(op.f('ix_outbound_targets_is_active'), 'outbound_targets', ['is_active'], unique=False)
        op.create_index(op.f('ix_outbound_targets_name'), 'outbound_targets', ['name'], unique=True)
        op.create_index(op.f('ix_outbound_targets_protocol'), 'outbound_targets', ['protocol'], unique=False)

    if "outbox_events" not in mevcut:
        op.create_table('outbox_events',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('topic', sa.String(length=120), nullable=False),
        sa.Column('dedup_key', sa.String(length=120), nullable=False),
        sa.Column('payload_json', sa.Text(), nullable=False),
        sa.Column('published', sa.Boolean(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('published_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('attempts', sa.Integer(), server_default='0', nullable=False),
        sa.Column('dead_letter_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('last_error', sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint('id')
        )
        op.create_index('ix_outbox_events_dead_letter', 'outbox_events', ['dead_letter_at'], unique=False, postgresql_where=sa.text('dead_letter_at IS NOT NULL'))
        op.create_index(op.f('ix_outbox_events_dedup_key'), 'outbox_events', ['dedup_key'], unique=True)
        op.create_index(op.f('ix_outbox_events_published_at'), 'outbox_events', ['published_at'], unique=False)
        op.create_index('ix_outbox_events_unpublished', 'outbox_events', ['id'], unique=False, postgresql_where=sa.text('published IS false AND dead_letter_at IS NULL'))

    if "processed_messages" not in mevcut:
        op.create_table('processed_messages',
        sa.Column('id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), nullable=False),
        sa.Column('consumer_name', sa.String(length=80), nullable=False),
        sa.Column('message_id', sa.String(length=120), nullable=False),
        sa.Column('processed_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('id')
        )
        op.create_index(op.f('ix_processed_messages_processed_at'), 'processed_messages', ['processed_at'], unique=False)

    if "project_settings" not in mevcut:
        op.create_table('project_settings',
        sa.Column('id', sa.Integer(), autoincrement=False, nullable=False),
        sa.Column('project_name', sa.String(length=200), nullable=True),
        sa.Column('customer_name', sa.String(length=200), nullable=True),
        sa.Column('customer_logo', sa.Text(), nullable=True),
        sa.Column('customer_logo_light', sa.Text(), nullable=True),
        sa.Column('battery_voltage_low', sa.Float(), nullable=True),
        sa.Column('battery_voltage_full', sa.Float(), nullable=True),
        sa.Column('battery_voltage_low_sat', sa.Float(), nullable=True),
        sa.Column('battery_voltage_full_sat', sa.Float(), nullable=True),
        sa.Column('site_title', sa.String(length=200), nullable=True),
        sa.Column('favicon', sa.Text(), nullable=True),
        sa.Column('login_image', sa.Text(), nullable=True),
        sa.Column('toast_position', sa.String(length=20), nullable=True),
        sa.Column('toast_muted', sa.Boolean(), nullable=True),
        sa.Column('phase_master', sa.String(length=4), nullable=True),
        sa.Column('phase_sat01', sa.String(length=4), nullable=True),
        sa.Column('phase_sat02', sa.String(length=4), nullable=True),
        sa.Column('phase_sat03', sa.String(length=4), nullable=True),
        sa.PrimaryKeyConstraint('id')
        )

    if "regions" not in mevcut:
        op.create_table('regions',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('code', sa.String(length=50), nullable=False),
        sa.Column('name', sa.String(length=200), nullable=False),
        sa.Column('description', sa.String(length=500), nullable=True),
        sa.Column('color', sa.String(length=20), nullable=True),
        sa.Column('is_active', sa.Boolean(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('id')
        )
        op.create_index(op.f('ix_regions_code'), 'regions', ['code'], unique=True)

    if "responsibility_areas" not in mevcut:
        op.create_table('responsibility_areas',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('code', sa.String(length=80), nullable=False),
        sa.Column('name', sa.String(length=160), nullable=False),
        sa.Column('description', sa.String(length=500), nullable=True),
        sa.Column('is_active', sa.Boolean(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('id')
        )
        op.create_index(op.f('ix_responsibility_areas_code'), 'responsibility_areas', ['code'], unique=True)
        op.create_index(op.f('ix_responsibility_areas_id'), 'responsibility_areas', ['id'], unique=False)

    if "signal_catalog" not in mevcut:
        op.create_table('signal_catalog',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('key', sa.String(length=120), nullable=False),
        sa.Column('model', sa.String(length=80), nullable=False),
        sa.Column('label', sa.String(length=200), nullable=False),
        sa.Column('unit', sa.String(length=40), nullable=True),
        sa.Column('description', sa.String(length=500), nullable=True),
        sa.Column('source', sa.String(length=20), nullable=False),
        sa.Column('dnp3_class', sa.String(length=20), nullable=False),
        sa.Column('data_type', sa.String(length=20), nullable=False),
        sa.Column('dnp3_object_group', sa.Integer(), nullable=False),
        sa.Column('dnp3_index', sa.Integer(), nullable=False),
        sa.Column('scale', sa.Float(), nullable=False),
        sa.Column('offset', sa.Float(), nullable=False),
        sa.Column('historize', sa.Boolean(), nullable=False),
        sa.Column('historize_deadband', sa.Float(), nullable=False),
        sa.Column('supports_alarm', sa.Boolean(), nullable=False),
        sa.Column('is_active', sa.Boolean(), nullable=False),
        sa.Column('display_order', sa.Integer(), nullable=False),
        sa.Column('iec104_type_id', sa.Integer(), nullable=True),
        sa.Column('iec104_ioa', sa.Integer(), nullable=True),
        sa.Column('iec104_ioa_offset', sa.Integer(), nullable=True),
        sa.Column('iec104_enabled', sa.Boolean(), nullable=False),
        sa.Column('iec104_with_timestamp', sa.Boolean(), nullable=False),
        sa.Column('modbus_function', sa.Integer(), nullable=True),
        sa.Column('modbus_address', sa.Integer(), nullable=True),
        sa.Column('mqtt_topic', sa.String(length=200), nullable=True),
        sa.Column('user_overrides', sa.JSON(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('model', 'key', name='uq_signal_catalog_model_key')
        )
        op.create_index(op.f('ix_signal_catalog_id'), 'signal_catalog', ['id'], unique=False)
        op.create_index(op.f('ix_signal_catalog_key'), 'signal_catalog', ['key'], unique=False)
        op.create_index(op.f('ix_signal_catalog_model'), 'signal_catalog', ['model'], unique=False)
        op.create_index(op.f('ix_signal_catalog_source'), 'signal_catalog', ['source'], unique=False)

    if "system_events" not in mevcut:
        op.create_table('system_events',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('category', sa.String(length=40), nullable=False),
        sa.Column('event_type', sa.String(length=80), nullable=False),
        sa.Column('severity', sa.String(length=20), nullable=False),
        sa.Column('message', sa.String(length=500), nullable=False),
        sa.Column('actor_username', sa.String(length=120), nullable=True),
        sa.Column('device_code', sa.String(length=80), nullable=True),
        sa.Column('metadata_json', sa.String(length=2000), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('id')
        )
        op.create_index(op.f('ix_system_events_actor_username'), 'system_events', ['actor_username'], unique=False)
        op.create_index(op.f('ix_system_events_category'), 'system_events', ['category'], unique=False)
        op.create_index(op.f('ix_system_events_created_at'), 'system_events', ['created_at'], unique=False)
        op.create_index(op.f('ix_system_events_device_code'), 'system_events', ['device_code'], unique=False)
        op.create_index(op.f('ix_system_events_event_type'), 'system_events', ['event_type'], unique=False)
        op.create_index(op.f('ix_system_events_severity'), 'system_events', ['severity'], unique=False)

    if "telemetry_history" not in mevcut:
        op.create_table('telemetry_history',
        sa.Column('device_id', sa.Integer(), nullable=False),
        sa.Column('signal_key', sa.String(length=120), nullable=False),
        sa.Column('source_timestamp', sa.DateTime(timezone=True), nullable=False),
        sa.Column('value', sa.Float(), nullable=True),
        sa.Column('value_string', sa.Text(), nullable=True),
        sa.Column('quality', sa.String(length=50), nullable=False),
        sa.Column('device_event_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('timestamp_quality', sa.String(length=20), nullable=True),
        sa.PrimaryKeyConstraint('device_id', 'signal_key', 'source_timestamp')
        )

    if "unknown_device_telemetry" not in mevcut:
        op.create_table('unknown_device_telemetry',
        sa.Column('id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), nullable=False),
        sa.Column('consumer_name', sa.String(length=80), nullable=False),
        sa.Column('dedup_key', sa.String(length=200), nullable=False),
        sa.Column('message_id', sa.String(length=120), nullable=False),
        sa.Column('gateway_code', sa.String(length=50), nullable=True),
        sa.Column('device_code', sa.String(length=50), nullable=False),
        sa.Column('subject', sa.String(length=255), nullable=True),
        sa.Column('stream_sequence', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), nullable=True),
        sa.Column('payload_json', sa.Text(), nullable=False),
        sa.Column('signal_key', sa.String(length=120), nullable=True),
        sa.Column('source_timestamp', sa.DateTime(timezone=True), nullable=True),
        sa.Column('reason', sa.String(length=40), nullable=False),
        sa.Column('status', sa.String(length=20), nullable=False),
        sa.Column('seen_count', sa.Integer(), nullable=False),
        sa.Column('first_seen_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('last_seen_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('replayed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('replay_attempts', sa.Integer(), nullable=False),
        sa.Column('last_replay_error', sa.String(length=500), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('consumer_name', 'dedup_key', name='uq_unknown_telemetry_consumer_dedup')
        )
        op.create_index(op.f('ix_unknown_device_telemetry_device_code'), 'unknown_device_telemetry', ['device_code'], unique=False)
        op.create_index(op.f('ix_unknown_device_telemetry_status'), 'unknown_device_telemetry', ['status'], unique=False)
        op.create_index('ix_unknown_telemetry_replay', 'unknown_device_telemetry', ['status', 'device_code', 'gateway_code'], unique=False)
        op.create_index('ix_unknown_telemetry_status_first_seen', 'unknown_device_telemetry', ['status', 'first_seen_at'], unique=False)

    if "users" not in mevcut:
        op.create_table('users',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('username', sa.String(length=80), nullable=False),
        sa.Column('email', sa.String(length=255), nullable=False),
        sa.Column('phone_number', sa.String(length=32), nullable=True),
        sa.Column('full_name', sa.String(length=255), nullable=False),
        sa.Column('avatar_url', sa.Text(), nullable=True),
        sa.Column('hashed_password', sa.String(length=255), nullable=True),
        sa.Column('role', sa.Enum('OPERATOR', 'ENGINEER', 'INSTALLER', 'OPS_MANAGER', name='userrole'), nullable=False),
        sa.Column('language', sa.String(length=8), nullable=True),
        sa.Column('failed_login_count', sa.Integer(), nullable=False),
        sa.Column('locked_until', sa.DateTime(timezone=True), nullable=True),
        sa.Column('must_change_password', sa.Boolean(), nullable=False),
        sa.Column('password_reset_token_hash', sa.String(length=128), nullable=True),
        sa.Column('password_reset_token_expires_at', sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint('id')
        )
        op.create_index(op.f('ix_users_email'), 'users', ['email'], unique=True)
        op.create_index(op.f('ix_users_id'), 'users', ['id'], unique=False)
        op.create_index(op.f('ix_users_username'), 'users', ['username'], unique=True)

    if "ws_tickets" not in mevcut:
        op.create_table('ws_tickets',
        sa.Column('ticket', sa.String(length=64), nullable=False),
        sa.Column('username', sa.String(length=150), nullable=False),
        sa.Column('jti', sa.String(length=64), nullable=True),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('ticket')
        )
        op.create_index(op.f('ix_ws_tickets_expires_at'), 'ws_tickets', ['expires_at'], unique=False)

    if "api_keys" not in mevcut:
        op.create_table('api_keys',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(length=120), nullable=False),
        sa.Column('token_hash', sa.String(length=64), nullable=False),
        sa.Column('token_prefix', sa.String(length=20), nullable=False),
        sa.Column('scopes', sa.String(length=500), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('last_used_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('revoked_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('allowed_ips', sa.String(length=500), nullable=True),
        sa.Column('is_active', sa.Boolean(), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id')
        )
        op.create_index(op.f('ix_api_keys_id'), 'api_keys', ['id'], unique=False)
        op.create_index(op.f('ix_api_keys_token_hash'), 'api_keys', ['token_hash'], unique=True)
        op.create_index(op.f('ix_api_keys_user_id'), 'api_keys', ['user_id'], unique=False)

    if "bulk_notification_jobs" not in mevcut:
        op.create_table('bulk_notification_jobs',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('scheduled_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('status', sa.String(length=20), nullable=False),
        sa.Column('subject', sa.String(length=200), nullable=False),
        sa.Column('message', sa.Text(), nullable=False),
        sa.Column('channels', sa.String(length=80), nullable=False),
        sa.Column('target_json', sa.Text(), nullable=False),
        sa.Column('created_by_user_id', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('executed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('result_summary', sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(['created_by_user_id'], ['users.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id')
        )
        op.create_index(op.f('ix_bulk_notification_jobs_id'), 'bulk_notification_jobs', ['id'], unique=False)
        op.create_index(op.f('ix_bulk_notification_jobs_scheduled_at'), 'bulk_notification_jobs', ['scheduled_at'], unique=False)
        op.create_index(op.f('ix_bulk_notification_jobs_status'), 'bulk_notification_jobs', ['status'], unique=False)

    if "bulk_notification_templates" not in mevcut:
        op.create_table('bulk_notification_templates',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(length=120), nullable=False),
        sa.Column('subject', sa.String(length=200), nullable=False),
        sa.Column('message', sa.Text(), nullable=False),
        sa.Column('channels', sa.String(length=80), nullable=False),
        sa.Column('target_json', sa.Text(), nullable=True),
        sa.Column('created_by_user_id', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['created_by_user_id'], ['users.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id')
        )
        op.create_index(op.f('ix_bulk_notification_templates_id'), 'bulk_notification_templates', ['id'], unique=False)
        op.create_index(op.f('ix_bulk_notification_templates_name'), 'bulk_notification_templates', ['name'], unique=True)

    if "devices" not in mevcut:
        op.create_table('devices',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('code', sa.String(length=50), nullable=False),
        sa.Column('name', sa.String(length=255), nullable=False),
        sa.Column('serial_number', sa.String(length=20), nullable=True),
        sa.Column('description', sa.String(length=500), nullable=True),
        sa.Column('model', sa.String(length=80), nullable=False),
        sa.Column('parent_device_id', sa.Integer(), nullable=True),
        sa.Column('subunit_index', sa.Integer(), nullable=True),
        sa.Column('subunit_satellites', sa.JSON(), nullable=True),
        sa.Column('installation_date', sa.Date(), nullable=True),
        sa.Column('gateway_code', sa.String(length=50), nullable=True),
        sa.Column('ip_address', sa.String(length=120), nullable=False),
        sa.Column('dnp3_outstation_port', sa.Integer(), nullable=False),
        sa.Column('dnp3_address', sa.Integer(), nullable=False),
        sa.Column('dnp3_extended', sa.JSON(), nullable=True),
        sa.Column('poll_interval_sec', sa.Integer(), nullable=False),
        sa.Column('timeout_ms', sa.Integer(), nullable=False),
        sa.Column('retry_count', sa.Integer(), nullable=False),
        sa.Column('signal_profile', sa.String(length=80), nullable=False),
        sa.Column('latitude', sa.Float(), nullable=False),
        sa.Column('longitude', sa.Float(), nullable=False),
        sa.Column('battery_percent', sa.Float(), nullable=True),
        sa.Column('communication_status', sa.Enum('ONLINE', 'OFFLINE', 'UNKNOWN', name='communicationstatus'), nullable=False),
        sa.Column('alarm_active', sa.Boolean(), nullable=False),
        sa.Column('last_update_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('iec104_common_address', sa.Integer(), nullable=True),
        sa.Column('phase_master', sa.String(length=4), nullable=True),
        sa.Column('phase_sat01', sa.String(length=4), nullable=True),
        sa.Column('phase_sat02', sa.String(length=4), nullable=True),
        sa.Column('phase_sat03', sa.String(length=4), nullable=True),
        sa.ForeignKeyConstraint(['gateway_code'], ['gateways.code'], onupdate='CASCADE', ondelete='RESTRICT'),
        sa.ForeignKeyConstraint(['parent_device_id'], ['devices.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id')
        )
        op.create_index(op.f('ix_devices_code'), 'devices', ['code'], unique=True)
        op.create_index(op.f('ix_devices_gateway_code'), 'devices', ['gateway_code'], unique=False)
        op.create_index(op.f('ix_devices_id'), 'devices', ['id'], unique=False)
        op.create_index(op.f('ix_devices_model'), 'devices', ['model'], unique=False)
        op.create_index(op.f('ix_devices_parent_device_id'), 'devices', ['parent_device_id'], unique=False)
        op.create_index(op.f('ix_devices_serial_number'), 'devices', ['serial_number'], unique=False)

    if "gateway_ingest_batches" not in mevcut:
        op.create_table('gateway_ingest_batches',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('gateway_code', sa.String(length=50), nullable=False),
        sa.Column('sequence_no', sa.Integer(), nullable=False),
        sa.Column('sent_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['gateway_code'], ['gateways.code'], onupdate='CASCADE', ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('gateway_code', 'sequence_no', name='uq_gateway_sequence')
        )
        op.create_index(op.f('ix_gateway_ingest_batches_created_at'), 'gateway_ingest_batches', ['created_at'], unique=False)
        op.create_index(op.f('ix_gateway_ingest_batches_gateway_code'), 'gateway_ingest_batches', ['gateway_code'], unique=False)
        op.create_index(op.f('ix_gateway_ingest_batches_sent_at'), 'gateway_ingest_batches', ['sent_at'], unique=False)
        op.create_index(op.f('ix_gateway_ingest_batches_sequence_no'), 'gateway_ingest_batches', ['sequence_no'], unique=False)

    if "lines" not in mevcut:
        op.create_table('lines',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('region_id', sa.Integer(), nullable=False),
        sa.Column('code', sa.String(length=80), nullable=False),
        sa.Column('name', sa.String(length=200), nullable=False),
        sa.Column('description', sa.String(length=500), nullable=True),
        sa.Column('color', sa.String(length=20), nullable=True),
        sa.Column('is_active', sa.Boolean(), nullable=False),
        sa.Column('branched_from_pole_id', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['branched_from_pole_id'], ['poles.id'], name='fk_lines_branched_from_pole_id', ondelete='SET NULL', use_alter=True),
        sa.ForeignKeyConstraint(['region_id'], ['regions.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('region_id', 'code', name='uq_line_region_code')
        )
        op.create_index(op.f('ix_lines_branched_from_pole_id'), 'lines', ['branched_from_pole_id'], unique=False)
        op.create_index(op.f('ix_lines_code'), 'lines', ['code'], unique=False)
        op.create_index(op.f('ix_lines_region_id'), 'lines', ['region_id'], unique=False)

    if "outbound_topic_mappings" not in mevcut:
        op.create_table('outbound_topic_mappings',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('target_id', sa.Integer(), nullable=False),
        sa.Column('topic', sa.String(length=500), nullable=False),
        sa.Column('device_codes', sa.Text(), nullable=False),
        sa.Column('signal_keys', sa.Text(), nullable=False),
        sa.Column('qos', sa.Integer(), nullable=True),
        sa.Column('retain', sa.Boolean(), nullable=True),
        sa.Column('is_active', sa.Boolean(), nullable=False),
        sa.ForeignKeyConstraint(['target_id'], ['outbound_targets.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id')
        )
        op.create_index(op.f('ix_outbound_topic_mappings_target_id'), 'outbound_topic_mappings', ['target_id'], unique=False)

    if "responsibility_area_regions" not in mevcut:
        op.create_table('responsibility_area_regions',
        sa.Column('area_id', sa.Integer(), nullable=False),
        sa.Column('region_id', sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(['area_id'], ['responsibility_areas.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['region_id'], ['regions.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('area_id', 'region_id')
        )

    if "responsibility_area_users" not in mevcut:
        op.create_table('responsibility_area_users',
        sa.Column('area_id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(['area_id'], ['responsibility_areas.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('area_id', 'user_id')
        )

    if "user_fcm_tokens" not in mevcut:
        op.create_table('user_fcm_tokens',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('token', sa.String(length=255), nullable=False),
        sa.Column('platform', sa.String(length=16), nullable=True),
        sa.Column('device_label', sa.String(length=120), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('last_seen_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('token', name='uq_fcm_token')
        )
        op.create_index(op.f('ix_user_fcm_tokens_id'), 'user_fcm_tokens', ['id'], unique=False)
        op.create_index(op.f('ix_user_fcm_tokens_user_id'), 'user_fcm_tokens', ['user_id'], unique=False)

    if "user_notification_preferences" not in mevcut:
        op.create_table('user_notification_preferences',
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('web_enabled', sa.Boolean(), nullable=False),
        sa.Column('email_enabled', sa.Boolean(), nullable=False),
        sa.Column('sms_enabled', sa.Boolean(), nullable=False),
        sa.Column('telegram_enabled', sa.Boolean(), nullable=False),
        sa.Column('whatsapp_web_enabled', sa.Boolean(), nullable=False),
        sa.Column('min_level_rank', sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('user_id')
        )

    if "user_sessions" not in mevcut:
        op.create_table('user_sessions',
        sa.Column('jti', sa.String(length=64), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('ip_address', sa.String(length=64), nullable=True),
        sa.Column('user_agent', sa.String(length=255), nullable=True),
        sa.Column('login_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('last_seen_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('revoked_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('revoked_by_user_id', sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(['revoked_by_user_id'], ['users.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('jti')
        )
        op.create_index(op.f('ix_user_sessions_expires_at'), 'user_sessions', ['expires_at'], unique=False)
        op.create_index(op.f('ix_user_sessions_last_seen_at'), 'user_sessions', ['last_seen_at'], unique=False)
        op.create_index(op.f('ix_user_sessions_user_id'), 'user_sessions', ['user_id'], unique=False)

    if "alarm_daily_counts" not in mevcut:
        op.create_table('alarm_daily_counts',
        sa.Column('day', sa.Date(), nullable=False),
        sa.Column('device_id', sa.Integer(), nullable=False),
        sa.Column('count', sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(['device_id'], ['devices.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('day', 'device_id')
        )

    if "alarm_events" not in mevcut:
        op.create_table('alarm_events',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('device_id', sa.Integer(), nullable=True),
        sa.Column('device_code', sa.String(length=64), nullable=True),
        sa.Column('device_name', sa.String(length=120), nullable=True),
        sa.Column('level', sa.String(length=30), nullable=False),
        sa.Column('title', sa.String(length=255), nullable=False),
        sa.Column('description', sa.String(length=1000), nullable=False),
        sa.Column('signal_key', sa.String(length=120), nullable=True),
        sa.Column('assigned_to', sa.String(length=120), nullable=True),
        sa.Column('acknowledged', sa.Boolean(), nullable=False),
        sa.Column('reset', sa.Boolean(), nullable=False),
        sa.Column('acknowledged_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('reset_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('produces_fault', sa.Boolean(), nullable=False),
        sa.Column('kind', sa.String(length=20), nullable=True),
        sa.Column('superseded_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['device_id'], ['devices.id'], ),
        sa.PrimaryKeyConstraint('id')
        )
        op.create_index(op.f('ix_alarm_events_acknowledged_at'), 'alarm_events', ['acknowledged_at'], unique=False)
        op.create_index(op.f('ix_alarm_events_assigned_to'), 'alarm_events', ['assigned_to'], unique=False)
        op.create_index(op.f('ix_alarm_events_created_at'), 'alarm_events', ['created_at'], unique=False)
        op.create_index(op.f('ix_alarm_events_device_id'), 'alarm_events', ['device_id'], unique=False)
        op.create_index(op.f('ix_alarm_events_kind'), 'alarm_events', ['kind'], unique=False)
        op.create_index(op.f('ix_alarm_events_level'), 'alarm_events', ['level'], unique=False)
        op.create_index(op.f('ix_alarm_events_reset_at'), 'alarm_events', ['reset_at'], unique=False)
        op.create_index(op.f('ix_alarm_events_signal_key'), 'alarm_events', ['signal_key'], unique=False)
        op.create_index(op.f('ix_alarm_events_superseded_at'), 'alarm_events', ['superseded_at'], unique=False)

    if "device_config_versions" not in mevcut:
        op.create_table('device_config_versions',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('device_id', sa.Integer(), nullable=False),
        sa.Column('version', sa.Integer(), nullable=False),
        sa.Column('raw', sa.LargeBinary(), nullable=False),
        sa.Column('source', sa.String(length=24), nullable=False),
        sa.Column('template_id', sa.Integer(), nullable=True),
        sa.Column('applied_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('note', sa.Text(), nullable=True),
        sa.Column('created_by', sa.String(length=80), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['device_id'], ['devices.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['template_id'], ['device_config_templates.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('device_id', 'version', name='uq_device_config_version')
        )
        op.create_index(op.f('ix_device_config_versions_created_at'), 'device_config_versions', ['created_at'], unique=False)
        op.create_index(op.f('ix_device_config_versions_device_id'), 'device_config_versions', ['device_id'], unique=False)
        op.create_index(op.f('ix_device_config_versions_id'), 'device_config_versions', ['id'], unique=False)

    if "outbound_modbus_slots" not in mevcut:
        op.create_table('outbound_modbus_slots',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('target_id', sa.Integer(), nullable=False),
        sa.Column('device_id', sa.Integer(), nullable=False),
        sa.Column('slot_index', sa.Integer(), nullable=False),
        sa.Column('unit_id', sa.Integer(), nullable=False),
        sa.Column('block_start', sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(['device_id'], ['devices.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['target_id'], ['outbound_targets.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('target_id', 'device_id', name='uq_modbus_slot_target_device')
        )
        op.create_index(op.f('ix_outbound_modbus_slots_device_id'), 'outbound_modbus_slots', ['device_id'], unique=False)
        op.create_index(op.f('ix_outbound_modbus_slots_target_id'), 'outbound_modbus_slots', ['target_id'], unique=False)

    if "poles" not in mevcut:
        op.create_table('poles',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('line_id', sa.Integer(), nullable=False),
        sa.Column('sequence_no', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(length=120), nullable=True),
        sa.Column('latitude', sa.Float(), nullable=False),
        sa.Column('longitude', sa.Float(), nullable=False),
        sa.Column('pole_type', sa.String(length=20), nullable=False),
        sa.Column('topology_role', sa.String(length=20), nullable=False),
        sa.Column('energy_role', sa.String(length=20), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['line_id'], ['lines.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('line_id', 'sequence_no', name='uq_pole_line_sequence')
        )
        op.create_index(op.f('ix_poles_line_id'), 'poles', ['line_id'], unique=False)

    if "responsibility_area_devices" not in mevcut:
        op.create_table('responsibility_area_devices',
        sa.Column('area_id', sa.Integer(), nullable=False),
        sa.Column('device_id', sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(['area_id'], ['responsibility_areas.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['device_id'], ['devices.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('area_id', 'device_id')
        )

    if "responsibility_area_lines" not in mevcut:
        op.create_table('responsibility_area_lines',
        sa.Column('area_id', sa.Integer(), nullable=False),
        sa.Column('line_id', sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(['area_id'], ['responsibility_areas.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['line_id'], ['lines.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('area_id', 'line_id')
        )

    if "telemetry" not in mevcut:
        op.create_table('telemetry',
        sa.Column('id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), nullable=False),
        sa.Column('device_id', sa.Integer(), nullable=False),
        sa.Column('signal_key', sa.String(length=120), nullable=False),
        sa.Column('value', sa.Float(), nullable=True),
        sa.Column('value_string', sa.Text(), nullable=True),
        sa.Column('quality', sa.String(length=50), nullable=False),
        sa.Column('source_timestamp', sa.DateTime(timezone=True), nullable=False),
        sa.Column('device_event_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('timestamp_quality', sa.String(length=20), nullable=True),
        sa.ForeignKeyConstraint(['device_id'], ['devices.id'], ),
        sa.PrimaryKeyConstraint('id')
        )

    if "telemetry_latest" not in mevcut:
        op.create_table('telemetry_latest',
        sa.Column('device_id', sa.Integer(), nullable=False),
        sa.Column('signal_key', sa.String(length=120), nullable=False),
        sa.Column('value', sa.Float(), nullable=True),
        sa.Column('value_string', sa.Text(), nullable=True),
        sa.Column('quality', sa.String(length=50), nullable=False),
        sa.Column('source_timestamp', sa.DateTime(timezone=True), nullable=False),
        sa.Column('timestamp_quality', sa.String(length=20), nullable=True),
        sa.Column('device_event_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['device_id'], ['devices.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('device_id', 'signal_key')
        )

    if "alarm_comments" not in mevcut:
        op.create_table('alarm_comments',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('alarm_event_id', sa.Integer(), nullable=False),
        sa.Column('author_username', sa.String(length=120), nullable=False),
        sa.Column('comment', sa.String(length=1000), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['alarm_event_id'], ['alarm_events.id'], ),
        sa.PrimaryKeyConstraint('id')
        )
        op.create_index(op.f('ix_alarm_comments_alarm_event_id'), 'alarm_comments', ['alarm_event_id'], unique=False)
        op.create_index(op.f('ix_alarm_comments_author_username'), 'alarm_comments', ['author_username'], unique=False)
        op.create_index(op.f('ix_alarm_comments_created_at'), 'alarm_comments', ['created_at'], unique=False)

    if "fault_events" not in mevcut:
        op.create_table('fault_events',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('line_id', sa.Integer(), nullable=False),
        sa.Column('region_id', sa.Integer(), nullable=False),
        sa.Column('last_red_device_id', sa.Integer(), nullable=False),
        sa.Column('first_green_device_id', sa.Integer(), nullable=True),
        sa.Column('from_pole_id', sa.Integer(), nullable=False),
        sa.Column('to_pole_id', sa.Integer(), nullable=False),
        sa.Column('from_pole_seq', sa.Integer(), nullable=True),
        sa.Column('to_pole_seq', sa.Integer(), nullable=True),
        sa.Column('zone_start_m', sa.Float(), nullable=True),
        sa.Column('zone_end_m', sa.Float(), nullable=True),
        sa.Column('zone_length_m', sa.Float(), nullable=True),
        sa.Column('zone_code', sa.String(length=64), nullable=True),
        sa.Column('status', sa.String(length=20), nullable=False),
        sa.Column('opened_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('resolved_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('closed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('assigned_to_username', sa.String(length=120), nullable=True),
        sa.Column('assigned_to_area_id', sa.Integer(), nullable=True),
        sa.Column('assigned_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('note', sa.String(length=500), nullable=True),
        sa.Column('resolution_note', sa.String(length=1000), nullable=True),
        sa.Column('notified_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('cause_code', sa.String(length=40), nullable=True),
        sa.Column('cause_detail', sa.String(length=500), nullable=True),
        sa.Column('fault_kind', sa.String(length=20), nullable=True),
        sa.Column('phase', sa.String(length=10), nullable=True),
        sa.Column('trigger_signals', sa.JSON(), nullable=True),
        sa.Column('auto_cause_code', sa.String(length=40), nullable=True),
        sa.Column('fault_direction', sa.String(length=20), nullable=True),
        sa.Column('fault_current_a', sa.Float(), nullable=True),
        sa.Column('load_current_before_a', sa.Float(), nullable=True),
        sa.Column('conductor_temp_c', sa.Float(), nullable=True),
        sa.Column('momentary_fault_count', sa.Integer(), nullable=True),
        sa.Column('permanent_fault_count', sa.Integer(), nullable=True),
        sa.Column('measured_at', sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(['assigned_to_area_id'], ['responsibility_areas.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['first_green_device_id'], ['devices.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['from_pole_id'], ['poles.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['last_red_device_id'], ['devices.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['line_id'], ['lines.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['region_id'], ['regions.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['to_pole_id'], ['poles.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id')
        )
        op.create_index('ix_fault_cause_opened', 'fault_events', ['cause_code', 'opened_at'], unique=False)
        op.create_index(op.f('ix_fault_events_assigned_to_area_id'), 'fault_events', ['assigned_to_area_id'], unique=False)
        op.create_index(op.f('ix_fault_events_assigned_to_username'), 'fault_events', ['assigned_to_username'], unique=False)
        op.create_index(op.f('ix_fault_events_auto_cause_code'), 'fault_events', ['auto_cause_code'], unique=False)
        op.create_index(op.f('ix_fault_events_cause_code'), 'fault_events', ['cause_code'], unique=False)
        op.create_index(op.f('ix_fault_events_fault_kind'), 'fault_events', ['fault_kind'], unique=False)
        op.create_index(op.f('ix_fault_events_from_pole_id'), 'fault_events', ['from_pole_id'], unique=False)
        op.create_index(op.f('ix_fault_events_last_red_device_id'), 'fault_events', ['last_red_device_id'], unique=False)
        op.create_index(op.f('ix_fault_events_line_id'), 'fault_events', ['line_id'], unique=False)
        op.create_index(op.f('ix_fault_events_notified_at'), 'fault_events', ['notified_at'], unique=False)
        op.create_index(op.f('ix_fault_events_opened_at'), 'fault_events', ['opened_at'], unique=False)
        op.create_index(op.f('ix_fault_events_region_id'), 'fault_events', ['region_id'], unique=False)
        op.create_index(op.f('ix_fault_events_status'), 'fault_events', ['status'], unique=False)
        op.create_index(op.f('ix_fault_events_to_pole_id'), 'fault_events', ['to_pole_id'], unique=False)
        op.create_index(op.f('ix_fault_events_zone_code'), 'fault_events', ['zone_code'], unique=False)
        op.create_index('ix_fault_line_opened', 'fault_events', ['line_id', 'opened_at'], unique=False)
        op.create_index('ix_fault_region_opened', 'fault_events', ['region_id', 'opened_at'], unique=False)
        op.create_index('ix_fault_span', 'fault_events', ['from_pole_id', 'to_pole_id'], unique=False)

    if "line_segments" not in mevcut:
        op.create_table('line_segments',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('line_id', sa.Integer(), nullable=False),
        sa.Column('from_pole_id', sa.Integer(), nullable=False),
        sa.Column('to_pole_id', sa.Integer(), nullable=False),
        sa.Column('device_id', sa.Integer(), nullable=True),
        sa.Column('device_position_t', sa.Float(), nullable=True),
        sa.Column('device_orientation', sa.String(length=20), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['device_id'], ['devices.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['from_pole_id'], ['poles.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['line_id'], ['lines.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['to_pole_id'], ['poles.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('device_id')
        )
        op.create_index(op.f('ix_line_segments_line_id'), 'line_segments', ['line_id'], unique=False)

    if "fault_comments" not in mevcut:
        op.create_table('fault_comments',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('fault_id', sa.Integer(), nullable=False),
        sa.Column('author_username', sa.String(length=120), nullable=False),
        sa.Column('body', sa.String(length=2000), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['fault_id'], ['fault_events.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id')
        )
        op.create_index(op.f('ix_fault_comments_author_username'), 'fault_comments', ['author_username'], unique=False)
        op.create_index(op.f('ix_fault_comments_created_at'), 'fault_comments', ['created_at'], unique=False)
        op.create_index(op.f('ix_fault_comments_fault_id'), 'fault_comments', ['fault_id'], unique=False)

    mevcut_sonrasi = _mevcut_tablolar()

    # ---- Dongusel FK: lines <-> poles -----------------------------------
    # `lines.branched_from_pole_id -> poles.id` ile `poles.line_id -> lines.id`
    # karsilikli bagimli. Modelde `use_alter=True` ile cozuluyor; burada da
    # kisit iki tablo da kurulduktan SONRA ayri ALTER ile ekleniyor.
    # Autogenerate bu kisiti atlamisti (sema parity testi yakaladi).
    if "lines" in mevcut_sonrasi and "poles" in mevcut_sonrasi:
        _fk = {
            f["name"] for f in sa.inspect(op.get_bind()).get_foreign_keys("lines")
        }
        if "fk_lines_branched_from_pole_id" not in _fk:
            op.create_foreign_key(
                "fk_lines_branched_from_pole_id",
                "lines",
                "poles",
                ["branched_from_pole_id"],
                ["id"],
                ondelete="SET NULL",
            )

    # ---- Tek seferlik VERI duzeltmesi ------------------------------------
    # Eskiden `app/main.py` acilista bunu `migration_markers` tablosuyla
    # koruyarak yapiyordu. Sema DDL'i runtime'dan kalkinca bu adimin da bir
    # sahibi olmali: Alembic zaten "bir kez" garantisi veriyor, ayri bir
    # isaret tablosuna gerek yok. Idempotent — mevcut sahada zaten TRUE.
    if "signal_catalog" in _mevcut_tablolar():
        op.execute(
            "UPDATE signal_catalog SET iec104_with_timestamp = TRUE "
            "WHERE data_type = 'binary' AND iec104_type_id IS NOT NULL"
        )


def downgrade() -> None:
    """Semayi bu migration'in kurdugu noktadan geri alir.

    Mevcut kurulumlarda 0072 NO-OP oldugu icin geri alma da tablo DUSURMEZ:
    asagidaki dongu yalnizca var olanlari hedefler ve FK sirasina uyar.
    DIKKAT: bu, temiz kurulumda kurulan semanin TAMAMINI dusurur.
    """
    mevcut = _mevcut_tablolar()

    # Dongusel FK ONCE dusmeli: `lines.branched_from_pole_id -> poles.id`
    # ayri bir ALTER ile eklendigi icin `drop_table("poles")` ona takilir
    # ("cannot drop table poles because other objects depend on it").
    if "lines" in mevcut:
        _fk = {f["name"] for f in sa.inspect(op.get_bind()).get_foreign_keys("lines")}
        if "fk_lines_branched_from_pole_id" in _fk:
            op.drop_constraint("fk_lines_branched_from_pole_id", "lines", type_="foreignkey")

    if "fault_comments" in mevcut:
        op.drop_table("fault_comments")
    if "line_segments" in mevcut:
        op.drop_table("line_segments")
    if "fault_events" in mevcut:
        op.drop_table("fault_events")
    if "alarm_comments" in mevcut:
        op.drop_table("alarm_comments")
    if "telemetry_latest" in mevcut:
        op.drop_table("telemetry_latest")
    if "telemetry" in mevcut:
        op.drop_table("telemetry")
    if "responsibility_area_lines" in mevcut:
        op.drop_table("responsibility_area_lines")
    if "responsibility_area_devices" in mevcut:
        op.drop_table("responsibility_area_devices")
    if "poles" in mevcut:
        op.drop_table("poles")
    if "outbound_modbus_slots" in mevcut:
        op.drop_table("outbound_modbus_slots")
    if "device_config_versions" in mevcut:
        op.drop_table("device_config_versions")
    if "alarm_events" in mevcut:
        op.drop_table("alarm_events")
    if "alarm_daily_counts" in mevcut:
        op.drop_table("alarm_daily_counts")
    if "user_sessions" in mevcut:
        op.drop_table("user_sessions")
    if "user_notification_preferences" in mevcut:
        op.drop_table("user_notification_preferences")
    if "user_fcm_tokens" in mevcut:
        op.drop_table("user_fcm_tokens")
    if "responsibility_area_users" in mevcut:
        op.drop_table("responsibility_area_users")
    if "responsibility_area_regions" in mevcut:
        op.drop_table("responsibility_area_regions")
    if "outbound_topic_mappings" in mevcut:
        op.drop_table("outbound_topic_mappings")
    if "lines" in mevcut:
        op.drop_table("lines")
    if "gateway_ingest_batches" in mevcut:
        op.drop_table("gateway_ingest_batches")
    if "devices" in mevcut:
        op.drop_table("devices")
    if "bulk_notification_templates" in mevcut:
        op.drop_table("bulk_notification_templates")
    if "bulk_notification_jobs" in mevcut:
        op.drop_table("bulk_notification_jobs")
    if "api_keys" in mevcut:
        op.drop_table("api_keys")
    if "ws_tickets" in mevcut:
        op.drop_table("ws_tickets")
    if "users" in mevcut:
        op.drop_table("users")
    if "unknown_device_telemetry" in mevcut:
        op.drop_table("unknown_device_telemetry")
    if "telemetry_history" in mevcut:
        op.drop_table("telemetry_history")
    if "system_events" in mevcut:
        op.drop_table("system_events")
    if "signal_catalog" in mevcut:
        op.drop_table("signal_catalog")
    if "responsibility_areas" in mevcut:
        op.drop_table("responsibility_areas")
    if "regions" in mevcut:
        op.drop_table("regions")
    if "project_settings" in mevcut:
        op.drop_table("project_settings")
    if "processed_messages" in mevcut:
        op.drop_table("processed_messages")
    if "outbox_events" in mevcut:
        op.drop_table("outbox_events")
    if "outbound_targets" in mevcut:
        op.drop_table("outbound_targets")
    if "notifications" in mevcut:
        op.drop_table("notifications")
    if "notification_settings" in mevcut:
        op.drop_table("notification_settings")
    if "infra_notification_state" in mevcut:
        op.drop_table("infra_notification_state")
    if "gateways" in mevcut:
        op.drop_table("gateways")
    if "gateway_health" in mevcut:
        op.drop_table("gateway_health")
    if "ftp_settings" in mevcut:
        op.drop_table("ftp_settings")
    if "device_purge_jobs" in mevcut:
        op.drop_table("device_purge_jobs")
    if "device_model_settings" in mevcut:
        op.drop_table("device_model_settings")
    if "device_config_templates" in mevcut:
        op.drop_table("device_config_templates")
    if "device_commands" in mevcut:
        op.drop_table("device_commands")
    if "backup_schedule" in mevcut:
        op.drop_table("backup_schedule")
    if "backup_jobs" in mevcut:
        op.drop_table("backup_jobs")
    if "alarm_rules" in mevcut:
        op.drop_table("alarm_rules")

    # Tablolarla birlikte yaratilan ENUM TIPLERI de dusmeli.
    # PostgreSQL'de tipi kullanan tablo dusunce TIP AYAKTA KALIR; sonraki
    # `upgrade` `CREATE TYPE userrole ...` derken "type already exists" ile
    # patlar. Yani downgrade/upgrade dongusu tipler dusurulmeden KAPANMAZ
    # (bu, migration testi tarafindan yakalandi).
    #
    # `IF EXISTS`: kismi bir downgrade'de tip zaten gitmis olabilir.
    for _tip in ("userrole", "communicationstatus"):
        op.execute(f"DROP TYPE IF EXISTS {_tip}")
