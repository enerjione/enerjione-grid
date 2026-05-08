export type CommunicationStatus = "online" | "offline" | "unknown";

export type IpEndpointType = "initiating" | "listening";

export type Dnp3ExtendedSettings = {
  ip_endpoint_type: IpEndpointType;
  master_ip_address: string;
  master_ip_port: number;
  master_address: number;
  unsolicited_reporting: boolean;
  unsolicited_on_startup: boolean;
  unsolicited_class_mask_id: number;
  link_status_period_min: number;
  enable_self_address: boolean;
  validate_source_address: boolean;
  session_timeout_listening_sec: number;
  socket_listening_timeout_sec: number;
};

/** Backend ile aynı varsayılanlar (merge edilmemiş cevaplar için) */
export const DEFAULT_DNP3_EXTENDED: Dnp3ExtendedSettings = {
  ip_endpoint_type: "listening",
  master_ip_address: "",
  master_ip_port: 20002,
  master_address: 100,
  unsolicited_reporting: true,
  unsolicited_on_startup: true,
  unsolicited_class_mask_id: 7,
  link_status_period_min: 0,
  enable_self_address: false,
  validate_source_address: false,
  session_timeout_listening_sec: 60,
  socket_listening_timeout_sec: 600
};

export function mergeDnp3Extended(
  raw: Partial<Dnp3ExtendedSettings> | undefined | null
): Dnp3ExtendedSettings {
  const rest = raw ? { ...raw } : {};
  delete (rest as Record<string, unknown>).tls_dnp3;
  return { ...DEFAULT_DNP3_EXTENDED, ...rest, ip_endpoint_type: "listening" };
}

export type DeviceModelOption = {
  code: string;
  label: string;
};

export const DEFAULT_DEVICE_MODEL = "horstmann_sn_2_0";

export type DeviceRow = {
  id: number;
  code: string;
  name: string;
  description?: string;
  model: string;
  gatewayCode?: string;
  ipAddress?: string;
  dnp3OutstationPort?: number;
  dnp3Address?: number;
  dnp3Extended?: Dnp3ExtendedSettings;
  pollIntervalSec?: number;
  timeoutMs?: number;
  retryCount?: number;
  signalProfile?: string;
  communicationStatus: CommunicationStatus;
  batteryPercent: number;
  alarmActive: boolean;
  lastUpdateAt?: string;
  latitude: number;
  longitude: number;
  // IEC 60870-5-104 ASDU Common Address. NULL ise outbound target'in default
  // CA'si kullanilir.
  iec104CommonAddress?: number | null;
};

export type UserRole = "operator" | "engineer" | "installer";

export type AuthSession = {
  accessToken: string;
  username: string;
  role: UserRole;
};

export type ApiDevice = {
  id: number;
  code: string;
  name: string;
  description?: string | null;
  model?: string | null;
  gateway_code?: string | null;
  ip_address: string;
  dnp3_outstation_port?: number;
  dnp3_address: number;
  dnp3_extended?: Dnp3ExtendedSettings | null;
  poll_interval_sec: number;
  timeout_ms: number;
  retry_count: number;
  signal_profile: string;
  latitude: number;
  longitude: number;
  battery_percent: number;
  communication_status: CommunicationStatus;
  alarm_active: boolean;
  last_update_at?: string | null;
  iec104_common_address?: number | null;
};

export type UserRead = {
  id: number;
  username: string;
  email: string;
  phone_number?: string | null;
  full_name: string;
  role: UserRole;
};

export type AlarmEvent = {
  id: number;
  device_id: number;
  level: string;
  title: string;
  description: string;
  /** Sinyal anahtarı (prefix master/sat01/sat02 → kaynak rozet türetir). */
  signal_key?: string | null;
  assigned_to?: string | null;
  acknowledged?: boolean;
  reset?: boolean;
  acknowledged_at?: string | null;
  reset_at?: string | null;
  created_at: string;
};

/** Hat Arizalari ("Fault") — anasayfa haritasindaki "son RED -> ilk GREEN
    arasi" hesabinin DB'ye yazilmis kalici karsiligi. Ticket sistemiyle
    birlikte: arizaya bir kullanici atanir, durum yonetir, yorum/rapor ekler. */
export type FaultEvent = {
  id: number;
  line_id: number;
  line_name: string;
  region_id: number;
  region_name: string;
  last_red_device_id: number;
  last_red_device_code?: string | null;
  last_red_device_name?: string | null;
  first_green_device_id?: number | null;
  first_green_device_code?: string | null;
  first_green_device_name?: string | null;
  from_pole_id: number;
  to_pole_id: number;
  from_pole_seq?: number | null;
  to_pole_seq?: number | null;
  status: "open" | "assigned" | "in_progress" | "resolved" | "closed" | string;
  opened_at: string;
  resolved_at?: string | null;
  closed_at?: string | null;
  note?: string | null;
  assigned_to_username?: string | null;
  assigned_to_full_name?: string | null;
  assigned_at?: string | null;
  comment_count: number;
};

export type FaultComment = {
  id: number;
  fault_id: number;
  author_username: string;
  body: string;
  created_at: string;
};

/** DB yedek dosyasi metadata'si. */
export type BackupJob = {
  id: number;
  job_type: "manual" | "scheduled" | string;
  status: "running" | "success" | "failed" | string;
  file_path?: string | null;
  size_bytes?: number | null;
  error_message?: string | null;
  created_by_username?: string | null;
  created_at: string;
  completed_at?: string | null;
  filename?: string | null;
};

/** Periyodik yedek ayarlari. */
export type BackupSchedule = {
  enabled: boolean;
  interval_hours: number;
  retention_count: number;
  last_run_at?: string | null;
};

/** Hat Arizalari — ozet istatistikler. */
export type FaultStats = {
  total: number;
  open: number;
  assigned: number;
  in_progress: number;
  resolved: number;
  closed: number;
  avg_resolution_seconds: number | null;
  last_30d_count: number;
};

/** Kullanici-bazli bildirim kanal tercihleri. */
export type UserNotificationPreferences = {
  user_id: number;
  web_enabled: boolean;
  email_enabled: boolean;
  sms_enabled: boolean;
  min_level_rank: number;
};

export type AlarmComment = {
  id: number;
  alarm_event_id: number;
  author_username: string;
  comment: string;
  created_at: string;
};

export type SystemEvent = {
  id: number;
  category: string;
  event_type: string;
  severity: string;
  message: string;
  actor_username?: string | null;
  device_code?: string | null;
  metadata_json?: string | null;
  created_at: string;
};

export type Gateway = {
  id: number;
  code: string;
  name: string;
  host: string;
  listen_port: number;
  upstream_url: string;
  batch_interval_sec: number;
  max_devices: number;
  device_code_prefix?: string | null;
  token: string;
  is_active: boolean;
  last_seen_at?: string | null;
  control_host: string;
  control_port: number;
  /** Initiating mode TCP server portu icin host tarafi baslangic portu.
   *  Cihaz "Master IP Port" alani bu degerden baslayan numarayi alir
   *  (port_base + cihaz idx). Coklu gateway senaryosunda her gateway
   *  benzersiz blok aldigi icin port catismasi olmaz (20100, 21100, ...). */
  initiating_port_base?: number;
  /** Bu gateway icin acilacak initiating port sayisi (= max initiating cihaz).
   *  Default 0: yalniz listening cihazlar (gateway cihaza outbound baglanir,
   *  port acilmaz). Initiating cihaz eklendiginde artirilir. */
  initiating_port_count?: number;
};

export type OutboundTarget = {
  id: number;
  name: string;
  protocol: "rest" | "mqtt" | "iec104";
  endpoint: string;
  topic?: string | null;
  event_filter: "all" | "telemetry" | "alarm";
  auth_header?: string | null;
  auth_token?: string | null;
  qos: number;
  retain: boolean;
  is_active: boolean;
  // IEC 60870-5-104 hedefi icin (protocol === "iec104"):
  listen_host?: string | null;
  listen_port?: number | null;
  iec104_common_address?: number | null;
  /** Virgulle ayrilmis IP whitelist; bos = serbest. */
  iec104_allowed_peers?: string | null;
};

export type Iec104RuntimeStatus = {
  target_id: number;
  server_running: boolean;
  whitelist_active: boolean;
  allowed_peers: string[];
  connected_clients: { peer: string; started: boolean; connected_at: string }[];
};

// ===== Sebeke topolojisi =====

export type Region = {
  id: number;
  code: string;
  name: string;
  description?: string | null;
  color?: string | null;
  is_active: boolean;
  created_at: string;
  line_count?: number;
};

export type Line = {
  id: number;
  region_id: number;
  code: string;
  name: string;
  description?: string | null;
  color?: string | null;
  is_active: boolean;
  /** Bransman: bu hat baska bir hattin diregine bagliysa o pole'un id'si. */
  branched_from_pole_id?: number | null;
  created_at: string;
  pole_count?: number;
  segment_count?: number;
};

export type PoleType = "pole" | "transformer";

export type Pole = {
  id: number;
  line_id: number;
  sequence_no: number;
  name?: string | null;
  latitude: number;
  longitude: number;
  pole_type?: PoleType;
  created_at: string;
};

export type LineSegment = {
  id: number;
  line_id: number;
  from_pole_id: number;
  to_pole_id: number;
  device_id?: number | null;
  /** Cihazin slot icindeki fiziksel konumu (0..1). NULL = otomatik dagilim. */
  device_position_t?: number | null;
  created_at: string;
  /** UI render kolaylığı için backend expand ediyor */
  from_pole_seq?: number | null;
  to_pole_seq?: number | null;
  device_code?: string | null;
  device_name?: string | null;
};

export type LineDetail = {
  line: Line;
  poles: Pole[];
  segments: LineSegment[];
};

export type ProjectSettings = {
  project_name?: string | null;
  customer_name?: string | null;
  /** Login ekrani buyuk logo (data URL: data:image/png;base64,...). */
  customer_logo?: string | null;
  /** Header'da gosterilecek koyu zemin uyumlu kucuk logo (data URL). */
  customer_logo_light?: string | null;
  /** Batarya yuzdesi voltajdan turetilirken kullanilan esikler (V). */
  battery_voltage_low?: number | null;
  battery_voltage_full?: number | null;
  /** Tarayici sekmesinde gozukecek baslik (document.title). */
  site_title?: string | null;
  /** Tarayici sekmesinde gozukecek favicon (data URL). */
  favicon?: string | null;
  /** Login ekraninin sag tarafindaki dekoratif gorsel (data URL). */
  login_image?: string | null;
};

export type NotificationSettings = {
  smtp_enabled: boolean;
  smtp_host: string;
  smtp_port: number;
  smtp_username: string;
  smtp_password: string;
  smtp_from_email: string;
  sms_enabled: boolean;
  sms_provider: string;
  sms_api_url: string;
  sms_api_key: string;
  /** Telegram Bot ile bildirim gondermek icin. */
  telegram_enabled?: boolean;
  telegram_bot_token?: string;
  /** Virgulle ayrili chat ID listesi (kanal/grup). */
  telegram_chat_ids?: string;
};

export type SignalDataType =
  | "analog"
  | "binary"
  | "counter"
  | "string";

export type SignalSource = "master" | "sat01" | "sat02";

export type SignalCatalogRow = {
  id: number;
  key: string;
  model: string;
  label: string;
  unit?: string | null;
  description?: string | null;
  source: SignalSource;
  dnp3_class: string;
  data_type: SignalDataType;
  dnp3_object_group: number;
  dnp3_index: number;
  scale: number;
  offset: number;
  supports_alarm: boolean;
  is_active: boolean;
  display_order: number;
  // IEC 60870-5-104 outbound template adresleme.
  // Yeni model: `iec104_ioa` mutlak IOA; cihaz bazli ayrim ASDU CA ile yapilir.
  // `iec104_ioa_offset` eski deploylar icin geri uyumlu fallback.
  iec104_type_id?: number | null;
  iec104_ioa?: number | null;
  iec104_ioa_offset?: number | null;
  /** Sinyal bazinda IEC 104 yayinini gecici kapatma. Default true. */
  iec104_enabled?: boolean;
  /** CP56Time2a zaman etiketi tasiyan ASDU tipinde mi yayinlansin?
   *  Dijital sinyallerde default true; analoglarda default false. */
  iec104_with_timestamp?: boolean;
  // Modbus outbound (function code + register/coil address).
  modbus_function?: number | null;
  modbus_address?: number | null;
  // MQTT outbound topic suffix'i.
  mqtt_topic?: string | null;
};

export type SignalLiveRow = {
  signal_key: string;
  signal_label: string;
  unit?: string | null;
  source: SignalSource;
  /** Backend tarafindan eklenen kategori (analog | binary | counter | analog_output | binary_output | string).
   *  Eski backend versiyonlarinda alan gelmeyebilir; o yuzden optional. */
  data_type?: string | null;
  device_id: number;
  device_code: string;
  device_name: string;
  value: number | null;
  /** DNP3 Group 110 (Octet String) sinyallerinde gateway numeric value yerine
   *  metin yollar; data_type === "string" satirlarda value_string gosterilir. */
  value_string?: string | null;
  quality: string | null;
  source_timestamp: string | null;
};

export type ResponsibilityAreaRow = {
  id: number;
  code: string;
  name: string;
  description?: string | null;
  is_active: boolean;
  created_at: string;
  user_count: number;
  device_count: number;
  region_count?: number;
  line_count?: number;
};

export type ResponsibilityAreaUser = {
  id: number;
  username: string;
  full_name: string;
  email: string;
};

export type ResponsibilityAreaDevice = {
  id: number;
  code: string;
  name: string;
};

export type ResponsibilityAreaRegion = {
  id: number;
  code: string;
  name: string;
};

export type ResponsibilityAreaLine = {
  id: number;
  code: string;
  name: string;
  region_id: number;
};

export type ResponsibilityAreaDetail = ResponsibilityAreaRow & {
  users: ResponsibilityAreaUser[];
  devices: ResponsibilityAreaDevice[];
  regions?: ResponsibilityAreaRegion[];
  lines?: ResponsibilityAreaLine[];
};

export type AlarmLevel = "info" | "warning" | "critical";
export type AlarmComparator =
  | "gt"
  | "gte"
  | "lt"
  | "lte"
  | "eq"
  | "ne"
  | "between"
  | "outside"
  | "boolean_true"
  | "boolean_false";

export type AlarmRuleRow = {
  id: number;
  signal_key: string;
  name: string;
  description?: string | null;
  level: AlarmLevel;
  comparator: AlarmComparator;
  threshold: number;
  threshold_high?: number | null;
  hysteresis: number;
  debounce_sec: number;
  device_code_filter?: string | null;
  is_active: boolean;
  /** Kural-bazli bildirim kanallari. Web bildirimi her zaman gider; bunlar
   *  sadece kuraldan acildiysa email/sms/telegram tetiklenir. Default false. */
  notify_email?: boolean;
  notify_sms?: boolean;
  notify_telegram?: boolean;
};

/** Backend host'unun anlik kaynak metrikleri (`/system-status/host`).
 *  Sistem Durumu sayfasinda canli yenilenir. */
export type HostStatusInfo = {
  hostname: string;
  os_name: string;
  os_release: string;
  machine: string;
  python_version: string;
  /** Unix timestamp (saniye). */
  boot_time: number;
  uptime_seconds: number;
  process_pid: number;
  process_uptime_seconds: number;
};

export type HostCpuMetrics = {
  /** 0-100 arasi tum CPU agirlikli ortalama. */
  percent: number;
  per_cpu_percent: number[];
  load_avg_1m?: number | null;
  load_avg_5m?: number | null;
  load_avg_15m?: number | null;
  physical_cores?: number | null;
  logical_cores?: number | null;
};

export type HostMemoryMetrics = {
  total_bytes: number;
  used_bytes: number;
  available_bytes: number;
  percent: number;
  swap_total_bytes?: number | null;
  swap_used_bytes?: number | null;
  swap_percent?: number | null;
};

export type HostDiskMetrics = {
  path: string;
  total_bytes: number;
  used_bytes: number;
  free_bytes: number;
  percent: number;
};

export type HostNetworkMetrics = {
  bytes_sent: number;
  bytes_recv: number;
  packets_sent: number;
  packets_recv: number;
};

export type HostStatus = {
  info: HostStatusInfo;
  cpu: HostCpuMetrics;
  memory: HostMemoryMetrics;
  disk: HostDiskMetrics;
  network: HostNetworkMetrics;
  /** Olcum aninin Unix timestamp'i (saniye). */
  sampled_at: number;
};

/** Sistem servisleri saglik raporu (`/system-status/services`). */
export type ServiceRole = "db" | "broker" | "worker" | "gateway" | "self";

export type ServiceStatus = {
  name: string;
  role: ServiceRole;
  healthy: boolean;
  /** Probe gidiş-dönüş suresi (ms). */
  latency_ms?: number | null;
  /** Hata veya bilgi mesaji. */
  detail?: string | null;
  /** host:port veya url. */
  endpoint?: string | null;
};

export type ServicesReport = {
  services: ServiceStatus[];
  sampled_at: number;
};

/** Bildirim merkezi (Header zil ikonu). */
export type NotificationCategory =
  | "alarm"
  | "alarm_assignment"
  | "alarm_comment"
  | "system"
  | "info"
  | "warning"
  | "error"
  | string;

export type NotificationItem = {
  id: number;
  recipient_username: string | null;
  category: NotificationCategory;
  severity: string;
  title: string;
  body?: string | null;
  actor_username?: string | null;
  link?: string | null;
  metadata_json?: string | null;
  is_read: boolean;
  read_at?: string | null;
  created_at: string;
};
