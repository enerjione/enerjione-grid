/**
 * Ariza sicaklik haritasi — hangi cografyada tekrar eden bir sorun var.
 *
 * UC KATMAN, UC AYRI SORU
 * -----------------------
 *   1. SEBEKE CIZGISI  "neresi" — sicak nokta hattin neresine denk geliyor.
 *   2. ISI LEKESI      "desen"  — yogunlasma var mi, nereye dogru.
 *   3. ISARETCILER     "olcu"   — tam olarak kac ariza.
 *
 * Ucu de gerekli. Leke tek basina komsu direkleri tek bir dev lekeye
 * kaynatir ve adet soylemez; cizgi olmadan leke bos bir zeminde asili kalir
 * ve "su fiderin ortasinda" diyemezsiniz. Bu ekran sahaya teknisyen gonderme
 * kararini besleyecek.
 *
 * KATMAN SIRASI PANEL ILE SABITLENDI. DOM ekleme sirasina birakilsaydi
 * isaretciler lekenin ALTINDA kalabilirdi — kesin adedi tasiyan katman
 * gorunmez olurdu.
 *
 * Cizim mantigi `heatField.ts` icinde — normalizasyon kararlarinin gerekcesi
 * ve testleri orada.
 */
import { useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { Waypoints } from "lucide-react";
import L from "leaflet";
import { CircleMarker, MapContainer, Polyline, Tooltip, useMap } from "react-leaflet";

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

/** Panel adi -> z-index. Leaflet varsayilanlari: overlayPane 400,
 *  markerPane 600. Uc katman bu araliga ACIKCA yerlestiriliyor. */
const PANELLER: [string, number][] = [
  ["faLines", 420],
  ["faHeat", 450],
  ["faPoints", 460]
];

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

/**
 * Panelleri kurar ve ANCAK ondan sonra katmanlari cizer.
 *
 * Cocuklar panel var olmadan monte edilirse Leaflet onlari sessizce
 * `overlayPane`e koyar ve z-sirasi bozulur — isaretciler lekenin altinda
 * kalir. Bir kare gecikme bu riski tamamen kaldiriyor.
 */
function Paneller({ children }: { children: React.ReactNode }) {
  const map = useMap();
  const [hazir, setHazir] = useState(false);

  useEffect(() => {
    for (const [ad, z] of PANELLER) {
      const panel = map.getPane(ad) ?? map.createPane(ad);
      panel.style.zIndex = String(z);
    }
    setHazir(true);
  }, [map]);

  return hazir ? <>{children}</> : null;
}

function HeatCanvas({ points }: { points: HeatPoint[] }) {
  const map = useMap();
  const lutRef = useRef<Uint8ClampedArray | null>(null);

  useEffect(() => {
    const panel = map.getPane("faHeat") ?? map.getPanes().overlayPane;
    const canvas = L.DomUtil.create("canvas", "fa-heat-canvas") as HTMLCanvasElement;
    canvas.style.pointerEvents = "none";
    panel.appendChild(canvas);
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
    };
  }, [map, points]);

  return null;
}

/** Gorunur her seyi bir kez ekrana sigdirir. Kullanici sonra elle
 *  gezinebilsin diye yalnizca VERI degisince tetiklenir. */
function FitAll({
  points,
  lines
}: {
  points: HeatPoint[];
  lines: [number, number][][];
}) {
  const map = useMap();
  const anahtar = `${points.length}|${lines.length}`;
  const sonRef = useRef<string>("");

  useEffect(() => {
    if (anahtar === sonRef.current || anahtar === "0|0") return;
    const kordinatlar: [number, number][] = [];
    for (const p of points) {
      if (Number.isFinite(p.latitude) && Number.isFinite(p.longitude)) {
        kordinatlar.push([p.latitude, p.longitude]);
      }
    }
    // Ariza yoksa da sebeke gorunsun: "arama alani bu" bilgisi degerli.
    if (kordinatlar.length === 0) lines.forEach((l) => kordinatlar.push(...l));
    if (kordinatlar.length === 0) return;
    sonRef.current = anahtar;
    // Kap henuz olculmemis olabilir (sekme yeni acildi): olcumu tazele.
    map.invalidateSize({ animate: false });
    map.fitBounds(L.latLngBounds(kordinatlar).pad(0.25), {
      animate: false,
      maxZoom: 15
    });
  }, [map, points, lines, anahtar]);

  return null;
}

type Props = {
  points: HeatPoint[];
  /** Sebeke parcalari (direk -> direk). Bos gecilebilir. */
  lines?: [number, number][][];
};

export function FaultHeatMap({ points, lines = [] }: Props) {
  const { t } = useTranslation();
  const yogunluklar = useMemo(() => heatIntensities(points), [points]);
  const katman = MAP_LAYERS.find((l) => l.key === DEFAULT_MAP_LAYER) ?? MAP_LAYERS[0];
  const enCok = points.reduce((m, p) => Math.max(m, p.weight), 0);
  const arizaVar = yogunluklar.some((y) => y !== null);

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
        <Paneller>
          {/* Sebeke: lekenin ALTINDA, notr ve ince — baglam verir, dikkat
              cekmez. Tek `Polyline` icinde cok parcali geometri: her segment
              icin ayri katman acmak 600 cihazli sahada binlerce DOM dugumu
              demekti. */}
          {lines.length ? (
            <Polyline
              pane="faLines"
              positions={lines}
              interactive={false}
              className="fa-heat-line"
              pathOptions={{ color: "#94a3b8", weight: 2, opacity: 0.85 }}
            />
          ) : null}

          <HeatCanvas points={points} />

          {points.map((p, i) => {
            const yogunluk = yogunluklar[i];
            if (yogunluk === null) return null;
            return (
              <CircleMarker
                key={`${p.latitude},${p.longitude},${i}`}
                pane="faPoints"
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
        </Paneller>

        <FitAll points={points} lines={lines} />
      </MapContainer>

      {/* Sebeke duruyor ama uzerinde ariza yok. Haritayi hic cizmemektense
          bunu SOYLEMEK dogru: "arama alani bu, burada ariza olmamis". */}
      {!arizaVar && lines.length ? (
        <span className="fa-heat-note">
          <Waypoints size={14} />
          {t("faultAnalytics.heatOnlyNetwork")}
        </span>
      ) : null}

      {/* Lejant olmadan renk bir sey ifade etmez: "kirmizi cok mu, biraz mi?" */}
      <div className="fa-heat-legend">
        {arizaVar ? (
          <>
            <span>{t("faultAnalytics.heatLow")}</span>
            <i className="fa-heat-ramp" />
            <span>{t("faultAnalytics.heatHigh", { count: enCok })}</span>
          </>
        ) : null}
        {lines.length ? (
          <span className="fa-heat-key">
            <i />
            {t("faultAnalytics.heatNetworkKey")}
          </span>
        ) : null}
      </div>
    </div>
  );
}
