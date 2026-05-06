import { useEffect, useMemo, useState } from "react";

import type { GridSnapshot } from "../../shared/api";
import type {
  AlarmEvent,
  DeviceRow,
  FaultComment,
  FaultEvent,
  UserRead
} from "../../shared/types";
import { FaultDetailModal } from "./FaultDetailModal";

type Props = {
  faults: FaultEvent[];
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

const STATUS_LABEL: Record<string, string> = {
  open: "Açık",
  assigned: "Atandı",
  in_progress: "Devam Ediyor",
  resolved: "Sahada Çözüldü",
  closed: "Kapatıldı"
};
const STATUS_COLOR: Record<string, string> = {
  open: "#ef4444",
  assigned: "#f59e0b",
  in_progress: "#3b82f6",
  resolved: "#10b981",
  closed: "#64748b"
};

function fmtRelative(iso?: string | null): string {
  if (!iso) return "—";
  const d = new Date(iso);
  const sec = Math.round((Date.now() - d.getTime()) / 1000);
  if (sec < 60) return `${sec} sn önce`;
  if (sec < 3600) return `${Math.round(sec / 60)} dk önce`;
  if (sec < 86400) return `${Math.round(sec / 3600)} sa önce`;
  return `${Math.round(sec / 86400)} gün önce`;
}

function fmtDate(iso?: string | null): string {
  if (!iso) return "—";
  return new Date(iso).toLocaleString("tr-TR");
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
          <span className="faults-stat-label">Toplam</span>
        </div>
        <div className="faults-stat-chip faults-stat-chip--open">
          <span className="faults-stat-num">{stats.open}</span>
          <span className="faults-stat-label">Açık</span>
        </div>
        <div className="faults-stat-chip faults-stat-chip--assigned">
          <span className="faults-stat-num">{stats.assigned}</span>
          <span className="faults-stat-label">Atandı</span>
        </div>
        <div className="faults-stat-chip faults-stat-chip--progress">
          <span className="faults-stat-num">{stats.inProgress}</span>
          <span className="faults-stat-label">Devam Ediyor</span>
        </div>
        <div className="faults-stat-chip faults-stat-chip--resolved">
          <span className="faults-stat-num">{stats.resolved}</span>
          <span className="faults-stat-label">Sahada Çözüldü</span>
        </div>
        <div className="faults-stat-chip faults-stat-chip--closed">
          <span className="faults-stat-num">{stats.closed}</span>
          <span className="faults-stat-label">Kapatıldı</span>
        </div>
      </div>

      {/* Filtre satırı */}
      <div className="faults-toolbar-row">
        <input
          type="search"
          placeholder="Bölge / hat / cihaz / atanan ara…"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          className="faults-search"
        />
        <select
          value={statusFilter}
          onChange={(e) => setStatusFilter(e.target.value as typeof statusFilter)}
          className="faults-filter"
        >
          <option value="active">Aktif</option>
          <option value="closed">Kapatılanlar</option>
          <option value="all">Hepsi</option>
        </select>
        <span className="faults-toolbar-count">{filtered.length} arıza</span>
      </div>

      {/* Tam genişlik kart listesi */}
      <div className="faults-cards faults-cards--full">
        {loading && filtered.length === 0 ? (
          <div className="faults-empty-card">
            <span className="material-symbols-outlined">hourglass_empty</span>
            <p>Yükleniyor…</p>
          </div>
        ) : filtered.length === 0 ? (
          <div className="faults-empty-card">
            <span className="material-symbols-outlined">check_circle</span>
            <h3>Aktif arıza yok</h3>
            <p>
              {search
                ? "Aramaya uygun arıza bulunamadı. Farklı bir terim deneyin."
                : statusFilter === "closed"
                ? "Kapatılmış arıza kaydı yok."
                : "Sistem temiz. Sahada arıza tespit edilince burada listelenecek."}
            </p>
          </div>
        ) : (
          filtered.map((f) => {
            const sc = STATUS_COLOR[f.status] ?? "#64748b";
            return (
              <button
                key={f.id}
                type="button"
                className="faults-card faults-card--rich"
                onClick={() => setOpenFaultId(f.id)}
                style={{ borderLeftColor: sc }}
                title={`Detay görüntüle — ${fmtDate(f.opened_at)}`}
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
                      <span className="faults-card-range-tag">Arıza Aralığı</span>
                      <strong className="faults-card-range-text">
                        Direk #{f.from_pole_seq ?? "?"} — Direk #{f.to_pole_seq ?? "?"}
                      </strong>
                    </div>
                  </div>

                  {/* Orta: Cihaz akışı */}
                  <div className="faults-card-rich-block faults-card-rich-block--devices">
                    <div className="faults-card-dev-card faults-card-dev-card--red">
                      <span className="faults-card-dev-card-label">
                        Son Arıza Algılayan Cihaz
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
                        İlk Arıza Algılamayan Cihaz
                      </span>
                      <div className="faults-card-dev-card-name">
                        <span className="faults-card-dev-card-dot" />
                        <strong>{f.first_green_device_name ?? "Hat ucu"}</strong>
                      </div>
                      {f.first_green_device_code ? (
                        <span className="faults-card-dev-card-code">{f.first_green_device_code}</span>
                      ) : null}
                    </div>
                  </div>

                  {/* Sağ: Durum + atanan + zaman */}
                  <div className="faults-card-rich-block faults-card-rich-block--status">
                    <span
                      className="faults-status-pill faults-status-pill--lg"
                      style={{ background: `${sc}22`, color: sc }}
                    >
                      {STATUS_LABEL[f.status] ?? f.status}
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
                          title={
                            isLive
                              ? "Arıza halen aktif — canlı süre"
                              : "Toplam arıza süresi"
                          }
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
                          <div>
                            <span className="faults-card-time-label">
                              {isLive ? "Arıza Süresi" : "Toplam Süre"}
                            </span>
                            <strong>{fmtElapsed(f.opened_at, endMs)}</strong>
                          </div>
                        </div>
                      );
                    })()}
                    <div className="faults-card-meta-stack">
                      <div className="faults-card-meta">
                        <span className="material-symbols-outlined">person</span>
                        <span>
                          {f.assigned_to_full_name ?? f.assigned_to_username ?? (
                            <em className="faults-card-meta-dim">Atanmamış</em>
                          )}
                        </span>
                      </div>
                      <div className="faults-card-meta" title={fmtDate(f.opened_at)}>
                        <span className="material-symbols-outlined">event</span>
                        <span>{fmtDate(f.opened_at)}</span>
                      </div>
                      <div className="faults-card-meta">
                        <span className="material-symbols-outlined">forum</span>
                        <span>{f.comment_count > 0 ? `${f.comment_count} yorum` : "Yorum yok"}</span>
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
