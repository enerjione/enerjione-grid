/** Mühendislik > Hat Yönetimi.
 *
 * Hiyerarsi: Bolge -> Hat -> Direk (sirali) -> Segment (iki direk arasi).
 * Cihaz-segment baglama bu sayfada YAPILMAZ — ayri "Cihaz Atama" sayfasi var.
 *
 * Layout: 3 kolon
 *   [Bölgeler] [Hatlar (seçili bölge)] [Hat Detayı + Harita]
 */
import { useEffect, useMemo, useState, type FormEvent } from "react";
import { MapContainer, Marker, Polyline, Popup, TileLayer, useMapEvents } from "react-leaflet";
import L from "leaflet";

import {
  createLine,
  createPole,
  createRegion,
  deleteLine,
  deletePole,
  deleteRegion,
  fetchLineDetail,
  fetchLines,
  fetchRegions,
  updateLine,
  updatePole,
  updateRegion
} from "../../shared/api";
import type { Line, LineDetail, Pole, Region } from "../../shared/types";
import { useToast } from "../../components/ToastProvider";

type Props = {
  accessToken: string;
};

const DEFAULT_REGION_COLOR = "#2563eb";

const poleIcon = (selected: boolean) =>
  L.divIcon({
    className: "grid-pole-marker-wrapper",
    html: `<span class="grid-pole-marker ${selected ? "grid-pole-marker--selected" : ""}"></span>`,
    iconSize: [16, 16],
    iconAnchor: [8, 8]
  });

export function GridManagementPanel({ accessToken }: Props) {
  const toast = useToast();

  // ----- Veri state -----
  const [regions, setRegions] = useState<Region[]>([]);
  const [lines, setLines] = useState<Line[]>([]);
  const [detail, setDetail] = useState<LineDetail | null>(null);
  const [selectedRegionId, setSelectedRegionId] = useState<number | null>(null);
  const [selectedLineId, setSelectedLineId] = useState<number | null>(null);

  // ----- UI state -----
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  // Modal state'leri
  const [regionModalOpen, setRegionModalOpen] = useState(false);
  const [editingRegion, setEditingRegion] = useState<Region | null>(null);
  const [lineModalOpen, setLineModalOpen] = useState(false);
  const [editingLine, setEditingLine] = useState<Line | null>(null);
  const [poleModalOpen, setPoleModalOpen] = useState(false);
  const [editingPole, setEditingPole] = useState<Pole | null>(null);

  // ----- Veri yukleme -----
  useEffect(() => {
    void reloadRegions();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (selectedRegionId === null) {
      setLines([]);
      setSelectedLineId(null);
      return;
    }
    void reloadLines(selectedRegionId);
  }, [selectedRegionId]);

  useEffect(() => {
    if (selectedLineId === null) {
      setDetail(null);
      return;
    }
    void reloadDetail(selectedLineId);
  }, [selectedLineId]);

  const reloadRegions = async () => {
    try {
      const rows = await fetchRegions(accessToken);
      setRegions(rows);
      if (rows.length > 0 && selectedRegionId === null) {
        setSelectedRegionId(rows[0].id);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Bölgeler alınamadı.");
    }
  };

  const reloadLines = async (regionId: number) => {
    try {
      const rows = await fetchLines(accessToken, regionId);
      setLines(rows);
      if (rows.length > 0 && (selectedLineId === null || !rows.some((l) => l.id === selectedLineId))) {
        setSelectedLineId(rows[0].id);
      } else if (rows.length === 0) {
        setSelectedLineId(null);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Hatlar alınamadı.");
    }
  };

  const reloadDetail = async (lineId: number) => {
    try {
      const d = await fetchLineDetail(accessToken, lineId);
      setDetail(d);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Hat detayı alınamadı.");
    }
  };

  // Harita merkezi: hat detayı varsa direklerin ortalaması, yoksa Türkiye merkezi
  const mapCenter = useMemo<[number, number]>(() => {
    if (detail && detail.poles.length > 0) {
      const avgLat = detail.poles.reduce((s, p) => s + p.latitude, 0) / detail.poles.length;
      const avgLon = detail.poles.reduce((s, p) => s + p.longitude, 0) / detail.poles.length;
      return [avgLat, avgLon];
    }
    return [39.0, 35.0];
  }, [detail]);

  const mapZoom = detail && detail.poles.length > 0 ? 12 : 6;

  // Polyline: direkler sequence_no sırasıyla
  const polyline = useMemo<[number, number][]>(() => {
    if (!detail) return [];
    return detail.poles
      .slice()
      .sort((a, b) => a.sequence_no - b.sequence_no)
      .map((p) => [p.latitude, p.longitude]);
  }, [detail]);

  const selectedRegion = regions.find((r) => r.id === selectedRegionId) ?? null;
  const selectedLine = lines.find((l) => l.id === selectedLineId) ?? null;

  return (
    <section className="tab-panel grid-mgmt-panel">
      <div className="grid-mgmt-layout">
        {/* SOL — Bölgeler */}
        <div className="grid-mgmt-col grid-mgmt-col-regions">
          <div className="grid-mgmt-col-head">
            <h4>Bölgeler</h4>
            <button
              className="add-user-btn"
              onClick={() => {
                setEditingRegion(null);
                setRegionModalOpen(true);
              }}
            >
              + Bölge
            </button>
          </div>
          <div className="grid-mgmt-list">
            {regions.length === 0 ? (
              <p className="helper-text">Henüz bölge yok.</p>
            ) : null}
            {regions.map((r) => (
              <div
                key={r.id}
                className={`grid-mgmt-list-item ${selectedRegionId === r.id ? "active" : ""}`}
                onClick={() => setSelectedRegionId(r.id)}
              >
                <span
                  className="grid-mgmt-color-dot"
                  style={{ background: r.color || DEFAULT_REGION_COLOR }}
                />
                <div className="grid-mgmt-list-item-main">
                  <strong>{r.name}</strong>
                  <code className="grid-mgmt-list-code">{r.code}</code>
                </div>
                <span className="grid-mgmt-list-count">{r.line_count ?? 0} hat</span>
                <div className="grid-mgmt-list-actions">
                  <button
                    type="button"
                    className="icon-btn"
                    title="Düzenle"
                    onClick={(e) => {
                      e.stopPropagation();
                      setEditingRegion(r);
                      setRegionModalOpen(true);
                    }}
                  >
                    ✎
                  </button>
                  <button
                    type="button"
                    className="icon-btn icon-btn-danger"
                    title="Sil"
                    onClick={(e) => {
                      e.stopPropagation();
                      void handleDeleteRegion(r);
                    }}
                  >
                    ✕
                  </button>
                </div>
              </div>
            ))}
          </div>
        </div>

        {/* ORTA — Seçili bölgenin hatları */}
        <div className="grid-mgmt-col grid-mgmt-col-lines">
          <div className="grid-mgmt-col-head">
            <h4>Hatlar {selectedRegion ? `· ${selectedRegion.name}` : ""}</h4>
            <button
              className="add-user-btn"
              disabled={!selectedRegion}
              onClick={() => {
                setEditingLine(null);
                setLineModalOpen(true);
              }}
            >
              + Hat
            </button>
          </div>
          <div className="grid-mgmt-list">
            {!selectedRegion ? (
              <p className="helper-text">Önce soldan bir bölge seçin.</p>
            ) : lines.length === 0 ? (
              <p className="helper-text">Bu bölgede henüz hat yok.</p>
            ) : null}
            {lines.map((l) => (
              <div
                key={l.id}
                className={`grid-mgmt-list-item ${selectedLineId === l.id ? "active" : ""}`}
                onClick={() => setSelectedLineId(l.id)}
              >
                <span
                  className="grid-mgmt-color-dot"
                  style={{ background: l.color || selectedRegion?.color || DEFAULT_REGION_COLOR }}
                />
                <div className="grid-mgmt-list-item-main">
                  <strong>{l.name}</strong>
                  <code className="grid-mgmt-list-code">{l.code}</code>
                </div>
                <span className="grid-mgmt-list-count">
                  {l.pole_count ?? 0} direk · {l.segment_count ?? 0} segment
                </span>
                <div className="grid-mgmt-list-actions">
                  <button
                    type="button"
                    className="icon-btn"
                    title="Düzenle"
                    onClick={(e) => {
                      e.stopPropagation();
                      setEditingLine(l);
                      setLineModalOpen(true);
                    }}
                  >
                    ✎
                  </button>
                  <button
                    type="button"
                    className="icon-btn icon-btn-danger"
                    title="Sil"
                    onClick={(e) => {
                      e.stopPropagation();
                      void handleDeleteLine(l);
                    }}
                  >
                    ✕
                  </button>
                </div>
              </div>
            ))}
          </div>
        </div>

        {/* SAG — Hat detayı: harita + direk listesi */}
        <div className="grid-mgmt-col grid-mgmt-col-detail">
          <div className="grid-mgmt-col-head">
            <h4>Hat Detayı {selectedLine ? `· ${selectedLine.name}` : ""}</h4>
            <button
              className="add-user-btn"
              disabled={!selectedLine}
              onClick={() => {
                setEditingPole(null);
                setPoleModalOpen(true);
              }}
            >
              + Direk
            </button>
          </div>

          {!selectedLine ? (
            <p className="helper-text">Bir hat seçin; direkleri haritada görürsünüz.</p>
          ) : (
            <>
              <div className="grid-mgmt-map-shell">
                <MapContainer center={mapCenter} zoom={mapZoom} className="grid-mgmt-map">
                  <TileLayer url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png" />
                  {polyline.length >= 2 ? (
                    <Polyline
                      positions={polyline}
                      pathOptions={{
                        color: selectedLine.color || selectedRegion?.color || DEFAULT_REGION_COLOR,
                        weight: 4,
                        opacity: 0.8
                      }}
                    />
                  ) : null}
                  {detail?.poles.map((p) => (
                    <Marker
                      key={p.id}
                      position={[p.latitude, p.longitude]}
                      icon={poleIcon(false)}
                    >
                      <Popup>
                        <div>
                          <strong>Direk #{p.sequence_no}</strong>
                          <br />
                          {p.name ?? ""}
                          <br />
                          <button
                            type="button"
                            className="primary-btn"
                            style={{ marginTop: 6 }}
                            onClick={() => {
                              setEditingPole(p);
                              setPoleModalOpen(true);
                            }}
                          >
                            Düzenle
                          </button>
                        </div>
                      </Popup>
                    </Marker>
                  ))}
                </MapContainer>
              </div>

              <div className="grid-mgmt-pole-list">
                <div className="grid-mgmt-pole-list-head">
                  <strong>Direkler</strong>
                  <span className="helper-text">
                    {detail?.poles.length ?? 0} direk
                  </span>
                </div>
                <table className="values-table grid-mgmt-pole-table">
                  <thead>
                    <tr>
                      <th style={{ width: 60 }}>Sıra</th>
                      <th>İsim</th>
                      <th>Enlem</th>
                      <th>Boylam</th>
                      <th style={{ width: 90 }}>İşlem</th>
                    </tr>
                  </thead>
                  <tbody>
                    {(detail?.poles ?? [])
                      .slice()
                      .sort((a, b) => a.sequence_no - b.sequence_no)
                      .map((p) => (
                        <tr key={p.id}>
                          <td>{p.sequence_no}</td>
                          <td>{p.name ?? "—"}</td>
                          <td>{p.latitude.toFixed(6)}</td>
                          <td>{p.longitude.toFixed(6)}</td>
                          <td className="actions-cell">
                            <button
                              type="button"
                              className="icon-btn"
                              onClick={() => {
                                setEditingPole(p);
                                setPoleModalOpen(true);
                              }}
                            >
                              ✎
                            </button>
                            <button
                              type="button"
                              className="icon-btn icon-btn-danger"
                              onClick={() => void handleDeletePole(p)}
                            >
                              ✕
                            </button>
                          </td>
                        </tr>
                      ))}
                  </tbody>
                </table>
              </div>
            </>
          )}
        </div>
      </div>

      {error ? <p className="error-text">{error}</p> : null}

      {regionModalOpen ? (
        <RegionModal
          initial={editingRegion}
          busy={busy}
          onClose={() => setRegionModalOpen(false)}
          onSubmit={async (payload) => {
            setBusy(true);
            try {
              if (editingRegion) {
                await updateRegion(accessToken, editingRegion.id, payload);
                toast.success("Bölge güncellendi.");
              } else {
                await createRegion(accessToken, payload);
                toast.success("Bölge eklendi.");
              }
              setRegionModalOpen(false);
              await reloadRegions();
            } catch (err) {
              toast.error(err instanceof Error ? err.message : "Bölge kaydedilemedi.");
            } finally {
              setBusy(false);
            }
          }}
        />
      ) : null}

      {lineModalOpen && selectedRegion ? (
        <LineModal
          regionId={selectedRegion.id}
          initial={editingLine}
          busy={busy}
          onClose={() => setLineModalOpen(false)}
          onSubmit={async (payload) => {
            setBusy(true);
            try {
              if (editingLine) {
                await updateLine(accessToken, editingLine.id, payload);
                toast.success("Hat güncellendi.");
              } else {
                await createLine(accessToken, payload);
                toast.success("Hat eklendi.");
              }
              setLineModalOpen(false);
              await reloadLines(selectedRegion.id);
            } catch (err) {
              toast.error(err instanceof Error ? err.message : "Hat kaydedilemedi.");
            } finally {
              setBusy(false);
            }
          }}
        />
      ) : null}

      {poleModalOpen && selectedLine ? (
        <PoleModal
          lineId={selectedLine.id}
          initial={editingPole}
          existingSequences={(detail?.poles ?? []).map((p) => p.sequence_no)}
          busy={busy}
          onClose={() => setPoleModalOpen(false)}
          onSubmit={async (payload) => {
            setBusy(true);
            try {
              if (editingPole) {
                await updatePole(accessToken, editingPole.id, payload);
                toast.success("Direk güncellendi.");
              } else {
                await createPole(accessToken, payload);
                toast.success("Direk eklendi.");
              }
              setPoleModalOpen(false);
              await reloadDetail(selectedLine.id);
              // Hat detayındaki pole_count etkilendi → liste rozetlerini yenile
              if (selectedRegion) await reloadLines(selectedRegion.id);
            } catch (err) {
              toast.error(err instanceof Error ? err.message : "Direk kaydedilemedi.");
            } finally {
              setBusy(false);
            }
          }}
        />
      ) : null}
    </section>
  );

  // ----- silme yardimcilari -----

  async function handleDeleteRegion(r: Region) {
    if (!window.confirm(`"${r.name}" bölgesi ve altındaki tüm hatlar/direkler/segmentler silinsin mi?`)) return;
    try {
      await deleteRegion(accessToken, r.id);
      toast.success("Bölge silindi.");
      if (selectedRegionId === r.id) setSelectedRegionId(null);
      await reloadRegions();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Bölge silinemedi.");
    }
  }
  async function handleDeleteLine(l: Line) {
    if (!window.confirm(`"${l.name}" hattı ve altındaki tüm direkler/segmentler silinsin mi?`)) return;
    try {
      await deleteLine(accessToken, l.id);
      toast.success("Hat silindi.");
      if (selectedLineId === l.id) setSelectedLineId(null);
      if (selectedRegionId !== null) await reloadLines(selectedRegionId);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Hat silinemedi.");
    }
  }
  async function handleDeletePole(p: Pole) {
    if (!window.confirm(`Direk #${p.sequence_no} silinsin mi? Bu direğe bağlı segmentler de kaldırılır.`)) return;
    try {
      await deletePole(accessToken, p.id);
      toast.success("Direk silindi.");
      if (selectedLineId !== null) await reloadDetail(selectedLineId);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Direk silinemedi.");
    }
  }
}

// ============= MODALS =============

function RegionModal({
  initial,
  busy,
  onClose,
  onSubmit
}: {
  initial: Region | null;
  busy: boolean;
  onClose: () => void;
  onSubmit: (payload: Partial<Region>) => Promise<void>;
}) {
  const [code, setCode] = useState(initial?.code ?? "");
  const [name, setName] = useState(initial?.name ?? "");
  const [description, setDescription] = useState(initial?.description ?? "");
  const [color, setColor] = useState(initial?.color ?? DEFAULT_REGION_COLOR);
  const [isActive, setIsActive] = useState(initial?.is_active ?? true);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    await onSubmit({
      code: code.trim(),
      name: name.trim(),
      description: description.trim() || null,
      color,
      is_active: isActive
    });
  };

  return (
    <div className="settings-modal-backdrop">
      <form className="settings-modal" onSubmit={submit}>
        <h3>{initial ? "Bölgeyi Düzenle" : "Yeni Bölge"}</h3>
        <label>
          Kod <input value={code} onChange={(e) => setCode(e.target.value)} required />
        </label>
        <label>
          Ad <input value={name} onChange={(e) => setName(e.target.value)} required />
        </label>
        <label>
          Açıklama
          <input value={description} onChange={(e) => setDescription(e.target.value)} />
        </label>
        <label>
          Renk
          <input type="color" value={color} onChange={(e) => setColor(e.target.value)} />
        </label>
        <label className="notify-option">
          <input type="checkbox" checked={isActive} onChange={(e) => setIsActive(e.target.checked)} />
          Aktif
        </label>
        <div className="settings-actions">
          <button type="button" onClick={onClose} disabled={busy}>İptal</button>
          <button type="submit" className="primary-btn" disabled={busy}>
            {busy ? "..." : "Kaydet"}
          </button>
        </div>
      </form>
    </div>
  );
}

function LineModal({
  regionId,
  initial,
  busy,
  onClose,
  onSubmit
}: {
  regionId: number;
  initial: Line | null;
  busy: boolean;
  onClose: () => void;
  onSubmit: (payload: Partial<Line>) => Promise<void>;
}) {
  const [code, setCode] = useState(initial?.code ?? "");
  const [name, setName] = useState(initial?.name ?? "");
  const [description, setDescription] = useState(initial?.description ?? "");
  const [color, setColor] = useState(initial?.color ?? "");
  const [isActive, setIsActive] = useState(initial?.is_active ?? true);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    await onSubmit({
      region_id: regionId,
      code: code.trim(),
      name: name.trim(),
      description: description.trim() || null,
      color: color || null,
      is_active: isActive
    });
  };

  return (
    <div className="settings-modal-backdrop">
      <form className="settings-modal" onSubmit={submit}>
        <h3>{initial ? "Hattı Düzenle" : "Yeni Hat"}</h3>
        <label>
          Kod <input value={code} onChange={(e) => setCode(e.target.value)} required />
        </label>
        <label>
          Ad <input value={name} onChange={(e) => setName(e.target.value)} required />
        </label>
        <label>
          Açıklama
          <input value={description} onChange={(e) => setDescription(e.target.value)} />
        </label>
        <label>
          Renk (boş = bölgenin rengi)
          <input
            type="color"
            value={color || "#94a3b8"}
            onChange={(e) => setColor(e.target.value)}
          />
        </label>
        <label className="notify-option">
          <input type="checkbox" checked={isActive} onChange={(e) => setIsActive(e.target.checked)} />
          Aktif
        </label>
        <div className="settings-actions">
          <button type="button" onClick={onClose} disabled={busy}>İptal</button>
          <button type="submit" className="primary-btn" disabled={busy}>
            {busy ? "..." : "Kaydet"}
          </button>
        </div>
      </form>
    </div>
  );
}

function PoleModal({
  lineId,
  initial,
  existingSequences,
  busy,
  onClose,
  onSubmit
}: {
  lineId: number;
  initial: Pole | null;
  existingSequences: number[];
  busy: boolean;
  onClose: () => void;
  onSubmit: (payload: Partial<Pole>) => Promise<void>;
}) {
  const nextSeq = useMemo(() => {
    if (initial) return initial.sequence_no;
    if (existingSequences.length === 0) return 1;
    return Math.max(...existingSequences) + 1;
  }, [existingSequences, initial]);

  const [sequenceNo, setSequenceNo] = useState(String(nextSeq));
  const [name, setName] = useState(initial?.name ?? "");
  const [latitude, setLatitude] = useState(String(initial?.latitude ?? ""));
  const [longitude, setLongitude] = useState(String(initial?.longitude ?? ""));

  const handleMapClick = (lat: number, lon: number) => {
    setLatitude(lat.toFixed(6));
    setLongitude(lon.toFixed(6));
  };

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    const lat = Number(latitude);
    const lon = Number(longitude);
    const seq = Number(sequenceNo);
    if (!Number.isFinite(lat) || !Number.isFinite(lon)) return;
    if (!Number.isFinite(seq) || seq < 1) return;
    await onSubmit({
      line_id: lineId,
      sequence_no: seq,
      name: name.trim() || null,
      latitude: lat,
      longitude: lon
    });
  };

  return (
    <div className="settings-modal-backdrop">
      <form className="settings-modal grid-pole-modal" onSubmit={submit}>
        <h3>{initial ? `Direği Düzenle (#${initial.sequence_no})` : "Yeni Direk"}</h3>

        <div className="grid-pole-modal-grid">
          <div>
            <label>
              Sıra No
              <input
                type="number"
                min={1}
                value={sequenceNo}
                onChange={(e) => setSequenceNo(e.target.value)}
                required
              />
            </label>
            <label>
              İsim (opsiyonel)
              <input value={name} onChange={(e) => setName(e.target.value)} placeholder="Direk-47" />
            </label>
            <div className="grid-pole-coords">
              <label>
                Enlem
                <input
                  type="number"
                  step="0.000001"
                  value={latitude}
                  onChange={(e) => setLatitude(e.target.value)}
                  required
                />
              </label>
              <label>
                Boylam
                <input
                  type="number"
                  step="0.000001"
                  value={longitude}
                  onChange={(e) => setLongitude(e.target.value)}
                  required
                />
              </label>
            </div>
            <p className="helper-text">Haritaya tıklayarak konumu seçebilirsiniz.</p>
          </div>

          <div className="grid-pole-modal-map-shell">
            <MapContainer
              center={[Number(latitude) || 39, Number(longitude) || 35]}
              zoom={Number(latitude) && Number(longitude) ? 13 : 6}
              className="grid-pole-modal-map"
            >
              <TileLayer url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png" />
              <PoleMapPicker
                lat={Number(latitude) || 0}
                lon={Number(longitude) || 0}
                onPick={handleMapClick}
              />
            </MapContainer>
          </div>
        </div>

        <div className="settings-actions">
          <button type="button" onClick={onClose} disabled={busy}>İptal</button>
          <button type="submit" className="primary-btn" disabled={busy}>
            {busy ? "..." : "Kaydet"}
          </button>
        </div>
      </form>
    </div>
  );
}

function PoleMapPicker({
  lat,
  lon,
  onPick
}: {
  lat: number;
  lon: number;
  onPick: (lat: number, lon: number) => void;
}) {
  useMapEvents({
    click(event) {
      onPick(Number(event.latlng.lat.toFixed(6)), Number(event.latlng.lng.toFixed(6)));
    }
  });
  if (!lat && !lon) return null;
  return <Marker position={[lat, lon]} icon={poleIcon(true)} />;
}
