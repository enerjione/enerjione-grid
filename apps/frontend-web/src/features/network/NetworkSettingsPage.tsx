/**
 * Ag Ayarlari — appliance (mini PC) modunda cihazin kendi IP/DNS ayari.
 *
 * Akis: mevcut durum goster -> kablolu arayuz icin DHCP/statik sec -> onay
 * modali (baglanti kopacak uyarisi) -> istek host ajanina kuyruklanir ->
 * cihaz yeniden baslar -> kullanici yeni adrese gider.
 *
 * Guvenlik agi: WiFi AP (EnerjiOne Grid) her zaman aciktir ve bu sayfadan
 * degistirilemez. Yanlis statik IP girilse bile cihaza AP uzerinden
 * http://e1-grid.local ile donulup duzeltilebilir — sayfa bunu her adimda
 * kullaniciya hatirlatir.
 *
 * Ikonografi: lucide-react (Sistem Durumu sayfasi ile ayni dil).
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import {
  AlertTriangle,
  Cable,
  Check,
  Copy,
  Globe,
  Info,
  Loader2,
  RefreshCw,
  RotateCw,
  ShieldAlert,
  Wifi
} from "lucide-react";

import { fetchNetworkStatus, updateNetworkConfig } from "../../shared/api";
import type {
  NetworkConfigAccepted,
  NetworkInterface,
  NetworkStatus
} from "../../shared/types";

type Props = {
  accessToken: string;
};

const REFRESH_INTERVAL_SEC = 10;
/** Reboot sonrasi tahmini acilis suresi — geri sayim bunun uzerinden isler. */
const REBOOT_COUNTDOWN_SEC = 75;

type Method = "dhcp" | "static";

type FormState = {
  ifname: string;
  method: Method;
  address: string;
  prefix: string;
  gateway: string;
  dns1: string;
  dns2: string;
};

const EMPTY_FORM: FormState = {
  ifname: "",
  method: "dhcp",
  address: "",
  prefix: "24",
  gateway: "",
  dns1: "",
  dns2: ""
};

// ---- IPv4 yardimcilari -----------------------------------------------------
function isIpv4(value: string): boolean {
  const parts = value.trim().split(".");
  if (parts.length !== 4) return false;
  return parts.every((part) => {
    if (!/^\d{1,3}$/.test(part)) return false;
    const n = Number(part);
    return n >= 0 && n <= 255;
  });
}

function ipToInt(value: string): number {
  return value
    .split(".")
    .reduce((acc, part) => (acc << 8) + Number(part), 0) >>> 0;
}

/** prefix -> "255.255.255.0" (kullaniciya tanidik gelen netmask gosterimi). */
function prefixToMask(prefix: number): string {
  if (!Number.isFinite(prefix) || prefix < 0 || prefix > 32) return "—";
  const mask = prefix === 0 ? 0 : (0xffffffff << (32 - prefix)) >>> 0;
  return [24, 16, 8, 0].map((shift) => (mask >>> shift) & 255).join(".");
}

function sameSubnet(a: string, b: string, prefix: number): boolean {
  if (!isIpv4(a) || !isIpv4(b) || prefix < 1 || prefix > 32) return false;
  const mask = prefix === 0 ? 0 : (0xffffffff << (32 - prefix)) >>> 0;
  return ((ipToInt(a) & mask) >>> 0) === ((ipToInt(b) & mask) >>> 0);
}

/** Arayuzun kalici modu: profil "manual" ise statik, degilse DHCP. */
function interfaceMethod(iface: NetworkInterface | undefined): Method {
  return iface?.method === "manual" ? "static" : "dhcp";
}

function splitCidr(cidr: string | undefined): { ip: string; prefix: string } {
  if (!cidr) return { ip: "", prefix: "24" };
  const [ip, prefix] = cidr.split("/");
  return { ip: ip ?? "", prefix: prefix ?? "24" };
}

export function NetworkSettingsPage({ accessToken }: Props) {
  const { t } = useTranslation();

  const [status, setStatus] = useState<NetworkStatus | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const inFlightRef = useRef(false);
  // Kullanici forma dokunduktan sonra polling formu EZMEMELI.
  const formTouchedRef = useRef(false);

  const [form, setForm] = useState<FormState>(EMPTY_FORM);
  const [formError, setFormError] = useState<string | null>(null);
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState<string | null>(null);
  const [accepted, setAccepted] = useState<NetworkConfigAccepted | null>(null);
  const [countdown, setCountdown] = useState(REBOOT_COUNTDOWN_SEC);
  const [copied, setCopied] = useState<string | null>(null);

  const ethernetInterfaces = useMemo(
    () => (status?.interfaces ?? []).filter((i) => i.type === "ethernet"),
    [status]
  );

  const selected = useMemo(
    () => ethernetInterfaces.find((i) => i.ifname === form.ifname),
    [ethernetInterfaces, form.ifname]
  );

  /** Secili arayuzun mevcut ayarlarini forma doldur. */
  const fillFromInterface = useCallback((iface: NetworkInterface) => {
    const current = splitCidr(iface.profile_addresses[0] ?? iface.addresses[0]);
    setForm({
      ifname: iface.ifname,
      method: interfaceMethod(iface),
      address: current.ip,
      prefix: current.prefix,
      gateway: iface.profile_gateway ?? iface.gateway ?? "",
      dns1: (iface.profile_dns[0] ?? iface.dns[0]) ?? "",
      dns2: (iface.profile_dns[1] ?? iface.dns[1]) ?? ""
    });
    setFormError(null);
  }, []);

  const load = useCallback(async () => {
    if (inFlightRef.current) return;
    inFlightRef.current = true;
    try {
      const next = await fetchNetworkStatus(accessToken);
      setStatus(next);
      setLoadError(null);
      // Ilk yuklemede formu mevcut ayarla doldur (kullanici dokunmadiysa).
      if (!formTouchedRef.current) {
        const eth = next.interfaces.filter((i) => i.type === "ethernet");
        const target = eth.find((i) => i.state === "connected") ?? eth[0];
        if (target) fillFromInterface(target);
      }
    } catch (exc) {
      const msg = exc instanceof Error ? exc.message : t("network.errors.load");
      if (msg !== "session_polling_401") setLoadError(msg);
    } finally {
      inFlightRef.current = false;
      setLoading(false);
    }
  }, [accessToken, fillFromInterface, t]);

  useEffect(() => {
    void load();
    const id = window.setInterval(() => void load(), REFRESH_INTERVAL_SEC * 1000);
    return () => window.clearInterval(id);
  }, [load]);

  // Yeniden baslatma geri sayimi.
  useEffect(() => {
    if (!accepted?.reboot) return;
    setCountdown(REBOOT_COUNTDOWN_SEC);
    const id = window.setInterval(() => {
      setCountdown((prev) => (prev <= 1 ? 0 : prev - 1));
    }, 1000);
    return () => window.clearInterval(id);
  }, [accepted]);

  const patch = (changes: Partial<FormState>) => {
    formTouchedRef.current = true;
    setForm((prev) => ({ ...prev, ...changes }));
    setFormError(null);
  };

  const copy = async (text: string) => {
    try {
      await navigator.clipboard.writeText(text);
      setCopied(text);
      window.setTimeout(() => setCopied(null), 1500);
    } catch {
      // pano erisimi yoksa sessizce gec — adres zaten ekranda yazili
    }
  };

  /** Gonderim oncesi dogrulama — backend ve host ajani ayni kurallari
   *  bagimsiz uygular; burasi sadece kullaniciyi erken uyarir. */
  const validate = (): string | null => {
    if (!form.ifname) return t("network.errors.pickInterface");
    if (form.method === "dhcp") return null;

    if (!isIpv4(form.address)) return t("network.errors.badAddress");
    const prefix = Number(form.prefix);
    if (!Number.isInteger(prefix) || prefix < 1 || prefix > 32) {
      return t("network.errors.badPrefix");
    }
    if (form.gateway) {
      if (!isIpv4(form.gateway)) return t("network.errors.badGateway");
      if (form.gateway === form.address) return t("network.errors.gatewayIsSelf");
      if (!sameSubnet(form.address, form.gateway, prefix)) {
        return t("network.errors.gatewayOtherSubnet");
      }
    }
    for (const dns of [form.dns1, form.dns2]) {
      if (dns && !isIpv4(dns)) return t("network.errors.badDns");
    }
    // AP alt agi ile cakisma cihazi her iki yoldan da erisilemez yapabilir.
    if (sameSubnet(form.address, "10.42.0.1", Math.min(prefix, 24))) {
      return t("network.errors.apSubnetClash");
    }
    return null;
  };

  const openConfirm = () => {
    const error = validate();
    if (error) {
      setFormError(error);
      return;
    }
    setSubmitError(null);
    setConfirmOpen(true);
  };

  const submit = async () => {
    setSubmitting(true);
    setSubmitError(null);
    try {
      const result = await updateNetworkConfig(accessToken, {
        ifname: form.ifname,
        method: form.method,
        address: form.method === "static" ? form.address : null,
        prefix: form.method === "static" ? Number(form.prefix) : null,
        gateway: form.method === "static" ? form.gateway || null : null,
        dns:
          form.method === "static"
            ? [form.dns1, form.dns2].filter((d) => d.trim().length > 0)
            : [],
        reboot: true
      });
      setConfirmOpen(false);
      setAccepted(result);
    } catch (exc) {
      setSubmitError(exc instanceof Error ? exc.message : t("network.errors.apply"));
    } finally {
      setSubmitting(false);
    }
  };

  const mdnsUrl = status?.mdns_name ? `http://${status.mdns_name}/` : null;
  const apUrl = status?.ap?.address ? `http://${status.ap.address}/` : null;

  // ---- Yeniden baslatma ekrani (her seyin onunde) --------------------------
  if (accepted) {
    return (
      <section className="net-page net-page--reboot">
        <div className="net-reboot-card">
          <div className="net-reboot-icon">
            <RotateCw size={40} className="net-spin" />
          </div>
          <h2>{t("network.reboot.title")}</h2>
          <p className="net-reboot-lead">{t("network.reboot.lead")}</p>
          <div className="net-reboot-countdown">
            <strong>{countdown}</strong>
            <span>{t("network.reboot.seconds")}</span>
          </div>
          <div className="net-reboot-links">
            {accepted.next_url ? (
              <a className="primary-btn" href={accepted.next_url}>
                {t("network.reboot.goNew", { url: accepted.next_url })}
              </a>
            ) : null}
            {mdnsUrl ? (
              <a className="secondary-btn" href={mdnsUrl}>
                {t("network.reboot.goMdns", { url: mdnsUrl })}
              </a>
            ) : null}
          </div>
          <p className="net-reboot-hint">
            <Wifi size={16} />
            {t("network.reboot.apHint", {
              ssid: status?.ap?.ssid ?? "EnerjiOne Grid",
              url: mdnsUrl ?? apUrl ?? ""
            })}
          </p>
        </div>
      </section>
    );
  }

  // ---- Appliance modu kurulu degil ----------------------------------------
  if (!loading && status && !status.available) {
    return (
      <section className="net-page">
        <div className="net-unavailable">
          <Info size={28} />
          <div>
            <h2>{t("network.unavailable.title")}</h2>
            <p>{t(`network.unavailable.reason.${status.reason ?? "unknown"}`)}</p>
            <p className="net-unavailable-cmd">
              <code className="inline-code">
                sudo bash infra/appliance/setup-appliance.sh
              </code>
            </p>
            <p className="helper-text">{t("network.unavailable.hint")}</p>
          </div>
        </div>
      </section>
    );
  }

  const busy = Boolean(status?.pending) || submitting;
  const lastFailed = status?.last_apply?.status === "failed";

  return (
    <section className="tab-panel net-page">
      {/* ---- Ust serit: erisim adresleri ----
          Uc ayri KPI karti yerine TEK satir: bunlar birbirinden bagimsiz
          metrikler degil, "cihaza nasil ulasilir"in uc yolu. Tek serit hem
          daha az dikey yer kaplar hem de diger sayfalardaki cubuk diliyle
          ayni durur. */}
      <div className="net-access-bar">
        <div className="net-access-item">
          <span className="net-access-icon">
            <Globe size={16} />
          </span>
          <span className="net-access-body">
            <span className="net-access-label">{t("network.access.deviceAddress")}</span>
            <strong className="net-access-value">{status?.mdns_name ?? "—"}</strong>
          </span>
          {mdnsUrl ? (
            <button
              type="button"
              className="net-copy-btn"
              onClick={() => void copy(mdnsUrl)}
              title={t("network.access.copy")}
            >
              {copied === mdnsUrl ? <Check size={15} /> : <Copy size={15} />}
            </button>
          ) : null}
        </div>

        <span className="net-access-sep" aria-hidden="true" />

        <div className={`net-access-item ${status?.ap?.active ? "is-ok" : "is-warn"}`}>
          <span className="net-access-icon">
            <Wifi size={16} />
          </span>
          <span className="net-access-body">
            <span className="net-access-label">{t("network.access.wifi")}</span>
            <strong className="net-access-value">
              {status?.ap?.active
                ? status?.ap?.address ?? status?.ap?.ssid ?? "—"
                : t("network.access.wifiInactive")}
            </strong>
          </span>
          {status?.ap?.exists && !status.ap.secured ? (
            <span className="net-chip net-chip--open">{t("network.access.wifiOpen")}</span>
          ) : null}
        </div>

        <span className="net-access-sep" aria-hidden="true" />

        <div className="net-access-item">
          <span className="net-access-icon">
            <Cable size={16} />
          </span>
          <span className="net-access-body">
            <span className="net-access-label">{t("network.access.wired")}</span>
            <strong className="net-access-value">
              {selected?.addresses[0] ?? t("network.access.noAddress")}
            </strong>
          </span>
          {selected ? (
            <span className="net-chip">
              {interfaceMethod(selected) === "static"
                ? t("network.method.static")
                : t("network.method.dhcp")}
            </span>
          ) : null}
        </div>
      </div>

      {loadError ? <p className="error-text">{loadError}</p> : null}

      {status?.reason === "state_stale" ? (
        <p className="net-banner net-banner--warn">
          <AlertTriangle size={16} />
          {t("network.staleAgent")}
        </p>
      ) : null}

      {lastFailed ? (
        <p className="net-banner net-banner--bad">
          <ShieldAlert size={16} />
          {t("network.lastApplyFailed", { error: status?.last_apply?.error ?? "" })}
        </p>
      ) : null}

      {status?.pending ? (
        <p className="net-banner net-banner--info">
          <Loader2 size={16} className="net-spin" />
          {t("network.pending")}
        </p>
      ) : null}

      {/* ---- Ayar formu ---- */}
      <section className="net-card">
        <header className="net-card-head">
          {/* Aciklama metni kaldirildi — "cihazin IP'sini degistirir,
              yeniden baslar" uyarisi zaten kaydet oncesi onay modalinda
              cikiyor; burada tekrar etmek gurultu. */}
          <h2>{t("network.form.title")}</h2>
          <button
            type="button"
            className="secondary-btn net-refresh"
            onClick={() => void load()}
            disabled={loading}
          >
            <RefreshCw size={16} className={loading ? "net-spin" : ""} />
            {t("common.refresh")}
          </button>
        </header>

        {ethernetInterfaces.length === 0 ? (
          <p className="net-empty">{t("network.form.noEthernet")}</p>
        ) : (
          <div className="net-form">
            {/* Arayuz secimi — birden fazla kablolu kart varsa anlamli */}
            <label className="net-field">
              <span>{t("network.form.interface")}</span>
              <select
                value={form.ifname}
                disabled={busy}
                onChange={(e) => {
                  const iface = ethernetInterfaces.find((i) => i.ifname === e.target.value);
                  formTouchedRef.current = true;
                  if (iface) fillFromInterface(iface);
                }}
              >
                {ethernetInterfaces.map((iface) => (
                  <option key={iface.ifname} value={iface.ifname}>
                    {iface.ifname}
                    {iface.addresses[0] ? ` — ${iface.addresses[0]}` : ""}
                    {iface.state === "connected" ? "" : ` (${t("network.form.noLink")})`}
                  </option>
                ))}
              </select>
            </label>

            {/* DHCP / Statik secimi */}
            <div className="net-field">
              <span>{t("network.form.method")}</span>
              <div className="net-segment">
                <button
                  type="button"
                  className={form.method === "dhcp" ? "is-active" : ""}
                  disabled={busy}
                  onClick={() => patch({ method: "dhcp" })}
                >
                  {t("network.method.dhcp")}
                </button>
                <button
                  type="button"
                  className={form.method === "static" ? "is-active" : ""}
                  disabled={busy}
                  onClick={() => patch({ method: "static" })}
                >
                  {t("network.method.static")}
                </button>
              </div>
            </div>

            {form.method === "static" ? (
              <div className="net-static-grid">
                <label className="net-field">
                  <span>{t("network.form.address")}</span>
                  <input
                    value={form.address}
                    disabled={busy}
                    placeholder="192.168.1.50"
                    inputMode="numeric"
                    onChange={(e) => patch({ address: e.target.value.trim() })}
                  />
                </label>

                <label className="net-field">
                  <span>{t("network.form.prefix")}</span>
                  <input
                    value={form.prefix}
                    disabled={busy}
                    placeholder="24"
                    inputMode="numeric"
                    onChange={(e) => patch({ prefix: e.target.value.trim() })}
                  />
                  <small className="helper-text">
                    {t("network.form.netmask", { mask: prefixToMask(Number(form.prefix)) })}
                  </small>
                </label>

                <label className="net-field">
                  <span>{t("network.form.gateway")}</span>
                  <input
                    value={form.gateway}
                    disabled={busy}
                    placeholder="192.168.1.1"
                    inputMode="numeric"
                    onChange={(e) => patch({ gateway: e.target.value.trim() })}
                  />
                </label>

                <label className="net-field">
                  <span>{t("network.form.dns1")}</span>
                  <input
                    value={form.dns1}
                    disabled={busy}
                    placeholder="8.8.8.8"
                    inputMode="numeric"
                    onChange={(e) => patch({ dns1: e.target.value.trim() })}
                  />
                </label>

                <label className="net-field">
                  <span>{t("network.form.dns2")}</span>
                  <input
                    value={form.dns2}
                    disabled={busy}
                    placeholder="1.1.1.1"
                    inputMode="numeric"
                    onChange={(e) => patch({ dns2: e.target.value.trim() })}
                  />
                </label>
              </div>
            ) : null}

            {formError ? <p className="error-text">{formError}</p> : null}
            {submitError ? <p className="error-text">{submitError}</p> : null}

            <div className="net-form-actions">
              <button
                type="button"
                className="primary-btn"
                disabled={busy || !form.ifname}
                onClick={openConfirm}
              >
                {t("network.form.save")}
              </button>
            </div>
          </div>
        )}
      </section>

      {/* ---- Onay modali ---- */}
      {confirmOpen ? (
        <div
          className="settings-modal-backdrop"
          onClick={() => !submitting && setConfirmOpen(false)}
        >
          <div
            className="settings-modal net-confirm"
            role="dialog"
            aria-modal="true"
            onClick={(e) => e.stopPropagation()}
          >
            <h3>
              <AlertTriangle size={20} />
              {t("network.confirm.title")}
            </h3>
            <p>{t("network.confirm.lead")}</p>

            <ul className="net-confirm-summary">
              <li>
                <span>{t("network.form.interface")}</span>
                <strong>{form.ifname}</strong>
              </li>
              <li>
                <span>{t("network.form.method")}</span>
                <strong>
                  {form.method === "dhcp" ? t("network.method.dhcp") : t("network.method.static")}
                </strong>
              </li>
              {form.method === "static" ? (
                <>
                  <li>
                    <span>{t("network.form.address")}</span>
                    <strong>
                      {form.address}/{form.prefix}
                    </strong>
                  </li>
                  <li>
                    <span>{t("network.form.gateway")}</span>
                    <strong>{form.gateway || "—"}</strong>
                  </li>
                  <li>
                    <span>DNS</span>
                    <strong>{[form.dns1, form.dns2].filter(Boolean).join(", ") || "—"}</strong>
                  </li>
                </>
              ) : null}
            </ul>

            <p className="net-confirm-warn">
              <Wifi size={16} />
              {t("network.confirm.apSafety", {
                ssid: status?.ap?.ssid ?? "EnerjiOne Grid",
                host: status?.mdns_name ?? "e1-grid.local"
              })}
            </p>

            <div className="net-confirm-actions">
              <button
                type="button"
                className="secondary-btn"
                disabled={submitting}
                onClick={() => setConfirmOpen(false)}
              >
                {t("common.cancel")}
              </button>
              <button
                type="button"
                className="danger-btn"
                disabled={submitting}
                onClick={() => void submit()}
              >
                {submitting ? t("network.confirm.applying") : t("network.confirm.apply")}
              </button>
            </div>
          </div>
        </div>
      ) : null}
    </section>
  );
}
