/**
 * Sistem Durumu — host kaynaklari + servis sagligi dashboard'u.
 *
 * Ikonografi NOTU: bu sayfa material-symbols DEGIL `lucide-react` kullanir
 * (ayni set Header/HeaderSearch'te de kullaniliyor). Lucide stroke-tabanli
 * oldugu icin metrik kartlarindaki donut/sparkline cizgileriyle ayni gorsel
 * dile sahip; material-symbols'un dolgun ikonlari bu sayfada agir duruyordu.
 * Yeni ikon eklerken lucide'dan named import edin, `material-symbols`
 * class'ini bu dosyada KULLANMAYIN.
 */
import { useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import {
  Activity,
  ArrowDown,
  ArrowLeftRight,
  ArrowUp,
  BellRing,
  Code,
  Cpu,
  Database,
  Gauge,
  Globe,
  HardDrive,
  Cog,
  FolderTree,
  Info,
  LayoutGrid,
  MemoryStick,
  Monitor,
  Network,
  Package,
  Plug,
  RefreshCw,
  Router,
  Server,
  Timer,
  TrendingUp,
  Wifi,
  WifiOff,
  Zap,
  type LucideIcon
} from "lucide-react";

import { fetchHostStatus, fetchServicesStatus, loadSession } from "../../shared/api";
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

type Tone = "ok" | "warn" | "bad";

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

/** Anlik ag hizi: iki olcum arasindaki byte farkindan turetilir. */
function formatRate(bytesPerSec: number | null): string {
  if (bytesPerSec == null || !Number.isFinite(bytesPerSec) || bytesPerSec < 0) return "—";
  if (bytesPerSec < 1) return "0 B/s";
  let n = bytesPerSec;
  let i = 0;
  while (n >= 1024 && i < BYTE_UNITS.length - 1) {
    n /= 1024;
    i += 1;
  }
  return `${n.toFixed(n >= 100 ? 0 : 1)} ${BYTE_UNITS[i]}/s`;
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

function percentTone(percent: number): Tone {
  if (percent >= 90) return "bad";
  if (percent >= 75) return "warn";
  return "ok";
}

const TONE_STROKE: Record<Tone, string> = {
  ok: "#10b981",
  warn: "#f59e0b",
  bad: "#dc2626"
};

const TONE_FILL: Record<Tone, string> = {
  ok: "rgba(16, 185, 129, 0.15)",
  warn: "rgba(245, 158, 11, 0.15)",
  bad: "rgba(220, 38, 38, 0.15)"
};

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
  tone: Tone;
}) {
  const safe = Math.max(0, Math.min(100, Number.isFinite(percent) ? percent : 0));
  const r = (size - thickness) / 2;
  const c = 2 * Math.PI * r;
  const offset = c * (1 - safe / 100);
  const trackColor = "rgba(148, 163, 184, 0.18)";
  return (
    <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`} className="donut">
      <circle cx={size / 2} cy={size / 2} r={r} stroke={trackColor} strokeWidth={thickness} fill="none" />
      <circle
        cx={size / 2}
        cy={size / 2}
        r={r}
        stroke={TONE_STROKE[tone]}
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
  tone: Tone;
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
  return (
    <svg width={width} height={height} viewBox={`0 0 ${width} ${height}`} preserveAspectRatio="none" className="sparkline">
      <path d={areaPath} fill={TONE_FILL[tone]} />
      <polyline
        points={points}
        fill="none"
        stroke={TONE_STROKE[tone]}
        strokeWidth={2}
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

function serviceRoleIcon(role: ServiceStatus["role"]): LucideIcon {
  switch (role) {
    case "db":
      return Database;
    case "broker":
      return Network;
    case "worker":
      return Cog;
    case "gateway":
      return Router;
    case "ftp":
      return FolderTree;
    case "web":
      return Globe;
    case "self":
      return Plug;
    default:
      return Server;
  }
}

/** Servis listesi rol'e gore gruplanir: cekirdek altyapi / isleyiciler / entegrasyon. */
const SERVICE_GROUPS: { key: "core" | "workers" | "integrations"; roles: ServiceStatus["role"][] }[] = [
  { key: "core", roles: ["self", "db", "broker", "web"] },
  { key: "workers", roles: ["worker"] },
  { key: "integrations", roles: ["gateway", "ftp"] }
];

export function SystemStatusPage({ devices, gateways, alarms, loading, onRefresh }: Props) {
  const { t, i18n } = useTranslation();
  const localeTag = i18n.language?.startsWith("tr") ? "tr-TR" : "en-US";
  const isTr = i18n.language?.startsWith("tr");
  const durationUnits = isTr
    ? { d: "g", h: "sa", m: "dk" }
    : { d: "d", h: "h", m: "m" };
  const serviceRoleLabel = (role: ServiceStatus["role"]): string => {
    if (
      role === "db" ||
      role === "broker" ||
      role === "worker" ||
      role === "gateway" ||
      role === "ftp" ||
      role === "web" ||
      role === "self"
    ) {
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

  // Anlik ag hizi (B/s) — iki ardisik olcumun byte farkindan hesaplanir.
  // Kumulatif sayaclar (boot'tan beri toplam) tek basina bir sey soylemez;
  // operatorun gormek istedigi "su an ne kadar trafik akiyor".
  const [netRate, setNetRate] = useState<{ up: number; down: number } | null>(null);
  const prevNetRef = useRef<{ sent: number; recv: number; at: number } | null>(null);

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
          const prev = prevNetRef.current;
          const dt = prev ? status.sampled_at - prev.at : 0;
          if (prev && dt > 0.5) {
            setNetRate({
              up: Math.max(0, (status.network.bytes_sent - prev.sent) / dt),
              down: Math.max(0, (status.network.bytes_recv - prev.recv) / dt)
            });
          }
          prevNetRef.current = {
            sent: status.network.bytes_sent,
            recv: status.network.bytes_recv,
            at: status.sampled_at
          };
        }
      } catch (exc) {
        if (!cancelled) {
          const msg = exc instanceof Error ? exc.message : t("systemStatus.host.errorMetrics");
          // session_polling_401 sentinel'ini kullaniciya gosterme — loading banner kalsin
          setHostError(msg === "session_polling_401" ? null : msg);
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
          const msg = exc instanceof Error ? exc.message : t("systemStatus.services.errorFetch");
          setServicesError(msg === "session_polling_401" ? null : msg);
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
    if (!services) return { total: 0, healthy: 0, unhealthy: 0, avgLatency: null as number | null };
    const total = services.services.length;
    const healthy = services.services.filter((s) => s.healthy).length;
    const latencies = services.services
      .filter((s) => s.healthy && s.latency_ms != null && s.latency_ms > 0)
      .map((s) => s.latency_ms as number);
    const avgLatency =
      latencies.length > 0 ? latencies.reduce((a, b) => a + b, 0) / latencies.length : null;
    return { total, healthy, unhealthy: total - healthy, avgLatency };
  }, [services]);

  // Servisleri rol gruplarina dagit — bos gruplar render edilmez.
  const serviceGroups = useMemo(() => {
    const list = services?.services ?? [];
    return SERVICE_GROUPS.map((group) => ({
      key: group.key,
      items: list.filter((svc) => group.roles.includes(svc.role))
    })).filter((group) => group.items.length > 0);
  }, [services]);

  const showSpinner = Boolean(loading);

  const cpuTone = host ? percentTone(host.cpu.percent) : "ok";
  const memTone = host ? percentTone(host.memory.percent) : "ok";
  const diskTone = host ? percentTone(host.disk.percent) : "ok";
  const swapPercent = host?.memory.swap_percent ?? null;
  const hasSwap = Boolean(host && (host.memory.swap_total_bytes ?? 0) > 0 && swapPercent != null);
  const swapTone: Tone = swapPercent != null ? percentTone(swapPercent) : "ok";
  const perCpu = host?.cpu.per_cpu_percent ?? [];

  return (
    <section className="system-status-shell">
      {/* Ust serit — Yenile artik KPI'larin uzerine binen floating buton degil,
          basligin yanindaki normal bir toolbar ogesi. Yaninda canli ornekleme
          saati ve host adi var; boylece butonun neyi yeniledigi belli. */}
      <header className="sys-page-head">
        <div className="sys-page-head-text">
          <h2>{t("systemStatus.title")}</h2>
          {host ? (
            <div className="sys-page-head-meta">
              <span>
                <Server size={14} strokeWidth={2} />
                {host.info.hostname}
              </span>
              <span>
                <Timer size={14} strokeWidth={2} />
                {formatDuration(host.info.uptime_seconds, durationUnits)}
              </span>
              <span
                className="sys-page-head-live"
                title={t("systemStatus.host.liveHint")}
              >
                <span className="sys-live-dot" />
                {new Date(host.sampled_at * 1000).toLocaleTimeString(localeTag)}
              </span>
            </div>
          ) : null}
        </div>
        {onRefresh ? (
          <button
            type="button"
            className="sys-refresh-btn"
            disabled={showSpinner}
            onClick={() => void onRefresh()}
          >
            <RefreshCw
              size={16}
              strokeWidth={2.2}
              className={showSpinner ? "sys-spin" : undefined}
            />
            {showSpinner ? t("common.refreshing") : t("common.refresh")}
          </button>
        ) : null}
      </header>

      {/* KPI: 6 ana sayim - Toplam / Haberleşen / Haberleşmeyen / Alarm / Gateway / Servis */}
      <section className="sys-kpis sys-kpis--lg">
        <article className="sys-kpi sys-kpi--total">
          <div className="sys-kpi-icon">
            <Router size={20} strokeWidth={2} />
          </div>
          <div className="sys-kpi-body">
            <span className="sys-kpi-label">{t("systemStatus.kpi.totalDevices")}</span>
            <strong className="sys-kpi-value">{deviceStats.total}</strong>
            <span className="sys-kpi-sub">{t("systemStatus.kpi.totalDevicesSub", { percent: deviceStats.onlineRatio })}</span>
          </div>
        </article>

        <article className="sys-kpi sys-kpi--ok">
          <div className="sys-kpi-icon">
            <Wifi size={20} strokeWidth={2} />
          </div>
          <div className="sys-kpi-body">
            <span className="sys-kpi-label">{t("systemStatus.kpi.online")}</span>
            <strong className="sys-kpi-value">{deviceStats.online}</strong>
            <span className="sys-kpi-sub">{t("systemStatus.kpi.onlineSub")}</span>
          </div>
        </article>

        <article className="sys-kpi sys-kpi--bad">
          <div className="sys-kpi-icon">
            <WifiOff size={20} strokeWidth={2} />
          </div>
          <div className="sys-kpi-body">
            <span className="sys-kpi-label">{t("systemStatus.kpi.offline")}</span>
            <strong className="sys-kpi-value">{deviceStats.offline}</strong>
            <span className="sys-kpi-sub">{t("systemStatus.kpi.offlineSub")}</span>
          </div>
        </article>

        <article className="sys-kpi sys-kpi--alarm">
          <div className="sys-kpi-icon">
            <BellRing size={20} strokeWidth={2} />
          </div>
          <div className="sys-kpi-body">
            <span className="sys-kpi-label">{t("systemStatus.kpi.activeAlarm")}</span>
            <strong className="sys-kpi-value">{alarmStats.open}</strong>
            <span className="sys-kpi-sub">{t("systemStatus.kpi.activeAlarmSub")}</span>
          </div>
        </article>

        <article className="sys-kpi sys-kpi--gw">
          <div className="sys-kpi-icon">
            <Network size={20} strokeWidth={2} />
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
            <Activity size={20} strokeWidth={2} />
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

      {/* 2 kolon: Sunucu Kaynaklari · Servisler (Gateway karti kaldirildi) */}
      <div className="sys-duo-grid">
        {/* ------ KART 1: SUNUCU KAYNAKLARI ------ */}
        <section className="sys-card">
          <header className="sys-card-head">
            <div className="sys-card-title-wrap">
              <span className="sys-card-icon sys-card-icon--cpu">
                <Cpu size={17} strokeWidth={2.1} />
              </span>
              <h2 className="sys-card-title">{t("systemStatus.host.title")}</h2>
            </div>
            <div className="sys-head-meta">
              {host ? (
                <span className={`sys-pill sys-pill--${cpuTone}`}>
                  {cpuTone === "bad"
                    ? t("systemStatus.host.stateBusy")
                    : cpuTone === "warn"
                    ? t("systemStatus.host.stateHigh")
                    : t("systemStatus.host.stateOk")}
                </span>
              ) : null}
            </div>
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
                  <span className="sys-mini-donut-free">
                    {t("systemStatus.host.freeLabel", { value: formatBytes(host.memory.available_bytes) })}
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
                  <span className="sys-mini-donut-free">
                    {t("systemStatus.host.freeLabel", { value: formatBytes(host.disk.free_bytes) })}
                  </span>
                </div>
              </div>

              {/* CPU + RAM sparkline trendleri yan yana */}
              <div className="sys-trend-duo">
                <div className={`sys-trend sys-trend--${cpuTone}`}>
                  <div className="sys-trend-head">
                    <TrendingUp size={15} strokeWidth={2.1} />
                    <span className="sys-trend-label">{t("systemStatus.host.trendCpu")}</span>
                    <strong className="sys-trend-current">{host.cpu.percent.toFixed(0)}%</strong>
                  </div>
                  <Sparkline values={cpuHistory} tone={cpuTone} width={280} height={42} />
                </div>
                <div className={`sys-trend sys-trend--${memTone}`}>
                  <div className="sys-trend-head">
                    <MemoryStick size={15} strokeWidth={2.1} />
                    <span className="sys-trend-label">{t("systemStatus.host.trendRam")}</span>
                    <strong className="sys-trend-current">{host.memory.percent.toFixed(0)}%</strong>
                  </div>
                  <Sparkline values={memHistory} tone={memTone} width={280} height={42} />
                </div>
              </div>

              {/* Cekirdek basina CPU kullanimi — tek bir cekirdegin dolmasi
                  ortalamada gorunmez, burada aninda fark edilir. */}
              {perCpu.length > 1 ? (
                <div className="sys-cores">
                  <div className="sys-metric-group-title">
                    <LayoutGrid size={14} strokeWidth={2.1} />
                    {t("systemStatus.host.cores", { value: perCpu.length })}
                  </div>
                  <div className="sys-core-bars">
                    {perCpu.map((value, index) => {
                      const tone = percentTone(value);
                      return (
                        <div
                          key={index}
                          className={`sys-core-bar sys-core-bar--${tone}`}
                          title={`#${index + 1} · ${value.toFixed(0)}%`}
                        >
                          <div className="sys-core-bar-track">
                            <div
                              className="sys-core-bar-fill"
                              style={{ height: `${Math.max(2, Math.min(100, value))}%` }}
                            />
                          </div>
                          <span className="sys-core-bar-label">{index + 1}</span>
                        </div>
                      );
                    })}
                  </div>
                </div>
              ) : null}

              {/* Takas alani — RAM tukendiginde buradan anlasilir (Linux/Windows). */}
              {hasSwap ? (
                <div className={`sys-meter sys-meter--${swapTone}`}>
                  <div className="sys-meter-head">
                    <ArrowLeftRight size={15} strokeWidth={2.1} />
                    <span className="sys-meter-label">{t("systemStatus.host.swap")}</span>
                    <strong className="sys-meter-value">{(swapPercent ?? 0).toFixed(0)}%</strong>
                  </div>
                  <div className="sys-meter-track">
                    <div
                      className={`sys-meter-fill sys-meter-fill--${swapTone}`}
                      style={{ width: `${Math.max(0, Math.min(100, swapPercent ?? 0))}%` }}
                    />
                  </div>
                  <span className="sys-meter-sub">
                    {formatBytes(host.memory.swap_used_bytes)} / {formatBytes(host.memory.swap_total_bytes)}
                  </span>
                </div>
              ) : null}

              {/* Yuk ortalamasi (sadece Linux'ta var) — tek bagimsiz rozet */}
              {host.cpu.load_avg_1m != null ? (
                <div className="sys-loadavg">
                  <div className="sys-loadavg-icon">
                    <Gauge size={18} strokeWidth={2.1} />
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

              {/* Ag trafigi — onde anlik hiz, arkada kumulatif toplam */}
              <div className="sys-net-panel">
                <div className="sys-metric-group-title">
                  <Network size={14} strokeWidth={2.1} />
                  {t("systemStatus.host.network")}
                </div>
                <div className="sys-net-flow">
                  <div className="sys-net-side sys-net-side--up">
                    <div className="sys-net-arrow">
                      <ArrowUp size={17} strokeWidth={2.4} />
                    </div>
                    <div className="sys-net-info">
                      <strong>{formatRate(netRate?.up ?? null)}</strong>
                      <span>{t("systemStatus.host.netTotalUp", { value: formatBytes(host.network.bytes_sent) })}</span>
                      <span>{t("systemStatus.host.netUp", { value: host.network.packets_sent.toLocaleString(localeTag) })}</span>
                    </div>
                  </div>
                  <div className="sys-net-divider" />
                  <div className="sys-net-side sys-net-side--down">
                    <div className="sys-net-arrow">
                      <ArrowDown size={17} strokeWidth={2.4} />
                    </div>
                    <div className="sys-net-info">
                      <strong>{formatRate(netRate?.down ?? null)}</strong>
                      <span>{t("systemStatus.host.netTotalDown", { value: formatBytes(host.network.bytes_recv) })}</span>
                      <span>{t("systemStatus.host.netDown", { value: host.network.packets_recv.toLocaleString(localeTag) })}</span>
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
              <span className="sys-card-icon sys-card-icon--svc">
                <Activity size={17} strokeWidth={2.1} />
              </span>
              <h2 className="sys-card-title">{t("systemStatus.services.title")}</h2>
            </div>
            <div className="sys-head-meta">
              {serviceCounts.avgLatency != null ? (
                <span className="sys-latency-chip" title={t("systemStatus.services.avgLatencyHint")}>
                  <Zap size={13} strokeWidth={2.2} />
                  {t("systemStatus.services.avgLatency", { value: serviceCounts.avgLatency.toFixed(0) })}
                </span>
              ) : null}
              <span
                className={`sys-pill ${
                  serviceCounts.unhealthy === 0 ? "sys-pill--ok" : "sys-pill--bad"
                }`}
              >
                {serviceCounts.healthy}/{serviceCounts.total}
              </span>
            </div>
          </header>

          {servicesError && !services ? (
            <p className="sys-error-banner">{servicesError}</p>
          ) : null}
          {!services && !servicesError ? (
            <p className="sys-loading-banner">{t("systemStatus.services.loading")}</p>
          ) : null}

          {services ? (
            <div className="sys-svc-groups">
              {serviceGroups.map((group) => {
                const groupBad = group.items.filter((s) => !s.healthy).length;
                return (
                  <div key={group.key} className="sys-svc-group">
                    <div className="sys-svc-group-head">
                      <span className="sys-svc-group-title">
                        {t(`systemStatus.services.group.${group.key}`)}
                      </span>
                      <span
                        className={`sys-svc-group-count ${
                          groupBad > 0 ? "sys-svc-group-count--bad" : ""
                        }`}
                      >
                        {group.items.length - groupBad}/{group.items.length}
                      </span>
                    </div>
                    <ul className="sys-card-list">
                      {group.items.map((svc) => {
                        const RoleIcon = serviceRoleIcon(svc.role);
                        return (
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
                            <RoleIcon size={17} strokeWidth={2} />
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
                                <span className="sys-list-latency">{svc.latency_ms.toFixed(0)} ms</span>
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
                            title={
                              svc.healthy
                                ? t("systemStatus.services.stateUp")
                                : t("systemStatus.services.stateDown")
                            }
                          >
                            <span className="sys-list-status-dot" />
                          </span>
                        </li>
                        );
                      })}
                    </ul>
                  </div>
                );
              })}
            </div>
          ) : null}
        </section>
      </div>

      {/* ------ ALT SERIT: SISTEM BILGISI (tam genislik) ------ */}
      {host ? (
        <section className="sys-card sys-card--strip">
          <header className="sys-card-head">
            <div className="sys-card-title-wrap">
              <span className="sys-card-icon sys-card-icon--info">
                <Info size={17} strokeWidth={2.1} />
              </span>
              <h2 className="sys-card-title">{t("systemStatus.host.systemInfo")}</h2>
            </div>
          </header>
          <div className="sys-info-tiles">
            <div className="sys-info-tile">
              <span className="sys-info-tile-icon">
                <Server size={17} strokeWidth={2} />
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
                <Monitor size={17} strokeWidth={2} />
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
                <Timer size={17} strokeWidth={2} />
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
                <Package size={17} strokeWidth={2} />
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
            <div className="sys-info-tile">
              <span className="sys-info-tile-icon">
                <Code size={17} strokeWidth={2} />
              </span>
              <div>
                <span className="sys-info-tile-label">{t("systemStatus.host.infoPython")}</span>
                <strong className="sys-info-tile-val sys-info-tile-val--mono">
                  {host.info.python_version}
                </strong>
              </div>
            </div>
            <div className="sys-info-tile">
              <span className="sys-info-tile-icon">
                <HardDrive size={17} strokeWidth={2} />
              </span>
              <div>
                <span className="sys-info-tile-label">{t("systemStatus.host.infoDiskPath")}</span>
                <strong className="sys-info-tile-val sys-info-tile-val--mono">
                  {host.disk.path}
                </strong>
                <span className="sys-info-tile-sub">
                  {t("systemStatus.host.freeLabel", { value: formatBytes(host.disk.free_bytes) })}
                </span>
              </div>
            </div>
          </div>
        </section>
      ) : null}
    </section>
  );
}
