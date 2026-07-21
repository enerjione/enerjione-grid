/**
 * DeviceDetailPage — cihaz detay sayfasi (profesyonel dashboard duzeni).
 *
 * Duzen: sol sabit sidebar (DeviceSidebar) + ust sekmeler + icerik.
 * Sekmeler: Genel Bakis (KPI serit + Mevcut Durum + Ariza Yonu + Son Olaylar) /
 * Trendler (grafikler) / Olaylar (tam liste) / Komutlar / Yapilandirma.
 *
 * Veri: signalLiveValues (SignalLiveRow[]) + historian (grafik/sparkline) +
 * sistem olaylari/komut gecmisi (Son Olaylar). activeSource sidebar'dan kontrol.
 */

import { useMemo, useState, type ReactNode } from "react";
import { useTranslation } from "react-i18next";

import type {
  DeviceRow,
  Gateway,
  SignalCatalogRow,
  SignalDataType,
  SignalLiveRow,
  SignalSource,
} from "../../shared/types";
import { DeviceCommandsPanel } from "./DeviceCommandsPanel";
import { DeviceChartsPanel } from "./DeviceChartsPanel";
import { DeviceConfigPanel } from "./DeviceConfigPanel";
import { DeviceEventsTable } from "./DeviceEventsTable";
import { DeviceSidebar } from "./DeviceSidebar";
import { Sparkline } from "./Sparkline";

type TopologyInfo = { regionName: string; lineName: string } | undefined;

type TabKey = "overview" | "trends" | "events" | "commands" | "config";

type Props = {
  deviceId: number;
  devices: DeviceRow[];
  values: SignalLiveRow[];
  signals: SignalCatalogRow[];
  gateways: Gateway[];
  topologyInfo?: TopologyInfo;
  canCommand?: boolean;
  canConfig?: boolean;
  onDeviceCommand?: (deviceCode: string, command: string, label: string) => Promise<void>;
  token?: string;
};

// ---- Kategori tanimi (source-agnostic suffix -> kategori + TR etiket) --------
type CatKey = "measure" | "fault" | "status" | "direction";
type SigDef = { suffix: string; label: string; cat: CatKey };

const SIGNALS: SigDef[] = [
  { suffix: "actual_current", label: "Akım", cat: "measure" },
  { suffix: "actual_voltage", label: "Gerilim", cat: "measure" },
  { suffix: "average_current", label: "Ort. Akım", cat: "measure" },
  { suffix: "maximum_current", label: "Max. Akım", cat: "measure" },
  { suffix: "conductor_temperature", label: "İletken Sıc.", cat: "measure" },
  { suffix: "device_temperature", label: "Cihaz Sıc.", cat: "measure" },
  { suffix: "fault_current", label: "Arıza Akımı", cat: "fault" },
  { suffix: "fault_duration", label: "Arıza Süresi", cat: "fault" },
  { suffix: "last_good_known_current", label: "Son İyi Akım", cat: "fault" },
  { suffix: "minimum_current", label: "Min. Akım", cat: "fault" },
  { suffix: "overcurrent_tripped", label: "Aşırı akım", cat: "status" },
  { suffix: "delta_i_delta_t_tripped", label: "ΔI/Δt", cat: "status" },
  { suffix: "voltage_loss", label: "Gerilim kaybı", cat: "status" },
  { suffix: "current_loss", label: "Akım kaybı", cat: "status" },
  { suffix: "battery_status", label: "Pil durumu", cat: "status" },
  { suffix: "permanent_fault", label: "Kalıcı arıza", cat: "status" },
  { suffix: "momentary_fault", label: "Geçici arıza", cat: "status" },
];

// Durum satiri gorsel meta (ikon).
const STATUS_ICONS: Record<string, string> = {
  overcurrent_tripped: "bolt",
  delta_i_delta_t_tripped: "show_chart",
  voltage_loss: "power_off",
  current_loss: "flash_off",
  battery_status: "battery_alert",
  permanent_fault: "warning",
  momentary_fault: "error_outline",
};

const CNT_PERMANENT = "permanent_fault_counter";
const CNT_MOMENTARY = "momentary_fault_counter";

const GATEWAY_LIVE_SEC = 60;
const NUMBER_FORMATTER = new Intl.NumberFormat("tr-TR", {
  minimumFractionDigits: 0,
  maximumFractionDigits: 6,
  useGrouping: false,
});

function suffixOf(signalKey: string): string {
  const i = signalKey.indexOf(".");
  return i >= 0 ? signalKey.slice(i + 1) : signalKey;
}

function isGatewayOnline(gw: Gateway | undefined): boolean {
  if (!gw || !gw.is_active || !gw.last_seen_at) return false;
  return (Date.now() - new Date(gw.last_seen_at).getTime()) / 1000 < GATEWAY_LIVE_SEC;
}

function fmt(
  value: number | null,
  dataType: SignalDataType | string | undefined | null,
  unit?: string | null
): string {
  if (value === null || value === undefined) return "—";
  if (dataType === "binary") return value ? "AKTİF" : "PASİF";
  if (!Number.isFinite(value)) return String(value);
  const text = dataType === "counter" ? Math.round(value).toString() : NUMBER_FORMATTER.format(value);
  return unit ? `${text} ${unit}` : text;
}

type Row = SignalLiveRow & { effQuality: string | null; effType?: string | null };

export function DeviceDetailPage({
  deviceId,
  devices,
  values,
  signals,
  gateways,
  topologyInfo,
  canCommand = false,
  canConfig = false,
  onDeviceCommand,
  token,
}: Props) {
  const { t } = useTranslation();
  const [activeTab, setActiveTab] = useState<TabKey>("overview");
  const [activeSource, setActiveSource] = useState<SignalSource>("master");
  const [busyReset, setBusyReset] = useState(false);

  const device = useMemo(() => devices.find((d) => d.id === deviceId), [devices, deviceId]);

  const dataTypeByKey = useMemo(() => {
    const m = new Map<string, SignalDataType>();
    for (const s of signals) m.set(s.key, s.data_type);
    return m;
  }, [signals]);

  const gwOnline = useMemo(() => {
    if (!device?.gatewayCode) return true;
    return isGatewayOnline(gateways.find((g) => g.code === device.gatewayCode));
  }, [gateways, device]);

  // Secili kaynaga gore satirlar.
  const rows = useMemo<Row[]>(() => {
    if (!device) return [];
    return values
      .filter((r) => r.device_id === device.id && r.source === activeSource)
      .map((r) => ({
        ...r,
        effQuality: gwOnline ? r.quality : "bad",
        effType: (r.data_type as string | undefined) ?? dataTypeByKey.get(r.signal_key),
      }));
  }, [values, device, activeSource, gwOnline, dataTypeByKey]);

  const rowBySuffix = useMemo(() => {
    const m = new Map<string, Row>();
    for (const r of rows) m.set(suffixOf(r.signal_key), r);
    return m;
  }, [rows]);

  // Tum kaynaklardan key->deger (sidebar RSSI + KPI icin).
  const valueByKey = useMemo(() => {
    const m = new Map<string, SignalLiveRow>();
    if (device) for (const r of values) if (r.device_id === device.id) m.set(r.signal_key, r);
    return m;
  }, [values, device]);

  const numVal = (key: string): number | undefined => {
    const v = valueByKey.get(key)?.value;
    return v == null ? undefined : v;
  };

  const sourceCounts = useMemo<Record<SignalSource, number>>(() => {
    const c: Record<SignalSource, number> = { master: 0, sat01: 0, sat02: 0 };
    if (device) {
      for (const r of values) {
        if (r.device_id === device.id && (r.source === "master" || r.source === "sat01" || r.source === "sat02")) {
          c[r.source] += 1;
        }
      }
    }
    return c;
  }, [values, device]);

  if (!device) {
    return (
      <div className="device-detail-empty">
        <span className="material-symbols-outlined">search_off</span>
        <p className="helper-text">{t("deviceDetail.notFound")}</p>
      </div>
    );
  }

  const runResetAlarm = async () => {
    if (!onDeviceCommand) return;
    const label = signals.find((s) => s.key === "master.reset_all_fcis")?.label ?? "reset_all_fcis";
    setBusyReset(true);
    try {
      await onDeviceCommand(device.code, "reset_all_fcis", label);
    } finally {
      setBusyReset(false);
    }
  };
  const hasResetCmd = signals.some(
    (s) => s.key === "master.reset_all_fcis" && s.data_type === "binary_output" && s.is_active
  );

  const permCount = rowBySuffix.get(CNT_PERMANENT)?.value ?? numVal("master.permanent_fault_counter");
  const momCount = rowBySuffix.get(CNT_MOMENTARY)?.value ?? numVal("master.momentary_fault_counter");
  const curNow = numVal(`${activeSource}.actual_current`);
  const voltNow = numVal(`${activeSource}.actual_voltage`);
  const tempNow = numVal(`${activeSource}.device_temperature`) ?? numVal(`${activeSource}.conductor_temperature`);

  const tabs: { key: TabKey; icon: string; show: boolean }[] = [
    { key: "overview", icon: "dashboard", show: true },
    { key: "trends", icon: "show_chart", show: true },
    { key: "events", icon: "history", show: true },
    { key: "commands", icon: "terminal", show: canCommand },
    { key: "config", icon: "tune", show: canConfig },
  ];

  return (
    <div className="device-detail-shell">
      <DeviceSidebar
        device={device}
        topologyInfo={topologyInfo}
        rssi={numVal("master.modem_rssi")}
        activeSource={activeSource}
        onSourceChange={setActiveSource}
        sourceCounts={sourceCounts}
        canCommand={canCommand && hasResetCmd}
        onResetAlarm={onDeviceCommand ? runResetAlarm : undefined}
        resetBusy={busyReset}
        onOtherActions={canCommand ? () => setActiveTab("commands") : undefined}
      />

      <div className="device-detail-main">
        <nav className="device-detail-tabs" role="tablist">
          {tabs.filter((tb) => tb.show).map((tb) => (
            <button
              key={tb.key}
              type="button"
              role="tab"
              aria-selected={activeTab === tb.key}
              className={`device-detail-tab${activeTab === tb.key ? " active" : ""}`}
              onClick={() => setActiveTab(tb.key)}
            >
              <span className="material-symbols-outlined">{tb.icon}</span>
              {t(`deviceDetail.tabs.${tb.key}`)}
            </button>
          ))}
        </nav>

        {activeTab === "overview" ? (
          <OverviewTab
            token={token ?? ""}
            device={device}
            activeSource={activeSource}
            rowBySuffix={rowBySuffix}
            curNow={curNow}
            voltNow={voltNow}
            tempNow={tempNow}
            permCount={permCount}
            momCount={momCount}
            onViewAllEvents={() => setActiveTab("events")}
            t={t}
          />
        ) : null}

        {activeTab === "trends" && token ? (
          <div className="device-detail-panel">
            <DeviceChartsPanel
              deviceCode={device.code}
              activeSource={activeSource}
              signals={signals}
              token={token}
            />
          </div>
        ) : null}

        {activeTab === "events" && token ? (
          <div className="device-detail-panel">
            <h3 className="device-panel-title">{t("deviceDetail.overview.recentEvents")}</h3>
            <DeviceEventsTable token={token} deviceCode={device.code} />
          </div>
        ) : null}

        {activeTab === "commands" && canCommand && onDeviceCommand && token ? (
          <DeviceCommandsPanel
            deviceCode={device.code}
            signals={signals}
            canCommand={canCommand}
            canConfig={canConfig}
            onDeviceCommand={onDeviceCommand}
            token={token}
          />
        ) : null}

        {activeTab === "config" && canConfig ? (
          <DeviceConfigPanel device={device} />
        ) : null}
      </div>
    </div>
  );
}

// ============================ Genel Bakis sekmesi ============================

function OverviewTab({
  token,
  device,
  activeSource,
  rowBySuffix,
  curNow,
  voltNow,
  tempNow,
  permCount,
  momCount,
  onViewAllEvents,
  t,
}: {
  token: string;
  device: DeviceRow;
  activeSource: SignalSource;
  rowBySuffix: Map<string, Row>;
  curNow?: number;
  voltNow?: number;
  tempNow?: number;
  permCount?: number;
  momCount?: number;
  onViewAllEvents: () => void;
  t: (key: string, opts?: Record<string, unknown>) => string;
}) {
  return (
    <div className="device-overview">
      {/* KPI serit */}
      <div className="device-overview-kpis">
        <KpiCard icon="bolt" tone="amber" label={t("deviceDetail.kpi.current")} value={fmt(curNow ?? null, "analog", "mA")}>
          {token ? (
            <Sparkline token={token} deviceCode={device.code} signalKey={`${activeSource}.actual_current`} />
          ) : null}
        </KpiCard>
        <KpiCard icon="electric_bolt" tone="blue" label={t("deviceDetail.kpi.voltage")} value={fmt(voltNow ?? null, "analog", "V")} />
        <KpiCard icon="device_thermostat" tone="rose" label={t("deviceDetail.kpi.temperature")} value={fmt(tempNow ?? null, "analog", "°C")} />
        <KpiCard icon="report" tone="red" label={t("deviceDetail.permanentFaults")} value={fmt(permCount ?? null, "counter")} />
        <KpiCard icon="flash_on" tone="orange" label={t("deviceDetail.momentaryFaults")} value={fmt(momCount ?? null, "counter")} />
      </div>

      {/* 2 kolon */}
      <div className="device-overview-grid">
        <div className="device-overview-col">
          <section className="device-card">
            <h3 className="device-card-title">{t("deviceDetail.overview.currentStatus")}</h3>
            <StatusTable rowBySuffix={rowBySuffix} t={t} />
          </section>
          <section className="device-card">
            <h3 className="device-card-title">{t("deviceDetail.direction.title")}</h3>
            <DirectionDiagram rowBySuffix={rowBySuffix} deviceCode={device.code} t={t} />
          </section>
        </div>

        <div className="device-overview-col">
          <section className="device-card is-events">
            <h3 className="device-card-title">{t("deviceDetail.overview.recentEvents")}</h3>
            {token ? (
              <DeviceEventsTable token={token} deviceCode={device.code} limit={5} onViewAll={onViewAllEvents} />
            ) : null}
          </section>
        </div>
      </div>
    </div>
  );
}

function KpiCard({
  icon,
  tone,
  label,
  value,
  children,
}: {
  icon: string;
  tone: string;
  label: string;
  value: string;
  children?: ReactNode;
}) {
  return (
    <div className={`device-kpi tone-${tone}`}>
      <div className="device-kpi-head">
        <span className="device-kpi-label">{label}</span>
        <span className="device-kpi-icon material-symbols-outlined">{icon}</span>
      </div>
      <div className="device-kpi-value">{value}</div>
      {children ? <div className="device-kpi-spark">{children}</div> : null}
    </div>
  );
}

// Mevcut Durum tablosu — status kategorisindeki binary sinyaller -> sinyal|durum.
function StatusTable({
  rowBySuffix,
  t,
}: {
  rowBySuffix: Map<string, Row>;
  t: (key: string, opts?: Record<string, unknown>) => string;
}) {
  const statusDefs = SIGNALS.filter((s) => s.cat === "status");
  return (
    <table className="device-status-table">
      <thead>
        <tr>
          <th>{t("deviceDetail.overview.signal")}</th>
          <th>{t("deviceDetail.overview.state")}</th>
        </tr>
      </thead>
      <tbody>
        {statusDefs.map((def) => {
          const row = rowBySuffix.get(def.suffix);
          const active = row?.value === 1;
          const icon = STATUS_ICONS[def.suffix] ?? "circle";
          return (
            <tr key={def.suffix}>
              <td className="device-status-name">
                <span className="material-symbols-outlined">{icon}</span>
                {def.label}
              </td>
              <td>
                <span className={`device-status-badge ${active ? "is-active" : "is-normal"}`}>
                  <span className="material-symbols-outlined">
                    {active ? "warning" : "check_circle"}
                  </span>
                  {active ? t("deviceDetail.status.active") : t("deviceDetail.status.normal")}
                </span>
              </td>
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}

// Ariza yonu: tek gorsel akis diyagrami (A—cihaz—B). Aktif yon renkli.
const DIRECTION_ROWS: { key: string; label: string; aSuffix: string; bSuffix: string }[] = [
  { key: "load_flow", label: "Yük Akışı", aSuffix: "load_flow_direction_green_a", bSuffix: "load_flow_direction_red_b" },
  { key: "overcurrent", label: "Aşırı Akım", aSuffix: "overcurrent_fault_direction_green_a", bSuffix: "overcurrent_fault_direction_red_b" },
  { key: "delta", label: "ΔI/Δt", aSuffix: "delta_i_delta_t_fault_direction_green_a", bSuffix: "delta_i_delta_t_fault_direction_red_b" },
];

function DirectionDiagram({
  rowBySuffix,
  deviceCode,
  t,
}: {
  rowBySuffix: Map<string, Row>;
  deviceCode: string;
  t: (key: string, opts?: Record<string, unknown>) => string;
}) {
  // Herhangi bir yon aktif mi (genel arıza yönü)?
  const anyActive = DIRECTION_ROWS.some(
    (d) => rowBySuffix.get(d.aSuffix)?.value === 1 || rowBySuffix.get(d.bSuffix)?.value === 1
  );
  // Ilk aktif yonu bul (gorsel A/B vurgusu icin).
  const firstActive = DIRECTION_ROWS.find(
    (d) => rowBySuffix.get(d.aSuffix)?.value === 1 || rowBySuffix.get(d.bSuffix)?.value === 1
  );
  const state = firstActive
    ? rowBySuffix.get(firstActive.aSuffix)?.value === 1
      ? "a"
      : "b"
    : "none";

  return (
    <div className="device-flow">
      <div className={`device-flow-diagram is-${state}`}>
        <span className={`device-flow-node node-a${state === "a" ? " active" : ""}`}>A</span>
        <span className="device-flow-track" aria-hidden="true">
          <span className="device-flow-line" />
          <span className="device-flow-chip">{deviceCode}</span>
          <span className="device-flow-line" />
        </span>
        <span className={`device-flow-node node-b${state === "b" ? " active" : ""}`}>B</span>
      </div>
      <p className="device-flow-caption">
        {anyActive
          ? state === "a"
            ? t("deviceDetail.direction.toA")
            : t("deviceDetail.direction.toB")
          : t("deviceDetail.direction.none")}
      </p>
      <p className="device-flow-note">{t("deviceDetail.direction.note")}</p>
    </div>
  );
}
