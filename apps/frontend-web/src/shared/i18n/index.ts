/**
 * i18n bootstrap
 * ----------------
 * Uygulama acilirken (main.tsx) bu modul import edilir; i18next baslar,
 * tr/en kaynaklarini yukler ve baslangic dilini belirler:
 *
 *   1) localStorage["lang"] (kullanici daha once kaydetmisse)
 *   2) tarayici dili (tr-* -> tr, digerleri -> en)
 *   3) fallback: tr (proje default'u)
 *
 * Kullanici giris yaptiginda useApplyUserLanguage hook'u backend'den
 * gelen `me.language` ile bu degeri override eder.
 *
 * Yeni dil eklemek icin:
 *   - resources/<code>.json dosyasini olustur
 *   - SUPPORTED_LANGUAGES'a ekle
 *   - backend SUPPORTED_LANGUAGES set'ine ekle (apps/backend-api/app/api/auth.py)
 */
import i18n from "i18next";
import LanguageDetector from "i18next-browser-languagedetector";
import { initReactI18next } from "react-i18next";

import tr from "./resources/tr.json";
import en from "./resources/en.json";

export const SUPPORTED_LANGUAGES = ["tr", "en"] as const;
export type SupportedLanguage = (typeof SUPPORTED_LANGUAGES)[number];

export const LANGUAGE_LABELS: Record<SupportedLanguage, string> = {
  tr: "Türkçe",
  en: "English",
};

const LANG_STORAGE_KEY = "lang";

export function isSupportedLanguage(code: unknown): code is SupportedLanguage {
  return typeof code === "string" && (SUPPORTED_LANGUAGES as readonly string[]).includes(code);
}

void i18n
  .use(LanguageDetector)
  .use(initReactI18next)
  .init({
    resources: {
      tr: { translation: tr },
      en: { translation: en },
    },
    fallbackLng: "tr",
    supportedLngs: SUPPORTED_LANGUAGES as unknown as string[],
    nonExplicitSupportedLngs: true,
    interpolation: { escapeValue: false },
    detection: {
      order: ["localStorage", "navigator"],
      lookupLocalStorage: LANG_STORAGE_KEY,
      caches: ["localStorage"],
    },
    returnNull: false,
  });

export function setLanguage(code: SupportedLanguage): void {
  void i18n.changeLanguage(code);
  try {
    localStorage.setItem(LANG_STORAGE_KEY, code);
  } catch {
    // private mode / quota — ignore.
  }
}

export function getCurrentLanguage(): SupportedLanguage {
  const code = i18n.language?.split("-")[0];
  return isSupportedLanguage(code) ? code : "tr";
}

export default i18n;
