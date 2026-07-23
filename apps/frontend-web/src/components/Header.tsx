import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { Home, Bell, TriangleAlert, FileText, Settings, type LucideIcon } from "lucide-react";

import { NotificationBell } from "./NotificationBell";
import { HeaderSearch } from "./HeaderSearch";
import { useProjectSettings } from "./ProjectSettingsProvider";
import type { DeviceRow, Line, Region, UserRole } from "../shared/types";
import type { WsConnectionState } from "../shared/useLiveValuesSocket";

type NavPage = "home" | "alarms" | "faults" | "events" | "engineering";
type DeviceTopology = Map<number, { regionId: number; regionName: string; lineId: number; lineName: string }>;

type Props = {
  fullName?: string;
  role?: UserRole;
  /** Bildirim merkezi icin oturum token'i; varsa zil header'da gozukur. */
  accessToken?: string;
  /** Canli veri WS baglantisinin durumu — header'da rozet olarak gosterilir. */
  wsState?: WsConnectionState;
  onLogout?: () => void;
  onSettings?: () => void;
  isEngineeringView?: boolean;
  onToggleEngineering?: () => void;
  onOpenSystemStatus?: () => void;
  activePage: NavPage;
  onChangePage: (page: NavPage) => void;
  // Global arama (cihaz + hat + bolge).
  devices: DeviceRow[];
  regions: Region[];
  lines: Line[];
  deviceTopology: DeviceTopology;
  onOpenDevice: (deviceId: number) => void;
  onSelectRegion: (regionId: number) => void;
  onSelectLine: (lineId: number) => void;
};

// Nav sekmeleri: sayfa + i18n anahtar + lucide ikon.
const NAV_ITEMS: { page: Exclude<NavPage, "engineering">; key: string; Icon: LucideIcon }[] = [
  { page: "home", key: "header.home", Icon: Home },
  { page: "alarms", key: "header.alarms", Icon: Bell },
  { page: "faults", key: "header.faults", Icon: TriangleAlert },
  { page: "events", key: "header.events", Icon: FileText },
];

export function Header({
  fullName,
  role,
  accessToken,
  onLogout,
  onSettings,
  isEngineeringView,
  onToggleEngineering,
  onOpenSystemStatus,
  activePage,
  onChangePage,
  devices,
  regions,
  lines,
  deviceTopology,
  onOpenDevice,
  onSelectRegion,
  onSelectLine
}: Props) {
  const { settings } = useProjectSettings();
  const { t } = useTranslation();
  const [menuOpen, setMenuOpen] = useState(false);
  const menuRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    const handleOutsideClick = (event: MouseEvent) => {
      if (!menuRef.current?.contains(event.target as Node)) {
        setMenuOpen(false);
      }
    };
    document.addEventListener("mousedown", handleOutsideClick);
    return () => document.removeEventListener("mousedown", handleOutsideClick);
  }, []);

  const initials =
    fullName
      ?.split(" ")
      .filter(Boolean)
      .slice(0, 2)
      .map((item) => item[0]?.toUpperCase())
      .join("") || "U";
  const roleLabel =
    role === "installer"
      ? t("roles.installer")
      : role === "engineer"
      ? t("roles.engineer")
      : role === "operator"
      ? t("roles.operator")
      : role === "ops_manager"
      ? t("roles.ops_manager")
      : t("roles.user");

  return (
    <header className="header">
      <div className="header-left">
        <div className="brand-logo-wrap">
          <img src="/logo.png" alt="EnerjiOne" className="logo" />
        </div>
        {/* Customer logosu yalnizca proje ayarlarinda kullanici yukledigi
            zaman gosterilir. Bos ise divider + logo blogu hic render edilmez
            (header daha temiz gorunur, ilk kurulumda placeholder spam yok). */}
        {(() => {
          const customerLogoSrc =
            settings.customer_logo_light || settings.customer_logo || "";
          if (!customerLogoSrc) return null;
          return (
            <>
              <span className="header-divider" />
              <div className="customer-logo-wrap">
                <img
                  className="header-customer-logo"
                  src={customerLogoSrc}
                  alt={settings.customer_name || t("header.customerLogoAlt")}
                  onError={(event) => {
                    // Yukleme basarisiz olursa img'i gizle (broken-image yerine)
                    event.currentTarget.style.display = "none";
                  }}
                />
              </div>
            </>
          );
        })()}
        <nav className="header-nav">
          {NAV_ITEMS.map(({ page, key, Icon }) => (
            <button
              key={page}
              className={`header-nav-btn${activePage === page ? " active" : ""}`}
              onClick={() => onChangePage(page)}
            >
              <Icon size={17} strokeWidth={2} />
              <span>{t(key)}</span>
            </button>
          ))}
        </nav>
      </div>

      <div className="header-right">
        {/* Global cihaz + bolge aramasi — sag tarafta, cark'in solunda */}
        <HeaderSearch
          devices={devices}
          regions={regions}
          lines={lines}
          deviceTopology={deviceTopology}
          onOpenDevice={onOpenDevice}
          onSelectRegion={onSelectRegion}
          onSelectLine={onSelectLine}
        />
        {/* Muhendislik/ayarlar: tum yetkili rollerde artik sadece cark ikonu. */}
        {role === "engineer" || role === "installer" || role === "ops_manager" ? (
          <button
            className={`engineering-btn engineering-btn--icon-only ${isEngineeringView ? "active" : ""}`}
            onClick={() => onToggleEngineering?.()}
            title={t("header.engineering")}
            aria-label={t("header.engineering")}
          >
            <Settings size={20} strokeWidth={2} />
          </button>
        ) : null}

        {/* Bildirim zili + kullanici menusu — ayni cerceve icinde gruplu */}
        <div className="header-user-cluster">
          {accessToken ? (
            <NotificationBell
              token={accessToken}
              onNavigate={(link) => {
                if (link.startsWith("/alarms")) onChangePage("alarms");
                else if (link.startsWith("/faults")) onChangePage("faults");
                else if (link.startsWith("/events")) onChangePage("events");
                else if (link.startsWith("/system-status")) onOpenSystemStatus?.();
              }}
            />
          ) : null}

          <span className="header-user-cluster-divider" aria-hidden="true" />

          <div className="profile-menu" ref={menuRef}>
            <button className="profile-trigger" onClick={() => setMenuOpen((prev) => !prev)}>
              <div className="profile-text">
                <strong>{fullName ?? t("header.user")}</strong>
                <small>{roleLabel}</small>
              </div>
              <div className="profile-avatar">{initials}</div>
            </button>

            {menuOpen ? (
              <div className="profile-dropdown">
                <button
                  onClick={() => {
                    setMenuOpen(false);
                    onSettings?.();
                  }}
                >
                  {t("header.settings")}
                </button>

                <button
                  onClick={() => {
                    setMenuOpen(false);
                    onLogout?.();
                  }}
                >
                  {t("header.logout")}
                </button>
              </div>
            ) : null}
          </div>
        </div>
      </div>
    </header>
  );
}
