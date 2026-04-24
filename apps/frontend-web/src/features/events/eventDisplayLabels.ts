/** Olay listesi ve filtreler için Türkçe etiketler (API hâlâ İngilizce kod kullanır). */

const SEVERITY_LABELS: Record<string, string> = {
  info: "Bilgi",
  warning: "Uyarı",
  error: "Hata",
  critical: "Kritik",
  debug: "Ayrıntı"
};

const CATEGORY_LABELS: Record<string, string> = {
  auth: "Giriş ve oturum",
  user: "Kullanıcı yönetimi",
  alarm: "Alarmlar",
  notification: "Bildirim gönderimi",
  settings: "SMTP / SMS ayarları",
  outbound: "Dış sistemlere aktarım",
  telemetry: "Cihaz telemetrisi",
  system: "Sistem"
};

export function severityLabelTr(severity: string): string {
  return SEVERITY_LABELS[severity] ?? severity;
}

export function categoryLabelTr(category: string): string {
  return CATEGORY_LABELS[category] ?? `Diğer (${category})`;
}

/** Filtre seçeneklerinde: bilinmeyen kategori satır içi de gösterilsin. */
export function categoryFilterLabel(category: string): string {
  return CATEGORY_LABELS[category] ?? category;
}

export function severityPillClass(severity: string): string {
  const s = severity.toLowerCase();
  if (s === "info") return "event-pill event-pill-sev event-pill-sev--info";
  if (s === "warning") return "event-pill event-pill-sev event-pill-sev--warning";
  if (s === "error" || s === "critical") return "event-pill event-pill-sev event-pill-sev--error";
  if (s === "debug") return "event-pill event-pill-sev event-pill-sev--debug";
  return "event-pill event-pill-sev event-pill-sev--unknown";
}

const CATEGORY_MOD: Record<string, string> = {
  auth: "auth",
  user: "user",
  alarm: "alarm",
  notification: "notification",
  settings: "settings",
  outbound: "outbound",
  telemetry: "telemetry",
  system: "system"
};

export function categoryPillClass(category: string): string {
  const mod = CATEGORY_MOD[category] ?? "other";
  return `event-pill event-pill-cat event-pill-cat--${mod}`;
}
