/**
 * Kafes direk silueti — sahnenin temel yapi tasi.
 *
 * NEDEN BU KADAR DETAY: onceki cizim tek dikey cizgi + duz bir traversti;
 * "sema" gibi degil "taslak" gibi duruyordu. Gercek bir havai hat direginin
 * okunur olmasi icin uc sey sart: ASAGI ACILAN kafes govde, CAPRAZ dolgular
 * ve travers uclarindaki IZOLATOR zinciri. Ucu birlikte, sahaya cikan ekibin
 * her gun gordugu siluete karsilik gelir.
 *
 * UC TRAVERS = UC FAZ
 * -------------------
 * Her faz kendi traversine asilir (master ustte, sat01 ortada, sat02 altta).
 * Onceki surumde iki seviye vardi ve iki faz ayni yukseklikte yan yana
 * duruyordu: sarkma egrileri ust uste binip hat tek kalin bir bant gibi
 * okunuyordu. Uc seviye ayrimi hem gercek bir direge benziyor hem de ARIZALI
 * FAZI kendi telinde gostermeyi mumkun kiliyor.
 *
 * TOPRAK TELI YOK: en ustte bos giden dorduncu bir tel ve onun traversi
 * vardi. Uzerinde cihaz yok, arizasi olmuyor, hicbir soruya cevap
 * vermiyordu; buna karsilik her satirdan yer yiyor ve "kac tel var"
 * sorusunu bulandiriyordu.
 *
 * Izolator zincirleri traversin IKI ucunda da var: bir span sol travers
 * ucundan sag travers ucuna gerilir, direk uzerinde ise atlama (jumper)
 * gecer. Tek uctan asmak "tel havada duruyor" hissi veriyordu.
 *
 * OLCEKLE DETAY: cizim yakinlastirilabildigi icin ince cizgiler (kafes
 * caprazlari) ayri bir katmanda ve daha ince; uzaklasinca gri bir doku gibi
 * okunur, yakinlasinca yapi netlesir.
 */
import { ARM_HALF, GROUND_Y, PEAK_Y, PHASE_LINES } from "./faultStripGeometry";

type Props = {
  x: number;
  /** Ariza bolgesi icinde mi — kirmizi cizilir. */
  hot: boolean;
  /** Hangi fazlar arizali (PHASE_LINES sirasinda). Direk ariza bolgesindeyse
   *  yalnizca bu fazlarin izolator zinciri kirmizi olur. */
  hotPhases?: boolean[];
  /** `branch` ise govdenin dibinde dallanma dugumu isaretlenir. */
  role?: string | null;
  onEnter: () => void;
  onLeave: () => void;
};

const GREY = "#8b98a9";
const RED = "#dc2626";
const BRANCH_INK = "#7c3aed";

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

export function StripTower({ x, hot, hotPhases, role, onEnter, onLeave }: Props) {
  const renk = hot ? RED : GREY;
  const kalinlik = hot ? 1.9 : 1.5;

  // Kafes caprazlari: TEPEDEN zemine kadar X dolgular — traverslerin
  // hizasindan da gecer, gercek bir kafes direkte oyle. Yalnizca alt govdeyi
  // doldurmak direkleri tepeden bakinca "iki cizgi" gibi bos birakiyordu.
  // Adim sayisi sabit; zoom'da detay artmasin diye geometri olcekten BAGIMSIZ.
  const capraz: { x1: number; y1: number; x2: number; y2: number }[] = [];
  const adim = 14;
  const govdeBas = PEAK_Y + 4;
  for (let y = govdeBas; y < GROUND_Y - adim; y += adim) {
    const w1 = yariGenislik(y);
    const w2 = yariGenislik(y + adim);
    capraz.push({ x1: x - w1, y1: y, x2: x + w2, y2: y + adim });
    capraz.push({ x1: x + w1, y1: y, x2: x - w2, y2: y + adim });
  }

  const wAlt = yariGenislik(GROUND_Y);
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
        {/* Ayak tabanlari. */}
        <line x1={x - wAlt - 3} y1={GROUND_Y} x2={x - wAlt + 3} y2={GROUND_Y} />
        <line x1={x + wAlt - 3} y1={GROUND_Y} x2={x + wAlt + 3} y2={GROUND_Y} />
        {/* Zemine yakin kusak. */}
        <line
          x1={x - yariGenislik(GROUND_Y - 30)}
          y1={GROUND_Y - 30}
          x2={x + yariGenislik(GROUND_Y - 30)}
          y2={GROUND_Y - 30}
        />

        {/* TEPE — govdenin ucu. Ustunde artik toprak teli YOK; kisa bir
            baslik cizgisi direge bitmislik hissi verir. */}
        <line x1={x - wPeak} y1={PEAK_Y} x2={x + wPeak} y2={PEAK_Y} />

        {/* UC FAZ TRAVERSI — govdeden iki yana. Uclarda hafif yukari donus
            ve govdeye takviye caprazi: gercek traverslerdeki tasiyici. */}
        {PHASE_LINES.map((f) => {
          const wBody = yariGenislik(f.armY);
          return (
            <g key={`arm-${f.key}`}>
              <line x1={x - ARM_HALF} y1={f.armY} x2={x + ARM_HALF} y2={f.armY} />
              <line x1={x - ARM_HALF} y1={f.armY} x2={x - ARM_HALF} y2={f.armY - 3.2} />
              <line x1={x + ARM_HALF} y1={f.armY} x2={x + ARM_HALF} y2={f.armY - 3.2} />
              <line x1={x - ARM_HALF} y1={f.armY} x2={x - wBody} y2={f.armY - 10} />
              <line x1={x + ARM_HALF} y1={f.armY} x2={x + wBody} y2={f.armY - 10} />
            </g>
          );
        })}
      </g>

      {/* IZOLATOR ZINCIRLERI — her traversin IKI ucunda.
          Span sol ucla sag uc arasinda gerilir; direk uzerinde atlama gecer.
          Arizali faz, direk ariza bolgesindeyse kendi zincirinde kirmizi
          gorunur — "hangi faz" bilgisi direkte de okunur. */}
      {PHASE_LINES.map((f, i) => {
        const fazRenk = hot && (hotPhases?.[i] ?? true) ? RED : GREY;
        return (
          <g key={`ins-${f.key}`}>
            <Izolator x={x - ARM_HALF} y1={f.armY} y2={f.wireY} renk={fazRenk} />
            <Izolator x={x + ARM_HALF} y1={f.armY} y2={f.wireY} renk={fazRenk} />
            {/* Atlama (jumper): iki izolator arasini direk uzerinden gecer. */}
            <line
              x1={x - ARM_HALF}
              y1={f.wireY}
              x2={x + ARM_HALF}
              y2={f.wireY}
              stroke={fazRenk}
              strokeWidth={hot && (hotPhases?.[i] ?? true) ? 1.8 : 1.3}
              strokeLinecap="round"
              opacity={0.9}
            />
          </g>
        );
      })}

      {/* BRANSMAN DUGUMU — bu direkten bir kol ayriliyor.
          Kolun kendisi sahnenin ALT SATIRINDA tam bir hat olarak cizilir;
          buradaki dugum "ayrilma noktasi burasi" der ve alt satira inen bag
          tam bu noktadan baslar. Once ince bir cizgi + bos halkaydi ve
          govdenin kafes caprazlari icinde kayboluyordu; artik dolu bir
          dugum + beyaz tas halkasi ile kafesten ayrisir. */}
      {role === "branch" ? (
        <g>
          <line
            x1={x}
            y1={GROUND_Y - 26}
            x2={x}
            y2={GROUND_Y}
            stroke={BRANCH_INK}
            strokeWidth={2.2}
            strokeLinecap="round"
            strokeDasharray="3 3"
          />
          <circle cx={x} cy={GROUND_Y} r={5.2} fill="#fff" opacity={0.95} />
          <circle cx={x} cy={GROUND_Y} r={3.4} fill={BRANCH_INK} />
        </g>
      ) : null}
    </g>
  );
}
