import { useMemo } from "react";

import type { AlarmEvent, DeviceRow, Gateway } from "../../shared/types";

type Props = {
  devices: DeviceRow[];
  gateways: Gateway[];
  alarms: AlarmEvent[];
  loading?: boolean;
  onRefresh?: () => void | Promise<void>;
};

function statusOrder(d: DeviceRow): number {
  if (d.communicationStatus === "offline") return 0;
  if (d.communicationStatus === "unknown") return 1;
  return 2;
}

function lastUpdateTs(d: DeviceRow): number {
  if (!d.lastUpdateAt) return 0;
  return new Date(d.lastUpdateAt).getTime();
}

function commLabel(status: DeviceRow["communicationStatus"]): string {
  if (status === "online") return "Çevrimiçi";
  if (status === "offline") return "Çevrimdışı";
  return "Belirsiz";
}

export function SystemStatusPage({ devices, gateways, alarms, loading, onRefresh }: Props) {
  const stats = useMemo(() => {
    const total = devices.length;
    const online = devices.filter((d) => d.communicationStatus === "online").length;
    const offline = devices.filter((d) => d.communicationStatus === "offline").length;
    const unknown = devices.filter((d) => d.communicationStatus === "unknown").length;
    const commIssue = offline + unknown;
    const openAlarms = alarms.filter((a) => !a.reset).length;
    const gwTotal = gateways.length;
    const gwActive = gateways.filter((g) => g.is_active).length;
    return { total, online, offline, unknown, commIssue, openAlarms, gwTotal, gwActive };
  }, [devices, gateways, alarms]);

  const riskiest = useMemo(() => {
    return devices
      .slice()
      .sort((a, b) => {
        const c = statusOrder(a) - statusOrder(b);
        if (c !== 0) return c;
        return lastUpdateTs(a) - lastUpdateTs(b);
      })
      .slice(0, 8);
  }, [devices]);

  const showSpinner = Boolean(loading);

  return (
    <section className="tab-panel system-status-page">
      <div className="system-status-head">
        <div>
          <h2 className="system-status-title">Sistem durumu</h2>
          <p className="helper-text system-status-lead">
            Cihaz ve haberleşme özeti. Sıralama, anlık durum ve <strong>son veri zamanı</strong>na göre yapılır
            (çevrimdışı / belirsiz önce, en eski veri yukarıda).
          </p>
        </div>
        {onRefresh ? (
          <button
            type="button"
            className="secondary-btn"
            disabled={showSpinner}
            onClick={() => void onRefresh()}
          >
            {showSpinner ? "Yenileniyor…" : "Yenile"}
          </button>
        ) : null}
      </div>

      <div className="system-status-grid">
        <article className="status-card">
          <span className="status-card-label">Toplam cihaz</span>
          <strong className="status-card-value">{stats.total}</strong>
        </article>
        <article className="status-card status-card--ok">
          <span className="status-card-label">Çevrimiçi</span>
          <strong className="status-card-value">{stats.online}</strong>
        </article>
        <article className="status-card status-card--warn">
          <span className="status-card-label">Belirsiz</span>
          <strong className="status-card-value">{stats.unknown}</strong>
        </article>
        <article className="status-card status-card--bad">
          <span className="status-card-label">Çevrimdışı</span>
          <strong className="status-card-value">{stats.offline}</strong>
        </article>
        <article className="status-card status-card--accent">
          <span className="status-card-label">Haberleşme riski (belirsiz + çevrimdışı)</span>
          <strong className="status-card-value">{stats.commIssue}</strong>
        </article>
        <article className="status-card">
          <span className="status-card-label">Aktif alarm (henüz reset yok)</span>
          <strong className="status-card-value">{stats.openAlarms}</strong>
        </article>
        <article className="status-card">
          <span className="status-card-label">Gateway (aktif / toplam)</span>
          <strong className="status-card-value">
            {stats.gwActive} / {stats.gwTotal}
          </strong>
        </article>
      </div>

      <div className="system-status-panels">
        <div className="status-panel">
          <h3>Öncelikli cihazlar (bağlantı + gecikme)</h3>
          {devices.length === 0 && !showSpinner ? (
            <p className="helper-text">Kayıtlı cihaz yok.</p>
          ) : (
            <div className="status-table-wrap">
              <table className="values-table system-status-table">
                <thead>
                  <tr>
                    <th>#</th>
                    <th>Ad</th>
                    <th>Kod</th>
                    <th>Durum</th>
                    <th>Son veri</th>
                    <th>Batarya</th>
                  </tr>
                </thead>
                <tbody>
                  {riskiest.map((d, i) => (
                    <tr key={d.id}>
                      <td>{i + 1}</td>
                      <td>{d.name}</td>
                      <td>
                        <code className="inline-code">{d.code}</code>
                      </td>
                      <td>
                        <span
                          className={`comm-badge comm-badge--${d.communicationStatus}`}
                        >
                          {commLabel(d.communicationStatus)}
                        </span>
                      </td>
                      <td>
                        {d.lastUpdateAt
                          ? new Date(d.lastUpdateAt).toLocaleString("tr-TR")
                          : "—"}
                      </td>
                      <td>{d.batteryPercent != null ? `${d.batteryPercent}%` : "—"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>

        <div className="status-panel">
          <h3>Gateway özeti</h3>
          {gateways.length === 0 && !showSpinner ? (
            <p className="helper-text">Tanımlı gateway yok.</p>
          ) : (
            <ul className="gateway-status-list">
              {gateways.map((g) => (
                <li key={g.id} className="gateway-status-item">
                  <div>
                    <strong>{g.name}</strong>
                    <span className="helper-text gateway-code"> {g.code}</span>
                  </div>
                  <div className="gateway-status-meta">
                    <span
                      className={`comm-badge comm-badge--${g.is_active ? "online" : "offline"}`}
                    >
                      {g.is_active ? "Aktif" : "Pasif"}
                    </span>
                    {g.last_seen_at ? (
                      <span className="helper-text">
                        Son görülme: {new Date(g.last_seen_at).toLocaleString("tr-TR")}
                      </span>
                    ) : (
                      <span className="helper-text">Henüz görülmedi</span>
                    )}
                  </div>
                </li>
              ))}
            </ul>
          )}
        </div>
      </div>
    </section>
  );
}
