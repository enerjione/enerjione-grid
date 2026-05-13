/**
 * MQTT TLS sertifika upload component'i.
 *
 * Tek bir sertifika tipi (ca / cert / key) icin file picker + durum gosterimi.
 * Dosya secilince hemen backend'e POST eder; backend disk'e yazip target
 * mqtt_tls_*_path alanini gunceller, donen path frontend'e bildirilir.
 * Mevcut yuklu sertifika varsa "Yuklu" badge + Sil butonu.
 *
 * Sadece edit modunda kullanilir (target_id gerek).
 */
import { useRef, useState } from "react";
import { useTranslation } from "react-i18next";

import { deleteMqttCert, uploadMqttCert, type MqttCertKind } from "../../shared/api";

type Props = {
  accessToken: string;
  targetId: number;
  kind: MqttCertKind;
  /** Mevcut yuklu sertifika path (varsa). Bos string = yok. */
  currentPath: string;
  /** Label ustte gosterilir, "CA Sertifika" vb. */
  label: string;
  onUploaded: (path: string) => void;
  onDeleted: () => void;
};

export function MqttCertUploader({
  accessToken,
  targetId,
  kind,
  currentPath,
  label,
  onUploaded,
  onDeleted,
}: Props) {
  const { t } = useTranslation();
  const inputRef = useRef<HTMLInputElement>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const filename = currentPath ? currentPath.split("/").pop() : null;

  const handleSelect = async (file: File) => {
    setError(null);
    setBusy(true);
    try {
      const updated = await uploadMqttCert(accessToken, targetId, kind, file);
      const newPath =
        kind === "ca"
          ? updated.mqtt_tls_ca_path ?? ""
          : kind === "cert"
          ? updated.mqtt_tls_cert_path ?? ""
          : updated.mqtt_tls_key_path ?? "";
      onUploaded(newPath);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Yüklenemedi");
    } finally {
      setBusy(false);
      // input value sifirla — ayni dosyayi tekrar secebilmek icin
      if (inputRef.current) inputRef.current.value = "";
    }
  };

  const handleDelete = async () => {
    if (!window.confirm(t("engineering.outbound.mqtt.certDeleteConfirm"))) return;
    setError(null);
    setBusy(true);
    try {
      await deleteMqttCert(accessToken, targetId, kind);
      onDeleted();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Silinemedi");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className={`mqtt-cert-uploader ${currentPath ? "is-loaded" : ""}`}>
      <div className="mqtt-cert-uploader-label">{label}</div>
      {currentPath ? (
        <div className="mqtt-cert-uploader-row">
          <span className="mqtt-cert-badge">
            <span className="material-symbols-outlined">verified</span>
            {filename || t("engineering.outbound.mqtt.certLoaded")}
          </span>
          <div className="mqtt-cert-actions">
            <button
              type="button"
              className="link-btn"
              onClick={() => inputRef.current?.click()}
              disabled={busy}
            >
              {t("engineering.outbound.mqtt.certReplace")}
            </button>
            <button
              type="button"
              className="danger-link"
              onClick={() => void handleDelete()}
              disabled={busy}
            >
              {t("common.delete")}
            </button>
          </div>
        </div>
      ) : (
        <button
          type="button"
          className="mqtt-cert-drop"
          onClick={() => inputRef.current?.click()}
          disabled={busy}
        >
          <span className="material-symbols-outlined">upload_file</span>
          <span>
            {busy
              ? t("common.loading")
              : t("engineering.outbound.mqtt.certUploadCta")}
          </span>
          <small>{t("engineering.outbound.mqtt.certUploadHint")}</small>
        </button>
      )}
      <input
        ref={inputRef}
        type="file"
        accept=".pem,.crt,.key,.cer"
        hidden
        onChange={(event) => {
          const file = event.target.files?.[0];
          if (file) void handleSelect(file);
        }}
      />
      {error ? <div className="mqtt-cert-error">{error}</div> : null}
    </div>
  );
}
