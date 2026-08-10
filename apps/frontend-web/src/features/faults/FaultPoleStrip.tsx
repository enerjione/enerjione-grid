/**
 * FaultPoleStrip — hattin SAHNE GORUNUMU ("tahmini ariza bolgesi").
 *
 * Cografi harita DEGIL (o is FaultDetailModal'daki Leaflet mini haritada);
 * buradaki amac tek bakista su bes soruyu cevaplamak:
 *   1. Ariza hangi direkler arasinda?
 *   2. Hangi cihaz "gordu", hangisi "gormedi"?
 *   3. Ariza tam olarak hangi TEL parcasinda ve hat basindan KAC METREDE?
 *   4. Hangi FAZ arizali? (master + sat01 + sat02 ayri fazlara takilir)
 *   5. Aralikta BRANSMAN kolu var mi — ekip orayi da gezmeli mi?
 *
 * ETKILESIM: tekerlek ile yakinlas/uzaklas, surukleyerek gez, cift tik ya da
 * "sigdir" dugmesiyle sifirla. Uzun hatlar tek ekrana sigmiyor; sabit
 * olcekte kaydirmak yerine kullanicinin kendi odagini secmesi gerekiyordu.
 *
 * DERINLIK: teknik resim duz cizgiden ibaret degil — zemin izgarasi ufka
 * dogru sikisir, direklerin yan yuzu ve zemin golgesi var, arka iletkenler
 * daha soluk. Bu 2.5D katman "bir sahneye bakiyorum" hissini veriyor ama
 * okunurlugu bozan gercek bir perspektif donusumune girmiyor: direk
 * ADRESLERI (sira/ad) her zaman ayni yukseklikte ve duz okunur.
 *
 * ARIZA PINI YOK: pin `transform` ATTRIBUTE'u ile konumlanip CSS animasyonu
 * `transform` OZELLIGINI yaziyordu; CSS ozelligi presentation attribute'unu
 * ezdigi icin pin her render'da (0,0)'a — cizimin sol ust kosesine —
 * sicriyordu. Arizanin yeri kirmizi tel ve olcu seridiyle isaretli.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { Maximize2, Minus, Plus } from "lucide-react";

import {
  BRANCH_CROSSARM_Y,
  BRANCH_GROUND_Y,
  BRANCH_LABEL_Y,
  BRANCH_NAME_Y,
  BRANCH_SPAN_W,
  BRANCH_WIRE_Y,
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
import type { StripBranch, StripPole, StripSegment } from "./faultStripGeometry";
import { formatDistanceM } from "../../shared/lineDistance";

export type { StripSegment, StripPole, StripBranch };

/** Cihaz basina, o cihazda ACIK olan alarmlarin faz kaynaklari. */
export type StripDeviceAlarms = {
  /** "master" | "sat01" | "sat02" — alarmi olan kaynaklar. */
  sources: string[];
  /** Alarm basliklari (tooltip'te ilki gosterilir). */
  titles: string[];
};

type Props = {
  poleSeqs: number[];
  poles?: StripPole[];
  /** Bu hattan ayrilan bransman kollari — dal olarak cizilir. */
  branches?: StripBranch[];
  segments?: StripSegment[];
  fromSeq: number | null | undefined;
  toSeq: number | null | undefined;
  lastRedDeviceCode?: string | null;
  firstGreenDeviceCode?: string | null;
  active?: boolean;
  zoneStartM?: number | null;
  zoneEndM?: number | null;
  alarmsByDevice?: Record<string, StripDeviceAlarms>;
};

const INK = "#334155";
const GREY = "#94a3b8";
const WIRE_GREY = "#cbd5e1";
const RED = "#dc2626";
const GREEN = "#16a34a";
const DIM_INK = "#b45309";
const BRANCH = "#7c3aed";

/** Faz sirasi — bir SN2 govdesindeki uc sensor, hattin uc fazi. */
const PHASES = ["master", "sat01", "sat02"] as const;

type Pt = { x: number; y: number };
type Hover =
  | { kind: "device" | "pole" | "branch"; key: string }
  | null;
type View = { x: number; y: number; w: number; h: number };

function poleLabel(p: StripPole): string {
  const ad = (p.name ?? "").trim();
  return ad || `#${p.seq}`;
}

/** Bir direk — travers, izolatorler, kafes ayaklar + 2.5D yan yuz ve golge. */
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
      <rect
        x={x - 15}
        y={CROSSARM_Y - 8}
        width={30}
        height={GROUND_Y - CROSSARM_Y + 24}
        fill="transparent"
      />
      {/* Zemin golgesi — cismi zemine oturtur, derinlik hissinin temeli. */}
      <ellipse
        cx={x}
        cy={GROUND_Y + 1.5}
        rx={spread + 3}
        ry={2.6}
        fill="#0f172a"
        opacity={0.1}
      />
      {/* Govdenin YAN YUZU: govde cizgisinin hemen sagina soluk bir serit.
          Tek cizgilik direge kalinlik kazandirir (2.5D). */}
      <path
        d={`M${x} ${CROSSARM_Y} L${x + 2.4} ${CROSSARM_Y + 2} L${x + 2.4} ${GROUND_Y} L${x} ${GROUND_Y} Z`}
        fill={stroke}
        opacity={0.16}
      />
      <g stroke={stroke} strokeWidth={hot ? 2 : 1.7} strokeLinecap="round" fill="none">
        <line x1={x} y1={CROSSARM_Y} x2={x} y2={GROUND_Y} />
        <line x1={x - arm} y1={CROSSARM_Y} x2={x + arm} y2={CROSSARM_Y} />
        <line x1={x - arm} y1={CROSSARM_Y} x2={x - arm} y2={WIRE_Y - 5} />
        <line x1={x + arm} y1={CROSSARM_Y} x2={x + arm} y2={WIRE_Y - 5} />
        <line x1={x} y1={CROSSARM_Y} x2={x} y2={WIRE_Y - 5} />
        <line x1={x} y1={GROUND_Y - 24} x2={x - spread} y2={GROUND_Y} />
        <line x1={x} y1={GROUND_Y - 24} x2={x + spread} y2={GROUND_Y} />
        <line
          x1={x - spread * 0.55}
          y1={GROUND_Y - 12}
          x2={x + spread * 0.55}
          y2={GROUND_Y - 12}
        />
      </g>
      {/* Bransman direginin traversinde ek kol — dal buradan cikar. */}
      {role === "branch" ? (
        <line
          x1={x}
          y1={CROSSARM_Y}
          x2={x}
          y2={CROSSARM_Y - 7}
          stroke={BRANCH}
          strokeWidth={2.2}
          strokeLinecap="round"
        />
      ) : null}
    </g>
  );
}

/** Iletken uzerindeki ariza gecis gostergesi + uc faz noktasi. */
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
      <rect x={-11} y={-11} width={22} height={30} fill="transparent" />
      <rect x={-4.2} y={-5} width={8.4} height={3.6} rx={1.2} fill={color} />
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
  branches,
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

  // ---- Gorunum penceresi (zoom + pan) ----------------------------------
  //
  // SVG'ye sabit bir yukseklik verildiginde kapsayicinin geri kalani BOS
  // kaliyordu: kart uzadikca cizimin altinda genis beyaz bir serit olusuyor,
  // sahne "sayfaya oturmamis" gorunuyordu. Cozum viewBox'in EN-BOY ORANINI
  // kapsayicidan almak — cizim tum alani doldurur ve icerik bozulmaz.
  const stageRef = useRef<HTMLDivElement | null>(null);
  const [oran, setOran] = useState<number>(STRIP_H / 900);
  useEffect(() => {
    const el = stageRef.current;
    if (!el || typeof ResizeObserver === "undefined") return;
    const ro = new ResizeObserver(() => {
      const r = el.getBoundingClientRect();
      if (r.width > 0 && r.height > 0) setOran(r.height / r.width);
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  const base: View = useMemo(() => {
    // Cizimin tamami HER ZAMAN sigsin: gereken yukseklik kapsayicidan
    // fazlaysa genisligi degil YUKSEKLIGI buyutmek cizimi kucultmek olurdu,
    // o yuzden genisligi oranla telafi ediyoruz.
    const gerekenH = width * oran;
    if (gerekenH >= STRIP_H) {
      // Kapsayici yeterince yuksek: cizim dikeyde ortalanir.
      return { x: 0, y: (STRIP_H - gerekenH) / 2, w: width, h: gerekenH };
    }
    // Kapsayici basik: cizimin tamamini gostermek icin daha genis bir
    // pencere ac (yanlarda bosluk kalir, icerik kirpilmaz).
    const w = STRIP_H / oran;
    return { x: (width - w) / 2, y: 0, w, h: STRIP_H };
  }, [width, oran]);
  const [view, setView] = useState<View | null>(null);
  const v = view ?? base;
  const svgRef = useRef<SVGSVGElement | null>(null);
  const drag = useRef<{ x: number; y: number; vx: number; vy: number } | null>(null);

  /** Ekran pikselini viewBox birimine cevirir (zoom seviyesinden bagimsiz). */
  const birimBasinaPx = useCallback(() => {
    const el = svgRef.current;
    if (!el) return { sx: 1, sy: 1 };
    const r = el.getBoundingClientRect();
    return { sx: v.w / (r.width || 1), sy: v.h / (r.height || 1) };
  }, [v.w, v.h]);

  const zoomAt = useCallback(
    (carpan: number, oranX = 0.5, oranY = 0.5) => {
      setView((mevcut) => {
        const c = mevcut ?? base;
        // Alt sinir: tum hat. Ust sinir: bir direk araliginin ~1/8'i —
        // daha ilerisi cizimde anlam tasimayan bir buyutme olurdu.
        const enAz = base.w / 40;
        const yeniW = Math.min(base.w, Math.max(enAz, c.w / carpan));
        const oran = yeniW / c.w;
        const yeniH = c.h * oran;
        // Imlecin altindaki nokta SABIT kalsin.
        const odakX = c.x + c.w * oranX;
        const odakY = c.y + c.h * oranY;
        let x = odakX - yeniW * oranX;
        let y = odakY - yeniH * oranY;
        // Cizim disina tasma: kenarlarda bosluga bakmak kafa karistirir.
        x = Math.max(-4, Math.min(base.w - yeniW + 4, x));
        y = Math.max(-4, Math.min(base.h - yeniH + 4, y));
        return { x, y, w: yeniW, h: yeniH };
      });
    },
    [base]
  );

  const onWheel = useCallback(
    (e: React.WheelEvent<SVGSVGElement>) => {
      // Sayfa kaydirmasini engelle: tekerlek burada zoom demek.
      e.preventDefault();
      const r = e.currentTarget.getBoundingClientRect();
      const oranX = (e.clientX - r.left) / (r.width || 1);
      const oranY = (e.clientY - r.top) / (r.height || 1);
      zoomAt(e.deltaY < 0 ? 1.18 : 1 / 1.18, oranX, oranY);
    },
    [zoomAt]
  );

  const onPointerDown = (e: React.PointerEvent<SVGSVGElement>) => {
    // Cihaz/direk isaretlerinde surukleme baslatma — onlar ipucu hedefi.
    e.currentTarget.setPointerCapture(e.pointerId);
    drag.current = { x: e.clientX, y: e.clientY, vx: v.x, vy: v.y };
  };
  const onPointerMove = (e: React.PointerEvent<SVGSVGElement>) => {
    const d = drag.current;
    if (!d) return;
    const { sx, sy } = birimBasinaPx();
    const x = d.vx - (e.clientX - d.x) * sx;
    const y = d.vy - (e.clientY - d.y) * sy;
    setView({
      x: Math.max(-4, Math.min(base.w - v.w + 4, x)),
      y: Math.max(-4, Math.min(base.h - v.h + 4, y)),
      w: v.w,
      h: v.h
    });
  };
  const onPointerUp = (e: React.PointerEvent<SVGSVGElement>) => {
    drag.current = null;
    try {
      e.currentTarget.releasePointerCapture(e.pointerId);
    } catch {
      /* yakalama zaten birakilmis olabilir */
    }
  };

  const yakinlasti = v.w < base.w - 0.5;

  // ---- Ariza ve olcu ----------------------------------------------------
  const faultColor = active ? RED : GREY;
  const hotFrom = span ? Math.floor(span.a) : -1;
  const hotTo = span ? Math.ceil(span.b) : -1;
  const hotPath = hotPathOf(geo);
  const dimA = span ? pointAt(span.a).x : null;
  const dimB = span ? pointAt(span.b).x : null;
  const spanM =
    zoneStartM != null && zoneEndM != null ? Math.max(0, zoneEndM - zoneStartM) : null;

  // ---- Bransman kollari -------------------------------------------------
  // Kol ana hattin ALTINA iner: ana hat yatay ekseni korur, dal asagi dogru
  // ayrilir. Boylece "hangi direkten ne cikiyor" tek bakista okunur.
  const branchDraw = useMemo(() => {
    // Kol ana hattin ALTINDA kendi kati olarak cizilir. Onceki surumde dal
    // kisa bir kesikli cizgi + nokta + etiketti; iki kol yan yana gelince
    // etiketler ust uste biniyor ("BR-2 BR-3"), kolun KENDI direkleri hic
    // gorunmuyordu. Kol ayri bir hattir — oyle de cizilmeli.
    const out: {
      key: string;
      name: string;
      /** Ana hattaki dallanma diregi. */
      anchorX: number;
      /** Dal katindaki direkler (en fazla GOSTER kadar). */
      poles: { x: number; label: string }[];
      /** Gosterilemeyen direk sayisi ("+N"). */
      fazla: number;
      hot: boolean;
      poleCount: number;
      endX: number;
    }[] = [];
    const GOSTER = 4;
    for (const b of branches ?? []) {
      const idx = seqs.indexOf(b.atSeq);
      if (idx === -1) continue;
      const anchorX = xOf(idx);
      const kolDirekleri = (b.poles ?? []).slice(0, GOSTER);
      // Kol direk kaydi gelmediyse en azindan sayidan yer tutucu uret:
      // dalin uzunlugu gorunsun.
      const adet = kolDirekleri.length || Math.min(GOSTER, Math.max(1, b.poleCount));
      const cizilecek = kolDirekleri.length
        ? kolDirekleri.map((p, i) => ({
            x: anchorX + 26 + i * BRANCH_SPAN_W,
            label: poleLabel(p)
          }))
        : Array.from({ length: adet }, (_, i) => ({
            x: anchorX + 26 + i * BRANCH_SPAN_W,
            label: `#${i + 1}`
          }));
      out.push({
        key: `${b.lineId}`,
        name: b.name,
        anchorX,
        poles: cizilecek,
        fazla: Math.max(0, b.poleCount - cizilecek.length),
        // Kol KENDI arizasini tasiyorsa ya da ariza araligi bu dallanma
        // diregini kapsiyorsa kirmizi.
        hot: Boolean(b.hasFault) || (active && idx >= hotFrom && idx <= hotTo),
        poleCount: b.poleCount,
        endX: cizilecek.length
          ? cizilecek[cizilecek.length - 1].x
          : anchorX + 26
      });
    }
    return out;
  }, [branches, seqs, xOf, active, hotFrom, hotTo]);

  // ---- Ipucu ------------------------------------------------------------
  const hoveredDevice =
    hover?.kind === "device" ? devices.find((d) => d.code === hover.key) ?? null : null;
  const hoveredPole =
    hover?.kind === "pole" ? poleList.find((p) => String(p.seq) === hover.key) ?? null : null;
  const hoveredBranch =
    hover?.kind === "branch" ? branchDraw.find((b) => b.key === hover.key) ?? null : null;
  const hoveredAlarms = hoveredDevice ? alarmsByDevice?.[hoveredDevice.code] : undefined;

  const tipUnit = hoveredDevice
    ? pointAt(hoveredDevice.pos)
    : hoveredPole
      ? { x: xOf(seqs.indexOf(hoveredPole.seq)), y: CROSSARM_Y + 26 }
      : hoveredBranch
        ? { x: hoveredBranch.anchorX, y: BRANCH_NAME_Y + 4 }
        : null;
  // Ipucu HTML katmaninda; viewBox birimini kapsayicinin yuzdesine cevir.
  const tipStyle = tipUnit
    ? {
        left: `${((tipUnit.x - v.x) / v.w) * 100}%`,
        top: `${((tipUnit.y - v.y) / v.h) * 100}%`
      }
    : undefined;

  return (
    <div className="fx-strip-wrap">
      <div className="fx-strip-stage" ref={stageRef}>
        <svg
          ref={svgRef}
          className={`fx-strip${yakinlasti ? " is-zoomed" : ""}`}
          viewBox={`${v.x} ${v.y} ${v.w} ${v.h}`}
          width="100%"
          height="100%"
          preserveAspectRatio="xMidYMid meet"
          role="img"
          aria-label={
            span
              ? t("faults.poleStrip.range", { from: fromSeq ?? "?", to: toSeq ?? "?" })
              : t("faults.poleStrip.unknown")
          }
          onWheel={onWheel}
          onPointerDown={onPointerDown}
          onPointerMove={onPointerMove}
          onPointerUp={onPointerUp}
          onPointerLeave={onPointerUp}
          onDoubleClick={() => setView(null)}
        >
          <defs>
            <filter id="fx-wire-glow" x="-30%" y="-200%" width="160%" height="500%">
              <feGaussianBlur stdDeviation="3.2" />
            </filter>
            <pattern
              id="fx-hatch"
              width="7"
              height="7"
              patternUnits="userSpaceOnUse"
              patternTransform="rotate(45)"
            >
              <line x1="0" y1="0" x2="0" y2="7" stroke={RED} strokeWidth="1" opacity="0.14" />
            </pattern>
            {/* Ufka dogru acilan zemin: yakin sarid koyu, uzak soluk. */}
            <linearGradient id="fx-ground" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor="#e2e8f0" stopOpacity="0" />
              <stop offset="100%" stopColor="#cbd5e1" stopOpacity="0.55" />
            </linearGradient>
          </defs>

          {/* Zemin duzlemi — direklerin uzerinde durdugu yuzey. */}
          <rect
            x={-4}
            y={GROUND_Y}
            width={width + 8}
            height={STRIP_H - GROUND_Y}
            fill="url(#fx-ground)"
          />
          {/* Derinlik izgarasi: asagi indikce SIKLASIR (ufuk etkisi). */}
          {[6, 14, 24, 36, 50].map((dy, i) => (
            <line
              key={dy}
              x1={-4}
              y1={GROUND_Y + dy}
              x2={width + 4}
              y2={GROUND_Y + dy}
              stroke="#94a3b8"
              strokeWidth={0.6}
              opacity={0.1 + i * 0.03}
            />
          ))}

          {/* Arizali bolge sutunu */}
          {dimA != null && dimB != null && active ? (
            <rect
              x={dimA}
              y={CROSSARM_Y - 10}
              width={Math.max(0, dimB - dimA)}
              height={GROUND_Y - CROSSARM_Y + 10}
              fill="url(#fx-hatch)"
            />
          ) : null}

          <line
            x1={4}
            y1={GROUND_Y}
            x2={width - 4}
            y2={GROUND_Y}
            stroke="#cbd5e1"
            strokeWidth={1.2}
          />

          {/* Arka iletkenler — daha soluk ve ince: derinlik. */}
          {[-8, -4].map((dy, i) => (
            <path
              key={dy}
              d={toPath(wire.map((p) => ({ x: p.x, y: p.y + dy })))}
              fill="none"
              stroke={WIRE_GREY}
              strokeWidth={1 + i * 0.15}
              strokeLinecap="round"
              opacity={0.4 + i * 0.12}
            />
          ))}

          <path
            d={toPath(wire)}
            fill="none"
            stroke={WIRE_GREY}
            strokeWidth={2.4}
            strokeLinecap="round"
          />

          {/* ---- BRANSMAN KOLLARI (ALT KAT) ---- */}
          {branchDraw.map((b) => {
            const renk = b.hot ? RED : BRANCH;
            return (
              <g
                key={b.key}
                className="fx-strip-branch"
                onMouseEnter={() => setHover({ kind: "branch", key: b.key })}
                onMouseLeave={() => setHover(null)}
              >
                <rect
                  x={b.anchorX - 10}
                  y={BRANCH_NAME_Y - 12}
                  width={b.endX - b.anchorX + 70}
                  height={BRANCH_LABEL_Y - BRANCH_NAME_Y + 20}
                  fill="transparent"
                />
                {/* Ana hattan dal katina INIS — kesikli: ayri bir hat. */}
                <path
                  d={`M${b.anchorX} ${GROUND_Y - 2} L${b.anchorX} ${BRANCH_CROSSARM_Y - 6} Q ${b.anchorX} ${BRANCH_WIRE_Y}, ${b.anchorX + 22} ${BRANCH_WIRE_Y}`}
                  fill="none"
                  stroke={renk}
                  strokeWidth={1.8}
                  strokeDasharray="6 4"
                  strokeLinecap="round"
                  opacity={0.85}
                />
                {/* Kol adi — dal katinin solunda, kendi satirinda. Iki kol
                    yan yana gelse bile etiketler artik carpismiyor. */}
                <text
                  x={b.anchorX + 2}
                  y={BRANCH_NAME_Y}
                  fontSize={9.5}
                  fontWeight={800}
                  fill={b.hot ? "#b91c1c" : "#6d28d9"}
                >
                  {b.name}
                </text>
                {/* Dal zemini */}
                <line
                  x1={b.anchorX + 18}
                  y1={BRANCH_GROUND_Y}
                  x2={b.endX + 18}
                  y2={BRANCH_GROUND_Y}
                  stroke="#e2e8f0"
                  strokeWidth={1}
                  strokeDasharray="3 4"
                />
                {/* Dal iletkeni — mini katener */}
                <path
                  d={b.poles
                    .map((p, i) =>
                      i === 0
                        ? `M${b.anchorX + 22} ${BRANCH_WIRE_Y} L${p.x} ${BRANCH_WIRE_Y}`
                        : `L${p.x} ${BRANCH_WIRE_Y}`
                    )
                    .join(" ")}
                  fill="none"
                  stroke={b.hot ? renk : WIRE_GREY}
                  strokeWidth={b.hot ? 2.4 : 1.8}
                  strokeLinecap="round"
                />
                {/* Kolun direkleri — ana hattakinin kucugu */}
                {b.poles.map((p) => (
                  <g key={p.x} stroke={renk} strokeWidth={1.4} strokeLinecap="round" fill="none">
                    <line x1={p.x} y1={BRANCH_CROSSARM_Y} x2={p.x} y2={BRANCH_GROUND_Y} />
                    <line
                      x1={p.x - 7}
                      y1={BRANCH_CROSSARM_Y}
                      x2={p.x + 7}
                      y2={BRANCH_CROSSARM_Y}
                    />
                    <line x1={p.x} y1={BRANCH_GROUND_Y - 11} x2={p.x - 5} y2={BRANCH_GROUND_Y} />
                    <line x1={p.x} y1={BRANCH_GROUND_Y - 11} x2={p.x + 5} y2={BRANCH_GROUND_Y} />
                  </g>
                ))}
                {b.poles.map((p) => (
                  <text
                    key={`bl-${p.x}`}
                    x={p.x}
                    y={BRANCH_LABEL_Y}
                    textAnchor="middle"
                    fontSize={8.5}
                    fontWeight={b.hot ? 700 : 500}
                    fill={b.hot ? "#b91c1c" : GREY}
                  >
                    {p.label}
                  </text>
                ))}
                {b.fazla > 0 ? (
                  <text
                    x={b.endX + 16}
                    y={BRANCH_WIRE_Y + 3}
                    fontSize={8.5}
                    fontWeight={700}
                    fill={GREY}
                  >
                    +{b.fazla}
                  </text>
                ) : null}
              </g>
            );
          })}

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

          {/* ---- OLCU SERIDI ---- */}
          {dimA != null && dimB != null && zoneStartM != null && zoneEndM != null ? (
            <g className="fx-strip-dim" stroke={DIM_INK} fill={DIM_INK}>
              <line x1={dimA} y1={LABEL_Y + 6} x2={dimA} y2={DIM_Y + 6} strokeWidth={1} opacity={0.5} />
              <line x1={dimB} y1={LABEL_Y + 6} x2={dimB} y2={DIM_Y + 6} strokeWidth={1} opacity={0.5} />
              <line x1={dimA} y1={DIM_Y} x2={dimB} y2={DIM_Y} strokeWidth={1.3} />
              <path d={`M${dimA} ${DIM_Y} l6 -3 v6 z`} stroke="none" />
              <path d={`M${dimB} ${DIM_Y} l-6 -3 v6 z`} stroke="none" />
              <text x={dimA} y={DIM_Y - 7} textAnchor="middle" fontSize={9.5} fontWeight={600} stroke="none" opacity={0.85}>
                {formatDistanceM(zoneStartM)}
              </text>
              <text x={dimB} y={DIM_Y - 7} textAnchor="middle" fontSize={9.5} fontWeight={600} stroke="none" opacity={0.85}>
                {formatDistanceM(zoneEndM)}
              </text>
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

        {/* ---- Yakinlastirma denetimleri ---- */}
        <div className="fx-strip-zoom">
          <button type="button" onClick={() => zoomAt(1.35)} aria-label={t("faults.poleStrip.zoomIn")}>
            <Plus size={14} strokeWidth={2.6} />
          </button>
          <button type="button" onClick={() => zoomAt(1 / 1.35)} aria-label={t("faults.poleStrip.zoomOut")}>
            <Minus size={14} strokeWidth={2.6} />
          </button>
          <button
            type="button"
            onClick={() => setView(null)}
            aria-label={t("faults.poleStrip.zoomFit")}
            disabled={!yakinlasti}
          >
            <Maximize2 size={13} strokeWidth={2.4} />
          </button>
        </div>
        {!yakinlasti ? (
          <div className="fx-strip-hint">{t("faults.poleStrip.zoomHint")}</div>
        ) : null}

        {/* ---- Ipucu ---- */}
        {tipUnit ? (
          <div className="fx-strip-tip" style={tipStyle} role="tooltip">
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
                    from: poleLabel(
                      poleList.find((p) => p.seq === hoveredDevice.fromSeq) ?? {
                        seq: hoveredDevice.fromSeq
                      }
                    ),
                    to: poleLabel(
                      poleList.find((p) => p.seq === hoveredDevice.toSeq) ?? {
                        seq: hoveredDevice.toSeq
                      }
                    )
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
            ) : hoveredBranch ? (
              <>
                <div className="fx-strip-tip-name">{hoveredBranch.name}</div>
                <div className="fx-strip-tip-role fx-strip-tip-role--idle">
                  {t("faults.poleStrip.poleRole.branch")}
                </div>
                <div className="fx-strip-tip-row">
                  {t("faults.poleStrip.branchPoles", { count: hoveredBranch.poleCount })}
                </div>
                {hoveredBranch.hot ? (
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
