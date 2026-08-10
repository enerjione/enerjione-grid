import { useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { LayersControl, MapContainer, Marker, Polyline, Tooltip, useMap } from "react-leaflet";
import { MAP_LAYERS } from "../../shared/mapTiles";
import { ResilientTileLayer } from "../../components/ResilientTileLayer";
import L from "leaflet";
import { MapLayerSwitchFix } from "../../components/MapLayerSwitchFix";

import type { GridSnapshot } from "../../shared/api";
import { formatDistanceM, formatDistanceRange } from "../../shared/lineDistance";
import type {
  AlarmEvent,
  DeviceRow,
  FaultCause,
  FaultCauseCatalog,
  FaultComment,
  FaultEvent,
  UserRead
} from "../../shared/types";
import { useModalDialog } from "../../shared/useModalDialog";

type Props = {
  fault: FaultEvent;
  users: UserRead[];
  currentUsername: string;
  canAssign: boolean;
  gridSnapshot?: GridSnapshot | null;
  /** Cihaz listesi — modal haritasinda cihaz marker'lari icin. */
  devices?: DeviceRow[];
  /** Aktif alarmlar — cihazin RED/GREEN durumunu hesaplamak icin. */
  alarms?: AlarmEvent[];
  onClose: () => void;
  onAssign: (faultId: number, username: string | null) => Promise<void>;
  onUpdateStatus: (faultId: number, status: string) => Promise<void>;
  onUpdateNote: (faultId: number, note: string | null) => Promise<void>;
  /** Ariza sebebi (katalogdan). Analiz katmaninin ogrenecegi TEK insan
      etiketi — durum degisiminden BAGIMSIZ girilebilir. */
  onUpdateCause: (
    faultId: number,
    payload: { cause_code: string | null; cause_detail?: string | null }
  ) => Promise<void>;
  /** Sebep katalogu — UST bilesende BIR KEZ cekilir. Modal her acildiginda
      yeniden cekmek, degismeyen bir listeyi tekrar tekrar istemek olurdu. */
  causeCatalog: FaultCauseCatalog | null;
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

// Cihaz marker'i — RED ise kirmizi yildirim, GREEN ise yesil yildirim.
const miniDeviceIcon = (isRed: boolean) => {
  const color = isRed ? "#dc2626" : "#10b981";
  return L.divIcon({
    className: "fault-modal-dev-icon-wrap",
    html: `
      <div class="fault-modal-dev-icon" style="--c:${color}">
        <svg viewBox="0 0 24 24" width="11" height="11" aria-hidden="true">
          <path fill="#fff" d="M13 2 4 14h6l-1 8 9-12h-6z"/>
        </svg>
      </div>
    `,
    iconSize: [22, 22],
    iconAnchor: [11, 11]
  });
};

// İki nokta arası lerp
function lerp(
  a: { latitude: number; longitude: number },
  b: { latitude: number; longitude: number },
  t: number
): [number, number] {
  return [
    a.latitude + (b.latitude - a.latitude) * t,
    a.longitude + (b.longitude - a.longitude) * t
  ];
}

/**
 * Secilen odaga gore haritayi cerceveler.
 *
 * `useMap` yalnizca MapContainer'in ICINDE calisir, bu yuzden ayri bir
 * bilesen. Nokta listesi degistiginde (odak butonlari) yeniden cerceveler;
 * kullanicinin elle yaptigi kaydirma/zoom bir sonraki odak degisimine kadar
 * korunur.
 */
function FitFocus({ points }: { points: [number, number][] }) {
  const map = useMap();
  // Anahtar: nokta sayisi + ilk/son nokta. Tum diziyi bagimlilik yapmak her
  // render'da yeni referans uretip sonsuz fitBounds dongusu kurardi.
  const anahtar = points.length
    ? `${points.length}|${points[0].join()}|${points[points.length - 1].join()}`
    : "bos";
  useEffect(() => {
    if (points.length === 0) return;
    if (points.length === 1) {
      map.setView(points[0], 16, { animate: true });
      return;
    }
    map.fitBounds(L.latLngBounds(points), { padding: [28, 28], animate: true });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [map, anahtar]);
  return null;
}

export function FaultDetailModal({
  fault,
  users,
  currentUsername,
  canAssign,
  gridSnapshot,
  devices,
  alarms,
  onClose,
  onAssign,
  onUpdateStatus,
  onUpdateNote,
  onUpdateCause,
  causeCatalog,
  onLoadComments,
  onAddComment
}: Props) {
  const { t, i18n } = useTranslation();
  const localeTag = i18n.language?.startsWith("tr") ? "tr-TR" : "en-US";
  const [comments, setComments] = useState<FaultComment[]>([]);
  const [commentDraft, setCommentDraft] = useState("");
  const [noteDraft, setNoteDraft] = useState(fault.note ?? "");
  const [causeDraft, setCauseDraft] = useState(fault.cause_code ?? "");
  const [causeDetailDraft, setCauseDetailDraft] = useState(fault.cause_detail ?? "");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  /** Harita odagi: ariza bolgesi / tum hat / tum sebeke. */
  const [mapFocus, setMapFocus] = useState<"zone" | "line" | "grid">("zone");
  // Canlı süre sayacı için "now" state'i her saniye güncellenir.
  const [now, setNow] = useState<number>(() => Date.now());
  useEffect(() => {
    const id = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(id);
  }, []);
  // Arıza süresi: open->resolved/closed arasi (closed varsa stop, yoksa
  // simdiki zamana kadar canli sayim).
  const elapsedText = useMemo(() => {
    const start = new Date(fault.opened_at).getTime();
    const end =
      fault.status === "closed" && fault.closed_at
        ? new Date(fault.closed_at).getTime()
        : fault.status === "resolved" && fault.resolved_at
          ? new Date(fault.resolved_at).getTime()
          : now;
    let sec = Math.max(0, Math.round((end - start) / 1000));
    const days = Math.floor(sec / 86400);
    sec -= days * 86400;
    const hours = Math.floor(sec / 3600);
    sec -= hours * 3600;
    const mins = Math.floor(sec / 60);
    sec -= mins * 60;
    if (days > 0) return `${days}g ${hours}sa ${mins}dk`;
    if (hours > 0) return `${hours}sa ${mins}dk ${sec}sn`;
    if (mins > 0) return `${mins}dk ${sec}sn`;
    return `${sec}sn`;
  }, [now, fault.opened_at, fault.status, fault.closed_at, fault.resolved_at]);
  const isLive = fault.status !== "closed" && fault.status !== "resolved";

  useEffect(() => {
    setNoteDraft(fault.note ?? "");
    setCommentDraft("");
    setError("");
    void (async () => {
      try {
        const list = await onLoadComments(fault.id);
        setComments(list);
      } catch (err) {
        setError(err instanceof Error ? err.message : t("faults.detail.loadingComments"));
      }
    })();
  }, [fault.id, onLoadComments, t]);

  // ESC ile kapatma + odak tuzagi. Dinleyici window yerine modal
  // kapsayicisinda: ic ice diyalogda ESC ikisini birden kapatmasin.
  const dialogRef = useModalDialog<HTMLDivElement>(onClose);

  const userOptions = useMemo(
    () => [...users].sort((a, b) => a.full_name.localeCompare(b.full_name, localeTag)),
    [users, localeTag]
  );

  const canEdit = canAssign || fault.assigned_to_username === currentUsername;

  // Aktif (resetlenmemis) alarm device id'leri — cihaz RED/GREEN durumu icin
  const alarmActiveDeviceIds = useMemo(() => {
    const s = new Set<number>();
    for (const a of alarms ?? []) if (!a.reset) s.add(a.device_id);
    return s;
  }, [alarms]);

  // Mini harita — anasayfa mantigi:
  //  1) Hat icindeki tum cihazlarin slot uzerindeki konumlari
  //     (device_position_t veya otomatik dagılim) hesaplanir.
  //  2) Hat polyline'i 3 parcaya bolunur:
  //     - hat basi -> Son ariza algilayan cihaz konumu  : YESIL (saglikli)
  //     - Son ariza algilayan -> Ilk algilamayan cihaz  : KIRMIZI KESIK
  //     - Ilk algilamayan cihaz -> hat ucu              : YESIL (saglikli)
  //  3) Cihaz marker'lari konumlarinda gosterilir (RED/GREEN).
  const mapView = useMemo(() => {
    if (!gridSnapshot) return null;
    const polesById = new Map(gridSnapshot.poles.map((p) => [p.id, p]));
    const linePoles = gridSnapshot.poles
      .filter((p) => p.line_id === fault.line_id)
      .sort((a, b) => a.sequence_no - b.sequence_no);
    if (linePoles.length === 0) return null;

    // Bu hatta atanmis cihazli segmentleri slot bazinda topla
    type SegRec = {
      deviceId: number;
      fromPoleId: number;
      toPoleId: number;
      fromSeq: number;
      toSeq: number;
      t: number; // slot icindeki konum 0..1
      isRed: boolean;
    };
    const slotSegs = new Map<string, SegRec[]>();
    for (const seg of gridSnapshot.segments) {
      if (seg.line_id !== fault.line_id || !seg.device_id) continue;
      const fp = polesById.get(seg.from_pole_id);
      const tp = polesById.get(seg.to_pole_id);
      if (!fp || !tp) continue;
      const t =
        seg.device_position_t !== null && seg.device_position_t !== undefined
          ? seg.device_position_t
          : 0.5;
      const key = `${seg.from_pole_id}|${seg.to_pole_id}`;
      const arr = slotSegs.get(key) ?? [];
      arr.push({
        deviceId: seg.device_id,
        fromPoleId: seg.from_pole_id,
        toPoleId: seg.to_pole_id,
        fromSeq: fp.sequence_no,
        toSeq: tp.sequence_no,
        t,
        isRed: alarmActiveDeviceIds.has(seg.device_id)
      });
      slotSegs.set(key, arr);
    }
    // Slot icindeki cihazlari t'ye gore sirala; otomatik dagılim (t==0.5
    // varsayilan) durumunda created_at sirasi ile esit araliklara dagıt
    for (const [, arr] of slotSegs) {
      arr.sort((a, b) => a.t - b.t);
      // hepsinin t'si ayni 0.5 ise (manuel ayarlanmamis) -> esit dagit
      if (arr.length > 1 && arr.every((r) => r.t === 0.5)) {
        const n = arr.length;
        arr.forEach((r, i) => {
          r.t = (i + 1) / (n + 1);
        });
      }
    }

    // Hat icindeki tum cihazlari sirayla diz: slot fromSeq, sonra slot ici t
    type DevPoint = SegRec & { lat: number; lon: number; idx: number };
    const lineDevices: DevPoint[] = [];
    for (let i = 0; i < linePoles.length - 1; i += 1) {
      const a = linePoles[i];
      const b = linePoles[i + 1];
      const key = `${a.id}|${b.id}`;
      const slot = slotSegs.get(key) ?? [];
      for (const r of slot) {
        const [lat, lon] = lerp(a, b, r.t);
        lineDevices.push({ ...r, lat, lon, idx: lineDevices.length });
      }
    }

    // Son alarmli (RED) cihaz indexi
    let lastRedIdx = -1;
    let firstGreenAfterRedIdx = -1;
    for (let i = 0; i < lineDevices.length; i += 1) {
      if (lineDevices[i].isRed) lastRedIdx = i;
    }
    if (lastRedIdx >= 0) {
      // Son RED'ten sonraki ilk GREEN
      for (let i = lastRedIdx + 1; i < lineDevices.length; i += 1) {
        if (!lineDevices[i].isRed) {
          firstGreenAfterRedIdx = i;
          break;
        }
      }
    }

    // Hat polyline'i: tum direklerin sirali koordinatlari
    const fullLine: [number, number][] = linePoles.map((p) => [p.latitude, p.longitude]);

    // Cihaz konumlarina gore 3-parca cizim:
    //   - "splitA" = son alarmli cihazin konumu (yoksa null)
    //   - "splitB" = ilk alarmsiz cihaz konumu (yoksa hat ucu)
    let splitA: [number, number] | null = null;
    let splitB: [number, number] | null = null;
    if (lastRedIdx >= 0) {
      splitA = [lineDevices[lastRedIdx].lat, lineDevices[lastRedIdx].lon];
      if (firstGreenAfterRedIdx >= 0) {
        splitB = [lineDevices[firstGreenAfterRedIdx].lat, lineDevices[firstGreenAfterRedIdx].lon];
      } else {
        // Son RED'ten sonra GREEN cihaz yok -> hat ucu (son direk)
        const last = linePoles[linePoles.length - 1];
        splitB = [last.latitude, last.longitude];
      }
    }

    // Polyline'i splitA ve splitB noktalarinda kes — gercek hat geometrisi
    // korunarak. Yaklasim: her splitPoint'i en yakin polyline edge'inde
    // dik projeksiyonla bulup polyline'i 2 parcaya ayir.
    const splitPolyline = (
      polyline: [number, number][],
      splitAt: [number, number]
    ): { pre: [number, number][]; post: [number, number][] } => {
      if (polyline.length < 2) return { pre: polyline, post: [] };
      let bestK = 0;
      let bestD = Infinity;
      let bestProj: [number, number] = polyline[0];
      for (let k = 0; k < polyline.length - 1; k += 1) {
        const a = polyline[k];
        const b = polyline[k + 1];
        const dx = b[0] - a[0];
        const dy = b[1] - a[1];
        const len2 = dx * dx + dy * dy;
        if (len2 === 0) continue;
        const t = Math.max(
          0,
          Math.min(1, ((splitAt[0] - a[0]) * dx + (splitAt[1] - a[1]) * dy) / len2)
        );
        const projX = a[0] + t * dx;
        const projY = a[1] + t * dy;
        const ddx = projX - splitAt[0];
        const ddy = projY - splitAt[1];
        const d = ddx * ddx + ddy * ddy;
        if (d < bestD) {
          bestD = d;
          bestK = k;
          bestProj = [projX, projY];
        }
      }
      return {
        pre: [...polyline.slice(0, bestK + 1), bestProj],
        post: [bestProj, ...polyline.slice(bestK + 1)]
      };
    };

    let preGreen: [number, number][] = fullLine; // varsayilan: tamami yesil
    let faultRed: [number, number][] = [];
    let postGreen: [number, number][] = [];
    if (splitA && splitB) {
      const splitAtA = splitPolyline(fullLine, splitA);
      preGreen = splitAtA.pre;
      const remainder = splitAtA.post;
      const splitAtB = splitPolyline(remainder, splitB);
      faultRed = splitAtB.pre;
      postGreen = splitAtB.post;
    } else if (splitA) {
      // Sadece son alarmli; hat ucuna kadar fault
      const splitAtA = splitPolyline(fullLine, splitA);
      preGreen = splitAtA.pre;
      faultRed = splitAtA.post;
    }

    // Bounds: arıza aralığındaki direkleri ve cihazlari kapsa
    const fromSeq = fault.from_pole_seq ?? null;
    const toSeq = fault.to_pole_seq ?? null;
    const lo = fromSeq != null && toSeq != null ? Math.min(fromSeq, toSeq) : null;
    const hi = fromSeq != null && toSeq != null ? Math.max(fromSeq, toSeq) : null;
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

    // Cihaz marker'lari listesi
    const deviceMarkers = lineDevices.map((d) => {
      const dev = (devices ?? []).find((dx) => dx.id === d.deviceId);
      return {
        deviceId: d.deviceId,
        lat: d.lat,
        lon: d.lon,
        isRed: d.isRed,
        name: dev?.name ?? `${t("common.device")} #${d.deviceId}`,
        code: dev?.code
      };
    });

    // Direk koordinat listesi (aralik icindekiler)
    const poleFallback = i18n.language?.startsWith("tr") ? "Direk" : "Pole";
    const rangePoles = focusPoles.map((p) => ({
      id: p.id,
      sequence_no: p.sequence_no,
      name: p.name ?? `${poleFallback} #${p.sequence_no}`,
      latitude: p.latitude,
      longitude: p.longitude,
      isStart: p.id === fault.from_pole_id,
      isEnd: p.id === fault.to_pole_id
    }));

    // ODAK SINIRLARI — haritanin ne kadarini gosterecegi.
    //
    // Tek bir gorunum yetmiyordu: ariza araligina zoom yapinca operator
    // "bu hattin neresi?" diye soruyor, tum hatta bakinca ariza noktasi
    // igne ucu kadar kaliyordu. Ucu de ayni haritada, secilebilir.
    const zoneBounds: [number, number][] =
      faultRed.length >= 2 ? faultRed : fullLine;
    const lineBounds: [number, number][] = fullLine;
    // Tum sebeke: ayni BOLGEDEKI diger hatlar da — komsu hat bilgisi
    // "ariza hangi besleme kolunda" sorusunu cevapliyor.
    const otherLines: { lineId: number; name: string; path: [number, number][] }[] = [];
    for (const ln of gridSnapshot.lines ?? []) {
      if (ln.id === fault.line_id) continue;
      const pts = gridSnapshot.poles
        .filter((p) => p.line_id === ln.id)
        .sort((a, b) => a.sequence_no - b.sequence_no)
        .map((p) => [p.latitude, p.longitude] as [number, number]);
      if (pts.length >= 2) otherLines.push({ lineId: ln.id, name: ln.name, path: pts });
    }
    const gridBounds: [number, number][] = [
      ...fullLine,
      ...otherLines.flatMap((l) => l.path)
    ];

    return {
      preGreen,
      faultRed,
      postGreen,
      center,
      zoom,
      zoneBounds,
      lineBounds,
      gridBounds,
      otherLines,
      polesWithRole: linePoles.map((p) => ({
        p,
        isFromFault: p.id === fault.from_pole_id,
        isToFault: p.id === fault.to_pole_id,
        isInFaultRange:
          lo !== null && hi !== null && p.sequence_no >= lo && p.sequence_no <= hi
      })),
      rangePoles,
      deviceMarkers
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [
    gridSnapshot,
    devices,
    alarmActiveDeviceIds,
    fault.line_id,
    fault.from_pole_id,
    fault.to_pole_id,
    fault.from_pole_seq,
    fault.to_pole_seq,
    i18n.language
  ]);

  const handleAssign = async (newUsername: string) => {
    setSaving(true);
    setError("");
    try {
      await onAssign(fault.id, newUsername || null);
    } catch (err) {
      setError(err instanceof Error ? err.message : t("alarms.errors.assignFailed"));
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
      setError(err instanceof Error ? err.message : t("common.errorOccurred"));
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
      setError(err instanceof Error ? err.message : t("common.errorOccurred"));
    } finally {
      setSaving(false);
    }
  };
  /** Katalog etiketi — arayuz dilini izler. */
  const causeLabel = (c: FaultCause) => (i18n.language?.startsWith("tr") ? c.label_tr : c.label_en);

  /** Aileye gore gruplanmis secim listesi. Duz bir 19'luk liste taranmasi zor;
      "dis etken / ekipman / hava / isletme" ayrimi secimi hizlandirir. */
  const causeGroups = useMemo<[string, FaultCause[]][]>(() => {
    if (!causeCatalog) return [];
    const harita = new Map<string, FaultCause[]>();
    for (const grup of causeCatalog.groups) harita.set(grup, []);
    for (const c of causeCatalog.causes) {
      const liste = harita.get(c.group);
      if (liste) liste.push(c);
      else harita.set(c.group, [c]);
    }
    return [...harita.entries()].filter(([, liste]) => liste.length > 0);
  }, [causeCatalog]);

  /** Kuralin onerdigi sebep. SECILI GELMEZ — operator onaylamadan bir etiket
      "girilmis" sayilirsa istatistik, kimsenin bakmadigi bir tahminle dolar.
      Zaten girilmis bir sebep varsa oneri de gosterilmez (is bitmis). */
  const suggestedCause = useMemo(() => {
    if (!causeCatalog || fault.cause_code) return null;
    const kod = fault.auto_cause_code;
    if (!kod) return null;
    const c = causeCatalog.causes.find((x) => x.code === kod);
    return c ? { code: c.code, label: causeLabel(c) } : null;
  }, [causeCatalog, fault.auto_cause_code, fault.cause_code, i18n.language]);

  const handleSaveCause = async () => {
    setSaving(true);
    setError("");
    try {
      await onUpdateCause(fault.id, {
        // Bos secim = sebebi GERI AL (yanlis secildiyse duzeltilebilmeli).
        cause_code: causeDraft || null,
        cause_detail: causeDetailDraft.trim() || null
      });
    } catch (err) {
      setError(err instanceof Error ? err.message : t("common.errorOccurred"));
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
      setError(err instanceof Error ? err.message : t("alarms.errors.commentFailed"));
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="fault-modal-backdrop" onClick={onClose}>
      <div
        className="fault-modal"
        onClick={(e) => e.stopPropagation()}
        role="dialog"
        aria-modal="true"
        ref={dialogRef}
      >
        <button
          type="button"
          className="fault-modal-close"
          onClick={onClose}
          aria-label={t("common.close")}
        >
          <span className="material-symbols-outlined">close</span>
        </button>

        <header className="fault-modal-head">
          <div className="fault-modal-head-left">
            {/* Atandı/Açık pill'i kaldırıldı — orta kolondaki "Sorumluluk"
                kartında zaten durum gosteriliyor; baslikta tekrari gerek
                degil (kullanici talebi). */}
            <h2>{fault.line_name}</h2>
            <div className="fault-modal-breadcrumbs">
              <span className="material-symbols-outlined">place</span>
              <span>{fault.region_name}</span>
              <span className="fault-modal-bc-sep">·</span>
              <span>{fault.line_name}</span>
              <span className="fault-modal-bc-sep">·</span>
              <strong>
                {t("faults.card.rangeText", { from: fault.from_pole_seq, to: fault.to_pole_seq })}
              </strong>
            </div>
          </div>
          <div className="fault-modal-head-right">
            <div className="fault-modal-time-pill fault-modal-time-pill--opened">
              <span className="fault-modal-time-pill-icon">
                <span className="material-symbols-outlined">event</span>
              </span>
              <div>
                <span className="fault-modal-time-pill-label">{t("faults.card.openedAt")}</span>
                <strong>{fmtDate(fault.opened_at, localeTag)}</strong>
              </div>
            </div>
            <div
              className={`fault-modal-time-pill fault-modal-time-pill--elapsed ${
                isLive ? "is-live" : "is-final"
              }`}
              title={isLive ? t("faults.card.durationLive") : t("faults.card.durationFinal")}
            >
              <span className="fault-modal-time-pill-icon">
                {isLive ? (
                  <span className="fault-modal-elapsed-pulse" aria-hidden="true" />
                ) : (
                  <span className="material-symbols-outlined">hourglass_top</span>
                )}
              </span>
              <div>
                <span className="fault-modal-time-pill-label">
                  {t("faults.detail.duration")}
                </span>
                <strong>{elapsedText}</strong>
              </div>
            </div>
          </div>
        </header>

        <div className="fault-modal-body">
          {/* Sol kolon: harita + cihaz bilgisi */}
          <div className="fault-modal-left">
            <div className="fault-modal-section">
              <div className="fault-modal-section-head">
                <h4>{t("faults.detail.mapTitle")}</h4>
                {/* ODAK SECICI: ariza bolgesine zoom yapinca "bu hattin
                    neresi?" belirsiz kaliyor, tum hatta bakinca ariza
                    noktasi kayboluyordu. Ucu de ayni haritada. */}
                {mapView ? (
                  <div className="fault-modal-focus" role="group">
                    {(["zone", "line", "grid"] as const).map((k) => (
                      <button
                        key={k}
                        type="button"
                        className={mapFocus === k ? "is-active" : undefined}
                        onClick={() => setMapFocus(k)}
                      >
                        {t(`faults.detail.focus.${k}`)}
                      </button>
                    ))}
                  </div>
                ) : null}
              </div>
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
                    <LayersControl position="topright">
                      <LayersControl.BaseLayer checked name="Sokak">
                        {/* maxZoom: verilmezse Leaflet 18'e duser ve sokak
                            gorunumu uydudan (19) bir kademe geride kalir. */}
                        <ResilientTileLayer
                          layer="osm"
                          attribution={MAP_LAYERS[0].attribution}
                          maxZoom={MAP_LAYERS[0].maxZoom}
                        />
                      </LayersControl.BaseLayer>
                      <LayersControl.BaseLayer name="Uydu">
                        <ResilientTileLayer
                          layer="satellite"
                          attribution={MAP_LAYERS[1].attribution}
                          maxZoom={MAP_LAYERS[1].maxZoom}
                        />
                      </LayersControl.BaseLayer>
                    </LayersControl>
                    <MapLayerSwitchFix />
                    <FitFocus
                      points={
                        mapFocus === "zone"
                          ? mapView.zoneBounds
                          : mapFocus === "line"
                            ? mapView.lineBounds
                            : mapView.gridBounds
                      }
                    />
                    {/* Tum sebeke gorunumunde komsu hatlar SOLUK cizilir —
                        ariza hatti one cikmaya devam etsin. */}
                    {mapFocus === "grid"
                      ? mapView.otherLines.map((l) => (
                          <Polyline
                            key={`ol-${l.lineId}`}
                            positions={l.path}
                            pathOptions={{ color: "#94a3b8", weight: 2.5, opacity: 0.45 }}
                          >
                            <Tooltip>{l.name}</Tooltip>
                          </Polyline>
                        ))
                      : null}
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
                        key={`p-${p.id}`}
                        position={[p.latitude, p.longitude]}
                        icon={miniPolePin(
                          String(p.sequence_no),
                          isFromFault,
                          isToFault || (isInFaultRange && !isFromFault)
                        )}
                      >
                        <Tooltip>
                          {p.name ?? `${t("faults.detail.tooltipPole")} #${p.sequence_no}`}
                          {isFromFault ? ` (${t("faults.detail.tooltipFaultStart")})` : ""}
                          {isToFault ? ` (${t("faults.detail.tooltipFaultEnd")})` : ""}
                        </Tooltip>
                      </Marker>
                    ))}
                    {/* Cihaz marker'lari (RED/GREEN) */}
                    {mapView.deviceMarkers.map((d) => (
                      <Marker
                        key={`d-${d.deviceId}`}
                        position={[d.lat, d.lon]}
                        icon={miniDeviceIcon(d.isRed)}
                      >
                        <Tooltip>
                          <strong>{d.name}</strong>
                          {d.code ? <><br /><span style={{ opacity: 0.7 }}>{d.code}</span></> : null}
                          <br />
                          <em style={{ color: d.isRed ? "#dc2626" : "#10b981" }}>
                            {d.isRed ? t("faults.detail.deviceDetectedFault") : t("faults.detail.deviceNoFault")}
                          </em>
                        </Tooltip>
                      </Marker>
                    ))}
                  </MapContainer>
                  <div className="fault-modal-map-legend">
                    <span><i style={{ background: "#ef4444" }} /> {t("faults.detail.mapLegendFault")}</span>
                    <span><i style={{ background: "#16a34a" }} /> {t("faults.detail.mapLegendOk")}</span>
                    <span><i className="fault-modal-legend-dot" style={{ background: "#dc2626" }} /> {t("faults.detail.mapLegendDeviceRed")}</span>
                    <span><i className="fault-modal-legend-dot" style={{ background: "#10b981" }} /> {t("faults.detail.mapLegendDeviceGreen")}</span>
                  </div>
                </div>
              ) : (
                <div className="fault-modal-map-empty">
                  {t("faults.detail.mapEmpty")}
                </div>
              )}
            </div>

            {/* Tel mesafesi — hat basindan ve son arizali cihazdan itibaren.
                Degerler backend'de direk + cihaz koordinatlarindan hat boyunca
                hesaplanip fault kaydina yazilir (line_distance_service). */}
            {formatDistanceRange(fault.zone_start_m, fault.zone_end_m) ? (
              <div className="fault-modal-section">
                <h4>{t("faults.detail.distanceTitle")}</h4>
                <div className="fault-modal-distance">
                  <div className="fault-modal-distance-row">
                    <span className="fault-modal-distance-label">
                      {t("faults.detail.distanceFromStart")}
                    </span>
                    <strong className="fault-modal-distance-value">
                      {formatDistanceRange(fault.zone_start_m, fault.zone_end_m)}
                    </strong>
                  </div>
                  {fault.zone_length_m != null ? (
                    <>
                      <div className="fault-modal-distance-row">
                        <span className="fault-modal-distance-label">
                          {t("faults.detail.distanceFromDevice", {
                            device:
                              fault.last_red_device_name ??
                              fault.last_red_device_code ??
                              "—"
                          })}
                        </span>
                        <strong className="fault-modal-distance-value">
                          {t("faults.detail.distanceAheadRange", {
                            span: formatDistanceM(fault.zone_length_m)
                          })}
                        </strong>
                      </div>
                    </>
                  ) : null}
                </div>
                {/* Olcum yonteminin uzun aciklamasi KALDIRILDI (bir paragraf
                    kot farki/sarkma notu). Ekipe operasyonel bilgi vermiyor,
                    her acilista okunmasi gereken bir metin gibi duruyordu;
                    ayni bilgi baslik altindaki tek satirlik ipucunda. */}
              </div>
            ) : null}

            {/* Arıza aralığındaki direklerin koordinat listesi */}
            {mapView && mapView.rangePoles.length > 0 ? (
              <div className="fault-modal-section">
                <h4>{t("faults.detail.rangePolesTitle")}</h4>
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
                            {t("faults.detail.poleRangeStart")}
                          </span>
                        ) : rp.isEnd ? (
                          <span className="fault-modal-pole-tag fault-modal-pole-tag--green">
                            {t("faults.detail.poleRangeEnd")}
                          </span>
                        ) : null}
                      </div>
                      {/* Ondalik koordinat KALDIRILDI: ekip sahada
                          "37.812390, 41.567505" ile yon bulmuyor, haritayi
                          ya da direk adini kullaniyor. Konum zaten ustteki
                          haritada isaretli; burada iki sutun sayi listesi
                          karti gereksiz teknik gosteriyordu. */}
                    </li>
                  ))}
                </ul>
              </div>
            ) : null}

            <div className="fault-modal-section">
              <h4>{t("faults.detail.devicesTitle")}</h4>
              <div className="fault-modal-devices">
                <div className="fault-modal-device fault-modal-device--red">
                  <span className="fault-modal-device-dot" />
                  <div>
                    <span className="fault-modal-device-role">
                      {t("faults.detail.deviceLastRedRole")}
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
                      {t("faults.detail.deviceFirstGreenRole")}
                    </span>
                    <strong>
                      {fault.first_green_device_name ?? t("faults.detail.deviceFirstGreenLineEnd")}
                    </strong>
                    {fault.first_green_device_code ? (
                      <small>{fault.first_green_device_code}</small>
                    ) : null}
                  </div>
                </div>
              </div>
              <p className="fault-modal-devices-hint">
                {t("faults.detail.devicesHint")}
              </p>
            </div>
          </div>

          {/* Orta kolon: ticket yönetimi — modern kart tasarımı */}
          <div className="fault-modal-mid">
            {/* Sorumluluk kart */}
            <div className="fault-modal-card">
              <div className="fault-modal-card-head">
                <span className="fault-modal-card-icon" style={{ background: "rgba(99,102,241,0.12)", color: "#6366f1" }}>
                  <span className="material-symbols-outlined">assignment_ind</span>
                </span>
                <div>
                  <h4>{t("faults.detail.tickets")}</h4>
                  <p>{t("faults.detail.ticketsHint")}</p>
                </div>
              </div>
              <div className="fault-modal-field">
                <label className="fault-modal-label">{t("faults.detail.assignee")}</label>
                {canAssign ? (
                  <select
                    value={fault.assigned_to_username ?? ""}
                    onChange={(e) => void handleAssign(e.target.value)}
                    disabled={saving}
                  >
                    <option value="">{t("faults.detail.assigneeUnset")}</option>
                    {userOptions.map((u) => (
                      <option key={u.id} value={u.username}>
                        {u.full_name} ({u.username})
                      </option>
                    ))}
                  </select>
                ) : (
                  <div className="fault-modal-assignee-display">
                    {fault.assigned_to_username ? (
                      <>
                        <span className="fault-modal-assignee-avatar">
                          {(fault.assigned_to_username || "?").substring(0, 2).toUpperCase()}
                        </span>
                        <div>
                          <strong>{fault.assigned_to_full_name ?? fault.assigned_to_username}</strong>
                          {fault.assigned_to_full_name ? (
                            <small>{fault.assigned_to_username}</small>
                          ) : null}
                        </div>
                      </>
                    ) : (
                      <span className="fault-modal-assignee-empty">{t("faults.detail.assigneeEmpty")}</span>
                    )}
                  </div>
                )}
              </div>
              <div className="fault-modal-field">
                <label className="fault-modal-label">{t("faults.detail.statusLabel")}</label>
                <div className="fault-modal-status-grid">
                  {(["assigned", "in_progress", "resolved", "closed"] as const).map((s) => {
                    const active = fault.status === s;
                    const color = STATUS_COLOR[s];
                    return (
                      <button
                        key={s}
                        type="button"
                        className={`fault-modal-status-card ${active ? "active" : ""}`}
                        onClick={() => void handleStatus(s)}
                        disabled={saving || !canEdit}
                        style={
                          active
                            ? {
                                background: color,
                                color: "#fff",
                                borderColor: color,
                                boxShadow: `0 4px 12px ${color}40`
                              }
                            : { borderColor: `${color}40` }
                        }
                      >
                        <span
                          className="fault-modal-status-card-dot"
                          style={{ background: color }}
                        />
                        <span>{t(`faults.status.${s}`)}</span>
                      </button>
                    );
                  })}
                </div>
              </div>
            </div>

            {/* Arıza sebebi — analiz katmanının öğreneceği TEK insan etiketi.
                Kural önerisi (auto_cause_code) varsa gösterilir ama SEÇİLİ
                GELMEZ: operatör onaylamadan bir etiket "girilmiş" sayılırsa
                istatistik, kimsenin bakmadığı bir tahminle dolar. */}
            <div className="fault-modal-card fault-cause-card">
              <div className="fault-modal-card-head">
                <span
                  className="fault-modal-card-icon"
                  style={{ background: "rgba(139,92,246,0.12)", color: "#7c3aed" }}
                >
                  <span className="material-symbols-outlined">troubleshoot</span>
                </span>
                <div>
                  <h4>{t("faults.detail.causeTitle")}</h4>
                  <p>{t("faults.detail.causeHint")}</p>
                </div>
              </div>

              {suggestedCause ? (
                <div className="fault-cause-suggestion">
                  <span className="material-symbols-outlined">lightbulb</span>
                  <span>
                    {t("faults.detail.causeSuggested", { cause: suggestedCause.label })}
                  </span>
                  {canEdit && causeDraft !== suggestedCause.code ? (
                    <button
                      type="button"
                      className="fault-cause-apply"
                      onClick={() => setCauseDraft(suggestedCause.code)}
                    >
                      {t("faults.detail.causeUseSuggestion")}
                    </button>
                  ) : null}
                </div>
              ) : null}

              <select
                className="fault-cause-select"
                value={causeDraft}
                disabled={saving || !canEdit || causeCatalog === null}
                onChange={(e) => setCauseDraft(e.target.value)}
              >
                <option value="">{t("faults.detail.causeNotSet")}</option>
                {causeGroups.map(([grup, liste]) => (
                  <optgroup key={grup} label={t(`faults.causeGroup.${grup}`, { defaultValue: grup })}>
                    {liste.map((c) => (
                      <option key={c.code} value={c.code}>
                        {causeLabel(c)}
                      </option>
                    ))}
                  </optgroup>
                ))}
              </select>

              <textarea
                className="fault-cause-detail"
                value={causeDetailDraft}
                onChange={(e) => setCauseDetailDraft(e.target.value)}
                disabled={saving || !canEdit}
                placeholder={t("faults.detail.causeDetailPlaceholder")}
              />

              {canEdit ? (
                <button
                  type="button"
                  className="fault-modal-save-btn"
                  onClick={() => void handleSaveCause()}
                  disabled={saving}
                >
                  <span className="material-symbols-outlined">save</span>
                  {t("faults.detail.saveCause")}
                </button>
              ) : null}
            </div>

            {/* Kısa Not kart — modal'ın en altına kadar uzanır (flex-grow) */}
            <div className="fault-modal-card fault-modal-card--note">
              <div className="fault-modal-card-head">
                <span className="fault-modal-card-icon" style={{ background: "rgba(245,158,11,0.12)", color: "#d97706" }}>
                  <span className="material-symbols-outlined">edit_note</span>
                </span>
                <div>
                  <h4>{t("faults.detail.writeNote")}</h4>
                  <p>{t("faults.detail.writeNoteHint")}</p>
                </div>
              </div>
              <textarea
                value={noteDraft}
                onChange={(e) => setNoteDraft(e.target.value)}
                disabled={saving || !canEdit}
                placeholder={t("faults.detail.writeNotePlaceholder")}
              />
              {canEdit ? (
                <button
                  type="button"
                  className="fault-modal-save-btn"
                  onClick={() => void handleSaveNote()}
                  disabled={saving}
                >
                  <span className="material-symbols-outlined">save</span>
                  {t("faults.detail.saveNote")}
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
                {t("faults.detail.commentsTitle")}
                {comments.length > 0 ? <span className="fault-modal-count">{comments.length}</span> : null}
              </h4>
              <ul className="fault-modal-comments">
                {comments.length === 0 ? (
                  <li className="fault-modal-comments-empty">
                    {t("faults.detail.commentsHint")}
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
                        <span className="fault-modal-comment-time">{fmtDate(c.created_at, localeTag)}</span>
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
                    placeholder={t("faults.detail.commentsAddPlaceholder")}
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
                    {t("faults.detail.addCommentBtn")}
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
