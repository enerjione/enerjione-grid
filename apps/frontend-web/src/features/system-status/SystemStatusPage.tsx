import { useEffect, useMemo, useRef, useState } from "react";

import { fetchHostStatus, loadSession } from "../../shared/api";
import type { AlarmEvent, DeviceRow, Gateway, HostStatus } from "../../shared/types";

type Props = {
  devices: DeviceRow[];
  gateways: Gateway[];
  alarms: AlarmEvent[];
  loading?: boolean;
  onRefresh?: () => void | Promise<void>;
};

/** Sunucu kaynak metriklerini yenileme aralığı (sn). */
const HOST_REFRESH_INTERVAL_SEC = 5;

/** CPU sparkline icin tutulacak ornek sayisi (60sn'lik pencere). */
const HOST_HISTORY_LEN = 24;

const BYTE_UNITS = ["B", "KB", "MB", "GB", "TB", "PB"] as const;

function formatBytes(value: number | null | undefined, fractionDigits = 1): string {
  if (value == null || !Number.isFinite(value) || value <= 0) return "—";
  let n = value;
  let i = 0;
  while (n >= 1024 && i < BYTE_UNITS.length - 1) {
    n /= 1024;
    i += 1;
  }
  return `${n.toFixed(fractionDigits)} ${BYTE_UNITS[i]}`;
}

function formatDuration(seconds: number | null | undefined): string {
  if (seconds == null || !Number.isFinite(seconds) || seconds < 0) return "—";
  const s = Math.floor(seconds);
  const days = Math.floor(s / 86400);
  const hours = Math.floor((s % 86400) / 3600);
  const minutes = Math.floor((s % 3600) / 60);
  const parts: string[] = [];
  if (days > 0) parts.push(`${days}g`);
  if (hours > 0 || days > 0) parts.push(`${hours}sa`);
  parts.push(`${minutes}dk`);
  return parts.join(" ");
}

function percentTone(percent: number): "ok" | "warn" | "bad" {
  if (percent >= 90) return "bad";
  if (percent >= 75) return "warn";
  return "ok";
}

/** SVG donut: 0-100 yuzdeyi yarim daire/tam daire arasinda animasyonlu boyar. */
function Donut({
  percent,
  size = 168,
  thickness = 14,
  tone
}: {
  percent: number;
  size?: number;
  thickness?: number;
  tone: "ok" | "warn" | "bad";
}) {
  const safe = Math.max(0, Math.min(100, Number.isFinite(percent) ? percent : 0));
  const r = (size - thickness) / 2;
  const c = 2 * Math.PI * r;
  const offset = c * (1 - safe / 100);
  const strokeColor =
    tone === "bad" ? "#dc2626" : tone === "warn" ? "#f59e0b" : "#10b981";
  const trackColor = "rgba(148, 163, 184, 0.18)";
  return (
    <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`} className="donut">
      <circle
        cx={size / 2}
        cy={size / 2}
        r={r}
        stroke={trackColor}
        strokeWidth={thickness}
        fill="none"
      />
      <circle
        cx={size / 2}
        cy={size / 2}
        r={r}
        stroke={strokeColor}
        strokeWidth={thickness}
        fill="none"
        strokeLinecap="round"
        strokeDasharray={c}
        strokeDashoffset={offset}
        transform={`rotate(-90 ${size / 2} ${size / 2})`}
        style={{ transition: "stroke-dashoffset 600ms ease, stroke 300ms ease" }}
      />
    </svg>
  );
}

/** Sparkline: son N degeri kucuk bir alan grafigine ceviren minimal SVG. */
function Sparkline({
  values,
  width = 220,
  height = 48,
  tone
}: {
  values: number[];
  width?: number;
  height?: number;
  tone: "ok" | "warn" | "bad";
}) {
  if (values.length < 2) {
    return <div className="sparkline sparkline--empty" style={{ width, height }} />;
  }
  const maxV = 100;
  const minV = 0;
  const span = maxV - minV || 1;
  const stepX = width / (values.length - 1);
  const points = values
    .map((v, i) => {
      const x = i * stepX;
      const y = height - ((v - minV) / span) * height;
      return `${x.toFixed(1)},${y.toFixed(1)}`;
    })
    .join(" ");
  const areaPath = `M0,${height} L${points
    .split(" ")
    .map((p) => p)
    .join(" L")} L${width},${height} Z`;
  const strokeColor =
    tone === "bad" ? "#dc2626" : tone === "warn" ? "#f59e0b" : "#10b981";
  const fillColor =
    tone === "bad"
      ? "rgba(220, 38, 38, 0.15)"
      : tone === "warn"
      ? "rgba(245, 158, 11, 0.15)"
      : "rgba(16, 185, 129, 0.15)";
  return (
    <svg
      width={width}
      height={height}
      viewBox={`0 0 ${width} ${height}`}
      preserveAspectRatio="none"
      className="sparkline"
    >
      <path d={areaPath} fill={fillColor} />
      <polyline points={points} fill="none" stroke={strokeColor} strokeWidth={2} strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

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

function lastSeenTone(gw: Gateway): "ok" | "warn" | "bad" | "muted" {
  if (!gw.is_active) return "muted";
  if (!gw.last_seen_at) return "warn";
  const sec = (Date.now() - new Date(gw.last_seen_at).getTime()) / 1000;
  if (sec < 60) return "ok";
  if (sec < 600) return "warn";
  return "bad";
}

export function SystemStatusPage({ devices, gateways, alarms, loading, onRefresh }: Props) {
  // Sunucu (backend host) anlik kaynak metrikleri
  const [host, setHost] = useState<HostStatus | null>(null);
  const [hostError, setHostError] = useState<string | null>(null);
  const inFlightRef = useRef(false);
  // Sparkline icin son N CPU/RAM ornegi.
  const [cpuHistory, setCpuHistory] = useState<number[]>([]);
  const [memHistory, setMemHistory] = useState<number[]>([]);

  useEffect(() => {
    let cancelled = false;
    async function tick() {
      if (inFlightRef.current) return;
      const session = loadSession();
      if (!session) return;
      inFlightRef.current = true;
      try {
        const status = await fetchHostStatus(session.accessToken);
        if (!cancelled) {
          setHost(status);
          setHostError(null);
          setCpuHistory((prev) => {
            const next = [...prev, status.cpu.percent];
            return next.length > HOST_HISTORY_LEN ? next.slice(-HOST_HISTORY_LEN) : next;
          });
          setMemHistory((prev) => {
            const next = [...prev, status.memory.percent];
            return next.length > HOST_HISTORY_LEN ? next.slice(-HOST_HISTORY_LEN) : next;
          });
        }
      } catch (exc) {
        if (!cancelled) {
          setHostError(exc instanceof Error ? exc.message : "Sunucu kaynak metrikleri alinamadi.");
        }
      } finally {
        inFlightRef.current = false;
      }
    }
    void tick();
    const id = window.setInterval(tick, HOST_REFRESH_INTERVAL_SEC * 1000);
    return () => {
      cancelled = true;
      window.clearInterval(id);
    };
  }, []);

  const stats = useMemo(() => {
    const total = devices.length;
    const online = devices.filter((d) => d.communicationStatus === "online").length;
    const offline = devices.filter((d) => d.communicationStatus === "offline").length;
    const unknown = devices.filter((d) => d.communicationStatus === "unknown").length;
    const commIssue = offline + unknown;
    const openAlarms = alarms.filter((a) => !a.reset).length;
    const gwTotal = gateways.length;
    const gwActive = gateways.filter((g) => g.is_active).length;
    const onlineRatio = total > 0 ? Math.round((online / total) * 100) : 0;
    return { total, online, offline, unknown, commIssue, openAlarms, gwTotal, gwActive, onlineRatio };
  }, [devices, gateways, alarms]);

  const riskiest = useMemo(() => {
    return devices
      .slice()
      .sort((a, b) => {
        const c = statusOrder(a) - statusOrder(b);
        if (c !== 0) return c;
        return lastUpdateTs(a) - lastUpdateTs(b);
      })
      .slice(0, 10);
  }, [devices]);

  const showSpinner = Boolean(loading);

  // CPU/RAM/Disk metrikleri (host varsa).
  const cpuTone = host ? percentTone(host.cpu.percent) : "ok";
  const memTone = host ? percentTone(host.memory.percent) : "ok";
  const diskTone = host ? percentTone(host.disk.percent) : "ok";

  return (
    <section className="system-status-shell">
      {/* HERO: baslik + global aksiyon */}
      <header className="sys-hero">
        <div className="sys-hero-text">
          <span className="sys-hero-eyebrow">Sistem durumu</span>
          <h1 className="sys-hero-title">Çatı yazılım canlı durum panosu</h1>
          <p className="sys-hero-lead">
            Cihaz haberleşmesi, gateway sağlığı ve sunucu kaynakları tek bakışta. Sunucu metrikleri her{" "}
            <strong>{HOST_REFRESH_INTERVAL_SEC} sn</strong>, cihaz/alarm verileri yenileme isteğiyle güncellenir.
          </p>
        </div>
        <div className="sys-hero-actions">
          {host ? (
            <div className="sys-hero-host">
              <span className="sys-hero-host-name">{host.info.hostname}</span>
              <span className="sys-hero-host-meta">
                {host.info.os_name} {host.info.os_release} · {host.info.machine}
              </span>
              <span className="sys-hero-host-meta">
                Uptime <strong>{formatDuration(host.info.uptime_seconds)}</strong> · Backend{" "}
                <strong>{formatDuration(host.info.process_uptime_seconds)}</strong>
              </span>
            </div>
          ) : null}
          {onRefresh ? (
            <button
              type="button"
              className="sys-hero-btn"
              disabled={showSpinner}
              onClick={() => void onRefresh()}
            >
              <span className="material-symbols-outlined">refresh</span>
              {showSpinner ? "Yenileniyor…" : "Yenile"}
            </button>
          ) : null}
        </div>
      </header>

      {/* KPI ŞERIDI: cihaz / haberleşme / alarm */}
      <section className="sys-kpis">
        <article className="sys-kpi sys-kpi--total">
          <div className="sys-kpi-icon">
            <span className="material-symbols-outlined">router</span>
          </div>
          <div className="sys-kpi-body">
            <span className="sys-kpi-label">Toplam cihaz</span>
            <strong className="sys-kpi-value">{stats.total}</strong>
            <span className="sys-kpi-sub">
              {stats.onlineRatio}% çevrimiçi
            </span>
          </div>
        </article>

        <article className="sys-kpi sys-kpi--ok">
          <div className="sys-kpi-icon">
            <span className="material-symbols-outlined">wifi</span>
          </div>
          <div className="sys-kpi-body">
            <span className="sys-kpi-label">Çevrimiçi</span>
            <strong className="sys-kpi-value">{stats.online}</strong>
            <span className="sys-kpi-sub">son veri zamanı sağlıklı</span>
          </div>
        </article>

        <article className="sys-kpi sys-kpi--warn">
          <div className="sys-kpi-icon">
            <span className="material-symbols-outlined">help</span>
          </div>
          <div className="sys-kpi-body">
            <span className="sys-kpi-label">Belirsiz</span>
            <strong className="sys-kpi-value">{stats.unknown}</strong>
            <span className="sys-kpi-sub">durum henüz raporlanmadı</span>
          </div>
        </article>

        <article className="sys-kpi sys-kpi--bad">
          <div className="sys-kpi-icon">
            <span className="material-symbols-outlined">wifi_off</span>
          </div>
          <div className="sys-kpi-body">
            <span className="sys-kpi-label">Çevrimdışı</span>
            <strong className="sys-kpi-value">{stats.offline}</strong>
            <span className="sys-kpi-sub">haberleşme kopuk</span>
          </div>
        </article>

        <article className="sys-kpi sys-kpi--accent">
          <div className="sys-kpi-icon">
            <span className="material-symbols-outlined">warning</span>
          </div>
          <div className="sys-kpi-body">
            <span className="sys-kpi-label">Haberleşme riski</span>
            <strong className="sys-kpi-value">{stats.commIssue}</strong>
            <span className="sys-kpi-sub">belirsiz + çevrimdışı</span>
          </div>
        </article>

        <article className="sys-kpi sys-kpi--alarm">
          <div className="sys-kpi-icon">
            <span className="material-symbols-outlined">notifications_active</span>
          </div>
          <div className="sys-kpi-body">
            <span className="sys-kpi-label">Aktif alarm</span>
            <strong className="sys-kpi-value">{stats.openAlarms}</strong>
            <span className="sys-kpi-sub">henüz reset edilmemiş</span>
          </div>
        </article>

        <article className="sys-kpi sys-kpi--gw">
          <div className="sys-kpi-icon">
            <span className="material-symbols-outlined">hub</span>
          </div>
          <div className="sys-kpi-body">
            <span className="sys-kpi-label">Gateway</span>
            <strong className="sys-kpi-value">
              {stats.gwActive} <span className="sys-kpi-frac">/ {stats.gwTotal}</span>
            </strong>
            <span className="sys-kpi-sub">aktif / toplam</span>
          </div>
        </article>
      </section>

      {/* SUNUCU KAYNAKLARI: 3 büyük donut + sağda ek metrikler */}
      <section className="sys-section sys-resources">
        <header className="sys-section-head">
          <div>
            <h2 className="sys-section-title">Sunucu kaynakları</h2>
            <p className="sys-section-lead">
              Çatı yazılım sunucusunun anlık CPU, bellek, disk ve ağ kullanımı. Her{" "}
              {HOST_REFRESH_INTERVAL_SEC} saniyede bir tazelenir.
            </p>
          </div>
          {host ? (
            <span className={`sys-pill sys-pill--${cpuTone}`}>
              {cpuTone === "bad" ? "Yoğun" : cpuTone === "warn" ? "Yüksek" : "Sağlıklı"}
            </span>
          ) : null}
        </header>

        {hostError && !host ? (
          <p className="sys-error-banner">{hostError}</p>
        ) : null}

        {!host && !hostError ? (
          <p className="sys-loading-banner">Sunucu metrikleri yükleniyor…</p>
        ) : null}

        {host ? (
          <div className="sys-resources-grid">
            <article className={`sys-resource sys-resource--${cpuTone}`}>
              <div className="sys-resource-donut">
                <Donut percent={host.cpu.percent} tone={cpuTone} />
                <div className="sys-resource-donut-center">
                  <strong>{host.cpu.percent.toFixed(0)}%</strong>
                  <span>CPU</span>
                </div>
              </div>
              <div className="sys-resource-body">
                <h3>İşlemci</h3>
                <p className="sys-resource-meta">
                  {host.cpu.physical_cores ?? "?"} fiziksel · {host.cpu.logical_cores ?? "?"} mantıksal çekirdek
                </p>
                {host.cpu.load_avg_1m != null ? (
                  <p className="sys-resource-meta">
                    Yük 1m / 5m / 15m:{" "}
                    <strong>
                      {host.cpu.load_avg_1m.toFixed(2)} · {(host.cpu.load_avg_5m ?? 0).toFixed(2)} ·{" "}
                      {(host.cpu.load_avg_15m ?? 0).toFixed(2)}
                    </strong>
                  </p>
                ) : null}
                <Sparkline values={cpuHistory} tone={cpuTone} />
              </div>
            </article>

            <article className={`sys-resource sys-resource--${memTone}`}>
              <div className="sys-resource-donut">
                <Donut percent={host.memory.percent} tone={memTone} />
                <div className="sys-resource-donut-center">
                  <strong>{host.memory.percent.toFixed(0)}%</strong>
                  <span>Bellek</span>
                </div>
              </div>
              <div className="sys-resource-body">
                <h3>RAM</h3>
                <p className="sys-resource-meta">
                  <strong>{formatBytes(host.memory.used_bytes)}</strong> /{" "}
                  {formatBytes(host.memory.total_bytes)} kullanımda
                </p>
                <p className="sys-resource-meta">
                  {formatBytes(host.memory.available_bytes)} boş
                </p>
                <Sparkline values={memHistory} tone={memTone} />
              </div>
            </article>

            <article className={`sys-resource sys-resource--${diskTone}`}>
              <div className="sys-resource-donut">
                <Donut percent={host.disk.percent} tone={diskTone} />
                <div className="sys-resource-donut-center">
                  <strong>{host.disk.percent.toFixed(0)}%</strong>
                  <span>Disk</span>
                </div>
              </div>
              <div className="sys-resource-body">
                <h3>Disk</h3>
                <p className="sys-resource-meta">
                  Yol: <code className="inline-code">{host.disk.path}</code>
                </p>
                <p className="sys-resource-meta">
                  <strong>{formatBytes(host.disk.used_bytes)}</strong> /{" "}
                  {formatBytes(host.disk.total_bytes)} kullanımda
                </p>
                <p className="sys-resource-meta">
                  {formatBytes(host.disk.free_bytes)} boş
                </p>
              </div>
            </article>
          </div>
        ) : null}

        {host ? (
          <div className="sys-resources-extras">
            {host.memory.swap_total_bytes != null && host.memory.swap_total_bytes > 0 ? (
              <div className="sys-extra-card">
                <span className="sys-extra-label">Swap</span>
                <strong className="sys-extra-value">
                  {(host.memory.swap_percent ?? 0).toFixed(0)}%
                </strong>
                <span className="sys-extra-sub">
                  {formatBytes(host.memory.swap_used_bytes ?? 0)} /{" "}
                  {formatBytes(host.memory.swap_total_bytes)}
                </span>
              </div>
            ) : null}
            <div className="sys-extra-card">
              <span className="sys-extra-label">Ağ ↑ (giden)</span>
              <strong className="sys-extra-value">{formatBytes(host.network.bytes_sent)}</strong>
              <span className="sys-extra-sub">
                {host.network.packets_sent.toLocaleString("tr-TR")} paket
              </span>
            </div>
            <div className="sys-extra-card">
              <span className="sys-extra-label">Ağ ↓ (gelen)</span>
              <strong className="sys-extra-value">{formatBytes(host.network.bytes_recv)}</strong>
              <span className="sys-extra-sub">
                {host.network.packets_recv.toLocaleString("tr-TR")} paket
              </span>
            </div>
            <div className="sys-extra-card">
              <span className="sys-extra-label">Boot zamanı</span>
              <strong className="sys-extra-value sys-extra-value--sm">
                {new Date(host.info.boot_time * 1000).toLocaleString("tr-TR")}
              </strong>
              <span className="sys-extra-sub">PID {host.info.process_pid}</span>
            </div>
          </div>
        ) : null}
      </section>

      {/* ALT GRID: 2 kolon — Öncelikli cihazlar + Gateway özeti */}
      <div className="sys-bottom-grid">
        <section className="sys-section sys-priority">
          <header className="sys-section-head">
            <div>
              <h2 className="sys-section-title">Öncelikli cihazlar</h2>
              <p className="sys-section-lead">Çevrimdışı / belirsiz olanlar önde, en eski veri yukarıda.</p>
            </div>
            <span className="sys-pill sys-pill--muted">{riskiest.length}</span>
          </header>
          {devices.length === 0 && !showSpinner ? (
            <p className="sys-empty">Kayıtlı cihaz yok.</p>
          ) : (
            <div className="sys-priority-table-wrap">
              <table className="sys-priority-table">
                <thead>
                  <tr>
                    <th style={{ width: 40 }}>#</th>
                    <th>Cihaz</th>
                    <th>Durum</th>
                    <th>Son veri</th>
                    <th style={{ width: 90 }}>Batarya</th>
                  </tr>
                </thead>
                <tbody>
                  {riskiest.map((d, i) => (
                    <tr key={d.id}>
                      <td className="sys-priority-rank">{i + 1}</td>
                      <td>
                        <div className="sys-priority-name">
                          <strong>{d.name}</strong>
                          <code className="inline-code">{d.code}</code>
                        </div>
                      </td>
                      <td>
                        <span className={`sys-status-chip sys-status-chip--${d.communicationStatus}`}>
                          <span className="sys-status-chip-dot" />
                          {commLabel(d.communicationStatus)}
                        </span>
                      </td>
                      <td className="sys-priority-time">
                        {d.lastUpdateAt ? new Date(d.lastUpdateAt).toLocaleString("tr-TR") : "—"}
                      </td>
                      <td>
                        {d.batteryPercent != null ? (
                          <div className="sys-battery">
                            <div className="sys-battery-track">
                              <div
                                className={`sys-battery-fill ${
                                  d.batteryPercent < 20
                                    ? "sys-battery-fill--bad"
                                    : d.batteryPercent < 40
                                    ? "sys-battery-fill--warn"
                                    : "sys-battery-fill--ok"
                                }`}
                                style={{ width: `${Math.max(0, Math.min(100, d.batteryPercent))}%` }}
                              />
                            </div>
                            <span>{Math.round(d.batteryPercent)}%</span>
                          </div>
                        ) : (
                          <span className="helper-text">—</span>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </section>

        <section className="sys-section sys-gateways">
          <header className="sys-section-head">
            <div>
              <h2 className="sys-section-title">Gateway özeti</h2>
              <p className="sys-section-lead">Aktiflik ve son görülme zamanı.</p>
            </div>
            <span className="sys-pill sys-pill--muted">{gateways.length}</span>
          </header>
          {gateways.length === 0 && !showSpinner ? (
            <p className="sys-empty">Tanımlı gateway yok.</p>
          ) : (
            <ul className="sys-gw-list">
              {gateways.map((g) => {
                const tone = lastSeenTone(g);
                return (
                  <li key={g.id} className={`sys-gw-item sys-gw-item--${tone}`}>
                    <div className="sys-gw-left">
                      <span className="sys-gw-dot" />
                      <div>
                        <strong className="sys-gw-name">{g.name}</strong>
                        <code className="inline-code">{g.code}</code>
                      </div>
                    </div>
                    <div className="sys-gw-right">
                      <span className={`sys-gw-status sys-gw-status--${g.is_active ? "active" : "passive"}`}>
                        {g.is_active ? "Aktif" : "Pasif"}
                      </span>
                      <span className="helper-text">
                        {g.last_seen_at
                          ? `Son görülme: ${new Date(g.last_seen_at).toLocaleString("tr-TR")}`
                          : "Henüz görülmedi"}
                      </span>
                    </div>
                  </li>
                );
              })}
            </ul>
          )}
        </section>
      </div>
    </section>
  );
}
