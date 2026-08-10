/**
 * Ariza sicaklik haritasi — hangi cografyada tekrar eden bir sorun var.
 *
 * SICAKLIK HARITASI TEK BASINA YETMEZ. Bulanik bir leke "buralarda bir sorun
 * var" der ama "kac ariza" demez; ustelik komsu direkleri tek bir lekeye
 * kaynatir. Bu ekran sahaya teknisyen gonderme kararini besleyecek, dolayisiyla
 * lekenin ustune ISARETCILER de konur: her nokta tiklanabilir/hover'lanabilir
 * ve KESIN adedi soyler. Leke deseni gosterir, isaretci olcuyu verir.
 *
 * Cizim mantigi `heatField.ts` icinde — normalizasyon kararlarinin gerekcesi
 * ve testleri orada.
 */
import { useEffect, useMemo, useRef } from "react";
import { useTranslation } from "react-i18next";
import L from "leaflet";
import { CircleMarker, MapContainer, Tooltip, useMap } from "react-leaflet";

import {
  HEAT_STOPS,
  heatColor,
  heatIntensities,
  heatRadius,
  type HeatPoint
} from "./heatField";

import { ResilientTileLayer } from "../../components/ResilientTileLayer";
import { DEFAULT_MAP_LAYER, MAP_LAYERS } from "../../shared/mapTiles";

/** Gradyan arama tablosu 256 kademe — canvas alfa kanali 0..255. */
const LUT_SIZE = 256;

function gradyanTablosu(): Uint8ClampedArray {
  const c = document.createElement("canvas");
  c.width = LUT_SIZE;
  c.height = 1;
  const ctx = c.getContext("2d");
  if (!ctx) return new Uint8ClampedArray(LUT_SIZE * 4);
  const g = ctx.createLinearGradient(0, 0, LUT_SIZE, 0);
  // Lejant ve leke AYNI tablodan turer; elle iki ayri palet tutulmaz.
  HEAT_STOPS.forEach((s) => g.addColorStop(s.at, s.color));
  ctx.fillStyle = g;
  ctx.fillRect(0, 0, LUT_SIZE, 1);
  return ctx.getImageData(0, 0, LUT_SIZE, 1).data;
}

function HeatCanvas({ points }: { points: HeatPoint[] }) {
  const map = useMap();
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const lutRef = useRef<Uint8ClampedArray | null>(null);

  useEffect(() => {
    const canvas = L.DomUtil.create("canvas", "fa-heat-canvas") as HTMLCanvasElement;
    canvas.style.pointerEvents = "none";
    map.getPanes().overlayPane.appendChild(canvas);
    canvasRef.current = canvas;
    if (!lutRef.current) lutRef.current = gradyanTablosu();

    const yogunluklar = heatIntensities(points);

    const ciz = () => {
      const ctx = canvas.getContext("2d", { willReadFrequently: true });
      if (!ctx) return;
      const size = map.getSize();
      if (size.x <= 0 || size.y <= 0) return;

      // Retina ekranda bulanik leke istemiyoruz; cizim DPR olceginde yapilir.
      const dpr = Math.min(2, window.devicePixelRatio || 1);
      canvas.width = Math.round(size.x * dpr);
      canvas.height = Math.round(size.y * dpr);
      canvas.style.width = `${size.x}px`;
      canvas.style.height = `${size.y}px`;
      L.DomUtil.setPosition(canvas, map.containerPointToLayerPoint([0, 0]));

      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      ctx.clearRect(0, 0, size.x, size.y);

      const r = heatRadius(map.getZoom());
      // 1. GECIS: gri tonda yogunluk biriktir. Ust uste binen noktalar
      //    alfa kanalinda dogal olarak toplanir.
      points.forEach((p, i) => {
        const yogunluk = yogunluklar[i];
        if (yogunluk === null) return;
        const pt = map.latLngToContainerPoint([p.latitude, p.longitude]);
        // Ekran disindaki noktalari cizmeye calisma (pan sirasinda bosuna is).
        if (pt.x < -r || pt.y < -r || pt.x > size.x + r || pt.y > size.y + r) return;
        const g = ctx.createRadialGradient(pt.x, pt.y, 0, pt.x, pt.y, r);
        g.addColorStop(0, "rgba(0,0,0,1)");
        g.addColorStop(1, "rgba(0,0,0,0)");
        ctx.globalAlpha = yogunluk;
        ctx.fillStyle = g;
        ctx.beginPath();
        ctx.arc(pt.x, pt.y, r, 0, Math.PI * 2);
        ctx.fill();
      });
      ctx.globalAlpha = 1;

      // 2. GECIS: biriken alfayi renge cevir.
      const lut = lutRef.current;
      if (!lut) return;
      const img = ctx.getImageData(0, 0, canvas.width, canvas.height);
      const d = img.data;
      for (let i = 0; i < d.length; i += 4) {
        const a = d[i + 3];
        if (a === 0) continue;
        const j = a * 4;
        d[i] = lut[j];
        d[i + 1] = lut[j + 1];
        d[i + 2] = lut[j + 2];
      }
      ctx.putImageData(img, 0, 0);
    };

    // Zoom ANIMASYONU sirasinda canvas kayar (icerik piksel uzayinda sabit,
    // harita olceklenir). Gizleyip bitince yeniden cizmek, kaymis bir lekeyi
    // gostermekten dogru: kaymis leke yanlis yeri isaret eder.
    const gizle = () => {
      canvas.style.opacity = "0";
    };
    const goster = () => {
      canvas.style.opacity = "1";
      ciz();
    };

    map.on("move", ciz);
    map.on("resize", goster);
    map.on("zoomstart", gizle);
    map.on("zoomend", goster);
    ciz();

    return () => {
      map.off("move", ciz);
      map.off("resize", goster);
      map.off("zoomstart", gizle);
      map.off("zoomend", goster);
      canvas.remove();
      canvasRef.current = null;
    };
  }, [map, points]);

  return null;
}

/** Noktalari bir kez ekrana sigdirir. Kullanici sonra elle gezinebilsin diye
 *  yalnizca VERI degisince tetiklenir, her yenilemede degil. */
function FitPoints({ points }: { points: HeatPoint[] }) {
  const map = useMap();
  const anahtar = points.length
    ? `${points.length}:${points[0].latitude.toFixed(4)},${points[0].longitude.toFixed(4)}`
    : "";
  const sonRef = useRef<string>("");

  useEffect(() => {
    if (!anahtar || anahtar === sonRef.current) return;
    const gecerli = points.filter(
      (p) => Number.isFinite(p.latitude) && Number.isFinite(p.longitude)
    );
    if (gecerli.length === 0) return;
    sonRef.current = anahtar;
    // Kap henuz olculmemis olabilir (kart yeni acildi): olcumu tazele.
    map.invalidateSize({ animate: false });
    const bounds = L.latLngBounds(gecerli.map((p) => [p.latitude, p.longitude] as [number, number]));
    map.fitBounds(bounds.pad(0.25), { animate: false, maxZoom: 15 });
  }, [map, points, anahtar]);

  return null;
}

type Props = {
  points: HeatPoint[];
};

export function FaultHeatMap({ points }: Props) {
  const { t } = useTranslation();
  const yogunluklar = useMemo(() => heatIntensities(points), [points]);
  const katman = MAP_LAYERS.find((l) => l.key === DEFAULT_MAP_LAYER) ?? MAP_LAYERS[0];
  const enCok = points.reduce((m, p) => Math.max(m, p.weight), 0);

  return (
    <div className="fa-heat">
      <MapContainer
        className="fa-heat-map"
        center={[39.0, 35.0]}
        zoom={6}
        scrollWheelZoom
        attributionControl={false}
      >
        <ResilientTileLayer layer={katman.key} maxZoom={katman.maxZoom} />
        <HeatCanvas points={points} />
        <FitPoints points={points} />
        {points.map((p, i) => {
          const yogunluk = yogunluklar[i];
          if (yogunluk === null) return null;
          return (
            <CircleMarker
              key={`${p.latitude},${p.longitude},${i}`}
              center={[p.latitude, p.longitude]}
              radius={5}
              pathOptions={{
                color: "#ffffff",
                weight: 1.5,
                fillColor: heatColor(yogunluk),
                fillOpacity: 1
              }}
            >
              {/* Lekenin soyleyemedigi sey: KESIN adet. */}
              <Tooltip direction="top" offset={[0, -6]}>
                {t("faultAnalytics.heatCount", { count: p.weight })}
              </Tooltip>
            </CircleMarker>
          );
        })}
      </MapContainer>

      {/* Lejant olmadan renk bir sey ifade etmez: "kirmizi cok mu, biraz mi?"
          ARIA: kapsayiciya `aria-hidden` konulunca iki metin de erisilebilirlik
          agacindan dusuyordu ve canvas lekesinin TEK metin karsiligi kayboluyordu
          (canvas'in kendisi zaten okunamaz). Yalnizca DEKORATIF renk rampasi
          gizlenir; olcegin uclarini anlatan metinler okunur kalir. */}
      <div className="fa-heat-legend">
        <span>{t("faultAnalytics.heatLow")}</span>
        <i className="fa-heat-ramp" aria-hidden="true" />
        <span>{t("faultAnalytics.heatHigh", { count: enCok })}</span>
      </div>
    </div>
  );
}
