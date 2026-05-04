import { useEffect, useState, type FormEvent } from "react";

import { useProjectSettings } from "../../components/ProjectSettingsProvider";

type Props = {
  onSubmit: (username: string, password: string, remember: boolean) => Promise<void>;
  loading: boolean;
  error?: string;
};

const REMEMBER_STORAGE_KEY = "hsl.login.remember";

export function LoginForm({ onSubmit, loading, error }: Props) {
  const { settings } = useProjectSettings();
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
      <div className="login-shell">
        <form className="login-card" onSubmit={handleSubmit} autoComplete="off">
          <img
            className="customer-logo"
            // Once DB'de kayitli logo (proje ayarlarindan); yoksa eski statik PNG.
            src={settings.customer_logo || "/customer-logo.png"}
            alt={settings.customer_name || "Müşteri Logo"}
            onError={(event) => {
              event.currentTarget.src = "/customer-logo-placeholder.svg";
            }}
          />
          <div className="login-form-fields">
            <h2>Giriş Yap</h2>
            <label>
              Kullanıcı Adı
              <input
                name="hsl-login-username"
                autoComplete="off"
                value={username}
                onChange={(event) => setUsername(event.target.value)}
                required
              />
            </label>
            <label>
              Şifre
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
                  aria-label={showPassword ? "Şifreyi gizle" : "Şifreyi göster"}
                  title={showPassword ? "Şifreyi gizle" : "Şifreyi göster"}
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
              <span>Beni hatırla</span>
            </label>
            {error ? <p className="error-text">{error}</p> : null}
            <button type="submit" disabled={loading}>
              {loading ? "Giriş yapılıyor..." : "Giriş"}
            </button>
          </div>
          <img className="form-logo-bottom" src="/form-logo.png" alt="Form Elektrik" />
        </form>

        <aside className="login-visual">
          <img className="visual-image" src="/login-visual.png" alt="Form Elektrik Görseli" />
        </aside>
      </div>
    </div>
  );
}
