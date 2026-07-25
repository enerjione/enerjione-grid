import { useEffect, useMemo, useState, type FormEvent } from "react";
import { asyncConfirm } from "../../components/ConfirmDialog";
import { createPortal } from "react-dom";
import { useTranslation } from "react-i18next";

import { ActiveSwitch } from "../../components/ActiveSwitch";
import type {
  AlarmAggFn,
  AlarmComparator,
  AlarmCompositeExpression,
  AlarmCompositeTerm,
  AlarmFormulaVar,
  AlarmLevel,
  AlarmRuleKind,
  AlarmRuleRow,
  DeviceRow,
  SignalCatalogRow,
  UserRole
} from "../../shared/types";

type Props = {
  role: UserRole;
  rules: AlarmRuleRow[];
  signals: SignalCatalogRow[];
  /** Kapsam alanindaki "Cihazlari Sec" modali icin tum cihazlar.
   *  Bos gecilirse modal acilamaz, kapsam alani yine de manuel kod girmeye
   *  imkan verir (geri uyumluluk). */
  devices?: DeviceRow[];
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
  notify_telegram: false,
  notify_whatsapp_web: false,
  // Varsayilan TRUE: yeni kural ariza uretir (harita kirmizi + Hat Arizasi).
  produces_fault: true
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
type RuleWizardStep = "signal" | "definition" | "condition" | "behavior" | "summary";

const WIZARD_STEPS: Array<{ key: RuleWizardStep; title: string; subtitle: string }> = [
  { key: "signal", title: "Sinyal", subtitle: "Alarm kaynağı" },
  { key: "definition", title: "Tanım", subtitle: "Ad ve seviye" },
  { key: "condition", title: "Koşul", subtitle: "Tetikleme" },
  { key: "behavior", title: "Davranış", subtitle: "Aksiyonlar" },
  { key: "summary", title: "Özet", subtitle: "Kontrol" }
];

export function AlarmRulesPage({
  role,
  rules,
  signals,
  devices,
  loading,
  error,
  onCreate,
  onUpdate,
  onDelete
}: Props) {
  const { t } = useTranslation();
  const canEdit = role === "installer";
  const [devicePickerOpen, setDevicePickerOpen] = useState(false);

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
  const [wizardStep, setWizardStep] = useState<RuleWizardStep>("signal");
  const [saving, setSaving] = useState(false);
  const [localError, setLocalError] = useState("");

  // Sol kural listesi filtreleri
  const [ruleSearch, setRuleSearch] = useState("");
  const [ruleLevelFilter, setRuleLevelFilter] = useState<"all" | AlarmLevel>("all");

  // Yeni kural sinyal seçici filtreleri
  const [pickerSource, setPickerSource] = useState<"master" | "sat01" | "sat02">("master");
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
      if (sig.source !== pickerSource) return false;
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
        notify_telegram: selectedRule.notify_telegram === true,
        notify_whatsapp_web: selectedRule.notify_whatsapp_web === true,
        // undefined (eski kural) => true (geriye uyum).
        produces_fault: selectedRule.produces_fault !== false
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
    setPickerSource("master");
    setPickerSearch("");
    setWizardStep("signal");
    setForm({ ...EMPTY_FORM });
    setLocalError("");
  };

  const startEdit = (ruleId: number) => {
    setMode("edit-existing");
    setSelectedRuleId(ruleId);
    setWizardStep("definition");
    setLocalError("");
  };

  const cancel = () => {
    setMode("list");
    setSelectedRuleId(null);
    setPickerSelectedKey("");
    setWizardStep("signal");
    setForm({ ...EMPTY_FORM });
    setLocalError("");
  };

  const isStepDone = (step: RuleWizardStep): boolean => {
    if (step === "signal") return mode === "edit-existing" || Boolean(form.signal_key);
    if (step === "definition") return (mode === "edit-existing" || Boolean(form.signal_key)) && form.name.trim().length > 0;
    if (step === "condition") return isStepDone("definition") && (!isRangeComparator(form.comparator) || form.threshold_high !== null);
    if (step === "behavior") return isStepDone("condition");
    return isStepDone("behavior");
  };

  const visibleWizardSteps = mode === "edit-existing" ? WIZARD_STEPS.filter((step) => step.key !== "signal") : WIZARD_STEPS;
  const wizardIndex = visibleWizardSteps.findIndex((step) => step.key === wizardStep);
  const isFirstWizardStep = wizardIndex <= 0;
  const isSummaryStep = wizardStep === "summary";

  const canOpenWizardStep = (step: RuleWizardStep): boolean => {
    const idx = visibleWizardSteps.findIndex((item) => item.key === step);
    if (idx === -1) return false;
    if (idx <= wizardIndex) return true;
    return visibleWizardSteps.slice(0, idx).every((item) => isStepDone(item.key));
  };

  const goToWizardStep = (step: RuleWizardStep) => {
    if (!canOpenWizardStep(step)) return;
    setWizardStep(step);
    setLocalError("");
  };

  const goNextWizardStep = () => {
    if (!isStepDone("signal")) {
      setLocalError("Bir sinyal seçin.");
      return;
    }
    if (wizardStep !== "signal" && !isStepDone("definition")) {
      setLocalError("Kural adı girin.");
      return;
    }
    const next = visibleWizardSteps[wizardIndex + 1];
    if (next) {
      setWizardStep(next.key);
      setLocalError("");
    }
  };

  const goPrevWizardStep = () => {
    const prev = visibleWizardSteps[wizardIndex - 1];
    if (prev) {
      setWizardStep(prev.key);
      setLocalError("");
    }
  };

  const buildPayload = (): Omit<AlarmRuleRow, "id"> => {
    const isComposite = (form.rule_kind ?? "simple") === "composite";
    let expression: AlarmCompositeExpression | null = null;
    if (isComposite && form.expression) {
      expression = {
        logic: form.expression.logic,
        terms: form.expression.terms.map((tm) => {
          const isAgg = tm.kind === "agg";
          const isFormula = tm.kind === "formula";
          return {
            kind: isFormula ? "formula" : isAgg ? "agg" : "compare",
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
            agg_arg: isAgg ? Number(tm.agg_arg || 0) : 0,
            formula_expr: isFormula ? (tm.formula_expr ?? "").trim() : null,
            formula_vars: isFormula
              ? (tm.formula_vars ?? []).map((fv) => ({
                  name: fv.name.trim(),
                  signal_key: fv.signal_key,
                  device_code: fv.device_code?.trim() || "*"
                }))
              : []
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
    if (!isSummaryStep) {
      goNextWizardStep();
      return;
    }
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
      // Formula terimleri: ifade + en az 1 degisken + benzersiz isimler
      for (let i = 0; i < terms.length; i++) {
        const tm = terms[i];
        if (tm.kind !== "formula") continue;
        const expr = (tm.formula_expr ?? "").trim();
        if (!expr) {
          setLocalError(t("engineering.alarmRules.errors.formulaEmpty", { idx: i + 1 }));
          return;
        }
        const vars = tm.formula_vars ?? [];
        if (vars.length === 0) {
          setLocalError(t("engineering.alarmRules.errors.formulaNoVars", { idx: i + 1 }));
          return;
        }
        const names = vars.map((v) => v.name.trim());
        if (names.some((n) => !n || !/^[A-Za-z_][A-Za-z0-9_]*$/.test(n))) {
          setLocalError(t("engineering.alarmRules.errors.formulaBadName", { idx: i + 1 }));
          return;
        }
        if (new Set(names).size !== names.length) {
          setLocalError(t("engineering.alarmRules.errors.formulaDupName", { idx: i + 1 }));
          return;
        }
        if (vars.some((v) => !v.signal_key)) {
          setLocalError(t("engineering.alarmRules.errors.formulaVarNoSignal", { idx: i + 1 }));
          return;
        }
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
    if (!await asyncConfirm(`"${rule.name}" alarm kuralı silinsin mi?`)) return;
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

  const duplicateRule = (rule: AlarmRuleRow) => {
    const { id: _ignored, ...copy } = rule;
    setForm({
      ...EMPTY_FORM,
      ...copy,
      name: `${rule.name} kopyası`,
      expression: rule.expression ?? null
    });
    setPickerSelectedKey(rule.signal_key);
    setSelectedRuleId(null);
    setMode("create");
    setWizardStep("definition");
    setLocalError("");
  };

  const formSignalUnit = formSignal?.unit ?? "";
  const formConditionText = isBooleanComparator(form.comparator)
    ? form.comparator === "boolean_true"
      ? "= TRUE"
      : "= FALSE"
    : isRangeComparator(form.comparator)
      ? `${COMPARATOR_SYMBOL[form.comparator]} ${form.threshold} … ${form.threshold_high ?? "?"}`
      : `${COMPARATOR_SYMBOL[form.comparator]} ${form.threshold}${formSignalUnit ? ` ${formSignalUnit}` : ""}`;
  const formScopeText = form.device_code_filter?.trim() ? form.device_code_filter : "Tüm cihazlar";
  const formNotifyText = [
    form.notify_sms ? "SMS" : null,
    form.notify_email ? "E-Posta" : null,
    form.notify_telegram ? "Telegram" : null,
    form.notify_whatsapp_web ? "WhatsApp" : null
  ].filter(Boolean).join(", ") || "—";
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
            <div className="rules-v3-wizard-steps" role="tablist" aria-label="Alarm kuralı adımları">
              {visibleWizardSteps.map((step, idx) => {
                const isActive = wizardStep === step.key;
                const isDone = isStepDone(step.key) && !isActive;
                const isLocked = !canOpenWizardStep(step.key);
                return (
                  <button
                    key={step.key}
                    type="button"
                    className={`rules-v3-wizard-step ${isActive ? "rules-v3-wizard-step--active" : ""} ${isDone ? "rules-v3-wizard-step--done" : ""}`}
                    onClick={() => goToWizardStep(step.key)}
                    disabled={isLocked}
                    aria-current={isActive ? "step" : undefined}
                  >
                    <span className="rules-v3-wizard-step-index">{isDone ? "✓" : idx + 1}</span>
                    <span className="rules-v3-wizard-step-text">
                      <strong>{step.title}</strong>
                      <small>{step.subtitle}</small>
                    </span>
                  </button>
                );
              })}
            </div>
            <div className="rules-v3-form-grid rules-v3-form-grid--wizard">
              {/* SOL: Sinyal seçici (yalnızca create modunda aktif) */}
              <aside className="rules-v3-picker">
                <h4 className="rules-v3-section-title">
                  {mode === "create" ? t("engineering.alarmRules.step1Signal") : t("engineering.alarmRules.signalLabel")}
                </h4>
                {mode === "create" ? (
                  <>
                    <div className="rules-v2-signals-tabs">
                      {(["master", "sat01", "sat02"] as const).map((src) => (
                        <button
                          key={src}
                          type="button"
                          className={`signal-picker-tab ${pickerSource === src ? "active" : ""}`}
                          onClick={() => setPickerSource(src)}
                        >
                          <span>{SOURCE_SHORT[src]}</span>
                          <span className="signal-picker-tab-count">
                            {sourceCounts[src] ?? 0}
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
                    {wizardStep === "definition" ? (
                      <>
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

                      </>
                    ) : null}

                    {wizardStep === "condition" ? (
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
                    ) : null}

                    {wizardStep === "behavior" ? (
                      <>
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
                      <ScopeDevicePicker
                        deviceCodeFilter={form.device_code_filter ?? ""}
                        onChange={(next) =>
                          setForm({ ...form, device_code_filter: next })
                        }
                        canEdit={canEdit}
                        onOpenPicker={() => setDevicePickerOpen(true)}
                        devicesAvailable={!!devices && devices.length > 0}
                        t={t}
                      />
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
                        <label className="rule-channel-option">
                          <input
                            type="checkbox"
                            checked={form.notify_whatsapp_web === true}
                            onChange={(e) =>
                              setForm({ ...form, notify_whatsapp_web: e.target.checked })
                            }
                          />
                          <span className="rule-channel-icon material-symbols-outlined">forum</span>
                          <span className="rule-channel-label">
                            <strong>{t("engineering.alarmRules.channelWhatsapp")}</strong>
                            <small>{t("engineering.alarmRules.channelWhatsappHint")}</small>
                          </span>
                        </label>
                      </div>
                    </fieldset>

                        <fieldset className="rule-fieldset" disabled={!canEdit}>
                          <legend>{t("engineering.alarmRules.fieldsetFault")}</legend>
                          <div className="rule-channel-grid">
                            <label className="rule-channel-option">
                              <input
                                type="checkbox"
                                checked={form.produces_fault !== false}
                                onChange={(e) =>
                                  setForm({ ...form, produces_fault: e.target.checked })
                                }
                              />
                              <span className="rule-channel-icon material-symbols-outlined">e911_emergency</span>
                              <span className="rule-channel-label">
                                <strong>{t("engineering.alarmRules.producesFault")}</strong>
                                <small>{t("engineering.alarmRules.producesFaultHint")}</small>
                              </span>
                            </label>
                          </div>
                        </fieldset>
                      </>
                    ) : null}

                    {wizardStep === "summary" ? (
                      <div className="rules-v3-summary-grid">
                        <section className="rules-v3-summary-card rules-v3-summary-card--hero">
                          <span className={`rule-level-badge level-${form.level}`}>{LEVEL_LABEL[form.level]}</span>
                          <h4>{form.name || "Adsız alarm kuralı"}</h4>
                          <p>{form.description || "Açıklama yok"}</p>
                        </section>
                        <section className="rules-v3-summary-card">
                          <h4>Sinyal</h4>
                          {formSignal ? (
                            <div className="rules-v3-summary-signal">
                              <span className={`badge badge-source badge-source-${formSignal.source}`}>
                                {SOURCE_SHORT[formSignal.source] ?? formSignal.source}
                              </span>
                              <span className={`badge badge-${formSignal.data_type}`}>{formSignal.data_type}</span>
                              <strong>{formSignal.label}</strong>
                              <code>{formSignal.key}</code>
                            </div>
                          ) : (
                            <span className="rules-v3-muted">Sinyal seçilmedi</span>
                          )}
                        </section>
                        <section className="rules-v3-summary-card">
                          <h4>Koşul</h4>
                          <div className="rules-v3-condition-preview">
                            <strong>{formSignal?.label ?? form.signal_key}</strong>
                            <code className="rules-v3-row-condition">{formConditionText}</code>
                          </div>
                        </section>
                        <section className="rules-v3-summary-card">
                          <h4>Ayarlar</h4>
                          <dl className="rules-v3-summary-list">
                            <div><dt>Durum</dt><dd>{form.is_active ? "Aktif" : "Pasif"}</dd></div>
                            <div><dt>Kapsam</dt><dd>{formScopeText}</dd></div>
                            <div><dt>Debounce</dt><dd>{form.debounce_sec} sn</dd></div>
                            <div><dt>Histerezis</dt><dd>{isBooleanComparator(form.comparator) ? "—" : form.hysteresis}</dd></div>
                            <div><dt>Bildirim</dt><dd>{formNotifyText}</dd></div>
                            <div><dt>Hat Arızası</dt><dd>{form.produces_fault !== false ? "✓" : "—"}</dd></div>
                          </dl>
                        </section>
                      </div>
                    ) : null}

                    {(localError || error) ? (
                      <p className="error-text rule-form-error">{localError || error}</p>
                    ) : null}
                  </div>
                )}
              </div>
            </div>

            <footer className="rules-v3-form-footer rules-v3-wizard-footer">
              <button type="button" className="secondary-btn" onClick={cancel} disabled={saving}>
                {t("engineering.alarmRules.cancel")}
              </button>
              <div className="rules-v3-form-footer-main">
                {!isFirstWizardStep ? (
                  <button type="button" className="secondary-btn" onClick={goPrevWizardStep} disabled={saving}>
                    Önceki
                  </button>
                ) : null}
                {canEdit ? (
                  <button
                    type="submit"
                    className="primary-btn"
                    disabled={saving || (mode === "create" && !pickerSelectedKey)}
                  >
                    {saving
                      ? t("engineering.alarmRules.saving")
                      : isSummaryStep
                        ? mode === "create"
                          ? "Kuralı Oluştur"
                          : "Değişiklikleri Kaydet"
                        : "Sonraki"}
                  </button>
                ) : null}
              </div>
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
            {t("engineering.alarmRules.newRule")}
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
          <table className="rules-v3-table">
            <thead>
              <tr>
                <th>Durum</th>
                <th>Seviye</th>
                <th>Cihaz</th>
                <th>Kural Adı</th>
                <th>Koşul</th>
                <th>Hat Arızası</th>
                <th>Bildirim</th>
                {canEdit ? <th>İşlemler</th> : null}
              </tr>
            </thead>
            <tbody>
              {filteredRules.map((rule) => {
                const sig = signalByKey.get(rule.signal_key);
                const isComposite = (rule.rule_kind ?? "simple") === "composite";
                const conditionText = isComposite && rule.expression
                  ? rule.expression.terms
                      .map((tm) => {
                        const tmSig = signalByKey.get(tm.signal_key);
                        const baseLbl = tmSig?.label ?? tm.signal_key;
                        const lbl =
                          tm.kind === "formula"
                            ? `ƒ(${tm.formula_expr ?? ""})`
                            : tm.kind === "agg" && tm.agg_fn
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
                  <tr key={rule.id} className={rule.is_active ? "" : "rules-v3-row-inactive"}>
                    <td className="rules-v3-status-cell">
                      <div className="rules-v3-status-wrap">
                        {canEdit ? (
                          <ActiveSwitch
                            checked={rule.is_active}
                            onChange={() => void handleToggleActive(rule)}
                          />
                        ) : (
                          <span className={`rules-v3-check ${rule.is_active ? "on" : "off"}`}>
                            {rule.is_active ? "✓" : "—"}
                          </span>
                        )}
                      </div>
                    </td>
                    <td>
                      <span className={`rule-level-badge level-${rule.level}`}>
                        {LEVEL_LABEL[rule.level]}
                      </span>
                    </td>
                    <td>
                      {sig ? (
                        <span className={`badge badge-source badge-source-${sig.source}`}>
                          {SOURCE_SHORT[sig.source] ?? sig.source}
                        </span>
                      ) : (
                        <span className="rules-v3-muted">—</span>
                      )}
                      {rule.device_code_filter ? (
                        <span className="rules-v3-row-scope" title="Cihaz filtresi">
                          {rule.device_code_filter}
                        </span>
                      ) : null}
                    </td>
                    <td>
                      <div className="rules-v3-name-cell">
                        <strong>{rule.name}</strong>
                        {rule.description ? <small>{rule.description}</small> : null}
                        <span className="rules-v3-id-pill">ID: {rule.id}</span>
                      </div>
                    </td>
                    <td>
                      <div className="rules-v3-condition-cell">
                        <strong>{sig?.label ?? rule.signal_key}</strong>
                        <code className="rules-v3-row-condition">{conditionText}</code>
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
                      </div>
                    </td>
                    <td>
                      <span className={`rules-v3-check ${rule.produces_fault !== false ? "on" : "off"}`}>
                        {rule.produces_fault !== false ? "✓" : "—"}
                      </span>
                    </td>
                    <td>
                      <div className="rules-v3-notify-cell">
                        {rule.notify_sms ? <span className="rules-v3-notify-pill">SMS</span> : null}
                        {rule.notify_email ? <span className="rules-v3-notify-pill">E-Posta</span> : null}
                        {rule.notify_telegram ? <span className="rules-v3-notify-pill">Telegram</span> : null}
                        {rule.notify_whatsapp_web ? <span className="rules-v3-notify-pill">WhatsApp</span> : null}
                        {!rule.notify_sms && !rule.notify_email && !rule.notify_telegram && !rule.notify_whatsapp_web ? <span className="rules-v3-muted">—</span> : null}
                      </div>
                    </td>
                    {canEdit ? (
                      <td>
                        <div className="rules-v3-table-actions">
                          <button
                            type="button"
                            className="rules-v3-icon-btn"
                            onClick={() => startEdit(rule.id)}
                            title={t("common.edit")}
                          >
                            <span className="material-symbols-outlined">edit</span>
                          </button>
                          <button
                            type="button"
                            className="rules-v3-icon-btn"
                            onClick={() => duplicateRule(rule)}
                            title="Kopyala"
                          >
                            <span className="material-symbols-outlined">content_copy</span>
                          </button>
                          <button
                            type="button"
                            className="rules-v3-icon-btn rules-v3-delete-btn"
                            onClick={() => void handleDelete(rule.id)}
                            title={t("common.delete")}
                          >
                            <span className="material-symbols-outlined">delete</span>
                          </button>
                        </div>
                      </td>
                    ) : null}
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
      </div>

      {(localError || error) ? <p className="error-text">{localError || error}</p> : null}

      {devicePickerOpen && devices ? (
        <DevicePickerModal
          devices={devices}
          selectedCodes={parseDeviceCodes(form.device_code_filter)}
          onCancel={() => setDevicePickerOpen(false)}
          onConfirm={(codes) => {
            setForm({
              ...form,
              device_code_filter: codes.length === 0 ? "" : codes.join(", ")
            });
            setDevicePickerOpen(false);
          }}
          t={t}
        />
      ) : null}
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
                {/* kind toggle: Anlik | Pencere | Formul */}
                <div className="rule-composite-kind-toggle">
                  <button
                    type="button"
                    className={`rule-mode-btn rule-mode-btn--compact ${tm.kind !== "agg" && tm.kind !== "formula" ? "is-active" : ""}`}
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
                  <button
                    type="button"
                    className={`rule-mode-btn rule-mode-btn--compact ${tm.kind === "formula" ? "is-active" : ""}`}
                    onClick={() =>
                      updateTerm(idx, {
                        kind: "formula",
                        formula_expr: tm.formula_expr ?? "",
                        formula_vars:
                          tm.formula_vars && tm.formula_vars.length > 0
                            ? tm.formula_vars
                            : [{ name: "X", signal_key: tm.signal_key || "", device_code: "*" }]
                      })
                    }
                    disabled={!canEdit}
                    title={t("engineering.alarmRules.composite.kindFormulaHint")}
                  >
                    <span className="material-symbols-outlined">function</span>
                    {t("engineering.alarmRules.composite.kindFormula")}
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
                {tm.kind !== "formula" ? (
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
                ) : null}
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
              {tm.kind === "formula" ? (
                <FormulaEditor
                  term={tm}
                  idx={idx}
                  signals={signals}
                  canEdit={canEdit}
                  onChange={(patch) => updateTerm(idx, patch)}
                  t={t}
                />
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


// =============================================================================
// FORMULA EDITOR — Faz 3: aritmetik ifade + degisken eslemeleri
// =============================================================================

type FormulaEditorProps = {
  term: AlarmCompositeTerm;
  idx: number;
  signals: SignalCatalogRow[];
  canEdit: boolean;
  onChange: (patch: Partial<AlarmCompositeTerm>) => void;
  t: (key: string, opts?: Record<string, unknown>) => string;
};

function FormulaEditor({ term, idx, signals, canEdit, onChange, t }: FormulaEditorProps) {
  const vars = term.formula_vars ?? [];
  const expr = term.formula_expr ?? "";

  const updateVar = (i: number, patch: Partial<AlarmFormulaVar>) => {
    const next = vars.map((v, j) => (j === i ? { ...v, ...patch } : v));
    onChange({ formula_vars: next });
  };
  const addVar = () => {
    if (vars.length >= 16) return;
    // Otomatik benzersiz isim: X, X2, X3...
    const taken = new Set(vars.map((v) => v.name));
    let name = "X";
    let n = 1;
    while (taken.has(name)) {
      n += 1;
      name = `X${n}`;
    }
    onChange({
      formula_vars: [...vars, { name, signal_key: "", device_code: "*" }]
    });
  };
  const removeVar = (i: number) => {
    if (vars.length <= 1) return;
    onChange({ formula_vars: vars.filter((_, j) => j !== i) });
  };

  return (
    <div className="rule-formula-editor">
      <label className="rule-field rule-formula-expr">
        <span>
          {t("engineering.alarmRules.composite.formulaExpr")}
        </span>
        <input
          type="text"
          value={expr}
          onChange={(e) => onChange({ formula_expr: e.target.value })}
          placeholder={t("engineering.alarmRules.composite.formulaExprPlaceholder")}
          disabled={!canEdit}
          spellCheck={false}
        />
      </label>
      <p className="rule-hint rule-formula-help">
        {t("engineering.alarmRules.composite.formulaAllowed")}
      </p>

      <div className="rule-formula-vars">
        <div className="rule-formula-vars-head">
          <strong>{t("engineering.alarmRules.composite.formulaVars")}</strong>
          <span className="rule-hint">{vars.length}/16</span>
        </div>
        <ul className="rule-formula-vars-list">
          {vars.map((v, i) => (
            <li key={i} className="rule-formula-var">
              <label className="rule-field rule-formula-var-name">
                <span>{t("engineering.alarmRules.composite.formulaVarName")}</span>
                <input
                  type="text"
                  value={v.name}
                  onChange={(e) => updateVar(i, { name: e.target.value })}
                  placeholder="X"
                  disabled={!canEdit}
                  spellCheck={false}
                />
              </label>
              <label className="rule-field rule-formula-var-signal">
                <span>{t("engineering.alarmRules.composite.termSignal")}</span>
                <select
                  value={v.signal_key}
                  onChange={(e) => updateVar(i, { signal_key: e.target.value })}
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
              {vars.length > 1 ? (
                <button
                  type="button"
                  className="icon-btn icon-btn-danger rule-formula-var-rm"
                  title={t("engineering.alarmRules.composite.formulaVarRemove")}
                  aria-label={t("engineering.alarmRules.composite.formulaVarRemove")}
                  onClick={() => removeVar(i)}
                  disabled={!canEdit}
                >
                  <span className="material-symbols-outlined">close</span>
                </button>
              ) : null}
            </li>
          ))}
        </ul>
        <button
          type="button"
          className="secondary-btn rule-formula-var-add"
          onClick={addVar}
          disabled={!canEdit || vars.length >= 16}
        >
          <span className="material-symbols-outlined">add</span>
          {t("engineering.alarmRules.composite.formulaVarAdd")}
        </button>
      </div>
      <p className="rule-hint rule-composite-term-hint">
        {t("engineering.alarmRules.composite.formulaResultHint", { idx: idx + 1 })}
      </p>
    </div>
  );
}


// =============================================================================
// SCOPE — Kapsam: cihaz_code_filter "kod1, kod2, ..." string'ini chip listesi
// olarak gosterir + 'Cihazlari Sec' butonuyla modal acar.
// =============================================================================

function parseDeviceCodes(raw: string | null | undefined): string[] {
  if (!raw) return [];
  return raw
    .split(",")
    .map((s) => s.trim())
    .filter(Boolean);
}

type ScopeDevicePickerProps = {
  deviceCodeFilter: string;
  onChange: (next: string) => void;
  canEdit: boolean;
  onOpenPicker: () => void;
  devicesAvailable: boolean;
  t: (key: string, opts?: Record<string, unknown>) => string;
};

function ScopeDevicePicker({
  deviceCodeFilter,
  onChange,
  canEdit,
  onOpenPicker,
  devicesAvailable,
  t,
}: ScopeDevicePickerProps) {
  const codes = parseDeviceCodes(deviceCodeFilter);

  const removeCode = (code: string) => {
    const next = codes.filter((c) => c !== code);
    onChange(next.join(", "));
  };

  return (
    <div className="rule-scope">
      <div className="rule-scope-head">
        <strong>{t("engineering.alarmRules.scope.title")}</strong>
        {codes.length === 0 ? (
          <span className="rule-scope-badge rule-scope-badge--all">
            {t("engineering.alarmRules.scope.allDevices")}
          </span>
        ) : (
          <span className="rule-scope-badge">
            {t("engineering.alarmRules.scope.selectedCount", { count: codes.length })}
          </span>
        )}
      </div>
      <p className="rule-hint">{t("engineering.alarmRules.scope.hint")}</p>
      {codes.length > 0 ? (
        <ul className="rule-scope-chips">
          {codes.map((code) => (
            <li key={code} className="rule-scope-chip">
              <span className="material-symbols-outlined">router</span>
              <span className="rule-scope-chip-code">{code}</span>
              {canEdit ? (
                <button
                  type="button"
                  className="rule-scope-chip-rm"
                  onClick={() => removeCode(code)}
                  title={t("engineering.alarmRules.scope.removeChip")}
                  aria-label={t("engineering.alarmRules.scope.removeChip")}
                >
                  <span className="material-symbols-outlined">close</span>
                </button>
              ) : null}
            </li>
          ))}
        </ul>
      ) : null}
      <div className="rule-scope-actions">
        <button
          type="button"
          className="secondary-btn"
          onClick={onOpenPicker}
          disabled={!canEdit || !devicesAvailable}
          title={
            !devicesAvailable
              ? t("engineering.alarmRules.scope.noDevicesYet")
              : t("engineering.alarmRules.scope.pickBtnTitle")
          }
        >
          <span className="material-symbols-outlined">checklist</span>
          {codes.length === 0
            ? t("engineering.alarmRules.scope.pickBtn")
            : t("engineering.alarmRules.scope.editBtn")}
        </button>
        {codes.length > 0 ? (
          <button
            type="button"
            className="rule-scope-clear"
            onClick={() => onChange("")}
            disabled={!canEdit}
          >
            {t("engineering.alarmRules.scope.clearAll")}
          </button>
        ) : null}
      </div>
    </div>
  );
}


// =============================================================================
// CIHAZ SECIM MODAL — birden fazla cihazi aranabilir + tikla-sec ile sec.
// =============================================================================

type DevicePickerModalProps = {
  devices: DeviceRow[];
  selectedCodes: string[];
  onCancel: () => void;
  onConfirm: (codes: string[]) => void;
  t: (key: string, opts?: Record<string, unknown>) => string;
};

function DevicePickerModal({
  devices,
  selectedCodes,
  onCancel,
  onConfirm,
  t,
}: DevicePickerModalProps) {
  const [search, setSearch] = useState("");
  const [selected, setSelected] = useState<Set<string>>(
    () => new Set(selectedCodes)
  );

  const filtered = useMemo(() => {
    const q = search.trim().toLowerCase();
    const sorted = [...devices].sort((a, b) =>
      (a.name || a.code).localeCompare(b.name || b.code, "tr")
    );
    if (!q) return sorted;
    return sorted.filter(
      (d) =>
        (d.name ?? "").toLowerCase().includes(q) ||
        d.code.toLowerCase().includes(q)
    );
  }, [devices, search]);

  const toggle = (code: string) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(code)) next.delete(code);
      else next.add(code);
      return next;
    });
  };

  const selectAllVisible = () => {
    setSelected((prev) => {
      const next = new Set(prev);
      for (const d of filtered) next.add(d.code);
      return next;
    });
  };

  const deselectAllVisible = () => {
    setSelected((prev) => {
      const next = new Set(prev);
      for (const d of filtered) next.delete(d.code);
      return next;
    });
  };

  const confirm = () => {
    // Ekrandaki cihaz listesinde olmayan eski kodlari da koru (kullanici
    // elle yazip kaydetmis olabilir).
    onConfirm(Array.from(selected));
  };

  // Modal'i body'ye portallayalim: form/section icindeki overflow:hidden veya
  // transform sahibi parent'lar fixed elemani kestiginde modal acilmiyor
  // gibi gozukuyordu — portal bu stacking sorunlarini ortadan kaldirir.
  return createPortal(
    <div
      className="settings-modal-backdrop rule-device-picker-backdrop"
      onClick={onCancel}
    >
      <div
        className="settings-modal rule-device-picker"
        onClick={(e) => e.stopPropagation()}
        role="dialog"
        aria-modal="true"
      >
        <h3>{t("engineering.alarmRules.scope.modalTitle")}</h3>
        <p className="helper-text">
          {t("engineering.alarmRules.scope.modalHint")}
        </p>
        <div className="rule-device-picker-search">
          <span className="material-symbols-outlined">search</span>
          <input
            type="text"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder={t("engineering.alarmRules.scope.searchPlaceholder")}
            autoFocus
          />
        </div>
        <div className="rule-device-picker-bulk">
          <button type="button" className="rule-scope-clear" onClick={selectAllVisible}>
            {t("engineering.alarmRules.scope.selectAllVisible")}
          </button>
          <button type="button" className="rule-scope-clear" onClick={deselectAllVisible}>
            {t("engineering.alarmRules.scope.deselectAllVisible")}
          </button>
          <span className="rule-scope-badge" style={{ marginLeft: "auto" }}>
            {t("engineering.alarmRules.scope.selectedCount", { count: selected.size })}
          </span>
        </div>
        <ul className="rule-device-picker-list">
          {filtered.length === 0 ? (
            <li className="rule-device-picker-empty">
              {t("engineering.alarmRules.scope.noResults")}
            </li>
          ) : (
            filtered.map((d) => {
              const isSel = selected.has(d.code);
              return (
                <li
                  key={d.code}
                  className={`rule-device-picker-row ${isSel ? "is-selected" : ""}`}
                  onClick={() => toggle(d.code)}
                >
                  <input
                    type="checkbox"
                    checked={isSel}
                    readOnly
                    tabIndex={-1}
                  />
                  <div className="rule-device-picker-row-main">
                    <strong>{d.name || d.code}</strong>
                    <small>{d.code}</small>
                  </div>
                </li>
              );
            })
          )}
        </ul>
        <div className="settings-actions">
          <button type="button" onClick={onCancel}>
            {t("engineering.alarmRules.cancel")}
          </button>
          <button type="button" className="primary-btn" onClick={confirm}>
            {t("engineering.alarmRules.scope.confirmBtn")}
          </button>
        </div>
      </div>
    </div>,
    document.body
  );
}
