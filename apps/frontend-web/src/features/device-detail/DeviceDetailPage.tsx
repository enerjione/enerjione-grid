/**
 * DeviceDetailPage — cihaz detay sayfasi.
 *
 * Bir Horstmann SN2 FCI cihazinin canli sinyallerini kaynak (Master / Satellite 01 /
 * Satellite 02) sekmeleri altinda, gorseldeki gibi kategorilere gruplu gosterir:
 * Sayaclar, Olcumler, Ariza Olcumleri, Durum, Ariza Yonu, Diger.
 *
 * Veri: signalLiveValues (SignalLiveRow[]) — GET /signals/live + WS. Backend'de
 * hazir; genisletme gerekmez. Filtre: row.device_id === deviceId, kaynak: row.source.
 * Grafikler/telemetri gecmisi AYRI IS (historian).
 */

import { useMemo, useState } from "react";
import { useTranslation } from "react-i18next";

import { locateDevice } from "../../shared/geoLookup";
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

type TopologyInfo = { regionName: string; lineName: string } | undefined;

type TabKey = "measures" | "charts" | "commands" | "config";

type Props = {
  deviceId: number;
  devices: DeviceRow[];
  values: SignalLiveRow[];
  signals: SignalCatalogRow[];
  gateways: Gateway[];
  topologyInfo?: TopologyInfo;
  /** ENGINEER/INSTALLER ise komut sekmesi + genel/alarm reset komutlari acilir. */
  canCommand?: boolean;
  /** INSTALLER ise config sekmesi + config komutlari acilir. */
  canConfig?: boolean;
  /** Komut gonderme handler'i (confirm + toast App.tsx'te merkezi). */
  onDeviceCommand?: (deviceCode: string, command: string, label: string) => Promise<void>;
  /** Historian/komut/config API cagrilari icin session token'i. */
  token?: string;
};

// ---- Kaynak (source) meta ---------------------------------------------------
const SOURCES: { key: SignalSource; label: string; tone: "master" | "green" | "amber" }[] = [
  { key: "master", label: "Master", tone: "master" },
  { key: "sat01", label: "Satellite 01", tone: "green" },
  { key: "sat02", label: "Satellite 02", tone: "amber" },
];

// ---- Kategori tanimi (source-agnostic suffix -> kategori + TR etiket) --------
// signal_key = "{source}.{suffix}" (or "master.actual_current"). Suffix ile eslenir.
type CatKey = "measure" | "fault" | "status" | "direction";

type SigDef = { suffix: string; label: string; cat: CatKey };

const SIGNALS: SigDef[] = [
  // Olcumler
  { suffix: "actual_current", label: "Akım", cat: "measure" },
  { suffix: "actual_voltage", label: "Gerilim", cat: "measure" },
  { suffix: "average_current", label: "Ort. Akım", cat: "measure" },
  { suffix: "maximum_current", label: "Max. Akım", cat: "measure" },
  { suffix: "conductor_temperature", label: "İletken Sıc.", cat: "measure" },
  { suffix: "device_temperature", label: "Cihaz Sıc.", cat: "measure" },
  // Ariza olcumleri
  { suffix: "fault_current", label: "Arıza Akımı", cat: "fault" },
  { suffix: "fault_duration", label: "Arıza Süresi", cat: "fault" },
  { suffix: "last_good_known_current", label: "Son İyi Akım", cat: "fault" },
  { suffix: "minimum_current", label: "Min. Akım", cat: "fault" },
  // Durum (binary)
  { suffix: "overcurrent_tripped", label: "Aşırı akım", cat: "status" },
  { suffix: "delta_i_delta_t_tripped", label: "ΔI/Δt", cat: "status" },
  { suffix: "voltage_loss", label: "Gerilim kaybı", cat: "status" },
  { suffix: "current_loss", label: "Akım kaybı", cat: "status" },
  { suffix: "battery_status", label: "Pil durumu", cat: "status" },
  { suffix: "permanent_fault", label: "Kalıcı arıza", cat: "status" },
  { suffix: "momentary_fault", label: "Geçici arıza", cat: "status" },
  // Ariza yonu (binary A=yesil / B=kirmizi)
  { suffix: "load_flow_direction_green_a", label: "Akış yönü A (yeşil)", cat: "direction" },
  { suffix: "load_flow_direction_red_b", label: "Akış yönü B (kırmızı)", cat: "direction" },
  { suffix: "overcurrent_fault_direction_green_a", label: "Aşırı akım arıza yönü A", cat: "direction" },
  { suffix: "overcurrent_fault_direction_red_b", label: "Aşırı akım arıza yönü B", cat: "direction" },
  { suffix: "delta_i_delta_t_fault_direction_green_a", label: "ΔI/Δt arıza yönü A", cat: "direction" },
  { suffix: "delta_i_delta_t_fault_direction_red_b", label: "ΔI/Δt arıza yönü B", cat: "direction" },
];

// Sayac suffix'leri (ust kutular).
const CNT_PERMANENT = "permanent_fault_counter";
const CNT_MOMENTARY = "momentary_fault_counter";

const SIG_BY_SUFFIX = new Map(SIGNALS.map((s) => [s.suffix, s]));

const CAT_META: Record<CatKey, { title: string; icon: string }> = {
  measure: { title: "ÖLÇÜMLER", icon: "monitoring" },
  fault: { title: "ARIZA ÖLÇÜMLERİ", icon: "warning" },
  status: { title: "DURUM", icon: "flag" },
  direction: { title: "ARIZA YÖNÜ", icon: "explore" },
};
const CAT_ORDER: CatKey[] = ["measure", "fault", "status", "direction"];

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
  unit?: string | null,
  valueString?: string | null
): string {
  if (dataType === "string") {
    const txt = (valueString ?? "").trim();
    return txt.length > 0 ? txt : "—";
  }
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
  const [activeTab, setActiveTab] = useState<TabKey>("measures");
  const [activeSource, setActiveSource] = useState<SignalSource>("master");
  const [showOther, setShowOther] = useState(false);
  const [busyQuick, setBusyQuick] = useState<string | null>(null);

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

  // Bu cihaza ait, secili kaynaga gore satirlar (kalite override + tip fallback).
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

  // Ust ozet icin: cihazin TUM kaynaklardaki (master+sat) sinyalleri, key ile.
  // activeSource'tan bagimsiz — header/ozet bandi master string'leri + satellite
  // seri no'lari gostermeli.
  const valueByKey = useMemo(() => {
    const m = new Map<string, SignalLiveRow>();
    if (!device) return m;
    for (const r of values) {
      if (r.device_id === device.id) m.set(r.signal_key, r);
    }
    return m;
  }, [values, device]);

  // Bir string sinyalin metnini getir (bos/yok ise undefined).
  const strVal = (key: string): string | undefined => {
    const v = valueByKey.get(key)?.value_string?.trim();
    return v && v.length > 0 ? v : undefined;
  };
  // Bir numeric sinyalin degerini getir.
  const numVal = (key: string): number | undefined => {
    const v = valueByKey.get(key)?.value;
    return v == null ? undefined : v;
  };

  // Kategori -> gorsel row'lar (curated). Diger = curated'de olmayan + string haric.
  const grouped = useMemo(() => {
    const g: Record<CatKey, { def: SigDef; row: Row | undefined }[]> = {
      measure: [], fault: [], status: [], direction: [],
    };
    for (const def of SIGNALS) {
      g[def.cat].push({ def, row: rowBySuffix.get(def.suffix) });
    }
    const curated = new Set(SIGNALS.map((s) => s.suffix));
    curated.add(CNT_PERMANENT);
    curated.add(CNT_MOMENTARY);
    const other = rows.filter(
      (r) => !curated.has(suffixOf(r.signal_key)) && r.effType !== "string"
    );
    return { g, other };
  }, [rowBySuffix, rows]);

  if (!device) {
    return (
      <div className="device-detail-empty">
        <span className="material-symbols-outlined">search_off</span>
        <p className="helper-text">{t("deviceDetail.notFound")}</p>
      </div>
    );
  }

  const online = device.communicationStatus === "online";
  const locationLabel = locateDevice(device.latitude, device.longitude).label;
  const permCnt = rowBySuffix.get(CNT_PERMANENT);
  const momCnt = rowBySuffix.get(CNT_MOMENTARY);
  const activeMeta = SOURCES.find((s) => s.key === activeSource)!;

  // Hizli komut (header): reset_all_fcis. Backend allowlist'te ise calisir.
  const runQuick = async (slug: string) => {
    if (!onDeviceCommand) return;
    const label = signals.find((s) => s.key === `master.${slug}`)?.label ?? slug;
    setBusyQuick(slug);
    try {
      await onDeviceCommand(device.code, slug, label);
    } finally {
      setBusyQuick(null);
    }
  };
  const hasResetCmd = signals.some(
    (s) => s.key === "master.reset_all_fcis" && s.data_type === "binary_output" && s.is_active
  );

  // Ust ozet verileri (tum kaynaklardan).
  const serialMaster = strVal("master.info_serial_number");
  const serialSat01 = strVal("sat01.info_serial_number");
  const serialSat02 = strVal("sat02.info_serial_number");
  const ipv4 = strVal("master.info_ipv4_address");
  const gps = strVal("master.info_gps_latitude_longitude") ?? strVal("master.info_gps_string");
  const netOp = strVal("master.info_network_operator");
  const rssi = numVal("master.modem_rssi");
  const curNow = numVal("master.actual_current");
  const voltNow = numVal("master.actual_voltage");
  const tempNow = numVal("master.device_temperature") ?? numVal("master.conductor_temperature");
  const permCount = permCnt?.value ?? numVal("master.permanent_fault_counter");
  const momCount = momCnt?.value ?? numVal("master.momentary_fault_counter");

  return (
    <div className="device-detail">
      {/* ---- Header + hizli aksiyonlar ---- */}
      <header className="device-detail-head">
        <div className="device-detail-title">
          <span className={`device-detail-dot ${online ? "online" : "offline"}`} aria-hidden="true" />
          <div>
            <h2>{device.code}</h2>
            <span className="device-detail-code">{device.name}</span>
          </div>
          <span className={`device-detail-status-pill ${online ? "is-online" : "is-offline"}`}>
            <span className="material-symbols-outlined">
              {online ? "wifi" : "wifi_off"}
            </span>
            {online ? t("deviceDetail.online") : t("deviceDetail.offline")}
          </span>
          {device.alarmActive ? (
            <span className="device-detail-status-pill is-alarm">
              <span className="material-symbols-outlined">warning</span>
              {t("deviceDetail.alarmActive")}
            </span>
          ) : null}
        </div>
        {canCommand && onDeviceCommand ? (
          <div className="device-detail-quick">
            {hasResetCmd ? (
              <button
                type="button"
                className="secondary-btn action-btn is-reset"
                disabled={busyQuick != null}
                aria-busy={busyQuick === "reset_all_fcis"}
                onClick={() => void runQuick("reset_all_fcis")}
                title={t("deviceDetail.quick.resetHint")}
              >
                {busyQuick === "reset_all_fcis" ? (
                  <span className="btn-spinner" aria-hidden="true" />
                ) : (
                  <span className="material-symbols-outlined">restart_alt</span>
                )}
                {t("deviceDetail.quick.reset")}
              </button>
            ) : null}
          </div>
        ) : null}
      </header>

      {/* ---- Ust ozet bandi ---- */}
      <section className="device-summary">
        {/* Kilit olcum KPI'lari */}
        <div className="device-summary-kpis">
          <SummaryKpi
            icon="bolt"
            tone="amber"
            label={t("deviceDetail.kpi.current")}
            value={fmt(curNow ?? null, "analog", "mA")}
          />
          <SummaryKpi
            icon="electric_bolt"
            tone="blue"
            label={t("deviceDetail.kpi.voltage")}
            value={fmt(voltNow ?? null, "analog", "V")}
          />
          <SummaryKpi
            icon="device_thermostat"
            tone="rose"
            label={t("deviceDetail.kpi.temperature")}
            value={fmt(tempNow ?? null, "analog", "°C")}
          />
          <SummaryKpi
            icon="report"
            tone="red"
            label={t("deviceDetail.permanentFaults")}
            value={fmt(permCount ?? null, "counter")}
          />
          <SummaryKpi
            icon="flash_on"
            tone="orange"
            label={t("deviceDetail.momentaryFaults")}
            value={fmt(momCount ?? null, "counter")}
          />
        </div>

        {/* Meta seridi: topoloji + haberlesme + kimlik */}
        <div className="device-summary-meta">
          {topologyInfo?.regionName ? (
            <MetaItem icon="map" label={t("deviceDetail.meta.region")} value={topologyInfo.regionName} />
          ) : null}
          {topologyInfo?.lineName ? (
            <MetaItem icon="timeline" label={t("deviceDetail.meta.line")} value={topologyInfo.lineName} />
          ) : null}
          <MetaItem
            icon="battery_full"
            label={t("deviceDetail.meta.battery")}
            value={`%${Math.round(device.batteryPercent)}`}
          />
          {rssi != null ? (
            <MetaItem icon="cell_tower" label="RSSI" value={`${Math.round(rssi)} dBm`} />
          ) : null}
          {netOp ? <MetaItem icon="signal_cellular_alt" label={t("deviceDetail.meta.operator")} value={netOp} /> : null}
          {ipv4 ? <MetaItem icon="lan" label="IP" value={ipv4} /> : null}
          {gps ? <MetaItem icon="location_on" label="GPS" value={gps} /> : (
            <MetaItem icon="location_off" label={t("deviceDetail.meta.location")} value={locationLabel} />
          )}
        </div>

        {/* Kimlik: Master + Satellite seri no'lari */}
        <div className="device-summary-serials">
          <SerialCard tone="master" label="Master" serial={serialMaster ?? device.code} />
          {serialSat01 ? <SerialCard tone="green" label="Satellite 01" serial={serialSat01} /> : null}
          {serialSat02 ? <SerialCard tone="amber" label="Satellite 02" serial={serialSat02} /> : null}
        </div>
      </section>

      {/* ---- Ana sekme cubugu ---- */}
      <nav className="device-detail-tabs" role="tablist">
        <button
          type="button"
          role="tab"
          aria-selected={activeTab === "measures"}
          className={`device-detail-tab${activeTab === "measures" ? " active" : ""}`}
          onClick={() => setActiveTab("measures")}
        >
          <span className="material-symbols-outlined">monitoring</span>
          {t("deviceDetail.tabs.measures")}
        </button>
        <button
          type="button"
          role="tab"
          aria-selected={activeTab === "charts"}
          className={`device-detail-tab${activeTab === "charts" ? " active" : ""}`}
          onClick={() => setActiveTab("charts")}
        >
          <span className="material-symbols-outlined">show_chart</span>
          {t("deviceDetail.tabs.charts")}
        </button>
        {canCommand ? (
          <button
            type="button"
            role="tab"
            aria-selected={activeTab === "commands"}
            className={`device-detail-tab${activeTab === "commands" ? " active" : ""}`}
            onClick={() => setActiveTab("commands")}
          >
            <span className="material-symbols-outlined">terminal</span>
            {t("deviceDetail.tabs.commands")}
          </button>
        ) : null}
        {canConfig ? (
          <button
            type="button"
            role="tab"
            aria-selected={activeTab === "config"}
            className={`device-detail-tab${activeTab === "config" ? " active" : ""}`}
            onClick={() => setActiveTab("config")}
          >
            <span className="material-symbols-outlined">tune</span>
            {t("deviceDetail.tabs.config")}
          </button>
        ) : null}
      </nav>

      {/* ---- Komutlar sekmesi ---- */}
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

      {/* ---- Config sekmesi ---- */}
      {activeTab === "config" && canConfig && token ? (
        <DeviceConfigPanel device={device} />
      ) : null}

      {/* ---- Grafikler sekmesi ---- */}
      {activeTab === "charts" && token ? (
        <>
          <div className="device-detail-sources">
            {SOURCES.map((s) => {
              const n = values.filter((r) => r.device_id === device.id && r.source === s.key).length;
              return (
                <button
                  key={s.key}
                  className={`device-detail-source-tab tone-${s.tone} ${activeSource === s.key ? "active" : ""}`}
                  onClick={() => setActiveSource(s.key)}
                  disabled={n === 0}
                >
                  <span className="device-detail-source-badge">{s.label}</span>
                  <span className="device-detail-source-count">{n}</span>
                </button>
              );
            })}
          </div>
          <DeviceChartsPanel
            deviceCode={device.code}
            activeSource={activeSource}
            signals={signals}
            token={token}
          />
        </>
      ) : null}

      {/* ---- Olcumler sekmesi ---- */}
      {activeTab !== "measures" ? null : (
      <>
      {/* Kaynak sekmeleri */}
      <div className="device-detail-sources">
        {SOURCES.map((s) => {
          const n = values.filter((r) => r.device_id === device.id && r.source === s.key).length;
          return (
            <button
              key={s.key}
              className={`device-detail-source-tab tone-${s.tone} ${activeSource === s.key ? "active" : ""}`}
              onClick={() => setActiveSource(s.key)}
              disabled={n === 0}
            >
              <span className="device-detail-source-badge">{s.label}</span>
              <span className="device-detail-source-count">{n}</span>
            </button>
          );
        })}
      </div>

      {rows.length === 0 ? (
        <section className="device-detail-charts-placeholder">
          <span className="material-symbols-outlined">sensors_off</span>
          <h3>{t("deviceDetail.noSignals", { source: activeMeta.label })}</h3>
        </section>
      ) : (
        <div className={`device-detail-body tone-${activeMeta.tone}`}>
          {/* Sayaclar (ust 2 kutu) */}
          <div className="device-detail-counters">
            <CounterBox
              icon="report"
              tone="red"
              label={t("deviceDetail.permanentFaults")}
              value={fmt(permCnt?.value ?? null, "counter")}
            />
            <CounterBox
              icon="bolt"
              tone="amber"
              label={t("deviceDetail.momentaryFaults")}
              value={fmt(momCnt?.value ?? null, "counter")}
            />
          </div>

          {/* Kategoriler */}
          {CAT_ORDER.map((cat) => {
            const items = grouped.g[cat];
            const meta = CAT_META[cat];
            return (
              <section key={cat} className="device-detail-cat">
                <h4 className="device-detail-cat-title">
                  <span className="material-symbols-outlined">{meta.icon}</span>
                  {meta.title}
                </h4>
                {cat === "direction" ? (
                  <DirectionDiagram rowBySuffix={rowBySuffix} t={t} />
                ) : cat === "status" ? (
                  <div className="device-detail-bits">
                    {items.map(({ def, row }) => {
                      const on = row?.value === 1;
                      return (
                        <div
                          key={def.suffix}
                          className={`device-detail-bit ${on ? "is-on" : ""}`}
                          title={row ? fmt(row.value, "binary") : "—"}
                        >
                          <span className="device-detail-bit-dot" />
                          <span className="device-detail-bit-label">{def.label}</span>
                        </div>
                      );
                    })}
                  </div>
                ) : (
                  <div className="device-detail-measures">
                    {items.map(({ def, row }) => (
                      <div key={def.suffix} className="device-detail-measure">
                        <span className="device-detail-measure-label">{def.label}</span>
                        <span className="device-detail-measure-value">
                          {fmt(row?.value ?? null, row?.effType ?? "analog", row?.unit)}
                        </span>
                      </div>
                    ))}
                  </div>
                )}
              </section>
            );
          })}

          {/* Diger sinyaller (collapsible) */}
          {grouped.other.length > 0 ? (
            <section className="device-detail-cat">
              <button
                className="device-detail-cat-title as-toggle"
                onClick={() => setShowOther((v) => !v)}
              >
                <span className="material-symbols-outlined">
                  {showOther ? "expand_less" : "expand_more"}
                </span>
                {t("deviceDetail.otherSignals", { count: grouped.other.length })}
              </button>
              {showOther ? (
                <div className="device-detail-measures is-other">
                  {grouped.other.map((r) => (
                    <div key={r.signal_key} className="device-detail-measure">
                      <span className="device-detail-measure-label">{r.signal_label}</span>
                      <span className="device-detail-measure-value">
                        {fmt(r.value, r.effType, r.unit, r.value_string)}
                      </span>
                    </div>
                  ))}
                </div>
              ) : null}
            </section>
          ) : null}
        </div>
      )}
      </>
      )}
    </div>
  );
}

function CounterBox({
  icon,
  tone,
  label,
  value,
}: {
  icon: string;
  tone: "red" | "amber";
  label: string;
  value: string;
}) {
  return (
    <div className={`device-detail-counter tone-${tone}`}>
      <span className="material-symbols-outlined">{icon}</span>
      <div>
        <span className="device-detail-counter-label">{label}</span>
        <strong>{value}</strong>
      </div>
    </div>
  );
}

// Ust ozet: kilit olcum / sayac KPI karti.
function SummaryKpi({
  icon,
  tone,
  label,
  value,
}: {
  icon: string;
  tone: "amber" | "blue" | "rose" | "red" | "orange";
  label: string;
  value: string;
}) {
  return (
    <div className={`device-kpi tone-${tone}`}>
      <span className="device-kpi-icon material-symbols-outlined">{icon}</span>
      <div className="device-kpi-body">
        <span className="device-kpi-value">{value}</span>
        <span className="device-kpi-label">{label}</span>
      </div>
    </div>
  );
}

// Ust ozet: meta bilgi ogesi (ikon + etiket + deger).
function MetaItem({ icon, label, value }: { icon: string; label: string; value: string }) {
  return (
    <span className="device-meta-item" title={`${label}: ${value}`}>
      <span className="material-symbols-outlined">{icon}</span>
      <span className="device-meta-label">{label}</span>
      <span className="device-meta-value">{value}</span>
    </span>
  );
}

// Ust ozet: Master/Satellite seri no karti.
function SerialCard({
  tone,
  label,
  serial,
}: {
  tone: "master" | "green" | "amber";
  label: string;
  serial: string;
}) {
  return (
    <div className={`device-serial-card tone-${tone}`}>
      <span className="device-serial-badge">{label}</span>
      <span className="device-serial-value" title={serial}>
        <span className="material-symbols-outlined">tag</span>
        {serial}
      </span>
    </div>
  );
}

// Ariza yonu: her cift (A=yesil, B=kirmizi) icin gorsel akis diyagrami.
// Aktif yon renkli ok ile vurgulanir; ikisi de pasifse notr.
const DIRECTION_ROWS: { key: string; label: string; aSuffix: string; bSuffix: string }[] = [
  {
    key: "load_flow",
    label: "Yük Akışı",
    aSuffix: "load_flow_direction_green_a",
    bSuffix: "load_flow_direction_red_b",
  },
  {
    key: "overcurrent",
    label: "Aşırı Akım Arıza Yönü",
    aSuffix: "overcurrent_fault_direction_green_a",
    bSuffix: "overcurrent_fault_direction_red_b",
  },
  {
    key: "delta",
    label: "ΔI/Δt Arıza Yönü",
    aSuffix: "delta_i_delta_t_fault_direction_green_a",
    bSuffix: "delta_i_delta_t_fault_direction_red_b",
  },
];

function DirectionDiagram({
  rowBySuffix,
  t,
}: {
  rowBySuffix: Map<string, Row>;
  t: (key: string, opts?: Record<string, unknown>) => string;
}) {
  return (
    <div className="device-dir-grid">
      {DIRECTION_ROWS.map((d) => {
        const aOn = rowBySuffix.get(d.aSuffix)?.value === 1;
        const bOn = rowBySuffix.get(d.bSuffix)?.value === 1;
        const state = aOn ? "a" : bOn ? "b" : "none";
        return (
          <div key={d.key} className={`device-dir-row is-${state}`}>
            <span className="device-dir-label">{d.label}</span>
            <div className="device-dir-flow">
              <span className={`device-dir-node node-a${aOn ? " active" : ""}`}>A</span>
              <span className="device-dir-track" aria-hidden="true">
                <span className="device-dir-arrow arrow-a material-symbols-outlined">arrow_back</span>
                <span className="device-dir-line" />
                <span className="device-dir-arrow arrow-b material-symbols-outlined">arrow_forward</span>
              </span>
              <span className={`device-dir-node node-b${bOn ? " active" : ""}`}>B</span>
            </div>
            <span className="device-dir-state">
              {state === "a"
                ? t("deviceDetail.direction.toA")
                : state === "b"
                  ? t("deviceDetail.direction.toB")
                  : t("deviceDetail.direction.none")}
            </span>
          </div>
        );
      })}
    </div>
  );
}
