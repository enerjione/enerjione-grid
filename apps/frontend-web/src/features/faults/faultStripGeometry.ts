/**
 * Ariza sahnesi (hatlarin yandan gorunusu) GEOMETRISI — saf fonksiyonlar.
 *
 * React'ten AYRI tutuluyor: projenin test kosucusu (esbuild + node:test,
 * jsdom YOK) yalnizca saf mantigi calistirabiliyor ve bu cizimdeki asil risk
 * matematikte — cihaz telin uzerine oturuyor mu, kirmizi parca dogru iki
 * cihazin arasinda mi, kol dogru direge asilmis mi.
 *
 * KOORDINAT MODELI
 * ----------------
 * `pos` = span indeksi + span icindeki oran (0..1). Yani 2.5 = "3. direkle
 * 4. direk arasinin tam ortasi". Hem cihaz konumu hem ariza sinirlari bu tek
 * olcekte ifade edilir; boylece "su cihazdan su cihaza kadar olan tel" bir
 * dilim islemine indirgenir.
 *
 * ILETKEN TRAVERS UCUNA BAGLANIR
 * ------------------------------
 * Bir span direk EKSENINDEN degil TRAVERS UCUNDAN travers ucuna gerilir
 * (`ARM_HALF` kadar iceriden). Direk uzerindeki iki uc arasi ise duz bir
 * atlama (jumper) ile gecilir. Onceki modelde tel direk ekseninden gecerken
 * izolatorler traversin uclarinda duruyordu: tel havada asili, izolator
 * hicbir seyi tutmuyor gibi gorunuyordu.
 *
 * UC FAZ = UC AYRI YUKSEKLIK
 * --------------------------
 * Uc iletken uc AYRI traverse asilir (master ustte, sat01 ortada, sat02
 * altta). Ayni yukseklikte yan yana dizildiklerinde sarkma egrileri ust uste
 * biniyor ve hat tek kalin bir bant gibi okunuyordu; artik hangi telin hangi
 * faz oldugu tek bakista secilir ve arizali faz kendi telinde kirmizi cizilir.
 *
 * COK SATIRLI SAHNE
 * -----------------
 * Bir ariza tek bir hat parcasinda olmayabilir: ariza bolgesi bir dallanma
 * diregini kapsiyorsa kol da adaydir. `buildFaultScene` bu yuzden ANA HAT +
 * ADAY KOLLARI ust uste satirlar halinde yerlestirir; her satir kendi tam
 * geometrisine sahiptir ve kol, asili oldugu direge dikey bir bagla baglanir.
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
  /** Cihaz bir BRANSMAN GIRISINDE mi oturuyor?
   *
   *  true ise segmentin bir ucu ana hatta, digeri kolun ilk diregindedir.
   *  Cihaz dallanma diregine cizilir ama ANA HAT uzerinde arizali parca
   *  TANIMLAMAZ: gordugu ariza kolun asagisindadir, ana telde degil.
   *  Kirmizi bir ana hat parcasi cizmek ekibi yanlis acikliga gonderirdi. */
  onBranch?: boolean;
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
  /** Iletken poligonu (ornekleme noktalari) — ORTA fazin ekseni. */
  wire: { pos: number; x: number; y: number }[];
  devices: StripDevice[];
  span: StripSpan | null;
  xOf: (idx: number) => number;
  pointAt: (pos: number) => { x: number; y: number };
};

// ---------------------------------------------------------------------------
// SAHNE OLCULERI (viewBox birimi)
// ---------------------------------------------------------------------------
// Bir SATIRIN dizilimi YUKARIDAN ASAGIYA:
//   satir basligi -> olcu seridi -> toprak teli -> uc faz traversi ->
//   kafes govde -> zemin -> direk adlari
//
// OLCU SERIDI EN USTTE: aranacak hat kesimi operatorun ILK okudugu sayi.
// Altta dururken direk adlariyla kol etiketleri arasinda kayboluyordu.
// ---------------------------------------------------------------------------

export const PAD_X = 60;
export const SPAN_W = 132;

/** Satir basligi (hat adi + "N. direkten bransman"). */
export const ROW_TITLE_Y = 13;

/** Olcu (dimension) cizgisi ve etiketi — direklerin USTUNDE. */
export const DIM_LABEL_Y = 26;
export const DIM_Y = 38;

/** Direk tepesi (toprak teli tasiyicisi). */
export const PEAK_Y = 52;
/** Toprak teli traversi ve teli. */
export const GW_ARM_Y = 60;
export const GW_WIRE_Y = 66;

/** UC FAZ TRAVERSI — yukaridan asagiya master / sat01 / sat02. */
export const ARM_YS = [78, 104, 130] as const;
/** Traverse asilan iletken (izolator zinciri boyu kadar asagida). */
export const WIRE_YS = [88, 114, 140] as const;

/** Referans iletken — `pointAt` bunun uzerinde calisir (ORTA faz). */
export const WIRE_Y = WIRE_YS[1];

/** Kafes govdenin zemine bastigi yer. */
export const GROUND_Y = 206;
/** Direk adlari. */
export const LABEL_Y = 222;

/** Bir SATIRIN toplam yuksekligi (baslik dahil). */
export const ROW_H = 238;
/** Iki satir arasindaki dusey adim — arada bag cizgisine yer kalir. */
export const ROW_PITCH = 250;

/** Tek satirlik sahnenin yuksekligi (geriye uyum adi). */
export const STRIP_H = ROW_H;

/** Iletken sarkmasi (katener derinligi). */
export const SAG = 11;

/** Travers yari genisligi — iletken travers UCUNA baglanir. */
export const ARM_HALF = 22;
/** Ust (toprak) travers yari genisligi. */
export const GW_ARM_HALF = 10;

/** Faz anahtarlari — bir SN2 govdesindeki uc sensor, hattin uc fazi. */
export type PhaseKey = "master" | "sat01" | "sat02";

/**
 * UC ILETKEN — yukaridan asagiya.
 *
 * `dy`: referans iletkene (orta faz) gore dusey ofset. Renk TASIMAZLAR:
 * cizimde renk yalnizca DURUM anlatir (gri = saglam, kirmizi = arizali).
 * Hangi telin hangi faz oldugu solda duran faz etiketinden okunur.
 */
export const PHASE_LINES: { key: PhaseKey; armY: number; wireY: number; dy: number }[] = [
  { key: "master", armY: ARM_YS[0], wireY: WIRE_YS[0], dy: WIRE_YS[0] - WIRE_Y },
  { key: "sat01", armY: ARM_YS[1], wireY: WIRE_YS[1], dy: WIRE_YS[1] - WIRE_Y },
  { key: "sat02", armY: ARM_YS[2], wireY: WIRE_YS[2], dy: WIRE_YS[2] - WIRE_Y }
];

const SAMPLES = 16;

/**
 * Bir SATIRIN sabit ekran yuksekligi (px).
 *
 * NEDEN SABIT: SVG `width:100%` ile cizildiginde viewBox orani korunarak
 * esniyordu; yani olcek HATTIN DIREK SAYISINA gore degisiyordu. 6 direkli
 * bir hat kartin genisligine yayilip devasa gorunurken 17 direkli hat ayni
 * alana sikisip minicik kaliyordu — iki ariza karti yan yana
 * KARSILASTIRILAMIYORDU. Artik olcek sabit: bir direk araligi her hatta ayni
 * piksel genisligindedir, cizim sigmazsa yakinlastirilip gezilir.
 */
export const STRIP_PX_H = 300;

/** viewBox birimi -> ekran pikseli. */
export const PX_PER_UNIT = STRIP_PX_H / ROW_H;

/** Cok satirli sahnenin ekranda kaplayabilecegi EN FAZLA yukseklik (px).
 *  Uc-dort satirli bir sahne bunu asarsa kucultulerek sigdirilir; kart
 *  sonsuza kadar uzamaz. */
export const SCENE_MAX_PX_H = 720;

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
  /** Cihaz/aralik bilgisi olmasa bile TUM hat aday sayilsin mi?
   *
   *  Aday kollarda kullanilir: kolun kendi ariza kaydi yoktur ama ana hattaki
   *  ariza dallanma diregini kapsadigi icin kol bastan sona suphelidir. */
  wholeLineHot?: boolean;
};

export function buildStripGeometry({
  poleSeqs,
  poles,
  segments,
  fromSeq,
  toSeq,
  lastRedDeviceCode,
  firstGreenDeviceCode,
  wholeLineHot
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

  // Iletken travers UCUNDAN travers ucuna gerilir; direk uzerindeki iki uc
  // arasi atlama (jumper) ile gecilir. Kisa spanlarda travers payi span'i
  // yutmasin diye ust sinir konur.
  const attach = Math.min(ARM_HALF, step > 0 ? step * 0.28 : ARM_HALF);

  const pointAt = (pos: number) => {
    const s = Math.max(0, Math.min(Math.max(0, count - 2), Math.floor(pos)));
    const tt = Math.max(0, Math.min(1, pos - s));
    const x1 = xOf(s) + attach;
    const x2 = xOf(s + 1) - attach;
    return { x: x1 + (x2 - x1) * tt, y: WIRE_Y + sagAt(tt) };
  };

  // Iletken poligonu — span basina SAMPLES nokta. Span SONU ile bir sonraki
  // span BASI ayni `pos`u paylasir ama farkli x'tedir (biri sol travers ucu,
  // digeri sag): aradaki duz parca direk uzerindeki ATLAMADIR. Bu yuzden
  // noktalar `pointAt` uzerinden uretilmez — `pointAt(s+1)` bir sonraki
  // spanin BASINI dondurur ve span sonu kaybolurdu.
  const wire: { pos: number; x: number; y: number }[] = [];
  // Hat basindaki gerdirme: tel ilk traversin SOL ucunda baslar.
  wire.push({ pos: 0, x: xOf(0) - attach, y: WIRE_Y });
  for (let s = 0; s < count - 1; s += 1) {
    const x1 = xOf(s) + attach;
    const x2 = xOf(s + 1) - attach;
    for (let i = 0; i <= SAMPLES; i += 1) {
      const tt = i / SAMPLES;
      wire.push({ pos: s + tt, x: x1 + (x2 - x1) * tt, y: WIRE_Y + sagAt(tt) });
    }
  }
  // Hat sonundaki gerdirme.
  if (count > 1) wire.push({ pos: count - 1, x: xOf(count - 1) + attach, y: WIRE_Y });
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
    const tone: StripDevice["tone"] =
      code === lastRedDeviceCode ? "red" : code === firstGreenDeviceCode ? "green" : "idle";

    // BRANSMAN GIRISI: bir uc ana hatta, digeri kolun ilk diregi (ana hattin
    // direk listesinde YOK).
    //
    // Bu segmentler "komsu degil" diye tamamen ELENIYORDU. Sonuc sessiz ve
    // agirdi: bransman girisini izleyen cihaz cizimden dusuyor, dolayisiyla
    // "gordum" diyen cihaz bulunamiyor ve `span` null kaliyordu — AKTIF bir
    // ariza karti TERTEMIZ, arizasiz bir hat gosteriyordu.
    //
    // Cihaz artik dallanma diregine cizilir. Ama ana hat uzerinde arizali
    // parca TANIMLAMAZ (bkz. `onBranch`): gordugu ariza kolun asagisindadir.
    const anaUc = iA !== -1 ? iA : iB;
    const digerUc = iA !== -1 ? iB : iA;
    if (anaUc === -1) continue; // iki uc da bu hatta degil: kayit bu cizime ait degil
    if (digerUc === -1) {
      devices.push({
        code,
        label: seg.device_name || code,
        pos: anaUc,
        tone,
        fromSeq: seqs[anaUc],
        toSeq: seqs[anaUc],
        onBranch: true
      });
      continue;
    }

    // Atlamali bir kayit (veri bozuklugu) sessizce yanlis yere cihaz koymasin.
    if (iB !== iA + 1) continue;
    const tt =
      seg.device_position_t == null ? 0.5 : Math.max(0, Math.min(1, seg.device_position_t));
    devices.push({
      code,
      label: seg.device_name || code,
      pos: iA + tt,
      tone,
      fromSeq: seqs[iA],
      toSeq: seqs[iB]
    });
  }
  devices.sort((x, y) => x.pos - y.pos);

  // ARIZALI PARCA: son "gordum" cihazindan ilk "gormedim" cihazina kadar.
  let span: StripSpan | null = null;
  // Bransman girisindeki cihazlar ana hat uzerinde parca TANIMLAMAZ.
  const red = devices.find((d) => d.tone === "red" && !d.onBranch);
  const green = devices.find((d) => d.tone === "green" && !d.onBranch);
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
  if (span === null && wholeLineHot && count > 1) {
    // ADAY KOL: kendi kaydi yok, ama bastan sona suphelidir.
    span = { a: 0, b: count - 1, byDevice: false };
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

/** Arizali parcanin path'i — uclar TAM cihaz konumunda baslar/biter.
 *
 * `dy`: faz ofseti. Uc iletken uc AYRI traverse asili oldugu icin arizali
 * parca da hangi FAZIN telinde ise oraya cizilir; tek bir cizgide gostermek
 * "hangi faz" bilgisini gorselden siler.
 */
export function hotPathOf(geo: StripGeometry, dy = 0): string {
  if (!geo.span) return "";
  const inner = geo.wire.filter((p) => p.pos > geo.span!.a && p.pos < geo.span!.b);
  const kaydir = (pt: { x: number; y: number }) => ({ x: pt.x, y: pt.y + dy });
  return toPath([
    kaydir(geo.pointAt(geo.span.a)),
    ...inner.map(kaydir),
    kaydir(geo.pointAt(geo.span.b))
  ]);
}

// ---------------------------------------------------------------------------
// COK SATIRLI SAHNE
// ---------------------------------------------------------------------------

/** Bir cihazda ACIK olan alarmlarin faz kaynaklari. */
export type StripDeviceAlarms = {
  /** "master" | "sat01" | "sat02" — alarmi olan kaynaklar. */
  sources: string[];
  /** Alarm basliklari (tooltip'te ilki gosterilir). */
  titles: string[];
};

/**
 * Sahnedeki bir HAT SATIRI.
 *
 * Ana hat + ariza bolgesine denk gelen her aday kol icin bir tane uretilir.
 * "Ariza su iki cihaz arasinda" demek, o araliktaki bir dallanma diregine
 * asili kolun da aday oldugu anlamina gelir — ekip hangi kolu gezecegini
 * ancak kolu CIZILMIS gorurse bilir.
 */
export type FaultRowInput = {
  /** Satir anahtari — kol satirlari `parentKey` ile buna baglanir. */
  key: string;
  kind: "main" | "branch";
  lineId?: number | null;
  /** Hat adi — satirin solunda yazar. */
  title: string;
  poleSeqs: number[];
  poles?: StripPole[];
  segments?: StripSegment[];
  fromSeq?: number | null;
  toSeq?: number | null;
  lastRedDeviceCode?: string | null;
  firstGreenDeviceCode?: string | null;
  zoneStartM?: number | null;
  zoneEndM?: number | null;
  /** Arizali faz kaynaklari. Bos = bilinmiyor (uc tel de vurgulanir). */
  faultPhases?: string[];
  alarmsByDevice?: Record<string, StripDeviceAlarms>;
  /** Ust satirin anahtari (kol satirlarinda). */
  parentKey?: string | null;
  /** Ust satirdaki dallanma diregi (sequence_no). */
  parentSeq?: number | null;
  /** Dallanma direginin adi — bag etiketinde gosterilir. */
  parentPoleName?: string | null;
  /** Kolun KENDI acik ariza kaydi var mi (dogrulandi) yoksa aday mi? */
  confirmed?: boolean;
  /** Satir aktif bir arizaya mi ait (kirmizi) yoksa gecmise mi (gri)? */
  active?: boolean;
};

export type FaultRow = FaultRowInput & {
  index: number;
  geo: StripGeometry;
  /** Satirin sahne icindeki sol-ust kosesi. */
  x0: number;
  y0: number;
  /** Ust satirdaki dallanma direginin sahne koordinati (kol satirlarinda). */
  link: { fromX: number; fromY: number; toX: number; toY: number } | null;
};

export type FaultScene = {
  rows: FaultRow[];
  width: number;
  height: number;
};

/**
 * Sahnede AYRI BIR SATIR olarak cizilecek aday bransman kolu.
 *
 * "Aday" olmasinin iki sebebi olabilir: kolun KENDI acik ariza kaydi vardir
 * (`confirmed`) ya da ana hattaki ariza bolgesi kolun dallanma diregini
 * kapsiyordur — kol da enerjisiz kalmis olabilir, ekip orayi da gezmeli.
 */
export type StripBranchRow = {
  lineId: number;
  name: string;
  /** Hangi satirdan ayriliyor: null/undefined = ana hat, degilse ust kolun id'si. */
  parentLineId?: number | null;
  /** Ust satirdaki dallanma diregi (sequence_no). */
  atSeq: number;
  /** Dallanma direginin adi — satir alt basliginda gecer. */
  atPoleName?: string | null;
  poleSeqs: number[];
  poles?: StripPole[];
  segments?: StripSegment[];
  /** Kolun KENDI ariza kaydindan gelen sinirlar (varsa). */
  fromSeq?: number | null;
  toSeq?: number | null;
  lastRedDeviceCode?: string | null;
  firstGreenDeviceCode?: string | null;
  zoneStartM?: number | null;
  zoneEndM?: number | null;
  faultPhases?: string[];
  alarmsByDevice?: Record<string, StripDeviceAlarms>;
  /** Kolda kendi acik ariza kaydi var mi — bolge KESIN cizilir. */
  confirmed: boolean;
};

/**
 * Satirlari ust uste yerlestirir ve her kolu asili oldugu direge baglar.
 *
 * Kol satiri, dallanma direginin TAM ALTINDAN baslar (`x0` buna gore kayar):
 * bag cizgisi dikey iner ve "bu kol su direkten ciktiyor" iliskisi cizimden
 * okunur — metin aciklamasina gerek kalmaz.
 */
export function buildFaultScene(inputs: FaultRowInput[]): FaultScene {
  const rows: FaultRow[] = [];
  let y = 0;

  for (const [index, input] of inputs.entries()) {
    const geo = buildStripGeometry({
      poleSeqs: input.poleSeqs,
      poles: input.poles,
      segments: input.segments,
      fromSeq: input.fromSeq,
      toSeq: input.toSeq,
      lastRedDeviceCode: input.lastRedDeviceCode,
      firstGreenDeviceCode: input.firstGreenDeviceCode,
      // Aday kol: kendi kaydi yoksa bastan sona suphelidir.
      wholeLineHot: input.kind === "branch" && !input.confirmed
    });

    let x0 = 0;
    let link: FaultRow["link"] = null;
    const parent = input.parentKey ? rows.find((r) => r.key === input.parentKey) : undefined;
    if (parent && input.parentSeq != null) {
      const idx = parent.geo.seqs.indexOf(input.parentSeq);
      if (idx !== -1) {
        const anchorX = parent.x0 + parent.geo.xOf(idx);
        // Kolun ILK diregi (yerel x = PAD_X) dallanma direginin altina gelsin.
        x0 = Math.max(0, anchorX - PAD_X);
        link = {
          fromX: anchorX,
          fromY: parent.y0 + GROUND_Y,
          toX: x0 + PAD_X,
          toY: y + PEAK_Y
        };
      }
    }

    rows.push({ ...input, index, geo, x0, y0: y, link });
    y += ROW_PITCH;
  }

  const width = rows.reduce((en, r) => Math.max(en, r.x0 + r.geo.width), 360);
  const height = rows.length > 0 ? rows[rows.length - 1].y0 + ROW_H : ROW_H;
  return { rows, width, height };
}
