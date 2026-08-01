/**
 * GatewayEditModal — mevcut bir gateway'in ayarlari.
 *
 * ESKI FORMDAN FARK: "Host" ve "Port" alanlari KALDIRILDI. Gateway artik DNP3
 * master rolunde calisiyor; outstation adresi cihazin kendi `ip_address`
 * alanindan okunuyor. Bu iki alan create akisinda backend semasini doldurmak
 * icin gonderilen placeholder'lardi ("auto" / 0) ve kullaniciya gercek bir
 * ayarmis gibi gorunuyordu.
 *
 * Token artik duz bir input degil: okunur kutu + kopyala + yeniden uret.
 * Yeniden uretmek CALISAN gateway'i keser (compose icindeki eski token
 * gecersizlesir), bu yuzden sonucu acikca yaziyoruz.
 */
import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { AlertTriangle, ArrowUpCircle, Loader2, RefreshCw, Trash2, X } from "lucide-react";

import {
  fetchGatewayAgentStatus,
  updateGatewayLocally,
  fetchGatewayToken,
  installGatewayLocally,
  removeGatewayLocally
} from "../../shared/api";
import type { Gateway, GatewayAgentStatus, LocalGateway } from "../../shared/types";
import { CopyButton, generateToken } from "./gatewayShared";
import { useModalDialog } from "../../shared/useModalDialog";

type Props = {
  accessToken: string;
  gateway: Gateway;
  onSave: (payload: { name: string; token: string }) => Promise<void>;
  onClose: () => void;
};

export function GatewayEditModal({ accessToken, gateway, onSave, onClose }: Props) {
  const { t } = useTranslation();
  // ESC ile kapanma + odak tuzagi (modal disina Tab ile cikilamasin).
  const dialogRef = useModalDialog<HTMLDivElement>(onClose);
  const [name, setName] = useState(gateway.name);
  // Token listede DONMUYOR (operator'a acik bir uctu ve telemetri
  // enjeksiyonuna izin veriyordu); modal acilinca installer'a ozel uctan
  // ayrica cekilir. `orijinalToken` "degisti mi" karsilastirmasi icin.
  const [token, setToken] = useState("");
  const [orijinalToken, setOrijinalToken] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  const [agent, setAgent] = useState<GatewayAgentStatus | null>(null);
  const [localBusy, setLocalBusy] = useState(false);

  // Token henuz cekilmediyse "degisti" sayma; aksi halde modal acilir acilmaz
  // bos deger yuzunden "token degisti" uyarisi cikardi.
  const tokenChanged = orijinalToken !== "" && token !== orijinalToken;

  const loadAgent = async () => {
    setAgent(await fetchGatewayAgentStatus(accessToken));
  };

  useEffect(() => {
    void loadAgent();
    // Token ayri uctan gelir; basarisiz olursa (yetki yok) kutu bos kalir ve
    // kaydetme token'a dokunmaz — modalin geri kalani calismaya devam eder.
    void (async () => {
      try {
        const mevcut = await fetchGatewayToken(accessToken, gateway.code);
        setToken(mevcut);
        setOrijinalToken(mevcut);
      } catch {
        setToken("");
        setOrijinalToken("");
      }
    })();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [accessToken, gateway.code]);

  const handleLocalUpdate = async () => {
    // KESINTIYI SOR. Gateway yeniden baslarken ona bagli cihazlardan
    // telemetri gelmez; operatorun bunu bilmeden tetiklememesi gerekir.
    if (!window.confirm(t("engineering.gateways.editForm.updateConfirm"))) return;
    setLocalBusy(true);
    setError("");
    try {
      await updateGatewayLocally(accessToken, gateway.code);
      // Ajan istegi ASENKRON isliyor; durumu birkac saniye sonra tazele.
      window.setTimeout(() => void loadAgent(), 3000);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setLocalBusy(false);
    }
  };

  const local: LocalGateway | null =
    agent?.gateways.find((g) => g.code === gateway.code) ?? null;

  const handleSave = async () => {
    setError("");
    setBusy(true);
    try {
      await onSave({ name: name.trim(), token });
      // Token degistiyse ve gateway BU cihazda kuruluysa compose'daki eski
      // token gecersiz kaldi — kurulumu yeni token ile tazele. Aksi halde
      // kullanici "kaydettim ama gateway offline oldu" ile bas basa kalir.
      if (tokenChanged && local) {
        await installGatewayLocally(accessToken, gateway.code);
      }
      onClose();
    } catch (err) {
      setError(err instanceof Error ? err.message : t("common.errorOccurred"));
    } finally {
      setBusy(false);
    }
  };

  const handleLocalRemove = async () => {
    setError("");
    setLocalBusy(true);
    try {
      await removeGatewayLocally(accessToken, gateway.code);
      // Ajan isi asenkron yapar; kisa bir bekleme sonrasi durumu tazele.
      window.setTimeout(() => void loadAgent(), 2500);
    } catch (err) {
      setError(err instanceof Error ? err.message : t("common.errorOccurred"));
    } finally {
      setLocalBusy(false);
    }
  };

  return (
    <div className="settings-modal-backdrop">
      <div className="settings-modal gw-wizard" role="dialog" aria-modal="true" ref={dialogRef}>
        <header className="gw-wizard-head">
          <div>
            <h3>{t("engineering.gateways.editGatewayModal")}</h3>
            <p>{gateway.code}</p>
          </div>
          <button
            type="button"
            className="gw-wizard-close"
            onClick={onClose}
            aria-label={t("common.close")}
          >
            <X size={18} strokeWidth={2.2} />
          </button>
        </header>

        <div className="gw-wizard-body">
          <label className="gw-field">
            <span>{t("engineering.gateways.form.name")}</span>
            <input value={name} onChange={(e) => setName(e.target.value)} autoFocus />
          </label>

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
            {tokenChanged ? (
              <p className="gw-token-warn">
                <AlertTriangle size={14} strokeWidth={2.2} />
                <span>
                  {local
                    ? t("engineering.gateways.editForm.tokenWarnLocal")
                    : t("engineering.gateways.editForm.tokenWarnRemote")}
                </span>
              </p>
            ) : null}
          </div>

          {/* Bu cihazdaki kurulum — yalnizca host ajani gateway'i goruyorsa. */}
          {local ? (
            <div className="gw-local-box">
              <div className="gw-local-head">
                <span
                  className={`gw-local-dot ${local.state === "running" ? "is-on" : "is-off"}`}
                  aria-hidden="true"
                />
                <div>
                  <strong>{t("engineering.gateways.editForm.localTitle")}</strong>
                  <small>{local.status || local.state}</small>
                </div>
                <button
                  type="button"
                  className="gw-danger-btn"
                  onClick={() => void handleLocalRemove()}
                  disabled={localBusy || busy}
                >
                  {localBusy ? (
                    <Loader2 size={14} strokeWidth={2.2} className="net-spin" />
                  ) : (
                    <Trash2 size={14} strokeWidth={2.2} />
                  )}
                  {t("engineering.gateways.editForm.localRemove")}
                </button>
              </div>
              {/* SURUM DURUMU — uc durumlu.
                  `update_available` null/undefined ise BILINMIYOR (kayit
                  defterine ulasilamadi). Bunu "guncel" gostermek, sormadan
                  verilmis bir iddia olurdu ve operator eski surumde
                  kaldigini fark etmezdi. */}
              <div className="gw-local-version">
                {local.update_available === true ? (
                  <>
                    <span className="gw-ver-badge is-new">
                      <ArrowUpCircle size={13} strokeWidth={2.2} />
                      {t("engineering.gateways.editForm.updateAvailable")}
                    </span>
                    <button
                      type="button"
                      className="primary-btn gw-update-btn"
                      onClick={() => void handleLocalUpdate()}
                      disabled={localBusy || busy}
                    >
                      {localBusy ? (
                        <Loader2 size={14} strokeWidth={2.2} className="net-spin" />
                      ) : (
                        <ArrowUpCircle size={14} strokeWidth={2.2} />
                      )}
                      {t("engineering.gateways.editForm.updateNow")}
                    </button>
                  </>
                ) : local.update_available === false ? (
                  <span className="gw-ver-badge is-current">
                    {t("engineering.gateways.editForm.upToDate")}
                  </span>
                ) : (
                  <span className="gw-ver-badge is-unknown" title={t("engineering.gateways.editForm.versionUnknownHint")}>
                    {t("engineering.gateways.editForm.versionUnknown")}
                  </span>
                )}
              </div>
              <small className="gw-local-hint">{t("engineering.gateways.editForm.localHint")}</small>
            </div>
          ) : null}

          {error ? <p className="error-text">{error}</p> : null}

          <div className="modal-actions">
            <button type="button" className="secondary-btn" onClick={onClose} disabled={busy}>
              {t("common.cancel")}
            </button>
            <button
              type="button"
              className="primary-btn"
              onClick={() => void handleSave()}
              disabled={busy || name.trim().length === 0}
            >
              {busy ? t("common.saving") : t("engineering.gateways.form.save")}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
