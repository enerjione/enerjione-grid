import { lazy, Suspense, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { asyncConfirm } from "../components/ConfirmDialog";
import { useTranslation } from "react-i18next";
import { ChangePasswordModal } from "../components/ChangePasswordModal";
import { Header } from "../components/Header";
import { useToast } from "../components/ToastProvider";
import { LoginForm } from "../features/auth/LoginForm";

import { DeviceSidebar } from "../features/devices/DeviceSidebar";

import { DeviceMapTab } from "../features/map/DeviceMapTab";
import { DashboardFilterBar, type StatusFilter } from "../features/dashboard/DashboardFilterBar";
import { TabBar } from "../features/tabs/TabBar";
import { EngineeringNav } from "../features/tabs/EngineeringNav";

import { routeToPageState, type PageMode, type EngineeringPage } from "../features/tabs/tabModel";

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

import {
  changeMyPassword,
  clearSession,
  createAlarmRule,
  createGateway,
  createDevice,
  createSignal,
  createUser,
  inviteUser,
  resendInvite,
  deleteAlarmRule,
  deleteDevice,
  deleteGateway,
  refreshGatewayAllDevices,
  sendDeviceCommand,
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
  fetchLicenseGate,
  fetchLicenseStatus,
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
  fetchWhatsappWebStatus,
  fetchWhatsappWebQr,
  fetchWhatsappWebGroups,
  testWhatsappWeb,
  logoutWhatsappWeb,
  updateNotificationSettings as updateNotificationSettingsApi,
  updateUser,
  updateMyProfile,
  updateMyLanguage,
  API_BASE_URL
} from "../shared/api";
import { useLiveValuesSocket } from "../shared/useLiveValuesSocket";
import { usePolling } from "../shared/usePolling";
import { useTabs } from "../features/tabs/useTabs";
// Uzaktan bakim izni ACIKKEN header'da duran uyari rozeti. Sayfanin kendisi
// lazy; rozet oturum acilir acilmaz gerektigi icin bu kucuk modul eager.
import { formatRemaining, useRemoteAccessBadge } from "../features/remote-access/remoteAccessShared";

// --- Tembel yuklenen sayfalar --------------------------------------
// Muhendislik sayfalari ilk yuklemede GELMEZ; kullanici o sekmeyi acinca
// indirilir. Onceden hepsi tek bundle'daydi (2.1 MB) ve panoya bakan bir
// kullanici bile Modbus plan ekranini, grafik kutuphanesini indiriyordu.
// Named export olduklari icin .then ile default'a cevriliyor.
const ActiveSessionsPage = lazy(() => import("../features/sessions/ActiveSessionsPage").then((m) => ({ default: m.ActiveSessionsPage })));
const AlarmRulesPage = lazy(() => import("../features/alarm-rules/AlarmRulesPage").then((m) => ({ default: m.AlarmRulesPage })));
const AlarmsPage = lazy(() => import("../features/alarms/AlarmsPage").then((m) => ({ default: m.AlarmsPage })));
const ApiAccessPanel = lazy(() => import("../features/api-access/ApiAccessPanel").then((m) => ({ default: m.ApiAccessPanel })));
const BackupsPanel = lazy(() => import("../features/backups/BackupsPanel").then((m) => ({ default: m.BackupsPanel })));
const BulkNotificationPage = lazy(() => import("../features/bulk-notify/BulkNotificationPage").then((m) => ({ default: m.BulkNotificationPage })));
const DeviceDetailPage = lazy(() => import("../features/device-detail/DeviceDetailPage").then((m) => ({ default: m.DeviceDetailPage })));
const DeviceManagementPanel = lazy(() => import("../features/devices/DeviceManagementPanel").then((m) => ({ default: m.DeviceManagementPanel })));
const EventsPage = lazy(() => import("../features/events/EventsPage").then((m) => ({ default: m.EventsPage })));
const FaultListPage = lazy(() => import("../features/faults/FaultListPage").then((m) => ({ default: m.FaultListPage })));
const GridManagementPanel = lazy(() => import("../features/grid/GridManagementPanel").then((m) => ({ default: m.GridManagementPanel })));
const LicenseManagementPanel = lazy(() => import("../features/license/LicenseManagementPanel").then((m) => ({ default: m.LicenseManagementPanel })));
const LiveValuesPage = lazy(() => import("../features/live-values/LiveValuesPage").then((m) => ({ default: m.LiveValuesPage })));
const NetworkSettingsPage = lazy(() => import("../features/network/NetworkSettingsPage").then((m) => ({ default: m.NetworkSettingsPage })));
const OfflineMapPage = lazy(() => import("../features/map/OfflineMapPage").then((m) => ({ default: m.OfflineMapPage })));
const NotificationSettingsPanel = lazy(() => import("../features/settings/NotificationSettingsPanel").then((m) => ({ default: m.NotificationSettingsPanel })));
const OutboundTargetsPanel = lazy(() => import("../features/outbound/OutboundTargetsPanel").then((m) => ({ default: m.OutboundTargetsPanel })));
const ProjectSettingsPanel = lazy(() => import("../features/settings/ProjectSettingsPanel").then((m) => ({ default: m.ProjectSettingsPanel })));
const RemoteAccessPage = lazy(() => import("../features/remote-access/RemoteAccessPage").then((m) => ({ default: m.RemoteAccessPage })));
const ResponsibilityAreasPage = lazy(() => import("../features/responsibility-areas/ResponsibilityAreasPage").then((m) => ({ default: m.ResponsibilityAreasPage })));
const SignalsPage = lazy(() => import("../features/signals/SignalsPage").then((m) => ({ default: m.SignalsPage })));
const SystemStatusPage = lazy(() => import("../features/system-status/SystemStatusPage").then((m) => ({ default: m.SystemStatusPage })));
const UserManagementPanel = lazy(() => import("../features/auth/UserManagementPanel").then((m) => ({ default: m.UserManagementPanel })));
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
  LicenseStatus,
  NotificationSettings,
  OutboundTarget,
  ResponsibilityAreaRow,
  SignalCatalogRow,
  SignalLiveRow,
  SystemEvent,
  UserRead,
  UserRole
} from "../shared/types";

// PageMode / EngineeringPage tipleri tabModel'den geliyor (tek kaynak). Sekme
// sistemi bunlari uretir; App aktif sekmeden turetir.

// Kullaniciyi lisans sayfasina kilitleyen lisans durumlari: uclunun ortak
// yani "bu makinede kullanilabilir lisans yok". `machine_unavailable`
// BILEREK disarida — o bir lisans durumu degil, sunucu tarafi arizasidir.
// Gerekce icin bkz. App icindeki `licenseGateActive`.
// Kullaniciyi lisans sayfasina KILITLEYEN tek durum: bu kuruluma HIC lisans
// yuklenmemis olmasi. Digerlerinde sistem calismaya devam eder:
//   invalid / machine_mismatch : lisans VAR ama bu makinede gecerli degil.
//     Sahada calisan bir SCADA'yi tamamen kapatmak izlemeyi de durdurur —
//     ariza takibi lisans sorunundan daha kritiktir.
//   quota full / over_limit    : CIHAZ SINIRI dolmus. Lisanslarda sure siniri
//     YOKTUR, yalnizca cihaz sayisi sinirlidir. Sistem calisir; sadece yeni
//     cihaz ekleme reddedilir (backend 403 doner, arayuz toast gosterir).
// `machine_unavailable` bir lisans durumu degil, sunucu tarafi arizasidir.
// NOT: Bu kume backend'deki `license_service.ENFORCED_STATES` ile AYNI olmak
// zorunda. Biri degisirse digeri de degismeli — yoksa arayuz kilitli
// gorunurken API acik (veya tersi) kalir.
const LICENSE_GATE_STATES: ReadonlySet<LicenseStatus["state"]> = new Set([
  "unlicensed",
]);

// Lisans durumu sorgusunun sonucu.
//   "checking" : henuz cevap yok (yalnizca HIC lisans gorulmemis kurulumda)
//   "open"     : lisans var, sistem serbest
//   "locked"   : lisans yok -> icerik RENDER EDILMEZ
//   "unknown"  : sorgu 3 denemede basarisiz VE daha once hic gecerli lisans
//                gorulmemis -> fail-closed
type LicenseGatePhase = "checking" | "open" | "locked" | "unknown";

// --- Lisans kilidi bir ILK KURULUM kontroludur ------------------------------
// Amac: yazilimi izinsiz indirip kuran birinin sisteme girememesi. Bizim
// kurdugumuz sahalarda lisans aktiflestirilir ve sistem acilir; ondan SONRA
// kullaniciyi bir daha rahatsiz etmemeli — gecici bir ag hatasi yuzunden
// "lisansiniz yok" demek yanlis alarmdir.
//
// Bu yuzden bir kez "lisans gecerli" cevabi alindiginda isaretliyoruz:
//   - acilista iyimser davran (kilit ekrani/bekleme hic gosterme),
//   - sonraki sorgu hatalarinda KILITLEME.
//
// GUVENLIK bu bayraga DAYANMAZ. Gercek zorlama backend'dedir
// (apps/backend-api/app/core/license_gate.py): lisanssiz kurulumda API zaten
// 403 doner, bayragi elle set etmek bos bir arayuzden baskasini vermez.
const LICENSE_ACTIVATED_KEY = "e1_license_activated";

function hasLicenseBeenActivated(): boolean {
  try {
    return window.localStorage.getItem(LICENSE_ACTIVATED_KEY) === "1";
  } catch {
    return false;
  }
}

function setLicenseActivatedFlag(activated: boolean): void {
  try {
    if (activated) window.localStorage.setItem(LICENSE_ACTIVATED_KEY, "1");
    else window.localStorage.removeItem(LICENSE_ACTIVATED_KEY);
  } catch {
    // localStorage kapali (gizli sekme/politika) — bayrak olmadan da calisir,
    // sadece acilista kisa bir bekleme gorunur.
  }
}

/** Anasayfanin GERCEKTEN okudugu sinyaller.
 *
 *  Harita sidebar'i uc batarya gerilimi + modem RSSI gosteriyor; baska bir
 *  sey okumuyor. Katalog 193 sinyal oldugu icin daraltma yaniti ~48 KAT
 *  kucultuyor.
 *
 *  DIKKAT: harita DETAY MODALI cok daha fazla sinyal okur (26 sonek x 3
 *  kaynak). Onun icin listeyi buyutmuyoruz — modal yalnizca SECILI cihaz
 *  icin acildigindan, o cihazin TAMAMI ayrica cekiliyor (bkz.
 *  `fetchLiveValuesForScope`). Boylece burasi kisa kalir ve modal yeni bir
 *  sinyal okumaya basladiginda burayi guncellemeyi unutma riski OLMAZ. */
const DASHBOARD_SIGNAL_KEYS = [
  "master.battery_voltage_satellite",
  "sat01.battery_voltage_satellite",
  "sat02.battery_voltage_satellite",
  "master.modem_rssi"
] as const;

type LiveValuesScope =
  | { kind: "none" }
  | { kind: "dashboard"; selectedDeviceCode?: string }
  | { kind: "device"; deviceCode: string }
  | { kind: "all" };

/** Kapsama gore canli degerleri ceker.
 *
 *  Anasayfada IKI istek yapilir ve birlestirilir:
 *    1) tum cihazlar x dar sinyal seti  (harita/sidebar)
 *    2) secili cihaz  x tum sinyaller   (detay modali)
 *  Ikinci istek yalnizca bir cihaz secili ise yapilir ve 193 satir doner —
 *  yani 115.800 yerine ~2.600 satir.
 */
async function fetchLiveValuesForScope(
  token: string,
  scope: LiveValuesScope
): Promise<SignalLiveRow[]> {
  if (scope.kind === "none") return [];
  if (scope.kind === "all") return fetchSignalLiveValues(token);
  if (scope.kind === "device") {
    return fetchSignalLiveValues(token, [scope.deviceCode]);
  }

  const dar = await fetchSignalLiveValues(token, undefined, DASHBOARD_SIGNAL_KEYS);
  if (!scope.selectedDeviceCode) return dar;

  let seciliTam: SignalLiveRow[] = [];
  try {
    seciliTam = await fetchSignalLiveValues(token, [scope.selectedDeviceCode]);
  } catch {
    // Secili cihazin ayrintisi alinamadi — harita yine de calismali.
    return dar;
  }

  // Birlestir: secili cihazin satirlari KAZANIR (daha genis kume).
  const anahtar = (r: SignalLiveRow) => `${r.device_code} ${r.signal_key}`;
  const secilenler = new Set(seciliTam.map(anahtar));
  return [...dar.filter((r) => !secilenler.has(anahtar(r))), ...seciliTam];
}

export function App() {
  const projectSettings = useProjectSettings();
  const [session, setSession] = useState<AuthSession | null>(() => loadSession());
  const [devices, setDevices] = useState<DeviceRow[]>([]);
  const [users, setUsers] = useState<UserRead[]>([]);
  const [alarms, setAlarms] = useState<AlarmEvent[]>([]);
  // Toast icin gorulmus (aktif) alarm ID seti. null = ilk yukleme yapilmadi.
  const seenAlarmIdsRef = useRef<Set<number> | null>(null);
  const [faults, setFaults] = useState<FaultEvent[]>([]);
  // Ilk cekim tamamlanana kadar `true` — aksi halde sayfa acilir acilmaz
  // (henuz veri yokken) yesil "Sistem temiz" gorunurdu.
  const [faultsLoading, setFaultsLoading] = useState(true);
  const [faultsError, setFaultsError] = useState("");
  const [faultStats, setFaultStats] = useState<FaultStats | null>(null);
  const [events, setEvents] = useState<SystemEvent[]>([]);
  const [gateways, setGateways] = useState<Gateway[]>([]);
  const [deviceInventoryError, setDeviceInventoryError] = useState("");
  /** Cihazlar sekmesinde listelenen gateway (kapsam); yenileme ve yoklama bunu kullanır */
  const [devicePanelGatewayCode, setDevicePanelGatewayCode] = useState<string>("");
  const [outboundTargets, setOutboundTargets] = useState<OutboundTarget[]>([]);
  // Public API icin kullanici PAT'lari. ApiAccessPanel kullanir.
  const [apiKeys, setApiKeys] = useState<ApiKey[]>([]);
  const [apiKeysLoading, setApiKeysLoading] = useState(false);
  const [gridSnapshot, setGridSnapshot] = useState<GridSnapshot | null>(null);
  const [alarmsLoading, setAlarmsLoading] = useState(false);
  const [currentUser, setCurrentUser] = useState<UserRead | null>(null);
  const [licenseStatus, setLicenseStatus] = useState<LicenseStatus | null>(null);
  const [licenseLoading, setLicenseLoading] = useState(false);
  // Kilit karari BU state uzerinden verilir, `licenseStatus` uzerinden DEGIL:
  // licenseStatus yalnizca engineer/installer'a acik olan detayli uctan gelir,
  // kilit ise her rol icin gecerli olmali.
  // Lisansi bir kez aktiflestirilmis kurulumda IYIMSER basla: bekleme ekrani
  // bile gosterme. Yanlis olsa dahi zararsiz — backend lisanssizsa API'yi
  // zaten kapatiyor ve gate cevabi gelince "locked"a duseriz.
  const [licenseGatePhase, setLicenseGatePhase] = useState<LicenseGatePhase>(() =>
    hasLicenseBeenActivated() ? "open" : "checking"
  );

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

  // Chrome tarzi sekme sistemi. Acik sekmeler + aktif sekme burada; pageMode/
  // engineeringPage aktif sekmeden TURETILIR (asagida). Session yokken login
  // ekrani render edilir ama hook kurali geregi kosulsuz cagrilir — rol yoksa
  // "operator" guvenli default.
  const tabsApi = useTabs(session?.role ?? "operator");
  const { pageMode: derivedPageMode, engineeringPage: derivedEngineeringPage } =
    routeToPageState(tabsApi.activeRoute);
  // App'in geri kalani bu iki turetilmis degeri kullanir (render zinciri aynen
  // korunur). device-detail durumunda pageMode "device-detail" olur.
  const pageMode = derivedPageMode;
  const engineeringPage: EngineeringPage = derivedEngineeringPage;
  const activeDeviceDetailId =
    tabsApi.activeRoute.kind === "device-detail"
      ? tabsApi.activeRoute.deviceId
      : null;

  // Uzaktan bakim izni acikken HER SAYFADA gorunen header rozeti icin.
  // AnyDesk mantiginda en buyuk risk "acik unutmak"; rozet bunu aktif olarak
  // engeller. Hook kosulsuz cagrilir (hook kurali) — token/rol uygun degilse
  // kendisi hic istek atmaz, appliance olmayan kurulumda ilk cevapta susar.
  const remoteAccessBadge = useRemoteAccessBadge(
    session?.accessToken ?? "",
    session?.role
  );

  // Ana sayfa (dashboard) ortak filtre state'i — Harita ve Tablo aynı filtreyi paylaşır.
  const [dashboardSearch, setDashboardSearch] = useState("");
  const [dashboardStatusFilter, setDashboardStatusFilter] = useState<StatusFilter>("all");
  const [dashboardAreaId, setDashboardAreaId] = useState<number | "all">("all");
  // Secili ekibin ham kapsam bilgisi: dogrudan atanan cihazlar + atanan
  // bolge/hat id'leri. Cihaz gorunurlugu sadece dogrudan cihazlarla degil,
  // ekibe atanan bolge/hatlarin uzerindeki cihazlarla da genisler (backend
  // get_visible_device_ids ile simetrik). null = ekip secili degil ("Tumu").
  const [dashboardAreaScope, setDashboardAreaScope] = useState<
    { deviceIds: number[]; regionIds: number[]; lineIds: number[] } | null
  >(null);
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

  // Hat Agaci'nda "goz" ile gizlenen hatlar. Cihazlar listede KALIR; haritada
  // hat/direk/marker griye doner. Operator kalabalik haritayi sadelestirmek
  // icin kullanir, secim oturumlar arasi korunur.
  const [hiddenLineIds, setHiddenLineIds] = useState<Set<number>>(() => {
    if (typeof window === "undefined") return new Set();
    try {
      const raw = window.localStorage.getItem("e1.dashboard.hidden-lines");
      if (!raw) return new Set();
      const parsed: unknown = JSON.parse(raw);
      return Array.isArray(parsed)
        ? new Set(parsed.filter((v): v is number => typeof v === "number"))
        : new Set();
    } catch {
      return new Set();
    }
  });

  useEffect(() => {
    try {
      window.localStorage.setItem(
        "e1.dashboard.hidden-lines",
        JSON.stringify(Array.from(hiddenLineIds))
      );
    } catch {
      // localStorage kapali/quota — gizleme oturumluk kalir, sorun degil.
    }
  }, [hiddenLineIds]);

  const handleToggleLineHidden = useCallback((lineId: number) => {
    setHiddenLineIds((prev) => {
      const next = new Set(prev);
      if (next.has(lineId)) next.delete(lineId);
      else next.add(lineId);
      return next;
    });
  }, []);

  useEffect(() => {
    window.localStorage.setItem(
      "hsl.dashboard.sidebar-collapsed",
      sidebarCollapsed ? "1" : "0"
    );
  }, [sidebarCollapsed]);

  // Sorumluluk alani secimi degistiginde o alanin cihaz id setini cek.
  useEffect(() => {
    if (dashboardAreaId === "all" || !session) {
      setDashboardAreaScope(null);
      return;
    }
    let cancelled = false;
    setDashboardAreaLoading(true);
    void fetchResponsibilityAreaDetail(session.accessToken, dashboardAreaId)
      .then((detail) => {
        if (cancelled) return;
        // Dogrudan cihazlar + atanan bolge/hat id'leri. Bolge/hat -> cihaz
        // donusumu deviceTopologyInfo ile asagidaki useMemo'da yapilir.
        setDashboardAreaScope({
          deviceIds: detail.devices.map((d) => d.id),
          regionIds: (detail.regions ?? []).map((r) => r.id),
          lineIds: (detail.lines ?? []).map((l) => l.id),
        });
      })
      .catch(() => {
        if (cancelled) return;
        setDashboardAreaScope({ deviceIds: [], regionIds: [], lineIds: [] });
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

  // --- CANLI DEGER KAPSAMI ---------------------------------------------
  //
  // Eskiden HER sayfa icin TUM kartezyen cekiliyordu: 600 cihaz x 193 sinyal
  // = 115.800 satir. Anasayfa bunu `device_codes` VERMEDEN istiyor ve WS
  // koptugunda periyot 30 sn'den 5 sn'ye dusuyordu — yani baglanti
  // bozuldugunda yuk 6 KATLANIYORDU.
  //
  // Artik yalnizca ACIK OLAN SAYFANIN gordugu kadari cekilir:
  //
  //   anasayfa      -> tum cihazlar x DORT sinyal (harita sidebar'i)
  //                    + secili cihazin TAMAMI (detay modali onu okur)
  //   cihaz detayi  -> yalnizca o cihaz, tum sinyalleri
  //   canli degerler-> tam kartezyen (sayfanin isi bu)
  //   digerleri     -> hic cekilmez
  // Cihaz KODLARI ayri memo'larda tutuluyor — `liveScope` dogrudan `devices`
  // dizisine baglansaydi, cihaz listesi her yenilendiginde (kendi polling'i
  // var) yeni bir dizi kimligi olusur, kapsam nesnesi degisir ve canli deger
  // cekimi GEREKSIZ YERE tetiklenirdi. Kodlar STRING oldugu icin deger ayni
  // kaldigi surece kimlik de sabit kalir.
  const selectedDeviceCode = useMemo(
    () => devices.find((d) => d.id === selectedDeviceId)?.code,
    [devices, selectedDeviceId]
  );
  const detailDeviceCode = useMemo(
    () =>
      activeDeviceDetailId === null
        ? undefined
        : devices.find((d) => d.id === activeDeviceDetailId)?.code,
    [devices, activeDeviceDetailId]
  );

  const liveScope = useMemo<LiveValuesScope>(() => {
    if (pageMode === "home") {
      return { kind: "dashboard", selectedDeviceCode };
    }
    if (pageMode === "device-detail") {
      return detailDeviceCode ? { kind: "device", deviceCode: detailDeviceCode } : { kind: "none" };
    }
    if (pageMode === "engineering" && engineeringPage === "live-values") {
      return { kind: "all" };
    }
    return { kind: "none" };
  }, [pageMode, engineeringPage, selectedDeviceCode, detailDeviceCode]);

  // Canli degerleri GERCEKTEN tuketen sayfalar. Baska bir sekmede (Alarmlar,
  // Olaylar, Ayarlar, ...) WS'i acik tutmak bedavaya cihaz x sinyal akisini
  // tarayiciya tasimak demekti: 600 cihazda kullanicinin bakmadigi bir ekran
  // icin saniyede yuzlerce mesaj islenmesi. Sekme degisince WS kapanir,
  // geri donunce ~100ms'de (ticket + handshake) yeniden baglanir.
  const liveValuesNeeded =
    pageMode === "home" ||
    pageMode === "device-detail" ||
    (pageMode === "engineering" && engineeringPage === "live-values");

  // WebSocket-based canli telemetri akisi. Polling fallback ile birlikte
  // calisir; WS bagli iken ~200ms gecikme. Bagi kopunca polling devam eder.
  const liveSocket = useLiveValuesSocket({
    token: session?.accessToken ?? "",
    apiBaseUrl: API_BASE_URL,
    enabled: Boolean(session?.accessToken) && liveValuesNeeded
  });

  // WS mesajlari hook icinde 250ms tamponlanip BATCH olarak gelir; tek
  // setState + satir dizisinde tek gecis. Mesaj basina render yok.
  useEffect(() => {
    liveSocket.registerHandler((msgs) => {
      setSignalLiveValues((prev) => liveSocket.applyMessages(prev, msgs));
    });
  }, [liveSocket]);

  useEffect(() => {
    const load = async () => {
      if (!session) return;
      // Lisans kilidi acik degilse veri CEKME. Backend lisanssiz kurulumda bu
      // uclarin hepsini 403'le reddediyor (core/license_gate.py); istekleri
      // yine de atmak sadece hata gurultusu uretir. "checking" fazinda da
      // beklenir — kilit karari verilmeden veri istemeyiz.
      if (licenseGatePhase !== "open") return;
      setSignalLiveValues([]);
      setSignalLiveError("");
      setLoadingData(true);
      try {
        const me = await fetchMe(session.accessToken);
        setCurrentUser(me);
        setSettingsFullName(me.full_name);
        setSettingsEmail(me.email);
        try {
          const [loadedDevices, gatewayRows] = await Promise.all([
            fetchDevices(session.accessToken),
            fetchGateways(session.accessToken)
          ]);
          setDevices(loadedDevices);
          setGateways(gatewayRows);
          setDeviceInventoryError("");
          setDevicePanelGatewayCode((current) =>
            current && gatewayRows.some((gateway) => gateway.code === current)
              ? current
              : (gatewayRows[0]?.code ?? "")
          );
        } catch (error) {
          setDeviceInventoryError(
            error instanceof Error ? error.message : t("common.errorOccurred")
          );
        }
        setAlarmsLoading(true);
        try {
          setAlarms(await fetchAlarmEvents(session.accessToken));
        } catch {
          setAlarms([]);
        }
        try {
          setEvents(await fetchSystemEvents(session.accessToken));
        } catch {
          setEvents([]);
        }
        // NOT: lisans durumu burada YUKLENMEZ — kendi efektinde yuklenir.
        // Bu dizideki herhangi bir istek patlayinca ardindaki her sey atlanir;
        // lisans durumu buraya bagli oldugunda `null` kalip lisans kilidini
        // sessizce devre disi birakiyordu.
        if (session.role === "engineer" || session.role === "installer") {
          try {
            setUsers(await fetchUsers(session.accessToken));
          } catch {
            // Kullanici listesi alinamazsa acilisin GERISI devam etmeli.
            setUsers([]);
          }
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
    // licenseGatePhase bagimliliktir: kilit acilinca (lisans yuklendiginde
    // veya acilis kontrolu bitince) veri yuklemesi bir kez calissin.
  }, [session, licenseGatePhase]);

  // ---- Lisans durumu: BAGIMSIZ yukleme -----------------------------------
  // Kendi efektinde duruyor cunku lisans kilidi (bkz. `licenseGateActive`)
  // buna bagli: buyuk acilis dizisinin icinde oldugunda, ondan onceki
  // herhangi bir istegin patlamasi lisans durumunu `null` birakiyor ve kilit
  // sessizce ACILMIYORDU. Artik hicbir sey bu istegi engelleyemez.
  //
  // Gecici ag hatasinda birkac kez tekrar denenir: tek seferlik bir "Failed
  // to fetch" yuzunden lisansi olmayan bir sistem kilitsiz kalmamali.
  useEffect(() => {
    if (!session) return;
    let cancelled = false;
    const token = session.accessToken;
    const isLicenseAdmin =
      session.role === "engineer" || session.role === "installer";
    // Lisansi aktiflestirilmis kurulumda "checking"e GERI DUSME — kullanici
    // her girisde bir bekleme/kilit ekrani gormemeli.
    if (!hasLicenseBeenActivated()) setLicenseGatePhase("checking");
    // Kilit karari netlestiginde tek yerden uygula: acik ise kurulumu
    // "aktiflestirilmis" olarak isaretle, kilitli ise isareti kaldir.
    const applyGate = (locked: boolean) => {
      setLicenseActivatedFlag(!locked);
      setLicenseGatePhase(locked ? "locked" : "open");
    };
    const load = async () => {
      for (let attempt = 0; attempt < 3; attempt += 1) {
        if (cancelled) return;
        try {
          // Kilit karari her rol icin ayni uctan: /license/gate.
          const gate = await fetchLicenseGate(token);
          if (cancelled) return;
          if (gate === null) {
            // Backend /license/gate'i tanimiyor (eski surum). Lisansi OLAN bir
            // sistemi surum uyusmazligi yuzunden kilitlemeyiz: eski yola dus.
            if (isLicenseAdmin) {
              const status = await fetchLicenseStatus(token);
              if (cancelled) return;
              setLicenseStatus(status);
              applyGate(LICENSE_GATE_STATES.has(status.state));
            } else {
              // Eski backend bu role kilit bilgisi veremiyor -> eski davranis.
              setLicenseStatus(null);
              setLicenseGatePhase("open");
            }
            return;
          }
          applyGate(gate.locked);
          // Detayli durum (musteri, kota, limit) yalnizca lisans yonetebilen
          // roller icin; basarisiz olmasi kilidi ETKILEMEZ.
          if (isLicenseAdmin) {
            try {
              const status = await fetchLicenseStatus(token);
              if (!cancelled) setLicenseStatus(status);
            } catch {
              if (!cancelled) setLicenseStatus(null);
            }
          } else {
            setLicenseStatus(null);
          }
          return;
        } catch (error) {
          if (attempt === 2) {
            // Ucunde de basarisiz. Ne yapacagimiz kurulumun GECMISINE bagli:
            //
            //   Lisans daha once aktiflestirilmis -> KILITLEME. Calisan bir
            //     sahayi gecici bir ag hatasi yuzunden "lisansiniz yok" diye
            //     durdurmak yanlis alarm; kaldi ki lisans dosyasi silinmiyor.
            //   Hic lisans gorulmemis (ilk kurulum) -> fail-closed. Korumanin
            //     asil hedefi bu: yazilimi izinsiz kuran biri istegi
            //     bloklayarak kilidi atlayamasin.
            //
            // Her iki durumda da son soz backend'in: lisanssizsa API 403
            // dondugu icin arayuz zaten bos kalir.
            const previouslyActivated = hasLicenseBeenActivated();
            console.warn(
              "[lisans] /license/gate okunamadi (3 deneme) — " +
                (previouslyActivated
                  ? "kurulum daha once aktiflestirilmis, kilit UYGULANMIYOR."
                  : "hic lisans gorulmemis, sistem KILITLI kabul edilir.") +
                " Sebep:",
              error
            );
            if (!cancelled) {
              setLicenseGatePhase(previouslyActivated ? "open" : "unknown");
              setLicenseStatus(null);
            }
            return;
          }
          await new Promise((resolve) => window.setTimeout(resolve, 1500));
        }
      }
    };
    void load();
    return () => {
      cancelled = true;
    };
  }, [session]);

  // Rol bazli sekme gorunurlugu artik useTabs icinde (visibleTabs / canAccessRoute
  // -> tabModel.ts) merkezi olarak yonetiliyor: rol degisince izinsiz sekmeler
  // elenir ve aktif sekme home'a duser. Eski manuel guard effect'i kaldirildi.

  const handleLogin = async (username: string, password: string, remember: boolean) => {
    setLoadingLogin(true);
    try {
      const nextSession = await login(username, password);
      saveSession(nextSession, remember);
      setSession(nextSession);
      tabsApi.openTab({ kind: "page", page: "home" });
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
    setLicenseStatus(null);
    setDevices([]);
    setUsers([]);
    setAlarms([]);
    seenAlarmIdsRef.current = null; // yeni oturumda alarm toast durumu sifir
    setFaults([]);
    setEvents([]);
    setGateways([]);
    setDeviceInventoryError("");
    setDevicePanelGatewayCode("");
    setOutboundTargets([]);
    setNotificationSettings(null);
    setSignalCatalog([]);
    setSignalLiveValues([]);
    setSignalLiveError("");
    setAlarmRules([]);
    tabsApi.openTab({ kind: "page", page: "home" });
  }, [session, tabsApi]);

  // ===== Sekme navigasyon yardimcilari =====
  // Header ust menusu: "engineering" gelirse son acik engineering sekmesine
  // don, yoksa devices ile ac. Diger page'ler dogrudan page sekmesi acar.
  const openTab = tabsApi.openTab;
  const handleChangePage = useCallback(
    (page: PageMode) => {
      if (page === "engineering") {
        const existingEng = tabsApi.tabs.find((tab) => tab.route.kind === "engineering");
        if (existingEng) {
          tabsApi.activateTab(existingEng.key);
        } else {
          openTab({ kind: "engineering", page: "devices" });
        }
        return;
      }
      openTab({ kind: "page", page });
    },
    [openTab, tabsApi]
  );
  // Engineering alt sayfasi ac (sekme).
  const openEng = useCallback(
    (page: EngineeringPage) => openTab({ kind: "engineering", page }),
    [openTab]
  );
  // Cihaz detay sekmesi ac (harita popup / sidebar "tum detaylar").
  const openDeviceDetail = useCallback(
    (deviceId: number) => openTab({ kind: "device-detail", deviceId }),
    [openTab]
  );

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
  // urettiginde kullanici sayfayi yenilemeden anlik gorebilsin. Yeni (onceden
  // gorulmemis, aktif) alarm gelince toast bildirimi at.
  //
  // Sayfa gecidi YOK (enabled: session var mi): alarm toast'i hangi sayfada
  // olursak olalim gorunmeli — operator alarmi kacirmasin. Yalnizca sekme
  // arka plandayken durur (usePolling).
  const pollAlarms = useCallback(async () => {
    if (!session) return;
    const notifyNew = (rows: AlarmEvent[]) => {
      const active = rows.filter((a) => !a.reset);
      // Ilk yukleme: mevcut alarmlar icin toast ATMA, sadece set'i doldur.
      if (seenAlarmIdsRef.current === null) {
        seenAlarmIdsRef.current = new Set(active.map((a) => a.id));
        return;
      }
      const seen = seenAlarmIdsRef.current;
      const fresh = active.filter((a) => !seen.has(a.id));
      for (const a of active) seen.add(a.id);
      if (fresh.length === 0) return;
      const deviceName = (id: number) => devices.find((d) => d.id === id)?.name ?? `#${id}`;
      // Alarm toast'i tiklanabilir: dogrudan Alarmlar sekmesini acar. Operator
      // bildirimi gorup ne yapacagini aramak zorunda kalmasin.
      // `spontaneous`: bu bildirim kullanicinin bir eylemine cevap DEGIL,
      // kendiliginden geliyor. Proje ayarindan susturulabilen TEK toast
      // sinifi budur. Susturulsa bile alarm kaybolmaz — bildirim caninda,
      // Alarmlar sayfasinda ve e-posta/SMS/Telegram/push kanallarinda kalir.
      //
      // KRITIK/ERROR SEVIYESI SUSTURMAYI DELER (`spontaneous` verilmez).
      // Susturma KURULUM GENELI: bir kisinin "cok rahatsiz ediyor" diye
      // actigi ayar, bundan haberi olmayan kontrol odasi operatorunun
      // ekranindan da kritik ariza bildirimini kaldirirdi. Can sikan hacim
      // info/warning kuyrugundan geliyor; kritigi de susturmak bu urunun
      // varlik sebebini (arizayi hizla one cikarmak) iptal eder. Zil rozeti
      // 30 sn'de bir guncellenen kucuk bir sayidir, kritik icin yeterli
      // degildir.
      const alarmAction = {
        onAction: () => openTab({ kind: "page", page: "alarms" }),
        actionLabel: t("toasts.goToAlarms")
      };
      // Susturulabilir varyant — yalnizca info/warning icin.
      const susturulabilir = { ...alarmAction, spontaneous: true };
      if (fresh.length === 1) {
        const a = fresh[0];
        const lvl = a.level.toLowerCase();
        const kritik = lvl === "critical" || lvl === "error";
        const opts = {
          title: `${a.title} · ${deviceName(a.device_id)}`,
          ...(kritik ? alarmAction : susturulabilir)
        };
        if (kritik) toast.error(a.description || a.title, opts);
        else if (lvl === "warning") toast.warning(a.description || a.title, opts);
        else toast.info(a.description || a.title, opts);
      } else {
        // Coklu: tek ozet toast (en yuksek seviyeye gore renk). Paketin
        // icinde bir kritik varsa ozet de susturulamaz.
        const hasCritical = fresh.some((a) => ["critical", "error"].includes(a.level.toLowerCase()));
        const body = t("toasts.newAlarmsBody", { count: fresh.length });
        const opts = {
          title: t("toasts.newAlarmsTitle"),
          ...(hasCritical ? alarmAction : susturulabilir)
        };
        if (hasCritical) toast.error(body, opts);
        else toast.warning(body, opts);
      }
    };
    try {
      const rows = await fetchAlarmEvents(session.accessToken);
      setAlarms(rows);
      notifyNew(rows);
    } catch {
      // sessizce yutuyoruz — gecici ag hatalari polling'i durdurmamali
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [session, devices, toast, t, openTab]);

  usePolling({ enabled: Boolean(session), intervalMs: 5000, fn: pollAlarms, immediate: false });

  // Hat Arizalari (faults) canli refresh: 5 sn'de bir. Backend'in
  // fault_recompute_service'i alarm degistikce DB'yi senkronlar; biz de
  // burada UI'da liste tazelenir.
  // `faults` YALNIZCA FaultListPage'e gidiyor — baska sayfadayken cekmenin
  // anlami yok. Sayfa acilinca usePolling hemen bir kez ceker (immediate).
  const pollFaults = useCallback(async () => {
    if (!session) return;
    setFaultsLoading(true);
    try {
      // "all": Hat Arizalari sayfasi hem "Aktif Ariza" hem "Gecmis
      // Arizalar" sekmesini tek istekten besliyor. Backend olgunlasmamis
      // (display-delay dolmamis) aktif arizalari yine gizler; resolved/
      // closed her durumda gelir.
      const rows = await fetchFaults(session.accessToken, "all");
      setFaults(rows);
      setFaultsError("");
    } catch (err) {
      // HATA ARTIK YUTULMUYOR.
      //
      // Onceden `catch { // ignore }` idi ve sayfaya `loading={false}`
      // geciliyordu. Sonuc: istemci veriyi HIC alamamis olsa bile ekranda
      // yesil tik ve "Aktif ariza yok — Sistem temiz" yaziyordu. Nobetci
      // operator "X hattinda ariza var mi?" sorusuna bakip "yok" diyordu.
      //
      // Mevcut liste KORUNUYOR (silmiyoruz): gecici bir ag hatasinda daha
      // once alinmis arizalari ekrandan kaldirmak, "ariza kayboldu" izlenimi
      // vererek ayni yaniltmayi ters yonden yapardi. Kullaniciya bunun
      // yerine "guncellenemedi" deniyor.
      setFaultsError(
        err instanceof Error ? err.message : "Arıza listesi güncellenemedi."
      );
    } finally {
      setFaultsLoading(false);
    }
  }, [session]);

  usePolling({
    enabled: Boolean(session) && pageMode === "faults",
    intervalMs: 5000,
    fn: pollFaults
  });

  // Hat Arizalari ozet istatistikleri (avg cozum suresi vb) — 30sn polling.
  // `faults` gibi sadece FaultListPage'de gosteriliyor.
  const pollFaultStats = useCallback(async () => {
    if (!session) return;
    try {
      const s = await fetchFaultStats(session.accessToken);
      setFaultStats(s);
    } catch {
      // ignore
    }
  }, [session]);

  usePolling({
    enabled: Boolean(session) && pageMode === "faults",
    intervalMs: 30000,
    fn: pollFaultStats
  });

  // Olaylar (system events) canli refresh: 5 sn'de bir. Olay sayfasinda
  // kullanici yeni kayitlari sayfa yenilemeden gorebilsin.
  //
  // `events` iki yere gidiyor: Olaylar sayfasi ve Alarmlar sayfasi (alarm
  // detayindaki olay zaman cizelgesi). Ikisi disinda cekilmiyor.
  const pollEvents = useCallback(async () => {
    if (!session) return;
    try {
      const rows = await fetchSystemEvents(session.accessToken);
      setEvents(rows);
    } catch {
      // ignore
    }
  }, [session]);

  usePolling({
    enabled: Boolean(session) && (pageMode === "events" || pageMode === "alarms"),
    intervalMs: 5000,
    fn: pollEvents
  });

  // Grid topology snapshot: Hat Yonetimi'nde yapilan degisiklikler (cihaz
  // konumu, direk ekle/sil/tasi, bransman) haritaya yansisin.
  //
  // Sayfa gecidi YOK: topolojiden turetilen `deviceTopologyInfo` (cihaz ->
  // bolge/hat) Header'da (her sayfada), Alarmlar'da, Arizalar'da ve cihaz
  // detayinda kullaniliyor; tek sayfaya baglanamaz.
  //
  // Onun yerine PERIYOT sayfaya bagli. Topoloji neredeyse STATIK bir veri
  // (yalnizca bir kullanici Hat Yonetimi'nden degistirince degisir) ama
  // /grid/snapshot yanitin en agirlarindan: 600 cihazli sahada
  // regions+lines+poles+segments birlikte binlerce satir. Bunu her sayfada
  // 5 saniyede bir cekmek yukun buyuk bolumuydu.
  //
  // Hizli periyot YALNIZCA topolojinin DUZENLENDIGI sayfalarda: orada kullanici
  // kendi degisikliginin yansimasini gormek istiyor.
  //
  // Digerleri (anasayfa haritasi dahil) 60 sn: topoloji ancak biri Hat
  // Yonetimi'nden degistirince degisir, kullanici haritaya bakarken kendiliginden
  // degismez. Kritik durum "baska sekmede duzenledim, haritaya geciyorum" —
  // onu periyot DEGIL, sayfa degisimindeki aninda cekme kapatiyor: pageMode
  // degisince intervalMs degisir, usePolling efekti yeniden kurulur ve
  // `immediate` ile hemen bir kez ceker.
  //
  // (Anasayfayi da 10 sn yapmayi denedim; sayfa basina istek yuku olcumunde
  // kazanci gotururken karsiliginda bir sey vermiyordu — geometri o pencerede
  // degismiyor.)
  const topologyIsHot =
    pageMode === "engineering" &&
    (engineeringPage === "grid" || engineeringPage === "responsibility-areas");

  const pollGridSnapshot = useCallback(async () => {
    if (!session) return;
    try {
      const snap = await fetchGridSnapshot(session.accessToken);
      setGridSnapshot(snap);
    } catch {
      // ignore
    }
  }, [session]);

  usePolling({
    enabled: Boolean(session),
    intervalMs: topologyIsHot ? 10000 : 60000,
    fn: pollGridSnapshot
  });

  // Cihaz listesi canli refresh. Haberlesme durumlari (yesil/gri dot),
  // batarya ve alarm bayraklari sayfa yenilenmeden guncellensin: cihaz online
  // olsa bile UI'da stale "haberlesme yok" goruntusu kalmasin.
  //
  // Sayfa gecidi YOK: `devices` Header ve TabBar'da (her sayfada; sekme
  // basliklarindaki cihaz adi buradan geliyor) kullaniliyor, gate edilemez.
  //
  // PERIYOT sayfaya bagli:
  //   * Cihaz DURUMUNU gosteren sayfalarda 5 sn — bu hizli periyot
  //     "haberlesme yok yaziyor ama cihaz online" sikayeti icin eklenmisti,
  //     o davranis korunuyor.
  //   * Diger sayfalarda 30 sn — orada `devices` yalnizca ad/kod eslemesi
  //     icin okunuyor (Header, sekme basliklari) ve bu veri statik.
  // Sayfa degisince intervalMs degisir, efekt yeniden kurulur ve `immediate`
  // ile aninda taze veri gelir; durum sayfasina gecen kullanici beklemez.
  const deviceStatusIsHot =
    pageMode === "home" ||
    pageMode === "device-detail" ||
    (pageMode === "engineering" &&
      (engineeringPage === "devices" ||
        engineeringPage === "system-status" ||
        engineeringPage === "live-values"));

  const pollDevices = useCallback(async () => {
    if (!session) return;
    try {
      const list = await fetchDevices(session.accessToken);
      setDevices(list);
    } catch {
      // ignore
    }
  }, [session]);

  usePolling({
    enabled: Boolean(session),
    intervalMs: deviceStatusIsHot ? 5000 : 30000,
    fn: pollDevices
  });

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
      const rows = await fetchLiveValuesForScope(session.accessToken, liveScope);
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
  }, [session, liveScope]);

  useEffect(() => {
    if (!session) {
      return;
    }
    if (liveScope.kind === "none") {
      return;
    }
    // Tam kartezyen yalnizca muhendislik "Canli Degerler" sayfasinda ve
    // yalnizca yetkili rollerde cekilir.
    if (
      liveScope.kind === "all" &&
      session.role !== "engineer" &&
      session.role !== "installer"
    ) {
      return;
    }
    void handleRefreshSignalLive();
    // KAPSAM DEGISIMINDE de tetiklenmeli: `usePolling` yalnizca `enabled`
    // degisince aninda ceker. Haritada baska bir cihaz secmek `enabled`i
    // degistirmedigi icin, bu efekt olmadan kullanici bir sonraki periyoda
    // (30 sn'ye) kadar eksik deger gorurdu.
  }, [session, liveScope, handleRefreshSignalLive]);

  // Anasayfada — harita ve tablo ikisi de canli degerlere ihtiyac duyar:
  //   - Tablo: liste hucreleri
  //   - Harita: popup batarya kartlari + sidebar master batarya yuzdesi
  //
  // WS bagli iken: telemetri push ile saniye altinda gelir; polling sadece
  // bir guvenlik agi (yeni cihaz eklenmesi, WS drop ettigi mesajlar). 30sn
  // polling yeterli — backend'e gereksiz yuk vermiyor, frontend hala canli.
  // WS bagli degilken (WS unsupported / nginx config eksik): polling 5sn
  // (degerler "yeterince" canli gozuksun).
  // Polling YALNIZCA canli deger gosteren sayfalarda kosar. Eskiden sadece
  // `pageMode === "home"` kosulu vardi; cihaz detayi acikken hicbir sey
  // yenilenmiyor, anasayfa kapaliyken de gereksiz istek atilmiyordu ama
  // anasayfada TUM kartezyen cekiliyordu. Artik kapsam ne ise o cekilir.
  usePolling({
    enabled:
      Boolean(session) &&
      (liveScope.kind === "dashboard" || liveScope.kind === "device"),
    intervalMs: liveSocket.connectionState === "open" ? 30000 : 5000,
    fn: handleRefreshSignalLive,
    // Ilk cekimi yukaridaki kapsam efekti yapiyor; `immediate` acik kalsaydi
    // sayfaya her giriste IKI istek atilirdi.
    immediate: false
  });

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

  /** Davet akisi — admin sifre belirlemeden user yaratir. Backend token uretip
   * setup_url doner; SMTP aktif ise mail otomatik gider. Aksi halde URL
   * admin'e dondurulur, admin panodan kopyalar. */
  const handleInviteUser = async (payload: {
    username: string;
    email: string;
    phone_number?: string | null;
    full_name: string;
    role: UserRole;
    send_email: boolean;
  }) => {
    if (!session) return null;
    const resp = await inviteUser(session.accessToken, {
      username: payload.username,
      email: payload.email,
      full_name: payload.full_name,
      phone_number: payload.phone_number ?? undefined,
      role: payload.role,
      send_email: payload.send_email,
    });
    await reloadUsers();
    toast.success(
      resp.email_sent
        ? t("toasts.userInvited", { username: payload.username, defaultValue: `${payload.username} davet edildi. Mail gonderildi.` })
        : t("toasts.userInvitedNoMail", { username: payload.username, defaultValue: `${payload.username} davet edildi. Setup linki UI'da gosteriliyor.` })
    );
    return resp;
  };

  const handleResendInvite = async (userId: number) => {
    if (!session) return null;
    const resp = await resendInvite(session.accessToken, userId);
    await reloadUsers();
    toast.success(
      resp.email_sent
        ? t("toasts.inviteResentMail", { defaultValue: "Davet maili yeniden gonderildi." })
        : t("toasts.inviteResentLink", { defaultValue: "Yeni davet linki uretildi." })
    );
    return resp;
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

  const panelDevices = useMemo(
    () =>
      devicePanelGatewayCode
        ? devices.filter((device) => device.gatewayCode === devicePanelGatewayCode)
        : devices.filter(
            (device) =>
              !device.gatewayCode || !gateways.some((gateway) => gateway.code === device.gatewayCode)
          ),
    [devicePanelGatewayCode, devices, gateways]
  );

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
    if (!await asyncConfirm(message)) return;
    try {
      await deleteGateway(session.accessToken, gatewayCode);
      const [nextGateways, all] = await Promise.all([
        fetchGateways(session.accessToken),
        fetchDevices(session.accessToken)
      ]);
      setGateways(nextGateways);
      setDevices(all);
      setDeviceInventoryError("");
      setDevicePanelGatewayCode(nextGateways[0]?.code ?? "");
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

  // Cihaz DNP3 komutu (CROB) — confirm + kuyruga al. Gateway NAT arkasinda,
  // komut config-poll ile ~30sn icinde iletilir; sonuc komut listesinde takip
  // edilir (fire-and-forget toast).
  const handleDeviceCommand = async (deviceCode: string, command: string, label: string) => {
    if (!session) return;
    if (!(await asyncConfirm(t("deviceDetail.commands.confirm", { command: label, code: deviceCode }))))
      return;
    try {
      await sendDeviceCommand(session.accessToken, deviceCode, command);
      toast.success(t("deviceDetail.commands.queued", { command: label }));
    } catch (err) {
      const msg = err instanceof Error ? err.message : t("toasts.requestSendFail");
      toast.error(t("deviceDetail.commands.fail", { msg }));
    }
  };

  const handleUpdateGateway = async (
    gatewayCode: string,
    payload: {
      name?: string;
      host?: string;
      listen_port?: number;
      token?: string;
      publish_dnp3_quality?: boolean;
    }
  ) => {
    if (!session) return;
    await updateGateway(session.accessToken, gatewayCode, payload);
    await reloadGateways();
    toast.success(t("toasts.gatewayUpdated"));
  };

  const handleSelectGatewayForDevices = useCallback(async (gatewayCode: string) => {
    setDevicePanelGatewayCode(gatewayCode);
  }, []);

  // Cihaz Yonetimi paneli icin GATEWAY envanteri.
  //
  // Onceden burada `fetchDevices` de cagriliyordu; ama global cihaz polling'i
  // (bkz. `pollDevices`) bu sayfada zaten 5 saniyede bir ayni ucu cekiyor.
  // Ikisi birlikte kosunca `/devices` ayni sayfada iki timer'dan (5 sn + 12 sn)
  // isteniyordu — ayni veri icin iki kat yuk ve `devices` dizisinin kimligi
  // beklenenden sik degisip memo'lari bosa dusuruyordu. Cihazlari global
  // polling'e biraktik; burada yalnizca gateway'ler cekiliyor.
  const refreshDevicePanelData = useCallback(async () => {
    if (!session) return;
    if (session.role !== "engineer" && session.role !== "installer") return;
    try {
      const gw = await fetchGateways(session.accessToken);
      setGateways(gw);
      setDeviceInventoryError("");
      setDevicePanelGatewayCode((current) =>
        current && gw.some((gateway) => gateway.code === current)
          ? current
          : (gw[0]?.code ?? "")
      );
    } catch (error) {
      // Son basarili envanteri koru; API hatasini bos liste gibi gosterme.
      setDeviceInventoryError(
        error instanceof Error ? error.message : t("common.errorOccurred")
      );
    }
  }, [session, t]);

  // Sekme her acildiginda hemen taze veri cek (immediate) - aksi halde stale
  // gateway durumu (haberlesme yok gibi) gosterilebiliyor.
  usePolling({
    enabled:
      Boolean(session) &&
      pageMode === "engineering" &&
      engineeringPage === "devices" &&
      (session?.role === "engineer" || session?.role === "installer"),
    intervalMs: 12000,
    fn: refreshDevicePanelData
  });

  const reloadLicenseStatus = useCallback(async () => {
    if (!session) return;
    setLicenseLoading(true);
    try {
      // Kilit durumunu da tazele: lisans import edildikten sonra kilidin
      // ACILMASI buna bagli (backend cache'i import'ta zaten dusuruluyor).
      try {
        const gate = await fetchLicenseGate(session.accessToken);
        // gate === null: eski backend, bu ucu tanimiyor -> kilide dokunma.
        if (gate !== null) {
          setLicenseActivatedFlag(!gate.locked);
          setLicenseGatePhase(gate.locked ? "locked" : "open");
        }
      } catch {
        // Yenileme hatasi mevcut kilidi DEGISTIRMEZ. Kullanici bir aksiyonun
        // (cihaz ekleme/silme) ardindan gecici bir ag hatasi yuzunden
        // sistemden atilmamali.
      }
      if (session.role === "engineer" || session.role === "installer") {
        setLicenseStatus(await fetchLicenseStatus(session.accessToken));
      }
    } catch {
      // Fail-closed: status bilinmiyorsa cihaz ekleme disabled kalir; cihaz
      // ekleme/silme akisini lisans status yenileme hatasiyla basarisiz gosterme.
      setLicenseStatus(null);
    } finally {
      setLicenseLoading(false);
    }
  }, [session]);

  const handleCreateDevice = async (payload: {
    code: string;
    name: string;
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
  }) => {
    if (!session) return;
    try {
      await createDevice(session.accessToken, payload);
    } catch (err) {
      // Lisansta SURE siniri yok, CIHAZ SAYISI siniri var. Sinir dolunca
      // backend 403 doner; sistem calismaya devam eder, yalnizca ekleme
      // reddedilir. Kullaniciya sebebi toast ile soyluyoruz — sessizce
      // basarisiz olmasi "kaydet calismiyor" gibi gorunuyordu.
      toast.error(err instanceof Error ? err.message : t("toasts.deviceAddFailed"));
      throw err;   // form acik kalsin, kullanici duzeltip tekrar denesin
    }
    const all = await fetchDevices(session.accessToken);
    setDevices(all);
    if (payload.gateway_code) {
      setDevicePanelGatewayCode(payload.gateway_code);
    }
    try {
      const signalsRows = await fetchSignals(session.accessToken);
      setSignalCatalog(signalsRows);
    } catch {
      // sinyal listesi tazelense iyi, canlı matrisin etiketleriyle uyum kalsin
    }
    await Promise.all([handleRefreshSignalLive(), reloadLicenseStatus()]);
    toast.success(t("toasts.deviceAdded", { name: payload.name }));
  };

  const handleUpdateDevice = async (
    deviceCode: string,
    payload: {
      name?: string;
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
    }
  ) => {
    if (!session) return;
    await updateDevice(session.accessToken, deviceCode, payload);
    const all = await fetchDevices(session.accessToken);
    setDevices(all);
    if (payload.gateway_code) {
      setDevicePanelGatewayCode(payload.gateway_code);
    }
    toast.success(t("toasts.deviceUpdated"));
  };

  const handleDeleteDevice = async (deviceCode: string) => {
    if (!session) return;
    await deleteDevice(session.accessToken, deviceCode);
    const all = await fetchDevices(session.accessToken);
    setDevices(all);
    await Promise.all([handleRefreshSignalLive(), reloadLicenseStatus()]);
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
  }) => {
    if (!session) return undefined;
    const created = await createOutboundTarget(session.accessToken, payload);
    await reloadOutboundTargets();
    toast.success(t("toasts.outboundAdded", { name: payload.name }));
    return created;
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

  // Muhendislik menusunden sayfa secimi. Sekmeyi acar ve o sayfaya ozel
  // veri yenilemelerini tetikler — eski duz sekme butonlarindaki onClick
  // govdeleriyle birebir ayni davranis.
  const handleEngNavSelect = (page: EngineeringPage) => {
    openEng(page);
    switch (page) {
      case "signals":
        void reloadSignals();
        break;
      case "alarm-rules":
        void reloadAlarmRules();
        void reloadSignals();
        break;
      case "users":
        void reloadUsers();
        break;
      case "responsibility-areas":
        void reloadResponsibilityAreas();
        break;
      case "api-access":
        void reloadApiKeys();
        break;
      case "notifications":
        void reloadNotificationSettings();
        break;
      case "license":
        void reloadLicenseStatus();
        break;
      default:
        break;
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

  const handleFetchWhatsappWebStatus = async () => {
    if (!session) {
      throw new Error("Oturum bulunamadı.");
    }
    return fetchWhatsappWebStatus(session.accessToken);
  };

  const handleFetchWhatsappWebQr = async () => {
    if (!session) {
      throw new Error("Oturum bulunamadı.");
    }
    return fetchWhatsappWebQr(session.accessToken);
  };

  const handleFetchWhatsappWebGroups = async () => {
    if (!session) {
      throw new Error("Oturum bulunamadı.");
    }
    return fetchWhatsappWebGroups(session.accessToken);
  };

  const handleTestWhatsappWeb = async (payload: { recipient_phone: string; message?: string }) => {
    if (!session) {
      throw new Error("Oturum bulunamadı.");
    }
    return testWhatsappWeb(session.accessToken, payload);
  };

  const handleLogoutWhatsappWeb = async () => {
    if (!session) {
      throw new Error("Oturum bulunamadı.");
    }
    return logoutWhatsappWeb(session.accessToken);
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
    key: "web_enabled" | "email_enabled" | "sms_enabled" | "whatsapp_web_enabled"
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
      {
        regionId: number;
        regionName: string;
        lineId: number;
        lineName: string;
        latitude?: number;
        longitude?: number;
      }
    >();
    if (!gridSnapshot) return map;
    const lineById = new Map(gridSnapshot.lines.map((l) => [l.id, l]));
    const regionById = new Map(gridSnapshot.regions.map((r) => [r.id, r]));
    const poleById = new Map(gridSnapshot.poles.map((p) => [p.id, p]));
    for (const seg of gridSnapshot.segments) {
      if (!seg.device_id) continue;
      const line = lineById.get(seg.line_id);
      if (!line) continue;
      const region = regionById.get(line.region_id);
      // Cihaz konumu: bagli oldugu segmentin iki diregi arasi (position_t ile
      // interpolasyon; yoksa orta nokta). Cihazin kendi lat/lon'u yoksa harita
      // bunu kullanir.
      const fromP = poleById.get(seg.from_pole_id);
      const toP = poleById.get(seg.to_pole_id);
      let lat: number | undefined;
      let lon: number | undefined;
      if (fromP && toP) {
        const t = seg.device_position_t ?? 0.5;
        lat = fromP.latitude + (toP.latitude - fromP.latitude) * t;
        lon = fromP.longitude + (toP.longitude - fromP.longitude) * t;
      } else if (fromP) {
        lat = fromP.latitude;
        lon = fromP.longitude;
      }
      map.set(seg.device_id, {
        regionId: line.region_id,
        regionName: region?.name ?? "",
        lineId: line.id,
        lineName: line.name,
        latitude: lat,
        longitude: lon,
      });
    }
    return map;
  }, [gridSnapshot]);

  // Secili ekibin gorunur cihaz id seti. Backend get_visible_device_ids ile
  // ayni mantik: dogrudan atanan cihazlar + ekibe atanan bolge/hatlarin
  // uzerindeki (deviceTopologyInfo'dan tureyen) cihazlar. null = ekip secili
  // degil ("Tumu"); bu durumda area filtresi uygulanmaz.
  const dashboardAreaDeviceIds = useMemo<Set<number> | null>(() => {
    if (!dashboardAreaScope) return null;
    const ids = new Set<number>(dashboardAreaScope.deviceIds);
    if (dashboardAreaScope.regionIds.length || dashboardAreaScope.lineIds.length) {
      const regionSet = new Set(dashboardAreaScope.regionIds);
      const lineSet = new Set(dashboardAreaScope.lineIds);
      for (const [deviceId, info] of deviceTopologyInfo.entries()) {
        if (regionSet.has(info.regionId) || lineSet.has(info.lineId)) {
          ids.add(deviceId);
        }
      }
    }
    return ids;
  }, [dashboardAreaScope, deviceTopologyInfo]);

  // Dashboard ortak filtrelerine göre süzülmüş cihaz listesi.
  // Harita marker'ları, sol sidebar listesi ve LiveValuesPage tablo satırları
  // bu kaynağı paylaşır → kullanıcı üst çubuğa girdiği değer her yerde aynı
  // anda etkili olur.
  const filteredDashboardDevices = useMemo(() => {
    const q = dashboardSearch.trim().toLowerCase();
    return devices.filter((d) => {
      // ESKI: atanmamis cihazlari listeden tamamen gizliyorduk. YENI:
      // hepsi gozuksun ama DeviceSidebar atanmamislari 'Hatta atanmadi'
      // rozeti ile isaretler (deviceTopology null kontrolu). Boylece
      // operator silinmis/yeni eklenmis cihazi anasayfada ariza tespit
      // etmek icin atayabilir/teshis edebilir.
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

  // Anasayfa: OTOMATIK cihaz secimi YAPILMAZ — ilk acilista hicbir cihaz
  // secili olmasin (kullanici tercihi). Karttan "kapat" (onSelectDevice(0))
  // ile secim birakildiginda da kapali kalir. Tek istisna: secili bir cihaz
  // filtre ile listeden cikarsa secimi birak (0), gecersiz secim kalmasin.
  useEffect(() => {
    if (selectedDeviceId === 0) return;
    const stillVisible = filteredDashboardDevices.some((d) => d.id === selectedDeviceId);
    if (!stillVisible) {
      setSelectedDeviceId(0);
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
      setDeviceInventoryError("");
      setDevicePanelGatewayCode((current) =>
        current && gw.some((gateway) => gateway.code === current)
          ? current
          : (gw[0]?.code ?? "")
      );
    } catch {
      // Oturum hatasi ust seviyede yakala
    } finally {
      setLoadingData(false);
    }
  };

  if (!session) {
    return <LoginForm onSubmit={handleLogin} loading={loadingLogin} />;
  }

  // Zorunlu sifre degistirme her iki ekranin (kilit + normal) uzerinde ayni
  // sekilde durur; tek yerde kurup iki yerde kullaniyoruz.
  const forcePasswordModal = session.mustChangePassword ? (
    <ChangePasswordModal
      forceful
      accessToken={session.accessToken}
      onSuccess={() => {
        // Backend basariyla sifreyi degistirdi; flag'i temizle ve devam et.
        const cleared = { ...session, mustChangePassword: false };
        saveSession(cleared, true);
        setSession(cleared);
      }}
    />
  ) : null;

  // ---- Lisanssiz sistem kilidi -------------------------------------------
  // Gecerli lisans YOKKEN kullanici hicbir sey yapamaz; dogrudan lisans
  // sayfasina kilitlenir. Zorunlu sifre degisimi bunun ONUNDE durur (modal
  // ustte render edilir), yani sira: giris -> sifre -> lisans.
  //
  // Kilit `state` uzerinden kurulur, `is_valid` uzerinden DEGIL. Ikisi ayni
  // sey degil (bkz. license_service.get_license_status):
  //
  //   unlicensed | invalid | machine_mismatch -> lisans fiilen YOK  -> KILIT
  //   machine_unavailable -> bizim tarafta depolama/machine-id arizasi.
  //       is_valid false doner ama bu bir lisans durumu degildir; mevcut
  //       tasarim burada "izleme acik kalir, cihaz ekleme kapanir" der.
  //       Gecici bir dosya izni hatasi arayuzu kilitlememeli.       -> KILIT YOK
  //   valid + kota dolu -> is_valid true kalir; sistem normal calisir,
  //       sadece yeni cihaz eklenemez.                              -> KILIT YOK
  //
  // Kilit `licenseGatePhase` uzerinden kurulur (bkz. LicenseGatePhase):
  //   locked  -> lisans yok
  //   unknown -> durum okunamadi, FAIL-CLOSED
  //   checking-> acilis, henuz cevap yok. Icerik yine RENDER EDILMEZ; aksi
  //              halde lisanssiz sistemde sayfalar bir an gorunurdu.
  const licenseGateActive = licenseGatePhase !== "open";

  // ONEMLI: Kilit artik CSS ile GIZLEME degil. Onceki surum sayfalari normal
  // render edip `.is-license-locked .body { filter: blur; pointer-events:none }`
  // ile bulaniklastiriyordu — DevTools'tan o kurali silmek (ya da klavyeyle
  // Tab'lamak) kilidi tamamen atlatiyordu. Artik icerik DOM'a HIC basilmaz;
  // ayrica backend de lisanssiz kurulumda API'yi kapatir
  // (bkz. apps/backend-api/app/core/license_gate.py).
  //
  // Kilidin DISINDA kalan sayfalar — ikisi de cikis yolu, kapatilirsa sistem
  // kendini asla acamaz:
  //   license          : lisansin yuklendigi sayfa.
  //   network-settings : agi bozuk lisanssiz cihaz once agi duzeltmeli
  //                      (bkz. commit 144539f; 1d8c605'te kaybolmustu).
  //   remote-access    : lisansi bozuk cihazi UZAKTAN duzeltebilmek icin
  //                      musterinin bize erisim verebilmesi sart. Bu sayfa
  //                      da kilitlenirse kilidi acacak yol kapanir.
  //                      Backend zaten ayni istisnayi yapiyor
  //                      (license_gate.py: "/remote-access/" izinli
  //                      prefix'ler arasinda, "/network ile AYNI gerekce").
  //                      Burada eksik oldugu icin arayuz, backend'in bilerek
  //                      acik biraktigi kapiyi kapatiyordu.
  const licenseGateExemptPage =
    pageMode === "engineering" &&
    (engineeringPage === "license" ||
      engineeringPage === "network-settings" ||
      engineeringPage === "remote-access");
  const licenseGateOpen = licenseGateActive && !licenseGateExemptPage;

  // Icerigin render edilip edilmeyecegi. Muaf sayfalar kilitliyken de acilir.
  const licenseAllowsContent = !licenseGateActive || licenseGateExemptPage;

  // KULLANICIYI BOSA KORKUTMA: lisans uyarisi YALNIZCA kesinlesmis bir sorun
  // varsa cikar. "checking" fazinda (her acilista birkac yuz ms) hicbir lisans
  // metni gosterilmez — lisansi OLAN sistemde de caktigi icin yanlis alarmdi.
  // O sirada sadece normal yukleniyor gostergesi vardir.
  const licenseGateModal =
    licenseGateOpen && licenseGatePhase !== "checking" ? (
      <div className="license-gate-overlay" role="alertdialog" aria-modal="true">
        <div className="license-gate-dialog">
          {licenseGatePhase === "unknown" ? (
            // Lisans YOK demiyoruz — durumu DOGRULAYAMADIK diyoruz. Ikisi
            // farkli sey; lisansli bir sistemde ag hatasi da buraya duser.
            <>
              <h2>{t("engineering.license.gateUnknownTitle")}</h2>
              <p>{t("engineering.license.gateUnknown")}</p>
              <div className="license-gate-actions">
                <button
                  type="button"
                  className="primary-btn"
                  onClick={() => void reloadLicenseStatus()}
                >
                  {t("engineering.license.gateRetry")}
                </button>
                {session.role === "installer" ? (
                  <button
                    type="button"
                    className="secondary-btn"
                    onClick={() => openEng("network-settings")}
                  >
                    {t("engineering.license.gateNetwork")}
                  </button>
                ) : null}
              </div>
            </>
          ) : (
            <>
              <h2>{t("engineering.license.gateTitle")}</h2>
              <p>{t("engineering.license.gateShort")}</p>
              <div className="license-gate-actions">
                <button
                  type="button"
                  className="primary-btn"
                  onClick={() => openEng("license")}
                >
                  {t("engineering.license.gateGo")}
                </button>
                {session.role === "installer" ? (
                  <button
                    type="button"
                    className="secondary-btn"
                    onClick={() => openEng("network-settings")}
                  >
                    {t("engineering.license.gateNetwork")}
                  </button>
                ) : null}
              </div>
            </>
          )}
        </div>
      </div>
    ) : null;

  // Tembel yuklenen sayfa chunk'i inerken icerik alaninda gosterilir.
  // GlobalLoading BILEREK kullanilmiyor: o `position: fixed; inset: 0` ile
  // TUM ekrani koyu bir scrim + blur ardina aliyor. Sayfa gecisinde bir kare
  // boyunca gorunup kayboldugu icin "grimsi flicker" olarak fark ediliyordu.
  // Burada yalnizca icerik alani, karartma olmadan yer tutar.
  const pageFallback = (
    <div className="page-suspense" role="status" aria-live="polite">
      <span className="panel-loading-spinner" aria-hidden="true" />
    </div>
  );

  return (
    <div className={`layout${licenseGateOpen ? " is-license-locked" : ""}`}>
      {forcePasswordModal}
      {licenseGateModal}
      <Header
        fullName={currentUser?.full_name ?? session.username}
        role={session.role}
        accessToken={session.accessToken}
        activePage={pageMode === "device-detail" ? "home" : pageMode}
        onChangePage={handleChangePage}
        isEngineeringView={pageMode === "engineering"}
        onToggleEngineering={() => handleChangePage("engineering")}
        onOpenSystemStatus={() => openEng("system-status")}
        remoteAccessActive={remoteAccessBadge.active}
        remoteAccessLabel={
          remoteAccessBadge.remainingSeconds === null
            ? undefined
            : formatRemaining(remoteAccessBadge.remainingSeconds, t)
        }
        onOpenRemoteAccess={() => openEng("remote-access")}
        onSettings={handleOpenSettings}
        onLogout={handleLogout}
        devices={devices}
        regions={gridSnapshot?.regions ?? []}
        lines={gridSnapshot?.lines ?? []}
        deviceTopology={deviceTopologyInfo}
        onOpenDevice={openDeviceDetail}
        onSelectRegion={(regionId) => {
          setDashboardRegionId(regionId);
          handleChangePage("home");
        }}
        onSelectLine={(lineId) => {
          setDashboardLineId(lineId);
          handleChangePage("home");
        }}
      />
      <TabBar
        tabs={tabsApi.tabs}
        activeKey={tabsApi.activeKey}
        onActivate={tabsApi.activateTab}
        onClose={tabsApi.closeTab}
        onCloseOthers={tabsApi.closeOthers}
        onCloseToRight={tabsApi.closeToRight}
        onReorder={tabsApi.reorderTabs}
        deviceLookup={(id) => {
          const d = devices.find((dev) => dev.id === id);
          return d ? { code: d.code, name: d.name } : undefined;
        }}
      />
      <div className="body">
        {/* Lisans kilidi: icerik DOM'a hic basilmaz (CSS ile gizleme DEGIL).
            Muaf sayfalar — lisans + ag ayarlari — kilitliyken de acilir.
            "checking" fazinda notr yukleniyor gostergesi; lisans metni YOK. */}
        {!licenseAllowsContent ? (
          licenseGatePhase === "checking" ? <GlobalLoading show /> : null
        ) : (
        // Suspense siniri SAYFA ICERIGINE daraltildi. Onceden tum `layout`u
        // sariyordu: tembel bir sayfa askiya alindiginda React header'i,
        // sekmeleri ve yan menuyu de sokup yerine tam ekran overlay
        // basiyordu — her ilk sayfa acilisinda tum arayuz bir an kararip
        // geri geliyordu. Artik ust cerceve YERINDE KALIR, yalnizca icerik
        // alani yer tutar.
        <Suspense fallback={pageFallback}>
        {pageMode === "device-detail" && activeDeviceDetailId !== null ? (
          <main className="content">
            <DeviceDetailPage
              deviceId={activeDeviceDetailId}
              devices={devices}
              values={signalLiveValues}
              signals={signalCatalog}
              gateways={gateways}
              topologyInfo={deviceTopologyInfo.get(activeDeviceDetailId)}
              canCommand={session.role === "engineer" || session.role === "installer"}
              canConfig={session.role === "installer"}
              onDeviceCommand={handleDeviceCommand}
              token={session.accessToken}
            />
          </main>
        ) : pageMode === "engineering" ? (
          <main className="content engineering-content">
            <EngineeringNav
              role={session.role}
              activePage={engineeringPage}
              onSelect={handleEngNavSelect}
            />

            {engineeringPage === "devices" &&
            (session.role === "engineer" || session.role === "installer") ? (
              <DeviceManagementPanel
                role={session.role}
                accessToken={session.accessToken}
                gateways={gateways}
                devices={panelDevices}
                unassignedCount={devices.filter(
                  (device) =>
                    !device.gatewayCode ||
                    !gateways.some((gateway) => gateway.code === device.gatewayCode)
                ).length}
                deviceModels={deviceModels}
                inventoryError={deviceInventoryError}
                licenseStatus={licenseStatus}
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
            {engineeringPage === "users" && (session.role === "engineer" || session.role === "installer" || session.role === "ops_manager") ? (
              <UserManagementPanel
                users={users}
                currentUserId={currentUser?.id}
                allowInstallerRole={session.role === "installer"}
                restrictToOperator={session.role === "ops_manager"}
                onCreate={handleCreateUser}
                onInvite={handleInviteUser}
                onResendInvite={handleResendInvite}
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
            (session.role === "engineer" || session.role === "installer" || session.role === "ops_manager") ? (
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
            {engineeringPage === "bulk-notify" &&
            (session.role === "engineer" || session.role === "installer" || session.role === "ops_manager") ? (
              <BulkNotificationPage
                accessToken={session.accessToken}
                currentRole={session.role}
              />
            ) : null}
            {engineeringPage === "outbound" && (session.role === "installer" || session.role === "engineer") ? (
              <OutboundTargetsPanel
                targets={outboundTargets}
                devices={devices}
                accessToken={session.accessToken}
                allowedProtocols={["mqtt", "iec104", "modbus"]}
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
            {engineeringPage === "api-access" && (session.role === "installer" || session.role === "engineer") ? (
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
                outboundTargets={outboundTargets}
                devices={devices}
                accessToken={session.accessToken}
                onCreateWebhook={handleCreateOutboundTarget}
                onUpdateWebhook={handleUpdateOutboundTarget}
                onDeleteWebhook={handleDeleteOutboundTarget}
                onSave={handleSaveNotificationSettings}
                onTestSmtp={handleTestNotificationSmtp}
                onTestSms={handleTestNotificationSms}
                onTestTelegram={handleTestNotificationTelegram}
                onDiscoverTelegramChats={handleDiscoverTelegramChats}
                onFetchWhatsappWebStatus={handleFetchWhatsappWebStatus}
                onFetchWhatsappWebQr={handleFetchWhatsappWebQr}
                onFetchWhatsappWebGroups={handleFetchWhatsappWebGroups}
                onTestWhatsappWeb={handleTestWhatsappWeb}
                onLogoutWhatsappWeb={handleLogoutWhatsappWeb}
              />
            ) : null}
            {engineeringPage === "project-settings" && session.role === "installer" ? (
              <ProjectSettingsPanel onSave={handleSaveProjectSettings} />
            ) : null}
            {engineeringPage === "grid" &&
            (session.role === "engineer" || session.role === "installer") ? (
              <GridManagementPanel accessToken={session.accessToken} devices={devices} gridSnapshot={gridSnapshot} />
            ) : null}
            {engineeringPage === "license" &&
            (session.role === "engineer" || session.role === "installer") ? (
              <LicenseManagementPanel
                accessToken={session.accessToken}
                status={licenseStatus}
                loading={licenseLoading}
                onStatusChange={setLicenseStatus}
                onRefresh={reloadLicenseStatus}
              />
            ) : null}
            {engineeringPage === "backups" &&
            (session.role === "engineer" || session.role === "installer") ? (
              <BackupsPanel accessToken={session.accessToken} currentRole={session.role} />
            ) : null}
            {engineeringPage === "system-status" && session.role === "installer" ? (
              <SystemStatusPage
                devices={devices}
                gateways={gateways}
                alarms={alarms}
                loading={loadingData}
                onRefresh={handleRefreshSystemStatus}
                wsState={liveSocket.connectionState}
                wsLastDataAt={liveSocket.lastDataAt}
              />
            ) : null}
            {engineeringPage === "network-settings" && session.role === "installer" ? (
              <NetworkSettingsPage accessToken={session.accessToken} />
            ) : null}
            {/* Uzaktan bakim izni. Gorunurluk UC yerde tanimli ve hepsi
                birbiriyle ayni olmali: EngineeringNav.canSee, tabModel'deki
                rol listeleri ve buradaki kosul. Izin VERME yetkisi ayrica
                backend'de (yalnizca engineer) — sayfa `can_grant` ile
                kendini kisitlar. */}
            {engineeringPage === "remote-access" &&
            (session.role === "installer" ||
              session.role === "engineer" ||
              session.role === "ops_manager") ? (
              <RemoteAccessPage accessToken={session.accessToken} />
            ) : null}
            {engineeringPage === "offline-map" &&
            (session.role === "engineer" || session.role === "installer") ? (
              <OfflineMapPage accessToken={session.accessToken} />
            ) : null}
            {engineeringPage === "active-sessions" && session.role === "installer" ? (
              <ActiveSessionsPage accessToken={session.accessToken} />
            ) : null}
          </main>
        ) : pageMode !== "home" ? (
          <main className="content">
            {pageMode === "alarms" ? (
              <AlarmsPage
                alarms={alarms}
                users={users}
                devices={devices}
                regions={gridSnapshot?.regions ?? []}
                lines={gridSnapshot?.lines ?? []}
                deviceTopology={deviceTopologyInfo}
                loading={alarmsLoading}
                onAssign={handleAssignAlarm}
                onLoadComments={handleLoadAlarmComments}
                onAddComment={handleAddAlarmComment}
                onAcknowledge={handleAcknowledgeAlarm}
                onAcknowledgeAll={handleAcknowledgeAllAlarms}
                onOpenDevice={openDeviceDetail}
                events={events}
              />
            ) : null}
            {pageMode === "faults" ? (
              <FaultListPage
                faults={faults}
                stats={faultStats}
                users={users}
                currentUsername={session.username}
                canAssign={session.role === "engineer" || session.role === "installer"}
                loading={faultsLoading}
                error={faultsError}
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
                  gridSnapshot={gridSnapshot}
                  hiddenLineIds={hiddenLineIds}
                  onToggleLineHidden={handleToggleLineHidden}
                />
              ) : null}
              <main className="content dashboard-content map-active">
                <DeviceMapTab
                  devices={filteredDashboardDevices}
                  selectedDevice={selectedDevice}
                  onSelectDevice={setSelectedDeviceId}
                  liveValues={signalLiveValues}
                  gridSnapshot={gridSnapshot}
                  alarms={alarms}
                  onOpenDetail={openDeviceDetail}
                  hiddenLineIds={hiddenLineIds}
                />
              </main>
            </div>
          </div>
        )}
        </Suspense>
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
                <h4>{t("userSettings.notifPrefs.title")}</h4>
                <div className="notif-prefs-row">
                  <div className="notif-prefs-row-label">
                    <strong>{t("userSettings.notifPrefs.web")}</strong>
                    <span>{t("userSettings.notifPrefs.webHint")}</span>
                  </div>
                  <button
                    type="button"
                    className={`notif-prefs-toggle ${notifPrefs.web_enabled ? "on" : ""}`}
                    onClick={() => void handleToggleNotifPref("web_enabled")}
                    disabled={notifPrefsSaving}
                    aria-label={t("userSettings.notifPrefs.web")}
                  />
                </div>
                <div className="notif-prefs-row">
                  <div className="notif-prefs-row-label">
                    <strong>{t("userSettings.notifPrefs.email")}</strong>
                    <span>
                      {t("userSettings.notifPrefs.emailHint")}
                      {currentUser?.email ? "" : t("userSettings.notifPrefs.emailMissing")}
                    </span>
                  </div>
                  <button
                    type="button"
                    className={`notif-prefs-toggle ${notifPrefs.email_enabled ? "on" : ""}`}
                    onClick={() => void handleToggleNotifPref("email_enabled")}
                    disabled={notifPrefsSaving}
                    aria-label={t("userSettings.notifPrefs.email")}
                  />
                </div>
                <div className="notif-prefs-row">
                  <div className="notif-prefs-row-label">
                    <strong>{t("userSettings.notifPrefs.sms")}</strong>
                    <span>
                      {t("userSettings.notifPrefs.smsHint")}
                      {currentUser?.phone_number ? "" : t("userSettings.notifPrefs.smsMissing")}
                    </span>
                  </div>
                  <button
                    type="button"
                    className={`notif-prefs-toggle ${notifPrefs.sms_enabled ? "on" : ""}`}
                    onClick={() => void handleToggleNotifPref("sms_enabled")}
                    disabled={notifPrefsSaving}
                    aria-label={t("userSettings.notifPrefs.sms")}
                  />
                </div>
                <div className="notif-prefs-row">
                  <div className="notif-prefs-row-label">
                    <strong>{t("userSettings.notifPrefs.whatsapp")}</strong>
                    <span>
                      {t("userSettings.notifPrefs.whatsappHint")}
                      {currentUser?.phone_number ? "" : t("userSettings.notifPrefs.whatsappMissing")}
                    </span>
                  </div>
                  <button
                    type="button"
                    className={`notif-prefs-toggle ${notifPrefs.whatsapp_web_enabled ? "on" : ""}`}
                    onClick={() => void handleToggleNotifPref("whatsapp_web_enabled")}
                    disabled={notifPrefsSaving}
                    aria-label={t("userSettings.notifPrefs.whatsapp")}
                  />
                </div>
              </div>
            ) : null}

            <div className="settings-actions">
              <button onClick={() => setSettingsOpen(false)}>{t("userSettings.actions.cancel")}</button>
              <button onClick={handleSaveSettings} disabled={settingsSaving}>
                {settingsSaving ? t("userSettings.actions.saving") : t("userSettings.actions.save")}
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
