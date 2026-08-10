/**
 * FaultPoleStrip — hattin YANDAN GORUNUSU ("tahmini ariza bolgesi").
 *
 * Cografi harita DEGIL (o is FaultDetailModal'daki Leaflet mini haritada);
 * buradaki amac tek bakista su uc soruyu cevaplamak:
 *   1. Ariza hangi direkler arasinda?
 *   2. Hangi cihaz "gordum", hangisi "gormedim" dedi?
 *   3. Ariza tam olarak hangi TEL parcasinda?
 *
 * ONCEKI SURUMDEN FARKI
 * ---------------------
 * Direkler diziliyor ama aralarina DUZ bir taban cizgisi cekiliyordu ve
 * cihazlar cizimde HIC YOKTU (kartin altinda ayri kutulardaydi). Yani
 * "ariza su iki cihaz arasinda" bilgisi metinle anlatiliyor, gorselde
 * gorunmuyordu. Simdi gercek bir havai hat kesiti var:
 *
 *   * Direkler travers + izolatorlu siluet.
 *   * Iletkenler direkler arasinda SARKARAK gecer (katener) — duz cizgi
 *     degil; "tel" okumasini aninda veriyor.
 *   * Cihazlar TELIN UZERINDE, segmentteki gercek konumlarinda
 *     (`device_position_t`).
 *   * ARIZALI PARCA: son "gordum" diyen cihaz ile ilk "gormedim" diyen
 *     cihaz arasindaki tel kirmizi — ariza fiziksel olarak tam orada.
 *
 * Geometri `faultStripGeometry.ts`te ve SAF: React'siz test edilebiliyor
 * (bkz. tests/faultStrip.test.ts). Burada yalnizca cizim var.
 */
import { useMemo } from "react";
import { useTranslation } from "react-i18next";

import {
  CROSSARM_Y,
  GROUND_Y,
  LABEL_Y,
  STRIP_H,
  WIRE_Y,
  buildStripGeometry,
  hotPathOf,
  toPath
} from "./faultStripGeometry";
import type { StripSegment } from "./faultStripGeometry";

export type { StripSegment };

type Props = {
  /** Hattin tum direk sira numaralari (artan). */
  poleSeqs: number[];
  /** Hattin segmentleri — cihazlar buradan gelir. Bos ise cihaz cizilmez. */
  segments?: StripSegment[];
  /** Arizali aralik uclari (dahil) — cihaz bilgisi yoksa yedek. */
  fromSeq: number | null | undefined;
  toSeq: number | null | undefined;
  /** Son "ariza gordum" diyen cihaz — arizanin YUKARI siniri. */
  lastRedDeviceCode?: string | null;
  /** Ilk "gormedim" diyen cihaz — arizanin ASAGI siniri. Yoksa hat ucu. */
  firstGreenDeviceCode?: string | null;
  /** Ariza hala aktif mi — pasifse gri/soluk cizilir. */
  active?: boolean;
};

const GREY = "#94a3b8";
const WIRE_GREY = "#cbd5e1";
const RED = "#dc2626";
const GREEN = "#16a34a";

type Pt = { x: number; y: number };

/** Bir direk — travers, izolatorler ve kafes ayaklar. */
function Tower({ x, hot }: { x: number; hot: boolean }) {
  const stroke = hot ? RED : GREY;
  const arm = 11;
  const spread = 8;
  return (
    <g stroke={stroke} strokeWidth={1.7} strokeLinecap="round" fill="none">
      <line x1={x} y1={CROSSARM_Y} x2={x} y2={GROUND_Y} />
      <line x1={x - arm} y1={CROSSARM_Y} x2={x + arm} y2={CROSSARM_Y} />
      {/* izolatorler */}
      <line x1={x - arm} y1={CROSSARM_Y} x2={x - arm} y2={WIRE_Y - 4} />
      <line x1={x + arm} y1={CROSSARM_Y} x2={x + arm} y2={WIRE_Y - 4} />
      <line x1={x} y1={CROSSARM_Y} x2={x} y2={WIRE_Y - 4} />
      {/* kafes ayaklar */}
      <line x1={x} y1={GROUND_Y - 22} x2={x - spread} y2={GROUND_Y} />
      <line x1={x} y1={GROUND_Y - 22} x2={x + spread} y2={GROUND_Y} />
      <line
        x1={x - spread * 0.55}
        y1={GROUND_Y - 11}
        x2={x + spread * 0.55}
        y2={GROUND_Y - 11}
      />
    </g>
  );
}

/** Iletken uzerindeki ariza gecis gostergesi (kelepce + govde). */
function DeviceMark({ p, tone, label }: { p: Pt; tone: "red" | "green" | "idle"; label: string }) {
  const color = tone === "red" ? RED : tone === "green" ? GREEN : "#64748b";
  return (
    <g transform={`translate(${p.x}, ${p.y})`}>
      <title>{label}</title>
      {/* kelepce — telin uzerine oturur */}
      <rect x={-3.6} y={-4.4} width={7.2} height={3.2} rx={1.1} fill={color} opacity={0.9} />
      {/* govde */}
      <rect
        x={-4.6}
        y={-1.4}
        width={9.2}
        height={8.6}
        rx={2.2}
        fill="#fff"
        stroke={color}
        strokeWidth={1.7}
      />
      <circle cx={0} cy={2.9} r={1.9} fill={color} />
    </g>
  );
}

export function FaultPoleStrip({
  poleSeqs,
  segments,
  fromSeq,
  toSeq,
  lastRedDeviceCode,
  firstGreenDeviceCode,
  active = true
}: Props) {
  const { t } = useTranslation();

  const geo = useMemo(
    () =>
      buildStripGeometry({
        poleSeqs,
        segments,
        fromSeq,
        toSeq,
        lastRedDeviceCode,
        firstGreenDeviceCode
      }),
    [poleSeqs, segments, fromSeq, toSeq, lastRedDeviceCode, firstGreenDeviceCode]
  );

  const { seqs, width, wire, devices, span, xOf, pointAt } = geo;
  const faultColor = active ? RED : GREY;
  const hotFrom = span ? Math.floor(span.a) : -1;
  const hotTo = span ? Math.ceil(span.b) : -1;
  const hotPath = hotPathOf(geo);
  const midPoint = span ? pointAt((span.a + span.b) / 2) : null;

  return (
    <svg
      className="fx-strip"
      viewBox={`0 0 ${width} ${STRIP_H}`}
      preserveAspectRatio="xMidYMid meet"
      role="img"
      aria-label={
        span
          ? t("faults.poleStrip.range", { from: fromSeq ?? "?", to: toSeq ?? "?" })
          : t("faults.poleStrip.unknown")
      }
    >
      <defs>
        <filter id="fx-wire-glow" x="-30%" y="-200%" width="160%" height="500%">
          <feGaussianBlur stdDeviation="3" />
        </filter>
      </defs>

      {/* zemin */}
      <line
        x1={4}
        y1={GROUND_Y}
        x2={width - 4}
        y2={GROUND_Y}
        stroke="#e2e8f0"
        strokeWidth={1.4}
        strokeDasharray="3 4"
      />

      {/* Ust iki iletken — derinlik hissi. Cihaz ve ariza rengi TASIMAZ. */}
      {[-7, -3.5].map((dy) => (
        <path
          key={dy}
          d={toPath(wire.map((p) => ({ x: p.x, y: p.y + dy })))}
          fill="none"
          stroke={WIRE_GREY}
          strokeWidth={1.2}
          strokeLinecap="round"
          opacity={0.55}
        />
      ))}

      {/* Ana iletken */}
      <path
        d={toPath(wire)}
        fill="none"
        stroke={WIRE_GREY}
        strokeWidth={2.4}
        strokeLinecap="round"
      />

      {/* ARIZALI PARCA */}
      {hotPath ? (
        <>
          <path
            d={hotPath}
            fill="none"
            stroke={faultColor}
            strokeWidth={6}
            strokeLinecap="round"
            filter="url(#fx-wire-glow)"
            opacity={active ? 0.5 : 0.2}
          />
          <path
            d={hotPath}
            fill="none"
            stroke={faultColor}
            strokeWidth={3.4}
            strokeLinecap="round"
          />
        </>
      ) : null}

      {/* Direkler */}
      {seqs.map((seq, idx) => (
        <Tower key={seq} x={xOf(idx)} hot={active && idx >= hotFrom && idx <= hotTo} />
      ))}

      {/* Cihazlar — telin uzerinde */}
      {devices.map((d) => (
        <DeviceMark
          key={d.code}
          p={pointAt(d.pos)}
          tone={active ? d.tone : "idle"}
          label={d.label}
        />
      ))}

      {/* Ariza pini — kirmizi parcanin ortasi */}
      {midPoint ? (
        <g
          transform={`translate(${midPoint.x}, ${midPoint.y - 15})`}
          className={active ? "fx-strip-pin is-active" : "fx-strip-pin"}
        >
          <path
            d="M0 11 C0 11 7.6 3.2 7.6 -2 A7.6 7.6 0 1 0 -7.6 -2 C-7.6 3.2 0 11 0 11 Z"
            fill={faultColor}
          />
          <circle cx={0} cy={-2.3} r={2.7} fill="#fff" />
        </g>
      ) : null}

      {/* Direk numaralari */}
      {seqs.map((seq, idx) => {
        const hot = active && idx >= hotFrom && idx <= hotTo;
        return (
          <text
            key={`l-${seq}`}
            x={xOf(idx)}
            y={LABEL_Y}
            textAnchor="middle"
            fontSize={10.5}
            fontWeight={hot ? 700 : 500}
            fill={hot ? "#b91c1c" : GREY}
          >
            {seq}
          </text>
        );
      })}
    </svg>
  );
}
