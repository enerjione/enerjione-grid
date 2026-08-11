import type {
  AlarmComment,
  AlarmEvent,
  AlarmRuleRow,
  ApiDevice,
  AuthSession,
  DeviceModelOption,
  DeviceRow,
  Dnp3ExtendedSettings,
  DeviceCommandQueued,
  DeviceCommandRow,
  Gateway,
  GatewayAgentStatus,
  GatewayLogs,
  HistoryBucket,
  HostStatus,
  LicenseGate,
  LicenseStatus,
  ModbusPlan,
  NetworkConfigAccepted,
  NetworkConfigPayload,
  NetworkStatus,
  FirewallConfig,
  FirewallConfigAccepted,
  FirewallStatus,
  RemoteAccessAccepted,
  RemoteAccessGrantPayload,
  RemoteAccessStatus,
  TelemetryHistoryPoint,
  TelemetryAggregatePoint,
  TelemetryPipelineStatus,
  HistorianStatus,
  DeviceScanResult,
  DnsResult,
  NotificationItem,
  PingResult,
  PortCheckResult,
  ServicesReport,
  TracerouteResult,
  NotificationSettings,
  OutboundTarget,
  ResponsibilityAreaDetail,
  ResponsibilityAreaRow,
  PhaseCode,
  SignalCatalogRow,
  SignalHistorianBulkPayload,
  SignalHistorianBulkResult,
  SignalLiveRow,
  SystemEvent,
  UserRead,
  UserRole,
  WhatsappWebGroup
} from "./types";
import { mergeDnp3Extended } from "./types";

/** API base URL.
 *
 * Üretim (Docker / nginx reverse proxy): aynı origin altında `/api/v1` proxy edilir.
 *   Bu durumda Vite build sırasında `VITE_API_BASE_URL` set edilirse o kullanılır;
 *   set edilmediğinde same-origin `/api/v1` kullanılır.
 *
 * Geliştirme (Windows native, port 5173 + 8000): VITE_API_BASE_URL boşsa
 *   `${hostname}:8000/api/v1` legacy davranışına geri dön — böylece eski geliştirici
 *   akışı kırılmaz.
 */
// WebSocket hook'undan da kullanilmak icin export. Ayni resolution mantigini
// orada da elde ediliyor olur.
export const API_BASE_URL = (() => {
  const fromEnv = (import.meta.env.VITE_API_BASE_URL ?? "").toString().trim();
  if (fromEnv) return fromEnv.replace(/\/+$/, "");
  // Vite dev server (5173) → backend ayrı port (8000) — eski davranış.
  if (typeof window !== "undefined" && window.location.port === "5173") {
    return `${window.location.protocol}//${window.location.hostname}:8000/api/v1`;
  }
  // Üretimde nginx aynı origin'de /api/v1 proxy ediyor.
  return "/api/v1";
})();
const AUTH_STORAGE_KEY = "hsl-auth";

type LoginResponse = {
  access_token: string;
  token_type: string;
  role: UserRole;
  username: string;
  must_change_password?: boolean;
};

type ApiErrorDetail =
  | string
  | {
      loc?: Array<string | number>;
      msg?: string;
      type?: string;
    };

type ApiErrorResponse = {
  detail?: ApiErrorDetail | ApiErrorDetail[];
};

function authHeaders(token: string): HeadersInit {
  // GERIYE UYUMLULUK: Token bos string ise (HttpOnly cookie kullaniliyor)
  // Authorization header'i koymayiz; backend cookie'den okur. Cookie yokken
  // (eski localStorage akisi) token degeri ile Bearer header'i kullanir.
  if (!token) {
    return { "Content-Type": "application/json" };
  }
  return {
    Authorization: `Bearer ${token}`,
    "Content-Type": "application/json"
  };
}

// HttpOnly cookie ile auth: `credentials: 'include'` tum fetch'lere eklenir
// ki tarayici e1_session cookie'sini istekle gondersin. Same-origin (nginx
// proxy ediyor) icin gerek yok aslinda — ama Vite dev server farkli portta
// olunca cross-origin oluyor ve include zorunlu.
const FETCH_CREDENTIALS: RequestCredentials = "include";

// Global fetch wrapper — tum API_BASE_URL istekleri otomatik `credentials:
// include` alir. Boylece her fetch callsite'ini guncelemeden cookie auth
// her cagrida zorla calisir. authHeaders("") kullanildiginda Authorization
// header'i gitmez; backend e1_session cookie'sinden kullaniciyi cozer.
//
// Backward-compatible: caller `credentials: 'omit'` gibi explicit deger
// gecirirse onu korur (override edilmez); aksi halde 'include' eklenir.
function apiFetch(input: RequestInfo | URL, init: RequestInit = {}): Promise<Response> {
  const opts: RequestInit = {
    ...init,
    credentials: init.credentials ?? FETCH_CREDENTIALS,
  };
  return fetch(input, opts);
}

const SESSION_401_TURKISH =
  "Oturum süresi doldu veya geçerli değil. Lütfen sağ üstten çıkış yapıp tekrar giriş yapın.";

/** 401 durumunda tüm uygulamaya "session expired" sinyali yayınla. App bu
 * event'i dinleyip session'ı temizliyor ve kullaniciyi login ekranina dusuruyor.
 * Bu sayede her API cagrisinin try/catch icine 401 mantigi koymaya gerek yok. */
export const SESSION_EXPIRED_EVENT = "hsl:session-expired";

function notifySessionExpired(): void {
  if (typeof window === "undefined") return;
  window.dispatchEvent(new CustomEvent(SESSION_EXPIRED_EVENT));
}

async function buildApiError(response: Response, fallbackMessage: string): Promise<Error> {
  if (response.status === 401) {
    notifySessionExpired();
    try {
      const data = (await response.json()) as ApiErrorResponse;
      const detail = data.detail;
      if (
        typeof detail === "string" &&
        (detail.includes("validate credentials") || detail.includes("Not authenticated"))
      ) {
        return new Error(SESSION_401_TURKISH);
      }
    } catch {
      // gövde yok
    }
    return new Error(SESSION_401_TURKISH);
  }
  try {
    const data = (await response.json()) as ApiErrorResponse;
    const detail = data.detail;
    if (typeof detail === "string" && detail.trim()) {
      return new Error(sanitizeErrorDetail(detail, response.status, fallbackMessage));
    }
    // Backend bazi hatalarda `detail`i NESNE olarak doner:
    //   {"code": "license_capacity_exceeded", "message": "..."}
    // Once yalnizca string ve dizi isleniyordu; nesne hali sessizce
    // fallback'e dusuyor ve kullaniciya "islem basarisiz" gibi genel bir
    // mesaj gidiyordu. Lisans kapasitesi tam bu yoldan geliyor.
    if (detail && typeof detail === "object" && !Array.isArray(detail)) {
      const msg = (detail as { message?: unknown }).message;
      if (typeof msg === "string" && msg.trim()) {
        return new Error(sanitizeErrorDetail(msg, response.status, fallbackMessage));
      }
    }
    if (Array.isArray(detail) && detail.length > 0) {
      const first = detail[0];
      if (typeof first === "string" && first.trim()) {
        return new Error(sanitizeErrorDetail(first, response.status, fallbackMessage));
      }
      if (first && typeof first === "object") {
        const field = first.loc ? String(first.loc[first.loc.length - 1]) : "alan";
        const msg = first.msg ?? "geçersiz değer";
        return new Error(`Doğrulama hatası (${field}): ${msg}`);
      }
    }
  } catch {
    // ignore body parse error and use fallback
  }
  return new Error(fallbackMessage);
}

/**
 * Backend `detail` mesajini kullaniciya gostermeden once sanitize et.
 *
 * Eski davranis: backend'in donen `detail` string'i (potansiyel olarak
 * SQL hatasi, dosya yolu, baglanti string'i, ic stack trace icerigi)
 * dogrudan toast'a basiliyordu. Bu IP/host disclosure + recon ipucu
 * verir.
 *
 * Yeni davranis: 4xx kullanici hatalari (Validation/RBAC/quota) icin
 * mesaj olduğu gibi gosterilir (kullaniciya yol gosterici). 5xx ve
 * suphell pattern'li mesajlar (path, port, connection string, stack
 * trace) generic fallback'e cevrilir.
 */
function sanitizeErrorDetail(detail: string, status: number, fallback: string): string {
  // Kullanici dostu kisa mesajlar (kanonik kullanim) — pass-through.
  // Tipik durumlar: "Invalid credentials", "Quota exceeded", "Not found",
  // "Account temporarily locked", Turkce kullanici mesajlari.
  if (status < 500 && detail.length <= 200) {
    // Su pattern'ler iceren mesajlari generic'e cevir (icsel detay sizintisi):
    //  - "Traceback", "File \"...\"", "line N" → stack trace
    //  - "psql:", "psycopg2.", "sqlalchemy.", "asyncpg" → DB driver iz
    //  - "HTTPConnection", "ConnectionRefusedError" → backend ag detayi
    //  - "/usr/", "C:\\", "/var/" → dosya yolu
    const suspicious = /Traceback|File "|psql:|psycopg2|sqlalchemy|asyncpg|HTTPConnection|ConnectionRefused|\/usr\/|\/var\/|C:\\\\/i;
    if (suspicious.test(detail)) return fallback;
    return detail;
  }
  // 5xx → her zaman generic. Detay backend log'unda kalsin.
  return fallback;
}

// "Beni hatırla" semantiği:
//  - true   → kalıcı (localStorage). Tarayıcı kapansa bile gelecek açılışta session geri yüklenir.
//  - false  → oturumluk (sessionStorage). Tarayıcı sekmesi kapanınca session silinir.
// loadSession her iki kaynağı da kontrol eder, önce sessionStorage'a (daha
// yeni / tek sekme tercihi) sonra localStorage'a bakar.
export function loadSession(): AuthSession | null {
  const fromSession = sessionStorage.getItem(AUTH_STORAGE_KEY);
  const fromLocal = localStorage.getItem(AUTH_STORAGE_KEY);
  const raw = fromSession ?? fromLocal;
  if (!raw) return null;
  try {
    return JSON.parse(raw) as AuthSession;
  } catch {
    sessionStorage.removeItem(AUTH_STORAGE_KEY);
    localStorage.removeItem(AUTH_STORAGE_KEY);
    return null;
  }
}

export function saveSession(session: AuthSession, remember: boolean = true): void {
  // Önce her iki depolamayı da temizle ki birden fazla kayıt birbirine karışmasın.
  sessionStorage.removeItem(AUTH_STORAGE_KEY);
  localStorage.removeItem(AUTH_STORAGE_KEY);
  // GUVENLIK NOTU: Backend HttpOnly cookie (e1_session) ile auth saglar
  // ama bazı tarayicilar/sub-resource akislarinda (polling setInterval,
  // dev port farkliligi vb.) cookie gonderilmeyebilir. Bu yuzden
  // accessToken'i ek olarak storage'da tutuyoruz; api.ts apiFetch
  // Authorization header'i ekler → cookie olmasa bile auth saglanir.
  // XSS riski: localStorage JS'ten okunabilir, ama uygulamanin CSP'si
  // sıkı; pratikte cookie + header katmanli savunma daha guvenilir.
  const safe: AuthSession = {
    accessToken: session.accessToken,
    username: session.username,
    role: session.role,
  };
  const payload = JSON.stringify(safe);
  if (remember) {
    localStorage.setItem(AUTH_STORAGE_KEY, payload);
  } else {
    sessionStorage.setItem(AUTH_STORAGE_KEY, payload);
  }
}

export function clearSession(): void {
  sessionStorage.removeItem(AUTH_STORAGE_KEY);
  localStorage.removeItem(AUTH_STORAGE_KEY);
  // Eski "hsl." prefix'li (Horstmann Smart Logger doneminden kalma) anahtarlari
  // + halen aktif olarak yazilan kullanici-ozel tercih anahtarlarini logout'ta
  // temizle. Paylasimli PC'de bir sonraki kullanici onceki PII'ye erismesin.
  // Sabit listede gozukmeyen ama "hsl." veya "e1.user." prefix'li tum anahtarlar
  // toplu silinir.
  const PREFIXES_TO_CLEAR = ["hsl.", "e1.user.", "sidebar-collapsed", "route.v1"];
  for (let i = localStorage.length - 1; i >= 0; i--) {
    const key = localStorage.key(i);
    if (!key) continue;
    if (PREFIXES_TO_CLEAR.some((p) => key === p || key.startsWith(p))) {
      localStorage.removeItem(key);
    }
  }
}

export async function logout(token: string): Promise<void> {
  await apiFetch(`${API_BASE_URL}/auth/logout`, {
    method: "POST",
    headers: authHeaders(token),
    // Cookie (e1_session) backend tarafindan delete_cookie ile temizlenir;
    // credentials zorunlu yoksa Set-Cookie atilmaz.
    credentials: FETCH_CREDENTIALS
  });
}

export async function login(username: string, password: string): Promise<AuthSession> {
  const response = await apiFetch(`${API_BASE_URL}/auth/login`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ username, password }),
    // Cookie auth: backend `Set-Cookie: e1_session` ile dondurur; credentials
    // include olmadan tarayici cookie'yi saklamaz. Future-ready.
    credentials: FETCH_CREDENTIALS
  });
  if (!response.ok) {
    throw new Error("Kullanıcı adı veya şifre hatalı.");
  }
  const data = (await response.json()) as LoginResponse;
  return {
    accessToken: data.access_token,
    username: data.username,
    role: data.role,
    mustChangePassword: data.must_change_password === true,
  };
}

/** Davet token'i ile yeni sifre belirle. Auth gerekmez — token zaten secret. */
export async function setupPassword(token: string, newPassword: string): Promise<void> {
  const response = await apiFetch(`${API_BASE_URL}/auth/setup-password`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ token, new_password: newPassword }),
  });
  if (!response.ok) {
    throw await buildApiError(response, "Sifre belirleme basarisiz oldu.");
  }
}

export type InviteUserPayload = {
  username: string;
  email: string;
  full_name: string;
  phone_number?: string;
  role: UserRole;
  send_email: boolean;
};

export type InviteUserResponse = {
  user_id: number;
  username: string;
  setup_url: string;
  expires_at: string;
  email_sent: boolean;
};

export async function inviteUser(
  token: string,
  payload: InviteUserPayload,
): Promise<InviteUserResponse> {
  const response = await apiFetch(`${API_BASE_URL}/users/invite`, {
    method: "POST",
    headers: authHeaders(token),
    body: JSON.stringify(payload),
  });
  if (!response.ok) {
    throw await buildApiError(response, "Davet gonderilemedi.");
  }
  return (await response.json()) as InviteUserResponse;
}

export async function resendInvite(
  token: string,
  userId: number,
): Promise<InviteUserResponse> {
  const response = await apiFetch(`${API_BASE_URL}/users/${userId}/resend-invite`, {
    method: "POST",
    headers: authHeaders(token),
  });
  if (!response.ok) {
    throw await buildApiError(response, "Davet tekrar gonderilemedi.");
  }
  return (await response.json()) as InviteUserResponse;
}

export async function fetchDevices(token: string, gatewayCode?: string): Promise<DeviceRow[]> {
  const endpoint = gatewayCode ? `${API_BASE_URL}/devices?gateway_code=${encodeURIComponent(gatewayCode)}` : `${API_BASE_URL}/devices`;
  const response = await apiFetch(endpoint, {
    headers: authHeaders(token)
  });
  if (!response.ok) throw await buildApiError(response, "Cihaz listesi alınamadı.");
  const devices = (await response.json()) as ApiDevice[];
  return devices.map((item) => ({
    id: item.id,
    code: item.code,
    name: item.name,
    serialNumber: item.serial_number ?? null,
    description: item.description ?? undefined,
    model: item.model ?? "horstmann_sn_2_0",
    installationDate: item.installation_date ?? undefined,
    gatewayCode: item.gateway_code ?? undefined,
    ipAddress: item.ip_address,
    dnp3OutstationPort: item.dnp3_outstation_port ?? 20001,
    dnp3Address: item.dnp3_address,
    dnp3Extended: mergeDnp3Extended(item.dnp3_extended),
    pollIntervalSec: item.poll_interval_sec,
    timeoutMs: item.timeout_ms,
    retryCount: item.retry_count,
    signalProfile: item.signal_profile,
    communicationStatus: item.communication_status,
    batteryPercent: item.battery_percent,
    alarmActive: item.alarm_active,
    lastUpdateAt: item.last_update_at ?? undefined,
    latitude: item.latitude,
    longitude: item.longitude,
    iec104CommonAddress: item.iec104_common_address ?? null,
    // Unite -> faz eslemesi (bu cihaza OZEL). null = proje konvansiyonu.
    phaseMaster: item.phase_master ?? null,
    phaseSat01: item.phase_sat01 ?? null,
    phaseSat02: item.phase_sat02 ?? null,
    phaseSat03: item.phase_sat03 ?? null,
    // Kit / sanal set bagi. Fiziksel kayitlarda parentDeviceId null'dir.
    parentDeviceId: item.parent_device_id ?? null,
    parentDeviceCode: item.parent_device_code ?? null,
    subunitIndex: item.subunit_index ?? null,
    satelliteSetCount: item.satellite_set_count ?? null,
    subunitSatellites: item.subunit_satellites ?? null
  }));
}

export async function fetchDeviceModels(token: string): Promise<DeviceModelOption[]> {
  const response = await apiFetch(`${API_BASE_URL}/device-models`, {
    headers: authHeaders(token)
  });
  if (!response.ok) throw await buildApiError(response, "Cihaz modeli listesi alınamadı.");
  return (await response.json()) as DeviceModelOption[];
}

// Backend PingResult (snake_case) — saha araclari ping testi.
type ApiPingResult = {
  host: string;
  success: boolean;
  packets_sent: number;
  packets_received: number;
  packet_loss_percent: number;
  rtt_min_ms: number | null;
  rtt_avg_ms: number | null;
  rtt_max_ms: number | null;
  output: string;
  duration_ms: number;
};

/** Mini PC'den hedef IP/hostname'e ping testi (installer/engineer). */
export async function pingFieldHost(
  token: string,
  host: string,
  count: number = 4,
): Promise<PingResult> {
  const response = await apiFetch(`${API_BASE_URL}/field-tools/ping`, {
    method: "POST",
    headers: authHeaders(token),
    body: JSON.stringify({ host, count }),
  });
  if (!response.ok) throw await buildApiError(response, "Ping testi çalıştırılamadı.");
  const data = (await response.json()) as ApiPingResult;
  return {
    host: data.host,
    success: data.success,
    packetsSent: data.packets_sent,
    packetsReceived: data.packets_received,
    packetLossPercent: data.packet_loss_percent,
    rttMinMs: data.rtt_min_ms,
    rttAvgMs: data.rtt_avg_ms,
    rttMaxMs: data.rtt_max_ms,
    output: data.output,
    durationMs: data.duration_ms,
  };
}

/** Hedefte TCP portu açık mı (DNP3 20001 vb.) — saha araçları. */
export async function checkFieldPort(
  token: string,
  host: string,
  port: number,
): Promise<PortCheckResult> {
  const response = await apiFetch(`${API_BASE_URL}/field-tools/port-check`, {
    method: "POST",
    headers: authHeaders(token),
    body: JSON.stringify({ host, port }),
  });
  if (!response.ok) throw await buildApiError(response, "Port testi çalıştırılamadı.");
  const data = (await response.json()) as {
    host: string;
    port: number;
    open: boolean;
    elapsed_ms: number;
    error: string | null;
  };
  return {
    host: data.host,
    port: data.port,
    open: data.open,
    elapsedMs: data.elapsed_ms,
    error: data.error,
  };
}

/** Hedefe giden rota (traceroute) — ham çıktı döner, dakikaya yakın sürebilir. */
export async function traceFieldRoute(
  token: string,
  host: string,
): Promise<TracerouteResult> {
  const response = await apiFetch(`${API_BASE_URL}/field-tools/traceroute`, {
    method: "POST",
    headers: authHeaders(token),
    body: JSON.stringify({ host }),
  });
  if (!response.ok) throw await buildApiError(response, "Traceroute çalıştırılamadı.");
  const data = (await response.json()) as {
    host: string;
    success: boolean;
    output: string;
    duration_ms: number;
  };
  return {
    host: data.host,
    success: data.success,
    output: data.output,
    durationMs: data.duration_ms,
  };
}

/** Ad -> IP çözümleme testi — saha araçları. */
export async function resolveFieldDns(token: string, name: string): Promise<DnsResult> {
  const response = await apiFetch(`${API_BASE_URL}/field-tools/dns`, {
    method: "POST",
    headers: authHeaders(token),
    body: JSON.stringify({ name }),
  });
  if (!response.ok) throw await buildApiError(response, "DNS testi çalıştırılamadı.");
  const data = (await response.json()) as {
    name: string;
    resolved: boolean;
    addresses: string[];
    elapsed_ms: number;
  };
  return {
    name: data.name,
    resolved: data.resolved,
    addresses: data.addresses,
    elapsedMs: data.elapsed_ms,
  };
}

/** Kayıtlı cihazlarda toplu ping + DNP3 port testi (<=50 id / istek). */
export async function scanFieldDevices(
  token: string,
  deviceIds: number[],
): Promise<DeviceScanResult[]> {
  const response = await apiFetch(`${API_BASE_URL}/field-tools/scan`, {
    method: "POST",
    headers: authHeaders(token),
    body: JSON.stringify({ device_ids: deviceIds }),
  });
  if (!response.ok) throw await buildApiError(response, "Toplu tarama çalıştırılamadı.");
  const data = (await response.json()) as Array<{
    device_id: number;
    host: string | null;
    ping_success: boolean | null;
    rtt_avg_ms: number | null;
    port: number | null;
    port_open: boolean | null;
    error: string | null;
  }>;
  return data.map((item) => ({
    deviceId: item.device_id,
    host: item.host,
    pingSuccess: item.ping_success,
    rttAvgMs: item.rtt_avg_ms,
    port: item.port,
    portOpen: item.port_open,
    error: item.error,
  }));
}

export async function createDevice(
  token: string,
  payload: {
    code: string;
    name: string;
    serial_number?: string | null;
    description?: string | null;
    model: string;
    installation_date?: string | null;
    gateway_code?: string | null;
    ip_address: string;
    dnp3_outstation_port: number;
    dnp3_address: number;
    dnp3_extended?: Dnp3ExtendedSettings | null;
    poll_interval_sec: number;
    timeout_ms: number;
    retry_count: number;
    signal_profile: string;
    latitude: number;
    longitude: number;
    iec104_common_address?: number | null;
    // Unite -> faz eslemesi. Tip zincirinde EKSIKTI: panel bu alanlari
    // gonderiyordu ama tip bilmiyordu; "tip var mi" diye bakan bir sonraki
    // kisi yanlis sonuca varirdi.
    phase_master?: PhaseCode | null;
    phase_sat01?: PhaseCode | null;
    phase_sat02?: PhaseCode | null;
    phase_sat03?: PhaseCode | null;
    /** Pole Master Kit'e bagli set sayisi (1..3). Yalnizca kit modelinde
     *  anlamli ve ZORUNLU: kac set takildigi sahada belli olur. Her set icin
     *  ayri bir cihaz kaydi acilir. */
    satellite_set_count?: number | null;
  }
): Promise<void> {
  const response = await apiFetch(`${API_BASE_URL}/devices`, {
    method: "POST",
    headers: authHeaders(token),
    body: JSON.stringify(payload)
  });
  if (!response.ok) throw await buildApiError(response, "Cihaz oluşturulamadı.");
}

export async function updateDevice(
  token: string,
  deviceCode: string,
  payload: {
    name?: string;
    serial_number?: string | null;
    description?: string | null;
    model?: string;
    installation_date?: string | null;
    gateway_code?: string | null;
    ip_address?: string;
    dnp3_outstation_port?: number;
    dnp3_address?: number;
    dnp3_extended?: Dnp3ExtendedSettings;
    poll_interval_sec?: number;
    timeout_ms?: number;
    retry_count?: number;
    latitude?: number;
    longitude?: number;
    iec104_common_address?: number | null;
    phase_master?: PhaseCode | null;
    phase_sat01?: PhaseCode | null;
    phase_sat02?: PhaseCode | null;
    phase_sat03?: PhaseCode | null;
    /** Set sayisini DUSURMEK veri siler (setin telemetrisi, alarmlari, ariza
     *  gecmisi ve hat yerlesimi). Arayuz once acik uyari gostermeli. */
    satellite_set_count?: number | null;
    /** Setin uydu atamasi (uc numara, 1..9). Kit genelinde BIJEKTIF olmali;
     *  backend cakismayi 422 ile reddeder. */
    subunit_satellites?: number[] | null;
  }
): Promise<void> {
  const response = await apiFetch(`${API_BASE_URL}/devices/${deviceCode}`, {
    method: "PATCH",
    headers: authHeaders(token),
    body: JSON.stringify(payload)
  });
  if (!response.ok) throw await buildApiError(response, "Cihaz güncellenemedi.");
}

export async function deleteDevice(token: string, deviceCode: string): Promise<void> {
  const response = await apiFetch(`${API_BASE_URL}/devices/${deviceCode}`, {
    method: "DELETE",
    headers: authHeaders(token)
  });
  if (!response.ok) throw await buildApiError(response, "Cihaz silinemedi.");
}


export async function fetchUsers(token: string): Promise<UserRead[]> {
  const response = await apiFetch(`${API_BASE_URL}/users`, {
    headers: authHeaders(token)
  });
  if (!response.ok) throw await buildApiError(response, "Kullanıcılar alınamadı.");
  return (await response.json()) as UserRead[];
}

export async function fetchMe(token: string): Promise<UserRead> {
  const response = await apiFetch(`${API_BASE_URL}/auth/me`, {
    headers: authHeaders(token)
  });
  if (!response.ok) throw await buildApiError(response, "Kullanıcı bilgisi alınamadı.");
  return (await response.json()) as UserRead;
}

export async function updateMyProfile(
  token: string,
  /** `phone_number` / `avatar_url`: `null` "dokunma" DEĞİL "temizle" demektir —
   *  backend her iki alanı da gövdeden aynen yazar. Bu yüzden çağıran taraf
   *  değiştirmediği alanları da mevcut değeriyle göndermelidir. */
  payload: {
    full_name: string;
    email: string;
    phone_number?: string | null;
    avatar_url?: string | null;
  }
): Promise<UserRead> {
  const response = await apiFetch(`${API_BASE_URL}/auth/me`, {
    method: "PATCH",
    headers: authHeaders(token),
    body: JSON.stringify(payload)
  });
  // `buildApiError` backend'in `detail`ini tasir. Once sabit bir metin
  // firlatiliyordu ve "bu e-posta baska bir kullanicida kayitli" (409) ya da
  // "gecerli bir e-posta degil" (422) gibi DUZELTILEBILIR sebepler ekrana
  // hic ulasmiyordu.
  if (!response.ok) throw await buildApiError(response, "Profil güncellenemedi.");
  return (await response.json()) as UserRead;
}

export async function changeMyPassword(
  token: string,
  payload: { current_password: string; new_password: string }
): Promise<void> {
  const response = await apiFetch(`${API_BASE_URL}/auth/me/change-password`, {
    method: "POST",
    headers: authHeaders(token),
    body: JSON.stringify(payload)
  });
  // AYNI GEREKCE: backend "Mevcut sifre yanlis" / "Yeni sifre eskisiyle ayni
  // olamaz" diyor, hiz siniri asilinca 429 donuyor. Sabit metin bunlarin
  // hepsini "Sifre degistirilemedi." e indirgiyor ve kullanici neyi
  // duzeltecegini bilemiyordu.
  if (!response.ok) throw await buildApiError(response, "Şifre değiştirilemedi.");
}

export async function updateMyLanguage(token: string, language: string): Promise<UserRead> {
  const response = await apiFetch(`${API_BASE_URL}/auth/me/language`, {
    method: "PUT",
    headers: authHeaders(token),
    body: JSON.stringify({ language })
  });
  if (!response.ok) throw await buildApiError(response, "Dil tercihi kaydedilemedi.");
  return (await response.json()) as UserRead;
}

export async function createUser(
  token: string,
  payload: {
    username: string;
    email: string;
    phone_number?: string | null;
    full_name: string;
    password: string;
    role: UserRole;
  }
): Promise<void> {
  const response = await apiFetch(`${API_BASE_URL}/users`, {
    method: "POST",
    headers: authHeaders(token),
    body: JSON.stringify(payload)
  });
  if (!response.ok) throw await buildApiError(response, "Kullanıcı oluşturulamadı.");
}

export async function deleteUser(token: string, userId: number): Promise<void> {
  const response = await apiFetch(`${API_BASE_URL}/users/${userId}`, {
    method: "DELETE",
    headers: authHeaders(token)
  });
  if (!response.ok) throw new Error("Kullanıcı silinemedi.");
}

export async function updateUser(
  token: string,
  userId: number,
  payload: { email: string; phone_number?: string | null; full_name: string; role: UserRole }
): Promise<void> {
  const response = await apiFetch(`${API_BASE_URL}/users/${userId}`, {
    method: "PATCH",
    headers: authHeaders(token),
    body: JSON.stringify(payload)
  });
  if (!response.ok) throw await buildApiError(response, "Kullanıcı güncellenemedi.");
}

export async function resetUserPassword(token: string, userId: number, newPassword: string): Promise<void> {
  const response = await apiFetch(`${API_BASE_URL}/users/${userId}/reset-password`, {
    method: "POST",
    headers: authHeaders(token),
    body: JSON.stringify({ new_password: newPassword })
  });
  if (!response.ok) throw await buildApiError(response, "Şifre sıfırlanamadı.");
}

export async function fetchAlarmEvents(token: string): Promise<AlarmEvent[]> {
  const response = await apiFetch(`${API_BASE_URL}/alarms/events`, {
    headers: authHeaders(token)
  });
  if (!response.ok) throw await buildApiError(response, "Alarmlar alınamadı.");
  return (await response.json()) as AlarmEvent[];
}

// ============= DB Yedekler =============

export async function fetchBackups(
  token: string
): Promise<import("./types").BackupJob[]> {
  const response = await apiFetch(`${API_BASE_URL}/admin/backups`, {
    headers: authHeaders(token)
  });
  if (!response.ok) throw await buildApiError(response, "Yedek listesi alınamadı.");
  return (await response.json()) as import("./types").BackupJob[];
}

export async function createManualBackup(
  token: string
): Promise<import("./types").BackupJob> {
  const response = await apiFetch(`${API_BASE_URL}/admin/backups`, {
    method: "POST",
    headers: authHeaders(token)
  });
  if (!response.ok) throw await buildApiError(response, "Yedek alınamadı.");
  return (await response.json()) as import("./types").BackupJob;
}

export async function deleteBackup(token: string, backupId: number): Promise<void> {
  const response = await apiFetch(`${API_BASE_URL}/admin/backups/${backupId}`, {
    method: "DELETE",
    headers: authHeaders(token)
  });
  if (!response.ok) throw await buildApiError(response, "Yedek silinemedi.");
}

export async function restoreBackup(token: string, backupId: number): Promise<void> {
  const response = await apiFetch(`${API_BASE_URL}/admin/backups/${backupId}/restore`, {
    method: "POST",
    headers: authHeaders(token)
  });
  if (!response.ok) throw await buildApiError(response, "Yedek geri yüklenemedi.");
}

/** Restore ilerleme durumu — backend restore_status_tracker.snapshot() */
export type RestoreStatus = {
  backup_id: number | null;
  filename: string | null;
  status: "idle" | "queued" | "validating" | "preparing" | "restoring" | "finalizing" | "done" | "failed";
  current_step: string;
  step_index: number;
  total_steps: number;
  progress_percent: number;
  message: string;
  error: string | null;
  started_by: string | null;
  started_at: number | null;
  finished_at: number | null;
  elapsed_sec: number | null;
  steps: string[];
  logs: Array<{ ts: string; step: string; level: "info" | "success" | "error"; message: string }>;
};

export async function getRestoreStatus(token: string): Promise<RestoreStatus> {
  const response = await apiFetch(`${API_BASE_URL}/admin/backups/restore/status`, {
    headers: authHeaders(token)
  });
  if (!response.ok) throw await buildApiError(response, "Restore durumu alınamadı.");
  return (await response.json()) as RestoreStatus;
}

/** Backend container'ini yeniden baslat. Yanit ~1.5sn sonra container exit
 * eder, Docker `restart: unless-stopped` policy'si ile yeniden baslar.
 * Toplam downtime ~5sn. Native kurulumda exit otomatik kalkis vermez. */
export async function restartBackend(token: string): Promise<void> {
  const response = await apiFetch(`${API_BASE_URL}/admin/system/restart`, {
    method: "POST",
    headers: authHeaders(token)
  });
  if (!response.ok) throw await buildApiError(response, "Sistem yeniden başlatılamadı.");
}

/** Yedek dosyasini indirme — backend FileResponse doner. */
export function backupDownloadUrl(backupId: number): string {
  return `${API_BASE_URL}/admin/backups/${backupId}/download`;
}

/** Daha onceden indirilmis .dump dosyasini yukle. Sunucuda BackupJob
 * (job_type='uploaded') olarak kaydedilir; sonra normal Restore akisi
 * uygulanabilir. */
export async function uploadBackupFile(
  token: string,
  file: File
): Promise<import("./types").BackupJob> {
  const fd = new FormData();
  fd.append("file", file);
  const response = await apiFetch(`${API_BASE_URL}/admin/backups/upload`, {
    method: "POST",
    headers: { Authorization: `Bearer ${token}` }, // Content-Type FormData ile auto
    body: fd
  });
  if (!response.ok) throw await buildApiError(response, "Yedek yüklenemedi.");
  return (await response.json()) as import("./types").BackupJob;
}

export async function downloadBackupFile(
  token: string,
  backupId: number,
  filename: string
): Promise<void> {
  // Authorization header'i ile fetch -> blob -> link click ile dosya indir.
  // Direkt <a href> kullanamiyoruz cunku endpoint'te token gerekiyor.
  const response = await apiFetch(backupDownloadUrl(backupId), {
    headers: { Authorization: `Bearer ${token}` }
  });
  if (!response.ok) throw await buildApiError(response, "Yedek indirilemedi.");
  const blob = await response.blob();
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
}

export async function fetchBackupSchedule(
  token: string
): Promise<import("./types").BackupSchedule> {
  const response = await apiFetch(`${API_BASE_URL}/admin/backups/schedule`, {
    headers: authHeaders(token)
  });
  if (!response.ok) throw await buildApiError(response, "Yedek programı alınamadı.");
  return (await response.json()) as import("./types").BackupSchedule;
}

export async function updateBackupSchedule(
  token: string,
  payload: Partial<import("./types").BackupSchedule>
): Promise<import("./types").BackupSchedule> {
  const response = await apiFetch(`${API_BASE_URL}/admin/backups/schedule`, {
    method: "PUT",
    headers: authHeaders(token),
    body: JSON.stringify(payload)
  });
  if (!response.ok) throw await buildApiError(response, "Yedek programı güncellenemedi.");
  return (await response.json()) as import("./types").BackupSchedule;
}

// ============= Hat Arızaları (Faults) — ticket sistemi =============

export async function fetchFaults(
  token: string,
  status: "active" | "all" | "open" | "closed" = "active"
): Promise<import("./types").FaultEvent[]> {
  const url = `${API_BASE_URL}/faults?status=${encodeURIComponent(status)}`;
  const response = await apiFetch(url, { headers: authHeaders(token) });
  if (!response.ok) throw await buildApiError(response, "Arızalar alınamadı.");
  return (await response.json()) as import("./types").FaultEvent[];
}

/**
 * Tek ariza — detay SAYFASI icin.
 *
 * NEDEN AYRI UC: detay artik bir sekme ve sekmeler localStorage'a
 * yaziliyor. Tarayici yenilendiginde sekme geri geliyor ama arizanin
 * listede olacaginin garantisi yok: kapanmis bir ariza aktif listede
 * DEGILDIR (gecmisten acilmisti). Sayfa yalnizca listeye guvenseydi
 * yenilemeden sonra bos acilirdi.
 */
export async function fetchFault(
  token: string,
  faultId: number
): Promise<import("./types").FaultEvent> {
  const response = await apiFetch(`${API_BASE_URL}/faults/${faultId}`, {
    headers: authHeaders(token)
  });
  if (!response.ok) throw await buildApiError(response, "Arıza kaydı alınamadı.");
  return (await response.json()) as import("./types").FaultEvent;
}

export async function fetchFaultStats(
  token: string
): Promise<import("./types").FaultStats> {
  const response = await apiFetch(`${API_BASE_URL}/faults/stats`, {
    headers: authHeaders(token)
  });
  if (!response.ok) throw await buildApiError(response, "Arıza istatistikleri alınamadı.");
  return (await response.json()) as import("./types").FaultStats;
}

export async function assignFault(
  token: string,
  faultId: number,
  username: string | null
): Promise<import("./types").FaultEvent> {
  const response = await apiFetch(`${API_BASE_URL}/faults/${faultId}/assign`, {
    method: "PATCH",
    headers: authHeaders(token),
    body: JSON.stringify({ assigned_to_username: username })
  });
  if (!response.ok) throw await buildApiError(response, "Arıza ataması yapılamadı.");
  return (await response.json()) as import("./types").FaultEvent;
}

export async function updateFaultStatus(
  token: string,
  faultId: number,
  newStatus: string
): Promise<import("./types").FaultEvent> {
  const response = await apiFetch(`${API_BASE_URL}/faults/${faultId}/status`, {
    method: "PATCH",
    headers: authHeaders(token),
    body: JSON.stringify({ status: newStatus })
  });
  if (!response.ok) throw await buildApiError(response, "Arıza durumu güncellenemedi.");
  return (await response.json()) as import("./types").FaultEvent;
}

export async function updateFaultNote(
  token: string,
  faultId: number,
  note: string | null
): Promise<import("./types").FaultEvent> {
  const response = await apiFetch(`${API_BASE_URL}/faults/${faultId}/note`, {
    method: "PATCH",
    headers: authHeaders(token),
    body: JSON.stringify({ note })
  });
  if (!response.ok) throw await buildApiError(response, "Arıza notu güncellenemedi.");
  return (await response.json()) as import("./types").FaultEvent;
}

/** Arıza analizi. TEK çağrı: ekran altı ayrı istek atsaydı hepsi aynı
 *  pencereyi ve aynı kapsamı tekrar hesaplardı, üstelik biri hata verince
 *  ekranın bir parçası sessizce boş kalırdı. */
export async function fetchFaultAnalytics(
  token: string,
  days: number
): Promise<import("./types").FaultAnalytics> {
  const response = await apiFetch(`${API_BASE_URL}/faults/analytics?days=${days}`, {
    headers: authHeaders(token)
  });
  if (!response.ok) throw await buildApiError(response, "Arıza analizi alınamadı.");
  return (await response.json()) as import("./types").FaultAnalytics;
}

/** Sistem sağlığı: alarm sıklığı + haberleşme kararlılığı. */
export async function fetchSystemHealth(
  token: string,
  days: number
): Promise<import("./types").SystemHealth> {
  const response = await apiFetch(`${API_BASE_URL}/faults/system-health?days=${days}`, {
    headers: authHeaders(token)
  });
  if (!response.ok) throw await buildApiError(response, "Sistem sağlığı alınamadı.");
  return (await response.json()) as import("./types").SystemHealth;
}

/** Cihaz sağlığı: batarya, sinyal, ısı haritası.
 *
 *  Varsayılan pencere ARIZA analizinden kısa (90 gün): ölçüm serisi saatlik
 *  kovada tutuluyor ve 365 günlük tarama gereksiz ağır. */
export async function fetchDeviceHealth(
  token: string,
  days: number
): Promise<import("./types").DeviceHealth> {
  const response = await apiFetch(`${API_BASE_URL}/faults/device-health?days=${days}`, {
    headers: authHeaders(token)
  });
  if (!response.ok) throw await buildApiError(response, "Cihaz sağlığı alınamadı.");
  return (await response.json()) as import("./types").DeviceHealth;
}

/** Arıza sebep kataloğu. Tek kaynak backend'dedir (`app/data/fault_causes.py`). */
/** Ünite → faz eşlemesi (kimlik doğrulamalı; public ayarlardan AYRI). */
export async function fetchPhaseMap(token: string): Promise<import("./types").PhaseMap> {
  const response = await apiFetch(`${API_BASE_URL}/project-settings/phase-map`, {
    headers: authHeaders(token)
  });
  if (!response.ok) throw await buildApiError(response, "Faz eşlemesi alınamadı.");
  return (await response.json()) as import("./types").PhaseMap;
}

export async function fetchFaultCauses(
  token: string
): Promise<import("./types").FaultCauseCatalog> {
  const response = await apiFetch(`${API_BASE_URL}/faults/causes`, {
    headers: authHeaders(token)
  });
  if (!response.ok) throw await buildApiError(response, "Arıza sebep listesi alınamadı.");
  return (await response.json()) as import("./types").FaultCauseCatalog;
}

/** Sahanın girdiği arıza sebebi.
 *
 *  DURUMDAN BAĞIMSIZ uç: ekip arızayı kapatırken sebebi bilmeyebilir ya da
 *  kapattıktan sonra öğrenebilir. `fault_kind`/`phase` GÖNDERİLMEZSE cihazdan
 *  türetilen değer korunur (boş = "dokunma", "sil" değil). */
export async function updateFaultCause(
  token: string,
  faultId: number,
  payload: {
    cause_code: string | null;
    cause_detail?: string | null;
    fault_kind?: string | null;
    phase?: string | null;
  }
): Promise<import("./types").FaultEvent> {
  const response = await apiFetch(`${API_BASE_URL}/faults/${faultId}/cause`, {
    method: "PATCH",
    headers: authHeaders(token),
    body: JSON.stringify(payload)
  });
  if (!response.ok) throw await buildApiError(response, "Arıza sebebi kaydedilemedi.");
  return (await response.json()) as import("./types").FaultEvent;
}

export async function fetchFaultComments(
  token: string,
  faultId: number
): Promise<import("./types").FaultComment[]> {
  const response = await apiFetch(`${API_BASE_URL}/faults/${faultId}/comments`, {
    headers: authHeaders(token)
  });
  if (!response.ok) throw await buildApiError(response, "Arıza yorumları alınamadı.");
  return (await response.json()) as import("./types").FaultComment[];
}

export async function addFaultComment(
  token: string,
  faultId: number,
  body: string
): Promise<import("./types").FaultComment> {
  const response = await apiFetch(`${API_BASE_URL}/faults/${faultId}/comments`, {
    method: "POST",
    headers: authHeaders(token),
    body: JSON.stringify({ body })
  });
  if (!response.ok) throw await buildApiError(response, "Yorum eklenemedi.");
  return (await response.json()) as import("./types").FaultComment;
}

// ============= Kullanıcı Bildirim Tercihleri =============

export async function fetchMyNotificationPrefs(
  token: string
): Promise<import("./types").UserNotificationPreferences> {
  const response = await apiFetch(`${API_BASE_URL}/me/notification-preferences`, {
    headers: authHeaders(token)
  });
  if (!response.ok) throw await buildApiError(response, "Bildirim tercihleri alınamadı.");
  return (await response.json()) as import("./types").UserNotificationPreferences;
}

export async function updateMyNotificationPrefs(
  token: string,
  payload: Partial<Omit<import("./types").UserNotificationPreferences, "user_id">>
): Promise<import("./types").UserNotificationPreferences> {
  const response = await apiFetch(`${API_BASE_URL}/me/notification-preferences`, {
    method: "PUT",
    headers: authHeaders(token),
    body: JSON.stringify(payload)
  });
  if (!response.ok) throw await buildApiError(response, "Bildirim tercihleri güncellenemedi.");
  return (await response.json()) as import("./types").UserNotificationPreferences;
}

/** Admin: bir kullanicinin bildirim tercihlerini cek (engineer/installer). */
export async function fetchUserNotificationPrefs(
  token: string,
  userId: number
): Promise<import("./types").UserNotificationPreferences> {
  const response = await apiFetch(
    `${API_BASE_URL}/users/${userId}/notification-preferences`,
    { headers: authHeaders(token) }
  );
  if (!response.ok) throw await buildApiError(response, "Bildirim tercihleri alınamadı.");
  return (await response.json()) as import("./types").UserNotificationPreferences;
}

/** Admin: bir kullanicinin bildirim tercihlerini guncelle. */
export async function updateUserNotificationPrefs(
  token: string,
  userId: number,
  payload: Partial<Omit<import("./types").UserNotificationPreferences, "user_id">>
): Promise<import("./types").UserNotificationPreferences> {
  const response = await apiFetch(
    `${API_BASE_URL}/users/${userId}/notification-preferences`,
    {
      method: "PUT",
      headers: authHeaders(token),
      body: JSON.stringify(payload)
    }
  );
  if (!response.ok) throw await buildApiError(response, "Bildirim tercihleri güncellenemedi.");
  return (await response.json()) as import("./types").UserNotificationPreferences;
}

export async function assignAlarm(token: string, alarmId: number, assignedTo: string | null): Promise<AlarmEvent> {
  const response = await apiFetch(`${API_BASE_URL}/alarms/events/${alarmId}/assign`, {
    method: "PATCH",
    headers: authHeaders(token),
    body: JSON.stringify({ assigned_to: assignedTo })
  });
  if (!response.ok) throw await buildApiError(response, "Alarm ataması yapılamadı.");
  return (await response.json()) as AlarmEvent;
}

export async function fetchAlarmComments(token: string, alarmId: number): Promise<AlarmComment[]> {
  const response = await apiFetch(`${API_BASE_URL}/alarms/events/${alarmId}/comments`, {
    headers: authHeaders(token)
  });
  if (!response.ok) throw await buildApiError(response, "Alarm yorumları alınamadı.");
  return (await response.json()) as AlarmComment[];
}

export async function addAlarmComment(token: string, alarmId: number, comment: string): Promise<AlarmComment> {
  const response = await apiFetch(`${API_BASE_URL}/alarms/events/${alarmId}/comments`, {
    method: "POST",
    headers: authHeaders(token),
    body: JSON.stringify({ comment })
  });
  if (!response.ok) throw await buildApiError(response, "Alarm yorumu kaydedilemedi.");
  return (await response.json()) as AlarmComment;
}

export async function acknowledgeAlarm(token: string, alarmId: number): Promise<AlarmEvent> {
  const response = await apiFetch(`${API_BASE_URL}/alarms/events/${alarmId}/ack`, {
    method: "PATCH",
    headers: authHeaders(token)
  });
  if (!response.ok) throw await buildApiError(response, "Alarm onaylanamadı.");
  return (await response.json()) as AlarmEvent;
}

export async function resetAlarm(token: string, alarmId: number): Promise<AlarmEvent> {
  const response = await apiFetch(`${API_BASE_URL}/alarms/events/${alarmId}/reset`, {
    method: "PATCH",
    headers: authHeaders(token)
  });
  if (!response.ok) throw await buildApiError(response, "Alarm resetlenemedi.");
  return (await response.json()) as AlarmEvent;
}

export async function deleteAlarm(token: string, alarmId: number): Promise<void> {
  const response = await apiFetch(`${API_BASE_URL}/alarms/events/${alarmId}`, {
    method: "DELETE",
    headers: authHeaders(token)
  });
  if (!response.ok) throw await buildApiError(response, "Alarm silinemedi.");
}

export async function acknowledgeAllAlarms(token: string): Promise<AlarmEvent[]> {
  const response = await apiFetch(`${API_BASE_URL}/alarms/events/ack-all`, {
    method: "POST",
    headers: authHeaders(token)
  });
  if (!response.ok) throw await buildApiError(response, "Tüm alarmlar onaylanamadı.");
  return (await response.json()) as AlarmEvent[];
}

export async function resetAllAlarms(token: string): Promise<AlarmEvent[]> {
  const response = await apiFetch(`${API_BASE_URL}/alarms/events/reset-all`, {
    method: "POST",
    headers: authHeaders(token)
  });
  if (!response.ok) throw await buildApiError(response, "Tüm alarmlar resetlenemedi.");
  return (await response.json()) as AlarmEvent[];
}

export async function fetchSystemEvents(token: string): Promise<SystemEvent[]> {
  const response = await apiFetch(`${API_BASE_URL}/events`, {
    headers: authHeaders(token)
  });
  if (!response.ok) throw await buildApiError(response, "Sistem olayları alınamadı.");
  return (await response.json()) as SystemEvent[];
}

export type SystemEventFilters = {
  category?: string;
  severity?: string;
  q?: string;
  /** Kullanıcı adı — kısmi eşleşir. */
  actorUsername?: string;
  deviceCode?: string;
  /** Durum grubu ILIKE desenleri (OR'lanır). */
  eventTypeLike?: string[];
  /** ISO 8601 (UTC) — new Date(...).toISOString() */
  dateFrom?: string;
  dateTo?: string;
  limit: number;
  offset: number;
};

/** Filtreleri query param'a çevirir. Liste ve export AYNI parametreleri
 *  kullansın diye ortak — aksi halde indirilen dosya ekrandakinden farklı
 *  bir kümeyi içerebilir. */
export function buildEventFilterParams(
  filters: Omit<SystemEventFilters, "limit" | "offset">,
): URLSearchParams {
  const params = new URLSearchParams();
  if (filters.category) params.set("category", filters.category);
  if (filters.severity) params.set("severity", filters.severity);
  if (filters.q?.trim()) params.set("q", filters.q.trim());
  if (filters.actorUsername?.trim()) params.set("actor_username", filters.actorUsername.trim());
  if (filters.deviceCode) params.set("device_code", filters.deviceCode);
  if (filters.eventTypeLike?.length) {
    params.set("event_type_like", filters.eventTypeLike.join(","));
  }
  if (filters.dateFrom) params.set("date_from", filters.dateFrom);
  if (filters.dateTo) params.set("date_to", filters.dateTo);
  return params;
}

/** Olaylar sayfası: sunucu taraflı filtre + sayfalama. Toplam kayıt sayısı
 *  backend'in X-Total-Count header'ından okunur. */
export async function fetchSystemEventsPaged(
  token: string,
  filters: SystemEventFilters,
): Promise<{ items: SystemEvent[]; total: number }> {
  const params = buildEventFilterParams(filters);
  params.set("limit", String(filters.limit));
  params.set("offset", String(filters.offset));
  const response = await apiFetch(`${API_BASE_URL}/events?${params.toString()}`, {
    headers: authHeaders(token)
  });
  if (!response.ok) throw await buildApiError(response, "Sistem olayları alınamadı.");
  const items = (await response.json()) as SystemEvent[];
  const rawTotal = Number(response.headers.get("X-Total-Count"));
  return { items, total: Number.isFinite(rawTotal) ? rawTotal : items.length };
}

export async function fetchGateways(token: string): Promise<Gateway[]> {
  const response = await apiFetch(`${API_BASE_URL}/gateways`, {
    headers: authHeaders(token)
  });
  if (!response.ok) throw await buildApiError(response, "Gateway listesi alınamadı.");
  return (await response.json()) as Gateway[];
}

/**
 * Gateway token'ini duz metin ceker — YALNIZCA INSTALLER.
 *
 * Token `GET /gateways` yanitindan cikarildi: o liste operator'a da acik ve
 * token telemetri gonderiminin TEK kimlik unsuru. Listede kaldigi surece
 * operator kendi alani disindaki cihazlar icin uydurma telemetri
 * gonderebiliyordu. Backend bu cagriyi denetim kaydina yazar.
 */
export async function fetchGatewayToken(
  token: string,
  gatewayCode: string
): Promise<string> {
  const response = await apiFetch(
    `${API_BASE_URL}/gateways/${encodeURIComponent(gatewayCode)}/token`,
    { headers: authHeaders(token) }
  );
  if (!response.ok) throw await buildApiError(response, "Gateway token'ı alınamadı.");
  const data = (await response.json()) as { code: string; token: string };
  return data.token;
}

export async function createGateway(
  token: string,
  payload: {
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
    control_host: string;
    control_port: number;
    initiating_port_count?: number;
  }
): Promise<void> {
  const response = await apiFetch(`${API_BASE_URL}/gateways`, {
    method: "POST",
    headers: authHeaders(token),
    body: JSON.stringify(payload)
  });
  if (!response.ok) throw await buildApiError(response, "Gateway oluşturulamadı.");
}

export async function updateGateway(
  token: string,
  gatewayCode: string,
  payload: {
    name?: string;
    host?: string;
    listen_port?: number;
    upstream_url?: string;
    batch_interval_sec?: number;
    max_devices?: number;
    device_code_prefix?: string | null;
    token?: string;
    publish_dnp3_quality?: boolean;
    is_active?: boolean;
    control_host?: string;
    control_port?: number;
    initiating_port_count?: number;
  }
): Promise<void> {
  const response = await apiFetch(`${API_BASE_URL}/gateways/${gatewayCode}`, {
    method: "PATCH",
    headers: authHeaders(token),
    body: JSON.stringify(payload)
  });
  if (!response.ok) throw await buildApiError(response, "Gateway güncellenemedi.");
}

export async function enableGateway(token: string, gatewayCode: string): Promise<void> {
  const response = await apiFetch(`${API_BASE_URL}/gateways/${gatewayCode}/enable`, {
    method: "POST",
    headers: authHeaders(token)
  });
  if (!response.ok) throw await buildApiError(response, "Gateway aktifleştirilemedi.");
}

export async function disableGateway(token: string, gatewayCode: string): Promise<void> {
  const response = await apiFetch(`${API_BASE_URL}/gateways/${gatewayCode}/disable`, {
    method: "POST",
    headers: authHeaders(token)
  });
  if (!response.ok) throw await buildApiError(response, "Gateway pasifleştirilemedi.");
}

export async function deleteGateway(token: string, gatewayCode: string): Promise<void> {
  const response = await apiFetch(`${API_BASE_URL}/gateways/${gatewayCode}`, {
    method: "DELETE",
    headers: authHeaders(token)
  });
  if (!response.ok) throw await buildApiError(response, "Gateway silinemedi.");
}

/** Gateway'e "tum cihazlara sorgu at" tetigi gonderir.
 *
 *  Backend gateways.refresh_nonce sayacini 1 artirir. Gateway en gec
 *  config_refresh_sec icinde tetigi yakalar (default 30sn) ve Class
 *  0+1+2+3 integrity poll yapar. Yanit anlik DEGILDIR — kullaniciya
 *  "istek gonderildi" geri bildirim verilir.
 */
export async function refreshGatewayAllDevices(
  token: string,
  gatewayCode: string
): Promise<Gateway> {
  const response = await apiFetch(
    `${API_BASE_URL}/gateways/${gatewayCode}/refresh-all`,
    {
      method: "POST",
      headers: authHeaders(token)
    }
  );
  if (!response.ok)
    throw await buildApiError(response, "Tüm cihazlara sorgu isteği gönderilemedi.");
  return (await response.json()) as Gateway;
}

/**
 * Cihaza DNP3 binary output (CROB) komutu KUYRUGA ALIR.
 *
 * `command` = SignalCatalog'daki binary_output sinyalinin slug'i (orn.
 * "trigger_config_download"). Gateway NAT arkasinda oldugundan komut anlik
 * gonderilemez: backend pending kaydi yazar, gateway config-poll'de (~30sn)
 * ceker ve CROB gonderir. Yanit {id, status:'pending'}; gercek sonuc
 * `fetchDeviceCommands` ile takip edilir.
 */
export async function sendDeviceCommand(
  token: string,
  deviceCode: string,
  command: string,
  opts?: { count?: number; onTimeMs?: number; offTimeMs?: number }
): Promise<DeviceCommandQueued> {
  const body: Record<string, unknown> = { command };
  if (opts?.count != null) body.count = opts.count;
  if (opts?.onTimeMs != null) body.on_time_ms = opts.onTimeMs;
  if (opts?.offTimeMs != null) body.off_time_ms = opts.offTimeMs;
  const response = await apiFetch(
    `${API_BASE_URL}/devices/${deviceCode}/command`,
    {
      method: "POST",
      headers: authHeaders(token),
      body: JSON.stringify(body)
    }
  );
  if (!response.ok)
    throw await buildApiError(response, "Cihaz komutu kuyruğa alınamadı.");
  return (await response.json()) as DeviceCommandQueued;
}

/** Cihazin son komutlari + durumlari (UI takip listesi). En yeni once. */
export async function fetchDeviceCommands(
  token: string,
  deviceCode: string,
  limit = 20
): Promise<DeviceCommandRow[]> {
  const response = await apiFetch(
    `${API_BASE_URL}/devices/${deviceCode}/commands?limit=${limit}`,
    { headers: authHeaders(token) }
  );
  if (!response.ok)
    throw await buildApiError(response, "Komut geçmişi alınamadı.");
  return (await response.json()) as DeviceCommandRow[];
}

/** Historian zaman serisi — cihaz detay grafikleri.
 *  bucket=raw -> ham nokta[]; 1m/1h -> aggregate nokta[]. */
export async function fetchDeviceHistory(
  token: string,
  deviceCode: string,
  signalKey: string,
  opts?: { bucket?: HistoryBucket; since?: string; until?: string; limit?: number }
): Promise<TelemetryHistoryPoint[] | TelemetryAggregatePoint[]> {
  const params = new URLSearchParams({ signal_key: signalKey });
  if (opts?.bucket) params.set("bucket", opts.bucket);
  if (opts?.since) params.set("since", opts.since);
  if (opts?.until) params.set("until", opts.until);
  if (opts?.limit != null) params.set("limit", String(opts.limit));
  const response = await apiFetch(
    `${API_BASE_URL}/devices/${deviceCode}/history?${params.toString()}`,
    { headers: authHeaders(token) }
  );
  if (!response.ok)
    throw await buildApiError(response, "Geçmiş veri alınamadı.");
  return (await response.json()) as
    | TelemetryHistoryPoint[]
    | TelemetryAggregatePoint[];
}

export type GatewayComposeDownloadOptions = {
  backendUrl: string;
  hostPort?: number;
  image?: string;
  appEnvironment?: "development" | "staging" | "production";
  fmt?: "compose" | "env";
  rabbitmqUrl?: string;
};

export async function downloadGatewayCompose(
  token: string,
  gatewayCode: string,
  opts: GatewayComposeDownloadOptions
): Promise<{ blob: Blob; filename: string }> {
  const params = new URLSearchParams();
  params.set("backend_url", opts.backendUrl);
  if (opts.hostPort != null && opts.hostPort > 0) params.set("host_port", String(opts.hostPort));
  if (opts.image) params.set("image", opts.image);
  if (opts.appEnvironment) params.set("app_environment", opts.appEnvironment);
  if (opts.fmt) params.set("fmt", opts.fmt);
  if (opts.rabbitmqUrl) params.set("rabbitmq_url", opts.rabbitmqUrl);

  const response = await apiFetch(
    `${API_BASE_URL}/gateways/${gatewayCode}/docker-compose?${params.toString()}`,
    { headers: authHeaders(token) }
  );
  if (!response.ok) throw await buildApiError(response, "Docker compose dosyası indirilemedi.");

  const headerName = response.headers.get("X-Filename");
  const disposition = response.headers.get("Content-Disposition") || "";
  const match = /filename="?([^";]+)"?/i.exec(disposition);
  const fallback = `e1-gw-${gatewayCode.toLowerCase()}.${opts.fmt === "env" ? "env" : "yml"}`;
  const filename = headerName || (match ? match[1] : fallback);
  const blob = await response.blob();
  return { blob, filename };
}

/** Host ajaninin (e1-gwd) durumu + bu cihazda kurulu gateway'ler.
 *
 *  Ajan kurulu degilse `available: false` doner — HATA DEGIL. UI "bu cihaza
 *  kur" secenegini kapali gosterir, "baska cihaza kur" akisi calismaya
 *  devam eder. Bu yuzden burada throw etmiyoruz. */
export async function fetchGatewayAgentStatus(token: string): Promise<GatewayAgentStatus> {
  const response = await apiFetch(`${API_BASE_URL}/gateways/local-agent`, {
    headers: authHeaders(token)
  });
  if (!response.ok) {
    return { available: false, reason: "unreachable", docker_available: false, gateways: [], pending: false };
  }
  return (await response.json()) as GatewayAgentStatus;
}

/** Gateway'i BU cihaza kur. 202 doner; kurulum asenkron ilerler ve
 *  `fetchGatewayAgentStatus` ile izlenir. */
export async function installGatewayLocally(
  token: string,
  gatewayCode: string,
  opts: { backendUrl?: string; hostPort?: number; image?: string } = {}
): Promise<{ request_id: string; code: string }> {
  const response = await apiFetch(`${API_BASE_URL}/gateways/${gatewayCode}/local-install`, {
    method: "POST",
    headers: { ...authHeaders(token), "Content-Type": "application/json" },
    body: JSON.stringify({
      backend_url: opts.backendUrl ?? null,
      host_port: opts.hostPort ?? null,
      image: opts.image ?? null,
      app_environment: "production"
    })
  });
  if (!response.ok) throw await buildApiError(response, "Gateway bu cihaza kurulamadı.");
  // request_id ile ajanin yazdigi status.json eslestirilir; boylece ONCEKI
  // bir kurulumun sonucunu yanlislikla "bizim sonucumuz" sanmayiz.
  return (await response.json()) as { request_id: string; code: string };
}

/** Bu cihazdaki gateway container'ini durdur ve kaldir. Gateway KAYDI silinmez. */
export async function removeGatewayLocally(token: string, gatewayCode: string): Promise<void> {
  const response = await apiFetch(`${API_BASE_URL}/gateways/${gatewayCode}/local-install`, {
    method: "DELETE",
    headers: authHeaders(token)
  });
  if (!response.ok) throw await buildApiError(response, "Gateway bu cihazdan kaldırılamadı.");
}

/** Gateway'i bu cihazda EN GUNCEL imaja yukselt. 202 doner; islem asenkron.
 *
 *  KESINTI: gateway yeniden baslarken ona bagli cihazlardan telemetri
 *  gelmez. Cagiran taraf kullaniciya bunu SORMALI. */
export async function updateGatewayLocally(
  token: string,
  gatewayCode: string
): Promise<{ request_id: string; code: string }> {
  const response = await apiFetch(`${API_BASE_URL}/gateways/${gatewayCode}/local-update`, {
    method: "POST",
    headers: authHeaders(token)
  });
  if (!response.ok) throw await buildApiError(response, "Gateway güncellenemedi.");
  return (await response.json()) as { request_id: string; code: string };
}

/** Gateway yasam dongusu uclari (durdur / baslat / yeniden baslat).
 *
 *  Ucu de 202 doner: ajan istegi asenkron isler, sonuc
 *  `fetchGatewayAgentStatus().last_apply` ile izlenir. Backend'e giden tek
 *  serbest deger gateway KODUDUR — komut metni gonderilmez. */
type YasamDongusuSonuc = { request_id: string; code: string };

async function gatewayLifecycle(
  token: string,
  gatewayCode: string,
  path: "local-stop" | "local-start" | "local-restart",
  hataMesaji: string
): Promise<YasamDongusuSonuc> {
  const response = await apiFetch(`${API_BASE_URL}/gateways/${gatewayCode}/${path}`, {
    method: "POST",
    headers: authHeaders(token)
  });
  if (!response.ok) throw await buildApiError(response, hataMesaji);
  return (await response.json()) as YasamDongusuSonuc;
}

/** Bu cihazdaki gateway container'ini DURDUR.
 *
 *  KALDIRMA DEGIL: container yerinde kalir, durumu `exited` olur — arayuz
 *  "durduruldu" ile "kurulu degil"i boylece ayirt eder.
 *  SONUC: bu gateway'e bagli cihazlardan telemetri gelmez. Cagiran taraf
 *  kullaniciya SORMALI. */
export async function stopGatewayLocally(
  token: string,
  gatewayCode: string
): Promise<YasamDongusuSonuc> {
  return gatewayLifecycle(token, gatewayCode, "local-stop", "Gateway durdurulamadı.");
}

/** Durdurulmus gateway container'ini yeniden baslat (imaj cekmeden). */
export async function startGatewayLocally(
  token: string,
  gatewayCode: string
): Promise<YasamDongusuSonuc> {
  return gatewayLifecycle(token, gatewayCode, "local-start", "Gateway başlatılamadı.");
}

/** Gateway container'ini ayni imajla yeniden baslat — kisa kesinti. */
export async function restartGatewayLocally(
  token: string,
  gatewayCode: string
): Promise<YasamDongusuSonuc> {
  return gatewayLifecycle(token, gatewayCode, "local-restart", "Gateway yeniden başlatılamadı.");
}

/** Gateway container loglarını ajandan İSTE (202, asenkron).
 *
 *  Backend Docker'a erişemez; ajan `docker compose logs` çıktısını paylaşılan
 *  dizine yazar. Sonuç birkaç saniye sonra `fetchGatewayLogs` ile okunur. */
export async function requestGatewayLogs(
  token: string,
  gatewayCode: string,
  tail = 300
): Promise<YasamDongusuSonuc> {
  const response = await apiFetch(
    `${API_BASE_URL}/gateways/${gatewayCode}/local-logs?tail=${tail}`,
    { method: "POST", headers: authHeaders(token) }
  );
  if (!response.ok) throw await buildApiError(response, "Gateway logu istenemedi.");
  return (await response.json()) as YasamDongusuSonuc;
}

/** Ajanın yazdığı SON log çıktısı. Henüz log alınmamışsa available=false. */
export async function fetchGatewayLogs(
  token: string,
  gatewayCode: string
): Promise<GatewayLogs> {
  const response = await apiFetch(`${API_BASE_URL}/gateways/${gatewayCode}/local-logs`, {
    headers: authHeaders(token)
  });
  if (!response.ok) throw await buildApiError(response, "Gateway logu alınamadı.");
  return (await response.json()) as GatewayLogs;
}

export async function fetchOutboundTargets(token: string): Promise<OutboundTarget[]> {
  const response = await apiFetch(`${API_BASE_URL}/outbound-targets`, {
    headers: authHeaders(token)
  });
  if (!response.ok) throw await buildApiError(response, "Outbound hedefleri alınamadı.");
  return (await response.json()) as OutboundTarget[];
}

/** MQTT-specific alanlar create + update payload'larina opsiyonel eklenir.
 *  Tum alanlar opsiyonel — REST/IEC104 target'larda gonderilmez. */
export type MqttPayloadFields = {
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
  mqtt_publish_interval_sec?: number;
  mqtt_topic_template?: string | null;
  mqtt_topic_prefix?: string;
  mqtt_customer_id?: string | null;
};

export async function createOutboundTarget(
  token: string,
  payload: {
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
    listen_host?: string | null;
    listen_port?: number | null;
    iec104_common_address?: number | null;
    iec104_allowed_peers?: string | null;
  } & MqttPayloadFields
): Promise<import("./types").OutboundTarget> {
  const response = await apiFetch(`${API_BASE_URL}/outbound-targets`, {
    method: "POST",
    headers: authHeaders(token),
    body: JSON.stringify(payload)
  });
  if (!response.ok) throw await buildApiError(response, "Outbound hedef oluşturulamadı.");
  return (await response.json()) as import("./types").OutboundTarget;
}

export async function updateOutboundTarget(
  token: string,
  targetId: number,
  payload: {
    endpoint?: string;
    topic?: string | null;
    event_filter?: "all" | "telemetry" | "alarm";
    auth_header?: string | null;
    auth_token?: string | null;
    qos?: number;
    retain?: boolean;
    is_active?: boolean;
    listen_host?: string | null;
    listen_port?: number | null;
    iec104_common_address?: number | null;
    iec104_allowed_peers?: string | null;
  } & MqttPayloadFields
): Promise<void> {
  const response = await apiFetch(`${API_BASE_URL}/outbound-targets/${targetId}`, {
    method: "PATCH",
    headers: authHeaders(token),
    body: JSON.stringify(payload)
  });
  if (!response.ok) throw await buildApiError(response, "Outbound hedef güncellenemedi.");
}

export async function deleteOutboundTarget(token: string, targetId: number): Promise<void> {
  const response = await apiFetch(`${API_BASE_URL}/outbound-targets/${targetId}`, {
    method: "DELETE",
    headers: authHeaders(token)
  });
  if (!response.ok) throw await buildApiError(response, "Outbound hedef silinemedi.");
}

/** REST/webhook hedefine ornek bir test olayi gonderir (n8n dogrulamasi icin). */
export async function testOutboundTarget(
  token: string,
  targetId: number
): Promise<{ ok: boolean; detail: string }> {
  const response = await apiFetch(`${API_BASE_URL}/outbound-targets/${targetId}/test`, {
    method: "POST",
    headers: authHeaders(token)
  });
  if (!response.ok) throw await buildApiError(response, "Webhook test gönderimi başarısız.");
  return response.json();
}

// ===========================================================================
// MQTT custom topic mappings — operator UI "Custom Topic Mapping" modal
// ===========================================================================

import type { OutboundTopicMapping } from "./types";

export async function fetchTopicMappings(
  token: string,
  targetId: number
): Promise<OutboundTopicMapping[]> {
  const response = await apiFetch(
    `${API_BASE_URL}/outbound-targets/${targetId}/topic-mappings`,
    { headers: authHeaders(token) }
  );
  if (!response.ok) throw await buildApiError(response, "Topic mapping listesi alınamadı.");
  return (await response.json()) as OutboundTopicMapping[];
}

export async function createTopicMapping(
  token: string,
  targetId: number,
  payload: {
    topic: string;
    device_codes: string;
    signal_keys: string;
    qos?: number | null;
    retain?: boolean | null;
    is_active: boolean;
  }
): Promise<OutboundTopicMapping> {
  const response = await apiFetch(
    `${API_BASE_URL}/outbound-targets/${targetId}/topic-mappings`,
    {
      method: "POST",
      headers: authHeaders(token),
      body: JSON.stringify(payload),
    }
  );
  if (!response.ok) throw await buildApiError(response, "Topic mapping eklenemedi.");
  return (await response.json()) as OutboundTopicMapping;
}

export async function updateTopicMapping(
  token: string,
  targetId: number,
  mappingId: number,
  payload: {
    topic?: string;
    device_codes?: string;
    signal_keys?: string;
    qos?: number | null;
    retain?: boolean | null;
    is_active?: boolean;
  }
): Promise<OutboundTopicMapping> {
  const response = await apiFetch(
    `${API_BASE_URL}/outbound-targets/${targetId}/topic-mappings/${mappingId}`,
    {
      method: "PATCH",
      headers: authHeaders(token),
      body: JSON.stringify(payload),
    }
  );
  if (!response.ok) throw await buildApiError(response, "Topic mapping güncellenemedi.");
  return (await response.json()) as OutboundTopicMapping;
}

export async function deleteTopicMapping(
  token: string,
  targetId: number,
  mappingId: number
): Promise<void> {
  const response = await apiFetch(
    `${API_BASE_URL}/outbound-targets/${targetId}/topic-mappings/${mappingId}`,
    { method: "DELETE", headers: authHeaders(token) }
  );
  if (!response.ok) throw await buildApiError(response, "Topic mapping silinemedi.");
}

// ---- MQTT TLS Cert upload ------------------------------------------------

export type MqttCertKind = "ca" | "cert" | "key";

export async function uploadMqttCert(
  token: string,
  targetId: number,
  kind: MqttCertKind,
  file: File
): Promise<import("./types").OutboundTarget> {
  const form = new FormData();
  form.append("file", file);
  // KRITIK: Multipart icin Content-Type'i set ETME — tarayici boundary ile
  // otomatik set eder. authHeaders('application/json' iceriyor) → multipart
  // body'yi JSON gibi parse ettiriyordu, FastAPI 'file required' donuyor.
  const headers: Record<string, string> = {};
  if (token) headers["Authorization"] = `Bearer ${token}`;
  const response = await apiFetch(
    `${API_BASE_URL}/outbound-targets/${targetId}/mqtt-cert/${kind}`,
    {
      method: "POST",
      headers,
      body: form,
    }
  );
  if (!response.ok) throw await buildApiError(response, "Sertifika yuklenemedi.");
  return (await response.json()) as import("./types").OutboundTarget;
}

export type OutboundRuntimeStatus = {
  connected?: boolean;
  last_publish_at?: string | null;
  last_success_at?: string | null;
  last_failure_at?: string | null;
  last_error?: string | null;
  sent_total?: number;
  failed_total?: number;
};

export type OutboundAutoTopic = {
  device_code: string;
  source: string;
  datatype: string;
  topic: string;
  is_custom: boolean;
};

export async function fetchOutboundRuntimeStatus(
  token: string
): Promise<Record<number, OutboundRuntimeStatus>> {
  const response = await apiFetch(`${API_BASE_URL}/outbound-targets/runtime-status`, {
    headers: authHeaders(token),
  });
  if (!response.ok) throw await buildApiError(response, "Durum bilgisi alinamadi.");
  return (await response.json()) as Record<number, OutboundRuntimeStatus>;
}

export async function fetchOutboundAutoTopics(
  token: string,
  targetId: number
): Promise<{ target_id: number; device_count: number; topics: OutboundAutoTopic[] }> {
  const response = await apiFetch(
    `${API_BASE_URL}/outbound-targets/${targetId}/auto-topics`,
    { headers: authHeaders(token) }
  );
  if (!response.ok) throw await buildApiError(response, "Otomatik topic listesi alinamadi.");
  return await response.json();
}

export async function deleteMqttCert(
  token: string,
  targetId: number,
  kind: MqttCertKind
): Promise<import("./types").OutboundTarget> {
  const response = await apiFetch(
    `${API_BASE_URL}/outbound-targets/${targetId}/mqtt-cert/${kind}`,
    { method: "DELETE", headers: authHeaders(token) }
  );
  if (!response.ok) throw await buildApiError(response, "Sertifika silinemedi.");
  return (await response.json()) as import("./types").OutboundTarget;
}

// ============================================================
// BULK NOTIFICATIONS (toplu bildirim — ops_manager / installer / engineer)
// ============================================================

export type BulkNotifyChannel = "web" | "email" | "sms" | "whatsapp";

export type BulkNotifyRequest = {
  subject: string;
  message: string;
  channels: BulkNotifyChannel[];
  user_ids?: number[];
  team_ids?: number[];
  send_to_all?: boolean;
};

export type BulkNotifyResult = {
  recipients_count: number;
  web_sent: number;
  email_sent: number;
  email_failed: number;
  sms_sent: number;
  sms_failed: number;
  whatsapp_sent: number;
  whatsapp_failed: number;
  skipped_no_email: number;
  skipped_no_phone: number;
  errors: string[];
};

export async function sendBulkNotification(
  token: string,
  payload: BulkNotifyRequest
): Promise<BulkNotifyResult> {
  const response = await apiFetch(`${API_BASE_URL}/bulk-notifications`, {
    method: "POST",
    headers: authHeaders(token),
    body: JSON.stringify(payload),
  });
  if (!response.ok) throw await buildApiError(response, "Toplu bildirim gönderilemedi.");
  return (await response.json()) as BulkNotifyResult;
}

// ---- Templates ----

export type BulkNotifyTemplateTarget = {
  user_ids: number[];
  team_ids: number[];
  send_to_all: boolean;
};

export type BulkNotifyTemplate = {
  id: number;
  name: string;
  subject: string;
  message: string;
  channels: BulkNotifyChannel[];
  target: BulkNotifyTemplateTarget | null;
  created_at: string;
  updated_at: string;
};

export type BulkNotifyTemplateCreate = {
  name: string;
  subject: string;
  message: string;
  channels: BulkNotifyChannel[];
  target?: BulkNotifyTemplateTarget | null;
};

export async function listBulkNotifyTemplates(
  token: string
): Promise<BulkNotifyTemplate[]> {
  const response = await apiFetch(`${API_BASE_URL}/bulk-notifications/templates`, {
    headers: authHeaders(token),
  });
  if (!response.ok) throw await buildApiError(response, "Şablonlar alınamadı.");
  return (await response.json()) as BulkNotifyTemplate[];
}

export async function createBulkNotifyTemplate(
  token: string,
  payload: BulkNotifyTemplateCreate
): Promise<BulkNotifyTemplate> {
  const response = await apiFetch(`${API_BASE_URL}/bulk-notifications/templates`, {
    method: "POST",
    headers: authHeaders(token),
    body: JSON.stringify(payload),
  });
  if (!response.ok) throw await buildApiError(response, "Şablon kaydedilemedi.");
  return (await response.json()) as BulkNotifyTemplate;
}

export async function deleteBulkNotifyTemplate(
  token: string,
  templateId: number
): Promise<void> {
  const response = await apiFetch(
    `${API_BASE_URL}/bulk-notifications/templates/${templateId}`,
    { method: "DELETE", headers: authHeaders(token) }
  );
  if (!response.ok) throw await buildApiError(response, "Şablon silinemedi.");
}

// ============================================================
// ACTIVE SESSIONS — installer-only oturum yonetimi
// ============================================================

export type ActiveSession = {
  jti: string;
  user_id: number;
  username: string;
  full_name: string;
  role: string;
  ip_address: string | null;
  user_agent: string | null;
  login_at: string;
  last_seen_at: string;
  /** JWT exp — 0015 oncesi kayitlarda null olabilir. */
  expires_at: string | null;
  is_self: boolean;
};

export async function fetchActiveSessions(token: string): Promise<ActiveSession[]> {
  const response = await apiFetch(`${API_BASE_URL}/admin/sessions`, {
    headers: authHeaders(token),
  });
  if (!response.ok) throw await buildApiError(response, "Aktif oturumlar alınamadı.");
  return (await response.json()) as ActiveSession[];
}

export async function revokeSession(token: string, jti: string): Promise<void> {
  const response = await apiFetch(
    `${API_BASE_URL}/admin/sessions/${encodeURIComponent(jti)}`,
    { method: "DELETE", headers: authHeaders(token) }
  );
  if (!response.ok) throw await buildApiError(response, "Oturum atılamadı.");
}

export async function fetchIec104Runtime(token: string, targetId: number) {
  const response = await apiFetch(`${API_BASE_URL}/outbound-targets/${targetId}/iec104-runtime`, {
    headers: authHeaders(token)
  });
  if (!response.ok) throw await buildApiError(response, "IEC 104 runtime alınamadı.");
  return (await response.json()) as import("./types").Iec104RuntimeStatus;
}

export async function fetchModbusRuntime(token: string, targetId: number) {
  const response = await apiFetch(`${API_BASE_URL}/outbound-targets/${targetId}/modbus-runtime`, {
    headers: authHeaders(token)
  });
  if (!response.ok) throw await buildApiError(response, "Modbus runtime alınamadı.");
  return (await response.json()) as import("./types").ModbusRuntimeStatus;
}

/** Bu IEC 104 hedefi icin Excel (.xlsx) sinyal listesini indirir.
 *  Cihaz × aktif IEC 104 sinyali kombinasyonlari + ASDU adresleri ile.
 *  Donus deger: indirilen point sayisi (X-Point-Count header'i; yoksa null). */
export async function downloadIec104PointsXlsx(
  token: string,
  targetId: number,
  suggestedName: string
): Promise<number | null> {
  const response = await apiFetch(`${API_BASE_URL}/outbound-targets/${targetId}/iec104-points.xlsx`, {
    headers: authHeaders(token)
  });
  if (!response.ok) throw await buildApiError(response, "Sinyal listesi indirilemedi.");
  const countHeader = response.headers.get("X-Point-Count");
  const count = countHeader !== null ? Number(countHeader) : null;
  const blob = await response.blob();
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = suggestedName;
  document.body.appendChild(a);
  a.click();
  a.remove();
  window.setTimeout(() => URL.revokeObjectURL(url), 1000);
  return count;
}

/** Cihazlara sirayla ASDU adresi ata (1, 2, 3...). */
export async function autoAssignDeviceCa(
  token: string,
  targetId: number,
  overwrite: boolean
): Promise<{ assigned: number; skipped: number; devices: { code: string; ca: number }[] }> {
  const response = await apiFetch(`${API_BASE_URL}/outbound-targets/${targetId}/auto-assign-device-ca`, {
    method: "POST",
    headers: authHeaders(token),
    body: JSON.stringify({ overwrite, start_at: 1 })
  });
  if (!response.ok) throw await buildApiError(response, "Otomatik atama başarısız.");
  return await response.json();
}

/** Bu IEC 104 hedefi icin point list CSV dosyasini browser'da indirir.
 *  Donus deger: indirilen point sayisi (X-Point-Count header'i; yoksa null). */
export async function downloadIec104PointsCsv(
  token: string,
  targetId: number,
  suggestedName: string
): Promise<number | null> {
  const response = await apiFetch(`${API_BASE_URL}/outbound-targets/${targetId}/iec104-points.csv`, {
    headers: authHeaders(token)
  });
  if (!response.ok) throw await buildApiError(response, "IEC 104 point list indirilemedi.");
  const countHeader = response.headers.get("X-Point-Count");
  const count = countHeader !== null ? Number(countHeader) : null;
  const blob = await response.blob();
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = suggestedName;
  // Bazi tarayicilar (Chromium nginx proxy arkasinda) DOM'a eklenmeyen
  // anchor'lar uzerinden gelen click event'i bastiriyor; explicit append
  // bunu garanti eder.
  document.body.appendChild(a);
  a.click();
  a.remove();
  // Blob'u biraz sonra revoke et — bazi tarayicilarda click async indirmeyi
  // tetikler, hemen revoke edersen "failed - network error" alirsin.
  window.setTimeout(() => URL.revokeObjectURL(url), 1000);
  return count;
}

/* ===== Modbus TCP outbound adres plani ===== */

/** Bir Modbus hedefinin adres plani (cihaz slotlari + tam adres tablosu).
 *
 *  Cagri sirasinda backend eksik cihaz slotlarini otomatik atar ve kalici
 *  yazar; mevcut slotlar korunur (adresler kaymaz). */
export async function fetchModbusPlan(
  token: string,
  targetId: number
): Promise<ModbusPlan> {
  const response = await apiFetch(
    `${API_BASE_URL}/outbound-targets/${targetId}/modbus-plan`,
    { headers: authHeaders(token) }
  );
  if (!response.ok) throw await buildApiError(response, "Modbus adres planı alınamadı.");
  return (await response.json()) as ModbusPlan;
}

/** Adres tablosunu CSV indir (Modicon gosterimi dahil). */
export async function downloadModbusPointsCsv(
  token: string,
  targetId: number,
  suggestedName: string
): Promise<number | null> {
  const response = await apiFetch(
    `${API_BASE_URL}/outbound-targets/${targetId}/modbus-points.csv`,
    { headers: authHeaders(token) }
  );
  if (!response.ok) throw await buildApiError(response, "Modbus adres listesi indirilemedi.");
  const countHeader = response.headers.get("X-Point-Count");
  const count = countHeader !== null ? Number(countHeader) : null;
  const blob = await response.blob();
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = suggestedName;
  document.body.appendChild(a);
  a.click();
  a.remove();
  window.setTimeout(() => URL.revokeObjectURL(url), 1000);
  return count;
}

// ----- Grid Topology (Bolge / Hat / Direk / Segment) -----
type _Region = import("./types").Region;
type _Line = import("./types").Line;
type _Pole = import("./types").Pole;
type _Segment = import("./types").LineSegment;
type _LineDetail = import("./types").LineDetail;

export type GridSnapshot = {
  regions: _Region[];
  lines: _Line[];
  poles: _Pole[];
  segments: _Segment[];
};

export async function fetchGridSnapshot(token: string): Promise<GridSnapshot> {
  const r = await apiFetch(`${API_BASE_URL}/grid/snapshot`, { headers: authHeaders(token) });
  if (!r.ok) throw await buildApiError(r, "Şebeke topolojisi alınamadı.");
  return (await r.json()) as GridSnapshot;
}

export async function fetchRegions(token: string): Promise<_Region[]> {
  const r = await apiFetch(`${API_BASE_URL}/grid/regions`, { headers: authHeaders(token) });
  if (!r.ok) throw await buildApiError(r, "Bölgeler alınamadı.");
  return (await r.json()) as _Region[];
}
export async function createRegion(token: string, payload: Partial<_Region>): Promise<_Region> {
  const r = await apiFetch(`${API_BASE_URL}/grid/regions`, {
    method: "POST", headers: authHeaders(token), body: JSON.stringify(payload)
  });
  if (!r.ok) throw await buildApiError(r, "Bölge oluşturulamadı.");
  return (await r.json()) as _Region;
}
export async function updateRegion(token: string, id: number, payload: Partial<_Region>): Promise<_Region> {
  const r = await apiFetch(`${API_BASE_URL}/grid/regions/${id}`, {
    method: "PATCH", headers: authHeaders(token), body: JSON.stringify(payload)
  });
  if (!r.ok) throw await buildApiError(r, "Bölge güncellenemedi.");
  return (await r.json()) as _Region;
}
export async function deleteRegion(token: string, id: number): Promise<void> {
  const r = await apiFetch(`${API_BASE_URL}/grid/regions/${id}`, {
    method: "DELETE", headers: authHeaders(token)
  });
  if (!r.ok) throw await buildApiError(r, "Bölge silinemedi.");
}

export async function fetchLines(token: string, regionId?: number): Promise<_Line[]> {
  const url = regionId ? `${API_BASE_URL}/grid/lines?region_id=${regionId}` : `${API_BASE_URL}/grid/lines`;
  const r = await apiFetch(url, { headers: authHeaders(token) });
  if (!r.ok) throw await buildApiError(r, "Hatlar alınamadı.");
  return (await r.json()) as _Line[];
}
export async function fetchLineDetail(token: string, lineId: number): Promise<_LineDetail> {
  const r = await apiFetch(`${API_BASE_URL}/grid/lines/${lineId}`, { headers: authHeaders(token) });
  if (!r.ok) throw await buildApiError(r, "Hat detayı alınamadı.");
  return (await r.json()) as _LineDetail;
}
export async function createLine(token: string, payload: Partial<_Line>): Promise<_Line> {
  const r = await apiFetch(`${API_BASE_URL}/grid/lines`, {
    method: "POST", headers: authHeaders(token), body: JSON.stringify(payload)
  });
  if (!r.ok) throw await buildApiError(r, "Hat oluşturulamadı.");
  return (await r.json()) as _Line;
}
export async function updateLine(token: string, id: number, payload: Partial<_Line>): Promise<_Line> {
  const r = await apiFetch(`${API_BASE_URL}/grid/lines/${id}`, {
    method: "PATCH", headers: authHeaders(token), body: JSON.stringify(payload)
  });
  if (!r.ok) throw await buildApiError(r, "Hat güncellenemedi.");
  return (await r.json()) as _Line;
}
export async function deleteLine(token: string, id: number): Promise<void> {
  const r = await apiFetch(`${API_BASE_URL}/grid/lines/${id}`, {
    method: "DELETE", headers: authHeaders(token)
  });
  if (!r.ok) throw await buildApiError(r, "Hat silinemedi.");
}

export async function createPole(token: string, payload: Partial<_Pole>): Promise<_Pole> {
  const r = await apiFetch(`${API_BASE_URL}/grid/poles`, {
    method: "POST", headers: authHeaders(token), body: JSON.stringify(payload)
  });
  if (!r.ok) throw await buildApiError(r, "Direk oluşturulamadı.");
  return (await r.json()) as _Pole;
}
export async function updatePole(token: string, id: number, payload: Partial<_Pole>): Promise<_Pole> {
  const r = await apiFetch(`${API_BASE_URL}/grid/poles/${id}`, {
    method: "PATCH", headers: authHeaders(token), body: JSON.stringify(payload)
  });
  if (!r.ok) throw await buildApiError(r, "Direk güncellenemedi.");
  return (await r.json()) as _Pole;
}
export async function deletePole(token: string, id: number): Promise<void> {
  const r = await apiFetch(`${API_BASE_URL}/grid/poles/${id}`, {
    method: "DELETE", headers: authHeaders(token)
  });
  if (!r.ok) throw await buildApiError(r, "Direk silinemedi.");
}

export async function reorderPoles(
  token: string,
  lineId: number,
  items: { pole_id: number; sequence_no: number }[]
): Promise<_Pole[]> {
  const r = await apiFetch(`${API_BASE_URL}/grid/lines/${lineId}/reorder-poles`, {
    method: "POST", headers: authHeaders(token),
    body: JSON.stringify({ line_id: lineId, items })
  });
  if (!r.ok) throw await buildApiError(r, "Direk sırası güncellenemedi.");
  return (await r.json()) as _Pole[];
}

export async function reversePoles(token: string, lineId: number): Promise<_Pole[]> {
  const r = await apiFetch(`${API_BASE_URL}/grid/lines/${lineId}/reverse-poles`, {
    method: "POST", headers: authHeaders(token)
  });
  if (!r.ok) throw await buildApiError(r, "Hat sırası tersine çevrilemedi.");
  return (await r.json()) as _Pole[];
}

export async function createSegment(token: string, payload: Partial<_Segment>): Promise<_Segment> {
  const r = await apiFetch(`${API_BASE_URL}/grid/segments`, {
    method: "POST", headers: authHeaders(token), body: JSON.stringify(payload)
  });
  if (!r.ok) throw await buildApiError(r, "Segment oluşturulamadı.");
  return (await r.json()) as _Segment;
}
export async function updateSegment(token: string, id: number, payload: Partial<_Segment>): Promise<_Segment> {
  const r = await apiFetch(`${API_BASE_URL}/grid/segments/${id}`, {
    method: "PATCH", headers: authHeaders(token), body: JSON.stringify(payload)
  });
  if (!r.ok) throw await buildApiError(r, "Segment güncellenemedi.");
  return (await r.json()) as _Segment;
}
export async function deleteSegment(token: string, id: number): Promise<void> {
  const r = await apiFetch(`${API_BASE_URL}/grid/segments/${id}`, {
    method: "DELETE", headers: authHeaders(token)
  });
  if (!r.ok) throw await buildApiError(r, "Segment silinemedi.");
}

// ----- Hat topolojisi Excel import (sablon + onizleme + commit) -----

export type GridImportRowError = { row: number; message: string };
export type GridImportPreview = {
  regions: number;
  lines: number;
  poles: number;
  devices: number;
  errors: GridImportRowError[];
};
export type GridImportResult = {
  regions_created: number;
  regions_updated: number;
  lines_created: number;
  lines_updated: number;
  poles_created: number;
  poles_updated: number;
  segments_created: number;
  skipped: number;
  errors: GridImportRowError[];
};

/** Bos hat topoloji import sablonunu (.xlsx) tarayicida indir. */
export async function downloadGridImportTemplate(token: string): Promise<void> {
  const response = await apiFetch(`${API_BASE_URL}/grid/import-template.xlsx`, {
    headers: authHeaders(token)
  });
  if (!response.ok) throw await buildApiError(response, "Şablon indirilemedi.");
  const disposition = response.headers.get("Content-Disposition") || "";
  const match = /filename="?([^";]+)"?/i.exec(disposition);
  const filename = match ? match[1] : "hat-topoloji-sablon.xlsx";
  const blob = await response.blob();
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a); // Chromium proxy arkasinda click yutulmasin
  a.click();
  a.remove();
  window.setTimeout(() => URL.revokeObjectURL(url), 1000);
}

/** Import onizleme (dry-run): dosyayi yolla, DB'ye yazmadan ozet + hatalar al. */
export async function previewGridImport(
  token: string,
  file: File
): Promise<GridImportPreview> {
  const form = new FormData();
  form.append("file", file);
  // KRITIK: Content-Type SET ETME — tarayici multipart boundary'yi kendi koyar.
  const headers: Record<string, string> = {};
  if (token) headers["Authorization"] = `Bearer ${token}`;
  const response = await apiFetch(`${API_BASE_URL}/grid/import/preview`, {
    method: "POST", headers, body: form
  });
  if (!response.ok) throw await buildApiError(response, "Önizleme başarısız.");
  return (await response.json()) as GridImportPreview;
}

/** Import'i uygula (commit). Onizleme ile ayni dosya. */
export async function commitGridImport(
  token: string,
  file: File
): Promise<GridImportResult> {
  const form = new FormData();
  form.append("file", file);
  const headers: Record<string, string> = {};
  if (token) headers["Authorization"] = `Bearer ${token}`;
  const response = await apiFetch(`${API_BASE_URL}/grid/import/commit`, {
    method: "POST", headers, body: form
  });
  if (!response.ok) throw await buildApiError(response, "İçe aktarma başarısız.");
  return (await response.json()) as GridImportResult;
}

/** Soru-cevap sihirbazi: tek hatti (bolge + hat + direk listesi) tek istekte
 *  kurar. Excel import ile ayni plan/apply yolundan gecer; hat mevcutsa
 *  direkler sonuna eklenir. */
export async function createGridWizardLine(
  token: string,
  payload: {
    region_code: string;
    region_name?: string | null;
    line_code: string;
    line_name?: string | null;
    poles: Array<{ latitude: number; longitude: number; name?: string | null; pole_type?: string | null }>;
    branch_line_code?: string;
    branch_pole_seq?: number;
  }
): Promise<GridImportResult> {
  const response = await apiFetch(`${API_BASE_URL}/grid/wizard-line`, {
    method: "POST",
    headers: { ...authHeaders(token), "Content-Type": "application/json" },
    body: JSON.stringify(payload)
  });
  if (!response.ok) throw await buildApiError(response, "Hat oluşturulamadı.");
  return (await response.json()) as GridImportResult;
}

// ----- Offline cihaz lisansi -----

/**
 * Arayuz kilidi icin minimal lisans durumu — TUM rollere acik.
 * `fetchLicenseStatus` ticari bilgi tasidigi icin engineer+installer ile
 * sinirli; operator/ops_manager kilidi bu uctan ogrenir.
 */
export async function fetchLicenseGate(token: string): Promise<LicenseGate | null> {
  const response = await apiFetch(`${API_BASE_URL}/license/gate`, {
    headers: authHeaders(token)
  });
  // 404 = backend bu ucu HENUZ tanimiyor (frontend backend'den yeni). Bu bir
  // lisans sorunu DEGIL; surum uyusmazligi. `null` donup cagirana eski yola
  // dusmesini soyluyoruz — aksi halde guncel arayuz + eski backend
  // kombinasyonu lisansi OLAN bir sistemi kilitler.
  if (response.status === 404) return null;
  if (!response.ok) throw await buildApiError(response, "Lisans durumu alınamadı.");
  return (await response.json()) as LicenseGate;
}

export async function fetchLicenseStatus(token: string): Promise<LicenseStatus> {
  const response = await apiFetch(`${API_BASE_URL}/license/status`, {
    headers: authHeaders(token)
  });
  if (!response.ok) throw await buildApiError(response, "Lisans durumu alınamadı.");
  return (await response.json()) as LicenseStatus;
}

export async function downloadLicenseRequest(token: string): Promise<void> {
  const response = await apiFetch(`${API_BASE_URL}/license/request`, {
    headers: authHeaders(token)
  });
  if (!response.ok) throw await buildApiError(response, "Lisans istek dosyası indirilemedi.");
  const disposition = response.headers.get("Content-Disposition") || "";
  const match = /filename="?([^";]+)"?/i.exec(disposition);
  const filename = match ? match[1] : "enerjione-license-request.licreq";
  const blob = await response.blob();
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  window.setTimeout(() => URL.revokeObjectURL(url), 1000);
}

export async function importLicense(token: string, file: File): Promise<LicenseStatus> {
  const form = new FormData();
  form.append("file", file);
  const headers: Record<string, string> = {};
  if (token) headers.Authorization = `Bearer ${token}`;
  const response = await apiFetch(`${API_BASE_URL}/license/import`, {
    method: "POST",
    headers,
    body: form
  });
  if (!response.ok) throw await buildApiError(response, "Lisans içe aktarılamadı.");
  return (await response.json()) as LicenseStatus;
}

// ----- Project Settings -----
// GET auth-siz kullanilabilsin; bazi yerlerde token vermeden de cagiriyoruz
// (login ekrani, header initial fetch). Backend GET /project-settings public.
// Govde `ProjectSettings` tipiyle oldugu gibi tasinir (alan secimi YOK), bu
// yuzden semaya yeni alan girince (or. toast_position/toast_muted) burada
// degisiklik gerekmez — `shared/types.ts` guncellenmesi yeter.
export async function fetchProjectSettings(): Promise<import("./types").ProjectSettings> {
  const response = await apiFetch(`${API_BASE_URL}/project-settings`);
  if (!response.ok) throw await buildApiError(response, "Proje ayarları alınamadı.");
  return (await response.json()) as import("./types").ProjectSettings;
}

export async function updateProjectSettings(
  token: string,
  payload: import("./types").ProjectSettingsSave
): Promise<import("./types").ProjectSettings> {
  const response = await apiFetch(`${API_BASE_URL}/project-settings`, {
    method: "PUT",
    headers: authHeaders(token),
    body: JSON.stringify(payload)
  });
  if (!response.ok) throw await buildApiError(response, "Proje ayarları kaydedilemedi.");
  return (await response.json()) as import("./types").ProjectSettings;
}

export async function fetchNotificationSettings(token: string): Promise<NotificationSettings> {
  const response = await apiFetch(`${API_BASE_URL}/notification-settings`, {
    headers: authHeaders(token)
  });
  if (!response.ok) throw await buildApiError(response, "Bildirim ayarları alınamadı.");
  return (await response.json()) as NotificationSettings;
}

export async function updateNotificationSettings(
  token: string,
  payload: NotificationSettings
): Promise<NotificationSettings> {
  const response = await apiFetch(`${API_BASE_URL}/notification-settings`, {
    method: "PUT",
    headers: authHeaders(token),
    body: JSON.stringify(payload)
  });
  if (!response.ok) throw await buildApiError(response, "Bildirim ayarları kaydedilemedi.");
  return (await response.json()) as NotificationSettings;
}

export async function testNotificationSmtp(
  token: string,
  payload: { recipient_email: string; subject?: string; message?: string }
): Promise<{ ok: boolean; detail: string }> {
  const response = await apiFetch(`${API_BASE_URL}/notification-settings/test-smtp`, {
    method: "POST",
    headers: authHeaders(token),
    body: JSON.stringify(payload)
  });
  if (!response.ok) throw await buildApiError(response, "SMTP test gönderimi başarısız.");
  return (await response.json()) as { ok: boolean; detail: string };
}

export async function testNotificationSms(
  token: string,
  payload: { recipient_phone: string; message?: string }
): Promise<{ ok: boolean; detail: string }> {
  const response = await apiFetch(`${API_BASE_URL}/notification-settings/test-sms`, {
    method: "POST",
    headers: authHeaders(token),
    body: JSON.stringify(payload)
  });
  if (!response.ok) throw await buildApiError(response, "SMS test gönderimi başarısız.");
  return (await response.json()) as { ok: boolean; detail: string };
}

export async function testNotificationTelegram(
  token: string,
  payload: { chat_id: string; message?: string }
): Promise<{ ok: boolean; detail: string }> {
  const response = await apiFetch(`${API_BASE_URL}/notification-settings/test-telegram`, {
    method: "POST",
    headers: authHeaders(token),
    body: JSON.stringify(payload)
  });
  if (!response.ok) throw await buildApiError(response, "Telegram test gönderimi başarısız.");
  return (await response.json()) as { ok: boolean; detail: string };
}

export async function fetchWhatsappWebStatus(
  token: string
): Promise<{ status: string; phone_number: string | null }> {
  const response = await apiFetch(`${API_BASE_URL}/notification-settings/whatsapp-web/status`, {
    headers: authHeaders(token)
  });
  if (!response.ok) throw await buildApiError(response, "WhatsApp Web durumu alınamadı.");
  return (await response.json()) as { status: string; phone_number: string | null };
}

export async function fetchWhatsappWebQr(token: string): Promise<{ qr: string | null }> {
  const response = await apiFetch(`${API_BASE_URL}/notification-settings/whatsapp-web/qr`, {
    headers: authHeaders(token)
  });
  if (!response.ok) throw await buildApiError(response, "WhatsApp Web QR kodu alınamadı.");
  return (await response.json()) as { qr: string | null };
}

export async function fetchWhatsappWebGroups(token: string): Promise<{ groups: WhatsappWebGroup[] }> {
  const response = await apiFetch(`${API_BASE_URL}/notification-settings/whatsapp-web/groups`, {
    headers: authHeaders(token)
  });
  if (!response.ok) throw await buildApiError(response, "WhatsApp grup listesi alınamadı.");
  return (await response.json()) as { groups: WhatsappWebGroup[] };
}

export async function testWhatsappWeb(
  token: string,
  payload: { recipient_phone: string; message?: string }
): Promise<{ ok: boolean; detail: string }> {
  const response = await apiFetch(`${API_BASE_URL}/notification-settings/whatsapp-web/test`, {
    method: "POST",
    headers: authHeaders(token),
    body: JSON.stringify(payload)
  });
  if (!response.ok) throw await buildApiError(response, "WhatsApp test gönderimi başarısız.");
  return (await response.json()) as { ok: boolean; detail: string };
}

export async function logoutWhatsappWeb(token: string): Promise<{ ok: boolean; detail: string }> {
  const response = await apiFetch(`${API_BASE_URL}/notification-settings/whatsapp-web/logout`, {
    method: "POST",
    headers: authHeaders(token)
  });
  if (!response.ok) throw await buildApiError(response, "WhatsApp bağlantısı kesilemedi.");
  return (await response.json()) as { ok: boolean; detail: string };
}

export type TelegramDiscoveredChat = {
  id: string;
  type: string;
  title: string;
};

/** Telegram getUpdates üzerinden bot'a yazılmış chat'leri listele.
 *  bot_token opsiyonel — verilmezse backend kayıtlı token'i kullanır. */
export async function discoverTelegramChats(
  token: string,
  payload?: { bot_token?: string }
): Promise<{ ok: boolean; detail: string; chats: TelegramDiscoveredChat[] }> {
  const response = await apiFetch(
    `${API_BASE_URL}/notification-settings/discover-telegram-chats`,
    {
      method: "POST",
      headers: authHeaders(token),
      body: JSON.stringify(payload ?? {})
    }
  );
  if (!response.ok) throw await buildApiError(response, "Telegram chat keşfi başarısız.");
  return (await response.json()) as {
    ok: boolean;
    detail: string;
    chats: TelegramDiscoveredChat[];
  };
}

// ----- Signal Catalog -----
export async function fetchSignals(token: string, model?: string): Promise<SignalCatalogRow[]> {
  const url = model
    ? `${API_BASE_URL}/signals?model=${encodeURIComponent(model)}`
    : `${API_BASE_URL}/signals`;
  const response = await apiFetch(url, { headers: authHeaders(token) });
  if (!response.ok) throw await buildApiError(response, "Sinyal listesi alınamadı.");
  return (await response.json()) as SignalCatalogRow[];
}

export async function createSignal(
  token: string,
  payload: Omit<SignalCatalogRow, "id">
): Promise<SignalCatalogRow> {
  const response = await apiFetch(`${API_BASE_URL}/signals`, {
    method: "POST",
    headers: authHeaders(token),
    body: JSON.stringify(payload)
  });
  if (!response.ok) throw await buildApiError(response, "Sinyal oluşturulamadı.");
  return (await response.json()) as SignalCatalogRow;
}

/** Sinyal anahtari MODEL BAZINDA tekildir; 192 anahtar birden fazla modelde
 *  var. `model` gonderilmezse backend belirsiz anahtarlari 409 ile reddeder —
 *  eskiden keyfi bir satiri (cogu zaman BASKA modelin satirini) duzenliyordu. */
export async function updateSignal(
  token: string,
  signalKey: string,
  payload: Partial<Omit<SignalCatalogRow, "id" | "key">>,
  model?: string
): Promise<SignalCatalogRow> {
  const qs = model ? `?model=${encodeURIComponent(model)}` : "";
  const response = await apiFetch(`${API_BASE_URL}/signals/${encodeURIComponent(signalKey)}${qs}`, {
    method: "PATCH",
    headers: authHeaders(token),
    body: JSON.stringify(payload)
  });
  if (!response.ok) throw await buildApiError(response, "Sinyal güncellenemedi.");
  return (await response.json()) as SignalCatalogRow;
}

/** Filtreye gore secilmis sinyallerin arsiv ayarini TEK istekte degistirir.
 *
 *  193 sinyali tek tek PATCH etmek 193 istek ve 193 denetim kaydi demekti.
 *  Backend ariza sinyallerini (binary / binary_output) `confirm_fault_signals`
 *  olmadan arsivden CIKARMAZ — bu kapi arayuzde degil sunucuda.
 */
export async function updateSignalsHistorian(
  token: string,
  payload: SignalHistorianBulkPayload
): Promise<SignalHistorianBulkResult> {
  const response = await apiFetch(`${API_BASE_URL}/signals/historian/bulk`, {
    method: "POST",
    headers: authHeaders(token),
    body: JSON.stringify(payload)
  });
  if (!response.ok) throw await buildApiError(response, "Arşiv ayarı güncellenemedi.");
  return (await response.json()) as SignalHistorianBulkResult;
}

export async function deleteSignal(
  token: string,
  signalKey: string,
  model?: string
): Promise<void> {
  const qs = model ? `?model=${encodeURIComponent(model)}` : "";
  const response = await apiFetch(`${API_BASE_URL}/signals/${encodeURIComponent(signalKey)}${qs}`, {
    method: "DELETE",
    headers: authHeaders(token)
  });
  if (!response.ok) throw await buildApiError(response, "Sinyal silinemedi.");
}

/** Canli sinyal degerleri (cihaz x sinyal).
 *
 *  Yanit cihaz x sinyal KARTEZYEN carpimidir (Horstmann SN2 = 193 sinyal) ve
 *  600 cihazda ~115.800 satira ulasir. Bu yuzden her ekran YALNIZCA
 *  GORDUGU kadarini istemeli:
 *
 *    `deviceCodes` — tek cihaz gosteren ekranlar (cihaz detayi, harita
 *                    secimi) bunu vermeli.
 *    `signalKeys`  — anasayfa gibi birkac sinyal okuyan ekranlar bunu
 *                    vermeli.
 *
 *  Ikisi de bos ise TUM kartezyen doner — yalnizca muhendislik "Canli
 *  Degerler" sayfasi icin dogru olan budur. */
export async function fetchSignalLiveValues(
  token: string,
  deviceCodes?: readonly string[],
  signalKeys?: readonly string[]
): Promise<SignalLiveRow[]> {
  const params = new URLSearchParams();
  if (deviceCodes && deviceCodes.length > 0) {
    params.set("device_codes", deviceCodes.join(","));
  }
  // Sinyal daraltmasi: anasayfa 193 sinyalden yalnizca DORDUNU okuyor
  // (uc batarya gerilimi + modem RSSI). Hepsini istemek 600 cihazda
  // 115.800 satirlik bir yanit demekti.
  if (signalKeys && signalKeys.length > 0) {
    params.set("signal_keys", signalKeys.join(","));
  }
  const query = params.toString() ? `?${params.toString()}` : "";
  const response = await apiFetch(`${API_BASE_URL}/signals/live${query}`, {
    headers: authHeaders(token)
  });
  if (!response.ok) throw await buildApiError(response, "Canlı sinyal değerleri alınamadı.");
  return (await response.json()) as SignalLiveRow[];
}

/** Sistem Durumu sayfasi icin: backend host'unun anlik CPU/RAM/disk/uptime
 *  metriklerini getirir. Sayfa kapali iken cagirilmaz; psutil hesaplamasi
 *  cok hizli oldugu icin polling 5-10 sn'de tekrarlanabilir.
 *
 *  ONEMLI: Polling icin session-expired event TETIKLENMEZ. Polling 401
 *  vermesi durumunda kullaniciyi login'e atmak yerine sessizce hata firlat —
 *  caller bir-iki tur dene, beklemeden gercek user action'larda (login,
 *  save) session expired akisi normal islesin. */
/** Calisan surum + guncelleme durumu (salt okunur; guncelleme uctan
 *  tetiklenemez — bilerek boyle). Lisans ve Sistem Durumu sayfalari kullanir. */
export async function fetchVersionInfo(
  token: string
): Promise<import("./types").VersionInfo> {
  const response = await apiFetch(`${API_BASE_URL}/system-status/version`, {
    headers: authHeaders(token)
  });
  if (!response.ok) {
    throw new Error(
      response.status === 401 ? "session_polling_401" : "Sürüm bilgisi alınamadı."
    );
  }
  return (await response.json()) as import("./types").VersionInfo;
}

export async function fetchHostStatus(token: string): Promise<HostStatus> {
  const response = await apiFetch(`${API_BASE_URL}/system-status/host`, {
    headers: authHeaders(token)
  });
  if (!response.ok) {
    throw new Error(
      response.status === 401
        ? "session_polling_401"
        : "Sunucu kaynak metrikleri alınamadı."
    );
  }
  return (await response.json()) as HostStatus;
}

/** Sistem Durumu sayfasi icin: backend'in bagli oldugu servislerin (DB,
 *  RabbitMQ, tag-engine vb.) saglik durumu. Her cagri tum servisleri
 *  paralel kontrol etmez (sirayla, kucuk timeout'la); pratikte 200ms altinda
 *  toplam suren bir cevap doner.
 *
 *  Polling icin session-expired event TETIKLENMEZ (fetchHostStatus ile ayni). */
export async function fetchServicesStatus(token: string): Promise<ServicesReport> {
  const response = await apiFetch(`${API_BASE_URL}/system-status/services`, {
    headers: authHeaders(token)
  });
  if (!response.ok) {
    throw new Error(
      response.status === 401
        ? "session_polling_401"
        : "Servis durumlari alınamadı."
    );
  }
  return (await response.json()) as ServicesReport;
}

/** Historian (telemetri arsivi) yapisal sagligi.
 *
 *  AYRI bir uc (servis listesine dahil degil): servis probe'lari ~1 sn toplam
 *  butceyle 10 sn'de bir kosuyor, historian introspection'i o butceye
 *  girmemeli. Backend 60 sn cache'liyor; bu yuzden sik pollemenin anlami yok.
 *
 *  Polling icin session-expired event TETIKLENMEZ (diger sistem-durumu
 *  uclariyla ayni davranis). */
export async function fetchHistorianStatus(
  token: string,
  opts?: { refresh?: boolean }
): Promise<HistorianStatus> {
  const query = opts?.refresh ? "?refresh=true" : "";
  const response = await apiFetch(`${API_BASE_URL}/system-status/historian${query}`, {
    headers: authHeaders(token)
  });
  if (!response.ok) {
    throw new Error(
      response.status === 401 ? "session_polling_401" : "Historian durumu alınamadı."
    );
  }
  return (await response.json()) as HistorianStatus;
}

/** Telemetri boru hatti — tuketici gelis hizina yetisiyor mu?
 *
 *  Historian ucundan FARKLI olarak bu ucun maliyeti sifira yakin: sayaclar
 *  surec-ici, DB'ye veya NATS'a ek sorgu YOK (backlog degeri JetStream mesaj
 *  metadata'sindan bedava geliyor). Bu yuzden sik pollenebilir.
 *
 *  Polling icin session-expired event TETIKLENMEZ (diger sistem-durumu
 *  uclariyla ayni davranis). */
export async function fetchTelemetryPipelineStatus(
  token: string
): Promise<TelemetryPipelineStatus> {
  const response = await apiFetch(`${API_BASE_URL}/system-status/telemetry-pipeline`, {
    headers: authHeaders(token)
  });
  if (!response.ok) {
    throw new Error(
      response.status === 401
        ? "session_polling_401"
        : "Telemetri boru hatti durumu alınamadı."
    );
  }
  return (await response.json()) as TelemetryPipelineStatus;
}

/* ===== Appliance ag ayarlari (mini PC IP/DNS) ===== */

/** Host'un ag durumu (arayuzler, WiFi AP, son uygulama sonucu).
 *
 *  Appliance modu kurulu degilse `available:false` + sebep doner — bu bir
 *  hata degildir (VPS kurulumunda normal), sayfa bunu bilgilendirme olarak
 *  gosterir. Polling icin session-expired event tetiklenmez. */
// ---- WiFi (appliance client baglantisi) ----------------------------------
// AP (erisim noktasi) buradan DEGISTIRILEMEZ; kurtarma yolu olarak korunur.
// Tek radyo oldugu icin bir aga baglanirken AP duser, baglanti kurulamazsa
// host ajani AP'yi otomatik geri acar.

export async function fetchWifiScan(
  token: string
): Promise<import("./types").WifiScanResult> {
  const response = await apiFetch(`${API_BASE_URL}/network/wifi/scan`, {
    headers: authHeaders(token)
  });
  if (!response.ok) throw await buildApiError(response, "WiFi taraması alınamadı.");
  return (await response.json()) as import("./types").WifiScanResult;
}

/** Yeni tarama tetikle.
 *
 *  `deep=true`: cihazin kendi agi ~15 sn KAPATILIR ve tam tarama yapilir.
 *  Tek radyo AP yayindayken kanal degistiremedigi icin normal tarama cogu
 *  surucude bos doner; bedeli, AP uzerinden bagli kullanicinin sayfasinin
 *  kisa sureligine acilmamasidir. Cagiran taraf bunu ONCEDEN soylemeli. */
export async function triggerWifiScan(token: string, deep = false): Promise<void> {
  const response = await apiFetch(`${API_BASE_URL}/network/wifi/scan`, {
    method: "POST",
    headers: { ...authHeaders(token), "Content-Type": "application/json" },
    body: JSON.stringify({ deep })
  });
  if (!response.ok) throw await buildApiError(response, "WiFi taraması başlatılamadı.");
}

export async function connectWifi(
  token: string,
  ssid: string,
  psk: string | null
): Promise<void> {
  const response = await apiFetch(`${API_BASE_URL}/network/wifi/connect`, {
    method: "POST",
    headers: { ...authHeaders(token), "Content-Type": "application/json" },
    // psk null ise sifresiz ag.
    body: JSON.stringify({ ssid, psk: psk || null })
  });
  if (!response.ok) throw await buildApiError(response, "WiFi ağına bağlanılamadı.");
}

/** Agi unutur. `ssid` verilirse yalnizca o ag bilinen aglar listesinden
 *  silinir (aktif baglanti ve AP etkilenmez); verilmezse aktif profil
 *  silinir ve cihazin kendi agi (AP) geri acilir. */
export async function forgetWifi(token: string, ssid?: string): Promise<void> {
  const response = await apiFetch(`${API_BASE_URL}/network/wifi/forget`, {
    method: "POST",
    headers: { ...authHeaders(token), "Content-Type": "application/json" },
    body: JSON.stringify(ssid ? { ssid } : {})
  });
  if (!response.ok) throw await buildApiError(response, "WiFi ağı unutulamadı.");
}

/** WiFi kartini ac/kapa (fiziksel onkosul).
 *
 *  Kapatma yalnizca IP almis bagli bir kablolu arayuz varken kabul edilir;
 *  aksi halde 409 doner ve `detail` metni dogrudan gosterilebilir. Donanim
 *  anahtari kapaliysa acma da 409 olur — yazilimla acilamaz. */
export async function setWifiRadio(token: string, enabled: boolean): Promise<void> {
  const response = await apiFetch(`${API_BASE_URL}/network/wifi/radio`, {
    method: "POST",
    headers: { ...authHeaders(token), "Content-Type": "application/json" },
    body: JSON.stringify({ enabled })
  });
  if (!response.ok) throw await buildApiError(response, "WiFi kartı açılıp kapatılamadı.");
}

/** WiFi kartinin gorevini sec (tek radyo — ikisi ayni anda olmaz).
 *
 *  "ap"     -> cihaz kendi agini yayinlar; ulasim garantisi, INTERNET YOK.
 *  "client" -> KAYITLI aga katilir, AP kapanir. Yeni bir ag secmek ayri akis:
 *              `connectWifi` (o da gorevi client yapar). */
export async function setWifiMode(token: string, mode: "ap" | "client"): Promise<void> {
  const response = await apiFetch(`${API_BASE_URL}/network/wifi/mode`, {
    method: "POST",
    headers: { ...authHeaders(token), "Content-Type": "application/json" },
    body: JSON.stringify({ mode })
  });
  if (!response.ok) throw await buildApiError(response, "WiFi kartının görevi değiştirilemedi.");
}

/** Internet durumunu SIMDI sina. Sonuc birkac saniye icinde
 *  `GET /network/status` -> `internet` alaninda gorunur. */
export async function checkInternet(token: string): Promise<void> {
  const response = await apiFetch(`${API_BASE_URL}/network/internet/check`, {
    method: "POST",
    headers: authHeaders(token)
  });
  if (!response.ok) throw await buildApiError(response, "İnternet durumu sınanamadı.");
}

export async function fetchNetworkStatus(token: string): Promise<NetworkStatus> {
  const response = await apiFetch(`${API_BASE_URL}/network/status`, {
    headers: authHeaders(token)
  });
  if (!response.ok) {
    throw new Error(
      response.status === 401 ? "session_polling_401" : "Ağ durumu alınamadı."
    );
  }
  return (await response.json()) as NetworkStatus;
}

/** IP/DNS ayarini uygula. 202 doner: istek host ajanina kuyruklandi.
 *  `reboot:true` ise cihaz birkac saniye icinde yeniden baslar ve bu, bu
 *  oturumdaki son basarili cagridir. */
export async function updateNetworkConfig(
  token: string,
  payload: NetworkConfigPayload
): Promise<NetworkConfigAccepted> {
  const response = await apiFetch(`${API_BASE_URL}/network/config`, {
    method: "PUT",
    headers: authHeaders(token),
    body: JSON.stringify(payload)
  });
  if (!response.ok) throw await buildApiError(response, "Ağ ayarı uygulanamadı.");
  return (await response.json()) as NetworkConfigAccepted;
}

/* ===== Uzaktan bakim izni (`/remote-access/*`) =====
   Backend host'a DOKUNMAZ: istegi request.json'a yazar, host'ta root ile
   calisan `e1-rad` ajani uygular (network/* ile ayni desen). Bu yuzden
   grant/revoke 202 doner ve sonuc bir sonraki durum okumasinda gorunur. */

/** Rol bu ucu goremiyor (backend: engineer/installer/ops_manager). Cagiran
 *  taraf bu sentinel'i gorunce yoklamayi KALICI olarak kapatir. */
export const REMOTE_ACCESS_FORBIDDEN = "remote_access_forbidden";

export async function fetchRemoteAccessStatus(
  token: string
): Promise<RemoteAccessStatus> {
  const response = await apiFetch(`${API_BASE_URL}/remote-access/status`, {
    headers: authHeaders(token)
  });
  if (!response.ok) {
    // DIKKAT: burada buildApiError KULLANILMAZ. O, 401'de session-expired
    // event'i yayiyor; bu uc periyodik yoklandigi icin gecici bir 401 tum
    // kullanicilari login ekranina dusururdu (bkz. fetchNetworkStatus).
    throw new Error(
      response.status === 401
        ? "session_polling_401"
        : response.status === 403
        ? REMOTE_ACCESS_FORBIDDEN
        : "Uzaktan bakım durumu alınamadı."
    );
  }
  return (await response.json()) as RemoteAccessStatus;
}

/** Sureli izin ver (acikken cagrilirsa sure TOPLANMAZ, yenisiyle DEGISIR).
 *  202: istek ajana kuyruklandi; kesin son tarihi ajan kendi saatinden verir. */
export async function grantRemoteAccess(
  token: string,
  payload: RemoteAccessGrantPayload
): Promise<RemoteAccessAccepted> {
  const response = await apiFetch(`${API_BASE_URL}/remote-access/grant`, {
    method: "POST",
    headers: authHeaders(token),
    body: JSON.stringify(payload)
  });
  if (!response.ok) {
    throw await buildApiError(response, "Uzaktan bakım izni verilemedi.");
  }
  return (await response.json()) as RemoteAccessAccepted;
}

/** Izni HEMEN kapat; sure dolmasini beklemez. Kapali olsa bile cagrilabilir
 *  (yakinsama garantisi + "kapat" niyetinin denetime yazilmasi). */
export async function revokeRemoteAccess(
  token: string,
  reason?: string | null
): Promise<RemoteAccessAccepted> {
  const response = await apiFetch(`${API_BASE_URL}/remote-access/revoke`, {
    method: "POST",
    headers: authHeaders(token),
    body: JSON.stringify({ reason: reason ?? null })
  });
  if (!response.ok) {
    throw await buildApiError(response, "Uzaktan bakım izni kapatılamadı.");
  }
  return (await response.json()) as RemoteAccessAccepted;
}

/** Denetim izi. Yeni DB modeli YOK: kaynak mevcut `system_events` tablosu,
 *  backend her izin/uzatma/kapatma icin `remote_access_*` olayi yaziyor.
 *  Ayri bir `/remote-access/history` ucu bilerek eklenmedi — filtre zaten
 *  `/events` uzerinde var, sayfa yalnizca kendi olay tiplerini suzer. */
export async function fetchRemoteAccessAudit(
  token: string,
  limit = 12
): Promise<SystemEvent[]> {
  const response = await apiFetch(`${API_BASE_URL}/events?category=security`, {
    headers: authHeaders(token)
  });
  if (!response.ok) {
    throw await buildApiError(response, "İşlem geçmişi alınamadı.");
  }
  const rows = (await response.json()) as SystemEvent[];
  return rows
    .filter((row) => row.event_type.startsWith("remote_access_"))
    .slice(0, limit);
}

/* ===== Guvenlik duvari (`/firewall/*`) =====
   Backend iptables CALISTIRMAZ: istenen yapilandirmayi request.json'a yazar,
   host'ta root ile calisan `e1-fwd` ajani uygular (remote-access/network ile
   ayni desen). Bu yuzden PUT 202 doner ve sonuc bir sonraki durum okumasinda
   gorunur. */

/** Rol bu ucu goremiyor (backend: engineer/installer/ops_manager). Cagiran
 *  taraf bu sentinel'i gorunce yoklamayi KALICI olarak kapatir. */
export const FIREWALL_FORBIDDEN = "firewall_forbidden";

export async function fetchFirewallStatus(token: string): Promise<FirewallStatus> {
  const response = await apiFetch(`${API_BASE_URL}/firewall/status`, {
    headers: authHeaders(token)
  });
  if (!response.ok) {
    // DIKKAT: buildApiError KULLANILMAZ — periyodik yoklanan uc; gecici bir
    // 401 session-expired event'i yayip herkesi login'e dusururdu
    // (bkz. fetchRemoteAccessStatus).
    throw new Error(
      response.status === 401
        ? "session_polling_401"
        : response.status === 403
        ? FIREWALL_FORBIDDEN
        : "Güvenlik duvarı durumu alınamadı."
    );
  }
  return (await response.json()) as FirewallStatus;
}

/** Yapilandirmanin TAMAMINI uygula (artimli degisiklik yok; ajan atomik
 *  degistirir). 202: istek ajana kuyruklandi. */
export async function updateFirewallConfig(
  token: string,
  payload: FirewallConfig
): Promise<FirewallConfigAccepted> {
  const response = await apiFetch(`${API_BASE_URL}/firewall/config`, {
    method: "PUT",
    headers: { ...authHeaders(token), "Content-Type": "application/json" },
    body: JSON.stringify(payload)
  });
  if (!response.ok) {
    throw await buildApiError(response, "Güvenlik duvarı ayarı uygulanamadı.");
  }
  return (await response.json()) as FirewallConfigAccepted;
}

/** Denetim izi. Yeni DB modeli YOK: kaynak `system_events`, backend her
 *  ac/kapat/kural degisikligi icin `firewall_*` olayi yaziyor. */
export async function fetchFirewallAudit(
  token: string,
  limit = 12
): Promise<SystemEvent[]> {
  const response = await apiFetch(`${API_BASE_URL}/events?category=security`, {
    headers: authHeaders(token)
  });
  if (!response.ok) {
    throw await buildApiError(response, "İşlem geçmişi alınamadı.");
  }
  const rows = (await response.json()) as SystemEvent[];
  return rows
    .filter((row) => row.event_type.startsWith("firewall_"))
    .slice(0, limit);
}

/* ===== Bildirim merkezi (zil ikonu) ===== */

export async function fetchNotifications(
  token: string,
  options?: { onlyUnread?: boolean; limit?: number }
): Promise<NotificationItem[]> {
  const params = new URLSearchParams();
  if (options?.onlyUnread) params.set("only_unread", "true");
  if (options?.limit != null) params.set("limit", String(options.limit));
  const qs = params.toString() ? `?${params.toString()}` : "";
  const response = await apiFetch(`${API_BASE_URL}/notifications${qs}`, {
    headers: authHeaders(token)
  });
  if (!response.ok) throw await buildApiError(response, "Bildirimler alınamadı.");
  return (await response.json()) as NotificationItem[];
}

export async function fetchNotificationUnreadCount(token: string): Promise<number> {
  const response = await apiFetch(`${API_BASE_URL}/notifications/unread-count`, {
    headers: authHeaders(token)
  });
  if (!response.ok) throw await buildApiError(response, "Okunmamış bildirim sayısı alınamadı.");
  const data = (await response.json()) as { unread: number };
  return Number(data.unread || 0);
}

export async function markNotificationRead(token: string, notificationId: number): Promise<void> {
  const response = await apiFetch(`${API_BASE_URL}/notifications/${notificationId}/read`, {
    method: "POST",
    headers: authHeaders(token)
  });
  if (!response.ok) throw await buildApiError(response, "Bildirim okundu işaretlenemedi.");
}

export async function markAllNotificationsRead(token: string): Promise<number> {
  const response = await apiFetch(`${API_BASE_URL}/notifications/read-all`, {
    method: "POST",
    headers: authHeaders(token)
  });
  if (!response.ok) throw await buildApiError(response, "Bildirimler okundu işaretlenemedi.");
  const data = (await response.json()) as { affected: number };
  return Number(data.affected || 0);
}

export async function resetSignalsToDefaults(token: string): Promise<{
  removed: number;
  inserted: number;
  updated: number;
  total_defaults: number;
}> {
  const response = await apiFetch(`${API_BASE_URL}/signals/reset-to-defaults`, {
    method: "POST",
    headers: authHeaders(token)
  });
  if (!response.ok) throw await buildApiError(response, "Sinyal kataloğu sıfırlanamadı.");
  return (await response.json()) as {
    removed: number;
    inserted: number;
    updated: number;
    total_defaults: number;
  };
}

// ----- Alarm Rules -----
export async function fetchAlarmRules(token: string): Promise<AlarmRuleRow[]> {
  const response = await apiFetch(`${API_BASE_URL}/alarm-rules`, { headers: authHeaders(token) });
  if (!response.ok) throw await buildApiError(response, "Alarm kuralları alınamadı.");
  return (await response.json()) as AlarmRuleRow[];
}

export async function createAlarmRule(
  token: string,
  payload: Omit<AlarmRuleRow, "id">
): Promise<AlarmRuleRow> {
  const response = await apiFetch(`${API_BASE_URL}/alarm-rules`, {
    method: "POST",
    headers: authHeaders(token),
    body: JSON.stringify(payload)
  });
  if (!response.ok) throw await buildApiError(response, "Alarm kuralı oluşturulamadı.");
  return (await response.json()) as AlarmRuleRow;
}

export async function updateAlarmRule(
  token: string,
  ruleId: number,
  payload: Partial<Omit<AlarmRuleRow, "id" | "signal_key">>
): Promise<AlarmRuleRow> {
  const response = await apiFetch(`${API_BASE_URL}/alarm-rules/${ruleId}`, {
    method: "PATCH",
    headers: authHeaders(token),
    body: JSON.stringify(payload)
  });
  if (!response.ok) throw await buildApiError(response, "Alarm kuralı güncellenemedi.");
  return (await response.json()) as AlarmRuleRow;
}

export async function deleteAlarmRule(token: string, ruleId: number): Promise<void> {
  const response = await apiFetch(`${API_BASE_URL}/alarm-rules/${ruleId}`, {
    method: "DELETE",
    headers: authHeaders(token)
  });
  if (!response.ok) throw await buildApiError(response, "Alarm kuralı silinemedi.");
}


// ----- Responsibility Areas -----
export async function fetchResponsibilityAreas(token: string): Promise<ResponsibilityAreaRow[]> {
  const response = await apiFetch(`${API_BASE_URL}/responsibility-areas`, { headers: authHeaders(token) });
  if (!response.ok) throw await buildApiError(response, "Sorumluluk alanları alınamadı.");
  return (await response.json()) as ResponsibilityAreaRow[];
}

export async function fetchResponsibilityAreaDetail(token: string, areaId: number): Promise<ResponsibilityAreaDetail> {
  const response = await apiFetch(`${API_BASE_URL}/responsibility-areas/${areaId}`, { headers: authHeaders(token) });
  if (!response.ok) throw await buildApiError(response, "Sorumluluk alanı detayı alınamadı.");
  return (await response.json()) as ResponsibilityAreaDetail;
}

export async function createResponsibilityArea(
  token: string,
  payload: { code: string; name: string; description?: string | null; is_active?: boolean }
): Promise<ResponsibilityAreaRow> {
  const response = await apiFetch(`${API_BASE_URL}/responsibility-areas`, {
    method: "POST",
    headers: authHeaders(token),
    body: JSON.stringify(payload)
  });
  if (!response.ok) throw await buildApiError(response, "Sorumluluk alanı oluşturulamadı.");
  return (await response.json()) as ResponsibilityAreaRow;
}

export async function updateResponsibilityArea(
  token: string,
  areaId: number,
  payload: { name?: string; description?: string | null; is_active?: boolean }
): Promise<ResponsibilityAreaRow> {
  const response = await apiFetch(`${API_BASE_URL}/responsibility-areas/${areaId}`, {
    method: "PATCH",
    headers: authHeaders(token),
    body: JSON.stringify(payload)
  });
  if (!response.ok) throw await buildApiError(response, "Sorumluluk alanı güncellenemedi.");
  return (await response.json()) as ResponsibilityAreaRow;
}

export async function deleteResponsibilityArea(token: string, areaId: number): Promise<void> {
  const response = await apiFetch(`${API_BASE_URL}/responsibility-areas/${areaId}`, {
    method: "DELETE",
    headers: authHeaders(token)
  });
  if (!response.ok) throw await buildApiError(response, "Sorumluluk alanı silinemedi.");
}

export async function addUserToArea(token: string, areaId: number, userId: number): Promise<void> {
  const response = await apiFetch(`${API_BASE_URL}/responsibility-areas/${areaId}/users/${userId}`, {
    method: "POST",
    headers: authHeaders(token)
  });
  if (!response.ok) throw await buildApiError(response, "Kullanıcı alana eklenemedi.");
}

export async function removeUserFromArea(token: string, areaId: number, userId: number): Promise<void> {
  const response = await apiFetch(`${API_BASE_URL}/responsibility-areas/${areaId}/users/${userId}`, {
    method: "DELETE",
    headers: authHeaders(token)
  });
  if (!response.ok) throw await buildApiError(response, "Kullanıcı alandan çıkarılamadı.");
}

export async function addDeviceToArea(token: string, areaId: number, deviceId: number): Promise<void> {
  const response = await apiFetch(`${API_BASE_URL}/responsibility-areas/${areaId}/devices/${deviceId}`, {
    method: "POST",
    headers: authHeaders(token)
  });
  if (!response.ok) throw await buildApiError(response, "Cihaz alana eklenemedi.");
}

export async function removeDeviceFromArea(token: string, areaId: number, deviceId: number): Promise<void> {
  const response = await apiFetch(`${API_BASE_URL}/responsibility-areas/${areaId}/devices/${deviceId}`, {
    method: "DELETE",
    headers: authHeaders(token)
  });
  if (!response.ok) throw await buildApiError(response, "Cihaz alandan çıkarılamadı.");
}

export async function addRegionToArea(token: string, areaId: number, regionId: number): Promise<void> {
  const response = await apiFetch(`${API_BASE_URL}/responsibility-areas/${areaId}/regions/${regionId}`, {
    method: "POST",
    headers: authHeaders(token)
  });
  if (!response.ok) throw await buildApiError(response, "Bölge alana eklenemedi.");
}

export async function removeRegionFromArea(token: string, areaId: number, regionId: number): Promise<void> {
  const response = await apiFetch(`${API_BASE_URL}/responsibility-areas/${areaId}/regions/${regionId}`, {
    method: "DELETE",
    headers: authHeaders(token)
  });
  if (!response.ok) throw await buildApiError(response, "Bölge alandan çıkarılamadı.");
}

export async function addLineToArea(token: string, areaId: number, lineId: number): Promise<void> {
  const response = await apiFetch(`${API_BASE_URL}/responsibility-areas/${areaId}/lines/${lineId}`, {
    method: "POST",
    headers: authHeaders(token)
  });
  if (!response.ok) throw await buildApiError(response, "Hat alana eklenemedi.");
}

export async function removeLineFromArea(token: string, areaId: number, lineId: number): Promise<void> {
  const response = await apiFetch(`${API_BASE_URL}/responsibility-areas/${areaId}/lines/${lineId}`, {
    method: "DELETE",
    headers: authHeaders(token)
  });
  if (!response.ok) throw await buildApiError(response, "Hat alandan çıkarılamadı.");
}

// ===== API Keys (Personal Access Token) =====
// Backend: app/api/api_keys.py — JWT korumali; her kullanici kendi tokenlarini
// yonetir, INSTALLER /all ile herkese erisir.

export async function fetchMyApiKeys(token: string): Promise<import("./types").ApiKey[]> {
  const response = await apiFetch(`${API_BASE_URL}/api-keys`, { headers: authHeaders(token) });
  if (!response.ok) throw await buildApiError(response, "API anahtarları alınamadı.");
  return (await response.json()) as import("./types").ApiKey[];
}

export async function createApiKey(
  token: string,
  payload: import("./types").ApiKeyCreatePayload
): Promise<import("./types").ApiKeyCreated> {
  const response = await apiFetch(`${API_BASE_URL}/api-keys`, {
    method: "POST",
    headers: authHeaders(token),
    body: JSON.stringify(payload)
  });
  if (!response.ok) throw await buildApiError(response, "API anahtarı oluşturulamadı.");
  return (await response.json()) as import("./types").ApiKeyCreated;
}

export async function revokeApiKey(token: string, keyId: number): Promise<void> {
  const response = await apiFetch(`${API_BASE_URL}/api-keys/${keyId}`, {
    method: "DELETE",
    headers: authHeaders(token)
  });
  if (!response.ok) throw await buildApiError(response, "API anahtarı iptal edilemedi.");
}

export async function setApiKeyActive(
  token: string,
  keyId: number,
  active: boolean
): Promise<import("./types").ApiKey> {
  const action = active ? "enable" : "disable";
  const response = await apiFetch(`${API_BASE_URL}/api-keys/${keyId}/${action}`, {
    method: "PATCH",
    headers: authHeaders(token)
  });
  if (!response.ok) throw await buildApiError(response, "API anahtarı güncellenemedi.");
  return (await response.json()) as import("./types").ApiKey;
}

/** Revoke edilmis API anahtarini listeden kalici sil. Aktif/pasif kayitlar
 *  reddedilir; once iptal edilmis olmali. */
export async function purgeApiKey(token: string, keyId: number): Promise<void> {
  const response = await apiFetch(`${API_BASE_URL}/api-keys/${keyId}/purge`, {
    method: "DELETE",
    headers: authHeaders(token)
  });
  if (!response.ok) throw await buildApiError(response, "API anahtarı silinemedi.");
}

// ---- Cevrimdisi harita karolari ------------------------------------------
export async function fetchMapTileSummary(
  token: string
): Promise<import("./types").MapTileSummary> {
  const response = await apiFetch(`${API_BASE_URL}/map-tiles/summary`, {
    headers: authHeaders(token)
  });
  if (!response.ok) throw await buildApiError(response, "Harita durumu alınamadı.");
  return (await response.json()) as import("./types").MapTileSummary;
}

export async function estimateMapArea(
  token: string,
  payload: import("./types").MapAreaRequest
): Promise<import("./types").MapEstimate> {
  const response = await apiFetch(`${API_BASE_URL}/map-tiles/estimate`, {
    method: "POST",
    headers: { ...authHeaders(token), "Content-Type": "application/json" },
    body: JSON.stringify(payload)
  });
  if (!response.ok) throw await buildApiError(response, "Alan hesaplanamadı.");
  return (await response.json()) as import("./types").MapEstimate;
}

export async function startMapPack(
  token: string,
  payload: import("./types").MapAreaRequest & { name: string }
): Promise<import("./types").MapPack> {
  const response = await apiFetch(`${API_BASE_URL}/map-tiles/packs`, {
    method: "POST",
    headers: { ...authHeaders(token), "Content-Type": "application/json" },
    body: JSON.stringify(payload)
  });
  if (!response.ok) throw await buildApiError(response, "İndirme başlatılamadı.");
  return (await response.json()) as import("./types").MapPack;
}

/** Yarim kalmis alani kuyruga geri koyar; diskteki karolar atlanir. */
export async function restartMapPack(
  token: string,
  packId: string
): Promise<import("./types").MapPack> {
  const response = await apiFetch(`${API_BASE_URL}/map-tiles/packs/${packId}/restart`, {
    method: "POST",
    headers: authHeaders(token)
  });
  if (!response.ok) throw await buildApiError(response, "İndirme sürdürülemedi.");
  return (await response.json()) as import("./types").MapPack;
}

export async function cancelMapPack(token: string, packId: string): Promise<void> {
  const response = await apiFetch(`${API_BASE_URL}/map-tiles/packs/${packId}/cancel`, {
    method: "POST",
    headers: authHeaders(token)
  });
  if (!response.ok) throw await buildApiError(response, "İndirme durdurulamadı.");
}

/** removeTiles=true ise alandaki karolar diskten de silinir. */
export async function deleteMapPack(
  token: string,
  packId: string,
  removeTiles: boolean
): Promise<void> {
  const response = await apiFetch(
    `${API_BASE_URL}/map-tiles/packs/${packId}?remove_tiles=${removeTiles ? "true" : "false"}`,
    { method: "DELETE", headers: authHeaders(token) }
  );
  if (!response.ok) throw await buildApiError(response, "Paket silinemedi.");
}

// ===== Cihaz yapilandirma dosyasi ========================================
// Backend: app/api/device_configs.py . Bu uclar yalnizca SURUM yaratir;
// dosyayi cihaza gondermek ayri bir adimdir (FTP + DNP3 komutu).

import type {
  BulkApplyResult,
  ConfigCurrent,
  ConfigDiffRow,
  ConfigRow,
  ConfigTemplate,
  ConfigVersion
} from "./types";

type ApiConfigRow = {
  cat_index: string; group: string; index: string; length: number;
  value_int: number | null; value_text: string | null; raw_hex: string;
  meaning: string | null; unit: string | null; description?: string | null;
};
type ApiConfigVersion = {
  id: number; device_id: number; version: number; source: ConfigVersion["source"];
  template_id: number | null; note: string | null; created_by: string | null;
  created_at: string; applied_at: string | null; size_bytes: number;
  checksum_valid: boolean | null; ftp_written?: boolean | null;
};

const mapConfigRow = (r: ApiConfigRow): ConfigRow => ({
  catIndex: r.cat_index, group: r.group, index: r.index, length: r.length,
  valueInt: r.value_int, valueText: r.value_text, rawHex: r.raw_hex,
  meaning: r.meaning, unit: r.unit, description: r.description ?? null
});

const mapConfigVersion = (v: ApiConfigVersion): ConfigVersion => ({
  id: v.id, deviceId: v.device_id, version: v.version, source: v.source,
  templateId: v.template_id, note: v.note, createdBy: v.created_by,
  createdAt: v.created_at, appliedAt: v.applied_at, sizeBytes: v.size_bytes,
  checksumValid: v.checksum_valid, ftpWritten: v.ftp_written ?? null
});

/** Guncel yapilandirma. Surum yoksa 404 doner — cagiran "henuz yok" olarak
 *  ele almali; bu bir HATA degil, olagan bir baslangic durumudur. */
export async function fetchDeviceConfig(token: string, deviceId: number): Promise<ConfigCurrent | null> {
  const response = await apiFetch(`${API_BASE_URL}/devices/${deviceId}/config`, {
    headers: authHeaders(token)
  });
  if (response.status === 404) return null;
  if (!response.ok) throw await buildApiError(response, "Yapılandırma alınamadı.");
  const data = (await response.json()) as {
    version: ApiConfigVersion; filename: string | null; rows: ApiConfigRow[];
    device_last_update?: string | null;
  };
  return {
    version: mapConfigVersion(data.version),
    filename: data.filename,
    rows: data.rows.map(mapConfigRow),
    deviceLastUpdate: data.device_last_update ?? null
  };
}

/** Sablonun ayar satirlari — sablon duzenleyicisi cihaz kartiyla ayni izgara. */
export async function fetchTemplateRows(token: string, templateId: number): Promise<ConfigRow[]> {
  const response = await apiFetch(`${API_BASE_URL}/config-templates/${templateId}/rows`, {
    headers: authHeaders(token)
  });
  if (!response.ok) throw await buildApiError(response, "Şablon okunamadı.");
  return ((await response.json()) as ApiConfigRow[]).map(mapConfigRow);
}

/** Sablon degerlerini YERINDE gunceller (gecmis cihaz surumleri etkilenmez). */
export async function updateConfigTemplate(
  token: string, templateId: number, changes: Record<string, number>
): Promise<void> {
  const response = await apiFetch(`${API_BASE_URL}/config-templates/${templateId}`, {
    method: "PATCH",
    headers: { ...authHeaders(token), "Content-Type": "application/json" },
    body: JSON.stringify({ changes })
  });
  if (!response.ok) throw await buildApiError(response, "Şablon kaydedilemedi.");
}

export async function fetchDeviceConfigVersions(token: string, deviceId: number): Promise<ConfigVersion[]> {
  const response = await apiFetch(`${API_BASE_URL}/devices/${deviceId}/config/versions`, {
    headers: authHeaders(token)
  });
  if (!response.ok) throw await buildApiError(response, "Sürüm geçmişi alınamadı.");
  return ((await response.json()) as ApiConfigVersion[]).map(mapConfigVersion);
}

export async function fetchDeviceConfigDiff(token: string, deviceId: number, version: number): Promise<ConfigDiffRow[]> {
  const response = await apiFetch(`${API_BASE_URL}/devices/${deviceId}/config/versions/${version}/diff`, {
    headers: authHeaders(token)
  });
  if (!response.ok) throw await buildApiError(response, "Fark hesaplanamadı.");
  const rows = (await response.json()) as {
    cat_index: string; meaning: string | null; before: string | null;
    after: string | null; before_int: number | null; after_int: number | null;
  }[];
  return rows.map((r) => ({
    catIndex: r.cat_index, meaning: r.meaning, before: r.before,
    after: r.after, beforeInt: r.before_int, afterInt: r.after_int
  }));
}

/** Degerleri degistirir — YENI surum yaratir, eskisini bozmaz. */
export async function updateDeviceConfig(
  token: string, deviceId: number, changes: Record<string, number>, note?: string
): Promise<ConfigVersion> {
  const response = await apiFetch(`${API_BASE_URL}/devices/${deviceId}/config`, {
    method: "PATCH",
    headers: { ...authHeaders(token), "Content-Type": "application/json" },
    body: JSON.stringify({ changes, note: note ?? null })
  });
  if (!response.ok) throw await buildApiError(response, "Yapılandırma kaydedilemedi.");
  return mapConfigVersion((await response.json()) as ApiConfigVersion);
}

export async function uploadDeviceConfig(token: string, deviceId: number, file: File): Promise<ConfigVersion> {
  const form = new FormData();
  form.append("file", file);
  // `authHeaders` KULLANILMAZ: icinde `Content-Type: application/json` var ve
  // bu, FormData'nin urettigi `multipart/...; boundary=...` basligini ezer.
  // Sunucu govdeyi JSON sanip ayristiramaz ve dosyayi "eksik alan" olarak
  // reddeder (FastAPI 422 "field required"). Ayni hata daha once baska bir
  // yukleme ucunda da yasandi; kalip diger FormData cagrilariyla ayni.
  const headers: Record<string, string> = {};
  if (token) headers["Authorization"] = `Bearer ${token}`;
  const response = await apiFetch(`${API_BASE_URL}/devices/${deviceId}/config/upload`, {
    method: "POST",
    headers,
    body: form
  });
  if (!response.ok) throw await buildApiError(response, "Dosya yüklenemedi.");
  return mapConfigVersion((await response.json()) as ApiConfigVersion);
}

export async function revertDeviceConfig(token: string, deviceId: number, version: number): Promise<ConfigVersion> {
  const response = await apiFetch(`${API_BASE_URL}/devices/${deviceId}/config/versions/${version}/revert`, {
    method: "POST",
    headers: authHeaders(token)
  });
  if (!response.ok) throw await buildApiError(response, "Geri alınamadı.");
  return mapConfigVersion((await response.json()) as ApiConfigVersion);
}

/** Ham dosyayi indirir. Ad `<seri>_Configuration.csv` — cihaz BASKA hicbir
 *  adi tanimadigi icin adi backend uretir, burada degistirilmez. */
export function deviceConfigDownloadUrl(deviceId: number, version?: number): string {
  const q = version === undefined ? "" : `?version=${version}`;
  return `${API_BASE_URL}/devices/${deviceId}/config/download${q}`;
}

export async function fetchConfigTemplates(token: string, deviceModel?: string): Promise<ConfigTemplate[]> {
  const q = deviceModel ? `?device_model=${encodeURIComponent(deviceModel)}` : "";
  const response = await apiFetch(`${API_BASE_URL}/config-templates${q}`, {
    headers: authHeaders(token)
  });
  if (!response.ok) throw await buildApiError(response, "Şablonlar alınamadı.");
  return ((await response.json()) as {
    id: number; name: string; device_model: string; source_filename: string | null;
    note: string | null; is_default: boolean; created_by: string | null;
    created_at: string; size_bytes: number;
  }[]).map((s) => ({
    id: s.id, name: s.name, deviceModel: s.device_model,
    sourceFilename: s.source_filename, note: s.note, isDefault: s.is_default,
    createdBy: s.created_by, createdAt: s.created_at, sizeBytes: s.size_bytes
  }));
}

export async function applyTemplateToDevices(
  token: string, templateId: number, deviceIds: number[], note?: string
): Promise<BulkApplyResult> {
  const response = await apiFetch(`${API_BASE_URL}/config-templates/bulk-apply`, {
    method: "POST",
    headers: { ...authHeaders(token), "Content-Type": "application/json" },
    body: JSON.stringify({ template_id: templateId, device_ids: deviceIds, note: note ?? null })
  });
  if (!response.ok) throw await buildApiError(response, "Toplu uygulama başarısız.");
  return (await response.json()) as BulkApplyResult;
}

/** FTP'de cihazin dosyasi varsa alir ve surume cevirir. 404 = dosya FTP'de
 *  yok (hata degil, "yukle ya da sablon kullan" durumu) — null doner. */
export async function pullDeviceConfigFromFtp(
  token: string, deviceId: number
): Promise<ConfigVersion | null> {
  const response = await apiFetch(`${API_BASE_URL}/devices/${deviceId}/config/pull-from-ftp`, {
    method: "POST",
    headers: authHeaders(token)
  });
  if (response.status === 404) return null;
  if (!response.ok) throw await buildApiError(response, "FTP sorgulanamadı.");
  return mapConfigVersion((await response.json()) as ApiConfigVersion);
}

/** Yapilandirmasi olmayan cihaz icin varsayilan sablondan ilk surumu uretir.
 *  Mevcut yapilandirmasi olan cihazda 409 doner (duzenlemeler ezilmez). */
export async function initDeviceConfigFromTemplate(
  token: string, deviceId: number
): Promise<ConfigVersion> {
  const response = await apiFetch(`${API_BASE_URL}/devices/${deviceId}/config/from-template`, {
    method: "POST",
    headers: authHeaders(token)
  });
  if (!response.ok) throw await buildApiError(response, "Şablondan oluşturulamadı.");
  return mapConfigVersion((await response.json()) as ApiConfigVersion);
}

/** Guncel surumu FTP'ye yazar + `config_update` DNP3 komutunu kuyruga alir.
 *  Eski akista dosyayi FTP'ye kullanici elle koymak zorundaydi; bu uc zinciri
 *  tek adimda kapatir. Basarida surumun `appliedAt` alani dolar. */
export async function applyDeviceConfig(token: string, deviceId: number): Promise<ConfigVersion> {
  const response = await apiFetch(`${API_BASE_URL}/devices/${deviceId}/config/apply`, {
    method: "POST",
    headers: authHeaders(token)
  });
  if (!response.ok) throw await buildApiError(response, "Yapılandırma cihaza gönderilemedi.");
  return mapConfigVersion((await response.json()) as ApiConfigVersion);
}

/** Sablon yukler. Backend `name`/`device_model` degerlerini QUERY parametresi
 *  olarak bekler; dosya multipart govdede gider. authHeaders KULLANILMAZ —
 *  icindeki Content-Type multipart boundary'sini ezer (bkz. uploadDeviceConfig). */
export async function uploadConfigTemplate(
  token: string,
  params: { name: string; deviceModel: string; isDefault: boolean; note?: string },
  file: File
): Promise<ConfigTemplate> {
  const q = new URLSearchParams({
    name: params.name,
    device_model: params.deviceModel,
    is_default: String(params.isDefault)
  });
  if (params.note) q.set("note", params.note);
  const form = new FormData();
  form.append("file", file);
  const headers: Record<string, string> = {};
  if (token) headers["Authorization"] = `Bearer ${token}`;
  const response = await apiFetch(`${API_BASE_URL}/config-templates?${q.toString()}`, {
    method: "POST",
    headers,
    body: form
  });
  if (!response.ok) throw await buildApiError(response, "Şablon yüklenemedi.");
  const s = (await response.json()) as {
    id: number; name: string; device_model: string; source_filename: string | null;
    note: string | null; is_default: boolean; created_by: string | null;
    created_at: string; size_bytes: number;
  };
  return {
    id: s.id, name: s.name, deviceModel: s.device_model,
    sourceFilename: s.source_filename, note: s.note, isDefault: s.is_default,
    createdBy: s.created_by, createdAt: s.created_at, sizeBytes: s.size_bytes
  };
}

export async function setDefaultConfigTemplate(token: string, templateId: number): Promise<void> {
  const response = await apiFetch(`${API_BASE_URL}/config-templates/${templateId}/default`, {
    method: "POST",
    headers: authHeaders(token)
  });
  if (!response.ok) throw await buildApiError(response, "Varsayılan yapılamadı.");
}

// ===== FTP sunucu ayarlari ================================================
// Backend: app/api/ftp_settings.py . Parola GET'te ACIK doner — cihazin FTP
// ekranina elle girilecegi icin kullanici okuyabilmeli.

import type {
  DeviceConfigSummary,
  FtpSettings,
  FtpSettingsUpdate,
  FtpStatus,
  FtpTestResult
} from "./types";

type ApiFtpSettings = {
  mode: FtpSettings["mode"];
  embedded_username: string; embedded_password: string | null; embedded_host: string | null;
  host: string | null; port: number; username: string;
  password: string | null; directory: string; poll_interval_sec: number;
  updated_by: string | null; updated_at: string | null;
};

const mapFtpSettings = (s: ApiFtpSettings): FtpSettings => ({
  mode: s.mode,
  embeddedUsername: s.embedded_username, embeddedPassword: s.embedded_password,
  embeddedHost: s.embedded_host,
  host: s.host, port: s.port, username: s.username,
  password: s.password, directory: s.directory, pollIntervalSec: s.poll_interval_sec,
  updatedBy: s.updated_by, updatedAt: s.updated_at
});

export async function fetchFtpSettings(token: string): Promise<FtpSettings> {
  const response = await apiFetch(`${API_BASE_URL}/ftp-settings`, {
    headers: authHeaders(token)
  });
  if (!response.ok) throw await buildApiError(response, "FTP ayarları alınamadı.");
  return mapFtpSettings((await response.json()) as ApiFtpSettings);
}

export async function updateFtpSettings(
  token: string, updates: FtpSettingsUpdate
): Promise<FtpSettings> {
  const body: Record<string, unknown> = {};
  if (updates.mode !== undefined) body.mode = updates.mode;
  if (updates.embeddedUsername !== undefined) body.embedded_username = updates.embeddedUsername;
  if (updates.embeddedPassword !== undefined) body.embedded_password = updates.embeddedPassword;
  if (updates.embeddedHost !== undefined) body.embedded_host = updates.embeddedHost;
  if (updates.host !== undefined) body.host = updates.host;
  if (updates.port !== undefined) body.port = updates.port;
  if (updates.username !== undefined) body.username = updates.username;
  if (updates.password !== undefined) body.password = updates.password;
  if (updates.directory !== undefined) body.directory = updates.directory;
  if (updates.pollIntervalSec !== undefined) body.poll_interval_sec = updates.pollIntervalSec;
  const response = await apiFetch(`${API_BASE_URL}/ftp-settings`, {
    method: "PUT",
    headers: { ...authHeaders(token), "Content-Type": "application/json" },
    body: JSON.stringify(body)
  });
  if (!response.ok) throw await buildApiError(response, "FTP ayarları kaydedilemedi.");
  return mapFtpSettings((await response.json()) as ApiFtpSettings);
}

/** Baglanti durumu: gomulu sunucunun sagligi + aktif kimlik + son hareketler. */
export async function fetchFtpStatus(token: string): Promise<FtpStatus> {
  const response = await apiFetch(`${API_BASE_URL}/ftp-settings/status`, {
    headers: authHeaders(token)
  });
  if (!response.ok) throw await buildApiError(response, "FTP durumu alınamadı.");
  const r = (await response.json()) as {
    mode: FtpStatus["mode"];
    server: { reachable: boolean; username: string | null; connections: number | null; synced: boolean | null } | null;
    events: {
      event_type: string; severity: string; message: string;
      device_code: string | null; created_at: string;
      metadata: Record<string, unknown> | null;
    }[];
  };
  return {
    mode: r.mode,
    server: r.server,
    events: r.events.map((e) => ({
      eventType: e.event_type, severity: e.severity, message: e.message,
      deviceCode: e.device_code, createdAt: e.created_at, metadata: e.metadata
    }))
  };
}

/** Cihaz basina guncel surum ozeti — cihaz listesi rozetleri (tek istek). */
export async function fetchDeviceConfigSummaries(token: string): Promise<DeviceConfigSummary[]> {
  const response = await apiFetch(`${API_BASE_URL}/device-configs/summary`, {
    headers: authHeaders(token)
  });
  if (!response.ok) throw await buildApiError(response, "Yapılandırma özeti alınamadı.");
  return ((await response.json()) as {
    device_id: number; version: number; source: DeviceConfigSummary["source"];
    created_at: string; applied_at: string | null;
  }[]).map((s) => ({
    deviceId: s.device_id, version: s.version, source: s.source,
    createdAt: s.created_at, appliedAt: s.applied_at
  }));
}

/** Okunabilir parola ONERISI — sunucu uretir ama KAYDETMEZ; kayit PUT ile. */
export async function generateFtpPassword(token: string): Promise<string> {
  const response = await apiFetch(`${API_BASE_URL}/ftp-settings/generate-password`, {
    method: "POST",
    headers: authHeaders(token)
  });
  if (!response.ok) throw await buildApiError(response, "Parola üretilemedi.");
  return ((await response.json()) as { password: string }).password;
}

/** KAYITLI ayarlarla harici sunucuya baglanti sinar. ok=false HTTP hatasi
 *  degildir — sinama calisti, sonuc olumsuz; ayrinti `detail` icinde. */
export async function testFtpSettings(token: string): Promise<FtpTestResult> {
  const response = await apiFetch(`${API_BASE_URL}/ftp-settings/test`, {
    method: "POST",
    headers: authHeaders(token)
  });
  if (!response.ok) throw await buildApiError(response, "Bağlantı sınanamadı.");
  const r = (await response.json()) as { ok: boolean; detail: string; config_files: number | null };
  return { ok: r.ok, detail: r.detail, configFiles: r.config_files };
}
