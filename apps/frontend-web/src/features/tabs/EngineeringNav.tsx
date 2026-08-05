/**
 * Muhendislik ust menusu — 16 duz sekme yerine 5 gruplu acilir menu.
 *
 * Sekmeler yatayda tasip kayiyordu; ilgili sayfalar App.tsx'te zaten yorum
 * satirlariyla gruplanmisti (TOPOLOJI, IZLEME, EKIP, ENTEGRASYON, SISTEM),
 * o gruplama buraya tasindi. Ust ogenin uzerine gelince grup acilir ve her
 * satir ikon + baslik + kisa aciklama gosterir.
 *
 * Gorunurluk kurallari App.tsx'teki eski `session.role === ...` kosullarinin
 * BIREBIR karsiligidir; degistirirken ikisini birlikte dusun.
 */
import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import {
  Activity,
  Boxes,
  ChevronDown,
  Plug,
  ServerCog,
  UsersRound,
  type LucideIcon,
} from "lucide-react";

import type { UserRole } from "../../shared/types";
import { ENGINEERING_PAGE_ICON } from "./tabIcons";
import type { EngineeringPage } from "./tabModel";

// Satir ikonlari sekme seridiyle ORTAK haritadan gelir (tabIcons.ts) — ayni
// sayfa menude ve sekmede ayni ikonla gozuksun.
type NavItem = {
  page: EngineeringPage;
  labelKey: string;
  descKey: string;
  /** App.tsx'teki eski role kosulunun aynisi. */
  canSee: (role: UserRole) => boolean;
};

type NavGroup = {
  key: string;
  labelKey: string;
  Icon: LucideIcon;
  items: NavItem[];
};

const isInstaller = (role: UserRole) => role === "installer";
const isInstallerOrEngineer = (role: UserRole) => role === "installer" || role === "engineer";
const isTeamManager = (role: UserRole) =>
  role === "installer" || role === "engineer" || role === "ops_manager";

export const ENGINEERING_NAV_GROUPS: NavGroup[] = [
  {
    key: "setup",
    labelKey: "engineering.navGroups.setup",
    Icon: Boxes,
    items: [
      {
        page: "devices",
        labelKey: "engineering.nav.devices",
        descKey: "engineering.navDesc.devices",
        canSee: (role) => role !== "ops_manager",
      },
      {
        // FTP sunucu ayarlari + config sablonlari + toplu uygulama.
        // Yanlis ayar sahada koruma degerini bozar -> engineer/installer.
        page: "device-config",
        labelKey: "engineering.nav.deviceConfig",
        descKey: "engineering.navDesc.deviceConfig",
        canSee: isInstallerOrEngineer,
      },
      {
        page: "signals",
        labelKey: "engineering.nav.signals",
        descKey: "engineering.navDesc.signals",
        canSee: isInstaller,
      },
      {
        page: "grid",
        labelKey: "engineering.nav.grid",
        descKey: "engineering.navDesc.grid",
        canSee: isInstallerOrEngineer,
      },
    ],
  },
  {
    key: "monitoring",
    labelKey: "engineering.navGroups.monitoring",
    Icon: Activity,
    items: [
      {
        page: "live-values",
        labelKey: "engineering.nav.liveValues",
        descKey: "engineering.navDesc.liveValues",
        canSee: isInstallerOrEngineer,
      },
      {
        page: "alarm-rules",
        labelKey: "engineering.nav.alarmRules",
        descKey: "engineering.navDesc.alarmRules",
        canSee: isInstaller,
      },
    ],
  },
  {
    key: "team",
    labelKey: "engineering.navGroups.team",
    Icon: UsersRound,
    items: [
      {
        page: "users",
        labelKey: "engineering.nav.users",
        descKey: "engineering.navDesc.users",
        canSee: isTeamManager,
      },
      {
        page: "responsibility-areas",
        labelKey: "engineering.nav.responsibilityAreas",
        descKey: "engineering.navDesc.responsibilityAreas",
        canSee: isTeamManager,
      },
      {
        page: "bulk-notify",
        labelKey: "engineering.nav.bulkNotify",
        descKey: "engineering.navDesc.bulkNotify",
        canSee: isTeamManager,
      },
    ],
  },
  {
    key: "integrations",
    labelKey: "engineering.navGroups.integrations",
    Icon: Plug,
    items: [
      {
        page: "outbound",
        labelKey: "engineering.nav.outboundTargets",
        descKey: "engineering.navDesc.outboundTargets",
        canSee: isInstallerOrEngineer,
      },
      {
        page: "api-access",
        labelKey: "engineering.nav.apiAccess",
        descKey: "engineering.navDesc.apiAccess",
        canSee: isInstallerOrEngineer,
      },
      {
        page: "notifications",
        labelKey: "engineering.nav.notificationSettings",
        descKey: "engineering.navDesc.notificationSettings",
        canSee: isInstaller,
      },
    ],
  },
  {
    key: "system",
    labelKey: "engineering.navGroups.system",
    Icon: ServerCog,
    items: [
      {
        page: "project-settings",
        labelKey: "engineering.nav.projectSettings",
        descKey: "engineering.navDesc.projectSettings",
        canSee: isInstaller,
      },
      {
        page: "license",
        labelKey: "engineering.nav.license",
        descKey: "engineering.navDesc.license",
        canSee: isInstallerOrEngineer,
      },
      {
        page: "backups",
        labelKey: "engineering.nav.backups",
        descKey: "engineering.navDesc.backups",
        canSee: isInstallerOrEngineer,
      },
      {
        page: "system-status",
        labelKey: "header.systemStatus",
        descKey: "engineering.navDesc.systemStatus",
        canSee: isInstaller,
      },
      {
        // Appliance (mini PC) modu: cihazin kendi IP/DNS ayari. Yanlis ayar
        // cihazi kablolu agdan erisilemez yapabilir -> sadece installer.
        page: "network-settings",
        labelKey: "engineering.nav.networkSettings",
        descKey: "engineering.navDesc.networkSettings",
        canSee: isInstaller,
      },
      {
        // Uzaktan bakim izni: uretici destek ekibine SURELI erisim. Durumu
        // GORME yetkisi genis (ops_manager dahil) — musteri tarafindaki
        // sorumlu kisi kimin ne zamana kadar izin verdigini gorebilmeli.
        // Izin VERME yetkisi backend'de yalnizca engineer'dadir ve sayfa
        // bunu `can_grant` ile kendisi uygular.
        page: "remote-access",
        labelKey: "engineering.nav.remoteAccess",
        descKey: "engineering.navDesc.remoteAccess",
        canSee: isTeamManager,
      },
      {
        // Cevrimdisi harita: sahaya cikmadan ONCE indirilir. Yonetim
        // alaninda olmasi dogru -- indirilmis alanlar harita uzerinde
        // gorunur ve yeni alan buradan secilir.
        page: "offline-map",
        labelKey: "engineering.nav.offlineMap",
        descKey: "engineering.navDesc.offlineMap",
        canSee: isInstallerOrEngineer,
      },
      {
        page: "active-sessions",
        labelKey: "engineering.nav.activeSessions",
        descKey: "engineering.navDesc.activeSessions",
        canSee: isInstaller,
      },
    ],
  },
];

type Props = {
  role: UserRole;
  activePage: EngineeringPage;
  onSelect: (page: EngineeringPage) => void;
};

export function EngineeringNav({ role, activePage, onSelect }: Props) {
  const { t } = useTranslation();
  const [openKey, setOpenKey] = useState<string | null>(null);
  const navRef = useRef<HTMLElement | null>(null);
  // Fareyle gruplar arasi gecerken menunun titremesini onlemek icin kisa
  // gecikmeli kapanma; imlec menuye inerken aradaki bosluga girse bile acik
  // kalir.
  const closeTimer = useRef<number | null>(null);

  const cancelClose = () => {
    if (closeTimer.current !== null) {
      window.clearTimeout(closeTimer.current);
      closeTimer.current = null;
    }
  };

  const scheduleClose = () => {
    cancelClose();
    closeTimer.current = window.setTimeout(() => setOpenKey(null), 180);
  };

  useEffect(() => cancelClose, []);

  // Disari tiklama + Esc ile kapat.
  useEffect(() => {
    if (openKey === null) return;
    const onPointerDown = (event: PointerEvent) => {
      const target = event.target;
      if (target instanceof Node && navRef.current?.contains(target)) return;
      setOpenKey(null);
    };
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") setOpenKey(null);
    };
    document.addEventListener("pointerdown", onPointerDown);
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("pointerdown", onPointerDown);
      document.removeEventListener("keydown", onKeyDown);
    };
  }, [openKey]);

  const visibleGroups = ENGINEERING_NAV_GROUPS.map((group) => ({
    ...group,
    items: group.items.filter((item) => item.canSee(role)),
  })).filter((group) => group.items.length > 0);

  return (
    <nav className="eng-nav" ref={navRef} aria-label={t("engineering.navGroups.aria")}>
      {visibleGroups.map((group) => {
        const isActiveGroup = group.items.some((item) => item.page === activePage);
        const isOpen = openKey === group.key;
        return (
          <div
            key={group.key}
            className="eng-nav-group"
            onMouseEnter={() => {
              cancelClose();
              setOpenKey(group.key);
            }}
            onMouseLeave={scheduleClose}
          >
            <button
              type="button"
              className={`eng-nav-trigger${isActiveGroup ? " active" : ""}`}
              aria-expanded={isOpen}
              aria-haspopup="true"
              onClick={() => setOpenKey(isOpen ? null : group.key)}
              onFocus={() => {
                cancelClose();
                setOpenKey(group.key);
              }}
            >
              <group.Icon size={16} strokeWidth={2} />
              <span>{t(group.labelKey)}</span>
              <ChevronDown size={14} strokeWidth={2} className="eng-nav-chevron" />
            </button>

            {isOpen ? (
              <div className="eng-nav-menu" role="menu">
                {group.items.map((item) => {
                  const ItemIcon = ENGINEERING_PAGE_ICON[item.page];
                  return (
                  <button
                    key={item.page}
                    type="button"
                    role="menuitem"
                    className={`eng-nav-item${item.page === activePage ? " active" : ""}`}
                    onClick={() => {
                      setOpenKey(null);
                      onSelect(item.page);
                    }}
                  >
                    <span className="eng-nav-item-icon">
                      <ItemIcon size={17} strokeWidth={2} />
                    </span>
                    <span className="eng-nav-item-text">
                      <strong>{t(item.labelKey)}</strong>
                      <small>{t(item.descKey)}</small>
                    </span>
                  </button>
                  );
                })}
              </div>
            ) : null}
          </div>
        );
      })}
    </nav>
  );
}
