import { useEffect, useMemo, useState, type FormEvent } from "react";
import { useTranslation } from "react-i18next";

import { ActiveSwitch } from "../../components/ActiveSwitch";
import type {
  AlarmAggFn,
  AlarmComparator,
  AlarmCompositeExpression,
  AlarmCompositeTerm,
  AlarmLevel,
  AlarmRuleKind,
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
  rule_kind: "simple",
  expression: null,
  comparator: "gt",
  threshold: 0,
  threshold_high: null,
  hysteresis: 0,
  debounce_sec: 0,
  device_code_filter: "",
  is_active: true,
  notify_email: false,
  notify_sms: false,
  notify_telegram: false
};

function makeEmptyTerm(signalKey = ""): AlarmCompositeTerm {
  return {
    signal_key: signalKey,
    device_code: "*",
    comparator: "gt",
    threshold: 0,
    threshold_high: null
  };
}

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
  const { t } = useTranslation();
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
        rule_kind: selectedRule.rule_kind ?? "simple",
        expression: selectedRule.expression ?? null,
        comparator: selectedRule.comparator,
        threshold: selectedRule.threshold,
        threshold_high: selectedRule.threshold_high,
        hysteresis: selectedRule.hysteresis,
        debounce_sec: selectedRule.debounce_sec,
        device_code_filter: selectedRule.device_code_filter ?? "",
        is_active: selectedRule.is_active,
        notify_email: selectedRule.notify_email === true,
        notify_sms: selectedRule.notify_sms === true,
        notify_telegram: selectedRule.notify_telegram === true
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

  const buildPayload = (): Omit<AlarmRuleRow, "id"> => {
    const isComposite = (form.rule_kind ?? "simple") === "composite";
    let expression: AlarmCompositeExpression | null = null;
    if (isComposite && form.expression) {
      expression = {
        logic: form.expression.logic,
        terms: form.expression.terms.map((tm) => {
          const isAgg = tm.kind === "agg";
          return {
            kind: isAgg ? "agg" : "compare",
            signal_key: tm.signal_key,
            device_code: tm.device_code?.trim() || "*",
            comparator: tm.comparator,
            threshold: isBooleanComparator(tm.comparator) ? 0 : Number(tm.threshold),
            threshold_high:
              !isRangeComparator(tm.comparator) ||
              tm.threshold_high === null ||
              tm.threshold_high === undefined
                ? null
                : Number(tm.threshold_high),
            agg_fn: isAgg ? (tm.agg_fn ?? "avg") : null,
            agg_window_sec: isAgg ? Number(tm.agg_window_sec || 60) : 60,
            agg_arg: isAgg ? Number(tm.agg_arg || 0) : 0
          } as AlarmCompositeTerm;
        })
      };
    }
    return {
      ...form,
      description: form.description?.toString().trim() || null,
      device_code_filter: form.device_code_filter?.toString().trim() || null,
      rule_kind: isComposite ? "composite" : "simple",
      expression,
      threshold: isBooleanComparator(form.comparator) ? 0 : Number(form.threshold),
      threshold_high:
        !isRangeComparator(form.comparator) ||
        form.threshold_high === null ||
        form.threshold_high === undefined
          ? null
          : Number(form.threshold_high),
      hysteresis: isBooleanComparator(form.comparator) ? 0 : Number(form.hysteresis),
      debounce_sec: Number(form.debounce_sec)
    };
  };

  const handleSubmit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!canEdit) return;
    if (!form.signal_key) {
      setLocalError(t("engineering.alarmRules.errors.pickSignal"));
      return;
    }
    const isComposite = (form.rule_kind ?? "simple") === "composite";
    if (isComposite) {
      const terms = form.expression?.terms ?? [];
      if (terms.length === 0) {
        setLocalError(t("engineering.alarmRules.errors.addAtLeastOneTerm"));
        return;
      }
      const empty = terms.findIndex((tm) => !tm.signal_key);
      if (empty !== -1) {
        setLocalError(t("engineering.alarmRules.errors.termMissingSignal", { idx: empty + 1 }));
        return;
      }
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
      setLocalError(err instanceof Error ? err.message : t("engineering.alarmRules.statusUpdateFail"));
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
              {t("engineering.alarmRules.back")}
            </button>
            <h3>{mode === "create"
              ? t("engineering.alarmRules.newRuleTitle")
              : t("engineering.alarmRules.editRuleTitle", { name: selectedRule?.name ?? "" })}</h3>
            <div className="rules-v3-form-header-actions">
              <ActiveSwitch
                checked={form.is_active}
                onChange={(v) => setForm({ ...form, is_active: v })}
                title={t("engineering.alarmRules.ruleInactiveTitle")}
              />
            </div>
          </header>

          <form className="rules-v3-form" onSubmit={handleSubmit}>
            <div className="rules-v3-form-grid">
              {/* SOL: Sinyal seçici (yalnızca create modunda aktif) */}
              <aside className="rules-v3-picker">
                <h4 className="rules-v3-section-title">
                  {mode === "create" ? t("engineering.alarmRules.step1Signal") : t("engineering.alarmRules.signalLabel")}
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
                          <span>{src === "all" ? t("engineering.alarmRules.all") : SOURCE_SHORT[src]}</span>
                          <span className="signal-picker-tab-count">
                            {src === "all" ? signals.length : sourceCounts[src] ?? 0}
                          </span>
                        </button>
                      ))}
                    </div>
                    <input
                      type="search"
                      className="signal-picker-search"
                      placeholder={t("engineering.alarmRules.signalSearch")}
                      value={pickerSearch}
                      onChange={(e) => setPickerSearch(e.target.value)}
                    />
                    <ul className="rules-v2-signal-list">
                      {filteredSignalsForPicker.length === 0 ? (
                        <li className="signal-picker-empty">
                          {signals.length === 0 ? t("engineering.alarmRules.noSignals") : t("engineering.alarmRules.noResults")}
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
                    <small className="rule-hint">{t("engineering.alarmRules.signalLocked")}</small>
                  </div>
                )}
              </aside>

              {/* SAĞ: Alarm özellikleri */}
              <div className="rules-v3-properties">
                <h4 className="rules-v3-section-title">
                  {mode === "create" ? t("engineering.alarmRules.step2Properties") : t("engineering.alarmRules.propertiesLabel")}
                </h4>

                {mode === "create" && !pickerSelectedKey ? (
                  <div className="rules-v3-properties-empty">
                    <p className="helper-text">{t("engineering.alarmRules.selectSignalFirst")}</p>
                  </div>
                ) : (
                  <div className="rules-v3-properties-body">
                    <fieldset className="rule-fieldset" disabled={!canEdit}>
                      <legend>{t("engineering.alarmRules.fieldsetDef")}</legend>
                      <label className="rule-field">
                        <span>{t("engineering.alarmRules.ruleName")}</span>
                        <input
                          value={form.name}
                          onChange={(e) => setForm({ ...form, name: e.target.value })}
                          required
                          placeholder={t("engineering.alarmRules.ruleNamePlaceholder")}
                        />
                      </label>
                      <label className="rule-field">
                        <span>{t("engineering.alarmRules.description")}</span>
                        <textarea
                          className="rule-textarea"
                          rows={2}
                          value={form.description ?? ""}
                          onChange={(e) => setForm({ ...form, description: e.target.value })}
                          placeholder={t("engineering.alarmRules.descriptionPlaceholder")}
                        />
                      </label>
                    </fieldset>

                    <fieldset className="rule-fieldset" disabled={!canEdit}>
                      <legend>{t("engineering.alarmRules.fieldsetSeverity")}</legend>
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
                      <legend>{t("engineering.alarmRules.fieldsetCondition")}</legend>
                      {/* Mod toggle: Basit (tek sinyal) | Gelişmiş (AND/OR
                          birden fazla terim). Composite mod ana sinyal
                          tetikleyicisi degismez; ek terimler expression
                          icinde tutulur. */}
                      <div className="rule-mode-toggle">
                        <button
                          type="button"
                          className={`rule-mode-btn ${(form.rule_kind ?? "simple") === "simple" ? "is-active" : ""}`}
                          onClick={() =>
                            setForm({
                              ...form,
                              rule_kind: "simple" as AlarmRuleKind,
                              expression: null
                            })
                          }
                          disabled={!canEdit}
                        >
                          <span className="material-symbols-outlined">looks_one</span>
                          {t("engineering.alarmRules.modeSimple")}
                        </button>
                        <button
                          type="button"
                          className={`rule-mode-btn ${form.rule_kind === "composite" ? "is-active" : ""}`}
                          onClick={() =>
                            setForm({
                              ...form,
                              rule_kind: "composite" as AlarmRuleKind,
                              expression: form.expression ?? {
                                logic: "AND",
                                terms: [
                                  makeEmptyTerm(form.signal_key),
                                  makeEmptyTerm("")
                                ]
                              }
                            })
                          }
                          disabled={!canEdit}
                        >
                          <span className="material-symbols-outlined">account_tree</span>
                          {t("engineering.alarmRules.modeComposite")}
                        </button>
                      </div>

                      {(form.rule_kind ?? "simple") === "simple" ? (
                        <>
                          <label className="rule-field">
                            <span>{t("engineering.alarmRules.comparator")}</span>
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
                                  {isRangeComparator(form.comparator) ? t("engineering.alarmRules.lowerLimit") : t("engineering.alarmRules.thresholdValue")}
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
                                  <span>{t("engineering.alarmRules.upperLimit")}{formSignalUnit ? ` (${formSignalUnit})` : ""}</span>
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
                              {t("engineering.alarmRules.booleanHint", { state: form.comparator === "boolean_true" ? "TRUE" : "FALSE" })}
                            </p>
                          )}
                        </>
                      ) : (
                        /* COMPOSITE MOD — AND/OR ile birden fazla terim */
                        <CompositeEditor
                          expression={form.expression ?? { logic: "AND", terms: [makeEmptyTerm(form.signal_key)] }}
                          signals={signals}
                          canEdit={canEdit}
                          onChange={(next) => setForm({ ...form, expression: next })}
                          t={t}
                        />
                      )}
                    </fieldset>

                    <fieldset className="rule-fieldset" disabled={!canEdit}>
                      <legend>{t("engineering.alarmRules.fieldsetBehavior")}</legend>
                      <div className="rule-grid-2">
                        {!isBooleanComparator(form.comparator) ? (
                          <label className="rule-field">
                            <span>{t("engineering.alarmRules.hysteresis")}{formSignalUnit ? ` (${formSignalUnit})` : ""}</span>
                            <input
                              type="number"
                              step="0.0001"
                              value={form.hysteresis}
                              onChange={(e) =>
                                setForm({ ...form, hysteresis: Number(e.target.value) })
                              }
                            />
                            <small className="rule-hint">
                              {t("engineering.alarmRules.hysteresisHint")}
                            </small>
                          </label>
                        ) : null}
                        <label className="rule-field">
                          <span>{t("engineering.alarmRules.debounce")}</span>
                          <input
                            type="number"
                            min={0}
                            value={form.debounce_sec}
                            onChange={(e) =>
                              setForm({ ...form, debounce_sec: Number(e.target.value) })
                            }
                          />
                          <small className="rule-hint">
                            {t("engineering.alarmRules.debounceHint")}
                          </small>
                        </label>
                      </div>
                    </fieldset>

                    <fieldset className="rule-fieldset" disabled={!canEdit}>
                      <legend>{t("engineering.alarmRules.fieldsetScope")}</legend>
                      <label className="rule-field">
                        <span>{t("engineering.alarmRules.deviceFilter")}</span>
                        <input
                          value={form.device_code_filter ?? ""}
                          onChange={(e) =>
                            setForm({ ...form, device_code_filter: e.target.value })
                          }
                          placeholder={t("engineering.alarmRules.deviceFilterPlaceholder")}
                        />
                        <small className="rule-hint">
                          {t("engineering.alarmRules.deviceFilterHint")}
                        </small>
                      </label>
                    </fieldset>

                    <fieldset className="rule-fieldset" disabled={!canEdit}>
                      <legend>{t("engineering.alarmRules.fieldsetChannels")}</legend>
                      <p className="rule-hint" style={{ margin: "0 0 10px" }}>
                        {t("engineering.alarmRules.channelsHint")}
                      </p>
                      <div className="rule-channel-grid">
                        <label className="rule-channel-option">
                          <input
                            type="checkbox"
                            checked={form.notify_email === true}
                            onChange={(e) =>
                              setForm({ ...form, notify_email: e.target.checked })
                            }
                          />
                          <span className="rule-channel-icon material-symbols-outlined">mail</span>
                          <span className="rule-channel-label">
                            <strong>{t("engineering.alarmRules.channelEmail")}</strong>
                            <small>{t("engineering.alarmRules.channelEmailHint")}</small>
                          </span>
                        </label>
                        <label className="rule-channel-option">
                          <input
                            type="checkbox"
                            checked={form.notify_sms === true}
                            onChange={(e) =>
                              setForm({ ...form, notify_sms: e.target.checked })
                            }
                          />
                          <span className="rule-channel-icon material-symbols-outlined">sms</span>
                          <span className="rule-channel-label">
                            <strong>{t("engineering.alarmRules.channelSms")}</strong>
                            <small>{t("engineering.alarmRules.channelSmsHint")}</small>
                          </span>
                        </label>
                        <label className="rule-channel-option">
                          <input
                            type="checkbox"
                            checked={form.notify_telegram === true}
                            onChange={(e) =>
                              setForm({ ...form, notify_telegram: e.target.checked })
                            }
                          />
                          <span className="rule-channel-icon material-symbols-outlined">send</span>
                          <span className="rule-channel-label">
                            <strong>{t("engineering.alarmRules.channelTelegram")}</strong>
                            <small>{t("engineering.alarmRules.channelTelegramHint")}</small>
                          </span>
                        </label>
                      </div>
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
                {t("engineering.alarmRules.cancel")}
              </button>
              {canEdit ? (
                <button
                  type="submit"
                  className="primary-btn"
                  disabled={saving || (mode === "create" && !pickerSelectedKey)}
                >
                  {saving ? t("engineering.alarmRules.saving") : mode === "create" ? t("engineering.alarmRules.create") : t("engineering.alarmRules.update")}
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
          placeholder={t("common.searchPlaceholder")}
          value={ruleSearch}
          onChange={(e) => setRuleSearch(e.target.value)}
        />
        <div className="rules-filter-group">
          <select
            value={ruleLevelFilter}
            onChange={(e) => setRuleLevelFilter(e.target.value as typeof ruleLevelFilter)}
          >
            <option value="all">{t("alarms.filterAllLevels")}</option>
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
            + {t("engineering.alarmRules.newRule")}
          </button>
        ) : null}
      </div>

      {!canEdit ? (
        <p className="helper-text rules-readonly-hint">
          {t("engineering.alarmRules.readonlyHint")}
        </p>
      ) : null}

      {loading ? <p className="helper-text">{t("common.loading")}</p> : null}

      <div className="rules-v3-list-wrap">
        {filteredRules.length === 0 ? (
          <div className="rules-v3-empty">
            <span className="material-symbols-outlined rules-v3-empty-icon">notifications_off</span>
            <h3>{t("engineering.alarmRules.emptyHeading")}</h3>
            <p className="helper-text">
              {rules.length === 0
                ? canEdit
                  ? t("engineering.alarmRules.emptyHintInstaller")
                  : t("engineering.alarmRules.emptyHintViewer")
                : t("engineering.alarmRules.emptyHintNoMatch")}
            </p>
          </div>
        ) : (
          <ul className="rules-v3-list">
            {filteredRules.map((rule) => {
              const sig = signalByKey.get(rule.signal_key);
              const isComposite = (rule.rule_kind ?? "simple") === "composite";
              const conditionText = isComposite && rule.expression
                ? rule.expression.terms
                    .map((tm) => {
                      const tmSig = signalByKey.get(tm.signal_key);
                      const baseLbl = tmSig?.label ?? tm.signal_key;
                      // agg ise "avg(signal, 60sn)" formatinda goster
                      const lbl =
                        tm.kind === "agg" && tm.agg_fn
                          ? `${tm.agg_fn}(${baseLbl}, ${tm.agg_window_sec ?? 60}s)`
                          : baseLbl;
                      if (isBooleanComparator(tm.comparator)) {
                        return `${lbl} ${tm.comparator === "boolean_true" ? "= TRUE" : "= FALSE"}`;
                      }
                      if (isRangeComparator(tm.comparator)) {
                        return `${lbl} ${COMPARATOR_SYMBOL[tm.comparator]} ${tm.threshold}…${tm.threshold_high ?? "?"}`;
                      }
                      return `${lbl} ${COMPARATOR_SYMBOL[tm.comparator]} ${tm.threshold}`;
                    })
                    .join(rule.expression.logic === "AND" ? "  ∧  " : "  ∨  ")
                : isBooleanComparator(rule.comparator)
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
                      {isComposite ? (
                        <span
                          className="rules-v3-row-kind"
                          title={t("engineering.alarmRules.compositeBadgeHint", {
                            logic: rule.expression?.logic ?? "AND"
                          })}
                        >
                          <span className="material-symbols-outlined">account_tree</span>
                          {rule.expression?.logic ?? "AND"}
                        </span>
                      ) : null}
                      {!rule.is_active ? (
                        <span className="rules-v3-row-flag">{t("common.inactive")}</span>
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
                        {t("common.edit")}
                      </button>
                      <button
                        type="button"
                        className="danger-btn"
                        onClick={() => void handleDelete(rule.id)}
                      >
                        {t("common.delete")}
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


// =============================================================================
// COMPOSITE EDITOR — AND/OR ile birden fazla terim
// =============================================================================

type CompositeEditorProps = {
  expression: AlarmCompositeExpression;
  signals: SignalCatalogRow[];
  canEdit: boolean;
  onChange: (next: AlarmCompositeExpression) => void;
  t: (key: string, opts?: Record<string, unknown>) => string;
};

function CompositeEditor({ expression, signals, canEdit, onChange, t }: CompositeEditorProps) {
  const signalByKey = useMemo(() => {
    const m = new Map<string, SignalCatalogRow>();
    for (const s of signals) m.set(s.key, s);
    return m;
  }, [signals]);

  const updateTerm = (idx: number, patch: Partial<AlarmCompositeTerm>) => {
    const next = expression.terms.map((tm, i) => (i === idx ? { ...tm, ...patch } : tm));
    onChange({ ...expression, terms: next });
  };

  const removeTerm = (idx: number) => {
    const next = expression.terms.filter((_, i) => i !== idx);
    onChange({ ...expression, terms: next });
  };

  const addTerm = () => {
    if (expression.terms.length >= 8) return;
    onChange({ ...expression, terms: [...expression.terms, makeEmptyTerm("")] });
  };

  const setLogic = (logic: "AND" | "OR") => onChange({ ...expression, logic });

  return (
    <div className="rule-composite-editor">
      {/* AND/OR secici */}
      <div className="rule-composite-logic">
        <span className="rule-composite-logic-label">
          {t("engineering.alarmRules.composite.logicLabel")}
        </span>
        <div className="rule-composite-logic-toggle">
          <button
            type="button"
            className={`rule-mode-btn rule-mode-btn--compact ${expression.logic === "AND" ? "is-active" : ""}`}
            onClick={() => setLogic("AND")}
            disabled={!canEdit}
          >
            AND
          </button>
          <button
            type="button"
            className={`rule-mode-btn rule-mode-btn--compact ${expression.logic === "OR" ? "is-active" : ""}`}
            onClick={() => setLogic("OR")}
            disabled={!canEdit}
          >
            OR
          </button>
        </div>
        <span className="rule-hint rule-composite-logic-hint">
          {expression.logic === "AND"
            ? t("engineering.alarmRules.composite.andHint")
            : t("engineering.alarmRules.composite.orHint")}
        </span>
      </div>

      {/* Terim listesi */}
      <ul className="rule-composite-terms">
        {expression.terms.map((tm, idx) => {
          const sig = signalByKey.get(tm.signal_key);
          const isBool = tm.comparator === "boolean_true" || tm.comparator === "boolean_false";
          const isRange = tm.comparator === "between" || tm.comparator === "outside";
          const isAgg = tm.kind === "agg";
          return (
            <li key={idx} className="rule-composite-term">
              <div className="rule-composite-term-head">
                <span className="rule-composite-term-idx">#{idx + 1}</span>
                {idx > 0 ? (
                  <span className="rule-composite-term-joint">{expression.logic}</span>
                ) : null}
                {/* kind toggle: Anlik | Pencere */}
                <div className="rule-composite-kind-toggle">
                  <button
                    type="button"
                    className={`rule-mode-btn rule-mode-btn--compact ${!isAgg ? "is-active" : ""}`}
                    onClick={() =>
                      updateTerm(idx, { kind: "compare", agg_fn: null })
                    }
                    disabled={!canEdit}
                    title={t("engineering.alarmRules.composite.kindCompareHint")}
                  >
                    <span className="material-symbols-outlined">bolt</span>
                    {t("engineering.alarmRules.composite.kindCompare")}
                  </button>
                  <button
                    type="button"
                    className={`rule-mode-btn rule-mode-btn--compact ${isAgg ? "is-active" : ""}`}
                    onClick={() =>
                      updateTerm(idx, {
                        kind: "agg",
                        agg_fn: tm.agg_fn ?? "avg",
                        agg_window_sec: tm.agg_window_sec ?? 60,
                        agg_arg: tm.agg_arg ?? 0
                      })
                    }
                    disabled={!canEdit}
                    title={t("engineering.alarmRules.composite.kindAggHint")}
                  >
                    <span className="material-symbols-outlined">timeline</span>
                    {t("engineering.alarmRules.composite.kindAgg")}
                  </button>
                </div>
                <span className="rule-composite-term-spacer" />
                {expression.terms.length > 1 ? (
                  <button
                    type="button"
                    className="icon-btn icon-btn-danger"
                    title={t("engineering.alarmRules.composite.removeTerm")}
                    aria-label={t("engineering.alarmRules.composite.removeTerm")}
                    onClick={() => removeTerm(idx)}
                    disabled={!canEdit}
                  >
                    <span className="material-symbols-outlined">close</span>
                  </button>
                ) : null}
              </div>
              <div className="rule-composite-term-row">
                <label className="rule-field rule-composite-field-signal">
                  <span>{t("engineering.alarmRules.composite.termSignal")}</span>
                  <select
                    value={tm.signal_key}
                    onChange={(e) => updateTerm(idx, { signal_key: e.target.value })}
                    disabled={!canEdit}
                  >
                    <option value="">
                      {t("engineering.alarmRules.composite.termSignalPlaceholder")}
                    </option>
                    {signals.map((s) => (
                      <option key={s.key} value={s.key}>
                        {SOURCE_SHORT[s.source] ?? s.source} · {s.label} ({s.key})
                      </option>
                    ))}
                  </select>
                </label>
                <label className="rule-field rule-composite-field-device">
                  <span>{t("engineering.alarmRules.composite.termDevice")}</span>
                  <input
                    type="text"
                    value={tm.device_code ?? "*"}
                    onChange={(e) => updateTerm(idx, { device_code: e.target.value })}
                    placeholder="*"
                    disabled={!canEdit}
                    title={t("engineering.alarmRules.composite.termDeviceHint")}
                  />
                </label>
                {isAgg ? (
                  <>
                    <label className="rule-field">
                      <span>{t("engineering.alarmRules.composite.aggFn")}</span>
                      <select
                        value={tm.agg_fn ?? "avg"}
                        onChange={(e) =>
                          updateTerm(idx, { agg_fn: e.target.value as AlarmAggFn })
                        }
                        disabled={!canEdit}
                      >
                        <option value="avg">{t("engineering.alarmRules.composite.aggAvg")}</option>
                        <option value="min">{t("engineering.alarmRules.composite.aggMin")}</option>
                        <option value="max">{t("engineering.alarmRules.composite.aggMax")}</option>
                        <option value="sum">{t("engineering.alarmRules.composite.aggSum")}</option>
                        <option value="count_above">{t("engineering.alarmRules.composite.aggCountAbove")}</option>
                        <option value="count_below">{t("engineering.alarmRules.composite.aggCountBelow")}</option>
                      </select>
                    </label>
                    <label className="rule-field">
                      <span>{t("engineering.alarmRules.composite.windowSec")}</span>
                      <input
                        type="number"
                        min={1}
                        max={86400}
                        value={tm.agg_window_sec ?? 60}
                        onChange={(e) =>
                          updateTerm(idx, { agg_window_sec: Number(e.target.value) })
                        }
                        disabled={!canEdit}
                      />
                    </label>
                    {tm.agg_fn === "count_above" || tm.agg_fn === "count_below" ? (
                      <label className="rule-field">
                        <span>
                          {t("engineering.alarmRules.composite.aggArg")}
                          {sig?.unit ? ` (${sig.unit})` : ""}
                        </span>
                        <input
                          type="number"
                          step="0.0001"
                          value={tm.agg_arg ?? 0}
                          onChange={(e) =>
                            updateTerm(idx, { agg_arg: Number(e.target.value) })
                          }
                          disabled={!canEdit}
                        />
                      </label>
                    ) : null}
                  </>
                ) : null}
                <label className="rule-field rule-composite-field-cmp">
                  <span>{t("engineering.alarmRules.comparator")}</span>
                  <select
                    value={tm.comparator}
                    onChange={(e) => updateTerm(idx, { comparator: e.target.value as AlarmComparator })}
                    disabled={!canEdit}
                  >
                    {COMPARATORS.map((c) => (
                      <option key={c.value} value={c.value}>
                        {c.symbol} {c.label}
                      </option>
                    ))}
                  </select>
                </label>
                {!isBool ? (
                  <label className="rule-field rule-composite-field-th">
                    <span>
                      {isRange
                        ? t("engineering.alarmRules.lowerLimit")
                        : t("engineering.alarmRules.thresholdValue")}
                      {sig?.unit && !isAgg ? ` (${sig.unit})` : ""}
                    </span>
                    <input
                      type="number"
                      step="0.0001"
                      value={tm.threshold}
                      onChange={(e) => updateTerm(idx, { threshold: Number(e.target.value) })}
                      disabled={!canEdit}
                    />
                  </label>
                ) : null}
                {isRange ? (
                  <label className="rule-field rule-composite-field-th">
                    <span>
                      {t("engineering.alarmRules.upperLimit")}
                      {sig?.unit && !isAgg ? ` (${sig.unit})` : ""}
                    </span>
                    <input
                      type="number"
                      step="0.0001"
                      value={tm.threshold_high ?? 0}
                      onChange={(e) =>
                        updateTerm(idx, { threshold_high: Number(e.target.value) })
                      }
                      disabled={!canEdit}
                    />
                  </label>
                ) : null}
              </div>
              {isAgg ? (
                <p className="rule-hint rule-composite-term-hint">
                  {t("engineering.alarmRules.composite.aggHint", {
                    fn: tm.agg_fn ?? "avg",
                    win: tm.agg_window_sec ?? 60
                  })}
                </p>
              ) : null}
            </li>
          );
        })}
      </ul>

      {/* Ekle butonu (max 8 terim) */}
      <div className="rule-composite-actions">
        <button
          type="button"
          className="secondary-btn"
          onClick={addTerm}
          disabled={!canEdit || expression.terms.length >= 8}
        >
          <span className="material-symbols-outlined">add</span>
          {t("engineering.alarmRules.composite.addTerm")}
        </button>
        <span className="rule-hint">
          {t("engineering.alarmRules.composite.maxTermsHint", {
            count: expression.terms.length,
            max: 8
          })}
        </span>
      </div>
    </div>
  );
}
