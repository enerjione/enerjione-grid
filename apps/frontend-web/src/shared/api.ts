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

const API_BASE_URL = `${window.location.protocol}//${window.location.hostname}:8000/api/v1`;
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

async function buildApiError(response: Response, fallbackMessage: string): Promise<Error> {
  if (response.status === 401) {
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

export function loadSession(): AuthSession | null {
  const raw = localStorage.getItem(AUTH_STORAGE_KEY);
  if (!raw) return null;
  try {
    return JSON.parse(raw) as AuthSession;
  } catch {
    localStorage.removeItem(AUTH_STORAGE_KEY);
    return null;
  }
}

export function saveSession(session: AuthSession): void {
  localStorage.setItem(AUTH_STORAGE_KEY, JSON.stringify(session));
}

export function clearSession(): void {
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
    longitude: item.longitude
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
    protocol: "rest" | "mqtt";
    endpoint: string;
    topic?: string | null;
    event_filter: "all" | "telemetry" | "alarm";
    auth_header?: string | null;
    auth_token?: string | null;
    qos: number;
    retain: boolean;
    is_active: boolean;
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
