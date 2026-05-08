import { useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";

import type { GridSnapshot } from "../../shared/api";
import type {
  AlarmEvent,
  DeviceRow,
  FaultComment,
  FaultEvent,
  FaultStats,
  UserRead
} from "../../shared/types";
import { FaultDetailModal } from "./FaultDetailModal";

type Props = {
  faults: FaultEvent[];
  /** Backend'den gelen ozet istatistikler (avg_resolution_seconds vb).
   * null ise henuz yuklenmedi/erisilmedi — chip'te "—" gosterilir. */
  stats?: FaultStats | null;
  users: UserRead[];
  currentUsername: string;
  canAssign: boolean; // engineer/installer
  loading?: boolean;
  gridSnapshot?: GridSnapshot | null;
  devices?: DeviceRow[];
  alarms?: AlarmEvent[];
  onAssign: (faultId: number, username: string | null) => Promise<void>;
  onUpdateStatus: (faultId: number, status: string) => Promise<void>;
  onUpdateNote: (faultId: number, note: string | null) => Promise<void>;
  onLoadComments: (faultId: number) => Promise<FaultComment[]>;
  onAddComment: (faultId: number, body: string) => Promise<void>;
};

const STATUS_COLOR: Record<string, string> = {
  open: "#ef4444",
  assigned: "#f59e0b",
  in_progress: "#3b82f6",
  resolved: "#10b981",
  closed: "#64748b"
};

function fmtDate(iso: string | null | undefined, localeTag: string): string {
  if (!iso) return "—";
  return new Date(iso).toLocaleString(localeTag);
}

function fmtDateParts(
  iso: string | null | undefined,
  localeTag: string
): { date: string; time: string } {
  if (!iso) return { date: "—", time: "" };
  const d = new Date(iso);
  return {
    date: d.toLocaleDateString(localeTag),
    time: d.toLocaleTimeString(localeTag, { hour: "2-digit", minute: "2-digit" })
  };
}

function fmtDurationSeconds(totalSec: number): string {
  let sec = Math.max(0, Math.round(totalSec));
  const days = Math.floor(sec / 86400);
  sec -= days * 86400;
  const hours = Math.floor(sec / 3600);
  sec -= hours * 3600;
  const mins = Math.floor(sec / 60);
  if (days > 0) return `${days}g ${hours}sa`;
  if (hours > 0) return `${hours}sa ${mins}dk`;
  if (mins > 0) return `${mins}dk`;
  return `<1dk`;
}

function fmtElapsed(openedIso: string, endMs: number): string {
  const start = new Date(openedIso).getTime();
  let sec = Math.max(0, Math.round((endMs - start) / 1000));
  const days = Math.floor(sec / 86400);
  sec -= days * 86400;
  const hours = Math.floor(sec / 3600);
  sec -= hours * 3600;
  const mins = Math.floor(sec / 60);
  if (days > 0) return `${days}g ${hours}sa ${mins}dk`;
  if (hours > 0) return `${hours}sa ${mins}dk`;
  if (mins > 0) return `${mins}dk`;
  return `<1dk`;
}

export function FaultListPage({
  faults,
  stats: backendStats,
  users,
  currentUsername,
  canAssign,
  loading,
  gridSnapshot,
  devices,
  alarms,
  onAssign,
  onUpdateStatus,
  onUpdateNote,
  onLoadComments,
  onAddComment
}: Props) {
  const { t, i18n } = useTranslation();
  const localeTag = i18n.language?.startsWith("tr") ? "tr-TR" : "en-US";
  const [statusFilter, setStatusFilter] = useState<"active" | "all" | "closed">("active");
  const [search, setSearch] = useState("");
  const [openFaultId, setOpenFaultId] = useState<number | null>(null);
  // Canlı süre sayacı için "now" — kart üzerindeki "x sa y dk" güncel kalsin.
  // Kart sayisi çok değil, 30sn'lik tick yeterli (ms hassasiyet anlamsız).
  const [now, setNow] = useState<number>(() => Date.now());
  useEffect(() => {
    const id = window.setInterval(() => setNow(Date.now()), 30_000);
    return () => window.clearInterval(id);
  }, []);

  const stats = useMemo(() => {
    const total = faults.length;
    const open = faults.filter((f) => f.status === "open").length;
    const assigned = faults.filter((f) => f.status === "assigned").length;
    const inProgress = faults.filter((f) => f.status === "in_progress").length;
    const resolved = faults.filter((f) => f.status === "resolved").length;
    const closed = faults.filter((f) => f.status === "closed").length;
    return { total, open, assigned, inProgress, resolved, closed };
  }, [faults]);

  const filtered = useMemo(() => {
    let arr = faults;
    if (statusFilter === "active") {
      arr = arr.filter((f) => f.status !== "closed");
    } else if (statusFilter === "closed") {
      arr = arr.filter((f) => f.status === "closed");
    }
    const q = search.trim().toLowerCase();
    if (q) {
      arr = arr.filter((f) => {
        const hay = `${f.line_name} ${f.region_name} ${f.last_red_device_name ?? ""} ${f.last_red_device_code ?? ""} ${f.first_green_device_name ?? ""} ${f.first_green_device_code ?? ""} ${f.assigned_to_username ?? ""}`.toLowerCase();
        return hay.includes(q);
      });
    }
    return [...arr].sort((a, b) => new Date(b.opened_at).getTime() - new Date(a.opened_at).getTime());
  }, [faults, statusFilter, search]);

  const openFault = useMemo(
    () => (openFaultId !== null ? faults.find((f) => f.id === openFaultId) ?? null : null),
    [faults, openFaultId]
  );

  return (
    <div className="faults-page">
      {/* Sayaç şeridi */}
      <div className="faults-stats">
        <div className="faults-stat-chip faults-stat-chip--total">
          <span className="faults-stat-num">{stats.total}</span>
          <span className="faults-stat-label">{t("faults.stats.total")}</span>
        </div>
        <div className="faults-stat-chip faults-stat-chip--open">
          <span className="faults-stat-num">{stats.open}</span>
          <span className="faults-stat-label">{t("faults.stats.open")}</span>
        </div>
        <div className="faults-stat-chip faults-stat-chip--assigned">
          <span className="faults-stat-num">{stats.assigned}</span>
          <span className="faults-stat-label">{t("faults.stats.assigned")}</span>
        </div>
        <div className="faults-stat-chip faults-stat-chip--progress">
          <span className="faults-stat-num">{stats.inProgress}</span>
          <span className="faults-stat-label">{t("faults.stats.inProgress")}</span>
        </div>
        <div className="faults-stat-chip faults-stat-chip--resolved">
          <span className="faults-stat-num">{stats.resolved}</span>
          <span className="faults-stat-label">{t("faults.stats.resolved")}</span>
        </div>
        <div className="faults-stat-chip faults-stat-chip--closed">
          <span className="faults-stat-num">{stats.closed}</span>
          <span className="faults-stat-label">{t("faults.stats.closed")}</span>
        </div>
        {/* Ortalama Çözüm Süresi — backend istatistiği (resolved/closed
            kayitlardan ortalama). Henuz kapatilmis kayit yoksa "—". */}
        <div className="faults-stat-chip faults-stat-chip--avg">
          <span className="faults-stat-num">
            {backendStats && backendStats.avg_resolution_seconds != null
              ? fmtDurationSeconds(backendStats.avg_resolution_seconds)
              : "—"}
          </span>
          <span className="faults-stat-label">{t("faults.stats.avgResolution")}</span>
        </div>
      </div>

      {/* Filtre satırı */}
      <div className="faults-toolbar-row">
        <input
          type="search"
          placeholder={t("faults.filter.search")}
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          className="faults-search"
        />
        <select
          value={statusFilter}
          onChange={(e) => setStatusFilter(e.target.value as typeof statusFilter)}
          className="faults-filter"
        >
          <option value="active">{t("faults.filter.active")}</option>
          <option value="closed">{t("faults.filter.closed")}</option>
          <option value="all">{t("faults.filter.all")}</option>
        </select>
        <span className="faults-toolbar-count">{t("faults.filter.count", { count: filtered.length })}</span>
      </div>

      {/* Tam genişlik kart listesi */}
      <div className="faults-cards faults-cards--full">
        {loading && filtered.length === 0 ? (
          <div className="faults-empty-card">
            <span className="material-symbols-outlined">hourglass_empty</span>
            <p>{t("faults.empty.loading")}</p>
          </div>
        ) : filtered.length === 0 ? (
          <div className="faults-empty-card">
            <span className="material-symbols-outlined">check_circle</span>
            <h3>{t("faults.empty.noActive")}</h3>
            <p>
              {search
                ? t("faults.empty.noResultsForSearch")
                : statusFilter === "closed"
                ? t("faults.empty.noClosed")
                : t("faults.empty.systemClean")}
            </p>
          </div>
        ) : (
          filtered.map((f) => {
            const sc = STATUS_COLOR[f.status] ?? "#64748b";
            const statusKey = `faults.status.${f.status}`;
            const statusLabel = i18n.exists(statusKey) ? t(statusKey) : f.status;
            return (
              <button
                key={f.id}
                type="button"
                className="faults-card faults-card--rich"
                onClick={() => setOpenFaultId(f.id)}
                style={{ borderLeftColor: sc }}
                title={t("faults.card.openTooltip", { at: fmtDate(f.opened_at, localeTag) })}
              >
                <div className="faults-card-rich-grid">
                  {/* Sol: Bölge + Hat + Aralık */}
                  <div className="faults-card-rich-block">
                    <div className="faults-card-region">
                      <span className="material-symbols-outlined">place</span>
                      <strong>{f.region_name}</strong>
                    </div>
                    <div className="faults-card-line-name">
                      <span className="material-symbols-outlined">timeline</span>
                      <strong>{f.line_name}</strong>
                    </div>
                    <div className="faults-card-range-row">
                      <span className="faults-card-range-tag">{t("faults.card.rangeTag")}</span>
                      <strong className="faults-card-range-text">
                        {t("faults.card.rangeText", { from: f.from_pole_seq ?? "?", to: f.to_pole_seq ?? "?" })}
                      </strong>
                    </div>
                  </div>

                  {/* Orta: Cihaz akışı */}
                  <div className="faults-card-rich-block faults-card-rich-block--devices">
                    <div className="faults-card-dev-card faults-card-dev-card--red">
                      <span className="faults-card-dev-card-label">
                        {t("faults.card.lastRedLabel")}
                      </span>
                      <div className="faults-card-dev-card-name">
                        <span className="faults-card-dev-card-dot" />
                        <strong>{f.last_red_device_name ?? "—"}</strong>
                      </div>
                      {f.last_red_device_code ? (
                        <span className="faults-card-dev-card-code">{f.last_red_device_code}</span>
                      ) : null}
                    </div>
                    <span className="faults-card-dev-arrow material-symbols-outlined">
                      arrow_forward
                    </span>
                    <div className="faults-card-dev-card faults-card-dev-card--green">
                      <span className="faults-card-dev-card-label">
                        {t("faults.card.firstGreenLabel")}
                      </span>
                      <div className="faults-card-dev-card-name">
                        <span className="faults-card-dev-card-dot" />
                        <strong>{f.first_green_device_name ?? t("faults.card.lineEnd")}</strong>
                      </div>
                      {f.first_green_device_code ? (
                        <span className="faults-card-dev-card-code">{f.first_green_device_code}</span>
                      ) : null}
                    </div>
                  </div>

                  {/* Sağ: Durum + atanan + zaman (modern grid) */}
                  <div className="faults-card-rich-block faults-card-rich-block--status">
                    {/* Üst satır: Durum + Süre pill yan yana */}
                    <div className="faults-card-status-row">
                      <span
                        className="faults-status-pill faults-status-pill--lg"
                        style={{ background: `${sc}22`, color: sc }}
                      >
                        {statusLabel}
                      </span>
                      {(() => {
                        const isLive =
                          f.status !== "closed" && f.status !== "resolved";
                        const endMs = isLive
                          ? now
                          : f.closed_at
                            ? new Date(f.closed_at).getTime()
                            : f.resolved_at
                              ? new Date(f.resolved_at).getTime()
                              : now;
                        return (
                          <div
                            className={`faults-card-time-pill ${
                              isLive ? "is-live" : "is-final"
                            }`}
                            title={isLive ? t("faults.card.durationLive") : t("faults.card.durationFinal")}
                          >
                            {isLive ? (
                              <span
                                className="faults-card-time-pulse"
                                aria-hidden="true"
                              />
                            ) : (
                              <span className="material-symbols-outlined">
                                hourglass_top
                              </span>
                            )}
                            <strong>{fmtElapsed(f.opened_at, endMs)}</strong>
                          </div>
                        );
                      })()}
                    </div>

                    {/* Alt: zaman + atanan + yorum, daha modern info chip'leri */}
                    <div className="faults-card-info-grid">
                      {(() => {
                        const parts = fmtDateParts(f.opened_at, localeTag);
                        return (
                          <div
                            className="faults-card-info-chip faults-card-info-chip--time"
                            title={fmtDate(f.opened_at, localeTag)}
                          >
                            <span className="material-symbols-outlined">schedule</span>
                            <div>
                              <span className="faults-card-info-label">{t("faults.card.openedAt")}</span>
                              <strong className="faults-card-info-datetime">
                                <span>{parts.date}</span>
                                <span className="faults-card-info-time">{parts.time}</span>
                              </strong>
                            </div>
                          </div>
                        );
                      })()}
                      <div className="faults-card-info-chip">
                        <span className="material-symbols-outlined">person</span>
                        <div>
                          <span className="faults-card-info-label">{t("faults.card.assignedTo")}</span>
                          <strong>
                            {f.assigned_to_full_name ?? f.assigned_to_username ?? (
                              <em className="faults-card-meta-dim">{t("faults.card.noAssignee")}</em>
                            )}
                          </strong>
                        </div>
                      </div>
                      <div className="faults-card-info-chip">
                        <span className="material-symbols-outlined">forum</span>
                        <div>
                          <span className="faults-card-info-label">{t("faults.card.comment")}</span>
                          <strong>
                            {f.comment_count > 0 ? t("faults.card.commentCount", { count: f.comment_count }) : "—"}
                          </strong>
                        </div>
                      </div>
                    </div>
                  </div>
                </div>
              </button>
            );
          })
        )}
      </div>

      {openFault ? (
        <FaultDetailModal
          fault={openFault}
          users={users}
          currentUsername={currentUsername}
          canAssign={canAssign}
          gridSnapshot={gridSnapshot}
          devices={devices}
          alarms={alarms}
          onClose={() => setOpenFaultId(null)}
          onAssign={onAssign}
          onUpdateStatus={onUpdateStatus}
          onUpdateNote={onUpdateNote}
          onLoadComments={onLoadComments}
          onAddComment={onAddComment}
        />
      ) : null}
    </div>
  );
}
