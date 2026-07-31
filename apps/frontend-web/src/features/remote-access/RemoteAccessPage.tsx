/**
 * Uzaktan Bakim Izni (Muhendislik > Sistem).
 *
 * Cihaz uretici bakim agina KAYITLI kalir ama gelen baglantilar VARSAYILAN
 * OLARAK REDDEDILIR. Musterinin yetkili kullanicisi buradan SURELI izin verir;
 * sure dolunca host ajani erisimi kendiliginden kapatir. "Acik unutma" sorunu
 * boyle ortadan kalkar — bu ozelligin varlik sebebi tam olarak budur.
 *
 * Mimari (Ag Ayarlari ile ayni): backend host'a dokunmaz, istegi request.json
 * dosyasina yazar; host'ta root ile calisan `e1-rad` ajani uygular ve durumu
 * state.json'a yazar. Bu sayfa yalnizca o durumu okur, 202'den sonra sonuc bir
 * sonraki yoklamada gorunur.
 *
 * TASARIM — "SALTER": sayfanin iki belirgin hali var (KAPALI / ACIK) ve gecis
 * anlasilir olsun diye ust blok gorsel olarak donusuyor. ACIK halde kahraman
 * oge GERI SAYIM'dir; kalan sure sunucudan gelir, yerelde saniye saniye akar
 * ve her yoklamada resenkron olur (saha PC'lerinde istemci saati guvenilmez).
 *
 * Metin tonu: bu ekrani MUSTERI okur ve "uretici firmaya erisim veriyorum"
 * karari verir. Jargon (tailnet/SSH/daemon) YOK; ne verildigi duz Turkce.
 *
 * Ikonografi: lucide-react (kardes sayfa NetworkSettingsPage ile ayni dil).
 */
import { useCallback, useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import {
  AlertTriangle,
  CalendarClock,
  Clock,
  Headset,
  History,
  Hourglass,
  Loader2,
  Lock,
  LockOpen,
  PlugZap,
  RefreshCw,
  ShieldAlert,
  ShieldCheck,
  Timer,
  UserCheck
} from "lucide-react";

import { useToast } from "../../components/ToastProvider";
import { formatEventMessage } from "../events/formatEventMessage";
import {
  fetchRemoteAccessAudit,
  fetchRemoteAccessStatus,
  grantRemoteAccess,
  revokeRemoteAccess
} from "../../shared/api";
import type { RemoteAccessStatus, SystemEvent } from "../../shared/types";
import { usePolling } from "../../shared/usePolling";
import {
  formatClockFull,
  formatClockShort,
  formatDurationMinutes,
  formatRemaining,
  notifyRemoteAccessChanged
} from "./remoteAccessShared";

type Props = {
  accessToken: string;
};

/** Durum yoklama periyodu. Ag Ayarlari ile ayni (10 sn) — bu sayfa acikken
 *  kullanici bir karar veriyor, bayat veri gostermek kabul edilemez. */
const REFRESH_INTERVAL_SEC = 10;

/** Hazir sure secenekleri (dk). Sinirlar KODA GOMULU DEGIL: backend
 *  min_duration_minutes / max_duration_minutes bildiriyor ve liste ona gore
 *  eleniyor. Site ayari tavani 4 saate cekerse 8/24 butonlari kendiliginden
 *  kaybolur. */
const PRESET_MINUTES = [30, 120, 480, 1440];

/** Son 15 dakika: "bakim suruyorsa uzatmalisin" uyarisi. */
const EXPIRING_SOON_SEC = 900;

/** Salterin gorsel hali. `unknown` = ajan/kayit eksik, "kapali" demeye
 *  hakkimiz yok; `loading` yalnizca ilk okumada ve kisa surer. */
type Mode = "loading" | "applying" | "on" | "off" | "unknown";

/** Her halin rozet/baslik/aciklama anahtarlari — ic ice ternary yerine tablo. */
const HERO_TEXT: Record<Mode, { state: string; title: string; lead: string }> = {
  loading: {
    state: "remoteAccess.state.loading",
    title: "remoteAccess.hero.loadingTitle",
    lead: "remoteAccess.hero.loadingLead"
  },
  applying: {
    state: "remoteAccess.state.applying",
    title: "remoteAccess.hero.applyingTitle",
    lead: "remoteAccess.hero.applyingLead"
  },
  on: {
    state: "remoteAccess.state.on",
    title: "remoteAccess.hero.onTitle",
    lead: "remoteAccess.hero.onLead"
  },
  off: {
    state: "remoteAccess.state.off",
    title: "remoteAccess.hero.offTitle",
    lead: "remoteAccess.hero.offLead"
  },
  unknown: {
    state: "remoteAccess.state.unknown",
    title: "remoteAccess.hero.unknownTitle",
    lead: "remoteAccess.hero.unknownLead"
  }
};

/** Ajanin urettigi kapanis sebepleri. Bunlar MAKINE KODUDUR; denetim
 *  listesinde cevrilerek gosterilir. Kullanicinin elle yazdigi sebep bu
 *  kumede olmadigi icin oldugu gibi gecer. */
const END_REASON_CODES = new Set([
  "expired",
  "revoked",
  "clock_skew",
  "lease_corrupt",
  "cli"
]);

function parseMetadata(raw?: string | null): Record<string, unknown> {
  if (!raw) return {};
  try {
    const parsed: unknown = JSON.parse(raw);
    return parsed && typeof parsed === "object" && !Array.isArray(parsed)
      ? (parsed as Record<string, unknown>)
      : {};
  } catch {
    return {};
  }
}

function numberOrNull(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function stringOrNull(value: unknown): string | null {
  return typeof value === "string" && value.trim().length > 0 ? value.trim() : null;
}

export function RemoteAccessPage({ accessToken }: Props) {
  const { t } = useTranslation();
  const toast = useToast();

  const [status, setStatus] = useState<RemoteAccessStatus | null>(null);
  const [audit, setAudit] = useState<SystemEvent[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);

  /** Geri sayim SUNUCU degeriyle beslenir; her yoklamada resenkron olur.
   *  Istemci saatiyle (expires_at - Date.now()) hesaplamak saha mini
   *  PC'lerinde "kalan sure: -3 saat" gibi sonuclar uretiyordu. */
  const [remaining, setRemaining] = useState<number | null>(null);

  const [minutes, setMinutes] = useState<number | null>(null);
  const [customOpen, setCustomOpen] = useState(false);
  const [customText, setCustomText] = useState("");
  const [reason, setReason] = useState("");
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState<string | null>(null);
  const [revoking, setRevoking] = useState(false);

  const load = useCallback(async () => {
    try {
      const next = await fetchRemoteAccessStatus(accessToken);
      setStatus(next);
      setRemaining(next.access.remaining_seconds ?? null);
      setLoadError(null);
    } catch (exc) {
      const message = exc instanceof Error ? exc.message : t("remoteAccess.errors.load");
      // 401 sentinel'i ekrana BASILMAZ: oturum yenilenirken gecici olarak
      // gorulebiliyor ve kullaniciya yanlis alarm verirdi.
      if (message !== "session_polling_401") setLoadError(message);
    } finally {
      setLoading(false);
    }
  }, [accessToken, t]);

  const loadAudit = useCallback(async () => {
    try {
      setAudit(await fetchRemoteAccessAudit(accessToken));
    } catch {
      // Gecmis ikincil bilgi — hatasi ana akisi bozmasin.
    }
  }, [accessToken]);

  usePolling({ enabled: true, intervalMs: REFRESH_INTERVAL_SEC * 1000, fn: load });

  useEffect(() => {
    void loadAudit();
  }, [loadAudit]);

  const open = Boolean(status?.access.open);

  // Yerel 1 sn'lik sayac. usePolling sekme arka plandayken duruyor; sekme one
  // gelince ANINDA sunucu degeri geldigi icin burada ayrica gorunurluk
  // yonetmeye gerek yok.
  useEffect(() => {
    if (!open) return undefined;
    const id = window.setInterval(() => {
      setRemaining((prev) => (prev === null ? null : Math.max(0, prev - 1)));
    }, 1000);
    return () => window.clearInterval(id);
  }, [open]);

  // Yerelde sifira dustu: ekranda "ACIK" yazili kalmasin, sunucuya sor.
  useEffect(() => {
    if (open && remaining === 0) {
      void load();
      void loadAudit();
    }
  }, [open, remaining, load, loadAudit]);

  // ---- Sure secenekleri (sinirlar backend'den) ------------------------------
  const minMinutes = status?.min_duration_minutes ?? 15;
  const maxMinutes = status?.max_duration_minutes ?? 1440;

  const presets = useMemo(
    () => PRESET_MINUTES.filter((m) => m >= minMinutes && m <= maxMinutes),
    [minMinutes, maxMinutes]
  );

  // Varsayilan sure: 2 saat varsa o, yoksa listenin ortasi. Kullanicinin
  // secimi polling ile EZILMEZ; yalnizca site tavani secilen sureyi gecersiz
  // kilarsa (ornegin tavan 4 saate cekilirse) secim kendini duzeltir.
  useEffect(() => {
    if (presets.length === 0) return;
    if (minutes !== null && presets.includes(minutes)) return;
    setMinutes(presets.includes(120) ? 120 : presets[Math.floor(presets.length / 2)]);
  }, [minutes, presets]);

  const customMinutes = useMemo(() => {
    const raw = customText.trim();
    if (!/^\d{1,5}$/.test(raw)) return null;
    return Number(raw);
  }, [customText]);

  const customError = useMemo(() => {
    if (!customOpen || customText.trim().length === 0) return null;
    if (customMinutes === null) return t("remoteAccess.grant.customInvalid");
    if (customMinutes < minMinutes || customMinutes > maxMinutes) {
      return t("remoteAccess.grant.customRange", {
        min: formatDurationMinutes(minMinutes, t),
        max: formatDurationMinutes(maxMinutes, t)
      });
    }
    return null;
  }, [customOpen, customText, customMinutes, minMinutes, maxMinutes, t]);

  const effectiveMinutes = customOpen ? (customError === null ? customMinutes : null) : minutes;

  /** Secilen sure somutlastirilir: kullanici "8 saat"i degil, KAPANIS SAATINI
   *  anlamali. Bitis baska bir gune tasiyorsa tarih de yazilir. */
  const endsAtPreview = useMemo(() => {
    if (effectiveMinutes === null) return null;
    const end = new Date(Date.now() + effectiveMinutes * 60_000);
    const sameDay = end.toDateString() === new Date().toDateString();
    return sameDay ? formatClockShort(end.toISOString()) : formatClockFull(end.toISOString());
  }, [effectiveMinutes]);

  /** "Sureyi uzat" GERCEKTEN uzatiyor mu?
   *
   *  Grant ucu bitisi "SIMDI + secilen sure" olarak yeniden yazar. Erisim
   *  ACIKKEN kalan sureden KISA bir secim yapilirsa buton adi "uzat" dese de
   *  sure KISALIR — kullanici tam tersini bekler. Sessizce yapmak yerine
   *  uyariyoruz (engellemiyoruz: kasitli kisaltmak mesru bir istek). */
  const shortensAccess = useMemo(() => {
    if (!open || effectiveMinutes === null || remaining === null) return false;
    return effectiveMinutes * 60 < remaining;
  }, [open, effectiveMinutes, remaining]);

  // ---- Durum turetmeleri ----------------------------------------------------
  const available = status?.available === true;
  const registered = status?.tailscale.registered === true;
  const canGrant = status?.can_grant === true;
  const pending = status?.pending === true;
  // Esigi BACKEND belirliyor (STALE_STATE_SECONDS); burada tekrar 180 yazmak
  // iki tarafi sessizce ayirabilirdi. Ajanin susmasi kritik: o durumda acik
  // bir izin KENDILIGINDEN KAPANMAYABILIR.
  const staleAgent = status?.reason === "state_stale";
  const overdue = status?.access.overdue === true;
  const applyFailed = status?.last_apply?.status === "failed";

  // Acik ise ACIK. Kapali ve her sey yerindeyse KAPALI (guvenli hal). Ajan
  // yok / cihaz kayitli degilse "kapali" DEMIYORUZ — bilmiyoruz, oyle de
  // yaziyoruz. Sonsuz spinner yerine ilk okumada kisa bir "loading" hali var.
  // `pending`: istek kuyrukta, ajan henuz UYGULAMADI. Bu arada olculen durum
  // hala ESKI degeri gosterir. Eskiden salter bu sirada "KAPALI" diye KESIN
  // konusuyordu; ayni anda "izin verildi" toast'i cikiyordu ve iki mesaj ~10
  // saniye birbirini yalanliyordu. Artik ara hali ACIKCA gosteriyoruz.
  const mode: Mode =
    status === null
      ? loading
        ? "loading"
        : "unknown"
      : pending
      ? "applying"
      : open
      ? "on"
      : available && registered
      ? "off"
      : "unknown";

  /** Neden izin verilemiyor? Tek bir sebep kodu — metin i18n'den gelir. */
  const blockedReason: string | null =
    status === null
      ? null
      : !available
      ? status.reason ?? "unknown"
      : !registered
      ? "not_registered"
      : status.tailscale.key_expired
      ? "key_expired"
      : !status.tailscale.daemon_running
      ? "daemon_not_running"
      : null;

  const formDisabled = blockedReason !== null || !canGrant || pending || submitting || revoking;

  // ---- Islemler -------------------------------------------------------------
  const submitGrant = async () => {
    if (effectiveMinutes === null) return;
    setSubmitting(true);
    setSubmitError(null);
    try {
      await grantRemoteAccess(accessToken, {
        duration_minutes: effectiveMinutes,
        reason: reason.trim() || null,
        // Destek ekibinin gercekten is yapabilmesi icin gerekli. Kullaniciya
        // ayri bir secenek olarak SORULMUYOR: "yarim izin" kavrami musteriyi
        // yanlis guvene sokar, ekran ise ne verildigini zaten duz yaziyor.
        allow_ssh: true
      });
      setConfirmOpen(false);
      setReason("");
      toast.success(
        t("remoteAccess.toasts.granted", {
          duration: formatDurationMinutes(effectiveMinutes, t)
        })
      );
      notifyRemoteAccessChanged();
      await load();
      await loadAudit();
    } catch (exc) {
      setSubmitError(exc instanceof Error ? exc.message : t("remoteAccess.errors.grant"));
    } finally {
      setSubmitting(false);
    }
  };

  // KAPATMA ONAY ISTEMEZ: guvenli yon budur, oraya surtunme koymak
  // kullaniciyi cezalandirmak olurdu.
  const revoke = async () => {
    setRevoking(true);
    try {
      await revokeRemoteAccess(accessToken);
      toast.success(t("remoteAccess.toasts.revoked"));
      notifyRemoteAccessChanged();
      await load();
      await loadAudit();
    } catch (exc) {
      toast.error(exc instanceof Error ? exc.message : t("remoteAccess.errors.revoke"));
    } finally {
      setRevoking(false);
    }
  };

  const grantedBy = status?.access.granted_by ?? null;
  const grantReason = status?.access.reason ?? null;

  return (
    <section className="tab-panel rad-page">
      {/* ================= SALTER — sayfanin tek ana mesaji ================= */}
      <div className={`rad-switch is-${mode}`}>
        {/* Sebeke salteri metaforu: I = acik, O = kapali. Dekoratif; asil
            bilgi yanindaki metinde. */}
        <span className="rad-breaker" aria-hidden="true">
          <span className="rad-breaker-marks">
            <b>I</b>
            <b>O</b>
          </span>
          <span className="rad-breaker-track">
            <span className="rad-breaker-knob" />
          </span>
        </span>

        <div className="rad-switch-body">
          <span className="rad-switch-pill">
            <span className="rad-dot" aria-hidden="true" />
            {t(HERO_TEXT[mode].state)}
          </span>
          <h2>
            {mode === "on" ? (
              <LockOpen size={20} strokeWidth={2.2} />
            ) : mode === "off" ? (
              <Lock size={20} strokeWidth={2.2} />
            ) : mode === "loading" ? (
              <Loader2 size={20} strokeWidth={2.2} className="net-spin" />
            ) : (
              <PlugZap size={20} strokeWidth={2.2} />
            )}
            {t(HERO_TEXT[mode].title)}
          </h2>
          <p>{t(HERO_TEXT[mode].lead)}</p>

          {mode === "on" ? (
            <ul className="rad-switch-meta">
              <li>
                <CalendarClock size={14} />
                {t("remoteAccess.hero.endsAt", {
                  time: formatClockFull(status?.access.expires_at)
                })}
              </li>
              {grantedBy ? (
                <li>
                  <UserCheck size={14} />
                  {t("remoteAccess.hero.grantedBy", {
                    user: grantedBy,
                    time: formatClockFull(status?.access.granted_at)
                  })}
                </li>
              ) : null}
              {grantReason ? (
                <li>
                  <History size={14} />
                  {t("remoteAccess.hero.grantReason", { note: grantReason })}
                </li>
              ) : null}
            </ul>
          ) : null}
        </div>

        {mode === "on" ? (
          <div className="rad-switch-actions">
            <div className="rad-countdown">
              <strong>{formatRemaining(remaining, t)}</strong>
              <span>{t("remoteAccess.hero.remaining")}</span>
            </div>
            {canGrant ? (
              <button
                type="button"
                className="danger-btn rad-revoke-btn"
                disabled={revoking}
                onClick={() => void revoke()}
              >
                {revoking ? t("remoteAccess.revoke.working") : t("remoteAccess.revoke.button")}
              </button>
            ) : null}
          </div>
        ) : null}
      </div>

      {/* ================= Uyari seritleri ================= */}
      {loadError ? <p className="net-banner net-banner--bad">{loadError}</p> : null}

      {overdue ? (
        <p className="net-banner net-banner--bad">
          <ShieldAlert size={16} />
          {t("remoteAccess.warn.overdue")}
        </p>
      ) : null}

      {staleAgent && available ? (
        <p className="net-banner net-banner--bad">
          <AlertTriangle size={16} />
          {t("remoteAccess.warn.staleAgent")}
        </p>
      ) : null}

      {applyFailed ? (
        <p className="net-banner net-banner--bad">
          <AlertTriangle size={16} />
          {t("remoteAccess.warn.applyFailed", {
            error: status?.last_apply?.error ?? ""
          })}
        </p>
      ) : null}

      {pending ? (
        <p className="net-banner net-banner--info">
          <Loader2 size={16} className="net-spin" />
          {t("remoteAccess.warn.pending")}
        </p>
      ) : null}

      {open && remaining !== null && remaining > 0 && remaining < EXPIRING_SOON_SEC ? (
        <p className="net-banner net-banner--warn">
          <Hourglass size={16} />
          {t("remoteAccess.warn.expiringSoon")}
        </p>
      ) : null}

      {/* ================= Iki sutun ================= */}
      <div className="rad-columns">
        {/* ---- SOL: izin ver / uzat ---- */}
        <section className="rad-card">
          <header className="rad-card-head">
            <h3>
              {t(open ? "remoteAccess.grant.titleExtend" : "remoteAccess.grant.title")}
            </h3>
            <button
              type="button"
              className="secondary-btn net-refresh"
              disabled={loading}
              onClick={() => {
                void load();
                void loadAudit();
              }}
            >
              <RefreshCw size={16} className={loading ? "net-spin" : ""} />
              {t("common.refresh")}
            </button>
          </header>

          {status === null ? (
            <div className="rad-blocked">
              {loading ? (
                <>
                  <Loader2 size={22} className="net-spin" />
                  <strong>{t("remoteAccess.loading")}</strong>
                </>
              ) : (
                <>
                  <AlertTriangle size={22} />
                  <strong>{t("remoteAccess.errors.load")}</strong>
                </>
              )}
            </div>
          ) : blockedReason !== null ? (
            <div className="rad-blocked">
              <PlugZap size={22} />
              <strong>{t("remoteAccess.blocked.title")}</strong>
              <p>{t(`remoteAccess.blocked.reason.${blockedReason}`)}</p>
              <small>{t("remoteAccess.blocked.hint")}</small>
            </div>
          ) : !canGrant ? (
            <div className="rad-blocked">
              <ShieldCheck size={22} />
              <strong>{t("remoteAccess.readOnly.title")}</strong>
              <p>{t("remoteAccess.readOnly.lead")}</p>
            </div>
          ) : (
            <>
              {/* SURE — bu ozelligin kalbi. Ikincil bir ayar degil, ekranin
                  ana karari; o yuzden buyuk segment kontrol. */}
              <div className="rad-field">
                <span className="rad-field-label">
                  <Timer size={13} />
                  {t("remoteAccess.grant.durationLabel")}
                </span>
                <div className="rad-durations">
                  {presets.map((m) => (
                    <button
                      key={m}
                      type="button"
                      className={`rad-duration${!customOpen && minutes === m ? " is-active" : ""}`}
                      disabled={formDisabled}
                      onClick={() => {
                        setCustomOpen(false);
                        setMinutes(m);
                      }}
                    >
                      <strong>{formatDurationMinutes(m, t)}</strong>
                    </button>
                  ))}
                  <button
                    type="button"
                    className={`rad-duration rad-duration--custom${customOpen ? " is-active" : ""}`}
                    disabled={formDisabled}
                    onClick={() => setCustomOpen(true)}
                  >
                    <strong>{t("remoteAccess.grant.custom")}</strong>
                  </button>
                </div>
              </div>

              {customOpen ? (
                <label className="rad-field rad-custom">
                  <span className="rad-field-label">
                    {t("remoteAccess.grant.customLabel")}
                  </span>
                  <span className="rad-custom-row">
                    <input
                      value={customText}
                      disabled={formDisabled}
                      inputMode="numeric"
                      autoFocus
                      placeholder={String(minMinutes)}
                      onChange={(event) =>
                        setCustomText(event.target.value.replace(/[^\d]/g, ""))
                      }
                    />
                    <span className="rad-custom-unit">
                      {t("remoteAccess.grant.customUnit")}
                    </span>
                  </span>
                  <small className={customError ? "rad-custom-error" : "helper-text"}>
                    {customError ??
                      t("remoteAccess.grant.customRange", {
                        min: formatDurationMinutes(minMinutes, t),
                        max: formatDurationMinutes(maxMinutes, t)
                      })}
                  </small>
                </label>
              ) : null}

              <label className="rad-field">
                <span className="rad-field-label">
                  {t("remoteAccess.grant.reasonLabel")}
                </span>
                <textarea
                  rows={2}
                  value={reason}
                  maxLength={200}
                  disabled={formDisabled}
                  placeholder={t("remoteAccess.grant.reasonPlaceholder")}
                  onChange={(event) => setReason(event.target.value)}
                />
                <small className="helper-text">{t("remoteAccess.grant.reasonHint")}</small>
              </label>

              {submitError ? <p className="error-text">{submitError}</p> : null}

              {/* Karar anindan ONCE somut sonuc: kullanici "8 saat"i degil,
                  "ne zaman kapanacagini" gormeli. */}
              <div className="rad-endsat">
                <Clock size={15} />
                {effectiveMinutes === null ? (
                  <span>{t("remoteAccess.grant.pickDuration")}</span>
                ) : (
                  <>
                    <span>{t("remoteAccess.grant.endsAtPreview")}</span>
                    <strong className="rad-endsat-time">{endsAtPreview}</strong>
                  </>
                )}
              </div>
              {shortensAccess ? (
                <p className="rad-endsat-warn">{t("remoteAccess.grant.shortensWarn")}</p>
              ) : null}

              <div className="rad-actions">
                <button
                  type="button"
                  className="primary-btn rad-grant-btn"
                  disabled={formDisabled || effectiveMinutes === null}
                  onClick={() => {
                    setSubmitError(null);
                    setConfirmOpen(true);
                  }}
                >
                  <Headset size={17} />
                  {t(open ? "remoteAccess.grant.submitExtend" : "remoteAccess.grant.submit")}
                </button>
              </div>
            </>
          )}
        </section>

        {/* ---- SAG: son islemler ----
             Kullanici istegi: gecmis sagda dursun, "ne olur" aciklamasi
             en altta tam genislikte. Denetim satirlari kisa oldugu icin dar
             sutuna oturuyor; aciklama metinleri genis alanda daha rahat
             okunuyor ve sag sutundaki buyuk bosluk kapaniyor. */}
        <section className="rad-card rad-log">
          <header className="rad-card-head">
            <h3>
              <History size={16} />
              {t("remoteAccess.log.title")}
            </h3>
            <small>{t("remoteAccess.log.hint")}</small>
          </header>
          {audit.length === 0 ? (
            <p className="net-empty">{t("remoteAccess.log.empty")}</p>
          ) : (
            <ul className="rad-log-list">
              {audit.map((event) => {
                const meta = parseMetadata(event.metadata_json);
                const durationMinutes = numberOrNull(meta.duration_minutes);
                // `reason` iki farkli seyi tasiyabiliyor: kullanicinin
                // yazdigi SERBEST METIN ya da ajanin urettigi MAKINE KODU
                // (expired / clock_skew / lease_corrupt ...). Kod oldugu
                // gibi basildiginda musteri ekraninda "clock_skew" gibi
                // anlamsiz bir ifade goruyordu. Bilinen kodlari cevir,
                // tanimadigimizi oldugu gibi birak.
                const rawNote = stringOrNull(meta.reason);
                const note =
                  rawNote && END_REASON_CODES.has(rawNote)
                    ? t(`remoteAccess.endReason.${rawNote}`)
                    : rawNote;
                return (
                  <li key={event.id} className={`rad-log-row is-${event.event_type}`}>
                    <span className="rad-log-icon">
                      {event.event_type === "remote_access_revoked" ? (
                        <ShieldCheck size={15} />
                      ) : event.event_type === "remote_access_session_ended" ? (
                        <Hourglass size={15} />
                      ) : event.event_type === "remote_access_apply_failed" ? (
                        <AlertTriangle size={15} />
                      ) : (
                        <ShieldAlert size={15} />
                      )}
                    </span>
                    <time>{formatClockFull(event.created_at)}</time>
                    <span className="rad-log-text">
                      {formatEventMessage(event)}
                      {note ? <em>{note}</em> : null}
                    </span>
                    <span className="rad-log-side">
                      {durationMinutes !== null ? (
                        <span className="rad-log-chip">
                          {formatDurationMinutes(durationMinutes, t)}
                        </span>
                      ) : null}
                      {event.actor_username ? <b>{event.actor_username}</b> : null}
                    </span>
                  </li>
                );
              })}
            </ul>
          )}
        </section>
      </div>

      {/* ================= Izin verdiginizde ne olur? =================
           EN ALTTA ve TAM GENISLIKTE: musteri burada guvenlik karari
           veriyor, metinlerin sikismamasi gerekiyor. */}
      <section className="rad-card rad-explain rad-explain--wide">
        <header className="rad-card-head">
          <h3>{t("remoteAccess.explain.title")}</h3>
        </header>
        <ul className="rad-explain-list">
          <li>
            <span className="rad-explain-icon">
              <Headset size={17} />
            </span>
            <span>
              <strong>{t("remoteAccess.explain.whoTitle")}</strong>
              <small>{t("remoteAccess.explain.whoText")}</small>
            </span>
          </li>
          <li>
            <span className="rad-explain-icon">
              <Timer size={17} />
            </span>
            <span>
              <strong>{t("remoteAccess.explain.howLongTitle")}</strong>
              <small>
                {t("remoteAccess.explain.howLongText", {
                  max: formatDurationMinutes(maxMinutes, t)
                })}
              </small>
            </span>
          </li>
          <li>
            <span className="rad-explain-icon">
              <Lock size={17} />
            </span>
            <span>
              <strong>{t("remoteAccess.explain.afterTitle")}</strong>
              <small>{t("remoteAccess.explain.afterText")}</small>
            </span>
          </li>
        </ul>

        {/* Cihazin bakim agindaki kimligi — teknik ama musteri "hangi cihaz"
            sorusunun cevabini gormeli. */}
        <div className="rad-device">
          <div>
            <span>{t("remoteAccess.device.name")}</span>
            <strong>{status?.tailscale.hostname ?? "—"}</strong>
          </div>
          <div>
            <span>{t("remoteAccess.device.address")}</span>
            <strong>{status?.tailscale.ipv4 ?? "—"}</strong>
          </div>
          <div>
            <span>{t("remoteAccess.device.link")}</span>
            <strong className={registered ? "is-ok" : "is-off"}>
              {t(
                registered
                  ? "remoteAccess.device.linkRegistered"
                  : "remoteAccess.device.linkMissing"
              )}
            </strong>
          </div>
        </div>
      </section>

      {/* ================= Onay modali — TEK surtunme noktasi ================= */}
      {confirmOpen && effectiveMinutes !== null ? (
        <div
          className="settings-modal-backdrop"
          onClick={() => !submitting && setConfirmOpen(false)}
        >
          <div
            className="settings-modal rad-confirm"
            role="dialog"
            aria-modal="true"
            onClick={(event) => event.stopPropagation()}
          >
            <h3>
              <ShieldAlert size={20} />
              {t(open ? "remoteAccess.confirm.titleExtend" : "remoteAccess.confirm.title")}
            </h3>
            <p className="rad-confirm-lead">{t("remoteAccess.confirm.lead")}</p>

            <ul className="rad-confirm-list">
              <li>
                <Headset size={15} />
                {t("remoteAccess.confirm.who")}
              </li>
              <li>
                <ShieldAlert size={15} />
                {t("remoteAccess.confirm.what")}
              </li>
              <li>
                <Timer size={15} />
                {t("remoteAccess.confirm.howLong", {
                  duration: formatDurationMinutes(effectiveMinutes, t)
                })}
              </li>
              <li>
                <Lock size={15} />
                {t("remoteAccess.confirm.earlyClose")}
              </li>
              <li>
                <History size={15} />
                {t("remoteAccess.confirm.audit")}
              </li>
            </ul>

            <ul className="net-confirm-summary">
              <li>
                <span>{t("remoteAccess.confirm.rowDuration")}</span>
                <strong>{formatDurationMinutes(effectiveMinutes, t)}</strong>
              </li>
              <li>
                <span>{t("remoteAccess.confirm.rowEndsAt")}</span>
                <strong>{endsAtPreview ?? "—"}</strong>
              </li>
              {reason.trim() ? (
                <li>
                  <span>{t("remoteAccess.confirm.rowReason")}</span>
                  <strong>{reason.trim()}</strong>
                </li>
              ) : null}
            </ul>

            {submitError ? <p className="error-text">{submitError}</p> : null}

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
                className="primary-btn"
                disabled={submitting}
                onClick={() => void submitGrant()}
              >
                {submitting
                  ? t("remoteAccess.confirm.working")
                  : t("remoteAccess.confirm.approve")}
              </button>
            </div>
          </div>
        </div>
      ) : null}
    </section>
  );
}
