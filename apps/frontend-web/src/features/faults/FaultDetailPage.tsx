/**
 * Ariza detayi — TAM SAYFA (kendi sekmesinde).
 *
 * NEDEN MODAL DEGIL
 * -----------------
 * Eskiden modaldi ve modalin uc bedeli vardi:
 *
 *   1. Sekme sisteminde yeri yoktu. Tarayici yenilenince kayboluyordu;
 *      operator sahayla telefondayken sayfayi tazelemek acik arizayi
 *      kapatmak demekti.
 *   2. Iki arizayi karsilastirmak imkansizdi — modal tek ve modaldir.
 *      Oysa "ayni hatta iki ariza var mi" siradan bir soru.
 *   3. Icerik modal cercevesine sigmadigi icin uc dar kolona sikismisti;
 *      harita 300 piksel kaliyordu, oysa bu ekranin en degerli parcasi o.
 *
 * Artik `{ kind: "fault-detail", faultId }` rotasiyla acilan bir sekme.
 *
 * KENDI BASINA AYAKTA DURUR
 * -------------------------
 * Sekmeler localStorage'a yazildigi icin sayfa, ariza listesi HIC
 * yuklenmemisken de acilabilir. Kayit listede yoksa (kapanmis ariza,
 * gecmisten acilmisti) kendi cekiyor.
 */
import { useCallback, useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import {
  ArrowLeft,
  ArrowRight,
  CalendarClock,
  CircleDot,
  Lightbulb,
  MapPin,
  MessagesSquare,
  Route,
  Save,
  Send,
  Timer,
  TriangleAlert,
  UserRound,
  Zap
} from "lucide-react";
import { LayersControl, MapContainer, Marker, Polyline, Tooltip } from "react-leaflet";
import L from "leaflet";

import { buildFaultMapView } from "./faultMapView";
import { FitFocus } from "./FaultMapFocus";

import { MapLayerSwitchFix } from "../../components/MapLayerSwitchFix";
import { ResilientTileLayer } from "../../components/ResilientTileLayer";
import { fetchFault, fetchFaultCauses, type GridSnapshot } from "../../shared/api";
import { formatDistanceM, formatDistanceRange } from "../../shared/lineDistance";
import { MAP_LAYERS } from "../../shared/mapTiles";
import type {
  AlarmEvent,
  DeviceRow,
  FaultCause,
  FaultCauseCatalog,
  FaultComment,
  FaultEvent,
  UserRead
} from "../../shared/types";

type Props = {
  faultId: number;
  /** Listeden gelen kayit. Yoksa sayfa kendi ceker. */
  faults: FaultEvent[];
  users: UserRead[];
  currentUsername: string;
  canAssign: boolean;
  accessToken: string;
  gridSnapshot?: GridSnapshot | null;
  devices?: DeviceRow[];
  alarms?: AlarmEvent[];
  onBack: () => void;
  onAssign: (faultId: number, username: string | null) => Promise<void>;
  onUpdateStatus: (faultId: number, status: string) => Promise<void>;
  onUpdateNote: (faultId: number, note: string | null) => Promise<void>;
  onUpdateCause: (
    faultId: number,
    payload: { cause_code: string | null; cause_detail?: string | null }
  ) => Promise<void>;
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

const AKIS: readonly ["assigned", "in_progress", "resolved", "closed"] = [
  "assigned",
  "in_progress",
  "resolved",
  "closed"
];

function fmtDate(iso: string | null | undefined, localeTag: string): string {
  if (!iso) return "—";
  return new Date(iso).toLocaleString(localeTag);
}

/** Mini harita direk pini. */
const polePin = (label: string, isRed: boolean, isGreen: boolean) => {
  const color = isRed ? "#ef4444" : isGreen ? "#10b981" : "#475569";
  return L.divIcon({
    className: "fd-pole-icon-wrap",
    html: `<div class="fd-pole-icon" style="background:${color}">${label}</div>`,
    iconSize: [22, 22],
    iconAnchor: [11, 11]
  });
};

/** Cihaz isaretcisi — ariza algiladiysa kirmizi. */
const deviceIcon = (isRed: boolean) => {
  const color = isRed ? "#dc2626" : "#10b981";
  return L.divIcon({
    className: "fd-dev-icon-wrap",
    html: `
      <div class="fd-dev-icon" style="--c:${color}">
        <svg viewBox="0 0 24 24" width="11" height="11" aria-hidden="true">
          <path fill="#fff" d="M13 2 4 14h6l-1 8 9-12h-6z"/>
        </svg>
      </div>
    `,
    iconSize: [22, 22],
    iconAnchor: [11, 11]
  });
};

export function FaultDetailPage({
  faultId,
  faults,
  users,
  currentUsername,
  canAssign,
  accessToken,
  gridSnapshot,
  devices,
  alarms,
  onBack,
  onAssign,
  onUpdateStatus,
  onUpdateNote,
  onUpdateCause,
  onLoadComments,
  onAddComment
}: Props) {
  const { t, i18n } = useTranslation();
  const localeTag = i18n.language?.startsWith("tr") ? "tr-TR" : "en-US";

  // Listedeki kayit ONCELIKLI: App onu duzenli tazeliyor ve mutasyonlardan
  // sonra guncelliyor. `cekilen` yalnizca listede OLMAYAN ariza icin yedek
  // (kapanmis ariza, sekme yenilemeden sonra geri geldi).
  const listeKaydi = useMemo(
    () => faults.find((f) => f.id === faultId) ?? null,
    [faults, faultId]
  );
  const [cekilen, setCekilen] = useState<FaultEvent | null>(null);
  const [yukleniyor, setYukleniyor] = useState(false);
  const [yuklemeHatasi, setYuklemeHatasi] = useState("");
  const fault = listeKaydi ?? cekilen;

  const cek = useCallback(async () => {
    try {
      setCekilen(await fetchFault(accessToken, faultId));
      setYuklemeHatasi("");
    } catch (err) {
      const msg = err instanceof Error ? err.message : t("common.errorOccurred");
      if (msg !== "session_polling_401") setYuklemeHatasi(msg);
    }
  }, [accessToken, faultId, t]);

  useEffect(() => {
    if (listeKaydi) return; // listede var, cekmeye gerek yok
    setYukleniyor(true);
    void cek().finally(() => setYukleniyor(false));
  }, [listeKaydi, cek]);

  /** Sebep katalogu — sayfa KENDI ceker. Prop olsaydi sekme yenilemeden
   *  sonra (liste sayfasi acik degilken) sebep secimi olu gelirdi.
   *  Backend tek kaynak (`app/data/fault_causes.py`); frontend'e gomulseydi
   *  ikisi ayrisir ve secilen kod backend'de taninmaz olurdu. */
  const [causeCatalog, setCauseCatalog] = useState<FaultCauseCatalog | null>(null);
  useEffect(() => {
    let iptal = false;
    fetchFaultCauses(accessToken)
      .then((k) => {
        if (!iptal) setCauseCatalog(k);
      })
      .catch(() => {
        // Katalog alinamazsa sebep secimi devre disi kalir; sayfanin geri
        // kalani (harita, durum, yorumlar) etkilenmez.
        if (!iptal) setCauseCatalog(null);
      });
    return () => {
      iptal = true;
    };
  }, [accessToken]);

  const [comments, setComments] = useState<FaultComment[]>([]);
  const [commentDraft, setCommentDraft] = useState("");
  const [noteDraft, setNoteDraft] = useState("");
  const [causeDraft, setCauseDraft] = useState("");
  const [causeDetailDraft, setCauseDetailDraft] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [mapFocus, setMapFocus] = useState<"zone" | "line" | "grid">("zone");

  // Taslaklar ariza KIMLIGI degisince sifirlanir — her render'da degil.
  // Aksi halde kullanici yazarken liste tazelenip yazdigini silerdi.
  useEffect(() => {
    setNoteDraft("");
    setCauseDraft("");
    setCauseDetailDraft("");
    setCommentDraft("");
    setError("");
  }, [faultId]);

  // Sunucudaki deger ilk geldiginde taslaga yansisin (kullanici henuz
  // dokunmadiysa). `?? ""` yerine bos kontrol: kullanicinin bilerek
  // bosalttigi bir alani geri doldurmayalim.
  const [taslakYuklendi, setTaslakYuklendi] = useState(false);
  useEffect(() => {
    if (!fault || taslakYuklendi) return;
    setNoteDraft(fault.note ?? "");
    setCauseDraft(fault.cause_code ?? "");
    setCauseDetailDraft(fault.cause_detail ?? "");
    setTaslakYuklendi(true);
  }, [fault, taslakYuklendi]);
  useEffect(() => setTaslakYuklendi(false), [faultId]);

  useEffect(() => {
    let iptal = false;
    void (async () => {
      try {
        const list = await onLoadComments(faultId);
        if (!iptal) setComments(list);
      } catch (err) {
        if (!iptal) {
          setError(err instanceof Error ? err.message : t("faults.detail.loadingComments"));
        }
      }
    })();
    return () => {
      iptal = true;
    };
  }, [faultId, onLoadComments, t]);

  // Canli sure sayaci.
  const [now, setNow] = useState<number>(() => Date.now());
  const isLive = fault ? fault.status !== "closed" && fault.status !== "resolved" : false;
  useEffect(() => {
    // Kapanmis arizada saniye saymanin anlami yok; bos yere her saniye
    // render etmeyelim.
    if (!isLive) return;
    const id = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(id);
  }, [isLive]);

  const elapsedText = useMemo(() => {
    if (!fault) return "—";
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
  }, [now, fault]);

  const userOptions = useMemo(
    () => [...users].sort((a, b) => a.full_name.localeCompare(b.full_name, localeTag)),
    [users, localeTag]
  );

  const canEdit = fault ? canAssign || fault.assigned_to_username === currentUsername : false;

  const alarmActiveDeviceIds = useMemo(() => {
    const s = new Set<number>();
    for (const a of alarms ?? []) if (!a.reset) s.add(a.device_id);
    return s;
  }, [alarms]);

  const mapView = useMemo(() => {
    if (!gridSnapshot || !fault) return null;
    return buildFaultMapView({
      poles: gridSnapshot.poles,
      segments: gridSnapshot.segments,
      lines: gridSnapshot.lines ?? [],
      fault,
      devices: devices ?? [],
      alarmActiveDeviceIds,
      poleFallback: t("faults.detail.tooltipPole"),
      deviceFallback: t("common.device")
    });
  }, [gridSnapshot, fault, devices, alarmActiveDeviceIds, t]);

  /** Mutasyon sarmalayicisi: hata mesajini tek yerde topla, listede olmayan
   *  bir arizada kendi kopyamizi tazele. */
  const calistir = useCallback(
    async (islem: () => Promise<void>, hataAnahtari: string) => {
      setSaving(true);
      setError("");
      try {
        await islem();
        if (!listeKaydi) await cek();
      } catch (err) {
        setError(err instanceof Error ? err.message : t(hataAnahtari));
      } finally {
        setSaving(false);
      }
    },
    [listeKaydi, cek, t]
  );

  const causeLabel = useCallback(
    (c: FaultCause) => (i18n.language?.startsWith("tr") ? c.label_tr : c.label_en),
    [i18n.language]
  );

  /** Aileye gore gruplanmis secim listesi — duz 19'luk liste taranmasi zor. */
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
   *  "girilmis" sayilirsa istatistik, kimsenin bakmadigi bir tahminle dolar. */
  const suggestedCause = useMemo(() => {
    if (!causeCatalog || !fault || fault.cause_code) return null;
    const kod = fault.auto_cause_code;
    if (!kod) return null;
    const c = causeCatalog.causes.find((x) => x.code === kod);
    return c ? { code: c.code, label: causeLabel(c) } : null;
  }, [causeCatalog, fault, causeLabel]);

  // ---- Yukleniyor / bulunamadi -------------------------------------------
  if (!fault) {
    return (
      <div className="fd-page fd-page--bare">
        <button type="button" className="fd-back" onClick={onBack}>
          <ArrowLeft size={15} />
          {t("faults.detail.backToList")}
        </button>
        <div className="fd-placeholder">
          {yukleniyor ? (
            <>
              <Timer size={26} strokeWidth={1.5} />
              {t("faults.detail.loading")}
            </>
          ) : (
            <>
              <TriangleAlert size={26} strokeWidth={1.5} />
              {yuklemeHatasi || t("faults.detail.notFound", { id: faultId })}
            </>
          )}
        </div>
      </div>
    );
  }

  const statusColor = STATUS_COLOR[fault.status] ?? "#64748b";

  return (
    <div className="fd-page">
      {/* ---- Ust serit: geri + kunye + olculer ---- */}
      <header className="fd-head">
        <div className="fd-head-top">
          <button type="button" className="fd-back" onClick={onBack}>
            <ArrowLeft size={15} />
            {t("faults.detail.backToList")}
          </button>
          <span
            className="fd-status-badge"
            style={{ background: `${statusColor}18`, color: statusColor }}
          >
            <CircleDot size={13} />
            {t(`faults.status.${fault.status}`, { defaultValue: fault.status })}
          </span>
        </div>

        <h1 className="fd-title">{fault.line_name}</h1>

        <nav className="fd-breadcrumb" aria-label={t("faults.detail.mapTitle")}>
          <MapPin size={13} />
          <span>{fault.region_name}</span>
          <em>/</em>
          <span>{fault.line_name}</span>
          <em>/</em>
          <strong>
            {t("faults.card.rangeText", { from: fault.from_pole_seq, to: fault.to_pole_seq })}
          </strong>
        </nav>

        <div className="fd-metrics">
          <Metric
            Icon={CalendarClock}
            label={t("faults.card.openedAt")}
            value={fmtDate(fault.opened_at, localeTag)}
          />
          <Metric
            Icon={Timer}
            label={t("faults.detail.duration")}
            value={elapsedText}
            canli={isLive}
            not={isLive ? t("faults.card.durationLive") : t("faults.card.durationFinal")}
          />
          <Metric
            Icon={UserRound}
            label={t("faults.detail.assignee")}
            value={
              fault.assigned_to_full_name ??
              fault.assigned_to_username ??
              t("faults.detail.assigneeEmpty")
            }
          />
          <Metric
            Icon={Route}
            label={t("faults.detail.distanceFromStart")}
            value={formatDistanceRange(fault.zone_start_m, fault.zone_end_m) || "—"}
          />
        </div>
      </header>

      <div className="fd-body">
        {/* ================= ANA KOLON ================= */}
        <div className="fd-main">
          <section className="fd-card fd-card--map">
            <header className="fd-card-head">
              <h2>
                <MapPin size={15} />
                {t("faults.detail.mapTitle")}
              </h2>
              {/* ODAK SECICI: ariza bolgesine zoom yapinca "bu hattin
                  neresi?" belirsiz kaliyor, tum hatta bakinca ariza noktasi
                  kayboluyordu. Ucu de ayni haritada. */}
              {mapView ? (
                <div className="fd-focus" role="group">
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
            </header>

            {mapView ? (
              <>
                <div className="fd-map-wrap">
                  <MapContainer
                    center={mapView.center}
                    zoom={mapView.zoom}
                    className="fd-map"
                    scrollWheelZoom={false}
                    dragging
                    doubleClickZoom={false}
                    attributionControl={false}
                  >
                    <LayersControl position="topright">
                      <LayersControl.BaseLayer checked name={t("map.layers.street")}>
                        {/* maxZoom verilmezse Leaflet 18'e duser ve sokak
                            gorunumu uydudan (19) bir kademe geride kalir. */}
                        <ResilientTileLayer
                          layer="osm"
                          attribution={MAP_LAYERS[0].attribution}
                          maxZoom={MAP_LAYERS[0].maxZoom}
                        />
                      </LayersControl.BaseLayer>
                      <LayersControl.BaseLayer name={t("map.layers.satellite")}>
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
                    {/* Tum sebeke gorunumunde komsu hatlar SOLUK — ariza
                        hatti one cikmaya devam etsin. */}
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
                        icon={polePin(
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
                    {mapView.deviceMarkers.map((d) => (
                      <Marker
                        key={`d-${d.deviceId}`}
                        position={[d.lat, d.lon]}
                        icon={deviceIcon(d.isRed)}
                      >
                        <Tooltip>
                          <strong>{d.name}</strong>
                          {d.code ? (
                            <>
                              <br />
                              <span style={{ opacity: 0.7 }}>{d.code}</span>
                            </>
                          ) : null}
                          <br />
                          <em style={{ color: d.isRed ? "#dc2626" : "#10b981" }}>
                            {d.isRed
                              ? t("faults.detail.deviceDetectedFault")
                              : t("faults.detail.deviceNoFault")}
                          </em>
                        </Tooltip>
                      </Marker>
                    ))}
                  </MapContainer>
                </div>
                <div className="fd-legend">
                  <span>
                    <i className="fd-legend-line" style={{ background: "#ef4444" }} />
                    {t("faults.detail.mapLegendFault")}
                  </span>
                  <span>
                    <i className="fd-legend-line" style={{ background: "#16a34a" }} />
                    {t("faults.detail.mapLegendOk")}
                  </span>
                  <span>
                    <i className="fd-legend-dot" style={{ background: "#dc2626" }} />
                    {t("faults.detail.mapLegendDeviceRed")}
                  </span>
                  <span>
                    <i className="fd-legend-dot" style={{ background: "#10b981" }} />
                    {t("faults.detail.mapLegendDeviceGreen")}
                  </span>
                </div>
              </>
            ) : (
              <p className="fd-empty">{t("faults.detail.mapEmpty")}</p>
            )}
          </section>

          <div className="fd-subgrid">
            {/* Ariza tespit eden cihazlar — arizanin YERINI daraltan cift. */}
            <section className="fd-card">
              <header className="fd-card-head">
                <h2>
                  <Zap size={15} />
                  {t("faults.detail.devicesTitle")}
                </h2>
                <small>{t("faults.detail.devicesHint")}</small>
              </header>
              <div className="fd-devices">
                <div className="fd-device fd-device--red">
                  <span className="fd-device-dot" />
                  <span className="fd-device-role">{t("faults.detail.deviceLastRedRole")}</span>
                  <strong>{fault.last_red_device_name ?? "—"}</strong>
                  {fault.last_red_device_code ? <small>{fault.last_red_device_code}</small> : null}
                </div>
                <ArrowRight className="fd-device-arrow" size={16} />
                <div className="fd-device fd-device--green">
                  <span className="fd-device-dot" />
                  <span className="fd-device-role">{t("faults.detail.deviceFirstGreenRole")}</span>
                  <strong>
                    {fault.first_green_device_name ?? t("faults.detail.deviceFirstGreenLineEnd")}
                  </strong>
                  {fault.first_green_device_code ? (
                    <small>{fault.first_green_device_code}</small>
                  ) : null}
                </div>
              </div>
            </section>

            {/* Tel mesafesi — backend'de direk + cihaz koordinatlarindan hat
                boyunca hesaplanip kayda yazilir (line_distance_service). */}
            {formatDistanceRange(fault.zone_start_m, fault.zone_end_m) ? (
              <section className="fd-card">
                <header className="fd-card-head">
                  <h2>
                    <Route size={15} />
                    {t("faults.detail.distanceTitle")}
                  </h2>
                </header>
                <dl className="fd-kv">
                  <div>
                    <dt>{t("faults.detail.distanceFromStart")}</dt>
                    <dd>{formatDistanceRange(fault.zone_start_m, fault.zone_end_m)}</dd>
                  </div>
                  {fault.zone_length_m != null ? (
                    <div>
                      <dt>
                        {t("faults.detail.distanceFromDevice", {
                          device:
                            fault.last_red_device_name ?? fault.last_red_device_code ?? "—"
                        })}
                      </dt>
                      <dd>
                        {t("faults.detail.distanceAheadRange", {
                          span: formatDistanceM(fault.zone_length_m)
                        })}
                      </dd>
                    </div>
                  ) : null}
                </dl>
              </section>
            ) : null}

            {mapView && mapView.rangePoles.length > 0 ? (
              <section className="fd-card">
                <header className="fd-card-head">
                  <h2>
                    <CircleDot size={15} />
                    {t("faults.detail.rangePolesTitle")}
                  </h2>
                </header>
                <ul className="fd-poles">
                  {mapView.rangePoles.map((rp) => (
                    <li
                      key={rp.id}
                      className={rp.isStart ? "is-start" : rp.isEnd ? "is-end" : undefined}
                    >
                      <span className="fd-pole-seq">#{rp.sequence_no}</span>
                      <strong>{rp.name}</strong>
                      {rp.isStart ? (
                        <span className="fd-tag fd-tag--red">
                          {t("faults.detail.poleRangeStart")}
                        </span>
                      ) : rp.isEnd ? (
                        <span className="fd-tag fd-tag--green">
                          {t("faults.detail.poleRangeEnd")}
                        </span>
                      ) : null}
                    </li>
                  ))}
                </ul>
              </section>
            ) : null}
          </div>
        </div>

        {/* ================= YAN KOLON (ticket) ================= */}
        <aside className="fd-rail">
          {error ? <p className="fd-error">{error}</p> : null}

          <section className="fd-card">
            <header className="fd-card-head">
              <h2>
                <UserRound size={15} />
                {t("faults.detail.tickets")}
              </h2>
              <small>{t("faults.detail.ticketsHint")}</small>
            </header>

            <label className="fd-label" htmlFor="fd-assignee">
              {t("faults.detail.assignee")}
            </label>
            {canAssign ? (
              <select
                id="fd-assignee"
                className="fd-select"
                value={fault.assigned_to_username ?? ""}
                onChange={(e) =>
                  void calistir(
                    () => onAssign(fault.id, e.target.value || null),
                    "alarms.errors.assignFailed"
                  )
                }
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
              <div className="fd-assignee">
                {fault.assigned_to_username ? (
                  <>
                    <span className="fd-avatar">
                      {(fault.assigned_to_username || "?").substring(0, 2).toUpperCase()}
                    </span>
                    <div>
                      <strong>
                        {fault.assigned_to_full_name ?? fault.assigned_to_username}
                      </strong>
                      {fault.assigned_to_full_name ? (
                        <small>{fault.assigned_to_username}</small>
                      ) : null}
                    </div>
                  </>
                ) : (
                  <span className="fd-muted">{t("faults.detail.assigneeEmpty")}</span>
                )}
              </div>
            )}

            <span className="fd-label">{t("faults.detail.statusLabel")}</span>
            <div className="fd-status-grid">
              {AKIS.map((s) => {
                const active = fault.status === s;
                const color = STATUS_COLOR[s];
                return (
                  <button
                    key={s}
                    type="button"
                    className={`fd-status-btn ${active ? "is-active" : ""}`}
                    onClick={() =>
                      void calistir(
                        () => onUpdateStatus(fault.id, s),
                        "common.errorOccurred"
                      )
                    }
                    disabled={saving || !canEdit}
                    style={
                      active
                        ? { background: color, borderColor: color, color: "#fff" }
                        : { borderColor: `${color}55` }
                    }
                  >
                    <span className="fd-status-dot" style={{ background: color }} />
                    {t(`faults.status.${s}`)}
                  </button>
                );
              })}
            </div>
          </section>

          {/* Ariza sebebi — analiz katmaninin ogrenecegi TEK insan etiketi. */}
          <section className="fd-card">
            <header className="fd-card-head">
              <h2>
                <TriangleAlert size={15} />
                {t("faults.detail.causeTitle")}
              </h2>
              <small>{t("faults.detail.causeHint")}</small>
            </header>

            {suggestedCause ? (
              <div className="fd-suggestion">
                <Lightbulb size={14} />
                <span>{t("faults.detail.causeSuggested", { cause: suggestedCause.label })}</span>
                {canEdit && causeDraft !== suggestedCause.code ? (
                  <button type="button" onClick={() => setCauseDraft(suggestedCause.code)}>
                    {t("faults.detail.causeUseSuggestion")}
                  </button>
                ) : null}
              </div>
            ) : null}

            <select
              className="fd-select"
              value={causeDraft}
              disabled={saving || !canEdit || causeCatalog === null}
              onChange={(e) => setCauseDraft(e.target.value)}
            >
              <option value="">{t("faults.detail.causeNotSet")}</option>
              {causeGroups.map(([grup, liste]) => (
                <optgroup
                  key={grup}
                  label={t(`faults.causeGroup.${grup}`, { defaultValue: grup })}
                >
                  {liste.map((c) => (
                    <option key={c.code} value={c.code}>
                      {causeLabel(c)}
                    </option>
                  ))}
                </optgroup>
              ))}
            </select>

            <textarea
              className="fd-textarea"
              rows={3}
              value={causeDetailDraft}
              onChange={(e) => setCauseDetailDraft(e.target.value)}
              disabled={saving || !canEdit}
              placeholder={t("faults.detail.causeDetailPlaceholder")}
            />

            {canEdit ? (
              <button
                type="button"
                className="fd-save"
                onClick={() =>
                  void calistir(
                    () =>
                      onUpdateCause(fault.id, {
                        // Bos secim = sebebi GERI AL (yanlis secildiyse
                        // duzeltilebilmeli).
                        cause_code: causeDraft || null,
                        cause_detail: causeDetailDraft.trim() || null
                      }),
                    "common.errorOccurred"
                  )
                }
                disabled={saving}
              >
                <Save size={14} />
                {t("faults.detail.saveCause")}
              </button>
            ) : null}
          </section>

          <section className="fd-card">
            <header className="fd-card-head">
              <h2>
                <Save size={15} />
                {t("faults.detail.writeNote")}
              </h2>
              <small>{t("faults.detail.writeNoteHint")}</small>
            </header>
            <textarea
              className="fd-textarea"
              rows={4}
              value={noteDraft}
              onChange={(e) => setNoteDraft(e.target.value)}
              disabled={saving || !canEdit}
              placeholder={t("faults.detail.writeNotePlaceholder")}
            />
            {canEdit ? (
              <button
                type="button"
                className="fd-save"
                onClick={() =>
                  void calistir(
                    () => onUpdateNote(fault.id, noteDraft.trim() || null),
                    "common.errorOccurred"
                  )
                }
                disabled={saving}
              >
                <Save size={14} />
                {t("faults.detail.saveNote")}
              </button>
            ) : null}
          </section>

          <section className="fd-card">
            <header className="fd-card-head">
              <h2>
                <MessagesSquare size={15} />
                {t("faults.detail.commentsTitle")}
                {comments.length > 0 ? <span className="fd-count">{comments.length}</span> : null}
              </h2>
            </header>
            <ul className="fd-comments">
              {comments.length === 0 ? (
                <li className="fd-muted">{t("faults.detail.commentsHint")}</li>
              ) : (
                comments.map((c) => (
                  <li key={c.id} className="fd-comment">
                    <header>
                      <span className="fd-avatar">
                        {(c.author_username || "?").substring(0, 2).toUpperCase()}
                      </span>
                      <strong>{c.author_username}</strong>
                      <time>{fmtDate(c.created_at, localeTag)}</time>
                    </header>
                    <p>{c.body}</p>
                  </li>
                ))
              )}
            </ul>
            {canEdit ? (
              <div className="fd-comment-add">
                <textarea
                  className="fd-textarea"
                  rows={3}
                  placeholder={t("faults.detail.commentsAddPlaceholder")}
                  value={commentDraft}
                  onChange={(e) => setCommentDraft(e.target.value)}
                  disabled={saving}
                />
                <button
                  type="button"
                  className="fd-save fd-save--send"
                  onClick={() =>
                    void calistir(async () => {
                      const body = commentDraft.trim();
                      if (!body) return;
                      await onAddComment(fault.id, body);
                      setComments(await onLoadComments(fault.id));
                      setCommentDraft("");
                    }, "alarms.errors.commentFailed")
                  }
                  disabled={saving || !commentDraft.trim()}
                >
                  <Send size={14} />
                  {t("faults.detail.addCommentBtn")}
                </button>
              </div>
            ) : null}
          </section>
        </aside>
      </div>
    </div>
  );
}

function Metric({
  Icon,
  label,
  value,
  not,
  canli
}: {
  Icon: typeof Timer;
  label: string;
  value: string;
  not?: string;
  canli?: boolean;
}) {
  return (
    <div className={`fd-metric ${canli ? "is-live" : ""}`} title={not}>
      <span className="fd-metric-icon">
        <Icon size={16} />
      </span>
      <span className="fd-metric-body">
        <span className="fd-metric-label">{label}</span>
        <strong className="fd-metric-value">
          {canli ? <i className="fd-pulse" aria-hidden="true" /> : null}
          {value}
        </strong>
      </span>
    </div>
  );
}
