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

import { useCallback, useEffect, useMemo, useState, type ReactNode } from "react";
import { useTranslation } from "react-i18next";

import { fetchAlarmEvents } from "../../shared/api";
import { SOURCES } from "../signals/signalCatalogConstants";
import { PoleMasterTab } from "./PoleMasterTab";
import { signalLabel } from "../../shared/signalLabel";
import { signalTrust } from "../../shared/signalQuality";
import type { AlarmEvent } from "../../shared/types";

import type {
  DeviceRow,
  Gateway,
  SignalCatalogRow,
  SignalDataType,
  SignalLiveRow,
  SignalSource,
} from "../../shared/types";
import { DeviceAlarmsCard } from "./DeviceAlarmsCard";
import { DeviceAllSignalsTab } from "./DeviceAllSignalsTab";
import { DeviceCommandsPanel } from "./DeviceCommandsPanel";
import { DeviceChartsPanel } from "./DeviceChartsPanel";
import { DeviceConfigPanel } from "./DeviceConfigPanel";
import { DeviceEventsTable } from "./DeviceEventsTable";
import { DeviceSidebar } from "./DeviceSidebar";
import { Sparkline } from "./Sparkline";

type TopologyInfo =
  | { regionName: string; lineName: string; latitude?: number; longitude?: number }
  | undefined;

type TabKey = "overview" | "all" | "poleMaster" | "trends" | "events" | "commands" | "config";

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

// ---- Mevcut Durum: sinyalleri kategori gruplarina ayir (suffix pattern) ----
// Kategori sirasi = gorunum sirasi. Her sinyal bir gruba duser; bilgi/komut
// sinyalleri (IP/serial/firmware, binary_output) HARIC (baska sekmelerde).
type GroupKey = "protection" | "measure" | "status" | "counter";

const GROUP_ORDER: { key: GroupKey; icon: string }[] = [
  { key: "protection", icon: "shield" },
  { key: "measure", icon: "monitoring" },
  { key: "status", icon: "toggle_on" },
  { key: "counter", icon: "pin" },
];

// Bilgi/altyapi sinyalleri Mevcut Durum'da GOSTERILMEZ (sidebar/Tumu'de).
// "info_" ile baslayan tum sinyaller bilgi kabul edilir (NWS/RF/modem vb dahil).
const INFO_SUFFIX_RE =
  /^info_|(serial_number|ipv4_address|ip_address|firmware|fw_version|modem|imei|sim_serial|gps|latitude|longitude|hardware_revision|part_no|rtu_status|network|operation_mode|device_position|test_point_level|comm_library|dial_in)/;

function groupOfSuffix(suffix: string, dataType: string | undefined): GroupKey {
  const s = suffix.toLowerCase();
  // Koruma / ariza yonu / trip / alarm esikleri + ariza olcum degerleri.
  // "_alarm" iceren tum sinyaller (conductor_temperature_alarm vb) koruma.
  if (
    /(overcurrent|delta_i_delta_t|fault_direction|load_flow|_tripped|voltage_loss|current_loss|tamper|pick_up|_alarm|permanent_fault$|momentary_fault$|fault_current|fault_duration|last_good|minimum_current|minimum_voltage|maximum_current|maximum_voltage|trip_level)/.test(
      s
    )
  ) {
    return "protection";
  }
  // Sayaclar
  if (dataType === "counter" || /_counter$/.test(s)) return "counter";
  // Olcumler (analog)
  if (dataType === "analog" || /(current|voltage|temperature|phase_angle|pitch_angle|nominal)/.test(s)) {
    return "measure";
  }
  // Kalan binary/durum
  return "status";
}

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
  const [deviceAlarms, setDeviceAlarms] = useState<AlarmEvent[]>([]);

  const device = useMemo(() => devices.find((d) => d.id === deviceId), [devices, deviceId]);

  // FIZIKSEL KIT KAYDI (varsa). Pole Master Kit'in kit seviyesindeki
  // olcumleri — modem, GPS, sebeke, solar/AC besleme, cihaz sicakligi —
  // burada durur ve "Pole Master" sekmesinde gosterilir. Telemetri
  // COGALTILMAZ (bkz. PoleMasterTab docstring'i), okuma tarafinda devralinir.
  const parentDevice = useMemo(
    () =>
      device?.parentDeviceId != null
        ? devices.find((d) => d.id === device.parentDeviceId)
        : undefined,
    [devices, device]
  );

  // Bir Pole Master Kit setinde olcum yapan uc unitenin ucu de uydudur;
  // kitin `master`i ortak RTU'dur. Bu yuzden set acildiginda varsayilan
  // kanal `sat01` olmali — `master` secili kalsaydi kullanici bos bir
  // kanal gorurdu (sette `master.*` telemetrisi YOK).
  useEffect(() => {
    if (device?.parentDeviceId != null) {
      setActiveSource((prev) => (prev === "master" ? "sat01" : prev));
    }
  }, [device?.parentDeviceId]);

  // Bu cihazin alarmlari — hem sidebar durum karti hem Alarmlar karti kullanir
  // (tek fetch). Aktif alarm = reset EDILMEMIS (giderilmemis) alarm.
  const loadAlarms = useCallback(async () => {
    if (!token || !device) return;
    try {
      const all = await fetchAlarmEvents(token).catch(() => [] as AlarmEvent[]);
      setDeviceAlarms(all.filter((a) => a.device_id === device.id));
    } catch {
      // sessiz
    }
  }, [token, device]);

  useEffect(() => {
    void loadAlarms();
  }, [loadAlarms]);

  const hasActiveAlarm = useMemo(
    () => deviceAlarms.some((a) => !a.reset),
    [deviceAlarms]
  );

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

  // Katalog birimleri (kullanici Sinyaller sayfasindan degistirebilir).
  const unitByKey = useMemo(() => {
    const m = new Map<string, string>();
    for (const s of signals) if (s.unit) m.set(s.key, s.unit);
    return m;
  }, [signals]);

  const numVal = (key: string): number | undefined => {
    const v = valueByKey.get(key)?.value;
    return v == null ? undefined : v;
  };
  const strVal = (key: string): string | undefined => {
    const s = (valueByKey.get(key)?.value_string ?? "").trim();
    return s.length > 0 ? s : undefined;
  };
  // Birim: canli deger unit'i, yoksa katalog unit'i (sabit "mA" DEGIL).
  const unitOf = (key: string): string | undefined =>
    valueByKey.get(key)?.unit || unitByKey.get(key) || undefined;

  // Sidebar: master IP + kanal (master/sat01/sat02) seri no'lari.
  // IP: G110 string (info_ipv4_address). Serial: analog (group 30) -> sayi,
  //   value_string DEGIL value; string variant (info_serial_number) fallback.
  const sidebarIp = strVal("master.info_ipv4_address") ?? strVal("master.info_modem_ip_address");
  const sidebarPartNo = strVal("master.info_part_no");
  // Firmware: cihaz ham deger olarak 2338 gonderir, gercek surum "2.338".
  // Ham deger >= 1000 ise X.YYY formatina cevir (2338 -> 2.338); string
  // variant (info_fw_version) varsa onu tercih et.
  const fwStr = strVal("master.info_fw_version");
  const fwNum = numVal("master.firmware_version");
  const fmtFirmware = (n: number): string => {
    if (n >= 1000) return (n / 1000).toFixed(3); // 2338 -> "2.338"
    return String(n);
  };
  const sidebarFirmware =
    fwStr ?? (fwNum != null && Number.isFinite(fwNum) ? fmtFirmware(fwNum) : undefined);
  /** Bu cihazda OLCUM YAPAN uniteler.
   *
   *  SN 2.0'da ucuncu unite `master`dir; Pole Master Kit setinde ucu de
   *  uydudur (sat01/sat02/sat03) ve o kayitta `master.*` telemetrisi HIC
   *  yoktur. Sabit uclu kullanmak, sette bos bir "Master" kanali gosterip
   *  gercek ucuncu uniteyi (Satellite 03) tamamen gizliyordu — pil rozeti,
   *  seri no ve "Tumu" karti dahil.
   */
  const measuringSources = useMemo<SignalSource[]>(
    () =>
      (device?.parentDeviceId ?? null) !== null
        ? ["sat01", "sat02", "sat03"]
        : ["master", "sat01", "sat02"],
    [device]
  );

  const serialOf = (src: SignalSource): string | undefined => {
    const n = numVal(`${src}.serial_number`);
    if (n != null && Number.isFinite(n) && n > 0) return String(Math.round(n));
    return strVal(`${src}.info_serial_number`);
  };
  const channelSerials = useMemo<Partial<Record<SignalSource, string>>>(() => {
    const out: Partial<Record<SignalSource, string>> = {};
    for (const src of measuringSources) {
      const seri = serialOf(src);
      if (seri) out[src] = seri;
    }
    return out;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [valueByKey, measuringSources]);

  // Kanal pil yuzdeleri. Master: device.batteryPercent (hesaplanmis). Satellite:
  // battery_voltage_satellite voltajindan basit Li-ion oran (3.2V=%0, 4.2V=%100).
  // ponytail: sabit Li-ion araligi; cihaz bazli esik gerekirse settings'e bagla.
  const voltToPct = (v: number | undefined): number | undefined => {
    if (v == null || !Number.isFinite(v)) return undefined;
    const pct = ((v - 3.2) / (4.2 - 3.2)) * 100;
    return Math.max(0, Math.min(100, Math.round(pct)));
  };
  const channelBattery = useMemo<Partial<Record<SignalSource, number>>>(() => {
    const out: Partial<Record<SignalSource, number>> = {};
    for (const src of measuringSources) {
      // `master` kanalinin pili cihaz kaydindan gelir (hesaplanmis yuzde);
      // uydularinki kendi batarya gerilimlerinden turetilir. Kit setinde
      // `master` bir olcum unitesi DEGIL, o yuzden listede de yok.
      if (src === "master") {
        if (device && Number.isFinite(device.batteryPercent)) out.master = device.batteryPercent;
        continue;
      }
      const pct = voltToPct(numVal(`${src}.battery_voltage_satellite`));
      if (pct != null) out[src] = pct;
    }
    return out;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [valueByKey, device, measuringSources]);

  const sourceCounts = useMemo<Record<SignalSource, number>>(() => {
    const c = Object.fromEntries(
      SOURCES.map((s) => [s, 0])
    ) as Record<SignalSource, number>;
    if (device) {
      for (const r of values) {
        if (r.device_id === device.id && r.source in c) {
          c[r.source as SignalSource] += 1;
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
    { key: "all", icon: "table_rows", show: true },
    // KIT SEVIYESI: yalnizca bir Pole Master Kit setinde gorunur. Kitin
    // modem/GPS/besleme olcumleri uc setin ORTAK varligidir; setin kendi
    // sinyalleriyle ayni listede gostermek hangi degerin nereye ait
    // oldugunu karistirirdi.
    { key: "poleMaster", icon: "solar_power", show: parentDevice != null },
    { key: "trends", icon: "show_chart", show: true },
    { key: "events", icon: "history", show: true },
    { key: "commands", icon: "terminal", show: canCommand },
    { key: "config", icon: "tune", show: canConfig },
  ];

  const showReset = canCommand && hasResetCmd && onDeviceCommand;

  return (
    <div className="device-detail-shell">
      <DeviceSidebar
        device={device}
        topologyInfo={topologyInfo}
        rssi={numVal("master.modem_rssi")}
        ip={sidebarIp}
        partNo={sidebarPartNo}
        firmware={sidebarFirmware}
        hasAlarm={hasActiveAlarm}
        channelSerials={channelSerials}
        channelBattery={channelBattery}
        activeSource={activeSource}
        onSourceChange={setActiveSource}
        sourceCounts={sourceCounts}
      />

      <div className="device-detail-main">
        <div className="device-detail-topbar">
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
          <div className="device-detail-topbar-actions">
            {device.alarmActive ? (
              <span className="device-alarm-badge">
                <span className="device-alarm-pulse" aria-hidden="true" />
                {t("deviceDetail.alarmActive")}
              </span>
            ) : null}
            {showReset ? (
              <button
                type="button"
                className="device-reset-btn"
                onClick={() => void runResetAlarm()}
                disabled={busyReset}
                aria-busy={busyReset}
              >
                {busyReset ? (
                  <span className="btn-spinner" aria-hidden="true" />
                ) : (
                  <span className="material-symbols-outlined">restart_alt</span>
                )}
                {t("deviceDetail.quick.reset")}
              </button>
            ) : null}
          </div>
        </div>

        {activeTab === "overview" ? (
          <OverviewTab
            token={token ?? ""}
            device={device}
            activeSource={activeSource}
            rowBySuffix={rowBySuffix}
            allRows={rows}
            deviceAlarms={deviceAlarms}
            curNow={curNow}
            voltNow={voltNow}
            tempNow={tempNow}
            curUnit={unitOf(`${activeSource}.actual_current`)}
            voltUnit={unitOf(`${activeSource}.actual_voltage`)}
            tempUnit={unitOf(`${activeSource}.device_temperature`) ?? unitOf(`${activeSource}.conductor_temperature`)}
            permCount={permCount}
            momCount={momCount}
            onViewAllEvents={() => setActiveTab("events")}
            t={t}
          />
        ) : null}

        {activeTab === "all" ? (
          <DeviceAllSignalsTab
            device={device}
            values={values}
            gwOnline={gwOnline}
            sourceCounts={sourceCounts}
            sources={measuringSources}
          />
        ) : null}

        {activeTab === "poleMaster" && parentDevice ? (
          <PoleMasterTab parent={parentDevice} values={values} />
        ) : null}

        {activeTab === "trends" && token ? (
          <div className="device-trend-panel">
            <DeviceChartsPanel
              deviceCode={device.code}
              activeSource={activeSource}
              signals={signals}
              token={token}
            />
          </div>
        ) : null}

        {activeTab === "events" && token ? (
          <div className="device-events-fullpanel">
            <section className="device-card is-events-full">
              <h3 className="device-card-title">{t("deviceDetail.overview.recentEvents")}</h3>
              <DeviceEventsTable token={token} deviceCode={device.code} variant="full" />
            </section>
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
          <DeviceConfigPanel
            device={device}
            token={token ?? ""}
            canConfig={canConfig}
            canCommand={canCommand}
          />
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
  allRows,
  deviceAlarms,
  curNow,
  voltNow,
  tempNow,
  curUnit,
  voltUnit,
  tempUnit,
  permCount,
  momCount,
  onViewAllEvents,
  t,
}: {
  token: string;
  device: DeviceRow;
  activeSource: SignalSource;
  rowBySuffix: Map<string, Row>;
  allRows: Row[];
  deviceAlarms: AlarmEvent[];
  curNow?: number;
  voltNow?: number;
  tempNow?: number;
  curUnit?: string;
  voltUnit?: string;
  tempUnit?: string;
  permCount?: number;
  momCount?: number;
  onViewAllEvents: () => void;
  t: (key: string, opts?: Record<string, unknown>) => string;
}) {
  // Canli deger yoksa (cihaz offline) historian son degeri (sparkline'dan) fallback.
  const [lastCur, setLastCur] = useState<number | null>(null);
  const [lastVolt, setLastVolt] = useState<number | null>(null);
  const [lastTemp, setLastTemp] = useState<number | null>(null);
  const curVal = curNow ?? lastCur ?? null;
  const voltVal = voltNow ?? lastVolt ?? null;
  const tempVal = tempNow ?? lastTemp ?? null;
  const stale = (live: number | undefined, fb: number | null) => live == null && fb != null;
  const srcLabel = activeSource === "master" ? "Master" : activeSource === "sat01" ? "Satellite 01" : "Satellite 02";

  return (
    <div className="device-overview">
      <div className="device-overview-srchint">
        <span className="material-symbols-outlined">tune</span>
        {t("deviceDetail.overview.sourceHint", { source: srcLabel })}
      </div>
      {/* KPI serit */}
      <div className="device-overview-kpis">
        <KpiCard icon="bolt" tone="amber" label={t("deviceDetail.kpi.current")} value={fmt(curVal, "analog", curUnit)} stale={stale(curNow, lastCur)}>
          {token ? (
            <Sparkline token={token} deviceCode={device.code} signalKey={`${activeSource}.actual_current`} color="#f59e0b" onLastValue={setLastCur} />
          ) : null}
        </KpiCard>
        <KpiCard icon="electric_bolt" tone="blue" label={t("deviceDetail.kpi.voltage")} value={fmt(voltVal, "analog", voltUnit)} stale={stale(voltNow, lastVolt)}>
          {token ? (
            <Sparkline token={token} deviceCode={device.code} signalKey={`${activeSource}.actual_voltage`} color="#3b82f6" onLastValue={setLastVolt} />
          ) : null}
        </KpiCard>
        <KpiCard icon="device_thermostat" tone="rose" label={t("deviceDetail.kpi.temperature")} value={fmt(tempVal, "analog", tempUnit)} stale={stale(tempNow, lastTemp)}>
          {token ? (
            <Sparkline token={token} deviceCode={device.code} signalKey={`${activeSource}.device_temperature`} color="#f43f5e" onLastValue={setLastTemp} />
          ) : null}
        </KpiCard>
        <KpiCard icon="report" tone="red" label={t("deviceDetail.permanentFaults")} value={fmt(permCount ?? null, "counter")} />
        <KpiCard icon="flash_on" tone="orange" label={t("deviceDetail.momentaryFaults")} value={fmt(momCount ?? null, "counter")} />
      </div>

      {/* 2 kolon: sol Mevcut Durum (2 kolonlu), sag Son Olaylar */}
      <div className="device-overview-grid">
        <div className="device-overview-col">
          <section className="device-card">
            <h3 className="device-card-title">{t("deviceDetail.overview.currentStatus")}</h3>
            <StatusTable rows={allRows} rowBySuffix={rowBySuffix} t={t} />
          </section>
        </div>

        <div className="device-overview-col">
          {/* Alarmlar — sadece bu cihaz + aktif kaynak */}
          <section className="device-card">
            <h3 className="device-card-title">
              <span className="material-symbols-outlined device-card-title-icon">notifications_active</span>
              {t("deviceDetail.alarms.title", { source: srcLabel })}
            </h3>
            <DeviceAlarmsCard alarms={deviceAlarms} activeSource={activeSource} />

          </section>
          {/* Son Olaylar */}
          <section className="device-card is-events">
            <div className="device-card-titlerow">
              <h3 className="device-card-title">{t("deviceDetail.overview.recentEvents")}</h3>
              <button type="button" className="device-card-viewall" onClick={onViewAllEvents}>
                {t("deviceDetail.overview.viewAllEvents")}
                <span className="material-symbols-outlined">chevron_right</span>
              </button>
            </div>
            {token ? (
              <DeviceEventsTable token={token} deviceCode={device.code} limit={6} />
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
  stale,
  children,
}: {
  icon: string;
  tone: string;
  label: string;
  value: string;
  stale?: boolean;
  children?: ReactNode;
}) {
  return (
    <div className={`device-kpi tone-${tone}`}>
      <div className="device-kpi-head">
        <span className="device-kpi-label">{label}</span>
        <span className="device-kpi-icon material-symbols-outlined">{icon}</span>
      </div>
      <div className={`device-kpi-value${stale ? " is-stale" : ""}`} title={stale ? "son bilinen deger" : undefined}>
        {value}
      </div>
      {children ? <div className="device-kpi-spark">{children}</div> : null}
    </div>
  );
}

// Mevcut Durum — aktif kaynagin TUM sinyalleri, kategori gruplu.
// binary/binary_output -> durum rozeti (Aktif/Normal); analog/counter -> deger.
function StatusItem({
  row,
  t,
}: {
  row: Row;
  t: (key: string, opts?: Record<string, unknown>) => string;
}) {
  const suffix = suffixOf(row.signal_key);
  // Katalog adi Ingilizce girildigi icin ceviri SONEK uzerinden yapilir;
  // sozlukte olmayan sinyal katalog adiyla gorunmeye devam eder.
  const label = signalLabel(row.signal_key, row.signal_label);
  const dt = (row.effType as string | undefined) ?? (row.data_type as string | undefined);
  const isBinary = dt === "binary" || dt === "binary_output";

  if (isBinary) {
    const icon = STATUS_ICONS[suffix] ?? "toggle_on";

    // UC DURUM — eskiden yalnizca `value === 1` bakiliyordu ve "veri yok" ile
    // "gercekten normal" AYNI yesil rozeti uretiyordu.
    //
    // En tehlikelisi ucuncu hal: haberlesmesi kopan cihaz icin gateway
    // `comm_lost` kalitesiyle 0.0 basiyor. Backend bu okumayi alarm
    // degerlendirmesine SOKMUYOR; arayuz ise onu "taze" sanip yesil
    // gosterip sunucunun kararini gecersiz kiliyordu.
    const trust = signalTrust(row.value, row.effQuality, true);
    if (trust !== "trusted") {
      return (
        <div className="device-status-item">
          <span className="device-status-name" title={row.signal_key}>
            <span className="material-symbols-outlined">{icon}</span>
            {label}
          </span>
          <span className="device-status-badge is-unknown">
            <span className="material-symbols-outlined">help</span>
            {trust === "missing"
              ? t("deviceDetail.status.noData")
              : t("deviceDetail.status.untrusted")}
          </span>
        </div>
      );
    }

    const active = row.value === 1;
    return (
      <div className="device-status-item">
        <span className="device-status-name" title={row.signal_key}>
          <span className="material-symbols-outlined">{icon}</span>
          {label}
        </span>
        <span className={`device-status-badge ${active ? "is-active" : "is-normal"}`}>
          <span className="material-symbols-outlined">{active ? "warning" : "check_circle"}</span>
          {active ? t("deviceDetail.status.active") : t("deviceDetail.status.normal")}
        </span>
      </div>
    );
  }
  // analog / counter -> deger
  const val =
    dt === "string"
      ? (row.value_string ?? "").trim() || "—"
      : fmt(row.value ?? null, dt, row.unit);
  return (
    <div className="device-status-item is-value">
      <span className="device-status-name" title={row.signal_key}>
        {label}
      </span>
      <span className="device-status-itemval">{val}</span>
    </div>
  );
}

function StatusTable({
  rows,
  t,
}: {
  rows: Row[];
  rowBySuffix: Map<string, Row>;
  t: (key: string, opts?: Record<string, unknown>) => string;
}) {
  // Sinyalleri gruplara ayir (bilgi/altyapi HARIC). Grup ici: binary'ler once,
  // sonra label'a gore.
  const byGroup = useMemo(() => {
    const m = new Map<GroupKey, Row[]>();
    for (const g of GROUP_ORDER) m.set(g.key, []);
    for (const r of rows) {
      const suffix = suffixOf(r.signal_key);
      if (INFO_SUFFIX_RE.test(suffix)) continue; // IP/serial/firmware -> sidebar/Tumu
      const dt = (r.effType as string | undefined) ?? (r.data_type as string | undefined);
      if (dt === "binary_output") continue; // komut -> Komutlar sekmesi
      const g = groupOfSuffix(suffix, dt);
      m.get(g)?.push(r);
    }
    // Grup ici siralama: once binary (durum rozetli), sonra analog/counter
    // (deger). Her tip kendi icinde label'a gore — analoglar EN ALTTA toplansin.
    const isBinaryRow = (r: Row): boolean => {
      const dt = (r.effType as string | undefined) ?? (r.data_type as string | undefined);
      return dt === "binary" || dt === "binary_output";
    };
    for (const arr of m.values()) {
      arr.sort((a, b) => {
        const ba = isBinaryRow(a) ? 0 : 1;
        const bb = isBinaryRow(b) ? 0 : 1;
        if (ba !== bb) return ba - bb; // binary once, analog sonra
        return (a.signal_label || a.signal_key).localeCompare(b.signal_label || b.signal_key);
      });
    }
    return m;
  }, [rows]);

  // Dolu gruplar (bos olanlar tab'da gorunmez).
  const activeGroups = GROUP_ORDER.filter((g) => (byGroup.get(g.key)?.length ?? 0) > 0);
  const [tab, setTab] = useState<GroupKey>(activeGroups[0]?.key ?? "protection");
  // Aktif tab bos kaldiysa (kaynak degisti) ilk dolu gruba dus.
  const curTab = activeGroups.some((g) => g.key === tab) ? tab : activeGroups[0]?.key;

  if (activeGroups.length === 0) {
    return (
      <div className="device-events-empty">
        <span className="material-symbols-outlined">sensors_off</span>
        <p>{t("deviceDetail.noSignals", { source: "" })}</p>
      </div>
    );
  }

  const items = curTab ? byGroup.get(curTab) ?? [] : [];
  // Binary (durum rozetli) ve analog (deger) ayri bloklar — analoglar EN ALTTA
  // toplu (grid akisinda araya karismasin).
  const isBin = (r: Row): boolean => {
    const dt = (r.effType as string | undefined) ?? (r.data_type as string | undefined);
    return dt === "binary" || dt === "binary_output";
  };
  const binItems = items.filter(isBin);
  const valItems = items.filter((r) => !isBin(r));

  return (
    <div className="device-status">
      {/* Alt-sekmeler (kategori) — scroll yerine tab */}
      <div className="device-status-tabs" role="tablist">
        {activeGroups.map((g) => (
          <button
            key={g.key}
            type="button"
            role="tab"
            aria-selected={curTab === g.key}
            className={`device-status-tab${curTab === g.key ? " active" : ""}`}
            onClick={() => setTab(g.key)}
          >
            <span className="material-symbols-outlined">{g.icon}</span>
            {t(`deviceDetail.groups.${g.key}`)}
            <span className="device-status-tab-count">{byGroup.get(g.key)?.length ?? 0}</span>
          </button>
        ))}
      </div>
      {/* Analoglar (deger) USTTE, binary (durum) ALTTA */}
      {valItems.length > 0 ? (
        <div className="device-status-grid is-values">
          {valItems.map((r) => (
            <StatusItem key={r.signal_key} row={r} t={t} />
          ))}
        </div>
      ) : null}
      {binItems.length > 0 ? (
        <div className={`device-status-grid${valItems.length > 0 ? " has-sep" : ""}`}>
          {binItems.map((r) => (
            <StatusItem key={r.signal_key} row={r} t={t} />
          ))}
        </div>
      ) : null}
    </div>
  );
}

