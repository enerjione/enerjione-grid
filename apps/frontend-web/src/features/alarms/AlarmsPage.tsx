import { useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { Check, ExternalLink, SlidersHorizontal, X } from "lucide-react";

import { asyncConfirm } from "../../components/ConfirmDialog";
import { TablePagination } from "../../components/TablePagination";
import type { AlarmComment, AlarmEvent, DeviceRow, Line, Region, SystemEvent, UserRead } from "../../shared/types";

// Cihaz id -> topoloji (bolge/hat) — App.tsx deviceTopologyInfo map'i.
type DeviceTopology = Map<number, { regionId: number; regionName: string; lineId: number; lineName: string }>;

type Props = {
  alarms: AlarmEvent[];
  users: UserRead[];
  devices: DeviceRow[];
  regions: Region[];
  lines: Line[];
  deviceTopology: DeviceTopology;
  loading?: boolean;
  onAssign: (alarmId: number, assignedTo: string | null) => Promise<void>;
  onLoadComments: (alarmId: number) => Promise<AlarmComment[]>;
  onAddComment: (alarmId: number, comment: string) => Promise<void>;
  onAcknowledge: (alarmId: number) => Promise<void>;
  onAcknowledgeAll: () => Promise<void>;
  onOpenDevice: (deviceId: number) => void;
  events: SystemEvent[];
};

type TimeFilter = "all" | "1h" | "24h" | "7d";
type StatusFilter = "all" | "open" | "ack" | "pendingAck";
type AlarmTab = "active" | "resolved" | "history";

export function AlarmsPage({
  alarms,
  users,
  devices,
  regions,
  lines,
  deviceTopology,
  loading,
  onAssign,
  onLoadComments,
  onAddComment,
  onAcknowledge,
  onAcknowledgeAll,
  onOpenDevice,
  events
}: Props) {
  const { t, i18n } = useTranslation();
  const localeTag = i18n.language?.startsWith("tr") ? "tr-TR" : "en-US";
  const [activeTab, setActiveTab] = useState<AlarmTab>("active");
  const [search, setSearch] = useState("");
  const [levelFilter, setLevelFilter] = useState<"all" | "critical" | "warning" | "info">("all");
  const [assignmentFilter, setAssignmentFilter] = useState<"all" | "assigned" | "unassigned">("all");
  const [timeFilter, setTimeFilter] = useState<TimeFilter>("all");
  const [dateFrom, setDateFrom] = useState<string>(""); // datetime-local
  const [dateTo, setDateTo] = useState<string>("");
  const [regionFilter, setRegionFilter] = useState<number | "all">("all");
  const [lineFilter, setLineFilter] = useState<number | "all">("all");
  const [deviceFilter, setDeviceFilter] = useState<number | "all">("all");
  const [statusFilter, setStatusFilter] = useState<StatusFilter>("all");
  const [filterOpen, setFilterOpen] = useState(false);
  const filterWrapRef = useRef<HTMLDivElement | null>(null);
  const [selectedAlarmId, setSelectedAlarmId] = useState<number | null>(null);
  const [panelTab, setPanelTab] = useState<"detail" | "comments">("detail");
  const [commentDraft, setCommentDraft] = useState("");
  const [commentsByAlarm, setCommentsByAlarm] = useState<Record<number, AlarmComment[]>>({});
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(50);

  const selectedAlarm = useMemo(
    () => alarms.find((item) => item.id === selectedAlarmId) ?? null,
    [alarms, selectedAlarmId]
  );

  // Cihaz id -> ad/kod lookup'i (satirlarda anlamli etiket).
  const deviceLabelById = useMemo(() => {
    const map = new Map<number, { name: string; code: string }>();
    for (const dev of devices) map.set(dev.id, { name: dev.name, code: dev.code });
    return map;
  }, [devices]);

  /** Cihaz hucresi: ad + kod ustte, kaynak · bolge · hat altta (bulunabilirlik). */
  const renderDeviceSourceCell = (deviceId: number, signalKey: string | null | undefined) => {
    const info = deviceLabelById.get(deviceId);
    const src = sourceOf(signalKey);
    const topo = deviceTopology.get(deviceId);
    // Alt satir: kaynak · bolge · hat (dolu olanlar birlestirilir).
    const metaParts = [
      src?.label,
      topo?.regionName || null,
      topo?.lineName || null,
    ].filter(Boolean);
    return (
      <div className="alarm-devsource-cell">
        <span className="alarm-devsource-name">
          {info ? info.name : `#${deviceId}`}
          {info ? <span className="alarm-devsource-code"> · {info.code}</span> : null}
        </span>
        <span className="alarm-devsource-src">
          {metaParts.length > 0 ? metaParts.join(" · ") : "—"}
        </span>
      </div>
    );
  };

  /** Seviye kodundan i18n etiketi. Bilinmeyen seviye icin ham deger doner. */
  const levelLabelTr = (level: string): string => {
    const k = level.toLowerCase();
    if (k === "info" || k === "warning" || k === "critical" || k === "error" || k === "debug") {
      return t(`alarms.level.${k}`);
    }
    return level;
  };

  /** Sinyal anahtari prefix'inden Master / Sat 01 / Sat 02 rozeti. */
  const sourceOf = (signalKey: string | null | undefined): { label: string; klass: string } | null => {
    if (!signalKey) return null;
    const prefix = signalKey.split(".", 1)[0]?.toLowerCase() ?? "";
    const map: Record<string, { label: string; klass: string }> = {
      master: { label: "Master", klass: "master" },
      sat01: { label: "Sat 01", klass: "sat01" },
      sat02: { label: "Sat 02", klass: "sat02" }
    };
    return map[prefix] ?? null;
  };

  /** Alarm durumu (SCADA): acik / onaylandi / normale-dondu-onay-bekliyor. */
  const alarmState = (a: AlarmEvent): { label: string; klass: string } => {
    if (a.reset && !a.acknowledged) return { label: t("alarms.state.pendingAck"), klass: "state-pending" };
    if (a.acknowledged) return { label: t("alarms.stateAck"), klass: "state-ack" };
    return { label: t("alarms.stateOpen"), klass: "state-open" };
  };

  /** Sure formatla (ms -> "12 dk" / "1 sa 3 dk"). */
  const formatDuration = (ms: number): string => {
    const totalMin = Math.max(0, Math.floor(ms / 60000));
    const h = Math.floor(totalMin / 60);
    const m = totalMin % 60;
    if (h > 0) return `${h} ${t("alarms.detail.hourShort")} ${m} ${t("alarms.detail.minShort")}`;
    return `${m} ${t("alarms.detail.minShort")}`;
  };

  // Genel filtre (arama / seviye / atama / zaman / bolge / cihaz / durum).
  const filterPredicate = (alarm: AlarmEvent): boolean => {
    const level = alarm.level.toLowerCase();
    const levelOk = levelFilter === "all" ? true : level === levelFilter;
    const assignmentOk =
      assignmentFilter === "all"
        ? true
        : assignmentFilter === "assigned"
          ? Boolean(alarm.assigned_to)
          : !alarm.assigned_to;
    const createdMs = new Date(alarm.created_at).getTime();
    // Zaman: hizli on-ayar (1h/24h/7d) VE/VEYA ozel tarih araligi (from/to).
    let timeOk = true;
    if (timeFilter !== "all") {
      const spanMs = timeFilter === "1h" ? 3600_000 : timeFilter === "24h" ? 86_400_000 : 604_800_000;
      timeOk = Date.now() - createdMs <= spanMs;
    }
    if (timeOk && dateFrom) timeOk = createdMs >= new Date(dateFrom).getTime();
    if (timeOk && dateTo) timeOk = createdMs <= new Date(dateTo).getTime();
    const topo = deviceTopology.get(alarm.device_id);
    const regionOk = regionFilter === "all" ? true : topo?.regionId === regionFilter;
    const lineOk = lineFilter === "all" ? true : topo?.lineId === lineFilter;
    const deviceOk = deviceFilter === "all" ? true : alarm.device_id === deviceFilter;
    // Durum: acik / onayli / normale-dondu-onay-bekliyor.
    const statusOk =
      statusFilter === "all"
        ? true
        : statusFilter === "pendingAck"
          ? Boolean(alarm.reset) && !alarm.acknowledged
          : statusFilter === "ack"
            ? Boolean(alarm.acknowledged)
            : !alarm.acknowledged && !alarm.reset; // "open"
    const dev = deviceLabelById.get(alarm.device_id);
    const text = `${alarm.title} ${alarm.description} ${alarm.device_id} ${dev?.name ?? ""} ${dev?.code ?? ""}`.toLowerCase();
    const searchOk = search.trim() ? text.includes(search.trim().toLowerCase()) : true;
    return levelOk && assignmentOk && timeOk && regionOk && lineOk && deviceOk && statusOk && searchOk;
  };

  // Aktif (varsayilandan farkli) filtre sayisi — Filtrele butonu rozeti.
  const activeFilterCount =
    (timeFilter !== "all" ? 1 : 0) +
    (dateFrom ? 1 : 0) +
    (dateTo ? 1 : 0) +
    (regionFilter !== "all" ? 1 : 0) +
    (lineFilter !== "all" ? 1 : 0) +
    (deviceFilter !== "all" ? 1 : 0) +
    (levelFilter !== "all" ? 1 : 0) +
    (statusFilter !== "all" ? 1 : 0) +
    (assignmentFilter !== "all" ? 1 : 0);

  const clearAllFilters = () => {
    setTimeFilter("all");
    setDateFrom("");
    setDateTo("");
    setRegionFilter("all");
    setLineFilter("all");
    setDeviceFilter("all");
    setLevelFilter("all");
    setStatusFilter("all");
    setAssignmentFilter("all");
  };

  // Aktif alarmlar: acik VEYA onaylanmis-ama-hala-aktif (reset degil).
  const activeAlarms = useMemo(
    () => alarms.filter((a) => !a.reset && filterPredicate(a)),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [alarms, search, levelFilter, assignmentFilter, timeFilter, dateFrom, dateTo, regionFilter, lineFilter, deviceFilter, statusFilter, deviceLabelById]
  );

  // Normale donenler: reset olmus ama henuz onaylanmamis (gorulmemis).
  const resolvedAlarms = useMemo(
    () => alarms.filter((a) => a.reset && !a.acknowledged && filterPredicate(a)),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [alarms, search, levelFilter, assignmentFilter, timeFilter, dateFrom, dateTo, regionFilter, lineFilter, deviceFilter, statusFilter, deviceLabelById]
  );

  // Gecmis: event log'dan alarm kategorili olaylar (olustu/onaylandi/normale dondu).
  const historyEvents = useMemo(() => {
    const q = search.trim().toLowerCase();
    return events
      .filter((e) => e.category === "alarm")
      .filter((e) => (q ? `${e.message} ${e.device_code ?? ""} ${e.actor_username ?? ""}`.toLowerCase().includes(q) : true))
      .slice(0, 300);
  }, [events, search]);

  // Aktif sekmenin listesi (aktif/normale) — sayfalama icin.
  const tabAlarms = activeTab === "resolved" ? resolvedAlarms : activeAlarms;

  useEffect(() => {
    setPage(1);
  }, [search, levelFilter, assignmentFilter, timeFilter, dateFrom, dateTo, regionFilter, lineFilter, deviceFilter, statusFilter, pageSize, activeTab]);

  const pagedTabAlarms = useMemo(() => {
    const start = (page - 1) * pageSize;
    return tabAlarms.slice(start, start + pageSize);
  }, [tabAlarms, page, pageSize]);

  // Secili yoksa (aktif/normale sekmesinde) ilk alarmi otomatik sec.
  useEffect(() => {
    if (activeTab === "history") return;
    if (selectedAlarmId !== null) return;
    if (tabAlarms.length === 0) return;
    setSelectedAlarmId(tabAlarms[0].id);
  }, [tabAlarms, selectedAlarmId, activeTab]);

  // Filtre popover: dis tik ile kapat.
  useEffect(() => {
    if (!filterOpen) return;
    const onDown = (e: MouseEvent) => {
      if (!filterWrapRef.current?.contains(e.target as Node)) setFilterOpen(false);
    };
    document.addEventListener("mousedown", onDown);
    return () => document.removeEventListener("mousedown", onDown);
  }, [filterOpen]);

  // Secili alarmin yorumlarini yukle.
  useEffect(() => {
    const load = async () => {
      if (!selectedAlarmId) return;
      if (commentsByAlarm[selectedAlarmId]) return;
      try {
        const comments = await onLoadComments(selectedAlarmId);
        setCommentsByAlarm((prev) => ({ ...prev, [selectedAlarmId]: comments }));
      } catch (err) {
        setError(err instanceof Error ? err.message : t("alarms.errors.loadComments"));
      }
    };
    void load();
  }, [commentsByAlarm, onLoadComments, selectedAlarmId]);

  const handleAssign = async (alarmId: number, assignedTo: string) => {
    setSaving(true);
    setError("");
    try {
      await onAssign(alarmId, assignedTo || null);
    } catch (err) {
      setError(err instanceof Error ? err.message : t("alarms.errors.assignFailed"));
    } finally {
      setSaving(false);
    }
  };

  const handleAddComment = async () => {
    if (!selectedAlarmId) return;
    const value = commentDraft.trim();
    if (!value) return;
    setSaving(true);
    setError("");
    try {
      await onAddComment(selectedAlarmId, value);
      const refreshed = await onLoadComments(selectedAlarmId);
      setCommentsByAlarm((prev) => ({ ...prev, [selectedAlarmId]: refreshed }));
      setCommentDraft("");
    } catch (err) {
      setError(err instanceof Error ? err.message : t("alarms.errors.commentFailed"));
    } finally {
      setSaving(false);
    }
  };

  const handleAcknowledge = async (alarmId: number) => {
    const alarm = alarms.find((a) => a.id === alarmId);
    const label = alarm ? `"${alarm.title}"` : t("alarms.confirmAckThis");
    if (!await asyncConfirm(t("alarms.confirmAck", { label }))) return;
    setSaving(true);
    setError("");
    try {
      await onAcknowledge(alarmId);
    } catch (err) {
      setError(err instanceof Error ? err.message : t("alarms.errors.ackFailed"));
    } finally {
      setSaving(false);
    }
  };

  // "Ata ve onayla": atama select bosalmis olsa da secili kullaniciya atar,
  // sonra onaylar. Basit: eger atanmamis + kullanici sec panelinde secim varsa
  // once ata. Burada sadece onayliyoruz; atama ayri select ile yapiliyor.
  const handleAckSelected = async () => {
    if (!selectedAlarm) return;
    await handleAcknowledge(selectedAlarm.id);
  };

  const handleAcknowledgeAll = async () => {
    if (activeAlarms.length === 0) return;
    if (!await asyncConfirm(t("alarms.confirmAckAllActive", { active: activeAlarms.length }))) return;
    setSaving(true);
    setError("");
    try {
      await onAcknowledgeAll();
    } catch (err) {
      setError(err instanceof Error ? err.message : t("alarms.errors.ackAllFailed"));
    } finally {
      setSaving(false);
    }
  };

  const renderAlarmDetail = () => {
    if (!selectedAlarm) {
      return (
        <div className="alarm-detail-empty">
          <span className="material-symbols-outlined">notifications_off</span>
          <h3>{t("alarms.detail.title")}</h3>
          <p className="helper-text">{t("alarms.detail.selectHint")}</p>
          {error ? <p className="error-text">{error}</p> : null}
        </div>
      );
    }
    const a = selectedAlarm;
    const created = new Date(a.created_at);
    const deviceInfo = deviceLabelById.get(a.device_id);
    const source = sourceOf(a.signal_key);
    const comments = commentsByAlarm[a.id] ?? [];
    const state = alarmState(a);
    // Sure: acildigindan bu yana (reset olduysa reset'e kadar).
    const endMs = a.reset && a.reset_at ? new Date(a.reset_at).getTime() : Date.now();
    const duration = formatDuration(endMs - created.getTime());
    const fmtTime = (iso: string) =>
      new Date(iso).toLocaleTimeString(localeTag, { hour: "2-digit", minute: "2-digit" });

    return (
      <div className="alarm-detail">
        {/* Sabit ust: baslik + kapat */}
        <header className="alarm-detail-top">
          <span className="alarm-detail-eyebrow">{t("alarms.detail.title")}</span>
          <button
            type="button"
            className="alarm-detail-close"
            onClick={() => setSelectedAlarmId(null)}
            aria-label={t("common.close")}
          >
            <X size={18} />
          </button>
        </header>

        <div className="alarm-detail-heading">
          <div className="alarm-detail-pills">
            <span className={`alarm-pill level-${a.level.toLowerCase()}`}>{levelLabelTr(a.level)}</span>
            <span className={`alarm-state ${state.klass}`}>{state.label}</span>
          </div>
          <h3 className="alarm-detail-alarmtitle">{a.title}</h3>
          {a.description && a.description.trim() !== a.title.trim() ? (
            <p className="alarm-detail-desc">{a.description}</p>
          ) : null}
          <div className="alarm-detail-sub">
            {deviceInfo ? (
              <>
                <span className="alarm-detail-sub-name">{deviceInfo.name}</span>
                <span className="alarm-detail-sub-code"> · {deviceInfo.code}</span>
              </>
            ) : (
              <span className="alarm-detail-sub-code">#{a.device_id}</span>
            )}
            {source ? <span className="alarm-detail-sub-source"> · {source.label}</span> : null}
          </div>
        </div>

        {/* Sekme cubugu: Detay | Yorumlar */}
        <div className="alarm-detail-tabs" role="tablist">
          <button
            type="button"
            role="tab"
            className={`alarm-detail-tab${panelTab === "detail" ? " active" : ""}`}
            onClick={() => setPanelTab("detail")}
          >
            {t("alarms.detail.tabDetail")}
          </button>
          <button
            type="button"
            role="tab"
            className={`alarm-detail-tab${panelTab === "comments" ? " active" : ""}`}
            onClick={() => setPanelTab("comments")}
          >
            {t("alarms.detail.sectionComments")}
            <span className="alarm-detail-tab-count">{comments.length}</span>
          </button>
        </div>

        {/* Kaydirilabilir govde — aktif sekmeye gore */}
        <div className="alarm-detail-scroll">
        {panelTab === "detail" ? (
        <>
        {/* Bilgi grid: Tarih / Baslangic / Sure / Atanan */}
        <div className="alarm-detail-metrics">
          <div className="alarm-detail-metric">
            <span className="alarm-detail-metric-label">{t("alarms.detail.fieldDate")}</span>
            <span className="alarm-detail-metric-value">{created.toLocaleDateString(localeTag)}</span>
          </div>
          <div className="alarm-detail-metric">
            <span className="alarm-detail-metric-label">{t("alarms.detail.fieldStart")}</span>
            <span className="alarm-detail-metric-value">{fmtTime(a.created_at)}</span>
          </div>
          <div className="alarm-detail-metric">
            <span className="alarm-detail-metric-label">{t("alarms.detail.duration")}</span>
            <span className="alarm-detail-metric-value">{duration}</span>
          </div>
          <div className="alarm-detail-metric">
            <span className="alarm-detail-metric-label">{t("alarms.detail.assignee")}</span>
            <span className="alarm-detail-metric-value">
              {a.assigned_to ?? t("alarms.detail.assignNone")}
            </span>
          </div>
        </div>

        {/* Sorumluya ata */}
        <label className="alarm-detail-assign">
          <span>{t("alarms.detail.assignTo")}</span>
          <select
            disabled={saving}
            value={a.assigned_to ?? ""}
            onChange={(e) => void handleAssign(a.id, e.target.value)}
          >
            <option value="">{t("alarms.detail.assignNone")}</option>
            {users.map((u) => (
              <option key={u.id} value={u.username}>{u.full_name}</option>
            ))}
          </select>
        </label>

        {/* Aksiyonlar */}
        <div className="alarm-detail-cta">
          {!a.acknowledged ? (
            <button
              type="button"
              className="alarm-detail-cta-primary"
              disabled={saving}
              onClick={() => void handleAckSelected()}
            >
              <Check size={18} />
              {t("alarms.actions.acknowledge")}
            </button>
          ) : null}
          <button
            type="button"
            className="alarm-detail-cta-ghost"
            onClick={() => onOpenDevice(a.device_id)}
          >
            <ExternalLink size={17} />
            {t("alarms.detail.openDevice")}
          </button>
        </div>

        {/* Durum gecmisi (timeline) */}
        <div className="alarm-detail-timeline">
          <span className="alarm-detail-section-title">{t("alarms.detail.history")}</span>
          <ul className="alarm-timeline">
            <li className="alarm-timeline-item is-created">
              <span className="alarm-timeline-dot" />
              <div className="alarm-timeline-body">
                <span className="alarm-timeline-time">{fmtTime(a.created_at)}</span>
                <span className="alarm-timeline-label">{t("alarms.detail.historyCreated")}</span>
              </div>
            </li>
            {a.acknowledged && a.acknowledged_at ? (
              <li className="alarm-timeline-item is-ack">
                <span className="alarm-timeline-dot" />
                <div className="alarm-timeline-body">
                  <span className="alarm-timeline-time">{fmtTime(a.acknowledged_at)}</span>
                  <span className="alarm-timeline-label">{t("alarms.detail.historyAck")}</span>
                </div>
              </li>
            ) : null}
            {a.reset && a.reset_at ? (
              <li className="alarm-timeline-item is-reset">
                <span className="alarm-timeline-dot" />
                <div className="alarm-timeline-body">
                  <span className="alarm-timeline-time">{fmtTime(a.reset_at)}</span>
                  <span className="alarm-timeline-label">{t("alarms.detail.historyReset")}</span>
                </div>
              </li>
            ) : null}
            {!a.acknowledged ? (
              <li className="alarm-timeline-item is-pending">
                <span className="alarm-timeline-dot" />
                <div className="alarm-timeline-body">
                  <span className="alarm-timeline-label">{t("alarms.detail.historyPending")}</span>
                </div>
              </li>
            ) : null}
          </ul>
        </div>
        </>
        ) : (
        /* ---- Yorumlar sekmesi ---- */
        <div className="alarm-detail-comments">
          <div className="alarm-detail-comments-list">
            {comments.map((c) => (
              <div key={c.id} className="alarm-comment-card">
                <div className="alarm-comment-card-meta">
                  <span className="alarm-comment-card-avatar">{c.author_username.slice(0, 1).toUpperCase()}</span>
                  <div className="alarm-comment-card-meta-text">
                    <strong>{c.author_username}</strong>
                    <span>{new Date(c.created_at).toLocaleString(localeTag)}</span>
                  </div>
                </div>
                <p className="alarm-comment-card-body">{c.comment}</p>
              </div>
            ))}
            {comments.length === 0 ? (
              <p className="alarm-detail-comments-empty">{t("alarms.detail.commentsEmpty")}</p>
            ) : null}
          </div>
          <div className="alarm-detail-comment-form">
            <textarea
              placeholder={t("alarms.detail.commentPlaceholder")}
              value={commentDraft}
              onChange={(e) => setCommentDraft(e.target.value)}
              rows={2}
            />
            <button
              type="button"
              className="primary-btn"
              disabled={saving || !commentDraft.trim()}
              onClick={() => void handleAddComment()}
            >
              {saving ? t("alarms.detail.savingComment") : t("alarms.detail.saveComment")}
            </button>
          </div>
        </div>
        )}
        </div>

        {error ? <p className="error-text">{error}</p> : null}
      </div>
    );
  };

  return (
    <section className="alarms-layout alarms-layout-split">
      <div className="alarms-main">
        <div className="alarms-toolbar alarms-page-toolbar">
          <input
            className="device-search-input"
            placeholder={t("alarms.search")}
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />
          <div className="alarms-filter-row">
            {/* Filtrele: acilir panel (tarih araligi + bolge + hat + cihaz + seviye + durum + atama) */}
            <div className="alarms-filter-wrap" ref={filterWrapRef}>
              <button
                type="button"
                className={`alarms-filter-btn${activeFilterCount > 0 ? " has-active" : ""}${filterOpen ? " open" : ""}`}
                onClick={() => setFilterOpen((o) => !o)}
              >
                <SlidersHorizontal size={16} />
                {t("alarms.filterBtn")}
                {activeFilterCount > 0 ? <span className="alarms-filter-badge">{activeFilterCount}</span> : null}
              </button>
              {filterOpen ? (
                <div className="alarms-filter-panel">
                  <div className="alarms-filter-panel-head">
                    <span>{t("alarms.filterBtn")}</span>
                    {activeFilterCount > 0 ? (
                      <button type="button" className="alarms-filter-clear" onClick={clearAllFilters}>
                        {t("alarms.filterClear")}
                      </button>
                    ) : null}
                  </div>
                  {/* Tarih araligi */}
                  <div className="alarms-filter-field">
                    <label>{t("alarms.filter.dateRange")}</label>
                    <div className="alarms-filter-daterow">
                      <input type="datetime-local" value={dateFrom} onChange={(e) => setDateFrom(e.target.value)} aria-label={t("alarms.filter.dateFrom")} />
                      <span>→</span>
                      <input type="datetime-local" value={dateTo} onChange={(e) => setDateTo(e.target.value)} aria-label={t("alarms.filter.dateTo")} />
                    </div>
                    <div className="alarms-filter-quick">
                      {(["all", "1h", "24h", "7d"] as TimeFilter[]).map((tf) => (
                        <button
                          key={tf}
                          type="button"
                          className={`alarms-filter-quick-btn${timeFilter === tf ? " active" : ""}`}
                          onClick={() => { setTimeFilter(tf); setDateFrom(""); setDateTo(""); }}
                        >
                          {t(`alarms.filter.time${tf === "all" ? "All" : tf === "1h" ? "1h" : tf === "24h" ? "24h" : "7d"}`)}
                        </button>
                      ))}
                    </div>
                  </div>
                  {/* Bolge */}
                  <div className="alarms-filter-field">
                    <label>{t("alarms.filter.region")}</label>
                    <select
                      value={regionFilter === "all" ? "all" : String(regionFilter)}
                      onChange={(e) => { setRegionFilter(e.target.value === "all" ? "all" : Number(e.target.value)); setLineFilter("all"); }}
                    >
                      <option value="all">{t("alarms.filter.regionAll")}</option>
                      {regions.map((r) => (<option key={r.id} value={r.id}>{r.name}</option>))}
                    </select>
                  </div>
                  {/* Hat (secili bolgeye gore filtreli) */}
                  <div className="alarms-filter-field">
                    <label>{t("alarms.filter.line")}</label>
                    <select
                      value={lineFilter === "all" ? "all" : String(lineFilter)}
                      onChange={(e) => setLineFilter(e.target.value === "all" ? "all" : Number(e.target.value))}
                    >
                      <option value="all">{t("alarms.filter.lineAll")}</option>
                      {lines
                        .filter((l) => regionFilter === "all" || l.region_id === regionFilter)
                        .map((l) => (<option key={l.id} value={l.id}>{l.name}</option>))}
                    </select>
                  </div>
                  {/* Cihaz */}
                  <div className="alarms-filter-field">
                    <label>{t("alarms.filter.device")}</label>
                    <select
                      value={deviceFilter === "all" ? "all" : String(deviceFilter)}
                      onChange={(e) => setDeviceFilter(e.target.value === "all" ? "all" : Number(e.target.value))}
                    >
                      <option value="all">{t("alarms.filter.deviceAll")}</option>
                      {devices.map((d) => (<option key={d.id} value={d.id}>{d.name}</option>))}
                    </select>
                  </div>
                  {/* Seviye + Durum + Atama */}
                  <div className="alarms-filter-field">
                    <label>{t("alarms.table.level")}</label>
                    <select value={levelFilter} onChange={(e) => setLevelFilter(e.target.value as typeof levelFilter)}>
                      <option value="all">{t("alarms.filterAllLevels")}</option>
                      <option value="critical">{t("alarms.level.critical")}</option>
                      <option value="warning">{t("alarms.level.warning")}</option>
                      <option value="info">{t("alarms.level.info")}</option>
                    </select>
                  </div>
                  <div className="alarms-filter-field">
                    <label>{t("alarms.table.status")}</label>
                    <select value={statusFilter} onChange={(e) => setStatusFilter(e.target.value as StatusFilter)}>
                      <option value="all">{t("alarms.filter.statusAll")}</option>
                      <option value="open">{t("alarms.filter.statusOpen")}</option>
                      <option value="ack">{t("alarms.filter.statusAck")}</option>
                      <option value="pendingAck">{t("alarms.state.pendingAck")}</option>
                    </select>
                  </div>
                  <div className="alarms-filter-field">
                    <label>{t("alarms.table.assignee")}</label>
                    <select value={assignmentFilter} onChange={(e) => setAssignmentFilter(e.target.value as typeof assignmentFilter)}>
                      <option value="all">{t("alarms.filterAllAssignments")}</option>
                      <option value="assigned">{t("alarms.assigned")}</option>
                      <option value="unassigned">{t("alarms.unassigned")}</option>
                    </select>
                  </div>
                </div>
              ) : null}
            </div>
            <button
              type="button"
              className="secondary-btn action-btn"
              disabled={saving || activeAlarms.length === 0}
              onClick={() => void handleAcknowledgeAll()}
              title={t("alarms.ackAllTooltip")}
            >
              {t("alarms.ackAll")}
            </button>
          </div>
        </div>

        {/* Sekme cubugu: Aktif / Normale Donenler / Gecmis */}
        <div className="alarms-section">
          <div className="alarms-tabs" role="tablist">
            <button
              type="button"
              role="tab"
              className={`alarms-tab${activeTab === "active" ? " active" : ""}`}
              onClick={() => setActiveTab("active")}
            >
              {t("alarms.tabs.active")}
              <span className="alarms-tab-count">{activeAlarms.length}</span>
            </button>
            <button
              type="button"
              role="tab"
              className={`alarms-tab${activeTab === "resolved" ? " active" : ""}`}
              onClick={() => setActiveTab("resolved")}
            >
              {t("alarms.tabs.resolved")}
              <span className="alarms-tab-count">{resolvedAlarms.length}</span>
            </button>
            <button
              type="button"
              role="tab"
              className={`alarms-tab${activeTab === "history" ? " active" : ""}`}
              onClick={() => setActiveTab("history")}
            >
              {t("alarms.tabs.history")}
            </button>
          </div>

          {activeTab === "history" ? (
            /* ---- Gecmis: event log ---- */
            <div className="alarms-table-wrap alarms-page-table-wrap">
              <table className="values-table alarms-page-table">
                <thead>
                  <tr>
                    <th scope="col">{t("alarms.table.date")}</th>
                    <th scope="col">{t("alarms.history.colEvent")}</th>
                    <th scope="col">{t("alarms.table.device")}</th>
                    <th scope="col">{t("alarms.history.colDetail")}</th>
                    <th scope="col">{t("alarms.history.colWho")}</th>
                  </tr>
                </thead>
                <tbody>
                  {historyEvents.map((ev) => {
                    const created = new Date(ev.created_at);
                    return (
                      <tr key={ev.id} className="alarm-row alarm-history-row">
                        <td className="alarm-cell-date">
                          <div className="alarm-date">{created.toLocaleDateString(localeTag)}</div>
                          <div className="alarm-time">{created.toLocaleTimeString(localeTag, { hour: "2-digit", minute: "2-digit", second: "2-digit" })}</div>
                        </td>
                        <td className="alarm-cell-event">
                          <span className={`alarm-event-badge ev-${ev.event_type}`}>
                            {t(`alarms.eventType.${ev.event_type}`, ev.event_type)}
                          </span>
                        </td>
                        <td className="alarm-cell-device">
                          {ev.device_code ? <span className="alarm-device-code">{ev.device_code}</span> : <span className="alarm-cell-empty">—</span>}
                        </td>
                        <td className="alarm-cell-title">
                          <div className="alarm-title-text" title={ev.message}>{ev.message}</div>
                        </td>
                        <td className="alarm-cell-assignee">{ev.actor_username ?? <span className="alarm-cell-empty">—</span>}</td>
                      </tr>
                    );
                  })}
                  {historyEvents.length === 0 ? (
                    <tr><td colSpan={5} className="alarms-empty-cell">{t("alarms.history.empty")}</td></tr>
                  ) : null}
                </tbody>
              </table>
            </div>
          ) : (
            /* ---- Aktif / Normale Donenler ---- */
            <div className="alarms-table-wrap alarms-page-table-wrap">
              <table className="values-table alarms-page-table">
                <thead>
                  <tr>
                    <th scope="col">{t("alarms.table.date")}</th>
                    <th scope="col">{t("alarms.table.level")}</th>
                    <th scope="col">{t("alarms.table.deviceSource")}</th>
                    <th scope="col">{t("alarms.table.alarm")}</th>
                    <th scope="col">{t("alarms.table.status")}</th>
                    <th scope="col">{t("alarms.table.assignee")}</th>
                    <th scope="col">{t("alarms.table.duration")}</th>
                    <th scope="col" className="alarm-actions-th">{t("alarms.table.actions")}</th>
                  </tr>
                </thead>
                <tbody>
                  {pagedTabAlarms.map((alarm) => {
                    const levelClass = `alarm-row-level-${alarm.level.toLowerCase()}`;
                    const selectedClass = selectedAlarmId === alarm.id ? "alarm-row-active" : "";
                    const created = new Date(alarm.created_at);
                    const state = alarmState(alarm);
                    const rowDuration = formatDuration(Date.now() - created.getTime());
                    return (
                      <tr
                        key={alarm.id}
                        className={`alarm-row ${levelClass} ${selectedClass}`.trim()}
                        onClick={() => setSelectedAlarmId(alarm.id)}
                      >
                        <td className="alarm-cell-date">
                          <div className="alarm-date">{created.toLocaleDateString(localeTag)}</div>
                          <div className="alarm-time">{created.toLocaleTimeString(localeTag, { hour: "2-digit", minute: "2-digit", second: "2-digit" })}</div>
                        </td>
                        <td className="alarm-cell-level">
                          <span className={`alarm-pill level-${alarm.level.toLowerCase()}`}>{levelLabelTr(alarm.level)}</span>
                        </td>
                        <td className="alarm-cell-devsource">{renderDeviceSourceCell(alarm.device_id, alarm.signal_key)}</td>
                        <td className="alarm-cell-title">
                          <div className="alarm-title-text" title={alarm.description || alarm.title}>{alarm.title}</div>
                        </td>
                        <td className="alarm-cell-state">
                          <span className={`alarm-state ${state.klass}`}>{state.label}</span>
                        </td>
                        <td className="alarm-cell-assignee">{alarm.assigned_to ?? <span className="alarm-cell-empty">—</span>}</td>
                        <td className="alarm-cell-duration">{rowDuration}</td>
                        <td className="actions-cell alarm-actions-cell">
                          <div className="alarm-row-actions">
                            {/* Hizli islem: onaysizsa Onayla; her zaman Incele (panel acar) */}
                            {!alarm.acknowledged ? (
                              <button
                                type="button"
                                className="alarm-row-ack"
                                onClick={(e) => { e.stopPropagation(); setSelectedAlarmId(alarm.id); void handleAcknowledge(alarm.id); }}
                                title={t("alarms.actions.acknowledge")}
                              >
                                <Check size={15} />
                                {t("alarms.actions.acknowledge")}
                              </button>
                            ) : null}
                            <button
                              type="button"
                              className="alarm-row-inspect"
                              onClick={(e) => { e.stopPropagation(); setSelectedAlarmId(alarm.id); }}
                            >
                              {t("alarms.actions.inspect")}
                            </button>
                          </div>
                        </td>
                      </tr>
                    );
                  })}
                  {tabAlarms.length === 0 && !loading ? (
                    <tr>
                      <td colSpan={8} className="alarms-empty-cell">
                        {activeTab === "resolved" ? t("alarms.noPending") : t("alarms.noActive")}
                      </td>
                    </tr>
                  ) : null}
                </tbody>
              </table>
            </div>
          )}
          {activeTab !== "history" && tabAlarms.length > pageSize ? (
            <TablePagination
              totalItems={tabAlarms.length}
              page={page}
              pageSize={pageSize}
              onPageChange={setPage}
              onPageSizeChange={setPageSize}
              itemLabel={t("alarms.itemLabel")}
            />
          ) : null}
        </div>
      </div>

      {/* SAG: Sabit alarm detay paneli */}
      <aside className="alarms-side-panel">
        {renderAlarmDetail()}
      </aside>
    </section>
  );
}
