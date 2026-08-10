/**
 * FaultPoleStrip — hattin YANDAN GORUNUSU ("tahmini ariza bolgesi").
 *
 * Cografi harita DEGIL (o is FaultDetailModal'daki Leaflet mini haritada);
 * buradaki amac tek bakista su dort soruyu cevaplamak:
 *   1. Ariza hangi direkler arasinda?
 *   2. Hangi cihaz "gordum", hangisi "gormedim" dedi?
 *   3. Ariza tam olarak hangi TEL parcasinda ve hat basindan KAC METREDE?
 *   4. Hangi FAZ arizali? (master + sat01 + sat02 ayri fazlara takilir)
 *
 * TASARIM DILI: elektrik tek hat semasi / teknik resim.
 * Kot cizgileri, ok uclu olcu (dimension) serit, tabular rakamlar. Dekoratif
 * kutucuk ve pastel dolgu YOK — cizim bir mühendislik belgesi gibi okunmali.
 *
 * ONCEKI SURUMDEN FARKLAR
 * -----------------------
 *   * SPAN genisligi 78 -> 116: direkler birbirine girmiyor, hat "uzun"
 *     okunuyor (kullanici talebi).
 *   * MESAFE artik yan kutuda degil CIZIMIN UZERINDE: arizali parcanin
 *     altinda ok uclu olcu seridi + hat basindan kot degerleri.
 *   * Cihaz isareti uc FAZ noktasi tasiyor; alarm gelen faz kirmizi yanar,
 *     boylece "hangi fazda ariza var" cizimde gorunur.
 *   * Cihaz uzerine gelince zengin TOOLTIP (rol, faz, konum, mesafe).
 *   * ARIZA PINI KALDIRILDI. Pin `transform` ATTRIBUTE'u ile konumlanip
 *     `fx-strip-pin` sinifindaki CSS animasyonu `transform` OZELLIGINI
 *     yaziyordu; CSS ozelligi presentation attribute'unu ezdigi icin pin
 *     her render'da (0,0)'a — yani cizimin SOL UST kosesine — sicriyordu.
 *     Ekranda "ne oldugu anlasilmayan kirmizi leke" tam olarak buydu.
 *     Arizanin yeri zaten kirmizi tel + olcu seridiyle isaretli.
 */
import { useMemo, useState } from "react";
import { useTranslation } from "react-i18next";

import {
  CROSSARM_Y,
  DIM_LABEL_Y,
  DIM_Y,
  GROUND_Y,
  LABEL_Y,
  STRIP_H,
  WIRE_Y,
  buildStripGeometry,
  hotPathOf,
  toPath
} from "./faultStripGeometry";
import type { StripSegment } from "./faultStripGeometry";
import { formatDistanceM } from "../../shared/lineDistance";

export type { StripSegment };

/** Cihaz basina, o cihazda ACIK olan alarmlarin faz kaynaklari. */
export type StripDeviceAlarms = {
  /** "master" | "sat01" | "sat02" — alarmi olan kaynaklar. */
  sources: string[];
  /** Alarm basliklari (tooltip'te ilk ikisi gosterilir). */
  titles: string[];
};

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
  /** Hat basindan arizanin yakin/uzak sinirina tel mesafesi (metre). */
  zoneStartM?: number | null;
  zoneEndM?: number | null;
  /** Cihaz kodu -> o cihazdaki acik alarmlarin faz bilgisi. */
  alarmsByDevice?: Record<string, StripDeviceAlarms>;
};

const INK = "#334155";
const GREY = "#94a3b8";
const WIRE_GREY = "#cbd5e1";
const RED = "#dc2626";
const GREEN = "#16a34a";
const DIM_INK = "#b45309";

/** Faz sirasi — bir SN2 govdesindeki uc sensor, hattin uc fazi. */
const PHASES = ["master", "sat01", "sat02"] as const;

type Pt = { x: number; y: number };

/** Bir direk — travers, izolatorler ve kafes ayaklar. */
function Tower({ x, hot }: { x: number; hot: boolean }) {
  const stroke = hot ? RED : GREY;
  const arm = 13;
  const spread = 9;
  return (
    <g stroke={stroke} strokeWidth={hot ? 2 : 1.7} strokeLinecap="round" fill="none">
      <line x1={x} y1={CROSSARM_Y} x2={x} y2={GROUND_Y} />
      <line x1={x - arm} y1={CROSSARM_Y} x2={x + arm} y2={CROSSARM_Y} />
      {/* izolatorler */}
      <line x1={x - arm} y1={CROSSARM_Y} x2={x - arm} y2={WIRE_Y - 5} />
      <line x1={x + arm} y1={CROSSARM_Y} x2={x + arm} y2={WIRE_Y - 5} />
      <line x1={x} y1={CROSSARM_Y} x2={x} y2={WIRE_Y - 5} />
      {/* kafes ayaklar */}
      <line x1={x} y1={GROUND_Y - 24} x2={x - spread} y2={GROUND_Y} />
      <line x1={x} y1={GROUND_Y - 24} x2={x + spread} y2={GROUND_Y} />
      <line
        x1={x - spread * 0.55}
        y1={GROUND_Y - 12}
        x2={x + spread * 0.55}
        y2={GROUND_Y - 12}
      />
    </g>
  );
}

/**
 * Iletken uzerindeki ariza gecis gostergesi.
 *
 * Govdenin altinda UC FAZ NOKTASI var (master / sat01 / sat02 sirasiyla).
 * Alarm gelen faz dolu kirmizi, digerleri bos. Boylece "hangi fazda ariza
 * var" sorusu cizimden okunuyor — kullanici alarm listesine gitmeden.
 */
function DeviceMark({
  p,
  tone,
  alarmSources,
  dim,
  onEnter,
  onLeave
}: {
  p: Pt;
  tone: "red" | "green" | "idle";
  alarmSources: string[];
  dim: boolean;
  onEnter: () => void;
  onLeave: () => void;
}) {
  const color = tone === "red" ? RED : tone === "green" ? GREEN : "#64748b";
  return (
    <g
      transform={`translate(${p.x}, ${p.y})`}
      opacity={dim ? 0.45 : 1}
      className="fx-strip-dev"
      onMouseEnter={onEnter}
      onMouseLeave={onLeave}
      onFocus={onEnter}
      onBlur={onLeave}
      tabIndex={0}
    >
      {/* imlec hedefi — ince cizime nisan almak zor */}
      <rect x={-11} y={-11} width={22} height={30} fill="transparent" />
      {/* kelepce — telin uzerine oturur */}
      <rect x={-4.2} y={-5} width={8.4} height={3.6} rx={1.2} fill={color} />
      {/* govde */}
      <rect
        x={-5.4}
        y={-1.6}
        width={10.8}
        height={10}
        rx={2.4}
        fill="#fff"
        stroke={color}
        strokeWidth={1.9}
      />
      {/* faz noktalari: master / sat01 / sat02 */}
      {PHASES.map((ph, i) => {
        const on = alarmSources.includes(ph);
        return (
          <circle
            key={ph}
            cx={-2.7 + i * 2.7}
            cy={3.4}
            r={1.15}
            fill={on ? RED : "#fff"}
            stroke={on ? RED : "#cbd5e1"}
            strokeWidth={0.8}
          />
        );
      })}
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
  active = true,
  zoneStartM,
  zoneEndM,
  alarmsByDevice
}: Props) {
  const { t } = useTranslation();
  const [hover, setHover] = useState<string | null>(null);

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

  // Olcu seridi yalnizca arizali parcanin altinda cizilir.
  const dimA = span ? pointAt(span.a).x : null;
  const dimB = span ? pointAt(span.b).x : null;
  const spanM =
    zoneStartM != null && zoneEndM != null ? Math.max(0, zoneEndM - zoneStartM) : null;

  const hovered = hover ? devices.find((d) => d.code === hover) ?? null : null;
  const hoveredAlarms = hovered ? alarmsByDevice?.[hovered.code] : undefined;
  const hoveredPt = hovered ? pointAt(hovered.pos) : null;

  return (
    <div className="fx-strip-wrap">
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
            <feGaussianBlur stdDeviation="3.2" />
          </filter>
          {/* Arizali bolgenin arka plan taramasi — teknik resimdeki
              "kesit" tarama dokusu. Dolgu degil doku: cizimi bogmuyor. */}
          <pattern
            id="fx-hatch"
            width="7"
            height="7"
            patternUnits="userSpaceOnUse"
            patternTransform="rotate(45)"
          >
            <line x1="0" y1="0" x2="0" y2="7" stroke={RED} strokeWidth="1" opacity="0.14" />
          </pattern>
        </defs>

        {/* Arizali bolge sutunu — direkler ve tel bunun uzerine biner */}
        {dimA != null && dimB != null && active ? (
          <rect
            x={dimA}
            y={CROSSARM_Y - 10}
            width={Math.max(0, dimB - dimA)}
            height={GROUND_Y - CROSSARM_Y + 10}
            fill="url(#fx-hatch)"
          />
        ) : null}

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
        {[-8, -4].map((dy) => (
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
              strokeWidth={7}
              strokeLinecap="round"
              filter="url(#fx-wire-glow)"
              opacity={active ? 0.45 : 0.18}
            />
            <path
              d={hotPath}
              fill="none"
              stroke={faultColor}
              strokeWidth={3.6}
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
            alarmSources={active ? alarmsByDevice?.[d.code]?.sources ?? [] : []}
            dim={hover != null && hover !== d.code}
            onEnter={() => setHover(d.code)}
            onLeave={() => setHover(null)}
          />
        ))}

        {/* Direk numaralari */}
        {seqs.map((seq, idx) => {
          const hot = active && idx >= hotFrom && idx <= hotTo;
          return (
            <text
              key={`l-${seq}`}
              x={xOf(idx)}
              y={LABEL_Y}
              textAnchor="middle"
              fontSize={11}
              fontWeight={hot ? 700 : 500}
              fill={hot ? "#b91c1c" : GREY}
            >
              {seq}
            </text>
          );
        })}

        {/* ---- OLCU SERIDI: arizanin hat basindan mesafesi ---- */}
        {dimA != null && dimB != null && zoneStartM != null && zoneEndM != null ? (
          <g className="fx-strip-dim" stroke={DIM_INK} fill={DIM_INK}>
            {/* uzanti cizgileri (extension lines) */}
            <line x1={dimA} y1={LABEL_Y + 6} x2={dimA} y2={DIM_Y + 6} strokeWidth={1} opacity={0.5} />
            <line x1={dimB} y1={LABEL_Y + 6} x2={dimB} y2={DIM_Y + 6} strokeWidth={1} opacity={0.5} />
            {/* olcu cizgisi + ok uclari */}
            <line x1={dimA} y1={DIM_Y} x2={dimB} y2={DIM_Y} strokeWidth={1.3} />
            <path d={`M${dimA} ${DIM_Y} l6 -3 v6 z`} stroke="none" />
            <path d={`M${dimB} ${DIM_Y} l-6 -3 v6 z`} stroke="none" />
            {/* uc kot degerleri */}
            <text
              x={dimA}
              y={DIM_Y - 7}
              textAnchor="middle"
              fontSize={10}
              fontWeight={600}
              stroke="none"
              opacity={0.85}
            >
              {formatDistanceM(zoneStartM)}
            </text>
            <text
              x={dimB}
              y={DIM_Y - 7}
              textAnchor="middle"
              fontSize={10}
              fontWeight={600}
              stroke="none"
              opacity={0.85}
            >
              {formatDistanceM(zoneEndM)}
            </text>
            {/* aralik genisligi — asil aranan sayi */}
            {spanM != null ? (
              <text
                x={(dimA + dimB) / 2}
                y={DIM_LABEL_Y}
                textAnchor="middle"
                fontSize={12.5}
                fontWeight={800}
                stroke="none"
              >
                {t("faults.poleStrip.spanLabel", { span: formatDistanceM(spanM) })}
              </text>
            ) : null}
          </g>
        ) : null}

        {/* Olcu yoksa en azindan aralik etiketi — cizim bos kalmasin */}
        {(dimA == null || zoneStartM == null) && span ? (
          <text
            x={width / 2}
            y={DIM_LABEL_Y - 8}
            textAnchor="middle"
            fontSize={11.5}
            fontWeight={600}
            fill={INK}
            opacity={0.6}
          >
            {t("faults.poleStrip.range", { from: fromSeq ?? "?", to: toSeq ?? "?" })}
          </text>
        ) : null}
      </svg>

      {/* ---- Cihaz tooltip'i ----
          SVG <title> yerine HTML: gecikmesiz acilir, bicimlendirilebilir ve
          faz/rol bilgisini satirlara ayirabiliyoruz. Konum yuzdeye
          cevriliyor cunku SVG olceklenerek cizilyor. */}
      {hovered && hoveredPt ? (
        <div
          className="fx-strip-tip"
          style={{
            left: `${(hoveredPt.x / width) * 100}%`,
            top: `${(hoveredPt.y / STRIP_H) * 100}%`
          }}
          role="tooltip"
        >
          <div className="fx-strip-tip-name">{hovered.label}</div>
          {hovered.code !== hovered.label ? (
            <code className="fx-strip-tip-code">{hovered.code}</code>
          ) : null}
          <div className={`fx-strip-tip-role fx-strip-tip-role--${hovered.tone}`}>
            {t(`faults.poleStrip.role.${hovered.tone}`)}
          </div>
          <div className="fx-strip-tip-row">
            {t("faults.poleStrip.between", { from: hovered.fromSeq, to: hovered.toSeq })}
          </div>
          {hovered.tone === "red" && zoneStartM != null ? (
            <div className="fx-strip-tip-row">
              {t("faults.poleStrip.atDistance", { d: formatDistanceM(zoneStartM) })}
            </div>
          ) : null}
          {hoveredAlarms && hoveredAlarms.sources.length > 0 ? (
            <div className="fx-strip-tip-phases">
              {PHASES.map((ph) => (
                <span
                  key={ph}
                  className={`fx-phase-chip${
                    hoveredAlarms.sources.includes(ph) ? " is-on" : ""
                  }`}
                >
                  {t(`faults.phase.${ph}`)}
                </span>
              ))}
            </div>
          ) : null}
          {hoveredAlarms?.titles.slice(0, 2).map((title) => (
            <div key={title} className="fx-strip-tip-alarm">
              {title}
            </div>
          ))}
        </div>
      ) : null}
    </div>
  );
}
