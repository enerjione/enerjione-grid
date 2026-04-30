import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Header } from "../components/Header";
import { useToast } from "../components/ToastProvider";
import { LoginForm } from "../features/auth/LoginForm";
import { UserManagementPanel } from "../features/auth/UserManagementPanel";
import { AlarmsPage } from "../features/alarms/AlarmsPage";
import { ResponsibilityAreasPage } from "../features/responsibility-areas/ResponsibilityAreasPage";
import { EventsPage } from "../features/events/EventsPage";
import { SystemStatusPage } from "../features/system-status/SystemStatusPage";
import { DeviceManagementPanel } from "../features/devices/DeviceManagementPanel";
import { OutboundTargetsPanel } from "../features/outbound/OutboundTargetsPanel";
import { NotificationSettingsPanel } from "../features/settings/NotificationSettingsPanel";
import { DeviceSidebar } from "../features/devices/DeviceSidebar";
import { LiveValuesPage } from "../features/live-values/LiveValuesPage";
import { DeviceMapTab } from "../features/map/DeviceMapTab";
import { SignalsPage } from "../features/signals/SignalsPage";
import { AlarmRulesPage } from "../features/alarm-rules/AlarmRulesPage";
import {
  changeMyPassword,
  clearSession,
  createAlarmRule,
  createGateway,
  createDevice,
  createSignal,
  createUser,
  deleteAlarmRule,
  deleteDevice,
  deleteGateway,
  downloadGatewayCompose,
  deleteSignal,
  deleteUser,
  addDeviceToArea,
  addUserToArea,
  createResponsibilityArea,
  deleteResponsibilityArea,
  fetchAlarmComments,
  fetchAlarmEvents,
  fetchAlarmRules,
  fetchDeviceModels,
  fetchDevices,
  fetchGateways,
  fetchResponsibilityAreaDetail,
  fetchResponsibilityAreas,
  removeDeviceFromArea,
  removeUserFromArea,
  updateResponsibilityArea,
  fetchSystemEvents,
  fetchMe,
  fetchNotificationSettings,
  fetchOutboundTargets,
  fetchSignals,
  fetchSignalLiveValues,
  fetchUsers,
  loadSession,
  login,
  logout,
  resetUserPassword,
  saveSession,
  addAlarmComment,
  acknowledgeAlarm,
  deleteAlarm,
  acknowledgeAllAlarms,
  assignAlarm,
  resetAlarm,
  resetAllAlarms,
  updateAlarmRule,
  updateGateway,
  updateOutboundTarget,
  updateDevice,
  updateSignal,
  createOutboundTarget,
  deleteOutboundTarget,
  testNotificationSms,
  testNotificationSmtp,
  updateNotificationSettings as updateNotificationSettingsApi,
  updateUser,
  updateMyProfile
} from "../shared/api";
import type {
  AlarmComment,
  AlarmEvent,
  AlarmRuleRow,
  AuthSession,
  DeviceModelOption,
  Dnp3ExtendedSettings,
  DeviceRow,
  Gateway,
  NotificationSettings,
  OutboundTarget,
  ResponsibilityAreaRow,
  SignalCatalogRow,
  SignalLiveRow,
  SystemEvent,
  UserRead,
  UserRole
} from "../shared/types";

type TabId = "map" | "values";
type PageMode = "home" | "alarms" | "events" | "system-status" | "engineering";
type EngineeringPage =
  | "devices"
  | "signals"
  | "live-values"
  | "alarm-rules"
  | "users"
  | "responsibility-areas"
  | "outbound"
  | "notifications";

const ROUTE_STORAGE_KEY = "hsl.route.v1";
const VALID_PAGE_MODES: PageMode[] = ["home", "alarms", "events", "system-status", "engineering"];
const VALID_ENGINEERING_PAGES: EngineeringPage[] = [
  "devices",
  "signals",
  "live-values",
  "alarm-rules",
  "users",
  "responsibility-areas",
  "outbound",
  "notifications"
];
const VALID_HOME_TABS: TabId[] = ["map", "values"];

type PersistedRoute = {
  pageMode: PageMode;
  engineeringPage: EngineeringPage;
  homeTab: TabId;
};

function loadPersistedRoute(): PersistedRoute {
  const fallback: PersistedRoute = { pageMode: "home", engineeringPage: "devices", homeTab: "map" };
  if (typeof window === "undefined") return fallback;
  try {
    const raw = window.localStorage.getItem(ROUTE_STORAGE_KEY);
    if (!raw) return fallback;
    const parsed = JSON.parse(raw) as Partial<PersistedRoute>;
    return {
      pageMode: VALID_PAGE_MODES.includes(parsed.pageMode as PageMode) ? (parsed.pageMode as PageMode) : fallback.pageMode,
      engineeringPage: VALID_ENGINEERING_PAGES.includes(parsed.engineeringPage as EngineeringPage)
        ? (parsed.engineeringPage as EngineeringPage)
        : fallback.engineeringPage,
      homeTab: VALID_HOME_TABS.includes(parsed.homeTab as TabId) ? (parsed.homeTab as TabId) : fallback.homeTab
    };
  } catch {
    return fallback;
  }
}

function savePersistedRoute(route: PersistedRoute): void {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.setItem(ROUTE_STORAGE_KEY, JSON.stringify(route));
  } catch {
    // sessizce yutuyoruz - localStorage devre dışı / quota dolmuş olabilir
  }
}

export function App() {
  const [session, setSession] = useState<AuthSession | null>(() => loadSession());
  const [devices, setDevices] = useState<DeviceRow[]>([]);
  const [users, setUsers] = useState<UserRead[]>([]);
  const [alarms, setAlarms] = useState<AlarmEvent[]>([]);
  const [events, setEvents] = useState<SystemEvent[]>([]);
  const [gateways, setGateways] = useState<Gateway[]>([]);
  const [devicesByGateway, setDevicesByGateway] = useState<DeviceRow[]>([]);
  /** Cihazlar sekmesinde listelenen gateway (kapsam); yenileme ve yoklama bunu kullanır */
  const [devicePanelGatewayCode, setDevicePanelGatewayCode] = useState<string>("");
  const [outboundTargets, setOutboundTargets] = useState<OutboundTarget[]>([]);
  const [alarmsLoading, setAlarmsLoading] = useState(false);
  const [currentUser, setCurrentUser] = useState<UserRead | null>(null);
  const [authError, setAuthError] = useState<string>();
  const [loadingLogin, setLoadingLogin] = useState(false);
  const [loadingData, setLoadingData] = useState(false);
  const [selectedDeviceId, setSelectedDeviceId] = useState<number>(0);
  const toast = useToast();
  const persistedRouteRef = useRef<PersistedRoute>(loadPersistedRoute());
  const [activeTab, setActiveTab] = useState<TabId>(() => persistedRouteRef.current.homeTab);
  const [engineeringPage, setEngineeringPage] = useState<EngineeringPage>(
    () => persistedRouteRef.current.engineeringPage
  );
  const [pageMode, setPageMode] = useState<PageMode>(() => persistedRouteRef.current.pageMode);

  useEffect(() => {
    savePersistedRoute({ pageMode, engineeringPage, homeTab: activeTab });
  }, [pageMode, engineeringPage, activeTab]);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [settingsFullName, setSettingsFullName] = useState("");
  const [settingsEmail, setSettingsEmail] = useState("");
  const [settingsCurrentPassword, setSettingsCurrentPassword] = useState("");
  const [settingsNewPassword, setSettingsNewPassword] = useState("");
  const [settingsSaving, setSettingsSaving] = useState(false);
  const [settingsError, setSettingsError] = useState("");
  const [notificationSettings, setNotificationSettings] = useState<NotificationSettings | null>(null);
  const [notificationSettingsLoading, setNotificationSettingsLoading] = useState(false);
  const [notificationSettingsSaving, setNotificationSettingsSaving] = useState(false);
  const [notificationSettingsError, setNotificationSettingsError] = useState("");
  const [signalCatalog, setSignalCatalog] = useState<SignalCatalogRow[]>([]);
  const [deviceModels, setDeviceModels] = useState<DeviceModelOption[]>([]);
  const [responsibilityAreas, setResponsibilityAreas] = useState<ResponsibilityAreaRow[]>([]);
  const [signalLiveValues, setSignalLiveValues] = useState<SignalLiveRow[]>([]);
  const [signalLoading, setSignalLoading] = useState(false);
  const [signalLiveLoading, setSignalLiveLoading] = useState(false);
  const [signalError, setSignalError] = useState("");
  const [signalLiveError, setSignalLiveError] = useState("");
  const [alarmRules, setAlarmRules] = useState<AlarmRuleRow[]>([]);
  const [alarmRulesLoading, setAlarmRulesLoading] = useState(false);
  const [alarmRulesError, setAlarmRulesError] = useState("");

  const signalLiveFetchIdRef = useRef(0);

  useEffect(() => {
    const load = async () => {
      if (!session) return;
      setSignalLiveValues([]);
      setSignalLiveError("");
      setLoadingData(true);
      try {
        const me = await fetchMe(session.accessToken);
        setCurrentUser(me);
        setSettingsFullName(me.full_name);
        setSettingsEmail(me.email);
        const loadedDevices = await fetchDevices(session.accessToken);
        setDevices(loadedDevices);
        setAlarmsLoading(true);
        const alarmRows = await fetchAlarmEvents(session.accessToken);
        setAlarms(alarmRows);
        const eventRows = await fetchSystemEvents(session.accessToken);
        setEvents(eventRows);
        const gatewayRows = await fetchGateways(session.accessToken);
        setGateways(gatewayRows);
        if (gatewayRows.length > 0) {
          const g0 = gatewayRows[0].code;
          setDevicePanelGatewayCode(g0);
          setDevicesByGateway(loadedDevices.filter((d) => d.gatewayCode === g0));
        } else {
          setDevicePanelGatewayCode("");
          setDevicesByGateway(loadedDevices);
        }
        if (session.role === "engineer" || session.role === "installer") {
          const allUsers = await fetchUsers(session.accessToken);
          setUsers(allUsers);
        } else {
          setUsers([]);
        }
        if (session.role === "installer") {
          const outboundRows = await fetchOutboundTargets(session.accessToken);
          setOutboundTargets(outboundRows);
          const notificationRows = await fetchNotificationSettings(session.accessToken);
          setNotificationSettings(notificationRows);
        } else {
          setOutboundTargets([]);
          setNotificationSettings(null);
        }
        try {
          const signalsRows = await fetchSignals(session.accessToken);
          setSignalCatalog(signalsRows);
        } catch {
          setSignalCatalog([]);
        }
        try {
          const modelRows = await fetchDeviceModels(session.accessToken);
          setDeviceModels(modelRows);
        } catch {
          setDeviceModels([]);
        }
        try {
          const ruleRows = await fetchAlarmRules(session.accessToken);
          setAlarmRules(ruleRows);
        } catch {
          setAlarmRules([]);
        }
        try {
          const areaRows = await fetchResponsibilityAreas(session.accessToken);
          setResponsibilityAreas(areaRows);
        } catch {
          setResponsibilityAreas([]);
        }
      } catch {
        setAuthError("Oturum geçersiz veya API erişilemiyor.");
      } finally {
        setAlarmsLoading(false);
        setLoadingData(false);
      }
    };
    void load();
  }, [session]);

  useEffect(() => {
    if (!session) return;
    const canAccessEngineering = session.role === "engineer" || session.role === "installer";
    if (!canAccessEngineering && pageMode === "engineering") {
      setPageMode("home");
      setEngineeringPage("devices");
    }
  }, [session, pageMode]);

  const handleLogin = async (username: string, password: string) => {
    setLoadingLogin(true);
    setAuthError(undefined);
    try {
      const nextSession = await login(username, password);
      saveSession(nextSession);
      setSession(nextSession);
      setPageMode("home");
      setEngineeringPage("devices");
    } catch (error) {
      setAuthError(error instanceof Error ? error.message : "Giriş başarısız.");
    } finally {
      setLoadingLogin(false);
    }
  };

  const handleLogout = () => {
    if (session) {
      void logout(session.accessToken);
    }
    clearSession();
    setSession(null);
    setCurrentUser(null);
    setDevices([]);
    setUsers([]);
    setAlarms([]);
    setEvents([]);
    setGateways([]);
    setDevicesByGateway([]);
    setDevicePanelGatewayCode("");
    setOutboundTargets([]);
    setNotificationSettings(null);
    setSignalCatalog([]);
    setSignalLiveValues([]);
    setSignalLiveError("");
    setAlarmRules([]);
    setEngineeringPage("devices");
    setPageMode("home");
  };

  const reloadSignals = async () => {
    if (!session) return;
    setSignalLoading(true);
    setSignalError("");
    try {
      const rows = await fetchSignals(session.accessToken);
      setSignalCatalog(rows);
    } catch (err) {
      setSignalError(err instanceof Error ? err.message : "Sinyal listesi alınamadı.");
    } finally {
      setSignalLoading(false);
    }
  };

  const handleCreateSignal = async (payload: Omit<SignalCatalogRow, "id">) => {
    if (!session) return;
    await createSignal(session.accessToken, payload);
    await reloadSignals();
    toast.success("Sinyal eklendi.");
  };

  const handleUpdateSignal = async (
    signalKey: string,
    payload: Partial<Omit<SignalCatalogRow, "id" | "key">>
  ) => {
    if (!session) return;
    await updateSignal(session.accessToken, signalKey, payload);
    await reloadSignals();
    toast.success("Sinyal güncellendi.");
  };

  const handleDeleteSignal = async (signalKey: string) => {
    if (!session) return;
    await deleteSignal(session.accessToken, signalKey);
    await reloadSignals();
    toast.success("Sinyal silindi.");
  };

  const handleRefreshSignalLive = useCallback(async () => {
    if (!session) return;
    const id = ++signalLiveFetchIdRef.current;
    setSignalLiveLoading(true);
    setSignalLiveError("");
    try {
      const rows = await fetchSignalLiveValues(session.accessToken);
      if (id !== signalLiveFetchIdRef.current) {
        return;
      }
      setSignalLiveValues(rows);
    } catch (err) {
      if (id !== signalLiveFetchIdRef.current) {
        return;
      }
      setSignalLiveValues([]);
      setSignalLiveError(
        err instanceof Error ? err.message : "Canlı değerler yüklenemedi."
      );
    } finally {
      if (id === signalLiveFetchIdRef.current) {
        setSignalLiveLoading(false);
      }
    }
  }, [session]);

  useEffect(() => {
    if (!session) {
      return;
    }
    if (pageMode === "engineering" && engineeringPage === "live-values") {
      if (session.role !== "engineer" && session.role !== "installer") {
        return;
      }
      void handleRefreshSignalLive();
    }
  }, [session, pageMode, engineeringPage, handleRefreshSignalLive]);

  useEffect(() => {
    if (!session) {
      return;
    }
    if (pageMode === "home" && activeTab === "values") {
      void handleRefreshSignalLive();
    }
  }, [session, pageMode, activeTab, handleRefreshSignalLive]);

  const reloadAlarmRules = async () => {
    if (!session) return;
    setAlarmRulesLoading(true);
    setAlarmRulesError("");
    try {
      const rows = await fetchAlarmRules(session.accessToken);
      setAlarmRules(rows);
    } catch (err) {
      setAlarmRulesError(err instanceof Error ? err.message : "Alarm kuralları alınamadı.");
    } finally {
      setAlarmRulesLoading(false);
    }
  };

  const handleCreateAlarmRule = async (payload: Omit<AlarmRuleRow, "id">) => {
    if (!session) return;
    await createAlarmRule(session.accessToken, payload);
    await reloadAlarmRules();
    toast.success("Alarm kuralı eklendi.");
  };

  const handleUpdateAlarmRule = async (
    ruleId: number,
    payload: Partial<Omit<AlarmRuleRow, "id" | "signal_key">>
  ) => {
    if (!session) return;
    await updateAlarmRule(session.accessToken, ruleId, payload);
    await reloadAlarmRules();
    toast.success("Alarm kuralı güncellendi.");
  };

  const handleDeleteAlarmRule = async (ruleId: number) => {
    if (!session) return;
    await deleteAlarmRule(session.accessToken, ruleId);
    await reloadAlarmRules();
    toast.success("Alarm kuralı silindi.");
  };

  const reloadUsers = async () => {
    if (!session) return;
    if (session.role !== "engineer" && session.role !== "installer") return;
    const allUsers = await fetchUsers(session.accessToken);
    setUsers(allUsers);
  };

  const handleCreateUser = async (payload: {
    username: string;
    email: string;
    phone_number?: string | null;
    full_name: string;
    password: string;
    role: UserRole;
  }) => {
    if (!session) return;
    await createUser(session.accessToken, payload);
    await reloadUsers();
    toast.success(`Kullanıcı "${payload.username}" eklendi.`);
  };

  const handleDeleteUser = async (userId: number) => {
    if (!session) return;
    await deleteUser(session.accessToken, userId);
    await reloadUsers();
    toast.success("Kullanıcı silindi.");
  };

  const handleUpdateUser = async (
    userId: number,
    payload: { email: string; phone_number?: string | null; full_name: string; role: UserRole }
  ) => {
    if (!session) return;
    await updateUser(session.accessToken, userId, payload);
    await reloadUsers();
    toast.success("Kullanıcı güncellendi.");
  };

  const handleResetUserPassword = async (userId: number, newPassword: string) => {
    if (!session) return;
    await resetUserPassword(session.accessToken, userId, newPassword);
    toast.success("Kullanıcı şifresi sıfırlandı.");
  };

  const handleAssignAlarm = async (alarmId: number, assignedTo: string | null) => {
    if (!session) return;
    const updated = await assignAlarm(session.accessToken, alarmId, assignedTo);
    setAlarms((prev) => prev.map((item) => (item.id === updated.id ? updated : item)));
    toast.success(assignedTo ? `Alarm ${assignedTo} kullanıcısına atandı.` : "Alarm ataması kaldırıldı.");
  };

  const handleLoadAlarmComments = async (alarmId: number): Promise<AlarmComment[]> => {
    if (!session) return [];
    return fetchAlarmComments(session.accessToken, alarmId);
  };

  const handleAddAlarmComment = async (alarmId: number, comment: string) => {
    if (!session) return;
    await addAlarmComment(session.accessToken, alarmId, comment);
    toast.success("Yorum eklendi.");
  };

  const handleAcknowledgeAlarm = async (alarmId: number) => {
    if (!session) return;
    const updated = await acknowledgeAlarm(session.accessToken, alarmId);
    setAlarms((prev) => prev.map((item) => (item.id === updated.id ? updated : item)));
    toast.success("Alarm onaylandı.");
  };

  const handleResetAlarm = async (alarmId: number) => {
    if (!session) return;
    const updated = await resetAlarm(session.accessToken, alarmId);
    setAlarms((prev) => prev.map((item) => (item.id === updated.id ? updated : item)));
    toast.success("Alarm sıfırlandı.");
  };

  const handleDeleteAlarm = async (alarmId: number) => {
    if (!session) return;
    await deleteAlarm(session.accessToken, alarmId);
    setAlarms((prev) => prev.filter((item) => item.id !== alarmId));
    toast.success("Alarm silindi.");
  };

  const handleAcknowledgeAllAlarms = async () => {
    if (!session) return;
    const updated = await acknowledgeAllAlarms(session.accessToken);
    setAlarms(updated);
    toast.success("Tüm alarmlar onaylandı.");
  };

  const handleResetAllAlarms = async () => {
    if (!session) return;
    const updated = await resetAllAlarms(session.accessToken);
    setAlarms(updated);
    toast.success("Tüm alarmlar sıfırlandı.");
  };

  const reloadResponsibilityAreas = async () => {
    if (!session) return;
    const rows = await fetchResponsibilityAreas(session.accessToken);
    setResponsibilityAreas(rows);
  };

  const handleLoadAreaDetail = async (areaId: number) => {
    if (!session) throw new Error("Oturum yok");
    return fetchResponsibilityAreaDetail(session.accessToken, areaId);
  };

  const handleCreateArea = async (payload: { code: string; name: string; description?: string | null }) => {
    if (!session) return;
    await createResponsibilityArea(session.accessToken, payload);
    await reloadResponsibilityAreas();
    toast.success("Sorumluluk alanı oluşturuldu.");
  };

  const handleUpdateArea = async (
    areaId: number,
    payload: { name?: string; description?: string | null; is_active?: boolean }
  ) => {
    if (!session) return;
    await updateResponsibilityArea(session.accessToken, areaId, payload);
    await reloadResponsibilityAreas();
    toast.success("Sorumluluk alanı güncellendi.");
  };

  const handleDeleteArea = async (areaId: number) => {
    if (!session) return;
    await deleteResponsibilityArea(session.accessToken, areaId);
    await reloadResponsibilityAreas();
    toast.success("Sorumluluk alanı silindi.");
  };

  const handleAddUserToArea = async (areaId: number, userId: number) => {
    if (!session) return;
    await addUserToArea(session.accessToken, areaId, userId);
    await reloadResponsibilityAreas();
    toast.success("Kullanıcı alana eklendi.");
  };

  const handleRemoveUserFromArea = async (areaId: number, userId: number) => {
    if (!session) return;
    await removeUserFromArea(session.accessToken, areaId, userId);
    await reloadResponsibilityAreas();
    toast.success("Kullanıcı alandan çıkarıldı.");
  };

  const handleAddDeviceToArea = async (areaId: number, deviceId: number) => {
    if (!session) return;
    await addDeviceToArea(session.accessToken, areaId, deviceId);
    await reloadResponsibilityAreas();
    toast.success("Cihaz alana eklendi.");
  };

  const handleRemoveDeviceFromArea = async (areaId: number, deviceId: number) => {
    if (!session) return;
    await removeDeviceFromArea(session.accessToken, areaId, deviceId);
    await reloadResponsibilityAreas();
    toast.success("Cihaz alandan çıkarıldı.");
  };

  const reloadGateways = async () => {
    if (!session) return;
    if (session.role !== "engineer" && session.role !== "installer") return;
    const rows = await fetchGateways(session.accessToken);
    setGateways(rows);
    return rows;
  };

  const handleCreateGateway = async (payload: {
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
  }) => {
    if (!session) return;
    await createGateway(session.accessToken, payload);
    await reloadGateways();
    toast.success(`Gateway "${payload.name}" eklendi.`);
  };

  const handleDownloadGatewayCompose = async (
    gatewayCode: string,
    params: { backendUrl: string; hostPort: number; fmt: "compose" | "env" }
  ) => {
    if (!session) return;
    const { blob, filename } = await downloadGatewayCompose(session.accessToken, gatewayCode, {
      backendUrl: params.backendUrl,
      hostPort: params.hostPort,
      fmt: params.fmt
    });
    const url = window.URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    a.remove();
    window.URL.revokeObjectURL(url);
    toast.success(`${filename} indirildi.`);
  };

  const handleDeleteGateway = async (gatewayCode: string) => {
    if (!session) return;
    const gateway = gateways.find((item) => item.code === gatewayCode);
    const displayName = gateway?.name ?? gatewayCode;
    const childCount = devices.filter((d) => d.gatewayCode === gatewayCode).length;
    const message =
      childCount > 0
        ? [
            `"${displayName}" gateway kalıcı olarak silinecek.`,
            `Bu gatewaye bağlı ${childCount} cihaz, bu cihazlara ait telemetri ve alarm kayıtları da silinecek.`,
            "Bu işlem geri alınamaz. Onaylıyor musunuz?"
          ].join("\n\n")
        : [
            `"${displayName}" gateway kalıcı olarak silinecek.`,
            "Bu işlem geri alınamaz. Onaylıyor musunuz?"
          ].join("\n\n");
    if (!window.confirm(message)) return;
    await deleteGateway(session.accessToken, gatewayCode);
    const nextGateways = await reloadGateways();
    const all = await fetchDevices(session.accessToken);
    setDevices(all);
    if (nextGateways && nextGateways.length > 0) {
      const firstCode = nextGateways[0].code;
      setDevicePanelGatewayCode(firstCode);
      const scoped = await fetchDevices(session.accessToken, firstCode);
      setDevicesByGateway(scoped);
    } else {
      setDevicePanelGatewayCode("");
      setDevicesByGateway([]);
    }
    toast.success(`Gateway "${displayName}" silindi.`);
  };

  const handleUpdateGateway = async (
    gatewayCode: string,
    payload: { name?: string; host?: string; listen_port?: number; token?: string }
  ) => {
    if (!session) return;
    await updateGateway(session.accessToken, gatewayCode, payload);
    await reloadGateways();
    toast.success("Gateway güncellendi.");
  };

  const handleSelectGatewayForDevices = useCallback(
    async (gatewayCode: string) => {
      if (!session) return;
      setDevicePanelGatewayCode(gatewayCode);
      const scopedDevices = await fetchDevices(session.accessToken, gatewayCode);
      setDevicesByGateway(scopedDevices);
    },
    [session]
  );

  const refreshDevicePanelData = useCallback(async () => {
    if (!session) return;
    if (session.role !== "engineer" && session.role !== "installer") return;
    try {
      const [gw, allDev] = await Promise.all([
        fetchGateways(session.accessToken),
        fetchDevices(session.accessToken)
      ]);
      setGateways(gw);
      setDevices(allDev);
      if (devicePanelGatewayCode) {
        const still = gw.some((g) => g.code === devicePanelGatewayCode);
        const code = still ? devicePanelGatewayCode : (gw[0]?.code ?? "");
        if (code) {
          if (!still) {
            setDevicePanelGatewayCode(code);
          }
          setDevicesByGateway(allDev.filter((d) => d.gatewayCode === code));
        } else {
          setDevicePanelGatewayCode("");
          setDevicesByGateway(allDev);
        }
      } else {
        if (gw.length > 0) {
          const c = gw[0].code;
          setDevicePanelGatewayCode(c);
          setDevicesByGateway(allDev.filter((d) => d.gatewayCode === c));
        } else {
          setDevicesByGateway(allDev);
        }
      }
    } catch {
      // API hatasi ust katmanda
    }
  }, [session, devicePanelGatewayCode]);

  useEffect(() => {
    if (!session) return;
    if (pageMode !== "engineering" || engineeringPage !== "devices") return;
    if (session.role !== "engineer" && session.role !== "installer") return;
    // Sekme her acildiginda hemen taze veri cek - aksi halde stale gateway
    // durumu (haberlesme yok gibi) gosterilebiliyor.
    void refreshDevicePanelData();
    const id = window.setInterval(() => {
      void refreshDevicePanelData();
    }, 12000);
    return () => window.clearInterval(id);
  }, [pageMode, engineeringPage, session, refreshDevicePanelData]);

  const handleCreateDevice = async (payload: {
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
  }) => {
    if (!session) return;
    await createDevice(session.accessToken, payload);
    const all = await fetchDevices(session.accessToken);
    setDevices(all);
    if (payload.gateway_code) {
      setDevicePanelGatewayCode(payload.gateway_code);
      const scoped = await fetchDevices(session.accessToken, payload.gateway_code);
      setDevicesByGateway(scoped);
    } else {
      setDevicesByGateway(all);
    }
    try {
      const signalsRows = await fetchSignals(session.accessToken);
      setSignalCatalog(signalsRows);
    } catch {
      // sinyal listesi tazelense iyi, canlı matrisin etiketleriyle uyum kalsin
    }
    await handleRefreshSignalLive();
    toast.success(`Cihaz "${payload.name}" eklendi.`);
  };

  const handleUpdateDevice = async (
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
  ) => {
    if (!session) return;
    await updateDevice(session.accessToken, deviceCode, payload);
    const all = await fetchDevices(session.accessToken);
    setDevices(all);
    if (payload.gateway_code) {
      const scoped = await fetchDevices(session.accessToken, payload.gateway_code);
      setDevicesByGateway(scoped);
    } else {
      setDevicesByGateway(all);
    }
    toast.success("Cihaz güncellendi.");
  };

  const handleDeleteDevice = async (deviceCode: string) => {
    if (!session) return;
    await deleteDevice(session.accessToken, deviceCode);
    const all = await fetchDevices(session.accessToken);
    setDevices(all);
    setDevicesByGateway((prev) => prev.filter((item) => item.code !== deviceCode));
    await handleRefreshSignalLive();
    toast.success("Cihaz silindi.");
  };

  const reloadOutboundTargets = async () => {
    if (!session || session.role !== "installer") return;
    const rows = await fetchOutboundTargets(session.accessToken);
    setOutboundTargets(rows);
  };

  const handleCreateOutboundTarget = async (payload: {
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
  }) => {
    if (!session) return;
    await createOutboundTarget(session.accessToken, payload);
    await reloadOutboundTargets();
    toast.success(`Outbound hedef "${payload.name}" eklendi.`);
  };

  const handleUpdateOutboundTarget = async (
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
  ) => {
    if (!session) return;
    await updateOutboundTarget(session.accessToken, targetId, payload);
    await reloadOutboundTargets();
    toast.success("Outbound hedef güncellendi.");
  };

  const handleDeleteOutboundTarget = async (targetId: number) => {
    if (!session) return;
    await deleteOutboundTarget(session.accessToken, targetId);
    await reloadOutboundTargets();
    toast.success("Outbound hedef silindi.");
  };

  const reloadNotificationSettings = async () => {
    if (!session || session.role !== "installer") return;
    setNotificationSettingsLoading(true);
    setNotificationSettingsError("");
    try {
      const rows = await fetchNotificationSettings(session.accessToken);
      setNotificationSettings(rows);
    } catch (error) {
      setNotificationSettingsError(error instanceof Error ? error.message : "Bildirim ayarları alınamadı.");
    } finally {
      setNotificationSettingsLoading(false);
    }
  };

  const handleSaveNotificationSettings = async (payload: NotificationSettings) => {
    if (!session) return;
    setNotificationSettingsSaving(true);
    setNotificationSettingsError("");
    try {
      const updated = await updateNotificationSettingsApi(session.accessToken, payload);
      setNotificationSettings(updated);
      toast.success("Bildirim ayarları kaydedildi.");
    } catch (error) {
      setNotificationSettingsError(error instanceof Error ? error.message : "Bildirim ayarları kaydedilemedi.");
      throw error;
    } finally {
      setNotificationSettingsSaving(false);
    }
  };

  const handleTestNotificationSmtp = async (payload: {
    recipient_email: string;
    subject?: string;
    message?: string;
  }) => {
    if (!session) {
      throw new Error("Oturum bulunamadı.");
    }
    return testNotificationSmtp(session.accessToken, payload);
  };

  const handleTestNotificationSms = async (payload: { recipient_phone: string; message?: string }) => {
    if (!session) {
      throw new Error("Oturum bulunamadı.");
    }
    return testNotificationSms(session.accessToken, payload);
  };

  const handleOpenSettings = () => {
    if (currentUser) {
      setSettingsFullName(currentUser.full_name);
      setSettingsEmail(currentUser.email);
    }
    setSettingsCurrentPassword("");
    setSettingsNewPassword("");
    setSettingsError("");
    setSettingsOpen(true);
  };

  const handleSaveSettings = async () => {
    if (!session) return;
    setSettingsSaving(true);
    setSettingsError("");
    try {
      const updated = await updateMyProfile(session.accessToken, {
        full_name: settingsFullName,
        email: settingsEmail
      });
      setCurrentUser(updated);
      if (settingsCurrentPassword && settingsNewPassword) {
        await changeMyPassword(session.accessToken, {
          current_password: settingsCurrentPassword,
          new_password: settingsNewPassword
        });
      }
      setSettingsOpen(false);
    } catch (error) {
      setSettingsError(error instanceof Error ? error.message : "Ayarlar kaydedilemedi.");
    } finally {
      setSettingsSaving(false);
    }
  };

  const selectedDevice = useMemo(
    () => devices.find((item) => item.id === selectedDeviceId),
    [devices, selectedDeviceId]
  );

  const handleRefreshSystemStatus = async () => {
    if (!session) return;
    setLoadingData(true);
    try {
      const [dev, gw, al] = await Promise.all([
        fetchDevices(session.accessToken),
        fetchGateways(session.accessToken),
        fetchAlarmEvents(session.accessToken)
      ]);
      setDevices(dev);
      setGateways(gw);
      setAlarms(al);
      if (devicePanelGatewayCode) {
        const still = gw.some((g) => g.code === devicePanelGatewayCode);
        const code = still ? devicePanelGatewayCode : (gw[0]?.code ?? "");
        if (code) {
          if (!still) {
            setDevicePanelGatewayCode(code);
          }
          setDevicesByGateway(dev.filter((d) => d.gatewayCode === code));
        } else {
          setDevicePanelGatewayCode("");
          setDevicesByGateway(dev);
        }
      } else {
        if (gw.length > 0) {
          const c = gw[0].code;
          setDevicePanelGatewayCode(c);
          setDevicesByGateway(dev.filter((d) => d.gatewayCode === c));
        } else {
          setDevicesByGateway(dev);
        }
      }
    } catch {
      // Oturum hatasi ust seviyede yakala
    } finally {
      setLoadingData(false);
    }
  };

  if (!session) {
    return <LoginForm onSubmit={handleLogin} loading={loadingLogin} error={authError} />;
  }

  return (
    <div className="layout">
      <Header
        fullName={currentUser?.full_name ?? session.username}
        role={session.role}
        activePage={pageMode}
        onChangePage={setPageMode}
        isEngineeringView={pageMode === "engineering"}
        onToggleEngineering={() => setPageMode("engineering")}
        onSettings={handleOpenSettings}
        onLogout={handleLogout}
      />
      <div className="body">
        {pageMode === "engineering" ? (
          <main className="content engineering-content">
            <div className="tabs">
              <button
                className={engineeringPage === "devices" ? "active" : ""}
                onClick={() => setEngineeringPage("devices")}
              >
                Cihazlar
              </button>
              {session.role === "engineer" || session.role === "installer" ? (
                <button
                  className={engineeringPage === "live-values" ? "active" : ""}
                  onClick={() => setEngineeringPage("live-values")}
                >
                  Canlı Değerler
                </button>
              ) : null}
              {session.role === "engineer" || session.role === "installer" ? (
                <button
                  className={engineeringPage === "users" ? "active" : ""}
                  onClick={() => {
                    setEngineeringPage("users");
                    void reloadUsers();
                  }}
                >
                  Kullanıcılar
                </button>
              ) : null}
              {session.role === "engineer" || session.role === "installer" ? (
                <button
                  className={engineeringPage === "responsibility-areas" ? "active" : ""}
                  onClick={() => {
                    setEngineeringPage("responsibility-areas");
                    void reloadResponsibilityAreas();
                  }}
                >
                  Sorumluluk Alanları
                </button>
              ) : null}
              {session.role === "installer" ? (
                <>
                  <button
                    className={engineeringPage === "signals" ? "active" : ""}
                    onClick={() => {
                      setEngineeringPage("signals");
                      void reloadSignals();
                    }}
                  >
                    Sinyaller
                  </button>
                  <button
                    className={engineeringPage === "alarm-rules" ? "active" : ""}
                    onClick={() => {
                      setEngineeringPage("alarm-rules");
                      void reloadAlarmRules();
                      void reloadSignals();
                    }}
                  >
                    Alarm Yönetimi
                  </button>
                  <button
                    className={engineeringPage === "outbound" ? "active" : ""}
                    onClick={() => setEngineeringPage("outbound")}
                  >
                    Outbound
                  </button>
                  <button
                    className={engineeringPage === "notifications" ? "active" : ""}
                    onClick={() => {
                      setEngineeringPage("notifications");
                      void reloadNotificationSettings();
                    }}
                  >
                    Bildirim Ayarları
                  </button>
                </>
              ) : null}
            </div>

            {engineeringPage === "devices" &&
            (session.role === "engineer" || session.role === "installer") ? (
              <DeviceManagementPanel
                role={session.role}
                gateways={gateways}
                devices={devicesByGateway}
                deviceModels={deviceModels}
                onSelectGateway={handleSelectGatewayForDevices}
                onCreateGateway={handleCreateGateway}
                onUpdateGateway={handleUpdateGateway}
                onDeleteGateway={handleDeleteGateway}
                onDownloadCompose={handleDownloadGatewayCompose}
                onCreate={handleCreateDevice}
                onUpdate={handleUpdateDevice}
                onDelete={handleDeleteDevice}
              />
            ) : null}
            {engineeringPage === "devices" &&
            session.role !== "engineer" &&
            session.role !== "installer" ? (
              <p className="helper-text">
                Cihaz yönetimi yalnızca <strong>mühendis</strong> veya <strong>kurulumcu</strong> rolü ile
                görüntülenebilir.
              </p>
            ) : null}
            {engineeringPage === "signals" && session.role === "installer" ? (
              <SignalsPage
                role={session.role}
                signals={signalCatalog}
                deviceModels={deviceModels}
                loading={signalLoading}
                error={signalError}
                onUpdate={handleUpdateSignal}
              />
            ) : null}
            {engineeringPage === "live-values" &&
            (session.role === "engineer" || session.role === "installer") ? (
              <LiveValuesPage
                values={signalLiveValues}
                signals={signalCatalog}
                devices={devices}
                gateways={gateways}
                loading={signalLiveLoading}
                error={signalLiveError}
                onRefresh={handleRefreshSignalLive}
              />
            ) : null}
            {engineeringPage === "alarm-rules" && session.role === "installer" ? (
              <AlarmRulesPage
                role={session.role}
                rules={alarmRules}
                signals={signalCatalog}
                loading={alarmRulesLoading}
                error={alarmRulesError}
                onCreate={handleCreateAlarmRule}
                onUpdate={handleUpdateAlarmRule}
                onDelete={handleDeleteAlarmRule}
              />
            ) : null}
            {engineeringPage === "users" && (session.role === "engineer" || session.role === "installer") ? (
              <UserManagementPanel
                users={users}
                currentUserId={currentUser?.id}
                allowInstallerRole={session.role === "installer"}
                onCreate={handleCreateUser}
                onDelete={handleDeleteUser}
                onUpdate={handleUpdateUser}
                onResetPassword={handleResetUserPassword}
              />
            ) : null}
            {engineeringPage === "responsibility-areas" &&
            (session.role === "engineer" || session.role === "installer") ? (
              <ResponsibilityAreasPage
                role={session.role}
                areas={responsibilityAreas}
                users={users}
                devices={devices}
                onLoadDetail={handleLoadAreaDetail}
                onCreate={handleCreateArea}
                onUpdate={handleUpdateArea}
                onDelete={handleDeleteArea}
                onAddUser={handleAddUserToArea}
                onRemoveUser={handleRemoveUserFromArea}
                onAddDevice={handleAddDeviceToArea}
                onRemoveDevice={handleRemoveDeviceFromArea}
              />
            ) : null}
            {engineeringPage === "outbound" && session.role === "installer" ? (
              <OutboundTargetsPanel
                targets={outboundTargets}
                onCreate={handleCreateOutboundTarget}
                onUpdate={handleUpdateOutboundTarget}
                onDelete={handleDeleteOutboundTarget}
              />
            ) : null}
            {engineeringPage === "notifications" && session.role === "installer" ? (
              <NotificationSettingsPanel
                initialSettings={notificationSettings}
                loading={notificationSettingsLoading}
                saving={notificationSettingsSaving}
                error={notificationSettingsError}
                onSave={handleSaveNotificationSettings}
                onTestSmtp={handleTestNotificationSmtp}
                onTestSms={handleTestNotificationSms}
              />
            ) : null}
          </main>
        ) : pageMode !== "home" ? (
          <main className="content">
            {pageMode === "alarms" ? (
              <AlarmsPage
                alarms={alarms}
                users={users}
                devices={devices}
                loading={alarmsLoading}
                onAssign={handleAssignAlarm}
                onLoadComments={handleLoadAlarmComments}
                onAddComment={handleAddAlarmComment}
                onAcknowledge={handleAcknowledgeAlarm}
                onReset={handleResetAlarm}
                onDelete={handleDeleteAlarm}
                onAcknowledgeAll={handleAcknowledgeAllAlarms}
                onResetAll={handleResetAllAlarms}
              />
            ) : null}
            {pageMode === "events" ? (
              <EventsPage events={events} loading={loadingData} />
            ) : null}
            {pageMode === "system-status" ? (
              <SystemStatusPage
                devices={devices}
                gateways={gateways}
                alarms={alarms}
                loading={loadingData}
                onRefresh={handleRefreshSystemStatus}
              />
            ) : null}
          </main>
        ) : (
          <>
            <DeviceSidebar devices={devices} selectedId={selectedDeviceId} onSelect={setSelectedDeviceId} />
            <main className={`content dashboard-content ${activeTab === "map" ? "map-active" : ""}`}>
              <div className="tabs dashboard-tabs">
                <button className={activeTab === "map" ? "active" : ""} onClick={() => setActiveTab("map")}>
                  Harita
                </button>
                <button className={activeTab === "values" ? "active" : ""} onClick={() => setActiveTab("values")}>
                  Tablo
                </button>
              </div>

              {loadingData ? <p>Yükleniyor...</p> : null}
              {activeTab === "map" ? (
                <DeviceMapTab
                  devices={devices}
                  selectedDevice={selectedDevice}
                  onSelectDevice={setSelectedDeviceId}
                />
              ) : null}
              {activeTab === "values" ? (
                <LiveValuesPage
                  values={signalLiveValues}
                  signals={signalCatalog}
                  devices={devices}
                  gateways={gateways}
                  loading={signalLiveLoading}
                  error={signalLiveError}
                  onRefresh={handleRefreshSignalLive}
                />
              ) : null}
            </main>
          </>
        )}
      </div>

      {settingsOpen ? (
        <div className="settings-modal-backdrop">
          <div className="settings-modal">
            <h3>Profil Ayarları</h3>
            <label>
              İsim Soyisim
              <input value={settingsFullName} onChange={(event) => setSettingsFullName(event.target.value)} />
            </label>
            <label>
              E-posta
              <input value={settingsEmail} onChange={(event) => setSettingsEmail(event.target.value)} />
            </label>
            <label>
              Mevcut Şifre (opsiyonel)
              <input
                type="password"
                value={settingsCurrentPassword}
                onChange={(event) => setSettingsCurrentPassword(event.target.value)}
              />
            </label>
            <label>
              Yeni Şifre (opsiyonel)
              <input
                type="password"
                value={settingsNewPassword}
                onChange={(event) => setSettingsNewPassword(event.target.value)}
              />
            </label>
            {settingsError ? <p className="error-text">{settingsError}</p> : null}
            <div className="settings-actions">
              <button onClick={() => setSettingsOpen(false)}>Vazgeç</button>
              <button onClick={handleSaveSettings} disabled={settingsSaving}>
                {settingsSaving ? "Kaydediliyor..." : "Kaydet"}
              </button>
            </div>
          </div>
        </div>
      ) : null}
    </div>
  );
}
