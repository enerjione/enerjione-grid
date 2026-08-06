/** Olay listesi ve filtreler için i18n etiketleri. API hâlâ İngilizce kod kullanır. */

import i18n from "../../shared/i18n";

const SEVERITY_KEY: Record<string, string> = {
  info: "events.severity.info",
  warning: "events.severity.warning",
  error: "events.severity.error",
  critical: "events.severity.critical",
  debug: "events.severity.debug",
};

const CATEGORY_KEY: Record<string, string> = {
  auth: "events.category.auth",
  user: "events.category.user",
  alarm: "events.category.alarm",
  "alarm-rule": "events.category.alarmRule",
  alarm_assignment: "events.category.alarmAssignment",
  alarm_comment: "events.category.alarmComment",
  notification: "events.category.notification",
  settings: "events.category.settings",
  "project-settings": "events.category.projectSettings",
  outbound: "events.category.outbound",
  outbound_target: "events.category.outboundTarget",
  telemetry: "events.category.telemetry",
  device: "events.category.device",
  signal: "events.category.signal",
  gateway: "events.category.gateway",
  grid: "events.category.grid",
  "responsibility-area": "events.category.responsibilityArea",
  responsibility_area: "events.category.responsibilityArea",
  fault: "events.category.fault",
  fault_assignment: "events.category.faultAssignment",
  fault_comment: "events.category.faultComment",
  system: "events.category.system",
  security: "events.category.security",
};

/** Bilinmeyen kategori string'ini insan okur formata çevirir:
 *    "alarm-rule" -> "Alarm rule" */
function _humanize(category: string): string {
  if (!category) return i18n.t("events.category.other");
  const cleaned = category.replace(/[_-]+/g, " ").trim();
  if (!cleaned) return i18n.t("events.category.other");
  return cleaned.charAt(0).toUpperCase() + cleaned.slice(1);
}

export function severityLabelTr(severity: string): string {
  const key = SEVERITY_KEY[severity];
  return key ? i18n.t(key) : severity;
}

export function categoryLabelTr(category: string): string {
  const key = CATEGORY_KEY[category];
  return key ? i18n.t(key) : _humanize(category);
}

export function categoryFilterLabel(category: string): string {
  return categoryLabelTr(category);
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
  fault_comment: "alarm",
  system: "system",
  security: "auth",
};

export function categoryPillClass(category: string): string {
  const mod = CATEGORY_MOD[category] ?? "other";
  return `event-pill event-pill-cat event-pill-cat--${mod}`;
}
