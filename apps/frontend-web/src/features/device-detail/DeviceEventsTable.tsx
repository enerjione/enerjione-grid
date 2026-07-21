/**
 * DeviceEventsTable — cihaza ait Son Olaylar (timeline + rozet).
 *
 * Iki kaynak birlestirilir: sistem olaylari (fetchSystemEvents, device_code
 * filtreli) + komut gecmisi (fetchDeviceCommands). Zamana gore sirali. Genel
 * Bakis'ta ilk N (compact); Olaylar sekmesinde tumu.
 *
 * NOT: "Haberlesme kesildi/Telemetri alindi" gibi comm/telemetry olaylari su an
 * backend'de yazilmiyor (ayri is); tablo mevcut olay+komut verisiyle dolar.
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";

import { fetchDeviceCommands, fetchSystemEvents } from "../../shared/api";
import { formatDateTime, formatRelative, formatTime } from "../../shared/format";
import type { DeviceCommandRow, SystemEvent } from "../../shared/types";

type Row = {
  id: string;
  ts: string;
  message: string;
  source: string;
  actor: string | null;
  isAlarm: boolean;
  tone: "ok" | "warn" | "err" | "info" | "pending";
  statusLabel: string;
};

type Props = {
  token: string;
  deviceCode: string;
  /** Genel Bakis: sinirli (orn 5). Olaylar sekmesi: tumu (undefined). */
  limit?: number;
  /** "Tum olaylari gor" -> Olaylar sekmesine gecis (compact modda gosterilir). */
  onViewAll?: () => void;
  /** "full": Olaylar sekmesi — tam tarih-saat + CSV export. undefined: compact. */
  variant?: "full";
};

function isAlarmEvent(e: SystemEvent): boolean {
  const et = (e.event_type || "").toLowerCase();
  return et.includes("alarm") || e.severity.toLowerCase() === "critical";
}

function severityTone(sev: string): Row["tone"] {
  const s = sev.toLowerCase();
  if (s === "error" || s === "critical") return "err";
  if (s === "warning") return "warn";
  return "info";
}

function commandTone(status: string): Row["tone"] {
  if (status === "ok") return "info";
  if (status === "failed") return "err";
  if (status === "pending" || status === "sent") return "pending";
  return "info";
}

export function DeviceEventsTable({ token, deviceCode, limit, onViewAll, variant }: Props) {
  const { t } = useTranslation();
  const isFull = variant === "full";
  const [events, setEvents] = useState<SystemEvent[]>([]);
  const [commands, setCommands] = useState<DeviceCommandRow[]>([]);
  const [loading, setLoading] = useState(true);

  const reload = useCallback(async () => {
    if (!token || !deviceCode) return;
    setLoading(true);
    try {
      const [ev, cmd] = await Promise.all([
        fetchSystemEvents(token).catch(() => [] as SystemEvent[]),
        fetchDeviceCommands(token, deviceCode, 25).catch(() => [] as DeviceCommandRow[]),
      ]);
      setEvents(ev.filter((e) => e.device_code === deviceCode));
      setCommands(cmd);
    } finally {
      setLoading(false);
    }
  }, [token, deviceCode]);

  useEffect(() => {
    void reload();
  }, [reload]);

  const rows = useMemo<Row[]>(() => {
    const evRows: Row[] = events.map((e) => ({
      id: `ev-${e.id}`,
      ts: e.created_at,
      message: e.message,
      source: e.category || "system",
      actor: e.actor_username ?? null,
      isAlarm: isAlarmEvent(e),
      tone: severityTone(e.severity),
      statusLabel: t(`deviceDetail.events.severity.${e.severity.toLowerCase()}`, {
        defaultValue: e.severity,
      }),
    }));
    const cmdRows: Row[] = commands.map((c) => ({
      id: `cmd-${c.id}`,
      ts: c.created_at,
      message: t("deviceDetail.events.commandMsg", { command: c.command }),
      source: "master",
      actor: c.actor_username ?? null,
      isAlarm: false,
      tone: commandTone(c.status),
      statusLabel: t(`deviceDetail.commands.status.${c.status}`, { defaultValue: c.status }),
    }));
    const all = [...evRows, ...cmdRows].sort(
      (a, b) => new Date(b.ts).getTime() - new Date(a.ts).getTime()
    );
    return limit ? all.slice(0, limit) : all;
  }, [events, commands, limit, t]);

  if (loading && rows.length === 0) {
    return (
      <div className="device-events-empty">
        <span className="btn-spinner" aria-hidden="true" />
      </div>
    );
  }
  if (rows.length === 0) {
    return (
      <div className="device-events-empty">
        <span className="material-symbols-outlined">history_toggle_off</span>
        <p>{t("deviceDetail.events.empty")}</p>
      </div>
    );
  }

  const exportCsv = () => {
    const head = ["Zaman", "Olay", "Kim", "Durum"];
    const esc = (s: string) => `"${(s ?? "").replace(/"/g, '""')}"`;
    const lines = rows.map((r) =>
      [formatDateTime(r.ts), r.message, r.actor ?? "Sistem", r.statusLabel].map(esc).join(",")
    );
    const blob = new Blob(["﻿" + [head.map(esc).join(","), ...lines].join("\r\n")], {
      type: "text/csv;charset=utf-8;",
    });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `${deviceCode}_olaylar.csv`;
    a.click();
    URL.revokeObjectURL(url);
  };

  return (
    <div className={`device-events${isFull ? " is-full" : ""}`}>
      {isFull ? (
        <div className="device-events-toolbar">
          <span className="device-events-count">{t("deviceDetail.events.total", { count: rows.length })}</span>
          <button type="button" className="device-events-export" onClick={exportCsv}>
            <span className="material-symbols-outlined">download</span>
            {t("deviceDetail.events.export")}
          </button>
        </div>
      ) : null}
      <table className="device-events-table">
        <thead>
          <tr>
            <th>{t("deviceDetail.events.time")}</th>
            <th>{t("deviceDetail.events.event")}</th>
            <th>{t("deviceDetail.events.who")}</th>
            <th className="device-events-th-status">{t("deviceDetail.events.status")}</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.id} className={r.isAlarm ? "is-alarm-row" : undefined}>
              <td className="device-events-time">
                <span className={`device-events-dot tone-${r.tone}`} aria-hidden="true" />
                {r.isAlarm ? (
                  <span className="device-events-alarm-tag">
                    <span className="material-symbols-outlined">notification_important</span>
                    {t("deviceDetail.events.alarm")}
                  </span>
                ) : null}
                <span title={formatRelative(r.ts)}>{isFull ? formatDateTime(r.ts) : formatTime(r.ts)}</span>
              </td>
              <td className="device-events-msg">{r.message}</td>
              <td className="device-events-who">
                {r.actor ? (
                  <span className="device-events-actor">
                    <span className="material-symbols-outlined">person</span>
                    {r.actor}
                  </span>
                ) : (
                  <span className="device-events-actor is-system">
                    <span className="material-symbols-outlined">smart_toy</span>
                    {t("deviceDetail.events.system")}
                  </span>
                )}
              </td>
              <td className="device-events-td-status">
                <span className={`device-events-badge tone-${r.tone}`}>{r.statusLabel}</span>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      {limit && onViewAll ? (
        <button type="button" className="device-events-viewall" onClick={onViewAll}>
          {t("deviceDetail.overview.viewAllEvents")}
          <span className="material-symbols-outlined">chevron_right</span>
        </button>
      ) : null}
    </div>
  );
}
