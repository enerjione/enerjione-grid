import { useCallback, useEffect, useRef, useState } from "react";

import {
  fetchNotifications,
  fetchNotificationUnreadCount,
  markAllNotificationsRead,
  markNotificationRead
} from "../shared/api";
import type { NotificationItem } from "../shared/types";

type Props = {
  token: string;
  /** Bildirim'e tiklayinca cagrilir (link varsa). */
  onNavigate?: (link: string) => void;
};

const POLL_INTERVAL_MS = 30_000; // 30 sn

function categoryLabel(cat: string): string {
  switch (cat) {
    case "alarm":
      return "Alarm";
    case "alarm_assignment":
      return "Atama";
    case "alarm_comment":
      return "Yorum";
    case "system":
      return "Sistem";
    case "warning":
      return "Uyarı";
    case "error":
      return "Hata";
    case "info":
      return "Bilgi";
    default:
      return cat;
  }
}

function severityClass(severity: string): string {
  const s = (severity || "info").toLowerCase();
  if (s === "critical" || s === "error") return "notif-sev-critical";
  if (s === "warning" || s === "warn") return "notif-sev-warning";
  return "notif-sev-info";
}

function categoryIcon(cat: string): string {
  switch (cat) {
    case "alarm":
      return "warning";
    case "alarm_assignment":
      return "assignment_ind";
    case "alarm_comment":
      return "chat";
    case "system":
      return "settings";
    case "error":
      return "error";
    case "warning":
      return "warning";
    default:
      return "notifications";
  }
}

function timeAgo(iso: string): string {
  const dt = new Date(iso).getTime();
  if (!Number.isFinite(dt)) return "";
  const diffSec = Math.max(0, Math.floor((Date.now() - dt) / 1000));
  if (diffSec < 60) return `${diffSec}s önce`;
  if (diffSec < 3600) return `${Math.floor(diffSec / 60)}dk önce`;
  if (diffSec < 86400) return `${Math.floor(diffSec / 3600)}sa önce`;
  return new Date(iso).toLocaleString("tr-TR");
}

// Sinyal kaynagi (master / sat01 / sat02 / ...) frontend dostu etikete cevir.
const _SOURCE_LABEL: Record<string, string> = {
  master: "Master",
  sat01: "Satellite 01",
  sat02: "Satellite 02"
};

function sourceLabel(src: string | null | undefined): string | null {
  if (!src) return null;
  const key = src.toLowerCase();
  return _SOURCE_LABEL[key] ?? key.charAt(0).toUpperCase() + key.slice(1);
}

// Backend metadata.operator string'ini insan-okur sembol/metne cevir.
function operatorSymbol(op: string | null | undefined): string {
  if (!op) return "";
  const k = op.toLowerCase();
  if (k === "gt" || k === ">") return ">";
  if (k === "ge" || k === ">=") return "≥";
  if (k === "lt" || k === "<") return "<";
  if (k === "le" || k === "<=") return "≤";
  if (k === "eq" || k === "=" || k === "==") return "=";
  if (k === "ne" || k === "!=") return "≠";
  return op;
}

// Sayisal degeri tr-TR locale'de, gereksiz trailing zero'yu kirpan formatla.
const _NUM_FMT = new Intl.NumberFormat("tr-TR", { maximumFractionDigits: 4 });
function fmtNumber(v: number | null | undefined): string {
  if (v === null || v === undefined || !Number.isFinite(v)) return "";
  return _NUM_FMT.format(v);
}

type AlarmMetadata = {
  alarm_id?: number | null;
  device_code?: string | null;
  device_name?: string | null;
  level?: string | null;
  signal_key?: string | null;
  signal_source?: string | null;
  source_gateway?: string | null;
  value?: number | null;
  value_string?: string | null;
  threshold?: number | null;
  operator?: string | null;
  source_timestamp?: string | null;
};

function parseMetadata(raw: string | null | undefined): AlarmMetadata | null {
  if (!raw) return null;
  try {
    const obj = JSON.parse(raw);
    if (obj && typeof obj === "object") return obj as AlarmMetadata;
  } catch {
    // Bozuk JSON — sessizce yut, kart yine baslik+body ile gosterilir.
  }
  return null;
}

export function NotificationBell({ token, onNavigate }: Props) {
  const [items, setItems] = useState<NotificationItem[]>([]);
  const [unread, setUnread] = useState(0);
  const [open, setOpen] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const wrapperRef = useRef<HTMLDivElement | null>(null);
  const pollInFlight = useRef(false);

  const refreshUnread = useCallback(async () => {
    if (pollInFlight.current) return;
    pollInFlight.current = true;
    try {
      const n = await fetchNotificationUnreadCount(token);
      setUnread(n);
    } catch {
      // Sessizce gec - polling hatalari kullaniciyi rahatsiz etmesin
    } finally {
      pollInFlight.current = false;
    }
  }, [token]);

  const refreshList = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      // Yalnizca okunmamislari getir — kullanici daha onceden okuduklarini
      // tekrar gormek istemiyor (istek). Liste boyutu da stabil kalir.
      const list = await fetchNotifications(token, { onlyUnread: true, limit: 50 });
      setItems(list);
      setUnread(list.length);
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : "Bildirimler alınamadı.");
    } finally {
      setLoading(false);
    }
  }, [token]);

  // Sayim polling
  useEffect(() => {
    void refreshUnread();
    const id = window.setInterval(() => void refreshUnread(), POLL_INTERVAL_MS);
    return () => window.clearInterval(id);
  }, [refreshUnread]);

  // Disari tiklayinca kapan
  useEffect(() => {
    if (!open) return;
    const onClick = (e: MouseEvent) => {
      if (!wrapperRef.current?.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", onClick);
    return () => document.removeEventListener("mousedown", onClick);
  }, [open]);

  const handleToggle = () => {
    setOpen((prev) => {
      const next = !prev;
      if (next) void refreshList();
      return next;
    });
  };

  const handleMarkAll = async () => {
    try {
      await markAllNotificationsRead(token);
      // Tum okundu → listeyi temizle (sadece okunmamis gosterdigimiz icin).
      setItems([]);
      setUnread(0);
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : "İşlem başarısız.");
    }
  };

  const handleItemClick = async (item: NotificationItem) => {
    // Once okundu isaretle ve listeden cikar
    if (!item.is_read) {
      try {
        await markNotificationRead(token, item.id);
      } catch {
        // Hata olsa bile UI'dan cikaralim — bir sonraki polling gercegi gosterir
      }
      setItems((prev) => prev.filter((it) => it.id !== item.id));
      setUnread((u) => Math.max(0, u - 1));
    }
    if (item.link && onNavigate) {
      onNavigate(item.link);
      setOpen(false);
    }
  };

  return (
    <div className="notif-bell-wrap" ref={wrapperRef}>
      <button
        type="button"
        className={`notif-bell-btn ${unread > 0 ? "notif-bell-btn--active" : ""}`}
        onClick={handleToggle}
        title="Bildirimler"
        aria-label="Bildirimler"
      >
        <span className="material-symbols-outlined">notifications</span>
        {unread > 0 ? (
          <span className="notif-bell-badge">{unread > 99 ? "99+" : unread}</span>
        ) : null}
      </button>

      {open ? (
        <div className="notif-dropdown">
          <header className="notif-dropdown-head">
            <strong>Bildirimler</strong>
            {items.length > 0 ? (
              <button type="button" className="notif-mark-all" onClick={() => void handleMarkAll()}>
                Hepsini okundu işaretle
              </button>
            ) : null}
          </header>

          {loading ? <div className="notif-empty">Yükleniyor…</div> : null}
          {error && !loading ? <div className="notif-error">{error}</div> : null}

          {!loading && !error && items.length === 0 ? (
            <div className="notif-empty">
              <span className="material-symbols-outlined">notifications_off</span>
              Henüz bildirim yok
            </div>
          ) : null}

          {!loading && items.length > 0 ? (
            <ul className="notif-list">
              {items.map((item) => {
                const meta = parseMetadata(item.metadata_json);
                const isAlarm = item.category === "alarm";
                const sevCls = severityClass(item.severity);
                const deviceLabel = meta?.device_name
                  ? meta.device_name
                  : meta?.device_code ?? null;
                const deviceCode = meta?.device_code ?? null;
                const srcLabel = sourceLabel(meta?.signal_source);
                const opSym = operatorSymbol(meta?.operator);
                // Sinyal etiketinin kullanici dostu hali: signal_key son
                // bolumunden ".battery_voltage_satellite" -> "Battery Voltage
                // Satellite" gibi degil, direkt anahtari gostermek SCADA
                // konvansiyonu icin daha tanidik. Kaynak ayri rozet.
                const signalKeyShort = (() => {
                  const key = meta?.signal_key;
                  if (!key) return null;
                  const idx = key.indexOf(".");
                  return idx >= 0 ? key.slice(idx + 1) : key;
                })();
                const valueDisplay = meta?.value_string
                  ? meta.value_string
                  : meta?.value !== null && meta?.value !== undefined
                  ? fmtNumber(meta.value)
                  : null;
                const thresholdDisplay = meta?.threshold !== null && meta?.threshold !== undefined
                  ? fmtNumber(meta.threshold)
                  : null;
                return (
                  <li
                    key={item.id}
                    className={`notif-item ${item.is_read ? "" : "notif-item--unread"} ${sevCls}`}
                    onClick={() => void handleItemClick(item)}
                  >
                    <span className={`notif-item-icon ${sevCls}`}>
                      <span className="material-symbols-outlined">{categoryIcon(item.category)}</span>
                    </span>
                    <div className="notif-item-body">
                      <div className="notif-item-title-row">
                        <strong className="notif-item-title">{item.title}</strong>
                        {!item.is_read ? <span className="notif-item-dot" /> : null}
                      </div>
                      {/* Cihaz / kaynak / sinyal rozet seridi — sadece alarm */}
                      {isAlarm && (deviceLabel || srcLabel || signalKeyShort) ? (
                        <div className="notif-item-chips">
                          {deviceLabel ? (
                            <span className="notif-chip notif-chip--device" title={deviceCode ?? undefined}>
                              <span className="material-symbols-outlined">router</span>
                              {deviceLabel}
                            </span>
                          ) : null}
                          {srcLabel ? (
                            <span className={`notif-chip notif-chip--source notif-chip--src-${(meta?.signal_source ?? "").toLowerCase()}`}>
                              {srcLabel}
                            </span>
                          ) : null}
                          {signalKeyShort ? (
                            <span className="notif-chip notif-chip--signal" title={meta?.signal_key ?? undefined}>
                              <span className="material-symbols-outlined">monitoring</span>
                              {signalKeyShort}
                            </span>
                          ) : null}
                        </div>
                      ) : null}
                      {/* Aciklama */}
                      {item.body ? <p className="notif-item-text">{item.body}</p> : null}
                      {/* Olcum + esik karsilastirmasi gorsel sunum */}
                      {isAlarm && valueDisplay ? (
                        <div className="notif-item-measure">
                          <span className="notif-measure-value">{valueDisplay}</span>
                          {opSym && thresholdDisplay ? (
                            <>
                              <span className="notif-measure-op">{opSym}</span>
                              <span className="notif-measure-threshold">
                                {thresholdDisplay}
                                <span className="notif-measure-threshold-label">eşik</span>
                              </span>
                            </>
                          ) : null}
                        </div>
                      ) : null}
                      <div className="notif-item-meta">
                        <span className={`notif-item-cat ${sevCls}`}>
                          {categoryLabel(item.category)}
                        </span>
                        {meta?.level ? (
                          <span className={`notif-level-badge ${sevCls}`}>
                            {meta.level.toUpperCase()}
                          </span>
                        ) : null}
                        {item.actor_username ? (
                          <span className="notif-item-actor">{item.actor_username}</span>
                        ) : null}
                        <span className="notif-item-time">{timeAgo(item.created_at)}</span>
                      </div>
                    </div>
                  </li>
                );
              })}
            </ul>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}
