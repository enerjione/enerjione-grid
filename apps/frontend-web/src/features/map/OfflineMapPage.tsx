import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { MapContainer, Rectangle, TileLayer, Tooltip, useMap, useMapEvents } from "react-leaflet";
import type { LatLngBoundsExpression, LeafletMouseEvent } from "leaflet";
import { Download, HardDrive, Loader2, MousePointerSquareDashed, Trash2 } from "lucide-react";

import { useToast } from "../../components/ToastProvider";
import {
  cancelMapPack,
  deleteMapPack,
  estimateMapArea,
  fetchMapTileSummary,
  startMapPack
} from "../../shared/api";
import { MAP_LAYERS, tileUrl } from "../../shared/mapTiles";
import type { MapAreaRequest, MapPack, MapTileSummary } from "../../shared/types";

/**
 * Cevrimdisi harita yonetimi (Muhendislik > Sistem).
 *
 * Sahaya cikmadan ONCE yapilan bir hazirlik isi oldugu icin cihaz haritasinin
 * uzerinde bir modal degil, kendi sayfasi olmasi dogru. Kazanci: indirilmis
 * alanlar haritada dikdortgen olarak GORUNUR — hangi bolgenin cevrimdisi
 * hazir oldugu bir bakista anlasilir, ustune tekrar indirme yapilmaz.
 */

type Props = {
  accessToken: string;
  /** Haritanin ilk merkezi — cihazlarin oldugu bolge. Yoksa Turkiye geneli. */
  initialCenter?: [number, number];
};

const TURKEY_CENTER: [number, number] = [39.0, 35.0];
const BYTES_IN_MB = 1024 * 1024;

function formatBytes(bytes: number): string {
  if (bytes >= 1024 ** 3) return `${(bytes / 1024 ** 3).toFixed(2)} GB`;
  if (bytes >= BYTES_IN_MB) return `${(bytes / BYTES_IN_MB).toFixed(1)} MB`;
  return `${Math.max(1, Math.round(bytes / 1024))} KB`;
}

/** bbox dizisi [guney, bati, kuzey, dogu] -> Leaflet bounds. */
function toBounds(bbox: number[]): LatLngBoundsExpression {
  return [
    [bbox[0], bbox[1]],
    [bbox[2], bbox[3]]
  ];
}

type Selection = { south: number; west: number; north: number; east: number };

/**
 * Haritada dikdortgen SECIMI.
 *
 * Secim modunda harita suruklemesi kapatilir; aksi halde basip cekmek
 * haritayi kaydirir ve dikdortgen hic cizilemez. Mod kapaninca geri acilir.
 */
function AreaPicker({
  active,
  onPick
}: {
  active: boolean;
  onPick: (sel: Selection) => void;
}) {
  const map = useMap();
  const startRef = useRef<{ lat: number; lng: number } | null>(null);
  const [draft, setDraft] = useState<Selection | null>(null);

  useEffect(() => {
    if (active) {
      map.dragging.disable();
      map.getContainer().style.cursor = "crosshair";
    } else {
      map.dragging.enable();
      map.getContainer().style.cursor = "";
      setDraft(null);
      startRef.current = null;
    }
    return () => {
      map.dragging.enable();
      map.getContainer().style.cursor = "";
    };
  }, [active, map]);

  const build = (a: { lat: number; lng: number }, b: { lat: number; lng: number }): Selection => ({
    south: Math.min(a.lat, b.lat),
    north: Math.max(a.lat, b.lat),
    west: Math.min(a.lng, b.lng),
    east: Math.max(a.lng, b.lng)
  });

  useMapEvents({
    mousedown(event: LeafletMouseEvent) {
      if (!active) return;
      startRef.current = { lat: event.latlng.lat, lng: event.latlng.lng };
      setDraft(null);
    },
    mousemove(event: LeafletMouseEvent) {
      if (!active || !startRef.current) return;
      setDraft(build(startRef.current, { lat: event.latlng.lat, lng: event.latlng.lng }));
    },
    mouseup(event: LeafletMouseEvent) {
      if (!active || !startRef.current) return;
      const sel = build(startRef.current, { lat: event.latlng.lat, lng: event.latlng.lng });
      startRef.current = null;
      setDraft(null);
      // Tek tiklama (suruklemeden) sifir alanli dikdortgen uretir; yok say.
      if (sel.north - sel.south < 1e-4 || sel.east - sel.west < 1e-4) return;
      onPick(sel);
    }
  });

  if (!draft) return null;
  return (
    <Rectangle
      bounds={toBounds([draft.south, draft.west, draft.north, draft.east])}
      pathOptions={{ color: "#e97800", weight: 2, dashArray: "6 4", fillOpacity: 0.12 }}
    />
  );
}

export function OfflineMapPage({ accessToken, initialCenter }: Props) {
  const { t } = useTranslation();
  const toast = useToast();
  const [summary, setSummary] = useState<MapTileSummary | null>(null);
  const [loading, setLoading] = useState(true);
  const [picking, setPicking] = useState(false);
  const [selection, setSelection] = useState<Selection | null>(null);
  const [layer, setLayer] = useState<string>(MAP_LAYERS[0].key);
  const [name, setName] = useState("");
  const [zoomMax, setZoomMax] = useState(16);
  const [estimate, setEstimate] = useState<{ tiles: number; bytes: number } | null>(null);
  const [estimateError, setEstimateError] = useState("");
  const [busy, setBusy] = useState(false);
  const pollRef = useRef<number | null>(null);

  const reload = useCallback(async () => {
    try {
      setSummary(await fetchMapTileSummary(accessToken));
    } catch (error) {
      toast.error(error instanceof Error ? error.message : t("common.errorOccurred"));
    } finally {
      setLoading(false);
    }
  }, [accessToken, t, toast]);

  useEffect(() => {
    void reload();
  }, [reload]);

  // Indirme surerken ilerlemeyi tazele; bitince polling durur.
  const hasActive = Boolean(
    summary?.packs.some((p) => p.status === "running" || p.status === "pending")
  );
  useEffect(() => {
    if (!hasActive) {
      if (pollRef.current !== null) {
        window.clearInterval(pollRef.current);
        pollRef.current = null;
      }
      return;
    }
    if (pollRef.current !== null) return;
    pollRef.current = window.setInterval(() => void reload(), 2000);
    return () => {
      if (pollRef.current !== null) {
        window.clearInterval(pollRef.current);
        pollRef.current = null;
      }
    };
  }, [hasActive, reload]);

  const zoomMin = 10;
  const request: MapAreaRequest | null = selection
    ? { layer, ...selection, zoom_min: zoomMin, zoom_max: zoomMax }
    : null;

  useEffect(() => {
    if (!request) {
      setEstimate(null);
      setEstimateError("");
      return;
    }
    let cancelled = false;
    setEstimateError("");
    void estimateMapArea(accessToken, request)
      .then((result) => {
        if (cancelled) return;
        setEstimate({ tiles: result.tile_count, bytes: result.estimated_bytes });
      })
      .catch((error: unknown) => {
        if (cancelled) return;
        setEstimate(null);
        setEstimateError(error instanceof Error ? error.message : t("common.errorOccurred"));
      });
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [accessToken, layer, zoomMax, selection]);

  const handleStart = async () => {
    if (!request) return;
    setBusy(true);
    try {
      await startMapPack(accessToken, { ...request, name });
      toast.success(t("map.offline.started"));
      setName("");
      setSelection(null);
      await reload();
    } catch (error) {
      toast.error(error instanceof Error ? error.message : t("common.errorOccurred"));
    } finally {
      setBusy(false);
    }
  };

  const handleCancel = async (pack: MapPack) => {
    try {
      await cancelMapPack(accessToken, pack.id);
      await reload();
    } catch (error) {
      toast.error(error instanceof Error ? error.message : t("common.errorOccurred"));
    }
  };

  const handleDelete = async (pack: MapPack) => {
    if (!window.confirm(t("map.offline.confirmDelete", { name: pack.name }))) return;
    try {
      await deleteMapPack(accessToken, pack.id, true);
      await reload();
    } catch (error) {
      toast.error(error instanceof Error ? error.message : t("common.errorOccurred"));
    }
  };

  const maxZoom = summary?.max_download_zoom ?? 17;
  const tooLarge = Boolean(estimate && summary && estimate.tiles > summary.max_pack_tiles);
  const packs = summary?.packs ?? [];
  const center = useMemo(() => initialCenter ?? TURKEY_CENTER, [initialCenter]);

  return (
    <section className="tab-panel offline-page">
      <div className="offline-page-grid">
        {/* ---- Harita: indirilmis alanlar + secim ---- */}
        <div className="offline-page-map">
          <MapContainer center={center} zoom={6} scrollWheelZoom className="offline-page-leaflet">
            <TileLayer url={tileUrl("osm")} attribution={MAP_LAYERS[0].attribution} />

            {/* Indirilmis alanlar — hangi bolge cevrimdisi hazir, bir bakista. */}
            {packs
              .filter((pack) => pack.bbox?.length === 4)
              .map((pack) => (
                <Rectangle
                  key={pack.id}
                  bounds={toBounds(pack.bbox)}
                  pathOptions={{
                    color: pack.status === "done" ? "#16a34a" : "#64748b",
                    weight: 2,
                    fillOpacity: pack.status === "done" ? 0.15 : 0.07
                  }}
                >
                  <Tooltip sticky>
                    {pack.name} · z{pack.zoom_min}–{pack.zoom_max} ·{" "}
                    {t(`map.offline.status.${pack.status}`)}
                  </Tooltip>
                </Rectangle>
              ))}

            {/* Secilen yeni alan */}
            {selection ? (
              <Rectangle
                bounds={toBounds([
                  selection.south,
                  selection.west,
                  selection.north,
                  selection.east
                ])}
                pathOptions={{ color: "#e97800", weight: 2, fillOpacity: 0.18 }}
              />
            ) : null}

            <AreaPicker
              active={picking}
              onPick={(sel) => {
                setSelection(sel);
                setPicking(false);
              }}
            />
          </MapContainer>

          <button
            type="button"
            className={`offline-pick-btn${picking ? " is-active" : ""}`}
            onClick={() => setPicking((value) => !value)}
          >
            <MousePointerSquareDashed size={15} />
            {picking ? t("map.offline.pickCancel") : t("map.offline.pick")}
          </button>
          {picking ? <div className="offline-pick-hint">{t("map.offline.pickHint")}</div> : null}
        </div>

        {/* ---- Yan panel: indirme formu + paket listesi ---- */}
        <aside className="offline-page-side">
          <div className="offline-page-card">
            <h3>{t("map.offline.newArea")}</h3>

            {!selection ? (
              <p className="offline-map-empty">{t("map.offline.noSelection")}</p>
            ) : (
              <>
                <label className="offline-map-field">
                  <span>{t("map.offline.name")}</span>
                  <input
                    type="text"
                    value={name}
                    maxLength={120}
                    placeholder={t("map.offline.namePlaceholder")}
                    onChange={(event) => setName(event.target.value)}
                  />
                </label>

                <label className="offline-map-field">
                  <span>{t("map.offline.layer")}</span>
                  <select value={layer} onChange={(event) => setLayer(event.target.value)}>
                    {MAP_LAYERS.map((item) => (
                      <option key={item.key} value={item.key}>
                        {t(item.labelKey)}
                      </option>
                    ))}
                  </select>
                </label>

                <label className="offline-map-field">
                  <span>{t("map.offline.detail", { zoom: zoomMax })}</span>
                  <input
                    type="range"
                    min={12}
                    max={maxZoom}
                    value={zoomMax}
                    onChange={(event) => setZoomMax(Number(event.target.value))}
                  />
                  <span className="offline-map-hint">{t("map.offline.detailHint")}</span>
                </label>

                <div className={`offline-map-estimate${tooLarge ? " is-bad" : ""}`}>
                  {estimateError ? (
                    <span className="offline-map-estimate__bad">{estimateError}</span>
                  ) : estimate ? (
                    <>
                      <strong>{estimate.tiles.toLocaleString()}</strong>
                      <span>{t("map.offline.tiles")}</span>
                      <span className="offline-map-estimate__sep">·</span>
                      <strong>~{formatBytes(estimate.bytes)}</strong>
                    </>
                  ) : (
                    <Loader2 size={14} className="net-spin" />
                  )}
                </div>

                <div className="offline-map-actions">
                  <button
                    type="button"
                    className="primary-btn"
                    disabled={busy || tooLarge || !estimate || hasActive}
                    onClick={() => void handleStart()}
                  >
                    {busy ? <Loader2 size={15} className="net-spin" /> : <Download size={15} />}
                    {t("map.offline.start")}
                  </button>
                  <button
                    type="button"
                    className="secondary-btn"
                    onClick={() => setSelection(null)}
                  >
                    {t("common.cancel")}
                  </button>
                </div>
                {hasActive ? (
                  <span className="offline-map-hint">{t("map.offline.busyHint")}</span>
                ) : null}
              </>
            )}
          </div>

          <div className="offline-page-card offline-page-card--grow">
            <div className="offline-map-packs__head">
              <h3>{t("map.offline.packsTitle")}</h3>
              {summary ? (
                <span className="offline-map-hint">
                  <HardDrive size={13} /> {formatBytes(summary.cache_bytes)}
                </span>
              ) : null}
            </div>

            {loading ? (
              <p className="offline-map-empty">
                <Loader2 size={14} className="net-spin" />
              </p>
            ) : !packs.length ? (
              <p className="offline-map-empty">{t("map.offline.noPacks")}</p>
            ) : (
              <ul className="offline-map-list">
                {packs.map((pack) => {
                  const pct =
                    pack.tile_total > 0 ? Math.round((pack.tile_done / pack.tile_total) * 100) : 0;
                  const active = pack.status === "running" || pack.status === "pending";
                  return (
                    <li key={pack.id} className={`offline-map-item is-${pack.status}`}>
                      <div className="offline-map-item__main">
                        <strong>{pack.name}</strong>
                        <span className="offline-map-hint">
                          z{pack.zoom_min}–{pack.zoom_max} · {pack.tile_total.toLocaleString()}{" "}
                          {t("map.offline.tiles")}
                          {pack.bytes_written > 0 ? ` · ${formatBytes(pack.bytes_written)}` : ""}
                        </span>
                        {active ? (
                          <div className="offline-map-progress">
                            <div
                              className="offline-map-progress__bar"
                              style={{ width: `${pct}%` }}
                            />
                          </div>
                        ) : null}
                        {pack.error ? (
                          <span className="offline-map-estimate__bad">{pack.error}</span>
                        ) : null}
                        {pack.tile_failed > 0 && !active ? (
                          <span className="offline-map-hint">
                            {t("map.offline.failedTiles", { count: pack.tile_failed })}
                          </span>
                        ) : null}
                      </div>
                      <div className="offline-map-item__side">
                        <span className={`offline-map-badge is-${pack.status}`}>
                          {active ? `%${pct}` : t(`map.offline.status.${pack.status}`)}
                        </span>
                        {active ? (
                          <button
                            type="button"
                            className="secondary-btn"
                            onClick={() => void handleCancel(pack)}
                          >
                            {t("map.offline.cancel")}
                          </button>
                        ) : (
                          <button
                            type="button"
                            className="offline-map-del"
                            title={t("map.offline.delete")}
                            onClick={() => void handleDelete(pack)}
                          >
                            <Trash2 size={15} />
                          </button>
                        )}
                      </div>
                    </li>
                  );
                })}
              </ul>
            )}
          </div>
        </aside>
      </div>
    </section>
  );
}
