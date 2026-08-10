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
  ARM_HALF,
  BRANCH_GROUND_Y,
  BRANCH_LABEL_Y,
  BRANCH_MAIN_ARM_Y,
  BRANCH_NAME_Y,
  BRANCH_SPAN_W,
  BRANCH_WIRE_Y,
  DIM_LABEL_Y,
  DIM_Y,
  GROUND_Y,
  LABEL_Y,
  MAIN_ARM_Y,
  PEAK_Y,
  STRIP_H,
  STRIP_PX_H,
  WIRE_Y,
  buildStripGeometry,
  hotPathOf,
  toPath
} from "./faultStripGeometry";
import { StripTower } from "./StripTower";
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

/**
 * UC ILETKEN — her faz kendi renginde ve kendi yuksekliginde.
 *
 * Tek gri tel "hat" demiyordu; uc renkli iletken hem gercek bir havai hatta
 * benziyor hem de arizanin HANGI FAZDA oldugunu telin kendisinde
 * gosterebiliyor. Renkler cihaz ekranindaki kaynak renkleriyle ayni:
 * operator ayni kodu bir kez ogreniyor.
 *
 * `dx`: ana traversteki izolator noktasinin merkeze gore ofseti.
 * `kaynak`: alarm faz eslemesi (master/sat01/sat02).
 */
const ILETKENLER = [
  { faz: "L1", kaynak: "master", dx: -ARM_HALF, renk: "#2563eb" },
  { faz: "L2", kaynak: "sat01", dx: 0, renk: "#16a34a" },
  { faz: "L3", kaynak: "sat02", dx: ARM_HALF, renk: "#c2410c" }
] as const;

type Pt = { x: number; y: number };
type Hover =
  | { kind: "device" | "pole" | "branch"; key: string }
  | null;
type View = { x: number; y: number; w: number; h: number };

function poleLabel(p: StripPole): string {
  const ad = (p.name ?? "").trim();
  return ad || `#${p.seq}`;
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
  // TABAN GORUNUM = CIZIMIN TAMAMI. Onceki surumde viewBox yuksekligi
  // kapsayicinin en-boy oranindan turetiliyordu; kapsayici uzadikca pencere
  // de uzuyor, icerik yukarida kucucuk kaliyor ve altinda ucu bucagi
  // gorunmeyen bos bir alan aciliyordu — "canvas sonsuza dek asagi kayiyor"
  // sikayeti tam olarak buydu. Artik pencere icerige bagli, kapsayiciya
  // degil: SVG sabit yukseklikte cizilir ve `preserveAspectRatio` ile
  // ortalanir. Kaydirma sinirlari da bu tabana gore kapatildi (asagida),
  // yani cizim disina asla cikilamaz.
  const base: View = useMemo(
    () => ({ x: 0, y: 0, w: width, h: STRIP_H }),
    [width]
  );
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
        // Tam sinir: pencere cizimin DISINA cikamaz.
        x = Math.max(0, Math.min(Math.max(0, base.w - yeniW), x));
        y = Math.max(0, Math.min(Math.max(0, base.h - yeniH), y));
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
      x: Math.max(0, Math.min(Math.max(0, base.w - v.w), x)),
      y: Math.max(0, Math.min(Math.max(0, base.h - v.h), y)),
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

  /** Arizali FAZLAR — "gordum" diyen cihazin acik alarmlarindan.
   *  Bos ise faz bilinmiyor (eski kayit) ve uc iletken de vurgulanir. */
  const arizaliFazlar = useMemo(() => {
    const k = (lastRedDeviceCode ?? "").trim();
    const kaynaklar = k ? alarmsByDevice?.[k]?.sources ?? [] : [];
    return new Set(kaynaklar);
  }, [alarmsByDevice, lastRedDeviceCode]);
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
      ? { x: xOf(seqs.indexOf(hoveredPole.seq)), y: MAIN_ARM_Y + 26 }
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
      <div className="fx-strip-stage">
        <svg
          ref={svgRef}
          className={`fx-strip${yakinlasti ? " is-zoomed" : ""}`}
          viewBox={`${v.x} ${v.y} ${v.w} ${v.h}`}
          width="100%"
          height={STRIP_PX_H}
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

          {/* Zemin duzlemi — direklerin ustunde durdugu yuzey. */}
          <rect
            x={-8}
            y={GROUND_Y}
            width={width + 16}
            height={STRIP_H - GROUND_Y}
            fill="url(#fx-ground)"
          />
          <line
            x1={-8}
            y1={GROUND_Y}
            x2={width + 8}
            y2={GROUND_Y}
            stroke="#cbd5e1"
            strokeWidth={1.1}
          />

          {/* Arizali bolge sutunu — direkler ve teller bunun uzerine biner. */}
          {dimA != null && dimB != null && active ? (
            <rect
              x={dimA}
              y={MAIN_ARM_Y - 26}
              width={Math.max(0, dimB - dimA)}
              height={GROUND_Y - MAIN_ARM_Y + 26}
              fill="url(#fx-hatch)"
            />
          ) : null}

          {/* Toprak teli — tepe noktalari arasinda, ince gri. */}
          <path
            d={toPath(
              wire.map((pt) => ({ x: pt.x, y: pt.y - (WIRE_Y - PEAK_Y) + 2 }))
            )}
            fill="none"
            stroke="#cbd5e1"
            strokeWidth={0.9}
            strokeLinecap="round"
            opacity={0.75}
          />

          {/* UC FAZ ILETKENI — her biri kendi renginde ve kendi
              izolator noktasindan geciyor. Tek gri tel "hat" demiyordu;
              uc renkli iletken hem gercek bir havai hatta benziyor hem de
              arizanin HANGI FAZDA oldugunu telin kendisinde gosteriyor. */}
          {ILETKENLER.map((ilt) => {
            const yol = toPath(wire.map((pt) => ({ x: pt.x + ilt.dx, y: pt.y })));
            const arizali = active && arizaliFazlar.has(ilt.kaynak);
            return (
              <g key={ilt.faz}>
                <path
                  d={yol}
                  fill="none"
                  stroke={ilt.renk}
                  strokeWidth={1.7}
                  strokeLinecap="round"
                  opacity={active && arizaliFazlar.size > 0 && !arizali ? 0.35 : 0.9}
                />
              </g>
            );
          })}

          {/* ARIZALI PARCA — arizali fazin telinde. Faz bilinmiyorsa
              (eski kayit) uc iletken de vurgulanir. */}
          {hotPath
            ? ILETKENLER.filter(
                (ilt) => arizaliFazlar.size === 0 || arizaliFazlar.has(ilt.kaynak)
              ).map((ilt) => {
                const yol = hotPathOf(geo, ilt.dx);
                if (!yol) return null;
                return (
                  <g key={`hot-${ilt.faz}`}>
                    <path
                      d={yol}
                      fill="none"
                      stroke={faultColor}
                      strokeWidth={6.5}
                      strokeLinecap="round"
                      filter="url(#fx-wire-glow)"
                      opacity={active ? 0.4 : 0.16}
                    />
                    <path
                      d={yol}
                      fill="none"
                      stroke={faultColor}
                      strokeWidth={2.8}
                      strokeLinecap="round"
                    />
                  </g>
                );
              })
            : null}

          {/* ---- BRANSMAN KOLLARI ----
               Kol ana direkten CAPRAZ iner ve kendi direklerine baglanir.
               Ariza ile ilgili kollar (bolge icinde ya da kendi arizasi olan)
               TAM cizilir; digerleri soluk kalir ki kalabalik etmesin. */}
          {branchDraw.map((b) => {
            const renk = b.hot ? RED : BRANCH;
            const solukluk = b.hot ? 1 : 0.42;
            return (
              <g
                key={b.key}
                className="fx-strip-branch"
                opacity={solukluk}
                onMouseEnter={() => setHover({ kind: "branch", key: b.key })}
                onMouseLeave={() => setHover(null)}
              >
                <rect
                  x={b.anchorX - 12}
                  y={MAIN_ARM_Y}
                  width={b.endX - b.anchorX + 74}
                  height={BRANCH_LABEL_Y - MAIN_ARM_Y + 12}
                  fill="transparent"
                />
                {/* CAPRAZ INIS: ana direkte traversten ayrilir, dal katina
                    egik bir dogru ile iner. Kesikli degil DUZ cizgi —
                    iletken gercekten oradan gecer. */}
                <line
                  x1={b.anchorX}
                  y1={MAIN_ARM_Y + 2}
                  x2={b.poles[0]?.x ?? b.anchorX + 40}
                  y2={BRANCH_MAIN_ARM_Y}
                  stroke={renk}
                  strokeWidth={1.6}
                  strokeLinecap="round"
                />
                {/* Kol adi — capraz inisin yaninda, kendi satirinda. */}
                <text
                  x={b.anchorX + 6}
                  y={BRANCH_NAME_Y}
                  fontSize={9.5}
                  fontWeight={800}
                  fill={b.hot ? "#b91c1c" : "#6d28d9"}
                >
                  {b.name}
                </text>

                {/* Dal zemini */}
                <line
                  x1={(b.poles[0]?.x ?? b.anchorX) - 16}
                  y1={BRANCH_GROUND_Y}
                  x2={b.endX + 16}
                  y2={BRANCH_GROUND_Y}
                  stroke="#e2e8f0"
                  strokeWidth={1}
                  strokeDasharray="3 4"
                />
                {/* Dal iletkeni — uc faz yerine tek hat: kol ikincil bilgi,
                    uc renkli tel burada gurultu olurdu. */}
                <path
                  d={b.poles
                    .map((pt, i) => `${i === 0 ? "M" : "L"}${pt.x} ${BRANCH_WIRE_Y}`)
                    .join(" ")}
                  fill="none"
                  stroke={b.hot ? renk : "#cbd5e1"}
                  strokeWidth={b.hot ? 2.2 : 1.6}
                  strokeLinecap="round"
                />
                {/* Kolun direkleri — ana hattakinin sadelestirilmis hali. */}
                {b.poles.map((pt) => (
                  <g key={pt.x} stroke={renk} strokeWidth={1.3} strokeLinecap="round" fill="none">
                    <line x1={pt.x} y1={BRANCH_MAIN_ARM_Y} x2={pt.x} y2={BRANCH_GROUND_Y} />
                    <line
                      x1={pt.x - 8}
                      y1={BRANCH_MAIN_ARM_Y}
                      x2={pt.x + 8}
                      y2={BRANCH_MAIN_ARM_Y}
                    />
                    <line x1={pt.x} y1={BRANCH_GROUND_Y - 10} x2={pt.x - 5} y2={BRANCH_GROUND_Y} />
                    <line x1={pt.x} y1={BRANCH_GROUND_Y - 10} x2={pt.x + 5} y2={BRANCH_GROUND_Y} />
                  </g>
                ))}
                {b.poles.map((pt) => (
                  <text
                    key={`bl-${pt.x}`}
                    x={pt.x}
                    y={BRANCH_LABEL_Y}
                    textAnchor="middle"
                    fontSize={8.5}
                    fontWeight={b.hot ? 700 : 500}
                    fill={b.hot ? "#b91c1c" : GREY}
                  >
                    {pt.label}
                  </text>
                ))}
                {b.fazla > 0 ? (
                  <text
                    x={b.endX + 14}
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
            <StripTower
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
              {/* Uzanti cizgileri direge kadar iner: olcunun NEREYI
                  olctugu belirsiz kalmasin. */}
              <line x1={dimA} y1={DIM_Y} x2={dimA} y2={MAIN_ARM_Y - 6} strokeWidth={0.9} opacity={0.35} />
              <line x1={dimB} y1={DIM_Y} x2={dimB} y2={MAIN_ARM_Y - 6} strokeWidth={0.9} opacity={0.35} />
              <line x1={dimA} y1={DIM_Y} x2={dimB} y2={DIM_Y} strokeWidth={1.3} />
              <path d={`M${dimA} ${DIM_Y} l6 -3 v6 z`} stroke="none" />
              <path d={`M${dimB} ${DIM_Y} l-6 -3 v6 z`} stroke="none" />
              <text x={dimA} y={DIM_Y + 12} textAnchor="middle" fontSize={9.5} fontWeight={600} stroke="none" opacity={0.85}>
                {formatDistanceM(zoneStartM)}
              </text>
              <text x={dimB} y={DIM_Y + 12} textAnchor="middle" fontSize={9.5} fontWeight={600} stroke="none" opacity={0.85}>
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
