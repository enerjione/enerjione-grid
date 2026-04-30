import { useEffect, useMemo, useState, type FormEvent } from "react";

import { ActiveSwitch } from "../../components/ActiveSwitch";
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

function isBooleanComparator(c: AlarmComparator): boolean {
  return c === "boolean_true" || c === "boolean_false";
}

function isRangeComparator(c: AlarmComparator): boolean {
  return c === "between" || c === "outside";
}

const SOURCE_LABEL: Record<string, string> = {
  master: "Master",
  sat01: "Satellite 01",
  sat02: "Satellite 02"
};

const SOURCE_SHORT: Record<string, string> = {
  master: "Master",
  sat01: "Sat 01",
  sat02: "Sat 02"
};

type Mode = "list" | "edit-existing" | "create";

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

  const signalByKey = useMemo(() => {
    const map = new Map<string, SignalCatalogRow>();
    for (const sig of signals) map.set(sig.key, sig);
    return map;
  }, [signals]);

  const sourceCounts = useMemo(() => {
    const c: Record<string, number> = { master: 0, sat01: 0, sat02: 0 };
    for (const sig of signals) {
      if (sig.source in c) c[sig.source] += 1;
    }
    return c;
  }, [signals]);

  // Mode: list (sol kural listesi + sağ detay), create (sinyal seç + form), edit-existing (kural seç + form)
  const [mode, setMode] = useState<Mode>("list");
  const [selectedRuleId, setSelectedRuleId] = useState<number | null>(null);
  const [form, setForm] = useState<Omit<AlarmRuleRow, "id">>({ ...EMPTY_FORM });
  const [saving, setSaving] = useState(false);
  const [localError, setLocalError] = useState("");

  // Sol kural listesi filtreleri
  const [ruleSearch, setRuleSearch] = useState("");
  const [ruleLevelFilter, setRuleLevelFilter] = useState<"all" | AlarmLevel>("all");

  // Yeni kural sinyal seçici filtreleri
  const [pickerSource, setPickerSource] = useState<"all" | "master" | "sat01" | "sat02">("all");
  const [pickerSearch, setPickerSearch] = useState("");
  const [pickerSelectedKey, setPickerSelectedKey] = useState<string>("");

  const filteredRules = useMemo(() => {
    const q = ruleSearch.trim().toLowerCase();
    return rules.filter((rule) => {
      if (ruleLevelFilter !== "all" && rule.level !== ruleLevelFilter) return false;
      if (!q) return true;
      const sig = signalByKey.get(rule.signal_key);
      const sigLabel = sig?.label ?? "";
      return (
        rule.name.toLowerCase().includes(q) ||
        rule.signal_key.toLowerCase().includes(q) ||
        sigLabel.toLowerCase().includes(q) ||
        (rule.description ?? "").toLowerCase().includes(q)
      );
    });
  }, [rules, ruleSearch, ruleLevelFilter, signalByKey]);

  const filteredSignalsForPicker = useMemo(() => {
    const q = pickerSearch.trim().toLowerCase();
    return signals.filter((sig) => {
      if (pickerSource !== "all" && sig.source !== pickerSource) return false;
      if (!q) return true;
      return (
        sig.label.toLowerCase().includes(q) ||
        sig.key.toLowerCase().includes(q) ||
        (sig.description ?? "").toLowerCase().includes(q)
      );
    });
  }, [signals, pickerSource, pickerSearch]);

  const selectedRule = useMemo(
    () => rules.find((r) => r.id === selectedRuleId) ?? null,
    [rules, selectedRuleId]
  );

  const formSignal = useMemo(
    () => signalByKey.get(form.signal_key) ?? null,
    [form.signal_key, signalByKey]
  );

  // Bir kural seçilince formu doldur
  useEffect(() => {
    if (mode === "edit-existing" && selectedRule) {
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
      setLocalError("");
    }
  }, [mode, selectedRule]);

  // Yeni kural modunda sinyal seçilince form'u sinyale göre hazırla
  useEffect(() => {
    if (mode !== "create") return;
    if (!pickerSelectedKey) {
      setForm({ ...EMPTY_FORM });
      return;
    }
    const sig = signalByKey.get(pickerSelectedKey);
    setForm({
      ...EMPTY_FORM,
      signal_key: pickerSelectedKey,
      name: sig ? `${sig.label} alarmı` : "",
      comparator: sig?.data_type === "binary" ? "boolean_true" : "gt"
    });
    setLocalError("");
  }, [mode, pickerSelectedKey, signalByKey]);

  const startCreate = () => {
    setMode("create");
    setSelectedRuleId(null);
    setPickerSelectedKey("");
    setPickerSource("all");
    setPickerSearch("");
    setForm({ ...EMPTY_FORM });
    setLocalError("");
  };

  const startEdit = (ruleId: number) => {
    setMode("edit-existing");
    setSelectedRuleId(ruleId);
    setLocalError("");
  };

  const cancel = () => {
    setMode("list");
    setSelectedRuleId(null);
    setPickerSelectedKey("");
    setForm({ ...EMPTY_FORM });
    setLocalError("");
  };

  const buildPayload = (): Omit<AlarmRuleRow, "id"> => ({
    ...form,
    description: form.description?.toString().trim() || null,
    device_code_filter: form.device_code_filter?.toString().trim() || null,
    threshold: isBooleanComparator(form.comparator) ? 0 : Number(form.threshold),
    threshold_high:
      !isRangeComparator(form.comparator) ||
      form.threshold_high === null ||
      form.threshold_high === undefined
        ? null
        : Number(form.threshold_high),
    hysteresis: isBooleanComparator(form.comparator) ? 0 : Number(form.hysteresis),
    debounce_sec: Number(form.debounce_sec)
  });

  const handleSubmit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!canEdit) return;
    if (!form.signal_key) {
      setLocalError("Bir sinyal seçin.");
      return;
    }
    setSaving(true);
    setLocalError("");
    try {
      const payload = buildPayload();
      if (mode === "create") {
        await onCreate(payload);
      } else if (mode === "edit-existing" && selectedRule) {
        const { signal_key: _ignored, ...rest } = payload;
        await onUpdate(selectedRule.id, rest);
      }
      setMode("list");
      setSelectedRuleId(null);
      setPickerSelectedKey("");
    } catch (err) {
      setLocalError(err instanceof Error ? err.message : "Alarm kuralı kaydedilemedi.");
    } finally {
      setSaving(false);
    }
  };

  const handleDelete = async (ruleId: number) => {
    const rule = rules.find((r) => r.id === ruleId);
    if (!rule) return;
    if (!window.confirm(`"${rule.name}" alarm kuralı silinsin mi?`)) return;
    setLocalError("");
    try {
      await onDelete(ruleId);
      if (selectedRuleId === ruleId) {
        setMode("list");
        setSelectedRuleId(null);
      }
    } catch (err) {
      setLocalError(err instanceof Error ? err.message : "Alarm kuralı silinemedi.");
    }
  };

  const handleToggleActive = async (rule: AlarmRuleRow) => {
    setLocalError("");
    try {
      await onUpdate(rule.id, { is_active: !rule.is_active });
    } catch (err) {
      setLocalError(err instanceof Error ? err.message : "Durum güncellenemedi.");
    }
  };

  const formSignalUnit = formSignal?.unit ?? "";
  const isFormMode = mode === "create" || mode === "edit-existing";

  // ========== RENDER: form modu ==========
  if (isFormMode) {
    return (
      <section className="tab-panel alarm-rules-modern alarm-rules-v3">
        <div className="rules-v3-form-shell">
          <header className="rules-v3-form-header">
            <button type="button" className="secondary-btn rules-v3-back" onClick={cancel}>
              ← Geri
            </button>
            <h3>{mode === "create" ? "Yeni Alarm Kuralı" : `Düzenle: ${selectedRule?.name ?? ""}`}</h3>
            <div className="rules-v3-form-header-actions">
              <ActiveSwitch
                checked={form.is_active}
                onChange={(v) => setForm({ ...form, is_active: v })}
                title="Pasif kurallar değerlendirilmez."
              />
            </div>
          </header>

          <form className="rules-v3-form" onSubmit={handleSubmit}>
            <div className="rules-v3-form-grid">
              {/* SOL: Sinyal seçici (yalnızca create modunda aktif) */}
              <aside className="rules-v3-picker">
                <h4 className="rules-v3-section-title">
                  {mode === "create" ? "1. Sinyal Seçin" : "Sinyal"}
                </h4>
                {mode === "create" ? (
                  <>
                    <div className="rules-v2-signals-tabs">
                      {(["all", "master", "sat01", "sat02"] as const).map((src) => (
                        <button
                          key={src}
                          type="button"
                          className={`signal-picker-tab ${pickerSource === src ? "active" : ""}`}
                          onClick={() => setPickerSource(src)}
                        >
                          <span>{src === "all" ? "Tümü" : SOURCE_SHORT[src]}</span>
                          <span className="signal-picker-tab-count">
                            {src === "all" ? signals.length : sourceCounts[src] ?? 0}
                          </span>
                        </button>
                      ))}
                    </div>
                    <input
                      type="search"
                      className="signal-picker-search"
                      placeholder="Sinyal ara..."
                      value={pickerSearch}
                      onChange={(e) => setPickerSearch(e.target.value)}
                    />
                    <ul className="rules-v2-signal-list">
                      {filteredSignalsForPicker.length === 0 ? (
                        <li className="signal-picker-empty">
                          {signals.length === 0 ? "Sinyal yok." : "Aramaya uygun sinyal yok."}
                        </li>
                      ) : (
                        filteredSignalsForPicker.map((sig) => {
                          const isActive = pickerSelectedKey === sig.key;
                          return (
                            <li
                              key={sig.key}
                              className={`rules-v2-signal-item ${isActive ? "active" : ""}`}
                              onClick={() => setPickerSelectedKey(sig.key)}
                            >
                              <div className="rules-v2-signal-item-top">
                                <span className={`badge badge-source badge-source-${sig.source}`}>
                                  {SOURCE_SHORT[sig.source] ?? sig.source}
                                </span>
                                <span className={`badge badge-${sig.data_type}`}>{sig.data_type}</span>
                              </div>
                              <strong>{sig.label}</strong>
                            </li>
                          );
                        })
                      )}
                    </ul>
                  </>
                ) : (
                  <div className="rule-signal-readonly">
                    {formSignal ? (
                      <>
                        <span className={`badge badge-source badge-source-${formSignal.source}`}>
                          {SOURCE_LABEL[formSignal.source] ?? formSignal.source}
                        </span>
                        <span className={`badge badge-${formSignal.data_type}`}>
                          {formSignal.data_type}
                        </span>
                        <strong>{formSignal.label}</strong>
                        <code>{formSignal.key}</code>
                      </>
                    ) : (
                      <span>{form.signal_key}</span>
                    )}
                    <small className="rule-hint">Sinyal sonradan değiştirilemez.</small>
                  </div>
                )}
              </aside>

              {/* SAĞ: Alarm özellikleri */}
              <div className="rules-v3-properties">
                <h4 className="rules-v3-section-title">
                  {mode === "create" ? "2. Alarm Özellikleri" : "Alarm Özellikleri"}
                </h4>

                {mode === "create" && !pickerSelectedKey ? (
                  <div className="rules-v3-properties-empty">
                    <p className="helper-text">Önce soldan bir sinyal seçin.</p>
                  </div>
                ) : (
                  <div className="rules-v3-properties-body">
                    <fieldset className="rule-fieldset" disabled={!canEdit}>
                      <legend>Tanım</legend>
                      <label className="rule-field">
                        <span>Kural Adı</span>
                        <input
                          value={form.name}
                          onChange={(e) => setForm({ ...form, name: e.target.value })}
                          required
                          placeholder="Örn: Akım üst eşiği"
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

                    <fieldset className="rule-fieldset" disabled={!canEdit}>
                      <legend>Önem Derecesi</legend>
                      <div className="rule-level-picker">
                        {LEVELS.map((lv) => (
                          <button
                            key={lv}
                            type="button"
                            className={`rule-level-option level-${lv} ${form.level === lv ? "rule-level-option-active" : ""}`}
                            onClick={() => setForm({ ...form, level: lv })}
                            disabled={!canEdit}
                          >
                            {LEVEL_LABEL[lv]}
                          </button>
                        ))}
                      </div>
                    </fieldset>

                    <fieldset className="rule-fieldset" disabled={!canEdit}>
                      <legend>Koşul</legend>
                      <label className="rule-field">
                        <span>Karşılaştırma</span>
                        <select
                          value={form.comparator}
                          onChange={(e) =>
                            setForm({ ...form, comparator: e.target.value as AlarmComparator })
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
                            <span>
                              {isRangeComparator(form.comparator) ? "Alt sınır" : "Eşik değer"}
                              {formSignalUnit ? ` (${formSignalUnit})` : ""}
                            </span>
                            <input
                              type="number"
                              step="0.0001"
                              value={form.threshold}
                              onChange={(e) =>
                                setForm({ ...form, threshold: Number(e.target.value) })
                              }
                            />
                          </label>
                          {isRangeComparator(form.comparator) ? (
                            <label className="rule-field">
                              <span>Üst sınır{formSignalUnit ? ` (${formSignalUnit})` : ""}</span>
                              <input
                                type="number"
                                step="0.0001"
                                value={form.threshold_high ?? 0}
                                onChange={(e) =>
                                  setForm({ ...form, threshold_high: Number(e.target.value) })
                                }
                              />
                            </label>
                          ) : null}
                        </div>
                      ) : (
                        <p className="rule-hint">
                          Boolean koşul için eşik değeri gerekmez — sinyal{" "}
                          {form.comparator === "boolean_true" ? "TRUE" : "FALSE"} olduğunda alarm
                          tetiklenir.
                        </p>
                      )}
                    </fieldset>

                    <fieldset className="rule-fieldset" disabled={!canEdit}>
                      <legend>Davranış</legend>
                      <div className="rule-grid-2">
                        {!isBooleanComparator(form.comparator) ? (
                          <label className="rule-field">
                            <span>Histerezis{formSignalUnit ? ` (${formSignalUnit})` : ""}</span>
                            <input
                              type="number"
                              step="0.0001"
                              value={form.hysteresis}
                              onChange={(e) =>
                                setForm({ ...form, hysteresis: Number(e.target.value) })
                              }
                            />
                            <small className="rule-hint">
                              Salınım engellemek için tampon — alarm sıfırlama eşiği.
                            </small>
                          </label>
                        ) : null}
                        <label className="rule-field">
                          <span>Debounce (sn)</span>
                          <input
                            type="number"
                            min={0}
                            value={form.debounce_sec}
                            onChange={(e) =>
                              setForm({ ...form, debounce_sec: Number(e.target.value) })
                            }
                          />
                          <small className="rule-hint">
                            Koşul bu süre boyunca sürerse alarm üretilir.
                          </small>
                        </label>
                      </div>
                    </fieldset>

                    <fieldset className="rule-fieldset" disabled={!canEdit}>
                      <legend>Kapsam</legend>
                      <label className="rule-field">
                        <span>Cihaz Kodu Filtresi</span>
                        <input
                          value={form.device_code_filter ?? ""}
                          onChange={(e) =>
                            setForm({ ...form, device_code_filter: e.target.value })
                          }
                          placeholder="örn: GW01-D1, GW01-D2  (boş = tüm cihazlar)"
                        />
                        <small className="rule-hint">
                          Virgülle ayırın. Boş bırakırsanız kural tüm cihazlara uygulanır.
                        </small>
                      </label>
                    </fieldset>

                    {(localError || error) ? (
                      <p className="error-text rule-form-error">{localError || error}</p>
                    ) : null}
                  </div>
                )}
              </div>
            </div>

            <footer className="rules-v3-form-footer">
              <button type="button" className="secondary-btn" onClick={cancel} disabled={saving}>
                İptal
              </button>
              {canEdit ? (
                <button
                  type="submit"
                  className="primary-btn"
                  disabled={saving || (mode === "create" && !pickerSelectedKey)}
                >
                  {saving ? "Kaydediliyor..." : mode === "create" ? "Oluştur" : "Güncelle"}
                </button>
              ) : null}
            </footer>
          </form>
        </div>
      </section>
    );
  }

  // ========== RENDER: list modu (varsayılan) ==========
  return (
    <section className="tab-panel alarm-rules-modern alarm-rules-v3">
      <div className="rules-v3-toolbar">
        <input
          type="search"
          className="rules-search"
          placeholder="Kural ara (ad, sinyal, açıklama)..."
          value={ruleSearch}
          onChange={(e) => setRuleSearch(e.target.value)}
        />
        <div className="rules-filter-group">
          <select
            value={ruleLevelFilter}
            onChange={(e) => setRuleLevelFilter(e.target.value as typeof ruleLevelFilter)}
          >
            <option value="all">Tüm seviyeler</option>
            {LEVELS.map((lv) => (
              <option key={lv} value={lv}>
                {LEVEL_LABEL[lv]}
              </option>
            ))}
          </select>
        </div>
        <span className="rules-count-pill">
          {filteredRules.length} / {rules.length}
        </span>
        {canEdit ? (
          <button type="button" className="primary-btn rules-new-btn" onClick={startCreate}>
            + Yeni Kural
          </button>
        ) : null}
      </div>

      {!canEdit ? (
        <p className="helper-text rules-readonly-hint">
          Alarm kurallarını yalnızca <strong>kurulumcu</strong> rolü düzenleyebilir.
        </p>
      ) : null}

      {loading ? <p className="helper-text">Yükleniyor…</p> : null}

      <div className="rules-v3-list-wrap">
        {filteredRules.length === 0 ? (
          <div className="rules-v3-empty">
            <span className="material-symbols-outlined rules-v3-empty-icon">notifications_off</span>
            <h3>Henüz alarm kuralı yok</h3>
            <p className="helper-text">
              {rules.length === 0
                ? canEdit
                  ? "“+ Yeni Kural” ile ilk kuralı tanımlayın."
                  : "Kurulumcu hesabıyla giriş yapan kişi yeni kural tanımlayabilir."
                : "Filtreye uygun kural bulunamadı."}
            </p>
          </div>
        ) : (
          <ul className="rules-v3-list">
            {filteredRules.map((rule) => {
              const sig = signalByKey.get(rule.signal_key);
              const conditionText = isBooleanComparator(rule.comparator)
                ? rule.comparator === "boolean_true"
                  ? "= TRUE"
                  : "= FALSE"
                : isRangeComparator(rule.comparator)
                  ? `${COMPARATOR_SYMBOL[rule.comparator]} ${rule.threshold} … ${rule.threshold_high ?? "?"}`
                  : `${COMPARATOR_SYMBOL[rule.comparator]} ${rule.threshold}${sig?.unit ? ` ${sig.unit}` : ""}`;
              return (
                <li
                  key={rule.id}
                  className={`rules-v3-row ${rule.is_active ? "" : "rules-v3-row-inactive"}`}
                >
                  <div className="rules-v3-row-main">
                    <div className="rules-v3-row-headline">
                      <span className={`rule-level-badge level-${rule.level}`}>
                        {LEVEL_LABEL[rule.level]}
                      </span>
                      <strong>{rule.name}</strong>
                      {!rule.is_active ? (
                        <span className="rules-v3-row-flag">Pasif</span>
                      ) : null}
                    </div>
                    <div className="rules-v3-row-meta">
                      {sig ? (
                        <span className="rules-v3-row-signal">
                          <span className={`badge badge-source badge-source-${sig.source}`}>
                            {SOURCE_SHORT[sig.source] ?? sig.source}
                          </span>
                          {sig.label}
                        </span>
                      ) : (
                        <span className="rules-v3-row-signal">{rule.signal_key}</span>
                      )}
                      <code className="rules-v3-row-condition">{conditionText}</code>
                      {rule.device_code_filter ? (
                        <span className="rules-v3-row-scope" title="Cihaz filtresi">
                          🔧 {rule.device_code_filter}
                        </span>
                      ) : null}
                    </div>
                  </div>
                  {canEdit ? (
                    <div className="rules-v3-row-actions">
                      <ActiveSwitch
                        checked={rule.is_active}
                        onChange={() => void handleToggleActive(rule)}
                      />
                      <button
                        type="button"
                        className="secondary-btn"
                        onClick={() => startEdit(rule.id)}
                      >
                        Düzenle
                      </button>
                      <button
                        type="button"
                        className="danger-btn"
                        onClick={() => void handleDelete(rule.id)}
                      >
                        Sil
                      </button>
                    </div>
                  ) : null}
                </li>
              );
            })}
          </ul>
        )}
      </div>

      {(localError || error) ? <p className="error-text">{localError || error}</p> : null}
    </section>
  );
}
