import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";

import { NotificationBell } from "./NotificationBell";
import { useProjectSettings } from "./ProjectSettingsProvider";
import {
  LANGUAGE_LABELS,
  SUPPORTED_LANGUAGES,
  isSupportedLanguage,
  type SupportedLanguage,
} from "../shared/i18n";
import type { UserRole } from "../shared/types";

type Props = {
  fullName?: string;
  role?: UserRole;
  /** Bildirim merkezi icin oturum token'i; varsa zil header'da gozukur. */
  accessToken?: string;
  onLogout?: () => void;
  onSettings?: () => void;
  /** Profil dropdown'undan dil degisikligi. Mevcut dil ve secilen kod. */
  currentLanguage?: string | null;
  onChangeLanguage?: (code: SupportedLanguage) => void | Promise<void>;
  isEngineeringView?: boolean;
  onToggleEngineering?: () => void;
  activePage: "home" | "alarms" | "faults" | "events" | "system-status" | "engineering";
  onChangePage: (page: "home" | "alarms" | "faults" | "events" | "system-status" | "engineering") => void;
};

export function Header({
  fullName,
  role,
  accessToken,
  onLogout,
  onSettings,
  currentLanguage,
  onChangeLanguage,
  isEngineeringView,
  onToggleEngineering,
  activePage,
  onChangePage
}: Props) {
  const { settings, loading: settingsLoading } = useProjectSettings();
  const { t, i18n } = useTranslation();
  const activeLang: SupportedLanguage = isSupportedLanguage(currentLanguage)
    ? currentLanguage
    : isSupportedLanguage(i18n.language)
    ? i18n.language
    : "tr";
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
          <img src="/form-logo.png" alt="Form Elektrik" className="logo" />
        </div>
        <span className="header-divider" />
        <div className="customer-logo-wrap">
          {/* Ayarlar yuklenmeden eski statik PNG'yi gosterip sonra DB logosuyla
              degistirmek flash yaratiyordu — yuklenene kadar saydam placeholder. */}
          {settingsLoading ? (
            <div className="header-customer-logo header-customer-logo--placeholder" aria-hidden="true" />
          ) : (
            <img
              className="header-customer-logo"
              src={
                settings.customer_logo_light ||
                settings.customer_logo ||
                "/customer-logo-placeholder.svg"
              }
              alt={settings.customer_name || t("header.customerLogoAlt")}
              onError={(event) => {
                event.currentTarget.src = "/customer-logo-placeholder.svg";
              }}
            />
          )}
        </div>
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

                {/* Dil seçici — kullanıcı profil dropdown'ından doğrudan
                    arayüz dilini değiştirebilir. Seçim backend'e kaydedilir
                    (App.handleChangeLanguage), localStorage'a yazılır ve
                    react-i18next senkronize edilir. */}
                {onChangeLanguage ? (
                  <div className="profile-dropdown__lang">
                    <span className="profile-dropdown__lang-label">
                      {t("language.label")}
                    </span>
                    <div className="profile-dropdown__lang-options">
                      {SUPPORTED_LANGUAGES.map((code) => (
                        <button
                          key={code}
                          type="button"
                          className={`profile-dropdown__lang-btn ${
                            activeLang === code ? "is-active" : ""
                          }`}
                          onClick={() => {
                            void onChangeLanguage(code);
                          }}
                          title={LANGUAGE_LABELS[code]}
                        >
                          {code.toUpperCase()}
                        </button>
                      ))}
                    </div>
                  </div>
                ) : null}

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
