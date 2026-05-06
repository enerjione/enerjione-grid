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
  "alarm-rule": "Alarm kuralı",
  alarm_assignment: "Alarm ataması",
  alarm_comment: "Alarm yorumu",
  notification: "Bildirim gönderimi",
  settings: "Bildirim ayarları",
  "project-settings": "Proje ayarları",
  outbound: "Dış sistemlere aktarım",
  outbound_target: "Outbound hedefleri",
  telemetry: "Cihaz telemetrisi",
  device: "Cihaz yönetimi",
  signal: "Sinyal kataloğu",
  gateway: "Gateway yönetimi",
  grid: "Hat yönetimi",
  "responsibility-area": "Sorumluluk alanı",
  responsibility_area: "Sorumluluk alanı",
  fault: "Hat arızası",
  fault_assignment: "Arıza ataması",
  system: "Sistem"
};

/** Bazi kategoriler "key.like.this" yerine isim turetilebilir; yine de
 *  bilinmeyen bir kategori gelirse insan-okur formata cevirelim:
 *    "alarm-rule"           -> "Alarm rule"
 *    "responsibility_area"  -> "Responsibility area"
 *  Bu basliklar sadece fallback amacli; "Diger (xxx)" benzeri parantezli
 *  ham gosterim KULLANILMAZ. */
function _humanize(category: string): string {
  if (!category) return "Diğer";
  const cleaned = category.replace(/[_-]+/g, " ").trim();
  if (!cleaned) return "Diğer";
  return cleaned.charAt(0).toUpperCase() + cleaned.slice(1);
}

export function severityLabelTr(severity: string): string {
  return SEVERITY_LABELS[severity] ?? severity;
}

export function categoryLabelTr(category: string): string {
  return CATEGORY_LABELS[category] ?? _humanize(category);
}

/** Filtre seçeneklerinde: bilinmeyen kategori satır içi de gösterilsin. */
export function categoryFilterLabel(category: string): string {
  return CATEGORY_LABELS[category] ?? _humanize(category);
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
  "alarm-rule": "alarm",
  alarm_assignment: "alarm",
  alarm_comment: "alarm",
  notification: "notification",
  settings: "settings",
  "project-settings": "settings",
  outbound: "outbound",
  outbound_target: "outbound",
  telemetry: "telemetry",
  device: "telemetry",
  signal: "telemetry",
  gateway: "telemetry",
  grid: "telemetry",
  "responsibility-area": "user",
  responsibility_area: "user",
  fault: "alarm",
  fault_assignment: "alarm",
  system: "system"
};

export function categoryPillClass(category: string): string {
  const mod = CATEGORY_MOD[category] ?? "other";
  return `event-pill event-pill-cat event-pill-cat--${mod}`;
}
