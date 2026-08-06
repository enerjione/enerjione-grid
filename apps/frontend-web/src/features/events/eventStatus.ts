/**
 * Olay tipi -> Durum rozeti eslemesi + mesaj sadeleştirme.
 *
 * Olay tablosunda "ne oldu" bilgisi ayri bir DURUM sutununda rozet olarak
 * gosterilir (Tetiklendi / Normale dondu / Eklendi / Silindi...). Mesaj
 * sutunu ise yalnizca OZNEYI tasir: "Alarm kurali gerceklesti: Test alarmi"
 * yerine mesajda "Test alarmi" gorunur, "Tetiklendi" rozeti durumu soyler.
 *
 * ~150 olay tipi tek tek listelenmez: sik gorulen/anlami ozel olanlar EXACT
 * haritasinda, kalani sonek kurallariyla (_created, _failed, _updated...)
 * cozulur. Yeni olay tipleri otomatik olarak makul bir rozete duser.
 */
import i18n from "../../shared/i18n";
import type { SystemEvent } from "../../shared/types";
import { formatEventMessage } from "./formatEventMessage";

export type StatusTone = "ok" | "bad" | "warn" | "info" | "neutral";
export type EventStatus = { key: string; tone: StatusTone };

const EXACT: Record<string, EventStatus> = {
  alarm_triggered: { key: "triggered", tone: "bad" },
  alarm_created: { key: "triggered", tone: "bad" },
  alarm_auto_cleared: { key: "cleared", tone: "ok" },
  alarm_auto_cleared_acked: { key: "cleared", tone: "ok" },
  alarm_acknowledged: { key: "acknowledged", tone: "ok" },
  alarm_acknowledge_all: { key: "acknowledged", tone: "ok" },
  alarm_reset: { key: "reset", tone: "neutral" },
  alarm_reset_all: { key: "reset", tone: "neutral" },
  fault_opened: { key: "faultOpen", tone: "bad" },
  fault_resolved: { key: "cleared", tone: "ok" },
  fault_status_changed: { key: "updated", tone: "info" },
  user_login: { key: "login", tone: "info" },
  user_logout: { key: "logout", tone: "neutral" },
  login_failed_locked: { key: "failed", tone: "bad" },
  device_command_queued: { key: "queued", tone: "warn" },
  device_command_sent: { key: "sent", tone: "info" },
  device_command_result: { key: "completed", tone: "ok" },
  device_command_failed: { key: "failed", tone: "bad" },
  bulk_notification_sent: { key: "sent", tone: "ok" },
};

// SIRA ONEMLI: ilk eslesen kural kazanir. Ornegin `_dead_letter` kuyruk
// kelimesi icerse de basarisizliktir, bu yuzden "failed" grubu en ustte.
const SUFFIX_RULES: Array<[RegExp, EventStatus]> = [
  [
    /(_failed|_rejected|_error|_unreachable|_dead_letter|_denied|_locked|_tasmasi|_critical)$/,
    { key: "failed", tone: "bad" },
  ],
  [/(_warning|_backlog_high|_stale_devices_unknown)$/, { key: "warning", tone: "warn" }],
  [/(_queued|_scheduled|_requested|_started|_pending)$/, { key: "inProgress", tone: "warn" }],
  [/(_created|_added|_imported|_uploaded|_granted|_invited)$/, { key: "created", tone: "ok" }],
  [/(_deleted|_removed|_purged|_revoked|_forgotten)$/, { key: "deleted", tone: "neutral" }],
  [
    /(_updated|_changed|_edited|_reordered|_reversed|_synced|_resent|_extended|_assigned)$/,
    { key: "updated", tone: "info" },
  ],
  [
    /(_ok|_delivered|_ingested|_finished|_recovered|_applied|_dispatched|_sent|_setup|_enabled|_downloaded|_viewed|_reindexed|_ended)$/,
    { key: "success", tone: "ok" },
  ],
  [/_disabled$/, { key: "disabled", tone: "neutral" }],
];

/**
 * Durum filtresi -> backend ILIKE desenleri.
 *
 * Rozet SONEK kurallariyla turetildigi icin filtre de ayni sonekleri
 * kullanir; boylece "Silindi" secildiginde kullanici tabloda tam olarak
 * "Silindi" rozetli satirlari gorur. EXACT haritasindaki istisnalar
 * (alarm_triggered gibi) desen listesine ACIKCA eklenir.
 */
export const STATUS_FILTERS: Array<{ key: string; patterns: string[] }> = [
  {
    key: "triggered",
    patterns: ["alarm_triggered", "alarm_created", "fault_opened"],
  },
  {
    key: "cleared",
    patterns: ["alarm_auto_cleared", "alarm_auto_cleared_acked", "fault_resolved"],
  },
  { key: "acknowledged", patterns: ["alarm_acknowledged", "alarm_acknowledge_all"] },
  { key: "reset", patterns: ["alarm_reset", "alarm_reset_all"] },
  // NOT: desenlerde `_` LIKE joker'idir (tek karakter) ve literal alt
  // cizgiyi de eslestirir — ESCAPE gerektirmeden dogru calisir.
  {
    key: "created",
    patterns: ["%_created", "%_added", "%_imported", "%_uploaded", "%_granted", "%_invited"],
  },
  {
    key: "deleted",
    patterns: ["%_deleted", "%_removed", "%_purged", "%_revoked", "%_forgotten"],
  },
  {
    key: "updated",
    patterns: ["%_updated", "%_changed", "%_edited", "%_assigned", "%_synced"],
  },
  {
    key: "failed",
    patterns: ["%_failed", "%_rejected", "%_error", "%_unreachable", "%_denied", "%_locked"],
  },
  {
    key: "inProgress",
    patterns: ["%_queued", "%_scheduled", "%_requested", "%_started", "%_pending"],
  },
  { key: "login", patterns: ["user_login"] },
  { key: "logout", patterns: ["user_logout"] },
];

export function eventStatus(eventType: string): EventStatus {
  const exact = EXACT[eventType];
  if (exact) return exact;
  for (const [pattern, status] of SUFFIX_RULES) {
    if (pattern.test(eventType)) return status;
  }
  return { key: "info", tone: "neutral" };
}

export function eventStatusLabel(eventType: string): string {
  return i18n.t(`events.status.${eventStatus(eventType).key}`);
}

export function eventStatusClass(eventType: string): string {
  return `event-pill event-pill-status event-pill-status--${eventStatus(eventType).tone}`;
}

/**
 * Mesajin OZNESI: "Alarm kurali gerceklesti: Test alarmi" -> "Test alarmi".
 *
 * Sablonlarin buyuk cogunlugu "<eylem>: <ozne/detay>" bicimindedir; ilk
 * ": " ayracindan sonrasi alinir (detay kaybi olmaz, cok parcali kuyruk
 * aynen kalir). Ayrac yoksa ya da cok gec geliyorsa (eylem oneki degil,
 * icerik icindeki iki nokta) mesaj oldugu gibi doner. Tam metin, satirin
 * `title` tooltip'inde her zaman durur.
 */
export function eventSubject(event: SystemEvent): string {
  const full = formatEventMessage(event);
  const idx = full.indexOf(": ");
  if (idx > 0 && idx <= 60) {
    const rest = full.slice(idx + 2).trim();
    if (rest) return rest;
  }
  return full;
}
