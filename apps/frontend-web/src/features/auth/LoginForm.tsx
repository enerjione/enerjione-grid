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
      {/* Sayfa basinda dil secici — ekranin sagina sabit. Login oncesi henuz
          oturum olmadigi icin secim sadece localStorage + i18next state'ini
          gunceller; giris sonrasi profil ayarlarindan kalici tercih kaydedilir. */}
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
      <div className="login-shell">
        <form className="login-card" onSubmit={handleSubmit} autoComplete="off">
          {/* Logo:
              - Cache'ten / backend'ten gelen DB logosu varsa onu goster.
              - Hic logo bilgisi yoksa (cache miss + henuz fetch tamamlanmadi
                veya backend'de kayit yok) statik default PNG gosterilir.
              Eski problem: ayarlar yuklenirken placeholder div'i gosteriyorduk
              ve cache hit oldugunda da logo gozukmuyordu. Artik: settings
              senkron cache'ten geliyor; customer_logo varsa anında render. */}
          <img
            className="customer-logo"
            src={settings.customer_logo || "/customer-logo.png"}
            alt={settings.customer_name || t("login.customerLogoAlt")}
            onError={(event) => {
              event.currentTarget.src = "/customer-logo-placeholder.svg";
            }}
          />
          <div className="login-form-fields">
            <h2>{t("login.title")}</h2>
            <label>
              {t("login.username")}
              <input
                name="hsl-login-username"
                autoComplete="off"
                value={username}
                onChange={(event) => setUsername(event.target.value)}
                required
              />
            </label>
            <label>
              {t("login.password")}
              <div className="password-input-wrap">
                <input
                  type={showPassword ? "text" : "password"}
                  name="hsl-login-password"
                  autoComplete="new-password"
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
                    <svg viewBox="0 0 24 24" width="18" height="18" aria-hidden="true">
                      <path d="M17.94 17.94A10.86 10.86 0 0 1 12 19.5C7 19.5 2.73 16.39 1 12c.84-2.13 2.29-3.95 4.11-5.23" />
                      <path d="M10.58 10.58A2 2 0 0 0 13.42 13.42" />
                      <path d="M9.88 5.08A11.28 11.28 0 0 1 12 4.5c5 0 9.27 3.11 11 7.5a11.85 11.85 0 0 1-1.67 2.8" />
                      <path d="M1 1L23 23" />
                    </svg>
                  ) : (
                    <svg viewBox="0 0 24 24" width="18" height="18" aria-hidden="true">
                      <path d="M1 12c1.73-4.39 6-7.5 11-7.5s9.27 3.11 11 7.5c-1.73 4.39-6 7.5-11 7.5S2.73 16.39 1 12z" />
                      <circle cx="12" cy="12" r="3" />
                    </svg>
                  )}
                </button>
              </div>
            </label>
            <label className="login-remember">
              <input
                type="checkbox"
                checked={remember}
                onChange={(event) => setRemember(event.target.checked)}
              />
              <span>{t("login.remember")}</span>
            </label>
            <button type="submit" disabled={loading}>
              {loading ? t("login.submitting") : t("login.submit")}
            </button>
          </div>
          <img className="form-logo-bottom" src="/form-logo.png" alt="Form Elektrik" />
        </form>

        <aside className="login-visual">
          {/* Proje Ayarlari'ndan login_image yuklenmisse onu, yoksa default
              statik gorseli goster. INSTALLER ayar uzerinden istedigi gorseli
              kullanabilir (sirket fotografi / saha cihaz cekimi vs). */}
          <img
            className="visual-image"
            src={settings.login_image || "/login-visual.png"}
            alt={t("login.visualAlt")}
          />
        </aside>
      </div>
    </div>
  );
}
