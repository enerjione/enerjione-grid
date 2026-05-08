import { useTranslation } from "react-i18next";

import {
  LANGUAGE_LABELS,
  SUPPORTED_LANGUAGES,
  getCurrentLanguage,
  isSupportedLanguage,
  setLanguage,
  type SupportedLanguage,
} from "../shared/i18n";

type Props = {
  /** Tek satir kompakt sunum (login basligi yaninda kullanilir). */
  compact?: boolean;
};

/**
 * Login ekraninda ve genel UI'da kullanici-bagimsiz dil secici.
 * Login sonrasi kullanicinin kalici tercihi olarak kaydetmek icin
 * Ayarlar > Dil bolumu kullanilir; bu bilesen sadece localStorage tabanli
 * gecici secim yapar.
 */
export function LanguageSwitcher({ compact = false }: Props) {
  const { i18n } = useTranslation();
  const current: SupportedLanguage = isSupportedLanguage(i18n.language)
    ? i18n.language
    : getCurrentLanguage();
  return (
    <div className={`lang-switcher ${compact ? "lang-switcher--compact" : ""}`}>
      {SUPPORTED_LANGUAGES.map((code) => (
        <button
          key={code}
          type="button"
          className={`lang-switcher__btn ${current === code ? "is-active" : ""}`}
          onClick={() => setLanguage(code)}
          title={LANGUAGE_LABELS[code]}
        >
          {code.toUpperCase()}
        </button>
      ))}
    </div>
  );
}
