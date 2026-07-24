import { useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import type { DeviceModelOption, SignalCatalogRow, SignalDataType, SignalSource, UserRole } from "../../shared/types";
import { SignalEditModal } from "./SignalEditModal";
import {
  DATA_TYPES,
  DATA_TYPE_LABEL,
  DATA_TYPE_SHORT,
  IEC104_MONITOR_TYPES,
  SOURCE_LABEL,
  SOURCES
} from "./signalCatalogConstants";

type Props = {
  role: UserRole;
  signals: SignalCatalogRow[];
  deviceModels: DeviceModelOption[];
  loading: boolean;
  error?: string;
  onUpdate: (signalKey: string, payload: Partial<Omit<SignalCatalogRow, "id" | "key">>) => Promise<void>;
};

type SourceFilter = "all" | SignalSource;
type TabKey = "all" | SignalDataType;

export function SignalsPage({ role, signals, deviceModels, loading, error, onUpdate }: Props) {
  const { t } = useTranslation();
  const canEdit = role === "installer";
  const dataTypeLabel = (type: SignalDataType): string =>
    t(`engineering.liveValues.dataType.${type}`, { defaultValue: DATA_TYPE_LABEL[type] });
  const [activeTab, setActiveTab] = useState<TabKey>("all");
  const [selectedKey, setSelectedKey] = useState<string>("");
  const [sourceFilter, setSourceFilter] = useState<SourceFilter>("all");
  const [searchTerm, setSearchTerm] = useState("");
  const [editModalSignal, setEditModalSignal] = useState<SignalCatalogRow | null>(null);
  const [iec104Expanded, setIec104Expanded] = useState(false);
  const [modbusExpanded, setModbusExpanded] = useState(false);
  const [exportMenuOpen, setExportMenuOpen] = useState(false);
  const [modelFilter, setModelFilter] = useState<string>(
    () => deviceModels[0]?.code ?? "horstmann_sn_2_0"
  );

  useEffect(() => {
    if (deviceModels.length === 0) return;
    if (!deviceModels.some((m) => m.code === modelFilter)) {
      setModelFilter(deviceModels[0].code);
    }
  }, [deviceModels, modelFilter]);

  const signalsForModel = useMemo(
    () => signals.filter((s) => s.model === modelFilter),
    [signals, modelFilter]
  );

  const countsByType = useMemo(() => {
    const map = new Map<SignalDataType, number>();
    DATA_TYPES.forEach((tp) => map.set(tp, 0));
    for (const sig of signalsForModel) {
      map.set(sig.data_type, (map.get(sig.data_type) ?? 0) + 1);
    }
    return map;
  }, [signalsForModel]);

  const filteredSignals = useMemo(() => {
    const q = searchTerm.trim().toLowerCase();
    return signalsForModel.filter((signal) => {
      if (activeTab !== "all" && signal.data_type !== activeTab) return false;
      if (sourceFilter !== "all" && signal.source !== sourceFilter) return false;
      if (!q) return true;
      return (
        signal.label.toLowerCase().includes(q) ||
        signal.key.toLowerCase().includes(q) ||
        (signal.description ?? "").toLowerCase().includes(q)
      );
    });
  }, [signalsForModel, activeTab, sourceFilter, searchTerm]);

  const selected = useMemo(
    () => signalsForModel.find((signal) => signal.key === selectedKey) ?? null,
    [signalsForModel, selectedKey]
  );

  const totalCount = signalsForModel.length;
  const visibleCount = filteredSignals.length;

  const downloadCsv = (filename: string, headers: string[], rows: (string | number)[][]) => {
    const escape = (v: string | number) => `"${String(v).replace(/"/g, '""')}"`;
    const csv = [headers, ...rows].map((row) => row.map(escape).join(",")).join("\r\n");
    const blob = new Blob(["﻿" + csv], { type: "text/csv;charset=utf-8;" });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = filename;
    link.click();
    URL.revokeObjectURL(url);
  };

  const exportIec104 = () => {
    const rows = signalsForModel
      .filter((s) => s.iec104_enabled !== false && s.iec104_type_id !== null && s.iec104_type_id !== undefined)
      .map((s) => [
        s.label,
        s.key,
        s.iec104_type_id ?? "",
        IEC104_MONITOR_TYPES.find((m) => m.id === s.iec104_type_id)?.code ?? "",
        s.iec104_ioa ?? "",
        SOURCE_LABEL[s.source]
      ]);
    downloadCsv(`iec104_${modelFilter}.csv`, ["Etiket", "Key", "Type ID", "ASDU", "IOA", "Kaynak"], rows);
    setExportMenuOpen(false);
  };

  const exportModbus = () => {
    const rows = signalsForModel
      .filter(
        (s) =>
          s.modbus_function !== null &&
          s.modbus_function !== undefined &&
          s.modbus_address !== null &&
          s.modbus_address !== undefined
      )
      .map((s) => [s.label, s.key, s.modbus_function ?? "", s.modbus_address ?? "", SOURCE_LABEL[s.source]]);
    downloadCsv(`modbus_${modelFilter}.csv`, ["Etiket", "Key", "Function Code", "Adres", "Kaynak"], rows);
    setExportMenuOpen(false);
  };

  return (
    <section className="tab-panel signals-panel signals-panel-modern">
      <div className="signals-header-row">
        <label className="signals-model-label">
          Cihaz Modeli
          <select
            className="signals-model-select"
            value={modelFilter}
            onChange={(event) => {
              setModelFilter(event.target.value);
              setSelectedKey("");
            }}
          >
            {deviceModels.length === 0 ? (
              <option value={modelFilter}>{modelFilter}</option>
            ) : (
              deviceModels.map((opt) => (
                <option key={opt.code} value={opt.code}>
                  {opt.label}
                </option>
              ))
            )}
          </select>
        </label>
        <div className="signals-source-chips">
          <button
            type="button"
            className={`chip ${sourceFilter === "all" ? "chip-active" : ""}`}
            onClick={() => setSourceFilter("all")}
          >
            {t("engineering.signals.allSources")}
          </button>
          {SOURCES.map((src) => (
            <button
              key={src}
              type="button"
              className={`chip chip-source-${src} ${sourceFilter === src ? "chip-active" : ""}`}
              onClick={() => setSourceFilter(src)}
            >
              {SOURCE_LABEL[src]}
            </button>
          ))}
        </div>
        <div className="signals-type-tabs signals-type-tabs--inline">
          <button
            className={`signals-type-tab ${activeTab === "all" ? "active" : ""}`}
            onClick={() => setActiveTab("all")}
          >
            <span className="stt-label">Tümü</span>
            <span className="stt-count">{totalCount}</span>
          </button>
          {DATA_TYPES.map((type) => (
            <button
              key={type}
              className={`signals-type-tab stt-${type} ${activeTab === type ? "active" : ""}`}
              onClick={() => setActiveTab(type)}
            >
              <span className="stt-label">{dataTypeLabel(type)}</span>
              <span className="stt-count">{countsByType.get(type) ?? 0}</span>
            </button>
          ))}
        </div>
        <div
          className="signals-export-wrap"
          onBlur={(event) => {
            const nextFocus = event.relatedTarget;
            if (!(nextFocus instanceof Node) || !event.currentTarget.contains(nextFocus)) {
              setExportMenuOpen(false);
            }
          }}
        >
          <button
            type="button"
            className="secondary-btn signals-export-btn"
            onClick={() => setExportMenuOpen((v) => !v)}
          >
            <span className="material-symbols-outlined" aria-hidden="true">download</span>
            {t("engineering.signals.export.button")}
          </button>
          {exportMenuOpen ? (
            <div className="signals-export-menu">
              <button type="button" onClick={exportIec104}>
                {t("engineering.signals.export.iec104")}
              </button>
              <button type="button" onClick={exportModbus}>
                {t("engineering.signals.export.modbus")}
              </button>
            </div>
          ) : null}
        </div>
      </div>

      <div className="signals-toolbar">
        <input
          className="signals-search"
          type="search"
          placeholder={t("engineering.signals.search")}
          value={searchTerm}
          onChange={(event) => setSearchTerm(event.target.value)}
        />
        <span className="signals-count-pill">
          {visibleCount} / {totalCount}
        </span>
        {!canEdit ? (
          <span className="helper-text signals-toolbar-readonly">{t("engineering.signals.readOnlyHint")}</span>
        ) : null}
      </div>

      <div className="signals-main-layout">
        <div className="signals-list-column">
          {loading ? <p className="helper-text">{t("common.loading")}</p> : null}
          <div className="signals-list-wrap">
            <table className="signals-list-table">
              <thead>
                <tr>
                  <th className="col-source-first">Cihaz</th>
                  <th className="col-type">Veri Tipi</th>
                  <th className="col-addr">Grup/Indeks</th>
                  <th className="col-label">Açıklama</th>
                  <th className={`col-proto ${iec104Expanded ? "col-proto--expanded" : ""}`}>
                    <span className="col-proto-head">
                      IEC104
                      <button
                        type="button"
                        className="col-proto-toggle"
                        title={iec104Expanded ? "Daralt" : "Genişlet"}
                        onClick={() => setIec104Expanded((v) => !v)}
                      >
                        <span className="material-symbols-outlined" aria-hidden="true">
                          {iec104Expanded ? "chevron_right" : "chevron_left"}
                        </span>
                      </button>
                    </span>
                  </th>
                  <th className={`col-proto ${modbusExpanded ? "col-proto--expanded" : ""}`}>
                    <span className="col-proto-head">
                      Modbus TCP
                      <button
                        type="button"
                        className="col-proto-toggle"
                        title={modbusExpanded ? "Daralt" : "Genişlet"}
                        onClick={() => setModbusExpanded((v) => !v)}
                      >
                        <span className="material-symbols-outlined" aria-hidden="true">
                          {modbusExpanded ? "chevron_right" : "chevron_left"}
                        </span>
                      </button>
                    </span>
                  </th>
                </tr>
              </thead>
              <tbody>
                {filteredSignals.map((signal) => {
                  const isActive = selectedKey === signal.key;
                  const iec104On =
                    signal.iec104_enabled !== false &&
                    signal.iec104_type_id !== null &&
                    signal.iec104_type_id !== undefined;
                  const iec104Off =
                    signal.iec104_type_id !== null &&
                    signal.iec104_type_id !== undefined &&
                    signal.iec104_enabled === false;
                  const modbusOn =
                    signal.modbus_function !== null &&
                    signal.modbus_function !== undefined &&
                    signal.modbus_address !== null &&
                    signal.modbus_address !== undefined;
                  return (
                    <tr
                      key={signal.key}
                      className={`signal-row ${isActive ? "signal-row-active" : ""} ${
                        signal.is_active ? "" : "signal-row-inactive"
                      }`}
                      onClick={() => setSelectedKey(signal.key)}
                    >
                      <td className="col-source-first">
                        <span className={`badge badge-source badge-source-${signal.source}`}>
                          {SOURCE_LABEL[signal.source]}
                        </span>
                      </td>
                      <td className="col-type">
                        <span className={`badge badge-${signal.data_type}`}>{DATA_TYPE_SHORT[signal.data_type]}</span>
                      </td>
                      <td className="col-addr">
                        <span className="mono">
                          G{signal.dnp3_object_group} · i{signal.dnp3_index}
                        </span>
                      </td>
                      <td className="col-label">
                        <span className="cell-strong">{signal.label}</span>
                        {!signal.is_active ? (
                          <span className="cell-inactive-hint">{t("engineering.signals.inactive")}</span>
                        ) : null}
                      </td>
                      <td className={`col-proto ${iec104Expanded ? "col-proto--expanded" : ""}`}>
                        {iec104On ? (
                          <span className="proto-check proto-check--on" title={t("engineering.signals.iec104OnTitle")}>
                            <span className="material-symbols-outlined" aria-hidden="true">check</span>
                            {iec104Expanded ? (
                              <span className="proto-check-code">
                                {IEC104_MONITOR_TYPES.find((m) => m.id === signal.iec104_type_id)?.code ?? "—"}
                              </span>
                            ) : null}
                            {signal.iec104_ioa ?? "—"}
                          </span>
                        ) : iec104Off ? (
                          <span className="proto-check proto-check--off" title={t("engineering.signals.iec104OffTitle")}>
                            <span className="material-symbols-outlined" aria-hidden="true">close</span>
                          </span>
                        ) : (
                          <span className="proto-check proto-check--empty">—</span>
                        )}
                      </td>
                      <td className={`col-proto ${modbusExpanded ? "col-proto--expanded" : ""}`}>
                        {modbusOn ? (
                          <span className="proto-check proto-check--on">
                            <span className="material-symbols-outlined" aria-hidden="true">check</span>
                            {modbusExpanded ? (
                              <span className="proto-check-code">FC{signal.modbus_function}</span>
                            ) : null}
                            {signal.modbus_address}
                          </span>
                        ) : (
                          <span className="proto-check proto-check--empty">—</span>
                        )}
                      </td>
                    </tr>
                  );
                })}
                {filteredSignals.length === 0 && !loading ? (
                  <tr>
                    <td className="signals-empty-cell" colSpan={6}>
                      {totalCount === 0 ? t("engineering.signals.noSignals") : t("engineering.signals.noResults")}
                    </td>
                  </tr>
                ) : null}
              </tbody>
            </table>
          </div>
        </div>

        <aside className="signals-detail-column">
          <div className="signals-detail-card">
            <div className="signals-detail-head">
              <div className="signals-detail-title">
                <h4>{selected ? selected.label : t("engineering.signals.selectHint")}</h4>
              </div>
              {selected ? (
                <div className="signals-detail-badges">
                  <span className={`badge badge-source badge-source-${selected.source}`}>
                    {SOURCE_LABEL[selected.source]}
                  </span>
                  <span className={`badge badge-${selected.data_type}`}>{DATA_TYPE_SHORT[selected.data_type]}</span>
                  {!selected.is_active ? (
                    <span className="cell-inactive-hint">{t("engineering.signals.inactive")}</span>
                  ) : null}
                </div>
              ) : null}
            </div>

            {!selected ? (
              <p className="helper-text signals-detail-empty">{t("engineering.signals.selectListHint")}</p>
            ) : (
              <div className="signals-detail-form-scroll">
                <dl className="signals-detail-readonly">
                  <div className="signals-detail-readonly-row">
                    <dt>{t("engineering.signals.labelDescription")}</dt>
                    <dd>{selected.description || "—"}</dd>
                  </div>
                  <div className="signals-detail-readonly-row">
                    <dt>{t("engineering.signals.labelDataType")}</dt>
                    <dd>{dataTypeLabel(selected.data_type)}</dd>
                  </div>
                  <div className="signals-detail-readonly-row">
                    <dt>Grup/Indeks</dt>
                    <dd>
                      G{selected.dnp3_object_group} · i{selected.dnp3_index}
                    </dd>
                  </div>
                  <div className="signals-detail-readonly-row">
                    <dt>{t("engineering.signals.labelSource")}</dt>
                    <dd>{SOURCE_LABEL[selected.source]}</dd>
                  </div>
                  <div className="signals-detail-readonly-row">
                    <dt>{t("engineering.signals.labelScale")}</dt>
                    <dd>
                      {selected.scale} / {selected.offset}
                    </dd>
                  </div>
                  <div className="signals-detail-readonly-row">
                    <dt>{t("engineering.signals.labelDnp3Class")}</dt>
                    <dd>{selected.dnp3_class}</dd>
                  </div>
                  {selected.unit ? (
                    <div className="signals-detail-readonly-row">
                      <dt>{t("engineering.signals.labelUnit")}</dt>
                      <dd>{selected.unit}</dd>
                    </div>
                  ) : null}
                  <div className="signals-detail-readonly-row">
                    <dt>IEC 104 Type ID</dt>
                    <dd>
                      {selected.iec104_enabled !== false &&
                      selected.iec104_type_id !== null &&
                      selected.iec104_type_id !== undefined
                        ? selected.iec104_type_id
                        : "—"}
                    </dd>
                  </div>
                  <div className="signals-detail-readonly-row">
                    <dt>IEC 104 IOA</dt>
                    <dd>
                      {selected.iec104_enabled !== false &&
                      selected.iec104_ioa !== null &&
                      selected.iec104_ioa !== undefined
                        ? selected.iec104_ioa
                        : "—"}
                    </dd>
                  </div>
                </dl>

                {canEdit ? (
                  <div className="signal-form-actions">
                    <button type="button" className="primary-btn" onClick={() => setEditModalSignal(selected)}>
                      {t("engineering.signals.editProperties")}
                    </button>
                  </div>
                ) : null}
              </div>
            )}
          </div>
        </aside>
      </div>

      {error ? <p className="error-text">{error}</p> : null}

      {editModalSignal ? (
        <SignalEditModal signal={editModalSignal} onSave={onUpdate} onClose={() => setEditModalSignal(null)} />
      ) : null}
    </section>
  );
}
