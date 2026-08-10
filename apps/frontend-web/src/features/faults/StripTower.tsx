/**
 * Kafes direk silueti — sahnenin temel yapi tasi.
 *
 * NEDEN BU KADAR DETAY: onceki cizim tek dikey cizgi + duz bir traversti;
 * "sema" gibi degil "taslak" gibi duruyordu. Gercek bir havai hat direginin
 * okunur olmasi icin uc sey sart: ASAGI ACILAN kafes govde, CAPRAZ dolgular
 * ve travers uclarindaki IZOLATOR zinciri. Ucu birlikte, sahaya cikan ekibin
 * her gun gordugu siluete karsilik gelir.
 *
 * OLCEKLE DETAY: cizim yakinlastirilabildigi icin ince cizgiler (kafes
 * caprazlari) ayri bir katmanda ve daha ince; uzaklasinca gri bir doku gibi
 * okunur, yakinlasinca yapi netlesir.
 */
import {
  ARM_HALF,
  GROUND_Y,
  MAIN_ARM_Y,
  PEAK_Y,
  TOP_ARM_HALF,
  TOP_ARM_Y,
  WIRE_Y
} from "./faultStripGeometry";

type Props = {
  x: number;
  /** Ariza bolgesi icinde mi — kirmizi cizilir. */
  hot: boolean;
  /** `branch` ise traversten cikan dal kolu isaretlenir. */
  role?: string | null;
  onEnter: () => void;
  onLeave: () => void;
};

const GREY = "#8b98a9";
const RED = "#dc2626";

/** Govde yari genisligi — tepede dar, zeminde genis (asagi acilir). */
const UST_YARI = 5;
const ALT_YARI = 15;

/** Verilen yukseklikte govde yari genisligi (dogrusal acilim). */
function yariGenislik(y: number): number {
  const t = Math.max(0, Math.min(1, (y - PEAK_Y) / (GROUND_Y - PEAK_Y)));
  return UST_YARI + (ALT_YARI - UST_YARI) * t;
}

/** Izolator zinciri — traversten iletkene inen kisa boncuklu ask. */
function Izolator({ x, y1, y2, renk }: { x: number; y1: number; y2: number; renk: string }) {
  const orta = (y1 + y2) / 2;
  return (
    <g stroke={renk} strokeWidth={1.1} strokeLinecap="round">
      <line x1={x} y1={y1} x2={x} y2={y2} />
      {/* Boncuklar: izolator zincirini tek cizgiden ayiran detay. */}
      <line x1={x - 1.8} y1={orta - 1.6} x2={x + 1.8} y2={orta - 1.6} />
      <line x1={x - 1.8} y1={orta + 1.6} x2={x + 1.8} y2={orta + 1.6} />
    </g>
  );
}

export function StripTower({ x, hot, role, onEnter, onLeave }: Props) {
  const renk = hot ? RED : GREY;
  const kalinlik = hot ? 1.9 : 1.5;

  // Kafes caprazlari: govde boyunca X dolgular. Adim sayisi sabit; zoom'da
  // detay artmasin diye geometri olcekten BAGIMSIZ.
  const capraz: { x1: number; y1: number; x2: number; y2: number }[] = [];
  const adim = 14;
  for (let y = MAIN_ARM_Y + 6; y < GROUND_Y - adim; y += adim) {
    const w1 = yariGenislik(y);
    const w2 = yariGenislik(y + adim);
    capraz.push({ x1: x - w1, y1: y, x2: x + w2, y2: y + adim });
    capraz.push({ x1: x + w1, y1: y, x2: x - w2, y2: y + adim });
  }

  const wAlt = yariGenislik(GROUND_Y);
  const wArm = yariGenislik(MAIN_ARM_Y);
  const wTop = yariGenislik(TOP_ARM_Y);
  const wPeak = yariGenislik(PEAK_Y);

  return (
    <g
      className="fx-strip-pole"
      onMouseEnter={onEnter}
      onMouseLeave={onLeave}
      onFocus={onEnter}
      onBlur={onLeave}
      tabIndex={0}
    >
      {/* Imlec hedefi — ince cizgilere nisan almak zor. */}
      <rect
        x={x - ARM_HALF - 4}
        y={PEAK_Y - 8}
        width={(ARM_HALF + 4) * 2}
        height={GROUND_Y - PEAK_Y + 26}
        fill="transparent"
      />

      {/* Zemin golgesi — cismi yuzeye oturtur. */}
      <ellipse cx={x} cy={GROUND_Y + 2} rx={wAlt + 5} ry={2.8} fill="#0f172a" opacity={0.11} />

      {/* Kafes caprazlari — ince, geri planda. */}
      <g stroke={renk} strokeWidth={0.75} opacity={0.55}>
        {capraz.map((c, i) => (
          <line key={i} x1={c.x1} y1={c.y1} x2={c.x2} y2={c.y2} />
        ))}
      </g>

      <g stroke={renk} strokeWidth={kalinlik} strokeLinecap="round" fill="none">
        {/* Asagi acilan iki ana ayak. */}
        <line x1={x - wPeak} y1={PEAK_Y} x2={x - wAlt} y2={GROUND_Y} />
        <line x1={x + wPeak} y1={PEAK_Y} x2={x + wAlt} y2={GROUND_Y} />
        {/* Yatay kusaklar — kafesin katlari. */}
        <line x1={x - wTop} y1={TOP_ARM_Y} x2={x + wTop} y2={TOP_ARM_Y} />
        <line x1={x - wArm} y1={MAIN_ARM_Y} x2={x + wArm} y2={MAIN_ARM_Y} />
        <line
          x1={x - yariGenislik(GROUND_Y - 34)}
          y1={GROUND_Y - 34}
          x2={x + yariGenislik(GROUND_Y - 34)}
          y2={GROUND_Y - 34}
        />
        {/* Ayak tabanlari. */}
        <line x1={x - wAlt - 3} y1={GROUND_Y} x2={x - wAlt + 3} y2={GROUND_Y} />
        <line x1={x + wAlt - 3} y1={GROUND_Y} x2={x + wAlt + 3} y2={GROUND_Y} />

        {/* TEPE: toprak teli tasiyicisi. */}
        <line x1={x} y1={PEAK_Y - 7} x2={x} y2={PEAK_Y} />
        <line x1={x - wPeak} y1={PEAK_Y} x2={x + wPeak} y2={PEAK_Y} />

        {/* UST TRAVERS (toprak teli). */}
        <line x1={x - TOP_ARM_HALF} y1={TOP_ARM_Y} x2={x + TOP_ARM_HALF} y2={TOP_ARM_Y} />
        {/* ANA TRAVERS (uc faz). Uclarda hafif yukari donus — gercek
            traverslerdeki takviye. */}
        <line x1={x - ARM_HALF} y1={MAIN_ARM_Y} x2={x + ARM_HALF} y2={MAIN_ARM_Y} />
        <line x1={x - ARM_HALF} y1={MAIN_ARM_Y} x2={x - ARM_HALF} y2={MAIN_ARM_Y - 3.5} />
        <line x1={x + ARM_HALF} y1={MAIN_ARM_Y} x2={x + ARM_HALF} y2={MAIN_ARM_Y - 3.5} />
        {/* Travers takviye caprazlari. */}
        <line x1={x - ARM_HALF} y1={MAIN_ARM_Y} x2={x - wArm} y2={MAIN_ARM_Y - 12} />
        <line x1={x + ARM_HALF} y1={MAIN_ARM_Y} x2={x + wArm} y2={MAIN_ARM_Y - 12} />
      </g>

      {/* Izolator zincirleri — uc faz ana traversin sol/orta/sag noktasinda. */}
      <Izolator x={x - ARM_HALF} y1={MAIN_ARM_Y} y2={WIRE_Y} renk={renk} />
      <Izolator x={x} y1={MAIN_ARM_Y} y2={WIRE_Y} renk={renk} />
      <Izolator x={x + ARM_HALF} y1={MAIN_ARM_Y} y2={WIRE_Y} renk={renk} />

      {/* BRANSMAN: traversten ayrilan kol — dal buradan CAPRAZ iner. */}
      {role === "branch" ? (
        <g stroke="#7c3aed" strokeWidth={2} strokeLinecap="round" fill="none">
          <line x1={x} y1={MAIN_ARM_Y - 3} x2={x} y2={TOP_ARM_Y + 4} />
          <circle cx={x} cy={TOP_ARM_Y + 2} r={2.2} fill="#7c3aed" stroke="none" />
        </g>
      ) : null}
    </g>
  );
}
