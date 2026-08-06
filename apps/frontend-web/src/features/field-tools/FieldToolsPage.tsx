/**
 * Saha Araclari — Cihaz Ayarlari menusu altinda diagnostik sayfa.
 *
 * Ilk arac: ping testi. ICMP paketleri MINI PC'den (backend) gonderilir,
 * tarayicidan degil — kullanicinin kendi agindan erisilebilen bir cihaz
 * sahadan erisilemiyor olabilir; bu ayrimi UI aciklamasi da soyler.
 *
 * Sol kart: hedef girisi + sonuc ozeti + ham cikti. Sag kart: kayitli
 * cihazlar (fetchDevices) — satira tiklaninca IP hedefe yazilir, "Test Et"
 * dogrudan ping baslatir. Yetki: installer/engineer (backend de ayni).
 */
import { useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";

import { useToast } from "../../components/ToastProvider";
import { fetchDevices, pingFieldHost } from "../../shared/api";
import type { DeviceRow, PingResult } from "../../shared/types";

type Props = {
  accessToken: string;
};

const PACKET_COUNTS = [1, 4, 8, 10];

function formatMs(value: number | null): string {
  return value === null ? "—" : String(Math.round(value * 100) / 100);
}

export function FieldToolsPage({ accessToken }: Props) {
  const { t } = useTranslation();
  const toast = useToast();

  const [host, setHost] = useState("");
  const [count, setCount] = useState(4);
  const [pinging, setPinging] = useState(false);
  const [result, setResult] = useState<PingResult | null>(null);
  const [error, setError] = useState<string | null>(null);

  const [devices, setDevices] = useState<DeviceRow[]>([]);
  const [devicesLoading, setDevicesLoading] = useState(true);
  const [search, setSearch] = useState("");

  useEffect(() => {
    let cancelled = false;
    fetchDevices(accessToken)
      .then((rows) => {
        if (!cancelled) setDevices(rows);
      })
      .catch(() => {
        // Cihaz listesi yardimci konfor — alinamazsa sayfa yine calisir,
        // kullanici IP'yi elle girer. Bu yuzden sessiz gec.
      })
      .finally(() => {
        if (!cancelled) setDevicesLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [accessToken]);

  const filteredDevices = useMemo(() => {
    const q = search.trim().toLowerCase();
    if (!q) return devices;
    return devices.filter(
      (d) =>
        d.name.toLowerCase().includes(q) ||
        d.code.toLowerCase().includes(q) ||
        (d.ipAddress ?? "").toLowerCase().includes(q)
    );
  }, [devices, search]);

  const runPing = async (target: string) => {
    const trimmed = target.trim();
    if (!trimmed) {
      setError(t("fieldTools.ping.invalidHost"));
      return;
    }
    setPinging(true);
    setError(null);
    setResult(null);
    try {
      const res = await pingFieldHost(accessToken, trimmed, count);
      setResult(res);
      if (res.success) {
        toast.success(
          t("fieldTools.ping.successToast", { host: res.host, avg: formatMs(res.rttAvgMs) })
        );
      } else {
        toast.error(t("fieldTools.ping.failToast", { host: res.host }));
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : t("common.errorOccurred"));
    } finally {
      setPinging(false);
    }
  };

  const handleDevicePick = (device: DeviceRow) => {
    if (!device.ipAddress) return;
    setHost(device.ipAddress);
    setResult(null);
    setError(null);
  };

  return (
    <section className="tab-panel ft-page">
      <header className="ft-header">
        <h2>{t("fieldTools.title")}</h2>
        <p>{t("fieldTools.subtitle")}</p>
      </header>

      <div className="ft-grid">
        {/* --- Ping karti --- */}
        <div className="ft-card">
          <div className="ft-card-head">
            <h3>
              <span className="material-symbols-outlined">network_ping</span>
              {t("fieldTools.ping.cardTitle")}
            </h3>
          </div>

          <form
            className="ft-ping-form"
            onSubmit={(e) => {
              e.preventDefault();
              void runPing(host);
            }}
          >
            <label className="ft-field ft-field--host">
              <span>{t("fieldTools.ping.hostLabel")}</span>
              <input
                type="text"
                value={host}
                placeholder={t("fieldTools.ping.hostPlaceholder")}
                onChange={(e) => setHost(e.target.value)}
                disabled={pinging}
                spellCheck={false}
                autoComplete="off"
              />
            </label>
            <label className="ft-field ft-field--count">
              <span>{t("fieldTools.ping.countLabel")}</span>
              <select
                value={count}
                onChange={(e) => setCount(Number(e.target.value))}
                disabled={pinging}
              >
                {PACKET_COUNTS.map((n) => (
                  <option key={n} value={n}>
                    {n}
                  </option>
                ))}
              </select>
            </label>
            <button type="submit" className="primary-btn ft-ping-btn" disabled={pinging}>
              <span className={`material-symbols-outlined ${pinging ? "ft-spin" : ""}`}>
                {pinging ? "progress_activity" : "network_ping"}
              </span>
              {pinging ? t("fieldTools.ping.running") : t("fieldTools.ping.run")}
            </button>
          </form>

          {error ? <p className="error-text">{error}</p> : null}

          {result ? (
            <div className="ft-result">
              <div
                className={`ft-result-badge ${result.success ? "is-ok" : "is-fail"}`}
              >
                <span className="material-symbols-outlined">
                  {result.success ? "check_circle" : "error"}
                </span>
                {result.success
                  ? t("fieldTools.ping.reachable")
                  : t("fieldTools.ping.unreachable")}
                <code>{result.host}</code>
              </div>

              <dl className="ft-result-stats">
                <div>
                  <dt>{t("fieldTools.ping.packets")}</dt>
                  <dd>
                    {t("fieldTools.ping.packetsValue", {
                      received: result.packetsReceived,
                      sent: result.packetsSent,
                    })}
                  </dd>
                </div>
                <div>
                  <dt>{t("fieldTools.ping.loss")}</dt>
                  <dd>%{result.packetLossPercent}</dd>
                </div>
                <div>
                  <dt>{t("fieldTools.ping.rtt")}</dt>
                  <dd>
                    {t("fieldTools.ping.rttValue", {
                      min: formatMs(result.rttMinMs),
                      avg: formatMs(result.rttAvgMs),
                      max: formatMs(result.rttMaxMs),
                    })}
                  </dd>
                </div>
                <div>
                  <dt>{t("fieldTools.ping.duration")}</dt>
                  <dd>{(result.durationMs / 1000).toFixed(1)} sn</dd>
                </div>
              </dl>

              <details className="ft-raw">
                <summary>{t("fieldTools.ping.rawOutput")}</summary>
                <pre>{result.output}</pre>
              </details>
            </div>
          ) : null}
        </div>

        {/* --- Kayitli cihazlar karti --- */}
        <div className="ft-card ft-card--devices">
          <div className="ft-card-head">
            <h3>
              <span className="material-symbols-outlined">router</span>
              {t("fieldTools.devices.cardTitle")}
            </h3>
            <small>{t("fieldTools.devices.hint")}</small>
          </div>

          <input
            type="search"
            className="ft-device-search"
            placeholder={t("fieldTools.devices.searchPlaceholder")}
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />

          <div className="ft-device-list">
            {devicesLoading ? (
              <p className="ft-device-empty">{t("common.loading")}</p>
            ) : filteredDevices.length === 0 ? (
              <p className="ft-device-empty">
                {devices.length === 0
                  ? t("fieldTools.devices.empty")
                  : t("fieldTools.devices.emptyNoMatch")}
              </p>
            ) : (
              filteredDevices.map((device) => (
                <div
                  key={device.id}
                  className={`ft-device-row ${device.ipAddress ? "" : "is-disabled"}`}
                  onClick={() => handleDevicePick(device)}
                >
                  <span className="ft-device-text">
                    <strong>{device.name}</strong>
                    <small>{device.code}</small>
                  </span>
                  <code className="ft-device-ip">
                    {device.ipAddress || t("fieldTools.devices.noIp")}
                  </code>
                  <button
                    type="button"
                    className="ft-device-test-btn"
                    disabled={pinging || !device.ipAddress}
                    onClick={(e) => {
                      e.stopPropagation();
                      if (!device.ipAddress) return;
                      setHost(device.ipAddress);
                      void runPing(device.ipAddress);
                    }}
                  >
                    <span className="material-symbols-outlined">network_ping</span>
                    {t("fieldTools.devices.test")}
                  </button>
                </div>
              ))
            )}
          </div>
        </div>
      </div>
    </section>
  );
}
