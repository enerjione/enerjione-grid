/**
 * Ag Ayarlari — appliance (mini PC) modunda cihazin kendi IP/DNS ayari.
 *
 * Akis: mevcut durum goster -> kablolu arayuz icin DHCP/statik sec -> onay
 * modali (baglanti kopacak uyarisi) -> istek host ajanina kuyruklanir ->
 * cihaz yeniden baslar -> kullanici yeni adrese gider.
 *
 * Ust serit DORT ayri seyi birbirine karistirmadan soyler:
 *   cihaz adresi | erisim noktasi (AP) | INTERNET | kablolu baglanti
 * "AP acik" ile "internet var" BAMBASKA seylerdir: AP cihaza ulasmayi saglar
 * ama internet vermez. Kullanici guncelleme / uzaktan bakim / saat senkronunun
 * calisip calismayacagini bilmek zorunda oldugu icin internet AYRI okunur.
 *
 * DEGISMEZ KURAL: her deger cihazin BILDIRDIGI olcumdur. Bilinmiyorsa
 * "Bilinmiyor" yazilir ve rozet ne yesil ne kirmizi olur.
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
  Globe2,
  Info,
  Loader2,
  RefreshCw,
  RotateCw,
  ShieldAlert,
  Wifi
} from "lucide-react";

import { checkInternet, fetchNetworkStatus, updateNetworkConfig } from "../../shared/api";
import type {
  NetworkConfigAccepted,
  NetworkInterface,
  NetworkStatus
} from "../../shared/types";

type Props = {
  accessToken: string;
};

import { WifiPanel } from "./WifiPanel";
import { useToast } from "../../components/ToastProvider";
import { usePolling } from "../../shared/usePolling";
import {
  hasLayers,
  internetImpactKey,
  internetLabelKey,
  internetOf,
  internetTone,
  roleOf
} from "./networkAccess";

const REFRESH_INTERVAL_SEC = 10;
/**
 * Bir islem SURERKEN yoklama araligi (ms).
 *
 * NEDEN: "AP yayinini acip kapatmak cok uzun suruyor" diye bildirildi.
 * Olculdugunde surenin buyuk kismi isin KENDISI degil, HABERIN GECIKMESI
 * cikti: 10 saniyelik yoklama yuzunden 2 saniyede biten bir gorev degisimi
 * ekranda 10 saniyeye kadar "suruyor" gorunuyordu.
 *
 * Yalnizca islem suresince hizlanip normale donuyoruz; surekli 1.5 sn'de bir
 * yoklamak cihazi bosuna yorardi (ajan her istekte nmcli kosuyor).
 */
const BUSY_REFRESH_MS = 1500;
/**
 * Istegi gonderdikten sonra ajanin `pending` bayragini kaldirmasi bir tur
 * surebilir. O aralikta hizli moddan cikarsak kullanici yine 10 saniye
 * beklerdi; bu pencere boyunca hizli kaliyoruz.
 */
const BUSY_GRACE_MS = 30_000;
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
  const toast = useToast();

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
  const [accepted, setAccepted] = useState<NetworkConfigAccepted | null>(null);
  const [countdown, setCountdown] = useState(REBOOT_COUNTDOWN_SEC);
  const [copied, setCopied] = useState<string | null>(null);
  const [internetChecking, setInternetChecking] = useState(false);

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
      if (msg !== "session_polling_401") {
        // Yalnizca DURUM DEGISTIGINDE toast: yoklama periyodik, her turda
        // toast atmak ekrani doldurur ve gercek uyarilari gomerdi.
        setLoadError((onceki) => {
          if (onceki !== msg) toast.error(msg);
          return msg;
        });
      }
    } finally {
      inFlightRef.current = false;
      setLoading(false);
    }
  }, [accessToken, fillFromInterface, t]);

  // Kullanici bir islem tetikledi mi? `onRefreshStatus` zaten her islemden
  // SONRA cagriliyor; ayri bir prop eklemeden o ani isaretliyoruz.
  const [sonIslemAt, setSonIslemAt] = useState(0);
  const refreshStatus = useCallback(() => {
    setSonIslemAt(Date.now());
    void load();
  }, [load]);

  // Islem suruyorsa sik, aksi halde normal yoklama. `status.pending` ajanin
  // olctugu gercek durum; grace penceresi yalnizca bayrak henuz kalkmadiysa
  // devreye girer.
  const islemSuruyor =
    Boolean(status?.pending) || (sonIslemAt > 0 && Date.now() - sonIslemAt < BUSY_GRACE_MS);

  usePolling({
    enabled: true,
    intervalMs: islemSuruyor ? BUSY_REFRESH_MS : REFRESH_INTERVAL_SEC * 1000,
    fn: load,
  });

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
    setConfirmOpen(true);
  };

  const submit = async () => {
    setSubmitting(true);
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
      // EYLEM hatasi toast'a. `formError` BILEREK satir ici kaldi:
      // dogrulama mesaji, kullanici alani duzeltene kadar gorunmeli;
      // kaybolan bir dogrulama uyarisi kullaniciyi kor birakir.
      toast.error(exc instanceof Error ? exc.message : t("network.errors.apply"));
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
          {/* Yalnizca AP GERCEKTEN yayindayken "bu aga baglanin" denir. */}
          {status?.ap?.active ? (
            <p className="net-reboot-hint">
              <Wifi size={16} />
              {t("network.reboot.apHint", {
                ssid: status?.ap?.ssid ?? "EnerjiOne Grid",
                url: mdnsUrl ?? apUrl ?? ""
              })}
            </p>
          ) : null}
        </div>
      </section>
    );
  }

  // Ag ajani (e1-netd) kurulu olmasa da SAYFAYI GOSTERIYORUZ.
  // Onceden burada tam sayfalik bir "appliance modu kurulu degil" bilgisi
  // vardi ve kablolu/WiFi alanlari hic gorunmuyordu. Kullanici sayfaya
  // baktiginda ne yapabilecegini goremiyordu. Artik normal duzen cikiyor;
  // ajan yoksa uygulama denemesi zaten hata dondurup toast gosteriyor.
  const busy = Boolean(status?.pending) || submitting;
  const lastFailed = status?.last_apply?.status === "failed";

  // ---- Ust seridin OLCULEN girdileri --------------------------------------
  const internet = internetOf(status);
  const role = roleOf(status);
  const internetImpact = internetImpactKey(internet);
  const netTone = internetTone(internet);
  // AP kapali ama gorev zaten "internete baglan" ise bu BEKLENEN bir durum;
  // uyari tonu vermek yanlis alarm olur. Gorev bilinmiyorsa uyar.
  const apTone = status?.ap?.active
    ? "is-ok"
    : role?.mode === "client"
      ? ""
      : "is-warn";
  // Ajan durum bildirmeyeli cok olduysa yesil/kirmizi noktalar guven vermesin.
  const staleBar = status?.reason === "state_stale";

  const runInternetCheck = async () => {
    setInternetChecking(true);
    try {
      await checkInternet(accessToken);
      // Ajan sonucu birkac saniye icinde state.json'a yazar.
      await new Promise((r) => setTimeout(r, 4000));
      await load();
    } catch {
      // Hata mesaji seritte yer kaplamasin; bir sonraki yoklama zaten
      // gercek durumu getirir.
    } finally {
      setInternetChecking(false);
    }
  };

  return (
    <section className="tab-panel net-page">
      {/* ---- Ust serit: cihazin disariyla iliskisi ----
          Dort oge: cihaz adresi | erisim noktasi | internet | kablolu.
          Erisim noktasi ile internet AYRI: ilki cihaza ulasmayi saglar,
          ikincisi guncelleme/uzaktan bakim/saat senkronunu belirler. */}
      <div className={`net-access-bar ${staleBar ? "is-stale" : ""}`}
        title={staleBar ? t("network.staleAgent") : undefined}
      >
        {/* BAGLANTI YOK isareti — seridin ICINDE, ayri bir satirda DEGIL.
            Eskiden seridin altina bir <p> ekleniyordu ve altindaki iki kart
            asagi kayiyordu. Burada yalnizca serit genisler, dikey akis
            bozulmaz.

            Toast olayi duyurur ama KAYBOLUR; bu isaret durum surdugu surece
            kalir. Aksi halde "neden her sey Bilinmiyor" sorusu cevapsiz
            kalirdi. */}
        {loadError ? (
          <>
            <div className="net-access-item is-bad" title={loadError}>
              <span className="net-access-icon">
                <AlertTriangle size={16} />
              </span>
              <span className="net-access-body">
                <span className="net-access-label">{t("network.access.linkLabel")}</span>
                <strong className="net-access-value">{t("network.access.linkDown")}</strong>
              </span>
            </div>
            <span className="net-access-sep" aria-hidden="true" />
          </>
        ) : null}

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

        {/* Erisim noktasi — YALNIZCA olculen ap.active. Kuraldan ("client
            yoksa AP acik olmali") durum TURETILMEZ. */}
        <div className={`net-access-item ${apTone}`}>
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
          {status?.ap?.active && status.ap.exists && !status.ap.secured ? (
            <span className="net-chip net-chip--open">{t("network.access.wifiOpen")}</span>
          ) : null}
        </div>

        <span className="net-access-sep" aria-hidden="true" />

        {/* Internet — "AP acik" ile ayni sey DEGIL. */}
        <div className={`net-access-item ${netTone ? `is-${netTone}` : ""}`}>
          <span className="net-access-icon">
            <Globe2 size={16} />
          </span>
          <span className="net-access-body">
            <span className="net-access-label">{t("network.access.internet")}</span>
            {/* Metin TEK anahtardan gelir; "Var" + " — " + "kablolu" gibi
                parca birlestirme ceviride kirilir. */}
            <strong className="net-access-value">{t(internetLabelKey(internet))}</strong>
          </span>
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


      {/* Internet kotu/bilinmiyorken NE ETKILENIR — serit icine sigmaz,
          tooltip dokunmatikte calismaz. Internet varken banner cikmaz. */}
      {status?.available && hasLayers(status) && internetImpact ? (
        <p className="net-banner net-banner--warn net-banner--action">
          <AlertTriangle size={16} />
          <span>{t(internetImpact)}</span>
          <button
            type="button"
            className="net-banner-btn"
            disabled={internetChecking}
            onClick={() => void runInternetCheck()}
          >
            {internetChecking ? t("network.internet.checking") : t("network.internet.check")}
          </button>
        </p>
      ) : null}

      {/* Ajan eski: radyo/gorev/internet bloklarini hic yazmiyor. Varsayilan
          degerleri OLCUM gibi gostermek, duzelttigimiz yalanin aynisi olurdu. */}
      {status?.available && !hasLayers(status) ? (
        <p className="net-banner net-banner--info">
          <AlertTriangle size={16} />
          {t("network.agentOld")}
        </p>
      ) : null}

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

      {/* ---- Iki sutun: solda kablolu yapilandirma, sagda WiFi ----
           Ikisi de ayni seyin (cihazin aga baglanmasi) alternatifi; alt alta
           dizildiginde WiFi listesi katlanma altinda kaliyordu. Dar ekranda
           tek sutuna duser (bkz. .net-columns). */}
      <div className="net-columns">
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

            {/* Karta yapisik alt bolum: solda kisa uyari, sagda aksiyon.
                `margin-top:auto` ile karti asagi kadar doldurur — yandaki
                WiFi kartiyla ayni yukseklikte biter, dugme havada kalmaz. */}
            <div className="net-form-actions net-form-footer">
              <span className="net-form-hint">
                <Info size={14} strokeWidth={2.2} />
                {t("network.form.saveHint")}
              </span>
              <button
                type="button"
                className="primary-btn"
                disabled={busy || !form.ifname}
                onClick={openConfirm}
              >
                <RotateCw size={15} strokeWidth={2.2} />
                {t("network.form.save")}
              </button>
            </div>
          </div>
        )}
        </section>

        {/* ---- WiFi bolumu: kart -> gorev -> olculen sonuc ----
             Tum durum gecirilir; panel `ap`, `radio`, `role`, `interfaces` ve
             `mdns_name` bilgilerinin hepsine dayaniyor. */}
        <WifiPanel
          accessToken={accessToken}
          status={status}
          onRefreshStatus={refreshStatus}
        />
      </div>

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

            {/* Guvenlik agi metni AP GERCEKTEN yayindaysa verilir. Eskiden
                kosulsuz yaziliyordu; AP kapaliyken bu bir yalandi ve
                kullaniciyi olmayan bir kurtarma yoluna guvendiriyordu. */}
            {status?.ap?.active ? (
              <p className="net-confirm-warn">
                <Wifi size={16} />
                {t("network.confirm.apSafety", {
                  ssid: status?.ap?.ssid ?? "EnerjiOne Grid",
                  host: status?.mdns_name ?? "enerjione.local"
                })}
              </p>
            ) : (
              <p className="net-confirm-warn is-bad">
                <ShieldAlert size={16} />
                {t("network.confirm.noApSafety")}
              </p>
            )}

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
