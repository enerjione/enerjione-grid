import { useEffect, useMemo, useState } from "react";
import { ActiveSwitch } from "../../components/ActiveSwitch";
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
type DetailTab = "general" | "iec104";

// IEC 60870-5-104 monitor-direction ASDU Type ID'leri.
// Kullanici dropdown'dan secsin diye standart kataloga referans.
const IEC104_MONITOR_TYPES: { id: number; code: string; desc: string; dataTypes: SignalDataType[] }[] = [
  { id: 1,  code: "M_SP_NA_1", desc: "Single point",                   dataTypes: ["binary"] },
  { id: 3,  code: "M_DP_NA_1", desc: "Double point",                   dataTypes: ["binary"] },
  { id: 9,  code: "M_ME_NA_1", desc: "Normalized measured value",      dataTypes: ["analog"] },
  { id: 11, code: "M_ME_NB_1", desc: "Scaled measured value",          dataTypes: ["analog"] },
  { id: 13, code: "M_ME_NC_1", desc: "Short floating point",           dataTypes: ["analog"] },
  { id: 15, code: "M_IT_NA_1", desc: "Integrated total (counter)",     dataTypes: ["counter"] },
  { id: 30, code: "M_SP_TB_1", desc: "Single point with CP56Time2a",   dataTypes: ["binary"] },
  { id: 31, code: "M_DP_TB_1", desc: "Double point with CP56Time2a",   dataTypes: ["binary"] },
  { id: 34, code: "M_ME_TD_1", desc: "Normalized + CP56Time2a",        dataTypes: ["analog"] },
  { id: 35, code: "M_ME_TE_1", desc: "Scaled + CP56Time2a",            dataTypes: ["analog"] },
  { id: 36, code: "M_ME_TF_1", desc: "Short float + CP56Time2a",       dataTypes: ["analog"] },
  { id: 37, code: "M_IT_TB_1", desc: "Counter + CP56Time2a",           dataTypes: ["counter"] }
];

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
  // Outbound template adresleme — IEC104 / Modbus / MQTT
  const [editIec104Enabled, setEditIec104Enabled] = useState(true);
  const [editIec104TypeId, setEditIec104TypeId] = useState("");
  const [editIec104Ioa, setEditIec104Ioa] = useState("");
  const [editIec104IoaOffset, setEditIec104IoaOffset] = useState("");
  const [editModbusFunction, setEditModbusFunction] = useState("");
  const [editModbusAddress, setEditModbusAddress] = useState("");
  const [editMqttTopic, setEditMqttTopic] = useState("");
  // Detay panel sekmesi: Genel ozellikler vs Outbound (IEC 104) adresleri.
  const [detailTab, setDetailTab] = useState<DetailTab>("general");

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
      setEditIec104TypeId(
        selected.iec104_type_id !== null && selected.iec104_type_id !== undefined
          ? String(selected.iec104_type_id)
          : ""
      );
      setEditIec104Ioa(
        selected.iec104_ioa !== null && selected.iec104_ioa !== undefined
          ? String(selected.iec104_ioa)
          : ""
      );
      // IEC 104 yayini default true; kayit yoksa veya alan tanimsizsa true kabul et.
      setEditIec104Enabled(selected.iec104_enabled !== false);
      setEditIec104IoaOffset(
        selected.iec104_ioa_offset !== null && selected.iec104_ioa_offset !== undefined
          ? String(selected.iec104_ioa_offset)
          : ""
      );
      setEditModbusFunction(
        selected.modbus_function !== null && selected.modbus_function !== undefined
          ? String(selected.modbus_function)
          : ""
      );
      setEditModbusAddress(
        selected.modbus_address !== null && selected.modbus_address !== undefined
          ? String(selected.modbus_address)
          : ""
      );
      setEditMqttTopic(selected.mqtt_topic ?? "");
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
      const parseIntOrNull = (v: string): number | null => {
        const t = v.trim();
        if (!t) return null;
        const n = Number(t);
        return Number.isFinite(n) ? Math.round(n) : null;
      };
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
        is_active: editIsActive,
        iec104_type_id: parseIntOrNull(editIec104TypeId),
        iec104_ioa: parseIntOrNull(editIec104Ioa),
        // iec104_ioa_offset deprecated — UI'dan kaldirildi, backend mevcut degeri korur.
        iec104_enabled: editIec104Enabled,
        modbus_function: parseIntOrNull(editModbusFunction),
        modbus_address: parseIntOrNull(editModbusAddress),
        mqtt_topic: editMqttTopic.trim() || null
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
            <ul className="signals-card-list">
              {filteredSignals.map((signal) => {
                const isActive = selectedKey === signal.key;
                // IEC 104 "Yayinda" sayilmasi icin: sinyal aktif + iec104_enabled !== false
                // + Type ID dolu. Hicbiri yoksa rozet gizlenir.
                const iec104On =
                  signal.iec104_enabled !== false &&
                  signal.iec104_type_id !== null &&
                  signal.iec104_type_id !== undefined;
                const iec104Off =
                  (signal.iec104_type_id !== null && signal.iec104_type_id !== undefined) &&
                  signal.iec104_enabled === false;
                return (
                  <li
                    key={signal.key}
                    className={`signal-card ${isActive ? "signal-card-active" : ""} ${
                      signal.is_active ? "" : "signal-card-inactive"
                    }`}
                    onClick={() => setSelectedKey(signal.key)}
                  >
                    <div className="signal-card-top">
                      <span className={`badge badge-source badge-source-${signal.source}`}>
                        {SOURCE_LABEL[signal.source]}
                      </span>
                      <span className={`badge badge-${signal.data_type}`}>
                        {DATA_TYPE_SHORT[signal.data_type]}
                      </span>
                      {iec104On ? (
                        <span className="signal-card-iec104 signal-card-iec104--on" title="IEC 104 yayinda">
                          104
                        </span>
                      ) : iec104Off ? (
                        <span className="signal-card-iec104 signal-card-iec104--off" title="IEC 104 yayindan kaldirildi">
                          104·off
                        </span>
                      ) : null}
                      <span className="signal-card-addr">
                        G{signal.dnp3_object_group} · i{signal.dnp3_index}
                      </span>
                      {signal.unit ? <span className="signal-card-unit">{signal.unit}</span> : null}
                      {!signal.is_active ? (
                        <span className="signal-card-inactive-flag">pasif</span>
                      ) : null}
                    </div>
                    <div className="signal-card-name">{signal.label}</div>
                    <div className="signal-card-key">{signal.key}</div>
                  </li>
                );
              })}
              {filteredSignals.length === 0 && !loading ? (
                <li className="signals-empty-cell">
                  {totalCount === 0
                    ? "Henüz sinyal tanımlı değil."
                    : "Filtreye uygun sinyal bulunamadı."}
                </li>
              ) : null}
            </ul>
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
                  <ActiveSwitch
                    checked={editIsActive}
                    onChange={setEditIsActive}
                    disabled={!canEdit}
                    title="Pasif sinyaller gateway tarafından okunmaz; tarihçede kalır."
                  />
                </div>
              ) : null}
            </div>

            {!selected ? (
              <p className="helper-text signals-detail-empty">Listeden bir sinyal seçin; özellikler burada düzenlenir.</p>
            ) : (
              <div className="signals-detail-form-scroll">
                <div className="signal-detail-tabs" role="tablist">
                  <button
                    type="button"
                    role="tab"
                    aria-selected={detailTab === "general"}
                    className={`signal-detail-tab ${detailTab === "general" ? "active" : ""}`}
                    onClick={() => setDetailTab("general")}
                  >
                    Genel
                  </button>
                  <button
                    type="button"
                    role="tab"
                    aria-selected={detailTab === "iec104"}
                    className={`signal-detail-tab ${detailTab === "iec104" ? "active" : ""}`}
                    onClick={() => setDetailTab("iec104")}
                  >
                    Outbound · IEC 104
                  </button>
                </div>
                <div className="signals-detail-form-v2">
                  {detailTab === "general" ? (
                  <>
                  <fieldset className="signal-fieldset" disabled={!canEdit}>
                    <legend>Tanım</legend>
                    <label className="signal-field signal-field--wide">
                      <span>Etiket</span>
                      <input
                        value={editLabel}
                        onChange={(event) => setEditLabel(event.target.value)}
                      />
                    </label>
                    <label className="signal-field signal-field--wide">
                      <span>Açıklama</span>
                      <input
                        value={editDescription}
                        onChange={(event) => setEditDescription(event.target.value)}
                        placeholder="Bu sinyalin amacını kısaca yazın..."
                      />
                    </label>
                    <label className="signal-field">
                      <span>Birim</span>
                      <input
                        value={editUnit}
                        onChange={(event) => setEditUnit(event.target.value)}
                        placeholder="V, A, °C..."
                      />
                    </label>
                    <label className="signal-field">
                      <span>Kaynak</span>
                      <select
                        value={editSource}
                        onChange={(event) => setEditSource(event.target.value as SignalSource)}
                      >
                        {SOURCES.map((src) => (
                          <option key={src} value={src}>
                            {SOURCE_LABEL[src]}
                          </option>
                        ))}
                      </select>
                    </label>
                    <label className="signal-field signal-field--wide">
                      <span>Veri Tipi</span>
                      <select
                        value={editDataType}
                        onChange={(event) => setEditDataType(event.target.value as SignalDataType)}
                      >
                        {DATA_TYPES.map((type) => (
                          <option key={type} value={type}>
                            {DATA_TYPE_LABEL[type]}
                          </option>
                        ))}
                      </select>
                    </label>
                  </fieldset>

                  <fieldset className="signal-fieldset" disabled={!canEdit}>
                    <legend>DNP3 Adres</legend>
                    <label className="signal-field">
                      <span>DNP3 Class</span>
                      <input
                        value={editDnp3Class}
                        onChange={(event) => setEditDnp3Class(event.target.value)}
                        placeholder="Class 1 / 2 / 3"
                      />
                    </label>
                    <label className="signal-field">
                      <span>Nokta (Index)</span>
                      <input
                        type="number"
                        value={editIndex}
                        onChange={(event) => setEditIndex(event.target.value)}
                      />
                    </label>
                    <p className="signal-fieldset-hint">
                      DNP3 nesne grubu, seçilen veri tipine göre otomatik atanır
                      (Analog=30, Binary=1, Counter=20, String=110).
                    </p>
                  </fieldset>

                  <fieldset className="signal-fieldset" disabled={!canEdit}>
                    <legend>Ölçeklendirme</legend>
                    <label className="signal-field">
                      <span>Scale</span>
                      <input
                        type="number"
                        step="0.0001"
                        value={editScale}
                        onChange={(event) => setEditScale(event.target.value)}
                      />
                    </label>
                    <label className="signal-field">
                      <span>Offset</span>
                      <input
                        type="number"
                        step="0.0001"
                        value={editOffset}
                        onChange={(event) => setEditOffset(event.target.value)}
                      />
                    </label>
                    <p className="signal-fieldset-hint">
                      Ham değer = scale × ham + offset. Birim sahaya göre uygulanır.
                    </p>
                  </fieldset>
                  </>
                  ) : null}

                  {detailTab === "iec104" ? (
                  <>
                  <fieldset className="signal-fieldset" disabled={!canEdit}>
                    <legend>IEC 60870-5-104</legend>
                    <div className="signal-iec104-toggle-row">
                      <span className="signal-iec104-toggle-label">
                        {editIec104Enabled ? "Yayında" : "Yayından kaldırıldı"}
                      </span>
                      <ActiveSwitch
                        checked={editIec104Enabled}
                        onChange={setEditIec104Enabled}
                        disabled={!canEdit}
                        title="IEC 104 outbound yayını sinyal bazında kapatılabilir."
                      />
                    </div>
                    <div
                      className={`signal-iec104-fields ${editIec104Enabled ? "" : "signal-iec104-fields--disabled"}`}
                      aria-hidden={!editIec104Enabled}
                    >
                      <label className="signal-field signal-field--wide">
                        <span>ASDU Type ID</span>
                        <select
                          value={editIec104TypeId}
                          onChange={(event) => setEditIec104TypeId(event.target.value)}
                          disabled={!canEdit || !editIec104Enabled}
                        >
                          <option value="">— Yayinlama —</option>
                          {IEC104_MONITOR_TYPES.filter(
                            (t) => t.dataTypes.includes(editDataType)
                          ).map((t) => (
                            <option key={t.id} value={t.id}>
                              {t.id} · {t.code} — {t.desc}
                            </option>
                          ))}
                          <optgroup label="Diger (uyumsuz veri tipi)">
                            {IEC104_MONITOR_TYPES.filter(
                              (t) => !t.dataTypes.includes(editDataType)
                            ).map((t) => (
                              <option key={t.id} value={t.id}>
                                {t.id} · {t.code} — {t.desc}
                              </option>
                            ))}
                          </optgroup>
                        </select>
                      </label>
                      <label className="signal-field">
                        <span>IOA (Information Object Address)</span>
                        <input
                          type="number"
                          min={0}
                          max={16777215}
                          value={editIec104Ioa}
                          onChange={(event) => setEditIec104Ioa(event.target.value)}
                          placeholder="örn. 1001"
                          disabled={!canEdit || !editIec104Enabled}
                        />
                      </label>
                      <p className="signal-fieldset-hint">
                        {editIec104Enabled
                          ? "Bu sinyal IEC 104 outbound master'a yayınlanırken kullanılır. ASDU Common Address (CA) ise cihaz bazlı; Cihazlar sayfasından ayarlanır. Type ID boş bırakılırsa bu sinyal yayınlanmaz."
                          : "Yayın kapalı. Sinyali yayına almak için yukarıdaki anahtarı açın."}
                      </p>
                    </div>
                  </fieldset>
                  </>
                  ) : null}

                  {canEdit ? (
                    <div className="signal-form-actions">
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
