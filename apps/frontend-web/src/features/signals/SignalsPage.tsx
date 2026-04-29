import { useEffect, useMemo, useState } from "react";
import type { DeviceModelOption, SignalCatalogRow, SignalDataType, SignalSource, UserRole } from "../../shared/types";

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

const DATA_TYPES: SignalDataType[] = [
  "analog",
  "binary",
  "counter",
  "string"
];

const SOURCES: SignalSource[] = ["master", "sat01", "sat02"];

const SOURCE_LABEL: Record<SignalSource, string> = {
  master: "Master",
  sat01: "Satellite 01",
  sat02: "Satellite 02"
};

const DATA_TYPE_LABEL: Record<SignalDataType, string> = {
  analog: "Analog Input",
  binary: "Binary Input",
  counter: "Counter",
  string: "String"
};

const DATA_TYPE_SHORT: Record<SignalDataType, string> = {
  analog: "Analog",
  binary: "Binary",
  counter: "Counter",
  string: "String"
};

// DNP3 standart nesne grubu - veri tipine göre 1-1 eşlesir.
// Kullanici UI'da elle girmemeli, veri tipi seciminden otomatik turetilir.
const DNP3_GROUP_BY_TYPE: Record<SignalDataType, number> = {
  analog: 30,
  binary: 1,
  counter: 20,
  string: 110
};

export function SignalsPage({
  role,
  signals,
  deviceModels,
  loading,
  error,
  onUpdate
}: Props) {
  const canEdit = role === "installer";
  const [activeTab, setActiveTab] = useState<TabKey>("all");
  const [selectedKey, setSelectedKey] = useState<string>("");
  const [localError, setLocalError] = useState("");
  const [sourceFilter, setSourceFilter] = useState<SourceFilter>("all");
  const [searchTerm, setSearchTerm] = useState("");
  const [savingEdit, setSavingEdit] = useState(false);
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
    DATA_TYPES.forEach((t) => map.set(t, 0));
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

  const [editLabel, setEditLabel] = useState("");
  const [editUnit, setEditUnit] = useState("");
  const [editDescription, setEditDescription] = useState("");
  const [editSource, setEditSource] = useState<SignalSource>("master");
  const [editDnp3Class, setEditDnp3Class] = useState("Class 1");
  const [editDataType, setEditDataType] = useState<SignalDataType>("analog");
  const [editIndex, setEditIndex] = useState("0");
  const [editScale, setEditScale] = useState("1");
  const [editOffset, setEditOffset] = useState("0");
  const [editIsActive, setEditIsActive] = useState(true);

  useEffect(() => {
    if (selected) {
      setEditLabel(selected.label);
      setEditUnit(selected.unit ?? "");
      setEditDescription(selected.description ?? "");
      setEditSource(selected.source);
      setEditDnp3Class(selected.dnp3_class);
      setEditDataType(selected.data_type);
      setEditIndex(String(selected.dnp3_index));
      setEditScale(String(selected.scale));
      setEditOffset(String(selected.offset));
      setEditIsActive(selected.is_active);
      setLocalError("");
    }
  }, [selected]);

  const handleSave = async () => {
    if (!selected) return;
    setLocalError("");
    setSavingEdit(true);
    try {
      // DNP3 Object Group veri tipine gore otomatik turetilir
      // (Analog=30, Analog Out=40, Binary=1, Binary Out=10, Counter=20, String=110).
      const dnp3Group = DNP3_GROUP_BY_TYPE[editDataType];
      await onUpdate(selected.key, {
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
        is_active: editIsActive
      });
    } catch (err) {
      setLocalError(err instanceof Error ? err.message : "Sinyal güncellenemedi.");
    } finally {
      setSavingEdit(false);
    }
  };

  const totalCount = signalsForModel.length;
  const visibleCount = filteredSignals.length;

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
            <span className="stt-label">{DATA_TYPE_LABEL[type]}</span>
            <span className="stt-count">{countsByType.get(type) ?? 0}</span>
          </button>
        ))}
        </div>
      </div>

      <div className="signals-toolbar">
        <input
          className="signals-search"
          type="search"
          placeholder="Ara (etiket, key veya açıklama)..."
          value={searchTerm}
          onChange={(event) => setSearchTerm(event.target.value)}
        />
        <div className="signals-source-chips">
          <button
            type="button"
            className={`chip ${sourceFilter === "all" ? "chip-active" : ""}`}
            onClick={() => setSourceFilter("all")}
          >
            Tüm Kaynaklar
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
        <span className="signals-count-pill">
          {visibleCount} / {totalCount}
        </span>
        {!canEdit ? (
          <span className="helper-text signals-toolbar-readonly">Düzenleme: kurulumcu</span>
        ) : null}
      </div>

      <div className="signals-main-layout">
        <div className="signals-list-column">
          {loading ? <p className="helper-text">Yükleniyor...</p> : null}
          <div className="signals-list-wrap">
            <table className="signals-list-table">
              <thead>
                <tr>
                  <th className="col-source-first">Kaynak</th>
                  <th className="col-label">Sinyal</th>
                  <th className="col-type">Tip</th>
                  <th className="col-addr">Adres</th>
                  <th className="col-unit">Birim</th>
                </tr>
              </thead>
              <tbody>
                {filteredSignals.map((signal) => {
                  const isActive = selectedKey === signal.key;
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
                      <td className="col-label">
                        <div className="cell-strong">{signal.label}</div>
                        <div className="cell-helper">{signal.key}</div>
                        {!signal.is_active ? (
                          <div className="cell-inactive-hint" title="Pasif sinyal">
                            pasif
                          </div>
                        ) : null}
                      </td>
                      <td className="col-type">
                        <span className={`badge badge-${signal.data_type}`}>
                          {DATA_TYPE_SHORT[signal.data_type]}
                        </span>
                      </td>
                      <td className="col-addr">
                        <span className="mono">
                          G{signal.dnp3_object_group} · i{signal.dnp3_index}
                        </span>
                      </td>
                      <td className="col-unit">{signal.unit ?? "-"}</td>
                    </tr>
                  );
                })}
                {filteredSignals.length === 0 && !loading ? (
                  <tr>
                    <td colSpan={5} className="signals-empty-cell">
                      {totalCount === 0
                        ? "Henüz sinyal tanımlı değil."
                        : "Filtreye uygun sinyal bulunamadı."}
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
                <h4>{selected ? selected.label : "Sinyal özellikleri"}</h4>
                {selected ? <code className="detail-key">{selected.key}</code> : null}
              </div>
              {selected ? (
                <div className="signals-detail-badges">
                  <span className={`badge badge-source badge-source-${selected.source}`}>
                    {SOURCE_LABEL[selected.source]}
                  </span>
                  <span className={`badge badge-${selected.data_type}`}>
                    {DATA_TYPE_SHORT[selected.data_type]}
                  </span>
                </div>
              ) : null}
            </div>

            {!selected ? (
              <p className="helper-text signals-detail-empty">Listeden bir sinyal seçin; özellikler burada düzenlenir.</p>
            ) : (
              <div className="signals-detail-form-scroll">
              <div className="device-detail-form detail-form-wide signals-detail-form">
                <div className="form-row-2col">
                  <label>
                    Etiket
                    <input
                      value={editLabel}
                      onChange={(event) => setEditLabel(event.target.value)}
                      disabled={!canEdit}
                    />
                  </label>
                  <label>
                    Birim
                    <input
                      value={editUnit}
                      onChange={(event) => setEditUnit(event.target.value)}
                      disabled={!canEdit}
                    />
                  </label>
                </div>

                <label>
                  Açıklama
                  <input
                    value={editDescription}
                    onChange={(event) => setEditDescription(event.target.value)}
                    disabled={!canEdit}
                  />
                </label>

                <div className="form-row-2col">
                  <label>
                    Kaynak
                    <select
                      value={editSource}
                      onChange={(event) => setEditSource(event.target.value as SignalSource)}
                      disabled={!canEdit}
                    >
                      {SOURCES.map((src) => (
                        <option key={src} value={src}>
                          {SOURCE_LABEL[src]}
                        </option>
                      ))}
                    </select>
                  </label>
                  <label>
                    Veri Tipi
                    <select
                      value={editDataType}
                      onChange={(event) => setEditDataType(event.target.value as SignalDataType)}
                      disabled={!canEdit}
                    >
                      {DATA_TYPES.map((type) => (
                        <option key={type} value={type}>
                          {DATA_TYPE_LABEL[type]}
                        </option>
                      ))}
                    </select>
                  </label>
                </div>

                <fieldset className="detail-fieldset">
                  <legend>DNP3 Adres</legend>
                  <div className="form-row-2col">
                    <label>
                      DNP3 Class
                      <input
                        value={editDnp3Class}
                        onChange={(event) => setEditDnp3Class(event.target.value)}
                        disabled={!canEdit}
                        placeholder="Class 1 / 2 / 3"
                      />
                    </label>
                    <label>
                      Nokta (Index)
                      <input
                        type="number"
                        value={editIndex}
                        onChange={(event) => setEditIndex(event.target.value)}
                        disabled={!canEdit}
                      />
                    </label>
                  </div>
                  <p className="field-hint">
                    DNP3 nesne grubu, seçilen veri tipine göre otomatik atanır
                    (Analog=30, Analog Out=40, Binary=1, Binary Out=10, Counter=20, String=110).
                  </p>
                </fieldset>

                <fieldset className="detail-fieldset">
                  <legend>Ölçeklendirme</legend>
                  <div className="form-row-2col">
                    <label>
                      Scale
                      <input
                        type="number"
                        step="0.0001"
                        value={editScale}
                        onChange={(event) => setEditScale(event.target.value)}
                        disabled={!canEdit}
                      />
                    </label>
                    <label>
                      Offset
                      <input
                        type="number"
                        step="0.0001"
                        value={editOffset}
                        onChange={(event) => setEditOffset(event.target.value)}
                        disabled={!canEdit}
                      />
                    </label>
                  </div>
                </fieldset>

                <div className="detail-toggles">
                  <label className="checkbox-row">
                    <input
                      type="checkbox"
                      checked={editIsActive}
                      onChange={(event) => setEditIsActive(event.target.checked)}
                      disabled={!canEdit}
                    />
                    <span>
                      <strong>Aktif</strong>
                      <small className="field-hint-inline">
                        Pasif sinyaller gateway tarafından okunmaz.
                      </small>
                    </span>
                  </label>
                </div>

                {canEdit ? (
                  <div className="device-form-actions">
                    <button
                      type="button"
                      className="primary-btn"
                      onClick={() => void handleSave()}
                      disabled={savingEdit}
                    >
                      {savingEdit ? "Kaydediliyor..." : "Kaydet"}
                    </button>
                  </div>
                ) : null}
              </div>
              </div>
            )}
          </div>
        </aside>
      </div>

      {(localError || error) ? <p className="error-text">{localError || error}</p> : null}
    </section>
  );
}
