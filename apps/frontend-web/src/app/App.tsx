import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Header } from "../components/Header";
import { LoginForm } from "../features/auth/LoginForm";
import { UserManagementPanel } from "../features/auth/UserManagementPanel";
import { AlarmsPage } from "../features/alarms/AlarmsPage";
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
  deleteSignal,
  deleteUser,
  fetchAlarmComments,
  fetchAlarmEvents,
  fetchAlarmRules,
  fetchDevices,
  fetchGateways,
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
  Dnp3ExtendedSettings,
  DeviceRow,
  Gateway,
  NotificationSettings,
  OutboundTarget,
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
  | "outbound"
  | "notifications";

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
  const [activeTab, setActiveTab] = useState<TabId>("map");
  const [engineeringPage, setEngineeringPage] = useState<EngineeringPage>("devices");
  const [pageMode, setPageMode] = useState<PageMode>("home");
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
          const ruleRows = await fetchAlarmRules(session.accessToken);
          setAlarmRules(ruleRows);
        } catch {
          setAlarmRules([]);
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
  };

  const handleUpdateSignal = async (
    signalKey: string,
    payload: Partial<Omit<SignalCatalogRow, "id" | "key">>
  ) => {
    if (!session) return;
    await updateSignal(session.accessToken, signalKey, payload);
    await reloadSignals();
  };

  const handleDeleteSignal = async (signalKey: string) => {
    if (!session) return;
    await deleteSignal(session.accessToken, signalKey);
    await reloadSignals();
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
  };

  const handleUpdateAlarmRule = async (
    ruleId: number,
    payload: Partial<Omit<AlarmRuleRow, "id" | "signal_key">>
  ) => {
    if (!session) return;
    await updateAlarmRule(session.accessToken, ruleId, payload);
    await reloadAlarmRules();
  };

  const handleDeleteAlarmRule = async (ruleId: number) => {
    if (!session) return;
    await deleteAlarmRule(session.accessToken, ruleId);
    await reloadAlarmRules();
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
  };

  const handleDeleteUser = async (userId: number) => {
    if (!session) return;
    await deleteUser(session.accessToken, userId);
    await reloadUsers();
  };

  const handleUpdateUser = async (
    userId: number,
    payload: { email: string; phone_number?: string | null; full_name: string; role: UserRole }
  ) => {
    if (!session) return;
    await updateUser(session.accessToken, userId, payload);
    await reloadUsers();
  };

  const handleResetUserPassword = async (userId: number, newPassword: string) => {
    if (!session) return;
    await resetUserPassword(session.accessToken, userId, newPassword);
  };

  const handleAssignAlarm = async (alarmId: number, assignedTo: string | null) => {
    if (!session) return;
    const updated = await assignAlarm(session.accessToken, alarmId, assignedTo);
    setAlarms((prev) => prev.map((item) => (item.id === updated.id ? updated : item)));
  };

  const handleLoadAlarmComments = async (alarmId: number): Promise<AlarmComment[]> => {
    if (!session) return [];
    return fetchAlarmComments(session.accessToken, alarmId);
  };

  const handleAddAlarmComment = async (alarmId: number, comment: string) => {
    if (!session) return;
    await addAlarmComment(session.accessToken, alarmId, comment);
  };

  const handleAcknowledgeAlarm = async (alarmId: number) => {
    if (!session) return;
    const updated = await acknowledgeAlarm(session.accessToken, alarmId);
    setAlarms((prev) => prev.map((item) => (item.id === updated.id ? updated : item)));
  };

  const handleResetAlarm = async (alarmId: number) => {
    if (!session) return;
    const updated = await resetAlarm(session.accessToken, alarmId);
    setAlarms((prev) => prev.map((item) => (item.id === updated.id ? updated : item)));
  };

  const handleAcknowledgeAllAlarms = async () => {
    if (!session) return;
    const updated = await acknowledgeAllAlarms(session.accessToken);
    setAlarms(updated);
  };

  const handleResetAllAlarms = async () => {
    if (!session) return;
    const updated = await resetAllAlarms(session.accessToken);
    setAlarms(updated);
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
  };

  const handleUpdateGateway = async (
    gatewayCode: string,
    payload: { name?: string; host?: string; listen_port?: number; token?: string }
  ) => {
    if (!session) return;
    await updateGateway(session.accessToken, gatewayCode, payload);
    await reloadGateways();
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
    const id = window.setInterval(() => {
      void refreshDevicePanelData();
    }, 12000);
    return () => window.clearInterval(id);
  }, [pageMode, engineeringPage, session, refreshDevicePanelData]);

  const handleCreateDevice = async (payload: {
    code: string;
    name: string;
    description?: string | null;
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
  };

  const handleUpdateDevice = async (
    deviceCode: string,
    payload: {
      name?: string;
      description?: string | null;
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
  };

  const handleDeleteDevice = async (deviceCode: string) => {
    if (!session) return;
    await deleteDevice(session.accessToken, deviceCode);
    const all = await fetchDevices(session.accessToken);
    setDevices(all);
    setDevicesByGateway((prev) => prev.filter((item) => item.code !== deviceCode));
    await handleRefreshSignalLive();
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
  };

  const handleDeleteOutboundTarget = async (targetId: number) => {
    if (!session) return;
    await deleteOutboundTarget(session.accessToken, targetId);
    await reloadOutboundTargets();
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
                onSelectGateway={handleSelectGatewayForDevices}
                onCreateGateway={handleCreateGateway}
                onUpdateGateway={handleUpdateGateway}
                onDeleteGateway={handleDeleteGateway}
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
                loading={alarmsLoading}
                onAssign={handleAssignAlarm}
                onLoadComments={handleLoadAlarmComments}
                onAddComment={handleAddAlarmComment}
                onAcknowledge={handleAcknowledgeAlarm}
                onReset={handleResetAlarm}
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
