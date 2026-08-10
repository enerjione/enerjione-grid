/**
 * FaultStripRow — sahnedeki TEK BIR HAT SATIRI.
 *
 * Sahne birden fazla satir tasiyabilir: ana hat + ariza bolgesine denk gelen
 * aday bransman kollari. Her satir kendi direkleri, kendi ucer fazi, kendi
 * cihazlari ve kendi ariza bolgesi ile TAM bir hat cizimidir — kol artik
 * "ana hattan cikan bir cizgi" degil, kendi basina okunabilen bir hat.
 *
 * Satir kendi yerel koordinatinda cizer; sahnedeki yerine `translate` ile
 * oturur (bkz. FaultPoleStrip).
 */
import {
  ARM_HALF,
  DIM_LABEL_Y,
  DIM_Y,
  GROUND_Y,
  GW_WIRE_Y,
  LABEL_Y,
  PAD_X,
  PHASE_LINES,
  ROW_TITLE_Y,
  WIRE_Y,
  hotPathOf,
  toPath
} from "./faultStripGeometry";
import type { FaultRow, StripDevice, StripPole } from "./faultStripGeometry";
import { StripTower } from "./StripTower";
import { formatDistanceM } from "../../shared/lineDistance";

const GREY = "#94a3b8";
const WIRE_GREY = "#cbd5e1";
const RED = "#dc2626";
const GREEN = "#16a34a";
const DIM_INK = "#b45309";
const INK = "#334155";

export type RowHover = { kind: "device" | "pole"; rowKey: string; key: string } | null;

type Props = {
  row: FaultRow;
  /** Faz kisa adlari (i18n) — tellerin solunda yazar. */
  phaseLabels: Record<string, string>;
  /** Satir basligindaki rozet metni; null ise rozet cizilmez. */
  badge: string | null;
  /** "N. direkten bransman" alt basligi. */
  subtitle: string | null;
  /** Kol satirinda kendi kaydi yoksa tum hat aday demektir. */
  suspectWholeLine: boolean;
  labels: {
    zoneSpan: (span: string) => string;
    wholeLineSuspect: string;
    range: string | null;
  };
  hover: RowHover;
  onHover: (h: RowHover) => void;
};

export function poleLabel(p: StripPole | { seq: number; name?: string | null }): string {
  const ad = (p.name ?? "").trim();
  return ad || `#${p.seq}`;
}

/**
 * CIHAZ = UC KELEPCE.
 *
 * Bir SN2 govdesi tek bir kutu degil: uc sensor hattin uc AYRI fazina
 * kelepcelenir. Onceki cizimde cihaz tek bir kutu olarak ORTA telin uzerinde
 * duruyor, hangi fazi olctugu kutunun altindaki uc minik noktadan okunuyordu.
 * Artik her sensor kendi telinin uzerinde: alarm veren faz DOLU kelepce, geri
 * kalanlar bos. Boylece "hangi faz arizali" sorusu cizimin kendisinden
 * cevaplanir.
 */
function DeviceMark({
  d,
  base,
  tone,
  alarmSources,
  dim,
  onEnter,
  onLeave
}: {
  d: StripDevice;
  base: { x: number; y: number };
  tone: "red" | "green" | "idle";
  alarmSources: string[];
  dim: boolean;
  onEnter: () => void;
  onLeave: () => void;
}) {
  const color = tone === "red" ? RED : tone === "green" ? GREEN : "#64748b";
  const ustY = base.y + PHASE_LINES[0].dy;
  const altY = base.y + PHASE_LINES[PHASE_LINES.length - 1].dy;
  return (
    <g
      opacity={dim ? 0.45 : 1}
      className="fx-strip-dev"
      onMouseEnter={onEnter}
      onMouseLeave={onLeave}
      onFocus={onEnter}
      onBlur={onLeave}
      tabIndex={0}
      data-code={d.code}
    >
      {/* Imlec hedefi — uc kelepceyi birden kapsar. */}
      <rect
        x={base.x - 9}
        y={ustY - 9}
        width={18}
        height={altY - ustY + 18}
        fill="transparent"
      />

      {/* Govde bagi: uc sensor AYNI cihaza ait — ince kesikli dikey hat. */}
      <line
        x1={base.x}
        y1={ustY}
        x2={base.x}
        y2={altY}
        stroke={color}
        strokeWidth={0.9}
        strokeDasharray="2.5 2.5"
        opacity={0.55}
      />

      {PHASE_LINES.map((f) => {
        const on = alarmSources.includes(f.key);
        const y = base.y + f.dy;
        return (
          <g key={f.key}>
            {/* Kelepce: telin uzerine oturan govde. */}
            <rect
              x={base.x - 4.6}
              y={y - 4.4}
              width={9.2}
              height={8.8}
              rx={2}
              fill={on ? color : "#fff"}
              stroke={color}
              strokeWidth={1.6}
            />
            {/* Telin gectigi oluk. */}
            <line
              x1={base.x - 4.6}
              y1={y - 1.4}
              x2={base.x + 4.6}
              y2={y - 1.4}
              stroke={on ? "#fff" : color}
              strokeWidth={0.9}
              opacity={0.85}
            />
          </g>
        );
      })}
    </g>
  );
}

export function FaultStripRow({
  row,
  phaseLabels,
  badge,
  subtitle,
  suspectWholeLine,
  labels,
  hover,
  onHover
}: Props) {
  const { geo, key: rowKey } = row;
  const { seqs, poles: poleList, width, wire, devices, span, xOf, pointAt } = geo;
  const active = row.active ?? true;

  // ARIZA BOLGESINDEKI DIREKLER — sinirlarin ICINDE kalanlar.
  //
  // Onceki hesap `floor(a)..ceil(b)` idi: bolge iki cihaz arasindaysa
  // sinirlarin DISINDA kalan iki direk de kirmizi isaretleniyordu. "Gormedim"
  // diyen cihazin OTESINDEKI direk kirmizi olamaz — ekip o direge bakmasin.
  const hotFrom = span ? Math.ceil(span.a) : -1;
  const hotTo = span ? Math.floor(span.b) : -1;

  // ARIZALI FAZLAR: bilinmiyorsa (eski kayit / aday kol) uc tel de vurgulanir.
  // "Bilmiyorum"u tek bir faza indirgemek ekibi yanlis tele bakmaya gonderir.
  const fazlar = row.faultPhases ?? [];
  const hotPhases = PHASE_LINES.map((f) => fazlar.length === 0 || fazlar.includes(f.key));

  const faultColor = active ? RED : GREY;
  // Aday kol: kendi kaydi yok, bolge kesin degil — KESIKLI cizilir. Kesin
  // bolge (cihaz sinirlari belli) duz cizgidir. Cizgi tipi guveni anlatir.
  const hotDash = suspectWholeLine ? "9 6" : undefined;

  const dimA = span ? pointAt(span.a).x : null;
  const dimB = span ? pointAt(span.b).x : null;
  const spanM =
    row.zoneStartM != null && row.zoneEndM != null
      ? Math.max(0, row.zoneEndM - row.zoneStartM)
      : null;

  return (
    <g transform={`translate(${row.x0}, ${row.y0})`}>
      {/* ---- SATIR BASLIGI ----
           Baslik/alt baslik/rozet TEK bir <text> icinde tspan'lerle diziliyor:
           SVG'de metin genisligi olculemedigi icin ayri elemanlari konumlamak
           "karakter sayisi x tahmini genislik" gibi kirilgan bir hesap
           gerektiriyordu; uzun hat adlarinda rozet basligin uzerine biniyor. */}
      {/* Beyaz hale: satirlar arasi bag cizgisi basligin altindan gecebiliyor;
          halesiz metin kesikli cizginin uzerinde okunmuyordu. */}
      <text
        x={2}
        y={ROW_TITLE_Y}
        fontSize={11}
        stroke="#fcfdff"
        strokeWidth={3.2}
        strokeLinejoin="round"
        paintOrder="stroke"
      >
        <tspan fontWeight={800} fill={INK}>
          {row.title}
        </tspan>
        {subtitle ? (
          <tspan dx={7} fontSize={9.5} fontWeight={600} fill={GREY}>
            {subtitle}
          </tspan>
        ) : null}
        {badge ? (
          <tspan
            dx={9}
            fontSize={9.5}
            fontWeight={800}
            fill={row.confirmed ? "#b91c1c" : "#b45309"}
          >
            {`• ${badge}`}
          </tspan>
        ) : null}
      </text>

      {/* Zemin: yalnizca ince bir cizgi. */}
      <line x1={-8} y1={GROUND_Y} x2={width + 8} y2={GROUND_Y} stroke="#cbd5e1" strokeWidth={1.1} />

      {/* Arizali bolge sutunu — direkler ve teller bunun uzerine biner. */}
      {dimA != null && dimB != null && active ? (
        <rect
          x={dimA}
          y={PHASE_LINES[0].armY - 14}
          width={Math.max(0, dimB - dimA)}
          height={GROUND_Y - PHASE_LINES[0].armY + 14}
          fill="url(#fx-hatch)"
        />
      ) : null}

      {/* Toprak teli — tepe noktalari arasinda, ince gri. */}
      <path
        d={toPath(wire.map((pt) => ({ x: pt.x, y: GW_WIRE_Y + (pt.y - WIRE_Y) * 0.35 })))}
        fill="none"
        stroke="#cbd5e1"
        strokeWidth={0.9}
        strokeLinecap="round"
        opacity={0.7}
      />

      {/* UC FAZ ILETKENI — her biri kendi traversinden, kendi yuksekliginde.
          Renk YALNIZCA DURUM tasir: gri = saglam, kirmizi = arizali. Faz
          kimligi renkten degil, solda duran faz etiketinden okunur. */}
      {PHASE_LINES.map((f) => (
        <path
          key={`w-${f.key}`}
          d={toPath(wire.map((pt) => ({ x: pt.x, y: pt.y + f.dy })))}
          fill="none"
          stroke={WIRE_GREY}
          strokeWidth={1.6}
          strokeLinecap="round"
        />
      ))}

      {/* ARIZALI PARCA — YALNIZCA arizali fazin telinde. Faz bilinmiyorsa
          (eski kayit / aday kol) uc iletken de vurgulanir. */}
      {span
        ? PHASE_LINES.map((f, i) => {
            if (!hotPhases[i]) return null;
            const yol = hotPathOf(geo, f.dy);
            if (!yol) return null;
            return (
              <g key={`hot-${f.key}`}>
                <path
                  d={yol}
                  fill="none"
                  stroke={faultColor}
                  strokeWidth={5}
                  strokeLinecap="round"
                  filter="url(#fx-wire-glow)"
                  opacity={active ? 0.3 : 0.12}
                />
                <path
                  d={yol}
                  fill="none"
                  stroke={faultColor}
                  strokeWidth={2.2}
                  strokeLinecap="round"
                  strokeDasharray={hotDash}
                />
              </g>
            );
          })
        : null}

      {/* ---- FAZ ETIKETLERI ----
           Teller solda adlandirilir. "Fazlar gorunsun" isteginin karsiligi
           bu: hangi telin hangi unite oldugu cizimde yaziyor, arizali olan
           kirmizi ve kalin. */}
      {PHASE_LINES.map((f, i) => {
        const arizali = Boolean(span) && active && hotPhases[i] && fazlar.length > 0;
        return (
          <text
            key={`pl-${f.key}`}
            x={PAD_X - ARM_HALF - 8}
            y={f.wireY + 3}
            textAnchor="end"
            fontSize={8.5}
            fontWeight={arizali ? 800 : 600}
            fill={arizali ? "#b91c1c" : GREY}
          >
            {phaseLabels[f.key] ?? f.key}
          </text>
        );
      })}

      {/* ---- DIREKLER ---- */}
      {poleList.map((p, idx) => (
        <StripTower
          key={p.seq}
          x={xOf(idx)}
          hot={active && idx >= hotFrom && idx <= hotTo}
          hotPhases={hotPhases}
          role={p.role}
          onEnter={() => onHover({ kind: "pole", rowKey, key: String(p.seq) })}
          onLeave={() => onHover(null)}
        />
      ))}

      {/* ---- CIHAZLAR ---- */}
      {devices.map((d) => (
        <DeviceMark
          key={d.code}
          d={d}
          base={pointAt(d.pos)}
          tone={active ? d.tone : "idle"}
          alarmSources={active ? row.alarmsByDevice?.[d.code]?.sources ?? [] : []}
          dim={hover?.kind === "device" && hover.rowKey === rowKey && hover.key !== d.code}
          onEnter={() => onHover({ kind: "device", rowKey, key: d.code })}
          onLeave={() => onHover(null)}
        />
      ))}

      {/* ---- DIREK ADLARI ---- */}
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
      {dimA != null && dimB != null && row.zoneStartM != null && row.zoneEndM != null ? (
        <g className="fx-strip-dim" stroke={DIM_INK} fill={DIM_INK}>
          {/* Uzanti cizgileri direge kadar iner: olcunun NEREYI olctugu
              belirsiz kalmasin. */}
          <line x1={dimA} y1={DIM_Y} x2={dimA} y2={PHASE_LINES[0].armY - 6} strokeWidth={0.9} opacity={0.35} />
          <line x1={dimB} y1={DIM_Y} x2={dimB} y2={PHASE_LINES[0].armY - 6} strokeWidth={0.9} opacity={0.35} />
          <line x1={dimA} y1={DIM_Y} x2={dimB} y2={DIM_Y} strokeWidth={1.3} />
          <path d={`M${dimA} ${DIM_Y} l6 -3 v6 z`} stroke="none" />
          <path d={`M${dimB} ${DIM_Y} l-6 -3 v6 z`} stroke="none" />
          <text x={dimA} y={DIM_Y + 12} textAnchor="middle" fontSize={9.5} fontWeight={600} stroke="none" opacity={0.85}>
            {formatDistanceM(row.zoneStartM)}
          </text>
          <text x={dimB} y={DIM_Y + 12} textAnchor="middle" fontSize={9.5} fontWeight={600} stroke="none" opacity={0.85}>
            {formatDistanceM(row.zoneEndM)}
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
              {labels.zoneSpan(formatDistanceM(spanM))}
            </text>
          ) : null}
        </g>
      ) : suspectWholeLine && dimA != null && dimB != null ? (
        // ADAY KOL: metre bilgisi yok — kolun tamaminin suphe altinda oldugu
        // bastan sona kesikli bir olcu seridiyle soylenir.
        <g className="fx-strip-dim" stroke={DIM_INK} fill={DIM_INK}>
          <line
            x1={dimA}
            y1={DIM_Y}
            x2={dimB}
            y2={DIM_Y}
            strokeWidth={1.2}
            strokeDasharray="6 4"
          />
          <text
            x={(dimA + dimB) / 2}
            y={DIM_LABEL_Y}
            textAnchor="middle"
            fontSize={10}
            fontWeight={700}
            stroke="none"
          >
            {labels.wholeLineSuspect}
          </text>
        </g>
      ) : labels.range ? (
        <text
          x={width / 2}
          y={DIM_LABEL_Y}
          textAnchor="middle"
          fontSize={11}
          fontWeight={600}
          fill={INK}
          opacity={0.6}
        >
          {labels.range}
        </text>
      ) : null}
    </g>
  );
}
