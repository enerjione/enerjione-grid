/**
 * Sekme (tab) modeli — Chrome tarzi sekme sisteminin saf (React'siz) cekirdegi.
 *
 * Her sekme bir "route" tanimidir. Uygulamada React Router yok; navigation
 * App.tsx'te pageMode + engineeringPage state'i ile yapiliyor. Sekme sistemi
 * bunun uzerine bir "router katmani" ekler: acik sekmeler listesi + aktif sekme.
 * Aktif sekmeden pageMode/engineeringPage TURETILIR (App.tsx senkronlar), boylece
 * mevcut render zinciri ve veri-yukleme effect'leri degismez.
 */

import type { UserRole } from "../../shared/types";

// ---- Route tipleri --------------------------------------------------------
// NOT: Bu iki tip App.tsx'te de kullaniliyordu; tek kaynak olsun diye buraya
// tasindi ve App.tsx buradan import ediyor.
export type PageMode =
  | "home"
  | "alarms"
  | "faults"
  | "events"
  | "engineering";

export type EngineeringPage =
  | "devices"
  | "device-config"
  | "signals"
  | "live-values"
  | "alarm-rules"
  | "users"
  | "responsibility-areas"
  | "bulk-notify"
  | "outbound"
  | "api-access"
  | "notifications"
  | "project-settings"
  | "grid"
  | "backups"
  | "license"
  | "system-status"
  | "network-settings"
  | "remote-access"
  | "offline-map"
  | "active-sessions";

export type TabRoute =
  // Ust menu sayfalari (engineering HARIC — o kendi alt sayfasiyla acilir)
  | { kind: "page"; page: Exclude<PageMode, "engineering"> }
  // Muhendislik alt sayfalari
  | { kind: "engineering"; page: EngineeringPage }
  // Cihaz detay sayfasi (ileride grafikler/veri). Simdilik placeholder.
  | { kind: "device-detail"; deviceId: number };

export type Tab = { key: string; route: TabRoute };

// ---- Key uretimi (dedup icin kararli string) ------------------------------
export function routeKey(route: TabRoute): string {
  switch (route.kind) {
    case "page":
      return `page:${route.page}`;
    case "engineering":
      return `eng:${route.page}`;
    case "device-detail":
      return `device:${route.deviceId}`;
  }
}

export function makeTab(route: TabRoute): Tab {
  return { key: routeKey(route), route };
}

// Ana sayfa sekmesi — her zaman ilk ve pinned (kapatilamaz).
export const HOME_ROUTE: TabRoute = { kind: "page", page: "home" };
export const HOME_KEY = routeKey(HOME_ROUTE);

export function isClosable(route: TabRoute): boolean {
  return routeKey(route) !== HOME_KEY;
}

// ---- Etiket + ikon --------------------------------------------------------
type TFn = (key: string, opts?: Record<string, unknown>) => string;

/** Sekme basligi. Mevcut header.* / engineering.nav.* i18n anahtarlari yeniden
 *  kullanilir. device-detail icin cihaz kodu/adi lookup ile cozulur. */
export function tabLabel(
  route: TabRoute,
  t: TFn,
  deviceLookup?: (id: number) => { code?: string; name?: string } | undefined
): string {
  switch (route.kind) {
    case "page": {
      const map: Record<Exclude<PageMode, "engineering">, string> = {
        home: "header.home",
        alarms: "header.alarms",
        faults: "header.faults",
        events: "header.events",
      };
      return t(map[route.page]);
    }
    case "engineering": {
      const map: Record<EngineeringPage, string> = {
        devices: "engineering.nav.devices",
        "device-config": "engineering.nav.deviceConfig",
        signals: "engineering.nav.signals",
        "live-values": "engineering.nav.liveValues",
        "alarm-rules": "engineering.nav.alarmRules",
        users: "engineering.nav.users",
        "responsibility-areas": "engineering.nav.responsibilityAreas",
        "bulk-notify": "engineering.nav.bulkNotify",
        outbound: "engineering.nav.outboundTargets",
        "api-access": "engineering.nav.apiAccess",
        notifications: "engineering.nav.notificationSettings",
        "project-settings": "engineering.nav.projectSettings",
        grid: "engineering.nav.grid",
        backups: "engineering.nav.backups",
        license: "engineering.nav.license",
        "system-status": "header.systemStatus",
        "network-settings": "engineering.nav.networkSettings",
        "remote-access": "engineering.nav.remoteAccess",
        "offline-map": "engineering.nav.offlineMap",
        "active-sessions": "engineering.nav.activeSessions",
      };
      return t(map[route.page]);
    }
    case "device-detail": {
      const dev = deviceLookup?.(route.deviceId);
      return dev?.code || dev?.name || `#${route.deviceId}`;
    }
  }
}

/** material-symbols ikon adi. */
// NOT: Sekme ikonlari `tabIcons.ts`'e tasindi (lucide bileseni dondurur, bu
// dosya bilerek React'siz kaliyor). Eski `tabIcon()` tum muhendislik
// sayfalari icin ayni "engineering" sembolunu donduruyordu.

// ---- pageMode/engineeringPage turetme -------------------------------------
/** Aktif route -> App.tsx'in bekledigi {pageMode, engineeringPage} state'i.
 *  device-detail bir "pseudo" pageMode dondurur; App render zinciri bunu ayri
 *  ele alir. engineeringPage her zaman gecerli bir deger dondurur (App state
 *  tipini bozmamak icin). */
export function routeToPageState(route: TabRoute): {
  pageMode: PageMode | "device-detail";
  engineeringPage: EngineeringPage;
} {
  switch (route.kind) {
    case "page":
      return { pageMode: route.page, engineeringPage: "devices" };
    case "engineering":
      return { pageMode: "engineering", engineeringPage: route.page };
    case "device-detail":
      return { pageMode: "device-detail", engineeringPage: "devices" };
  }
}

// ---- Rol gorunurluk filtresi ----------------------------------------------
// App.tsx:466-493 guard mantiginin sekme karsiligi. Persist'ten gelen ama
// rolun goremeyecegi sekmeler elenir; kapatma/acmada da referans budur.
const OPS_MANAGER_ENG: EngineeringPage[] = [
  "users",
  "responsibility-areas",
  "bulk-notify",
  // Uzaktan bakim izni: musteri tarafindaki sorumlu kisi durumu GORMELI
  // (kim, ne zamana kadar izin vermis). Izin VERME yetkisi backend'de
  // yalnizca engineer'dadir; sayfa `can_grant` ile kendini kisitlar.
  "remote-access",
];
const ENGINEER_ENG: EngineeringPage[] = [
  "devices",
  "live-values",
  "users",
  "responsibility-areas",
  "bulk-notify",
  "grid",
  "outbound",
  "api-access",
  "backups",
  "license",
  // Cevrimdisi harita: saha oncesi hazirlik isi, engineer da yapabilmeli.
  "offline-map",
  // Uzaktan bakim iznini VEREN tek rol engineer (bkz. backend
  // api/remote_access.py: _GRANT_ROLES).
  "remote-access",
];
// installer: tum engineering sayfalari.

export function canAccessRoute(route: TabRoute, role: UserRole): boolean {
  switch (route.kind) {
    case "page":
      return true; // home/alarms/faults/events herkes
    case "engineering": {
      const canEngineering =
        role === "installer" || role === "engineer" || role === "ops_manager";
      if (!canEngineering) return false;
      if (role === "installer") return true;
      if (role === "ops_manager") return OPS_MANAGER_ENG.includes(route.page);
      if (role === "engineer") return ENGINEER_ENG.includes(route.page);
      return false;
    }
    case "device-detail":
      return true; // cihaz detay herkes (gorunur cihaz zaten scope'lu)
  }
}

export function visibleTabs(tabs: Tab[], role: UserRole): Tab[] {
  const filtered = tabs.filter((tab) => canAccessRoute(tab.route, role));
  // Home her zaman ilk ve mevcut olmali.
  if (!filtered.some((tab) => tab.key === HOME_KEY)) {
    return [makeTab(HOME_ROUTE), ...filtered];
  }
  return filtered;
}

// ---- Persist (localStorage) — App.tsx loadPersistedRoute pattern'i ---------
const TABS_STORAGE_KEY = "hsl.tabs.v1";

type PersistedTabs = { tabKeys: string[]; activeKey: string; routes: Tab[] };

/** Sekmeleri (route + sira) ve aktif sekmeyi kaydet. */
export function saveTabs(tabs: Tab[], activeKey: string): void {
  if (typeof window === "undefined") return;
  try {
    const payload: PersistedTabs = {
      tabKeys: tabs.map((tterm) => tterm.key),
      activeKey,
      routes: tabs,
    };
    window.localStorage.setItem(TABS_STORAGE_KEY, JSON.stringify(payload));
  } catch {
    // localStorage devre disi / quota — sessizce yut (App pattern'i).
  }
}

/** Kaydedilmis sekmeleri yukle. Bozuk/eski/yok ise [home] doner. Rol filtresi
 *  cagiran tarafta (useTabs) visibleTabs ile uygulanir. */
export function loadTabs(): { tabs: Tab[]; activeKey: string } {
  const fallback = { tabs: [makeTab(HOME_ROUTE)], activeKey: HOME_KEY };
  if (typeof window === "undefined") return fallback;
  try {
    const raw = window.localStorage.getItem(TABS_STORAGE_KEY);
    if (!raw) return fallback;
    const parsed = JSON.parse(raw) as Partial<PersistedTabs>;
    const routes = Array.isArray(parsed.routes) ? parsed.routes : [];
    // Yalnizca gecerli route'lari al; key'i route'tan yeniden uret (tutarlilik).
    const valid: Tab[] = [];
    const seen = new Set<string>();
    for (const item of routes) {
      if (!item || typeof item !== "object" || !item.route) continue;
      const route = item.route as TabRoute;
      if (!isValidRoute(route)) continue;
      const key = routeKey(route);
      if (seen.has(key)) continue;
      seen.add(key);
      valid.push({ key, route });
    }
    if (valid.length === 0) return fallback;
    // Home yoksa basa ekle.
    if (!valid.some((tterm) => tterm.key === HOME_KEY)) {
      valid.unshift(makeTab(HOME_ROUTE));
    }
    const activeKey =
      typeof parsed.activeKey === "string" &&
      valid.some((tterm) => tterm.key === parsed.activeKey)
        ? parsed.activeKey
        : HOME_KEY;
    return { tabs: valid, activeKey };
  } catch {
    return fallback;
  }
}

function isValidRoute(route: TabRoute): boolean {
  if (!route || typeof route !== "object") return false;
  if (route.kind === "page") {
    return ["home", "alarms", "faults", "events"].includes(route.page);
  }
  if (route.kind === "engineering") {
    return typeof route.page === "string";
  }
  if (route.kind === "device-detail") {
    return typeof route.deviceId === "number" && Number.isFinite(route.deviceId);
  }
  return false;
}
