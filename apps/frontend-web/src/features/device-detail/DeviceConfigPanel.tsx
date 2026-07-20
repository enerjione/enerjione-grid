/**
 * DeviceConfigPanel — cihaz DNP3 config (installer-only) + FTP placeholder.
 *
 * Mevcut Dnp3SettingsForm'u yeniden kullanir; degisiklikleri PATCH /devices
 * ile kaydeder (updateDevice). FTP bolumu simdilik placeholder — cihaz FTP
 * config formati tersine muhendislik edilince (roadmap) aktiflesir.
 *
 * RBAC: yalnizca canConfig (installer) ise render edilir; backend PATCH da
 * installer-only (devices.py update_device require_role INSTALLER).
 */

import { useMemo, useState } from "react";
import { useTranslation } from "react-i18next";

import { updateDevice } from "../../shared/api";
import { Dnp3SettingsForm } from "../devices/Dnp3SettingsForm";
import { mergeDnp3Extended } from "../../shared/types";
import type { DeviceRow, Dnp3ExtendedSettings } from "../../shared/types";

type Props = {
  device: DeviceRow;
  /** initiating port otomatik atamasi icin diger cihazlarin master portlari. */
  usedMasterPorts?: number[];
  token: string;
  /** Kaydettikten sonra ust listeyi tazelemek icin (opsiyonel). */
  onSaved?: () => void;
};

export function DeviceConfigPanel({ device, usedMasterPorts, token, onSaved }: Props) {
  const { t } = useTranslation();
  const initial = useMemo(() => mergeDnp3Extended(device.dnp3Extended), [device.dnp3Extended]);
  const [draft, setDraft] = useState<Dnp3ExtendedSettings>(initial);
  const [saving, setSaving] = useState(false);
  const [msg, setMsg] = useState<{ kind: "ok" | "err"; text: string } | null>(null);

  const dirty = useMemo(
    () => JSON.stringify(draft) !== JSON.stringify(initial),
    [draft, initial]
  );

  const onChange = (patch: Partial<Dnp3ExtendedSettings>) => {
    setDraft((d) => ({ ...d, ...patch }));
    setMsg(null);
  };

  const save = async () => {
    setSaving(true);
    setMsg(null);
    try {
      await updateDevice(token, device.code, { dnp3_extended: draft });
      setMsg({ kind: "ok", text: t("deviceDetail.config.saved") });
      onSaved?.();
    } catch (e) {
      setMsg({ kind: "err", text: e instanceof Error ? e.message : String(e) });
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="device-config-panel">
      <section className="device-config-section">
        <h4 className="device-config-title">
          <span className="material-symbols-outlined">settings_ethernet</span>
          {t("deviceDetail.config.dnp3Title")}
        </h4>
        <Dnp3SettingsForm value={draft} onChange={onChange} usedMasterPorts={usedMasterPorts} />
        <div className="device-config-actions">
          {msg ? (
            <span className={`device-config-msg is-${msg.kind}`}>
              <span className="material-symbols-outlined">
                {msg.kind === "ok" ? "check_circle" : "error"}
              </span>
              {msg.text}
            </span>
          ) : null}
          <button
            type="button"
            className="primary-btn"
            disabled={!dirty || saving}
            aria-busy={saving}
            onClick={() => void save()}
          >
            {saving ? <span className="btn-spinner" aria-hidden="true" /> : null}
            {t("deviceDetail.config.save")}
          </button>
        </div>
      </section>

      {/* FTP — placeholder. Gercek dosya cekme/config parse cihaz erisimi olunca. */}
      <section className="device-config-section is-placeholder">
        <h4 className="device-config-title">
          <span className="material-symbols-outlined">folder_shared</span>
          {t("deviceDetail.config.ftpTitle")}
          <span className="device-config-badge">{t("deviceDetail.config.soon")}</span>
        </h4>
        <p className="device-config-hint">{t("deviceDetail.config.ftpHint")}</p>
        <div className="device-config-ftp-grid" aria-disabled="true">
          {(["server", "port", "user", "pass", "dir"] as const).map((f) => (
            <label key={f} className="dnp3-field">
              <span className="dnp3-label">{t(`deviceDetail.config.ftp.${f}`)}</span>
              <input type="text" disabled placeholder="—" />
            </label>
          ))}
        </div>
      </section>
    </div>
  );
}
