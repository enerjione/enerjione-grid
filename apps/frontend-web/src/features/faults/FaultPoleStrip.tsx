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
 * Kot cizgileri, ok uclu olcu (dimension) serit, tabular rakamlar.
 *
 * OLCEK SABITTIR (bkz. STRIP_PX_H)
 * --------------------------------
 * Cizim eskiden `width:100%` ile kapsayiciya yayiliyordu; yani olcek hattin
 * DIREK SAYISINA gore degisiyordu — 6 direkli hat devasa, 17 direkli hat
 * minicik gorunuyor, iki ariza karti karsilastirilamiyordu. Artik bir direk
 * araligi her hatta ayni piksel genisligindedir; cizim sigmazsa YATAY
 * KAYDIRILIR. Uzun hatlarda tek ekrana sigdirmaya calismak zaten okunaksizdi.
 *
 * ARIZA PINI YOK: pin `transform` ATTRIBUTE'u ile konumlanip `fx-strip-pin`
 * sinifindaki CSS animasyonu `transform` OZELLIGINI yaziyordu; CSS ozelligi
 * presentation attribute'unu ezdigi icin pin her render'da (0,0)'a — cizimin
 * sol ust kosesine — sicriyordu. Arizanin yeri zaten kirmizi tel ve olcu
 * seridiyle isaretli.
 */
import { useMemo, useState } from "react";
import { useTranslation } from "react-i18next";

import {
  CROSSARM_Y,
  DIM_LABEL_Y,
  DIM_Y,
  GROUND_Y,
  LABEL_Y,
  PX_PER_UNIT,
  STRIP_H,
  STRIP_PX_H,
  WIRE_Y,
  buildStripGeometry,
  hotPathOf,
  toPath
} from "./faultStripGeometry";
import type { StripPole, StripSegment } from "./faultStripGeometry";
import { formatDistanceM } from "../../shared/lineDistance";

export type { StripSegment, StripPole };

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
  /** Direk ad/rol bilgisi — etiketler ve ipucu icin. */
  poles?: StripPole[];
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
type Hover = { kind: "device" | "pole"; key: string } | null;

/** Direk etiketi: ad varsa ad, yoksa sira numarasi. */
function poleLabel(p: StripPole): string {
  const ad = (p.name ?? "").trim();
  return ad || `#${p.seq}`;
}

/** Bir direk — travers, izolatorler ve kafes ayaklar. */
function Tower({
  x,
  hot,
  role,
  onEnter,
  onLeave
}: {
  x: number;
  hot: boolean;
  role?: string | null;
  onEnter: () => void;
  onLeave: () => void;
}) {
  const stroke = hot ? RED : GREY;
  const arm = 13;
  const spread = 9;
  return (
    <g
      className="fx-strip-pole"
      onMouseEnter={onEnter}
      onMouseLeave={onLeave}
      onFocus={onEnter}
      onBlur={onLeave}
      tabIndex={0}
    >
      {/* imlec hedefi — ince cizgilere nisan almak zor */}
      <rect
        x={x - 15}
        y={CROSSARM_Y - 8}
        width={30}
        height={GROUND_Y - CROSSARM_Y + 24}
        fill="transparent"
      />
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
        {/* BRANSMAN: hattan ayrilan kolu tek bakista belli et. Ariza dalda
            ise bu direk arama alaninin siniridir. */}
        {role === "branch" ? (
          <>
            <line x1={x} y1={CROSSARM_Y + 8} x2={x + arm + 8} y2={CROSSARM_Y + 20} />
            <circle cx={x + arm + 9} cy={CROSSARM_Y + 21} r={2.4} fill={stroke} stroke="none" />
          </>
        ) : null}
      </g>
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
  poles,
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
  const [hover, setHover] = useState<Hover>(null);

  const geo = useMemo(
    () =>
      buildStripGeometry({
        poleSeqs,
        poles,
        segments,
        fromSeq,
        toSeq,
        lastRedDeviceCode,
        firstGreenDeviceCode
      }),
    [poleSeqs, poles, segments, fromSeq, toSeq, lastRedDeviceCode, firstGreenDeviceCode]
  );

  const { seqs, poles: poleList, width, wire, devices, span, xOf, pointAt } = geo;
  const faultColor = active ? RED : GREY;
  const hotFrom = span ? Math.floor(span.a) : -1;
  const hotTo = span ? Math.ceil(span.b) : -1;
  const hotPath = hotPathOf(geo);

  // Olcu seridi yalnizca arizali parcanin altinda cizilir.
  const dimA = span ? pointAt(span.a).x : null;
  const dimB = span ? pointAt(span.b).x : null;
  const spanM =
    zoneStartM != null && zoneEndM != null ? Math.max(0, zoneEndM - zoneStartM) : null;

  const hoveredDevice =
    hover?.kind === "device" ? devices.find((d) => d.code === hover.key) ?? null : null;
  const hoveredPole =
    hover?.kind === "pole" ? poleList.find((p) => String(p.seq) === hover.key) ?? null : null;
  const hoveredAlarms = hoveredDevice ? alarmsByDevice?.[hoveredDevice.code] : undefined;

  // Ipucu konumu — viewBox biriminden PIKSELE. Cizim kaydirilabildigi icin
  // yuzde kullanilamaz: kaydirma sonrasi yuzde baska bir noktayi gosterirdi.
  const tipX = hoveredDevice
    ? pointAt(hoveredDevice.pos).x
    : hoveredPole
      ? xOf(seqs.indexOf(hoveredPole.seq))
      : 0;
  const tipY = hoveredDevice ? pointAt(hoveredDevice.pos).y : CROSSARM_Y + 26;
  const pxW = width * PX_PER_UNIT;

  return (
    <div className="fx-strip-wrap">
      {/* Sabit olcek + yatay kaydirma. Ic katman `position:relative`: ipucu
          cizimle BIRLIKTE kayar (disarida birakilsaydi kaydirmada kopardi). */}
      <div className="fx-strip-inner" style={{ width: pxW }}>
        <svg
          className="fx-strip"
          viewBox={`0 0 ${width} ${STRIP_H}`}
          width={pxW}
          height={STRIP_PX_H}
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
          {poleList.map((p, idx) => (
            <Tower
              key={p.seq}
              x={xOf(idx)}
              hot={active && idx >= hotFrom && idx <= hotTo}
              role={p.role}
              onEnter={() => setHover({ kind: "pole", key: String(p.seq) })}
              onLeave={() => setHover(null)}
            />
          ))}

          {/* Cihazlar — telin uzerinde */}
          {devices.map((d) => (
            <DeviceMark
              key={d.code}
              p={pointAt(d.pos)}
              tone={active ? d.tone : "idle"}
              alarmSources={active ? alarmsByDevice?.[d.code]?.sources ?? [] : []}
              dim={hover?.kind === "device" && hover.key !== d.code}
              onEnter={() => setHover({ kind: "device", key: d.code })}
              onLeave={() => setHover(null)}
            />
          ))}

          {/* Direk etiketleri — ad varsa ad, yoksa sira numarasi */}
          {poleList.map((p, idx) => {
            const hot = active && idx >= hotFrom && idx <= hotTo;
            return (
              <text
                key={`l-${p.seq}`}
                x={xOf(idx)}
                y={LABEL_Y}
                textAnchor="middle"
                fontSize={10}
                fontWeight={hot ? 700 : 500}
                fill={hot ? "#b91c1c" : GREY}
              >
                {poleLabel(p)}
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
                fontSize={9.5}
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
                fontSize={9.5}
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
                  fontSize={11}
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
              fontSize={11}
              fontWeight={600}
              fill={INK}
              opacity={0.6}
            >
              {t("faults.poleStrip.range", { from: fromSeq ?? "?", to: toSeq ?? "?" })}
            </text>
          ) : null}
        </svg>

        {/* ---- Ipucu ----
            SVG <title> yerine HTML: gecikmesiz acilir, bicimlendirilebilir ve
            satirlara ayrilabilir. ASAGI acilir: yukari acilsaydi kartin ust
            kenarindan tasar ve `overflow:hidden` yuzunden kirpilirdi (ekranda
            "yarim gorunen ipucu" sikayeti tam olarak buydu). */}
        {hoveredDevice || hoveredPole ? (
          <div
            className="fx-strip-tip"
            style={{ left: tipX * PX_PER_UNIT, top: tipY * PX_PER_UNIT + 12 }}
            role="tooltip"
          >
            {hoveredDevice ? (
              <>
                <div className="fx-strip-tip-name">{hoveredDevice.label}</div>
                {hoveredDevice.code !== hoveredDevice.label ? (
                  <code className="fx-strip-tip-code">{hoveredDevice.code}</code>
                ) : null}
                <div className={`fx-strip-tip-role fx-strip-tip-role--${hoveredDevice.tone}`}>
                  {t(`faults.poleStrip.role.${hoveredDevice.tone}`)}
                </div>
                <div className="fx-strip-tip-row">
                  {t("faults.poleStrip.between", {
                    from: poleLabel(poleList.find((p) => p.seq === hoveredDevice.fromSeq) ?? {
                      seq: hoveredDevice.fromSeq
                    }),
                    to: poleLabel(poleList.find((p) => p.seq === hoveredDevice.toSeq) ?? {
                      seq: hoveredDevice.toSeq
                    })
                  })}
                </div>
                {hoveredDevice.tone === "red" && zoneStartM != null ? (
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
                {hoveredAlarms?.titles.slice(0, 1).map((title) => (
                  <div key={title} className="fx-strip-tip-alarm">
                    {title}
                  </div>
                ))}
              </>
            ) : hoveredPole ? (
              <>
                <div className="fx-strip-tip-name">{poleLabel(hoveredPole)}</div>
                <div className="fx-strip-tip-row">
                  {t("faults.poleStrip.poleSeq", { seq: hoveredPole.seq })}
                </div>
                {hoveredPole.role ? (
                  <div className="fx-strip-tip-role fx-strip-tip-role--idle">
                    {t(`faults.poleStrip.poleRole.${hoveredPole.role}`, {
                      defaultValue: hoveredPole.role
                    })}
                  </div>
                ) : null}
                {active &&
                seqs.indexOf(hoveredPole.seq) >= hotFrom &&
                seqs.indexOf(hoveredPole.seq) <= hotTo ? (
                  <div className="fx-strip-tip-row fx-strip-tip-row--warn">
                    {t("faults.poleStrip.inZone")}
                  </div>
                ) : null}
              </>
            ) : null}
          </div>
        ) : null}
      </div>
    </div>
  );
}
