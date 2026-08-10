/**
 * Ariza seridi (hattin yandan gorunusu) GEOMETRISI — saf fonksiyonlar.
 *
 * React'ten AYRI tutuluyor: projenin test kosucusu (esbuild + node:test,
 * jsdom YOK) yalnizca saf mantigi calistirabiliyor ve bu cizimdeki asil risk
 * matematikte — cihaz telin uzerine oturuyor mu, kirmizi parca dogru iki
 * cihazin arasinda mi. Cizim tarafi (SVG) bu modulun ciktisini basar.
 *
 * KOORDINAT MODELI
 * ----------------
 * `pos` = span indeksi + span icindeki oran (0..1). Yani 2.5 = "3. direkle
 * 4. direk arasinin tam ortasi". Hem cihaz konumu hem ariza sinirlari bu tek
 * olcekte ifade edilir; boylece "su cihazdan su cihaza kadar olan tel" bir
 * dilim islemine indirgenir.
 */

/** Cizimdeki bir direk. `seq` zorunlu; ad/rol varsa etiket ve ipucu zenginlesir. */
export type StripPole = {
  seq: number;
  name?: string | null;
  /** `line_start | transit | branch | line_end | cable_transition` */
  role?: string | null;
};

/** Cizimde kullanilacak segment (bir direk araligi + uzerindeki cihaz). */
export type StripSegment = {
  from_pole_seq?: number | null;
  to_pole_seq?: number | null;
  device_code?: string | null;
  device_name?: string | null;
  /** Cihazin segment icindeki konumu (0..1). NULL ise ortaya konur. */
  device_position_t?: number | null;
};

export type StripDevice = {
  code: string;
  label: string;
  /** Global konum: span indeksi + oran. */
  pos: number;
  tone: "red" | "green" | "idle";
  /** Cihazin oturdugu direk araligi — tooltip "Direk 3 – 4 arasi" der. */
  fromSeq: number;
  toSeq: number;
};

export type StripSpan = {
  a: number;
  b: number;
  /** Sinirlar cihazlardan mi geldi (true) yoksa direk araligindan mi (false)? */
  byDevice: boolean;
};

export type StripGeometry = {
  seqs: number[];
  /** `seqs` ile AYNI sirada direk bilgileri (ad/rol). */
  poles: StripPole[];
  width: number;
  /** Iletken poligonu (ornekleme noktalari). */
  wire: { pos: number; x: number; y: number }[];
  devices: StripDevice[];
  span: StripSpan | null;
  xOf: (idx: number) => number;
  pointAt: (pos: number) => { x: number; y: number };
};

// ---- Cizim sabitleri (viewBox koordinati) ---------------------------------
//
// OLCU: onceki surumde bir span 78 birimdi ve serit 118 birim yuksekti; kart
// icinde ~520px'e sikistigi icin direkler birbirine giriyor, cihaz isaretleri
// ust uste biniyordu. Artik span 116 — hat "uzun" okunuyor ve cihaz/faz
// isaretleri birbirine degmiyor. Yukseklik artisi ise ALTTAKI OLCU SERIDI
// icin: mesafe artik metin kutusunda degil, cizimin uzerinde.
export const STRIP_H = 176;
export const PAD_X = 38;
export const SPAN_W = 116;
export const GROUND_Y = 96;
export const CROSSARM_Y = 20;
export const WIRE_Y = 36;
export const SAG = 11;
export const LABEL_Y = 114;
/** Olcu (dimension) cizgisinin y'si — teknik resimdeki kot cizgisi gibi. */
export const DIM_Y = 140;
/** Olcu etiketinin taban cizgisi. */
export const DIM_LABEL_Y = 162;
const SAMPLES = 16;

/**
 * Cizimin SABIT ekran yuksekligi (px).
 *
 * NEDEN SABIT: SVG `width:100%` ile cizildiginde viewBox oranı korunarak
 * esniyordu; yani olcek HATTIN DIREK SAYISINA gore degisiyordu. 6 direkli
 * bir hat kartin genisligine yayilip devasa gorunurken 17 direkli hat ayni
 * alana sikisip minicik kaliyordu — iki ariza karti yan yana
 * KARSILASTIRILAMIYORDU. Artik olcek sabit: bir direk araligi her hatta ayni
 * piksel genisligindedir, cizim sigmazsa yatay kaydirilir.
 */
export const STRIP_PX_H = 188;

/** viewBox birimi -> ekran pikseli. */
export const PX_PER_UNIT = STRIP_PX_H / STRIP_H;

/** Katener yukseklik ofseti: uclarda 0, ortada `SAG`. */
export function sagAt(t: number): number {
  return 4 * SAG * t * (1 - t);
}

type Input = {
  poleSeqs: number[];
  /** Direk ad/rol bilgisi. Verilmezse yalnizca sira numarasi gosterilir. */
  poles?: StripPole[];
  segments?: StripSegment[];
  fromSeq?: number | null;
  toSeq?: number | null;
  lastRedDeviceCode?: string | null;
  firstGreenDeviceCode?: string | null;
};

export function buildStripGeometry({
  poleSeqs,
  poles,
  segments,
  fromSeq,
  toSeq,
  lastRedDeviceCode,
  firstGreenDeviceCode
}: Input): StripGeometry {
  const uniq = Array.from(new Set(poleSeqs)).sort((a, b) => a - b);
  const seqs =
    uniq.length >= 2
      ? uniq
      : (() => {
          // Snapshot yok/eksik — en azindan ariza araligini ciz.
          const a = fromSeq ?? 1;
          const b = toSeq ?? a + 1;
          return a === b ? [a, a + 1] : [Math.min(a, b), Math.max(a, b)];
        })();

  const count = seqs.length;
  const width = Math.max(360, PAD_X * 2 + (count - 1) * SPAN_W);
  const step = count > 1 ? (width - PAD_X * 2) / (count - 1) : 0;
  const xOf = (idx: number) => PAD_X + idx * step;
  const idxOf = (seq: number) => seqs.indexOf(seq);

  const pointAt = (pos: number) => {
    const s = Math.max(0, Math.min(Math.max(0, count - 2), Math.floor(pos)));
    const tt = Math.max(0, Math.min(1, pos - s));
    const x1 = xOf(s);
    const x2 = xOf(s + 1);
    return { x: x1 + (x2 - x1) * tt, y: WIRE_Y + sagAt(tt) };
  };

  // Iletken poligonu — span basina SAMPLES nokta, bitisler tekrarlanmaz.
  const wire: { pos: number; x: number; y: number }[] = [];
  for (let s = 0; s < count - 1; s += 1) {
    const last = s === count - 2 ? SAMPLES : SAMPLES - 1;
    for (let i = 0; i <= last; i += 1) {
      const tt = i / SAMPLES;
      const p = pointAt(s + tt);
      wire.push({ pos: s + tt, x: p.x, y: p.y });
    }
  }
  if (wire.length === 0) wire.push({ pos: 0, x: xOf(0), y: WIRE_Y });

  // Cihazlar — segmentlerdeki gercek konumlariyla.
  const devices: StripDevice[] = [];
  for (const seg of segments ?? []) {
    const code = (seg.device_code ?? "").trim();
    if (!code) continue;
    const a = seg.from_pole_seq;
    const b = seg.to_pole_seq;
    if (a == null || b == null) continue;
    const iA = idxOf(Math.min(a, b));
    const iB = idxOf(Math.max(a, b));
    // Yalnizca KOMSU direkler arasindaki segmentler cizilebilir; atlamali
    // bir kayit (veri bozuklugu) sessizce yanlis yere cihaz koymasin.
    if (iA === -1 || iB === -1 || iB !== iA + 1) continue;
    const tt =
      seg.device_position_t == null ? 0.5 : Math.max(0, Math.min(1, seg.device_position_t));
    devices.push({
      code,
      label: seg.device_name || code,
      pos: iA + tt,
      tone: code === lastRedDeviceCode ? "red" : code === firstGreenDeviceCode ? "green" : "idle",
      fromSeq: seqs[iA],
      toSeq: seqs[iB]
    });
  }
  devices.sort((x, y) => x.pos - y.pos);

  // ARIZALI PARCA: son "gordum" cihazindan ilk "gormedim" cihazina kadar.
  let span: StripSpan | null = null;
  const red = devices.find((d) => d.tone === "red");
  const green = devices.find((d) => d.tone === "green");
  if (red) {
    // Yesil cihaz yoksa ariza hat ucuna kadar suruyor demektir.
    const end = green ? green.pos : count - 1;
    if (end > red.pos) span = { a: red.pos, b: end, byDevice: true };
  }
  if (span === null && fromSeq != null && toSeq != null) {
    // Cihaz bilgisi yok — kaba direk araligina duseriz (eski davranis).
    const iA = idxOf(Math.min(fromSeq, toSeq));
    const iB = idxOf(Math.max(fromSeq, toSeq));
    if (iA !== -1 && iB !== -1 && iB > iA) span = { a: iA, b: iB, byDevice: false };
  }

  // Direk bilgileri seqs ile AYNI sirada; kaydi olmayan direk icin yalnizca
  // sira numarasi tasiyan bir yer tutucu uretilir (cizim hep tam kalsin).
  const bySeq = new Map((poles ?? []).map((p) => [p.seq, p]));
  const poleList: StripPole[] = seqs.map((s) => bySeq.get(s) ?? { seq: s });

  return { seqs, poles: poleList, width, wire, devices, span, xOf, pointAt };
}

/** Ornek noktalari SVG path'ine cevirir. */
export function toPath(pts: { x: number; y: number }[]): string {
  return pts.map((p, i) => `${i === 0 ? "M" : "L"}${p.x.toFixed(1)} ${p.y.toFixed(1)}`).join(" ");
}

/** Arizali parcanin path'i — uclar TAM cihaz konumunda baslar/biter. */
export function hotPathOf(geo: StripGeometry): string {
  if (!geo.span) return "";
  const inner = geo.wire.filter((p) => p.pos > geo.span!.a && p.pos < geo.span!.b);
  return toPath([geo.pointAt(geo.span.a), ...inner, geo.pointAt(geo.span.b)]);
}
