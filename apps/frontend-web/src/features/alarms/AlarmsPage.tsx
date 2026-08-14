import { useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import {
  Calendar, Check, CircleAlert, CircleCheck, CircleDot, Clock, ExternalLink,
  GitBranch, MapPin, SlidersHorizontal, Timer, User, X, type LucideIcon
} from "lucide-react";

import { asyncConfirm } from "../../components/ConfirmDialog";
import { TablePagination } from "../../components/TablePagination";
import type { AlarmComment, AlarmEvent, DeviceRow, Line, Region, SystemEvent, UserRead } from "../../shared/types";
import { formatEventMessage } from "../events/formatEventMessage";

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
  /** Alarm listesi CEKILEMEDIYSE dolu. Bos liste ile "veri yok" ayni sey
   *  degil — bkz. asagidaki hata dali. (Bilesenin kendi `error` state'i
   *  yorum kaydetme hatasi icin; ikisi ayri seyler.) */
  loadError?: string;
  onAssign: (alarmId: number, assignedTo: string | null) => Promise<void>;
  onLoadComments: (alarmId: number) => Promise<AlarmComment[]>;
  onAddComment: (alarmId: number, comment: string) => Promise<void>;
  onAcknowledge: (alarmId: number) => Promise<void>;
  /** `only` HANGI KUME onaylanacagini soyler; bkz. handleAcknowledgeAll. */
  onAcknowledgeAll: (only?: "active" | "resolved") => Promise<void>;
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
  loadError,
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

  /** Cihaz hucresi: ad ustte, kod altta (ortali). */
  const renderDeviceCell = (deviceId: number) => {
    const info = deviceLabelById.get(deviceId);
    if (!info) return <span className="alarm-device-fallback">#{deviceId}</span>;
    return (
      <div className="alarm-device-cell">
        <span className="alarm-device-name">{info.name}</span>
        <span className="alarm-device-code">{info.code}</span>
      </div>
    );
  };

  /** Kaynak hucresi: Master/Sat renkli rozeti. */
  const renderSourceCell = (signalKey: string | null | undefined) => {
    const entry = sourceOf(signalKey);
    if (!entry) return <span className="alarm-cell-empty">—</span>;
    return <span className={`badge badge-source badge-source-${entry.klass}`}>{entry.label}</span>;
  };

  // Olay kaydi cihazi KOD ile tasir (id degil) — gecmis sekmesinde ham kodu
  // ("7", "SN2_0") gostermek yerine cihaz adiyla eslestirmek icin.
  const deviceByCode = useMemo(() => {
    const map = new Map<string, DeviceRow>();
    for (const dev of devices) map.set(dev.code, dev);
    return map;
  }, [devices]);

  /** Gecmis satirinin cihaz hucresi: ad ustte, kod altta.
   *  Eslesme yoksa ham kod kalir — kayit silinmis bir cihaza ait olabilir. */
  const renderHistoryDeviceCell = (code: string | null | undefined) => {
    if (!code) return <span className="alarm-cell-empty">—</span>;
    const dev = deviceByCode.get(code);
    if (!dev) return <span className="alarm-device-code">{code}</span>;
    return (
      <div className="alarm-device-cell">
        <span className="alarm-device-name">{dev.name}</span>
        <span className="alarm-device-code">{dev.code}</span>
      </div>
    );
  };

  /** Olayin sinyal anahtari — `alarm_created` metadata'sinda tasinir
   *  (bkz. alarm_engine_service). Master/uydu rozetinin kaynagi bu; alarm
   *  hangi uniteden geldi sorusu gecmiste hic cevaplanmiyordu. */
  const eventSignalKey = (ev: SystemEvent): string | null => {
    if (!ev.metadata_json) return null;
    try {
      const parsed = JSON.parse(ev.metadata_json) as { signal_key?: unknown };
      return typeof parsed?.signal_key === "string" ? parsed.signal_key : null;
    } catch {
      return null; // bozuk metadata — rozet yok, satirin geri kalani calisir
    }
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
  const alarmState = (a: AlarmEvent): { label: string; klass: string; Icon: LucideIcon } => {
    if (a.reset && !a.acknowledged) return { label: t("alarms.state.pendingAck"), klass: "state-pending", Icon: CircleDot };
    if (a.acknowledged) return { label: t("alarms.stateAck"), klass: "state-ack", Icon: CircleCheck };
    return { label: t("alarms.stateOpen"), klass: "state-open", Icon: CircleAlert };
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

  /** Toplu onay — HANGI KUME oldugu acikca secilir.
   *
   *  Tek bir "Tumunu Onayla" dugmesi vardi ve iki farkli isi ayni tikla
   *  yapiyordu: normale donmus kayitlari arsivlemek (dongu bitti, liste
   *  temizlensin) ve SAHADA DEVAM EDEN alarmlari "gordum" diye isaretlemek.
   *  Listeyi toparlamak isteyen operator, farkinda olmadan suren alarmlari da
   *  gorulmus sayiyordu — o kayitlar bir daha kimsenin dikkatini cekmiyordu.
   *
   *  NOT: kapsam SUNUCUDAKI gorunur kumedir, ekrandaki filtre degil. Onay
   *  metni bu yuzden sayfadaki sayiyi degil yapilacak isi anlatir. */
  const handleAcknowledgeAll = async (only: "active" | "resolved") => {
    const sayi = only === "resolved" ? resolvedAlarms.length : activeAlarms.length;
    if (sayi === 0) return;
    const soru =
      only === "resolved"
        ? t("alarms.confirmAckAllResolved", { count: sayi })
        : t("alarms.confirmAckAllActive", { active: sayi });
    if (!await asyncConfirm(soru)) return;
    setSaving(true);
    setError("");
    try {
      await onAcknowledgeAll(only);
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
    const fmtDate = (iso: string) =>
      new Date(iso).toLocaleDateString(localeTag, { day: "2-digit", month: "2-digit", year: "numeric" });

    // ---- Tekrar sikl igi: son 30 gunde bu cihaz+baslik alarmi gun gun kac kez (heatmap) ----
    // Kaynak: event log (alarm_triggered/created) + mevcut aktif alarm listesi.
    const repeatDays = 30;
    const dayMs = 86_400_000;
    const todayStart = (() => { const d = new Date(); d.setHours(0, 0, 0, 0); return d.getTime(); })();
    const startMs = todayStart - (repeatDays - 1) * dayMs;
    const counts = new Array(repeatDays).fill(0);
    const bump = (ts: number) => {
      if (ts < startMs) return;
      const idx = Math.floor((ts - startMs) / dayMs);
      if (idx >= 0 && idx < repeatDays) counts[idx] += 1;
    };
    // Event log: bu cihaza ait alarm-olustu olaylari.
    for (const ev of events) {
      if (ev.category !== "alarm") continue;
      if (ev.event_type !== "alarm_triggered" && ev.event_type !== "alarm_created") continue;
      if (deviceInfo && ev.device_code && ev.device_code !== deviceInfo.code) continue;
      bump(new Date(ev.created_at).getTime());
    }
    // Ayni cihaz+baslik aktif alarmlari (event log eksikse tamamla).
    for (const al of alarms) {
      if (al.device_id === a.device_id && al.title === a.title) bump(new Date(al.created_at).getTime());
    }
    const totalRepeat = counts.reduce((s, n) => s + n, 0);
    const maxCount = Math.max(1, ...counts);

    // ---- Durum gecmisi: event log'dan bu alarma ait olaylar (yorum + atama dahil) ----
    // metadata_json icindeki alarm_id === a.id eslesmesi. Alarm olusma (created_at)
    // her zaman ilk, digerleri kronolojik.
    type TlItem = { ts: number; label: string; kind: string };
    const tlItems: TlItem[] = [
      { ts: created.getTime(), label: t("alarms.detail.historyCreated"), kind: "created" },
    ];
    for (const ev of events) {
      if (ev.category !== "alarm") continue;
      let evAlarmId: number | null = null;
      if (ev.metadata_json) {
        try { evAlarmId = (JSON.parse(ev.metadata_json) as { alarm_id?: number }).alarm_id ?? null; } catch { /* yut */ }
      }
      if (evAlarmId !== a.id) continue;
      const ts = new Date(ev.created_at).getTime();
      const who = ev.actor_username ? ` · ${ev.actor_username}` : "";
      if (ev.event_type === "alarm_acknowledged") tlItems.push({ ts, label: t("alarms.detail.historyAck") + who, kind: "ack" });
      else if (ev.event_type === "alarm_assigned") tlItems.push({ ts, label: t("alarms.detail.historyAssigned") + who, kind: "assign" });
      else if (ev.event_type === "alarm_comment_added") tlItems.push({ ts, label: t("alarms.detail.historyComment") + who, kind: "comment" });
      else if (ev.event_type === "alarm_reset" || ev.event_type === "alarm_auto_cleared") tlItems.push({ ts, label: t("alarms.detail.historyReset") + who, kind: "reset" });
    }
    // Event log eksikse alarmin kendi damgalarindan tamamla (dedup).
    if (a.acknowledged && a.acknowledged_at && !tlItems.some((x) => x.kind === "ack")) {
      tlItems.push({ ts: new Date(a.acknowledged_at).getTime(), label: t("alarms.detail.historyAck"), kind: "ack" });
    }
    if (a.reset && a.reset_at && !tlItems.some((x) => x.kind === "reset")) {
      tlItems.push({ ts: new Date(a.reset_at).getTime(), label: t("alarms.detail.historyReset"), kind: "reset" });
    }
    tlItems.sort((x, y) => x.ts - y.ts);

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

        {/* Seviye seridi + baslik */}
        <div className={`alarm-detail-heading level-band-${a.level.toLowerCase()}`}>
          <div className="alarm-detail-pills">
            <span className={`alarm-pill level-${a.level.toLowerCase()}`}>{levelLabelTr(a.level)}</span>
            <span className={`alarm-state ${state.klass}`}>
              <state.Icon size={13} />
              {state.label}
            </span>
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
        {/* Bilgi kartlari: ikonlu 2x2 (Tarih / Baslangic / Sure / Atanan) */}
        <div className="alarm-detail-metrics">
          <div className="alarm-detail-metric">
            <span className="alarm-detail-metric-icon"><Calendar size={15} /></span>
            <div className="alarm-detail-metric-body">
              <span className="alarm-detail-metric-label">{t("alarms.detail.fieldDate")}</span>
              <span className="alarm-detail-metric-value">{created.toLocaleDateString(localeTag)}</span>
            </div>
          </div>
          <div className="alarm-detail-metric">
            <span className="alarm-detail-metric-icon"><Clock size={15} /></span>
            <div className="alarm-detail-metric-body">
              <span className="alarm-detail-metric-label">{t("alarms.detail.fieldStart")}</span>
              <span className="alarm-detail-metric-value">{fmtTime(a.created_at)}</span>
            </div>
          </div>
          <div className="alarm-detail-metric">
            <span className="alarm-detail-metric-icon"><Timer size={15} /></span>
            <div className="alarm-detail-metric-body">
              <span className="alarm-detail-metric-label">{t("alarms.detail.duration")}</span>
              <span className="alarm-detail-metric-value">{duration}</span>
            </div>
          </div>
          <div className="alarm-detail-metric">
            <span className="alarm-detail-metric-icon"><User size={15} /></span>
            <div className="alarm-detail-metric-body">
              <span className="alarm-detail-metric-label">{t("alarms.detail.assignee")}</span>
              <span className="alarm-detail-metric-value">{a.assigned_to ?? t("alarms.detail.assignNone")}</span>
            </div>
          </div>
        </div>

        {/* Konum: bolge · hat (varsa) */}
        {(() => {
          const topo = deviceTopology.get(a.device_id);
          if (!topo?.regionName && !topo?.lineName) return null;
          return (
            <div className="alarm-detail-location">
              {topo.regionName ? (
                <span className="alarm-detail-loc-item"><MapPin size={14} />{topo.regionName}</span>
              ) : null}
              {topo.lineName ? (
                <span className="alarm-detail-loc-item"><GitBranch size={14} />{topo.lineName}</span>
              ) : null}
            </div>
          );
        })()}

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

        {/* Tekrar sikligi heatmap (son 30 gun, GitHub katki grafigi tarzi) */}
        <div className="alarm-detail-repeat">
          <div className="alarm-detail-repeat-head">
            <span className="alarm-detail-section-title">{t("alarms.detail.repeatTitle")}</span>
            <span className="alarm-detail-repeat-total">{t("alarms.detail.repeatCount", { count: totalRepeat })}</span>
          </div>
          <div className="alarm-heatmap" title={t("alarms.detail.repeatHint", { days: repeatDays })}>
            {counts.map((c, i) => {
              const dayTs = startMs + i * dayMs;
              const lvl = c === 0 ? 0 : c >= maxCount ? 4 : Math.ceil((c / maxCount) * 3);
              return (
                <span
                  key={i}
                  className={`alarm-heatmap-cell hm-${lvl}`}
                  title={`${new Date(dayTs).toLocaleDateString(localeTag)} — ${c}`}
                />
              );
            })}
          </div>
          <div className="alarm-heatmap-legend">
            <span>{t("alarms.detail.repeatLess")}</span>
            <span className="alarm-heatmap-cell hm-0" />
            <span className="alarm-heatmap-cell hm-1" />
            <span className="alarm-heatmap-cell hm-2" />
            <span className="alarm-heatmap-cell hm-3" />
            <span className="alarm-heatmap-cell hm-4" />
            <span>{t("alarms.detail.repeatMore")}</span>
          </div>
        </div>

        {/* Durum gecmisi (timeline) */}
        <div className="alarm-detail-timeline">
          <span className="alarm-detail-section-title">{t("alarms.detail.history")}</span>
          <ul className="alarm-timeline">
            {tlItems.map((item, i) => (
              <li key={i} className={`alarm-timeline-item is-${item.kind}`}>
                <span className="alarm-timeline-dot" />
                <div className="alarm-timeline-body">
                  <span className="alarm-timeline-time">{fmtDate(new Date(item.ts).toISOString())} · {fmtTime(new Date(item.ts).toISOString())}</span>
                  <span className="alarm-timeline-label">{item.label}</span>
                </div>
              </li>
            ))}
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
            {/* IKI AYRI DUGME: "arsivle" ile "gordum" ayni is degil.
                Once tek dugme ikisini birden yapiyordu — bkz.
                handleAcknowledgeAll. Normale donenler once geliyor: gunluk
                kullanimda sik olan ve risksiz olan o. */}
            <button
              type="button"
              className="secondary-btn action-btn"
              disabled={saving || resolvedAlarms.length === 0}
              onClick={() => void handleAcknowledgeAll("resolved")}
              title={t("alarms.ackResolvedTooltip")}
            >
              {t("alarms.ackResolved")}
              {resolvedAlarms.length > 0 ? (
                <span className="action-btn-count">{resolvedAlarms.length}</span>
              ) : null}
            </button>
            <button
              type="button"
              className="secondary-btn action-btn"
              disabled={saving || activeAlarms.length === 0}
              onClick={() => void handleAcknowledgeAll("active")}
              title={t("alarms.ackActiveTooltip")}
            >
              {t("alarms.ackActive")}
              {activeAlarms.length > 0 ? (
                <span className="action-btn-count">{activeAlarms.length}</span>
              ) : null}
            </button>
          </div>
        </div>

        {/* Sekme cubugu: Aktif / Normale Donenler / Gecmis */}
        <div className="alarms-section">
          {/* Elde ESKI liste varken cekim basarisiz olursa sayilar guncel
              gorunur ama degildir. Uyari YALNIZCA sorun varken cikar; normal
              calismada ust seritte yer kaplamaz. */}
          {loadError && tabAlarms.length > 0 ? (
            <div className="alarms-stale" role="status" title={loadError}>
              {t("alarms.staleWarning")}
            </div>
          ) : null}
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
                    <th scope="col" className="alarm-col-date">{t("alarms.table.date")}</th>
                    <th scope="col" className="alarm-col-status">{t("alarms.history.colEvent")}</th>
                    <th scope="col" className="alarm-col-device">{t("alarms.table.device")}</th>
                    {/* KAYNAK: alarm hangi uniteden (master/uydu) geldi.
                        Gecmiste hic gorunmuyordu; ayni cihazin uc unitesi
                        ayri fazlara bagli oldugu icin bu ayrim ariza sebebi
                        cikariminin girdisi. */}
                    <th scope="col" className="alarm-col-source">{t("alarms.table.source")}</th>
                    <th scope="col" className="alarm-col-alarm">{t("alarms.history.colDetail")}</th>
                    <th scope="col" className="alarm-col-assignee">{t("alarms.history.colWho")}</th>
                  </tr>
                </thead>
                <tbody>
                  {historyEvents.map((ev) => {
                    const created = new Date(ev.created_at);
                    // Mesaj i18n'den gecirilir: kayitlarin bir kismi backend'de
                    // Ingilizce uretilmis ("Alarm rule triggered: ...") ve
                    // gecmis sekmesi ham metni basiyordu — ayni ekranda iki dil.
                    const mesaj = formatEventMessage(ev);
                    return (
                      <tr key={ev.id} className={`alarm-row alarm-history-row ev-row-${ev.event_type}`}>
                        <td className="alarm-cell-date">
                          <div className="alarm-date">{created.toLocaleDateString(localeTag)}</div>
                          <div className="alarm-time">{created.toLocaleTimeString(localeTag, { hour: "2-digit", minute: "2-digit", second: "2-digit" })}</div>
                        </td>
                        <td className="alarm-cell-event">
                          <span className={`alarm-event-badge ev-${ev.event_type}`}>
                            {t(`alarms.eventType.${ev.event_type}`, ev.event_type)}
                          </span>
                        </td>
                        <td className="alarm-cell-device">{renderHistoryDeviceCell(ev.device_code)}</td>
                        <td className="alarm-cell-source">{renderSourceCell(eventSignalKey(ev))}</td>
                        <td className="alarm-cell-title">
                          <div className="alarm-title-text" title={mesaj}>{mesaj}</div>
                        </td>
                        <td className="alarm-cell-assignee">
                          {ev.actor_username ? (
                            <span className="alarm-history-actor">{ev.actor_username}</span>
                          ) : (
                            /* Aktoru olmayan satir SISTEM uretimi: kural motoru
                               ya da otomatik temizleme. "—" bunu soylemiyordu. */
                            <span className="alarm-history-system">{t("alarms.history.systemActor")}</span>
                          )}
                        </td>
                      </tr>
                    );
                  })}
                  {historyEvents.length === 0 ? (
                    <tr><td colSpan={6} className="alarms-empty-cell">{t("alarms.history.empty")}</td></tr>
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
                    <th scope="col" className="alarm-col-level">{t("alarms.table.level")}</th>
                    <th scope="col" className="alarm-col-date">{t("alarms.table.date")}</th>
                    <th scope="col" className="alarm-col-device">{t("alarms.table.device")}</th>
                    <th scope="col" className="alarm-col-source">{t("alarms.table.source")}</th>
                    <th scope="col" className="alarm-col-line">{t("alarms.table.line")}</th>
                    <th scope="col" className="alarm-col-region">{t("alarms.table.region")}</th>
                    <th scope="col" className="alarm-col-alarm">{t("alarms.table.alarm")}</th>
                    <th scope="col" className="alarm-col-status">{t("alarms.table.status")}</th>
                    <th scope="col" className="alarm-col-assignee">{t("alarms.table.assignee")}</th>
                    <th scope="col" className="alarm-col-duration">{t("alarms.table.duration")}</th>
                    <th scope="col" className="alarm-actions-th">{t("alarms.table.actions")}</th>
                  </tr>
                </thead>
                <tbody>
                  {pagedTabAlarms.map((alarm) => {
                    const levelClass = `alarm-row-level-${alarm.level.toLowerCase()}`;
                    const selectedClass = selectedAlarmId === alarm.id ? "alarm-row-active" : "";
                    const created = new Date(alarm.created_at);
                    const state = alarmState(alarm);
                    // SURE NORMALE DONUSTE DURUR. Satir her zaman "simdi"ye
                    // gore hesapliyordu: sahada bitmis bir alarmin suresi
                    // ekranda saymaya devam ediyor, 17 saatlik bir kesinti
                    // gibi gorunuyordu. Bitmis kaydin suresi olaya aittir,
                    // ekrana bakma anina degil (detay paneli zaten boyle).
                    const rowEndMs =
                      alarm.reset && alarm.reset_at
                        ? new Date(alarm.reset_at).getTime()
                        : Date.now();
                    const rowDuration = formatDuration(rowEndMs - created.getTime());
                    const rowLive = !alarm.reset;
                    const topo = deviceTopology.get(alarm.device_id);
                    return (
                      <tr
                        key={alarm.id}
                        className={`alarm-row ${levelClass} ${selectedClass}`.trim()}
                        onClick={() => setSelectedAlarmId(alarm.id)}
                      >
                        <td className="alarm-cell-level">
                          <span className={`alarm-pill level-${alarm.level.toLowerCase()}`}>{levelLabelTr(alarm.level)}</span>
                        </td>
                        <td className="alarm-cell-datetime">
                          {created.toLocaleDateString(localeTag)} {created.toLocaleTimeString(localeTag, { hour: "2-digit", minute: "2-digit", second: "2-digit" })}
                        </td>
                        <td className="alarm-cell-device">{renderDeviceCell(alarm.device_id)}</td>
                        <td className="alarm-cell-source">{renderSourceCell(alarm.signal_key)}</td>
                        <td className="alarm-cell-line">{topo?.lineName || <span className="alarm-cell-empty">—</span>}</td>
                        <td className="alarm-cell-region">{topo?.regionName || <span className="alarm-cell-empty">—</span>}</td>
                        <td className="alarm-cell-title">
                          <div className="alarm-title-text" title={alarm.description || alarm.title}>{alarm.title}</div>
                        </td>
                        <td className="alarm-cell-state">
                          <span className={`alarm-state ${state.klass}`}>
                            <state.Icon size={13} />
                            {state.label}
                          </span>
                        </td>
                        <td className="alarm-cell-assignee">{alarm.assigned_to ?? <span className="alarm-cell-empty">—</span>}</td>
                        {/* Suren alarmda canli nokta, bitmiste kesin sure.
                            Ayni sayinin "artiyor mu, durdu mu" oldugu
                            rakamdan anlasilmiyordu. */}
                        <td className={`alarm-cell-duration${rowLive ? " is-live" : " is-final"}`}>
                          {rowLive ? <i className="alarm-duration-dot" aria-hidden="true" /> : null}
                          {rowDuration}
                        </td>
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
                      {/* HATA DALI ONCE GELIR — "yesil yalan"in kapatildigi yer.
                          Veri alinamadiginda "Aktif alarm yok" yazmak, sistemin
                          BILMEDIGINI "sorun yok" diye gostermektir. Bir ariza
                          izleme urununde en agir hata sinifi budur. */}
                      <td
                        colSpan={11}
                        className={
                          loadError
                            ? "alarms-empty-cell alarms-empty-cell--error"
                            : "alarms-empty-cell"
                        }
                      >
                        {loadError
                          ? `${t("alarms.loadFailed")} — ${loadError}`
                          : activeTab === "resolved"
                            ? t("alarms.noPending")
                            : t("alarms.noActive")}
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
