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
  const alarmableSignals = useMemo(() => signals.filter((s) => s.supports_alarm), [signals]);

  const ruleBySignalKey = useMemo(() => {
    const map = new Map<string, AlarmRuleRow>();
    for (const rule of rules) map.set(rule.signal_key, rule);
    return map;
  }, [rules]);

  const sourceCounts = useMemo(() => {
    const c: Record<string, number> = { master: 0, sat01: 0, sat02: 0 };
    for (const sig of alarmableSignals) {
      if (sig.source in c) c[sig.source] += 1;
    }
    return c;
  }, [alarmableSignals]);

  const [sourceFilter, setSourceFilter] = useState<"all" | "master" | "sat01" | "sat02">("all");
  const [search, setSearch] = useState("");
  const [selectedKey, setSelectedKey] = useState<string>("");
  const [form, setForm] = useState<Omit<AlarmRuleRow, "id">>({ ...EMPTY_FORM });
  const [saving, setSaving] = useState(false);
  const [localError, setLocalError] = useState("");

  const filteredSignals = useMemo(() => {
    const q = search.trim().toLowerCase();
    return alarmableSignals.filter((sig) => {
      if (sourceFilter !== "all" && sig.source !== sourceFilter) return false;
      if (!q) return true;
      return (
        sig.label.toLowerCase().includes(q) ||
        sig.key.toLowerCase().includes(q) ||
        (sig.description ?? "").toLowerCase().includes(q)
      );
    });
  }, [alarmableSignals, sourceFilter, search]);

  const selectedSignal = useMemo(
    () => signals.find((s) => s.key === selectedKey) ?? null,
    [signals, selectedKey]
  );

  const existingRule = useMemo(
    () => (selectedKey ? ruleBySignalKey.get(selectedKey) ?? null : null),
    [selectedKey, ruleBySignalKey]
  );

  // Cihaz seçilince var olan kuralı yükle, yoksa varsayılan boş form
  useEffect(() => {
    if (!selectedKey) {
      setForm({ ...EMPTY_FORM });
      setLocalError("");
      return;
    }
    if (existingRule) {
      setForm({
        signal_key: existingRule.signal_key,
        name: existingRule.name,
        description: existingRule.description ?? "",
        level: existingRule.level,
        comparator: existingRule.comparator,
        threshold: existingRule.threshold,
        threshold_high: existingRule.threshold_high,
        hysteresis: existingRule.hysteresis,
        debounce_sec: existingRule.debounce_sec,
        device_code_filter: existingRule.device_code_filter ?? "",
        is_active: existingRule.is_active
      });
    } else {
      const sig = signals.find((s) => s.key === selectedKey);
      setForm({
        ...EMPTY_FORM,
        signal_key: selectedKey,
        name: sig ? `${sig.label} alarmı` : "",
        comparator: sig?.data_type === "binary" ? "boolean_true" : "gt"
      });
    }
    setLocalError("");
  }, [selectedKey, existingRule, signals]);

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
    if (!selectedKey || !canEdit) return;
    setSaving(true);
    setLocalError("");
    try {
      const payload = buildPayload();
      if (existingRule) {
        const { signal_key: _ignored, ...rest } = payload;
        await onUpdate(existingRule.id, rest);
      } else {
        await onCreate(payload);
      }
      // Başarı sonrası: App.tsx success toast'u tetikleniyor; burada
      // ek bilgilendirme için form'da pasif feedback verelim.
    } catch (err) {
      setLocalError(err instanceof Error ? err.message : "Alarm kuralı kaydedilemedi.");
    } finally {
      setSaving(false);
    }
  };

  const handleDelete = async () => {
    if (!existingRule) return;
    if (!window.confirm(`"${existingRule.name}" alarm kuralı silinsin mi?`)) return;
    setLocalError("");
    try {
      await onDelete(existingRule.id);
    } catch (err) {
      setLocalError(err instanceof Error ? err.message : "Alarm kuralı silinemedi.");
    }
  };

  const formSignalUnit = selectedSignal?.unit ?? "";

  return (
    <section className="tab-panel alarm-rules-modern alarm-rules-v2">
      {!canEdit ? (
        <p className="helper-text rules-readonly-hint">
          Alarm kurallarını yalnızca <strong>kurulumcu</strong> rolü düzenleyebilir.
        </p>
      ) : null}
      {alarmableSignals.length === 0 && canEdit ? (
        <p className="helper-text rules-readonly-hint">
          Hiçbir sinyal alarmı desteklemiyor. Sinyaller sekmesinden ilgili sinyallerde
          "Alarm destekli" seçeneğini açın.
        </p>
      ) : null}

      <div className="rules-v2-layout">
        {/* SOL: Sinyal listesi */}
        <aside className="rules-v2-signals">
          <div className="rules-v2-signals-tabs">
            {(["all", "master", "sat01", "sat02"] as const).map((src) => (
              <button
                key={src}
                type="button"
                className={`signal-picker-tab ${sourceFilter === src ? "active" : ""}`}
                onClick={() => setSourceFilter(src)}
              >
                <span>{src === "all" ? "Tümü" : SOURCE_SHORT[src]}</span>
                <span className="signal-picker-tab-count">
                  {src === "all" ? alarmableSignals.length : sourceCounts[src] ?? 0}
                </span>
              </button>
            ))}
          </div>
          <input
            type="search"
            className="signal-picker-search"
            placeholder="Sinyal ara..."
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />
          <ul className="rules-v2-signal-list">
            {filteredSignals.length === 0 ? (
              <li className="signal-picker-empty">
                {alarmableSignals.length === 0
                  ? "Hiçbir sinyal alarmı desteklemiyor."
                  : "Aramaya uygun sinyal yok."}
              </li>
            ) : (
              filteredSignals.map((sig) => {
                const isActive = selectedKey === sig.key;
                const hasRule = ruleBySignalKey.has(sig.key);
                return (
                  <li
                    key={sig.key}
                    className={`rules-v2-signal-item ${isActive ? "active" : ""}`}
                    onClick={() => setSelectedKey(sig.key)}
                  >
                    <div className="rules-v2-signal-item-top">
                      <span className={`badge badge-source badge-source-${sig.source}`}>
                        {SOURCE_SHORT[sig.source] ?? sig.source}
                      </span>
                      <span className={`badge badge-${sig.data_type}`}>{sig.data_type}</span>
                      {hasRule ? (
                        <span
                          className={`rules-v2-rule-pill rules-v2-rule-pill--${ruleBySignalKey.get(sig.key)?.level}`}
                          title="Bu sinyal için kural tanımlı"
                        >
                          ●
                        </span>
                      ) : null}
                    </div>
                    <strong>{sig.label}</strong>
                  </li>
                );
              })
            )}
          </ul>
        </aside>

        {/* SAĞ: Sinyale göre detay/kural formu */}
        <div className="rules-v2-detail">
          {!selectedSignal ? (
            <div className="rules-detail-empty">
              <div className="rules-detail-empty-icon">⚙️</div>
              <h3>Bir sinyal seçin</h3>
              <p className="helper-text">
                Soldaki listeden bir sinyal seçtiğinizde mevcut kural gösterilir veya
                yeni bir kural tanımlayabilirsiniz.
              </p>
            </div>
          ) : (
            <form className="rules-v2-form" onSubmit={handleSubmit}>
              <header className="rules-v2-detail-header">
                <div className="rules-v2-detail-title">
                  <div className="rules-v2-detail-title-top">
                    <span className={`badge badge-source badge-source-${selectedSignal.source}`}>
                      {SOURCE_LABEL[selectedSignal.source] ?? selectedSignal.source}
                    </span>
                    <span className={`badge badge-${selectedSignal.data_type}`}>
                      {selectedSignal.data_type}
                    </span>
                    {existingRule ? (
                      <span className={`rule-level-badge level-${existingRule.level}`}>
                        Mevcut: {LEVEL_LABEL[existingRule.level]}
                      </span>
                    ) : (
                      <span className="rules-v2-new-flag">Yeni kural</span>
                    )}
                  </div>
                  <h2>{selectedSignal.label}</h2>
                  <code className="rules-v2-detail-key">{selectedSignal.key}</code>
                  {selectedSignal.description ? (
                    <p className="rules-v2-detail-desc">{selectedSignal.description}</p>
                  ) : null}
                </div>
                {canEdit ? (
                  <div className="rules-v2-actions">
                    <ActiveSwitch
                      checked={form.is_active}
                      onChange={(v) => setForm({ ...form, is_active: v })}
                      title="Pasif kurallar değerlendirilmez."
                    />
                    {existingRule ? (
                      <button
                        type="button"
                        className="danger-btn"
                        onClick={() => void handleDelete()}
                      >
                        Sil
                      </button>
                    ) : null}
                    <button type="submit" className="primary-btn" disabled={saving}>
                      {saving ? "Kaydediliyor..." : existingRule ? "Güncelle" : "Oluştur"}
                    </button>
                  </div>
                ) : null}
              </header>

              <div className="rules-v2-form-body">
                {/* TANIM */}
                <fieldset className="rule-fieldset" disabled={!canEdit}>
                  <legend>Tanım</legend>
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

                {/* ÖNEM DERECESİ */}
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

                {/* KOŞUL */}
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

                {/* DAVRANIŞ */}
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

                {/* KAPSAM */}
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
            </form>
          )}
        </div>
      </div>

      {loading ? <p className="helper-text">Yükleniyor...</p> : null}
    </section>
  );
}
