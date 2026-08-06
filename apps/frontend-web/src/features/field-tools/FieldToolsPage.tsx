/**
 * Saha Araclari — Cihaz Ayarlari menusu altinda diagnostik sayfa.
 *
 * Araclar: ping, TCP port kontrolu, traceroute, DNS cozumleme + kayitli
 * cihazlarda toplu tarama. Testler SISTEM uzerinden (backend) kosulur,
 * tarayicidan degil — kullanicinin kendi agindan erisilebilen bir cihaz
 * sahadan erisilemiyor olabilir.
 *
 * Sol kart: arac secimi (sekmeli) + hedef + sonuc. Sag kart: kayitli
 * cihazlar; satira tiklaninca IP hedefe yazilir, "Tumunu Tara" tum
 * cihazlara parca parca (<=20 id/istek) ping + DNP3 port testi kosar.
 * Yetki: installer/engineer (backend de ayni).
 */
import { useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";

import { useToast } from "../../components/ToastProvider";
import {
  checkFieldPort,
  fetchDevices,
  pingFieldHost,
  resolveFieldDns,
  scanFieldDevices,
  traceFieldRoute,
} from "../../shared/api";
import type {
  DeviceRow,
  DeviceScanResult,
  DnsResult,
  PingResult,
  PortCheckResult,
  TracerouteResult,
} from "../../shared/types";

type Props = {
  accessToken: string;
};

type Tool = "ping" | "port" | "trace" | "dns";

const TOOLS: Tool[] = ["ping", "port", "trace", "dns"];
const PACKET_COUNTS = [1, 4, 8, 10];
// Toplu taramada istek basina cihaz sayisi (backend siniri 50; 20 ile
// ilerleme cubugu daha akici guncellenir).
const SCAN_CHUNK = 20;

function formatMs(value: number | null): string {
  return value === null ? "—" : String(Math.round(value * 100) / 100);
}

export function FieldToolsPage({ accessToken }: Props) {
  const { t } = useTranslation();
  const toast = useToast();

  const [tool, setTool] = useState<Tool>("ping");
  const [host, setHost] = useState("");
  const [count, setCount] = useState(4);
  const [port, setPort] = useState("20001");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const [pingResult, setPingResult] = useState<PingResult | null>(null);
  const [portResult, setPortResult] = useState<PortCheckResult | null>(null);
  const [traceResult, setTraceResult] = useState<TracerouteResult | null>(null);
  const [dnsResult, setDnsResult] = useState<DnsResult | null>(null);

  const [devices, setDevices] = useState<DeviceRow[]>([]);
  const [devicesLoading, setDevicesLoading] = useState(true);
  const [search, setSearch] = useState("");

  // Toplu tarama durumu. cancelRef: "Durdur" sonraki parcayi engeller
  // (kosan istek tamamlanir — backend'te iptal mekanizmasi yok).
  const [scanResults, setScanResults] = useState<Map<number, DeviceScanResult>>(
    new Map()
  );
  const [scanning, setScanning] = useState(false);
  const [scanProgress, setScanProgress] = useState({ done: 0, total: 0 });
  const cancelRef = useRef(false);

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

  // Sayfadan cikarken suren taramayi durdur.
  useEffect(() => {
    return () => {
      cancelRef.current = true;
    };
  }, []);

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

  const clearResults = () => {
    setPingResult(null);
    setPortResult(null);
    setTraceResult(null);
    setDnsResult(null);
    setError(null);
  };

  const runTool = async (activeTool: Tool, target: string) => {
    const trimmed = target.trim();
    if (!trimmed) {
      setError(t("fieldTools.invalidHost"));
      return;
    }
    const portNum = Number(port);
    if (activeTool === "port" && (!Number.isInteger(portNum) || portNum < 1 || portNum > 65535)) {
      setError(t("fieldTools.port.invalidPort"));
      return;
    }
    setBusy(true);
    clearResults();
    try {
      if (activeTool === "ping") {
        const res = await pingFieldHost(accessToken, trimmed, count);
        setPingResult(res);
        if (res.success) {
          toast.success(
            t("fieldTools.ping.successToast", { host: res.host, avg: formatMs(res.rttAvgMs) })
          );
        } else {
          toast.error(t("fieldTools.ping.failToast", { host: res.host }));
        }
      } else if (activeTool === "port") {
        const res = await checkFieldPort(accessToken, trimmed, portNum);
        setPortResult(res);
      } else if (activeTool === "trace") {
        const res = await traceFieldRoute(accessToken, trimmed);
        setTraceResult(res);
      } else {
        const res = await resolveFieldDns(accessToken, trimmed);
        setDnsResult(res);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : t("common.errorOccurred"));
    } finally {
      setBusy(false);
    }
  };

  const handleDevicePick = (device: DeviceRow) => {
    if (!device.ipAddress) return;
    setHost(device.ipAddress);
    clearResults();
  };

  const runScan = async () => {
    const targets = devices.filter((d) => d.ipAddress);
    if (targets.length === 0) return;
    cancelRef.current = false;
    setScanning(true);
    setScanResults(new Map());
    setScanProgress({ done: 0, total: targets.length });
    try {
      for (let i = 0; i < targets.length; i += SCAN_CHUNK) {
        if (cancelRef.current) break;
        const chunk = targets.slice(i, i + SCAN_CHUNK);
        const results = await scanFieldDevices(
          accessToken,
          chunk.map((d) => d.id)
        );
        setScanResults((prev) => {
          const next = new Map(prev);
          for (const r of results) next.set(r.deviceId, r);
          return next;
        });
        setScanProgress({
          done: Math.min(i + chunk.length, targets.length),
          total: targets.length,
        });
      }
    } catch (err) {
      toast.error(err instanceof Error ? err.message : t("common.errorOccurred"));
    } finally {
      setScanning(false);
    }
  };

  const stopScan = () => {
    cancelRef.current = true;
  };

  return (
    <section className="tab-panel ft-page">
      <div className="ft-grid">
        {/* --- Test karti --- */}
        <div className="ft-card">
          <div className="ft-tools" role="tablist">
            {TOOLS.map((key) => (
              <button
                key={key}
                type="button"
                role="tab"
                aria-selected={tool === key}
                className={`ft-tool-tab${tool === key ? " active" : ""}`}
                onClick={() => {
                  setTool(key);
                  clearResults();
                }}
              >
                {t(`fieldTools.tools.${key}`)}
              </button>
            ))}
          </div>

          <p className="ft-tool-hint">{t(`fieldTools.hints.${tool}`)}</p>

          <form
            className="ft-ping-form"
            onSubmit={(e) => {
              e.preventDefault();
              void runTool(tool, host);
            }}
          >
            <label className="ft-field ft-field--host">
              <span>{t("fieldTools.hostLabel")}</span>
              <input
                type="text"
                value={host}
                placeholder={t("fieldTools.hostPlaceholder")}
                onChange={(e) => setHost(e.target.value)}
                disabled={busy}
                spellCheck={false}
                autoComplete="off"
              />
            </label>
            {tool === "ping" ? (
              <label className="ft-field ft-field--count">
                <span>{t("fieldTools.ping.countLabel")}</span>
                <select
                  value={count}
                  onChange={(e) => setCount(Number(e.target.value))}
                  disabled={busy}
                >
                  {PACKET_COUNTS.map((n) => (
                    <option key={n} value={n}>
                      {n}
                    </option>
                  ))}
                </select>
              </label>
            ) : null}
            {tool === "port" ? (
              <label className="ft-field ft-field--count">
                <span>{t("fieldTools.port.portLabel")}</span>
                <input
                  type="number"
                  min={1}
                  max={65535}
                  value={port}
                  onChange={(e) => setPort(e.target.value)}
                  disabled={busy}
                />
              </label>
            ) : null}
            <button type="submit" className="primary-btn ft-ping-btn" disabled={busy}>
              {busy ? t("fieldTools.running") : t(`fieldTools.run.${tool}`)}
            </button>
          </form>

          {error ? <p className="error-text">{error}</p> : null}

          {tool === "ping" && pingResult ? (
            <div className="ft-result">
              <div className={`ft-result-badge ${pingResult.success ? "is-ok" : "is-fail"}`}>
                {pingResult.success
                  ? t("fieldTools.ping.reachable")
                  : t("fieldTools.ping.unreachable")}
                <code>{pingResult.host}</code>
              </div>
              <dl className="ft-result-stats">
                <div>
                  <dt>{t("fieldTools.ping.packets")}</dt>
                  <dd>
                    {t("fieldTools.ping.packetsValue", {
                      received: pingResult.packetsReceived,
                      sent: pingResult.packetsSent,
                    })}
                  </dd>
                </div>
                <div>
                  <dt>{t("fieldTools.ping.loss")}</dt>
                  <dd>%{pingResult.packetLossPercent}</dd>
                </div>
                <div>
                  <dt>{t("fieldTools.ping.rtt")}</dt>
                  <dd>
                    {t("fieldTools.ping.rttValue", {
                      min: formatMs(pingResult.rttMinMs),
                      avg: formatMs(pingResult.rttAvgMs),
                      max: formatMs(pingResult.rttMaxMs),
                    })}
                  </dd>
                </div>
                <div>
                  <dt>{t("fieldTools.ping.duration")}</dt>
                  <dd>
                    {t("fieldTools.ping.durationValue", {
                      s: (pingResult.durationMs / 1000).toFixed(1),
                    })}
                  </dd>
                </div>
              </dl>
              <details className="ft-raw">
                <summary>{t("fieldTools.rawOutput")}</summary>
                <pre>{pingResult.output}</pre>
              </details>
            </div>
          ) : null}

          {tool === "port" && portResult ? (
            <div className="ft-result">
              <div className={`ft-result-badge ${portResult.open ? "is-ok" : "is-fail"}`}>
                {portResult.open
                  ? t("fieldTools.port.open", { port: portResult.port })
                  : t("fieldTools.port.closed", { port: portResult.port })}
                <code>{portResult.host}</code>
              </div>
              <dl className="ft-result-stats">
                <div>
                  <dt>{t("fieldTools.ping.duration")}</dt>
                  <dd>{portResult.elapsedMs} ms</dd>
                </div>
                {portResult.error ? (
                  <div>
                    <dt>{t("fieldTools.port.reason")}</dt>
                    <dd>{portResult.error}</dd>
                  </div>
                ) : null}
              </dl>
            </div>
          ) : null}

          {tool === "trace" && traceResult ? (
            <div className="ft-result">
              <div className="ft-result-badge is-neutral">
                {t("fieldTools.trace.done", {
                  s: (traceResult.durationMs / 1000).toFixed(1),
                })}
                <code>{traceResult.host}</code>
              </div>
              <pre className="ft-trace-output">{traceResult.output}</pre>
            </div>
          ) : null}

          {tool === "dns" && dnsResult ? (
            <div className="ft-result">
              <div className={`ft-result-badge ${dnsResult.resolved ? "is-ok" : "is-fail"}`}>
                {dnsResult.resolved
                  ? t("fieldTools.dns.resolved", { ms: dnsResult.elapsedMs })
                  : t("fieldTools.dns.unresolved")}
                <code>{dnsResult.name}</code>
              </div>
              {dnsResult.addresses.length > 0 ? (
                <div className="ft-dns-addresses">
                  {dnsResult.addresses.map((addr) => (
                    <code key={addr}>{addr}</code>
                  ))}
                </div>
              ) : null}
            </div>
          ) : null}
        </div>

        {/* --- Kayitli cihazlar karti --- */}
        <div className="ft-card ft-card--devices">
          <div className="ft-card-head">
            <h3>{t("fieldTools.devices.cardTitle")}</h3>
            <small>{t("fieldTools.devices.hint")}</small>
          </div>

          <div className="ft-scan-bar">
            <input
              type="search"
              className="ft-device-search"
              placeholder={t("fieldTools.devices.searchPlaceholder")}
              value={search}
              onChange={(e) => setSearch(e.target.value)}
            />
            {scanning ? (
              <button type="button" className="ft-scan-btn is-stop" onClick={stopScan}>
                {t("fieldTools.scan.stop")}
              </button>
            ) : (
              <button
                type="button"
                className="ft-scan-btn"
                onClick={() => void runScan()}
                disabled={devicesLoading || devices.every((d) => !d.ipAddress)}
              >
                {t("fieldTools.scan.run")}
              </button>
            )}
          </div>

          {scanning || scanProgress.total > 0 ? (
            <div className="ft-scan-progress">
              <div className="ft-scan-progress-track">
                <div
                  className="ft-scan-progress-fill"
                  style={{
                    width:
                      scanProgress.total > 0
                        ? `${(scanProgress.done / scanProgress.total) * 100}%`
                        : "0%",
                  }}
                />
              </div>
              <span>
                {t("fieldTools.scan.progress", {
                  done: scanProgress.done,
                  total: scanProgress.total,
                })}
              </span>
            </div>
          ) : null}

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
              filteredDevices.map((device) => {
                const scan = scanResults.get(device.id);
                return (
                  <div
                    key={device.id}
                    className={`ft-device-row ${device.ipAddress ? "" : "is-disabled"}`}
                    onClick={() => handleDevicePick(device)}
                  >
                    <span className="ft-device-text">
                      <strong>{device.name}</strong>
                      <small>{device.code}</small>
                    </span>
                    {scan ? (
                      <span className="ft-scan-badges">
                        {scan.error === "no_ip" ? (
                          <span className="ft-scan-badge is-fail">
                            {t("fieldTools.devices.noIp")}
                          </span>
                        ) : (
                          <>
                            <span
                              className={`ft-scan-badge ${scan.pingSuccess ? "is-ok" : "is-fail"}`}
                              title={
                                scan.pingSuccess && scan.rttAvgMs !== null
                                  ? `${formatMs(scan.rttAvgMs)} ms`
                                  : undefined
                              }
                            >
                              {scan.pingSuccess
                                ? t("fieldTools.scan.pingOk")
                                : t("fieldTools.scan.pingFail")}
                            </span>
                            <span
                              className={`ft-scan-badge ${scan.portOpen ? "is-ok" : "is-fail"}`}
                            >
                              {scan.portOpen
                                ? t("fieldTools.scan.portOpen", { port: scan.port })
                                : t("fieldTools.scan.portClosed", { port: scan.port })}
                            </span>
                          </>
                        )}
                      </span>
                    ) : null}
                    <code className="ft-device-ip">
                      {device.ipAddress || t("fieldTools.devices.noIp")}
                    </code>
                    <button
                      type="button"
                      className="ft-device-test-btn"
                      disabled={busy || !device.ipAddress}
                      onClick={(e) => {
                        e.stopPropagation();
                        if (!device.ipAddress) return;
                        setHost(device.ipAddress);
                        void runTool(tool, device.ipAddress);
                      }}
                    >
                      {t("fieldTools.devices.test")}
                    </button>
                  </div>
                );
              })
            )}
          </div>
        </div>
      </div>
    </section>
  );
}
