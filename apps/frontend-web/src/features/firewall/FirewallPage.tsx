/**
 * Guvenlik Duvari (Muhendislik > Cihaz Ayarlari).
 *
 * Appliance'in (mini PC / VPS) host guvenlik duvarini yonetir: acma/kapama,
 * izin/engel kurallari ve port yonlendirmeleri. docker-compose'un yayinladigi
 * SCADA portlari (502 Modbus, 2404-2406 IEC104, 4222 NATS...) kimlik
 * dogrulamasiz oldugu icin varsayilan kurulumda tum aga aciktir; bu sayfa o
 * yuzeyi kullanicinin sectigi kurallara indirger.
 *
 * Mimari (Uzaktan Bakim ile ayni): backend iptables CALISTIRMAZ, istegi
 * request.json'a yazar; host'ta root ile calisan `e1-fwd` ajani uygular ve
 * durumu state.json'a yazar. Bu sayfa yalnizca o durumu okur; PUT 202 doner
 * ve sonuc bir sonraki yoklamada gorunur.
 *
 * KILITLENME KORUMASI AJANDADIR: web arayuzu (80/443), SSH (22) ve uzaktan
 * bakim tuneli HER ZAMAN acik kalir — buradan yazilan hicbir kural onlari
 * kapatamaz. Sayfa bunu acikca soyler; "kendimi disari kilitledim" senaryosu
 * tasarim geregi imkansizdir.
 *
 * DUZENLEME MODELI: sayfa sunucudaki yapilandirmanin bir TASLAGINI tutar.
 * Ekleme/silme/siralama yerelde birikir, "Kaydet ve Uygula" TAMAMINI tek
 * istekte gonderir (artimli uc yok — ajan atomik degistirir). Taslak kirliyken
 * yoklama gelen veriyi taslagin USTUNE YAZMAZ.
 */
import { useCallback, useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import {
  AlertTriangle,
  ArrowRightLeft,
  BrickWall,
  ChevronDown,
  ChevronUp,
  History,
  Loader2,
  Lock,
  Plus,
  RefreshCw,
  ShieldAlert,
  ShieldCheck,
  ShieldOff,
  Trash2
} from "lucide-react";

import { useToast } from "../../components/ToastProvider";
import { formatEventMessage } from "../events/formatEventMessage";
import {
  fetchFirewallAudit,
  fetchFirewallStatus,
  updateFirewallConfig
} from "../../shared/api";
import type {
  FirewallConfig,
  FirewallForward,
  FirewallRule,
  FirewallStatus,
  SystemEvent
} from "../../shared/types";
import { usePolling } from "../../shared/usePolling";

type Props = {
  accessToken: string;
};

/** Durum yoklama periyodu. Uzaktan Bakim ile ayni (10 sn). */
const REFRESH_INTERVAL_SEC = 10;

/** Sayfanin ana durum blogu. `unknown` = ajan/olcum eksik ya da istenen ile
 *  olculen ayristi; "kapali" veya "acik" demeye hakkimiz yok. */
type Mode = "loading" | "applying" | "on" | "off" | "unknown";

const HERO_TEXT: Record<Mode, { state: string; title: string; lead: string }> = {
  loading: {
    state: "firewall.state.loading",
    title: "firewall.hero.loadingTitle",
    lead: "firewall.hero.loadingLead"
  },
  applying: {
    state: "firewall.state.applying",
    title: "firewall.hero.applyingTitle",
    lead: "firewall.hero.applyingLead"
  },
  on: {
    state: "firewall.state.on",
    title: "firewall.hero.onTitle",
    lead: "firewall.hero.onLead"
  },
  off: {
    state: "firewall.state.off",
    title: "firewall.hero.offTitle",
    lead: "firewall.hero.offLead"
  },
  unknown: {
    state: "firewall.state.unknown",
    title: "firewall.hero.unknownTitle",
    lead: "firewall.hero.unknownLead"
  }
};

/** Bilinen servis portlari — tek tikla izin kurali. Liste docker-compose'un
 *  yayinladigi portlardan gelir (bkz. e1-ap-firewall.sh docstring'i); yeni
 *  port yayinlanirsa buraya da eklenmeli. */
const SERVICE_PRESETS: Array<{
  key: string;
  labelKey: string;
  rules: Array<Pick<FirewallRule, "proto" | "ports">>;
}> = [
  { key: "iec104", labelKey: "firewall.presets.iec104", rules: [{ proto: "tcp", ports: "2404-2406" }] },
  { key: "modbus", labelKey: "firewall.presets.modbus", rules: [{ proto: "tcp", ports: "502" }, { proto: "tcp", ports: "5020-5021" }] },
  { key: "nats", labelKey: "firewall.presets.nats", rules: [{ proto: "tcp", ports: "4222" }] },
  { key: "rabbitmq", labelKey: "firewall.presets.rabbitmq", rules: [{ proto: "tcp", ports: "5672" }] },
  { key: "ftp", labelKey: "firewall.presets.ftp", rules: [{ proto: "tcp", ports: "21" }, { proto: "tcp", ports: "30000-30009" }] }
];

const PORTS_RE = /^\d{1,5}(-\d{1,5})?$/;
const CIDR_RE = /^\d{1,3}(\.\d{1,3}){3}(\/\d{1,2})?$/;
const IP_RE = /^\d{1,3}(\.\d{1,3}){3}$/;

const EMPTY_CONFIG: FirewallConfig = { enabled: false, rules: [], forwards: [] };

function normalizeConfig(config?: FirewallConfig | null): FirewallConfig {
  if (!config) return EMPTY_CONFIG;
  return {
    enabled: Boolean(config.enabled),
    rules: (config.rules ?? []).map((rule) => ({
      action: rule.action,
      proto: rule.proto,
      ports: rule.ports,
      source: rule.source ?? null,
      comment: rule.comment ?? null
    })),
    forwards: (config.forwards ?? []).map((fwd) => ({
      proto: fwd.proto,
      listen_port: fwd.listen_port,
      dest_ip: fwd.dest_ip,
      dest_port: fwd.dest_port,
      comment: fwd.comment ?? null
    }))
  };
}

function serialize(config: FirewallConfig): string {
  return JSON.stringify(config);
}

function portsValid(text: string): boolean {
  if (!PORTS_RE.test(text)) return false;
  const [lo, hi = lo] = text.split("-").map(Number);
  return lo >= 1 && lo <= 65535 && hi >= 1 && hi <= 65535 && lo <= hi;
}

function portInRange(ports: string, port: number): boolean {
  const [lo, hi = lo] = ports.split("-").map(Number);
  return port >= lo && port <= hi;
}

/** Verilen porta ILK eslesen kuralin aksiyonu (kaynak kisiti olmayanlar).
 *  Eslesme yoksa null — duvar acikken bu "varsayilan engel" demektir. */
function firstMatchAction(rules: FirewallRule[], proto: string, port: number): "allow" | "deny" | null {
  for (const rule of rules) {
    if (rule.proto === proto && !rule.source && portInRange(rule.ports, port)) {
      return rule.action;
    }
  }
  return null;
}

function formatClock(value?: string | null): string {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "—";
  return date.toLocaleString([], {
    day: "2-digit",
    month: "2-digit",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit"
  });
}

export function FirewallPage({ accessToken }: Props) {
  const { t } = useTranslation();
  const toast = useToast();

  const [status, setStatus] = useState<FirewallStatus | null>(null);
  const [audit, setAudit] = useState<SystemEvent[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);

  // Taslak + sunucu temeli. `dirty` iken yoklama taslagi EZMEZ.
  const [draft, setDraft] = useState<FirewallConfig>(EMPTY_CONFIG);
  const [baseline, setBaseline] = useState<string>(serialize(EMPTY_CONFIG));

  const [confirmOpen, setConfirmOpen] = useState<null | "save" | "enable" | "disable">(null);
  /** Aktif sekme: kurallar / port yonlendirme (tek kart, iki sekme). */
  const [tab, setTab] = useState<"rules" | "forwards">("rules");
  const [submitting, setSubmitting] = useState(false);

  // Kural ekleme formu
  const [ruleAction, setRuleAction] = useState<"allow" | "deny">("allow");
  const [ruleProto, setRuleProto] = useState<"tcp" | "udp">("tcp");
  const [rulePorts, setRulePorts] = useState("");
  const [ruleSource, setRuleSource] = useState("");
  const [ruleComment, setRuleComment] = useState("");

  // Yonlendirme ekleme formu
  const [fwdProto, setFwdProto] = useState<"tcp" | "udp">("tcp");
  const [fwdListen, setFwdListen] = useState("");
  const [fwdDestIp, setFwdDestIp] = useState("");
  const [fwdDestPort, setFwdDestPort] = useState("");
  const [fwdComment, setFwdComment] = useState("");

  const dirty = serialize(draft) !== baseline;

  const load = useCallback(async () => {
    try {
      const next = await fetchFirewallStatus(accessToken);
      setStatus(next);
      setLoadError(null);
    } catch (exc) {
      const message = exc instanceof Error ? exc.message : t("firewall.errors.load");
      // 401 sentinel'i ekrana BASILMAZ (oturum yenilenirken gecici gorunur).
      if (message !== "session_polling_401") setLoadError(message);
    } finally {
      setLoading(false);
    }
  }, [accessToken, t]);

  const loadAudit = useCallback(async () => {
    try {
      setAudit(await fetchFirewallAudit(accessToken));
    } catch {
      // Gecmis ikincil bilgi — hatasi ana akisi bozmasin.
    }
  }, [accessToken]);

  usePolling({ enabled: true, intervalMs: REFRESH_INTERVAL_SEC * 1000, fn: load });

  useEffect(() => {
    void loadAudit();
  }, [loadAudit]);

  // Sunucu yapilandirmasi taslaga yalnizca taslak TEMIZKEN akar; kullanicinin
  // yarim duzenlemesi yoklamayla kaybolmaz. Taslak kirliyken temel yine de
  // guncellenir ki "kirli" bayragi gercek farki olcmeye devam etsin.
  useEffect(() => {
    if (status === null) return;
    const server = normalizeConfig(status.config);
    const serverSerialized = serialize(server);
    if (serverSerialized === baseline) return;
    setDraft((prev) => (serialize(prev) === baseline ? server : prev));
    setBaseline(serverSerialized);
  }, [status, baseline]);

  // ---- Durum turetmeleri ----------------------------------------------------
  const available = status?.available === true;
  const canManage = status?.can_manage === true;
  const pending = status?.pending === true;
  const staleAgent = status?.reason === "state_stale";
  const mismatch = status?.mismatch ?? null;
  const applyFailed = status?.last_apply?.status === "failed";
  const guardPorts = status?.guard_tcp_ports?.length ? status.guard_tcp_ports : [22, 80, 443];
  const maxRules = status?.max_rules ?? 50;
  const maxForwards = status?.max_forwards ?? 20;

  const mode: Mode =
    status === null
      ? loading
        ? "loading"
        : "unknown"
      : pending
      ? "applying"
      : !available
      ? "unknown"
      : status.enabled
      ? status.active
        ? "on"
        : "unknown"
      : "off";

  /** Neden yonetilemiyor? Tek sebep kodu — metin i18n'den. */
  const blockedReason: string | null =
    status === null
      ? null
      : !available
      ? status.reason ?? "unknown"
      : !status.iptables
      ? "iptables_missing"
      : null;

  const formDisabled = blockedReason !== null || !canManage || pending || submitting;

  // ---- Taslak islemleri -----------------------------------------------------
  const ruleError = useMemo(() => {
    if (rulePorts.trim().length === 0) return null;
    if (!portsValid(rulePorts.trim())) return t("firewall.rules.invalidPorts");
    const source = ruleSource.trim();
    if (source && !CIDR_RE.test(source)) return t("firewall.rules.invalidSource");
    return null;
  }, [rulePorts, ruleSource, t]);

  const addRule = () => {
    const ports = rulePorts.trim();
    if (!portsValid(ports) || ruleError) return;
    if (draft.rules.length >= maxRules) {
      toast.error(t("firewall.rules.limitReached", { max: maxRules }));
      return;
    }
    const rule: FirewallRule = {
      action: ruleAction,
      proto: ruleProto,
      ports,
      source: ruleSource.trim() || null,
      comment: ruleComment.trim() || null
    };
    setDraft((prev) => ({ ...prev, rules: [...prev.rules, rule] }));
    setRulePorts("");
    setRuleSource("");
    setRuleComment("");
  };

  const removeRule = (index: number) => {
    setDraft((prev) => ({ ...prev, rules: prev.rules.filter((_, i) => i !== index) }));
  };

  /** Kural sirasi ONEMLI (ilk eslesen kazanir) — engel kurali izinden once
   *  gelmeli; bu yuzden siralama dugmeleri var. */
  const moveRule = (index: number, delta: -1 | 1) => {
    setDraft((prev) => {
      const next = [...prev.rules];
      const target = index + delta;
      if (target < 0 || target >= next.length) return prev;
      [next[index], next[target]] = [next[target], next[index]];
      return { ...prev, rules: next };
    });
  };

  const presetCovered = (preset: (typeof SERVICE_PRESETS)[number]): boolean =>
    preset.rules.every((entry) =>
      draft.rules.some(
        (rule) =>
          rule.action === "allow" &&
          rule.proto === entry.proto &&
          rule.ports === entry.ports &&
          !rule.source
      )
    );

  const addPreset = (preset: (typeof SERVICE_PRESETS)[number]) => {
    if (draft.rules.length + preset.rules.length > maxRules) {
      toast.error(t("firewall.rules.limitReached", { max: maxRules }));
      return;
    }
    const label = t(preset.labelKey);
    setDraft((prev) => ({
      ...prev,
      rules: [
        ...prev.rules,
        ...preset.rules
          .filter(
            (entry) =>
              !prev.rules.some(
                (rule) =>
                  rule.action === "allow" &&
                  rule.proto === entry.proto &&
                  rule.ports === entry.ports &&
                  !rule.source
              )
          )
          .map((entry) => ({
            action: "allow" as const,
            proto: entry.proto,
            ports: entry.ports,
            source: null,
            comment: label
          }))
      ]
    }));
  };

  const forwardError = useMemo(() => {
    if (!fwdListen.trim() && !fwdDestIp.trim() && !fwdDestPort.trim()) return null;
    const listen = Number(fwdListen.trim());
    if (!/^\d{1,5}$/.test(fwdListen.trim()) || listen < 1 || listen > 65535) {
      return t("firewall.forwards.invalidListen");
    }
    if ((status?.reserved_listen_ports ?? []).includes(listen)) {
      return t("firewall.forwards.reservedListen", { port: listen });
    }
    if (draft.forwards.some((fwd) => fwd.proto === fwdProto && fwd.listen_port === listen)) {
      return t("firewall.forwards.duplicateListen", { port: listen });
    }
    if (!IP_RE.test(fwdDestIp.trim())) return t("firewall.forwards.invalidDestIp");
    const destPort = Number(fwdDestPort.trim());
    if (!/^\d{1,5}$/.test(fwdDestPort.trim()) || destPort < 1 || destPort > 65535) {
      return t("firewall.forwards.invalidDestPort");
    }
    return null;
  }, [fwdListen, fwdDestIp, fwdDestPort, fwdProto, draft.forwards, status?.reserved_listen_ports, t]);

  const forwardComplete =
    fwdListen.trim().length > 0 && fwdDestIp.trim().length > 0 && fwdDestPort.trim().length > 0;

  const addForward = () => {
    if (!forwardComplete || forwardError) return;
    if (draft.forwards.length >= maxForwards) {
      toast.error(t("firewall.forwards.limitReached", { max: maxForwards }));
      return;
    }
    const fwd: FirewallForward = {
      proto: fwdProto,
      listen_port: Number(fwdListen.trim()),
      dest_ip: fwdDestIp.trim(),
      dest_port: Number(fwdDestPort.trim()),
      comment: fwdComment.trim() || null
    };
    setDraft((prev) => ({ ...prev, forwards: [...prev.forwards, fwd] }));
    setFwdListen("");
    setFwdDestIp("");
    setFwdDestPort("");
    setFwdComment("");
  };

  const removeForward = (index: number) => {
    setDraft((prev) => ({ ...prev, forwards: prev.forwards.filter((_, i) => i !== index) }));
  };

  const discardDraft = () => {
    if (status) setDraft(normalizeConfig(status.config));
  };

  // ---- Gonderim -------------------------------------------------------------
  /** Onay modalinda gosterilecek yapilandirma: ac/kapat dugmeleri taslagin
   *  `enabled` alanini degistirip AYNI gonderim yolunu kullanir. */
  const pendingConfig: FirewallConfig | null =
    confirmOpen === null
      ? null
      : confirmOpen === "enable"
      ? { ...draft, enabled: true }
      : confirmOpen === "disable"
      ? { ...draft, enabled: false }
      : draft;

  /** Duvar acikken telemetri portu (NATS 4222) kapali kalirsa uzak gateway'ler
   *  veri basamaz — bu sessiz kalirsa saha "veri gelmiyor" arizasi olarak
   *  doner. Onay modalinda acikca uyarilir. */
  const telemetryBlocked =
    pendingConfig !== null &&
    pendingConfig.enabled &&
    firstMatchAction(pendingConfig.rules, "tcp", 4222) !== "allow";

  const submit = async () => {
    if (pendingConfig === null) return;
    setSubmitting(true);
    try {
      await updateFirewallConfig(accessToken, pendingConfig);
      setConfirmOpen(null);
      // Taslak gonderilenle esitlenir; ajan uygulayinca yoklama dogrular.
      setDraft(pendingConfig);
      setBaseline(serialize(pendingConfig));
      toast.success(t("firewall.toasts.saved"));
      await load();
      await loadAudit();
    } catch (exc) {
      toast.error(exc instanceof Error ? exc.message : t("firewall.errors.save"));
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <section className="tab-panel rad-page fw-page">
      {/* NOT: Buyuk "durum blogu" (hero) KALDIRILDI (kullanici istegi) —
          durum rozeti + ac/kapat dugmesi asagidaki sekmeli kartin
          basligindadir. */}

      {/* ================= Uyari seritleri ================= */}
      {loadError ? <p className="net-banner net-banner--bad">{loadError}</p> : null}

      {blockedReason !== null && status !== null ? (
        <p className="net-banner net-banner--bad">
          <AlertTriangle size={16} />
          {t(`firewall.blocked.reason.${blockedReason}`, {
            defaultValue: t("firewall.blocked.generic")
          })}
        </p>
      ) : null}

      {staleAgent && available ? (
        <p className="net-banner net-banner--bad">
          <AlertTriangle size={16} />
          {t("firewall.warn.staleAgent")}
        </p>
      ) : null}

      {mismatch ? (
        <p className="net-banner net-banner--bad">
          <ShieldAlert size={16} />
          {t(`firewall.warn.mismatch.${mismatch}`, {
            defaultValue: t("firewall.warn.mismatch.generic")
          })}
        </p>
      ) : null}

      {applyFailed ? (
        <p className="net-banner net-banner--bad">
          <AlertTriangle size={16} />
          {t("firewall.warn.applyFailed", { error: status?.last_apply?.error ?? "" })}
        </p>
      ) : null}

      {/* ================= Iki sutun: solda sekmeli kart, sagda gecmis ======
           Kurallar/Port Yonlendirme tek kartin iki sekmesi; "Son islemler"
           yaninda ayri kutu (kullanici istegi). "Nasil calisir" karti
           kaldirildi — kilitlenme korumasi zaten ustteki seritte yaziyor. */}
      <div className="fw-columns">
      <div className="fw-col-main">
      <section className="rad-card fw-card fw-main">
        <header className="fw-tabs-head">
          <div className="fw-tabs" role="tablist">
            <button
              type="button"
              role="tab"
              aria-selected={tab === "rules"}
              className={`fw-tab${tab === "rules" ? " is-active" : ""}`}
              onClick={() => setTab("rules")}
            >
              <BrickWall size={15} />
              {t("firewall.rules.title", { count: draft.rules.length, max: maxRules })}
            </button>
            <button
              type="button"
              role="tab"
              aria-selected={tab === "forwards"}
              className={`fw-tab${tab === "forwards" ? " is-active" : ""}`}
              onClick={() => setTab("forwards")}
            >
              <ArrowRightLeft size={15} />
              {t("firewall.forwards.title", { count: draft.forwards.length, max: maxForwards })}
            </button>
          </div>
          <div className="fw-head-actions">
            <span className={`fw-state-pill is-${mode}`}>
              <span className="rad-dot" aria-hidden="true" />
              {t(HERO_TEXT[mode].state)}
            </span>
            {canManage && blockedReason === null ? (
              mode === "off" ? (
                <button
                  type="button"
                  className="primary-btn"
                  disabled={formDisabled}
                  onClick={() => setConfirmOpen("enable")}
                >
                  <ShieldCheck size={16} />
                  {t("firewall.actions.enable")}
                </button>
              ) : mode === "on" || (status?.enabled && mode === "unknown") ? (
                <button
                  type="button"
                  className="danger-btn"
                  disabled={formDisabled}
                  onClick={() => setConfirmOpen("disable")}
                >
                  <ShieldOff size={16} />
                  {t("firewall.actions.disable")}
                </button>
              ) : null
            ) : null}
            <button
              type="button"
              className="secondary-btn net-refresh"
              disabled={loading}
              onClick={() => {
                void load();
                void loadAudit();
              }}
            >
              <RefreshCw size={16} className={loading ? "net-spin" : ""} />
              {t("common.refresh")}
            </button>
          </div>
        </header>

        {tab === "rules" ? (
        <>
        <p className="fw-hint">{t("firewall.rules.hint")}</p>

        {/* Servis on tanimlari: yayinlanan portlar tek tikla izinlenir. */}
        {canManage ? (
          <div className="fw-presets">
            <span className="fw-presets-label">{t("firewall.presets.title")}</span>
            {SERVICE_PRESETS.map((preset) => (
              <button
                key={preset.key}
                type="button"
                className="fw-preset-chip"
                disabled={formDisabled || presetCovered(preset)}
                title={preset.rules.map((r) => `${r.ports}/${r.proto}`).join(", ")}
                onClick={() => addPreset(preset)}
              >
                <Plus size={13} />
                {t(preset.labelKey)}
              </button>
            ))}
          </div>
        ) : null}

        {canManage ? (
          <div className="fw-add-row">
            <select
              value={ruleAction}
              disabled={formDisabled}
              onChange={(event) => setRuleAction(event.target.value as "allow" | "deny")}
            >
              <option value="allow">{t("firewall.rules.allow")}</option>
              <option value="deny">{t("firewall.rules.deny")}</option>
            </select>
            <select
              value={ruleProto}
              disabled={formDisabled}
              onChange={(event) => setRuleProto(event.target.value as "tcp" | "udp")}
            >
              <option value="tcp">TCP</option>
              <option value="udp">UDP</option>
            </select>
            <input
              value={rulePorts}
              disabled={formDisabled}
              placeholder={t("firewall.rules.portsPlaceholder")}
              onChange={(event) => setRulePorts(event.target.value.replace(/[^\d-]/g, ""))}
            />
            <input
              value={ruleSource}
              disabled={formDisabled}
              placeholder={t("firewall.rules.sourcePlaceholder")}
              onChange={(event) => setRuleSource(event.target.value.replace(/[^\d./]/g, ""))}
            />
            <input
              value={ruleComment}
              disabled={formDisabled}
              maxLength={80}
              placeholder={t("firewall.rules.commentPlaceholder")}
              onChange={(event) => setRuleComment(event.target.value)}
            />
            <button
              type="button"
              className="secondary-btn"
              disabled={formDisabled || rulePorts.trim().length === 0 || ruleError !== null}
              onClick={addRule}
            >
              <Plus size={15} />
              {t("firewall.rules.add")}
            </button>
          </div>
        ) : null}
        {ruleError ? <small className="fw-form-error">{ruleError}</small> : null}

        {draft.rules.length === 0 ? (
          <p className="net-empty">{t("firewall.rules.empty")}</p>
        ) : (
          <div className="fw-table-wrap">
            <table className="fw-table">
              <thead>
                <tr>
                  <th className="fw-col-order">#</th>
                  <th>{t("firewall.rules.colAction")}</th>
                  <th>{t("firewall.rules.colProto")}</th>
                  <th>{t("firewall.rules.colPorts")}</th>
                  <th>{t("firewall.rules.colSource")}</th>
                  <th>{t("firewall.rules.colComment")}</th>
                  {canManage ? <th className="fw-col-tools" /> : null}
                </tr>
              </thead>
              <tbody>
                {draft.rules.map((rule, index) => (
                  <tr key={`${rule.proto}-${rule.ports}-${rule.source ?? ""}-${index}`}>
                    <td className="fw-col-order">{index + 1}</td>
                    <td>
                      <span className={`fw-chip ${rule.action === "allow" ? "is-allow" : "is-deny"}`}>
                        {t(rule.action === "allow" ? "firewall.rules.allow" : "firewall.rules.deny")}
                      </span>
                    </td>
                    <td className="fw-mono">{rule.proto.toUpperCase()}</td>
                    <td className="fw-mono">{rule.ports}</td>
                    <td className="fw-mono">{rule.source ?? t("firewall.rules.anySource")}</td>
                    <td className="fw-comment">{rule.comment ?? "—"}</td>
                    {canManage ? (
                      <td className="fw-col-tools">
                        <button
                          type="button"
                          className="fw-tool-btn"
                          disabled={formDisabled || index === 0}
                          title={t("firewall.rules.moveUp")}
                          onClick={() => moveRule(index, -1)}
                        >
                          <ChevronUp size={15} />
                        </button>
                        <button
                          type="button"
                          className="fw-tool-btn"
                          disabled={formDisabled || index === draft.rules.length - 1}
                          title={t("firewall.rules.moveDown")}
                          onClick={() => moveRule(index, 1)}
                        >
                          <ChevronDown size={15} />
                        </button>
                        <button
                          type="button"
                          className="fw-tool-btn fw-tool-btn--danger"
                          disabled={formDisabled}
                          title={t("firewall.rules.delete")}
                          onClick={() => removeRule(index)}
                        >
                          <Trash2 size={15} />
                        </button>
                      </td>
                    ) : null}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}

        </>
        ) : (
        <>
        <p className="fw-hint">{t("firewall.forwards.hint")}</p>

        {canManage ? (
          <div className="fw-add-row fw-add-row--forward">
            <select
              value={fwdProto}
              disabled={formDisabled}
              onChange={(event) => setFwdProto(event.target.value as "tcp" | "udp")}
            >
              <option value="tcp">TCP</option>
              <option value="udp">UDP</option>
            </select>
            <input
              value={fwdListen}
              disabled={formDisabled}
              inputMode="numeric"
              placeholder={t("firewall.forwards.listenPlaceholder")}
              onChange={(event) => setFwdListen(event.target.value.replace(/[^\d]/g, ""))}
            />
            <span className="fw-add-arrow" aria-hidden="true">
              <ArrowRightLeft size={14} />
            </span>
            <input
              value={fwdDestIp}
              disabled={formDisabled}
              placeholder={t("firewall.forwards.destIpPlaceholder")}
              onChange={(event) => setFwdDestIp(event.target.value.replace(/[^\d.]/g, ""))}
            />
            <input
              value={fwdDestPort}
              disabled={formDisabled}
              inputMode="numeric"
              placeholder={t("firewall.forwards.destPortPlaceholder")}
              onChange={(event) => setFwdDestPort(event.target.value.replace(/[^\d]/g, ""))}
            />
            <input
              value={fwdComment}
              disabled={formDisabled}
              maxLength={80}
              placeholder={t("firewall.rules.commentPlaceholder")}
              onChange={(event) => setFwdComment(event.target.value)}
            />
            <button
              type="button"
              className="secondary-btn"
              disabled={formDisabled || !forwardComplete || forwardError !== null}
              onClick={addForward}
            >
              <Plus size={15} />
              {t("firewall.rules.add")}
            </button>
          </div>
        ) : null}
        {forwardError ? <small className="fw-form-error">{forwardError}</small> : null}

        {draft.forwards.length === 0 ? (
          <p className="net-empty">{t("firewall.forwards.empty")}</p>
        ) : (
          <div className="fw-table-wrap">
            <table className="fw-table">
              <thead>
                <tr>
                  <th>{t("firewall.rules.colProto")}</th>
                  <th>{t("firewall.forwards.colListen")}</th>
                  <th>{t("firewall.forwards.colDest")}</th>
                  <th>{t("firewall.rules.colComment")}</th>
                  {canManage ? <th className="fw-col-tools" /> : null}
                </tr>
              </thead>
              <tbody>
                {draft.forwards.map((fwd, index) => (
                  <tr key={`${fwd.proto}-${fwd.listen_port}-${index}`}>
                    <td className="fw-mono">{fwd.proto.toUpperCase()}</td>
                    <td className="fw-mono">{fwd.listen_port}</td>
                    <td className="fw-mono">
                      {fwd.dest_ip}:{fwd.dest_port}
                    </td>
                    <td className="fw-comment">{fwd.comment ?? "—"}</td>
                    {canManage ? (
                      <td className="fw-col-tools">
                        <button
                          type="button"
                          className="fw-tool-btn fw-tool-btn--danger"
                          disabled={formDisabled}
                          title={t("firewall.rules.delete")}
                          onClick={() => removeForward(index)}
                        >
                          <Trash2 size={15} />
                        </button>
                      </td>
                    ) : null}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}

        </>
        )}

        {/* Kilitlenme korumasi — ust seritten buraya alindi: her zaman
            gorunur ama sayfanin basini isgal etmez. Koruma AJANDA sabittir. */}
        <p className="fw-guard-note">
          <Lock size={13} />
          {t("firewall.guard.text", { ports: guardPorts.join(", ") })}
        </p>
      </section>

      {/* ================= Kaydet cubugu — taslak kirliyken ================= */}
      {dirty && canManage ? (
        <div className="fw-savebar">
          <AlertTriangle size={16} />
          <span>{t("firewall.save.dirty")}</span>
          <div className="fw-savebar-actions">
            <button type="button" className="secondary-btn" disabled={submitting} onClick={discardDraft}>
              {t("firewall.save.discard")}
            </button>
            <button
              type="button"
              className="primary-btn"
              disabled={formDisabled}
              onClick={() => setConfirmOpen("save")}
            >
              {t("firewall.save.apply")}
            </button>
          </div>
        </div>
      ) : null}
      </div>

        {/* ---- SAG: son islemler ---- */}
        <section className="rad-card rad-log">
          <header className="rad-card-head">
            <h3>
              <History size={16} />
              {t("firewall.log.title")}
            </h3>
            <small>{t("firewall.log.hint")}</small>
          </header>
          {audit.length === 0 ? (
            <p className="net-empty">{t("firewall.log.empty")}</p>
          ) : (
            <ul className="rad-log-list">
              {audit.map((event) => (
                <li key={event.id} className={`rad-log-row is-${event.event_type}`}>
                  <span className="rad-log-icon">
                    {event.event_type === "firewall_enabled" ? (
                      <ShieldCheck size={15} />
                    ) : event.event_type === "firewall_disabled" ? (
                      <ShieldOff size={15} />
                    ) : (
                      <BrickWall size={15} />
                    )}
                  </span>
                  <time>{formatClock(event.created_at)}</time>
                  <span className="rad-log-text">{formatEventMessage(event)}</span>
                  <span className="rad-log-side">
                    {event.actor_username ? <b>{event.actor_username}</b> : null}
                  </span>
                </li>
              ))}
            </ul>
          )}
        </section>
      </div>

      {/* ================= Onay modali — TEK surtunme noktasi ================= */}
      {confirmOpen !== null && pendingConfig !== null ? (
        <div
          className="settings-modal-backdrop"
          onClick={() => !submitting && setConfirmOpen(null)}
        >
          <div
            className="settings-modal rad-confirm"
            role="dialog"
            aria-modal="true"
            onClick={(event) => event.stopPropagation()}
          >
            <h3>
              <ShieldAlert size={20} />
              {t(
                confirmOpen === "enable"
                  ? "firewall.confirm.titleEnable"
                  : confirmOpen === "disable"
                  ? "firewall.confirm.titleDisable"
                  : "firewall.confirm.titleSave"
              )}
            </h3>
            <p className="rad-confirm-lead">
              {t(
                confirmOpen === "disable"
                  ? "firewall.confirm.leadDisable"
                  : "firewall.confirm.lead"
              )}
            </p>

            <ul className="net-confirm-summary">
              <li>
                <span>{t("firewall.confirm.rowState")}</span>
                <strong>
                  {t(pendingConfig.enabled ? "firewall.state.on" : "firewall.state.off")}
                </strong>
              </li>
              <li>
                <span>{t("firewall.confirm.rowRules")}</span>
                <strong>{pendingConfig.rules.length}</strong>
              </li>
              <li>
                <span>{t("firewall.confirm.rowForwards")}</span>
                <strong>{pendingConfig.forwards.length}</strong>
              </li>
            </ul>

            {telemetryBlocked ? (
              <p className="net-banner net-banner--warn fw-confirm-warn">
                <ShieldAlert size={16} />
                {t("firewall.confirm.telemetryWarn")}
              </p>
            ) : null}

            <div className="net-confirm-actions">
              <button
                type="button"
                className="secondary-btn"
                disabled={submitting}
                onClick={() => setConfirmOpen(null)}
              >
                {t("common.cancel")}
              </button>
              <button
                type="button"
                className={pendingConfig.enabled ? "primary-btn" : "danger-btn"}
                disabled={submitting}
                onClick={() => void submit()}
              >
                {submitting ? t("firewall.confirm.working") : t("firewall.confirm.approve")}
              </button>
            </div>
          </div>
        </div>
      ) : null}
    </section>
  );
}
