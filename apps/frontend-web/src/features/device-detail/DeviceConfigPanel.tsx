/**
 * DeviceConfigPanel — cihaz ayarlari SALT-OKUMA ozeti + FTP placeholder.
 *
 * IP/DNP3 duzenleme buradan KALDIRILDI (kullanici istegi): duzenleme Cihaz
 * Yonetimi sayfasindan yapilir. Burada sadece mevcut config gorunur (installer
 * teshis icin). Ileride FTP config yonetimi eklenince duzenlenebilir alanlar
 * bu sekmede aktiflesir.
 */

import { useMemo } from "react";
import { useTranslation } from "react-i18next";

import { mergeDnp3Extended } from "../../shared/types";
import type { DeviceRow } from "../../shared/types";

type Props = {
  device: DeviceRow;
};

function Row({ label, value }: { label: string; value: string | number | undefined | null }) {
  const shown = value === undefined || value === null || value === "" ? "—" : String(value);
  return (
    <div className="device-cfg-row">
      <span className="device-cfg-label">{label}</span>
      <span className="device-cfg-value">{shown}</span>
    </div>
  );
}

export function DeviceConfigPanel({ device }: Props) {
  const { t } = useTranslation();
  const ext = useMemo(() => mergeDnp3Extended(device.dnp3Extended), [device.dnp3Extended]);

  return (
    <div className="device-config-panel">
      <section className="device-config-section is-readonly">
        <h4 className="device-config-title">
          <span className="material-symbols-outlined">settings_ethernet</span>
          {t("deviceDetail.config.dnp3Title")}
          <span className="device-config-badge is-muted">{t("deviceDetail.config.readonly")}</span>
        </h4>
        <p className="device-config-hint">{t("deviceDetail.config.readonlyHint")}</p>
        <div className="device-cfg-grid">
          <Row label={t("deviceDetail.config.field.ip")} value={device.ipAddress} />
          <Row label={t("deviceDetail.config.field.port")} value={device.dnp3OutstationPort} />
          <Row label={t("deviceDetail.config.field.dnp3Address")} value={device.dnp3Address} />
          <Row label={t("deviceDetail.config.field.endpointType")} value={ext.ip_endpoint_type} />
          <Row label={t("deviceDetail.config.field.masterAddress")} value={ext.master_address} />
          <Row label={t("deviceDetail.config.field.gateway")} value={device.gatewayCode} />
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
      </section>
    </div>
  );
}
