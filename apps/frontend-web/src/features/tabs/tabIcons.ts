/**
 * Sekme / menu ikonlari — TEK KAYNAK.
 *
 * Hem ust menu acilir listesi (EngineeringNav) hem de sekme seridi (TabBar)
 * buradan besleniyor; boylece bir sayfa her iki yerde de AYNI ikonla gozukur.
 * Ikon seti lucide-react (Header nav ile ayni).
 *
 * NOT: `tabModel.ts` bilerek React'siz tutuluyor, o yuzden ikon eslemesi
 * orada degil burada duruyor.
 */
import {
  Activity,
  ChartColumn,
  BadgeCheck,
  Bell,
  BellRing,
  BrickWall,
  Cpu,
  DatabaseBackup,
  FileCog,
  FileText,
  GitBranch,
  HeartPulse,
  Headset,
  Home,
  KeyRound,
  MapPinned,
  Megaphone,
  MonitorSmartphone,
  Network,
  Radio,
  Router,
  Settings,
  Share2,
  TriangleAlert,
  UserCog,
  Users,
  MapPin,
  Wrench,
  type LucideIcon
} from "lucide-react";

import type { EngineeringPage, PageMode, TabRoute } from "./tabModel";

export const ENGINEERING_PAGE_ICON: Record<EngineeringPage, LucideIcon> = {
  devices: Router,
  "device-config": FileCog,
  signals: Radio,
  grid: GitBranch,
  "live-values": Activity,
  "alarm-rules": BellRing,
  users: Users,
  "responsibility-areas": MapPinned,
  "bulk-notify": Megaphone,
  outbound: Share2,
  "api-access": KeyRound,
  notifications: Bell,
  "project-settings": Settings,
  license: BadgeCheck,
  backups: DatabaseBackup,
  "system-status": HeartPulse,
  "network-settings": Network,
  // Uzaktan bakim izni: "destek/kulaklik" — sayfa ICINDE durum ikonu kilit
  // (Lock/LockOpen), menu ikonuyla karismasin diye bilerek farkli.
  "remote-access": Headset,
  firewall: BrickWall,
  "offline-map": MapPin,
  "active-sessions": MonitorSmartphone,
  "field-tools": Wrench,
  // Kendi profili — menude gorunmez, kullanici menusunden acilir.
  profile: UserCog,
  "fault-analytics": ChartColumn,
};

const PAGE_ICON: Record<Exclude<PageMode, "engineering">, LucideIcon> = {
  home: Home,
  alarms: Bell,
  faults: TriangleAlert,
  events: FileText,
};

/** Bir sekme route'unun ikonu. */
export function routeIcon(route: TabRoute): LucideIcon {
  switch (route.kind) {
    case "page":
      return PAGE_ICON[route.page];
    case "engineering":
      return ENGINEERING_PAGE_ICON[route.page];
    case "device-detail":
      return Cpu;
    case "fault-detail":
      return TriangleAlert;
  }
}
