import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { ActiveSwitch } from "../../components/ActiveSwitch";
import type { SignalCatalogRow, SignalDataType, SignalSource } from "../../shared/types";
import {
  DATA_TYPES,
  DATA_TYPE_LABEL,
  DNP3_GROUP_BY_TYPE,
  IEC104_MONITOR_TYPES,
  SOURCE_LABEL,
  SOURCES,
  convertTypeIdForTimeTag,
  parseIntOrNullModule
} from "./signalCatalogConstants";

type DetailTab = "general" | "iec104";

type Props = {
  signal: SignalCatalogRow;
  onSave: (
    signalKey: string,
    payload: Partial<Omit<SignalCatalogRow, "id" | "key">>
  ) => Promise<void>;
  onClose: () => void;
};

export function SignalEditModal({ signal, onSave, onClose }: Props) {
  const { t } = useTranslation();
  const dataTypeLabel = (type: SignalDataType): string =>
    t(`engineering.liveValues.dataType.${type}`, { defaultValue: DATA_TYPE_LABEL[type] });

  const [detailTab, setDetailTab] = useState<DetailTab>("general");
  const [savingEdit, setSavingEdit] = useState(false);
  const [localError, setLocalError] = useState("");

  const [editLabel, setEditLabel] = useState(signal.label);
  const [editUnit, setEditUnit] = useState(signal.unit ?? "");
  const [editDescription, setEditDescription] = useState(signal.description ?? "");
  const [editSource, setEditSource] = useState<SignalSource>(signal.source);
  const [editDnp3Class, setEditDnp3Class] = useState(signal.dnp3_class);
  const [editDataType, setEditDataType] = useState<SignalDataType>(signal.data_type);
  const [editIndex, setEditIndex] = useState(String(signal.dnp3_index));
  const [editScale, setEditScale] = useState(String(signal.scale));
  const [editOffset, setEditOffset] = useState(String(signal.offset));
  const [editIsActive, setEditIsActive] = useState(signal.is_active);
  const [editIec104Enabled, setEditIec104Enabled] = useState(signal.iec104_enabled !== false);
  const [editIec104TypeId, setEditIec104TypeId] = useState(
    signal.iec104_type_id !== null && signal.iec104_type_id !== undefined
      ? String(signal.iec104_type_id)
      : ""
  );
  const [editIec104Ioa, setEditIec104Ioa] = useState(
    signal.iec104_ioa !== null && signal.iec104_ioa !== undefined ? String(signal.iec104_ioa) : ""
  );
  const [editIec104WithTimestamp, setEditIec104WithTimestamp] = useState(
    signal.iec104_with_timestamp === true
  );
  const [editModbusFunction, setEditModbusFunction] = useState(
    signal.modbus_function !== null && signal.modbus_function !== undefined
      ? String(signal.modbus_function)
      : ""
  );
  const [editModbusAddress, setEditModbusAddress] = useState(
    signal.modbus_address !== null && signal.modbus_address !== undefined
      ? String(signal.modbus_address)
      : ""
  );
  const [editMqttTopic, setEditMqttTopic] = useState(signal.mqtt_topic ?? "");

  // Farkli bir sinyale ait modal yeniden acilirsa (ayni component instance
  // korunursa) formu yeni sinyalin degerleriyle resetle.
  useEffect(() => {
    setEditLabel(signal.label);
    setEditUnit(signal.unit ?? "");
    setEditDescription(signal.description ?? "");
    setEditSource(signal.source);
    setEditDnp3Class(signal.dnp3_class);
    setEditDataType(signal.data_type);
    setEditIndex(String(signal.dnp3_index));
    setEditScale(String(signal.scale));
    setEditOffset(String(signal.offset));
    setEditIsActive(signal.is_active);
    setEditIec104Enabled(signal.iec104_enabled !== false);
    setEditIec104TypeId(
      signal.iec104_type_id !== null && signal.iec104_type_id !== undefined
        ? String(signal.iec104_type_id)
        : ""
    );
    setEditIec104Ioa(
      signal.iec104_ioa !== null && signal.iec104_ioa !== undefined ? String(signal.iec104_ioa) : ""
    );
    setEditIec104WithTimestamp(signal.iec104_with_timestamp === true);
    setEditModbusFunction(
      signal.modbus_function !== null && signal.modbus_function !== undefined
        ? String(signal.modbus_function)
        : ""
    );
    setEditModbusAddress(
      signal.modbus_address !== null && signal.modbus_address !== undefined
        ? String(signal.modbus_address)
        : ""
    );
    setEditMqttTopic(signal.mqtt_topic ?? "");
    setLocalError("");
  }, [signal]);

  const handleSave = async () => {
    setLocalError("");
    setSavingEdit(true);
    try {
      const dnp3Group = DNP3_GROUP_BY_TYPE[editDataType];
      const parseIntOrNull = (v: string): number | null => {
        const t = v.trim();
        if (!t) return null;
        const n = Number(t);
        return Number.isFinite(n) ? Math.round(n) : null;
      };
      await onSave(signal.key, {
        label: editLabel,
        unit: editUnit.trim() || null,
        description: editDescription.trim() || null,
        source: editSource,
        dnp3_class: editDnp3Class,
        data_type: editDataType,
        dnp3_object_group: dnp3Group,
        dnp3_index: Number(editIndex),
        scale: Number(editScale),
        offset: Number(editOffset),
        is_active: editIsActive,
        iec104_type_id: parseIntOrNull(editIec104TypeId),
        iec104_ioa: parseIntOrNull(editIec104Ioa),
        iec104_enabled: editIec104Enabled,
        iec104_with_timestamp: editIec104WithTimestamp,
        modbus_function: parseIntOrNull(editModbusFunction),
        modbus_address: parseIntOrNull(editModbusAddress),
        mqtt_topic: editMqttTopic.trim() || null
      });
      onClose();
    } catch (err) {
      setLocalError(err instanceof Error ? err.message : t("engineering.signals.form.saveFail"));
    } finally {
      setSavingEdit(false);
    }
  };

  return (
    <div className="settings-modal-backdrop" onClick={onClose}>
      <div
        className="settings-modal signal-edit-modal"
        onClick={(e) => e.stopPropagation()}
        role="dialog"
        aria-modal="true"
      >
        <div className="mqtt-mapping-header signal-edit-header">
          <div>
            <h3>{signal.label}</h3>
            <p className="mqtt-mapping-subtitle">{signal.key}</p>
          </div>
          <div className="signal-edit-header-actions">
            <button
              type="button"
              className={`signal-active-pill ${editIsActive ? "signal-active-pill-on" : ""}`}
              onClick={() => setEditIsActive((v) => !v)}
              title={t("engineering.signals.labelInactiveTitle")}
            >
              <span className="signal-active-pill-dot" />
              {editIsActive ? t("common.active", { defaultValue: "Aktif" }) : t("common.inactive", { defaultValue: "Pasif" })}
            </button>
            <button type="button" className="signal-modal-close" onClick={onClose} aria-label={t("common.close", { defaultValue: "Kapat" })}>
              ×
            </button>
          </div>
        </div>

        <div className="signal-detail-tabs" role="tablist">
          <button
            type="button"
            role="tab"
            aria-selected={detailTab === "general"}
            className={`signal-detail-tab ${detailTab === "general" ? "active" : ""}`}
            onClick={() => setDetailTab("general")}
          >
            {t("engineering.signals.tabGeneral")}
          </button>
          <button
            type="button"
            role="tab"
            aria-selected={detailTab === "iec104"}
            className={`signal-detail-tab ${detailTab === "iec104" ? "active" : ""}`}
            onClick={() => setDetailTab("iec104")}
          >
            {t("engineering.signals.tabOutboundIec104")}
          </button>
        </div>

        <div className="signals-detail-form-v2 signal-edit-modal-body">
          {detailTab === "general" ? (
            <>
              <fieldset className="signal-fieldset">
                <legend>{t("engineering.signals.fieldsetDef")}</legend>
                <label className="signal-field signal-field--wide">
                  <span>{t("engineering.signals.labelEtiket")}</span>
                  <input value={editLabel} onChange={(event) => setEditLabel(event.target.value)} />
                </label>
                <label className="signal-field signal-field--wide">
                  <span>{t("engineering.signals.labelDescription")}</span>
                  <input
                    value={editDescription}
                    onChange={(event) => setEditDescription(event.target.value)}
                    placeholder={t("engineering.signals.form.descriptionPlaceholder")}
                  />
                </label>
                <label className="signal-field">
                  <span>{t("engineering.signals.labelUnit")}</span>
                  <input
                    value={editUnit}
                    onChange={(event) => setEditUnit(event.target.value)}
                    placeholder={t("engineering.signals.form.unitPlaceholder")}
                  />
                </label>
                <label className="signal-field">
                  <span>{t("engineering.signals.labelSource")}</span>
                  <select value={editSource} onChange={(event) => setEditSource(event.target.value as SignalSource)}>
                    {SOURCES.map((src) => (
                      <option key={src} value={src}>
                        {SOURCE_LABEL[src]}
                      </option>
                    ))}
                  </select>
                </label>
                <label className="signal-field signal-field--wide">
                  <span>{t("engineering.signals.labelDataType")}</span>
                  <select
                    value={editDataType}
                    onChange={(event) => setEditDataType(event.target.value as SignalDataType)}
                  >
                    {DATA_TYPES.map((type) => (
                      <option key={type} value={type}>
                        {dataTypeLabel(type)}
                      </option>
                    ))}
                  </select>
                </label>
              </fieldset>

              <fieldset className="signal-fieldset">
                <legend>{t("engineering.signals.fieldsetDnp3")}</legend>
                <label className="signal-field">
                  <span>{t("engineering.signals.labelDnp3Class")}</span>
                  <select value={editDnp3Class} onChange={(event) => setEditDnp3Class(event.target.value)}>
                    <option value="Class 0">Class 0</option>
                    <option value="Class 1">Class 1</option>
                    <option value="Class 2">Class 2</option>
                    <option value="Class 3">Class 3</option>
                  </select>
                </label>
                <label className="signal-field">
                  <span>{t("engineering.signals.labelDnp3Index")}</span>
                  <input type="number" value={editIndex} onChange={(event) => setEditIndex(event.target.value)} />
                </label>
              </fieldset>

              <fieldset className="signal-fieldset">
                <legend>{t("engineering.signals.fieldsetScale")}</legend>
                <label className="signal-field">
                  <span>{t("engineering.signals.labelScale")}</span>
                  <input
                    type="number"
                    step="0.0001"
                    value={editScale}
                    onChange={(event) => setEditScale(event.target.value)}
                  />
                </label>
                <label className="signal-field">
                  <span>{t("engineering.signals.labelOffset")}</span>
                  <input
                    type="number"
                    step="0.0001"
                    value={editOffset}
                    onChange={(event) => setEditOffset(event.target.value)}
                  />
                </label>
              </fieldset>
            </>
          ) : null}

          {detailTab === "iec104" ? (
            <fieldset className="signal-fieldset">
              <legend>{t("engineering.signals.fieldsetIec104")}</legend>
              <div className="signal-iec104-toggle-row">
                <span className="signal-iec104-toggle-label">
                  {editIec104Enabled
                    ? t("engineering.signals.iec104Published")
                    : t("engineering.signals.iec104Unpublished")}
                </span>
                <ActiveSwitch
                  checked={editIec104Enabled}
                  onChange={setEditIec104Enabled}
                  title={t("engineering.signals.iec104ToggleTitle")}
                />
              </div>
              <div
                className={`signal-iec104-fields ${editIec104Enabled ? "" : "signal-iec104-fields--disabled"}`}
                aria-hidden={!editIec104Enabled}
              >
                <label className="signal-field signal-field--wide">
                  <span>{t("engineering.signals.iec104TypeId")}</span>
                  <select
                    value={editIec104TypeId}
                    onChange={(event) => setEditIec104TypeId(event.target.value)}
                    disabled={!editIec104Enabled}
                  >
                    <option value="">{t("engineering.signals.iec104NoPublish")}</option>
                    {IEC104_MONITOR_TYPES.filter((item) => item.dataTypes.includes(editDataType)).map((item) => (
                      <option key={item.id} value={item.id}>
                        {item.id} · {item.code} — {item.desc}
                      </option>
                    ))}
                    <optgroup label={t("engineering.signals.iec104OtherGroup")}>
                      {IEC104_MONITOR_TYPES.filter((item) => !item.dataTypes.includes(editDataType)).map((item) => (
                        <option key={item.id} value={item.id}>
                          {item.id} · {item.code} — {item.desc}
                        </option>
                      ))}
                    </optgroup>
                  </select>
                </label>
                <label className="signal-field">
                  <span>{t("engineering.signals.iec104Ioa")}</span>
                  <input
                    type="number"
                    min={0}
                    max={16777215}
                    value={editIec104Ioa}
                    onChange={(event) => setEditIec104Ioa(event.target.value)}
                    placeholder={t("engineering.signals.form.iec104IoaPlaceholder")}
                    disabled={!editIec104Enabled}
                  />
                </label>
                <label className={`signal-toggle-card${editIec104WithTimestamp ? " signal-toggle-card-on" : ""}`}>
                  <input
                    type="checkbox"
                    checked={editIec104WithTimestamp}
                    onChange={(event) => {
                      const newWithTs = event.target.checked;
                      setEditIec104WithTimestamp(newWithTs);
                      const currentId = parseIntOrNullModule(editIec104TypeId);
                      const newId = convertTypeIdForTimeTag(currentId, newWithTs);
                      if (newId !== currentId) {
                        setEditIec104TypeId(newId === null ? "" : String(newId));
                      }
                    }}
                    disabled={!editIec104Enabled}
                  />
                  <span className="signal-toggle-text">
                    <span className="signal-toggle-title">{t("engineering.signals.iec104WithTs")}</span>
                  </span>
                </label>
              </div>
            </fieldset>
          ) : null}

          {localError ? <p className="error-text">{localError}</p> : null}
        </div>

        <div className="signal-form-actions">
          <button type="button" className="primary-btn" onClick={() => void handleSave()} disabled={savingEdit}>
            {savingEdit ? t("engineering.signals.form.saving") : t("engineering.signals.form.save")}
          </button>
        </div>
      </div>
    </div>
  );
}
