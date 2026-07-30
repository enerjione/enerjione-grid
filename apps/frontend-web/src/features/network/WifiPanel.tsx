/**
 * WifiPanel — Ag Ayarlari sayfasindaki WiFi bolumu.
 *
 * Appliance'i mevcut bir WiFi agina baglar (client/station modu). Erisim
 * noktasi (AP) buradan DEGISTIRILMEZ; o kurtarma yolu olarak korunur.
 *
 * TEK RADYO UYARISI:
 *   Cihazda tek WiFi karti var. Bir aga baglanirken AP DUSER. Baglanti
 *   kurulamazsa host ajani muhafiz suresi sonunda AP'yi otomatik geri acar;
 *   ayrica "bagli degilse AP acik" kurali her 30 sn'de dogrulanir. Bu yuzden
 *   kullanici hicbir senaryoda cihaza erisimini tamamen kaybetmez.
 *   Arayuz bunu baglanma oncesi acikca soyler ve muhafiz suresince geri
 *   sayim gosterir.
 */
import { useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import {
  Loader2,
  Lock,
  LockOpen,
  RefreshCw,
  ShieldAlert,
  Wifi,
  WifiOff,
  X
} from "lucide-react";

import { connectWifi, fetchWifiScan, forgetWifi, triggerWifiScan } from "../../shared/api";
import type { WifiNetwork, WifiScanResult, WifiState } from "../../shared/types";

type Props = {
  accessToken: string;
  wifi: WifiState | undefined;
  /** Ust sayfanin durum yenileyicisi — baglanti sonrasi tazelemek icin. */
  onRefreshStatus: () => void;
};

/** Sinyal yuzdesini 0-4 arasi cubuk sayisina cevir. */
function signalBars(signal: number): number {
  if (signal >= 75) return 4;
  if (signal >= 55) return 3;
  if (signal >= 35) return 2;
  if (signal >= 15) return 1;
  return 0;
}

function SignalIcon({ signal }: { signal: number }) {
  const bars = signalBars(signal);
  return (
    <span className="wifi-bars" title={`${signal}%`} aria-label={`${signal}%`}>
      {[1, 2, 3, 4].map((i) => (
        <i key={i} className={i <= bars ? "is-on" : undefined} style={{ height: 4 + i * 3 }} />
      ))}
    </span>
  );
}

export function WifiPanel({ accessToken, wifi, onRefreshStatus }: Props) {
  const { t } = useTranslation();
  const [scan, setScan] = useState<WifiScanResult | null>(null);
  const [scanning, setScanning] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // Baglanma dialogu
  const [target, setTarget] = useState<WifiNetwork | null>(null);
  const [psk, setPsk] = useState("");
  const [busy, setBusy] = useState(false);
  const [now, setNow] = useState(() => Date.now());

  const loadScan = async () => {
    try {
      setScan(await fetchWifiScan(accessToken));
    } catch {
      /* tarama yoksa sessiz gec */
    }
  };

  useEffect(() => {
    void loadScan();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [accessToken]);

  // Muhafiz geri sayimi icin saniyelik tick (yalnizca muhafiz aktifken).
  useEffect(() => {
    if (!wifi?.guard_active) return;
    const id = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(id);
  }, [wifi?.guard_active]);

  const guardLeft = useMemo(() => {
    if (!wifi?.guard_active || !wifi.guard_deadline) return null;
    return Math.max(0, Math.round(wifi.guard_deadline * 1000 - now) / 1000);
  }, [wifi?.guard_active, wifi?.guard_deadline, now]);

  const handleScan = async () => {
    setScanning(true);
    setError(null);
    try {
      await triggerWifiScan(accessToken);
      // Ajan path unit ile tetiklenir; sonuc birkac saniye icinde dosyaya duser.
      await new Promise((r) => setTimeout(r, 3500));
      await loadScan();
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : t("common.errorOccurred"));
    } finally {
      setScanning(false);
    }
  };

  const handleConnect = async () => {
    if (!target) return;
    setBusy(true);
    setError(null);
    try {
      await connectWifi(accessToken, target.ssid, target.secured ? psk : null);
      setTarget(null);
      setPsk("");
      onRefreshStatus();
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : t("common.errorOccurred"));
    } finally {
      setBusy(false);
    }
  };

  const handleForget = async () => {
    setBusy(true);
    setError(null);
    try {
      await forgetWifi(accessToken);
      onRefreshStatus();
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : t("common.errorOccurred"));
    } finally {
      setBusy(false);
    }
  };

  if (wifi && !wifi.supported) {
    return (
      <section className="net-card wifi-card">
        <header className="net-card-head">
          <WifiOff size={18} strokeWidth={2} />
          <h3>{t("network.wifi.title")}</h3>
        </header>
        <p className="net-hint">{t("network.wifi.noAdapter")}</p>
      </section>
    );
  }

  return (
    <section className="net-card wifi-card">
      <header className="net-card-head">
        <Wifi size={18} strokeWidth={2} />
        <h3>{t("network.wifi.title")}</h3>
        <button
          type="button"
          className="wifi-scan-btn"
          onClick={() => void handleScan()}
          disabled={scanning || busy}
        >
          {scanning ? (
            <Loader2 size={15} strokeWidth={2.2} className="net-spin" />
          ) : (
            <RefreshCw size={15} strokeWidth={2.2} />
          )}
          {scanning ? t("network.wifi.scanning") : t("network.wifi.scan")}
        </button>
      </header>

      {/* Bagli ag kunyesi */}
      {wifi?.connected ? (
        <div className="wifi-current is-connected">
          <span className="wifi-current-icon">
            <Wifi size={18} strokeWidth={2.2} />
          </span>
          <div className="wifi-current-body">
            <small>{t("network.wifi.connectedTo")}</small>
            <strong>{wifi.ssid ?? "—"}</strong>
            {wifi.addresses.length > 0 ? (
              <code>{wifi.addresses.join(", ")}</code>
            ) : null}
          </div>
          <button
            type="button"
            className="wifi-forget-btn"
            onClick={() => void handleForget()}
            disabled={busy}
          >
            {t("network.wifi.forget")}
          </button>
        </div>
      ) : (
        <div className="wifi-current">
          <span className="wifi-current-icon">
            <WifiOff size={18} strokeWidth={2.2} />
          </span>
          {/* AP adresi ve "nasil baglanirim" aciklamasi ustteki durum
              seridinde zaten duruyor; burada tekrar etmek listeyi
              gereksiz uzatiyordu. */}
          <div className="wifi-current-body">
            <small>{t("network.wifi.notConnected")}</small>
            <strong>{t("network.wifi.apActiveTitle")}</strong>
          </div>
        </div>
      )}

      {/* Muhafiz geri sayimi — baglanma denemesi surerken */}
      {guardLeft !== null ? (
        <div className="wifi-guard">
          <Loader2 size={16} strokeWidth={2.2} className="net-spin" />
          <div>
            <strong>{t("network.wifi.guardTitle", { ssid: wifi?.ssid ?? "" })}</strong>
            <span>
              {t("network.wifi.guardHint", { seconds: Math.ceil(guardLeft) })}
            </span>
          </div>
        </div>
      ) : null}

      {error ? <p className="net-error">{error}</p> : null}

      {/* Ag listesi */}
      {scan && scan.networks.length > 0 ? (
        <ul className="wifi-list">
          {scan.networks.map((n) => {
            const isCurrent = wifi?.connected && wifi.ssid === n.ssid;
            return (
              <li key={n.ssid} className={isCurrent ? "is-current" : undefined}>
                <SignalIcon signal={n.signal} />
                <span className="wifi-list-ssid">
                  {n.ssid}
                  {n.secured ? (
                    <Lock size={12} strokeWidth={2.4} />
                  ) : (
                    <LockOpen size={12} strokeWidth={2.4} />
                  )}
                </span>
                <span className="wifi-list-sec">
                  {n.secured ? n.security ?? "WPA" : t("network.wifi.open")}
                </span>
                {isCurrent ? (
                  <span className="wifi-list-badge">{t("network.wifi.current")}</span>
                ) : (
                  <button
                    type="button"
                    className="wifi-connect-btn"
                    onClick={() => {
                      setTarget(n);
                      setPsk("");
                      setError(null);
                    }}
                    disabled={busy}
                  >
                    {t("network.wifi.connect")}
                  </button>
                )}
              </li>
            );
          })}
        </ul>
      ) : (
        <p className="net-hint wifi-empty">
          {scan ? t("network.wifi.emptyScan") : t("network.wifi.notScanned")}
        </p>
      )}

      {/* Baglanma dialogu */}
      {target ? (
        <div className="net-modal-backdrop" onClick={() => !busy && setTarget(null)}>
          <div className="net-modal wifi-modal" onClick={(e) => e.stopPropagation()}>
            <header>
              <h4>{t("network.wifi.connectTo", { ssid: target.ssid })}</h4>
              <button type="button" onClick={() => setTarget(null)} disabled={busy}>
                <X size={18} strokeWidth={2.2} />
              </button>
            </header>

            {/* AP dusecek uyarisi — tek radyo gercegi bastan soylenir. */}
            <div className="wifi-warn">
              <ShieldAlert size={18} strokeWidth={2.1} />
              <div>
                <strong>{t("network.wifi.apDropTitle")}</strong>
                <span>{t("network.wifi.apDropHint")}</span>
              </div>
            </div>

            {target.secured ? (
              <label className="wifi-psk-label">
                {t("network.wifi.password")}
                <input
                  type="password"
                  value={psk}
                  onChange={(e) => setPsk(e.target.value)}
                  placeholder={t("network.wifi.passwordPlaceholder")}
                  autoFocus
                  minLength={8}
                  maxLength={63}
                />
                <small>{t("network.wifi.passwordHint")}</small>
              </label>
            ) : (
              <p className="net-hint">{t("network.wifi.openHint")}</p>
            )}

            <div className="net-modal-actions">
              <button type="button" onClick={() => setTarget(null)} disabled={busy}>
                {t("common.cancel")}
              </button>
              <button
                type="button"
                className="wifi-connect-confirm"
                onClick={() => void handleConnect()}
                disabled={busy || (target.secured && psk.length < 8)}
              >
                {busy ? t("network.wifi.connecting") : t("network.wifi.connect")}
              </button>
            </div>
          </div>
        </div>
      ) : null}
    </section>
  );
}
