import { useEffect, useState, type FormEvent } from "react";
import { useTranslation } from "react-i18next";

import { useProjectSettings } from "../../components/ProjectSettingsProvider";
import {
  LANGUAGE_LABELS,
  SUPPORTED_LANGUAGES,
  isSupportedLanguage,
  setLanguage,
  type SupportedLanguage,
} from "../../shared/i18n";

type Props = {
  onSubmit: (username: string, password: string, remember: boolean) => Promise<void>;
  loading: boolean;
};

const REMEMBER_STORAGE_KEY = "hsl.login.remember";

export function LoginForm({ onSubmit, loading }: Props) {
  const { settings } = useProjectSettings();
  const { t, i18n } = useTranslation();
  const activeLang: SupportedLanguage = isSupportedLanguage(i18n.language)
    ? i18n.language
    : "tr";
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [remember, setRemember] = useState<boolean>(() => {
    if (typeof window === "undefined") return true;
    const raw = window.localStorage.getItem(REMEMBER_STORAGE_KEY);
    // Varsayılan: işaretli (kullanıcının çoğu zaman istediği davranış).
    return raw === null ? true : raw === "1";
  });

  useEffect(() => {
    window.localStorage.setItem(REMEMBER_STORAGE_KEY, remember ? "1" : "0");
  }, [remember]);

  const handleSubmit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    await onSubmit(username, password, remember);
  };

  return (
    <div className="login-page">
      <div className="login-lang-bar">
        <label className="login-lang-select">
          <span className="login-lang-select__label">{t("language.label")}</span>
          <select
            value={activeLang}
            onChange={(event) => setLanguage(event.target.value as SupportedLanguage)}
            aria-label={t("language.label")}
          >
            {SUPPORTED_LANGUAGES.map((code) => (
              <option key={code} value={code}>
                {LANGUAGE_LABELS[code]}
              </option>
            ))}
          </select>
        </label>
      </div>

      {/* Background split visuals */}
      <div className="login-bg-left" />
      <div className="login-bg-right" />
      <div className="login-bg-divider" />

      <div className="login-layout">
        {/* Sağ Taraf: Giriş Formu */}
        <main className="login-form-container">
          <div className="login-glass-card">
            {(() => {
              const customerLogoSrc = settings.customer_logo_light || settings.customer_logo || "";
              if (!customerLogoSrc) return null;
              return (
                <div className="login-customer-brand">
                  <img
                    className="login-customer-logo"
                    src={customerLogoSrc}
                    alt={settings.customer_name || t("login.customerLogoAlt")}
                    onError={(event) => {
                      event.currentTarget.style.display = "none";
                    }}
                  />
                </div>
              );
            })()}

            <form onSubmit={handleSubmit} autoComplete="off" className="login-form-core">
              <h2 className="login-form-title">{t("login.title")}</h2>

              <div className="login-input-group">
                <label htmlFor="hsl-login-username">{t("login.username")}</label>
                <div className="login-input-wrapper">
                  <svg className="login-input-icon" viewBox="0 0 24 24" width="18" height="18" aria-hidden="true" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                    <path d="M19 21v-2a4 4 0 0 0-4-4H9a4 4 0 0 0-4 4v2" />
                    <circle cx="12" cy="7" r="4" />
                  </svg>
                  <input
                    id="hsl-login-username"
                    name="hsl-login-username"
                    autoComplete="off"
                    placeholder="Kullanıcı adınızı giriniz"
                    value={username}
                    onChange={(event) => setUsername(event.target.value)}
                    required
                  />
                </div>
              </div>

              <div className="login-input-group">
                <label htmlFor="hsl-login-password">{t("login.password")}</label>
                <div className="login-input-wrapper">
                  <svg className="login-input-icon" viewBox="0 0 24 24" width="18" height="18" aria-hidden="true" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                    <rect width="18" height="11" x="3" y="11" rx="2" ry="2" />
                    <path d="M7 11V7a5 5 0 0 1 10 0v4" />
                  </svg>
                  <input
                    id="hsl-login-password"
                    type={showPassword ? "text" : "password"}
                    name="hsl-login-password"
                    autoComplete="new-password"
                    placeholder="Şifrenizi giriniz"
                    value={password}
                    onChange={(event) => setPassword(event.target.value)}
                    required
                  />
                  <button
                    type="button"
                    className="password-toggle-btn"
                    onClick={() => setShowPassword((prev) => !prev)}
                    aria-label={showPassword ? t("login.hidePassword") : t("login.showPassword")}
                    title={showPassword ? t("login.hidePassword") : t("login.showPassword")}
                  >
                    {showPassword ? (
                      <svg viewBox="0 0 24 24" width="18" height="18" aria-hidden="true" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                        <path d="M9.88 9.88a3 3 0 1 0 4.24 4.24" />
                        <path d="M10.73 5.08A10.43 10.43 0 0 1 12 5c7 0 10 7 10 7a13.16 13.16 0 0 1-1.67 2.68" />
                        <path d="M6.61 6.61A13.526 13.526 0 0 0 2 12s3 7 10 7a9.74 9.74 0 0 0 5.39-1.61" />
                        <line x1="2" x2="22" y1="2" y2="22" />
                      </svg>
                    ) : (
                      <svg viewBox="0 0 24 24" width="18" height="18" aria-hidden="true" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                        <path d="M2 12s3-7 10-7 10 7 10 7-3 7-10 7-10-7-10-7Z" />
                        <circle cx="12" cy="12" r="3" />
                      </svg>
                    )}
                  </button>
                </div>
              </div>

              <div className="login-form-options">
                <label className="login-remember">
                  <input
                    type="checkbox"
                    checked={remember}
                    onChange={(event) => setRemember(event.target.checked)}
                  />
                  <span className="checkbox-custom">
                    <svg viewBox="0 0 24 24" width="12" height="12" fill="none" stroke="currentColor" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round">
                      <polyline points="20 6 9 17 4 12" />
                    </svg>
                  </span>
                  <span>{t("login.remember")}</span>
                </label>
                <button type="button" className="login-forgot-btn">
                  Şifremi Unuttum?
                </button>
              </div>

              <button type="submit" className="login-submit-btn" disabled={loading}>
                <span>{loading ? t("login.submitting") : t("login.submit")}</span>
                {!loading && (
                  <svg viewBox="0 0 24 24" width="20" height="20" aria-hidden="true" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                    <line x1="5" y1="12" x2="19" y2="12" />
                    <polyline points="12 5 19 12 12 19" />
                  </svg>
                )}
              </button>
            </form>

            <div className="login-footer">
              <div className="login-footer-brand">
                <img src="/logo.png" alt="EnerjiOne" className="login-footer-logo" />
                <div className="login-footer-text">
                  <strong>EnerjiOne</strong>
                  <span>Enerjinizin Dijital Gücü</span>
                </div>
              </div>
              <div className="login-security-badge">
                <div className="login-security-icon">
                  <svg viewBox="0 0 24 24" width="16" height="16" aria-hidden="true" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                    <path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z" />
                    <path d="m9 12 2 2 4-4" />
                  </svg>
                </div>
                <div className="login-security-text">
                  <strong>Güvenli Bağlantı</strong>
                  <span>256-bit SSL şifreleme</span>
                </div>
              </div>
            </div>
          </div>
        </main>
      </div>
    </div>
  );
}
