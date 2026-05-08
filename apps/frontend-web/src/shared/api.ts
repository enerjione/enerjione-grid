import type {
  AlarmComment,
  AlarmEvent,
  AlarmRuleRow,
  ApiDevice,
  AuthSession,
  DeviceModelOption,
  DeviceRow,
  Dnp3ExtendedSettings,
  Gateway,
  HostStatus,
  NotificationItem,
  ServicesReport,
  NotificationSettings,
  OutboundTarget,
  ResponsibilityAreaDetail,
  ResponsibilityAreaRow,
  SignalCatalogRow,
  SignalLiveRow,
  SystemEvent,
  UserRead,
  UserRole
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
  return {
    Authorization: `Bearer ${token}`,
    "Content-Type": "application/json"
  };
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
      return new Error(detail);
    }
    if (Array.isArray(detail) && detail.length > 0) {
      const first = detail[0];
      if (typeof first === "string" && first.trim()) {
        return new Error(first);
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
  const payload = JSON.stringify(session);
  if (remember) {
    localStorage.setItem(AUTH_STORAGE_KEY, payload);
  } else {
    sessionStorage.setItem(AUTH_STORAGE_KEY, payload);
  }
}

export function clearSession(): void {
  sessionStorage.removeItem(AUTH_STORAGE_KEY);
  localStorage.removeItem(AUTH_STORAGE_KEY);
}

export async function logout(token: string): Promise<void> {
  await fetch(`${API_BASE_URL}/auth/logout`, {
    method: "POST",
    headers: authHeaders(token)
  });
}

export async function login(username: string, password: string): Promise<AuthSession> {
  const response = await fetch(`${API_BASE_URL}/auth/login`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ username, password })
  });
  if (!response.ok) {
    throw new Error("Kullanıcı adı veya şifre hatalı.");
  }
  const data = (await response.json()) as LoginResponse;
  return {
    accessToken: data.access_token,
    username: data.username,
    role: data.role
  };
}

export async function fetchDevices(token: string, gatewayCode?: string): Promise<DeviceRow[]> {
  const endpoint = gatewayCode ? `${API_BASE_URL}/devices?gateway_code=${encodeURIComponent(gatewayCode)}` : `${API_BASE_URL}/devices`;
  const response = await fetch(endpoint, {
    headers: authHeaders(token)
  });
  if (!response.ok) throw await buildApiError(response, "Cihaz listesi alınamadı.");
  const devices = (await response.json()) as ApiDevice[];
  return devices.map((item) => ({
    id: item.id,
    code: item.code,
    name: item.name,
    description: item.description ?? undefined,
    model: item.model ?? "horstmann_sn_2_0",
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
    iec104CommonAddress: item.iec104_common_address ?? null
  }));
}

export async function fetchDeviceModels(token: string): Promise<DeviceModelOption[]> {
  const response = await fetch(`${API_BASE_URL}/device-models`, {
    headers: authHeaders(token)
  });
  if (!response.ok) throw await buildApiError(response, "Cihaz modeli listesi alınamadı.");
  return (await response.json()) as DeviceModelOption[];
}

export async function createDevice(
  token: string,
  payload: {
    code: string;
    name: string;
    description?: string | null;
    model: string;
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
  }
): Promise<void> {
  const response = await fetch(`${API_BASE_URL}/devices`, {
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
    description?: string | null;
    model?: string;
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
  }
): Promise<void> {
  const response = await fetch(`${API_BASE_URL}/devices/${deviceCode}`, {
    method: "PATCH",
    headers: authHeaders(token),
    body: JSON.stringify(payload)
  });
  if (!response.ok) throw await buildApiError(response, "Cihaz güncellenemedi.");
}

export async function deleteDevice(token: string, deviceCode: string): Promise<void> {
  const response = await fetch(`${API_BASE_URL}/devices/${deviceCode}`, {
    method: "DELETE",
    headers: authHeaders(token)
  });
  if (!response.ok) throw await buildApiError(response, "Cihaz silinemedi.");
}


export async function fetchUsers(token: string): Promise<UserRead[]> {
  const response = await fetch(`${API_BASE_URL}/users`, {
    headers: authHeaders(token)
  });
  if (!response.ok) throw await buildApiError(response, "Kullanıcılar alınamadı.");
  return (await response.json()) as UserRead[];
}

export async function fetchMe(token: string): Promise<UserRead> {
  const response = await fetch(`${API_BASE_URL}/auth/me`, {
    headers: authHeaders(token)
  });
  if (!response.ok) throw await buildApiError(response, "Kullanıcı bilgisi alınamadı.");
  return (await response.json()) as UserRead;
}

export async function updateMyProfile(
  token: string,
  payload: { full_name: string; email: string }
): Promise<UserRead> {
  const response = await fetch(`${API_BASE_URL}/auth/me`, {
    method: "PATCH",
    headers: authHeaders(token),
    body: JSON.stringify(payload)
  });
  if (!response.ok) throw new Error("Profil güncellenemedi.");
  return (await response.json()) as UserRead;
}

export async function changeMyPassword(
  token: string,
  payload: { current_password: string; new_password: string }
): Promise<void> {
  const response = await fetch(`${API_BASE_URL}/auth/me/change-password`, {
    method: "POST",
    headers: authHeaders(token),
    body: JSON.stringify(payload)
  });
  if (!response.ok) throw new Error("Şifre değiştirilemedi.");
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
  const response = await fetch(`${API_BASE_URL}/users`, {
    method: "POST",
    headers: authHeaders(token),
    body: JSON.stringify(payload)
  });
  if (!response.ok) throw await buildApiError(response, "Kullanıcı oluşturulamadı.");
}

export async function deleteUser(token: string, userId: number): Promise<void> {
  const response = await fetch(`${API_BASE_URL}/users/${userId}`, {
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
  const response = await fetch(`${API_BASE_URL}/users/${userId}`, {
    method: "PATCH",
    headers: authHeaders(token),
    body: JSON.stringify(payload)
  });
  if (!response.ok) throw await buildApiError(response, "Kullanıcı güncellenemedi.");
}

export async function resetUserPassword(token: string, userId: number, newPassword: string): Promise<void> {
  const response = await fetch(`${API_BASE_URL}/users/${userId}/reset-password`, {
    method: "POST",
    headers: authHeaders(token),
    body: JSON.stringify({ new_password: newPassword })
  });
  if (!response.ok) throw await buildApiError(response, "Şifre sıfırlanamadı.");
}

export async function fetchAlarmEvents(token: string): Promise<AlarmEvent[]> {
  const response = await fetch(`${API_BASE_URL}/alarms/events`, {
    headers: authHeaders(token)
  });
  if (!response.ok) throw await buildApiError(response, "Alarmlar alınamadı.");
  return (await response.json()) as AlarmEvent[];
}

// ============= DB Yedekler =============

export async function fetchBackups(
  token: string
): Promise<import("./types").BackupJob[]> {
  const response = await fetch(`${API_BASE_URL}/admin/backups`, {
    headers: authHeaders(token)
  });
  if (!response.ok) throw await buildApiError(response, "Yedek listesi alınamadı.");
  return (await response.json()) as import("./types").BackupJob[];
}

export async function createManualBackup(
  token: string
): Promise<import("./types").BackupJob> {
  const response = await fetch(`${API_BASE_URL}/admin/backups`, {
    method: "POST",
    headers: authHeaders(token)
  });
  if (!response.ok) throw await buildApiError(response, "Yedek alınamadı.");
  return (await response.json()) as import("./types").BackupJob;
}

export async function deleteBackup(token: string, backupId: number): Promise<void> {
  const response = await fetch(`${API_BASE_URL}/admin/backups/${backupId}`, {
    method: "DELETE",
    headers: authHeaders(token)
  });
  if (!response.ok) throw await buildApiError(response, "Yedek silinemedi.");
}

export async function restoreBackup(token: string, backupId: number): Promise<void> {
  const response = await fetch(`${API_BASE_URL}/admin/backups/${backupId}/restore`, {
    method: "POST",
    headers: authHeaders(token)
  });
  if (!response.ok) throw await buildApiError(response, "Yedek geri yüklenemedi.");
}

/** Yedek dosyasini indirme — backend FileResponse doner. */
export function backupDownloadUrl(backupId: number): string {
  return `${API_BASE_URL}/admin/backups/${backupId}/download`;
}

export async function downloadBackupFile(
  token: string,
  backupId: number,
  filename: string
): Promise<void> {
  // Authorization header'i ile fetch -> blob -> link click ile dosya indir.
  // Direkt <a href> kullanamiyoruz cunku endpoint'te token gerekiyor.
  const response = await fetch(backupDownloadUrl(backupId), {
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
  const response = await fetch(`${API_BASE_URL}/admin/backups/schedule`, {
    headers: authHeaders(token)
  });
  if (!response.ok) throw await buildApiError(response, "Yedek programı alınamadı.");
  return (await response.json()) as import("./types").BackupSchedule;
}

export async function updateBackupSchedule(
  token: string,
  payload: Partial<import("./types").BackupSchedule>
): Promise<import("./types").BackupSchedule> {
  const response = await fetch(`${API_BASE_URL}/admin/backups/schedule`, {
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
  const response = await fetch(url, { headers: authHeaders(token) });
  if (!response.ok) throw await buildApiError(response, "Arızalar alınamadı.");
  return (await response.json()) as import("./types").FaultEvent[];
}

export async function fetchFaultStats(
  token: string
): Promise<import("./types").FaultStats> {
  const response = await fetch(`${API_BASE_URL}/faults/stats`, {
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
  const response = await fetch(`${API_BASE_URL}/faults/${faultId}/assign`, {
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
  const response = await fetch(`${API_BASE_URL}/faults/${faultId}/status`, {
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
  const response = await fetch(`${API_BASE_URL}/faults/${faultId}/note`, {
    method: "PATCH",
    headers: authHeaders(token),
    body: JSON.stringify({ note })
  });
  if (!response.ok) throw await buildApiError(response, "Arıza notu güncellenemedi.");
  return (await response.json()) as import("./types").FaultEvent;
}

export async function fetchFaultComments(
  token: string,
  faultId: number
): Promise<import("./types").FaultComment[]> {
  const response = await fetch(`${API_BASE_URL}/faults/${faultId}/comments`, {
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
  const response = await fetch(`${API_BASE_URL}/faults/${faultId}/comments`, {
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
  const response = await fetch(`${API_BASE_URL}/me/notification-preferences`, {
    headers: authHeaders(token)
  });
  if (!response.ok) throw await buildApiError(response, "Bildirim tercihleri alınamadı.");
  return (await response.json()) as import("./types").UserNotificationPreferences;
}

export async function updateMyNotificationPrefs(
  token: string,
  payload: Partial<Omit<import("./types").UserNotificationPreferences, "user_id">>
): Promise<import("./types").UserNotificationPreferences> {
  const response = await fetch(`${API_BASE_URL}/me/notification-preferences`, {
    method: "PUT",
    headers: authHeaders(token),
    body: JSON.stringify(payload)
  });
  if (!response.ok) throw await buildApiError(response, "Bildirim tercihleri güncellenemedi.");
  return (await response.json()) as import("./types").UserNotificationPreferences;
}

export async function assignAlarm(token: string, alarmId: number, assignedTo: string | null): Promise<AlarmEvent> {
  const response = await fetch(`${API_BASE_URL}/alarms/events/${alarmId}/assign`, {
    method: "PATCH",
    headers: authHeaders(token),
    body: JSON.stringify({ assigned_to: assignedTo })
  });
  if (!response.ok) throw await buildApiError(response, "Alarm ataması yapılamadı.");
  return (await response.json()) as AlarmEvent;
}

export async function fetchAlarmComments(token: string, alarmId: number): Promise<AlarmComment[]> {
  const response = await fetch(`${API_BASE_URL}/alarms/events/${alarmId}/comments`, {
    headers: authHeaders(token)
  });
  if (!response.ok) throw await buildApiError(response, "Alarm yorumları alınamadı.");
  return (await response.json()) as AlarmComment[];
}

export async function addAlarmComment(token: string, alarmId: number, comment: string): Promise<AlarmComment> {
  const response = await fetch(`${API_BASE_URL}/alarms/events/${alarmId}/comments`, {
    method: "POST",
    headers: authHeaders(token),
    body: JSON.stringify({ comment })
  });
  if (!response.ok) throw await buildApiError(response, "Alarm yorumu kaydedilemedi.");
  return (await response.json()) as AlarmComment;
}

export async function acknowledgeAlarm(token: string, alarmId: number): Promise<AlarmEvent> {
  const response = await fetch(`${API_BASE_URL}/alarms/events/${alarmId}/ack`, {
    method: "PATCH",
    headers: authHeaders(token)
  });
  if (!response.ok) throw await buildApiError(response, "Alarm onaylanamadı.");
  return (await response.json()) as AlarmEvent;
}

export async function resetAlarm(token: string, alarmId: number): Promise<AlarmEvent> {
  const response = await fetch(`${API_BASE_URL}/alarms/events/${alarmId}/reset`, {
    method: "PATCH",
    headers: authHeaders(token)
  });
  if (!response.ok) throw await buildApiError(response, "Alarm resetlenemedi.");
  return (await response.json()) as AlarmEvent;
}

export async function deleteAlarm(token: string, alarmId: number): Promise<void> {
  const response = await fetch(`${API_BASE_URL}/alarms/events/${alarmId}`, {
    method: "DELETE",
    headers: authHeaders(token)
  });
  if (!response.ok) throw await buildApiError(response, "Alarm silinemedi.");
}

export async function acknowledgeAllAlarms(token: string): Promise<AlarmEvent[]> {
  const response = await fetch(`${API_BASE_URL}/alarms/events/ack-all`, {
    method: "POST",
    headers: authHeaders(token)
  });
  if (!response.ok) throw await buildApiError(response, "Tüm alarmlar onaylanamadı.");
  return (await response.json()) as AlarmEvent[];
}

export async function resetAllAlarms(token: string): Promise<AlarmEvent[]> {
  const response = await fetch(`${API_BASE_URL}/alarms/events/reset-all`, {
    method: "POST",
    headers: authHeaders(token)
  });
  if (!response.ok) throw await buildApiError(response, "Tüm alarmlar resetlenemedi.");
  return (await response.json()) as AlarmEvent[];
}

export async function fetchSystemEvents(token: string): Promise<SystemEvent[]> {
  const response = await fetch(`${API_BASE_URL}/events`, {
    headers: authHeaders(token)
  });
  if (!response.ok) throw await buildApiError(response, "Sistem olayları alınamadı.");
  return (await response.json()) as SystemEvent[];
}

export async function fetchGateways(token: string): Promise<Gateway[]> {
  const response = await fetch(`${API_BASE_URL}/gateways`, {
    headers: authHeaders(token)
  });
  if (!response.ok) throw await buildApiError(response, "Gateway listesi alınamadı.");
  return (await response.json()) as Gateway[];
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
  const response = await fetch(`${API_BASE_URL}/gateways`, {
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
    is_active?: boolean;
    control_host?: string;
    control_port?: number;
    initiating_port_count?: number;
  }
): Promise<void> {
  const response = await fetch(`${API_BASE_URL}/gateways/${gatewayCode}`, {
    method: "PATCH",
    headers: authHeaders(token),
    body: JSON.stringify(payload)
  });
  if (!response.ok) throw await buildApiError(response, "Gateway güncellenemedi.");
}

export async function enableGateway(token: string, gatewayCode: string): Promise<void> {
  const response = await fetch(`${API_BASE_URL}/gateways/${gatewayCode}/enable`, {
    method: "POST",
    headers: authHeaders(token)
  });
  if (!response.ok) throw await buildApiError(response, "Gateway aktifleştirilemedi.");
}

export async function disableGateway(token: string, gatewayCode: string): Promise<void> {
  const response = await fetch(`${API_BASE_URL}/gateways/${gatewayCode}/disable`, {
    method: "POST",
    headers: authHeaders(token)
  });
  if (!response.ok) throw await buildApiError(response, "Gateway pasifleştirilemedi.");
}

export async function deleteGateway(token: string, gatewayCode: string): Promise<void> {
  const response = await fetch(`${API_BASE_URL}/gateways/${gatewayCode}`, {
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
  const response = await fetch(
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

  const response = await fetch(
    `${API_BASE_URL}/gateways/${gatewayCode}/docker-compose?${params.toString()}`,
    { headers: authHeaders(token) }
  );
  if (!response.ok) throw await buildApiError(response, "Docker compose dosyası indirilemedi.");

  const headerName = response.headers.get("X-Filename");
  const disposition = response.headers.get("Content-Disposition") || "";
  const match = /filename="?([^";]+)"?/i.exec(disposition);
  const fallback = `hsl-gw-${gatewayCode.toLowerCase()}.${opts.fmt === "env" ? "env" : "yml"}`;
  const filename = headerName || (match ? match[1] : fallback);
  const blob = await response.blob();
  return { blob, filename };
}

export async function fetchOutboundTargets(token: string): Promise<OutboundTarget[]> {
  const response = await fetch(`${API_BASE_URL}/outbound-targets`, {
    headers: authHeaders(token)
  });
  if (!response.ok) throw await buildApiError(response, "Outbound hedefleri alınamadı.");
  return (await response.json()) as OutboundTarget[];
}

export async function createOutboundTarget(
  token: string,
  payload: {
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
    listen_host?: string | null;
    listen_port?: number | null;
    iec104_common_address?: number | null;
    iec104_allowed_peers?: string | null;
  }
): Promise<void> {
  const response = await fetch(`${API_BASE_URL}/outbound-targets`, {
    method: "POST",
    headers: authHeaders(token),
    body: JSON.stringify(payload)
  });
  if (!response.ok) throw await buildApiError(response, "Outbound hedef oluşturulamadı.");
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
  }
): Promise<void> {
  const response = await fetch(`${API_BASE_URL}/outbound-targets/${targetId}`, {
    method: "PATCH",
    headers: authHeaders(token),
    body: JSON.stringify(payload)
  });
  if (!response.ok) throw await buildApiError(response, "Outbound hedef güncellenemedi.");
}

export async function deleteOutboundTarget(token: string, targetId: number): Promise<void> {
  const response = await fetch(`${API_BASE_URL}/outbound-targets/${targetId}`, {
    method: "DELETE",
    headers: authHeaders(token)
  });
  if (!response.ok) throw await buildApiError(response, "Outbound hedef silinemedi.");
}

export async function fetchIec104Runtime(token: string, targetId: number) {
  const response = await fetch(`${API_BASE_URL}/outbound-targets/${targetId}/iec104-runtime`, {
    headers: authHeaders(token)
  });
  if (!response.ok) throw await buildApiError(response, "IEC 104 runtime alınamadı.");
  return (await response.json()) as import("./types").Iec104RuntimeStatus;
}

/** Bu IEC 104 hedefi icin Excel (.xlsx) sinyal listesini indirir.
 *  Cihaz × aktif IEC 104 sinyali kombinasyonlari + ASDU adresleri ile.
 *  Donus deger: indirilen point sayisi (X-Point-Count header'i; yoksa null). */
export async function downloadIec104PointsXlsx(
  token: string,
  targetId: number,
  suggestedName: string
): Promise<number | null> {
  const response = await fetch(`${API_BASE_URL}/outbound-targets/${targetId}/iec104-points.xlsx`, {
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
  const response = await fetch(`${API_BASE_URL}/outbound-targets/${targetId}/auto-assign-device-ca`, {
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
  const response = await fetch(`${API_BASE_URL}/outbound-targets/${targetId}/iec104-points.csv`, {
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
  const r = await fetch(`${API_BASE_URL}/grid/snapshot`, { headers: authHeaders(token) });
  if (!r.ok) throw await buildApiError(r, "Şebeke topolojisi alınamadı.");
  return (await r.json()) as GridSnapshot;
}

export async function fetchRegions(token: string): Promise<_Region[]> {
  const r = await fetch(`${API_BASE_URL}/grid/regions`, { headers: authHeaders(token) });
  if (!r.ok) throw await buildApiError(r, "Bölgeler alınamadı.");
  return (await r.json()) as _Region[];
}
export async function createRegion(token: string, payload: Partial<_Region>): Promise<_Region> {
  const r = await fetch(`${API_BASE_URL}/grid/regions`, {
    method: "POST", headers: authHeaders(token), body: JSON.stringify(payload)
  });
  if (!r.ok) throw await buildApiError(r, "Bölge oluşturulamadı.");
  return (await r.json()) as _Region;
}
export async function updateRegion(token: string, id: number, payload: Partial<_Region>): Promise<_Region> {
  const r = await fetch(`${API_BASE_URL}/grid/regions/${id}`, {
    method: "PATCH", headers: authHeaders(token), body: JSON.stringify(payload)
  });
  if (!r.ok) throw await buildApiError(r, "Bölge güncellenemedi.");
  return (await r.json()) as _Region;
}
export async function deleteRegion(token: string, id: number): Promise<void> {
  const r = await fetch(`${API_BASE_URL}/grid/regions/${id}`, {
    method: "DELETE", headers: authHeaders(token)
  });
  if (!r.ok) throw await buildApiError(r, "Bölge silinemedi.");
}

export async function fetchLines(token: string, regionId?: number): Promise<_Line[]> {
  const url = regionId ? `${API_BASE_URL}/grid/lines?region_id=${regionId}` : `${API_BASE_URL}/grid/lines`;
  const r = await fetch(url, { headers: authHeaders(token) });
  if (!r.ok) throw await buildApiError(r, "Hatlar alınamadı.");
  return (await r.json()) as _Line[];
}
export async function fetchLineDetail(token: string, lineId: number): Promise<_LineDetail> {
  const r = await fetch(`${API_BASE_URL}/grid/lines/${lineId}`, { headers: authHeaders(token) });
  if (!r.ok) throw await buildApiError(r, "Hat detayı alınamadı.");
  return (await r.json()) as _LineDetail;
}
export async function createLine(token: string, payload: Partial<_Line>): Promise<_Line> {
  const r = await fetch(`${API_BASE_URL}/grid/lines`, {
    method: "POST", headers: authHeaders(token), body: JSON.stringify(payload)
  });
  if (!r.ok) throw await buildApiError(r, "Hat oluşturulamadı.");
  return (await r.json()) as _Line;
}
export async function updateLine(token: string, id: number, payload: Partial<_Line>): Promise<_Line> {
  const r = await fetch(`${API_BASE_URL}/grid/lines/${id}`, {
    method: "PATCH", headers: authHeaders(token), body: JSON.stringify(payload)
  });
  if (!r.ok) throw await buildApiError(r, "Hat güncellenemedi.");
  return (await r.json()) as _Line;
}
export async function deleteLine(token: string, id: number): Promise<void> {
  const r = await fetch(`${API_BASE_URL}/grid/lines/${id}`, {
    method: "DELETE", headers: authHeaders(token)
  });
  if (!r.ok) throw await buildApiError(r, "Hat silinemedi.");
}

export async function createPole(token: string, payload: Partial<_Pole>): Promise<_Pole> {
  const r = await fetch(`${API_BASE_URL}/grid/poles`, {
    method: "POST", headers: authHeaders(token), body: JSON.stringify(payload)
  });
  if (!r.ok) throw await buildApiError(r, "Direk oluşturulamadı.");
  return (await r.json()) as _Pole;
}
export async function updatePole(token: string, id: number, payload: Partial<_Pole>): Promise<_Pole> {
  const r = await fetch(`${API_BASE_URL}/grid/poles/${id}`, {
    method: "PATCH", headers: authHeaders(token), body: JSON.stringify(payload)
  });
  if (!r.ok) throw await buildApiError(r, "Direk güncellenemedi.");
  return (await r.json()) as _Pole;
}
export async function deletePole(token: string, id: number): Promise<void> {
  const r = await fetch(`${API_BASE_URL}/grid/poles/${id}`, {
    method: "DELETE", headers: authHeaders(token)
  });
  if (!r.ok) throw await buildApiError(r, "Direk silinemedi.");
}

export async function reorderPoles(
  token: string,
  lineId: number,
  items: { pole_id: number; sequence_no: number }[]
): Promise<_Pole[]> {
  const r = await fetch(`${API_BASE_URL}/grid/lines/${lineId}/reorder-poles`, {
    method: "POST", headers: authHeaders(token),
    body: JSON.stringify({ line_id: lineId, items })
  });
  if (!r.ok) throw await buildApiError(r, "Direk sırası güncellenemedi.");
  return (await r.json()) as _Pole[];
}

export async function reversePoles(token: string, lineId: number): Promise<_Pole[]> {
  const r = await fetch(`${API_BASE_URL}/grid/lines/${lineId}/reverse-poles`, {
    method: "POST", headers: authHeaders(token)
  });
  if (!r.ok) throw await buildApiError(r, "Hat sırası tersine çevrilemedi.");
  return (await r.json()) as _Pole[];
}

export async function createSegment(token: string, payload: Partial<_Segment>): Promise<_Segment> {
  const r = await fetch(`${API_BASE_URL}/grid/segments`, {
    method: "POST", headers: authHeaders(token), body: JSON.stringify(payload)
  });
  if (!r.ok) throw await buildApiError(r, "Segment oluşturulamadı.");
  return (await r.json()) as _Segment;
}
export async function updateSegment(token: string, id: number, payload: Partial<_Segment>): Promise<_Segment> {
  const r = await fetch(`${API_BASE_URL}/grid/segments/${id}`, {
    method: "PATCH", headers: authHeaders(token), body: JSON.stringify(payload)
  });
  if (!r.ok) throw await buildApiError(r, "Segment güncellenemedi.");
  return (await r.json()) as _Segment;
}
export async function deleteSegment(token: string, id: number): Promise<void> {
  const r = await fetch(`${API_BASE_URL}/grid/segments/${id}`, {
    method: "DELETE", headers: authHeaders(token)
  });
  if (!r.ok) throw await buildApiError(r, "Segment silinemedi.");
}

// ----- Project Settings -----
// GET auth-siz kullanilabilsin; bazi yerlerde token vermeden de cagiriyoruz
// (login ekrani, header initial fetch). Backend GET /project-settings public.
export async function fetchProjectSettings(): Promise<import("./types").ProjectSettings> {
  const response = await fetch(`${API_BASE_URL}/project-settings`);
  if (!response.ok) throw await buildApiError(response, "Proje ayarları alınamadı.");
  return (await response.json()) as import("./types").ProjectSettings;
}

export async function updateProjectSettings(
  token: string,
  payload: import("./types").ProjectSettings
): Promise<import("./types").ProjectSettings> {
  const response = await fetch(`${API_BASE_URL}/project-settings`, {
    method: "PUT",
    headers: authHeaders(token),
    body: JSON.stringify(payload)
  });
  if (!response.ok) throw await buildApiError(response, "Proje ayarları kaydedilemedi.");
  return (await response.json()) as import("./types").ProjectSettings;
}

export async function fetchNotificationSettings(token: string): Promise<NotificationSettings> {
  const response = await fetch(`${API_BASE_URL}/notification-settings`, {
    headers: authHeaders(token)
  });
  if (!response.ok) throw await buildApiError(response, "Bildirim ayarları alınamadı.");
  return (await response.json()) as NotificationSettings;
}

export async function updateNotificationSettings(
  token: string,
  payload: NotificationSettings
): Promise<NotificationSettings> {
  const response = await fetch(`${API_BASE_URL}/notification-settings`, {
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
  const response = await fetch(`${API_BASE_URL}/notification-settings/test-smtp`, {
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
  const response = await fetch(`${API_BASE_URL}/notification-settings/test-sms`, {
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
  const response = await fetch(`${API_BASE_URL}/notification-settings/test-telegram`, {
    method: "POST",
    headers: authHeaders(token),
    body: JSON.stringify(payload)
  });
  if (!response.ok) throw await buildApiError(response, "Telegram test gönderimi başarısız.");
  return (await response.json()) as { ok: boolean; detail: string };
}

// ----- Signal Catalog -----
export async function fetchSignals(token: string, model?: string): Promise<SignalCatalogRow[]> {
  const url = model
    ? `${API_BASE_URL}/signals?model=${encodeURIComponent(model)}`
    : `${API_BASE_URL}/signals`;
  const response = await fetch(url, { headers: authHeaders(token) });
  if (!response.ok) throw await buildApiError(response, "Sinyal listesi alınamadı.");
  return (await response.json()) as SignalCatalogRow[];
}

export async function createSignal(
  token: string,
  payload: Omit<SignalCatalogRow, "id">
): Promise<SignalCatalogRow> {
  const response = await fetch(`${API_BASE_URL}/signals`, {
    method: "POST",
    headers: authHeaders(token),
    body: JSON.stringify(payload)
  });
  if (!response.ok) throw await buildApiError(response, "Sinyal oluşturulamadı.");
  return (await response.json()) as SignalCatalogRow;
}

export async function updateSignal(
  token: string,
  signalKey: string,
  payload: Partial<Omit<SignalCatalogRow, "id" | "key">>
): Promise<SignalCatalogRow> {
  const response = await fetch(`${API_BASE_URL}/signals/${encodeURIComponent(signalKey)}`, {
    method: "PATCH",
    headers: authHeaders(token),
    body: JSON.stringify(payload)
  });
  if (!response.ok) throw await buildApiError(response, "Sinyal güncellenemedi.");
  return (await response.json()) as SignalCatalogRow;
}

export async function deleteSignal(token: string, signalKey: string): Promise<void> {
  const response = await fetch(`${API_BASE_URL}/signals/${encodeURIComponent(signalKey)}`, {
    method: "DELETE",
    headers: authHeaders(token)
  });
  if (!response.ok) throw await buildApiError(response, "Sinyal silinemedi.");
}

export async function fetchSignalLiveValues(token: string): Promise<SignalLiveRow[]> {
  const response = await fetch(`${API_BASE_URL}/signals/live`, { headers: authHeaders(token) });
  if (!response.ok) throw await buildApiError(response, "Canlı sinyal değerleri alınamadı.");
  return (await response.json()) as SignalLiveRow[];
}

/** Sistem Durumu sayfasi icin: backend host'unun anlik CPU/RAM/disk/uptime
 *  metriklerini getirir. Sayfa kapali iken cagirilmaz; psutil hesaplamasi
 *  cok hizli oldugu icin polling 5-10 sn'de tekrarlanabilir. */
export async function fetchHostStatus(token: string): Promise<HostStatus> {
  const response = await fetch(`${API_BASE_URL}/system-status/host`, {
    headers: authHeaders(token)
  });
  if (!response.ok) throw await buildApiError(response, "Sunucu kaynak metrikleri alınamadı.");
  return (await response.json()) as HostStatus;
}

/** Sistem Durumu sayfasi icin: backend'in bagli oldugu servislerin (DB,
 *  RabbitMQ, tag-engine vb.) saglik durumu. Her cagri tum servisleri
 *  paralel kontrol etmez (sirayla, kucuk timeout'la); pratikte 200ms altinda
 *  toplam suren bir cevap doner. */
export async function fetchServicesStatus(token: string): Promise<ServicesReport> {
  const response = await fetch(`${API_BASE_URL}/system-status/services`, {
    headers: authHeaders(token)
  });
  if (!response.ok) throw await buildApiError(response, "Servis durumlari alınamadı.");
  return (await response.json()) as ServicesReport;
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
  const response = await fetch(`${API_BASE_URL}/notifications${qs}`, {
    headers: authHeaders(token)
  });
  if (!response.ok) throw await buildApiError(response, "Bildirimler alınamadı.");
  return (await response.json()) as NotificationItem[];
}

export async function fetchNotificationUnreadCount(token: string): Promise<number> {
  const response = await fetch(`${API_BASE_URL}/notifications/unread-count`, {
    headers: authHeaders(token)
  });
  if (!response.ok) throw await buildApiError(response, "Okunmamış bildirim sayısı alınamadı.");
  const data = (await response.json()) as { unread: number };
  return Number(data.unread || 0);
}

export async function markNotificationRead(token: string, notificationId: number): Promise<void> {
  const response = await fetch(`${API_BASE_URL}/notifications/${notificationId}/read`, {
    method: "POST",
    headers: authHeaders(token)
  });
  if (!response.ok) throw await buildApiError(response, "Bildirim okundu işaretlenemedi.");
}

export async function markAllNotificationsRead(token: string): Promise<number> {
  const response = await fetch(`${API_BASE_URL}/notifications/read-all`, {
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
  const response = await fetch(`${API_BASE_URL}/signals/reset-to-defaults`, {
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
  const response = await fetch(`${API_BASE_URL}/alarm-rules`, { headers: authHeaders(token) });
  if (!response.ok) throw await buildApiError(response, "Alarm kuralları alınamadı.");
  return (await response.json()) as AlarmRuleRow[];
}

export async function createAlarmRule(
  token: string,
  payload: Omit<AlarmRuleRow, "id">
): Promise<AlarmRuleRow> {
  const response = await fetch(`${API_BASE_URL}/alarm-rules`, {
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
  const response = await fetch(`${API_BASE_URL}/alarm-rules/${ruleId}`, {
    method: "PATCH",
    headers: authHeaders(token),
    body: JSON.stringify(payload)
  });
  if (!response.ok) throw await buildApiError(response, "Alarm kuralı güncellenemedi.");
  return (await response.json()) as AlarmRuleRow;
}

export async function deleteAlarmRule(token: string, ruleId: number): Promise<void> {
  const response = await fetch(`${API_BASE_URL}/alarm-rules/${ruleId}`, {
    method: "DELETE",
    headers: authHeaders(token)
  });
  if (!response.ok) throw await buildApiError(response, "Alarm kuralı silinemedi.");
}


// ----- Responsibility Areas -----
export async function fetchResponsibilityAreas(token: string): Promise<ResponsibilityAreaRow[]> {
  const response = await fetch(`${API_BASE_URL}/responsibility-areas`, { headers: authHeaders(token) });
  if (!response.ok) throw await buildApiError(response, "Sorumluluk alanları alınamadı.");
  return (await response.json()) as ResponsibilityAreaRow[];
}

export async function fetchResponsibilityAreaDetail(token: string, areaId: number): Promise<ResponsibilityAreaDetail> {
  const response = await fetch(`${API_BASE_URL}/responsibility-areas/${areaId}`, { headers: authHeaders(token) });
  if (!response.ok) throw await buildApiError(response, "Sorumluluk alanı detayı alınamadı.");
  return (await response.json()) as ResponsibilityAreaDetail;
}

export async function createResponsibilityArea(
  token: string,
  payload: { code: string; name: string; description?: string | null; is_active?: boolean }
): Promise<ResponsibilityAreaRow> {
  const response = await fetch(`${API_BASE_URL}/responsibility-areas`, {
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
  const response = await fetch(`${API_BASE_URL}/responsibility-areas/${areaId}`, {
    method: "PATCH",
    headers: authHeaders(token),
    body: JSON.stringify(payload)
  });
  if (!response.ok) throw await buildApiError(response, "Sorumluluk alanı güncellenemedi.");
  return (await response.json()) as ResponsibilityAreaRow;
}

export async function deleteResponsibilityArea(token: string, areaId: number): Promise<void> {
  const response = await fetch(`${API_BASE_URL}/responsibility-areas/${areaId}`, {
    method: "DELETE",
    headers: authHeaders(token)
  });
  if (!response.ok) throw await buildApiError(response, "Sorumluluk alanı silinemedi.");
}

export async function addUserToArea(token: string, areaId: number, userId: number): Promise<void> {
  const response = await fetch(`${API_BASE_URL}/responsibility-areas/${areaId}/users/${userId}`, {
    method: "POST",
    headers: authHeaders(token)
  });
  if (!response.ok) throw await buildApiError(response, "Kullanıcı alana eklenemedi.");
}

export async function removeUserFromArea(token: string, areaId: number, userId: number): Promise<void> {
  const response = await fetch(`${API_BASE_URL}/responsibility-areas/${areaId}/users/${userId}`, {
    method: "DELETE",
    headers: authHeaders(token)
  });
  if (!response.ok) throw await buildApiError(response, "Kullanıcı alandan çıkarılamadı.");
}

export async function addDeviceToArea(token: string, areaId: number, deviceId: number): Promise<void> {
  const response = await fetch(`${API_BASE_URL}/responsibility-areas/${areaId}/devices/${deviceId}`, {
    method: "POST",
    headers: authHeaders(token)
  });
  if (!response.ok) throw await buildApiError(response, "Cihaz alana eklenemedi.");
}

export async function removeDeviceFromArea(token: string, areaId: number, deviceId: number): Promise<void> {
  const response = await fetch(`${API_BASE_URL}/responsibility-areas/${areaId}/devices/${deviceId}`, {
    method: "DELETE",
    headers: authHeaders(token)
  });
  if (!response.ok) throw await buildApiError(response, "Cihaz alandan çıkarılamadı.");
}

export async function addRegionToArea(token: string, areaId: number, regionId: number): Promise<void> {
  const response = await fetch(`${API_BASE_URL}/responsibility-areas/${areaId}/regions/${regionId}`, {
    method: "POST",
    headers: authHeaders(token)
  });
  if (!response.ok) throw await buildApiError(response, "Bölge alana eklenemedi.");
}

export async function removeRegionFromArea(token: string, areaId: number, regionId: number): Promise<void> {
  const response = await fetch(`${API_BASE_URL}/responsibility-areas/${areaId}/regions/${regionId}`, {
    method: "DELETE",
    headers: authHeaders(token)
  });
  if (!response.ok) throw await buildApiError(response, "Bölge alandan çıkarılamadı.");
}

export async function addLineToArea(token: string, areaId: number, lineId: number): Promise<void> {
  const response = await fetch(`${API_BASE_URL}/responsibility-areas/${areaId}/lines/${lineId}`, {
    method: "POST",
    headers: authHeaders(token)
  });
  if (!response.ok) throw await buildApiError(response, "Hat alana eklenemedi.");
}

export async function removeLineFromArea(token: string, areaId: number, lineId: number): Promise<void> {
  const response = await fetch(`${API_BASE_URL}/responsibility-areas/${areaId}/lines/${lineId}`, {
    method: "DELETE",
    headers: authHeaders(token)
  });
  if (!response.ok) throw await buildApiError(response, "Hat alandan çıkarılamadı.");
}
