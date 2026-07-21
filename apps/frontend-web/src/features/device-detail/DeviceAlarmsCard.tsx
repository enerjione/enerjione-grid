/**
 * DeviceAlarmsCard — Genel Bakis'ta bu cihazin alarmlari (ayri kart).
 *
 * fetchAlarmEvents -> device_id filtreli. signal_key prefix'inden kaynak
 * (master/sat01/sat02) turetilir; aktif kaynaga gore filtre (sadece secili
 * kanalin alarmlari). Her satirda kaynak rozeti + baslik + zaman + seviye.
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";

import { fetchAlarmEvents } from "../../shared/api";
import { formatDateTime, formatRelative } from "../../shared/format";
import type { AlarmEvent, SignalSource } from "../../shared/types";

type Props = {
  token: string;
  deviceId: number;
  /** Aktif kaynak — sadece bu kanalin alarmlari gosterilir. */
  activeSource: SignalSource;
  limit?: number;
};

const SRC_META: Record<SignalSource, { label: string; tone: string }> = {
  master: { label: "Master", tone: "master" },
  sat01: { label: "Satellite 01", tone: "green" },
  sat02: { label: "Satellite 02", tone: "amber" },
};

function sourceOf(signalKey: string | null | undefined): SignalSource {
  const k = signalKey ?? "";
  if (k.startsWith("sat01.")) return "sat01";
  if (k.startsWith("sat02.")) return "sat02";
  return "master";
}

function levelTone(level: string): "err" | "warn" | "info" {
  const l = level.toLowerCase();
  if (l === "critical" || l === "high" || l === "error") return "err";
  if (l === "warning" || l === "medium") return "warn";
  return "info";
}

export function DeviceAlarmsCard({ token, deviceId, activeSource, limit = 6 }: Props) {
  const { t } = useTranslation();
  const [alarms, setAlarms] = useState<AlarmEvent[]>([]);
  const [loading, setLoading] = useState(true);

  const reload = useCallback(async () => {
    if (!token) return;
    setLoading(true);
    try {
      const all = await fetchAlarmEvents(token).catch(() => [] as AlarmEvent[]);
      setAlarms(all.filter((a) => a.device_id === deviceId));
    } finally {
      setLoading(false);
    }
  }, [token, deviceId]);

  useEffect(() => {
    void reload();
  }, [reload]);

  // Aktif kaynagin alarmlari (zamana gore, en yeni ustte).
  const rows = useMemo(() => {
    return alarms
      .filter((a) => sourceOf(a.signal_key) === activeSource)
      .sort((a, b) => new Date(b.created_at).getTime() - new Date(a.created_at).getTime())
      .slice(0, limit);
  }, [alarms, activeSource, limit]);

  if (loading && rows.length === 0) {
    return (
      <div className="device-alarms-empty">
        <span className="btn-spinner" aria-hidden="true" />
      </div>
    );
  }

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
        const done = a.reset || a.acknowledged;
        return (
          <li key={a.id} className={`device-alarm-row tone-${tone}${done ? " is-done" : ""}`}>
            <span className={`device-alarm-icon tone-${tone}`}>
              <span className="material-symbols-outlined">
                {done ? "check_circle" : tone === "err" ? "error" : "warning"}
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
              </span>
            </div>
            {done ? (
              <span className="device-alarm-badge is-done">{t("deviceDetail.alarms.cleared")}</span>
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
