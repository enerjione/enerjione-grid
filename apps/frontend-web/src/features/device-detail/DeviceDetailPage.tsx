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

type TopologyInfo = { regionName: string; lineName: string } | undefined;

type Props = {
  deviceId: number;
  devices: DeviceRow[];
  values: SignalLiveRow[];
  signals: SignalCatalogRow[];
  gateways: Gateway[];
  topologyInfo?: TopologyInfo;
  /** ENGINEER/INSTALLER ise komut butonlari gorunur. */
  canCommand?: boolean;
  /** Komut gonderme handler'i (confirm + toast App.tsx'te merkezi). */
  onDeviceCommand?: (deviceCode: string, command: string, label: string) => Promise<void>;
};

// Cihaz detay komut butonlari: SignalCatalog binary_output slug -> UI meta.
// Index backend'de SignalCatalog'dan cozulur; burada sadece ikon/vurgu.
// 4 ana komut buton olarak; kalan binary_output sinyalleri "Diger" menusunde.
const PRIMARY_COMMANDS: { slug: string; icon: string; danger?: boolean }[] = [
  { slug: "trigger_config_download", icon: "download" },
  { slug: "config_update", icon: "settings" },
  { slug: "clear_counters", icon: "delete_sweep" },
  { slug: "software_reset", icon: "restart_alt", danger: true },
];

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
  onDeviceCommand,
}: Props) {
  const { t } = useTranslation();
  const [activeSource, setActiveSource] = useState<SignalSource>("master");
  const [showOther, setShowOther] = useState(false);
  const [showMoreCmds, setShowMoreCmds] = useState(false);
  const [busyCmd, setBusyCmd] = useState<string | null>(null);

  const device = useMemo(() => devices.find((d) => d.id === deviceId), [devices, deviceId]);

  // Komut listesi: master kaynak binary_output sinyalleri (SignalCatalog'dan).
  // slug = key'in "master." sonrasi. Ana komutlar PRIMARY_COMMANDS sirasinda,
  // kalani "Diger" menusunde (display_order'a gore).
  const commandSignals = useMemo(() => {
    return signals
      .filter((s) => s.data_type === "binary_output" && s.source === "master" && s.is_active)
      .map((s) => ({ slug: s.key.replace(/^master\./, ""), label: s.label, order: s.display_order }))
      .sort((a, b) => a.order - b.order);
  }, [signals]);

  const primaryCmds = useMemo(
    () =>
      PRIMARY_COMMANDS.map((p) => {
        const sig = commandSignals.find((c) => c.slug === p.slug);
        return sig ? { ...p, label: sig.label } : null;
      }).filter((x): x is { slug: string; icon: string; danger?: boolean; label: string } => x != null),
    [commandSignals]
  );
  const primarySlugs = useMemo(() => new Set(primaryCmds.map((c) => c.slug)), [primaryCmds]);
  const moreCmds = useMemo(
    () => commandSignals.filter((c) => !primarySlugs.has(c.slug)),
    [commandSignals, primarySlugs]
  );

  const runCommand = async (slug: string, label: string) => {
    if (!onDeviceCommand || !device) return;
    setBusyCmd(slug);
    try {
      await onDeviceCommand(device.code, slug, label);
    } finally {
      setBusyCmd(null);
    }
  };

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

  return (
    <div className="device-detail">
      {/* ---- Header ---- */}
      <header className="device-detail-head">
        <div className="device-detail-title">
          <span className={`device-detail-dot ${online ? "online" : "offline"}`} aria-hidden="true" />
          <div>
            <h2>{device.code}</h2>
            <span className="device-detail-code">{device.name}</span>
          </div>
        </div>
        <div className="device-detail-chips">
          {topologyInfo?.regionName ? (
            <span className="device-detail-chip">
              <span className="material-symbols-outlined">map</span>
              {topologyInfo.regionName}
            </span>
          ) : null}
          {topologyInfo?.lineName ? (
            <span className="device-detail-chip is-line">
              <span className="material-symbols-outlined">timeline</span>
              {topologyInfo.lineName}
            </span>
          ) : null}
          <span className="device-detail-chip is-battery">
            <span className="material-symbols-outlined">battery_full</span>
            %{Math.round(device.batteryPercent)}
          </span>
          <span className="device-detail-chip" title={locationLabel}>
            <span className="material-symbols-outlined">location_on</span>
            {locationLabel}
          </span>
          {device.alarmActive ? (
            <span className="device-detail-chip is-alarm">
              <span className="material-symbols-outlined">warning</span>
              {t("deviceDetail.alarmActive")}
            </span>
          ) : null}
        </div>
      </header>

      {/* ---- Komutlar (ENGINEER/INSTALLER) ---- */}
      {canCommand && onDeviceCommand && primaryCmds.length > 0 ? (
        <section className="device-detail-commands">
          <span className="device-detail-commands-title">
            <span className="material-symbols-outlined">terminal</span>
            {t("deviceDetail.commands.title")}
          </span>
          <div className="device-detail-commands-row">
            {primaryCmds.map((c) => (
              <button
                key={c.slug}
                type="button"
                className={`secondary-btn action-btn${c.danger ? " danger" : ""}`}
                disabled={busyCmd != null}
                aria-busy={busyCmd === c.slug}
                onClick={() => void runCommand(c.slug, c.label)}
                title={c.label}
              >
                {busyCmd === c.slug ? (
                  <span className="btn-spinner" aria-hidden="true" />
                ) : (
                  <span className="material-symbols-outlined">{c.icon}</span>
                )}
                {c.label}
              </button>
            ))}
            {moreCmds.length > 0 ? (
              <button
                type="button"
                className="secondary-btn action-btn"
                disabled={busyCmd != null}
                onClick={() => setShowMoreCmds((v) => !v)}
                title={t("deviceDetail.commands.more")}
              >
                <span className="material-symbols-outlined">more_horiz</span>
                {t("deviceDetail.commands.more")}
              </button>
            ) : null}
          </div>
          {showMoreCmds && moreCmds.length > 0 ? (
            <div className="device-detail-commands-more">
              {moreCmds.map((c) => (
                <button
                  key={c.slug}
                  type="button"
                  className="secondary-btn action-btn"
                  disabled={busyCmd != null}
                  aria-busy={busyCmd === c.slug}
                  onClick={() => void runCommand(c.slug, c.label)}
                >
                  {busyCmd === c.slug ? (
                    <span className="btn-spinner" aria-hidden="true" />
                  ) : null}
                  {c.label}
                </button>
              ))}
            </div>
          ) : null}
        </section>
      ) : null}

      {/* ---- Kaynak sekmeleri ---- */}
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
            const binary = cat === "status" || cat === "direction";
            return (
              <section key={cat} className="device-detail-cat">
                <h4 className="device-detail-cat-title">
                  <span className="material-symbols-outlined">{meta.icon}</span>
                  {meta.title}
                </h4>
                {binary ? (
                  <div className={`device-detail-bits ${cat === "direction" ? "is-direction" : ""}`}>
                    {items.map(({ def, row }) => {
                      const on = row?.value === 1;
                      const dir = def.suffix.includes("green_a")
                        ? "a"
                        : def.suffix.includes("red_b")
                          ? "b"
                          : "";
                      return (
                        <div
                          key={def.suffix}
                          className={`device-detail-bit ${on ? "is-on" : ""} ${dir ? `dir-${dir}` : ""}`}
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
