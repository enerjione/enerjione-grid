import { useCallback, useEffect, useRef, useState, type MouseEvent as ReactMouseEvent } from "react";
import { useTranslation } from "react-i18next";

import {
  fetchNotifications,
  markAllNotificationsRead,
  markNotificationRead
} from "../shared/api";
import type { NotificationItem } from "../shared/types";

type Props = {
  token: string;
  /** Bildirim'e tiklayinca cagrilir (link varsa). */
  onNavigate?: (link: string) => void;
};

const POLL_INTERVAL_MS = 30_000; // 30 sn

function severityClass(severity: string): string {
  const s = (severity || "info").toLowerCase();
  if (s === "critical" || s === "error") return "notif-sev-critical";
  if (s === "warning" || s === "warn") return "notif-sev-warning";
  return "notif-sev-info";
}

function timeAgo(
  iso: string,
  localeTag: string,
  t: (key: string, opts?: Record<string, unknown>) => string,
): string {
  const dt = new Date(iso).getTime();
  if (!Number.isFinite(dt)) return "";
  const diffSec = Math.max(0, Math.floor((Date.now() - dt) / 1000));
  if (diffSec < 60) return t("notifications.timeAgo.seconds", { count: diffSec });
  if (diffSec < 3600) return t("notifications.timeAgo.minutes", { count: Math.floor(diffSec / 60) });
  if (diffSec < 86400) return t("notifications.timeAgo.hours", { count: Math.floor(diffSec / 3600) });
  return new Date(iso).toLocaleString(localeTag);
}

// Sinyal kaynagi (master / sat01 / sat02 / ...) frontend dostu etikete cevir.
const _SOURCE_LABEL: Record<string, string> = {
  master: "Master",
  sat01: "Satellite 01",
  sat02: "Satellite 02"
};

function sourceLabel(src: string | null | undefined): string | null {
  if (!src) return null;
  const key = src.toLowerCase();
  return _SOURCE_LABEL[key] ?? key.charAt(0).toUpperCase() + key.slice(1);
}

// Alarm seviyesini i18n etiketine cevir.
function levelLabelTr(
  level: string | null | undefined,
  t: (key: string) => string,
): string | null {
  if (!level) return null;
  const k = level.toLowerCase();
  if (k === "critical" || k === "high") return t("notifications.level.critical");
  if (k === "warning" || k === "warn" || k === "medium") return t("notifications.level.warning");
  if (k === "info" || k === "low") return t("notifications.level.info");
  if (k === "error") return t("notifications.level.error");
  return k.charAt(0).toUpperCase() + k.slice(1);
}

// Backend metadata.operator string'ini insan-okur sembol/metne cevir.
function operatorSymbol(op: string | null | undefined): string {
  if (!op) return "";
  const k = op.toLowerCase();
  if (k === "gt" || k === ">") return ">";
  if (k === "ge" || k === ">=") return "≥";
  if (k === "lt" || k === "<") return "<";
  if (k === "le" || k === "<=") return "≤";
  if (k === "eq" || k === "=" || k === "==") return "=";
  if (k === "ne" || k === "!=") return "≠";
  return op;
}

// Sayisal degeri tr-TR locale'de, gereksiz trailing zero'yu kirpan formatla.
const _NUM_FMT = new Intl.NumberFormat("tr-TR", { maximumFractionDigits: 4 });
function fmtNumber(v: number | null | undefined): string {
  if (v === null || v === undefined || !Number.isFinite(v)) return "";
  return _NUM_FMT.format(v);
}

type I18nRef = {
  key?: string | null;
  params?: Record<string, unknown> | null;
};

type AlarmMetadata = {
  alarm_id?: number | null;
  device_code?: string | null;
  device_name?: string | null;
  level?: string | null;
  signal_key?: string | null;
  signal_source?: string | null;
  source_gateway?: string | null;
  line_name?: string | null;
  line_code?: string | null;
  region_name?: string | null;
  value?: number | null;
  value_string?: string | null;
  threshold?: number | null;
  operator?: string | null;
  source_timestamp?: string | null;
  _title_i18n?: I18nRef | null;
  _body_i18n?: I18nRef | null;
};

function parseMetadata(raw: string | null | undefined): AlarmMetadata | null {
  if (!raw) return null;
  try {
    const obj = JSON.parse(raw);
    if (obj && typeof obj === "object") return obj as AlarmMetadata;
  } catch {
    // Bozuk JSON — sessizce yut, kart yine baslik+body ile gosterilir.
  }
  return null;
}

export function NotificationBell({ token, onNavigate }: Props) {
  const { t, i18n } = useTranslation();
  const localeTag = i18n.language?.startsWith("tr") ? "tr-TR" : "en-US";
  const [items, setItems] = useState<NotificationItem[]>([]);
  const [unread, setUnread] = useState(0);
  const [open, setOpen] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const wrapperRef = useRef<HTMLDivElement | null>(null);
  const pollInFlight = useRef(false);

  const refreshUnread = useCallback(async () => {
    if (pollInFlight.current) return;
    pollInFlight.current = true;
    try {
      // Backend tum okunmamislari sayar; frontend ise eski format alarmlari
      // (metadata'siz) gizliyor. Tutarli rakam icin polling sirasinda da
      // ayni filtre kuralini uygula: list cek, filtreden gec, count'i kullan.
      const list = await fetchNotifications(token, { onlyUnread: true, limit: 50 });
      const filteredCount = list.filter((item) => {
        if (item.category !== "alarm") return true;
        if (!item.metadata_json) return false;
        try {
          const meta = JSON.parse(item.metadata_json) as { device_code?: unknown };
          return Boolean(meta && typeof meta.device_code === "string" && meta.device_code);
        } catch {
          return false;
        }
      }).length;
      setUnread(filteredCount);
    } catch {
      // Sessizce gec - polling hatalari kullaniciyi rahatsiz etmesin
    } finally {
      pollInFlight.current = false;
    }
  }, [token]);

  const refreshList = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      // Yalnizca okunmamislari getir — kullanici daha onceden okuduklarini
      // tekrar gormek istemiyor (istek). Liste boyutu da stabil kalir.
      const list = await fetchNotifications(token, { onlyUnread: true, limit: 50 });
      // Eski format alarm bildirimlerini gizle: yeni alarm kayitlari
      // metadata_json icinde device_code (ve genelde line_name/region_name)
      // tasiyor; bunlardan once uretilmis "ALARM Test alarmi" gibi bos
      // kartlar artik anlamsiz. Kategori "alarm" + metadata'da device_code
      // yoksa filtrele. Diger kategoriler (alarm_assignment, fault_*) ve
      // dolu metadata'li alarmlar etkilenmez.
      const filtered = list.filter((item) => {
        if (item.category !== "alarm") return true;
        if (!item.metadata_json) return false;
        try {
          const meta = JSON.parse(item.metadata_json) as { device_code?: unknown };
          return Boolean(meta && typeof meta.device_code === "string" && meta.device_code);
        } catch {
          return false;
        }
      });
      setItems(filtered);
      setUnread(filtered.length);
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : t("notifications.fetchFail"));
    } finally {
      setLoading(false);
    }
  }, [token]);

  // Sayim polling
  useEffect(() => {
    void refreshUnread();
    const id = window.setInterval(() => void refreshUnread(), POLL_INTERVAL_MS);
    return () => window.clearInterval(id);
  }, [refreshUnread]);

  // Disari tiklayinca kapan
  useEffect(() => {
    if (!open) return;
    const onClick = (e: MouseEvent) => {
      if (!wrapperRef.current?.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", onClick);
    return () => document.removeEventListener("mousedown", onClick);
  }, [open]);

  const handleToggle = () => {
    setOpen((prev) => {
      const next = !prev;
      if (next) void refreshList();
      return next;
    });
  };

  const handleMarkAll = async () => {
    try {
      await markAllNotificationsRead(token);
      // Tum okundu → listeyi temizle (sadece okunmamis gosterdigimiz icin).
      setItems([]);
      setUnread(0);
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : t("notifications.markFail"));
    }
  };

  const handleItemClick = async (item: NotificationItem) => {
    // Karta tiklandiginda LINK varsa o sayfaya git; okundu isaretleme
    // ayri ✓ butonu ile yapilir (kullanici sadece detaya bakmak isteyince
    // yanlislikla listeden silmemis olur).
    if (item.link && onNavigate) {
      onNavigate(item.link);
      setOpen(false);
    }
  };

  // Tekli onaylama (✓ butonu): bildirimi okundu isaretler ve listeden
  // cikarir. Karta tiklamayi tetiklememesi icin event propagation durdurulur.
  const handleMarkOne = async (
    e: ReactMouseEvent<HTMLButtonElement>,
    item: NotificationItem
  ) => {
    e.stopPropagation();
    if (item.is_read) return;
    try {
      await markNotificationRead(token, item.id);
    } catch {
      // Hata olsa bile UI'dan cikaralim
    }
    setItems((prev) => prev.filter((it) => it.id !== item.id));
    setUnread((u) => Math.max(0, u - 1));
  };

  return (
    <div className="notif-bell-wrap" ref={wrapperRef}>
      <button
        type="button"
        className={`notif-bell-btn ${unread > 0 ? "notif-bell-btn--active" : ""}`}
        onClick={handleToggle}
        title={t("notifications.title")}
        aria-label={t("notifications.title")}
      >
        <span className="material-symbols-outlined">notifications</span>
        {unread > 0 ? (
          <span className="notif-bell-badge">{unread > 99 ? "99+" : unread}</span>
        ) : null}
      </button>

      {open ? (
        <div className="notif-dropdown">
          <header className="notif-dropdown-head">
            <strong>{t("notifications.title")}</strong>
            {items.length > 0 ? (
              <button type="button" className="notif-mark-all" onClick={() => void handleMarkAll()}>
                {t("notifications.markAllRead")}
              </button>
            ) : null}
          </header>

          {loading ? <div className="notif-empty">{t("notifications.loading")}</div> : null}
          {error && !loading ? <div className="notif-error">{error}</div> : null}

          {!loading && !error && items.length === 0 ? (
            <div className="notif-empty">
              <span className="material-symbols-outlined">notifications_off</span>
              {t("notifications.emptyHint")}
            </div>
          ) : null}

          {!loading && items.length > 0 ? (
            <ul className="notif-list">
              {items.map((item) => {
                const meta = parseMetadata(item.metadata_json);
                const isAlarm = item.category === "alarm";
                const isAssignment =
                  item.category === "alarm_assignment" ||
                  item.category === "fault_assignment";
                const isFault =
                  item.category === "fault" || item.category === "fault_assignment";
                const isComment =
                  item.category === "alarm_comment" ||
                  item.category === "fault_comment";
                // Kategori-bazli renk degistirici. Severity sinifi (kritik=
                // kirmizi, uyari=turuncu) koru AMA "atama" ve "yorum" gibi
                // bilgilendirici kategorilerde fault icin mor, alarm icin
                // turkuaz, yorum icin gri-mavi seritle ayri renk paleti
                // gonderilir. CSS bu ek sinifa gore border + arka plan
                // gradient'ini override eder.
                const catCls =
                  item.category === "alarm_assignment"
                    ? "notif-cat-alarm-assignment"
                    : item.category === "fault_assignment"
                    ? "notif-cat-fault-assignment"
                    : item.category === "alarm_comment"
                    ? "notif-cat-alarm-comment"
                    : item.category === "fault_comment"
                    ? "notif-cat-fault-comment"
                    : "";
                const sevCls = severityClass(item.severity);
                const deviceLabel = meta?.device_name
                  ? meta.device_name
                  : meta?.device_code ?? null;
                const deviceCode = meta?.device_code ?? null;
                const srcLabel = sourceLabel(meta?.signal_source);
                const opSym = operatorSymbol(meta?.operator);
                const valueDisplay = meta?.value_string
                  ? meta.value_string
                  : meta?.value !== null && meta?.value !== undefined
                  ? fmtNumber(meta.value)
                  : null;
                const thresholdDisplay = meta?.threshold !== null && meta?.threshold !== undefined
                  ? fmtNumber(meta.threshold)
                  : null;
                const levelTr = levelLabelTr(meta?.level ?? item.severity, t);
                const lineLabel = meta?.line_name ?? meta?.line_code ?? null;
                const regionLabel = meta?.region_name ?? null;
                // Baslik formati: "Yeni alarm: <Alarm adi>" -> ust satira
                // "Yeni alarm" kucuk eyebrow, alta buyuk alarm adi.
                // Format uymuyorsa baslik tek parca gosterilir.
                // Backend metadata._title_i18n varsa onceligi alir (cevirili
                // baslik elde edilir); aksi halde DB'deki TR baslik kullanilir.
                const localizedTitle = (() => {
                  const ref = meta?._title_i18n;
                  if (ref && typeof ref.key === "string" && ref.key) {
                    const params = (ref.params ?? {}) as Record<string, unknown>;
                    return t(`notifications.titles.${ref.key}`, params);
                  }
                  return item.title;
                })();
                // body alarm bildiriminde basligin kopyasi olabiliyor ("Yeni
                // alarm: Test alarmi" -> body "Test alarmi"). Tekrari gizle.
                // Atama/yorum bildirimlerinde body genellikle anlamli ek bilgi
                // (atayan, hat aciklamasi, yorum metni) — koruyalim.
                // Backend metadata._body_i18n varsa cevirili govde gosterilir.
                const localizedBody = (() => {
                  const ref = meta?._body_i18n;
                  if (ref && typeof ref.key === "string" && ref.key) {
                    const params = (ref.params ?? {}) as Record<string, unknown>;
                    return t(`notifications.bodies.${ref.key}`, params);
                  }
                  return item.body ?? null;
                })();
                const bodyToShow = (() => {
                  if (!localizedBody) return null;
                  const body = localizedBody.trim();
                  if (!body) return null;
                  const title = localizedTitle.trim();
                  if (title === body) return null;
                  if (title.endsWith(`: ${body}`)) return null;
                  return body;
                })();
                const titleParts = (() => {
                  const raw = localizedTitle.trim();
                  const idx = raw.indexOf(":");
                  if (idx > 0 && idx < raw.length - 1) {
                    return {
                      eyebrow: raw.slice(0, idx).trim(),
                      main: raw.slice(idx + 1).trim(),
                    };
                  }
                  return { eyebrow: null as string | null, main: raw };
                })();
                return (
                  <li
                    key={item.id}
                    className={`notif-item ${item.is_read ? "" : "notif-item--unread"} ${sevCls} ${catCls}`}
                    onClick={() => void handleItemClick(item)}
                  >
                    <div className="notif-item-body">
                      {/* Ust satir: baslik + sag tarafta seviye rozeti + onay butonu.
                          Eski mavi okunmadi yuvarlagi kaldirildi; sol kalin renk
                          seridi zaten okunmadi/severity'yi gosteriyor. */}
                      <div className="notif-item-title-row">
                        <div className="notif-item-title-stack">
                          {titleParts.eyebrow ? (
                            <span className="notif-item-eyebrow">{titleParts.eyebrow}</span>
                          ) : null}
                          <strong className="notif-item-title">{titleParts.main}</strong>
                        </div>
                        <div className="notif-item-actions">
                          {/* Kategoriye gore farkli rozet:
                              - alarm: severity (Kritik/Uyari/Bilgi)
                              - alarm_assignment / fault_assignment: "Atama"
                              - alarm_comment / fault_comment: "Yorum"
                              - fault: "Arıza" */}
                          {isAssignment ? (
                            <span className="notif-level-badge notif-badge-assignment">{t("notifications.badgeAssignment")}</span>
                          ) : isComment ? (
                            <span className="notif-level-badge notif-badge-comment">{t("notifications.badgeComment")}</span>
                          ) : item.category === "fault" ? (
                            <span className="notif-level-badge notif-badge-fault">{t("notifications.badgeFault")}</span>
                          ) : levelTr ? (
                            <span className={`notif-level-badge ${sevCls}`}>{levelTr}</span>
                          ) : null}
                          {!item.is_read ? (
                            <button
                              type="button"
                              className="notif-item-mark-btn"
                              title={t("notifications.markRead")}
                              aria-label={t("notifications.markReadShort")}
                              onClick={(e) => void handleMarkOne(e, item)}
                            >
                              <span className="material-symbols-outlined">done</span>
                            </button>
                          ) : null}
                        </div>
                      </div>
                      {/* Cihaz + Kaynak + Hat + Bolge satir satir */}
                      {(isAlarm || isFault || isComment) &&
                      (deviceLabel || srcLabel || lineLabel || regionLabel) ? (
                        <div className="notif-item-rows">
                          {deviceLabel ? (
                            <div className="notif-row">
                              <span className="notif-row-icon material-symbols-outlined">router</span>
                              <span className="notif-row-label">{t("notifications.rowDevice")}</span>
                              <span className="notif-row-value" title={deviceCode ?? undefined}>
                                {deviceLabel}
                              </span>
                            </div>
                          ) : null}
                          {srcLabel ? (
                            <div className="notif-row">
                              <span className="notif-row-icon material-symbols-outlined">satellite_alt</span>
                              <span className="notif-row-label">{t("notifications.rowSource")}</span>
                              <span className={`notif-row-value notif-row-value--src notif-row-value--src-${(meta?.signal_source ?? "").toLowerCase()}`}>
                                {srcLabel}
                              </span>
                            </div>
                          ) : null}
                          {lineLabel ? (
                            <div className="notif-row">
                              <span className="notif-row-icon material-symbols-outlined">power</span>
                              <span className="notif-row-label">{t("notifications.rowLine")}</span>
                              <span className="notif-row-value" title={meta?.line_code ?? undefined}>
                                {lineLabel}
                              </span>
                            </div>
                          ) : null}
                          {regionLabel ? (
                            <div className="notif-row">
                              <span className="notif-row-icon material-symbols-outlined">map</span>
                              <span className="notif-row-label">{t("notifications.rowRegion")}</span>
                              <span className="notif-row-value">{regionLabel}</span>
                            </div>
                          ) : null}
                        </div>
                      ) : null}
                      {/* Aciklama (basliktan farkli ise) */}
                      {bodyToShow ? <p className="notif-item-text">{bodyToShow}</p> : null}
                      {/* Eski ölçüm/eşik kutusu kullanici talebiyle kart-
                          dan kaldirildi (özellikle binary sinyallerde
                          "1 boolean_true 0 eşik" anlamsizdi). Detayli ol-
                          cum bilgisi mail/Telegram bildiriminde ve alarm
                          detay sayfasinda yer alir. */}
                      {/* Alt satir: zaman + actor (eger varsa). Seviye rozeti
                          ust satira tasinarak meta sadelesti. */}
                      <div className="notif-item-meta">
                        {item.actor_username ? (
                          <span className="notif-item-actor">{item.actor_username}</span>
                        ) : null}
                        <span className="notif-item-time">{timeAgo(item.created_at, localeTag, t)}</span>
                      </div>
                    </div>
                  </li>
                );
              })}
            </ul>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}
