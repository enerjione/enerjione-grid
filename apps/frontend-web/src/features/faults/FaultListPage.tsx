import { useEffect, useMemo, useState } from "react";

import type { FaultComment, FaultEvent, UserRead } from "../../shared/types";

type Props = {
  faults: FaultEvent[];
  users: UserRead[];
  currentUsername: string;
  canAssign: boolean; // engineer/installer
  loading?: boolean;
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

function fmtDate(iso?: string | null): string {
  if (!iso) return "—";
  const d = new Date(iso);
  return d.toLocaleString("tr-TR");
}

function fmtRelative(iso?: string | null): string {
  if (!iso) return "—";
  const d = new Date(iso);
  const sec = Math.round((Date.now() - d.getTime()) / 1000);
  if (sec < 60) return `${sec} sn önce`;
  if (sec < 3600) return `${Math.round(sec / 60)} dk önce`;
  if (sec < 86400) return `${Math.round(sec / 3600)} sa önce`;
  return `${Math.round(sec / 86400)} gün önce`;
}

export function FaultListPage({
  faults,
  users,
  currentUsername,
  canAssign,
  loading,
  onAssign,
  onUpdateStatus,
  onUpdateNote,
  onLoadComments,
  onAddComment
}: Props) {
  const [statusFilter, setStatusFilter] = useState<"active" | "all" | "closed">("active");
  const [search, setSearch] = useState("");
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [comments, setComments] = useState<FaultComment[]>([]);
  const [commentDraft, setCommentDraft] = useState("");
  const [noteDraft, setNoteDraft] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");

  // İstatistik chip'ler (toplam aktif, atanmış, devam ediyor, kapatılan)
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
        const hay = `${f.line_name} ${f.region_name} ${f.last_red_device_name ?? ""} ${f.last_red_device_code ?? ""} ${f.assigned_to_username ?? ""}`.toLowerCase();
        return hay.includes(q);
      });
    }
    // En yeni en üstte
    return [...arr].sort((a, b) => new Date(b.opened_at).getTime() - new Date(a.opened_at).getTime());
  }, [faults, statusFilter, search]);

  const selected = useMemo(
    () => filtered.find((f) => f.id === selectedId) ?? faults.find((f) => f.id === selectedId) ?? null,
    [filtered, faults, selectedId]
  );

  // Detay paneline gec / yorum ve note state'i hazırla
  useEffect(() => {
    if (selected) {
      setNoteDraft(selected.note ?? "");
      setCommentDraft("");
      setError("");
      void (async () => {
        try {
          const list = await onLoadComments(selected.id);
          setComments(list);
        } catch (err) {
          setError(err instanceof Error ? err.message : "Yorumlar alınamadı.");
        }
      })();
    } else {
      setComments([]);
      setNoteDraft("");
      setCommentDraft("");
    }
  }, [selected?.id, onLoadComments]);

  const userOptions = useMemo(() => {
    return [...users].sort((a, b) => a.full_name.localeCompare(b.full_name, "tr"));
  }, [users]);

  const canEditFault = (f: FaultEvent | null): boolean => {
    if (!f) return false;
    if (canAssign) return true; // engineer/installer
    return f.assigned_to_username === currentUsername;
  };

  const handleAssign = async (newUsername: string) => {
    if (!selected) return;
    setSaving(true);
    setError("");
    try {
      await onAssign(selected.id, newUsername || null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Atama yapılamadı.");
    } finally {
      setSaving(false);
    }
  };

  const handleStatus = async (newStatus: string) => {
    if (!selected) return;
    setSaving(true);
    setError("");
    try {
      await onUpdateStatus(selected.id, newStatus);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Durum güncellenemedi.");
    } finally {
      setSaving(false);
    }
  };

  const handleSaveNote = async () => {
    if (!selected) return;
    setSaving(true);
    setError("");
    try {
      await onUpdateNote(selected.id, noteDraft.trim() || null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Not kaydedilemedi.");
    } finally {
      setSaving(false);
    }
  };

  const handleAddComment = async () => {
    if (!selected) return;
    const body = commentDraft.trim();
    if (!body) return;
    setSaving(true);
    setError("");
    try {
      await onAddComment(selected.id, body);
      const list = await onLoadComments(selected.id);
      setComments(list);
      setCommentDraft("");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Yorum eklenemedi.");
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="faults-page">
      {/* Üst başlık + istatistik şeridi */}
      <header className="faults-page-header">
        <div className="faults-page-title-wrap">
          <span className="material-symbols-outlined faults-page-icon">report</span>
          <div>
            <h2>Hat Arızaları</h2>
            <p className="faults-page-sub">
              Sahada tespit edilen arıza noktaları ve sorumluluk atamaları.
            </p>
          </div>
        </div>
        <div className="faults-page-toolbar">
          <input
            type="search"
            placeholder="Hat / cihaz / atanan ara…"
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
        </div>
      </header>

      {/* Sayaç chip'leri (toplam, atandı, devam, çözüldü) */}
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

      <div className="faults-page-body">
        <div className="faults-cards">
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
              const isSelected = selectedId === f.id;
              const sc = STATUS_COLOR[f.status] ?? "#64748b";
              return (
                <button
                  key={f.id}
                  type="button"
                  className={`faults-card ${isSelected ? "selected" : ""}`}
                  onClick={() => setSelectedId(f.id)}
                  style={{ borderLeftColor: sc }}
                >
                  <div className="faults-card-row faults-card-row--top">
                    <div className="faults-card-line">
                      <span className="material-symbols-outlined faults-card-line-icon">timeline</span>
                      <div>
                        <strong>{f.line_name}</strong>
                        <span>{f.region_name}</span>
                      </div>
                    </div>
                    <span
                      className="faults-status-pill"
                      style={{ background: `${sc}22`, color: sc }}
                    >
                      {STATUS_LABEL[f.status] ?? f.status}
                    </span>
                  </div>

                  <div className="faults-card-row faults-card-row--mid">
                    <div className="faults-card-range">
                      <span className="faults-card-range-label">Arıza Aralığı</span>
                      <strong>
                        {f.from_pole_seq != null && f.to_pole_seq != null
                          ? `Direk #${f.from_pole_seq} — #${f.to_pole_seq}`
                          : "—"}
                      </strong>
                    </div>
                    <div className="faults-card-devices">
                      <div className="faults-card-dev faults-card-dev--red">
                        <span className="faults-card-dev-dot" />
                        <div>
                          <span>Son RED</span>
                          <strong>{f.last_red_device_name ?? "—"}</strong>
                        </div>
                      </div>
                      <span className="faults-card-arrow">→</span>
                      <div className="faults-card-dev faults-card-dev--green">
                        <span className="faults-card-dev-dot" />
                        <div>
                          <span>İlk YEŞİL</span>
                          <strong>{f.first_green_device_name ?? "Hat ucu"}</strong>
                        </div>
                      </div>
                    </div>
                  </div>

                  <div className="faults-card-row faults-card-row--bot">
                    <div className="faults-card-meta">
                      <span className="material-symbols-outlined">schedule</span>
                      <span title={fmtDate(f.opened_at)}>{fmtRelative(f.opened_at)}</span>
                    </div>
                    <div className="faults-card-meta">
                      <span className="material-symbols-outlined">person</span>
                      <span>
                        {f.assigned_to_full_name ?? f.assigned_to_username ?? (
                          <em className="faults-card-meta-dim">Atanmamış</em>
                        )}
                      </span>
                    </div>
                    <div className="faults-card-meta">
                      <span className="material-symbols-outlined">forum</span>
                      <span>{f.comment_count > 0 ? `${f.comment_count} yorum` : "Yorum yok"}</span>
                    </div>
                  </div>
                </button>
              );
            })
          )}
        </div>

        {selected ? (
          <aside className="faults-detail">
            <header className="faults-detail-head">
              <div>
                <h3>{selected.line_name}</h3>
                <span className="faults-detail-sub">
                  {selected.region_name} · Direk #{selected.from_pole_seq} — #{selected.to_pole_seq}
                </span>
              </div>
              <button
                type="button"
                className="faults-detail-close"
                onClick={() => setSelectedId(null)}
                aria-label="Kapat"
              >
                <span className="material-symbols-outlined">close</span>
              </button>
            </header>

            <div className="faults-detail-info">
              <div>
                <span className="faults-detail-label">Açılış</span>
                <span>{fmtDate(selected.opened_at)}</span>
              </div>
              {selected.resolved_at ? (
                <div>
                  <span className="faults-detail-label">Çözüldü</span>
                  <span>{fmtDate(selected.resolved_at)}</span>
                </div>
              ) : null}
              {selected.closed_at ? (
                <div>
                  <span className="faults-detail-label">Kapatıldı</span>
                  <span>{fmtDate(selected.closed_at)}</span>
                </div>
              ) : null}
              <div>
                <span className="faults-detail-label">Son RED Cihaz</span>
                <span>
                  {selected.last_red_device_name ?? "—"}
                  {selected.last_red_device_code ? ` (${selected.last_red_device_code})` : ""}
                </span>
              </div>
              <div>
                <span className="faults-detail-label">İlk YEŞİL Cihaz</span>
                <span>
                  {selected.first_green_device_name
                    ? `${selected.first_green_device_name}${
                        selected.first_green_device_code ? ` (${selected.first_green_device_code})` : ""
                      }`
                    : "— hat ucu"}
                </span>
              </div>
            </div>

            <div className="faults-detail-section">
              <span className="faults-detail-label">Atanan</span>
              {canAssign ? (
                <select
                  value={selected.assigned_to_username ?? ""}
                  onChange={(e) => void handleAssign(e.target.value)}
                  disabled={saving}
                >
                  <option value="">— atanmamış —</option>
                  {userOptions.map((u) => (
                    <option key={u.id} value={u.username}>
                      {u.full_name} ({u.username})
                    </option>
                  ))}
                </select>
              ) : (
                <span>{selected.assigned_to_full_name ?? selected.assigned_to_username ?? "—"}</span>
              )}
            </div>

            <div className="faults-detail-section">
              <span className="faults-detail-label">Durum</span>
              <div className="faults-status-buttons">
                {(["assigned", "in_progress", "resolved", "closed"] as const).map((s) => (
                  <button
                    key={s}
                    type="button"
                    className={`faults-status-btn ${selected.status === s ? "active" : ""}`}
                    onClick={() => void handleStatus(s)}
                    disabled={saving || !canEditFault(selected)}
                    style={selected.status === s ? { background: STATUS_COLOR[s], color: "#fff" } : undefined}
                  >
                    {STATUS_LABEL[s]}
                  </button>
                ))}
              </div>
            </div>

            <div className="faults-detail-section">
              <span className="faults-detail-label">Kısa Not</span>
              <textarea
                rows={2}
                value={noteDraft}
                onChange={(e) => setNoteDraft(e.target.value)}
                disabled={saving || !canEditFault(selected)}
                placeholder="Kısa açıklama (opsiyonel)…"
              />
              {canEditFault(selected) ? (
                <button
                  type="button"
                  className="faults-detail-save"
                  onClick={() => void handleSaveNote()}
                  disabled={saving}
                >
                  Notu Kaydet
                </button>
              ) : null}
            </div>

            <div className="faults-detail-section">
              <span className="faults-detail-label">Saha Raporu / Yorumlar</span>
              <ul className="faults-comments">
                {comments.length === 0 ? (
                  <li className="faults-comments-empty">Henüz yorum yok.</li>
                ) : (
                  comments.map((c) => (
                    <li key={c.id} className="faults-comment-item">
                      <header>
                        <strong>{c.author_username}</strong>
                        <span>{fmtDate(c.created_at)}</span>
                      </header>
                      <p>{c.body}</p>
                    </li>
                  ))
                )}
              </ul>
              {canEditFault(selected) ? (
                <div className="faults-comment-add">
                  <textarea
                    rows={3}
                    placeholder="Saha gözlemi, bakım/onarım adımları, parça değişimi…"
                    value={commentDraft}
                    onChange={(e) => setCommentDraft(e.target.value)}
                    disabled={saving}
                  />
                  <button
                    type="button"
                    onClick={() => void handleAddComment()}
                    disabled={saving || !commentDraft.trim()}
                  >
                    Yorum Ekle
                  </button>
                </div>
              ) : null}
            </div>

            {error ? <div className="faults-detail-error">{error}</div> : null}
          </aside>
        ) : null}
      </div>
    </div>
  );
}
