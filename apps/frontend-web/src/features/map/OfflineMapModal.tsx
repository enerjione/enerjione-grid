import { useCallback, useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { Download, HardDrive, Loader2, Trash2, X } from "lucide-react";

import { useToast } from "../../components/ToastProvider";
import {
  cancelMapPack,
  deleteMapPack,
  estimateMapArea,
  fetchMapTileSummary,
  startMapPack
} from "../../shared/api";
import { MAP_LAYERS } from "../../shared/mapTiles";
import type { MapAreaRequest, MapPack, MapTileSummary } from "../../shared/types";

/**
 * Cevrimdisi harita alani indirme.
 *
 * ALAN SECIMI = HARITANIN O ANKI GORUNUMU. Haritaya dikdortgen cizdirmek
 * yerine bunu tercih ettik: operator zaten ilgilendigi bolgeye zoom yapmis
 * oluyor, "gordugun yeri indir" tek cumlede anlasiliyor ve ayri bir cizim
 * araci (ve onun mobil/dokunmatik sorunlari) gerekmiyor.
 */

type Props = {
  accessToken: string;
  /** Haritanin o anki sinirlari: [guney, bati, kuzey, dogu] */
  bounds: [number, number, number, number];
  /** Haritanin o anki zoom'u — indirme araliginin alt siniri icin. */
  currentZoom: number;
  onClose: () => void;
};

const BYTES_IN_MB = 1024 * 1024;

function formatBytes(bytes: number): string {
  if (bytes >= 1024 ** 3) return `${(bytes / 1024 ** 3).toFixed(2)} GB`;
  if (bytes >= BYTES_IN_MB) return `${(bytes / BYTES_IN_MB).toFixed(1)} MB`;
  return `${Math.max(1, Math.round(bytes / 1024))} KB`;
}

export function OfflineMapModal({ accessToken, bounds, currentZoom, onClose }: Props) {
  const { t } = useTranslation();
  const toast = useToast();
  const [summary, setSummary] = useState<MapTileSummary | null>(null);
  const [layer, setLayer] = useState<string>(MAP_LAYERS[0].key);
  const [name, setName] = useState("");
  // Alt sinir haritanin mevcut zoom'u: daha genis zoom'lar zaten az karo
  // tutar ve uzaklastirinca bos ekran gorunmesin diye dahil ediyoruz.
  const zoomMin = Math.max(0, Math.min(currentZoom, 12));
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
    }
  }, [accessToken, t, toast]);

  useEffect(() => {
    void reload();
  }, [reload]);

  // Devam eden indirme varken ilerlemeyi tazele. Is bitince polling durur —
  // acik modal bosuna istek atmasin.
  useEffect(() => {
    const running = summary?.packs.some((p) => p.status === "running" || p.status === "pending");
    if (!running) {
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
  }, [summary, reload]);

  const request: MapAreaRequest = {
    layer,
    south: bounds[0],
    west: bounds[1],
    north: bounds[2],
    east: bounds[3],
    zoom_min: zoomMin,
    zoom_max: zoomMax
  };

  // Zoom/katman degisince tahmini tazele. Alan sabit (modal acildigi andaki
  // gorunum), o yuzden bounds bagimlilikta yok.
  useEffect(() => {
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
  }, [accessToken, layer, zoomMax, zoomMin]);

  const handleStart = async () => {
    setBusy(true);
    try {
      await startMapPack(accessToken, { ...request, name });
      toast.success(t("map.offline.started"));
      setName("");
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
  const downloading = summary?.packs.some((p) => p.status === "running" || p.status === "pending");

  return (
    <div className="settings-modal-backdrop" onClick={onClose}>
      <div className="settings-modal offline-map-modal" onClick={(e) => e.stopPropagation()}>
        <header className="offline-map-head">
          <h2>
            <Download size={18} />
            {t("map.offline.title")}
          </h2>
          <button type="button" className="offline-map-close" onClick={onClose} aria-label={t("common.close")}>
            <X size={18} />
          </button>
        </header>

        <p className="offline-map-intro">{t("map.offline.intro")}</p>

        <div className="offline-map-form">
          <label className="offline-map-field">
            <span>{t("map.offline.name")}</span>
            <input
              type="text"
              value={name}
              maxLength={120}
              placeholder={t("map.offline.namePlaceholder")}
              onChange={(e) => setName(e.target.value)}
            />
          </label>

          <label className="offline-map-field">
            <span>{t("map.offline.layer")}</span>
            <select value={layer} onChange={(e) => setLayer(e.target.value)}>
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
              min={Math.max(zoomMin + 1, 10)}
              max={maxZoom}
              value={zoomMax}
              onChange={(e) => setZoomMax(Number(e.target.value))}
            />
            <span className="offline-map-hint">{t("map.offline.detailHint")}</span>
          </label>
        </div>

        <div className={`offline-map-estimate${tooLarge ? " is-bad" : ""}`}>
          {estimateError ? (
            <span className="offline-map-estimate__bad">{estimateError}</span>
          ) : estimate ? (
            <>
              <strong>{estimate.tiles.toLocaleString()}</strong>
              <span>{t("map.offline.tiles")}</span>
              <span className="offline-map-estimate__sep">·</span>
              <strong>~{formatBytes(estimate.bytes)}</strong>
              <span>{t("map.offline.diskSpace")}</span>
            </>
          ) : (
            <Loader2 size={14} className="net-spin" />
          )}
        </div>

        <div className="offline-map-actions">
          <button
            type="button"
            className="primary-btn"
            disabled={busy || tooLarge || !estimate || downloading}
            onClick={() => void handleStart()}
          >
            {busy ? <Loader2 size={15} className="net-spin" /> : <Download size={15} />}
            {t("map.offline.start")}
          </button>
          {downloading ? (
            <span className="offline-map-hint">{t("map.offline.busyHint")}</span>
          ) : null}
        </div>

        {/* ---- Indirilmis alanlar ---- */}
        <div className="offline-map-packs">
          <div className="offline-map-packs__head">
            <h3>{t("map.offline.packsTitle")}</h3>
            {summary ? (
              <span className="offline-map-hint">
                <HardDrive size={13} /> {formatBytes(summary.cache_bytes)}
              </span>
            ) : null}
          </div>

          {!summary?.packs.length ? (
            <p className="offline-map-empty">{t("map.offline.noPacks")}</p>
          ) : (
            <ul className="offline-map-list">
              {summary.packs.map((pack) => {
                const pct =
                  pack.tile_total > 0
                    ? Math.round((pack.tile_done / pack.tile_total) * 100)
                    : 0;
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
                          <div className="offline-map-progress__bar" style={{ width: `${pct}%` }} />
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
      </div>
    </div>
  );
}
