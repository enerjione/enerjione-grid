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
import { formatDateTime, formatRelative } from "../../shared/format";
import type { DeviceCommandRow, SystemEvent } from "../../shared/types";

type Row = {
  id: string;
  ts: string;
  message: string;
  source: string;
  /** Kaynak kanal: master | sat01 | sat02 | null (bilinmiyor). */
  channel: "master" | "sat01" | "sat02" | null;
  actor: string | null;
  isAlarm: boolean;
  tone: "ok" | "warn" | "err" | "info" | "pending";
  statusLabel: string;
};

const CHANNEL_META: Record<"master" | "sat01" | "sat02", { label: string; tone: string }> = {
  master: { label: "Master", tone: "master" },
  sat01: { label: "Sat 01", tone: "green" },
  sat02: { label: "Sat 02", tone: "amber" },
};

// SystemEvent metadata_json / message'dan kaynak kanal turet.
function channelOfEvent(e: SystemEvent): "master" | "sat01" | "sat02" | null {
  const raw = `${e.metadata_json ?? ""} ${e.message ?? ""}`.toLowerCase();
  if (raw.includes("sat01") || raw.includes("satellite 01") || raw.includes("satellite 1")) return "sat01";
  if (raw.includes("sat02") || raw.includes("satellite 02") || raw.includes("satellite 2")) return "sat02";
  if (raw.includes("master")) return "master";
  return null;
}

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

const PAGE_SIZE = 12;

export function DeviceEventsTable({ token, deviceCode, limit, onViewAll, variant }: Props) {
  const { t } = useTranslation();
  const isFull = variant === "full";
  const [events, setEvents] = useState<SystemEvent[]>([]);
  const [commands, setCommands] = useState<DeviceCommandRow[]>([]);
  const [loading, setLoading] = useState(true);
  const [page, setPage] = useState(0);

  const reload = useCallback(async () => {
    if (!token || !deviceCode) return;
    setLoading(true);
    try {
      const [ev, cmd] = await Promise.all([
        fetchSystemEvents(token).catch(() => [] as SystemEvent[]),
        fetchDeviceCommands(token, deviceCode, isFull ? 100 : 25).catch(() => [] as DeviceCommandRow[]),
      ]);
      setEvents(ev.filter((e) => e.device_code === deviceCode));
      setCommands(cmd);
    } finally {
      setLoading(false);
    }
  }, [token, deviceCode, isFull]);

  useEffect(() => {
    void reload();
  }, [reload]);

  // Tum satirlar (sirali). Pagination full modda uygulanir.
  const allRows = useMemo<Row[]>(() => {
    const evRows: Row[] = events.map((e) => ({
      id: `ev-${e.id}`,
      ts: e.created_at,
      message: e.message,
      source: e.category || "system",
      channel: channelOfEvent(e),
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
      channel: "master", // komutlar her zaman master'a gonderilir
      actor: c.actor_username ?? null,
      isAlarm: false,
      tone: commandTone(c.status),
      statusLabel: t(`deviceDetail.commands.status.${c.status}`, { defaultValue: c.status }),
    }));
    return [...evRows, ...cmdRows].sort((a, b) => new Date(b.ts).getTime() - new Date(a.ts).getTime());
  }, [events, commands, t]);

  const pageCount = Math.max(1, Math.ceil(allRows.length / PAGE_SIZE));
  const safePage = Math.min(page, pageCount - 1);
  const rows = useMemo<Row[]>(() => {
    if (limit) return allRows.slice(0, limit); // compact (Genel Bakis)
    if (!isFull) return allRows;
    return allRows.slice(safePage * PAGE_SIZE, safePage * PAGE_SIZE + PAGE_SIZE);
  }, [allRows, limit, isFull, safePage]);

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
    const head = ["Zaman", "Alarm", "Olay", "Kaynak", "Kim", "Durum"];
    const esc = (s: string) => `"${(s ?? "").replace(/"/g, '""')}"`;
    const lines = allRows.map((r) =>
      [
        formatDateTime(r.ts),
        r.isAlarm ? "ALARM" : "",
        r.message,
        r.channel ? CHANNEL_META[r.channel].label : "",
        r.actor ?? "Sistem",
        r.statusLabel,
      ]
        .map(esc)
        .join(",")
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
          <span className="device-events-count">{t("deviceDetail.events.total", { count: allRows.length })}</span>
          <button type="button" className="device-events-export" onClick={exportCsv}>
            <span className="material-symbols-outlined">download</span>
            {t("deviceDetail.events.export")}
          </button>
        </div>
      ) : null}
      <table className={`device-events-table${isFull ? " is-full" : ""}`}>
        <thead>
          <tr>
            {isFull ? <th className="device-events-th-alarm">{t("deviceDetail.events.alarmCol")}</th> : null}
            <th className="device-events-th-time">{t("deviceDetail.events.time")}</th>
            <th>{t("deviceDetail.events.event")}</th>
            {isFull ? <th className="device-events-th-channel">{t("deviceDetail.events.channel")}</th> : null}
            <th>{t("deviceDetail.events.who")}</th>
            <th className="device-events-th-status">{t("deviceDetail.events.status")}</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.id} className={r.isAlarm ? "is-alarm-row" : undefined}>
              {isFull ? (
                <td className="device-events-td-alarm">
                  {r.isAlarm ? (
                    <span className="device-events-alarm-tag">
                      <span className="material-symbols-outlined">notification_important</span>
                      {t("deviceDetail.events.alarm")}
                    </span>
                  ) : (
                    <span className="device-events-alarm-none" aria-hidden="true">—</span>
                  )}
                </td>
              ) : null}
              <td className="device-events-time">
                <span className={`device-events-dot tone-${r.tone}`} aria-hidden="true" />
                {!isFull && r.isAlarm ? (
                  <span className="device-events-alarm-tag">
                    <span className="material-symbols-outlined">notification_important</span>
                    {t("deviceDetail.events.alarm")}
                  </span>
                ) : null}
                <span title={formatRelative(r.ts)}>
                  {isFull
                    ? formatDateTime(r.ts)
                    : formatDateTime(r.ts, {
                        day: "2-digit",
                        month: "2-digit",
                        hour: "2-digit",
                        minute: "2-digit",
                      })}
                </span>
              </td>
              <td className="device-events-msg">{r.message}</td>
              {isFull ? (
                <td className="device-events-channel">
                  {r.channel ? (
                    <span className={`device-events-chan tone-${CHANNEL_META[r.channel].tone}`}>
                      {CHANNEL_META[r.channel].label}
                    </span>
                  ) : (
                    <span className="device-events-chan-none">—</span>
                  )}
                </td>
              ) : null}
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

      {isFull && pageCount > 1 ? (
        <div className="device-events-pager">
          <button
            type="button"
            className="device-events-pagebtn"
            onClick={() => setPage((p) => Math.max(0, p - 1))}
            disabled={safePage === 0}
          >
            <span className="material-symbols-outlined">chevron_left</span>
            {t("deviceDetail.events.prev")}
          </button>
          <span className="device-events-pageinfo">
            {t("deviceDetail.events.pageOf", { page: safePage + 1, total: pageCount })}
          </span>
          <button
            type="button"
            className="device-events-pagebtn"
            onClick={() => setPage((p) => Math.min(pageCount - 1, p + 1))}
            disabled={safePage >= pageCount - 1}
          >
            {t("deviceDetail.events.next")}
            <span className="material-symbols-outlined">chevron_right</span>
          </button>
        </div>
      ) : null}

      {limit && onViewAll ? (
        <button type="button" className="device-events-viewall" onClick={onViewAll}>
          {t("deviceDetail.overview.viewAllEvents")}
          <span className="material-symbols-outlined">chevron_right</span>
        </button>
      ) : null}
    </div>
  );
}
