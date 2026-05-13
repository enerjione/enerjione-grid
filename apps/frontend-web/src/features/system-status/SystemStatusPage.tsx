import { useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";

import { fetchHostStatus, fetchServicesStatus, loadSession } from "../../shared/api";
import { WsStatusBadge } from "../../components/WsStatusBadge";
import type { WsConnectionState } from "../../shared/useLiveValuesSocket";
import type {
  AlarmEvent,
  DeviceRow,
  Gateway,
  HostStatus,
  ServicesReport,
  ServiceStatus
} from "../../shared/types";

type Props = {
  devices: DeviceRow[];
  gateways: Gateway[];
  alarms: AlarmEvent[];
  loading?: boolean;
  onRefresh?: () => void | Promise<void>;
  /** Live telemetry WebSocket baglantisi durumu — header'dan tasindi. */
  wsState?: WsConnectionState;
};

/** Sunucu kaynak / servis durumu yenileme aralığı (sn). */
const HOST_REFRESH_INTERVAL_SEC = 5;
const SERVICES_REFRESH_INTERVAL_SEC = 10;
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

function formatDuration(
  seconds: number | null | undefined,
  units?: { d: string; h: string; m: string }
): string {
  if (seconds == null || !Number.isFinite(seconds) || seconds < 0) return "—";
  const u = units ?? { d: "g", h: "sa", m: "dk" };
  const s = Math.floor(seconds);
  const days = Math.floor(s / 86400);
  const hours = Math.floor((s % 86400) / 3600);
  const minutes = Math.floor((s % 3600) / 60);
  const parts: string[] = [];
  if (days > 0) parts.push(`${days}${u.d}`);
  if (hours > 0 || days > 0) parts.push(`${hours}${u.h}`);
  parts.push(`${minutes}${u.m}`);
  return parts.join(" ");
}

function percentTone(percent: number): "ok" | "warn" | "bad" {
  if (percent >= 90) return "bad";
  if (percent >= 75) return "warn";
  return "ok";
}

/** SVG donut: 0-100 yuzdeyi animasyonlu cizer. */
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
  const strokeColor = tone === "bad" ? "#dc2626" : tone === "warn" ? "#f59e0b" : "#10b981";
  const trackColor = "rgba(148, 163, 184, 0.18)";
  return (
    <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`} className="donut">
      <circle cx={size / 2} cy={size / 2} r={r} stroke={trackColor} strokeWidth={thickness} fill="none" />
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
  const stepX = width / (values.length - 1);
  const points = values
    .map((v, i) => {
      const x = i * stepX;
      const y = height - (Math.max(0, Math.min(100, v)) / 100) * height;
      return `${x.toFixed(1)},${y.toFixed(1)}`;
    })
    .join(" ");
  const areaPath = `M0,${height} L${points
    .split(" ")
    .map((p) => p)
    .join(" L")} L${width},${height} Z`;
  const strokeColor = tone === "bad" ? "#dc2626" : tone === "warn" ? "#f59e0b" : "#10b981";
  const fillColor =
    tone === "bad"
      ? "rgba(220, 38, 38, 0.15)"
      : tone === "warn"
      ? "rgba(245, 158, 11, 0.15)"
      : "rgba(16, 185, 129, 0.15)";
  return (
    <svg width={width} height={height} viewBox={`0 0 ${width} ${height}`} preserveAspectRatio="none" className="sparkline">
      <path d={areaPath} fill={fillColor} />
      <polyline
        points={points}
        fill="none"
        stroke={strokeColor}
        strokeWidth={2}
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

function gatewayLastSeenTone(gw: Gateway): "ok" | "warn" | "bad" | "muted" {
  if (!gw.is_active) return "muted";
  if (!gw.last_seen_at) return "warn";
  const sec = (Date.now() - new Date(gw.last_seen_at).getTime()) / 1000;
  if (sec < 60) return "ok";
  if (sec < 600) return "warn";
  return "bad";
}

function serviceRoleIcon(role: ServiceStatus["role"]): string {
  switch (role) {
    case "db":
      return "database";
    case "broker":
      return "lan";
    case "worker":
      return "memory";
    case "gateway":
      return "hub";
    case "self":
      return "api";
    default:
      return "settings";
  }
}

export function SystemStatusPage({ devices, gateways, alarms, loading, onRefresh, wsState }: Props) {
  const { t, i18n } = useTranslation();
  const localeTag = i18n.language?.startsWith("tr") ? "tr-TR" : "en-US";
  const isTr = i18n.language?.startsWith("tr");
  const durationUnits = isTr
    ? { d: "g", h: "sa", m: "dk" }
    : { d: "d", h: "h", m: "m" };
  const gatewayStatusLabel = (tone: "ok" | "warn" | "bad" | "muted"): string => {
    if (tone === "ok") return t("systemStatus.gateways.stateOnline");
    if (tone === "warn") return t("systemStatus.gateways.stateLagging");
    if (tone === "bad") return t("systemStatus.gateways.stateOffline");
    return t("systemStatus.gateways.stateInactive");
  };
  const serviceRoleLabel = (role: ServiceStatus["role"]): string => {
    if (role === "db" || role === "broker" || role === "worker" || role === "gateway" || role === "self") {
      return t(`systemStatus.services.role.${role}`);
    }
    return role;
  };
  // Sunucu (backend host) anlik kaynak metrikleri
  const [host, setHost] = useState<HostStatus | null>(null);
  const [hostError, setHostError] = useState<string | null>(null);
  const hostInFlightRef = useRef(false);

  // Servis durumlari
  const [services, setServices] = useState<ServicesReport | null>(null);
  const [servicesError, setServicesError] = useState<string | null>(null);
  const servicesInFlightRef = useRef(false);

  const [cpuHistory, setCpuHistory] = useState<number[]>([]);
  const [memHistory, setMemHistory] = useState<number[]>([]);

  // Host metrikleri polling
  useEffect(() => {
    let cancelled = false;
    async function tick() {
      if (hostInFlightRef.current) return;
      const session = loadSession();
      if (!session) return;
      hostInFlightRef.current = true;
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
          setHostError(exc instanceof Error ? exc.message : t("systemStatus.host.errorMetrics"));
        }
      } finally {
        hostInFlightRef.current = false;
      }
    }
    void tick();
    const id = window.setInterval(tick, HOST_REFRESH_INTERVAL_SEC * 1000);
    return () => {
      cancelled = true;
      window.clearInterval(id);
    };
  }, []);

  // Servis durumlari polling (DB/Rabbit/worker'lar)
  useEffect(() => {
    let cancelled = false;
    async function tick() {
      if (servicesInFlightRef.current) return;
      const session = loadSession();
      if (!session) return;
      servicesInFlightRef.current = true;
      try {
        const report = await fetchServicesStatus(session.accessToken);
        if (!cancelled) {
          setServices(report);
          setServicesError(null);
        }
      } catch (exc) {
        if (!cancelled) {
          setServicesError(exc instanceof Error ? exc.message : t("systemStatus.services.errorFetch"));
        }
      } finally {
        servicesInFlightRef.current = false;
      }
    }
    void tick();
    const id = window.setInterval(tick, SERVICES_REFRESH_INTERVAL_SEC * 1000);
    return () => {
      cancelled = true;
      window.clearInterval(id);
    };
  }, []);

  // Cihaz ozeti — sadece sayilar, tablo yok.
  const deviceStats = useMemo(() => {
    const total = devices.length;
    const online = devices.filter((d) => d.communicationStatus === "online").length;
    // Haberleşmeyen = offline + unknown (kullanici 'bunlari ayiralim' dedi:
    // 'haberlesen' = online, geri kalan haberlesmeyen).
    const offline = total - online;
    const onlineRatio = total > 0 ? Math.round((online / total) * 100) : 0;
    return { total, online, offline, onlineRatio };
  }, [devices]);

  const alarmStats = useMemo(() => {
    return { open: alarms.filter((a) => !a.reset).length };
  }, [alarms]);

  const gatewayStats = useMemo(() => {
    const total = gateways.length;
    const active = gateways.filter((g) => g.is_active).length;
    return { total, active };
  }, [gateways]);

  const serviceCounts = useMemo(() => {
    if (!services) return { total: 0, healthy: 0, unhealthy: 0 };
    const total = services.services.length;
    const healthy = services.services.filter((s) => s.healthy).length;
    return { total, healthy, unhealthy: total - healthy };
  }, [services]);

  const showSpinner = Boolean(loading);

  const cpuTone = host ? percentTone(host.cpu.percent) : "ok";
  const memTone = host ? percentTone(host.memory.percent) : "ok";
  const diskTone = host ? percentTone(host.disk.percent) : "ok";

  return (
    <section className="system-status-shell">
      {/* Sag ust kose floating Yenile butonu (sticky; scroll'da hep erisilebilir) */}
      {onRefresh ? (
        <button
          type="button"
          className="sys-fab"
          disabled={showSpinner}
          onClick={() => void onRefresh()}
          title={host ? `${host.info.hostname} · ${t("systemStatus.host.infoUptime")} ${formatDuration(host.info.uptime_seconds, durationUnits)}` : t("common.refresh")}
        >
          <span
            className={`material-symbols-outlined ${showSpinner ? "sys-fab-spin" : ""}`}
          >
            refresh
          </span>
          <span>{showSpinner ? t("common.refreshing") : t("common.refresh")}</span>
        </button>
      ) : null}

      {/* Canli telemetri WebSocket baglantisi rozeti — header'dan tasindi */}
      {wsState ? (
        <section className="sys-ws-status-bar">
          <div className="sys-ws-status-label">
            <span className="material-symbols-outlined">sync_alt</span>
            <strong>{t("systemStatus.liveSocket.title")}</strong>
            <small>{t("systemStatus.liveSocket.hint")}</small>
          </div>
          <WsStatusBadge state={wsState} />
        </section>
      ) : null}

      {/* KPI: 4 ana sayim - Toplam / Haberleşen / Haberleşmeyen / Alarm */}
      <section className="sys-kpis sys-kpis--lg">
        <article className="sys-kpi sys-kpi--total">
          <div className="sys-kpi-icon">
            <span className="material-symbols-outlined">router</span>
          </div>
          <div className="sys-kpi-body">
            <span className="sys-kpi-label">{t("systemStatus.kpi.totalDevices")}</span>
            <strong className="sys-kpi-value">{deviceStats.total}</strong>
            <span className="sys-kpi-sub">{t("systemStatus.kpi.totalDevicesSub", { percent: deviceStats.onlineRatio })}</span>
          </div>
        </article>

        <article className="sys-kpi sys-kpi--ok">
          <div className="sys-kpi-icon">
            <span className="material-symbols-outlined">wifi</span>
          </div>
          <div className="sys-kpi-body">
            <span className="sys-kpi-label">{t("systemStatus.kpi.online")}</span>
            <strong className="sys-kpi-value">{deviceStats.online}</strong>
            <span className="sys-kpi-sub">{t("systemStatus.kpi.onlineSub")}</span>
          </div>
        </article>

        <article className="sys-kpi sys-kpi--bad">
          <div className="sys-kpi-icon">
            <span className="material-symbols-outlined">wifi_off</span>
          </div>
          <div className="sys-kpi-body">
            <span className="sys-kpi-label">{t("systemStatus.kpi.offline")}</span>
            <strong className="sys-kpi-value">{deviceStats.offline}</strong>
            <span className="sys-kpi-sub">{t("systemStatus.kpi.offlineSub")}</span>
          </div>
        </article>

        <article className="sys-kpi sys-kpi--alarm">
          <div className="sys-kpi-icon">
            <span className="material-symbols-outlined">notifications_active</span>
          </div>
          <div className="sys-kpi-body">
            <span className="sys-kpi-label">{t("systemStatus.kpi.activeAlarm")}</span>
            <strong className="sys-kpi-value">{alarmStats.open}</strong>
            <span className="sys-kpi-sub">{t("systemStatus.kpi.activeAlarmSub")}</span>
          </div>
        </article>

        <article className="sys-kpi sys-kpi--gw">
          <div className="sys-kpi-icon">
            <span className="material-symbols-outlined">hub</span>
          </div>
          <div className="sys-kpi-body">
            <span className="sys-kpi-label">{t("systemStatus.kpi.gateway")}</span>
            <strong className="sys-kpi-value">
              {gatewayStats.active} <span className="sys-kpi-frac">/ {gatewayStats.total}</span>
            </strong>
            <span className="sys-kpi-sub">{t("systemStatus.kpi.gatewaySub")}</span>
          </div>
        </article>

        <article
          className={`sys-kpi ${
            serviceCounts.unhealthy > 0 ? "sys-kpi--bad" : "sys-kpi--ok"
          }`}
        >
          <div className="sys-kpi-icon">
            <span className="material-symbols-outlined">monitor_heart</span>
          </div>
          <div className="sys-kpi-body">
            <span className="sys-kpi-label">{t("systemStatus.kpi.services")}</span>
            <strong className="sys-kpi-value">
              {serviceCounts.healthy} <span className="sys-kpi-frac">/ {serviceCounts.total}</span>
            </strong>
            <span className="sys-kpi-sub">
              {serviceCounts.unhealthy === 0
                ? t("systemStatus.kpi.servicesAllOk")
                : t("systemStatus.kpi.servicesIssue", { count: serviceCounts.unhealthy })}
            </span>
          </div>
        </article>
      </section>

      {/* 3 dikey kart yan yana: Sunucu Kaynaklari · Servisler · Gateway'ler */}
      <div className="sys-tri-grid">
        {/* ------ KART 1: SUNUCU KAYNAKLARI ------ */}
        <section className="sys-card">
          <header className="sys-card-head">
            <div className="sys-card-title-wrap">
              <span className="material-symbols-outlined sys-card-icon sys-card-icon--cpu">
                memory
              </span>
              <h2 className="sys-card-title">{t("systemStatus.host.title")}</h2>
            </div>
            {host ? (
              <span className={`sys-pill sys-pill--${cpuTone}`}>
                {cpuTone === "bad"
                  ? t("systemStatus.host.stateBusy")
                  : cpuTone === "warn"
                  ? t("systemStatus.host.stateHigh")
                  : t("systemStatus.host.stateOk")}
              </span>
            ) : null}
          </header>

          {hostError && !host ? <p className="sys-error-banner">{hostError}</p> : null}
          {!host && !hostError ? <p className="sys-loading-banner">{t("systemStatus.host.loading")}</p> : null}

          {host ? (
            <div className="sys-card-body">
              {/* Tek satir 3 mini-donut: CPU / RAM / Disk + altlarinda detay */}
              <div className="sys-mini-donuts">
                <div className={`sys-mini-donut sys-mini-donut--${cpuTone}`}>
                  <div className="sys-mini-donut-vis">
                    <Donut percent={host.cpu.percent} tone={cpuTone} size={84} thickness={9} />
                    <div className="sys-mini-donut-center">
                      <strong>{host.cpu.percent.toFixed(0)}%</strong>
                    </div>
                  </div>
                  <span className="sys-mini-donut-label">CPU</span>
                  <span className="sys-mini-donut-detail">
                    {host.cpu.physical_cores ?? "?"}P · {host.cpu.logical_cores ?? "?"}T
                  </span>
                </div>

                <div className={`sys-mini-donut sys-mini-donut--${memTone}`}>
                  <div className="sys-mini-donut-vis">
                    <Donut percent={host.memory.percent} tone={memTone} size={84} thickness={9} />
                    <div className="sys-mini-donut-center">
                      <strong>{host.memory.percent.toFixed(0)}%</strong>
                    </div>
                  </div>
                  <span className="sys-mini-donut-label">RAM</span>
                  <span className="sys-mini-donut-detail">
                    {formatBytes(host.memory.used_bytes)} / {formatBytes(host.memory.total_bytes)}
                  </span>
                </div>

                <div className={`sys-mini-donut sys-mini-donut--${diskTone}`}>
                  <div className="sys-mini-donut-vis">
                    <Donut percent={host.disk.percent} tone={diskTone} size={84} thickness={9} />
                    <div className="sys-mini-donut-center">
                      <strong>{host.disk.percent.toFixed(0)}%</strong>
                    </div>
                  </div>
                  <span className="sys-mini-donut-label">Disk</span>
                  <span className="sys-mini-donut-detail">
                    {formatBytes(host.disk.used_bytes)} / {formatBytes(host.disk.total_bytes)}
                  </span>
                </div>
              </div>

              {/* CPU sparkline trendi */}
              <div className={`sys-trend sys-trend--${cpuTone}`}>
                <div className="sys-trend-head">
                  <span className="material-symbols-outlined">trending_up</span>
                  <span className="sys-trend-label">{t("systemStatus.host.trendCpu")}</span>
                  <strong className="sys-trend-current">{host.cpu.percent.toFixed(0)}%</strong>
                </div>
                <Sparkline values={cpuHistory} tone={cpuTone} width={280} height={42} />
              </div>

              {/* Yuk ortalamasi (sadece Linux'ta var) — tek bagimsiz rozet */}
              {host.cpu.load_avg_1m != null ? (
                <div className="sys-loadavg">
                  <div className="sys-loadavg-icon">
                    <span className="material-symbols-outlined">speed</span>
                  </div>
                  <div className="sys-loadavg-body">
                    <span className="sys-loadavg-title">{t("systemStatus.host.loadAvg")}</span>
                    <div className="sys-loadavg-values">
                      <span>
                        <em>1m</em>
                        <strong>{host.cpu.load_avg_1m.toFixed(2)}</strong>
                      </span>
                      <span>
                        <em>5m</em>
                        <strong>{(host.cpu.load_avg_5m ?? 0).toFixed(2)}</strong>
                      </span>
                      <span>
                        <em>15m</em>
                        <strong>{(host.cpu.load_avg_15m ?? 0).toFixed(2)}</strong>
                      </span>
                    </div>
                  </div>
                </div>
              ) : null}

              {/* Ag trafigi ozel gorsel — yatay akis */}
              <div className="sys-net-panel">
                <div className="sys-metric-group-title">
                  <span className="material-symbols-outlined">lan</span>
                  {t("systemStatus.host.network")}
                </div>
                <div className="sys-net-flow">
                  <div className="sys-net-side sys-net-side--up">
                    <div className="sys-net-arrow">
                      <span className="material-symbols-outlined">arrow_upward</span>
                    </div>
                    <div className="sys-net-info">
                      <strong>{formatBytes(host.network.bytes_sent)}</strong>
                      <span>{t("systemStatus.host.netUp", { value: host.network.packets_sent.toLocaleString(localeTag) })}</span>
                    </div>
                  </div>
                  <div className="sys-net-divider" />
                  <div className="sys-net-side sys-net-side--down">
                    <div className="sys-net-arrow">
                      <span className="material-symbols-outlined">arrow_downward</span>
                    </div>
                    <div className="sys-net-info">
                      <strong>{formatBytes(host.network.bytes_recv)}</strong>
                      <span>{t("systemStatus.host.netDown", { value: host.network.packets_recv.toLocaleString(localeTag) })}</span>
                    </div>
                  </div>
                </div>
              </div>

              {/* Sistem bilgileri grubu */}
              <div className="sys-info-group">
                <div className="sys-metric-group-title">
                  <span className="material-symbols-outlined">badge</span>
                  {t("systemStatus.host.systemInfo")}
                </div>
                <div className="sys-info-tiles">
                  <div className="sys-info-tile">
                    <span className="sys-info-tile-icon">
                      <span className="material-symbols-outlined">dns</span>
                    </span>
                    <div>
                      <span className="sys-info-tile-label">{t("systemStatus.host.infoHost")}</span>
                      <strong className="sys-info-tile-val sys-info-tile-val--mono">
                        {host.info.hostname}
                      </strong>
                    </div>
                  </div>
                  <div className="sys-info-tile">
                    <span className="sys-info-tile-icon">
                      <span className="material-symbols-outlined">monitor</span>
                    </span>
                    <div>
                      <span className="sys-info-tile-label">{t("systemStatus.host.infoOS")}</span>
                      <strong className="sys-info-tile-val">
                        {host.info.os_name} {host.info.os_release}
                      </strong>
                      <span className="sys-info-tile-sub">{host.info.machine}</span>
                    </div>
                  </div>
                  <div className="sys-info-tile">
                    <span className="sys-info-tile-icon">
                      <span className="material-symbols-outlined">timer</span>
                    </span>
                    <div>
                      <span className="sys-info-tile-label">{t("systemStatus.host.infoUptime")}</span>
                      <strong className="sys-info-tile-val">
                        {formatDuration(host.info.uptime_seconds, durationUnits)}
                      </strong>
                      <span className="sys-info-tile-sub">
                        {new Date(host.info.boot_time * 1000).toLocaleString(localeTag)}
                      </span>
                    </div>
                  </div>
                  <div className="sys-info-tile">
                    <span className="sys-info-tile-icon">
                      <span className="material-symbols-outlined">deployed_code</span>
                    </span>
                    <div>
                      <span className="sys-info-tile-label">{t("systemStatus.host.infoBackend")}</span>
                      <strong className="sys-info-tile-val">
                        PID {host.info.process_pid}
                      </strong>
                      <span className="sys-info-tile-sub">
                        {t("systemStatus.host.backendActive", { duration: formatDuration(host.info.process_uptime_seconds, durationUnits) })}
                      </span>
                    </div>
                  </div>
                </div>
              </div>
            </div>
          ) : null}
        </section>

        {/* ------ KART 2: SERVIS DURUMLARI ------ */}
        <section className="sys-card">
          <header className="sys-card-head">
            <div className="sys-card-title-wrap">
              <span className="material-symbols-outlined sys-card-icon sys-card-icon--svc">
                monitor_heart
              </span>
              <h2 className="sys-card-title">{t("systemStatus.services.title")}</h2>
            </div>
            <span
              className={`sys-pill ${
                serviceCounts.unhealthy === 0 ? "sys-pill--ok" : "sys-pill--bad"
              }`}
            >
              {serviceCounts.healthy}/{serviceCounts.total}
            </span>
          </header>

          {servicesError && !services ? (
            <p className="sys-error-banner">{servicesError}</p>
          ) : null}
          {!services && !servicesError ? (
            <p className="sys-loading-banner">{t("systemStatus.services.loading")}</p>
          ) : null}

          {services ? (
            <ul className="sys-card-list">
              {services.services.map((svc) => (
                <li
                  key={`${svc.role}-${svc.name}`}
                  className={`sys-list-item ${
                    svc.healthy ? "sys-list-item--ok" : "sys-list-item--bad"
                  }`}
                >
                  <div
                    className={`sys-list-leading ${
                      svc.healthy ? "sys-list-leading--ok" : "sys-list-leading--bad"
                    }`}
                  >
                    <span className="material-symbols-outlined">
                      {serviceRoleIcon(svc.role)}
                    </span>
                  </div>
                  <div className="sys-list-body">
                    <div className="sys-list-head">
                      <strong>{svc.name}</strong>
                      <span className="sys-list-role">{serviceRoleLabel(svc.role)}</span>
                    </div>
                    <div className="sys-list-meta">
                      {svc.endpoint ? (
                        <code className="inline-code">{svc.endpoint}</code>
                      ) : null}
                      {svc.healthy && svc.latency_ms != null ? (
                        <span className="helper-text">{svc.latency_ms.toFixed(0)} ms</span>
                      ) : null}
                      {!svc.healthy && svc.detail ? (
                        <span className="sys-list-error" title={svc.detail}>
                          {svc.detail}
                        </span>
                      ) : null}
                    </div>
                  </div>
                  <span
                    className={`sys-list-status ${
                      svc.healthy ? "sys-list-status--ok" : "sys-list-status--bad"
                    }`}
                  >
                    <span className="sys-list-status-dot" />
                  </span>
                </li>
              ))}
            </ul>
          ) : null}
        </section>

        {/* ------ KART 3: GATEWAY DURUMLARI ------ */}
        <section className="sys-card">
          <header className="sys-card-head">
            <div className="sys-card-title-wrap">
              <span className="material-symbols-outlined sys-card-icon sys-card-icon--gw">
                hub
              </span>
              <h2 className="sys-card-title">{t("systemStatus.gateways.title")}</h2>
            </div>
            <span className="sys-pill sys-pill--muted">{gateways.length}</span>
          </header>
          {gateways.length === 0 && !showSpinner ? (
            <p className="sys-empty">{t("systemStatus.gateways.empty")}</p>
          ) : (
            <ul className="sys-card-list">
              {gateways.map((g) => {
                const tone = gatewayLastSeenTone(g);
                const ok = tone === "ok";
                return (
                  <li
                    key={g.id}
                    className={`sys-list-item sys-list-item--${tone}`}
                  >
                    <div className={`sys-list-leading sys-list-leading--${tone}`}>
                      <span className="material-symbols-outlined">router</span>
                    </div>
                    <div className="sys-list-body">
                      <div className="sys-list-head">
                        <strong>{g.name}</strong>
                        <code className="inline-code">{g.code}</code>
                      </div>
                      <div className="sys-list-meta">
                        <span className={`sys-gw-status sys-gw-status--${tone}`}>
                          {gatewayStatusLabel(tone)}
                        </span>
                        <span className="helper-text">
                          {g.last_seen_at
                            ? new Date(g.last_seen_at).toLocaleString(localeTag)
                            : t("systemStatus.gateways.neverSeen")}
                        </span>
                      </div>
                    </div>
                    <span
                      className={`sys-list-status ${
                        ok ? "sys-list-status--ok" : "sys-list-status--bad"
                      }`}
                    >
                      <span className="sys-list-status-dot" />
                    </span>
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
