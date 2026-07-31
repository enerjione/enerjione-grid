/**
 * networkAccess — Ag Ayarlari sayfasinin SAF karar mantigi.
 *
 * TEMEL ILKE: OLCMEDIGIMIZ HICBIR DURUMU GOSTERME.
 * Buradaki her fonksiyon yalnizca cihazin BILDIRDIGI alanlardan hesap yapar;
 * bilgi yoksa `null` doner ve arayuz "Bilinmiyor" yazar. Eski panel bunun
 * tersini yapiyordu: ajanin "client yoksa AP acik olmali" KURALINI olcum gibi
 * gosterip AP gercekte kapaliyken "erisim noktasi acik" diyordu.
 *
 * Neden ayri dosya: "bu islem neyi bozar" hesabi uc ayri yere giriyor (gorev
 * degistirme onayi, baglanma modali, WiFi kartini kapatma onayi). Kopyalanirsa
 * biri gunlenmez ve yine yanlis sey soyler — yalanin ikinci dogus yolu tam
 * olarak budur.
 *
 * DOM/React yok: tek dis dokunus `window.location.hostname` ve o da
 * parametre olarak gecilebiliyor.
 */
import type {
  InternetState,
  NetworkStatus,
  WifiRadioState,
  WifiRoleState
} from "../../shared/types";

/** Radyo/gorev/internet bloklarini yazan ilk ajan sema surumu. Daha eskisi
 *  bu bloklari HIC yazmaz; backend varsayilan doldurur ve "WiFi karti yok"
 *  gibi gorunur. Bu yuzden okumadan once surum kontrolu SART. */
export const AGENT_SCHEMA_LAYERS = 3;

/** Ajan uc katmanli bilgiyi bildiriyor mu? */
export function hasLayers(status: NetworkStatus | null): boolean {
  return (status?.agent_schema ?? 0) >= AGENT_SCHEMA_LAYERS;
}

/** Olculen radyo durumu — eski ajanda null ("bilinmiyor"). */
export function radioOf(status: NetworkStatus | null): WifiRadioState | null {
  return hasLayers(status) ? status?.radio ?? null : null;
}

/** Kartin gorevi (tercih + olcum) — eski ajanda null. */
export function roleOf(status: NetworkStatus | null): WifiRoleState | null {
  return hasLayers(status) ? status?.role ?? null : null;
}

/** Internet durumu — eski ajanda null. */
export function internetOf(status: NetworkStatus | null): InternetState | null {
  return hasLayers(status) ? status?.internet ?? null : null;
}

/** "192.168.1.50/24" -> "192.168.1.50" */
function bareIp(cidr: string): string {
  return (cidr.split("/")[0] ?? "").trim();
}

/** Cihaza ulasilabilen kablolu IPv4 adresi (varsa).
 *  WiFi kapatma engelinin girdisi: kablolu adres yoksa geri donus yolu yok. */
export function wiredAddress(status: NetworkStatus | null): string | null {
  for (const iface of status?.interfaces ?? []) {
    if (iface.type !== "ethernet") continue;
    for (const cidr of iface.addresses) {
      const ip = bareIp(cidr);
      // 169.254.x.x link-local adres bir ulasim yolu DEGILDIR.
      if (ip && !ip.startsWith("169.254.")) return ip;
    }
  }
  return null;
}

/** Bu sayfaya hangi yoldan bagliyiz. */
export type AccessPath = "wired" | "ap" | "wifi" | "unknown";

/**
 * Tarayicinin adres cubugundaki host, cihazin BILDIRDIGI adreslerle
 * eslestirilir. Eslesme yoksa (mDNS adi, ters proxy, IPv6, localhost)
 * DURUSTCE "unknown" doner — "guvenli" varsaymak, kullanicinin kendi
 * baglantisini sessizce kesmesine izin vermek olurdu.
 */
export function detectAccessPath(
  status: NetworkStatus | null,
  hostname?: string
): AccessPath {
  const host = (hostname ?? (typeof window !== "undefined" ? window.location.hostname : ""))
    .trim()
    .toLowerCase();
  if (!status || !host) return "unknown";

  const apAddress = status.ap?.address ? bareIp(status.ap.address).toLowerCase() : "";
  if (apAddress && host === apAddress) return "ap";

  const wifiAddresses = (status.wifi?.addresses ?? []).map((a) => bareIp(a).toLowerCase());
  if (wifiAddresses.includes(host)) return "wifi";

  for (const iface of status.interfaces ?? []) {
    if (iface.type !== "ethernet") continue;
    if (iface.addresses.some((a) => bareIp(a).toLowerCase() === host)) return "wired";
  }
  return "unknown";
}

// ---- Erisim noktasi neden kapali -----------------------------------------

/** AP kapaliyken sebep kodu. Her dal OLCULEN bir alana dayanir; hicbiri
 *  "kural"dan turetilmez. Karsiligi i18n'de `network.wifi.apOffReason.*`. */
export type ApOffReason =
  | "no_adapter"
  | "hard_blocked"
  | "radio_off"
  | "client_mode"
  | "profile_missing"
  | "unknown";

export function apOffReason(status: NetworkStatus | null): ApOffReason {
  const radio = radioOf(status);
  const role = roleOf(status);

  if (radio) {
    if (!radio.supported) return "no_adapter";
    if (!radio.hardware_enabled || radio.blocked_by === "hardware") return "hard_blocked";
    if (!radio.enabled) return "radio_off";
  }
  // Kart bir aga baglanmak icin kullaniliyor (OLCUM: effective).
  if (role?.effective === "client") return "client_mode";
  // AP profili hic tanimli degil — kurulum AP'siz yapilmis.
  if (status?.ap && !status.ap.exists) return "profile_missing";
  return "unknown";
}

// ---- Internet ------------------------------------------------------------

/** Ust seritteki internet metninin i18n anahtari. Kod icinde string
 *  birlestirme YOK: "Var" + " — " + "kablolu" ceviride kirilir. */
export function internetLabelKey(net: InternetState | null): string {
  if (!net || net.state === "unknown") return "network.internet.unknown";
  if (net.state === "none") return "network.internet.no";
  if (net.state === "portal") return "network.internet.portal";
  if (net.state === "limited") return "network.internet.limited";
  if (net.via === "ethernet") return "network.internet.yesWired";
  if (net.via === "wifi") return "network.internet.yesWifi";
  return "network.internet.yesOther";
}

/** Serit ogesinin tonu. "Bilinmiyor" ASLA yesil/kirmizi olmaz. */
export function internetTone(net: InternetState | null): "ok" | "warn" | "" {
  if (!net || net.state === "unknown") return "";
  return net.state === "full" ? "ok" : "warn";
}

/** Internet kotu/bilinmiyorken seridin altina cikan aciklama; iyi durumda
 *  null (haber yoksa iyi haberdir). */
export function internetImpactKey(net: InternetState | null): string | null {
  if (!net || net.state === "unknown") return "network.internet.impactUnknown";
  if (net.state === "full") return null;
  if (net.state === "portal") return "network.internet.impactPortal";
  if (net.state === "limited") return "network.internet.impactLimited";
  return "network.internet.impactNone";
}

// ---- Islem sonuclari -----------------------------------------------------

/** Onay isteyebilecek islemler. */
export type WifiAction =
  | { kind: "radio_off" }
  | { kind: "role"; mode: "ap" | "client" }
  | { kind: "connect"; ssid: string }
  | { kind: "forget" }
  | { kind: "deep_scan" };

export type Consequence = {
  tone: "bad" | "warn" | "ok";
  /** i18n anahtari — `network.wifi.effect.*` */
  key: string;
  vars?: Record<string, string>;
};

/** Bu islem bizim oturumumuzu koparir mi? Yalnizca yol tespitine bakar. */
function sessionEffect(
  path: AccessPath,
  drops: "ap" | "wifi" | "any"
): Consequence | null {
  if (path === "wired") return null; // kablolu oturum etkilenmez
  if (path === "unknown") return { tone: "warn", key: "network.wifi.effect.sessionMayDrop" };
  if (path === "ap" && (drops === "ap" || drops === "any")) {
    return { tone: "bad", key: "network.wifi.effect.sessionDropsAp" };
  }
  if (path === "wifi" && (drops === "wifi" || drops === "any")) {
    return { tone: "bad", key: "network.wifi.effect.sessionDropsWifi" };
  }
  return null;
}

/**
 * Bir islemin ADI KONABILEN sonuclari — hepsi olculen duruma dayanir.
 * Liste bossa onay diyalogu HIC acilmaz: gereksiz onay, onay korlugu uretir.
 */
export function consequencesOf(
  action: WifiAction,
  status: NetworkStatus | null,
  path: AccessPath
): Consequence[] {
  const out: Consequence[] = [];
  const ssid = status?.ap?.ssid ?? "EnerjiOne Grid";
  const apActive = status?.ap?.active === true;
  const net = internetOf(status);
  const wifiInternet = net !== null && net.via === "wifi" && net.state !== "none";
  const wired = wiredAddress(status);

  const push = (c: Consequence | null) => {
    if (c) out.push(c);
  };

  switch (action.kind) {
    case "radio_off":
      if (apActive) push({ tone: "bad", key: "network.wifi.effect.apDown", vars: { ssid } });
      if (wifiInternet) push({ tone: "bad", key: "network.wifi.effect.internetLost" });
      push(sessionEffect(path, "any"));
      if (wired) {
        push({ tone: "ok", key: "network.wifi.effect.wiredSafe", vars: { address: wired } });
      }
      break;

    case "role":
      if (action.mode === "ap") {
        if (wifiInternet) push({ tone: "bad", key: "network.wifi.effect.internetLost" });
        push(sessionEffect(path, "wifi"));
        push({ tone: "ok", key: "network.wifi.effect.apUp", vars: { ssid } });
      } else {
        if (apActive) push({ tone: "warn", key: "network.wifi.effect.apDown", vars: { ssid } });
        push(sessionEffect(path, "ap"));
        push({ tone: "ok", key: "network.wifi.effect.guardBack", vars: { ssid } });
        if (wired) {
          push({ tone: "ok", key: "network.wifi.effect.wiredSafe", vars: { address: wired } });
        }
      }
      break;

    case "connect":
      if (apActive) push({ tone: "warn", key: "network.wifi.effect.apDown", vars: { ssid } });
      push(sessionEffect(path, "ap"));
      push({ tone: "ok", key: "network.wifi.effect.guardBack", vars: { ssid } });
      if (wired) {
        push({ tone: "ok", key: "network.wifi.effect.wiredSafe", vars: { address: wired } });
      }
      break;

    case "forget":
      push({ tone: "warn", key: "network.wifi.effect.forgetPassword" });
      if (wifiInternet) push({ tone: "warn", key: "network.wifi.effect.internetLost" });
      push(sessionEffect(path, "wifi"));
      push({ tone: "ok", key: "network.wifi.effect.apUp", vars: { ssid } });
      break;

    case "deep_scan":
      if (apActive) {
        push({ tone: "warn", key: "network.wifi.effect.apDownTemp", vars: { ssid } });
        push(
          path === "ap"
            ? { tone: "warn", key: "network.wifi.effect.sessionPausesAp" }
            : path === "unknown"
              ? { tone: "warn", key: "network.wifi.effect.sessionMayPause" }
              : { tone: "ok", key: "network.wifi.effect.scanThenBack" }
        );
      }
      break;
  }
  return out;
}

/** Bu islem bizim oturumumuzu koparabilir mi? Islem sonrasi "devir teslim"
 *  bilgilendirmesi bununla acilir. */
export function willDropSession(
  action: WifiAction,
  status: NetworkStatus | null,
  path: AccessPath
): boolean {
  return consequencesOf(action, status, path).some(
    (c) =>
      c.key === "network.wifi.effect.sessionDropsAp" ||
      c.key === "network.wifi.effect.sessionDropsWifi" ||
      c.key === "network.wifi.effect.sessionMayDrop"
  );
}

/**
 * Cihaza giden TUM yollari kapatacak islemi ENGELLE.
 *
 * Tek somut vaka: WiFi kartini kapat + kablolu arayuzun IPv4 adresi yok.
 * Kurtarmasi cihazin basina gitmektir; arayuz bunu bilerek onler. Ajan da
 * ayni kurali bagimsiz uygular (409 `radio_off_would_strand`) — burasi
 * kullaniciyi erken uyarir, otorite ajandir.
 */
export function blockReason(
  action: WifiAction,
  status: NetworkStatus | null
): "no_way_back" | null {
  if (action.kind !== "radio_off") return null;
  return wiredAddress(status) ? null : "no_way_back";
}

/** Cihazin kendi agina baglanildiginda girilecek adres.
 *  mDNS adi DEGIL IP tercih edilir: telefonlarin bir kismi ".local" adlarini
 *  cozemez ve kullanici tam da erisimi kaybettigi anda takilir. */
export function apUrl(status: NetworkStatus | null): string {
  const host = status?.ap?.address ?? status?.mdns_name ?? "10.42.0.1";
  return `http://${host}`;
}

/** Backend'in dondugu MAKINE KODUNU kullaniciya gosterilecek metne cevirir.
 *
 *  Backend hata gövdesinde `detail` olarak kod doner ("radio_off_would_strand"
 *  gibi) — bu bilincli: metin arayuzde, kullanicinin dilinde uretilmeli.
 *  Ama frontend bu kodu `exc.message` olarak OLDUGU GIBI basiyordu; musteri
 *  ekraninda "radio_off_would_strand" gorunuyor, ustelik Ingilizce arayuzde
 *  backend'in Turkce fallback metinleri cikiyordu.
 *
 *  Tanimadigimiz bir kod gelirse (backend yeni bir sey ekledi, arayuz eski)
 *  genel hata metnine duseriz — ham kod ASLA ekrana basilmaz.
 */
const NETWORK_ERROR_CODES = new Set([
  "wifi_not_supported",
  "radio_off",
  "radio_off_would_strand",
  "radio_hardware_blocked",
  "no_saved_network",
  "invalid_mode",
  "request_pending"
]);

export function networkErrorText(
  err: unknown,
  t: (key: string) => string
): string {
  const raw = err instanceof Error ? err.message.trim() : "";
  if (raw && NETWORK_ERROR_CODES.has(raw)) return t(`network.errorCode.${raw}`);
  // Kod degil ama okunabilir bir cumle ise (eski backend, ag hatasi) gecir.
  if (raw && /\s/.test(raw)) return raw;
  return t("common.errorOccurred");
}
