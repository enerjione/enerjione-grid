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
import { useEffect, useMemo, useRef, useState, type FormEvent } from "react";
import { useTranslation } from "react-i18next";
import { asyncConfirm } from "../../components/ConfirmDialog";
import { LayersControl, MapContainer, Marker, Polyline, TileLayer, Tooltip, useMap, useMapEvents } from "react-leaflet";
import L from "leaflet";
import { MapLayerSwitchFix } from "../../components/MapLayerSwitchFix";

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
import type { GridSnapshot } from "../../shared/api";
import { useToast } from "../../components/ToastProvider";
import { LineWizardModal } from "./LineWizardModal";

type Props = {
  accessToken: string;
  devices: DeviceRow[];
  /** Tum sistemin topoloji ozeti — diger hat/bolgelerdeki kullanilan cihazlari
   *  gizlemek icin kullanilir. Bir cihaz tum sistemde tek bir segmente
   *  baglanabilir (DB unique constraint), bu yuzden "Cihaz Ekle" listesinde
   *  baska hatta atanmis cihazlar gorunmemeli. */
  gridSnapshot: GridSnapshot | null;
};

type DetailTab = "map" | "list";

const DEFAULT_REGION_COLOR = "#2563eb";

const poleIcon = (
  label: string,
  isStart: boolean,
  isEnd: boolean,
  draftState?: "added" | "moved" | null,
  poleType?: string
) => {
  const typeCls =
    poleType === "transformer"
      ? "is-transformer"
      : poleType === "breaker"
        ? "is-breaker"
        : "";
  const cls = [
    isStart ? "is-start" : isEnd ? "is-end" : "",
    typeCls,
    draftState === "added" ? "is-draft-added" : "",
    draftState === "moved" ? "is-draft-moved" : ""
  ]
    .filter(Boolean)
    .join(" ");
  // Trafo/kesici icin sembolu icine yaz; aksi halde sira numarasi.
  let inner = `<span>${label}</span>`;
  if (poleType === "transformer") {
    inner = `<span class="grid-pole-symbol" title="Trafo">⚡</span><span class="grid-pole-seq">${label}</span>`;
  } else if (poleType === "breaker") {
    inner = `<span class="grid-pole-symbol" title="Kesici">▣</span><span class="grid-pole-seq">${label}</span>`;
  }
  const size: [number, number] = poleType && poleType !== "pole" ? [34, 34] : [28, 28];
  return L.divIcon({
    className: "grid-pole-leaflet-wrap",
    html: `<div class="grid-pole-pin ${cls}">${inner}</div>`,
    iconSize: size,
    iconAnchor: [size[0] / 2, size[1] / 2]
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

export function GridManagementPanel({ accessToken, devices, gridSnapshot }: Props) {
  const toast = useToast();
  const { t } = useTranslation();

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

  // Direk düzenleme draft modu — açıkken drag/sil/ekle backend'e yansımaz,
  // Kaydet'e basana kadar yerelde tutulur. Geri Al ile baseline'a dönülür.
  const [editMode, setEditMode] = useState(false);
  // Mevcut direkler için override (lat/lng/name değişikliği)
  const [draftPoleEdits, setDraftPoleEdits] = useState<
    Map<number, { latitude?: number; longitude?: number; name?: string | null }>
  >(new Map());
  // Yeni eklenen direkler (henüz backend'de yok). id geçici negatif sayı.
  const [draftPoleAdds, setDraftPoleAdds] = useState<Pole[]>([]);
  // Silinmek üzere işaretli direkler (id'leri).
  const [draftPoleDeletes, setDraftPoleDeletes] = useState<Set<number>>(new Set());
  // Sürüklenme sırasında anlık konum (polyline'in canlı yeniden çizilmesi için).
  // Sürükleme bitince ya draftPoleEdits / draftPoleAdds'e taşınır.
  const [draggingPole, setDraggingPole] = useState<{ id: number; lat: number; lng: number } | null>(null);
  // Cihaz konum drag'i icin draft. segmentId -> { device_position_t, slotKey, order }
  // slotKey: "fromPoleId|toPoleId" — ayni slot icindeki sira swap'larini takip
  // edebilmek icin. order: bu slot icindeki yeni sira (drag esnasinda diger
  // cihazi gectiginde 0/1 yer degistirebilir).
  const [draftDevicePositions, setDraftDevicePositions] = useState<
    Map<number, { device_position_t: number }>
  >(new Map());

  // Bağlam menüsü (segment için)
  const [segmentMenu, setSegmentMenu] = useState<{
    segment: LineSegment | null;
    pseudoFromId?: number;
    pseudoToId?: number;
    x: number;
    y: number;
  } | null>(null);

  // Harita bos alan / direk / polyline icin sag tik menu.
  // type='map'    -> bos haritada sag tik (Buraya direk ekle, vb.)
  // type='pole'   -> direk uzerine sag tik (Sil, Araya direk ekle)
  // type='line'   -> polyline uzerine sag tik (Buraya direk ekle, vb.)
  const [mapContextMenu, setMapContextMenu] = useState<{
    type: "map" | "pole" | "line";
    lat: number;
    lng: number;
    x: number;
    y: number;
    poleId?: number;
    poleSeq?: number;
  } | null>(null);

  // Modaller
  const [regionModalOpen, setRegionModalOpen] = useState(false);
  const [importModalOpen, setImportModalOpen] = useState(false);
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
      setError(err instanceof Error ? err.message : t("engineering.grid.loadLinesFail"));
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

  // sequence_no sırasıyla direk listesi.
  // editMode açıkken: backend listesine draft eklemeleri kat, draft silmeleri çıkar,
  // mevcut direklerin lat/lng/name override'larını uygula.
  const sortedPoles = useMemo<Pole[]>(() => {
    if (!detail) return [];
    let base: Pole[] = detail.poles;
    if (editMode) {
      base = base.filter((p) => !draftPoleDeletes.has(p.id));
      if (draftPoleEdits.size > 0) {
        base = base.map((p) => {
          const ov = draftPoleEdits.get(p.id);
          if (!ov) return p;
          return {
            ...p,
            latitude: ov.latitude ?? p.latitude,
            longitude: ov.longitude ?? p.longitude,
            name: ov.name !== undefined ? ov.name : p.name
          };
        });
      }
      base = [...base, ...draftPoleAdds];
    }
    return [...base].sort((a, b) => a.sequence_no - b.sequence_no);
  }, [detail, editMode, draftPoleEdits, draftPoleAdds, draftPoleDeletes]);

  const hasUnsavedDraft =
    editMode && (
      draftPoleEdits.size > 0
      || draftPoleAdds.length > 0
      || draftPoleDeletes.size > 0
      || draftDevicePositions.size > 0
    );

  // Sürükleme sırasında anlık konumu uygula (canlı polyline güncellemesi için).
  const livePoles = useMemo<Pole[]>(() => {
    if (!draggingPole) return sortedPoles;
    return sortedPoles.map((p) =>
      p.id === draggingPole.id ? { ...p, latitude: draggingPole.lat, longitude: draggingPole.lng } : p
    );
  }, [sortedPoles, draggingPole]);

  const polesById = useMemo<Map<number, Pole>>(
    () => new Map(livePoles.map((p) => [p.id, p])),
    [livePoles]
  );

  // Polyline pozisyonları
  const polylinePositions = useMemo<[number, number][]>(
    () => livePoles.map((p) => [p.latitude, p.longitude]),
    [livePoles]
  );

  // React-Leaflet Marker'in `position` propu referansiyla karsilastiriliyor
  // (props.position !== prevProps.position). Yeni [lat,lng] array her render'da
  // yeni referans uretirse, leaflet aktif drag'i kesintiye ugratir. Bu yuzden
  // her direk icin lat/lng degismedikce STABIL kalan tek bir tuple cache'liyoruz.
  const poleMarkerPositions = useMemo(() => {
    const cache = new Map<number, [number, number]>();
    for (const p of sortedPoles) {
      cache.set(p.id, [p.latitude, p.longitude]);
    }
    return cache;
    // sortedPoles.length ve her direk icin lat/lng dependency.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [
    sortedPoles.length,
    sortedPoles.map((p) => `${p.id}:${p.latitude}:${p.longitude}`).join("|")
  ]);

  // poleIcon cagrisi her render'da yeni L.divIcon uretir. Drag sirasinda surekli
  // setIcon cagrildiginda DOM swap olusur. Pole-id + state karmasinda cache.
  const poleMarkerIcons = useMemo(() => {
    const cache = new Map<number, L.DivIcon>();
    sortedPoles.forEach((p, idx) => {
      const isStart = idx === 0;
      const isEnd = idx === sortedPoles.length - 1;
      const draftState: "added" | "moved" | null = editMode
        ? p.id < 0
          ? "added"
          : draftPoleEdits.has(p.id)
            ? "moved"
            : null
        : null;
      cache.set(
        p.id,
        poleIcon(String(p.sequence_no), isStart, isEnd, draftState, p.pole_type)
      );
    });
    return cache;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [
    sortedPoles.length,
    editMode,
    sortedPoles.map((p) => `${p.id}:${p.sequence_no}:${p.pole_type ?? ""}:${draftPoleEdits.has(p.id) ? "m" : ""}:${p.id < 0 ? "n" : ""}`).join("|")
  ]);

  // Ardışık direk segmentleri (otomatik segment listesi). DB'deki LineSegment
  // kayıtlarıyla eşleşenleri bağla, eşleşmeyen pseudo segmentlere de cihaz
  // atanabilsin diye liste döner.
  // SegmentSlot: ardisik iki direk arasi.
  // NOT: Backend coklu cihaz destekliyor (ayni from/to icin birden cok kayit).
  // UI taraf coklu cihaz render'i bir sonraki iterasyonda eklenecek; simdilik
  // ayni slot'taki TUM kayitlari da tutuyoruz, ana referansa ilk olani veriyoruz.
  type SegmentSlot = {
    fromPole: Pole;
    toPole: Pole;
    segment: LineSegment | null;
    /** Slot'taki tum kayitlar (coklu cihaz icin) — ileride UI bunu kullanacak. */
    segments: LineSegment[];
  };

  const segmentSlots = useMemo<SegmentSlot[]>(() => {
    const slots: SegmentSlot[] = [];
    for (let i = 0; i < livePoles.length - 1; i += 1) {
      const fromPole = livePoles[i];
      const toPole = livePoles[i + 1];
      const segs = (detail?.segments ?? [])
        .filter((s) => s.from_pole_id === fromPole.id && s.to_pole_id === toPole.id)
        .sort((a, b) => {
          // Once draft (drag aninda guncellenen), sonra backend'deki
          // device_position_t'ye gore sirala. Iki cihazin t'leri varsa
          // suruklenen cihaz digerinin onune/arkasina gectiginde sira
          // otomatik degisir.
          const ta = draftDevicePositions.get(a.id)?.device_position_t ?? a.device_position_t;
          const tb = draftDevicePositions.get(b.id)?.device_position_t ?? b.device_position_t;
          const aHasT = ta !== null && ta !== undefined;
          const bHasT = tb !== null && tb !== undefined;
          if (aHasT && bHasT) return (ta as number) - (tb as number);
          if (aHasT) return -1;
          if (bHasT) return 1;
          // Her ikisi de NULL: created_at + id'ye fallback
          const ad = new Date(a.created_at).getTime();
          const bd = new Date(b.created_at).getTime();
          if (ad !== bd) return ad - bd;
          return a.id - b.id;
        });
      slots.push({
        fromPole,
        toPole,
        segment: segs[0] ?? null,
        segments: segs
      });
    }
    return slots;
  }, [detail, livePoles, draftDevicePositions]);

  // Cihazlarin hangileri TUM SISTEMDE zaten bir segmente bagli.
  // DB seviyesinde LineSegment.device_id UNIQUE — bir cihaz sadece bir
  // segmente baglanabilir. UI'de 'Cihaz Ekle' listesinde sadece tum sistemde
  // bos olan cihazlar gorunmeli; aksi halde kullanici yanlislikla baska
  // hatta atanmis bir cihazi secmeye calisabilir, backend 409 doner.
  const usedDeviceIds = useMemo<Set<number>>(() => {
    const s = new Set<number>();
    // Mevcut hat detayinin segmentleri (en taze bilgi).
    if (detail) {
      for (const seg of detail.segments) {
        if (seg.device_id) s.add(seg.device_id);
      }
    }
    // Tum sistem snapshot'i (diger hat ve bolgelerdeki kullanilan cihazlar).
    if (gridSnapshot) {
      for (const seg of gridSnapshot.segments) {
        if (seg.device_id) s.add(seg.device_id);
      }
    }
    return s;
  }, [detail, gridSnapshot]);

  const availableDevices = useMemo<DeviceRow[]>(
    () => devices.filter((d) => !usedDeviceIds.has(d.id)),
    [devices, usedDeviceIds]
  );

  // Bransman: secili hat baska bir hattin diregine bagliysa, o pole'un
  // konumu (gridSnapshot'tan). Hat haritasinda dal-baglanti cizgisi cizilir.
  const branchParentPos = useMemo<[number, number] | null>(() => {
    const selLine = lines.find((l) => l.id === selectedLineId);
    const parentPoleId = selLine?.branched_from_pole_id;
    if (!parentPoleId || !gridSnapshot) return null;
    const parent = gridSnapshot.poles.find((p) => p.id === parentPoleId);
    if (!parent) return null;
    return [parent.latitude, parent.longitude];
  }, [lines, selectedLineId, gridSnapshot]);

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

    // Draft modunda yerelde tut.
    if (editMode) {
      const tempId = -Date.now();
      const draftPole: Pole = {
        id: tempId,
        line_id: selectedLine.id,
        sequence_no: nextSeq,
        latitude: lat,
        longitude: lon,
        name: null
      } as Pole;
      setDraftPoleAdds((prev) => [...prev, draftPole]);
      toast.success(t("toasts.poleDraftAdded", { seq: nextSeq }));
      return;
    }

    setBusy(true);
    try {
      await createPole(accessToken, {
        line_id: selectedLine.id,
        sequence_no: nextSeq,
        latitude: lat,
        longitude: lon,
        name: null
      });
      toast.success(t("toasts.poleAdded", { seq: nextSeq }));
      await reloadDetail(selectedLine.id);
      if (selectedRegionId !== null) await reloadLines(selectedRegionId);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : t("toasts.poleAddFail"));
    } finally {
      setBusy(false);
    }
  };

  const handleAttachDevice = async (slot: SegmentSlot, deviceId: number) => {
    if (!selectedLine) return;
    setBusy(true);
    setSegmentMenu(null);
    try {
      // Coklu cihaz: ayni slot'ta cihazsiz segment varsa onu kullan, yoksa yeni
      // segment yarat. Cihazli segmentlere dokunma — cunku coklu cihaz mumkun.
      const emptySeg = slot.segments.find((s) => !s.device_id) ?? null;
      if (emptySeg) {
        await updateSegment(accessToken, emptySeg.id, { device_id: deviceId });
      } else {
        await createSegment(accessToken, {
          line_id: selectedLine.id,
          from_pole_id: slot.fromPole.id,
          to_pole_id: slot.toPole.id,
          device_id: deviceId
        });
      }
      toast.success(t("engineering.grid.deviceAssigned"));
      await reloadDetail(selectedLine.id);
      if (selectedRegionId !== null) await reloadLines(selectedRegionId);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : t("toasts.deviceAssignFail"));
    } finally {
      setBusy(false);
    }
  };

  // Cihazi segmentten kaldir. Coklu cihaz icin: sadece bu segment'i SIL
  // (cihazsiz segment kaydi tutmaya gerek yok). Boylece slot'ta kalan diger
  // cihazlar otomatik yeniden dagilir.
  const handleDetachDevice = async (segment: LineSegment) => {
    if (!selectedLine) return;
    setBusy(true);
    setSegmentMenu(null);
    try {
      await deleteSegment(accessToken, segment.id);
      toast.success(t("engineering.grid.deviceDetached"));
      await reloadDetail(selectedLine.id);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : t("toasts.deviceDetachFail"));
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
      // Sonra hedefe yaz: hedef slotta cihazsiz segment varsa onu kullan,
      // yoksa coklu cihaz icin yeni segment yarat.
      const emptyTargetSeg = targetSlot.segments.find((s) => !s.device_id) ?? null;
      if (emptyTargetSeg) {
        await updateSegment(accessToken, emptyTargetSeg.id, { device_id: fromSegment.device_id });
      } else {
        await createSegment(accessToken, {
          line_id: selectedLine.id,
          from_pole_id: targetSlot.fromPole.id,
          to_pole_id: targetSlot.toPole.id,
          device_id: fromSegment.device_id
        });
      }
      toast.success(t("engineering.grid.deviceMoved"));
      await reloadDetail(selectedLine.id);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : t("toasts.deviceMoveFail"));
    } finally {
      setBusy(false);
    }
  };

  const handleDeleteSegment = async (segment: LineSegment) => {
    if (!await asyncConfirm(t("engineering.grid.confirmDeleteSegment"))) return;
    setBusy(true);
    setSegmentMenu(null);
    try {
      await deleteSegment(accessToken, segment.id);
      toast.success(t("toasts.segmentDeleted"));
      if (selectedLine) await reloadDetail(selectedLine.id);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : t("toasts.segmentDeleteFail"));
    } finally {
      setBusy(false);
    }
  };

  const handleReverseOrder = async () => {
    if (!selectedLine) return;
    if (!await asyncConfirm(t("engineering.grid.confirmReverse"))) return;
    setBusy(true);
    try {
      await reversePoles(accessToken, selectedLine.id);
      toast.success(t("toasts.lineReversed"));
      await reloadDetail(selectedLine.id);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : t("toasts.lineReverseFail"));
    } finally {
      setBusy(false);
    }
  };

  // Drag-to-reorder (HTML5 native, basit)
  const [draggedPoleId, setDraggedPoleId] = useState<number | null>(null);
  // Segmentlere bağlı cihazları sürükle-bırak ile başka segmente taşımak için.
  const [draggedDeviceSegId, setDraggedDeviceSegId] = useState<number | null>(null);

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
      toast.success(t("toasts.orderUpdated"));
      await reloadDetail(selectedLine.id);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : t("toasts.orderUpdateFail"));
    } finally {
      setBusy(false);
    }
  };

  // Liste görünümünde bir segmentin cihazını başka segmente sürükle-bırak ile taşı.
  // (Backend zaten handleMoveDeviceToOtherSlot ile çalışıyor; burada yalnız hedef segmenti seçiyoruz.)
  const handleDeviceDropOnSlot = async (targetSlot: SegmentSlot) => {
    if (draggedDeviceSegId === null) return;
    if (targetSlot.segment && targetSlot.segment.id === draggedDeviceSegId) {
      setDraggedDeviceSegId(null);
      return;
    }
    const fromSeg = detail?.segments.find((s) => s.id === draggedDeviceSegId) ?? null;
    setDraggedDeviceSegId(null);
    if (!fromSeg) return;
    await handleMoveDeviceToOtherSlot(fromSeg, targetSlot);
  };

  return (
    <section className="tab-panel grid-mgmt-panel">
      <div className="grid-mgmt-layout">
        {/* SOL — Bölgeler */}
        <div className="grid-mgmt-col grid-mgmt-col-regions">
          <div className="grid-mgmt-col-head">
            <h4>{t("engineering.grid.regions")}</h4>
            <div className="grid-mgmt-head-btns">
              <button
                type="button"
                className="device-add-plus-btn"
                title={t("engineering.grid.wizard.title")}
                aria-label={t("engineering.grid.wizard.title")}
                onClick={() => setImportModalOpen(true)}
              >
                <span className="material-symbols-outlined">auto_awesome</span>
              </button>
              <button
                type="button"
                className="device-add-plus-btn device-add-plus-btn--primary"
                title={t("engineering.grid.addRegionShort")}
                aria-label={t("engineering.grid.addRegionShort")}
                onClick={() => {
                  setEditingRegion(null);
                  setRegionModalOpen(true);
                }}
              >
                +
              </button>
            </div>
          </div>
          <div className="grid-mgmt-list">
            {regions.length === 0 ? (
              <div className="empty-state empty-state--compact">
                <span className="material-symbols-outlined">map</span>
                <p>{t("engineering.grid.noRegions")}</p>
              </div>
            ) : null}
            {regions.map((r) => (
              <div
                key={r.id}
                className={`grid-mgmt-list-item ${selectedRegionId === r.id ? "active" : ""}`}
                onClick={() => setSelectedRegionId(r.id)}
              >
                <div className="grid-mgmt-list-item-main">
                  <strong>{r.name}</strong>
                </div>
                <span className="grid-mgmt-list-count">{t("engineering.grid.lineCount", { count: r.line_count ?? 0 })}</span>
                <div className="grid-mgmt-list-actions">
                  <button type="button" className="icon-btn" title={t("common.edit")}
                    onClick={(e) => { e.stopPropagation(); setEditingRegion(r); setRegionModalOpen(true); }}>✎</button>
                  <button type="button" className="icon-btn icon-btn-danger" title={t("common.delete")}
                    onClick={(e) => { e.stopPropagation(); void handleDeleteRegion(r); }}>✕</button>
                </div>
              </div>
            ))}
          </div>
        </div>

        {/* ORTA — Hatlar */}
        <div className="grid-mgmt-col grid-mgmt-col-lines">
          <div className="grid-mgmt-col-head">
            <h4>{t("engineering.grid.lines")}{selectedRegion ? ` · ${selectedRegion.name}` : ""}</h4>
            <button
              type="button"
              className="device-add-plus-btn device-add-plus-btn--primary"
              disabled={!selectedRegion}
              title={t("engineering.grid.addLineShort")}
              aria-label={t("engineering.grid.addLineShort")}
              onClick={() => { setEditingLine(null); setLineModalOpen(true); }}
            >
              +
            </button>
          </div>
          <div className="grid-mgmt-list">
            {!selectedRegion ? (
              <div className="empty-state empty-state--compact">
                <span className="material-symbols-outlined">west</span>
                <p>{t("engineering.grid.noLinesPickRegion")}</p>
              </div>
            ) : lines.length === 0 ? (
              <div className="empty-state empty-state--compact">
                <span className="material-symbols-outlined">polyline</span>
                <p>{t("engineering.grid.noLinesInRegion")}</p>
              </div>
            ) : null}
            {lines.map((l) => (
              <div key={l.id}
                className={`grid-mgmt-list-item ${selectedLineId === l.id ? "active" : ""}`}
                onClick={() => setSelectedLineId(l.id)}
              >
                <div className="grid-mgmt-list-item-main">
                  <strong>{l.name}</strong>
                </div>
                <span className="grid-mgmt-list-count">{t("engineering.grid.poleSegmentCount", { poles: l.pole_count ?? 0, segments: l.segment_count ?? 0 })}</span>
                <div className="grid-mgmt-list-actions">
                  <button type="button" className="icon-btn" title={t("common.edit")}
                    onClick={(e) => { e.stopPropagation(); setEditingLine(l); setLineModalOpen(true); }}>✎</button>
                  <button type="button" className="icon-btn icon-btn-danger" title={t("common.delete")}
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
              <h4>{t("engineering.grid.lineDetailTitle")}{selectedLine ? ` · ${selectedLine.name}` : ""}</h4>
              {selectedLine ? (
                <span className="helper-text">{t("engineering.grid.poleSegmentCount", { poles: sortedPoles.length, segments: detail?.segments.length ?? 0 })}</span>
              ) : null}
            </div>
            {selectedLine ? (
              <div className="grid-mgmt-tabs">
                <button className={`grid-mgmt-tab ${detailTab === "map" ? "active" : ""}`}
                  onClick={() => setDetailTab("map")}>{t("engineering.grid.tabMap")}</button>
                <button className={`grid-mgmt-tab ${detailTab === "list" ? "active" : ""}`}
                  onClick={() => setDetailTab("list")}>{t("engineering.grid.tabList")}</button>
              </div>
            ) : null}
          </div>

          {!selectedLine ? (
            <div className="empty-state">
              <span className="material-symbols-outlined">route</span>
              <p>{t("engineering.grid.pickLineHint")}</p>
            </div>
          ) : detailTab === "map" ? (
            <>
              <div className={`grid-mgmt-map-toolbar ${editMode ? "is-edit-mode" : ""}`}>
                <div className="grid-mgmt-toolbar-group grid-mgmt-toolbar-left">
                  <button
                    className={`grid-mgmt-tool-btn ${editMode ? "is-active" : ""}`}
                    onClick={async () => {
                      if (editMode && hasUnsavedDraft) {
                        if (!(await asyncConfirm(t("engineering.grid.editModeUnsavedConfirm")))) return;
                        setDraftPoleAdds([]);
                        setDraftPoleEdits(new Map());
                        setDraftPoleDeletes(new Set());
                        setDraftDevicePositions(new Map());
                      }
                      setEditMode(!editMode);
                      if (editMode) setAddPoleMode(false);
                    }}
                    disabled={busy}
                    title={t("engineering.grid.editModeBtnTooltip")}
                  >
                    <span className="material-symbols-outlined">edit</span>
                    {editMode ? t("engineering.grid.editModeOn") : t("engineering.grid.editModeOff")}
                  </button>

                  <label className="grid-mgmt-toggle">
                    <input
                      type="checkbox"
                      checked={showOtherLines}
                      onChange={(e) => setShowOtherLines(e.target.checked)}
                    />
                    <span>{t("engineering.grid.showOtherLines")}</span>
                  </label>
                </div>

                {editMode ? (
                  <div className="grid-mgmt-toolbar-group grid-mgmt-toolbar-right">
                    <button
                      className={`grid-mgmt-tool-btn ${addPoleMode ? "is-active" : ""}`}
                      onClick={() => setAddPoleMode(!addPoleMode)}
                      disabled={busy}
                      title={t("engineering.grid.addPoleModeTooltip")}
                    >
                      <span className="material-symbols-outlined">add_location_alt</span>
                      {t("engineering.grid.addPole")}
                    </button>

                    <button
                      className="grid-mgmt-tool-btn"
                      disabled={sortedPoles.length < 2 || busy}
                      onClick={() => void handleReverseOrder()}
                      title={t("engineering.grid.reverseOrderTooltip")}
                    >
                      <span className="material-symbols-outlined">swap_horiz</span>
                      {t("engineering.grid.reverseShort")}
                    </button>

                    <span className="grid-mgmt-toolbar-sep" />

                    <button
                      className="grid-mgmt-tool-btn is-undo"
                      onClick={() => void handleUndoDraft()}
                      disabled={!hasUnsavedDraft || busy}
                      title={t("engineering.grid.undoDraftTooltip")}
                    >
                      <span className="material-symbols-outlined">undo</span>
                      {t("engineering.grid.undoDraft")}
                    </button>
                    <button
                      className="grid-mgmt-tool-btn is-save"
                      onClick={() => void handleSaveDraft()}
                      disabled={!hasUnsavedDraft || busy}
                      title={t("engineering.grid.saveDraftTooltip")}
                    >
                      <span className="material-symbols-outlined">save</span>
                      {t("engineering.grid.saveDraft")}
                      {hasUnsavedDraft ? (
                        <span className="grid-mgmt-draft-badge">
                          {draftPoleAdds.length + draftPoleEdits.size + draftPoleDeletes.size + draftDevicePositions.size}
                        </span>
                      ) : null}
                    </button>
                  </div>
                ) : null}
              </div>

              <div
                className={`grid-mgmt-map-shell ${addPoleMode ? "is-add-mode" : ""}`}
                onClick={() => {
                  setSegmentMenu(null);
                  setMapContextMenu(null);
                }}
              >
                <MapContainer
                  center={mapCenter}
                  zoom={mapZoom}
                  className="grid-mgmt-map"
                  scrollWheelZoom
                >
                  <LayersControl position="topright">
                    <LayersControl.BaseLayer checked name="Sokak (OSM)">
                      <TileLayer
                        url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"
                        attribution='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>'
                      />
                    </LayersControl.BaseLayer>
                    <LayersControl.BaseLayer name="Uydu (Esri)">
                      <TileLayer
                        url="https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}"
                        attribution="Tiles &copy; Esri"
                        maxZoom={19}
                      />
                    </LayersControl.BaseLayer>
                    <LayersControl.BaseLayer name="Topografya">
                      <TileLayer
                        url="https://{s}.tile.opentopomap.org/{z}/{x}/{y}.png"
                        attribution='&copy; <a href="https://opentopomap.org">OpenTopoMap</a>'
                        maxZoom={17}
                      />
                    </LayersControl.BaseLayer>
                  </LayersControl>
                  <MapLayerSwitchFix />

                  {/* Hat secildiginde haritayi otomatik olarak hattin tum
                      direklerini kapsayan alana zoomla. */}
                  <FitToLine lineId={selectedLineId} poles={sortedPoles} />

                  {addPoleMode ? (
                    <MapClickHandler onClick={(lat, lon) => void handleMapClickAddPole(lat, lon)} />
                  ) : null}

                  {/* Edit modunda haritaya sag tik -> context menu (direk ekle vb.) */}
                  {editMode ? (
                    <MapContextMenuHandler
                      onContextMenu={(lat, lng, x, y) =>
                        setMapContextMenu({ type: "map", lat, lng, x, y })
                      }
                    />
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

                  {/* Bransman: secili hat baska bir hattin diregine bagliysa,
                      o pole konumu ile hattin ilk diregi arasinda kesik mavi cizgi. */}
                  {branchParentPos && sortedPoles.length > 0 ? (
                    <Polyline
                      positions={[
                        branchParentPos,
                        [sortedPoles[0].latitude, sortedPoles[0].longitude]
                      ]}
                      pathOptions={{
                        color: "#6366f1",
                        weight: 3,
                        opacity: 0.85,
                        dashArray: "6 4"
                      }}
                    >
                      <Tooltip sticky>
                        <strong>{t("engineering.grid.branch")}</strong>
                        <br />
                        <em>{t("engineering.grid.branchExplain")}</em>
                      </Tooltip>
                    </Polyline>
                  ) : null}

                  {/* Polyline segmentleri ayri cizilir; sol VE sag tik ayni menuyu acar.
                      Direk ekleme modu aktif ise tikla geçer (haritaya direk eklenmesin diye
                      stopPropagation ile yutulur). */}
                  {segmentSlots.map((slot, idx) => {
                    const positions: [number, number][] = [
                      [slot.fromPole.latitude, slot.fromPole.longitude],
                      [slot.toPole.latitude, slot.toPole.longitude]
                    ];
                    const hasDevice = slot.segments.some((s) => s.device_id);
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
                          // Saglikli (alarm yok) hat -> yesil; cihaz baglandiginda
                          // daha kalin/koyu, cihazsiz slot biraz daha ince/acik.
                          color: hasDevice ? "#16a34a" : "#22c55e",
                          weight: hasDevice ? 6 : 4,
                          opacity: hasDevice ? 0.95 : 0.7
                        }}
                        eventHandlers={{
                          click: openMenu,
                          contextmenu: openMenu
                        }}
                      />
                    );
                  })}

                  {/* Direkler — sequence_no etiketli. Draft modunda taslak işaretleri */}
                  {sortedPoles.map((p, idx) => {
                    const isStart = idx === 0;
                    const isEnd = idx === sortedPoles.length - 1;
                    const draftState: "added" | "moved" | null = editMode
                      ? p.id < 0
                        ? "added"
                        : draftPoleEdits.has(p.id)
                          ? "moved"
                          : null
                      : null;
                    return (
                      <Marker
                        key={p.id}
                        position={poleMarkerPositions.get(p.id) ?? [p.latitude, p.longitude]}
                        icon={
                          poleMarkerIcons.get(p.id) ??
                          poleIcon(String(p.sequence_no), isStart, isEnd, draftState, p.pole_type)
                        }
                        draggable={editMode}
                        eventHandlers={{
                          click: () => {
                            // Draft eklenen direği düzenle modali ile değiştirmek
                            // mantıksız (henüz backend'de yok). Sessiz geç.
                            if (p.id < 0) return;
                            setEditingPole(p);
                          },
                          contextmenu: (event: L.LeafletMouseEvent) => {
                            event.originalEvent.preventDefault();
                            event.originalEvent.stopPropagation();
                            // Edit modunda direk uzerine sag tik -> context menu
                            // (Sil, Aralik ekle vs.); edit disinda direkt sil.
                            if (editMode) {
                              const native = event.originalEvent;
                              setMapContextMenu({
                                type: "pole",
                                lat: p.latitude,
                                lng: p.longitude,
                                x: native.clientX,
                                y: native.clientY,
                                poleId: p.id,
                                poleSeq: p.sequence_no
                              });
                            } else {
                              void handleDeletePole(p);
                            }
                          },
                          dragstart: () => {
                            setDraggingPole({ id: p.id, lat: p.latitude, lng: p.longitude });
                          },
                          drag: (event: L.LeafletEvent) => {
                            const ll = (event.target as L.Marker).getLatLng();
                            setDraggingPole({ id: p.id, lat: ll.lat, lng: ll.lng });
                          },
                          dragend: (event: L.DragEndEvent) => {
                            const ll = (event.target as L.Marker).getLatLng();
                            setDraggingPole(null);
                            void handlePoleDragEnd(p, ll.lat, ll.lng);
                          }
                        }}
                      >
                        <Tooltip>
                          {p.name ?? `Direk #${p.sequence_no}`}
                          {isStart ? " (BAŞ)" : isEnd ? " (SON)" : ""}
                          {draftState === "added" ? " · YENİ (taslak)" : ""}
                          {draftState === "moved" ? " · TAŞINDI (taslak)" : ""}
                          <br />
                          <em style={{ fontSize: 10 }}>
                            {editMode
                              ? "Sürükle: taşı (taslak) · Sağ tık: sil (taslak)"
                              : "Düzenleme için 'Düzenleme Modu'nu açın."}
                          </em>
                        </Tooltip>
                      </Marker>
                    );
                  })}

                  {/* Segmentlere bagli cihazlar — coklu cihaz icin ayni slot'ta
                      birden fazla marker, dagitik konumlarda (1/n+1, 2/n+1, ...). */}
                  {segmentSlots.flatMap((slot) => {
                    const segsWithDevice = slot.segments.filter((s) => s.device_id);
                    const total = segsWithDevice.length;
                    if (total === 0) return [];
                    // Slot uzerinde projektif kesim icin a (from) -> b (to) vektoru.
                    const ax = slot.fromPole.latitude;
                    const ay = slot.fromPole.longitude;
                    const bx = slot.toPole.latitude;
                    const by = slot.toPole.longitude;
                    const dx = bx - ax;
                    const dy = by - ay;
                    const len2 = dx * dx + dy * dy;
                    return segsWithDevice.map((seg, idx) => {
                      // Once draft, sonra backend value, sonra auto orta-nokta.
                      const draft = draftDevicePositions.get(seg.id);
                      const tManual = draft?.device_position_t ?? seg.device_position_t;
                      const tParam = (tManual !== null && tManual !== undefined && tManual >= 0 && tManual <= 1)
                        ? tManual
                        : (idx + 1) / (total + 1);
                      const lat = ax + dx * tParam;
                      const lon = ay + dy * tParam;
                      const dev = devices.find((d) => d.id === seg.device_id);
                      const openMenu = (event: L.LeafletMouseEvent) => {
                        event.originalEvent.preventDefault();
                        event.originalEvent.stopPropagation();
                        const native = event.originalEvent;
                        setSegmentMenu({
                          segment: seg,
                          pseudoFromId: slot.fromPole.id,
                          pseudoToId: slot.toPole.id,
                          x: native.clientX,
                          y: native.clientY
                        });
                      };
                      // Cihaz suruklendiginde: drag boyunca her tickte slot
                      // vektorune perpendicular projekte et, marker'i zorla
                      // hat uzerinde tut. Bu sayede cihaz "havada" duramaz,
                      // sadece slot uzerinde kayar.
                      const handleDrag = (event: L.LeafletEvent) => {
                        if (len2 === 0) return;
                        const m = event.target as L.Marker;
                        const ll = m.getLatLng();
                        const px = ll.lat - ax;
                        const py = ll.lng - ay;
                        const tProj = Math.max(0, Math.min(1, (px * dx + py * dy) / len2));
                        const projLat = ax + dx * tProj;
                        const projLng = ay + dy * tProj;
                        // Sadece sapma varsa duzelt (sonsuz dongu olmasin diye threshold)
                        if (Math.abs(projLat - ll.lat) > 1e-9 || Math.abs(projLng - ll.lng) > 1e-9) {
                          m.setLatLng([projLat, projLng]);
                        }
                      };
                      const handleDragEnd = (event: L.DragEndEvent) => {
                        if (len2 === 0) return;
                        const ll = (event.target as L.Marker).getLatLng();
                        const px = ll.lat - ax;
                        const py = ll.lng - ay;
                        const tNew = Math.max(0, Math.min(1, (px * dx + py * dy) / len2));
                        void handleDeviceDragEnd(seg, tNew);
                      };
                      return (
                        <Marker
                          key={`dev-${seg.id}-${editMode ? "edit" : "view"}`}
                          position={[lat, lon]}
                          icon={deviceIcon(dev?.alarmActive ?? false)}
                          draggable={editMode}
                          eventHandlers={{
                            click: openMenu,
                            contextmenu: openMenu,
                            drag: handleDrag,
                            dragend: handleDragEnd
                          }}
                        >
                          {/* Cihaz adi her zaman kalici etiket olarak gozuksun
                              (edit modunda olmasak da). Yakindan bakanlar
                              hangi cihazin nerede oldugunu hizla gorur. */}
                          {dev ? (
                            <Tooltip
                              permanent
                              direction="top"
                              offset={[0, -8]}
                              className="grid-device-name-label"
                            >
                              {dev.name}
                            </Tooltip>
                          ) : null}
                          <Tooltip sticky direction="bottom" offset={[0, 8]}>
                            {dev ? `${dev.name} (${dev.code})` : `Cihaz #${seg.device_id}`}
                            <br />
                            {slot.fromPole.sequence_no} ↔ {slot.toPole.sequence_no}
                            {total > 1 ? ` · ${idx + 1}/${total}` : ""}
                            {editMode ? (<><br /><em>{t("engineering.grid.clickMoveOrRemove")}</em></>) : null}
                          </Tooltip>
                        </Marker>
                      );
                    });
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

                {/* Harita context menu (sag tik): bos alan / direk / polyline */}
                {mapContextMenu ? (
                  <div
                    className="map-ctx-menu"
                    style={{ left: mapContextMenu.x, top: mapContextMenu.y }}
                    onClick={(e) => e.stopPropagation()}
                  >
                    <div className="map-ctx-menu-head">
                      {mapContextMenu.type === "pole"
                        ? t("engineering.grid.poleHeader", { seq: mapContextMenu.poleSeq })
                        : mapContextMenu.type === "line"
                          ? t("engineering.grid.onLine")
                          : t("engineering.grid.mapLocation")}
                      <button
                        type="button"
                        className="map-ctx-menu-close"
                        onClick={() => setMapContextMenu(null)}
                      >
                        ×
                      </button>
                    </div>
                    {mapContextMenu.type === "map" ? (
                      <>
                        <button
                          type="button"
                          className="map-ctx-menu-item"
                          onClick={() => {
                            void handleMapClickAddPole(mapContextMenu.lat, mapContextMenu.lng);
                            setMapContextMenu(null);
                          }}
                        >
                          <span className="material-symbols-outlined">add_location</span>
                          {t("engineering.grid.addPoleAtEnd")}
                        </button>
                      </>
                    ) : null}
                    {mapContextMenu.type === "pole" && mapContextMenu.poleId !== undefined ? (
                      <>
                        <button
                          type="button"
                          className="map-ctx-menu-item"
                          onClick={() => {
                            const poleObj = sortedPoles.find((p) => p.id === mapContextMenu.poleId);
                            if (poleObj) setEditingPole(poleObj);
                            setMapContextMenu(null);
                          }}
                        >
                          <span className="material-symbols-outlined">edit</span>
                          {t("engineering.grid.editPole")}
                        </button>
                        <button
                          type="button"
                          className="map-ctx-menu-item"
                          onClick={() => {
                            // Bu direkten ONCE araya direk ekle (yeni direk
                            // bu seq'i alir, mevcut diregin seq'i 1 artar).
                            const seq = mapContextMenu.poleSeq ?? 1;
                            void handleInsertPoleAt(seq, mapContextMenu.lat, mapContextMenu.lng);
                            setMapContextMenu(null);
                          }}
                        >
                          <span className="material-symbols-outlined">arrow_upward</span>
                          {t("engineering.grid.insertPoleBefore")}
                        </button>
                        <button
                          type="button"
                          className="map-ctx-menu-item"
                          onClick={() => {
                            // Bu direkten SONRA araya direk ekle.
                            const seq = (mapContextMenu.poleSeq ?? 0) + 1;
                            void handleInsertPoleAt(seq, mapContextMenu.lat, mapContextMenu.lng);
                            setMapContextMenu(null);
                          }}
                        >
                          <span className="material-symbols-outlined">arrow_downward</span>
                          {t("engineering.grid.insertPoleAfter")}
                        </button>
                        <button
                          type="button"
                          className="map-ctx-menu-item map-ctx-menu-item--danger"
                          onClick={() => {
                            const poleObj = sortedPoles.find((p) => p.id === mapContextMenu.poleId);
                            if (poleObj) void handleDeletePole(poleObj);
                            setMapContextMenu(null);
                          }}
                        >
                          <span className="material-symbols-outlined">delete</span>
                          {t("engineering.grid.deletePole")}
                        </button>
                      </>
                    ) : null}
                  </div>
                ) : null}
              </div>
            </>
          ) : (
            // ===== LISTE TAB =====
            <div className="grid-mgmt-list-tab">
              <div className="grid-mgmt-list-tab-toolbar">
                <button
                  className="grid-mgmt-tool-btn"
                  disabled={sortedPoles.length < 2 || busy}
                  onClick={() => void handleReverseOrder()}
                >
                  <span className="material-symbols-outlined">swap_horiz</span>
                  {t("engineering.grid.listTab.reverseOrder")}
                </button>
                <span className="helper-text">
                  {t("engineering.grid.listTab.dragHint")}
                </span>
              </div>

              <div className="grid-mgmt-tree grid-mgmt-tree-scroll">
                {sortedPoles.length === 0 ? (
                  <p className="helper-text">{t("engineering.grid.listTab.emptyHint")}</p>
                ) : null}
                {sortedPoles.map((p, idx) => {
                  const isStart = idx === 0;
                  const isEnd = idx === sortedPoles.length - 1;
                  const nextSlot = segmentSlots[idx]; // i. direk -> i+1
                  const dev = nextSlot?.segment?.device_id
                    ? devices.find((d) => d.id === nextSlot.segment!.device_id)
                    : null;
                  // Material icon — pin/lightning emoji yerine. SVG-tabanli,
                  // tarayicilar arasi tutarli, renklendirme CSS'ten gelir.
                  const iconName =
                    p.pole_type === "transformer"
                      ? "bolt"
                      : p.pole_type === "breaker"
                        ? "electrical_services"
                        : "location_on";
                  const isDeviceDropTarget =
                    draggedDeviceSegId !== null &&
                    nextSlot &&
                    (!nextSlot.segment || nextSlot.segment.id !== draggedDeviceSegId);
                  return (
                    <div key={p.id} className="grid-mgmt-tree-pair">
                      <div
                        className={`grid-mgmt-pole-card ${
                          draggedPoleId === p.id ? "is-dragging" : ""
                        } ${isStart ? "is-start" : isEnd ? "is-end" : ""}`}
                        draggable
                        onDragStart={() => setDraggedPoleId(p.id)}
                        onDragOver={(e) => e.preventDefault()}
                        onDrop={() => void handleDrop(p.id)}
                      >
                        <span className="grid-mgmt-pole-card-handle" title={t("common.drag")}>
                          <span className="material-symbols-outlined">drag_indicator</span>
                        </span>
                        <span className={`grid-mgmt-pole-card-icon ${
                          p.pole_type === "transformer"
                            ? "is-transformer"
                            : p.pole_type === "breaker"
                              ? "is-breaker"
                              : ""
                        }`}>
                          <span className="material-symbols-outlined">{iconName}</span>
                        </span>
                        <div className="grid-mgmt-pole-card-main">
                          <div className="grid-mgmt-pole-card-title">
                            <span className={`grid-mgmt-pole-card-seq ${
                              isStart ? "is-start" : isEnd ? "is-end" : ""
                            }`}>
                              #{p.sequence_no}
                            </span>
                            <strong>{p.name ?? "(adsız)"}</strong>
                            {isStart ? <span className="grid-mgmt-pole-card-tag is-start">BAŞ</span> : null}
                            {isEnd ? <span className="grid-mgmt-pole-card-tag is-end">SON</span> : null}
                            {p.pole_type === "transformer" ? (
                              <span className="grid-mgmt-pole-card-tag is-type">{t("engineering.grid.typeTransformer")}</span>
                            ) : p.pole_type === "breaker" ? (
                              <span className="grid-mgmt-pole-card-tag is-type">{t("engineering.grid.typeBreaker")}</span>
                            ) : null}
                          </div>
                          <div className="grid-mgmt-pole-card-meta">
                            <span className="material-symbols-outlined">location_on</span>
                            {p.latitude.toFixed(5)}, {p.longitude.toFixed(5)}
                          </div>
                        </div>
                        <div className="grid-mgmt-pole-card-actions">
                          <button className="icon-btn" title="Düzenle"
                            onClick={() => setEditingPole(p)}>
                            <span className="material-symbols-outlined">edit</span>
                          </button>
                          <button className="icon-btn icon-btn-danger" title="Sil"
                            onClick={() => void handleDeletePole(p)}>
                            <span className="material-symbols-outlined">delete</span>
                          </button>
                        </div>
                      </div>

                      {nextSlot ? (
                        <div
                          className={`grid-mgmt-segment-card ${
                            isDeviceDropTarget ? "is-drop-target" : ""
                          } ${dev ? "has-device" : "is-empty"}`}
                          onDragOver={(e) => {
                            if (draggedDeviceSegId !== null) e.preventDefault();
                          }}
                          onDrop={() => void handleDeviceDropOnSlot(nextSlot)}
                        >
                          <div className="grid-mgmt-segment-card-track">
                            <span className="grid-mgmt-segment-card-dot is-from">
                              {nextSlot.fromPole.sequence_no}
                            </span>
                            <span className="grid-mgmt-segment-card-line" />
                            <span className="grid-mgmt-segment-card-dot is-to">
                              {nextSlot.toPole.sequence_no}
                            </span>
                          </div>

                          {dev ? (
                            <div
                              className={`grid-mgmt-device-chip ${
                                dev.alarmActive ? "is-alarm" : "is-normal"
                              } ${draggedDeviceSegId === nextSlot.segment?.id ? "is-dragging" : ""}`}
                              draggable
                              onDragStart={() => {
                                if (nextSlot.segment) setDraggedDeviceSegId(nextSlot.segment.id);
                              }}
                              onDragEnd={() => setDraggedDeviceSegId(null)}
                              title="Sürükle: başka segmente taşı"
                            >
                              <span className="grid-mgmt-device-chip-handle">
                                <span className="material-symbols-outlined">drag_indicator</span>
                              </span>
                              <span className={`grid-mgmt-device-chip-status ${dev.alarmActive ? "is-alarm" : "is-normal"}`}></span>
                              <div className="grid-mgmt-device-chip-main">
                                <strong>{dev.name}</strong>
                                <code>{dev.code}</code>
                              </div>
                              <button
                                className="icon-btn icon-btn-danger"
                                title="Cihazı bu segmentten kaldır"
                                onClick={() => void handleDetachDevice(nextSlot.segment!)}
                              >
                                <span className="material-symbols-outlined">link_off</span>
                              </button>
                            </div>
                          ) : (
                            <div className="grid-mgmt-device-empty">
                              <span className="material-symbols-outlined">add_link</span>
                              <select
                                defaultValue=""
                                className="grid-mgmt-segment-select"
                                onChange={(e) => {
                                  const id = Number(e.target.value);
                                  if (id) void handleAttachDevice(nextSlot, id);
                                }}
                              >
                                <option value="">{t("engineering.grid.assignDevicePlaceholder")}</option>
                                {availableDevices.map((d) => (
                                  <option key={d.id} value={d.id}>{d.name} ({d.code})</option>
                                ))}
                              </select>
                              <span className="helper-text">veya cihazı buraya sürükle</span>
                            </div>
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
      {importModalOpen ? (
        <LineWizardModal
          accessToken={accessToken}
          onClose={() => setImportModalOpen(false)}
          onImported={async () => {
            await reloadRegions();
            if (selectedRegionId !== null) await reloadLines(selectedRegionId);
            if (selectedLineId !== null) await reloadDetail(selectedLineId);
          }}
        />
      ) : null}
      {regionModalOpen ? (
        <RegionModal initial={editingRegion} busy={busy}
          onClose={() => setRegionModalOpen(false)}
          onSubmit={async (payload) => {
            setBusy(true);
            try {
              if (editingRegion) {
                await updateRegion(accessToken, editingRegion.id, payload);
                toast.success(t("toasts.regionUpdated"));
              } else {
                await createRegion(accessToken, payload);
                toast.success(t("toasts.regionAdded"));
              }
              setRegionModalOpen(false);
              await reloadRegions();
            } catch (err) {
              toast.error(err instanceof Error ? err.message : t("toasts.regionSaveFail"));
            } finally { setBusy(false); }
          }}
        />
      ) : null}

      {lineModalOpen && selectedRegion ? (
        <LineModal
          regionId={selectedRegion.id}
          initial={editingLine}
          busy={busy}
          gridSnapshot={gridSnapshot}
          currentLineId={editingLine?.id ?? null}
          onClose={() => setLineModalOpen(false)}
          onSubmit={async (payload) => {
            setBusy(true);
            try {
              if (editingLine) {
                await updateLine(accessToken, editingLine.id, payload);
                toast.success(t("toasts.lineUpdated"));
              } else {
                await createLine(accessToken, payload);
                toast.success(t("toasts.lineAdded"));
              }
              setLineModalOpen(false);
              await reloadLines(selectedRegion.id);
            } catch (err) {
              toast.error(err instanceof Error ? err.message : t("toasts.lineSaveFail"));
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
              toast.success(t("toasts.poleUpdated"));
              setEditingPole(null);
              if (selectedLine) await reloadDetail(selectedLine.id);
            } catch (err) {
              toast.error(err instanceof Error ? err.message : t("toasts.poleUpdateFail"));
            } finally { setBusy(false); }
          }}
        />
      ) : null}
    </section>
  );

  // ----- silme yardimcilari -----
  async function handleDeleteRegion(r: Region) {
    if (!await asyncConfirm(t("toasts.regionDeleteConfirm", { name: r.name }))) return;
    try {
      await deleteRegion(accessToken, r.id);
      toast.success(t("toasts.regionDeleted"));
      if (selectedRegionId === r.id) setSelectedRegionId(null);
      await reloadRegions();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : t("toasts.regionDeleteFail"));
    }
  }
  async function handleDeleteLine(l: Line) {
    if (!await asyncConfirm(t("toasts.lineDeleteConfirm", { name: l.name }))) return;
    try {
      await deleteLine(accessToken, l.id);
      toast.success(t("toasts.lineDeleted"));
      if (selectedLineId === l.id) setSelectedLineId(null);
      if (selectedRegionId !== null) await reloadLines(selectedRegionId);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : t("toasts.lineDeleteFail"));
    }
  }
  async function handleDeletePole(p: Pole) {
    // Draft modunda: yeni eklenen draft direkse listeden çıkar; aksi halde delete olarak işaretle.
    if (editMode) {
      if (p.id < 0) {
        setDraftPoleAdds((prev) => prev.filter((x) => x.id !== p.id));
        toast.success(t("toasts.poleDraftRemoved"));
      } else {
        setDraftPoleDeletes((prev) => new Set(prev).add(p.id));
        // Taslakta sadece bu direğin override'ı varsa o da artık anlamsız — temizle.
        setDraftPoleEdits((prev) => {
          if (!prev.has(p.id)) return prev;
          const next = new Map(prev);
          next.delete(p.id);
          return next;
        });
        toast.success(t("toasts.poleDraftDeleted"));
      }
      return;
    }
    if (!await asyncConfirm(t("toasts.poleDeleteConfirm", { seq: p.sequence_no }))) return;
    try {
      await deletePole(accessToken, p.id);
      toast.success(t("toasts.poleDeleted"));
      if (selectedLineId !== null) await reloadDetail(selectedLineId);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : t("toasts.poleDeleteFail"));
    }
  }

  async function handlePoleDragEnd(p: Pole, lat: number, lng: number) {
    const newLat = Number(lat.toFixed(6));
    const newLng = Number(lng.toFixed(6));

    // Draft modunda: pozisyonu yerelde tut.
    if (editMode) {
      if (p.id < 0) {
        // Henüz kaydedilmemiş direk — adds listesindeki kaydı güncelle.
        setDraftPoleAdds((prev) =>
          prev.map((x) => (x.id === p.id ? { ...x, latitude: newLat, longitude: newLng } : x))
        );
      } else {
        setDraftPoleEdits((prev) => {
          const next = new Map(prev);
          const existing = next.get(p.id) ?? {};
          next.set(p.id, { ...existing, latitude: newLat, longitude: newLng });
          return next;
        });
      }
      return;
    }

    try {
      await import("../../shared/api").then((m) =>
        m.updatePole(accessToken, p.id, {
          latitude: newLat,
          longitude: newLng
        })
      );
      toast.success(`Direk #${p.sequence_no} taşındı.`);
      if (selectedLineId !== null) await reloadDetail(selectedLineId);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Direk taşınamadı.");
      if (selectedLineId !== null) await reloadDetail(selectedLineId); // hata olunca eski koordinata gerial
    }
  }

  // Belirli sequence_no'ya direk ekler (araya ekleme — backend mevcut
  // direkleri otomatik kaydirir).
  async function handleInsertPoleAt(seq: number, lat: number, lng: number) {
    if (!selectedLine) return;
    setBusy(true);
    try {
      await createPole(accessToken, {
        line_id: selectedLine.id,
        sequence_no: seq,
        latitude: lat,
        longitude: lng,
        name: null
      });
      toast.success(`Direk #${seq} eklendi.`);
      await reloadDetail(selectedLine.id);
      if (selectedRegionId !== null) await reloadLines(selectedRegionId);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : t("toasts.poleAddFail"));
    } finally {
      setBusy(false);
    }
  }

  // Slot uzerindeki cihaz marker'i suruklendiginde sadece DRAFT'e yazar;
  // kullanici 'Kaydet' butonuna basana kadar backend'e gitmez. Boylece:
  //   - 'Kaydet' butonu hasUnsavedDraft uzerinden etkin olur
  //   - sayfayi yenilemeden cihazi suruklemek/birakmak/yeniden suruklemek
  //     mumkun (optimistic 'eski yerine donme' problemi yasanmaz)
  function handleDeviceDragEnd(seg: LineSegment, tNew: number) {
    const tRounded = Number(tNew.toFixed(4));
    setDraftDevicePositions((prev) => {
      const next = new Map(prev);
      next.set(seg.id, { device_position_t: tRounded });
      return next;
    });
  }

  async function handleSaveDraft() {
    if (!selectedLine || !hasUnsavedDraft) return;
    setBusy(true);
    try {
      const api = await import("../../shared/api");
      // 1) Yeni eklenenler (sıralı, sequence_no doğru gitsin diye).
      const sortedAdds = [...draftPoleAdds].sort((a, b) => a.sequence_no - b.sequence_no);
      for (const p of sortedAdds) {
        await api.createPole(accessToken, {
          line_id: selectedLine.id,
          sequence_no: p.sequence_no,
          latitude: p.latitude,
          longitude: p.longitude,
          name: p.name ?? null
        });
      }
      // 2) Mevcut direk override'ları (move / rename).
      for (const [poleId, ov] of draftPoleEdits.entries()) {
        const payload: { latitude?: number; longitude?: number; name?: string | null } = {};
        if (ov.latitude !== undefined) payload.latitude = ov.latitude;
        if (ov.longitude !== undefined) payload.longitude = ov.longitude;
        if (ov.name !== undefined) payload.name = ov.name;
        if (Object.keys(payload).length > 0) {
          await api.updatePole(accessToken, poleId, payload);
        }
      }
      // 3) Silmeler (en son — bağlı segmentler de düşer).
      for (const poleId of draftPoleDeletes) {
        await api.deletePole(accessToken, poleId);
      }
      // 4) Cihaz konum override'lari (slot icindeki t parametresi).
      for (const [segmentId, ov] of draftDevicePositions.entries()) {
        await api.updateSegment(accessToken, segmentId, {
          device_position_t: ov.device_position_t
        });
      }
      toast.success(t("toasts.changesSaved"));
      setDraftPoleAdds([]);
      setDraftPoleEdits(new Map());
      setDraftPoleDeletes(new Set());
      setDraftDevicePositions(new Map());
      await reloadDetail(selectedLine.id);
      if (selectedRegionId !== null) await reloadLines(selectedRegionId);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Kaydetme başarısız.");
    } finally {
      setBusy(false);
    }
  }

  async function handleUndoDraft() {
    if (!hasUnsavedDraft) {
      setEditMode(false);
      return;
    }
    if (!(await asyncConfirm("Tüm taslak değişiklikler geri alınsın mı?"))) return;
    setDraftPoleAdds([]);
    setDraftPoleEdits(new Map());
    setDraftPoleDeletes(new Set());
    setDraftDevicePositions(new Map());
    toast.success(t("toasts.draftReverted"));
  }
}

// ============= Helper components =============

/** Hat secimi degistiginde haritayi secili hattin direk tutarini kapsayan
 *  alana otomatik fitBounds ile zoomlar. lineId degisikligini izler;
 *  ayni hat icinde direk eklemek/silmek harita zoom'unu yenilemez. */
function FitToLine({
  lineId,
  poles
}: {
  lineId: number | null;
  poles: { latitude: number; longitude: number }[];
}) {
  const map = useMap();
  const lastLineRef = useRef<number | null>(null);
  useEffect(() => {
    if (lineId === lastLineRef.current) return;
    lastLineRef.current = lineId;
    if (lineId === null || poles.length === 0) return;
    if (poles.length === 1) {
      map.setView([poles[0].latitude, poles[0].longitude], 16, { animate: true });
      return;
    }
    const latlngs: L.LatLngExpression[] = poles.map((p) => [p.latitude, p.longitude]);
    const bounds = L.latLngBounds(latlngs);
    // Pad: kenarlardan biraz nefes payi ver; max zoom 17 (cok yakin gitmesin).
    map.fitBounds(bounds, { padding: [40, 40], maxZoom: 17, animate: true });
  }, [lineId, poles, map]);
  return null;
}

function MapClickHandler({ onClick }: { onClick: (lat: number, lon: number) => void }) {
  useMapEvents({
    click(event) {
      onClick(Number(event.latlng.lat.toFixed(6)), Number(event.latlng.lng.toFixed(6)));
    }
  });
  return null;
}

// Harita uzerinde bos alana sag tikla -> context menu acan helper.
function MapContextMenuHandler({
  onContextMenu
}: {
  onContextMenu: (lat: number, lon: number, x: number, y: number) => void;
}) {
  useMapEvents({
    contextmenu(event) {
      // Sag tik default browser menusunu engelle
      event.originalEvent.preventDefault();
      const native = event.originalEvent;
      onContextMenu(
        Number(event.latlng.lat.toFixed(6)),
        Number(event.latlng.lng.toFixed(6)),
        native.clientX,
        native.clientY
      );
    }
  });
  return null;
}

type CtxSlot = {
  fromPole: Pole;
  toPole: Pole;
  segment: LineSegment | null;
  segments: LineSegment[];
};

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
  slot: CtxSlot;
  allSlots: CtxSlot[];
  devices: DeviceRow[];
  availableDevices: DeviceRow[];
  onAttach: (deviceId: number) => void;
  onDetach: (seg: LineSegment) => void;
  onMove: (seg: LineSegment, target: CtxSlot) => void;
  onDeleteSegment: (seg: LineSegment) => void;
  onClose: () => void;
}) {
  const { t } = useTranslation();
  // Goruntu modlari: 'list' = mevcut cihazlar listesi (default),
  //                  'add'  = yeni cihaz secme arama paneli,
  //                  'move' = bir cihazi baska segmente tasi (movingSeg ile)
  const [view, setView] = useState<"list" | "add" | "move">("list");
  const [movingSeg, setMovingSeg] = useState<LineSegment | null>(null);
  const [search, setSearch] = useState("");

  // Slot'taki cihazli segmentler (sirali).
  const segsWithDevice = slot.segments.filter((s) => s.device_id);

  const filteredAvailable = useMemo(() => {
    const q = search.trim().toLowerCase();
    if (!q) return availableDevices;
    return availableDevices.filter((d) => {
      const text = `${d.name} ${d.code} ${d.gatewayCode ?? ""}`.toLowerCase();
      return text.includes(q);
    });
  }, [availableDevices, search]);

  const otherSlots = useMemo(
    () =>
      allSlots.filter(
        (s) => !(s.fromPole.id === slot.fromPole.id && s.toPole.id === slot.toPole.id)
      ),
    [allSlots, slot]
  );

  const filteredTargets = useMemo(() => {
    const q = search.trim().toLowerCase();
    if (!q) return otherSlots;
    return otherSlots.filter((s) => {
      const text = `${s.fromPole.sequence_no} ${s.toPole.sequence_no} ${s.fromPole.name ?? ""} ${s.toPole.name ?? ""}`.toLowerCase();
      return text.includes(q);
    });
  }, [otherSlots, search]);

  const back = () => {
    setView("list");
    setMovingSeg(null);
    setSearch("");
  };

  return (
    <div className="seg-menu">
      <header className="seg-menu-head">
        <div className="seg-menu-head-text">
          <div className="seg-menu-eyebrow">SEGMENT</div>
          <div className="seg-menu-title">
            <span className="seg-menu-pole">#{slot.fromPole.sequence_no}</span>
            <span className="seg-menu-arrow">→</span>
            <span className="seg-menu-pole">#{slot.toPole.sequence_no}</span>
            {segsWithDevice.length > 0 ? (
              <span className="seg-menu-count">
                {segsWithDevice.length} cihaz
              </span>
            ) : (
              <span className="seg-menu-count seg-menu-count--empty">boş</span>
            )}
          </div>
        </div>
        <button type="button" className="seg-menu-close" onClick={onClose} aria-label="Kapat">
          <span className="material-symbols-outlined">close</span>
        </button>
      </header>

      {view === "list" ? (
        // ===== MEVCUT CIHAZLAR + CIHAZ EKLE =====
        <div className="seg-menu-body">
          {segsWithDevice.length === 0 ? (
            <div className="seg-menu-empty">
              <span className="material-symbols-outlined">cable</span>
              <div>
                <strong>{t("engineering.grid.noDeviceTitle")}</strong>
                <p>{t("engineering.grid.noDeviceHint")}</p>
              </div>
            </div>
          ) : (
            <ul className="seg-menu-devices">
              {segsWithDevice.map((seg, idx) => {
                const d = devices.find((x) => x.id === seg.device_id);
                if (!d) return null;
                const online = d.communicationStatus === "online";
                return (
                  <li key={seg.id} className="seg-menu-device-item">
                    <div className={`seg-menu-device-icon ${online ? "is-online" : "is-offline"}`}>
                      <span className="material-symbols-outlined">offline_bolt</span>
                    </div>
                    <div className="seg-menu-device-main">
                      <div className="seg-menu-device-name-row">
                        <strong className="seg-menu-device-name">{d.name}</strong>
                        {segsWithDevice.length > 1 ? (
                          <span className="seg-menu-device-pos">{idx + 1}/{segsWithDevice.length}</span>
                        ) : null}
                      </div>
                      <div className="seg-menu-device-meta">
                        <code className="seg-menu-device-code">{d.code}</code>
                        {d.gatewayCode ? (
                          <span className="seg-menu-device-gw">{d.gatewayCode}</span>
                        ) : null}
                        <span className={`seg-menu-device-status ${online ? "is-online" : "is-offline"}`}>
                          <span className="dot" />
                          {online ? "çevrimiçi" : "çevrimdışı"}
                        </span>
                      </div>
                    </div>
                    <div className="seg-menu-device-actions">
                      <button
                        type="button"
                        className="seg-menu-icon-btn"
                        title="Başka segmente taşı"
                        onClick={() => {
                          setMovingSeg(seg);
                          setView("move");
                          setSearch("");
                        }}
                      >
                        <span className="material-symbols-outlined">swap_horiz</span>
                      </button>
                      <button
                        type="button"
                        className="seg-menu-icon-btn seg-menu-icon-btn--danger"
                        title="Cihazı kaldır"
                        onClick={() => onDetach(seg)}
                      >
                        <span className="material-symbols-outlined">link_off</span>
                      </button>
                    </div>
                  </li>
                );
              })}
            </ul>
          )}

          <button
            type="button"
            className="seg-menu-add-btn"
            disabled={availableDevices.length === 0}
            onClick={() => {
              setView("add");
              setSearch("");
            }}
            title={
              availableDevices.length === 0
                ? t("engineering.grid.noAssignableDevice")
                : t("engineering.grid.addDeviceTitle")
            }
          >
            <span className="material-symbols-outlined">add</span>
            <span>{t("engineering.grid.addDevice")}</span>
            {availableDevices.length > 0 ? (
              <span className="seg-menu-add-count">{t("engineering.grid.candidateCount", { count: availableDevices.length })}</span>
            ) : null}
          </button>
        </div>
      ) : view === "add" ? (
        // ===== CIHAZ EKLE (arama + secim) =====
        <div className="seg-menu-body">
          <div className="seg-menu-search">
            <span className="material-symbols-outlined">search</span>
            <input
              type="search"
              placeholder={t("engineering.grid.deviceSearch")}
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              autoFocus
            />
            <span className="seg-menu-search-count">
              {filteredAvailable.length}/{availableDevices.length}
            </span>
          </div>

          <ul className="seg-menu-pickable">
            {filteredAvailable.length === 0 ? (
              <li className="seg-menu-empty">
                <span className="material-symbols-outlined">search_off</span>
                <div>
                  <strong>
                    {availableDevices.length === 0
                      ? "Atanabilir cihaz yok"
                      : "Sonuç bulunamadı"}
                  </strong>
                  <p>
                    {availableDevices.length === 0
                      ? "Önce başka bir segmentten cihaz çıkarın."
                      : "Farklı bir arama deneyin."}
                  </p>
                </div>
              </li>
            ) : (
              filteredAvailable.map((d) => {
                const online = d.communicationStatus === "online";
                return (
                  <li key={d.id}>
                    <button
                      type="button"
                      className="seg-menu-pickable-item"
                      onClick={() => onAttach(d.id)}
                    >
                      <div className={`seg-menu-device-icon ${online ? "is-online" : "is-offline"}`}>
                        <span className="material-symbols-outlined">offline_bolt</span>
                      </div>
                      <div className="seg-menu-device-main">
                        <strong className="seg-menu-device-name">{d.name}</strong>
                        <div className="seg-menu-device-meta">
                          <code className="seg-menu-device-code">{d.code}</code>
                          {d.gatewayCode ? (
                            <span className="seg-menu-device-gw">{d.gatewayCode}</span>
                          ) : null}
                          <span className={`seg-menu-device-status ${online ? "is-online" : "is-offline"}`}>
                            <span className="dot" />
                            {online ? "çevrimiçi" : "çevrimdışı"}
                          </span>
                        </div>
                      </div>
                      <span className="seg-menu-pickable-arrow material-symbols-outlined">
                        chevron_right
                      </span>
                    </button>
                  </li>
                );
              })
            )}
          </ul>

          <button type="button" className="seg-menu-back-btn" onClick={back}>
            <span className="material-symbols-outlined">arrow_back</span>
            Geri dön
          </button>
        </div>
      ) : (
        // ===== TASI: hedef segment sec =====
        <div className="seg-menu-body">
          {movingSeg ? (
            (() => {
              const movingDev = devices.find((x) => x.id === movingSeg.device_id);
              return movingDev ? (
                <div className="seg-menu-move-info">
                  <span className="material-symbols-outlined">swap_horiz</span>
                  <div>
                    <strong>{movingDev.name}</strong>
                    <p>{t("engineering.grid.moveTargetHint")}</p>
                  </div>
                </div>
              ) : null;
            })()
          ) : null}
          <div className="seg-menu-search">
            <span className="material-symbols-outlined">search</span>
            <input
              type="search"
              placeholder={t("engineering.grid.segmentSearch")}
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              autoFocus
            />
          </div>
          <ul className="seg-menu-pickable">
            {filteredTargets.length === 0 ? (
              <li className="seg-menu-empty">
                <span className="material-symbols-outlined">search_off</span>
                <div>
                  <strong>
                    {otherSlots.length === 0 ? "Başka segment yok" : "Sonuç bulunamadı"}
                  </strong>
                </div>
              </li>
            ) : (
              filteredTargets.map((s) => {
                const occupiedCount = s.segments.filter((x) => x.device_id).length;
                return (
                  <li key={`${s.fromPole.id}-${s.toPole.id}`}>
                    <button
                      type="button"
                      className="seg-menu-pickable-item"
                      onClick={() => movingSeg && onMove(movingSeg, s)}
                    >
                      <div className="seg-menu-target-route">
                        <span className="seg-menu-pole">#{s.fromPole.sequence_no}</span>
                        <span className="seg-menu-arrow">→</span>
                        <span className="seg-menu-pole">#{s.toPole.sequence_no}</span>
                      </div>
                      <span className={`seg-menu-target-status ${occupiedCount > 0 ? "is-warn" : "is-ok"}`}>
                        {occupiedCount > 0
                          ? `${occupiedCount} cihaz var · yan yana eklenecek`
                          : "boş"}
                      </span>
                      <span className="seg-menu-pickable-arrow material-symbols-outlined">
                        chevron_right
                      </span>
                    </button>
                  </li>
                );
              })
            )}
          </ul>
          <button type="button" className="seg-menu-back-btn" onClick={back}>
            <span className="material-symbols-outlined">arrow_back</span>
            Geri dön
          </button>
        </div>
      )}

    </div>
  );
}

// ============= MODALS =============

// Backend code alani NOT NULL bekledigi icin isimden otomatik kod uretiyoruz.
// Kullanici "Kod" alanini gormez; isim degistirildiginde unique kalmasi icin
// timestamp suffix eklenir.
function autoCodeFromName(name: string, prefix: string = ""): string {
  const slug = (name || prefix || "ITEM")
    .toLocaleUpperCase("tr-TR")
    .normalize("NFD")
    .replace(/[̀-ͯ]/g, "")
    .replace(/[^A-Z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 30);
  const ts = Date.now().toString(36).slice(-5).toUpperCase();
  return `${slug || prefix || "ITEM"}-${ts}`;
}

function RegionModal({
  initial, busy, onClose, onSubmit
}: {
  initial: Region | null; busy: boolean;
  onClose: () => void;
  onSubmit: (payload: Partial<Region>) => Promise<void>;
}) {
  const [name, setName] = useState(initial?.name ?? "");
  const [description, setDescription] = useState(initial?.description ?? "");
  const submit = async (e: FormEvent) => {
    e.preventDefault();
    // Yeni kayitsa otomatik kod uret; mevcut kayitsa eski kodu koru.
    const code = initial?.code ?? autoCodeFromName(name, "BOLGE");
    // color + is_active alanlari UI'dan kaldirildi (kullanici secimine gerek yok).
    // Backend default'lari: color=null (sistem genelinde standart renkler kullanilir),
    // is_active=true. Mevcut kayitta degerleri koru, yeni kayitta default.
    await onSubmit({
      code,
      name: name.trim(),
      description: description.trim() || null,
      color: initial?.color ?? null,
      is_active: initial?.is_active ?? true,
    });
  };
  const { t } = useTranslation();
  return (
    <div className="settings-modal-backdrop">
      <form className="settings-modal" onSubmit={submit}>
        <h3>{initial ? t("engineering.grid.regionEditTitle") : t("engineering.grid.newRegion")}</h3>
        <label>{t("engineering.grid.name")} <input value={name} onChange={(e) => setName(e.target.value)} required autoFocus /></label>
        <label>{t("engineering.grid.description")} <input value={description} onChange={(e) => setDescription(e.target.value)} /></label>
        <div className="settings-actions">
          <button type="button" onClick={onClose} disabled={busy}>{t("engineering.grid.cancel")}</button>
          <button type="submit" className="primary-btn" disabled={busy}>{busy ? "..." : t("engineering.grid.save")}</button>
        </div>
      </form>
    </div>
  );
}

function LineModal({
  regionId, initial, busy, onClose, onSubmit, gridSnapshot, currentLineId
}: {
  regionId: number; initial: Line | null; busy: boolean;
  onClose: () => void;
  onSubmit: (payload: Partial<Line>) => Promise<void>;
  gridSnapshot: GridSnapshot | null;
  currentLineId: number | null;
}) {
  const [name, setName] = useState(initial?.name ?? "");
  const [description, setDescription] = useState(initial?.description ?? "");
  // is_active UI'dan kaldirildi (gereksiz toggle). Yeni kayitlar default
  // true; mevcut kayitlarda eski deger korunur.
  // Bransman: bu hat baska bir hattin diregine bagli mi?
  const [isBranch, setIsBranch] = useState<boolean>(
    initial?.branched_from_pole_id !== undefined && initial?.branched_from_pole_id !== null
  );
  const [parentLineId, setParentLineId] = useState<number | "">(() => {
    if (!initial?.branched_from_pole_id || !gridSnapshot) return "";
    const parentPole = gridSnapshot.poles.find((p) => p.id === initial.branched_from_pole_id);
    return parentPole ? parentPole.line_id : "";
  });
  const [parentPoleId, setParentPoleId] = useState<number | "">(initial?.branched_from_pole_id ?? "");

  // Branşman secebilecegimiz hatlar: kendisi DISINDA tum hatlar.
  const branchableLines = (gridSnapshot?.lines ?? []).filter(
    (l) => l.id !== currentLineId
  );
  const parentPoles = parentLineId
    ? (gridSnapshot?.poles ?? [])
        .filter((p) => p.line_id === parentLineId)
        .sort((a, b) => a.sequence_no - b.sequence_no)
    : [];

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    const code = initial?.code ?? autoCodeFromName(name, "HAT");
    await onSubmit({
      region_id: regionId, code, name: name.trim(),
      description: description.trim() || null,
      // color alani sistem genelinde standartlasti (saglikli=yesil, ariza=kirmizi);
      // kullanici secimine gerek yok. Backend'e null gonderiliyor.
      color: null,
      is_active: initial?.is_active ?? true,
      branched_from_pole_id: isBranch && typeof parentPoleId === "number" ? parentPoleId : null
    });
  };
  const { t } = useTranslation();
  return (
    <div className="settings-modal-backdrop">
      <form className="settings-modal" onSubmit={submit}>
        <h3>{initial ? t("engineering.grid.lineEditTitle") : t("engineering.grid.newLine")}</h3>
        <label>{t("engineering.grid.name")} <input value={name} onChange={(e) => setName(e.target.value)} required autoFocus /></label>
        <label>{t("engineering.grid.description")} <input value={description} onChange={(e) => setDescription(e.target.value)} /></label>

        <fieldset className="line-branch-fieldset">
          <legend>
            <label className="notify-option">
              <input
                type="checkbox"
                checked={isBranch}
                onChange={(e) => {
                  setIsBranch(e.target.checked);
                  if (!e.target.checked) {
                    setParentLineId("");
                    setParentPoleId("");
                  }
                }}
              />
              {t("engineering.grid.isBranch")}
            </label>
          </legend>
          {isBranch ? (
            <div className="line-branch-grid">
              <label>
                {t("engineering.grid.parentLine")}
                <select
                  value={parentLineId === "" ? "" : String(parentLineId)}
                  onChange={(e) => {
                    const v = e.target.value;
                    setParentLineId(v === "" ? "" : Number(v));
                    setParentPoleId("");
                  }}
                  required={isBranch}
                >
                  <option value="">{t("engineering.grid.selectPlaceholder")}</option>
                  {branchableLines.map((l) => (
                    <option key={l.id} value={l.id}>{l.name}</option>
                  ))}
                </select>
              </label>
              <label>
                {t("engineering.grid.parentPole")}
                <select
                  value={parentPoleId === "" ? "" : String(parentPoleId)}
                  onChange={(e) => {
                    const v = e.target.value;
                    setParentPoleId(v === "" ? "" : Number(v));
                  }}
                  required={isBranch}
                  disabled={parentLineId === "" || parentPoles.length === 0}
                >
                  <option value="">{t("engineering.grid.selectPlaceholder")}</option>
                  {parentPoles.map((p) => (
                    <option key={p.id} value={p.id}>
                      {t("dashboard.popup.poleDefault", { seq: p.sequence_no })}{p.name ? ` · ${p.name}` : ""}
                    </option>
                  ))}
                </select>
              </label>
            </div>
          ) : null}
          <p className="helper-text" style={{ marginTop: 6, fontSize: 11 }}>
            {t("engineering.grid.branchHint")}
          </p>
        </fieldset>
        <div className="settings-actions">
          <button type="button" onClick={onClose} disabled={busy}>{t("engineering.grid.cancel")}</button>
          <button type="submit" className="primary-btn" disabled={busy}>{busy ? "..." : t("engineering.grid.save")}</button>
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
  const [poleType, setPoleType] = useState<string>(pole.pole_type ?? "pole");
  const submit = async (e: FormEvent) => {
    e.preventDefault();
    const lat = Number(latitude);
    const lon = Number(longitude);
    if (!Number.isFinite(lat) || !Number.isFinite(lon)) return;
    await onSubmit({
      name: name.trim() || null,
      latitude: lat, longitude: lon,
      pole_type: poleType as Pole["pole_type"]
    });
  };
  const { t } = useTranslation();
  // Material icon adlari (SVG-tabanli, emoji yerine).
  const typeOptions: { value: NonNullable<Pole["pole_type"]>; labelKey: string; icon: string }[] = [
    { value: "pole", labelKey: "engineering.grid.typePole", icon: "location_on" },
    { value: "transformer", labelKey: "engineering.grid.typeTransformer", icon: "bolt" },
    { value: "breaker", labelKey: "engineering.grid.typeBreaker", icon: "electrical_services" }
  ];
  return (
    <div className="settings-modal-backdrop">
      <form className="settings-modal" onSubmit={submit}>
        <h3>{t("engineering.grid.poleEditTitle", { seq: pole.sequence_no })}</h3>
        <label>{t("engineering.grid.poleNameLabel")} <input value={name} onChange={(e) => setName(e.target.value)} placeholder={t("engineering.grid.poleNamePlaceholder")} /></label>
        <fieldset className="pole-type-fieldset">
          <legend>{t("engineering.grid.symbolType")}</legend>
          <div className="pole-type-options">
            {typeOptions.map((opt) => (
              <label
                key={opt.value}
                className={`pole-type-option ${poleType === opt.value ? "is-selected" : ""}`}
              >
                <input
                  type="radio"
                  name="pole_type"
                  value={opt.value}
                  checked={poleType === opt.value}
                  onChange={() => setPoleType(opt.value)}
                />
                <span className="pole-type-icon">
                  <span className="material-symbols-outlined">{opt.icon}</span>
                </span>
                <span>{t(opt.labelKey)}</span>
              </label>
            ))}
          </div>
        </fieldset>
        <label>{t("engineering.grid.latitude")}
          <input type="number" step="0.000001" value={latitude} onChange={(e) => setLatitude(e.target.value)} required />
        </label>
        <label>{t("engineering.grid.longitude")}
          <input type="number" step="0.000001" value={longitude} onChange={(e) => setLongitude(e.target.value)} required />
        </label>
        <p className="helper-text">{t("engineering.grid.polePosHint")}</p>
        <div className="settings-actions">
          <button type="button" onClick={onClose} disabled={busy}>{t("engineering.grid.cancel")}</button>
          <button type="submit" className="primary-btn" disabled={busy}>{busy ? "..." : t("engineering.grid.save")}</button>
        </div>
      </form>
    </div>
  );
}
