/**
 * GatewayCreateModal — "Yeni gateway ekle" sihirbazi.
 *
 * Uc adim:
 *   1. kimlik  — kod + ad. Token OTOMATIK uretilir (elle girilmez).
 *   2. hedef   — "bu cihaza kur" / "baska cihaza kur"
 *   3. sonuc   — bu cihaz: canli kurulum ilerlemesi
 *                baska cihaz: backend adresi + dosya indirme + docker adimlari
 *
 * "Bu cihaza kur" akisi backend'de Docker CALISTIRMAZ; backend host ajanina
 * (e1-gwd) bir istek dosyasi yazar, ajan compose'u kendi kurallariyla
 * dogrulayip calistirir. Ajan kurulu degilse bu secenek KAPALI gorunur ve
 * sebebi yazilir — "baska cihaza kur" akisi her durumda calisir.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import {
  AlertTriangle,
  CheckCircle2,
  Download,
  HardDrive,
  Loader2,
  RefreshCw,
  Server,
  X
} from "lucide-react";

import {
  downloadGatewayCompose,
  fetchGatewayAgentStatus,
  installGatewayLocally
} from "../../shared/api";
import type { GatewayAgentStatus } from "../../shared/types";
import { CopyButton, generateToken } from "./gatewayShared";
import { useModalDialog } from "../../shared/useModalDialog";

type CreatePayload = {
  code: string;
  name: string;
  token: string;
};

type Props = {
  accessToken: string;
  /** Mevcut gateway kodlari — sonraki kodu onermek ve cakismayi onlemek icin. */
  existingCodes: string[];
  /** Gateway kaydini olusturur (DeviceManagementPanel'deki mevcut akis). */
  onCreate: (payload: CreatePayload) => Promise<void>;
  onClose: () => void;
};

type Step = "identity" | "target" | "local" | "remote";

/** Mevcut GW-NNN kodlarina bakarak siradakini oner. */
function suggestCode(existing: string[]): string {
  let max = 0;
  for (const code of existing) {
    const match = /^GW-(\d+)$/i.exec(code.trim());
    if (match) max = Math.max(max, Number(match[1]));
  }
  return `GW-${String(max + 1).padStart(3, "0")}`;
}

export function GatewayCreateModal({
  accessToken,
  existingCodes,
  onCreate,
  onClose
}: Props) {
  const { t } = useTranslation();
  // ESC ile kapanma + odak tuzagi (modal disina Tab ile cikilamasin).
  const dialogRef = useModalDialog<HTMLDivElement>(onClose);

  const [step, setStep] = useState<Step>("identity");
  const [code, setCode] = useState(() => suggestCode(existingCodes));
  const [name, setName] = useState("");
  const [token, setToken] = useState(() => generateToken());
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  // --- host ajani durumu -------------------------------------------------
  const [agent, setAgent] = useState<GatewayAgentStatus | null>(null);
  const [requestId, setRequestId] = useState<string | null>(null);
  // Ajanin bize ait sonucu — request id eslestikten sonra dolar.
  const applied = useMemo(() => {
    if (!requestId) return null;
    const last = agent?.last_apply;
    return last && last.id === requestId ? last : null;
  }, [agent, requestId]);
  const installDone = applied != null && applied.running === false;

  // --- baska cihaz akisi -------------------------------------------------
  const [backendIp, setBackendIp] = useState(() => window.location.hostname);
  const [downloadBusy, setDownloadBusy] = useState(false);

  const loadAgent = useCallback(async () => {
    setAgent(await fetchGatewayAgentStatus(accessToken));
  }, [accessToken]);

  // Modal acilir acilmaz ajan durumunu al — "bu cihaza kur" karti bu bilgiye
  // gore aktif/pasif cizilir.
  useEffect(() => {
    void loadAgent();
  }, [loadAgent]);

  // Kurulum surerken 2 sn'de bir durum cek. Bitince (veya adimdan cikinca)
  // interval temizlenir; aksi halde modal kapansa bile istek atmaya devam eder.
  const timerRef = useRef<number | null>(null);
  useEffect(() => {
    if (step !== "local" || installDone) {
      if (timerRef.current != null) {
        window.clearInterval(timerRef.current);
        timerRef.current = null;
      }
      return;
    }
    timerRef.current = window.setInterval(() => void loadAgent(), 2000);
    return () => {
      if (timerRef.current != null) {
        window.clearInterval(timerRef.current);
        timerRef.current = null;
      }
    };
  }, [step, installDone, loadAgent]);

  const codeTaken = existingCodes.some((c) => c.trim().toLowerCase() === code.trim().toLowerCase());
  const codeValid = /^[A-Za-z0-9][A-Za-z0-9._-]{1,49}$/.test(code.trim());
  const identityOk = codeValid && !codeTaken && name.trim().length > 0;

  const agentUsable = Boolean(agent?.available && agent.docker_available);
  const agentReason = agent?.available ? (agent.docker_available ? null : "docker_missing") : agent?.reason ?? "loading";

  /** Kaydi olustur, sonra secilen hedefe gore adima gec.
   *
   *  `created` bayragi SART: kayit olusup kurulum istegi reddedilirse
   *  (ajan o an mesgul veya kapali) kullanici karta tekrar basiyor ve
   *  ikinci onCreate "bu kod zaten var" hatasi veriyordu — kullanici da
   *  gercek sebebi hic gormuyordu. Kayit bir kez olusur, sonraki denemeler
   *  dogrudan kurulum adimina gider.
   */
  const [created, setCreated] = useState(false);
  const createThen = async (next: "local" | "remote") => {
    setError("");
    setBusy(true);
    try {
      if (!created) {
        await onCreate({ code: code.trim(), name: name.trim(), token });
        setCreated(true);
      }
      if (next === "remote") {
        setStep("remote");
        return;
      }
      const res = await installGatewayLocally(accessToken, code.trim());
      setRequestId(res.request_id);
      setStep("local");
      void loadAgent();
    } catch (err) {
      setError(err instanceof Error ? err.message : t("common.errorOccurred"));
    } finally {
      setBusy(false);
    }
  };

  const backendUrl = useMemo(() => {
    const ip = backendIp.trim();
    if (!ip) return "";
    if (ip.startsWith("http://") || ip.startsWith("https://")) {
      const trimmed = ip.replace(/\/+$/, "");
      return trimmed.includes("/api/v1") ? trimmed : `${trimmed}/api/v1`;
    }
    return `http://${ip}/api/v1`;
  }, [backendIp]);

  const handleDownload = async () => {
    if (!backendUrl) return;
    setError("");
    setDownloadBusy(true);
    try {
      const { blob, filename } = await downloadGatewayCompose(accessToken, code.trim(), {
        backendUrl,
        fmt: "compose"
      });
      const url = window.URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = filename;
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      window.URL.revokeObjectURL(url);
    } catch (err) {
      setError(err instanceof Error ? err.message : t("common.errorOccurred"));
    } finally {
      setDownloadBusy(false);
    }
  };

  const composeFile = `e1-gw-${code.trim().toLowerCase()}.yml`;
  const runCommand = `docker compose -f ${composeFile} up -d`;

  // Ajanin bildirdigi asamayi kullaniciya anlamli bir cumleye cevir.
  const stageLabel = (stage: string | null | undefined): string => {
    switch (stage) {
      case "validate":
        return t("engineering.gateways.wizard.stageValidate");
      case "pull":
        return t("engineering.gateways.wizard.stagePull");
      case "up":
        return t("engineering.gateways.wizard.stageUp");
      default:
        return t("engineering.gateways.wizard.stageStarting");
    }
  };

  return (
    <div className="settings-modal-backdrop">
      <div className="settings-modal gw-wizard" role="dialog" aria-modal="true" ref={dialogRef}>
        <header className="gw-wizard-head">
          <div>
            <h3>{t("engineering.gateways.wizard.title")}</h3>
            <p>{t(`engineering.gateways.wizard.sub.${step}`)}</p>
          </div>
          <button type="button" className="gw-wizard-close" onClick={onClose} aria-label={t("common.close")}>
            <X size={18} strokeWidth={2.2} />
          </button>
        </header>

        {/* Adim gostergesi */}
        <ol className="gw-steps" aria-hidden="true">
          {(["identity", "target", step === "remote" ? "remote" : "local"] as const).map((s, i) => {
            const order: Step[] = ["identity", "target", step === "remote" ? "remote" : "local"];
            const activeIdx = order.indexOf(step);
            const cls = i < activeIdx ? "is-done" : i === activeIdx ? "is-active" : "";
            return (
              <li key={s} className={cls}>
                <span>{i + 1}</span>
              </li>
            );
          })}
        </ol>

        {/* ---------------------------------------------------- 1. KIMLIK */}
        {step === "identity" ? (
          <div className="gw-wizard-body">
            <div className="gw-field-row">
              <label className="gw-field">
                <span>{t("engineering.gateways.form.code")}</span>
                <input
                  value={code}
                  onChange={(e) => setCode(e.target.value)}
                  placeholder="GW-001"
                  autoFocus
                />
              </label>
              <label className="gw-field">
                <span>{t("engineering.gateways.form.name")}</span>
                <input
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                  placeholder={t("engineering.gateways.form.namePlaceholder")}
                />
              </label>
            </div>
            {codeTaken ? (
              <p className="gw-inline-warn">{t("engineering.gateways.wizard.codeTaken")}</p>
            ) : !codeValid && code.trim() ? (
              <p className="gw-inline-warn">{t("engineering.gateways.wizard.codeInvalid")}</p>
            ) : null}

            <div className="gw-token-box">
              <div className="gw-token-head">
                <span>{t("engineering.gateways.wizard.tokenTitle")}</span>
                <div className="gw-token-actions">
                  <button
                    type="button"
                    className="gw-copy-btn"
                    onClick={() => setToken(generateToken())}
                    title={t("engineering.gateways.wizard.tokenRegenerate")}
                    aria-label={t("engineering.gateways.wizard.tokenRegenerate")}
                  >
                    <RefreshCw size={15} strokeWidth={2.2} />
                  </button>
                  <CopyButton value={token} label={t("engineering.gateways.compose.copy")} />
                </div>
              </div>
              <code className="gw-token-value">{token}</code>
              <small>{t("engineering.gateways.wizard.tokenHint")}</small>
            </div>

            {error ? <p className="error-text">{error}</p> : null}

            <div className="modal-actions">
              <button type="button" className="secondary-btn" onClick={onClose}>
                {t("common.cancel")}
              </button>
              <button
                type="button"
                className="primary-btn"
                disabled={!identityOk}
                onClick={() => setStep("target")}
              >
                {t("common.next")}
              </button>
            </div>
          </div>
        ) : null}

        {/* ---------------------------------------------------- 2. HEDEF */}
        {step === "target" ? (
          <div className="gw-wizard-body">
            <div className="gw-target-grid">
              <button
                type="button"
                className="gw-target-card"
                disabled={!agentUsable || busy}
                onClick={() => void createThen("local")}
              >
                <span className="gw-target-icon">
                  <HardDrive size={22} strokeWidth={2} />
                </span>
                <strong>{t("engineering.gateways.wizard.localTitle")}</strong>
                <span className="gw-target-desc">{t("engineering.gateways.wizard.localDesc")}</span>
                {agentUsable ? (
                  <span className="gw-target-badge is-ok">
                    {t("engineering.gateways.wizard.localReady")}
                  </span>
                ) : (
                  <span className="gw-target-badge is-off">
                    {t(`engineering.gateways.wizard.agentReason.${agentReason}`, {
                      defaultValue: t("engineering.gateways.wizard.agentReason.unavailable")
                    })}
                  </span>
                )}
              </button>

              <button
                type="button"
                className="gw-target-card"
                disabled={busy}
                onClick={() => void createThen("remote")}
              >
                <span className="gw-target-icon">
                  <Server size={22} strokeWidth={2} />
                </span>
                <strong>{t("engineering.gateways.wizard.remoteTitle")}</strong>
                <span className="gw-target-desc">{t("engineering.gateways.wizard.remoteDesc")}</span>
                <span className="gw-target-badge">{t("engineering.gateways.wizard.remoteBadge")}</span>
              </button>
            </div>

            {busy ? (
              <p className="gw-inline-busy">
                <Loader2 size={15} strokeWidth={2.2} className="net-spin" />
                {t("engineering.gateways.wizard.creating")}
              </p>
            ) : null}
            {error ? <p className="error-text">{error}</p> : null}

            <div className="modal-actions">
              {/* Kayit olustuktan sonra geri donus KAPALI: kod/token artik
                  veritabaninda, degistirmek yeni bir gateway yaratmaz. */}
              <button
                type="button"
                className="secondary-btn"
                onClick={() => (created ? onClose() : setStep("identity"))}
                disabled={busy}
              >
                {created ? t("common.close") : t("common.back")}
              </button>
            </div>
          </div>
        ) : null}

        {/* --------------------------------------- 3a. BU CIHAZA KURULUM */}
        {step === "local" ? (
          <div className="gw-wizard-body">
            {!installDone ? (
              <div className="gw-install-live">
                <Loader2 size={28} strokeWidth={2.2} className="net-spin" />
                <strong>{stageLabel(applied?.stage ?? agent?.last_apply?.stage)}</strong>
                <span>{t("engineering.gateways.wizard.installWait")}</span>
              </div>
            ) : applied?.ok ? (
              <div className="gw-install-result is-ok">
                <CheckCircle2 size={30} strokeWidth={2.1} />
                <strong>{t("engineering.gateways.wizard.installOk", { code: code.trim() })}</strong>
                <span>{t("engineering.gateways.wizard.installOkHint")}</span>
              </div>
            ) : (
              <div className="gw-install-result is-fail">
                <AlertTriangle size={30} strokeWidth={2.1} />
                <strong>{t("engineering.gateways.wizard.installFail")}</strong>
                <span>{applied?.message}</span>
                {applied?.detail ? <pre className="gw-install-log">{applied.detail}</pre> : null}
                {/* Kurulum basarisiz olsa da gateway KAYDI olustu; kullanici
                    dosyayi indirip elle kurabilsin diye diger yola gecis. */}
                <button type="button" className="secondary-btn" onClick={() => setStep("remote")}>
                  {t("engineering.gateways.wizard.fallbackToManual")}
                </button>
              </div>
            )}

            <div className="modal-actions">
              <button type="button" className="primary-btn" onClick={onClose} disabled={!installDone}>
                {t("common.close")}
              </button>
            </div>
          </div>
        ) : null}

        {/* ------------------------------------- 3b. BASKA CIHAZA KURULUM */}
        {step === "remote" ? (
          <div className="gw-wizard-body">
            <label className="gw-field">
              <span>{t("engineering.gateways.compose.backendIp")}</span>
              <input
                value={backendIp}
                onChange={(e) => setBackendIp(e.target.value)}
                placeholder={t("engineering.gateways.compose.backendIpPlaceholder")}
              />
              <small>{t("engineering.gateways.wizard.backendIpHint")}</small>
            </label>

            <ol className="gw-manual-steps">
              <li>
                <span className="gw-manual-num">1</span>
                <div>
                  <strong>{t("engineering.gateways.wizard.step1Title")}</strong>
                  <div className="gw-manual-actions">
                    <button
                      type="button"
                      className="secondary-btn"
                      disabled={!backendUrl || downloadBusy}
                      onClick={() => void handleDownload()}
                    >
                      <Download size={15} strokeWidth={2.2} />
                      {composeFile}
                    </button>
                  </div>
                </div>
              </li>
              <li>
                <span className="gw-manual-num">2</span>
                <div>
                  <strong>{t("engineering.gateways.wizard.step2Title")}</strong>
                  <span className="gw-manual-desc">{t("engineering.gateways.wizard.step2Desc")}</span>
                </div>
              </li>
              <li>
                <span className="gw-manual-num">3</span>
                <div>
                  <strong>{t("engineering.gateways.wizard.step3Title")}</strong>
                  <div className="gw-cmd-row">
                    <code>{runCommand}</code>
                    <CopyButton value={runCommand} label={t("engineering.gateways.compose.copy")} />
                  </div>
                </div>
              </li>
            </ol>

            {error ? <p className="error-text">{error}</p> : null}

            <div className="modal-actions">
              <button type="button" className="primary-btn" onClick={onClose}>
                {t("common.done")}
              </button>
            </div>
          </div>
        ) : null}
      </div>
    </div>
  );
}
