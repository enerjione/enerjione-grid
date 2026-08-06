/**
 * Gateway kontrol + uzaktan log — Sistem Durumu sayfasi karti.
 *
 * NEDEN BURADA: "veri neden gelmiyor" sorusunun teshisi bu sayfada basliyor
 * (cihaz sayimlari, servis sagligi). Cevap cogu zaman gateway'de ve eskiden
 * kullanici Cihazlar sayfasina gidip kurulum akisini acmak zorundaydi.
 *
 * YETKI — arayuz backend'i TEKRAR EDER, yerine gecmez:
 *   Durdur/Baslat/Yeniden baslat -> yalniz installer (backend: require_role)
 *   Log al/goruntule             -> installer + engineer (backend: require_roles)
 * Yetkisiz rolde butonlar hic cizilmez; backend zaten 403 doner.
 *
 * ASENKRON: ucu de 202 doner, isi host ajani (e1-gwd) yapar. Sonuc
 * `last_apply` ile izlenir — bu yuzden istek sonrasi durum yenilemesi
 * hizlanir (POLL_FAST_MS) ve is bitince normale doner.
 *
 * GORUNUM (2026-08-06, kullanici istegi): kart minimal tutulur —
 *   * "Son islem: logs · GW-001 — 300 satir log alindi" satiri KALDIRILDI.
 *     Basarili islem zaten toast ile bildiriliyor ve durum rozetinden
 *     okunuyor; ekranda kalici bir islem gunlugu tutulmuyor. BASARISIZLIK
 *     ise ilgili gateway satirinin altinda gosterilir — istek "gonderildi"
 *     dedikten sonra ajanda patlarsa kullanici bunu baska yerden ogrenemez.
 *   * Log ciktisi kartin altinda DEGIL POPUP'ta acilir; uzun cikti sayfayi
 *     asagi itip Sistem Durumu'nun geri kalanini ekrandan kaciriyordu.
 *   * Aksiyonlar ikon dugme; uc metinli buton satiri doldurup kalabalik
 *     yapiyordu.
 */
import { useCallback, useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { AlertTriangle, FileText, Play, RefreshCw, RotateCw, Square } from "lucide-react";

import { asyncConfirm } from "../../components/ConfirmDialog";
import { useToast } from "../../components/ToastProvider";
import {
  fetchGatewayAgentStatus,
  fetchGatewayLogs,
  requestGatewayLogs,
  restartGatewayLocally,
  startGatewayLocally,
  stopGatewayLocally
} from "../../shared/api";
import { usePolling } from "../../shared/usePolling";
import type { GatewayAgentStatus, GatewayLogs, UserRole } from "../../shared/types";

type Props = {
  accessToken: string;
  role: UserRole;
};

/** Ajan durumu yenileme: normalde 15 sn, bekleyen istek varken 3 sn. */
const POLL_IDLE_MS = 15_000;
const POLL_FAST_MS = 3_000;
const LOG_TAIL_OPTIONS = [100, 300, 1000, 2000];

type Action = "stop" | "start" | "restart";

export function GatewayControlCard({ accessToken, role }: Props) {
  const { t } = useTranslation();
  const toast = useToast();

  const [agent, setAgent] = useState<GatewayAgentStatus | null>(null);
  const [busyCode, setBusyCode] = useState<string | null>(null);

  const [logCode, setLogCode] = useState<string | null>(null);
  const [logTail, setLogTail] = useState(300);
  const [logs, setLogs] = useState<GatewayLogs | null>(null);
  const [logBusy, setLogBusy] = useState(false);
  const [logError, setLogError] = useState<string | null>(null);
  // Log istegi gonderildikten sonra ciktinin gelmesini bekleyen sayac;
  // ajan dosyayi yazana kadar birkac saniye gecer.
  const logWaitRef = useRef<number | null>(null);

  const canControl = role === "installer";
  const canViewLogs = role === "installer" || role === "engineer";

  const loadAgent = useCallback(async () => {
    try {
      setAgent(await fetchGatewayAgentStatus(accessToken));
    } catch {
      // fetchGatewayAgentStatus zaten hata durumunda available:false doner;
      // buraya dusmesi beklenmiyor, sessiz gec.
    }
  }, [accessToken]);

  usePolling({
    enabled: true,
    intervalMs: agent?.pending ? POLL_FAST_MS : POLL_IDLE_MS,
    fn: loadAgent
  });

  useEffect(() => {
    return () => {
      if (logWaitRef.current !== null) window.clearTimeout(logWaitRef.current);
    };
  }, []);

  const runAction = async (code: string, action: Action) => {
    // Durdurma VERI AKISINI KESER — tek onay noktasi burasi.
    if (action === "stop" || action === "restart") {
      const soru =
        action === "stop"
          ? t("systemStatus.gwControl.confirmStop", { code })
          : t("systemStatus.gwControl.confirmRestart", { code });
      if (!(await asyncConfirm(soru))) return;
    }
    setBusyCode(code);
    try {
      if (action === "stop") await stopGatewayLocally(accessToken, code);
      else if (action === "start") await startGatewayLocally(accessToken, code);
      else await restartGatewayLocally(accessToken, code);
      toast.success(t("systemStatus.gwControl.requested"));
      await loadAgent();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : t("common.errorOccurred"));
    } finally {
      setBusyCode(null);
    }
  };

  /** Ajandan yeni log iste, sonra ciktiyi oku (ajan birkac saniye surer). */
  const pullLogs = async (code: string) => {
    setLogBusy(true);
    setLogError(null);
    try {
      await requestGatewayLogs(accessToken, code, logTail);
      // Ajan istegi isleyene kadar bekle, sonra oku. Tek denemede
      // yakalayamazsak ikinci kez dene — ajan timer'i 30 sn'de bir kosuyor
      // olabilir, ama path unit ile genelde saniyeler icinde isler.
      const oku = async (kalanDeneme: number) => {
        const data = await fetchGatewayLogs(accessToken, code);
        if (data.available && !data.stale) {
          setLogs(data);
          setLogBusy(false);
          return;
        }
        if (kalanDeneme <= 0) {
          // Eski cikti varsa yine goster; kullanici tazeligi damgadan gorur.
          setLogs(data.available ? data : null);
          if (!data.available) setLogError(t("systemStatus.gwControl.logTimeout"));
          setLogBusy(false);
          return;
        }
        logWaitRef.current = window.setTimeout(() => void oku(kalanDeneme - 1), 2000);
      };
      logWaitRef.current = window.setTimeout(() => void oku(6), 1500);
    } catch (err) {
      setLogError(err instanceof Error ? err.message : t("common.errorOccurred"));
      setLogBusy(false);
    }
  };

  const openLogs = async (code: string) => {
    setLogCode(code);
    setLogs(null);
    setLogError(null);
    // Once VARSA onceki ciktiyi goster — bekletmeden bir sey gostermek,
    // bos ekranda "Log Al"i beklemekten iyi.
    try {
      const mevcut = await fetchGatewayLogs(accessToken, code);
      if (mevcut.available) setLogs(mevcut);
    } catch {
      /* yoksa sorun degil */
    }
  };

  const gateways = agent?.gateways ?? [];
  const lastApply = agent?.last_apply ?? null;

  return (
    <section className="sys-card sys-card--gw">
      <header className="sys-card-head">
        <div className="sys-card-title-wrap">
          <span className="sys-card-icon sys-card-icon--gw">
            <RotateCw size={17} strokeWidth={2.1} />
          </span>
          <h2 className="sys-card-title">{t("systemStatus.gwControl.title")}</h2>
        </div>
        <div className="sys-head-meta">
          {agent?.pending ? (
            <span className="sys-pill sys-pill--warn">
              {t("systemStatus.gwControl.pending")}
            </span>
          ) : null}
          <button
            type="button"
            className="sys-refresh-btn sys-refresh-btn--sm"
            onClick={() => void loadAgent()}
          >
            <RefreshCw size={14} strokeWidth={2.2} />
          </button>
        </div>
      </header>

      {agent && !agent.available ? (
        <p className="sys-loading-banner">
          {t("systemStatus.gwControl.agentUnavailable")}
        </p>
      ) : gateways.length === 0 ? (
        <p className="sys-loading-banner">{t("systemStatus.gwControl.empty")}</p>
      ) : (
        <div className="gwc-list">
          {gateways.map((gw) => {
            const calisiyor = gw.state === "running";
            const busy = busyCode === gw.code || Boolean(agent?.pending);
            // Son ajan islemi BU gateway'e mi aitti ve BASARISIZ mi bitti?
            // Basarili islem satir alti metni olarak YAZILMAZ (kullanici
            // istegi): zaten toast ile bildirildi ve durum rozeti guncellendi.
            // Basarisizlik ise sessiz gecilemez — istek "gonderildi" toast'i
            // aldiktan sonra ajanda patlarsa kullanici bunu baska hicbir
            // yerden ogrenemez.
            const hata =
              lastApply && lastApply.ok === false && lastApply.code === gw.code
                ? lastApply
                : null;
            return (
              <div
                key={gw.code}
                className={`gwc-row ${calisiyor ? "is-run" : "is-stop"}${
                  hata ? " has-error" : ""
                }`}
              >
                <span className={`gwc-dot ${calisiyor ? "is-run" : "is-stop"}`} aria-hidden="true" />
                <span className="gwc-ident">
                  <strong>{gw.code}</strong>
                  <small title={gw.image ?? undefined}>
                    {gw.status ?? gw.state ?? "—"}
                  </small>
                </span>

                <span className={`gwc-state ${calisiyor ? "is-run" : "is-stop"}`}>
                  {calisiyor
                    ? t("systemStatus.gwControl.running")
                    : t("systemStatus.gwControl.stopped")}
                </span>

                {/* Aksiyonlar IKON dugmeler: uc metinli buton satiri
                    doldurup karti kalabaliklastiriyordu. Erisim icin
                    aria-label + title birlikte veriliyor. */}
                <span className="gwc-actions">
                  {canControl ? (
                    <>
                      {calisiyor ? (
                        <button
                          type="button"
                          className="gwc-icon-btn is-stop"
                          disabled={busy}
                          onClick={() => void runAction(gw.code, "stop")}
                          title={t("systemStatus.gwControl.stop")}
                          aria-label={t("systemStatus.gwControl.stop")}
                        >
                          <Square size={15} strokeWidth={2.4} />
                        </button>
                      ) : (
                        <button
                          type="button"
                          className="gwc-icon-btn is-start"
                          disabled={busy}
                          onClick={() => void runAction(gw.code, "start")}
                          title={t("systemStatus.gwControl.start")}
                          aria-label={t("systemStatus.gwControl.start")}
                        >
                          <Play size={15} strokeWidth={2.4} />
                        </button>
                      )}
                      <button
                        type="button"
                        className="gwc-icon-btn"
                        disabled={busy || !calisiyor}
                        onClick={() => void runAction(gw.code, "restart")}
                        title={t("systemStatus.gwControl.restart")}
                        aria-label={t("systemStatus.gwControl.restart")}
                      >
                        <RotateCw
                          size={15}
                          strokeWidth={2.4}
                          className={busy ? "sys-spin" : undefined}
                        />
                      </button>
                    </>
                  ) : null}
                  {canViewLogs ? (
                    <button
                      type="button"
                      className="gwc-icon-btn"
                      onClick={() => void openLogs(gw.code)}
                      title={t("systemStatus.gwControl.logs")}
                      aria-label={t("systemStatus.gwControl.logs")}
                    >
                      <FileText size={15} strokeWidth={2.4} />
                    </button>
                  ) : null}
                </span>

                {/* Yalnizca BASARISIZ son islem — satirin altinda ince serit. */}
                {hata ? (
                  <p className="gwc-row-error">
                    <AlertTriangle size={13} strokeWidth={2.4} />
                    {t("systemStatus.gwControl.applyFailed", {
                      action: hata.action ?? "—",
                      message: hata.message ?? "—"
                    })}
                  </p>
                ) : null}
              </div>
            );
          })}
        </div>
      )}

      {/* --- Uzaktan log: POPUP (kullanici istegi) ---
          Eskiden kartin ALTINDA aciliyordu; uzun cikti sayfayi asagi
          itiyor ve Sistem Durumu'nun geri kalanini ekrandan kaciriyordu. */}
      {canViewLogs && logCode ? (
        <div
          className="settings-modal-backdrop"
          onClick={() => {
            setLogCode(null);
            setLogs(null);
          }}
        >
          <div
            className="settings-modal gwc-log-modal"
            role="dialog"
            aria-modal="true"
            onClick={(e) => e.stopPropagation()}
          >
            <header className="gwc-log-head">
              <span className="gwc-log-icon">
                <FileText size={17} strokeWidth={2.1} />
              </span>
              <h3>{t("systemStatus.gwControl.logsFor", { code: logCode })}</h3>
              <button
                type="button"
                className="gw-wizard-close"
                onClick={() => {
                  setLogCode(null);
                  setLogs(null);
                }}
                aria-label={t("common.close")}
              >
                ×
              </button>
            </header>

            <div className="gwc-log-toolbar">
              <select
                value={logTail}
                onChange={(e) => setLogTail(Number(e.target.value))}
                disabled={logBusy}
              >
                {LOG_TAIL_OPTIONS.map((n) => (
                  <option key={n} value={n}>
                    {t("systemStatus.gwControl.lastLines", { count: n })}
                  </option>
                ))}
              </select>
              <button
                type="button"
                className="secondary-btn gwc-log-fetch"
                disabled={logBusy}
                onClick={() => void pullLogs(logCode)}
              >
                <RefreshCw
                  size={14}
                  strokeWidth={2.4}
                  className={logBusy ? "sys-spin" : undefined}
                />
                {logBusy
                  ? t("systemStatus.gwControl.logFetching")
                  : t("systemStatus.gwControl.logFetch")}
              </button>
              {/* Cikti damgasi ve tazelik uyarilari araç cubugunun saginda:
                  ayri bir satir daha acmaya degmez. */}
              {logs?.available ? (
                <span className="gwc-log-meta">
                  {logs.generated_at
                    ? new Date(logs.generated_at).toLocaleString()
                    : "—"}
                  {logs.stale ? (
                    <em className="gwc-log-flag">
                      {t("systemStatus.gwControl.logStale")}
                    </em>
                  ) : null}
                  {logs.truncated ? (
                    <em className="gwc-log-flag">
                      {t("systemStatus.gwControl.logTruncated")}
                    </em>
                  ) : null}
                </span>
              ) : null}
            </div>

            {logError ? <p className="sys-error-banner">{logError}</p> : null}

            {logs?.available ? (
              <pre className="gwc-log-body">{logs.output || "—"}</pre>
            ) : !logBusy && !logError ? (
              <p className="gwc-log-empty">{t("systemStatus.gwControl.logEmpty")}</p>
            ) : null}
          </div>
        </div>
      ) : null}
    </section>
  );
}
