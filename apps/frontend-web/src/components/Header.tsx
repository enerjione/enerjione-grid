import { useEffect, useRef, useState } from "react";

import { useProjectSettings } from "./ProjectSettingsProvider";
import type { UserRole } from "../shared/types";

type Props = {
  fullName?: string;
  role?: UserRole;
  onLogout?: () => void;
  onSettings?: () => void;
  isEngineeringView?: boolean;
  onToggleEngineering?: () => void;
  activePage: "home" | "alarms" | "events" | "system-status" | "engineering";
  onChangePage: (page: "home" | "alarms" | "events" | "system-status" | "engineering") => void;
};

export function Header({
  fullName,
  role,
  onLogout,
  onSettings,
  isEngineeringView,
  onToggleEngineering,
  activePage,
  onChangePage
}: Props) {
  const { settings } = useProjectSettings();
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
    role === "installer" ? "Kurulumcu" : role === "engineer" ? "Mühendis" : role === "operator" ? "Operatör" : "Kullanıcı";

  return (
    <header className="header">
      <div className="header-left">
        <div className="brand-logo-wrap">
          <img src="/form-logo.png" alt="Form Elektrik" className="logo" />
        </div>
        <span className="header-divider" />
        <div className="customer-logo-wrap">
          <img
            className="header-customer-logo"
            // Once light, yoksa buyuk logoyu (header'da kucultur), o da yoksa default PNG.
            src={settings.customer_logo_light || settings.customer_logo || "/customer-logo-light.png"}
            alt={settings.customer_name || "Müşteri Logosu"}
            onError={(event) => {
              event.currentTarget.src = "/customer-logo-placeholder.svg";
            }}
          />
        </div>
        <nav className="header-nav">
          <button className={activePage === "home" ? "active" : ""} onClick={() => onChangePage("home")}>
            Anasayfa
          </button>
          <button className={activePage === "alarms" ? "active" : ""} onClick={() => onChangePage("alarms")}>
            Alarmlar
          </button>
          <button className={activePage === "events" ? "active" : ""} onClick={() => onChangePage("events")}>
            Olaylar
          </button>
          <button
            className={activePage === "system-status" ? "active" : ""}
            onClick={() => onChangePage("system-status")}
          >
            Sistem durumu
          </button>
        </nav>
      </div>

      <div className="header-right">
        {role === "engineer" || role === "installer" ? (
          <button
            className={`engineering-btn ${isEngineeringView ? "active" : ""}`}
            onClick={() => onToggleEngineering?.()}
          >
            Mühendislik
          </button>
        ) : null}

        <div className="profile-menu" ref={menuRef}>
          <button className="profile-trigger" onClick={() => setMenuOpen((prev) => !prev)}>
            <div className="profile-text">
              <strong>{fullName ?? "Kullanıcı"}</strong>
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
                Ayarlar
              </button>
              <button
                onClick={() => {
                  setMenuOpen(false);
                  onLogout?.();
                }}
              >
                Çıkış Yap
              </button>
            </div>
          ) : null}
        </div>
      </div>
    </header>
  );
}
