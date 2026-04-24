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

export type DeviceRow = {
  id: number;
  code: string;
  name: string;
  description?: string;
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
  assigned_to?: string | null;
  acknowledged?: boolean;
  reset?: boolean;
  acknowledged_at?: string | null;
  reset_at?: string | null;
  created_at: string;
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
};

export type OutboundTarget = {
  id: number;
  name: string;
  protocol: "rest" | "mqtt";
  endpoint: string;
  topic?: string | null;
  event_filter: "all" | "telemetry" | "alarm";
  auth_header?: string | null;
  auth_token?: string | null;
  qos: number;
  retain: boolean;
  is_active: boolean;
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
};

export type SignalDataType =
  | "analog"
  | "analog_output"
  | "binary"
  | "binary_output"
  | "counter"
  | "string";

export type SignalSource = "master" | "sat01" | "sat02";

export type SignalCatalogRow = {
  id: number;
  key: string;
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
};

export type SignalLiveRow = {
  signal_key: string;
  signal_label: string;
  unit?: string | null;
  source: SignalSource;
  device_id: number;
  device_code: string;
  device_name: string;
  value: number | null;
  quality: string | null;
  source_timestamp: string | null;
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
};
