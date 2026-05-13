/**
 * MQTT Custom Topic Mapping modal.
 *
 * Operator UI: bir MQTT outbound target icin "ozel topic" kurar. Default
 * template ({prefix}/{customer}/{device}/{source}/{datatype}/telemetry)
 * disinda spesifik cihaz/sinyal kombinasyonlarini farkli topic'e yollar.
 *
 * Mantik:
 *  - device_codes = "" -> tum cihazlar
 *  - signal_keys  = "" -> tum sinyaller
 *  - Birden fazla mapping ayni reading'i yakalarsa hepsine publish (broadcast).
 *  - Topic'te variable expansion: {prefix} {customer} {device} {source}
 *    {datatype} {signal} runtime resolve edilir.
 *  - Mapping'lerden hicbiri match etmezse default template kullanilir.
 *
 * UI:
 *  - Mevcut mapping'ler tablo halinde gosterilir.
 *  - "+ Yeni" satir form'unu acar (inline edit).
 *  - Her satirda Edit / Delete butonlari.
 */
import { useEffect, useMemo, useState, type FormEvent } from "react";
import { useTranslation } from "react-i18next";

import {
  fetchTopicMappings,
  createTopicMapping,
  updateTopicMapping,
  deleteTopicMapping,
} from "../../shared/api";
import type { DeviceRow, OutboundTarget, OutboundTopicMapping } from "../../shared/types";

type Props = {
  accessToken: string;
  target: OutboundTarget;
  devices: DeviceRow[];
  onClose: () => void;
};

export function MqttTopicMappingModal({ accessToken, target, devices, onClose }: Props) {
  const { t } = useTranslation();
  const [mappings, setMappings] = useState<OutboundTopicMapping[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [editing, setEditing] = useState<OutboundTopicMapping | "new" | null>(null);

  // Inline form state
  const [formTopic, setFormTopic] = useState("");
  const [formDeviceCodes, setFormDeviceCodes] = useState("");
  const [formSignalKeys, setFormSignalKeys] = useState("");
  const [formQos, setFormQos] = useState<string>("");
  const [formRetain, setFormRetain] = useState<"target" | "true" | "false">("target");
  const [formActive, setFormActive] = useState(true);
  const [saving, setSaving] = useState(false);

  const reload = async () => {
    setLoading(true);
    try {
      const list = await fetchTopicMappings(accessToken, target.id);
      setMappings(list);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Yüklenemedi");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void reload();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [target.id]);

  const resetForm = () => {
    setFormTopic("");
    setFormDeviceCodes("");
    setFormSignalKeys("");
    setFormQos("");
    setFormRetain("target");
    setFormActive(true);
  };

  const openNew = () => {
    setEditing("new");
    resetForm();
  };

  const openEdit = (m: OutboundTopicMapping) => {
    setEditing(m);
    setFormTopic(m.topic);
    setFormDeviceCodes(m.device_codes);
    setFormSignalKeys(m.signal_keys);
    setFormQos(m.qos != null ? String(m.qos) : "");
    setFormRetain(m.retain == null ? "target" : m.retain ? "true" : "false");
    setFormActive(m.is_active);
  };

  const cancelForm = () => {
    setEditing(null);
    setError("");
  };

  const handleSave = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setError("");
    setSaving(true);
    try {
      const qosNum = formQos.trim() === "" ? null : Number(formQos);
      const retainVal: boolean | null =
        formRetain === "target" ? null : formRetain === "true";
      const payload = {
        topic: formTopic.trim(),
        device_codes: formDeviceCodes.trim(),
        signal_keys: formSignalKeys.trim(),
        qos: qosNum,
        retain: retainVal,
        is_active: formActive,
      };
      if (editing === "new") {
        await createTopicMapping(accessToken, target.id, payload);
      } else if (editing && editing !== "new") {
        await updateTopicMapping(accessToken, target.id, editing.id, payload);
      }
      setEditing(null);
      await reload();
    } catch (err) {
      setError(err instanceof Error ? err.message : t("common.errorOccurred"));
    } finally {
      setSaving(false);
    }
  };

  const handleDelete = async (m: OutboundTopicMapping) => {
    if (!window.confirm(t("engineering.outbound.mqtt.mapping.confirmDelete", { topic: m.topic }))) {
      return;
    }
    try {
      await deleteTopicMapping(accessToken, target.id, m.id);
      await reload();
    } catch (err) {
      setError(err instanceof Error ? err.message : t("common.errorOccurred"));
    }
  };

  // Cihaz kodlari icin datalist (operator yazarken autocomplete)
  const deviceCodesHint = useMemo(
    () => devices.map((d) => d.code).join(", "),
    [devices]
  );

  return (
    <div className="settings-modal-backdrop" onClick={onClose}>
      <div
        className="settings-modal mqtt-mapping-modal"
        onClick={(e) => e.stopPropagation()}
        role="dialog"
        aria-modal="true"
      >
        <div className="mqtt-mapping-header">
          <div>
            <h3>{t("engineering.outbound.mqtt.mapping.title")}</h3>
            <p className="mqtt-mapping-subtitle">
              {t("engineering.outbound.mqtt.mapping.subtitle", { name: target.name })}
            </p>
          </div>
          <button type="button" className="link-btn" onClick={onClose}>
            ×
          </button>
        </div>

        {/* Aciklama — kullanici nasil mapping yapacagini anlamak ister */}
        <div className="mqtt-mapping-help">
          <p>{t("engineering.outbound.mqtt.mapping.helpIntro")}</p>
          <ul>
            <li>{t("engineering.outbound.mqtt.mapping.helpDeviceCodes")}</li>
            <li>{t("engineering.outbound.mqtt.mapping.helpSignalKeys")}</li>
            <li>{t("engineering.outbound.mqtt.mapping.helpTemplateVars")}</li>
            <li>{t("engineering.outbound.mqtt.mapping.helpFallback")}</li>
          </ul>
        </div>

        {error ? <div className="settings-error">{error}</div> : null}

        {loading ? (
          <p className="helper-text">{t("common.loading")}</p>
        ) : (
          <>
            <table className="mqtt-mapping-table">
              <thead>
                <tr>
                  <th>{t("engineering.outbound.mqtt.mapping.colTopic")}</th>
                  <th>{t("engineering.outbound.mqtt.mapping.colDevices")}</th>
                  <th>{t("engineering.outbound.mqtt.mapping.colSignals")}</th>
                  <th>QoS</th>
                  <th>Retain</th>
                  <th>{t("common.active")}</th>
                  <th>{t("common.actions")}</th>
                </tr>
              </thead>
              <tbody>
                {mappings.length === 0 ? (
                  <tr>
                    <td colSpan={7} className="helper-text" style={{ padding: 12, textAlign: "center" }}>
                      {t("engineering.outbound.mqtt.mapping.empty")}
                    </td>
                  </tr>
                ) : (
                  mappings.map((m) => (
                    <tr key={m.id} className={m.is_active ? "" : "is-inactive"}>
                      <td><code>{m.topic}</code></td>
                      <td>
                        {m.device_codes ? (
                          <span title={m.device_codes}>
                            {m.device_codes.split(",").length}{" "}
                            {t("engineering.outbound.mqtt.mapping.deviceCount")}
                          </span>
                        ) : (
                          <em>{t("engineering.outbound.mqtt.mapping.allDevices")}</em>
                        )}
                      </td>
                      <td>
                        {m.signal_keys ? (
                          <span title={m.signal_keys}>
                            {m.signal_keys.split(",").length}{" "}
                            {t("engineering.outbound.mqtt.mapping.signalCount")}
                          </span>
                        ) : (
                          <em>{t("engineering.outbound.mqtt.mapping.allSignals")}</em>
                        )}
                      </td>
                      <td>{m.qos == null ? "—" : m.qos}</td>
                      <td>{m.retain == null ? "—" : m.retain ? "✓" : "—"}</td>
                      <td>{m.is_active ? "✓" : "—"}</td>
                      <td className="actions-cell">
                        <button className="edit-btn action-btn" onClick={() => openEdit(m)}>
                          {t("common.edit")}
                        </button>
                        <button
                          className="danger-btn action-btn"
                          onClick={() => void handleDelete(m)}
                        >
                          {t("common.delete")}
                        </button>
                      </td>
                    </tr>
                  ))
                )}
              </tbody>
            </table>

            {editing == null ? (
              <button type="button" className="primary-btn" onClick={openNew}>
                + {t("engineering.outbound.mqtt.mapping.add")}
              </button>
            ) : (
              <form onSubmit={handleSave} className="mqtt-mapping-form">
                <h4>
                  {editing === "new"
                    ? t("engineering.outbound.mqtt.mapping.addTitle")
                    : t("engineering.outbound.mqtt.mapping.editTitle")}
                </h4>
                <label>
                  {t("engineering.outbound.mqtt.mapping.colTopic")}
                  <input
                    value={formTopic}
                    onChange={(e) => setFormTopic(e.target.value)}
                    placeholder="e1/customer/DEV-001/master/analog/voltage"
                    required
                  />
                  <small>{t("engineering.outbound.mqtt.mapping.topicHelp")}</small>
                </label>
                <label>
                  {t("engineering.outbound.mqtt.mapping.colDevices")}
                  <input
                    value={formDeviceCodes}
                    onChange={(e) => setFormDeviceCodes(e.target.value)}
                    placeholder={t("engineering.outbound.mqtt.mapping.devicesPlaceholder")}
                    list="mapping-device-codes"
                  />
                  <small>
                    {t("engineering.outbound.mqtt.mapping.devicesHelp")}
                    {deviceCodesHint ? (
                      <> · {t("engineering.outbound.mqtt.mapping.availableDevices")}: <code>{deviceCodesHint}</code></>
                    ) : null}
                  </small>
                </label>
                <datalist id="mapping-device-codes">
                  {devices.map((d) => (
                    <option key={d.code} value={d.code} />
                  ))}
                </datalist>
                <label>
                  {t("engineering.outbound.mqtt.mapping.colSignals")}
                  <input
                    value={formSignalKeys}
                    onChange={(e) => setFormSignalKeys(e.target.value)}
                    placeholder="master.voltage_a, master.current_a"
                  />
                  <small>{t("engineering.outbound.mqtt.mapping.signalsHelp")}</small>
                </label>
                <div className="mqtt-mapping-form-grid">
                  <label>
                    QoS
                    <select
                      value={formQos}
                      onChange={(e) => setFormQos(e.target.value)}
                    >
                      <option value="">{t("engineering.outbound.mqtt.mapping.useTargetDefault")}</option>
                      <option value="0">0</option>
                      <option value="1">1</option>
                      <option value="2">2</option>
                    </select>
                  </label>
                  <label>
                    Retain
                    <select
                      value={formRetain}
                      onChange={(e) =>
                        setFormRetain(e.target.value as "target" | "true" | "false")
                      }
                    >
                      <option value="target">{t("engineering.outbound.mqtt.mapping.useTargetDefault")}</option>
                      <option value="true">{t("common.yes")}</option>
                      <option value="false">{t("common.no")}</option>
                    </select>
                  </label>
                  <label className="notify-option">
                    <input
                      type="checkbox"
                      checked={formActive}
                      onChange={(e) => setFormActive(e.target.checked)}
                    />
                    {t("common.active")}
                  </label>
                </div>
                <div className="settings-actions">
                  <button type="button" onClick={cancelForm} disabled={saving}>
                    {t("common.cancel")}
                  </button>
                  <button type="submit" className="primary-btn" disabled={saving}>
                    {saving ? t("common.saving") : t("common.save")}
                  </button>
                </div>
              </form>
            )}
          </>
        )}
      </div>
    </div>
  );
}
