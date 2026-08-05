/**
 * WifiPanel — Ag Ayarlari sayfasindaki WiFi bolumu.
 *
 * UC DURUST KATMAN (yukaridan asagi: onkosul -> secim -> olculen sonuc):
 *   1. WiFi karti  — radyo acik mi. Kapaliysa ne AP yayinlanabilir ne ag
 *                    taranabilir; buradan acilip kapatilabilir.
 *   2. Kartin gorevi — "cihaza baglanmak icin (erisim noktasi)" VEYA
 *                    "internete baglanmak icin (bir aga katil)". Cihazda TEK
 *                    WiFi karti var; ikisi ayni anda OLAMAZ.
 *   3. Secilen gorevin OLCULEN sonucu — AP gercekten yayinda mi, ag gercekten
 *                    bagli mi.
 *
 * DEGISMEZ KURAL: her satirin arkasinda cihazdan gelen bir OLCUM olacak.
 * Alan yoksa "Bilinmiyor" yazilir; KURALDAN durum uydurulmaz. Eski panel
 * "client'a bagli degilsek AP acik olmali" kuralini olcum gibi gosteriyor ve
 * AP gercekte kapaliyken "erisim noktasi acik" diyordu — sahadaki celiskinin
 * kaynagi buydu.
 *
 * IYIMSER (optimistic) UI YOK: gorev secimi yerinde kalir, cihaz yeni durumu
 * bildirene kadar "degistiriliyor" gosterilir. Aksi halde ayni yalan sinifi
 * baska kilikta geri doner.
 */
import { useCallback, useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import {
  CircleHelp,
  Globe,
  Loader2,
  Lock,
  LockOpen,
  Plus,
  RadioTower,
  RefreshCw,
  RotateCw,
  Settings2,
  ShieldAlert,
  Wifi,
  WifiOff,
  X
} from "lucide-react";

import {
  connectWifi,
  fetchWifiScan,
  forgetWifi,
  setWifiMode,
  setWifiRadio,
  triggerWifiScan
} from "../../shared/api";
import type { NetworkStatus, WifiNetwork, WifiScanResult } from "../../shared/types";
import {
  apOffReason,
  apUrl,
  blockReason,
  consequencesOf,
  detectAccessPath,
  networkErrorText,
  radioOf,
  roleOf,
  willDropSession,
  wiredAddress,
  type WifiAction
} from "./networkAccess";
import { WifiActionConfirm, WifiConsequences } from "./WifiActionConfirm";
import WifiModeProgress, { type ProgressAction } from "./WifiModeProgress";
import { useToast } from "../../components/ToastProvider";

type Props = {
  accessToken: string;
  /** Tum sayfa durumu: panel artik yalnizca `wifi` degil `radio`, `role`,
   *  `ap`, `interfaces` ve `mdns_name` bilgilerine de dayaniyor. */
  status: NetworkStatus | null;
  /** Ust sayfanin durum yenileyicisi. */
  onRefreshStatus: () => void;
};

/** Bekleyen bir onay: islem + baslik anahtari. */
type PendingAsk = { action: WifiAction; titleKey: string };

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

export function WifiPanel({ accessToken, status, onRefreshStatus }: Props) {
  const { t } = useTranslation();
  // EYLEM hatalari toast'a: bunlar bir tiklamanin sonucu ve GECICI. Satir
  // ici gosterilince kart yuksekligi degisiyor, altindaki her sey kayiyordu.
  // (Kalici durumlar toast'a TASINMADI — kaybolan bir uyari, olmayan uyaridir.)
  const toast = useToast();

  const [scan, setScan] = useState<WifiScanResult | null>(null);
  const [scanning, setScanning] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [now, setNow] = useState(() => Date.now());

  // Baglanma dialogu (listeden secilen ag)
  const [target, setTarget] = useState<WifiNetwork | null>(null);
  const [psk, setPsk] = useState("");

  // "Agi elle ekle" dialogu — AP modunda tarama eksik donebilir, gizli aglar
  // hic gorunmez. Bu, ilk kurulumu kilitleyen sinifin cikis kapisi.
  const [manualOpen, setManualOpen] = useState(false);
  const [manualSsid, setManualSsid] = useState("");
  const [manualPsk, setManualPsk] = useState("");

  const [ask, setAsk] = useState<PendingAsk | null>(null);
  // Istek gonderildi ve oturumumuz kopabilir: polling "basarisiz" oldugunda
  // kullanici hata sanmasin diye ne olacagini onceden yaziyoruz.
  const [handoverAt, setHandoverAt] = useState<number | null>(null);

  // Gorev degisimi ANINDA sonuclanmaz: kart dusurulur, yeni profil kaldirilir,
  // AP'de yayin gercekten baslayana kadar saniyeler gecer. Eskiden ekranda tek
  // satirlik bir metin vardi ve kullanici bir sey olup olmadigini anlamiyordu;
  // ayni butona tekrar basiliyordu. Artik islem boyunca ilerleme modali acik
  // kalir. `null` = islem yok.
  const [progress, setProgress] = useState<
    { action: ProgressAction; startedAt: number; drops: boolean } | null
  >(null);
  // Ac/kapa, gorev secimi ve olculen durum artik ayri bir popup'ta; panelde
  // yer kalmiyordu ve ag listesi ekranin disina dusuyordu.
  const [ayarlarAcik, setAyarlarAcik] = useState(false);

  // ---- OLCULEN degerler. Varsayim YOK: alan yoksa null = "bilinmiyor". ----
  const wifi = status?.wifi;
  const radio = radioOf(status);
  const role = roleOf(status);
  const radioOn: boolean | null = radio ? radio.enabled : null;
  const hardBlocked = radio ? !radio.hardware_enabled || radio.blocked_by === "hardware" : false;
  // Kart VAR ama NetworkManager yonetmiyor. "Kart yok" demekten farkli:
  // duzeltilebilir bir durum, o yuzden ayri bir aciklama gosteriyoruz.
  const unmanaged = radio?.unmanaged === true || radio?.blocked_by === "unmanaged";
  const roleMode: "ap" | "client" | null = role ? role.mode : null;
  const apActive: boolean | null = status?.ap ? status.ap.active : null;
  const apSsid = status?.ap?.ssid ?? "EnerjiOne Grid";
  const offReason = apOffReason(status);

  const path = useMemo(() => detectAccessPath(status), [status]);
  const busy = Boolean(status?.pending) || submitting;
  // Kablolu adres yoksa WiFi'yi kapatmak cihazi erisilemez birakir; ajan da
  // ayni istegi reddeder (409). Dugmeyi kapatip sebebini altina yaziyoruz.
  const radioOffBlocked = blockReason({ kind: "radio_off" }, status) !== null;
  // Radyo kapaliyken tarama anlamsiz; bilinmiyorsa engellemiyoruz (eski ajan).
  const canScan = radioOn !== false;

  const loadScan = useCallback(async () => {
    try {
      setScan(await fetchWifiScan(accessToken));
    } catch {
      /* tarama yoksa sessiz gec */
    }
  }, [accessToken]);

  useEffect(() => {
    void loadScan();
  }, [loadScan]);

  // Muhafiz / yeniden deneme geri sayimi icin saniyelik tick.
  const ticking = Boolean(wifi?.guard_active) || Boolean(role?.fallback_active) || handoverAt !== null;
  useEffect(() => {
    if (!ticking) return;
    const id = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(id);
  }, [ticking]);

  // Devir teslim bilgisi: cihaz yeni durumu bildirdiyse ve makul sure gectiyse
  // kendiliginden kalkar (kullanici da kapatabilir).
  useEffect(() => {
    if (handoverAt === null) return;
    if (!status?.pending && now - handoverAt > 15000) setHandoverAt(null);
  }, [handoverAt, now, status?.pending]);

  const guardLeft = useMemo(() => {
    if (!wifi?.guard_active || !wifi.guard_deadline) return null;
    return Math.max(0, Math.ceil((wifi.guard_deadline * 1000 - now) / 1000));
  }, [wifi?.guard_active, wifi?.guard_deadline, now]);

  const retryLeft = useMemo(() => {
    if (!role?.fallback_active || !role.next_retry_at) return null;
    return Math.max(0, Math.ceil((role.next_retry_at * 1000 - now) / 1000));
  }, [role?.fallback_active, role?.next_retry_at, now]);

  // ---- Islem calistirici -------------------------------------------------
  /** WiFi kartini ac — hicbir seyi bozmaz, onay sorulmaz. */
  const turnRadioOn = useCallback(async () => {
    setSubmitting(true);
    try {
      await setWifiRadio(accessToken, true);
      onRefreshStatus();
    } catch (exc) {
      toast.error(networkErrorText(exc, t));
    } finally {
      setSubmitting(false);
    }
  }, [accessToken, onRefreshStatus, t]);

  /** Istegi cihaza gonder. Oturumu koparacaksa devir teslim bilgisini ac.
   *  `secret` yalnizca baglanma isleminde kullanilir; state uzerinden
   *  okunmaz cunku setState asenkrondur ve bir onceki sifreyi gonderirdi. */
  const run = useCallback(
    async (action: WifiAction, secret?: string | null) => {
      setSubmitting(true);
      const drops = willDropSession(action, status, path);
      try {
        switch (action.kind) {
          case "radio_off":
            await setWifiRadio(accessToken, false);
            break;
          case "role":
            // Modal istekten ONCE aciliyor: `setWifiMode` oturumu dusurebilir
            // ve sonrasina hic donmeyebiliriz. Sonra acsaydik, tam da geri
            // bildirime en cok ihtiyac duyulan durumda ekran bos kalirdi.
            setProgress({
              action: { kind: "role", mode: action.mode },
              startedAt: Date.now(),
              drops,
            });
            await setWifiMode(accessToken, action.mode);
            break;
          case "connect":
            await connectWifi(accessToken, action.ssid, secret ?? null);
            setTarget(null);
            setManualOpen(false);
            setPsk("");
            setManualSsid("");
            setManualPsk("");
            break;
          case "forget":
            await forgetWifi(accessToken);
            break;
          case "deep_scan":
            await runScan(true);
            break;
        }
        if (drops) setHandoverAt(Date.now());
        onRefreshStatus();
      } catch (exc) {
        // Istek ULASMADIYSA ilerleme modali kapanmali — aksi halde hicbir zaman
        // gerceklesmeyecek bir islemi bekler halde asili kalirdi. Oturumun
        // dustugu durum FARKLIDIR: orada istek ulasmis olabilir, o yuzden
        // modal acik kalir ve sonucu kendisi olcer.
        if (!drops) setProgress(null);
        toast.error(networkErrorText(exc, t));
      } finally {
        setSubmitting(false);
      }
    },
    // runScan asagida bir fonksiyon bildirimi (hoisted) — kimlikte degismedigi
    // icin bagimlilik listesine alinmiyor.
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [accessToken, onRefreshStatus, path, status, t]
  );

  /** Onay gerekiyorsa sor, gerekmiyorsa dogrudan uygula. */
  const request = useCallback(
    (action: WifiAction, titleKey: string) => {
      if (blockReason(action, status)) {
        toast.error(t("network.wifi.confirm.blockedNoPath"));
        return;
      }
      if (consequencesOf(action, status, path).length === 0) {
        void run(action);
        return;
      }
      setAsk({ action, titleKey });
    },
    [path, run, status, t]
  );

  // ---- Tarama ------------------------------------------------------------
  /** Tarama tetikle ve sonuc dosyasi tazelenene kadar bekle.
   *  Derin taramada AP ~15 sn indirildigi icin bekleme suresi uzun. */
  async function runScan(deep: boolean) {
    setScanning(true);
    const previous = scan?.updated_at ?? null;
    try {
      await triggerWifiScan(accessToken, deep);
      const deadline = Date.now() + (deep ? 45000 : 15000);
      while (Date.now() < deadline) {
        await new Promise((r) => setTimeout(r, 2500));
        try {
          const next = await fetchWifiScan(accessToken);
          setScan(next);
          if (next.updated_at && next.updated_at !== previous) break;
        } catch {
          /* ajan dosyayi yaziyor olabilir — beklemeye devam */
        }
      }
      onRefreshStatus();
    } catch (exc) {
      toast.error(networkErrorText(exc, t));
    } finally {
      setScanning(false);
    }
  }

  // ---- WiFi karti yok ----------------------------------------------------
  // "Cihazda WiFi karti yok" OLCULMUS bir donanim iddiasidir; yalnizca ajan
  // GERCEKTEN olctuyse soyleriz. Blok hic yoksa (eski ajan ya da ajan hata
  // state'i yazmis) `radio`/`wifi` null gelir ve biz "bilinmiyor" tarafinda
  // kaliriz — aksi halde nmcli bir kez zaman asimina ugradiginda ekran
  // "USB WiFi adaptoru takin" diyerek kullaniciyi yanlis ise yonlendiriyordu.
  const noAdapter =
    radio != null ? !radio.supported : wifi != null ? !wifi.supported : false;
  if (noAdapter) {
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

  const askItems = ask ? consequencesOf(ask.action, status, path) : [];
  const connectAction: WifiAction | null = target
    ? { kind: "connect", ssid: target.ssid }
    : manualOpen && manualSsid.trim()
      ? { kind: "connect", ssid: manualSsid.trim() }
      : null;
  const connectItems = connectAction ? consequencesOf(connectAction, status, path) : [];

  return (
    <section className="net-card wifi-card">
      <header className="net-card-head">
        <Wifi size={18} strokeWidth={2} />
        <h3>{t("network.wifi.title")}</h3>
        {/* Iki eylem tek grup: baslik `space-between` oldugu icin ayri
            birakilsalar ekranin iki ucuna dagilirlardi. */}
        <div className="wifi-head-actions">
        <button
          type="button"
          className="wifi-settings-btn"
          onClick={() => setAyarlarAcik(true)}
        >
          <Settings2 size={15} strokeWidth={2.2} />
          {t("network.wifi.settingsOpen")}
        </button>
        <button
          type="button"
          className="wifi-scan-btn"
          onClick={() => void runScan(false)}
          disabled={scanning || busy || !canScan}
        >
          {scanning ? (
            <Loader2 size={15} strokeWidth={2.2} className="net-spin" />
          ) : (
            <RefreshCw size={15} strokeWidth={2.2} />
          )}
          {scanning ? t("network.wifi.scanning") : t("network.wifi.scan")}
        </button>
        </div>
      </header>

      {/* ---- Ozet serit: durum TEK BAKISTA, detay popup'ta ----
           Bagli ag burada GORUNUR: "hangi agdayim" sorusu bu sayfanin en sik
           sorulan sorusu ve eskiden uc katmanin altina gomuluydu. */}
      <div className="wifi-summary">
        <div className={`wifi-sum-item ${radioOn === true ? "is-on" : radioOn === false ? "is-off" : ""}`}>
          {radioOn === true ? (
            <Wifi size={15} strokeWidth={2.2} />
          ) : radioOn === false ? (
            <WifiOff size={15} strokeWidth={2.2} />
          ) : (
            <CircleHelp size={15} strokeWidth={2.2} />
          )}
          <span>
            <small>{t("network.wifi.radioLabel")}</small>
            <strong>
              {radioOn === true
                ? t("network.wifi.radioOn")
                : radioOn === false
                  ? t("network.wifi.radioOff")
                  : t("network.wifi.radioUnknown")}
            </strong>
          </span>
        </div>

        <div className="wifi-sum-item">
          {roleMode === "ap" ? (
            <RadioTower size={15} strokeWidth={2.2} />
          ) : (
            <Globe size={15} strokeWidth={2.2} />
          )}
          <span>
            <small>{t("network.wifi.roleLabel")}</small>
            <strong>
              {roleMode === "ap"
                ? t("network.wifi.roleAp")
                : roleMode === "client"
                  ? t("network.wifi.roleClient")
                  : t("network.wifi.roleUnknown")}
            </strong>
          </span>
        </div>

        {/* BAGLI AG — olculen `wifi.connected`e dayanir; kayitli ama bagli
            olmayan profil "bagli" gosterilmez. */}
        <div className={`wifi-sum-item is-wide ${wifi?.connected ? "is-on" : ""}`}>
          <Globe size={15} strokeWidth={2.2} />
          <span>
            <small>{t("network.wifi.connectedLabel")}</small>
            <strong>
              {wifi?.connected && wifi.ssid
                ? wifi.ssid
                : wifi?.ssid
                  ? t("network.wifi.savedNotConnected", { ssid: wifi.ssid })
                  : t("network.wifi.noConnection")}
            </strong>
          </span>
          {wifi?.connected && typeof wifi.signal === "number" ? (
            <SignalIcon signal={wifi.signal} />
          ) : null}
        </div>
      </div>


      {/* ================= AYARLAR POPUP'I ================================
          Uc katman (kart -> gorev -> olculen sonuc) buraya tasindi. Panelde
          yer kalmiyordu: ac/kapa anahtari, gorev secimi, AP durumu ve bagli
          ag kunyesi ust uste yiginca ag listesi ekranin disina dusuyordu.

          MANTIK TASINMADI, yalnizca nerede CIZILDIGI degisti — onay
          diyaloglari, engelleme kurallari ve "iyimser UI yok" davranisi
          aynen calisiyor. */}
      {ayarlarAcik ? (
        <div className="settings-modal-backdrop" onClick={() => setAyarlarAcik(false)}>
          <div
            className="settings-modal wifi-settings-modal"
            role="dialog"
            aria-modal="true"
            aria-label={t("network.wifi.settingsTitle")}
            onClick={(e) => e.stopPropagation()}
          >
            <div className="modal-head">
              <h3>
                <Wifi size={17} strokeWidth={2.2} />
                {t("network.wifi.settingsTitle")}
              </h3>
              <button
                type="button"
                className="icon-btn"
                onClick={() => setAyarlarAcik(false)}
                aria-label={t("common.close")}
              >
                <X size={18} strokeWidth={2.2} />
              </button>
            </div>

            <div className="wifi-settings-body">
          {/* ================= KATMAN 1: WiFi karti (fiziksel onkosul) ========= */}
          <div
            className={`wifi-radio-row ${
              radioOn === true ? "is-on" : radioOn === false ? "is-off" : "is-unknown"
            }`}
          >
            <span className="wifi-current-icon">
              {radioOn === true ? (
                <Wifi size={18} strokeWidth={2.2} />
              ) : radioOn === false ? (
                <WifiOff size={18} strokeWidth={2.2} />
              ) : (
                <CircleHelp size={18} strokeWidth={2.2} />
              )}
            </span>
            <div className="wifi-current-body">
              <small>{t("network.wifi.radioLabel")}</small>
              <strong aria-live="polite">
                {radioOn === true
                  ? t("network.wifi.radioOn")
                  : radioOn === false
                    ? hardBlocked
                      ? t("network.wifi.radioHardBlocked")
                      : t("network.wifi.radioOff")
                    : t("network.wifi.radioUnknown")}
              </strong>
            </div>
            {/* Durum bilinmiyorken ac/kapa GOSTERILMEZ: kapali konumdaki bir
                anahtar "kapali" demektir, oysa biz bilmiyoruz. */}
            {radioOn !== null ? (
              <button
                type="button"
                role="switch"
                className="wifi-switch"
                aria-checked={radioOn}
                aria-label={radioOn ? t("network.wifi.radioTurnOff") : t("network.wifi.radioTurnOn")}
                title={radioOn ? t("network.wifi.radioTurnOff") : t("network.wifi.radioTurnOn")}
                disabled={busy || hardBlocked || (radioOn && radioOffBlocked)}
                onClick={() => {
                  if (radioOn) request({ kind: "radio_off" }, "network.wifi.confirm.radioOff");
                  else void turnRadioOn();
                }}
              />
            ) : null}
          </div>

          {radioOn !== true ? (
            <p className="wifi-hint">
              {unmanaged
                ? t("network.wifi.radioUnmanagedHint")
                : hardBlocked
                ? t("network.wifi.radioHardBlockedHint")
                : radioOn === false
                  ? t("network.wifi.radioOffHint")
                  : t("network.wifi.radioUnknownHint")}
            </p>
          ) : null}

          {radio?.auto_restored_at ? (
            <p className="wifi-hint">{t("network.wifi.radioAutoRestored")}</p>
          ) : null}

          {/* Engelleme: cihaza giden tum yollari kapatacak islem YAPILMAZ. */}
          {radioOn === true && radioOffBlocked ? (
            <p className="wifi-hint is-muted">{t("network.wifi.confirm.blockedNoPath")}</p>
          ) : null}

          {/* ================= KATMAN 2: kartin gorevi ========================= */}
          <div className="wifi-role" aria-disabled={radioOn !== true}>
            <span className="wifi-role-label">{t("network.wifi.roleLabel")}</span>
            <div className="net-segment wifi-role-seg" role="radiogroup" aria-label={t("network.wifi.roleLabel")}>
              <button
                type="button"
                aria-pressed={roleMode === "ap"}
                className={roleMode === "ap" ? "is-active" : ""}
                disabled={busy || radioOn !== true}
                // Zaten secili gorevi tekrar istemek anlamsiz bir onay diyalogu
                // acardi; AP'yi yeniden zorlamak icin ayri "Yeniden dene" var.
                onClick={() => {
                  if (roleMode !== "ap") {
                    request({ kind: "role", mode: "ap" }, "network.wifi.confirm.toAp");
                  }
                }}
              >
                <RadioTower size={15} strokeWidth={2.2} />
                <span>{t("network.wifi.roleAp")}</span>
                <small>{t("network.wifi.roleApSub")}</small>
              </button>
              <button
                type="button"
                aria-pressed={roleMode === "client"}
                className={roleMode === "client" ? "is-active" : ""}
                disabled={busy || radioOn !== true}
                onClick={() => {
                  if (roleMode !== "client") {
                    request({ kind: "role", mode: "client" }, "network.wifi.confirm.toClient");
                  }
                }}
              >
                <Globe size={15} strokeWidth={2.2} />
                <span>{t("network.wifi.roleClient")}</span>
                <small>{t("network.wifi.roleClientSub")}</small>
              </button>
            </div>
            <p className="wifi-role-hint">
              {roleMode === "ap"
                ? t("network.wifi.roleApHint")
                : roleMode === "client"
                  ? t("network.wifi.roleClientHint", { ssid: apSsid })
                  : t("network.wifi.roleUnknown")}
            </p>
            {/* "Tek kart var, ikisi ayni anda olmaz" bilgisi BIR KEZ veriliyor.
                Eskiden ayni gercek ekranda uc yerde tekrarlaniyordu (buradaki
                muted satir, rol ipucu ve AP kapali yardimi); panel bilgi
                yiginina donuyor ve asil eylem gorunmez hale geliyordu. */}
            {status?.pending ? (
              <p className="wifi-role-hint" aria-live="polite">
                {t("network.wifi.roleSwitching")}
              </p>
            ) : null}
          </div>

          {/* ============ KATMAN 3: secilen gorevin OLCULEN sonucu ============= */}
          {/* Erisim noktasi — YALNIZCA olculen ap.active'e dayanir. */}
          <div
            className={`wifi-ap-state ${
              apActive === true
                ? "is-ok"
                : apActive === false
                  ? roleMode === "client"
                    ? "is-unknown"
                    : "is-bad"
                  : "is-unknown"
            }`}
          >
            <span className="wifi-current-icon">
              <RadioTower size={18} strokeWidth={2.2} />
            </span>
            <div className="wifi-current-body">
              <small>{t("network.wifi.apStateLabel")}</small>
              <strong aria-live="polite">
                {apActive === true
                  ? t("network.wifi.apOn")
                  : apActive === false
                    ? t("network.wifi.apOff")
                    : t("network.wifi.apUnknown")}
              </strong>
              {apActive === true ? <code>{apUrl(status)}</code> : null}
              {apActive === false ? (
                <span className="wifi-reason">{t(`network.wifi.apOffReason.${offReason}`)}</span>
              ) : null}
            </div>
            {apActive === false && radioOn === true && roleMode === "ap" ? (
              <button
                type="button"
                className="wifi-retry-btn"
                disabled={busy}
                onClick={() => void run({ kind: "role", mode: "ap" })}
              >
                <RotateCw size={14} strokeWidth={2.2} />
                {t("network.wifi.apRetry")}
              </button>
            ) : null}
          </div>

          {/* AP ACIKKEN "nasil baglanirim" bilgisi ISE YARAR; kapaliyken
              gosterilen yardim metni ise gorev secicinin hemen altindaki
              ipucunu tekrarliyordu. Kapali olma SEBEBI zaten yukaridaki durum
              kartinda (`apOffReason`) tek satir olarak yaziyor. */}
          {apActive === true ? (
            <p className="wifi-hint">
              {t("network.wifi.apJoinHint", { ssid: apSsid, url: apUrl(status) })}
            </p>
          ) : null}

          {/* Bagli ag kunyesi — olculen wifi.connected. */}
          {wifi && (wifi.connected || wifi.saved || roleMode === "client") ? (
            <div className={`wifi-current ${wifi.connected ? "is-connected" : ""}`}>
              <span className="wifi-current-icon">
                {wifi.connected ? (
                  <Wifi size={18} strokeWidth={2.2} />
                ) : (
                  <WifiOff size={18} strokeWidth={2.2} />
                )}
              </span>
              <div className="wifi-current-body">
                <small>
                  {wifi.connected ? t("network.wifi.connectedTo") : t("network.wifi.savedNetwork")}
                </small>
                <strong>{wifi.ssid ?? t("network.wifi.noSavedNetwork")}</strong>
                {wifi.connected && wifi.addresses.length > 0 ? (
                  <code>{wifi.addresses.join(", ")}</code>
                ) : !wifi.connected && wifi.saved ? (
                  <span className="wifi-reason">{t("network.wifi.clientNotConnected")}</span>
                ) : null}
              </div>
              {wifi.saved ? (
                <button
                  type="button"
                  className="wifi-forget-btn"
                  disabled={busy}
                  onClick={() => request({ kind: "forget" }, "network.wifi.confirm.forget")}
                >
                  {t("network.wifi.forget")}
                </button>
              ) : null}
            </div>
          ) : null}

            </div>

            {/* Ikinci bir "Kapat" dugmesi BILEREK YOK: basliktaki X yeterli.
                Iki kapatma denetimi (ustte X solda, altta Kapat) hem gorsel
                cift hem hiza tutarsizligiydi — tum modallarda tek desen:
                sag ustte X. */}
          </div>
        </div>
      ) : null}


      {/* Muhafiz geri sayimi — baglanma denemesi surerken */}
      {guardLeft !== null ? (
        <div className="wifi-guard">
          <Loader2 size={16} strokeWidth={2.2} className="net-spin" />
          <div>
            <strong>{t("network.wifi.guardTitle", { ssid: wifi?.ssid ?? "" })}</strong>
            <span>{t("network.wifi.guardHint", { seconds: guardLeft })}</span>
          </div>
        </div>
      ) : null}

      {/* Tercih "internete baglan" ama aga ulasilamiyor: cihaz erisim
          garantisi icin kendi agini geri acti. Bu bir hata degil, anlatilacak
          bir durum. */}
      {role?.fallback_active ? (
        <div className="wifi-guard">
          <ShieldAlert size={16} strokeWidth={2.2} />
          <div>
            <strong>{t("network.wifi.fallbackTitle")}</strong>
            <span>
              {retryLeft !== null
                ? t("network.wifi.fallbackRetry", { seconds: retryLeft })
                : t("network.wifi.fallbackHint")}
            </span>
          </div>
        </div>
      ) : null}

      {role?.foreign_client ? (
        <p className="wifi-hint">
          {t("network.wifi.foreignClient", { ssid: role.foreign_client })}
        </p>
      ) : null}

      {/* Islem gonderildi, baglantimiz kopabilir. */}
      {handoverAt !== null ? (
        <div className="wifi-handover">
          <Loader2 size={16} strokeWidth={2.2} className="net-spin" />
          <div>
            <strong>{t("network.wifi.handover.title")}</strong>
            <span>{t("network.wifi.handover.mayDrop")}</span>
            <span>
              {wiredAddress(status)
                ? t("network.wifi.handover.backWired", {
                    url: `http://${wiredAddress(status)}`
                  })
                : t("network.wifi.handover.backVia", { ssid: apSsid, url: apUrl(status) })}
            </span>
          </div>
          <button
            type="button"
            className="wifi-handover-close"
            onClick={() => setHandoverAt(null)}
            aria-label={t("common.close")}
          >
            <X size={16} strokeWidth={2.2} />
          </button>
        </div>
      ) : null}


      {/* ---- Ag listesi ---- */}
      {canScan ? (
        <>
          <div className="wifi-list-head">
            <span className="wifi-scan-age">
              {scan?.age_seconds != null
                ? t("network.wifi.scanAge", { seconds: Math.round(scan.age_seconds) })
                : t("network.wifi.scanNever")}
            </span>
            <button
              type="button"
              className="wifi-manual-btn"
              disabled={busy}
              onClick={() => {
                setManualSsid("");
                setManualPsk("");
                setManualOpen(true);
              }}
            >
              <Plus size={14} strokeWidth={2.4} />
              {t("network.wifi.manualAdd")}
            </button>
          </div>

          {scan && scan.networks.length > 0 ? (
            <ul className="wifi-list">
              {[...scan.networks]
                .sort((a, b) => {
                  // BAGLI AG HER ZAMAN EN USTTE: kullanici once kendi
                  // durumunu gorur, listede aramaz. Kalani sinyale gore.
                  const aBagli =
                    a.in_use || (Boolean(wifi?.connected) && wifi?.ssid === a.ssid) ? 1 : 0;
                  const bBagli =
                    b.in_use || (Boolean(wifi?.connected) && wifi?.ssid === b.ssid) ? 1 : 0;
                  if (aBagli !== bBagli) return bBagli - aBagli;
                  return (b.signal ?? 0) - (a.signal ?? 0);
                })
                .map((n) => {
                // Bagli ag listede de ISARETLENIR. Once taramanin KENDI
                // olcumu (nmcli IN-USE) — en dogrudan kanit; `wifi` blogu
                // okunamasa ya da SSID yazimi ayrissa bile dogru satiri
                // isaretler. Ikincil olarak baglanti kunyesiyle eslesme.
                const isCurrent =
                  n.in_use || (Boolean(wifi?.connected) && wifi?.ssid === n.ssid);
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
                        disabled={busy}
                        onClick={() => {
                          setTarget(n);
                          setPsk("");
                        }}
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
              {!scan
                ? t("network.wifi.notScanned")
                : scan.ap_was_active
                  ? t("network.wifi.emptyScanApMode")
                  : t("network.wifi.emptyScan")}
            </p>
          )}

          {/* Derin tarama — AP yayindayken tek radyo kanal degistiremez ve
              liste EKSIK doner. Bedeli AP'nin ~15 sn inmesidir; bu yuzden
              ayri dugme ve ayri onay. */}
          {apActive === true ? (
            <button
              type="button"
              className="wifi-deep-btn"
              disabled={busy || scanning}
              onClick={() => request({ kind: "deep_scan" }, "network.wifi.confirm.deepScan")}
            >
              {t("network.wifi.scanDeep")}
            </button>
          ) : null}
        </>
      ) : (
        <p className="net-hint wifi-empty">{t("network.wifi.emptyScanRadioOff")}</p>
      )}

      {/* ---- Baglanma dialogu (listeden secilen ag) ---- */}
      {target ? (
        <div className="net-modal-backdrop" onClick={() => !busy && setTarget(null)}>
          <div className="net-modal wifi-modal" onClick={(e) => e.stopPropagation()}>
            <header>
              <h4>{t("network.wifi.connectTo", { ssid: target.ssid })}</h4>
              <button type="button" onClick={() => setTarget(null)} disabled={busy}>
                <X size={18} strokeWidth={2.2} />
              </button>
            </header>

            <p className="wifi-consequences-lead">{t("network.wifi.confirm.effects")}</p>
            <WifiConsequences items={connectItems} />

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
                disabled={busy || (target.secured && psk.length < 8)}
                onClick={() =>
                  void run({ kind: "connect", ssid: target.ssid }, target.secured ? psk : null)
                }
              >
                {busy ? t("network.wifi.connecting") : t("network.wifi.connect")}
              </button>
            </div>
          </div>
        </div>
      ) : null}

      {/* ---- Agi elle ekle ---- */}
      {manualOpen ? (
        <div className="net-modal-backdrop" onClick={() => !busy && setManualOpen(false)}>
          <div className="net-modal wifi-modal" onClick={(e) => e.stopPropagation()}>
            <header>
              <h4>{t("network.wifi.manualTitle")}</h4>
              <button type="button" onClick={() => setManualOpen(false)} disabled={busy}>
                <X size={18} strokeWidth={2.2} />
              </button>
            </header>

            <label className="wifi-psk-label">
              {t("network.wifi.manualSsid")}
              <input
                type="text"
                value={manualSsid}
                onChange={(e) => setManualSsid(e.target.value)}
                placeholder={t("network.wifi.manualSsidPlaceholder")}
                autoFocus
                maxLength={32}
              />
              <small>{t("network.wifi.manualHint")}</small>
            </label>

            <label className="wifi-psk-label">
              {t("network.wifi.password")}
              <input
                type="password"
                value={manualPsk}
                onChange={(e) => setManualPsk(e.target.value)}
                placeholder={t("network.wifi.manualPskPlaceholder")}
                minLength={8}
                maxLength={63}
              />
              <small>{t("network.wifi.passwordHint")}</small>
            </label>

            {connectItems.length > 0 ? (
              <>
                <p className="wifi-consequences-lead">{t("network.wifi.confirm.effects")}</p>
                <WifiConsequences items={connectItems} />
              </>
            ) : null}

            <div className="net-modal-actions">
              <button type="button" onClick={() => setManualOpen(false)} disabled={busy}>
                {t("common.cancel")}
              </button>
              <button
                type="button"
                className="wifi-connect-confirm"
                disabled={
                  busy ||
                  manualSsid.trim().length === 0 ||
                  (manualPsk.length > 0 && manualPsk.length < 8)
                }
                onClick={() =>
                  void run(
                    { kind: "connect", ssid: manualSsid.trim() },
                    manualPsk.length > 0 ? manualPsk : null
                  )
                }
              >
                {busy ? t("network.wifi.connecting") : t("network.wifi.connect")}
              </button>
            </div>
          </div>
        </div>
      ) : null}

      {/* ---- Tek onay diyalogu (radyo kapat / gorev / unut / derin tarama) ---- */}
      {ask ? (
        <WifiActionConfirm
          titleKey={ask.titleKey}
          items={askItems}
          risky={askItems.some((i) => i.tone === "bad")}
          busy={busy}
          onCancel={() => setAsk(null)}
          onConfirm={() => {
            const pending = ask.action;
            setAsk(null);
            void run(pending);
          }}
        />
      ) : null}

      {/* Gorev degisimi ilerlemesi. Onay diyalogundan SONRA render ediliyor ki
          ikisi ayni anda aciksa ustte kalsin. Kapatmayi bilesenin kendisi
          yonetir: islem bitmeden `onClose` cagirmaz. */}
      {progress ? (
        <WifiModeProgress
          action={progress.action}
          status={status}
          startedAt={progress.startedAt}
          dropsSession={progress.drops}
          onClose={() => setProgress(null)}
        />
      ) : null}
    </section>
  );
}
