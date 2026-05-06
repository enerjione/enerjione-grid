import { useEffect, useMemo, useState } from "react";
import { MapContainer, Marker, Polyline, TileLayer, Tooltip } from "react-leaflet";
import L from "leaflet";

import type { GridSnapshot } from "../../shared/api";
import type { FaultComment, FaultEvent, UserRead } from "../../shared/types";

type Props = {
  fault: FaultEvent;
  users: UserRead[];
  currentUsername: string;
  canAssign: boolean;
  gridSnapshot?: GridSnapshot | null;
  onClose: () => void;
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
  return new Date(iso).toLocaleString("tr-TR");
}

// Mini harita için sade direk pin'i
const miniPolePin = (label: string, isRed: boolean, isGreen: boolean) => {
  const color = isRed ? "#ef4444" : isGreen ? "#10b981" : "#475569";
  return L.divIcon({
    className: "fault-modal-pole-icon-wrap",
    html: `<div class="fault-modal-pole-icon" style="background:${color}">${label}</div>`,
    iconSize: [22, 22],
    iconAnchor: [11, 11]
  });
};

export function FaultDetailModal({
  fault,
  users,
  currentUsername,
  canAssign,
  gridSnapshot,
  onClose,
  onAssign,
  onUpdateStatus,
  onUpdateNote,
  onLoadComments,
  onAddComment
}: Props) {
  const [comments, setComments] = useState<FaultComment[]>([]);
  const [commentDraft, setCommentDraft] = useState("");
  const [noteDraft, setNoteDraft] = useState(fault.note ?? "");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    setNoteDraft(fault.note ?? "");
    setCommentDraft("");
    setError("");
    void (async () => {
      try {
        const list = await onLoadComments(fault.id);
        setComments(list);
      } catch (err) {
        setError(err instanceof Error ? err.message : "Yorumlar alınamadı.");
      }
    })();
  }, [fault.id, onLoadComments]);

  // ESC ile kapatma
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const userOptions = useMemo(
    () => [...users].sort((a, b) => a.full_name.localeCompare(b.full_name, "tr")),
    [users]
  );

  const canEdit = canAssign || fault.assigned_to_username === currentUsername;

  // Mini harita — arızanın bulunduğu hattin TUM direkleri sirayli polyline.
  // Ariza araligi = from_pole_seq..to_pole_seq arasindaki ardisik tum
  // direkleri takip eden parça (KIRMIZI KESIK). Dışındaki bölümler YESIL.
  // Onceden hatali olarak from_pole'dan to_pole'a DUZ cizgi cekiyordu —
  // hattin gercek geometrisini takip etmiyordu.
  const mapView = useMemo(() => {
    if (!gridSnapshot) return null;
    const linePoles = gridSnapshot.poles
      .filter((p) => p.line_id === fault.line_id)
      .sort((a, b) => a.sequence_no - b.sequence_no);
    if (linePoles.length === 0) return null;

    const fromSeq = fault.from_pole_seq ?? null;
    const toSeq = fault.to_pole_seq ?? null;
    const lo = fromSeq != null && toSeq != null ? Math.min(fromSeq, toSeq) : null;
    const hi = fromSeq != null && toSeq != null ? Math.max(fromSeq, toSeq) : null;

    // Hat polyline'ini 3 parcaya boluyoruz (sequence_no sirasiyla):
    //   pre   : sequence_no <= lo (arıza baslangici dahil) -> YESIL saglikli
    //   fault : lo..hi arasi tum direkler -> KIRMIZI KESIK (ariza araligi)
    //   post  : hi >= sequence_no -> YESIL saglikli
    // Boylece pre ve fault paylasiyor "lo" indeksli direk; fault ve post
    // paylasiyor "hi" indeksli direk — boylece polyline kopuk gozukmez.
    const preGreen: [number, number][] = [];
    const faultRed: [number, number][] = [];
    const postGreen: [number, number][] = [];
    if (lo !== null && hi !== null) {
      for (const p of linePoles) {
        const s = p.sequence_no;
        if (s <= lo) preGreen.push([p.latitude, p.longitude]);
        if (s >= lo && s <= hi) faultRed.push([p.latitude, p.longitude]);
        if (s >= hi) postGreen.push([p.latitude, p.longitude]);
      }
    } else {
      // sequence yoksa hepsini yesil ciz
      for (const p of linePoles) preGreen.push([p.latitude, p.longitude]);
    }

    // Bounds — sadece arıza aralığı varsa onu, yoksa tum hat
    const focusPoles =
      lo !== null && hi !== null
        ? linePoles.filter((p) => p.sequence_no >= lo && p.sequence_no <= hi)
        : linePoles;
    const lats = focusPoles.map((p) => p.latitude);
    const lons = focusPoles.map((p) => p.longitude);
    const center: [number, number] = [
      lats.reduce((a, b) => a + b, 0) / lats.length,
      lons.reduce((a, b) => a + b, 0) / lons.length
    ];
    const span = Math.max(
      Math.max(...lats) - Math.min(...lats),
      Math.max(...lons) - Math.min(...lons)
    );
    const zoom = span < 0.003 ? 16 : span < 0.01 ? 15 : span < 0.03 ? 14 : span < 0.1 ? 13 : 11;

    // Direk koordinat listesi (aralik icindekiler)
    const rangePoles = focusPoles.map((p) => ({
      id: p.id,
      sequence_no: p.sequence_no,
      name: p.name ?? `Direk #${p.sequence_no}`,
      latitude: p.latitude,
      longitude: p.longitude,
      isStart: p.id === fault.from_pole_id,
      isEnd: p.id === fault.to_pole_id
    }));

    return {
      preGreen,
      faultRed,
      postGreen,
      center,
      zoom,
      polesWithRole: linePoles.map((p) => ({
        p,
        isFromFault: p.id === fault.from_pole_id,
        isToFault: p.id === fault.to_pole_id,
        isInFaultRange:
          lo !== null && hi !== null && p.sequence_no >= lo && p.sequence_no <= hi
      })),
      rangePoles
    };
  }, [
    gridSnapshot,
    fault.line_id,
    fault.from_pole_id,
    fault.to_pole_id,
    fault.from_pole_seq,
    fault.to_pole_seq
  ]);

  const handleAssign = async (newUsername: string) => {
    setSaving(true);
    setError("");
    try {
      await onAssign(fault.id, newUsername || null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Atama yapılamadı.");
    } finally {
      setSaving(false);
    }
  };
  const handleStatus = async (newStatus: string) => {
    setSaving(true);
    setError("");
    try {
      await onUpdateStatus(fault.id, newStatus);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Durum güncellenemedi.");
    } finally {
      setSaving(false);
    }
  };
  const handleSaveNote = async () => {
    setSaving(true);
    setError("");
    try {
      await onUpdateNote(fault.id, noteDraft.trim() || null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Not kaydedilemedi.");
    } finally {
      setSaving(false);
    }
  };
  const handleAddComment = async () => {
    const body = commentDraft.trim();
    if (!body) return;
    setSaving(true);
    setError("");
    try {
      await onAddComment(fault.id, body);
      const list = await onLoadComments(fault.id);
      setComments(list);
      setCommentDraft("");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Yorum eklenemedi.");
    } finally {
      setSaving(false);
    }
  };

  const statusColor = STATUS_COLOR[fault.status] ?? "#64748b";

  return (
    <div className="fault-modal-backdrop" onClick={onClose}>
      <div
        className="fault-modal"
        onClick={(e) => e.stopPropagation()}
        role="dialog"
        aria-modal="true"
      >
        <button
          type="button"
          className="fault-modal-close"
          onClick={onClose}
          aria-label="Kapat"
        >
          <span className="material-symbols-outlined">close</span>
        </button>

        <header className="fault-modal-head">
          <div className="fault-modal-head-left">
            <span
              className="fault-modal-status-pill"
              style={{ background: `${statusColor}22`, color: statusColor }}
            >
              {STATUS_LABEL[fault.status] ?? fault.status}
            </span>
            <h2>{fault.line_name}</h2>
            <div className="fault-modal-breadcrumbs">
              <span className="material-symbols-outlined">place</span>
              <span>{fault.region_name}</span>
              <span className="fault-modal-bc-sep">·</span>
              <span>{fault.line_name}</span>
              <span className="fault-modal-bc-sep">·</span>
              <strong>
                Direk #{fault.from_pole_seq} — #{fault.to_pole_seq}
              </strong>
            </div>
          </div>
          <div className="fault-modal-head-right">
            <div className="fault-modal-head-meta">
              <span className="fault-modal-head-label">Açılış</span>
              <strong>{fmtDate(fault.opened_at)}</strong>
            </div>
          </div>
        </header>

        <div className="fault-modal-body">
          {/* Sol kolon: harita + cihaz bilgisi */}
          <div className="fault-modal-left">
            <div className="fault-modal-section">
              <h4>Konum</h4>
              {mapView ? (
                <div className="fault-modal-map-wrap">
                  <MapContainer
                    center={mapView.center}
                    zoom={mapView.zoom}
                    className="fault-modal-map"
                    scrollWheelZoom={false}
                    dragging
                    doubleClickZoom={false}
                    attributionControl={false}
                  >
                    <TileLayer url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png" />
                    {/* Sırayla: pre yeşil, ariza kirmizi kesik, post yeşil.
                        Üç parça hattin gercek geometrisini takip eder; eski
                        from->to duz cizgi yanlistı. */}
                    {mapView.preGreen.length >= 2 ? (
                      <Polyline
                        positions={mapView.preGreen}
                        pathOptions={{ color: "#16a34a", weight: 4, opacity: 0.85 }}
                      />
                    ) : null}
                    {mapView.postGreen.length >= 2 ? (
                      <Polyline
                        positions={mapView.postGreen}
                        pathOptions={{ color: "#16a34a", weight: 4, opacity: 0.85 }}
                      />
                    ) : null}
                    {mapView.faultRed.length >= 2 ? (
                      <Polyline
                        positions={mapView.faultRed}
                        pathOptions={{
                          color: "#ef4444",
                          weight: 6,
                          opacity: 0.95,
                          dashArray: "10 6"
                        }}
                      />
                    ) : null}
                    {mapView.polesWithRole.map(({ p, isFromFault, isToFault, isInFaultRange }) => (
                      <Marker
                        key={p.id}
                        position={[p.latitude, p.longitude]}
                        icon={miniPolePin(
                          String(p.sequence_no),
                          isFromFault,
                          isToFault || (isInFaultRange && !isFromFault)
                        )}
                      >
                        <Tooltip>
                          {p.name ?? `Direk #${p.sequence_no}`}
                          {isFromFault ? " (Arıza başlangıcı)" : ""}
                          {isToFault ? " (Arıza bitişi)" : ""}
                        </Tooltip>
                      </Marker>
                    ))}
                  </MapContainer>
                  <div className="fault-modal-map-legend">
                    <span><i style={{ background: "#ef4444" }} /> Arıza aralığı (kırmızı kesik)</span>
                    <span><i style={{ background: "#16a34a" }} /> Hat sağlıklı bölüm</span>
                  </div>
                </div>
              ) : (
                <div className="fault-modal-map-empty">
                  Harita verisi bulunamadı.
                </div>
              )}
            </div>

            {/* Arıza aralığındaki direklerin koordinat listesi */}
            {mapView && mapView.rangePoles.length > 0 ? (
              <div className="fault-modal-section">
                <h4>Arıza Aralığındaki Direkler</h4>
                <ul className="fault-modal-poles-list">
                  {mapView.rangePoles.map((rp) => (
                    <li
                      key={rp.id}
                      className={`fault-modal-pole-row ${
                        rp.isStart ? "is-start" : rp.isEnd ? "is-end" : ""
                      }`}
                    >
                      <span className="fault-modal-pole-seq">#{rp.sequence_no}</span>
                      <div className="fault-modal-pole-name">
                        <strong>{rp.name}</strong>
                        {rp.isStart ? (
                          <span className="fault-modal-pole-tag fault-modal-pole-tag--red">
                            Arıza başlangıcı
                          </span>
                        ) : rp.isEnd ? (
                          <span className="fault-modal-pole-tag fault-modal-pole-tag--green">
                            Arıza bitişi
                          </span>
                        ) : null}
                      </div>
                      <span className="fault-modal-pole-coords" title="Enlem, Boylam">
                        {rp.latitude.toFixed(6)}, {rp.longitude.toFixed(6)}
                      </span>
                    </li>
                  ))}
                </ul>
              </div>
            ) : null}

            <div className="fault-modal-section">
              <h4>Arıza Tespit Eden Cihazlar</h4>
              <div className="fault-modal-devices">
                <div className="fault-modal-device fault-modal-device--red">
                  <span className="fault-modal-device-dot" />
                  <div>
                    <span className="fault-modal-device-role">
                      Son arıza algılayan cihaz
                    </span>
                    <strong>{fault.last_red_device_name ?? "—"}</strong>
                    {fault.last_red_device_code ? (
                      <small>{fault.last_red_device_code}</small>
                    ) : null}
                  </div>
                </div>
                <div className="fault-modal-device-arrow">
                  <span className="material-symbols-outlined">arrow_forward</span>
                </div>
                <div className="fault-modal-device fault-modal-device--green">
                  <span className="fault-modal-device-dot" />
                  <div>
                    <span className="fault-modal-device-role">
                      İlk arıza algılamayan cihaz
                    </span>
                    <strong>
                      {fault.first_green_device_name ?? "Hat ucu (cihaz yok)"}
                    </strong>
                    {fault.first_green_device_code ? (
                      <small>{fault.first_green_device_code}</small>
                    ) : null}
                  </div>
                </div>
              </div>
              <p className="fault-modal-devices-hint">
                Bu iki cihaz arasındaki direk-direk bölgesi sahada arıza
                yapılması muhtemel kısımdır.
              </p>
            </div>
          </div>

          {/* Orta kolon: ticket yönetimi (sorumluluk + durum + not) */}
          <div className="fault-modal-mid">
            <div className="fault-modal-section">
              <h4>Sorumluluk</h4>
              <div className="fault-modal-row">
                <label className="fault-modal-label">Atanan</label>
                {canAssign ? (
                  <select
                    value={fault.assigned_to_username ?? ""}
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
                  <span className="fault-modal-value">
                    {fault.assigned_to_full_name ?? fault.assigned_to_username ?? "—"}
                  </span>
                )}
              </div>
              <div className="fault-modal-row">
                <label className="fault-modal-label">Durum</label>
                <div className="fault-modal-status-buttons">
                  {(["assigned", "in_progress", "resolved", "closed"] as const).map((s) => (
                    <button
                      key={s}
                      type="button"
                      className={`fault-modal-status-btn ${fault.status === s ? "active" : ""}`}
                      onClick={() => void handleStatus(s)}
                      disabled={saving || !canEdit}
                      style={
                        fault.status === s
                          ? { background: STATUS_COLOR[s], color: "#fff", borderColor: "transparent" }
                          : undefined
                      }
                    >
                      {STATUS_LABEL[s]}
                    </button>
                  ))}
                </div>
              </div>
            </div>

            <div className="fault-modal-section">
              <h4>Kısa Not</h4>
              <textarea
                rows={3}
                value={noteDraft}
                onChange={(e) => setNoteDraft(e.target.value)}
                disabled={saving || !canEdit}
                placeholder="Kısa açıklama (opsiyonel)…"
              />
              {canEdit ? (
                <button
                  type="button"
                  className="fault-modal-save-btn"
                  onClick={() => void handleSaveNote()}
                  disabled={saving}
                >
                  Notu Kaydet
                </button>
              ) : null}
            </div>

            {error ? <div className="fault-modal-error">{error}</div> : null}
          </div>

          {/* Sağ kolon: SADECE saha raporu / yorumlar */}
          <div className="fault-modal-comments-col">
            <div className="fault-modal-section fault-modal-section--comments">
              <h4>
                <span className="material-symbols-outlined" aria-hidden="true">forum</span>
                Saha Raporu / Yorumlar
                {comments.length > 0 ? <span className="fault-modal-count">{comments.length}</span> : null}
              </h4>
              <ul className="fault-modal-comments">
                {comments.length === 0 ? (
                  <li className="fault-modal-comments-empty">
                    Henüz yorum yok. Saha gözlemi veya yapılan işlemleri buradan paylaşın.
                  </li>
                ) : (
                  comments.map((c) => (
                    <li key={c.id} className="fault-modal-comment">
                      <header>
                        <span className="fault-modal-comment-author">
                          <span className="fault-modal-comment-avatar">
                            {(c.author_username || "?").substring(0, 2).toUpperCase()}
                          </span>
                          <strong>{c.author_username}</strong>
                        </span>
                        <span className="fault-modal-comment-time">{fmtDate(c.created_at)}</span>
                      </header>
                      <p>{c.body}</p>
                    </li>
                  ))
                )}
              </ul>
              {canEdit ? (
                <div className="fault-modal-comment-add">
                  <textarea
                    rows={3}
                    placeholder="Saha gözlemi, yapılan bakım/onarım adımları, parça değişimi…"
                    value={commentDraft}
                    onChange={(e) => setCommentDraft(e.target.value)}
                    disabled={saving}
                  />
                  <button
                    type="button"
                    onClick={() => void handleAddComment()}
                    disabled={saving || !commentDraft.trim()}
                  >
                    <span className="material-symbols-outlined">send</span>
                    Yorum Ekle
                  </button>
                </div>
              ) : null}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
