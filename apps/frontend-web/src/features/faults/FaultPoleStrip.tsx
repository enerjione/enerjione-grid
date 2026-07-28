/**
 * FaultPoleStrip — "Tahmini ariza bolgesi" sematik direk seridi.
 *
 * Hattin direklerini soldan saga esit arayla dizer, arizali araligi kirmizi
 * vurgular ve araligin ortasina bir konum pini koyar. Cografi harita DEGIL
 * (o is FaultDetailModal'daki Leaflet mini haritada); buradaki amac "kacinci
 * direkler arasi?" sorusunu bir bakista cevaplamak.
 *
 * Direk listesi gridSnapshot'tan gelir. Snapshot yoksa yalnizca ariza
 * araligindaki direkler cizilir (degraded ama yine anlamli).
 */
import { useMemo } from "react";

type Props = {
  /** Hattin tum direk sira numaralari (artan). */
  poleSeqs: number[];
  /** Arizali aralik uclari (dahil). */
  fromSeq: number | null | undefined;
  toSeq: number | null | undefined;
  /** Ariza hala aktif mi — pasifse gri/soluk cizilir. */
  active?: boolean;
};

/** Cizim sabitleri (viewBox koordinati). */
const H = 96;
const PAD_X = 26;
const TOWER_TOP = 26;
const BASE_Y = 66;
const LABEL_Y = 86;

/** Tek bir direk (iletim kulesi) glyph'i — iki traversli sade siluet. */
function Tower({ x, highlight }: { x: number; highlight: boolean }) {
  const stroke = highlight ? "#dc2626" : "#94a3b8";
  const w = 7; // yarim genislik (gövde ayak açıklığı)
  return (
    <g stroke={stroke} strokeWidth={1.6} strokeLinecap="round" fill="none">
      {/* gövde */}
      <line x1={x} y1={TOWER_TOP} x2={x} y2={BASE_Y} />
      {/* üst travers */}
      <line x1={x - w} y1={TOWER_TOP + 4} x2={x + w} y2={TOWER_TOP + 4} />
      {/* alt travers */}
      <line x1={x - w + 1.5} y1={TOWER_TOP + 12} x2={x + w - 1.5} y2={TOWER_TOP + 12} />
      {/* ayaklar */}
      <line x1={x} y1={BASE_Y - 10} x2={x - w + 1} y2={BASE_Y} />
      <line x1={x} y1={BASE_Y - 10} x2={x + w - 1} y2={BASE_Y} />
    </g>
  );
}

export function FaultPoleStrip({ poleSeqs, fromSeq, toSeq, active = true }: Props) {
  const seqs = useMemo(() => {
    const uniq = Array.from(new Set(poleSeqs)).sort((a, b) => a - b);
    if (uniq.length >= 2) return uniq;
    // Snapshot yok/eksik — en azindan ariza araligini ciz.
    const a = fromSeq ?? 1;
    const b = toSeq ?? a + 1;
    return a === b ? [a, a + 1] : [Math.min(a, b), Math.max(a, b)];
  }, [poleSeqs, fromSeq, toSeq]);

  const count = seqs.length;
  // Genislik direk sayisina gore buyur; CSS tarafinda %100'e olceklenir.
  const width = Math.max(220, PAD_X * 2 + (count - 1) * 62);
  const step = count > 1 ? (width - PAD_X * 2) / (count - 1) : 0;
  const xOf = (idx: number) => PAD_X + idx * step;

  const lo = fromSeq != null && toSeq != null ? Math.min(fromSeq, toSeq) : null;
  const hi = fromSeq != null && toSeq != null ? Math.max(fromSeq, toSeq) : null;
  const fromIdx = lo != null ? seqs.indexOf(lo) : -1;
  const toIdx = hi != null ? seqs.indexOf(hi) : -1;
  const hasSpan = fromIdx !== -1 && toIdx !== -1 && toIdx > fromIdx;

  const spanX1 = hasSpan ? xOf(fromIdx) : 0;
  const spanX2 = hasSpan ? xOf(toIdx) : 0;
  const midX = (spanX1 + spanX2) / 2;

  const faultColor = active ? "#dc2626" : "#94a3b8";
  const gid = `fx-strip-glow-${lo ?? 0}-${hi ?? 0}-${active ? "a" : "p"}`;

  return (
    <svg
      className="fx-strip"
      viewBox={`0 0 ${width} ${H}`}
      preserveAspectRatio="xMidYMid meet"
      role="img"
      aria-label={
        hasSpan
          ? `Arıza aralığı: direk ${lo} ile ${hi} arası`
          : "Arıza aralığı belirlenemedi"
      }
    >
      <defs>
        <filter id={gid} x="-40%" y="-300%" width="180%" height="700%">
          <feGaussianBlur stdDeviation="3.5" />
        </filter>
      </defs>

      {/* Taban iletkeni — tum hat */}
      <line
        x1={xOf(0)}
        y1={BASE_Y}
        x2={xOf(count - 1)}
        y2={BASE_Y}
        stroke="#cbd5e1"
        strokeWidth={2.5}
        strokeLinecap="round"
      />

      {/* Arizali aralik — once glow, sonra net cizgi */}
      {hasSpan ? (
        <>
          <line
            x1={spanX1}
            y1={BASE_Y}
            x2={spanX2}
            y2={BASE_Y}
            stroke={faultColor}
            strokeWidth={5}
            strokeLinecap="round"
            filter={`url(#${gid})`}
            opacity={active ? 0.55 : 0.25}
          />
          <line
            x1={spanX1}
            y1={BASE_Y}
            x2={spanX2}
            y2={BASE_Y}
            stroke={faultColor}
            strokeWidth={4}
            strokeLinecap="round"
          />
        </>
      ) : null}

      {/* Direkler */}
      {seqs.map((seq, idx) => {
        const inSpan = lo != null && hi != null && seq >= lo && seq <= hi;
        return <Tower key={seq} x={xOf(idx)} highlight={Boolean(inSpan) && active} />;
      })}

      {/* Konum pini — arizali araligin ortasi */}
      {hasSpan ? (
        <g
          transform={`translate(${midX}, ${BASE_Y - 30})`}
          className={active ? "fx-strip-pin is-active" : "fx-strip-pin"}
        >
          <path
            d="M0 12 C0 12 8.5 3.6 8.5 -2.2 A8.5 8.5 0 1 0 -8.5 -2.2 C-8.5 3.6 0 12 0 12 Z"
            fill={faultColor}
          />
          <circle cx={0} cy={-2.6} r={3} fill="#fff" />
        </g>
      ) : null}

      {/* Direk numaralari */}
      {seqs.map((seq, idx) => {
        const inSpan = lo != null && hi != null && seq >= lo && seq <= hi;
        return (
          <text
            key={`l-${seq}`}
            x={xOf(idx)}
            y={LABEL_Y}
            textAnchor="middle"
            fontSize={10.5}
            fontWeight={inSpan ? 700 : 500}
            fill={inSpan && active ? "#b91c1c" : "#94a3b8"}
          >
            {seq}
          </text>
        );
      })}
    </svg>
  );
}
