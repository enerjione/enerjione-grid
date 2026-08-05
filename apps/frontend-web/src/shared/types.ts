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
  return { ...DEFAULT_DNP3_EXTENDED, ...rest };
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
  installationDate?: string;
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

export type UserRole = "operator" | "engineer" | "installer" | "ops_manager";

export type LicenseState =
  | "valid"
  | "unlicensed"
  | "invalid"
  | "machine_mismatch"
  | "machine_unavailable";

/** Arayuz kilidinin minimal gorunumu (GET /license/gate) — tum rollere acik.
    Ticari bilgi TASIMAZ; sadece "kilitli mi, neden". */
export type LicenseGate = {
  locked: boolean;
  state: LicenseState;
  reason_code: string;
};

export type LicenseStatus = {
  state: LicenseState;
  reason_code: string;
  is_valid: boolean;
  can_add_device: boolean;
  quota_state: "available" | "full" | "over_limit" | "unavailable";
  installation_id: string;
  machine_fingerprint?: string | null;
  license_id?: string | null;
  customer_code?: string | null;
  customer_name?: string | null;
  project_name?: string | null;
  note?: string | null;
  device_limit: number;
  device_count: number;
  remaining: number;
  issued_at?: string | null;
};

export type AuthSession = {
  accessToken: string;
  username: string;
  role: UserRole;
  /** Backend `must_change_password=true` dondurduyse frontend zorla
   * ChangePasswordModal acar; kullanici sifresini degistirene kadar
   * diger sayfalara navigation yapamamali. */
  mustChangePassword?: boolean;
};

export type ApiDevice = {
  id: number;
  code: string;
  name: string;
  description?: string | null;
  model?: string | null;
  installation_date?: string | null;
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
  language?: string | null;
  /** true = davet edildi ama henuz sifre belirlemedi (hashed_password=NULL).
   *  UI'da "Davet bekliyor" rozeti + "Daveti yeniden gonder" butonu gosterir. */
  pending_invitation?: boolean;
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
  /** Bu alarm gercek hat arizasi uretir mi? Harita cihazi yalniz
   *  produces_fault !== false ise kirmizi gosterir. Eski/undefined kayitlar
   *  true kabul edilir (geriye uyum). */
  produces_fault?: boolean;
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
  /** Hat basindan arizanin en yakin sinirina TEL mesafesi (metre, kus ucusu
      degil). Sinir = son arizayi goren cihazin konumu. */
  zone_start_m?: number | null;
  /** Hat basindan arizanin en uzak sinirina tel mesafesi (metre). Sinir = ilk
      arizayi gormeyen cihaz; yoksa hattin son diregi. */
  zone_end_m?: number | null;
  /** Belirsizlik araligi = zone_end_m - zone_start_m (metre). */
  zone_length_m?: number | null;
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

/** Calisan surum + (bilgi amacli) guncelleme durumu.
 *  `GET /system-status/version`. Panelden guncelleme YAPILAMAZ — bu veri
 *  yalnizca gosterim icindir. */
export type VersionInfo = {
  current: string;
  /** UPDATE_CHECK_URL tanimli mi; false ise "kontrol kapali". */
  check_enabled: boolean;
  latest?: string | null;
  update_available: boolean;
  error?: string | null;
  checked_at?: number | null;
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
  /** Bugun (UTC gun basindan itibaren) normale donen/kapatilan ariza sayisi. */
  resolved_today_count?: number;
};

/** Kullanici-bazli bildirim kanal tercihleri. */
export type UserNotificationPreferences = {
  user_id: number;
  web_enabled: boolean;
  email_enabled: boolean;
  sms_enabled: boolean;
  telegram_enabled?: boolean;
  whatsapp_web_enabled?: boolean;
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
  /** Token ARTIK LISTEDE DONMUYOR — yalnizca "tanimli mi" bilgisi gelir.
   *  Liste operator'a da acik ve token telemetri gonderiminin tek kimlik
   *  unsuru; duz metin dondugu surece operator kendi alani disindaki cihazlar
   *  icin uydurma telemetri gonderebiliyordu. Gercek degeri INSTALLER
   *  `fetchGatewayToken()` ile ister (backend denetim kaydina yazar). */
  has_token?: boolean;
  /** DNP3 kalite bayraklarini yayinla mi (invalid / restart / forced).
   *  Acmak saha davranisini degistirir: kotu olcumler alarm
   *  degerlendirmesinden bloke olur. Gateway bazinda ayarlanir. */
  publish_dnp3_quality?: boolean;
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

/** Bu cihazda (host ajani e1-gwd ile) kurulu bir gateway container'i. */
export type LocalGateway = {
  code: string;
  name?: string | null;
  container?: string | null;
  /** running | exited | created | absent | unknown */
  state: string;
  status?: string | null;
  image?: string | null;
  ports?: string | null;
  installed_at?: string | null;
  /** Calisan imajin kayit defteri digest'i. */
  image_digest?: string | null;
  /** Etiketin kayit defterindeki su anki digest'i. */
  remote_digest?: string | null;
  /**
   * UC DURUMLU. `null`/`undefined` = BILINMIYOR (kayit defterine
   * ulasilamadi). `false` ile ayni sayma: "guncel" demek, sormadan
   * verilmis bir iddia olur ve arayuzde yanlis guven yaratir.
   */
  update_available?: boolean | null;
  /** Calisan imajin okunabilir surumu (OCI etiketi). Yalnizca GOSTERIM;
   *  guncelleme karari `update_available` ile verilir. */
  local_version?: string | null;
  /** Kayit defterindeki imajin okunabilir surumu. */
  remote_version?: string | null;
};

/** Host ajaninin isledigi son kurulum/kaldirma istegi. */
export type GatewayApplyStatus = {
  id?: string | null;
  action?: string | null;
  code?: string | null;
  ok?: boolean | null;
  /** validate | pull | up | down | restart | cleanup | done | docker */
  stage?: string | null;
  message?: string | null;
  /** Docker ciktisinin son satirlari — hata durumunda UI'da gosterilir. */
  detail?: string | null;
  running: boolean;
  at?: string | null;
};

/** Gateway kurulum ajaninin (e1-gwd) durumu.
 *
 *  `available: false` HATA DEGIL: ajan kurulu olmayan bir kurulumda (ornegin
 *  setup-gateway-agent.sh calistirilmamis) "bu cihaza kur" secenegi kapali
 *  gosterilir; dosya indirip baska cihaza kurma akisi etkilenmez. */
export type GatewayAgentStatus = {
  available: boolean;
  /** state_dir_missing | state_dir_not_writable | agent_never_reported |
   *  state_stale | unreachable */
  reason?: string | null;
  docker_available: boolean;
  updated_at?: string | null;
  state_age_seconds?: number | null;
  gateways: LocalGateway[];
  pending: boolean;
  last_apply?: GatewayApplyStatus | null;
};

/** MQTT outbound target icin custom topic mapping satiri.
 *  Operator UI "Custom Topic Mapping" modal'inda her satir bir mapping.
 *  device_codes/signal_keys CSV (bos = tum cihazlar/sinyaller). */
export type OutboundTopicMapping = {
  id: number;
  target_id: number;
  topic: string;
  device_codes: string;
  signal_keys: string;
  qos: number | null;
  retain: boolean | null;
  is_active: boolean;
};

export type OutboundTarget = {
  id: number;
  name: string;
  protocol: "rest" | "mqtt" | "iec104" | "modbus";
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
  // Modbus TCP hedefi icin (protocol === "modbus"):
  /** block = tek unit, cihazlar adres bloklarinda | unit = cihaz basina unit id */
  modbus_mode?: "block" | "unit";
  modbus_unit_id?: number;
  modbus_value_format?: "int16" | "float32";
  modbus_word_order?: "big" | "little";
  /** null = otomatik (int16 -> 100, float32 -> 200) */
  modbus_block_stride?: number | null;
  modbus_base_address?: number;
  modbus_allowed_peers?: string | null;
  // MQTT hedefi icin (protocol === "mqtt"):
  mqtt_port?: number | null;
  mqtt_username?: string | null;
  mqtt_password?: string | null;
  mqtt_client_id?: string | null;
  mqtt_tls_enabled?: boolean;
  mqtt_tls_insecure?: boolean;
  mqtt_tls_ca_path?: string | null;
  mqtt_tls_cert_path?: string | null;
  mqtt_tls_key_path?: string | null;
  mqtt_keepalive_sec?: number;
  mqtt_connect_timeout_sec?: number;
  /** Per-target periyodik publish saniye. 0 = anlik. */
  mqtt_publish_interval_sec?: number;
  /** Topic template — variables: {prefix} {customer} {device} {source} {datatype} {signal}. */
  mqtt_topic_template?: string | null;
  mqtt_topic_prefix?: string;
  mqtt_customer_id?: string | null;
  /** Liste sayfasinda mapping'ler de yuklenir (selectinload). */
  topic_mappings?: OutboundTopicMapping[];
};

export type Iec104RuntimeStatus = {
  target_id: number;
  server_running: boolean;
  whitelist_active: boolean;
  allowed_peers: string[];
  connected_clients: { peer: string; started: boolean; connected_at: string }[];
};

// ===== Public API — Personal Access Token (PAT) =====
// Kullanici Postman/curl/script ile dis erisim icin token uretir. Token plain
// hali yalniz olusturma cevabinda bir kez doner; sonra DB'de sha256 hash kalir.

export type ApiKey = {
  id: number;
  name: string;
  token_prefix: string;
  scopes: string[];
  created_at: string;
  expires_at?: string | null;
  last_used_at?: string | null;
  revoked_at?: string | null;
  allowed_ips?: string[] | null;
  is_active: boolean;
};

/** /api-keys POST yaniti — ApiKey alanlari + plain token (bir kerelik). */
export type ApiKeyCreated = ApiKey & { token: string };

export type ApiKeyCreatePayload = {
  name: string;
  scopes?: string[];
  expires_at?: string | null;
  allowed_ips?: string[] | null;
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

export type PoleType = "pole" | "transformer" | "breaker";

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
  /** FCI yon oryantasyonu: "green_forward" | "red_forward" | null. */
  device_orientation?: string | null;
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

/** Toast bildiriminin ekrandaki kosesi.
 *
 * Degerler backend `schemas/project_settings.py` ToastPosition ve
 * `styles.css` `.toast-container--<deger>` sinifi ile BIREBIR ayni metindir —
 * uc yerde birlikte degistirilmeli. */
export type ToastPosition = "bottom-right" | "bottom-left" | "top-right" | "top-left";

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
  /** Toast bildirimlerinin kosesi. null/tanimsiz -> "bottom-right" (mevcut davranis). */
  toast_position?: ToastPosition | null;
  /** Kendiliginden gelen (alarm) bildirimleri sustur. null/false -> gorunur.
   *  Kullanici eyleminin sonucu olan toast'lar (kaydedildi/hata) HER ZAMAN
   *  gosterilir; bu bayrak onlari ETKILEMEZ. */
  toast_muted?: boolean | null;
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
  /** Twilio'ya ozel — Account SID (AC...) ve gonderen numara (E.164). */
  sms_account_sid?: string;
  sms_from_number?: string;
  /** WhatsApp Web (Baileys self-hosted sidecar, QR ile giris) aktif mi.
   *  Baglanti durumu/QR DB'de saklanmaz, her zaman sidecar'dan canli cekilir. */
  whatsapp_web_enabled?: boolean;
  /** Secili grup JID listesi (virgulle ayrili), sidecar'in /groups
   *  endpoint'inden kesfedilip UI'da secilir. Grup JID: <id>@g.us. */
  whatsapp_web_group_jids?: string;
  /** Acik: sadece secili gruplara git. Kapali: kullanicinin phone_number'ina git. */
  whatsapp_web_group_mode?: boolean;
  /** Telegram Bot ile bildirim gondermek icin. */
  telegram_enabled?: boolean;
  telegram_bot_token?: string;
  /** Virgulle ayrili chat ID listesi (kanal/grup). */
  telegram_chat_ids?: string;
};

export type WhatsappWebGroup = {
  jid: string;
  name: string;
  participants: number;
};

export type SignalDataType =
  | "analog"
  | "binary"
  | "counter"
  | "string"
  // binary_output (G10) = DNP3 CROB komut kanali; yayinlanmaz, cihaz komutu
  // (Trigger Download, Reset...) icin dnp3_index adresini tutar.
  | "binary_output"
  | "analog_output";

/** Komut durumu. pending=kuyrukta, sent=gateway'e iletildi (config-poll),
 *  ok/failed=gateway sonuc bildirdi, expired=sonuc gelmedi. */
export type DeviceCommandStatus = "pending" | "sent" | "ok" | "failed" | "expired";

/** POST /devices/{code}/command yaniti — komut KUYRUGA alindi (anlik sonuc degil).
 *  Gateway NAT arkasinda; komut ~config_refresh_sec icinde iletilir. */
export type DeviceCommandQueued = {
  id: number;
  status: DeviceCommandStatus;
  command: string;
  dnp3_index: number;
};

/** GET /devices/{code}/commands satiri — komut kaydi + durum takibi. */
export type DeviceCommandRow = {
  id: number;
  device_code: string;
  command: string;
  dnp3_index: number;
  status: DeviceCommandStatus;
  result_status?: string | null;
  result_error?: string | null;
  actor_username?: string | null;
  created_at: string;
  completed_at?: string | null;
};

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
  /** Bu sinyalin okumalari arsive (historian) yazilsin mi? Kapaliysa yalnizca
   *  son deger tutulur — ekran ve alarm etkilenmez, GECMIS olusmaz.
   *  Eski backend surumleri alani gondermiyor; okurken `!== false` kullanin. */
  historize?: boolean;
  /** Olu bant — MUTLAK, sinyalin kendi biriminde. 0 = suzgec kapali.
   *  YALNIZCA `analog` tipte uygulanir; diger tiplerde deger tasinsa bile
   *  motor onu yok sayar (bkz. historian_policy.OLU_BANT_TIPLERI). */
  historize_deadband?: number;
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
  /** Operatorun elle degistirdigi alanlarin adlari.
   *
   *  Backend her acilista fabrika katalogunu senkronlar; bu listedeki
   *  alanlara DOKUNMAZ. Onceden isaret yoktu ve kaydedilen duzenlemeler
   *  (IOA, scale, label...) ilk yeniden baslatmada sessizce geri aliniyordu.
   *  Arayuz bu alanlari "fabrika degerinden farkli" olarak isaretleyebilir. */
  user_overrides?: string[] | null;
};

/** Toplu arsiv ayari istegi (POST /signals/historian/bulk). */
export type SignalHistorianBulkPayload = {
  signal_keys: string[];
  historize?: boolean;
  historize_deadband?: number;
  /** Ariza gecisi tasiyan sinyallerin (binary / binary_output) arsivi
   *  KAPATILIYORSA zorunlu; kullanici uyariyi onaylayinca true gider. */
  confirm_fault_signals?: boolean;
};

export type SignalHistorianBulkResult = {
  updated: number;
  unchanged: number;
  /** Olu bant istendi ama tip analog olmadigi icin uygulanmayan sinyaller. */
  skipped_deadband: string[];
  not_found: string[];
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
  /** Cihaz saatinin guvenilirligi: "synchronized" | "unsynchronized" |
   *  "invalid". `null`/undefined = BILGI YOK (eski gateway alani gondermiyor)
   *  ve bu durumda UI hicbir uyari gostermez — "bilmiyoruz" ile "saat bozuk"
   *  ayni sey degildir. `quality` ile karistirilmamali: o DNP3 olcum
   *  kalitesidir, saat kaymasi olcumu gecersiz kilmaz. */
  timestamp_quality?: string | null;
  /** Cihazin kendi bildirdigi olay zamani. `source_timestamp` (gateway saati)
   *  ile yan yana gosterilir; kaymanin yonunu ve buyuklugunu operator boylece
   *  gorur. */
  device_event_at?: string | null;
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

/** Kural tipi: 'simple' = tek sinyal + tek karsilastirma (legacy/varsayilan).
 *  'composite' = AND/OR ile birden fazla terim (Faz 1). */
export type AlarmRuleKind = "simple" | "composite";

/** Composite kuralda agg fonksiyonlari (Faz 2). */
export type AlarmAggFn =
  | "avg"
  | "min"
  | "max"
  | "sum"
  | "count_above"
  | "count_below";

/** Formul ifadesindeki bir degisken (Faz 3). */
export type AlarmFormulaVar = {
  name: string;
  signal_key: string;
  device_code: string;
};

/** Composite kuraldaki tek bir terim.
 *   kind='compare' : Faz 1 — anlik sinyal degeri.
 *   kind='agg'     : Faz 2 — son N saniyenin penceresi.
 *   kind='formula' : Faz 3 — guvenli aritmetik ifade (degiskenler sinyaller). */
export type AlarmCompositeTerm = {
  kind?: "compare" | "agg" | "formula";
  signal_key: string;
  /** "*" => kuralin anchor cihazi. Spesifik cihaz kodu da yazilabilir. */
  device_code: string;
  comparator: AlarmComparator;
  threshold: number;
  threshold_high?: number | null;
  /** kind === 'agg' icin pencere fonksiyonu. */
  agg_fn?: AlarmAggFn | null;
  agg_window_sec?: number;
  /** count_above / count_below icin sayilacak deger. */
  agg_arg?: number;
  /** kind === 'formula' icin ifade ve degiskenler. */
  formula_expr?: string | null;
  formula_vars?: AlarmFormulaVar[];
};

/** Composite kural ifadesi. */
export type AlarmCompositeExpression = {
  logic: "AND" | "OR";
  terms: AlarmCompositeTerm[];
};

export type AlarmRuleRow = {
  id: number;
  signal_key: string;
  name: string;
  description?: string | null;
  level: AlarmLevel;
  rule_kind?: AlarmRuleKind;
  expression?: AlarmCompositeExpression | null;
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
  notify_whatsapp_web?: boolean;
  /** "Bu alarm gercek hat arizasi uretir mi?" True (default): harita kirmizi +
   *  Hat Arizasi acilir. False: yalniz Alarmlar ekraninda gorunur. */
  produces_fault?: boolean;
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
export type ServiceRole = "db" | "broker" | "worker" | "gateway" | "ftp" | "web" | "self";

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

/** Historian (`telemetry_history`) yapisal sagligi — `/system-status/historian`.
 *
 *  Bu tablo 90 gun retention'li bir TimescaleDB hypertable olarak tasarlandi.
 *  Retention kurulmazsa 600 cihazda gunde ~26M satir birikir ve disk dolana
 *  kadar hicbir belirti vermez; bu kart o sessiz arizayi gorunur kilar. */
export type HistorianProblem =
  | "timescaledb_missing"
  | "not_hypertable"
  | "no_retention"
  | "no_compression"
  | "retention_failing"
  | "retention_mismatch";

export type HistorianStatus = {
  table: string;
  timescaledb: "installed" | "available_not_installed" | "unavailable";
  is_hypertable: boolean;
  retention_days?: number | null;
  compression_enabled: boolean;
  continuous_aggregates: string[];
  /** pg_class.reltuples TAHMINI — tam sayim degil (COUNT(*) cok pahali). */
  row_estimate?: number | null;
  total_bytes?: number | null;
  oldest_sample_at?: string | null;
  newest_sample_at?: string | null;
  retention_last_run_status?: string | null;
  severity: "ok" | "warning" | "critical";
  problems: HistorianProblem[];
};

/** Telemetri boru hattinin anlik durumu — "tuketici yetisiyor mu?".
 *
 *  NEDEN VAR: telemetri NATS stream'inde tamponlanir ve stream `discard=old`
 *  ile calisir. Tuketici gelis hizinin gerisine duserse tampon dolar ve EN
 *  ESKI mesajlar SESSIZCE dusurulur — ekranda hata yok, sadece bazi okumalar
 *  hic gelmemis olur. Bu gosterge o sessizligi gorunur kilar. */
export type TelemetryPipelineStatus = {
  running: boolean;
  connected: boolean;
  /** Kalicilastirmanin BESLENDIGI akis. "normalized" hedef mimaridir
   *  (tag-engine cikisi); "raw" gecis oncesi drenaj fazidir. */
  source?: "raw" | "normalized" | null;
  /** Tuketicinin ONUNDE bekleyen mesaj sayisi (JetStream num_pending).
   *  Surekli 0 civari beklenir; kalici buyume tuketicinin geride oldugunu
   *  ve tampon tasarsa veri kaybi baslayacagini gosterir. */
  backlog?: number | null;
  /** Son 60 saniyelik kayan ortalama islenmis mesaj/sn. */
  throughput_msgs_per_sec: number;
  last_batch_size: number;
  last_batch_duration_sec: number;
  last_fetch_at?: string | null;
  processed_total: number;
  /** Parse/dogrulama hatasi alip DLQ'ya giden mesajlar. */
  bad_total: number;
  reconnects: number;
  last_error?: string | null;
  backlog_warn_threshold: number;
  /** ok | warning (backlog yuksek) | critical (tuketici durmus veya NATS yok). */
  severity: "ok" | "warning" | "critical";
  /** Asama bazli kuyruklar (NATS monitor'den). null/undefined = monitor'e
   *  ulasilamadi; panel asamasiz gosterime duser. */
  stages?: PipelineStageQueues | null;
};

/** Boru hattinin her asamasinin kendi kuyrugu. Tek "bekleyen" sayisi, ust
 *  kuyruk alt kuyruga bosalirken "kuyruk kendi kendine artiyor" yanilgisi
 *  yaratiyordu; asamalar ayri gosterilir. Hizlar sunucudan GELMEZ — ardisik
 *  iki orneklemin last_seq farkindan istemcide turetilir. */
export type PipelineStageQueues = {
  raw_pending?: number | null;
  normalized_prio_pending?: number | null;
  normalized_bulk_pending?: number | null;
  normalized_legacy_pending?: number | null;
  alarm_prio_pending?: number | null;
  alarm_bulk_pending?: number | null;
  raw_last_seq?: number | null;
  normalized_last_seq?: number | null;
  sampled_at?: string | null;
};

// ---- Modbus TCP outbound adres plani (`/outbound-targets/{id}/modbus-plan`) --
// Plan backend'de uretilir; modbus-outbound worker'i AYNI plani uygular.
// Yani buradaki adres, sahada yayinlanan adresin ta kendisidir.

export type ModbusLayoutSummary = {
  register_words: number;
  discrete_bits: number;
  coil_bits: number;
  analog_count: number;
  counter_count: number;
  binary_count: number;
  binary_output_count: number;
  /** Modbus'a dahil edilmeyen string sinyal sayisi. */
  excluded_string_count: number;
};

export type ModbusCapacity = {
  mode: string;
  stride: number;
  max_devices: number;
  device_count: number;
  remaining: number;
  /** Cihazin tum register'lari tek Modbus okumasina (125) siginiyor mu? */
  single_read_per_device: boolean;
  /** address_space | bit_space | unit_id_range */
  limit_reason: string;
};

export type ModbusDeviceSlot = {
  device_id: number;
  device_code: string;
  device_name: string;
  slot_index: number;
  unit_id: number;
  block_start: number;
};

export type ModbusPlanPoint = {
  device_code: string;
  device_name: string;
  signal_key: string;
  label: string;
  source: string;
  data_type: string;
  unit?: string | null;
  unit_id: number;
  /** 1=coil, 2=discrete input, 3=holding register (4=input register aynasi) */
  function: number;
  address: number;
  word_count: number;
  scale: number;
  offset: number;
  manual: boolean;
};

export type ModbusPlan = {
  target_id: number;
  target_name: string;
  mode: string;
  value_format: string;
  word_order: string;
  unit_id: number;
  base_address: number;
  stride: number;
  listen_host: string;
  listen_port: number;
  is_active: boolean;
  allowed_peers: string[];
  summary: ModbusLayoutSummary;
  capacity: ModbusCapacity;
  devices: ModbusDeviceSlot[];
  points: ModbusPlanPoint[];
};

// ---- Appliance ag ayarlari (`/network/*`) ---------------------------------
// Kaynak: mini PC'de root ile calisan e1-netd ajaninin yazdigi state.json.
// Backend host agina dokunmaz; sadece bu dosyayi okur.

export type NetworkInterface = {
  ifname: string;
  type: string;
  state?: string | null;
  connection?: string | null;
  managed_by_e1: boolean;
  mac?: string | null;
  /** Cihazin su anki adresleri — "192.168.1.50/24". */
  addresses: string[];
  gateway?: string | null;
  dns: string[];
  /** Profildeki kalici niyet: "auto" (DHCP) | "manual" (statik). */
  method?: string | null;
  profile_addresses: string[];
  profile_gateway?: string | null;
  profile_dns: string[];
};

export type AccessPointInfo = {
  connection?: string | null;
  exists: boolean;
  active: boolean;
  ssid?: string | null;
  ifname?: string | null;
  address?: string | null;
  secured: boolean;
};

/** Appliance'in WiFi CLIENT (station) durumu — bir aga baglanma tarafi.
 *  AP (erisim noktasi) ayri: `AccessPointInfo`. */
export type WifiState = {
  supported: boolean;
  ifname?: string | null;
  connection?: string | null;
  connected: boolean;
  /** Bagli degilken bile KAYITLI profilin SSID'i doner (ajan sema 3). */
  ssid?: string | null;
  signal?: number | null;
  addresses: string[];
  /** Kayitli profil var mi (baglanti kopuk olsa bile). */
  saved: boolean;
  /** AP geri donus muhafizi aktif mi + ne zaman dolacak (epoch saniye). */
  guard_active: boolean;
  guard_deadline?: number | null;
};

/** WiFi KARTININ kendisi — fiziksel onkosul (olcum).
 *  Kart kapaliyken ne erisim noktasi yayinlanabilir ne de ag taranabilir. */
export type WifiRadioState = {
  supported: boolean;
  enabled: boolean;
  hardware_enabled: boolean;
  /** "hardware" -> cihaz uzerindeki anahtar; arayuzden ACILAMAZ. */
  blocked_by?: "software" | "hardware" | "unmanaged" | null;
  /** Kart cekirdekte var ama NetworkManager yonetmiyor. */
  unmanaged?: boolean;
  /** Kullanicinin arayuzden verdigi son acik karar (null = hic dokunulmamis). */
  desired?: "on" | "off" | null;
  changed_at?: string | null;
  /** Kablo gidince ajanin WiFi'yi kendiliginden actigi an. */
  auto_restored_at?: string | null;
};

/** WiFi kartinin GOREVI. `mode` TERCIH, `effective` OLCUM — ayni rozete
 *  BAGLANMAZ: eski panel kurali olcum gibi gosterip yalan soyluyordu. */
export type WifiRoleState = {
  mode: "ap" | "client";
  effective: "ap" | "client" | "off" | "idle";
  since?: string | null;
  set_by?: string | null;
  /** Tercih client ama aga ulasilamadigi icin AP'ye donuldu mu. */
  fallback_active: boolean;
  /** epoch saniye — guard_deadline ile ayni birim. */
  fallback_since?: number | null;
  next_retry_at?: number | null;
  /** Radyoda bizim olmayan bir client baglantisi varsa SSID'i. */
  foreign_client?: string | null;
};

/** Internet erisimi — "erisim noktasi acik" ile AYNI SEY DEGIL.
 *  Guncelleme / uzaktan bakim / saat senkronu buna baglidir. */
export type InternetState = {
  state: "full" | "portal" | "limited" | "none" | "unknown";
  source?: "nm" | "route" | "probe" | null;
  ifname?: string | null;
  via?: "ethernet" | "wifi" | "vpn" | "other" | null;
  gateway?: string | null;
  checked_at?: string | null;
};

/** Taramada gorunen tek bir WiFi agi. */
export type WifiNetwork = {
  ssid: string;
  signal: number;
  security?: string | null;
  secured: boolean;
  freq?: string | null;
  in_use: boolean;
};

export type WifiScanResult = {
  available: boolean;
  updated_at?: string | null;
  ifname?: string | null;
  networks: WifiNetwork[];
  age_seconds?: number | null;
  /** Bu sonuc cihazin kendi agi indirilerek mi alindi (derin tarama)? */
  deep?: boolean;
  /** Tarama sirasinda cihazin kendi agi yayindaydi: tek radyo AP modunda
   *  kanal degistiremez, liste EKSIK olabilir. */
  ap_was_active?: boolean;
};

export type NetworkApplyStatus = {
  request_id?: string | null;
  /** applying | applied | rebooting | failed */
  status?: string | null;
  error?: string | null;
  at?: string | null;
  applied?: Record<string, unknown> | null;
};

export type NetworkStatus = {
  available: boolean;
  /** Kapaliysa sebep kodu: state_dir_missing | agent_never_reported | state_stale ... */
  reason?: string | null;
  hostname?: string | null;
  mdns_name?: string | null;
  updated_at?: string | null;
  state_age_seconds?: number | null;
  /** Ajanin state.json sema surumu. Uc katmanli gorunum (radio/role/internet)
   *  YALNIZCA `>= 3` iken ANLAMLIDIR; eski ajan bu bloklari hic yazmaz ve
   *  varsayilanlari "WiFi karti yok" gibi gorunur. UI eski ajanda bu alanlari
   *  OKUMAZ, "bilinmiyor" der. */
  agent_schema?: number | null;
  ap: AccessPointInfo;
  /** WiFi client (station) durumu — bir aga baglanma tarafi. */
  /** OLCUM YOK ise null (bkz. radio/role/internet). */
  wifi?: WifiState | null;
  /** OLCUM YOK ise null gelir (eski ajan ya da ajan hata state'i yazmis).
   *  Backend bilerek BOS NESNE URETMEZ: supported=false iceren bir nesne
   *  arayuzde "Cihazda WiFi karti bulunamadi" gibi OLCULMUS bir donanim
   *  iddiasina donusuyordu. null = "bilinmiyor". */
  radio?: WifiRadioState | null;
  role?: WifiRoleState | null;
  internet?: InternetState | null;
  interfaces: NetworkInterface[];
  pending: boolean;
  last_apply?: NetworkApplyStatus | null;
};

export type NetworkConfigPayload = {
  ifname: string;
  method: "dhcp" | "static";
  address?: string | null;
  prefix?: number | null;
  gateway?: string | null;
  dns: string[];
  reboot: boolean;
};

export type NetworkConfigAccepted = {
  request_id: string;
  reboot: boolean;
  next_url?: string | null;
};

// ---- Uzaktan bakim izni (`/remote-access/*`) -------------------------------
// Cihaz uretici bakim agina KAYITLI kalir; gelen baglantilar VARSAYILAN OLARAK
// reddedilir. Musterinin yetkili kullanicisi SURELI izin verir, sure dolunca
// host ajani erisimi kendiliginden kapatir (sayaci backend DEGIL ajan tutar).
// Kaynak: apps/backend-api/app/schemas/remote_access.py

/** Cihazin bakim agindaki dugum bilgisi. Kullaniciya gosterilen kisim
 *  bilincli olarak dar: ad + adres + kayitli/cevrimici. */
export type RemoteAccessLink = {
  installed: boolean;
  version?: string | null;
  daemon_running: boolean;
  /** Running | Stopped | NeedsLogin | NoState | Starting */
  backend_state?: string | null;
  registered: boolean;
  hostname?: string | null;
  dns_name?: string | null;
  ipv4?: string | null;
  tags: string[];
  /** null = kayit suresi uygulanmiyor (etiketli katilimda istenen durum). */
  key_expiry?: string | null;
  key_expired: boolean;
  ssh_enabled: boolean;
  shields_up: boolean;
};

/** SU ANKI izin penceresi. Kapali ise open=false ve alanlar null. */
export type RemoteAccessGrantState = {
  /** OLCULEN deger — ajan gercekten "gelen baglanti acik" diyorsa true. */
  open: boolean;
  verified: boolean;
  /** prefs_unreadable | open_failed | close_failed */
  mismatch?: string | null;
  session_id?: string | null;
  granted_by?: string | null;
  granted_by_role?: string | null;
  granted_at?: string | null;
  expires_at?: string | null;
  duration_minutes?: number | null;
  reason?: string | null;
  /** ui | install | update | cli */
  source?: string | null;
  /** Kalan sure — SUNUCU hesaplar. Istemci saati saha PC'lerinde guvenilmez. */
  remaining_seconds?: number | null;
  /** Son tarih gecmis ama hala acik: ajan durmus olabilir -> kirmizi uyari. */
  overdue: boolean;
};

/** Kapanmis son oturumun ozeti. */
export type RemoteAccessSession = {
  session_id?: string | null;
  granted_by?: string | null;
  granted_by_role?: string | null;
  granted_at?: string | null;
  expires_at?: string | null;
  ended_at?: string | null;
  /** expired | revoked | clock_skew | lease_corrupt | cli */
  end_reason?: string | null;
  duration_minutes?: number | null;
  reason?: string | null;
  source?: string | null;
};

export type RemoteAccessApplyStatus = {
  request_id?: string | null;
  /** grant | revoke */
  action?: string | null;
  /** applying | applied | failed */
  status?: string | null;
  error?: string | null;
  at?: string | null;
  applied?: Record<string, unknown> | null;
};

export type RemoteAccessStatus = {
  available: boolean;
  /** state_dir_missing | state_dir_not_writable | agent_never_reported | state_stale */
  reason?: string | null;
  /** tailscale_not_installed | daemon_not_running | not_registered | key_expired */
  agent_reason?: string | null;
  updated_at?: string | null;
  /** state.json'in yasi (sn). Buyukse zamanlayici durmus demektir. */
  state_age_seconds?: number | null;
  /** shields | down */
  lock_mode?: string | null;
  tailscale: RemoteAccessLink;
  access: RemoteAccessGrantState;
  last_session?: RemoteAccessSession | null;
  pending: boolean;
  last_apply?: RemoteAccessApplyStatus | null;
  /** Hazir sure butonlari BU sinirlara gore uretilir; koda gomulmez. */
  min_duration_minutes: number;
  max_duration_minutes: number;
  /** Izin verme yetkisi (backend: yalnizca engineer). UI butonu buna bakar. */
  can_grant: boolean;
};

export type RemoteAccessGrantPayload = {
  duration_minutes: number;
  reason?: string | null;
  allow_ssh?: boolean;
};

export type RemoteAccessAccepted = {
  request_id: string;
  action: "grant" | "revoke";
  /** TAHMINI son tarih; kesin deger ajanda. Bir sonraki durum okumasi tazeler. */
  expires_at?: string | null;
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

// ---- Historian (telemetry_history) ----------------------------------------
export type HistoryBucket = "raw" | "10s" | "1m" | "5m" | "1h";

/** bucket=raw: ham historian okuma noktasi. */
export type TelemetryHistoryPoint = {
  signal_key: string;
  value: number | null;
  value_string: string | null;
  quality: string;
  source_timestamp: string;
};

/** bucket=1m|1h: continuous aggregate ozet noktasi. */
export type TelemetryAggregatePoint = {
  signal_key: string;
  bucket: string;
  avg_value: number | null;
  min_value: number | null;
  max_value: number | null;
  sample_count: number;
};

// ---- Cevrimdisi harita karolari ------------------------------------------
export type MapPackStatus = "pending" | "running" | "done" | "failed" | "cancelled";

export type MapPack = {
  id: string;
  name: string;
  layer: string;
  /** [guney, bati, kuzey, dogu] */
  bbox: number[];
  zoom_min: number;
  zoom_max: number;
  tile_total: number;
  tile_done: number;
  tile_failed: number;
  bytes_written: number;
  status: MapPackStatus;
  error: string;
  created_at: string;
  finished_at: string;
};

export type MapTileSummary = {
  layers: { key: string; label: string; max_zoom: number; attribution: string }[];
  cache_bytes: number;
  max_cache_bytes: number;
  max_download_zoom: number;
  max_pack_tiles: number;
  online_fallback: boolean;
  prefer_online: boolean;
  /** Yukari akis su an erisilebilir sayiliyor mu. */
  online: boolean;
  packs: MapPack[];
};

export type MapAreaRequest = {
  layer: string;
  south: number;
  west: number;
  north: number;
  east: number;
  zoom_min: number;
  zoom_max: number;
};

export type MapEstimate = {
  tile_count: number;
  estimated_bytes: number;
  max_tiles: number;
};

// ===== Cihaz yapilandirma dosyasi (Horstmann Configuration.csv) ===========
// Backend semasi: app/schemas/device_config.py — birlikte guncellenir.

export type ConfigRow = {
  catIndex: string;
  group: string;
  index: string;
  length: number;
  // Kisa alanlar sayi, uzun alanlar metin olarak anlamli; backend ikisini de
  // dondurur, hangisinin gosterilecegine arayuz karar verir.
  valueInt: number | null;
  valueText: string | null;
  rawHex: string;
  // Explorer XML katalogu yuklenmemisse null — katalog eksikligi dosyayi
  // goruntulenemez yapmaz.
  meaning: string | null;
  unit: string | null;
};

export type ConfigVersion = {
  id: number;
  deviceId: number;
  version: number;
  source: "sablon" | "cihazdan_cekildi" | "yuklendi" | "duzenlendi";
  templateId: number | null;
  note: string | null;
  createdBy: string | null;
  createdAt: string;
  appliedAt: string | null;
  sizeBytes: number;
  // "gecerli" / "gecersiz" / "bilinmiyor (ayak yok)" UC AYRI durum.
  checksumValid: boolean | null;
};

export type ConfigCurrent = {
  version: ConfigVersion;
  filename: string;
  rows: ConfigRow[];
};

export type ConfigDiffRow = {
  catIndex: string;
  meaning: string | null;
  before: string | null;
  after: string | null;
  beforeInt: number | null;
  afterInt: number | null;
};

export type ConfigTemplate = {
  id: number;
  name: string;
  deviceModel: string;
  sourceFilename: string | null;
  note: string | null;
  isDefault: boolean;
  createdBy: string | null;
  createdAt: string;
  sizeBytes: number;
};

export type BulkApplyResult = {
  applied: number[];
  // Atlananlar SESSIZCE yutulmaz; hangi cihaz neden alamadi burada doner.
  failed: { device_id: number; reason: string; detail?: string }[];
};

// ===== FTP sunucu ayarlari ================================================
// Backend semasi: app/schemas/ftp_settings.py — birlikte guncellenir.

export type FtpMode = "gomulu" | "harici";

export type FtpSettings = {
  mode: FtpMode;
  host: string | null;
  port: number;
  username: string;
  // Acik metin: cihazin FTP ekranina ELLE girilecegi icin kullanici okumali.
  password: string | null;
  directory: string;
  pollIntervalSec: number;
  updatedBy: string | null;
  updatedAt: string | null;
};

export type FtpSettingsUpdate = Partial<{
  mode: FtpMode;
  host: string;
  port: number;
  username: string;
  password: string;
  directory: string;
  pollIntervalSec: number;
}>;

export type FtpTestResult = {
  ok: boolean;
  detail: string;
  // Dizinde gorulen `<seri>_Configuration.csv` sayisi.
  configFiles: number | null;
};

export type FtpEventRow = {
  eventType: string;
  severity: string;
  message: string;
  deviceCode: string | null;
  createdAt: string;
};

export type FtpServerHealth = {
  reachable: boolean;
  // Sunucunun SU AN kabul ettigi kullanici adi.
  username: string | null;
  connections: number | null;
  // Sunucudaki aktif kimlik == ayarlardaki kimlik. Kimlik degisiminden
  // sonraki ~30 saniyede false gorunur.
  synced: boolean | null;
};

export type FtpStatus = {
  mode: FtpMode;
  // Yalnizca gomulu modda dolu.
  server: FtpServerHealth | null;
  events: FtpEventRow[];
};

// Cihaz basina guncel config surumu ozeti (sol liste rozetleri).
export type DeviceConfigSummary = {
  deviceId: number;
  version: number;
  source: ConfigVersion["source"];
  createdAt: string;
  appliedAt: string | null;
};
