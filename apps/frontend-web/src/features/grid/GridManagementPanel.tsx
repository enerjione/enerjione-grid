/** Mühendislik > Hat Yönetimi.
 *
 * Hiyerarsi: Bolge -> Hat -> Direk (sirali) -> Segment (iki direk arasi).
 *
 * Sayfa duzeni:
 *   [Bölgeler]  [Hatlar]  [Hat Detayı: HARITA / LISTE sekmeleri]
 *
 * Harita sekmesi:
 *   - "+ Direk Ekle Modu" toggle: ON iken haritaya tıkla -> direk ekle
 *   - Hattaki direkler sequence_no sırasıyla polyline ile birleştirilir
 *   - Polyline'in bir SEGMENTINE sağ tıkla -> bağlam menüsü:
 *       "Cihaz ata", "Cihazı kaldır", "Başka segmente taşı"
 *   - Segment üzerindeki cihaz orta noktada marker olarak görünür
 *
 * Liste sekmesi:
 *   - Direkler: sequence_no sırasıyla, drag-to-reorder, "Tersine çevir" butonu
 *   - Her direk satırının altında o direkten sonraki segment + atanmış cihaz
 */
import { useEffect, useMemo, useState, type FormEvent } from "react";
import { MapContainer, Marker, Polyline, TileLayer, Tooltip, useMapEvents } from "react-leaflet";
import L from "leaflet";

import {
  createLine,
  createPole,
  createRegion,
  createSegment,
  deleteLine,
  deletePole,
  deleteRegion,
  deleteSegment,
  fetchLineDetail,
  fetchLines,
  fetchRegions,
  reorderPoles,
  reversePoles,
  updateLine,
  updateRegion,
  updateSegment
} from "../../shared/api";
import type { DeviceRow, Line, LineDetail, LineSegment, Pole, Region } from "../../shared/types";
import { useToast } from "../../components/ToastProvider";

type Props = {
  accessToken: string;
  devices: DeviceRow[];
};

type DetailTab = "map" | "list";

const DEFAULT_REGION_COLOR = "#2563eb";

const poleIcon = (label: string, isStart: boolean, isEnd: boolean) => {
  const cls = isStart ? "is-start" : isEnd ? "is-end" : "";
  return L.divIcon({
    className: "grid-pole-leaflet-wrap",
    html: `<div class="grid-pole-pin ${cls}"><span>${label}</span></div>`,
    iconSize: [28, 28],
    iconAnchor: [14, 14]
  });
};

const deviceIcon = (alarmActive: boolean) => {
  const cls = alarmActive ? "is-alarm" : "";
  return L.divIcon({
    className: "grid-device-leaflet-wrap",
    html: `<div class="grid-device-pin ${cls}"></div>`,
    iconSize: [18, 18],
    iconAnchor: [9, 9]
  });
};

function midpoint(a: Pole | undefined, b: Pole | undefined): [number, number] | null {
  if (!a || !b) return null;
  return [(a.latitude + b.latitude) / 2, (a.longitude + b.longitude) / 2];
}

export function GridManagementPanel({ accessToken, devices }: Props) {
  const toast = useToast();

  // ----- Veri state -----
  const [regions, setRegions] = useState<Region[]>([]);
  const [lines, setLines] = useState<Line[]>([]);
  const [detail, setDetail] = useState<LineDetail | null>(null);
  const [selectedRegionId, setSelectedRegionId] = useState<number | null>(null);
  const [selectedLineId, setSelectedLineId] = useState<number | null>(null);

  // ----- UI state -----
  const [detailTab, setDetailTab] = useState<DetailTab>("map");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  // Direk ekleme modu (harita tıklamayla)
  const [addPoleMode, setAddPoleMode] = useState(false);

  // Diger hatlari arka planda goster (referans icin)
  const [showOtherLines, setShowOtherLines] = useState(false);
  const [otherLineDetails, setOtherLineDetails] = useState<Map<number, LineDetail>>(new Map());

  // Bağlam menüsü (segment için)
  const [segmentMenu, setSegmentMenu] = useState<{
    segment: LineSegment | null;
    pseudoFromId?: number;
    pseudoToId?: number;
    x: number;
    y: number;
  } | null>(null);

  // Modaller
  const [regionModalOpen, setRegionModalOpen] = useState(false);
  const [editingRegion, setEditingRegion] = useState<Region | null>(null);
  const [lineModalOpen, setLineModalOpen] = useState(false);
  const [editingLine, setEditingLine] = useState<Line | null>(null);
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

  // showOtherLines aktif iken aynı bölgedeki diğer hatların detayını paralel çek.
  // Lines listesi her değiştiğinde tekrar yenile (yeni hat eklendiyse otomatik).
  useEffect(() => {
    if (!showOtherLines) {
      setOtherLineDetails(new Map());
      return;
    }
    const others = lines.filter((l) => l.id !== selectedLineId);
    if (others.length === 0) {
      setOtherLineDetails(new Map());
      return;
    }
    let cancelled = false;
    void (async () => {
      const results = await Promise.all(
        others.map(async (l) => {
          try {
            return [l.id, await fetchLineDetail(accessToken, l.id)] as const;
          } catch {
            return null;
          }
        })
      );
      if (cancelled) return;
      const m = new Map<number, LineDetail>();
      for (const r of results) if (r) m.set(r[0], r[1]);
      setOtherLineDetails(m);
    })();
    return () => {
      cancelled = true;
    };
  }, [showOtherLines, lines, selectedLineId, accessToken, detail]);

  // sequence_no sırasıyla direk listesi
  const sortedPoles = useMemo<Pole[]>(() => {
    if (!detail) return [];
    return [...detail.poles].sort((a, b) => a.sequence_no - b.sequence_no);
  }, [detail]);

  const polesById = useMemo<Map<number, Pole>>(
    () => new Map(sortedPoles.map((p) => [p.id, p])),
    [sortedPoles]
  );

  // Polyline pozisyonları
  const polylinePositions = useMemo<[number, number][]>(
    () => sortedPoles.map((p) => [p.latitude, p.longitude]),
    [sortedPoles]
  );

  // Ardışık direk segmentleri (otomatik segment listesi). DB'deki LineSegment
  // kayıtlarıyla eşleşenleri bağla, eşleşmeyen pseudo segmentlere de cihaz
  // atanabilsin diye liste döner.
  type SegmentSlot = {
    fromPole: Pole;
    toPole: Pole;
    segment: LineSegment | null;
  };

  const segmentSlots = useMemo<SegmentSlot[]>(() => {
    const slots: SegmentSlot[] = [];
    for (let i = 0; i < sortedPoles.length - 1; i += 1) {
      const fromPole = sortedPoles[i];
      const toPole = sortedPoles[i + 1];
      const seg =
        detail?.segments.find(
          (s) => s.from_pole_id === fromPole.id && s.to_pole_id === toPole.id
        ) ?? null;
      slots.push({ fromPole, toPole, segment: seg });
    }
    return slots;
  }, [detail, sortedPoles]);

  // Cihazların hangileri zaten bir segmente bağlı (başka segmentte boştalar gizlensin)
  const usedDeviceIds = useMemo<Set<number>>(() => {
    const s = new Set<number>();
    if (!detail) return s;
    for (const seg of detail.segments) {
      if (seg.device_id) s.add(seg.device_id);
    }
    return s;
  }, [detail]);

  const availableDevices = useMemo<DeviceRow[]>(
    () => devices.filter((d) => !usedDeviceIds.has(d.id)),
    [devices, usedDeviceIds]
  );

  // Harita merkezi
  const mapCenter = useMemo<[number, number]>(() => {
    if (sortedPoles.length > 0) {
      const avgLat = sortedPoles.reduce((s, p) => s + p.latitude, 0) / sortedPoles.length;
      const avgLon = sortedPoles.reduce((s, p) => s + p.longitude, 0) / sortedPoles.length;
      return [avgLat, avgLon];
    }
    return [39.0, 35.0];
  }, [sortedPoles]);

  const mapZoom = sortedPoles.length > 0 ? 13 : 6;

  const selectedRegion = regions.find((r) => r.id === selectedRegionId) ?? null;
  const selectedLine = lines.find((l) => l.id === selectedLineId) ?? null;
  const lineColor = selectedLine?.color || selectedRegion?.color || DEFAULT_REGION_COLOR;

  // ----- Aksiyonlar -----

  const handleMapClickAddPole = async (lat: number, lon: number) => {
    if (!selectedLine) return;
    const nextSeq = sortedPoles.length === 0 ? 1 : sortedPoles[sortedPoles.length - 1].sequence_no + 1;
    setBusy(true);
    try {
      await createPole(accessToken, {
        line_id: selectedLine.id,
        sequence_no: nextSeq,
        latitude: lat,
        longitude: lon,
        name: null
      });
      toast.success(`Direk #${nextSeq} eklendi.`);
      await reloadDetail(selectedLine.id);
      if (selectedRegionId !== null) await reloadLines(selectedRegionId);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Direk eklenemedi.");
    } finally {
      setBusy(false);
    }
  };

  const handleAttachDevice = async (slot: SegmentSlot, deviceId: number) => {
    if (!selectedLine) return;
    setBusy(true);
    setSegmentMenu(null);
    try {
      if (slot.segment) {
        // Mevcut segmenti güncelle
        await updateSegment(accessToken, slot.segment.id, { device_id: deviceId });
      } else {
        // Yeni segment yarat
        await createSegment(accessToken, {
          line_id: selectedLine.id,
          from_pole_id: slot.fromPole.id,
          to_pole_id: slot.toPole.id,
          device_id: deviceId
        });
      }
      toast.success("Cihaz segmente atandı.");
      await reloadDetail(selectedLine.id);
      if (selectedRegionId !== null) await reloadLines(selectedRegionId);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Cihaz atanamadı.");
    } finally {
      setBusy(false);
    }
  };

  const handleDetachDevice = async (segment: LineSegment) => {
    if (!selectedLine) return;
    setBusy(true);
    setSegmentMenu(null);
    try {
      await updateSegment(accessToken, segment.id, { device_id: null });
      toast.success("Cihaz segmentten kaldırıldı.");
      await reloadDetail(selectedLine.id);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Cihaz kaldırılamadı.");
    } finally {
      setBusy(false);
    }
  };

  const handleMoveDeviceToOtherSlot = async (
    fromSegment: LineSegment,
    targetSlot: SegmentSlot
  ) => {
    if (!selectedLine || !fromSegment.device_id) return;
    if (targetSlot.segment && targetSlot.segment.id === fromSegment.id) return;
    setBusy(true);
    try {
      // Önce kaynaktan kaldır
      await updateSegment(accessToken, fromSegment.id, { device_id: null });
      // Sonra hedefe yaz (yeni segment ya da mevcut)
      if (targetSlot.segment) {
        await updateSegment(accessToken, targetSlot.segment.id, { device_id: fromSegment.device_id });
      } else {
        await createSegment(accessToken, {
          line_id: selectedLine.id,
          from_pole_id: targetSlot.fromPole.id,
          to_pole_id: targetSlot.toPole.id,
          device_id: fromSegment.device_id
        });
      }
      toast.success("Cihaz başka segmente taşındı.");
      await reloadDetail(selectedLine.id);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Cihaz taşınamadı.");
    } finally {
      setBusy(false);
    }
  };

  const handleDeleteSegment = async (segment: LineSegment) => {
    if (!window.confirm("Bu segment ve bağlı cihaz kaydı silinsin mi?")) return;
    setBusy(true);
    setSegmentMenu(null);
    try {
      await deleteSegment(accessToken, segment.id);
      toast.success("Segment silindi.");
      if (selectedLine) await reloadDetail(selectedLine.id);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Segment silinemedi.");
    } finally {
      setBusy(false);
    }
  };

  const handleReverseOrder = async () => {
    if (!selectedLine) return;
    if (!window.confirm("Hat direklerinin sırası tersine çevrilsin mi? (Baş ↔ son swap)")) return;
    setBusy(true);
    try {
      await reversePoles(accessToken, selectedLine.id);
      toast.success("Hat sırası tersine çevrildi.");
      await reloadDetail(selectedLine.id);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Tersine çevirme başarısız.");
    } finally {
      setBusy(false);
    }
  };

  // Drag-to-reorder (HTML5 native, basit)
  const [draggedPoleId, setDraggedPoleId] = useState<number | null>(null);

  const handleDrop = async (targetPoleId: number) => {
    if (draggedPoleId === null || draggedPoleId === targetPoleId) {
      setDraggedPoleId(null);
      return;
    }
    if (!selectedLine) return;
    const fromIdx = sortedPoles.findIndex((p) => p.id === draggedPoleId);
    const toIdx = sortedPoles.findIndex((p) => p.id === targetPoleId);
    if (fromIdx < 0 || toIdx < 0) return;
    const reordered = [...sortedPoles];
    const [moved] = reordered.splice(fromIdx, 1);
    reordered.splice(toIdx, 0, moved);
    const items = reordered.map((p, i) => ({ pole_id: p.id, sequence_no: i + 1 }));
    setDraggedPoleId(null);
    setBusy(true);
    try {
      await reorderPoles(accessToken, selectedLine.id, items);
      toast.success("Sıra güncellendi.");
      await reloadDetail(selectedLine.id);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Sıra güncellenemedi.");
    } finally {
      setBusy(false);
    }
  };

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
                  <button type="button" className="icon-btn" title="Düzenle"
                    onClick={(e) => { e.stopPropagation(); setEditingRegion(r); setRegionModalOpen(true); }}>✎</button>
                  <button type="button" className="icon-btn icon-btn-danger" title="Sil"
                    onClick={(e) => { e.stopPropagation(); void handleDeleteRegion(r); }}>✕</button>
                </div>
              </div>
            ))}
          </div>
        </div>

        {/* ORTA — Hatlar */}
        <div className="grid-mgmt-col grid-mgmt-col-lines">
          <div className="grid-mgmt-col-head">
            <h4>Hatlar {selectedRegion ? `· ${selectedRegion.name}` : ""}</h4>
            <button className="add-user-btn" disabled={!selectedRegion}
              onClick={() => { setEditingLine(null); setLineModalOpen(true); }}>+ Hat</button>
          </div>
          <div className="grid-mgmt-list">
            {!selectedRegion ? (
              <p className="helper-text">Önce soldan bir bölge seçin.</p>
            ) : lines.length === 0 ? (
              <p className="helper-text">Bu bölgede henüz hat yok.</p>
            ) : null}
            {lines.map((l) => (
              <div key={l.id}
                className={`grid-mgmt-list-item ${selectedLineId === l.id ? "active" : ""}`}
                onClick={() => setSelectedLineId(l.id)}
              >
                <span className="grid-mgmt-color-dot"
                  style={{ background: l.color || selectedRegion?.color || DEFAULT_REGION_COLOR }} />
                <div className="grid-mgmt-list-item-main">
                  <strong>{l.name}</strong>
                  <code className="grid-mgmt-list-code">{l.code}</code>
                </div>
                <span className="grid-mgmt-list-count">{l.pole_count ?? 0} direk · {l.segment_count ?? 0} segment</span>
                <div className="grid-mgmt-list-actions">
                  <button type="button" className="icon-btn" title="Düzenle"
                    onClick={(e) => { e.stopPropagation(); setEditingLine(l); setLineModalOpen(true); }}>✎</button>
                  <button type="button" className="icon-btn icon-btn-danger" title="Sil"
                    onClick={(e) => { e.stopPropagation(); void handleDeleteLine(l); }}>✕</button>
                </div>
              </div>
            ))}
          </div>
        </div>

        {/* SAG — Hat Detayı: 2 sekme */}
        <div className="grid-mgmt-col grid-mgmt-col-detail">
          <div className="grid-mgmt-detail-head">
            <div className="grid-mgmt-detail-title">
              <h4>Hat Detayı {selectedLine ? `· ${selectedLine.name}` : ""}</h4>
              {selectedLine ? (
                <span className="helper-text">{sortedPoles.length} direk · {detail?.segments.length ?? 0} segment</span>
              ) : null}
            </div>
            {selectedLine ? (
              <div className="grid-mgmt-tabs">
                <button className={`grid-mgmt-tab ${detailTab === "map" ? "active" : ""}`}
                  onClick={() => setDetailTab("map")}>Harita</button>
                <button className={`grid-mgmt-tab ${detailTab === "list" ? "active" : ""}`}
                  onClick={() => setDetailTab("list")}>Liste</button>
              </div>
            ) : null}
          </div>

          {!selectedLine ? (
            <p className="helper-text">Bir hat seçin; direkleri ve segmentleri burada düzenleyin.</p>
          ) : detailTab === "map" ? (
            <>
              <div className="grid-mgmt-map-toolbar">
                <button
                  className={`secondary-btn ${addPoleMode ? "primary-btn" : ""}`}
                  onClick={() => setAddPoleMode(!addPoleMode)}
                  disabled={busy}
                >
                  {addPoleMode ? "Direk ekleme açık (haritaya tıkla)" : "+ Direk Ekle Modu"}
                </button>
                <button className="secondary-btn" disabled={sortedPoles.length < 2 || busy}
                  onClick={() => void handleReverseOrder()}>
                  ↔ Sırayı Tersine Çevir
                </button>
                <label className="grid-mgmt-toggle">
                  <input
                    type="checkbox"
                    checked={showOtherLines}
                    onChange={(e) => setShowOtherLines(e.target.checked)}
                  />
                  <span>Diğer hatları göster</span>
                </label>
                <span className="helper-text grid-mgmt-tip">
                  {sortedPoles.length >= 2
                    ? "Bir segmente tıklayarak cihaz atayın. Atanmış cihazı taşımak için cihaz simgesine tıklayın."
                    : "Önce en az 2 direk ekleyin (haritaya tıklayarak)."}
                </span>
              </div>

              <div
                className={`grid-mgmt-map-shell ${addPoleMode ? "is-add-mode" : ""}`}
                onClick={() => setSegmentMenu(null)}
              >
                <MapContainer
                  center={mapCenter}
                  zoom={mapZoom}
                  className="grid-mgmt-map"
                  scrollWheelZoom
                >
                  <TileLayer url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png" />

                  {addPoleMode ? (
                    <MapClickHandler onClick={(lat, lon) => void handleMapClickAddPole(lat, lon)} />
                  ) : null}

                  {/* Diger hatlari arka planda goster (refernas amacli, soluk) */}
                  {showOtherLines
                    ? Array.from(otherLineDetails.values()).map((d) => {
                        const sortedOther = [...d.poles].sort((a, b) => a.sequence_no - b.sequence_no);
                        if (sortedOther.length < 2) return null;
                        const positions: [number, number][] = sortedOther.map((p) => [
                          p.latitude,
                          p.longitude
                        ]);
                        const otherLineColor =
                          d.line.color || selectedRegion?.color || DEFAULT_REGION_COLOR;
                        return (
                          <Polyline
                            key={`other-${d.line.id}`}
                            positions={positions}
                            pathOptions={{
                              color: otherLineColor,
                              weight: 3,
                              opacity: 0.35,
                              dashArray: "6 6"
                            }}
                          >
                            <Tooltip sticky>
                              <strong>{d.line.name}</strong>
                              <br />
                              <em style={{ fontSize: 11 }}>(diğer hat — referans)</em>
                            </Tooltip>
                          </Polyline>
                        );
                      })
                    : null}
                  {showOtherLines
                    ? Array.from(otherLineDetails.values()).flatMap((d) =>
                        d.poles.map((p) => (
                          <Marker
                            key={`other-pole-${p.id}`}
                            position={[p.latitude, p.longitude]}
                            icon={L.divIcon({
                              className: "grid-pole-leaflet-wrap",
                              html: `<div class="grid-pole-pin grid-pole-pin--ghost"><span>${p.sequence_no}</span></div>`,
                              iconSize: [22, 22],
                              iconAnchor: [11, 11]
                            })}
                            interactive={false}
                          />
                        ))
                      )
                    : null}

                  {/* Polyline segmentleri ayri cizilir; sol VE sag tik ayni menuyu acar.
                      Direk ekleme modu aktif ise tikla geçer (haritaya direk eklenmesin diye
                      stopPropagation ile yutulur). */}
                  {segmentSlots.map((slot, idx) => {
                    const positions: [number, number][] = [
                      [slot.fromPole.latitude, slot.fromPole.longitude],
                      [slot.toPole.latitude, slot.toPole.longitude]
                    ];
                    const openMenu = (event: L.LeafletMouseEvent) => {
                      if (addPoleMode) return; // direk ekleme aktifse menu acma
                      event.originalEvent.preventDefault();
                      event.originalEvent.stopPropagation();
                      const native = event.originalEvent;
                      setSegmentMenu({
                        segment: slot.segment,
                        pseudoFromId: slot.fromPole.id,
                        pseudoToId: slot.toPole.id,
                        x: native.clientX,
                        y: native.clientY
                      });
                    };
                    return (
                      <Polyline
                        key={`seg-${idx}`}
                        positions={positions}
                        pathOptions={{
                          color: slot.segment?.device_id ? "#16a34a" : lineColor,
                          weight: slot.segment?.device_id ? 6 : 5,
                          opacity: 0.9
                        }}
                        eventHandlers={{
                          click: openMenu,
                          contextmenu: openMenu
                        }}
                      />
                    );
                  })}

                  {/* Direkler — sequence_no etiketli */}
                  {sortedPoles.map((p, idx) => {
                    const isStart = idx === 0;
                    const isEnd = idx === sortedPoles.length - 1;
                    return (
                      <Marker
                        key={p.id}
                        position={[p.latitude, p.longitude]}
                        icon={poleIcon(String(p.sequence_no), isStart, isEnd)}
                        draggable
                        eventHandlers={{
                          click: () => setEditingPole(p),
                          contextmenu: (event: L.LeafletMouseEvent) => {
                            event.originalEvent.preventDefault();
                            event.originalEvent.stopPropagation();
                            void handleDeletePole(p);
                          },
                          dragend: (event: L.DragEndEvent) => {
                            const ll = (event.target as L.Marker).getLatLng();
                            void handlePoleDragEnd(p, ll.lat, ll.lng);
                          }
                        }}
                      >
                        <Tooltip>
                          {p.name ?? `Direk #${p.sequence_no}`}
                          {isStart ? " (BAŞ)" : isEnd ? " (SON)" : ""}
                          <br />
                          <em style={{ fontSize: 10 }}>Tıkla: düzenle · Sürükle: taşı · Sağ tık: sil</em>
                        </Tooltip>
                      </Marker>
                    );
                  })}

                  {/* Segmentlere bağlı cihazlar — orta noktada. Hem sol tık hem
                      sağ tık ayni menuyu acar — kullanici hangisinde takıldıysa. */}
                  {segmentSlots.map((slot) => {
                    if (!slot.segment?.device_id) return null;
                    const mid = midpoint(slot.fromPole, slot.toPole);
                    if (!mid) return null;
                    const dev = devices.find((d) => d.id === slot.segment?.device_id);
                    const openMenu = (event: L.LeafletMouseEvent) => {
                      event.originalEvent.preventDefault();
                      event.originalEvent.stopPropagation();
                      const native = event.originalEvent;
                      setSegmentMenu({
                        segment: slot.segment,
                        pseudoFromId: slot.fromPole.id,
                        pseudoToId: slot.toPole.id,
                        x: native.clientX,
                        y: native.clientY
                      });
                    };
                    return (
                      <Marker
                        key={`dev-${slot.segment.id}`}
                        position={mid}
                        icon={deviceIcon(dev?.alarmActive ?? false)}
                        eventHandlers={{
                          click: openMenu,
                          contextmenu: openMenu
                        }}
                      >
                        <Tooltip>
                          {dev ? `${dev.name} (${dev.code})` : `Cihaz #${slot.segment.device_id}`}
                          <br />
                          {slot.fromPole.sequence_no} ↔ {slot.toPole.sequence_no}
                          <br />
                          <em>Tıkla: taşı / kaldır</em>
                        </Tooltip>
                      </Marker>
                    );
                  })}
                </MapContainer>

                {/* Bağlam menüsü — segment için */}
                {segmentMenu ? (
                  <div
                    className="grid-segment-menu"
                    style={{ left: segmentMenu.x, top: segmentMenu.y }}
                    onClick={(e) => e.stopPropagation()}
                  >
                    {(() => {
                      const slot = segmentSlots.find(
                        (s) =>
                          s.fromPole.id === segmentMenu.pseudoFromId &&
                          s.toPole.id === segmentMenu.pseudoToId
                      );
                      if (!slot) return <p className="helper-text">Segment bulunamadı.</p>;
                      return (
                        <SegmentContextMenu
                          slot={slot}
                          allSlots={segmentSlots}
                          devices={devices}
                          availableDevices={availableDevices}
                          onAttach={(devId) => void handleAttachDevice(slot, devId)}
                          onDetach={(seg) => void handleDetachDevice(seg)}
                          onMove={(seg, target) => void handleMoveDeviceToOtherSlot(seg, target)}
                          onDeleteSegment={(seg) => void handleDeleteSegment(seg)}
                          onClose={() => setSegmentMenu(null)}
                        />
                      );
                    })()}
                  </div>
                ) : null}
              </div>
            </>
          ) : (
            // ===== LISTE TAB =====
            <div className="grid-mgmt-list-tab">
              <div className="grid-mgmt-list-tab-toolbar">
                <button className="secondary-btn" disabled={sortedPoles.length < 2 || busy}
                  onClick={() => void handleReverseOrder()}>
                  ↔ Sırayı Tersine Çevir
                </button>
                <span className="helper-text">
                  Direk satırlarını sürükleyerek sırayı değiştirebilirsiniz.
                </span>
              </div>

              <div className="grid-mgmt-pole-rows">
                {sortedPoles.length === 0 ? (
                  <p className="helper-text">Henüz direk yok. Harita sekmesine geçip direk ekleyin.</p>
                ) : null}
                {sortedPoles.map((p, idx) => {
                  const isStart = idx === 0;
                  const isEnd = idx === sortedPoles.length - 1;
                  // Bu direkten sonraki segment
                  const nextSlot = segmentSlots[idx]; // i. direkten i+1'e
                  const dev = nextSlot?.segment?.device_id
                    ? devices.find((d) => d.id === nextSlot.segment!.device_id)
                    : null;
                  return (
                    <div key={p.id}>
                      <div
                        className={`grid-mgmt-pole-row ${draggedPoleId === p.id ? "is-dragging" : ""}`}
                        draggable
                        onDragStart={() => setDraggedPoleId(p.id)}
                        onDragOver={(e) => e.preventDefault()}
                        onDrop={() => void handleDrop(p.id)}
                      >
                        <span className="grid-mgmt-pole-row-handle">⋮⋮</span>
                        <span className={`grid-mgmt-pole-row-seq ${isStart ? "is-start" : isEnd ? "is-end" : ""}`}>
                          #{p.sequence_no}
                          {isStart ? " · BAŞ" : isEnd ? " · SON" : ""}
                        </span>
                        <span className="grid-mgmt-pole-row-name">{p.name ?? "(adsız)"}</span>
                        <span className="grid-mgmt-pole-row-coord">
                          {p.latitude.toFixed(5)}, {p.longitude.toFixed(5)}
                        </span>
                        <div className="grid-mgmt-pole-row-actions">
                          <button className="icon-btn" title="Düzenle"
                            onClick={() => setEditingPole(p)}>✎</button>
                          <button className="icon-btn icon-btn-danger" title="Sil"
                            onClick={() => void handleDeletePole(p)}>✕</button>
                        </div>
                      </div>
                      {nextSlot ? (
                        <div className="grid-mgmt-segment-row">
                          <span className="grid-mgmt-segment-label">
                            ↳ Segment {p.sequence_no} → {nextSlot.toPole.sequence_no}
                          </span>
                          {dev ? (
                            <>
                              <span className="grid-mgmt-segment-device">
                                <strong>{dev.name}</strong> <code>{dev.code}</code>
                              </span>
                              <button className="secondary-btn action-btn"
                                onClick={() => void handleDetachDevice(nextSlot.segment!)}>
                                Cihazı Kaldır
                              </button>
                            </>
                          ) : (
                            <select
                              defaultValue=""
                              className="grid-mgmt-segment-select"
                              onChange={(e) => {
                                const id = Number(e.target.value);
                                if (id) void handleAttachDevice(nextSlot, id);
                              }}
                            >
                              <option value="">Cihaz seç...</option>
                              {availableDevices.map((d) => (
                                <option key={d.id} value={d.id}>{d.name} ({d.code})</option>
                              ))}
                            </select>
                          )}
                        </div>
                      ) : null}
                    </div>
                  );
                })}
              </div>
            </div>
          )}
        </div>
      </div>

      {error ? <p className="error-text">{error}</p> : null}

      {/* Modallar */}
      {regionModalOpen ? (
        <RegionModal initial={editingRegion} busy={busy}
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
            } finally { setBusy(false); }
          }}
        />
      ) : null}

      {lineModalOpen && selectedRegion ? (
        <LineModal regionId={selectedRegion.id} initial={editingLine} busy={busy}
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
            } finally { setBusy(false); }
          }}
        />
      ) : null}

      {editingPole ? (
        <PoleEditModal pole={editingPole} busy={busy}
          onClose={() => setEditingPole(null)}
          onSubmit={async (payload) => {
            setBusy(true);
            try {
              await import("../../shared/api").then((m) =>
                m.updatePole(accessToken, editingPole.id, payload)
              );
              toast.success("Direk güncellendi.");
              setEditingPole(null);
              if (selectedLine) await reloadDetail(selectedLine.id);
            } catch (err) {
              toast.error(err instanceof Error ? err.message : "Direk güncellenemedi.");
            } finally { setBusy(false); }
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
    if (!window.confirm(`Direk #${p.sequence_no} silinsin mi? Bağlı segmentler de kaldırılır.`)) return;
    try {
      await deletePole(accessToken, p.id);
      toast.success("Direk silindi.");
      if (selectedLineId !== null) await reloadDetail(selectedLineId);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Direk silinemedi.");
    }
  }

  async function handlePoleDragEnd(p: Pole, lat: number, lng: number) {
    try {
      await import("../../shared/api").then((m) =>
        m.updatePole(accessToken, p.id, {
          latitude: Number(lat.toFixed(6)),
          longitude: Number(lng.toFixed(6))
        })
      );
      toast.success(`Direk #${p.sequence_no} taşındı.`);
      if (selectedLineId !== null) await reloadDetail(selectedLineId);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Direk taşınamadı.");
      if (selectedLineId !== null) await reloadDetail(selectedLineId); // hata olunca eski koordinata gerial
    }
  }
}

// ============= Helper components =============

function MapClickHandler({ onClick }: { onClick: (lat: number, lon: number) => void }) {
  useMapEvents({
    click(event) {
      onClick(Number(event.latlng.lat.toFixed(6)), Number(event.latlng.lng.toFixed(6)));
    }
  });
  return null;
}

function SegmentContextMenu({
  slot,
  allSlots,
  devices,
  availableDevices,
  onAttach,
  onDetach,
  onMove,
  onDeleteSegment,
  onClose
}: {
  slot: { fromPole: Pole; toPole: Pole; segment: LineSegment | null };
  allSlots: { fromPole: Pole; toPole: Pole; segment: LineSegment | null }[];
  devices: DeviceRow[];
  availableDevices: DeviceRow[];
  onAttach: (deviceId: number) => void;
  onDetach: (seg: LineSegment) => void;
  onMove: (
    seg: LineSegment,
    target: { fromPole: Pole; toPole: Pole; segment: LineSegment | null }
  ) => void;
  onDeleteSegment: (seg: LineSegment) => void;
  onClose: () => void;
}) {
  const [moveMode, setMoveMode] = useState(false);
  const dev = slot.segment?.device_id
    ? devices.find((d) => d.id === slot.segment?.device_id)
    : null;
  return (
    <div className="grid-segment-menu-inner">
      <div className="grid-segment-menu-head">
        <strong>Segment {slot.fromPole.sequence_no} → {slot.toPole.sequence_no}</strong>
        <button type="button" onClick={onClose}>×</button>
      </div>
      {dev ? (
        <>
          <div className="grid-segment-menu-device">
            Atanmış: <strong>{dev.name}</strong> <code>{dev.code}</code>
          </div>
          {!moveMode ? (
            <>
              <button onClick={() => setMoveMode(true)}>Başka Segmente Taşı</button>
              <button onClick={() => slot.segment && onDetach(slot.segment)}>Cihazı Kaldır</button>
              {slot.segment ? (
                <button className="danger-btn" onClick={() => onDeleteSegment(slot.segment!)}>
                  Segmenti Sil
                </button>
              ) : null}
            </>
          ) : (
            <div className="grid-segment-menu-target-list">
              <p className="helper-text">Hedef segment seçin:</p>
              {allSlots
                .filter(
                  (s) =>
                    !(s.fromPole.id === slot.fromPole.id && s.toPole.id === slot.toPole.id)
                )
                .map((s) => (
                  <button
                    key={`${s.fromPole.id}-${s.toPole.id}`}
                    onClick={() => slot.segment && onMove(slot.segment, s)}
                  >
                    {s.fromPole.sequence_no} → {s.toPole.sequence_no}
                    {s.segment?.device_id ? " (dolu — değiştirilecek)" : ""}
                  </button>
                ))}
              <button className="secondary-btn" onClick={() => setMoveMode(false)}>İptal</button>
            </div>
          )}
        </>
      ) : (
        <>
          {availableDevices.length === 0 ? (
            <p className="helper-text">Tüm cihazlar zaten atanmış. Önce başka segmentlerden çıkarmanız gerekir.</p>
          ) : (
            <>
              <p className="helper-text">Cihaz ata:</p>
              <div className="grid-segment-menu-device-list">
                {availableDevices.map((d) => (
                  <button key={d.id} onClick={() => onAttach(d.id)}>
                    <strong>{d.name}</strong> <code>{d.code}</code>
                  </button>
                ))}
              </div>
            </>
          )}
        </>
      )}
    </div>
  );
}

// ============= MODALS =============

function RegionModal({
  initial, busy, onClose, onSubmit
}: {
  initial: Region | null; busy: boolean;
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
      code: code.trim(), name: name.trim(),
      description: description.trim() || null,
      color, is_active: isActive
    });
  };
  return (
    <div className="settings-modal-backdrop">
      <form className="settings-modal" onSubmit={submit}>
        <h3>{initial ? "Bölgeyi Düzenle" : "Yeni Bölge"}</h3>
        <label>Kod <input value={code} onChange={(e) => setCode(e.target.value)} required /></label>
        <label>Ad <input value={name} onChange={(e) => setName(e.target.value)} required /></label>
        <label>Açıklama <input value={description} onChange={(e) => setDescription(e.target.value)} /></label>
        <label>Renk <input type="color" value={color} onChange={(e) => setColor(e.target.value)} /></label>
        <label className="notify-option">
          <input type="checkbox" checked={isActive} onChange={(e) => setIsActive(e.target.checked)} />
          Aktif
        </label>
        <div className="settings-actions">
          <button type="button" onClick={onClose} disabled={busy}>İptal</button>
          <button type="submit" className="primary-btn" disabled={busy}>{busy ? "..." : "Kaydet"}</button>
        </div>
      </form>
    </div>
  );
}

function LineModal({
  regionId, initial, busy, onClose, onSubmit
}: {
  regionId: number; initial: Line | null; busy: boolean;
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
      region_id: regionId, code: code.trim(), name: name.trim(),
      description: description.trim() || null, color: color || null,
      is_active: isActive
    });
  };
  return (
    <div className="settings-modal-backdrop">
      <form className="settings-modal" onSubmit={submit}>
        <h3>{initial ? "Hattı Düzenle" : "Yeni Hat"}</h3>
        <label>Kod <input value={code} onChange={(e) => setCode(e.target.value)} required /></label>
        <label>Ad <input value={name} onChange={(e) => setName(e.target.value)} required /></label>
        <label>Açıklama <input value={description} onChange={(e) => setDescription(e.target.value)} /></label>
        <label>Renk (boş = bölgenin rengi)
          <input type="color" value={color || "#94a3b8"} onChange={(e) => setColor(e.target.value)} />
        </label>
        <label className="notify-option">
          <input type="checkbox" checked={isActive} onChange={(e) => setIsActive(e.target.checked)} />
          Aktif
        </label>
        <div className="settings-actions">
          <button type="button" onClick={onClose} disabled={busy}>İptal</button>
          <button type="submit" className="primary-btn" disabled={busy}>{busy ? "..." : "Kaydet"}</button>
        </div>
      </form>
    </div>
  );
}

function PoleEditModal({
  pole, busy, onClose, onSubmit
}: {
  pole: Pole; busy: boolean;
  onClose: () => void;
  onSubmit: (payload: Partial<Pole>) => Promise<void>;
}) {
  const [name, setName] = useState(pole.name ?? "");
  const [latitude, setLatitude] = useState(String(pole.latitude));
  const [longitude, setLongitude] = useState(String(pole.longitude));
  const submit = async (e: FormEvent) => {
    e.preventDefault();
    const lat = Number(latitude);
    const lon = Number(longitude);
    if (!Number.isFinite(lat) || !Number.isFinite(lon)) return;
    await onSubmit({
      name: name.trim() || null,
      latitude: lat, longitude: lon
    });
  };
  return (
    <div className="settings-modal-backdrop">
      <form className="settings-modal" onSubmit={submit}>
        <h3>Direk Düzenle (#{pole.sequence_no})</h3>
        <label>İsim <input value={name} onChange={(e) => setName(e.target.value)} placeholder="Direk-47" /></label>
        <label>Enlem
          <input type="number" step="0.000001" value={latitude} onChange={(e) => setLatitude(e.target.value)} required />
        </label>
        <label>Boylam
          <input type="number" step="0.000001" value={longitude} onChange={(e) => setLongitude(e.target.value)} required />
        </label>
        <p className="helper-text">Sıra numarası "Sırayı Tersine Çevir" veya direk satırlarını sürükleyerek değişir.</p>
        <div className="settings-actions">
          <button type="button" onClick={onClose} disabled={busy}>İptal</button>
          <button type="submit" className="primary-btn" disabled={busy}>{busy ? "..." : "Kaydet"}</button>
        </div>
      </form>
    </div>
  );
}
