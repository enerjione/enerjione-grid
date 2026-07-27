/**
 * Aktif Oturumlar — installer'a ozel sayfa.
 *
 * Sistemde gercekten aktif (revoked_at IS NULL VE expires_at > now) tum
 * kullanici oturumlarini listeler: kullanici, rol, IP, tarayici (UA
 * truncate), login zamani, son aktivite. Her satirda 'At' butonu — backend
 * jti'yi revoke eder, kullanici bir sonraki API cagrisinda 401 alir ve login
 * ekranina dusurulur.
 *
 * Gorsel duzen Alarm Yonetimi / API Erisimi sayfalari ile ortak: ustte
 * toolbar (arama + rol filtresi + sayac), altta tam yukseklikte tablo karti.
 *
 * Polling 10sn; tablo last_seen_at DESC sirali, en aktif olanlar uste.
 */
import { useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";

import { asyncConfirm } from "../../components/ConfirmDialog";
import { useToast } from "../../components/ToastProvider";
import {
  fetchActiveSessions,
  revokeSession,
  type ActiveSession,
} from "../../shared/api";

type Props = {
  accessToken: string;
};

const POLL_INTERVAL_SEC = 10;

function timeAgo(iso: string): string {
  const diff = Date.now() - new Date(iso).getTime();
  const sec = Math.floor(diff / 1000);
  if (sec < 5) return "şimdi";
  if (sec < 60) return `${sec} sn önce`;
  if (sec < 3600) return `${Math.floor(sec / 60)} dk önce`;
  if (sec < 86400) return `${Math.floor(sec / 3600)} sa önce`;
  return new Date(iso).toLocaleString();
}

type DeviceInfo = { browser: string; os: string; icon: string };

function parseUserAgent(ua: string | null): DeviceInfo {
  if (!ua) return { browser: "—", os: "", icon: "help" };
  // Kısa özet (browser + OS); uzun UA'yi gostermeyiz.
  const browser =
    /Edg\//.test(ua) ? "Edge" :
    /Chrome\//.test(ua) ? "Chrome" :
    /Firefox\//.test(ua) ? "Firefox" :
    /Safari\//.test(ua) ? "Safari" :
    /curl\//.test(ua) ? "curl" :
    "Diğer";
  const os =
    /Windows NT/.test(ua) ? "Windows" :
    /Mac OS X/.test(ua) ? "macOS" :
    /Android/.test(ua) ? "Android" :
    /iPhone|iPad/.test(ua) ? "iOS" :
    /Linux/.test(ua) ? "Linux" :
    "";
  // Mobil isletim sistemlerinde telefon, aksi halde masaustu ikonu.
  const icon =
    os === "Android" || os === "iOS"
      ? "smartphone"
      : browser === "curl"
      ? "terminal"
      : "computer";
  return { browser, os, icon };
}

/** Oturumun "canli mi" gostergesi — son aktivite 2 dk icindeyse online. */
const ONLINE_THRESHOLD_SEC = 120;

export function ActiveSessionsPage({ accessToken }: Props) {
  const { t } = useTranslation();
  const toast = useToast();

  const [sessions, setSessions] = useState<ActiveSession[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [revokingJti, setRevokingJti] = useState<string | null>(null);
  const [search, setSearch] = useState("");
  const [roleFilter, setRoleFilter] = useState("all");

  const load = async () => {
    try {
      const data = await fetchActiveSessions(accessToken);
      setSessions(data);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : t("common.errorOccurred"));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void load();
    const id = window.setInterval(() => void load(), POLL_INTERVAL_SEC * 1000);
    return () => window.clearInterval(id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [accessToken]);

  /** Listede gorunen rollerden filtre secenekleri. */
  const roleOptions = useMemo(
    () => Array.from(new Set(sessions.map((s) => s.role))).sort(),
    [sessions]
  );

  const filtered = useMemo(() => {
    const q = search.trim().toLowerCase();
    return sessions.filter((s) => {
      if (roleFilter !== "all" && s.role !== roleFilter) return false;
      if (!q) return true;
      const dev = parseUserAgent(s.user_agent);
      return (
        s.username.toLowerCase().includes(q) ||
        (s.full_name || "").toLowerCase().includes(q) ||
        (s.ip_address || "").toLowerCase().includes(q) ||
        `${dev.browser} ${dev.os}`.toLowerCase().includes(q)
      );
    });
  }, [sessions, search, roleFilter]);

  /** Ayni kullanicinin kac oturumu var — satirda "2/3" rozeti icin. */
  const perUserCount = useMemo(() => {
    const m = new Map<number, number>();
    for (const s of sessions) m.set(s.user_id, (m.get(s.user_id) ?? 0) + 1);
    return m;
  }, [sessions]);

  const handleRevoke = async (sess: ActiveSession) => {
    if (
      !(await asyncConfirm(
        t("activeSessions.confirmRevoke", { name: sess.full_name || sess.username })
      ))
    ) {
      return;
    }
    setRevokingJti(sess.jti);
    try {
      await revokeSession(accessToken, sess.jti);
      setSessions((prev) => prev.filter((s) => s.jti !== sess.jti));
      toast.success(t("activeSessions.revokedToast", { name: sess.username }));
    } catch (err) {
      toast.error(err instanceof Error ? err.message : t("common.errorOccurred"));
    } finally {
      setRevokingJti(null);
    }
  };

  return (
    <section className="tab-panel active-sessions-panel">
      {/* Toolbar — Alarm Yonetimi / API Erisimi ile ortak gorsel. */}
      <div className="sessions-toolbar">
        <input
          type="search"
          className="sessions-search-input"
          placeholder={t("activeSessions.searchPlaceholder")}
          value={search}
          onChange={(e) => setSearch(e.target.value)}
        />
        <div className="sessions-filter-group">
          <select value={roleFilter} onChange={(e) => setRoleFilter(e.target.value)}>
            <option value="all">{t("activeSessions.filterAllRoles")}</option>
            {roleOptions.map((r) => (
              <option key={r} value={r}>
                {r}
              </option>
            ))}
          </select>
        </div>
        <span className="sessions-count-pill">
          {filtered.length} / {sessions.length}
        </span>
        <button
          type="button"
          className={`sessions-refresh-btn ${loading ? "is-spinning" : ""}`}
          onClick={() => void load()}
          disabled={loading}
          title={t("common.refresh")}
        >
          <span className="material-symbols-outlined">refresh</span>
          {t("common.refresh")}
        </button>
      </div>

      {error ? <p className="error-text">{error}</p> : null}

      <div className="active-sessions-table-wrap">
        {loading && sessions.length === 0 ? (
          <div className="sessions-empty">
            <span className="material-symbols-outlined sessions-empty-icon">
              progress_activity
            </span>
            <p>{t("common.loading")}</p>
          </div>
        ) : filtered.length === 0 ? (
          <div className="sessions-empty">
            <span className="material-symbols-outlined sessions-empty-icon">devices_off</span>
            <h3>{t("activeSessions.emptyHeading")}</h3>
            <p>
              {sessions.length === 0
                ? t("activeSessions.empty")
                : t("activeSessions.emptyNoMatch")}
            </p>
          </div>
        ) : (
          <table className="sessions-table">
            <thead>
              <tr>
                <th>{t("activeSessions.col.user")}</th>
                <th>{t("activeSessions.col.role")}</th>
                <th>{t("activeSessions.col.ip")}</th>
                <th>{t("activeSessions.col.device")}</th>
                <th>{t("activeSessions.col.loginAt")}</th>
                <th>{t("activeSessions.col.lastSeen")}</th>
                <th>{t("activeSessions.col.action")}</th>
              </tr>
            </thead>
            <tbody>
              {filtered.map((s) => {
                const dev = parseUserAgent(s.user_agent);
                const idleSec = (Date.now() - new Date(s.last_seen_at).getTime()) / 1000;
                const isOnline = idleSec < ONLINE_THRESHOLD_SEC;
                const sessionCount = perUserCount.get(s.user_id) ?? 1;
                const display = s.full_name || s.username;
                return (
                  <tr key={s.jti}>
                    <td>
                      <div className="sessions-user-cell">
                        <span
                          className={`sessions-avatar ${isOnline ? "is-online" : ""}`}
                          title={
                            isOnline
                              ? t("activeSessions.online")
                              : t("activeSessions.idle")
                          }
                        >
                          {display.charAt(0).toUpperCase()}
                        </span>
                        <span className="sessions-user-text">
                          <strong>{display}</strong>
                          <small>{s.username}</small>
                        </span>
                        {sessionCount > 1 ? (
                          <span
                            className="sessions-multi-pill"
                            title={t("activeSessions.multiSessionHint", {
                              count: sessionCount
                            })}
                          >
                            <span className="material-symbols-outlined">layers</span>
                            {sessionCount}
                          </span>
                        ) : null}
                      </div>
                    </td>
                    <td>
                      <span className={`sessions-role-pill role-${s.role}`}>{s.role}</span>
                    </td>
                    <td>
                      <code className="sessions-ip">{s.ip_address || "—"}</code>
                    </td>
                    <td>
                      <span className="sessions-device-cell">
                        <span className="material-symbols-outlined">{dev.icon}</span>
                        <span className="sessions-device-text">
                          <strong>{dev.browser}</strong>
                          {dev.os ? <small>{dev.os}</small> : null}
                        </span>
                      </span>
                    </td>
                    <td
                      className="sessions-time-cell"
                      title={new Date(s.login_at).toLocaleString()}
                    >
                      {timeAgo(s.login_at)}
                    </td>
                    <td
                      className="sessions-time-cell"
                      title={new Date(s.last_seen_at).toLocaleString()}
                    >
                      <span
                        className={`sessions-lastseen ${isOnline ? "is-online" : ""}`}
                      >
                        <span className="sessions-lastseen-dot" />
                        {timeAgo(s.last_seen_at)}
                      </span>
                    </td>
                    <td>
                      <button
                        type="button"
                        className="sessions-revoke-btn"
                        onClick={() => void handleRevoke(s)}
                        disabled={revokingJti === s.jti}
                        title={t("activeSessions.revoke")}
                      >
                        <span className="material-symbols-outlined">logout</span>
                        {revokingJti === s.jti
                          ? t("activeSessions.revoking")
                          : t("activeSessions.revoke")}
                      </button>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
      </div>
    </section>
  );
}
