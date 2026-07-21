import i18n from "./i18n";

/**
 * Merkezi tarih/saat formatlama helper'i — `toLocaleString("tr-TR", ...)`
 * gibi hardcoded locale kullanim'larin yerine. `i18n.language` degerini
 * okuyup TR/EN moduna gore otomatik dogru locale doner.
 *
 * Kullanim:
 *   formatDateTime("2026-05-13T12:00:00Z")
 *   formatDateTime(new Date(), { dateStyle: "short" })
 *   formatDate(value) / formatTime(value)
 */

function resolveLocale(): string {
  // i18n.language Browser-detector tarafindan set edilir (tr / en / vb.).
  // Eksik durumlarda fallback browser locale.
  const lang = (i18n?.language || "").toString().toLowerCase();
  if (lang.startsWith("tr")) return "tr-TR";
  if (lang.startsWith("en")) return "en-US";
  return lang || (typeof navigator !== "undefined" ? navigator.language : "tr-TR");
}

function toDate(value: string | number | Date | null | undefined): Date | null {
  if (value == null) return null;
  if (value instanceof Date) return Number.isFinite(value.getTime()) ? value : null;
  const d = new Date(value);
  return Number.isFinite(d.getTime()) ? d : null;
}

const DEFAULT_DATETIME_OPTIONS: Intl.DateTimeFormatOptions = {
  year: "numeric",
  month: "2-digit",
  day: "2-digit",
  hour: "2-digit",
  minute: "2-digit",
};

export function formatDateTime(
  value: string | number | Date | null | undefined,
  options?: Intl.DateTimeFormatOptions,
): string {
  const d = toDate(value);
  if (d == null) return "—";
  try {
    return d.toLocaleString(resolveLocale(), options ?? DEFAULT_DATETIME_OPTIONS);
  } catch {
    return d.toLocaleString();
  }
}

const DEFAULT_DATE_OPTIONS: Intl.DateTimeFormatOptions = {
  year: "numeric",
  month: "2-digit",
  day: "2-digit",
};

export function formatDate(
  value: string | number | Date | null | undefined,
  options?: Intl.DateTimeFormatOptions,
): string {
  const d = toDate(value);
  if (d == null) return "—";
  try {
    return d.toLocaleDateString(resolveLocale(), options ?? DEFAULT_DATE_OPTIONS);
  } catch {
    return d.toLocaleDateString();
  }
}

const DEFAULT_TIME_OPTIONS: Intl.DateTimeFormatOptions = {
  hour: "2-digit",
  minute: "2-digit",
  second: "2-digit",
};

export function formatTime(
  value: string | number | Date | null | undefined,
  options?: Intl.DateTimeFormatOptions,
): string {
  const d = toDate(value);
  if (d == null) return "—";
  try {
    return d.toLocaleTimeString(resolveLocale(), options ?? DEFAULT_TIME_OPTIONS);
  } catch {
    return d.toLocaleTimeString();
  }
}

/** Saniye-bazli sure (orn alarm geciktirmesi) → "12s", "3dk 5s", "1sa 4dk". */
export function formatDuration(seconds: number): string {
  if (!Number.isFinite(seconds) || seconds < 0) return "—";
  const lang = (i18n?.language || "").toString().toLowerCase();
  const isTr = lang.startsWith("tr");
  const sec = Math.floor(seconds % 60);
  const min = Math.floor((seconds / 60) % 60);
  const hr = Math.floor(seconds / 3600);
  if (hr > 0) {
    return isTr ? `${hr}sa ${min}dk` : `${hr}h ${min}m`;
  }
  if (min > 0) {
    return isTr ? `${min}dk ${sec}s` : `${min}m ${sec}s`;
  }
  return isTr ? `${sec}s` : `${sec}s`;
}

/** Gecmis bir zamana gore goreli ifade → "az once", "12 dk once", "2 saat once",
 *  "3 gun once". Cihaz detay "Son iletisim" + olay zaman sutunu icin. */
export function formatRelative(
  value: string | number | Date | null | undefined,
): string {
  const d = toDate(value);
  if (d == null) return "—";
  const isTr = (i18n?.language || "").toString().toLowerCase().startsWith("tr");
  const diffSec = Math.max(0, (Date.now() - d.getTime()) / 1000);
  if (diffSec < 45) return isTr ? "az önce" : "just now";
  const min = Math.floor(diffSec / 60);
  if (min < 60) return isTr ? `${min} dk önce` : `${min} min ago`;
  const hr = Math.floor(diffSec / 3600);
  if (hr < 24) return isTr ? `${hr} saat önce` : `${hr} h ago`;
  const day = Math.floor(diffSec / 86400);
  if (day < 30) return isTr ? `${day} gün önce` : `${day} d ago`;
  // 30 gunden eski: tam tarih daha anlamli
  return formatDate(d);
}
