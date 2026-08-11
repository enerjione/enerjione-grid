import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import {
  Home,
  Bell,
  TriangleAlert,
  FileText,
  ChartLine,
  Settings,
  LockOpen,
  type LucideIcon
} from "lucide-react";

import { NotificationBell } from "./NotificationBell";
import { HeaderSearch } from "./HeaderSearch";
import { useProjectSettings } from "./ProjectSettingsProvider";
import type { DeviceRow, Line, Region, UserRole } from "../shared/types";

type NavPage = "home" | "alarms" | "faults" | "events" | "engineering";
type DeviceTopology = Map<number, { regionId: number; regionName: string; lineId: number; lineName: string }>;

type Props = {
  fullName?: string;
  avatarUrl?: string | null;
  role?: UserRole;
  /** Bildirim merkezi icin oturum token'i; varsa zil header'da gozukur. */
  accessToken?: string;
  onLogout?: () => void;
  onSettings?: () => void;
  isEngineeringView?: boolean;
  onToggleEngineering?: () => void;
  onOpenSystemStatus?: () => void;
  /** Uzaktan bakim izni SU AN acik mi. Acikken header'da kalici uyari rozeti
   *  cikar — "acik unutma" bu ozelligin bilinen en buyuk riski. */
  remoteAccessActive?: boolean;
  /** Kalan sure metni ("3 sa 12 dk"). Bicimleme App'te yapilir: `components/`
   *  katmani `features/` altindan import etmiyor, bu sinir korunuyor. */
  remoteAccessLabel?: string;
  onOpenRemoteAccess?: () => void;
  activePage: NavPage;
  onChangePage: (page: NavPage) => void;
  /** "Ariza Analizi" sayfasini acar. Sayfa muhendislik agacinda yasiyor ama
   *  gunluk kullanilan bir ekran; ust menude olmasi isteniyor. */
  onOpenFaultAnalytics?: () => void;
  /** Analiz sekmesi su an acik mi (ust menude vurgulanir). */
  faultAnalyticsActive?: boolean;
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
  avatarUrl,
  role,
  accessToken,
  onLogout,
  onSettings,
  isEngineeringView,
  onToggleEngineering,
  onOpenSystemStatus,
  remoteAccessActive,
  remoteAccessLabel,
  onOpenRemoteAccess,
  activePage,
  onOpenFaultAnalytics,
  faultAnalyticsActive,
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
          {/* Ariza Analizi muhendislik agacinda yasiyor ama gunluk bakilan
              bir ekran; menu icinde aramak yerine ust seride alindi.
              Yetkisi olmayan (operator) rolde geri cagri gecilmez. */}
          {onOpenFaultAnalytics ? (
            <button
              className={`header-nav-btn${faultAnalyticsActive ? " active" : ""}`}
              onClick={onOpenFaultAnalytics}
            >
              <ChartLine size={17} strokeWidth={2} />
              <span>{t("engineering.nav.faultAnalytics")}</span>
            </button>
          ) : null}
        </nav>
      </div>

      <div className="header-right">
        {/* Canli veri rozeti header'dan KALDIRILDI (kullanici karari): surekli
            gorunen bir "canli/kopuk" isareti gunluk kullanimda gurultu
            yaratiyordu. Ayni rozet Sistem Durumu sayfasinda duruyor
            (features/system-status/SystemStatusPage.tsx) — bilgi kaybi yok,
            yalnizca dogru yere tasindi. */}
        {/* Uzaktan bakim izni ACIK uyarisi. Kapatilamaz ve sureli degil:
            erisim 8 saat surebilir, kaybolan bir bildirim bunu garanti
            edemez. Tiklaninca izin sayfasina goturur. */}
        {remoteAccessActive ? (
          <button
            type="button"
            className="header-remote-badge"
            onClick={() => onOpenRemoteAccess?.()}
            /* Rozet header'da SADECE SIMGE: eskiden metin + geri sayimla
               genis bir serit kapliyordu ve arama kutusunu sikistiriyordu.
               Ayrinti hover/odakta aciliyor. Uyarinin kendisi (nabiz atan
               nokta) her zaman gorunur kaliyor — asil is o. */
            aria-label={`${t("remoteAccess.badge.title")}${
              remoteAccessLabel ? ` — ${remoteAccessLabel}` : ""
            }`}
          >
            <span className="header-remote-dot" aria-hidden="true" />
            <LockOpen size={15} strokeWidth={2.2} aria-hidden="true" />
            {/* Ipucu: hover/odakta gorunur. aria-hidden — erisilebilir ad
                zaten butonun aria-label'inda ve orada sure de var. */}
            <span className="header-remote-tip" aria-hidden="true">
              <span className="header-remote-tip-text">
                {t("remoteAccess.badge.short")}
              </span>
              {remoteAccessLabel ? (
                <span className="header-remote-time">{remoteAccessLabel}</span>
              ) : null}
            </span>
          </button>
        ) : null}

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
              {/* Avatar HIC okunmuyordu: header her zaman bas harfleri
                  ciziyordu, bu yuzden profil fotografi kaydedilse bile
                  burada gorunmuyordu. */}
              <div className="profile-avatar">
                {avatarUrl ? <img src={avatarUrl} alt="" /> : initials}
              </div>
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
