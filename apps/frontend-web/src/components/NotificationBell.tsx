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
              {items.map((item) => (
                <li
                  key={item.id}
                  className={`notif-item ${item.is_read ? "" : "notif-item--unread"} ${severityClass(item.severity)}`}
                  onClick={() => void handleItemClick(item)}
                >
                  <span className={`notif-item-icon ${severityClass(item.severity)}`}>
                    <span className="material-symbols-outlined">{categoryIcon(item.category)}</span>
                  </span>
                  <div className="notif-item-body">
                    <div className="notif-item-title-row">
                      <strong className="notif-item-title">{item.title}</strong>
                      {!item.is_read ? <span className="notif-item-dot" /> : null}
                    </div>
                    {item.body ? <p className="notif-item-text">{item.body}</p> : null}
                    <div className="notif-item-meta">
                      <span className={`notif-item-cat ${severityClass(item.severity)}`}>
                        {categoryLabel(item.category)}
                      </span>
                      {item.actor_username ? (
                        <span className="notif-item-actor">{item.actor_username}</span>
                      ) : null}
                      <span className="notif-item-time">{timeAgo(item.created_at)}</span>
                    </div>
                  </div>
                </li>
              ))}
            </ul>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}
