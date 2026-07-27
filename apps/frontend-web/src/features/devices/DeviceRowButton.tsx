/**
 * Duz liste gorunumunun cihaz KARTI (ad + topoloji + batarya + son gorulme).
 *
 * Hat Agaci bunu KULLANMAZ; orada tarama hizi onemli oldugu icin ince tek
 * satirlik `DeviceTreeRow` var (bkz. DeviceLineTree.tsx). Iki gorunum
 * bilerek farkli yogunlukta.
 *
 * Satirin icerdigi bilgiler (alarm durumu, batarya) disaridan hesaplanip
 * verilir; bilesen saf render.
 */
import { useTranslation } from "react-i18next";

import type { DeviceRow } from "../../shared/types";

export type DeviceAlarmState = "open" | "ack" | null;

type Props = {
  device: DeviceRow;
  selected: boolean;
  onSelect: (id: number) => void;
  alarmState: DeviceAlarmState;
  /** 0-100 arasi normalize batarya; bilinmiyorsa null. */
  batteryPercent: number | null;
  /** "Bolge · Hat" etiketi. */
  subtitle: string;
  /** Hicbir hatta bagli degil — etiket soluk + link_off ikonu. */
  unassigned?: boolean;
};

function batteryClass(percent: number | null | undefined): string {
  if (percent === null || percent === undefined) return "device-battery--unknown";
  if (percent <= 20) return "device-battery--critical";
  if (percent <= 50) return "device-battery--low";
  return "device-battery--ok";
}

export function DeviceRowButton({
  device,
  selected,
  onSelect,
  alarmState,
  batteryPercent,
  subtitle,
  unassigned = false
}: Props) {
  const { t } = useTranslation();
  const isOnline = device.communicationStatus === "online";
  const hasAlarm = alarmState !== null;

  return (
    <button
      className={[
        "device-row",
        selected ? "selected" : "",
        isOnline ? "device-row--online" : "device-row--offline",
        hasAlarm ? "device-row--alarm" : "",
        unassigned ? "device-row--unassigned" : ""
      ]
        .filter(Boolean)
        .join(" ")}
      onClick={() => onSelect(device.id)}
    >
      {/* Sağ üst köşede alarm rozeti — onaylanmamışsa kırmızı yanıp söner,
         onaylanmışsa sarı/sabit (kullanıcı görmüş, üzerinde çalışılıyor). */}
      {alarmState === "open" ? (
        <span
          className="device-row-alarm-pulse device-row-alarm-pulse--corner"
          title={t("dashboard.sidebar.alarmOpen")}
          aria-label={t("dashboard.sidebar.alarmOpen")}
        >
          <span className="material-symbols-outlined">warning</span>
        </span>
      ) : alarmState === "ack" ? (
        <span
          className="device-row-alarm-acked device-row-alarm-pulse--corner"
          title={t("dashboard.sidebar.alarmAcked")}
          aria-label={t("dashboard.sidebar.alarmAcked")}
        >
          <span className="material-symbols-outlined">verified</span>
        </span>
      ) : null}

      {/* Üst satır: durum noktası + cihaz adı */}
      <div className="device-row-top">
        <span
          className={`device-status-dot ${isOnline ? "online" : "offline"}`}
          title={isOnline ? t("dashboard.sidebar.online") : t("dashboard.sidebar.offline")}
        />
        <div className="device-row-name">
          <strong>{device.name}</strong>
          <span className="device-row-code">{device.code}</span>
        </div>
      </div>

      {/* Orta satır: bölge/hat + batarya yan yana (çerçevesiz) */}
      <div className="device-row-meta-row device-row-meta-row--bare">
        <span
          className={`device-row-location ${unassigned ? "device-row-location--unassigned" : ""}`}
          title={subtitle}
        >
          <span className="material-symbols-outlined">{unassigned ? "link_off" : "cable"}</span>
          {subtitle}
        </span>
        <span
          className={`device-battery-chip device-battery-chip--bare ${batteryClass(batteryPercent)}`}
          title={
            batteryPercent !== null
              ? t("dashboard.sidebar.battery", { value: Math.round(batteryPercent) })
              : t("dashboard.sidebar.noBattery")
          }
        >
          <span className="device-battery-icon" aria-hidden="true">
            <span className="device-battery-fill" style={{ width: `${batteryPercent ?? 0}%` }} />
          </span>
          <span className="device-battery-text">
            {batteryPercent !== null ? `%${Math.round(batteryPercent)}` : "—"}
          </span>
        </span>
      </div>

      {/* Alt satır: tarih + saat (sade) */}
      <div className="device-row-last-line">
        <span className="material-symbols-outlined">schedule</span>
        <span className="device-row-last-text">
          {device.lastUpdateAt
            ? new Date(device.lastUpdateAt).toLocaleString(undefined, {
                day: "2-digit",
                month: "2-digit",
                year: "numeric",
                hour: "2-digit",
                minute: "2-digit",
                second: "2-digit"
              })
            : "—"}
        </span>
      </div>
    </button>
  );
}
