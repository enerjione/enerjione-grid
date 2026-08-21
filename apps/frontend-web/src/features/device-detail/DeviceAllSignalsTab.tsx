/**
 * DeviceAllSignalsTab — "Tumu" sekmesi: SET OZETI.
 *
 * Master + Satellite 01 + Satellite 02 uc cihazi YAN YANA kartlarda gosterir
 * (scroll yok). Her kart: kimlik + kilit KPI (akim/gerilim/sicaklik/pil) +
 * ariza durumu (rozet) + sayaclar. Bagli olmayan uydu -> soluk kart + "Veri yok".
 * Tum sinyaller degil, set'in genel durumu tek bakista.
 */

import { useCallback, useMemo } from "react";
import { useTranslation } from "react-i18next";

import { useDeviceModelSettings } from "../../components/DeviceModelSettingsProvider";
import { voltageToPercent } from "../../shared/battery";

import { signalTrust } from "../../shared/signalQuality";
import { sourceLabel, sourceTone } from "../signals/signalCatalogConstants";
import type { DeviceRow, SignalLiveRow, SignalSource } from "../../shared/types";

type Props = {
  device: DeviceRow;
  values: SignalLiveRow[];
  gwOnline: boolean;
  /** Her kaynaktaki sinyal sayisi — 0 ise cihaz bagli degil (soluk kart). */
  sourceCounts: Record<SignalSource, number>;
  /** Bu cihazda OLCUM YAPAN uniteler (modele gore).
   *
   *  SN 2.0'da `master` + iki uydu; Pole Master Kit setinde UC UYDU. Sabit
   *  uclu kullanildiginda sette bos bir "Master" karti ciziliyor, gercek
   *  ucuncu unite (Satellite 03) ise hic gorunmuyordu. */
  sources: SignalSource[];
};

/** Kaynak -> kart gorseli. Master ana unite (RTU) ikonu, uydular anten. */
function sourceCard(key: SignalSource): { key: SignalSource; label: string; tone: string; icon: string } {
  return {
    key,
    label: sourceLabel(key),
    tone: sourceTone(key),
    icon: key === "master" ? "dns" : "settings_input_antenna"
  };
}

const NUMBER_FORMATTER = new Intl.NumberFormat("tr-TR", {
  minimumFractionDigits: 0,
  maximumFractionDigits: 3,
  useGrouping: false,
});

// Kilit durum sinyalleri (binary) — rozet.
const STATUS_DEFS: { suffix: string; labelKey: string; icon: string }[] = [
  { suffix: "overcurrent_tripped", labelKey: "overcurrent", icon: "bolt" },
  { suffix: "voltage_loss", labelKey: "voltageLoss", icon: "power_off" },
  { suffix: "current_loss", labelKey: "currentLoss", icon: "flash_off" },
  { suffix: "delta_i_delta_t_tripped", labelKey: "delta", icon: "show_chart" },
  { suffix: "permanent_fault", labelKey: "permanent", icon: "warning" },
  { suffix: "momentary_fault", labelKey: "momentary", icon: "error_outline" },
];

// Analog olcum degerleri — bos alani doldur, daha fazla bilgi (2 kolon liste).
const MEASURE_DEFS: { suffix: string; labelKey: string }[] = [
  { suffix: "average_current", labelKey: "avgCurrent" },
  { suffix: "maximum_current", labelKey: "maxCurrent" },
  { suffix: "minimum_current", labelKey: "minCurrent" },
  { suffix: "maximum_voltage", labelKey: "maxVoltage" },
  { suffix: "minimum_voltage", labelKey: "minVoltage" },
  { suffix: "conductor_temperature", labelKey: "conductorTemp" },
  { suffix: "fault_current", labelKey: "faultCurrent" },
  { suffix: "fault_duration", labelKey: "faultDuration" },
  { suffix: "last_good_known_current", labelKey: "lastGood" },
  { suffix: "trip_level", labelKey: "tripLevel" },
];

function fmtNum(v: number | undefined, unit: string | null | undefined): string {
  if (v == null || !Number.isFinite(v)) return "—";
  const txt = NUMBER_FORMATTER.format(v);
  return unit ? `${txt} ${unit}` : txt;
}

function batteryClass(pct: number): string {
  if (pct <= 20) return "critical";
  if (pct <= 50) return "low";
  return "ok";
}

export function DeviceAllSignalsTab({
  device,
  values,
  gwOnline,
  sourceCounts,
  sources
}: Props) {
  const { t } = useTranslation();
  // Batarya esigi cihaz TURUNDEN gelir; burada sabit 3.2/4.2 vardi ve
  // backend'in 3.40/3.71'i ile uyusmuyordu.
  const { thresholdsFor } = useDeviceModelSettings();
  // ESIK UNITEYE GORE: uydu hucresi master hucresiyle ayni aralikta calismaz;
  // ortak esikle olculunce uydu kartlari sahada saglamken %0 gosteriyordu.
  const voltToPct = useCallback(
    (v: number | undefined, unite: string) =>
      voltageToPercent(v, thresholdsFor(device.model, unite)),
    [thresholdsFor, device.model]
  );

  const kartlar = useMemo(() => sources.map(sourceCard), [sources]);

  // kaynak -> (suffix -> row). Bu cihazin tum satirlari.
  const bySource = useMemo(() => {
    const m = new Map<SignalSource, Map<string, SignalLiveRow>>();
    for (const src of kartlar) m.set(src.key, new Map());
    for (const r of values) {
      if (r.device_id !== device.id) continue;
      const inner = m.get(r.source as SignalSource);
      if (!inner) continue;
      const i = r.signal_key.indexOf(".");
      inner.set(i >= 0 ? r.signal_key.slice(i + 1) : r.signal_key, r);
    }
    return m;
  }, [values, device.id, kartlar]);

  return (
    <div className="device-set">
      {kartlar.map((src) => {
        const rows = bySource.get(src.key) ?? new Map();
        const get = (suffix: string): SignalLiveRow | undefined => rows.get(suffix);
        const numOf = (suffix: string): number | undefined => {
          const v = get(suffix)?.value;
          return v == null ? undefined : v;
        };

        // KPI degerleri (birim sinyalin kendi unit'i).
        const cur = get("actual_current");
        const volt = get("actual_voltage");
        const temp = get("device_temperature") ?? get("conductor_temperature");
        const battPct =
          src.key === "master"
            ? Number.isFinite(device.batteryPercent)
              ? device.batteryPercent
              : undefined
            : voltToPct(numOf("battery_voltage_satellite"), src.key);

        const permCount = numOf("permanent_fault_counter");
        const momCount = numOf("momentary_fault_counter");

        // Uydu bagli degil (sinyal yok).
        const noData = (sourceCounts[src.key] ?? 0) === 0;

        return (
          <section
            key={src.key}
            className={`device-set-card tone-${src.tone}${noData ? " is-offline" : ""}`}
          >
            {/* Baslik */}
            <header className="device-set-head">
              <span className="device-set-icon material-symbols-outlined">{src.icon}</span>
              <div className="device-set-title">
                <span className="device-set-name">{src.label}</span>
                {rows.get("serial_number")?.value != null ? (
                  <span className="device-set-serial">
                    {Math.round(rows.get("serial_number")!.value as number)}
                  </span>
                ) : null}
              </div>
            </header>

            {noData ? (
              <div className="device-set-nodata">
                <span className="material-symbols-outlined">sensors_off</span>
                <p>{t("deviceDetail.set.noDataLong")}</p>
              </div>
            ) : (
              <>
                {/* KPI mini grid (2x2) */}
                <div className="device-set-kpis">
                  {/* AKIM / GERILIM / SICAKLIK birer OLCUMDUR, durum degil.
                      Her birine ayri renkli rozet vermek renge anlam
                      yuklemeden gurultu uretiyordu; ustelik Akim rozeti
                      (amber) kartin Master KIMLIK rozetiyle BIREBIR ayni
                      renkti. Notr cizilirler. */}
                  <SetKpi icon="bolt" tone="notr" label={t("deviceDetail.kpi.current")} value={fmtNum(cur?.value ?? undefined, cur?.unit)} />
                  <SetKpi icon="electric_bolt" tone="notr" label={t("deviceDetail.kpi.voltage")} value={fmtNum(volt?.value ?? undefined, volt?.unit)} />
                  <SetKpi icon="device_thermostat" tone="notr" label={t("deviceDetail.kpi.temperature")} value={fmtNum(temp?.value ?? undefined, temp?.unit)} />
                  {/* PIL ISTISNA: yuzdesi bir DURUMDUR (iyi/dusuk/kritik) ve
                      rengi o durumdan gelir. Olculemediyse notr — "bilmiyoruz"
                      bir uyari degildir. */}
                  <SetKpi
                    icon="battery_full"
                    tone={
                      battPct == null
                        ? "notr"
                        : batteryClass(battPct) === "ok"
                          ? "green"
                          : batteryClass(battPct) === "low"
                            ? "amber"
                            : "red"
                    }
                    label={t("deviceDetail.meta.battery")}
                    value={battPct == null ? "—" : `%${Math.round(battPct)}`}
                  />
                </div>

                {/* Ariza durumu */}
                <div className="device-set-block">
                  <span className="device-set-block-title">{t("deviceDetail.set.faultStatus")}</span>
                  <ul className="device-set-status-list">
                    {STATUS_DEFS.map((d) => {
                      const row = get(d.suffix);
                      // UC DURUM — bkz. shared/signalQuality.ts.
                      // Eskiden yalnizca `value === 1` bakiliyordu: "veri yok"
                      // ve haberlesmesi kopmus cihazin `comm_lost` kaliteli
                      // 0.0 degeri, gercekten normal olan bir olcumle AYNI
                      // yesil rozeti uretiyordu.
                      const trust = signalTrust(row?.value, row?.quality, gwOnline);
                      if (trust !== "trusted") {
                        return (
                          <li key={d.suffix} className="device-set-status-row">
                            <span className="device-set-status-name">
                              <span className="material-symbols-outlined">{d.icon}</span>
                              {t(`deviceDetail.set.signal.${d.labelKey}`)}
                            </span>
                            <span className="device-set-badge is-unknown">
                              <span className="material-symbols-outlined">help</span>
                              {trust === "missing"
                                ? t("deviceDetail.status.noData")
                                : t("deviceDetail.status.untrusted")}
                            </span>
                          </li>
                        );
                      }
                      const active = row?.value === 1;
                      return (
                        <li key={d.suffix} className="device-set-status-row">
                          <span className="device-set-status-name">
                            <span className="material-symbols-outlined">{d.icon}</span>
                            {t(`deviceDetail.set.signal.${d.labelKey}`)}
                          </span>
                          <span className={`device-set-badge ${active ? "is-active" : "is-normal"}`}>
                            <span className="material-symbols-outlined">{active ? "warning" : "check_circle"}</span>
                            {active ? t("deviceDetail.status.active") : t("deviceDetail.status.normal")}
                          </span>
                        </li>
                      );
                    })}
                  </ul>
                </div>

                {/* Analog olcumler (2 kolon) — bos alani doldurur */}
                <div className="device-set-block">
                  <span className="device-set-block-title">{t("deviceDetail.set.measures")}</span>
                  <div className="device-set-measures">
                    {MEASURE_DEFS.map((m) => {
                      const row = get(m.suffix);
                      if (!row) return null; // o kaynakta yoksa gosterme
                      return (
                        <div key={m.suffix} className="device-set-measure">
                          <span className="device-set-measure-label">{t(`deviceDetail.set.measure.${m.labelKey}`)}</span>
                          <span className="device-set-measure-value">{fmtNum(row.value ?? undefined, row.unit)}</span>
                        </div>
                      );
                    })}
                  </div>
                </div>

                {/* Sayaclar */}
                {/* SAYACLAR — renk DEGERDEN gelir, KAPTAN degil.
                    Onceden iki kutu da KOSULSUZ alarm zeminliydi: sifir
                    ariza, yani IYI HABER, kart dibinde iki kirmizi kutu
                    olarak duruyordu. Duzen KPI ile ayni (deger ustte). */}
                <div className="device-set-counters">
                  <SetCounter
                    icon="report"
                    tone="red"
                    label={t("deviceDetail.permanentFaults")}
                    count={permCount}
                  />
                  <SetCounter
                    icon="flash_on"
                    tone="orange"
                    label={t("deviceDetail.momentaryFaults")}
                    count={momCount}
                  />
                </div>
              </>
            )}
          </section>
        );
      })}
    </div>
  );
}

/** Ariza sayaci. Renk YALNIZCA sifirdan buyukken.
 *
 *  `count == null` (olculemedi) bir ariza DEGILDIR: tire gosterilir ve
 *  kutu notr kalir. "Bilmiyoruz"u alarm gibi gostermek, gercek bir
 *  arizanin dikkat cekiciligini azaltirdi. */
function SetCounter({
  icon,
  tone,
  label,
  count
}: {
  icon: string;
  tone: "red" | "orange";
  label: string;
  count: number | null | undefined;
}) {
  const sayi = count != null && Number.isFinite(count) ? Math.round(count) : null;
  const arizaVar = sayi != null && sayi > 0;
  return (
    <div
      className={`device-set-counter tone-${tone}${arizaVar ? " has-fault" : ""}`}
    >
      <span className="material-symbols-outlined">{icon}</span>
      <div>
        <strong>{sayi != null ? sayi : "—"}</strong>
        <span className="device-set-counter-label">{label}</span>
      </div>
    </div>
  );
}

function SetKpi({ icon, tone, label, value }: { icon: string; tone: string; label: string; value: string }) {
  return (
    <div className={`device-set-kpi tone-${tone}`}>
      <span className="device-set-kpi-icon material-symbols-outlined">{icon}</span>
      <div className="device-set-kpi-body">
        <span className="device-set-kpi-value">{value}</span>
        <span className="device-set-kpi-label">{label}</span>
      </div>
    </div>
  );
}
