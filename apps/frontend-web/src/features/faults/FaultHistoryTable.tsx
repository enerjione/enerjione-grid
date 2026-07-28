/**
 * FaultHistoryTable — "Geçmiş Arızalar" sekmesi.
 *
 * Normale donmus (resolved) ve kapatilmis (closed) arizalar. Satir acilinca
 * iki panel gelir:
 *   - Kullanici Yorumu     -> fault_comments (lazy yuklenir)
 *   - Çözüm Açıklaması     -> fault_events.note
 * Ikisi de opsiyonel; bos ise "yok" yazip ekleme butonunu gosterir.
 *
 * Durum otomatik: backend ariza normale donunce status'u "resolved" yapar
 * (bkz. fault_recompute_service._resolve_fault), burada elle bir sey
 * isaretlemek gerekmez — tablo sadece yansitir.
 */
import { Fragment, useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import {
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  FilterX,
  MessageSquare,
  MessageSquarePlus,
  PencilLine,
  Search,
  Wrench
} from "lucide-react";

import type { FaultComment, FaultEvent } from "../../shared/types";

type Props = {
  faults: FaultEvent[];
  localeTag: string;
  onLoadComments: (faultId: number) => Promise<FaultComment[]>;
  onAddComment: (faultId: number, body: string) => Promise<void>;
  onUpdateNote: (faultId: number, note: string | null) => Promise<void>;
};

/** Tarih araligi on ayarlari — tam takvim secici yerine sade preset. */
type RangeKey = "7d" | "30d" | "90d" | "all";
const RANGE_DAYS: Record<RangeKey, number | null> = {
  "7d": 7,
  "30d": 30,
  "90d": 90,
  all: null
};

function fmtDateTime(iso: string | null | undefined, localeTag: string): string {
  if (!iso) return "—";
  return new Date(iso).toLocaleString(localeTag, {
    day: "2-digit",
    month: "2-digit",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit"
  });
}

function fmtDuration(f: FaultEvent): string {
  const end = f.closed_at ?? f.resolved_at;
  if (!end) return "—";
  let sec = Math.max(
    0,
    Math.round((new Date(end).getTime() - new Date(f.opened_at).getTime()) / 1000)
  );
  const days = Math.floor(sec / 86400);
  sec -= days * 86400;
  const hours = Math.floor(sec / 3600);
  sec -= hours * 3600;
  const mins = Math.floor(sec / 60);
  if (days > 0) return `${days}g ${hours}sa`;
  if (hours > 0) return `${hours}sa ${String(mins).padStart(2, "0")}dk`;
  if (mins > 0) return `${mins}dk`;
  return "<1dk";
}

export function FaultHistoryTable({
  faults,
  localeTag,
  onLoadComments,
  onAddComment,
  onUpdateNote
}: Props) {
  const { t } = useTranslation();
  const [search, setSearch] = useState("");
  const [range, setRange] = useState<RangeKey>("30d");
  const [statusFilter, setStatusFilter] = useState<"all" | "resolved" | "closed">("all");
  const [expanded, setExpanded] = useState<number | null>(null);

  // fault_id -> yorumlar. Satir ilk acildiginda yuklenir, sonra cache'lenir.
  const [comments, setComments] = useState<Record<number, FaultComment[]>>({});
  const [loadingComments, setLoadingComments] = useState<number | null>(null);

  // Yorum / cozum yazma formlari
  const [commentDraft, setCommentDraft] = useState("");
  const [noteDraft, setNoteDraft] = useState("");
  const [noteEditing, setNoteEditing] = useState<number | null>(null);
  const [saving, setSaving] = useState(false);

  const filtered = useMemo(() => {
    const q = search.trim().toLowerCase();
    const days = RANGE_DAYS[range];
    const cutoff = days != null ? Date.now() - days * 86400_000 : null;
    return faults
      .filter((f) => {
        if (statusFilter !== "all" && f.status !== statusFilter) return false;
        if (cutoff != null && new Date(f.opened_at).getTime() < cutoff) return false;
        if (!q) return true;
        const hay = `${f.line_name} ${f.region_name} ${f.from_pole_seq ?? ""} ${
          f.to_pole_seq ?? ""
        } ${f.last_red_device_name ?? ""} ${f.last_red_device_code ?? ""} ${
          f.assigned_to_full_name ?? ""
        } ${f.assigned_to_username ?? ""} ${f.note ?? ""}`.toLowerCase();
        return hay.includes(q);
      })
      .sort(
        (a, b) =>
          new Date(b.closed_at ?? b.resolved_at ?? b.opened_at).getTime() -
          new Date(a.closed_at ?? a.resolved_at ?? a.opened_at).getTime()
      );
  }, [faults, search, range, statusFilter]);

  // Satir acilinca yorumlari yukle (bir kez).
  useEffect(() => {
    if (expanded == null || comments[expanded] != null) return;
    let cancelled = false;
    setLoadingComments(expanded);
    void onLoadComments(expanded)
      .then((rows) => {
        if (!cancelled) setComments((prev) => ({ ...prev, [expanded]: rows }));
      })
      .catch(() => {
        if (!cancelled) setComments((prev) => ({ ...prev, [expanded]: [] }));
      })
      .finally(() => {
        if (!cancelled) setLoadingComments(null);
      });
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [expanded]);

  const toggle = (id: number) => {
    setExpanded((prev) => (prev === id ? null : id));
    setCommentDraft("");
    setNoteEditing(null);
  };

  const submitComment = async (faultId: number) => {
    const body = commentDraft.trim();
    if (!body) return;
    setSaving(true);
    try {
      await onAddComment(faultId, body);
      const rows = await onLoadComments(faultId);
      setComments((prev) => ({ ...prev, [faultId]: rows }));
      setCommentDraft("");
    } finally {
      setSaving(false);
    }
  };

  const submitNote = async (faultId: number) => {
    setSaving(true);
    try {
      await onUpdateNote(faultId, noteDraft.trim() || null);
      setNoteEditing(null);
    } finally {
      setSaving(false);
    }
  };

  const clearFilters = () => {
    setSearch("");
    setRange("30d");
    setStatusFilter("all");
  };

  const filtersDirty = search !== "" || range !== "30d" || statusFilter !== "all";

  return (
    <section className="fx-history">
      <header className="fx-history-head">
        <h3>{t("faults.history.title")}</h3>
        <div className="fx-history-filters">
          <label className="fx-search">
            <Search size={15} strokeWidth={2.1} />
            <input
              type="search"
              placeholder={t("faults.history.searchPlaceholder")}
              value={search}
              onChange={(e) => setSearch(e.target.value)}
            />
          </label>
          <select value={range} onChange={(e) => setRange(e.target.value as RangeKey)}>
            <option value="7d">{t("faults.history.range7d")}</option>
            <option value="30d">{t("faults.history.range30d")}</option>
            <option value="90d">{t("faults.history.range90d")}</option>
            <option value="all">{t("faults.history.rangeAll")}</option>
          </select>
          <select
            value={statusFilter}
            onChange={(e) => setStatusFilter(e.target.value as typeof statusFilter)}
          >
            <option value="all">{t("faults.history.statusAll")}</option>
            <option value="resolved">{t("faults.status.resolved")}</option>
            <option value="closed">{t("faults.status.closed")}</option>
          </select>
          <button
            type="button"
            className="fx-btn fx-btn--ghost"
            onClick={clearFilters}
            disabled={!filtersDirty}
          >
            <FilterX size={15} strokeWidth={2.1} />
            {t("faults.history.clearFilters")}
          </button>
        </div>
      </header>

      {filtered.length === 0 ? (
        <div className="fx-empty">
          <CheckCircle2 size={44} strokeWidth={1.6} />
          <h4>{t("faults.history.emptyHeading")}</h4>
          <p>
            {filtersDirty
              ? t("faults.history.emptyNoMatch")
              : t("faults.history.emptyNone")}
          </p>
        </div>
      ) : (
        <div className="fx-history-table-wrap">
          <table className="fx-history-table">
            <thead>
              <tr>
                <th className="fx-col-toggle" />
                <th>{t("faults.history.colLine")}</th>
                <th>{t("faults.card.rangeTag")}</th>
                <th>{t("faults.history.colDate")}</th>
                <th>{t("faults.history.colDuration")}</th>
                <th>{t("faults.history.colStatus")}</th>
                <th>{t("faults.card.assignedTo")}</th>
              </tr>
            </thead>
            <tbody>
              {filtered.map((f) => {
                const isOpen = expanded === f.id;
                const rows = comments[f.id];
                return (
                  <Fragment key={f.id}>
                    <tr
                      className={`fx-history-row ${isOpen ? "is-open" : ""}`}
                      onClick={() => toggle(f.id)}
                    >
                      <td className="fx-col-toggle">
                        <span className="fx-toggle-btn" aria-hidden="true">
                          {isOpen ? (
                            <ChevronDown size={16} strokeWidth={2.2} />
                          ) : (
                            <ChevronRight size={16} strokeWidth={2.2} />
                          )}
                        </span>
                      </td>
                      <td>
                        <strong className="fx-history-line">{f.line_name}</strong>
                        <small className="fx-history-region">{f.region_name}</small>
                      </td>
                      <td className="fx-history-range">
                        {t("faults.card.rangeText", {
                          from: f.from_pole_seq ?? "?",
                          to: f.to_pole_seq ?? "?"
                        })}
                      </td>
                      <td className="fx-history-date">
                        {fmtDateTime(f.closed_at ?? f.resolved_at ?? f.opened_at, localeTag)}
                      </td>
                      <td className="fx-history-duration">{fmtDuration(f)}</td>
                      <td>
                        <span className={`fx-badge fx-badge--status-${f.status}`}>
                          <CheckCircle2 size={12} strokeWidth={2.4} />
                          {t(`faults.status.${f.status}`, { defaultValue: f.status })}
                        </span>
                      </td>
                      <td>
                        {f.assigned_to_full_name ?? f.assigned_to_username ?? (
                          <em className="fx-dim">{t("faults.card.noAssignee")}</em>
                        )}
                      </td>
                    </tr>

                    {isOpen ? (
                      <tr className="fx-history-detail-row">
                        <td colSpan={7}>
                          <div className="fx-history-detail">
                            {/* --- Kullanici yorumlari --- */}
                            <div className="fx-panel">
                              <div className="fx-panel-head">
                                <MessageSquare size={15} strokeWidth={2.1} />
                                {t("faults.history.commentPanel")}
                              </div>
                              {loadingComments === f.id && rows == null ? (
                                <p className="fx-dim">{t("common.loading")}</p>
                              ) : rows && rows.length > 0 ? (
                                <ul className="fx-comment-list">
                                  {rows.map((c) => (
                                    <li key={c.id}>
                                      <div className="fx-comment-meta">
                                        <strong>{c.author_username}</strong>
                                        <span>{fmtDateTime(c.created_at, localeTag)}</span>
                                      </div>
                                      <p>{c.body}</p>
                                    </li>
                                  ))}
                                </ul>
                              ) : (
                                <p className="fx-dim">{t("faults.history.noComment")}</p>
                              )}

                              <div className="fx-inline-form">
                                <textarea
                                  rows={2}
                                  value={commentDraft}
                                  onChange={(e) => setCommentDraft(e.target.value)}
                                  placeholder={t("faults.history.commentPlaceholder")}
                                />
                                <button
                                  type="button"
                                  className="fx-btn fx-btn--soft"
                                  disabled={saving || !commentDraft.trim()}
                                  onClick={() => void submitComment(f.id)}
                                >
                                  <MessageSquarePlus size={15} strokeWidth={2.1} />
                                  {t("faults.history.addComment")}
                                </button>
                              </div>
                            </div>

                            {/* --- Cozum aciklamasi (fault.note) --- */}
                            <div className="fx-panel">
                              <div className="fx-panel-head">
                                <Wrench size={15} strokeWidth={2.1} />
                                {t("faults.history.notePanel")}
                              </div>
                              {noteEditing === f.id ? (
                                <div className="fx-inline-form">
                                  <textarea
                                    rows={3}
                                    value={noteDraft}
                                    onChange={(e) => setNoteDraft(e.target.value)}
                                    placeholder={t("faults.history.notePlaceholder")}
                                  />
                                  <div className="fx-inline-form-actions">
                                    <button
                                      type="button"
                                      className="fx-btn fx-btn--ghost"
                                      onClick={() => setNoteEditing(null)}
                                      disabled={saving}
                                    >
                                      {t("common.cancel")}
                                    </button>
                                    <button
                                      type="button"
                                      className="fx-btn fx-btn--soft"
                                      onClick={() => void submitNote(f.id)}
                                      disabled={saving}
                                    >
                                      {t("common.save")}
                                    </button>
                                  </div>
                                </div>
                              ) : (
                                <>
                                  {f.note ? (
                                    <p className="fx-note-body">{f.note}</p>
                                  ) : (
                                    <p className="fx-dim">{t("faults.history.noNote")}</p>
                                  )}
                                  <button
                                    type="button"
                                    className="fx-btn fx-btn--soft"
                                    onClick={() => {
                                      setNoteDraft(f.note ?? "");
                                      setNoteEditing(f.id);
                                    }}
                                  >
                                    <PencilLine size={15} strokeWidth={2.1} />
                                    {f.note
                                      ? t("faults.history.editNote")
                                      : t("faults.history.addNote")}
                                  </button>
                                </>
                              )}
                            </div>
                          </div>
                        </td>
                      </tr>
                    ) : null}
                  </Fragment>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}
