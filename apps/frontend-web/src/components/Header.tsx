import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";

import { NotificationBell } from "./NotificationBell";
import { useProjectSettings } from "./ProjectSettingsProvider";
import { WsStatusBadge } from "./WsStatusBadge";
import type { UserRole } from "../shared/types";
import type { WsConnectionState } from "../shared/useLiveValuesSocket";

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
  activePage: "home" | "alarms" | "faults" | "events" | "system-status" | "engineering";
  onChangePage: (page: "home" | "alarms" | "faults" | "events" | "system-status" | "engineering") => void;
};

export function Header({
  fullName,
  role,
  accessToken,
  wsState,
  onLogout,
  onSettings,
  isEngineeringView,
  onToggleEngineering,
  activePage,
  onChangePage
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
        <nav className="header-nav header-nav--framed">
          <button className={activePage === "home" ? "active" : ""} onClick={() => onChangePage("home")}>
            {t("header.home")}
          </button>
          <button className={activePage === "alarms" ? "active" : ""} onClick={() => onChangePage("alarms")}>
            {t("header.alarms")}
          </button>
          <button className={activePage === "faults" ? "active" : ""} onClick={() => onChangePage("faults")}>
            {t("header.faults")}
          </button>
          <button className={activePage === "events" ? "active" : ""} onClick={() => onChangePage("events")}>
            {t("header.events")}
          </button>
          <button
            className={activePage === "system-status" ? "active" : ""}
            onClick={() => onChangePage("system-status")}
          >
            {t("header.systemStatus")}
          </button>
        </nav>
      </div>

      <div className="header-right">
        {wsState ? <WsStatusBadge state={wsState} /> : null}
        {role === "engineer" || role === "installer" ? (
          <button
            className={`engineering-btn ${isEngineeringView ? "active" : ""}`}
            onClick={() => onToggleEngineering?.()}
          >
            {t("header.engineering")}
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
                else if (link.startsWith("/system-status")) onChangePage("system-status");
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
