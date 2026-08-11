/**
 * FaultPoleStrip — arizanin SAHNE GORUNUMU ("tahmini ariza bolgesi").
 *
 * Cografi harita DEGIL (o is FaultDetailModal'daki Leaflet mini haritada);
 * buradaki amac tek bakista su alti soruyu cevaplamak:
 *   1. Ariza hangi direkler arasinda?
 *   2. Hangi cihaz "gordu", hangisi "gormedi"?
 *   3. Ariza tam olarak hangi TEL parcasinda ve hat basindan KAC METREDE?
 *   4. Hangi FAZ arizali? (master + sat01 + sat02 ayri fazlara kelepcelenir)
 *   5. Aralikta BRANSMAN kolu var mi — ekip orayi da gezmeli mi?
 *   6. Ariza BIRDEN FAZLA hat kesiminde olabiliyorsa hepsi neresi?
 *
 * COK SATIRLI SAHNE (yeni)
 * ------------------------
 * Ariza bolgesi bir dallanma diregini kapsiyorsa ariza ana hatta da olabilir
 * o kolda da; harita bunu zaten iki ayri kirmizi kesik olarak ciziyordu ama
 * sema yalnizca ana hatti gosteriyor, kollari kenarda kucuk bir "dal" olarak
 * geciyordu. Ekip hangi kolu gezecegini o daldan okuyamiyordu.
 *
 * Artik her ADAY HAT KESIMI kendi SATIRINDA, tam bir hat olarak cizilir:
 * kendi direkleri, ucer fazi, cihazlari ve ariza bolgesi ile. Satirlar
 * birbirine, koldun ayrildigi DIREKTEN inen bir bagla baglanir — "bu kol su
 * direkten cikiyor" cizimden okunur.
 *
 * ETKILESIM: tekerlek ile yakinlas/uzaklas, surukleyerek gez, cift tik ya da
 * "sigdir" dugmesiyle sifirla.
 *
 * ARIZA PINI YOK: pin `transform` ATTRIBUTE'u ile konumlanip CSS animasyonu
 * `transform` OZELLIGINI yaziyordu; CSS ozelligi presentation attribute'unu
 * ezdigi icin pin her render'da (0,0)'a sicriyordu. Arizanin yeri kirmizi tel
 * ve olcu seridiyle isaretli.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { Maximize2, Minus, Plus } from "lucide-react";

import {
  GROUND_Y,
  PHASE_LINES,
  buildFaultScene,
  frameSceneToBox
} from "./faultStripGeometry";
import type {
  FaultRowInput,
  StripBranchRow,
  StripDeviceAlarms,
  StripPole,
  StripSegment
} from "./faultStripGeometry";
import { FaultStripRow, poleLabel } from "./FaultStripRow";
import type { RowHover } from "./FaultStripRow";
import { formatDistanceM } from "../../shared/lineDistance";

export type { StripSegment, StripPole, StripDeviceAlarms, StripBranchRow };

type Props = {
  /** Ana hattin adi — satir basliginda gorunur. */
  lineName: string;
  poleSeqs: number[];
  poles?: StripPole[];
  segments?: StripSegment[];
  fromSeq: number | null | undefined;
  toSeq: number | null | undefined;
  lastRedDeviceCode?: string | null;
  firstGreenDeviceCode?: string | null;
  active?: boolean;
  zoneStartM?: number | null;
  zoneEndM?: number | null;
  alarmsByDevice?: Record<string, StripDeviceAlarms>;
  /** Arizali faz kaynaklari ("master" | "sat01" | "sat02"). Bos = bilinmiyor. */
  faultPhases?: string[];
  /** Ariza bolgesine denk gelen aday kollar — her biri AYRI satir. */
  branchRows?: StripBranchRow[];
  /** Sigmadigi icin cizilemeyen kol sayisi. */
  hiddenBranchCount?: number;
  /** Cizimdeki bir cihaza tiklaninca o cihazin sayfasini ac. */
  onOpenDevice?: (code: string) => void;
};

const GREY = "#94a3b8";
const RED = "#dc2626";

type View = { x: number; y: number; w: number; h: number };

export function FaultPoleStrip({
  lineName,
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
  alarmsByDevice,
  faultPhases,
  branchRows,
  hiddenBranchCount = 0,
  onOpenDevice
}: Props) {
  const { t } = useTranslation();
  const [hover, setHover] = useState<RowHover>(null);

  // ---- Sahne: ana hat + aday kollar ------------------------------------
  const scene = useMemo(() => {
    const inputs: FaultRowInput[] = [
      {
        key: "main",
        kind: "main",
        title: lineName,
        poleSeqs,
        poles,
        segments,
        fromSeq,
        toSeq,
        lastRedDeviceCode,
        firstGreenDeviceCode,
        zoneStartM,
        zoneEndM,
        faultPhases,
        alarmsByDevice,
        active
      }
    ];
    for (const b of branchRows ?? []) {
      inputs.push({
        key: `b${b.lineId}`,
        kind: "branch",
        lineId: b.lineId,
        title: b.name,
        poleSeqs: b.poleSeqs,
        poles: b.poles,
        segments: b.segments,
        fromSeq: b.fromSeq,
        toSeq: b.toSeq,
        lastRedDeviceCode: b.lastRedDeviceCode,
        firstGreenDeviceCode: b.firstGreenDeviceCode,
        zoneStartM: b.zoneStartM,
        zoneEndM: b.zoneEndM,
        faultPhases: b.faultPhases,
        alarmsByDevice: b.alarmsByDevice,
        parentKey: b.parentLineId ? `b${b.parentLineId}` : "main",
        parentSeq: b.atSeq,
        parentPoleName: b.atPoleName,
        confirmed: b.confirmed,
        active
      });
    }
    return buildFaultScene(inputs);
  }, [
    lineName,
    poleSeqs,
    poles,
    segments,
    fromSeq,
    toSeq,
    lastRedDeviceCode,
    firstGreenDeviceCode,
    zoneStartM,
    zoneEndM,
    faultPhases,
    alarmsByDevice,
    branchRows,
    active
  ]);

  /** Faz etiketleri — tellerin SOLUNDA yazar; dar bir seride sigmasi icin
   *  kisa bicim ("Sat 01"), ipucunda uzun bicim ("Satellite 01"). */
  const phaseLabels = useMemo(() => {
    const m: Record<string, string> = {};
    for (const f of PHASE_LINES) {
      m[f.key] = t(`faults.poleStrip.phaseShort.${f.key}`, {
        defaultValue: t(`faults.phase.${f.key}`, { defaultValue: f.key })
      });
    }
    return m;
  }, [t]);
  const phaseLongLabels = useMemo(() => {
    const m: Record<string, string> = {};
    for (const f of PHASE_LINES) m[f.key] = t(`faults.phase.${f.key}`, { defaultValue: f.key });
    return m;
  }, [t]);

  // ---- Gorunum penceresi (zoom + pan) ----------------------------------
  const svgRef = useRef<SVGSVGElement | null>(null);

  /** Cizim alaninin GERCEK piksel olcusu. Taban gorunum buna gore kurulur. */
  const [boxPx, setBoxPx] = useState<{ w: number; h: number } | null>(null);
  useEffect(() => {
    const el = svgRef.current;
    if (!el || typeof ResizeObserver === "undefined") return;
    const ro = new ResizeObserver(([entry]) => {
      const r = entry.contentRect;
      if (r.width > 0 && r.height > 0) setBoxPx({ w: r.width, h: r.height });
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  // TABAN GORUNUM = SAHNE, KUTUYA GORE CERCEVELENMIS (bkz. frameSceneToBox).
  const base: View = useMemo(
    () => frameSceneToBox(scene, boxPx),
    [boxPx, scene]
  );

  const [view, setView] = useState<View | null>(null);
  const v = view ?? base;
  const drag = useRef<{ x: number; y: number; vx: number; vy: number } | null>(null);

  /** Pencereyi taban cercevenin ICINDE tutar — kenarda bosluga bakilmaz. */
  const sinirla = useCallback(
    (x: number, y: number, w: number, h: number): View => ({
      x: Math.max(base.x, Math.min(base.x + Math.max(0, base.w - w), x)),
      y: Math.max(base.y, Math.min(base.y + Math.max(0, base.h - h), y)),
      w,
      h
    }),
    [base]
  );

  // Satir sayisi degisince (baska bir arizaya gecildi) yakinlastirma sifirlanir;
  // yoksa yeni sahnenin disinda kalmis bir pencereye bakiliyor.
  useEffect(() => setView(null), [scene.rows.length, scene.width]);

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
        // Alt sinir: tum sahne. Ust sinir: bir direk araliginin ~1/8'i —
        // daha ilerisi cizimde anlam tasimayan bir buyutme olurdu.
        const enAz = base.w / 40;
        const yeniW = Math.min(base.w, Math.max(enAz, c.w / carpan));
        const oran = yeniW / c.w;
        const yeniH = c.h * oran;
        // Imlecin altindaki nokta SABIT kalsin.
        const odakX = c.x + c.w * oranX;
        const odakY = c.y + c.h * oranY;
        const x = odakX - yeniW * oranX;
        const y = odakY - yeniH * oranY;
        // Cizim disina tasma: kenarlarda bosluga bakmak kafa karistirir.
        return sinirla(x, y, yeniW, yeniH);
      });
    },
    [base, sinirla]
  );

  // TEKERLEK: React'in `onWheel` prop'u ile DEGIL, dogrudan DOM uzerinden.
  //
  // React 18 `wheel` dinleyicisini kok kapsayiciya PASSIVE olarak bagliyor
  // (react-dom addTrappedEventListener: touchstart/touchmove/wheel icin
  // isPassiveListener = true). Passive bir dinleyicide `preventDefault()`
  // hicbir sey yapmaz — tarayici konsola uyari bile basmadan yok sayar.
  // Sonuc: semada zoom yaparken SAYFA DA kayiyordu.
  useEffect(() => {
    const el = svgRef.current;
    if (!el) return;
    const isle = (e: WheelEvent) => {
      e.preventDefault();
      const r = el.getBoundingClientRect();
      const oranX = (e.clientX - r.left) / (r.width || 1);
      const oranY = (e.clientY - r.top) / (r.height || 1);
      zoomAt(e.deltaY < 0 ? 1.18 : 1 / 1.18, oranX, oranY);
    };
    el.addEventListener("wheel", isle, { passive: false });
    return () => el.removeEventListener("wheel", isle);
  }, [zoomAt]);

  const onPointerDown = (e: React.PointerEvent<SVGSVGElement>) => {
    e.currentTarget.setPointerCapture(e.pointerId);
    drag.current = { x: e.clientX, y: e.clientY, vx: v.x, vy: v.y };
  };
  const onPointerMove = (e: React.PointerEvent<SVGSVGElement>) => {
    const d = drag.current;
    if (!d) return;
    const { sx, sy } = birimBasinaPx();
    const x = d.vx - (e.clientX - d.x) * sx;
    const y = d.vy - (e.clientY - d.y) * sy;
    setView(sinirla(x, y, v.w, v.h));
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

  // CIZIM ALANI SABIT YUKSEKLIK ALMAZ.
  //
  // Bir sure "en az sahne kadar yuksek" bir alt sinir vardi; cok satirli bir
  // sahnede bu kartin ekrandan tasmasina ve SAYFANIN KAYMASINA yol aciyordu.
  // Artik alan karttaki bos yerin tamamini kaplar, sahne de o kutuya
  // olceklenir (bkz. frameSceneToBox): kirpilma yok, kayma yok.

  // ---- Ipucu ------------------------------------------------------------
  const hoveredRow = hover ? scene.rows.find((r) => r.key === hover.rowKey) ?? null : null;
  const hoveredDevice =
    hover?.kind === "device" && hoveredRow
      ? hoveredRow.geo.devices.find((d) => d.code === hover.key) ?? null
      : null;
  const hoveredPole =
    hover?.kind === "pole" && hoveredRow
      ? hoveredRow.geo.poles.find((p) => String(p.seq) === hover.key) ?? null
      : null;
  const hoveredAlarms =
    hoveredDevice && hoveredRow ? hoveredRow.alarmsByDevice?.[hoveredDevice.code] : undefined;

  const tipUnit =
    hoveredRow && hoveredDevice
      ? (() => {
          const p = hoveredRow.geo.pointAt(hoveredDevice.pos);
          return {
            x: hoveredRow.x0 + p.x,
            y: hoveredRow.y0 + p.y + PHASE_LINES[PHASE_LINES.length - 1].dy + 10
          };
        })()
      : hoveredRow && hoveredPole
        ? {
            x: hoveredRow.x0 + hoveredRow.geo.xOf(hoveredRow.geo.seqs.indexOf(hoveredPole.seq)),
            y: hoveredRow.y0 + GROUND_Y - 22
          }
        : null;
  // Ipucu HTML katmaninda; viewBox birimini kapsayicinin yuzdesine cevir.
  const tipStyle = tipUnit
    ? {
        left: `${((tipUnit.x - v.x) / v.w) * 100}%`,
        top: `${((tipUnit.y - v.y) / v.h) * 100}%`
      }
    : undefined;

  const hoveredRowZoneStart = hoveredRow?.zoneStartM;

  return (
    <div className="fx-strip-wrap">
      <div className="fx-strip-stage">
        <svg
          ref={svgRef}
          className={`fx-strip${yakinlasti ? " is-zoomed" : ""}`}
          viewBox={`${v.x} ${v.y} ${v.w} ${v.h}`}
          width="100%"
          preserveAspectRatio="xMidYMid meet"
          role="img"
          aria-label={
            fromSeq != null && toSeq != null
              ? t("faults.poleStrip.range", { from: fromSeq, to: toSeq })
              : t("faults.poleStrip.unknown")
          }
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
          </defs>

          {/* ---- SATIRLAR ARASI BAG ----
               Kol, ayrildigi DIREKTEN iner ve alt satirin ilk diregine
               girer. Bag saga tasarak dolasir: dumduz inseydi ust satirdaki
               direk ADININ uzerinden gecerdi.

               UC KATMAN: genis soluk bir govde (bagin "kalinligi"), uzerinde
               kesikli ince cizgi (yon), iki ucta dugum. Once tek ince bir
               kesik cizgiydi ve iki satiri birbirine BAGLADIGI degil
               tesadufen yanindan gectigi izlenimi veriyordu. */}
          {scene.rows.map((r) => {
            if (!r.link) return null;
            const renk = active ? RED : GREY;
            const { fromX, fromY, toX, toY } = r.link;
            // Yon: +1 asagi inen kol, -1 yukari cikan kol. Egri ve giris
            // parcasi ayni formulle iki tarafa da calisir.
            const d = r.side === -1 ? -1 : 1;
            const yol =
              `M${fromX} ${fromY + 5 * d}` +
              ` C${fromX + 30} ${fromY + 26 * d}, ${toX + 30} ${toY - 42 * d}, ${toX} ${toY - 12 * d}`;
            return (
              <g key={`lnk-${r.key}`} className="fx-strip-link">
                {/* Govde: bagin fiziksel kalinligi. */}
                <path
                  d={yol}
                  fill="none"
                  stroke={renk}
                  strokeWidth={6}
                  strokeLinecap="round"
                  opacity={0.12}
                />
                <path
                  d={yol}
                  fill="none"
                  stroke={renk}
                  strokeWidth={1.9}
                  strokeDasharray="6 5"
                  strokeLinecap="round"
                />
                {/* Kol satirina giris: ilk direginin tepesine (asagi inen
                    kolda) ya da dibine (yukari cikan kolda) oturur. */}
                <line
                  x1={toX}
                  y1={toY - 12 * d}
                  x2={toX}
                  y2={toY}
                  stroke={renk}
                  strokeWidth={1.9}
                  strokeLinecap="round"
                />
              </g>
            );
          })}

          {scene.rows.map((r) => (
            <FaultStripRow
              key={r.key}
              row={r}
              phaseLabels={phaseLabels}
              badge={
                r.kind === "main"
                  ? null
                  : r.confirmed
                    ? t("faults.poleStrip.branchConfirmed")
                    : t("faults.poleStrip.branchSuspect")
              }
              subtitle={
                r.kind === "main"
                  ? t("faults.poleStrip.mainLine")
                  : t("faults.poleStrip.branchAtPole", {
                      pole: r.parentPoleName || `#${r.parentSeq ?? "?"}`
                    })
              }
              suspectWholeLine={r.kind === "branch" && !r.confirmed}
              labels={{
                zoneSpan: (s) => t("faults.poleStrip.spanLabel", { span: s }),
                wholeLineSuspect: t("faults.poleStrip.wholeLineSuspect"),
                range:
                  r.geo.span && r.zoneStartM == null
                    ? t("faults.poleStrip.range", {
                        from: r.fromSeq ?? "?",
                        to: r.toSeq ?? "?"
                      })
                    : null
              }}
              hover={hover}
              onHover={setHover}
              onOpenDevice={onOpenDevice}
            />
          ))}

          {/* ---- BAG DUGUMLERI ----
               Satirlarin USTUNDE cizilir. Ust uctaki dugum, direkteki mor
               "bransman noktasi" isaretinin tam uzerine oturur: ayni noktada
               iki farkli renk (mor dugum + kirmizi bag) tesadufi bir cakisma
               gibi duruyordu; artik tek bir kirmizi birlesim var. Alt uctaki
               dugum de direk govdesinin ustune biner, altinda kalmaz. */}
          {scene.rows.map((r) => {
            if (!r.link) return null;
            const renk = active ? RED : GREY;
            return (
              <g key={`node-${r.key}`}>
                <circle cx={r.link.fromX} cy={r.link.fromY} r={5.4} fill="#fff" />
                <circle cx={r.link.fromX} cy={r.link.fromY} r={3.4} fill={renk} />
                <circle cx={r.link.toX} cy={r.link.toY} r={5} fill="#fff" />
                <circle cx={r.link.toX} cy={r.link.toY} r={3.1} fill={renk} />
              </g>
            );
          })}

          {/* Sigmadigi icin cizilmeyen kollar SESSIZCE dusmesin: ekip
              "hepsi bu" sanarsa gezmedigi bir kol kalir. */}
          {hiddenBranchCount > 0 ? (
            <text
              x={12}
              y={scene.height - 6}
              fontSize={10}
              fontWeight={700}
              fill={GREY}
            >
              {t("faults.poleStrip.moreBranches", { count: hiddenBranchCount })}
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
        {tipUnit && hoveredRow ? (
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
                      hoveredRow.geo.poles.find((p) => p.seq === hoveredDevice.fromSeq) ?? {
                        seq: hoveredDevice.fromSeq
                      }
                    ),
                    to: poleLabel(
                      hoveredRow.geo.poles.find((p) => p.seq === hoveredDevice.toSeq) ?? {
                        seq: hoveredDevice.toSeq
                      }
                    )
                  })}
                </div>
                {hoveredDevice.tone === "red" && hoveredRowZoneStart != null ? (
                  <div className="fx-strip-tip-row">
                    {t("faults.poleStrip.atDistance", {
                      d: formatDistanceM(hoveredRowZoneStart)
                    })}
                  </div>
                ) : null}
                {hoveredAlarms && hoveredAlarms.sources.length > 0 ? (
                  <div className="fx-strip-tip-phases">
                    {PHASE_LINES.map((f) => (
                      <span
                        key={f.key}
                        className={`fx-phase-chip${
                          hoveredAlarms.sources.includes(f.key) ? " is-on" : ""
                        }`}
                      >
                        {phaseLongLabels[f.key]}
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
                {hoveredRow.kind === "branch" ? (
                  <div className="fx-strip-tip-row">{hoveredRow.title}</div>
                ) : null}
                {hoveredPole.role ? (
                  <div className="fx-strip-tip-role fx-strip-tip-role--idle">
                    {t(`faults.poleStrip.poleRole.${hoveredPole.role}`, {
                      defaultValue: hoveredPole.role
                    })}
                  </div>
                ) : null}
                {active && hoveredRow.geo.span
                  ? (() => {
                      const idx = hoveredRow.geo.seqs.indexOf(hoveredPole.seq);
                      const icinde =
                        idx >= Math.floor(hoveredRow.geo.span.a) &&
                        idx <= Math.ceil(hoveredRow.geo.span.b);
                      return icinde ? (
                        <div className="fx-strip-tip-row fx-strip-tip-row--warn">
                          {t("faults.poleStrip.inZone")}
                        </div>
                      ) : null;
                    })()
                  : null}
              </>
            ) : null}
          </div>
        ) : null}
      </div>
    </div>
  );
}
