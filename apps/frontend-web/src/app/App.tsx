import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { Header } from "../components/Header";
import { useToast } from "../components/ToastProvider";
import { LoginForm } from "../features/auth/LoginForm";
import { UserManagementPanel } from "../features/auth/UserManagementPanel";
import { AlarmsPage } from "../features/alarms/AlarmsPage";
import { FaultListPage } from "../features/faults/FaultListPage";
import { BackupsPanel } from "../features/backups/BackupsPanel";
import { ResponsibilityAreasPage } from "../features/responsibility-areas/ResponsibilityAreasPage";
import { EventsPage } from "../features/events/EventsPage";
import { SystemStatusPage } from "../features/system-status/SystemStatusPage";
import { DeviceManagementPanel } from "../features/devices/DeviceManagementPanel";
import { OutboundTargetsPanel } from "../features/outbound/OutboundTargetsPanel";
import { ApiAccessPanel } from "../features/api-access/ApiAccessPanel";
import { NotificationSettingsPanel } from "../features/settings/NotificationSettingsPanel";
import { ProjectSettingsPanel } from "../features/settings/ProjectSettingsPanel";
import { GridManagementPanel } from "../features/grid/GridManagementPanel";
import { DeviceSidebar } from "../features/devices/DeviceSidebar";
import { LiveValuesPage } from "../features/live-values/LiveValuesPage";
import { DeviceMapTab } from "../features/map/DeviceMapTab";
import { DashboardFilterBar, type StatusFilter } from "../features/dashboard/DashboardFilterBar";
import { GridOverviewPage } from "../features/dashboard/GridOverviewPage";
import { GlobalLoading } from "../components/GlobalLoading";
import { useProjectSettings } from "../components/ProjectSettingsProvider";
import {
  isSupportedLanguage,
  setLanguage as setI18nLanguage,
  SUPPORTED_LANGUAGES,
  LANGUAGE_LABELS,
  type SupportedLanguage,
} from "../shared/i18n";
import { locateDevice } from "../shared/geoLookup";
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
  refreshGatewayAllDevices,
  downloadGatewayCompose,
  deleteSignal,
  deleteUser,
  addDeviceToArea,
  addLineToArea,
  addRegionToArea,
  addUserToArea,
  createResponsibilityArea,
  deleteResponsibilityArea,
  fetchAlarmComments,
  fetchAlarmEvents,
  fetchFaults,
  fetchFaultStats,
  assignFault,
  updateFaultStatus,
  updateFaultNote,
  fetchFaultComments,
  addFaultComment,
  fetchMyNotificationPrefs,
  fetchUserNotificationPrefs,
  updateUserNotificationPrefs,
  updateMyNotificationPrefs,
  fetchAlarmRules,
  fetchDeviceModels,
  fetchDevices,
  fetchGateways,
  fetchResponsibilityAreaDetail,
  fetchResponsibilityAreas,
  removeDeviceFromArea,
  removeLineFromArea,
  removeRegionFromArea,
  removeUserFromArea,
  updateResponsibilityArea,
  fetchSystemEvents,
  fetchMe,
  fetchNotificationSettings,
  fetchOutboundTargets,
  fetchMyApiKeys,
  createApiKey,
  revokeApiKey,
  purgeApiKey,
  setApiKeyActive,
  fetchGridSnapshot,
  type GridSnapshot,
  fetchSignals,
  fetchSignalLiveValues,
  fetchUsers,
  loadSession,
  login,
  logout,
  resetUserPassword,
  saveSession,
  SESSION_EXPIRED_EVENT,
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
  downloadIec104PointsCsv,
  downloadIec104PointsXlsx,
  autoAssignDeviceCa,
  fetchIec104Runtime,
  updateProjectSettings,
  testNotificationSms,
  testNotificationSmtp,
  testNotificationTelegram,
  discoverTelegramChats,
  updateNotificationSettings as updateNotificationSettingsApi,
  updateUser,
  updateMyProfile,
  updateMyLanguage,
  API_BASE_URL
} from "../shared/api";
import { useLiveValuesSocket } from "../shared/useLiveValuesSocket";
import type {
  AlarmComment,
  AlarmEvent,
  AlarmRuleRow,
  ApiKey,
  AuthSession,
  DeviceModelOption,
  Dnp3ExtendedSettings,
  DeviceRow,
  FaultComment,
  FaultEvent,
  FaultStats,
  UserNotificationPreferences,
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
type PageMode = "home" | "alarms" | "faults" | "events" | "system-status" | "engineering";
type EngineeringPage =
  | "devices"
  | "signals"
  | "live-values"
  | "alarm-rules"
  | "users"
  | "responsibility-areas"
  | "outbound"
  | "api-access"
  | "notifications"
  | "project-settings"
  | "grid"
  | "backups";

const ROUTE_STORAGE_KEY = "hsl.route.v1";
const VALID_PAGE_MODES: PageMode[] = ["home", "alarms", "faults", "events", "system-status", "engineering"];
const VALID_ENGINEERING_PAGES: EngineeringPage[] = [
  "devices",
  "signals",
  "live-values",
  "alarm-rules",
  "users",
  "responsibility-areas",
  "outbound",
  "api-access",
  "notifications",
  "project-settings",
  "grid",
  "backups"
];
type PersistedRoute = {
  pageMode: PageMode;
  engineeringPage: EngineeringPage;
};

// Ana sayfa sekmesi (Harita / Tablo) kasten persist edilmez — kullanıcı her
// oturumda haritayla başlasın; tabloya geçtikten sonra yenilemede yine harita
// gelir. Sayfa modu (Anasayfa/Alarmlar/Olaylar/Mühendislik) ve engineering alt
// sayfası persist olur — kullanıcı çalıştığı sayfada kalır.
function loadPersistedRoute(): PersistedRoute {
  const fallback: PersistedRoute = { pageMode: "home", engineeringPage: "devices" };
  if (typeof window === "undefined") return fallback;
  try {
    const raw = window.localStorage.getItem(ROUTE_STORAGE_KEY);
    if (!raw) return fallback;
    const parsed = JSON.parse(raw) as Partial<PersistedRoute>;
    return {
      pageMode: VALID_PAGE_MODES.includes(parsed.pageMode as PageMode) ? (parsed.pageMode as PageMode) : fallback.pageMode,
      engineeringPage: VALID_ENGINEERING_PAGES.includes(parsed.engineeringPage as EngineeringPage)
        ? (parsed.engineeringPage as EngineeringPage)
        : fallback.engineeringPage
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
  const projectSettings = useProjectSettings();
  const [session, setSession] = useState<AuthSession | null>(() => loadSession());
  const [devices, setDevices] = useState<DeviceRow[]>([]);
  const [users, setUsers] = useState<UserRead[]>([]);
  const [alarms, setAlarms] = useState<AlarmEvent[]>([]);
  const [faults, setFaults] = useState<FaultEvent[]>([]);
  const [faultStats, setFaultStats] = useState<FaultStats | null>(null);
  const [events, setEvents] = useState<SystemEvent[]>([]);
  const [gateways, setGateways] = useState<Gateway[]>([]);
  const [devicesByGateway, setDevicesByGateway] = useState<DeviceRow[]>([]);
  /** Cihazlar sekmesinde listelenen gateway (kapsam); yenileme ve yoklama bunu kullanır */
  const [devicePanelGatewayCode, setDevicePanelGatewayCode] = useState<string>("");
  const [outboundTargets, setOutboundTargets] = useState<OutboundTarget[]>([]);
  // Public API icin kullanici PAT'lari. ApiAccessPanel kullanir.
  const [apiKeys, setApiKeys] = useState<ApiKey[]>([]);
  const [apiKeysLoading, setApiKeysLoading] = useState(false);
  const [gridSnapshot, setGridSnapshot] = useState<GridSnapshot | null>(null);
  const [alarmsLoading, setAlarmsLoading] = useState(false);
  const [currentUser, setCurrentUser] = useState<UserRead | null>(null);

  // i18n: kullanici tercihi degistiginde (login sonrasi me yuklenince veya
  // ayarlardan dil secildiginde) react-i18next'i ona gore senkronize et.
  useEffect(() => {
    const code = currentUser?.language;
    if (isSupportedLanguage(code)) {
      setI18nLanguage(code);
    }
  }, [currentUser?.language]);
  const [loadingLogin, setLoadingLogin] = useState(false);
  const [loadingData, setLoadingData] = useState(false);
  const [selectedDeviceId, setSelectedDeviceId] = useState<number>(0);
  const toast = useToast();
  const { t } = useTranslation();
  const persistedRouteRef = useRef<PersistedRoute>(loadPersistedRoute());
  // Harita ana sayfada her oturumda varsayılan olsun — persist edilmez.
  const [activeTab, setActiveTab] = useState<TabId>("map");
  const [engineeringPage, setEngineeringPage] = useState<EngineeringPage>(
    () => persistedRouteRef.current.engineeringPage
  );
  const [pageMode, setPageMode] = useState<PageMode>(() => persistedRouteRef.current.pageMode);

  useEffect(() => {
    savePersistedRoute({ pageMode, engineeringPage });
  }, [pageMode, engineeringPage]);

  // Ana sayfa (dashboard) ortak filtre state'i — Harita ve Tablo aynı filtreyi paylaşır.
  const [dashboardSearch, setDashboardSearch] = useState("");
  const [dashboardStatusFilter, setDashboardStatusFilter] = useState<StatusFilter>("all");
  const [dashboardAreaId, setDashboardAreaId] = useState<number | "all">("all");
  const [dashboardAreaDeviceIds, setDashboardAreaDeviceIds] = useState<Set<number> | null>(null);
  const [dashboardAreaLoading, setDashboardAreaLoading] = useState(false);
  const [dashboardLocationFilter, setDashboardLocationFilter] = useState<string>("all");
  // "unassigned" = topolojiye dahil olmayan cihazlar (hat/bolge atanmamis).
  // Kullanici saha ekipmaninin gridSnapshot'a baglanmamis olanlari haritada
  // gormek isterse bu seceneği seçer.
  const [dashboardRegionId, setDashboardRegionId] = useState<number | "all" | "unassigned">("all");
  const [dashboardLineId, setDashboardLineId] = useState<number | "all" | "unassigned">("all");
  const [sidebarCollapsed, setSidebarCollapsed] = useState<boolean>(() => {
    if (typeof window === "undefined") return false;
    return window.localStorage.getItem("hsl.dashboard.sidebar-collapsed") === "1";
  });

  useEffect(() => {
    window.localStorage.setItem(
      "hsl.dashboard.sidebar-collapsed",
      sidebarCollapsed ? "1" : "0"
    );
  }, [sidebarCollapsed]);

  // Sorumluluk alani secimi degistiginde o alanin cihaz id setini cek.
  useEffect(() => {
    if (dashboardAreaId === "all" || !session) {
      setDashboardAreaDeviceIds(null);
      return;
    }
    let cancelled = false;
    setDashboardAreaLoading(true);
    void fetchResponsibilityAreaDetail(session.accessToken, dashboardAreaId)
      .then((detail) => {
        if (cancelled) return;
        setDashboardAreaDeviceIds(new Set(detail.devices.map((d) => d.id)));
      })
      .catch(() => {
        if (cancelled) return;
        setDashboardAreaDeviceIds(new Set());
      })
      .finally(() => {
        if (cancelled) return;
        setDashboardAreaLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [dashboardAreaId, session]);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [notifPrefs, setNotifPrefs] = useState<UserNotificationPreferences | null>(null);
  const [notifPrefsSaving, setNotifPrefsSaving] = useState(false);
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

  // WebSocket-based canli telemetri akisi. Polling fallback ile birlikte
  // calisir; WS bagli iken ~200ms gecikme. Bagi kopunca polling devam eder.
  const liveSocket = useLiveValuesSocket({
    token: session?.accessToken ?? "",
    apiBaseUrl: API_BASE_URL,
    enabled: Boolean(session?.accessToken)
  });

  useEffect(() => {
    liveSocket.registerHandler((msg) => {
      setSignalLiveValues((prev) => liveSocket.apply(prev, msg));
    });
  }, [liveSocket]);

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
        try {
          const snap = await fetchGridSnapshot(session.accessToken);
          setGridSnapshot(snap);
        } catch {
          setGridSnapshot(null);
        }
      } catch {
        toast.error(t("toasts.sessionInvalidBody"), {
          title: t("toasts.sessionInvalidTitle")
        });
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

  const handleLogin = async (username: string, password: string, remember: boolean) => {
    setLoadingLogin(true);
    try {
      const nextSession = await login(username, password);
      saveSession(nextSession, remember);
      setSession(nextSession);
      setPageMode("home");
      setEngineeringPage("devices");
    } catch (error) {
      // Backend tipik olarak 401 'Invalid username or password' donderir; bunu
      // dile uygun toast'a cevir. Network/server hatasi farkli ele alinir.
      const msg = error instanceof Error ? error.message : "";
      // api.ts login() artik 'Kullanıcı adı veya şifre hatalı.' donderir
      // (eski sabit TR string). Hangi dilde olursak olalim bunu detect edip
      // i18n karsiligina cevir. Backend'den gelen anlamli baska bir mesaj
      // (orn. backend-side login validator) varsa olduğu gibi goster.
      let body: string;
      if (
        /invalid username or password|incorrect|hatali|hatalı|kullanıcı adı veya şifre/i.test(msg)
      ) {
        body = t("toasts.loginInvalidCredentials");
      } else if (
        /failed to fetch|network|cannot reach|sunucu(?:ya)?\s*ula/i.test(msg)
      ) {
        body = t("toasts.loginNetworkFail");
      } else if (msg) {
        body = msg;
      } else {
        body = t("toasts.loginGenericFail");
      }
      toast.error(body, { title: t("toasts.loginFailedTitle") });
    } finally {
      setLoadingLogin(false);
    }
  };

  const handleLogout = useCallback(() => {
    if (session) {
      void logout(session.accessToken);
    }
    clearSession();
    setSession(null);
    setCurrentUser(null);
    setDevices([]);
    setUsers([]);
    setAlarms([]);
    setFaults([]);
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
  }, [session]);

  // Token suresi dolup 401 dondugunde otomatik login ekranina dus.
  // api.ts buildApiError 401 yakaladiginda "hsl:session-expired" event'ini yayar;
  // burada dinleyip session'i temizler ve uyari toast'u gosteririz.
  useEffect(() => {
    const onExpired = () => {
      if (!session) return; // zaten login ekranindayiz
      toast.warning(t("toasts.sessionExpiredBody"), {
        title: t("toasts.sessionExpiredTitle")
      });
      handleLogout();
    };
    window.addEventListener(SESSION_EXPIRED_EVENT, onExpired);
    return () => window.removeEventListener(SESSION_EXPIRED_EVENT, onExpired);
  }, [session, handleLogout, toast]);

  // Alarm listesini arka planda her 5 sn yenile — alarm-service yeni alarm
  // urettiginde kullanici sayfayi yenilemeden anlik gorebilsin.
  useEffect(() => {
    if (!session) return;
    const tick = async () => {
      try {
        const rows = await fetchAlarmEvents(session.accessToken);
        setAlarms(rows);
      } catch {
        // sessizce yutuyoruz — gecici ag hatalari polling'i durdurmamali
      }
    };
    const id = window.setInterval(() => {
      void tick();
    }, 5000);
    return () => window.clearInterval(id);
  }, [session]);

  // Hat Arizalari (faults) canli refresh: 5 sn'de bir. Backend'in
  // fault_recompute_service'i alarm degistikce DB'yi senkronlar; biz de
  // burada UI'da liste tazelenir.
  useEffect(() => {
    if (!session) return;
    const tick = async () => {
      try {
        const rows = await fetchFaults(session.accessToken, "active");
        setFaults(rows);
      } catch {
        // ignore
      }
    };
    void tick();
    const id = window.setInterval(() => {
      void tick();
    }, 5000);
    return () => window.clearInterval(id);
  }, [session]);

  // Hat Arizalari ozet istatistikleri (avg cozum suresi vb) — 30sn polling.
  useEffect(() => {
    if (!session) return;
    const tick = async () => {
      try {
        const s = await fetchFaultStats(session.accessToken);
        setFaultStats(s);
      } catch {
        // ignore
      }
    };
    void tick();
    const id = window.setInterval(() => {
      void tick();
    }, 30000);
    return () => window.clearInterval(id);
  }, [session]);

  // Olaylar (system events) canli refresh: 5 sn'de bir. Olay sayfasinda
  // kullanici yeni kayitlari sayfa yenilemeden gorebilsin.
  useEffect(() => {
    if (!session) return;
    const tick = async () => {
      try {
        const rows = await fetchSystemEvents(session.accessToken);
        setEvents(rows);
      } catch {
        // ignore
      }
    };
    const id = window.setInterval(() => {
      void tick();
    }, 5000);
    return () => window.clearInterval(id);
  }, [session]);

  // Grid topology snapshot canli refresh: 5 sn'de bir. Hat Yonetimi'nde
  // yapilan degisiklikler (cihaz konumu, direk ekle/sil/tasi, bransman)
  // anasayfa haritasina hizla yansisin.
  useEffect(() => {
    if (!session) return;
    const tick = async () => {
      try {
        const snap = await fetchGridSnapshot(session.accessToken);
        setGridSnapshot(snap);
      } catch {
        // ignore
      }
    };
    const id = window.setInterval(() => {
      void tick();
    }, 5000);
    return () => window.clearInterval(id);
  }, [session]);

  // Cihaz listesi canli refresh: 5sn'de bir. Anasayfa cihaz panelindeki
  // haberlesme durumlari (yesil/gri dot), batarya, alarm bayraklari
  // sayfa yenilenmeden guncellensin. Onceden sadece ilk yuklemede ve
  // manuel refresh'te cekiliyordu — bu yuzden cihaz online olsa bile
  // UI'da stale "haberlesme yok" goruntusu kaliyordu.
  useEffect(() => {
    if (!session) return;
    const tick = async () => {
      try {
        const list = await fetchDevices(session.accessToken);
        setDevices(list);
      } catch {
        // ignore
      }
    };
    const id = window.setInterval(() => {
      void tick();
    }, 5000);
    return () => window.clearInterval(id);
  }, [session]);

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
    toast.success(t("toasts.signalAdded"));
  };

  const handleUpdateSignal = async (
    signalKey: string,
    payload: Partial<Omit<SignalCatalogRow, "id" | "key">>
  ) => {
    if (!session) return;
    await updateSignal(session.accessToken, signalKey, payload);
    await reloadSignals();
    toast.success(t("toasts.signalUpdated"));
  };

  const handleDeleteSignal = async (signalKey: string) => {
    if (!session) return;
    await deleteSignal(session.accessToken, signalKey);
    await reloadSignals();
    toast.success(t("toasts.signalDeleted"));
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
    // Anasayfada — harita ve tablo ikisi de canli degerlere ihtiyac duyar:
    //   - Tablo: liste hucreleri
    //   - Harita: popup batarya kartlari + sidebar master batarya yuzdesi
    //
    // WS bagli iken: telemetri push ile saniye altinda gelir; polling sadece
    // bir guvenlik agi (yeni cihaz eklenmesi, WS drop ettigi mesajlar). 30sn
    // polling yeterli — backend'e gereksiz yuk vermiyor, frontend hala canli.
    // WS bagli degilken (WS unsupported / nginx config eksik): polling 5sn
    // (degerler "yeterince" canli gozuksun).
    if (pageMode !== "home") return;
    if (activeTab !== "values" && activeTab !== "map") return;
    void handleRefreshSignalLive();
    const wsConnected = liveSocket.connectionState === "open";
    const intervalMs = wsConnected ? 30000 : 5000;
    const interval = window.setInterval(() => {
      void handleRefreshSignalLive();
    }, intervalMs);
    return () => window.clearInterval(interval);
  }, [session, pageMode, activeTab, handleRefreshSignalLive, liveSocket.connectionState]);

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
    toast.success(t("toasts.alarmRuleAdded"));
  };

  const handleUpdateAlarmRule = async (
    ruleId: number,
    payload: Partial<Omit<AlarmRuleRow, "id" | "signal_key">>
  ) => {
    if (!session) return;
    await updateAlarmRule(session.accessToken, ruleId, payload);
    await reloadAlarmRules();
    toast.success(t("toasts.alarmRuleUpdated"));
  };

  const handleDeleteAlarmRule = async (ruleId: number) => {
    if (!session) return;
    await deleteAlarmRule(session.accessToken, ruleId);
    await reloadAlarmRules();
    toast.success(t("toasts.alarmRuleDeleted"));
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
    toast.success(t("toasts.userAdded", { username: payload.username }));
  };

  const handleDeleteUser = async (userId: number) => {
    if (!session) return;
    await deleteUser(session.accessToken, userId);
    await reloadUsers();
    toast.success(t("toasts.userDeleted"));
  };

  const handleUpdateUser = async (
    userId: number,
    payload: { email: string; phone_number?: string | null; full_name: string; role: UserRole }
  ) => {
    if (!session) return;
    await updateUser(session.accessToken, userId, payload);
    await reloadUsers();
    toast.success(t("toasts.userUpdated"));
  };

  const handleResetUserPassword = async (userId: number, newPassword: string) => {
    if (!session) return;
    await resetUserPassword(session.accessToken, userId, newPassword);
    toast.success(t("toasts.userPasswordReset"));
  };

  const handleAssignAlarm = async (alarmId: number, assignedTo: string | null) => {
    if (!session) return;
    const updated = await assignAlarm(session.accessToken, alarmId, assignedTo);
    setAlarms((prev) => prev.map((item) => (item.id === updated.id ? updated : item)));
    toast.success(
      assignedTo
        ? t("toasts.alarmAssigned", { username: assignedTo })
        : t("toasts.alarmAssignCleared")
    );
  };

  // ===== Hat Arizalari (Fault) ticket handlers =====
  const handleAssignFault = async (faultId: number, username: string | null) => {
    if (!session) return;
    const updated = await assignFault(session.accessToken, faultId, username);
    setFaults((prev) => prev.map((f) => (f.id === updated.id ? updated : f)));
    toast.success(
      username
        ? t("toasts.faultAssigned", { username })
        : t("toasts.faultAssignCleared")
    );
  };
  const handleUpdateFaultStatus = async (faultId: number, newStatus: string) => {
    if (!session) return;
    const updated = await updateFaultStatus(session.accessToken, faultId, newStatus);
    setFaults((prev) => prev.map((f) => (f.id === updated.id ? updated : f)));
    toast.success(t("toasts.faultStatusUpdated"));
  };
  const handleUpdateFaultNote = async (faultId: number, note: string | null) => {
    if (!session) return;
    const updated = await updateFaultNote(session.accessToken, faultId, note);
    setFaults((prev) => prev.map((f) => (f.id === updated.id ? updated : f)));
    toast.success(t("toasts.noteSaved"));
  };
  const handleLoadFaultComments = async (faultId: number): Promise<FaultComment[]> => {
    if (!session) return [];
    return fetchFaultComments(session.accessToken, faultId);
  };
  const handleAddFaultComment = async (faultId: number, body: string) => {
    if (!session) return;
    await addFaultComment(session.accessToken, faultId, body);
    // Yorum eklenince comment_count'u +1 yapalim ki listede gozuksun
    setFaults((prev) =>
      prev.map((f) =>
        f.id === faultId ? { ...f, comment_count: (f.comment_count ?? 0) + 1 } : f
      )
    );
    toast.success(t("toasts.commentAdded"));
  };

  const handleLoadAlarmComments = async (alarmId: number): Promise<AlarmComment[]> => {
    if (!session) return [];
    return fetchAlarmComments(session.accessToken, alarmId);
  };

  const handleAddAlarmComment = async (alarmId: number, comment: string) => {
    if (!session) return;
    await addAlarmComment(session.accessToken, alarmId, comment);
    toast.success(t("toasts.commentAdded"));
  };

  const handleAcknowledgeAlarm = async (alarmId: number) => {
    if (!session) return;
    const updated = await acknowledgeAlarm(session.accessToken, alarmId);
    setAlarms((prev) => prev.map((item) => (item.id === updated.id ? updated : item)));
    toast.success(t("toasts.alarmAcknowledged"));
  };

  const handleResetAlarm = async (alarmId: number) => {
    if (!session) return;
    const updated = await resetAlarm(session.accessToken, alarmId);
    setAlarms((prev) => prev.map((item) => (item.id === updated.id ? updated : item)));
    toast.success(t("toasts.alarmReset"));
  };

  const handleDeleteAlarm = async (alarmId: number) => {
    if (!session) return;
    await deleteAlarm(session.accessToken, alarmId);
    setAlarms((prev) => prev.filter((item) => item.id !== alarmId));
    toast.success(t("toasts.alarmDeleted"));
  };

  const handleAcknowledgeAllAlarms = async () => {
    if (!session) return;
    const updated = await acknowledgeAllAlarms(session.accessToken);
    setAlarms(updated);
    toast.success(t("toasts.allAlarmsAcked"));
  };

  const handleResetAllAlarms = async () => {
    if (!session) return;
    const updated = await resetAllAlarms(session.accessToken);
    setAlarms(updated);
    toast.success(t("toasts.allAlarmsReset"));
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
    toast.success(t("toasts.areaCreated"));
  };

  const handleUpdateArea = async (
    areaId: number,
    payload: { name?: string; description?: string | null; is_active?: boolean }
  ) => {
    if (!session) return;
    await updateResponsibilityArea(session.accessToken, areaId, payload);
    await reloadResponsibilityAreas();
    toast.success(t("toasts.areaUpdated"));
  };

  const handleDeleteArea = async (areaId: number) => {
    if (!session) return;
    await deleteResponsibilityArea(session.accessToken, areaId);
    await reloadResponsibilityAreas();
    toast.success(t("toasts.areaDeleted"));
  };

  const handleAddUserToArea = async (areaId: number, userId: number) => {
    if (!session) return;
    await addUserToArea(session.accessToken, areaId, userId);
    await reloadResponsibilityAreas();
    toast.success(t("toasts.areaUserAdded"));
  };

  const handleRemoveUserFromArea = async (areaId: number, userId: number) => {
    if (!session) return;
    await removeUserFromArea(session.accessToken, areaId, userId);
    await reloadResponsibilityAreas();
    toast.success(t("toasts.areaUserRemoved"));
  };

  const handleAddDeviceToArea = async (areaId: number, deviceId: number) => {
    if (!session) return;
    await addDeviceToArea(session.accessToken, areaId, deviceId);
    await reloadResponsibilityAreas();
    toast.success(t("toasts.areaDeviceAdded"));
  };

  const handleRemoveDeviceFromArea = async (areaId: number, deviceId: number) => {
    if (!session) return;
    await removeDeviceFromArea(session.accessToken, areaId, deviceId);
    await reloadResponsibilityAreas();
    toast.success(t("toasts.areaDeviceRemoved"));
  };

  const handleAddRegionToArea = async (areaId: number, regionId: number) => {
    if (!session) return;
    await addRegionToArea(session.accessToken, areaId, regionId);
    await reloadResponsibilityAreas();
    toast.success(t("toasts.areaRegionAdded"));
  };

  const handleRemoveRegionFromArea = async (areaId: number, regionId: number) => {
    if (!session) return;
    await removeRegionFromArea(session.accessToken, areaId, regionId);
    await reloadResponsibilityAreas();
    toast.success(t("toasts.areaRegionRemoved"));
  };

  const handleAddLineToArea = async (areaId: number, lineId: number) => {
    if (!session) return;
    await addLineToArea(session.accessToken, areaId, lineId);
    await reloadResponsibilityAreas();
    toast.success(t("toasts.areaLineAdded"));
  };

  const handleRemoveLineFromArea = async (areaId: number, lineId: number) => {
    if (!session) return;
    await removeLineFromArea(session.accessToken, areaId, lineId);
    await reloadResponsibilityAreas();
    toast.success(t("toasts.areaLineRemoved"));
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
    initiating_port_count: number;
  }) => {
    if (!session) return;
    await createGateway(session.accessToken, payload);
    await reloadGateways();
    toast.success(t("toasts.gatewayAdded", { name: payload.name }));
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
    toast.success(t("toasts.gatewayDownloaded", { filename }));
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
    try {
      await deleteGateway(session.accessToken, gatewayCode);
      // Gateway listesi ve "tum cihazlar" listesi birbirinden bagimsiz —
      // ardisik degil paralel cek.
      const [nextGateways, all] = await Promise.all([
        reloadGateways(),
        fetchDevices(session.accessToken)
      ]);
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
      toast.success(t("toasts.gatewayDeleted", { name: displayName }));
    } catch (err) {
      const msg = err instanceof Error ? err.message : "Gateway silinemedi.";
      toast.error(t("toasts.gatewayDeleteFail", { msg }));
      throw err; // DeviceManagementPanel'in busy state'i de finally'de kapanir
    }
  };

  const handleRefreshGatewayAll = async (gatewayCode: string) => {
    if (!session) return;
    const gateway = gateways.find((g) => g.code === gatewayCode);
    const displayName = gateway?.name ?? gatewayCode;
    try {
      await refreshGatewayAllDevices(session.accessToken, gatewayCode);
      toast.success(t("toasts.gatewayRefreshQueued", { name: displayName }));
    } catch (err) {
      const msg = err instanceof Error ? err.message : t("toasts.requestSendFail");
      toast.error(t("toasts.gatewayRefreshFail", { msg }));
    }
  };

  const handleUpdateGateway = async (
    gatewayCode: string,
    payload: { name?: string; host?: string; listen_port?: number; token?: string }
  ) => {
    if (!session) return;
    await updateGateway(session.accessToken, gatewayCode, payload);
    await reloadGateways();
    toast.success(t("toasts.gatewayUpdated"));
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
    iec104_common_address?: number | null;
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
    toast.success(t("toasts.deviceAdded", { name: payload.name }));
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
      iec104_common_address?: number | null;
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
    toast.success(t("toasts.deviceUpdated"));
  };

  const handleDeleteDevice = async (deviceCode: string) => {
    if (!session) return;
    await deleteDevice(session.accessToken, deviceCode);
    const all = await fetchDevices(session.accessToken);
    setDevices(all);
    setDevicesByGateway((prev) => prev.filter((item) => item.code !== deviceCode));
    await handleRefreshSignalLive();
    toast.success(t("toasts.deviceDeleted"));
  };

  const reloadApiKeys = async () => {
    if (!session) return;
    setApiKeysLoading(true);
    try {
      const rows = await fetchMyApiKeys(session.accessToken);
      setApiKeys(rows);
    } catch (err) {
      // Sessizce yutmak yerine kullaniciya gosterelim — onceden olusturulmus
      // tokenlar 'gozukmuyor' sikayetini engellemek icin: backend'den gelen
      // hata mesaji burada loglanir ve toast olarak yansir.
      console.error("api_keys_fetch_failed", err);
      toast.error(
        err instanceof Error ? err.message : t("common.errorOccurred")
      );
    } finally {
      setApiKeysLoading(false);
    }
  };

  // API Erisimi paneli her acildiginda (veya kullanici degisince) listeyi
  // otomatik yukle. Onceden olusturulmus tokenlar refresh edilirken kullanici
  // 'liste bos gozukuyor' sikayeti yasamasin. Tab tiklamasinda da reload var
  // ama ilk acilis + sayfa F5 senaryolarinda kritik.
  useEffect(() => {
    if (!session) return;
    if (engineeringPage !== "api-access") return;
    if (session.role !== "installer") return;
    void reloadApiKeys();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [session, engineeringPage]);

  const handleCreateApiKey = async (payload: {
    name: string;
    scopes: string[];
    expires_at: string | null;
    allowed_ips: string[] | null;
  }) => {
    if (!session) throw new Error("Oturum yok.");
    return await createApiKey(session.accessToken, payload);
  };

  const handleRevokeApiKey = async (keyId: number) => {
    if (!session) throw new Error("Oturum yok.");
    await revokeApiKey(session.accessToken, keyId);
  };

  const handlePurgeApiKey = async (keyId: number) => {
    if (!session) throw new Error("Oturum yok.");
    await purgeApiKey(session.accessToken, keyId);
  };

  const handleToggleApiKeyActive = async (keyId: number, active: boolean) => {
    if (!session) throw new Error("Oturum yok.");
    await setApiKeyActive(session.accessToken, keyId, active);
  };

  const reloadOutboundTargets = async () => {
    if (!session || session.role !== "installer") return;
    const rows = await fetchOutboundTargets(session.accessToken);
    setOutboundTargets(rows);
  };

  const handleCreateOutboundTarget = async (payload: {
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
  }) => {
    if (!session) return;
    await createOutboundTarget(session.accessToken, payload);
    await reloadOutboundTargets();
    toast.success(t("toasts.outboundAdded", { name: payload.name }));
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
      listen_host?: string | null;
      listen_port?: number | null;
      iec104_common_address?: number | null;
      iec104_allowed_peers?: string | null;
    }
  ) => {
    if (!session) return;
    await updateOutboundTarget(session.accessToken, targetId, payload);
    await reloadOutboundTargets();
    toast.success(t("toasts.outboundUpdated"));
  };

  const handleDeleteOutboundTarget = async (targetId: number) => {
    if (!session) return;
    await deleteOutboundTarget(session.accessToken, targetId);
    await reloadOutboundTargets();
    toast.success(t("toasts.outboundDeleted"));
  };

  const handleDownloadIec104Points = async (targetId: number, suggestedName: string) => {
    if (!session) return;
    try {
      const count = await downloadIec104PointsCsv(session.accessToken, targetId, suggestedName);
      if (count === 0) {
        toast.warning(t("toasts.iec104DownloadedEmpty"));
      } else if (count !== null) {
        toast.success(t("toasts.iec104DownloadedCount", { count }));
      } else {
        toast.success(t("toasts.iec104Downloaded"));
      }
    } catch (err) {
      toast.error(err instanceof Error ? err.message : t("toasts.iec104DownloadFail"));
    }
  };

  const handleUpdateDeviceCa = async (deviceCode: string, ca: number | null) => {
    if (!session) return;
    await updateDevice(session.accessToken, deviceCode, { iec104_common_address: ca });
    const all = await fetchDevices(session.accessToken);
    setDevices(all);
    toast.success(t("toasts.asduAddressSaved", { deviceCode }));
  };

  const handleAutoAssignDeviceCa = async (targetId: number, overwrite: boolean) => {
    if (!session) return;
    try {
      const result = await autoAssignDeviceCa(session.accessToken, targetId, overwrite);
      const all = await fetchDevices(session.accessToken);
      setDevices(all);
      const msg = overwrite
        ? t("toasts.asduAutoAssignOverwrite", { assigned: result.assigned })
        : t("toasts.asduAutoAssignNew", { assigned: result.assigned, skipped: result.skipped });
      toast.success(msg);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : t("toasts.autoAssignFail"));
    }
  };

  const handleSaveProjectSettings = async (payload: import("../shared/types").ProjectSettings) => {
    if (!session) return;
    await updateProjectSettings(session.accessToken, payload);
    // Provider'i yenile — Login + Header logosu hemen guncellensin diye.
    await projectSettings.refresh();
    toast.success(t("toasts.projectSettingsSaved"));
  };

  const handleDownloadIec104Xlsx = async (targetId: number, suggestedName: string) => {
    if (!session) return;
    try {
      const count = await downloadIec104PointsXlsx(session.accessToken, targetId, suggestedName);
      if (count === 0) {
        toast.warning(t("toasts.signalListDownloadedEmpty"));
      } else if (count !== null) {
        toast.success(t("toasts.signalListDownloadedCount", { count }));
      } else {
        toast.success(t("toasts.signalListDownloaded"));
      }
    } catch (err) {
      toast.error(err instanceof Error ? err.message : t("toasts.signalListDownloadFail"));
    }
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
      toast.success(t("toasts.notificationSettingsSaved"));
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

  const handleTestNotificationTelegram = async (payload: { chat_id: string; message?: string }) => {
    if (!session) {
      throw new Error("Oturum bulunamadı.");
    }
    return testNotificationTelegram(session.accessToken, payload);
  };

  const handleDiscoverTelegramChats = async (payload?: { bot_token?: string }) => {
    if (!session) {
      throw new Error("Oturum bulunamadı.");
    }
    return discoverTelegramChats(session.accessToken, payload);
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
    if (session) {
      void (async () => {
        try {
          const prefs = await fetchMyNotificationPrefs(session.accessToken);
          setNotifPrefs(prefs);
        } catch {
          setNotifPrefs(null);
        }
      })();
    }
  };

  const handleToggleNotifPref = async (
    key: "web_enabled" | "email_enabled" | "sms_enabled"
  ) => {
    if (!session || !notifPrefs) return;
    const next: Partial<UserNotificationPreferences> = { [key]: !notifPrefs[key] };
    setNotifPrefsSaving(true);
    try {
      const updated = await updateMyNotificationPrefs(session.accessToken, next);
      setNotifPrefs(updated);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : t("toasts.preferenceSaveFail"));
    } finally {
      setNotifPrefsSaving(false);
    }
  };

  const handleChangeLanguage = async (code: SupportedLanguage) => {
    if (!session) return;
    // Optimistic: UI ani degissin, hata olursa eski state'e geri donelim.
    const previous = currentUser?.language ?? null;
    setI18nLanguage(code);
    setCurrentUser((u) => (u ? { ...u, language: code } : u));
    try {
      const updated = await updateMyLanguage(session.accessToken, code);
      setCurrentUser(updated);
    } catch (err) {
      if (isSupportedLanguage(previous)) setI18nLanguage(previous);
      setCurrentUser((u) => (u ? { ...u, language: previous } : u));
      toast.error(err instanceof Error ? err.message : t("toasts.languagePrefSaveFail"));
    }
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

  // Cihaz başına konum (il/ülke) etiketi — geo-lookup memo'ya alındı.
  const deviceLocationLabel = useMemo(() => {
    const map = new Map<number, string>();
    for (const d of devices) {
      map.set(d.id, locateDevice(d.latitude, d.longitude).label);
    }
    return map;
  }, [devices]);

  // Filtre dropdown'ı için benzersiz konum listesi (Türkçe sıralı).
  const dashboardLocationOptions = useMemo(() => {
    const set = new Set<string>();
    for (const label of deviceLocationLabel.values()) {
      if (label && label !== "Konum yok") set.add(label);
    }
    return Array.from(set).sort((a, b) => a.localeCompare(b, "tr"));
  }, [deviceLocationLabel]);

  // Cihaz id -> {regionId, regionName, lineId, lineName} — gridSnapshot'tan turetilir.
  const deviceTopologyInfo = useMemo(() => {
    const map = new Map<
      number,
      { regionId: number; regionName: string; lineId: number; lineName: string }
    >();
    if (!gridSnapshot) return map;
    const lineById = new Map(gridSnapshot.lines.map((l) => [l.id, l]));
    const regionById = new Map(gridSnapshot.regions.map((r) => [r.id, r]));
    for (const seg of gridSnapshot.segments) {
      if (!seg.device_id) continue;
      const line = lineById.get(seg.line_id);
      if (!line) continue;
      const region = regionById.get(line.region_id);
      map.set(seg.device_id, {
        regionId: line.region_id,
        regionName: region?.name ?? "",
        lineId: line.id,
        lineName: line.name
      });
    }
    return map;
  }, [gridSnapshot]);

  // Dashboard ortak filtrelerine göre süzülmüş cihaz listesi.
  // Harita marker'ları, sol sidebar listesi ve LiveValuesPage tablo satırları
  // bu kaynağı paylaşır → kullanıcı üst çubuğa girdiği değer her yerde aynı
  // anda etkili olur.
  const filteredDashboardDevices = useMemo(() => {
    const q = dashboardSearch.trim().toLowerCase();
    // gridSnapshot henuz yuklenmediginde topology bos olur — bu durumda
    // cihazlari "hat'a atanmamis" sayip filtrelemek tum listeyi siler.
    // O yuzden snapshot bilgisi varken (en az bir hat varken) "hat'a
    // atanmis" filtresini uygula; aksi halde cihazlari tum kalsin.
    const topologyReady = !!gridSnapshot && deviceTopologyInfo.size >= 0 && (gridSnapshot.lines.length > 0);
    return devices.filter((d) => {
      // Anasayfa = "izlenen sebeke". Cihaz herhangi bir hat segmentine
      // ATANMAMIS ise anasayfada (sidebar/harita/canli degerler) gosterilmez.
      // Cihaz Yonetimi'nde tum cihazlar ayrica yonetilir; oradan silmek
      // yerine sadece hat'tan kaldirmak yeterli — cihaz kayitli kalir,
      // tekrar atanabilir; ama gosterim acisindan "topolojinin parcasi
      // degil" sayilir.
      if (topologyReady && !deviceTopologyInfo.has(d.id)) return false;
      if (dashboardStatusFilter === "online" && d.communicationStatus !== "online") return false;
      if (dashboardStatusFilter === "offline" && d.communicationStatus === "online") return false;
      if (dashboardStatusFilter === "alarm" && !d.alarmActive) return false;
      if (dashboardAreaDeviceIds && !dashboardAreaDeviceIds.has(d.id)) return false;
      if (dashboardLocationFilter !== "all") {
        const label = deviceLocationLabel.get(d.id);
        if (label !== dashboardLocationFilter) return false;
      }
      if (dashboardRegionId !== "all") {
        const info = deviceTopologyInfo.get(d.id);
        if (dashboardRegionId === "unassigned") {
          // Topoloji bilgisi yok = hicbir hat'in segmentine baglanmamis cihaz
          if (info) return false;
        } else {
          if (!info || info.regionId !== dashboardRegionId) return false;
        }
      }
      if (dashboardLineId !== "all") {
        const info = deviceTopologyInfo.get(d.id);
        if (dashboardLineId === "unassigned") {
          if (info) return false;
        } else {
          if (!info || info.lineId !== dashboardLineId) return false;
        }
      }
      if (q) {
        const text = `${d.name} ${d.code}`.toLowerCase();
        if (!text.includes(q)) return false;
      }
      return true;
    });
  }, [
    gridSnapshot,
    devices,
    dashboardSearch,
    dashboardStatusFilter,
    dashboardAreaDeviceIds,
    dashboardLocationFilter,
    deviceLocationLabel,
    dashboardRegionId,
    dashboardLineId,
    deviceTopologyInfo
  ]);

  // Filtre çubuğu için ham sayım rozetleri (filtre uygulanmamış toplam).
  const dashboardCounts = useMemo(() => {
    const total = devices.length;
    const online = devices.filter((d) => d.communicationStatus === "online").length;
    const alarm = devices.filter((d) => d.alarmActive).length;
    return { total, online, offline: total - online, alarm };
  }, [devices]);

  // Anasayfa otomatik secim: hicbir cihaz secili degilse (selectedDeviceId=0)
  // veya secili cihaz mevcut filtrelenmis listede yoksa, listenin ilk elemanini
  // sec. Boylece anasayfa ilk acildiginda harita o cihaza yaklasir (DeviceMapTab
  // selectedDevice degisince flyTo cagiriyor) ve sidebar'da cihaz vurgulanir.
  // Liste bos olunca dokunulmaz (0 kalir, secim olmaz).
  useEffect(() => {
    if (filteredDashboardDevices.length === 0) return;
    const stillVisible = filteredDashboardDevices.some((d) => d.id === selectedDeviceId);
    if (!stillVisible) {
      setSelectedDeviceId(filteredDashboardDevices[0].id);
    }
  }, [filteredDashboardDevices, selectedDeviceId]);

  // LiveValuesPage'e geçilecek filtrelenmiş canlı değer satırları.
  const filteredDashboardLiveValues = useMemo(() => {
    const allowedIds = new Set(filteredDashboardDevices.map((d) => d.id));
    return signalLiveValues.filter((row) => allowedIds.has(row.device_id));
  }, [signalLiveValues, filteredDashboardDevices]);

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
    return <LoginForm onSubmit={handleLogin} loading={loadingLogin} />;
  }

  return (
    <div className="layout">
      <Header
        fullName={currentUser?.full_name ?? session.username}
        role={session.role}
        accessToken={session.accessToken}
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
                {t("engineering.nav.devices")}
              </button>
              {session.role === "engineer" || session.role === "installer" ? (
                <button
                  className={engineeringPage === "live-values" ? "active" : ""}
                  onClick={() => setEngineeringPage("live-values")}
                >
                  {t("engineering.nav.liveValues")}
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
                  {t("engineering.nav.users")}
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
                  {t("engineering.nav.responsibilityAreas")}
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
                    {t("engineering.nav.signals")}
                  </button>
                  <button
                    className={engineeringPage === "alarm-rules" ? "active" : ""}
                    onClick={() => {
                      setEngineeringPage("alarm-rules");
                      void reloadAlarmRules();
                      void reloadSignals();
                    }}
                  >
                    {t("engineering.nav.alarmRules")}
                  </button>
                  <button
                    className={engineeringPage === "outbound" ? "active" : ""}
                    onClick={() => setEngineeringPage("outbound")}
                  >
                    {t("engineering.nav.outboundTargets")}
                  </button>
                  <button
                    className={engineeringPage === "api-access" ? "active" : ""}
                    onClick={() => {
                      setEngineeringPage("api-access");
                      void reloadApiKeys();
                    }}
                  >
                    {t("engineering.nav.apiAccess")}
                  </button>
                  <button
                    className={engineeringPage === "notifications" ? "active" : ""}
                    onClick={() => {
                      setEngineeringPage("notifications");
                      void reloadNotificationSettings();
                    }}
                  >
                    {t("engineering.nav.notificationSettings")}
                  </button>
                  <button
                    className={engineeringPage === "project-settings" ? "active" : ""}
                    onClick={() => setEngineeringPage("project-settings")}
                  >
                    {t("engineering.nav.projectSettings")}
                  </button>
                  <button
                    className={engineeringPage === "grid" ? "active" : ""}
                    onClick={() => setEngineeringPage("grid")}
                  >
                    {t("engineering.nav.grid")}
                  </button>
                  <button
                    className={engineeringPage === "backups" ? "active" : ""}
                    onClick={() => setEngineeringPage("backups")}
                  >
                    {t("engineering.nav.backups")}
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
                onRefreshGatewayAll={handleRefreshGatewayAll}
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
                devices={devices}
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
                onFetchUserPrefs={(userId) =>
                  fetchUserNotificationPrefs(session.accessToken, userId)
                }
                onUpdateUserPrefs={(userId, payload) =>
                  updateUserNotificationPrefs(session.accessToken, userId, payload)
                }
              />
            ) : null}
            {engineeringPage === "responsibility-areas" &&
            (session.role === "engineer" || session.role === "installer") ? (
              <ResponsibilityAreasPage
                role={session.role}
                areas={responsibilityAreas}
                users={users}
                devices={devices}
                regions={gridSnapshot?.regions ?? []}
                lines={gridSnapshot?.lines ?? []}
                onLoadDetail={handleLoadAreaDetail}
                onCreate={handleCreateArea}
                onUpdate={handleUpdateArea}
                onDelete={handleDeleteArea}
                onAddUser={handleAddUserToArea}
                onRemoveUser={handleRemoveUserFromArea}
                onAddDevice={handleAddDeviceToArea}
                onRemoveDevice={handleRemoveDeviceFromArea}
                onAddRegion={handleAddRegionToArea}
                onRemoveRegion={handleRemoveRegionFromArea}
                onAddLine={handleAddLineToArea}
                onRemoveLine={handleRemoveLineFromArea}
              />
            ) : null}
            {engineeringPage === "outbound" && session.role === "installer" ? (
              <OutboundTargetsPanel
                targets={outboundTargets}
                devices={devices}
                onCreate={handleCreateOutboundTarget}
                onUpdate={handleUpdateOutboundTarget}
                onDelete={handleDeleteOutboundTarget}
                onDownloadIec104Points={handleDownloadIec104Points}
                onDownloadIec104Xlsx={handleDownloadIec104Xlsx}
                onUpdateDeviceCa={handleUpdateDeviceCa}
                onAutoAssignDeviceCa={handleAutoAssignDeviceCa}
                onFetchIec104Runtime={async (id) => {
                  if (!session) throw new Error("Oturum yok.");
                  return fetchIec104Runtime(session.accessToken, id);
                }}
              />
            ) : null}
            {engineeringPage === "api-access" && session.role === "installer" ? (
              <ApiAccessPanel
                apiBaseUrl={API_BASE_URL}
                keys={apiKeys}
                loading={apiKeysLoading}
                onRefresh={reloadApiKeys}
                onCreate={handleCreateApiKey}
                onRevoke={handleRevokeApiKey}
                onPurge={handlePurgeApiKey}
                onToggleActive={handleToggleApiKeyActive}
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
                onTestTelegram={handleTestNotificationTelegram}
                onDiscoverTelegramChats={handleDiscoverTelegramChats}
              />
            ) : null}
            {engineeringPage === "project-settings" && session.role === "installer" ? (
              <ProjectSettingsPanel onSave={handleSaveProjectSettings} />
            ) : null}
            {engineeringPage === "grid" &&
            (session.role === "engineer" || session.role === "installer") ? (
              <GridManagementPanel accessToken={session.accessToken} devices={devices} gridSnapshot={gridSnapshot} />
            ) : null}
            {engineeringPage === "backups" &&
            (session.role === "engineer" || session.role === "installer") ? (
              <BackupsPanel accessToken={session.accessToken} />
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
            {pageMode === "faults" ? (
              <FaultListPage
                faults={faults}
                stats={faultStats}
                users={users}
                currentUsername={session.username}
                canAssign={session.role === "engineer" || session.role === "installer"}
                loading={false}
                gridSnapshot={gridSnapshot}
                devices={devices}
                alarms={alarms}
                onAssign={handleAssignFault}
                onUpdateStatus={handleUpdateFaultStatus}
                onUpdateNote={handleUpdateFaultNote}
                onLoadComments={handleLoadFaultComments}
                onAddComment={handleAddFaultComment}
              />
            ) : null}
            {pageMode === "events" ? (
              <EventsPage events={events} loading={loadingData} devices={devices} />
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
          <div className="dashboard-shell">
            <DashboardFilterBar
              search={dashboardSearch}
              onSearchChange={setDashboardSearch}
              statusFilter={dashboardStatusFilter}
              onStatusFilterChange={setDashboardStatusFilter}
              areaId={dashboardAreaId}
              onAreaIdChange={setDashboardAreaId}
              responsibilityAreas={responsibilityAreas}
              regionId={dashboardRegionId}
              onRegionIdChange={setDashboardRegionId}
              lineId={dashboardLineId}
              onLineIdChange={setDashboardLineId}
              regions={gridSnapshot?.regions ?? []}
              lines={gridSnapshot?.lines ?? []}
              counts={dashboardCounts}
              visibleCount={filteredDashboardDevices.length}
              areaLoading={dashboardAreaLoading}
              sidebarCollapsed={sidebarCollapsed}
              onToggleSidebar={() => setSidebarCollapsed((prev) => !prev)}
              activeTab={activeTab}
              onActiveTabChange={setActiveTab}
            />
            <div className={`dashboard-body ${sidebarCollapsed ? "sidebar-collapsed" : ""}`}>
              {!sidebarCollapsed ? (
                <DeviceSidebar
                  devices={filteredDashboardDevices}
                  selectedId={selectedDeviceId}
                  onSelect={setSelectedDeviceId}
                  alarms={alarms}
                  liveValues={signalLiveValues}
                  deviceTopology={
                    new Map(
                      Array.from(deviceTopologyInfo.entries()).map(([id, v]) => [
                        id,
                        { regionName: v.regionName, lineName: v.lineName }
                      ])
                    )
                  }
                />
              ) : null}
              <main className={`content dashboard-content ${activeTab === "map" ? "map-active" : ""}`}>
                {activeTab === "map" ? (
                  <DeviceMapTab
                    devices={filteredDashboardDevices}
                    selectedDevice={selectedDevice}
                    onSelectDevice={setSelectedDeviceId}
                    liveValues={signalLiveValues}
                    gridSnapshot={gridSnapshot}
                    alarms={alarms}
                  />
                ) : null}
                {activeTab === "values" ? (
                  <GridOverviewPage
                    devices={filteredDashboardDevices}
                    alarms={alarms}
                    gridSnapshot={gridSnapshot}
                    onSelectDevice={setSelectedDeviceId}
                    selectedDeviceId={selectedDeviceId}
                  />
                ) : null}
              </main>
            </div>
          </div>
        )}
      </div>

      {settingsOpen ? (
        <div className="settings-modal-backdrop">
          <div className="settings-modal">
            <h3>{t("userSettings.title")}</h3>
            <label>
              {t("common.fullName")}
              <input value={settingsFullName} onChange={(event) => setSettingsFullName(event.target.value)} />
            </label>
            <label>
              {t("common.email")}
              <input value={settingsEmail} onChange={(event) => setSettingsEmail(event.target.value)} />
            </label>
            <label>
              {t("userSettings.language")}
              <select
                value={
                  isSupportedLanguage(currentUser?.language) ? (currentUser!.language as string) : "tr"
                }
                onChange={(event) => void handleChangeLanguage(event.target.value as SupportedLanguage)}
              >
                {SUPPORTED_LANGUAGES.map((code) => (
                  <option key={code} value={code}>
                    {LANGUAGE_LABELS[code]}
                  </option>
                ))}
              </select>
            </label>
            <label>
              {t("userSettings.currentPassword")}
              <input
                type="password"
                value={settingsCurrentPassword}
                onChange={(event) => setSettingsCurrentPassword(event.target.value)}
              />
            </label>
            <label>
              {t("userSettings.newPassword")}
              <input
                type="password"
                value={settingsNewPassword}
                onChange={(event) => setSettingsNewPassword(event.target.value)}
              />
            </label>
            {settingsError ? <p className="error-text">{settingsError}</p> : null}

            {/* Bildirim tercihleri — kanal bazli toggle. Kullanici burada
                kapatirsa sistem cap'inda etkin olsa bile bildirim almaz. */}
            {notifPrefs ? (
              <div className="notif-prefs-section">
                <h4>{t("settings.notifPrefs.title")}</h4>
                <div className="notif-prefs-row">
                  <div className="notif-prefs-row-label">
                    <strong>{t("settings.notifPrefs.web")}</strong>
                    <span>{t("settings.notifPrefs.webHint")}</span>
                  </div>
                  <button
                    type="button"
                    className={`notif-prefs-toggle ${notifPrefs.web_enabled ? "on" : ""}`}
                    onClick={() => void handleToggleNotifPref("web_enabled")}
                    disabled={notifPrefsSaving}
                    aria-label={t("settings.notifPrefs.web")}
                  />
                </div>
                <div className="notif-prefs-row">
                  <div className="notif-prefs-row-label">
                    <strong>{t("settings.notifPrefs.email")}</strong>
                    <span>
                      {t("settings.notifPrefs.emailHint")}
                      {currentUser?.email ? "" : t("settings.notifPrefs.emailMissing")}
                    </span>
                  </div>
                  <button
                    type="button"
                    className={`notif-prefs-toggle ${notifPrefs.email_enabled ? "on" : ""}`}
                    onClick={() => void handleToggleNotifPref("email_enabled")}
                    disabled={notifPrefsSaving}
                    aria-label={t("settings.notifPrefs.email")}
                  />
                </div>
                <div className="notif-prefs-row">
                  <div className="notif-prefs-row-label">
                    <strong>{t("settings.notifPrefs.sms")}</strong>
                    <span>
                      {t("settings.notifPrefs.smsHint")}
                      {currentUser?.phone_number ? "" : t("settings.notifPrefs.smsMissing")}
                    </span>
                  </div>
                  <button
                    type="button"
                    className={`notif-prefs-toggle ${notifPrefs.sms_enabled ? "on" : ""}`}
                    onClick={() => void handleToggleNotifPref("sms_enabled")}
                    disabled={notifPrefsSaving}
                    aria-label={t("settings.notifPrefs.sms")}
                  />
                </div>
              </div>
            ) : null}

            <div className="settings-actions">
              <button onClick={() => setSettingsOpen(false)}>{t("settings.actions.cancel")}</button>
              <button onClick={handleSaveSettings} disabled={settingsSaving}>
                {settingsSaving ? t("settings.actions.saving") : t("settings.actions.save")}
              </button>
            </div>
          </div>
        </div>
      ) : null}

      <GlobalLoading
        show={loadingData || alarmsLoading || dashboardAreaLoading}
      />
    </div>
  );
}
