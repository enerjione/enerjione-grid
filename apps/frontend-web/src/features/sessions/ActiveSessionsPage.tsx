/**
 * Aktif Oturumlar — installer'a ozel sayfa.
 *
 * Sistemde aktif (revoked_at IS NULL) tum kullanici oturumlarini listeler:
 * kullanici, rol, IP, tarayici (UA truncate), login zamani, son aktivite.
 * Her satirda 'At' butonu — backend jti'yi revoke eder, kullanici bir
 * sonraki API cagrisinda 401 alir ve login ekranina dusurulur.
 *
 * Polling 10sn; tablo last_seen_at DESC sirali, en aktif olanlar uste.
 */
import { useEffect, useState } from "react";
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

function parseUserAgent(ua: string | null): string {
  if (!ua) return "—";
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
    /Linux/.test(ua) ? "Linux" :
    /Android/.test(ua) ? "Android" :
    /iPhone|iPad/.test(ua) ? "iOS" :
    "";
  return os ? `${browser} · ${os}` : browser;
}

export function ActiveSessionsPage({ accessToken }: Props) {
  const { t } = useTranslation();
  const toast = useToast();

  const [sessions, setSessions] = useState<ActiveSession[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [revokingJti, setRevokingJti] = useState<string | null>(null);

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
      <div className="panel-head">
        <div>
          <h3>
            <span className="material-symbols-outlined">devices</span>
            {t("activeSessions.title")}
          </h3>
          <p className="helper-text">{t("activeSessions.subtitle")}</p>
        </div>
        <button type="button" className="secondary-btn" onClick={() => void load()}>
          <span className="material-symbols-outlined">refresh</span>
          {t("common.refresh")}
        </button>
      </div>

      {error ? <p className="error-text">{error}</p> : null}

      <div className="active-sessions-table-wrap">
        <table className="values-table">
          <thead>
            <tr>
              <th>{t("activeSessions.col.user")}</th>
              <th>{t("activeSessions.col.role")}</th>
              <th>{t("activeSessions.col.ip")}</th>
              <th>{t("activeSessions.col.device")}</th>
              <th>{t("activeSessions.col.loginAt")}</th>
              <th>{t("activeSessions.col.lastSeen")}</th>
              <th className="actions-header">{t("activeSessions.col.action")}</th>
            </tr>
          </thead>
          <tbody>
            {loading && sessions.length === 0 ? (
              <tr>
                <td colSpan={7} className="helper-text" style={{ padding: 24, textAlign: "center" }}>
                  {t("common.loading")}
                </td>
              </tr>
            ) : sessions.length === 0 ? (
              <tr>
                <td colSpan={7} className="helper-text" style={{ padding: 24, textAlign: "center" }}>
                  {t("activeSessions.empty")}
                </td>
              </tr>
            ) : null}
            {sessions.map((s) => (
              <tr key={s.jti}>
                <td>
                  <div className="active-sessions-user">
                    <strong>{s.full_name || s.username}</strong>
                    <small>{s.username}</small>
                  </div>
                </td>
                <td>
                  <span className={`active-sessions-role role-${s.role}`}>{s.role}</span>
                </td>
                <td>
                  <code className="active-sessions-ip">{s.ip_address || "—"}</code>
                </td>
                <td>{parseUserAgent(s.user_agent)}</td>
                <td title={new Date(s.login_at).toLocaleString()}>
                  {timeAgo(s.login_at)}
                </td>
                <td title={new Date(s.last_seen_at).toLocaleString()}>
                  <span className="active-sessions-lastseen">{timeAgo(s.last_seen_at)}</span>
                </td>
                <td className="actions-cell">
                  <button
                    type="button"
                    className="danger-btn action-btn"
                    onClick={() => void handleRevoke(s)}
                    disabled={revokingJti === s.jti}
                  >
                    <span className="material-symbols-outlined">logout</span>
                    {revokingJti === s.jti ? t("activeSessions.revoking") : t("activeSessions.revoke")}
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}
