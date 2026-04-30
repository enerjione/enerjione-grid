import { useEffect, useMemo, useState, type FormEvent } from "react";
import type {
  AlarmComparator,
  AlarmLevel,
  AlarmRuleRow,
  SignalCatalogRow,
  UserRole
} from "../../shared/types";

type Props = {
  role: UserRole;
  rules: AlarmRuleRow[];
  signals: SignalCatalogRow[];
  loading: boolean;
  error?: string;
  onCreate: (payload: Omit<AlarmRuleRow, "id">) => Promise<void>;
  onUpdate: (ruleId: number, payload: Partial<Omit<AlarmRuleRow, "id" | "signal_key">>) => Promise<void>;
  onDelete: (ruleId: number) => Promise<void>;
};

type Mode = "view" | "edit" | "create";

const LEVELS: AlarmLevel[] = ["info", "warning", "critical"];
const LEVEL_LABEL: Record<AlarmLevel, string> = {
  info: "Bilgi",
  warning: "Uyarı",
  critical: "Kritik"
};

const COMPARATORS: Array<{ value: AlarmComparator; label: string; symbol: string }> = [
  { value: "gt", label: "Büyüktür", symbol: ">" },
  { value: "gte", label: "Büyük-eşit", symbol: "≥" },
  { value: "lt", label: "Küçüktür", symbol: "<" },
  { value: "lte", label: "Küçük-eşit", symbol: "≤" },
  { value: "eq", label: "Eşittir", symbol: "=" },
  { value: "ne", label: "Eşit değil", symbol: "≠" },
  { value: "between", label: "Aralıkta", symbol: "↔" },
  { value: "outside", label: "Aralık dışı", symbol: "⇹" },
  { value: "boolean_true", label: "BOOL = TRUE", symbol: "✓" },
  { value: "boolean_false", label: "BOOL = FALSE", symbol: "✗" }
];

const COMPARATOR_LABEL = COMPARATORS.reduce<Record<AlarmComparator, string>>(
  (acc, item) => {
    acc[item.value] = item.label;
    return acc;
  },
  {} as Record<AlarmComparator, string>
);
const COMPARATOR_SYMBOL = COMPARATORS.reduce<Record<AlarmComparator, string>>(
  (acc, item) => {
    acc[item.value] = item.symbol;
    return acc;
  },
  {} as Record<AlarmComparator, string>
);

const EMPTY_FORM: Omit<AlarmRuleRow, "id"> = {
  signal_key: "",
  name: "",
  description: "",
  level: "warning",
  comparator: "gt",
  threshold: 0,
  threshold_high: null,
  hysteresis: 0,
  debounce_sec: 0,
  device_code_filter: "",
  is_active: true
};

function isBooleanComparator(comparator: AlarmComparator): boolean {
  return comparator === "boolean_true" || comparator === "boolean_false";
}

function isRangeComparator(comparator: AlarmComparator): boolean {
  return comparator === "between" || comparator === "outside";
}

export function AlarmRulesPage({
  role,
  rules,
  signals,
  loading,
  error,
  onCreate,
  onUpdate,
  onDelete
}: Props) {
  const canEdit = role === "installer";
  const alarmableSignals = useMemo(() => signals.filter((signal) => signal.supports_alarm), [signals]);
  const signalByKey = useMemo(() => {
    const map = new Map<string, SignalCatalogRow>();
    for (const signal of signals) map.set(signal.key, signal);
    return map;
  }, [signals]);

  // Sinyal seciciye sunulacak — kaynak sekmesi + arama metnine gore
  const filteredAlarmableSignals = useMemo(() => {
    const q = signalPickerSearch.trim().toLowerCase();
    return alarmableSignals.filter((sig) => {
      if (signalPickerSource !== "all" && sig.source !== signalPickerSource) return false;
      if (!q) return true;
      return (
        sig.label.toLowerCase().includes(q) ||
        sig.key.toLowerCase().includes(q) ||
        (sig.description ?? "").toLowerCase().includes(q)
      );
    });
  }, [alarmableSignals, signalPickerSource, signalPickerSearch]);

  const signalSourceCounts = useMemo(() => {
    const map: Record<string, number> = { master: 0, sat01: 0, sat02: 0 };
    for (const sig of alarmableSignals) {
      if (sig.source in map) map[sig.source] += 1;
    }
    return map;
  }, [alarmableSignals]);

  const [search, setSearch] = useState("");
  const [levelFilter, setLevelFilter] = useState<"all" | AlarmLevel>("all");
  const [statusFilter, setStatusFilter] = useState<"all" | "active" | "inactive">("all");
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [mode, setMode] = useState<Mode>("view");
  const [form, setForm] = useState<Omit<AlarmRuleRow, "id">>({ ...EMPTY_FORM });
  const [localError, setLocalError] = useState("");
  const [saving, setSaving] = useState(false);
  // Sinyal secici icin kaynak (master/sat01/sat02) sekmesi + arama
  const [signalPickerSource, setSignalPickerSource] = useState<"all" | "master" | "sat01" | "sat02">("all");
  const [signalPickerSearch, setSignalPickerSearch] = useState("");

  const filteredRules = useMemo(() => {
    const q = search.trim().toLowerCase();
    return rules.filter((rule) => {
      if (levelFilter !== "all" && rule.level !== levelFilter) return false;
      if (statusFilter === "active" && !rule.is_active) return false;
      if (statusFilter === "inactive" && rule.is_active) return false;
      if (!q) return true;
      const sigLabel = signalByKey.get(rule.signal_key)?.label ?? "";
      return (
        rule.name.toLowerCase().includes(q) ||
        rule.signal_key.toLowerCase().includes(q) ||
        sigLabel.toLowerCase().includes(q) ||
        (rule.description ?? "").toLowerCase().includes(q)
      );
    });
  }, [rules, search, levelFilter, statusFilter, signalByKey]);

  const selectedRule = useMemo(
    () => rules.find((rule) => rule.id === selectedId) ?? null,
    [rules, selectedId]
  );

  // Seçili kural değişince formu doldur
  useEffect(() => {
    if (mode === "create") return;
    if (selectedRule) {
      setForm({
        signal_key: selectedRule.signal_key,
        name: selectedRule.name,
        description: selectedRule.description ?? "",
        level: selectedRule.level,
        comparator: selectedRule.comparator,
        threshold: selectedRule.threshold,
        threshold_high: selectedRule.threshold_high,
        hysteresis: selectedRule.hysteresis,
        debounce_sec: selectedRule.debounce_sec,
        device_code_filter: selectedRule.device_code_filter ?? "",
        is_active: selectedRule.is_active
      });
      setMode("view");
      setLocalError("");
    }
  }, [selectedRule, mode]);

  const startCreate = () => {
    setSelectedId(null);
    setForm({ ...EMPTY_FORM, signal_key: alarmableSignals[0]?.key ?? "" });
    setMode("create");
    setLocalError("");
  };

  const cancelCreate = () => {
    setMode("view");
    setForm({ ...EMPTY_FORM });
    setLocalError("");
  };

  const startEdit = () => {
    if (!selectedRule) return;
    setMode("edit");
    setLocalError("");
  };

  const cancelEdit = () => {
    if (!selectedRule) return;
    setForm({
      signal_key: selectedRule.signal_key,
      name: selectedRule.name,
      description: selectedRule.description ?? "",
      level: selectedRule.level,
      comparator: selectedRule.comparator,
      threshold: selectedRule.threshold,
      threshold_high: selectedRule.threshold_high,
      hysteresis: selectedRule.hysteresis,
      debounce_sec: selectedRule.debounce_sec,
      device_code_filter: selectedRule.device_code_filter ?? "",
      is_active: selectedRule.is_active
    });
    setMode("view");
    setLocalError("");
  };

  const buildPayload = (): Omit<AlarmRuleRow, "id"> => ({
    ...form,
    description: form.description?.toString().trim() || null,
    device_code_filter: form.device_code_filter?.toString().trim() || null,
    threshold: isBooleanComparator(form.comparator) ? 0 : Number(form.threshold),
    threshold_high:
      !isRangeComparator(form.comparator) || form.threshold_high === null || form.threshold_high === undefined
        ? null
        : Number(form.threshold_high),
    hysteresis: isBooleanComparator(form.comparator) ? 0 : Number(form.hysteresis),
    debounce_sec: Number(form.debounce_sec)
  });

  const handleSubmit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setLocalError("");
    if (!form.signal_key) {
      setLocalError("Bir sinyal seçin.");
      return;
    }
    setSaving(true);
    try {
      const payload = buildPayload();
      if (mode === "create") {
        await onCreate(payload);
        setForm({ ...EMPTY_FORM });
        setMode("view");
      } else if (mode === "edit" && selectedRule) {
        const { signal_key: _ignored, ...rest } = payload;
        await onUpdate(selectedRule.id, rest);
        setMode("view");
      }
    } catch (err) {
      setLocalError(err instanceof Error ? err.message : "Alarm kuralı kaydedilemedi.");
    } finally {
      setSaving(false);
    }
  };

  const handleToggle = async (rule: AlarmRuleRow) => {
    setLocalError("");
    try {
      await onUpdate(rule.id, { is_active: !rule.is_active });
    } catch (err) {
      setLocalError(err instanceof Error ? err.message : "Alarm kuralı güncellenemedi.");
    }
  };

  const handleDelete = async () => {
    if (!selectedRule) return;
    if (!window.confirm(`"${selectedRule.name}" alarm kuralı silinsin mi?`)) return;
    setLocalError("");
    try {
      await onDelete(selectedRule.id);
      setSelectedId(null);
    } catch (err) {
      setLocalError(err instanceof Error ? err.message : "Alarm kuralı silinemedi.");
    }
  };

  const isEditing = mode === "edit" || mode === "create";
  const formSignal = signalByKey.get(form.signal_key);
  const formSignalUnit = formSignal?.unit ?? "";

  // İnsan-okunaklı özet cümlesi
  const previewSentence = useMemo(() => {
    if (!form.signal_key) return "Sinyal seçilmedi";
    const sig = signalByKey.get(form.signal_key);
    const label = sig?.label ?? form.signal_key;
    const unit = sig?.unit ? ` ${sig.unit}` : "";
    if (form.comparator === "boolean_true") return `${label} = TRUE → ${LEVEL_LABEL[form.level]}`;
    if (form.comparator === "boolean_false") return `${label} = FALSE → ${LEVEL_LABEL[form.level]}`;
    if (isRangeComparator(form.comparator)) {
      const low = form.threshold;
      const high = form.threshold_high ?? "?";
      const verb = form.comparator === "between" ? "arasında" : "dışında";
      return `${label}, ${low}${unit} – ${high}${unit} ${verb} ise → ${LEVEL_LABEL[form.level]}`;
    }
    return `${label} ${COMPARATOR_SYMBOL[form.comparator]} ${form.threshold}${unit} ise → ${LEVEL_LABEL[form.level]}`;
  }, [form, signalByKey]);

  return (
    <section className="tab-panel alarm-rules-modern">
      <div className="rules-toolbar">
        <input
          className="rules-search"
          type="search"
          placeholder="Kural ara (ad, sinyal, açıklama)..."
          value={search}
          onChange={(e) => setSearch(e.target.value)}
        />
        <div className="rules-filter-group">
          <select value={levelFilter} onChange={(e) => setLevelFilter(e.target.value as typeof levelFilter)}>
            <option value="all">Tüm seviyeler</option>
            {LEVELS.map((lv) => (
              <option key={lv} value={lv}>
                {LEVEL_LABEL[lv]}
              </option>
            ))}
          </select>
          <select value={statusFilter} onChange={(e) => setStatusFilter(e.target.value as typeof statusFilter)}>
            <option value="all">Tüm durumlar</option>
            <option value="active">Sadece aktif</option>
            <option value="inactive">Sadece pasif</option>
          </select>
        </div>
        <span className="rules-count-pill">
          {filteredRules.length} / {rules.length}
        </span>
        {canEdit ? (
          <button
            type="button"
            className="primary-btn rules-new-btn"
            onClick={startCreate}
            disabled={alarmableSignals.length === 0}
          >
            + Yeni Kural
          </button>
        ) : null}
      </div>

      {!canEdit ? (
        <p className="helper-text rules-readonly-hint">
          Alarm kurallarını yalnızca <strong>kurulumcu</strong> rolü düzenleyebilir.
        </p>
      ) : null}
      {alarmableSignals.length === 0 && canEdit ? (
        <p className="helper-text rules-readonly-hint">
          Hiçbir sinyal alarmı desteklemiyor. Sinyaller sekmesinden ilgili sinyallerde "Alarm destekli" seçeneğini açın.
        </p>
      ) : null}

      <div className="rules-layout">
        {/* SOL: Kural listesi */}
        <aside className="rules-list-pane">
          {loading ? <p className="helper-text">Yükleniyor...</p> : null}
          {!loading && filteredRules.length === 0 ? (
            <div className="rules-empty">
              {rules.length === 0 ? "Henüz alarm kuralı tanımlı değil." : "Filtreye uygun kural bulunamadı."}
            </div>
          ) : null}
          <ul className="rules-list">
            {filteredRules.map((rule) => {
              const sig = signalByKey.get(rule.signal_key);
              const isSelected = selectedId === rule.id && mode !== "create";
              return (
                <li
                  key={rule.id}
                  className={`rule-card ${isSelected ? "rule-card-active" : ""} ${rule.is_active ? "" : "rule-card-inactive"}`}
                  onClick={() => {
                    setSelectedId(rule.id);
                    setMode("view");
                  }}
                >
                  <div className="rule-card-top">
                    <span className={`rule-level-badge level-${rule.level}`}>{LEVEL_LABEL[rule.level]}</span>
                    <span className={`rule-status-dot ${rule.is_active ? "rule-status-active" : "rule-status-inactive"}`} title={rule.is_active ? "Aktif" : "Pasif"} />
                  </div>
                  <div className="rule-card-name">{rule.name || <em>İsimsiz kural</em>}</div>
                  <div className="rule-card-signal">{sig ? sig.label : rule.signal_key}</div>
                  <div className="rule-card-condition">
                    <code>
                      {isBooleanComparator(rule.comparator)
                        ? rule.comparator === "boolean_true"
                          ? "= TRUE"
                          : "= FALSE"
                        : isRangeComparator(rule.comparator)
                          ? `${COMPARATOR_SYMBOL[rule.comparator]} ${rule.threshold} … ${rule.threshold_high ?? "?"}`
                          : `${COMPARATOR_SYMBOL[rule.comparator]} ${rule.threshold}${sig?.unit ? ` ${sig.unit}` : ""}`}
                    </code>
                  </div>
                </li>
              );
            })}
          </ul>
        </aside>

        {/* SAĞ: Detay / form */}
        <div className="rules-detail-pane">
          {mode === "create" || selectedRule ? (
            <form className="rule-form" onSubmit={handleSubmit}>
              <header className="rule-form-header">
                <div className="rule-form-titlebar">
                  <h3>
                    {mode === "create"
                      ? "Yeni Alarm Kuralı"
                      : isEditing
                        ? `Düzenle: ${selectedRule?.name}`
                        : selectedRule?.name}
                  </h3>
                  {mode !== "create" && selectedRule ? (
                    <span className={`rule-level-badge level-${selectedRule.level}`}>
                      {LEVEL_LABEL[selectedRule.level]}
                    </span>
                  ) : null}
                  {/* Aktif/Pasif inline switch — fieldset yerine başlık satırında küçük yer kaplar */}
                  <label
                    className={`rule-active-switch ${form.is_active ? "rule-active-switch-on" : ""}`}
                    title={
                      form.is_active
                        ? "Kural aktif — değerlendirilir"
                        : "Kural pasif — değerlendirilmez, tarihçede kalır"
                    }
                  >
                    <input
                      type="checkbox"
                      checked={form.is_active}
                      onChange={(e) => setForm({ ...form, is_active: e.target.checked })}
                      disabled={!canEdit || !isEditing}
                    />
                    <span className="rule-active-switch-track">
                      <span className="rule-active-switch-thumb" />
                    </span>
                    <span className="rule-active-switch-label">
                      {form.is_active ? "Aktif" : "Pasif"}
                    </span>
                  </label>
                </div>
                {canEdit ? (
                  <div className="rule-form-actions">
                    {mode === "view" && selectedRule ? (
                      <>
                        <button
                          type="button"
                          className="secondary-btn"
                          onClick={() => void handleToggle(selectedRule)}
                        >
                          {selectedRule.is_active ? "Pasifleştir" : "Aktifleştir"}
                        </button>
                        <button type="button" className="primary-btn" onClick={startEdit}>
                          Düzenle
                        </button>
                        <button type="button" className="danger-btn" onClick={() => void handleDelete()}>
                          Sil
                        </button>
                      </>
                    ) : null}
                    {mode === "edit" ? (
                      <>
                        <button type="button" className="secondary-btn" onClick={cancelEdit} disabled={saving}>
                          İptal
                        </button>
                        <button type="submit" className="primary-btn" disabled={saving}>
                          {saving ? "Kaydediliyor..." : "Kaydet"}
                        </button>
                      </>
                    ) : null}
                    {mode === "create" ? (
                      <>
                        <button type="button" className="secondary-btn" onClick={cancelCreate} disabled={saving}>
                          İptal
                        </button>
                        <button type="submit" className="primary-btn" disabled={saving}>
                          {saving ? "Oluşturuluyor..." : "Oluştur"}
                        </button>
                      </>
                    ) : null}
                  </div>
                ) : null}
              </header>

              {/* Anlık özet */}
              <div className="rule-preview">
                <span className="rule-preview-label">Önizleme</span>
                <span className="rule-preview-text">{previewSentence}</span>
              </div>

              <div className="rule-form-grid">
                {/* TANIM */}
                <fieldset className="rule-fieldset" disabled={!canEdit || !isEditing}>
                  <legend>Tanım</legend>
                  <div className="rule-grid-2">
                    <div className="rule-field">
                      <span>Sinyal</span>
                      {mode === "create" ? (
                        <SignalPicker
                          source={signalPickerSource}
                          onSourceChange={setSignalPickerSource}
                          search={signalPickerSearch}
                          onSearchChange={setSignalPickerSearch}
                          counts={{
                            all: alarmableSignals.length,
                            master: signalSourceCounts.master ?? 0,
                            sat01: signalSourceCounts.sat01 ?? 0,
                            sat02: signalSourceCounts.sat02 ?? 0
                          }}
                          options={filteredAlarmableSignals}
                          selectedKey={form.signal_key}
                          onSelect={(key) => setForm({ ...form, signal_key: key })}
                        />
                      ) : (
                        <div className="rule-signal-readonly">
                          {(() => {
                            const sig = signalByKey.get(form.signal_key);
                            if (!sig) return <span>{form.signal_key || "—"}</span>;
                            return (
                              <>
                                <span className={`badge badge-source badge-source-${sig.source}`}>
                                  {sig.source === "master"
                                    ? "Master"
                                    : sig.source === "sat01"
                                      ? "Satellite 01"
                                      : sig.source === "sat02"
                                        ? "Satellite 02"
                                        : sig.source}
                                </span>
                                <strong>{sig.label}</strong>
                                <code>{sig.key}</code>
                              </>
                            );
                          })()}
                          {mode === "edit" ? (
                            <small className="rule-hint">Sinyal sonradan değiştirilemez.</small>
                          ) : null}
                        </div>
                      )}
                    </div>
                    <label className="rule-field">
                      <span>Seviye</span>
                      <div className="rule-level-picker">
                        {LEVELS.map((lv) => (
                          <button
                            key={lv}
                            type="button"
                            className={`rule-level-option level-${lv} ${form.level === lv ? "rule-level-option-active" : ""}`}
                            onClick={() => setForm({ ...form, level: lv })}
                            disabled={!canEdit || !isEditing}
                          >
                            {LEVEL_LABEL[lv]}
                          </button>
                        ))}
                      </div>
                    </label>
                  </div>
                  <label className="rule-field">
                    <span>Kural Adı</span>
                    <input
                      value={form.name}
                      onChange={(e) => setForm({ ...form, name: e.target.value })}
                      required
                      placeholder="Örn: Akım sınırı aşıldı"
                    />
                  </label>
                  <label className="rule-field">
                    <span>Açıklama</span>
                    <textarea
                      className="rule-textarea"
                      rows={2}
                      value={form.description ?? ""}
                      onChange={(e) => setForm({ ...form, description: e.target.value })}
                      placeholder="Bu alarmın amacını / aksiyonunu kısaca yazın..."
                    />
                  </label>
                </fieldset>

                {/* KOŞUL */}
                <fieldset className="rule-fieldset" disabled={!canEdit || !isEditing}>
                  <legend>Koşul</legend>
                  <label className="rule-field">
                    <span>Karşılaştırma</span>
                    <select
                      value={form.comparator}
                      onChange={(e) =>
                        setForm({
                          ...form,
                          comparator: e.target.value as AlarmComparator
                        })
                      }
                    >
                      {COMPARATORS.map((item) => (
                        <option key={item.value} value={item.value}>
                          {item.symbol}  {item.label}
                        </option>
                      ))}
                    </select>
                  </label>

                  {!isBooleanComparator(form.comparator) ? (
                    <div className={isRangeComparator(form.comparator) ? "rule-grid-2" : ""}>
                      <label className="rule-field">
                        <span>{isRangeComparator(form.comparator) ? "Alt sınır" : "Eşik değer"}{formSignalUnit ? ` (${formSignalUnit})` : ""}</span>
                        <input
                          type="number"
                          step="0.0001"
                          value={form.threshold}
                          onChange={(e) => setForm({ ...form, threshold: Number(e.target.value) })}
                        />
                      </label>
                      {isRangeComparator(form.comparator) ? (
                        <label className="rule-field">
                          <span>Üst sınır{formSignalUnit ? ` (${formSignalUnit})` : ""}</span>
                          <input
                            type="number"
                            step="0.0001"
                            value={form.threshold_high ?? 0}
                            onChange={(e) => setForm({ ...form, threshold_high: Number(e.target.value) })}
                          />
                        </label>
                      ) : null}
                    </div>
                  ) : (
                    <p className="rule-hint">
                      Boolean koşul için eşik değeri gerekmez — sinyal {form.comparator === "boolean_true" ? "TRUE" : "FALSE"} olduğunda alarm tetiklenir.
                    </p>
                  )}
                </fieldset>

                {/* DAVRANIŞ */}
                <fieldset className="rule-fieldset" disabled={!canEdit || !isEditing}>
                  <legend>Davranış</legend>
                  <div className="rule-grid-2">
                    {!isBooleanComparator(form.comparator) ? (
                      <label className="rule-field">
                        <span>Histerezis{formSignalUnit ? ` (${formSignalUnit})` : ""}</span>
                        <input
                          type="number"
                          step="0.0001"
                          value={form.hysteresis}
                          onChange={(e) => setForm({ ...form, hysteresis: Number(e.target.value) })}
                        />
                        <small className="rule-hint">Salınım engellemek için tampon — alarm sıfırlama eşiği.</small>
                      </label>
                    ) : null}
                    <label className="rule-field">
                      <span>Debounce (sn)</span>
                      <input
                        type="number"
                        min={0}
                        value={form.debounce_sec}
                        onChange={(e) => setForm({ ...form, debounce_sec: Number(e.target.value) })}
                      />
                      <small className="rule-hint">Koşul bu süre boyunca sürerse alarm üretilir.</small>
                    </label>
                  </div>
                </fieldset>

                {/* KAPSAM */}
                <fieldset className="rule-fieldset" disabled={!canEdit || !isEditing}>
                  <legend>Kapsam</legend>
                  <label className="rule-field">
                    <span>Cihaz Kodu Filtresi</span>
                    <input
                      value={form.device_code_filter ?? ""}
                      onChange={(e) => setForm({ ...form, device_code_filter: e.target.value })}
                      placeholder="örn: GW01-D1, GW01-D2  (boş = tüm cihazlar)"
                    />
                    <small className="rule-hint">Virgülle ayırın. Boş bırakırsanız kural tüm cihazlara uygulanır.</small>
                  </label>
                </fieldset>

              </div>

              {(localError || error) ? <p className="error-text rule-form-error">{localError || error}</p> : null}
            </form>
          ) : (
            <div className="rules-detail-empty">
              <div className="rules-detail-empty-icon">⚙️</div>
              <h3>Bir kural seçin</h3>
              <p className="helper-text">
                Soldaki listeden bir alarm kuralı seçerek görüntüleyebilir veya düzenleyebilirsiniz.
                {canEdit && alarmableSignals.length > 0 ? " Yeni bir kural eklemek için sağ üstteki + butonunu kullanın." : ""}
              </p>
            </div>
          )}
        </div>
      </div>
    </section>
  );
}

// =====================================================================
// SignalPicker — alarm kuralında sinyal seçimi için kaynak sekmeleri +
// arama + scrollable sinyal listesi.
// =====================================================================
type SignalPickerProps = {
  source: "all" | "master" | "sat01" | "sat02";
  onSourceChange: (s: "all" | "master" | "sat01" | "sat02") => void;
  search: string;
  onSearchChange: (s: string) => void;
  counts: { all: number; master: number; sat01: number; sat02: number };
  options: SignalCatalogRow[];
  selectedKey: string;
  onSelect: (key: string) => void;
};

function SignalPicker({
  source,
  onSourceChange,
  search,
  onSearchChange,
  counts,
  options,
  selectedKey,
  onSelect
}: SignalPickerProps) {
  const tabs: Array<{ key: "all" | "master" | "sat01" | "sat02"; label: string; count: number }> = [
    { key: "all", label: "Tümü", count: counts.all },
    { key: "master", label: "Master", count: counts.master },
    { key: "sat01", label: "Satellite 01", count: counts.sat01 },
    { key: "sat02", label: "Satellite 02", count: counts.sat02 }
  ];
  return (
    <div className="signal-picker">
      <div className="signal-picker-tabs">
        {tabs.map((tab) => (
          <button
            key={tab.key}
            type="button"
            className={`signal-picker-tab ${source === tab.key ? "active" : ""}`}
            onClick={() => onSourceChange(tab.key)}
          >
            <span>{tab.label}</span>
            <span className="signal-picker-tab-count">{tab.count}</span>
          </button>
        ))}
      </div>
      <input
        type="search"
        className="signal-picker-search"
        placeholder="Sinyal ara (ad, anahtar, açıklama)..."
        value={search}
        onChange={(e) => onSearchChange(e.target.value)}
      />
      <div className="signal-picker-list" role="listbox">
        {options.length === 0 ? (
          <div className="signal-picker-empty">
            {counts.all === 0
              ? "Hiçbir sinyal alarmı desteklemiyor."
              : "Aramaya uygun sinyal yok."}
          </div>
        ) : (
          options.map((sig) => {
            const isActive = sig.key === selectedKey;
            return (
              <button
                key={sig.key}
                type="button"
                role="option"
                aria-selected={isActive}
                className={`signal-picker-item ${isActive ? "active" : ""}`}
                onClick={() => onSelect(sig.key)}
              >
                <span className={`badge badge-source badge-source-${sig.source}`}>
                  {sig.source === "master"
                    ? "Master"
                    : sig.source === "sat01"
                      ? "Sat 01"
                      : sig.source === "sat02"
                        ? "Sat 02"
                        : sig.source}
                </span>
                <div className="signal-picker-item-text">
                  <strong>{sig.label}</strong>
                  <code>{sig.key}</code>
                </div>
                <span className={`badge badge-${sig.data_type}`}>
                  {sig.data_type}
                </span>
              </button>
            );
          })
        )}
      </div>
    </div>
  );
}
