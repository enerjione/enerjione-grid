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
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import {
  Activity,
  ArrowDown,
  ArrowUp,
  ArrowUpCircle,
  BellRing,
  Building2,
  CalendarClock,
  CheckCircle2,
  Cpu,
  Database,
  FolderKanban,
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

import {
  fetchHistorianStatus,
  fetchTelemetryPipelineStatus,
  fetchHostStatus,
  fetchServicesStatus,
  fetchVersionInfo,
  loadSession
} from "../../shared/api";
import { GatewayControlCard } from "./GatewayControlCard";
import { useProjectSettings } from "../../components/ProjectSettingsProvider";
import { deviceRuntimeStateOf } from "../../shared/deviceRuntimeState";
import { usePolling } from "../../shared/usePolling";
import type {
  AlarmEvent,
  DeviceRow,
  Gateway,
  HistorianStatus,
  TelemetryPipelineStatus,
  HostStatus,
  ServicesReport,
  ServiceStatus,
  VersionInfo
} from "../../shared/types";

type Props = {
  devices: DeviceRow[];
  gateways: Gateway[];
  alarms: AlarmEvent[];
  loading?: boolean;
  onRefresh?: () => void | Promise<void>;
  // WS rozeti BU SAYFADA YOK — bilincli. Soket burada zaten kapali
  // (App.tsx `liveValuesNeeded`), dolayisiyla rozet her zaman "Kopuk"
  // gosteriyordu ve gercek bir kopmayla ayirt edilemiyordu. Rozet soketin
  // ACIK oldugu Canli Degerler sayfasina tasindi.
};

/** Sunucu kaynak / servis durumu yenileme aralığı (sn). */
const HOST_REFRESH_INTERVAL_SEC = 5;
const SERVICES_REFRESH_INTERVAL_SEC = 10;
/** Historian saglik yenileme araligi (sn). Backend zaten 60 sn cache'liyor ve
 *  bu bilgi (hypertable/retention politikasi) saniyeler icinde degismez. */
const HISTORIAN_REFRESH_INTERVAL_SEC = 60;
/** Telemetri boru hatti yenileme araligi (sn). Historian'dan SIK: backlog
 *  saniyeler icinde degisebilen canli bir buyukluk ve tam da hizli
 *  buyudugunde gorulmesi gereken sey. Ucun maliyeti sifira yakin (surec-ici
 *  sayaclar, ek DB/NATS sorgusu yok), bu yuzden 10 sn sorun degil. */
const PIPELINE_REFRESH_INTERVAL_SEC = 10;
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

export function SystemStatusPage({
  devices,
  gateways,
  alarms,
  loading,
  onRefresh
}: Props) {
  const { t, i18n } = useTranslation();
  // Kurulumun kimligi (proje/musteri adi) — Proje Ayarlari'ndan gelir ve
  // Sistem Bilgisi kartinda "hangi saha" sorusunu cevaplar.
  const project = useProjectSettings().settings;
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
  const [historian, setHistorian] = useState<HistorianStatus | null>(null);
  const [pipeline, setPipeline] = useState<TelemetryPipelineStatus | null>(null);
  const [pipelineError, setPipelineError] = useState<string | null>(null);
  const [historianError, setHistorianError] = useState<string | null>(null);
  const servicesInFlightRef = useRef(false);

  // Oturum (token + rol) — gateway kontrol karti icin. `loadSession` senkron
  // storage okur; her render'da cagirmak yerine bir kez sakliyoruz.
  const [sessionInfo] = useState(() => loadSession());

  // Surum + guncelleme durumu. Backend sonucu 6 saat cache'liyor, o yuzden
  // burada polling YOK — sayfa acilisinda bir kez cekiyoruz.
  const [versionInfo, setVersionInfo] = useState<VersionInfo | null>(null);
  useEffect(() => {
    const session = loadSession();
    if (!session) return;
    let cancelled = false;
    void fetchVersionInfo(session.accessToken)
      .then((info) => {
        if (!cancelled) setVersionInfo(info);
      })
      .catch(() => {
        /* surum gosterimi kritik degil */
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const [cpuHistory, setCpuHistory] = useState<number[]>([]);
  const [memHistory, setMemHistory] = useState<number[]>([]);

  // Anlik ag hizi (B/s) — iki ardisik olcumun byte farkindan hesaplanir.
  // Kumulatif sayaclar (boot'tan beri toplam) tek basina bir sey soylemez;
  // operatorun gormek istedigi "su an ne kadar trafik akiyor".
  const [netRate, setNetRate] = useState<{ up: number; down: number } | null>(null);
  const prevNetRef = useRef<{ sent: number; recv: number; at: number } | null>(null);

  // Host metrikleri polling.
  //
  // `cancelled` bayragi kaldirildi: usePolling unmount'ta interval'i temizler,
  // React 18'de unmount sonrasi setState zaten sessiz no-op. inFlight ref'i
  // duruyor — yavas yanit ust uste istek yigmasin.
  const pollHost = useCallback(async () => {
    if (hostInFlightRef.current) return;
    const session = loadSession();
    if (!session) return;
    hostInFlightRef.current = true;
    try {
      const status = await fetchHostStatus(session.accessToken);
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
    } catch (exc) {
      const msg = exc instanceof Error ? exc.message : t("systemStatus.host.errorMetrics");
      // session_polling_401 sentinel'ini kullaniciya gosterme — loading banner kalsin
      setHostError(msg === "session_polling_401" ? null : msg);
    } finally {
      hostInFlightRef.current = false;
    }
  }, [t]);

  usePolling({ enabled: true, intervalMs: HOST_REFRESH_INTERVAL_SEC * 1000, fn: pollHost });

  // Servis durumlari polling (DB/Rabbit/worker'lar)
  const pollServices = useCallback(async () => {
    if (servicesInFlightRef.current) return;
    const session = loadSession();
    if (!session) return;
    servicesInFlightRef.current = true;
    try {
      const report = await fetchServicesStatus(session.accessToken);
      setServices(report);
      setServicesError(null);
    } catch (exc) {
      const msg = exc instanceof Error ? exc.message : t("systemStatus.services.errorFetch");
      setServicesError(msg === "session_polling_401" ? null : msg);
    } finally {
      servicesInFlightRef.current = false;
    }
  }, [t]);

  usePolling({
    enabled: true,
    intervalMs: SERVICES_REFRESH_INTERVAL_SEC * 1000,
    fn: pollServices
  });

  // --- Historian (telemetri arsivi) sagligi -------------------------------
  const pollHistorian = useCallback(async () => {
    const session = loadSession();
    if (!session) return;
    try {
      setHistorian(await fetchHistorianStatus(session.accessToken));
      setHistorianError(null);
    } catch (exc) {
      const msg = exc instanceof Error ? exc.message : t("systemStatus.historian.errorFetch");
      setHistorianError(msg === "session_polling_401" ? null : msg);
    }
  }, [t]);

  usePolling({
    enabled: true,
    intervalMs: HISTORIAN_REFRESH_INTERVAL_SEC * 1000,
    fn: pollHistorian
  });

  // --- Telemetri boru hatti: tuketici yetisiyor mu? -----------------------
  // Stream `discard=old` ile calisiyor: tampon dolarsa en eski mesajlar
  // SESSIZCE dusurulur. Bu gosterge o sessizligi gorunur kilan tek sey.
  //
  // Asama HIZLARI sunucudan gelmez: ardisik iki orneklemin last_seq
  // farkindan burada turetilir (sunucu durumsuz kalir). Ilk orneklemde hiz
  // bilinmez (null) — "olculuyor" gosterilir, sifir degil.
  const oncekiOrneklem = useRef<{ raw: number; norm: number; at: number } | null>(null);
  const [asamaHizlari, setAsamaHizlari] = useState<{
    giris: number | null;
    normalize: number | null;
  }>({ giris: null, normalize: null });
  const pollPipeline = useCallback(async () => {
    const session = loadSession();
    if (!session) return;
    try {
      const veri = await fetchTelemetryPipelineStatus(session.accessToken);
      setPipeline(veri);
      setPipelineError(null);
      const s = veri.stages;
      if (s?.raw_last_seq != null && s.normalized_last_seq != null) {
        const simdi = Date.now();
        const onceki = oncekiOrneklem.current;
        if (onceki && simdi > onceki.at) {
          const saniye = (simdi - onceki.at) / 1000;
          setAsamaHizlari({
            // Negatif fark = stream sifirlanmis (purge/yeniden kurulum);
            // sacma negatif hiz gostermek yerine "olculuyor"a don.
            giris:
              s.raw_last_seq >= onceki.raw
                ? Math.round((s.raw_last_seq - onceki.raw) / saniye)
                : null,
            normalize:
              s.normalized_last_seq >= onceki.norm
                ? Math.round((s.normalized_last_seq - onceki.norm) / saniye)
                : null
          });
        }
        oncekiOrneklem.current = { raw: s.raw_last_seq, norm: s.normalized_last_seq, at: simdi };
      }
    } catch (exc) {
      const msg = exc instanceof Error ? exc.message : t("systemStatus.pipeline.errorFetch");
      setPipelineError(msg === "session_polling_401" ? null : msg);
    }
  }, [t]);

  usePolling({
    enabled: true,
    intervalMs: PIPELINE_REFRESH_INTERVAL_SEC * 1000,
    fn: pollPipeline
  });

  // Cihaz ozeti — sadece sayilar, tablo yok.
  const deviceStats = useMemo(() => {
    const total = devices.length;
    // "Haberlesen" = CALISMA-ZAMANI kovasi `healthy`, yani `online` VE
    // `smart_idle`. Uyuyan bir Horstmann haberlesmiyor degildir; sadece
    // sirasini bekliyordur (sozlesme bolum 5). Eski ikili sayim onu
    // "haberlesmeyen" kovasina koyup saglikli filoyu arizali gosteriyordu.
    const online = devices.filter((d) => deviceRuntimeStateOf(d).bucket === "healthy").length;
    // Haberleşmeyen = geri kalan (bozulmus, arizali, bilinmeyen).
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
  // NOT: takas (swap) olcumleri artik GOSTERILMIYOR (kullanici istegi,
  // 2026-08-06). Backend alanlari duruyor; RAM baskisi RAM halkasi ve
  // trend grafiginden okunuyor.
  const perCpu = host?.cpu.per_cpu_percent ?? [];

  return (
    <section className="system-status-shell">
      {/* UST SERIT — tek parca gosterge cubugu.
          Sayfa BASLIGI YOK: sekme zaten "Sistem Durumu" diyor, ikinci kez
          yazmak dikey alan harciyor ve asil bilgiyi (canli veri akiyor mu,
          hangi makine, hangi surum) asagi itiyordu.
          Onceki hal uc ayri kume gibi duruyordu (baslik bloku / surum kutusu /
          Yenile). Hepsi tek bir seride toplandi: soldan saga "veri akiyor mu"
          -> "hangi makine, ne zamandir ayakta, son ornek" -> "hangi surum" ->
          eylem. Okuma sirasi operatorun sordugu sirayla ayni. */}
      <header className="sys-bar">
        {host ? (
          <div className="sys-bar-meta">
            <span className="sys-bar-chip" title={host.info.hostname}>
              <Server size={14} strokeWidth={2} />
              <span className="sys-bar-chip-text">{host.info.hostname}</span>
            </span>
            <span className="sys-bar-sep" aria-hidden="true" />
            <span className="sys-bar-chip">
              <Timer size={14} strokeWidth={2} />
              {formatDuration(host.info.uptime_seconds, durationUnits)}
            </span>
            <span className="sys-bar-sep" aria-hidden="true" />
            <span className="sys-bar-clock" title={t("systemStatus.host.liveHint")}>
              <span className="sys-live-dot" />
              {new Date(host.sampled_at * 1000).toLocaleTimeString(localeTag)}
            </span>
          </div>
        ) : null}

        {/* KPI'LAR BURADA — eskiden altta 6 ayri buyuk kart olarak duruyor ve
            ekranin ust ucte birini yiyordu. Ayni 6 sayi tek seride sigiyor;
            asil icerik (kaynaklar, servisler, gateway) yukari geliyor. */}
        <div className="sys-bar-stats">
          <span className="sys-stat" title={t("systemStatus.kpi.totalDevices")}>
            <Router size={14} strokeWidth={2} />
            <strong>{deviceStats.total}</strong>
            <span>{t("systemStatus.kpi.totalDevices")}</span>
          </span>
          <span className="sys-stat sys-stat--ok" title={t("systemStatus.kpi.onlineSub")}>
            <Wifi size={14} strokeWidth={2} />
            <strong>{deviceStats.online}</strong>
            <span>{t("systemStatus.kpi.online")}</span>
          </span>
          <span
            className={`sys-stat ${deviceStats.offline > 0 ? "sys-stat--bad" : ""}`}
            title={t("systemStatus.kpi.offlineSub")}
          >
            <WifiOff size={14} strokeWidth={2} />
            <strong>{deviceStats.offline}</strong>
            <span>{t("systemStatus.kpi.offline")}</span>
          </span>
          <span
            className={`sys-stat ${alarmStats.open > 0 ? "sys-stat--alarm" : ""}`}
            title={t("systemStatus.kpi.activeAlarmSub")}
          >
            <BellRing size={14} strokeWidth={2} />
            <strong>{alarmStats.open}</strong>
            <span>{t("systemStatus.kpi.activeAlarm")}</span>
          </span>
          <span className="sys-stat" title={t("systemStatus.kpi.gatewaySub")}>
            <Network size={14} strokeWidth={2} />
            <strong>
              {gatewayStats.active}
              <span className="sys-stat-frac">/{gatewayStats.total}</span>
            </strong>
            <span>{t("systemStatus.kpi.gateway")}</span>
          </span>
          <span
            className={`sys-stat ${
              serviceCounts.unhealthy > 0 ? "sys-stat--bad" : "sys-stat--ok"
            }`}
            title={
              serviceCounts.unhealthy === 0
                ? t("systemStatus.kpi.servicesAllOk")
                : t("systemStatus.kpi.servicesIssue", { count: serviceCounts.unhealthy })
            }
          >
            <Activity size={14} strokeWidth={2} />
            <strong>
              {serviceCounts.healthy}
              <span className="sys-stat-frac">/{serviceCounts.total}</span>
            </strong>
            <span>{t("systemStatus.kpi.services")}</span>
          </span>
        </div>

        <div className="sys-bar-actions">
          {/* Surum + guncelleme ibaresi.
              BILEREK sadece BILGI: buradan guncelleme baslatilamaz. Guncelleme
              `update.sh` ile operator kontrolunde yapilir; calisan bir SCADA
              sisteminin arayuzden tetiklenen bir islemle yeniden baslamasi
              istenmiyor. */}
          {versionInfo ? (
            <div className="sys-bar-version">
              <span className="sys-ver-chip" title={t("systemStatus.version.label")}>
                <Package size={13} strokeWidth={2} />
                <span>v{versionInfo.current}</span>
              </span>
              {versionInfo.update_available && versionInfo.latest ? (
                <span
                  className="sys-version-badge is-update"
                  title={t("systemStatus.version.updateHint")}
                >
                  <ArrowUpCircle size={13} strokeWidth={2.2} />
                  {t("systemStatus.version.updateAvailable", {
                    version: versionInfo.latest
                  })}
                </span>
              ) : versionInfo.check_enabled && !versionInfo.error ? (
                <span className="sys-version-badge is-current">
                  <CheckCircle2 size={13} strokeWidth={2.2} />
                  {t("systemStatus.version.upToDate")}
                </span>
              ) : null}
            </div>
          ) : null}
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
              <span className="sys-refresh-label">
                {showSpinner ? t("common.refreshing") : t("common.refresh")}
              </span>
            </button>
          ) : null}
        </div>
      </header>

      {/* Gateway kontrol + uzaktan log. Kaynak/servis kartlarindan ONCE:
          "veri neden gelmiyor" teshisinde ilk bakilacak yer gateway'in
          ayakta olup olmadigi ve ne dedigi. */}
      {sessionInfo ? (
        <GatewayControlCard accessToken={sessionInfo.accessToken} role={sessionInfo.role} />
      ) : null}

      {/* 2 kolon: Sunucu Kaynaklari · Servisler (Gateway karti kaldirildi) */}
      <div className="sys-duo-grid">
        {/* SOL SUTUN: kaynak karti + altinda cekirdek/takas/ag ucluleri.
            Servis listesi sag sutunda cok uzun oldugu icin sol tarafta
            genis bir bosluk kaliyordu; ucluler tam o boslugu doldurur
            (kullanici istegi, 2026-08-06). Yapiskanlik (sticky) kart
            yerine SUTUNA uygulanir — aksi halde sutun icindeki kart
            yapisamaz. */}
        <div className="sys-col-left">
        {/* ------ KART 1: SUNUCU KAYNAKLARI ------ */}
        <section className="sys-card sys-card--resources">
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

              {/* NOT: Cekirdek kullanimi, takas alani ve ag trafigi bu karttan
                  CIKARILDI (kullanici istegi, 2026-08-06). Ucu de alt alta
                  dizilip sagda genis bir bosluk birakiyordu; artik kartin
                  altinda YAN YANA uc ayri kart olarak duruyorlar. */}

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

            </div>
          ) : null}
        </section>

        {/* ------ IKI KART YAN YANA: CEKIRDEK / AG ------
            Ikisi de "kaynak" kartinin icinde alt alta duruyordu; her biri tam
            genisligi kaplayip sagda genis bir bosluk birakiyordu. Ayri kartlar
            olarak yan yana konuldular (kullanici istegi, 2026-08-06).
            Gorunurluk kosulu korundu: tek cekirdekli makinede cekirdek karti
            CIKMAZ — o durumda ag karti satiri tek basina doldurur. */}
        {host ? (
          <div className="sys-duo-cards">
            {perCpu.length > 1 ? (
              <section className="sys-card sys-metric-card">
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
              </section>
            ) : null}

            {/* NOT: Takas alani (swap) karti KALDIRILDI (kullanici istegi,
                2026-08-06). RAM baskisi zaten yukaridaki RAM halkasindan ve
                trend grafiginden okunuyor; takas yuzdesi sahada nadiren
                karar degistiren bir olcum. */}

            {/* Ag trafigi — onde anlik hiz, arkada kumulatif toplam */}
            <section className="sys-card sys-metric-card">
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
            </section>
          </div>
        ) : null}
        </div>

        {/* ------ KART 2: SERVIS DURUMLARI ------
            Liste KIRPILMAZ / ic scroll YOK: tum servisler alt alta listelenir,
            sayfa kayar. Yanindaki kaynak karti sabit durdugu icin uzun liste
            baglami kaybettirmez. */}
        <section className="sys-card sys-card--services">
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
            {/* NOT: "Python surumu" ve "Disk yolu" kutulari KALDIRILDI
                (kullanici istegi, 2026-08-06). Python surumu operatorun
                karar veremeyecegi bir ic ayrinti; disk yolu ve bos alan
                zaten yukaridaki Disk halkasinda gorunuyor. */}

            {/* Kurulumun kimligi: proje ve musteri adi kurulum sirasinda
                Proje Ayarlari'ndan giriliyor. Sistem Durumu ekran goruntusu
                destege gonderildiginde "hangi saha" sorusunu bu iki kutu
                cevaplar. Bos birakilmislarsa kutu HIC cikmaz — bos etiket
                gostermek bilgi vermez. */}
            {project.project_name ? (
              <div className="sys-info-tile">
                <span className="sys-info-tile-icon">
                  <FolderKanban size={17} strokeWidth={2} />
                </span>
                <div>
                  <span className="sys-info-tile-label">{t("systemStatus.host.infoProject")}</span>
                  <strong className="sys-info-tile-val">{project.project_name}</strong>
                </div>
              </div>
            ) : null}
            {project.customer_name ? (
              <div className="sys-info-tile">
                <span className="sys-info-tile-icon">
                  <Building2 size={17} strokeWidth={2} />
                </span>
                <div>
                  <span className="sys-info-tile-label">{t("systemStatus.host.infoCustomer")}</span>
                  <strong className="sys-info-tile-val">{project.customer_name}</strong>
                </div>
              </div>
            ) : null}
            {host.info.first_started_at ? (
              <div className="sys-info-tile">
                <span className="sys-info-tile-icon">
                  <CalendarClock size={17} strokeWidth={2} />
                </span>
                <div>
                  {/* Etiket "kurulum" DEGIL "ilk calistirma": olculen sey
                      denetim kaydindaki en eski olaydir. Kurulum damgasi
                      tutan bir alan yok; "kurulum tarihi" demek olculmeyen
                      bir seyi olculmus gibi gosterirdi. */}
                  <span className="sys-info-tile-label">{t("systemStatus.host.infoFirstStarted")}</span>
                  <strong className="sys-info-tile-val">
                    {new Date(host.info.first_started_at).toLocaleDateString(localeTag)}
                  </strong>
                  <span className="sys-info-tile-sub">
                    {new Date(host.info.first_started_at).toLocaleTimeString(localeTag, {
                      hour: "2-digit",
                      minute: "2-digit"
                    })}
                  </span>
                </div>
              </div>
            ) : null}
          </div>
        </section>
      ) : null}

      {/* --- Historian (telemetri arsivi) sagligi --------------------------
          NEDEN AYRI KART: `telemetry_history` 90 gun retention'li bir
          TimescaleDB hypertable olarak tasarlandi. Retention kurulmamissa
          tablo sinirsiz buyur (600 cihazda gunde ~26M satir) ve bunun TEK
          belirtisi diskin dolmasidir. Operatorun bunu onceden gormesi icin
          durumu burada acikca gosteriyoruz. */}
      {historian ? (
        <section className="sys-card">
          <header className="sys-card-head">
            <div className="sys-card-title-wrap">
              <span className="sys-card-icon sys-card-icon--svc">
                <Database size={17} strokeWidth={2.1} />
              </span>
              <h2 className="sys-card-title">{t("systemStatus.historian.title")}</h2>
            </div>
            <div className="sys-head-meta">
              <span
                className={
                  historian.severity === "ok"
                    ? "sys-version-badge is-current"
                    : "sys-version-badge is-update"
                }
              >
                {historian.severity === "ok" ? (
                  <CheckCircle2 size={13} strokeWidth={2.4} />
                ) : (
                  <BellRing size={13} strokeWidth={2.4} />
                )}
                {t(`systemStatus.historian.severity.${historian.severity}`)}
              </span>
            </div>
          </header>
          <div className="sys-card-body">
            {historian.problems.length > 0 ? (
              <ul className="sys-card-list">
                {historian.problems.map((code) => (
                  <li key={code} className="sys-svc-row">
                    <span className="sys-svc-name">
                      {t(`systemStatus.historian.problem.${code}`)}
                    </span>
                  </li>
                ))}
              </ul>
            ) : null}
            <div className="sys-info-tiles">
              <div className="sys-info-tile">
                <span className="sys-info-tile-icon">
                  <FolderTree size={17} strokeWidth={2} />
                </span>
                <div>
                  <span className="sys-info-tile-label">
                    {t("systemStatus.historian.retention")}
                  </span>
                  <strong className="sys-info-tile-val">
                    {historian.retention_days != null
                      ? t("systemStatus.historian.retentionDays", {
                          count: historian.retention_days
                        })
                      : t("systemStatus.historian.retentionNone")}
                  </strong>
                  <span className="sys-info-tile-sub">
                    {historian.is_hypertable
                      ? t("systemStatus.historian.hypertableYes")
                      : t("systemStatus.historian.hypertableNo")}
                  </span>
                </div>
              </div>
              <div className="sys-info-tile">
                <span className="sys-info-tile-icon">
                  <HardDrive size={17} strokeWidth={2} />
                </span>
                <div>
                  <span className="sys-info-tile-label">
                    {t("systemStatus.historian.size")}
                  </span>
                  <strong className="sys-info-tile-val">
                    {formatBytes(historian.total_bytes)}
                  </strong>
                  <span className="sys-info-tile-sub">
                    {historian.compression_enabled
                      ? t("systemStatus.historian.compressionOn")
                      : t("systemStatus.historian.compressionOff")}
                  </span>
                </div>
              </div>
              <div className="sys-info-tile">
                <span className="sys-info-tile-icon">
                  <LayoutGrid size={17} strokeWidth={2} />
                </span>
                <div>
                  <span className="sys-info-tile-label">
                    {t("systemStatus.historian.rows")}
                  </span>
                  <strong className="sys-info-tile-val">
                    {historian.row_estimate != null
                      ? historian.row_estimate.toLocaleString()
                      : "—"}
                  </strong>
                  {/* Tahmin oldugu ACIKCA yazilir: tam sayim (COUNT(*)) bu
                      tabloda tam tarama demek, bilerek yapmiyoruz. */}
                  <span className="sys-info-tile-sub">
                    {t("systemStatus.historian.rowsEstimate")}
                  </span>
                </div>
              </div>
              <div className="sys-info-tile">
                <span className="sys-info-tile-icon">
                  <Timer size={17} strokeWidth={2} />
                </span>
                <div>
                  <span className="sys-info-tile-label">
                    {t("systemStatus.historian.newest")}
                  </span>
                  <strong className="sys-info-tile-val sys-info-tile-val--mono">
                    {historian.newest_sample_at
                      ? new Date(historian.newest_sample_at).toLocaleString()
                      : "—"}
                  </strong>
                  {historian.oldest_sample_at ? (
                    <span className="sys-info-tile-sub">
                      {t("systemStatus.historian.oldest", {
                        value: new Date(historian.oldest_sample_at).toLocaleDateString()
                      })}
                    </span>
                  ) : null}
                </div>
              </div>
            </div>
          </div>
        </section>
      ) : historianError ? (
        <section className="sys-card">
          <div className="sys-card-body">{historianError}</div>
        </section>
      ) : null}

      {/* --- Telemetri boru hatti ------------------------------------------
          NEDEN AYRI KART: telemetri NATS stream'inde tamponlanir ve stream
          `discard=old` ile calisir. Tuketici gelis hizinin gerisine duserse
          tampon dolar ve EN ESKI mesajlar SESSIZCE dusurulur — ekranda hata
          yok, alarm yok, sadece bazi okumalar hic gelmemis olur. Bu kart o
          sessizligi gorunur kilar; "backlog" surekli 0 civari beklenir. */}
      {pipeline ? (
        <section className="sys-card">
          <header className="sys-card-head">
            <div className="sys-card-title-wrap">
              <span className="sys-card-icon sys-card-icon--svc">
                <Activity size={17} strokeWidth={2.1} />
              </span>
              <h2 className="sys-card-title">{t("systemStatus.pipeline.title")}</h2>
            </div>
            <div className="sys-head-meta">
              <span
                className={
                  pipeline.severity === "ok"
                    ? "sys-version-badge is-current"
                    : "sys-version-badge is-update"
                }
              >
                {pipeline.severity === "ok" ? (
                  <CheckCircle2 size={13} strokeWidth={2.4} />
                ) : (
                  <BellRing size={13} strokeWidth={2.4} />
                )}
                {t(`systemStatus.pipeline.severity.${pipeline.severity}`)}
              </span>
            </div>
          </header>
          <div className="sys-card-body">
            {pipeline.severity !== "ok" ? (
              <ul className="sys-card-list">
                <li className="sys-svc-row">
                  <span className="sys-svc-name">
                    {!pipeline.running || !pipeline.connected
                      ? t("systemStatus.pipeline.problem.disconnected")
                      : t("systemStatus.pipeline.problem.backlogHigh")}
                  </span>
                </li>
              </ul>
            ) : null}
            {/* ASAMA GORUNUMU — her kuyruk ayri. Tek "bekleyen" sayisi, ust
                kuyruk alt kuyruga bosalirken "kuyruk kendi kendine artiyor"
                yanilgisi yaratiyordu (sahada yasandi). Oklarin ustunde o
                asamayi isleyen bilesenin hizi yazar. */}
            {pipeline.stages ? (
              <div className="sys-pipe-flow">
                <div className="sys-pipe-stage">
                  <span className="sys-pipe-stage-etiket">
                    {t("systemStatus.pipeline.stages.rawQueue")}
                  </span>
                  <strong className="sys-pipe-stage-deger">
                    {pipeline.stages.raw_pending == null
                      ? "—"
                      : pipeline.stages.raw_pending.toLocaleString()}
                  </strong>
                  <span className="sys-pipe-stage-alt">
                    {asamaHizlari.giris == null
                      ? t("systemStatus.pipeline.stages.measuring")
                      : t("systemStatus.pipeline.stages.inRate", {
                          count: asamaHizlari.giris
                        })}
                  </span>
                </div>
                <div className="sys-pipe-ok" aria-hidden="true">
                  <span className="sys-pipe-ok-etiket">tag-engine</span>
                  <span className="sys-pipe-ok-hiz">
                    {asamaHizlari.normalize == null
                      ? "…"
                      : t("systemStatus.pipeline.stages.perSec", {
                          count: asamaHizlari.normalize
                        })}
                  </span>
                  <span className="sys-pipe-ok-cizgi">→</span>
                </div>
                <div className="sys-pipe-stage">
                  <span className="sys-pipe-stage-etiket">
                    {t("systemStatus.pipeline.stages.normQueue")}
                  </span>
                  <strong className="sys-pipe-stage-deger">
                    {(
                      (pipeline.stages.normalized_prio_pending ?? 0) +
                      (pipeline.stages.normalized_bulk_pending ?? 0) +
                      (pipeline.stages.normalized_legacy_pending ?? 0)
                    ).toLocaleString()}
                  </strong>
                  <span className="sys-pipe-stage-alt">
                    {t("systemStatus.pipeline.stages.prio")}:{" "}
                    {(pipeline.stages.normalized_prio_pending ?? 0).toLocaleString()}
                    {" · "}
                    {t("systemStatus.pipeline.stages.bulk")}:{" "}
                    {(pipeline.stages.normalized_bulk_pending ?? 0).toLocaleString()}
                  </span>
                </div>
                <div className="sys-pipe-ok" aria-hidden="true">
                  <span className="sys-pipe-ok-etiket">
                    {t("systemStatus.pipeline.stages.persister")}
                  </span>
                  <span className="sys-pipe-ok-hiz">
                    {t("systemStatus.pipeline.stages.perSec", {
                      count: Math.round(pipeline.throughput_msgs_per_sec)
                    })}
                  </span>
                  <span className="sys-pipe-ok-cizgi">→</span>
                </div>
                <div className="sys-pipe-stage sys-pipe-stage--son">
                  <span className="sys-pipe-stage-etiket">
                    {t("systemStatus.pipeline.stages.archive")}
                  </span>
                  <strong className="sys-pipe-stage-deger">
                    {pipeline.processed_total.toLocaleString()}
                  </strong>
                  <span className="sys-pipe-stage-alt">
                    {t("systemStatus.pipeline.stages.archiveSub")}
                  </span>
                </div>
              </div>
            ) : null}
            <div className="sys-info-tiles">
              {!pipeline.stages ? (
              <div className="sys-info-tile">
                <span className="sys-info-tile-icon">
                  <Timer size={17} strokeWidth={2} />
                </span>
                <div>
                  <span className="sys-info-tile-label">
                    {t("systemStatus.pipeline.backlog")}
                  </span>
                  <strong className="sys-info-tile-val">
                    {pipeline.backlog == null ? "—" : pipeline.backlog.toLocaleString()}
                  </strong>
                  <span className="sys-info-tile-sub">
                    {t("systemStatus.pipeline.backlogHint", {
                      count: pipeline.backlog_warn_threshold
                    })}
                  </span>
                  {/* Kalicilastirma hangi akistan besleniyor. "raw" gecis
                      oncesi drenaj fazidir; birikim erimeden NORMALIZED'e
                      gecilmez. Bu satir olmadan gecisin tamamlanip
                      tamamlanmadigi ancak NATS CLI ile anlasilirdi. */}
                  {pipeline.source ? (
                    <span className="sys-info-tile-sub">
                      {t(
                        pipeline.source === "normalized"
                          ? "systemStatus.pipeline.sourceNormalized"
                          : "systemStatus.pipeline.sourceRaw"
                      )}
                    </span>
                  ) : null}
                </div>
              </div>
              ) : null}
              <div className="sys-info-tile">
                <span className="sys-info-tile-icon">
                  <TrendingUp size={17} strokeWidth={2} />
                </span>
                <div>
                  <span className="sys-info-tile-label">
                    {t("systemStatus.pipeline.throughput")}
                  </span>
                  <strong className="sys-info-tile-val">
                    {t("systemStatus.pipeline.msgsPerSec", {
                      count: Math.round(pipeline.throughput_msgs_per_sec)
                    })}
                  </strong>
                  <span className="sys-info-tile-sub">
                    {t("systemStatus.pipeline.batchSub", {
                      size: pipeline.last_batch_size,
                      sec: pipeline.last_batch_duration_sec.toFixed(2)
                    })}
                  </span>
                </div>
              </div>
              <div className="sys-info-tile">
                <span className="sys-info-tile-icon">
                  <Database size={17} strokeWidth={2} />
                </span>
                <div>
                  <span className="sys-info-tile-label">
                    {t("systemStatus.pipeline.processed")}
                  </span>
                  <strong className="sys-info-tile-val">
                    {pipeline.processed_total.toLocaleString()}
                  </strong>
                  <span className="sys-info-tile-sub">
                    {t("systemStatus.pipeline.badSub", { count: pipeline.bad_total })}
                  </span>
                </div>
              </div>
              <div className="sys-info-tile">
                <span className="sys-info-tile-icon">
                  <Plug size={17} strokeWidth={2} />
                </span>
                <div>
                  <span className="sys-info-tile-label">
                    {t("systemStatus.pipeline.connection")}
                  </span>
                  <strong className="sys-info-tile-val">
                    {pipeline.connected
                      ? t("systemStatus.pipeline.connected")
                      : t("systemStatus.pipeline.disconnected")}
                  </strong>
                  <span className="sys-info-tile-sub">
                    {t("systemStatus.pipeline.reconnectsSub", {
                      count: pipeline.reconnects
                    })}
                  </span>
                </div>
              </div>
            </div>
          </div>
        </section>
      ) : pipelineError ? (
        <section className="sys-card">
          <div className="sys-card-body">{pipelineError}</div>
        </section>
      ) : null}
    </section>
  );
}
