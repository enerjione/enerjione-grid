/**
 * DeviceAlarmsCard — Genel Bakis'ta bu cihazin alarmlari (ayri kart).
 *
 * fetchAlarmEvents -> device_id filtreli. signal_key prefix'inden kaynak
 * (master/sat01/sat02) turetilir; aktif kaynaga gore filtre (sadece secili
 * kanalin alarmlari). Her satirda kaynak rozeti + baslik + zaman + seviye.
 */

import { useMemo } from "react";
import { useTranslation } from "react-i18next";

import { formatDateTime, formatRelative } from "../../shared/format";
import {
  SOURCES,
  signalSourceOf,
  sourceLabel,
  sourceTone
} from "../signals/signalCatalogConstants";
import type { AlarmEvent, SignalSource } from "../../shared/types";

type Props = {
  /** Cihazin alarmlari (DeviceDetailPage tek fetch eder). */
  alarms: AlarmEvent[];
  /** Aktif kaynak — sadece bu kanalin alarmlari gosterilir. */
  activeSource: SignalSource;
  limit?: number;
};

// Kaynak etiketi/tonu TEK KAYNAKTAN gelir (signalCatalogConstants). Elle
// yazilmis bir sozluk, Pole Master Kit'in dokuz uydusunda eksik kalirdi.
const SRC_META: Record<SignalSource, { label: string; tone: string }> =
  Object.fromEntries(
    SOURCES.map((s) => [s, { label: sourceLabel(s), tone: sourceTone(s) }])
  ) as Record<SignalSource, { label: string; tone: string }>;

const sourceOf = signalSourceOf;

function levelTone(level: string): "err" | "warn" | "info" {
  const l = level.toLowerCase();
  if (l === "critical" || l === "high" || l === "error") return "err";
  if (l === "warning" || l === "medium") return "warn";
  return "info";
}

export function DeviceAlarmsCard({ alarms, activeSource, limit = 50 }: Props) {
  const { t } = useTranslation();

  // Aktif kaynagin AKTIF alarmlari (giderilen/reset olan GOSTERILMEZ).
  const rows = useMemo(() => {
    return alarms
      .filter((a) => sourceOf(a.signal_key) === activeSource && !a.reset)
      .sort((a, b) => new Date(b.created_at).getTime() - new Date(a.created_at).getTime())
      .slice(0, limit);
  }, [alarms, activeSource, limit]);

  if (rows.length === 0) {
    return (
      <div className="device-alarms-empty is-clear">
        <span className="material-symbols-outlined">check_circle</span>
        <p>{t("deviceDetail.alarms.none", { source: SRC_META[activeSource].label })}</p>
      </div>
    );
  }

  return (
    <ul className="device-alarms-list">
      {rows.map((a) => {
        const src = sourceOf(a.signal_key);
        const tone = levelTone(a.level);
        const meta = SRC_META[src];
        // SCADA durum: onaylanmis ama hala aktif (reset olmadi) -> "Onaylandi",
        // aksi -> acik alarm (seviyeye gore). Reset olanlar zaten listede yok.
        const acked = a.acknowledged;
        return (
          <li key={a.id} className={`device-alarm-row tone-${tone}${acked ? " is-acked" : ""}`}>
            <span className={`device-alarm-icon tone-${tone}${acked ? " is-acked" : ""}`}>
              <span className="material-symbols-outlined">
                {acked ? "task_alt" : tone === "err" ? "error" : "warning"}
              </span>
            </span>
            <div className="device-alarm-body">
              <span className="device-alarm-title">{a.title}</span>
              <span className="device-alarm-meta">
                <span className={`device-alarm-src tone-${meta.tone}`}>{meta.label}</span>
                <span className="device-alarm-time" title={formatRelative(a.created_at)}>
                  {formatDateTime(a.created_at, {
                    day: "2-digit",
                    month: "2-digit",
                    hour: "2-digit",
                    minute: "2-digit",
                  })}
                </span>
                {acked && a.acknowledged_at ? (
                  <span className="device-alarm-acked-at">
                    · {t("deviceDetail.alarms.ackedAt")} {formatDateTime(a.acknowledged_at, { hour: "2-digit", minute: "2-digit" })}
                  </span>
                ) : null}
              </span>
            </div>
            {acked ? (
              <span className="device-alarm-badge is-acked">{t("deviceDetail.alarms.acknowledged")}</span>
            ) : (
              <span className={`device-alarm-badge tone-${tone}`}>
                {t(`deviceDetail.alarms.level.${tone}`)}
              </span>
            )}
          </li>
        );
      })}
    </ul>
  );
}
