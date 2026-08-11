/**
 * ADAY HAT KESIMLERI — arizanin bulunabilecegi TUM kollari cikarir.
 *
 * NEDEN: "Ariza su iki cihaz arasinda" cumlesi tek bir tel parcasini
 * gostermiyor. O araligin icindeki bir dallanma diregine asili kol da ayni
 * anda enerjisiz kalir; ariza ana hatta da olabilir o kolda da. Harita bunu
 * zaten iki ayri kirmizi kesik olarak ciziyordu, sema ciziyordu ama sadece
 * ana hatti. Ekip hangi kollari gezecegini cizimden okuyamiyordu.
 *
 * Bu modul ariza bolgesinden baslayip asagi dogru yurur:
 *   - Ana hattaki ariza araliginda dallanan her kol ADAYDIR.
 *   - Bir kolun KENDI acik ariza kaydi varsa aday alan o kaydin araligina
 *     daralir (kol icinde nerede oldugunu cihazlar soyluyor).
 *   - Kendi kaydi yoksa kol BASTAN SONA adaydir; dolayisiyla o kolun
 *     altindaki alt kollar da adaydir (hepsi ayni beslemeden dusmustur).
 *
 * React'ten AYRI: bu bir topoloji yurumesi ve yanlis yurumek ekibi olmayan
 * bir hatta gonderir; node:test ile dogrulanabilsin diye saf tutuldu.
 */
import { buildStripGeometry } from "./faultStripGeometry";
import type { StripBranchRow } from "./faultStripGeometry";

export type BranchScanLine = {
  id: number;
  name: string;
  branched_from_pole_id?: number | null;
};

export type BranchScanPole = {
  id: number;
  line_id: number;
  sequence_no: number;
  name?: string | null;
  topology_role?: string | null;
};

export type BranchScanSegment = {
  line_id: number;
  from_pole_seq?: number | null;
  to_pole_seq?: number | null;
  device_code?: string | null;
  device_name?: string | null;
  device_position_t?: number | null;
};

export type BranchScanFault = {
  line_id: number;
  from_pole_seq?: number | null;
  to_pole_seq?: number | null;
  last_red_device_code?: string | null;
  first_green_device_code?: string | null;
  zone_start_m?: number | null;
  zone_end_m?: number | null;
  trigger_alarms?: {
    device_code?: string | null;
    signal_source?: string | null;
    title?: string | null;
  }[];
};

type Input = {
  lines: BranchScanLine[];
  poles: BranchScanPole[];
  segments: BranchScanSegment[];
  /** Cizilen ariza kaydi (ana satir). */
  fault: BranchScanFault;
  /** line_id -> o hatta ACIK ariza kaydi. Kolun kendi kaydi varsa bolge
   *  kesinlesir; yoksa kol bastan sona aday kalir. */
  openFaultByLine: Map<number, BranchScanFault>;
  /** O AN alarmi olan cihaz KODLARI (haritadaki kural: `!reset` ve
   *  `produces_fault !== false`). Kolun kendi ariza kaydi olmasa bile
   *  uzerindeki cihazlarin ne dedigi buradan okunur. */
  alarmedDeviceCodes?: Set<string>;
  /** Cizime sigacak en fazla kol satiri. */
  maxRows?: number;
};

/** Sahnede ana hattin altinda cizilecek en fazla kol satiri.
 *
 *  Ust sinir GORSEL bir karar: her satir tam bir hat cizimi ve dorttan
 *  fazlasi kartta okunamayacak kadar kucuk kaliyor. Sinira takilanlar
 *  sessizce dusmez — sayilari cizimde "+N" olarak yazar. */
export const MAX_BRANCH_ROWS = 4;

/** Ariza kaydindan cihaz basina alarm ozeti + arizali faz listesi. */
function alarmOzeti(f: BranchScanFault): {
  alarmsByDevice: StripBranchRow["alarmsByDevice"];
  faultPhases: string[];
} {
  const alarmsByDevice: NonNullable<StripBranchRow["alarmsByDevice"]> = {};
  const fazlar = new Set<string>();
  for (const a of f.trigger_alarms ?? []) {
    const code = (a.device_code ?? "").trim();
    if (a.signal_source) fazlar.add(a.signal_source);
    if (!code) continue;
    const entry = (alarmsByDevice[code] ??= { sources: [], titles: [] });
    if (a.signal_source && !entry.sources.includes(a.signal_source)) {
      entry.sources.push(a.signal_source);
    }
    const baslik = (a.title ?? "").trim();
    if (baslik && !entry.titles.includes(baslik)) entry.titles.push(baslik);
  }
  return { alarmsByDevice, faultPhases: Array.from(fazlar) };
}

export function buildBranchRows({
  lines,
  poles,
  segments,
  fault,
  openFaultByLine,
  alarmedDeviceCodes,
  maxRows = MAX_BRANCH_ROWS
}: Input): { rows: StripBranchRow[]; hidden: number } {
  /** dallanma diregi id -> o direkten cikan kollar. */
  const cocukKollar = new Map<number, BranchScanLine[]>();
  for (const ln of lines) {
    const anchorId = ln.branched_from_pole_id;
    if (!anchorId) continue;
    const arr = cocukKollar.get(anchorId);
    if (arr) arr.push(ln);
    else cocukKollar.set(anchorId, [ln]);
  }

  const direkler = new Map<number, BranchScanPole[]>();
  for (const p of poles) {
    const arr = direkler.get(p.line_id);
    if (arr) arr.push(p);
    else direkler.set(p.line_id, [p]);
  }
  for (const arr of direkler.values()) arr.sort((a, b) => a.sequence_no - b.sequence_no);

  const segmentler = new Map<number, BranchScanSegment[]>();
  for (const s of segments) {
    const arr = segmentler.get(s.line_id);
    if (arr) arr.push(s);
    else segmentler.set(s.line_id, [s]);
  }

  const direkleriniAl = (lineId: number) => direkler.get(lineId) ?? [];
  const segmentleriniAl = (lineId: number) => segmentler.get(lineId) ?? [];

  /**
   * ARIZA BOLGESINDEKI DIREKLER — cizimin KIRMIZI boyadigi direklerin AYNISI.
   *
   * NEDEN CIZIMDEN TURETILIYOR
   * --------------------------
   * Once bolge dogrudan `from_pole_seq`..`to_pole_seq` araligiydi. Bu iki
   * alan ariza bolgesini CEVRELER, icindekileri vermez; ustelik cizim (ve
   * harita) bolgeyi CIHAZLARDAN turetir: son "gordum" diyen cihazdan ilk
   * "gormedim" diyene kadar, gormeyen yoksa hat ucuna kadar.
   *
   * Iki farkli hesap iki farkli cevap veriyordu ve sonuc sahada goruluyordu:
   * cihazin YUKARI tarafinda kalan — yani haritada yemyesil duran — bir
   * direge asili kol, semada "kontrol edilmeli" diye isaretleniyordu. Ekip
   * arizasiz bir kolu gezmeye gidiyordu.
   *
   * Artik tek kaynak var: `buildStripGeometry`. Bir kol, asili oldugu direk
   * cizimde KIRMIZI ise adaydir. Cizim ile liste tanim geregi ayrisamaz.
   */
  const bolgeDirekleri = (lineId: number, f: BranchScanFault | null): Set<number> | null => {
    const direkler = direkleriniAl(lineId);
    if (direkler.length === 0) return new Set();
    // Kendi kaydi yok: kol bastan sona supheli (null = sinirsiz).
    if (!f) return null;
    const geo = buildStripGeometry({
      poleSeqs: direkler.map((p) => p.sequence_no),
      segments: segmentleriniAl(lineId),
      fromSeq: f.from_pole_seq,
      toSeq: f.to_pole_seq,
      lastRedDeviceCode: f.last_red_device_code,
      firstGreenDeviceCode: f.first_green_device_code
    });
    const kume = new Set<number>();
    // Arizayi goren cihaz bir bransman girisindeyse ana hatta kirmizi bir
    // parca YOKTUR ama o kol kesinlikle adaydir — hatta tek adaydir.
    if (geo.branchTapSeq != null) {
      kume.add(geo.branchTapSeq);
      return kume;
    }
    if (!geo.span) return kume;
    // Cizimdeki "sicak direk" araligiyla BIREBIR ayni (bkz. FaultStripRow).
    const lo = Math.ceil(geo.span.a);
    const hi = Math.floor(geo.span.b);
    for (let i = lo; i <= hi; i += 1) {
      const seq = geo.seqs[i];
      if (seq != null) kume.add(seq);
    }
    return kume;
  };

  /**
   * KOLUN KENDI CIHAZLARI NE DIYOR?
   *
   * Aday kolun ayri bir ariza kaydi olmayabilir; bu, "kol hakkinda hicbir
   * sey bilmiyoruz" demek DEGILDIR. Kolun uzerindeki cihazlar o anda alarm
   * veriyor mu, veriyorsa hangisi — bu bilgi zaten elimizde.
   *
   * Kol bastan sona kirmizi cizilirken uzerindeki "ariza gormedim" diyen
   * cihaz yok sayiliyordu: o cihazdan asagisi kesinlikle saglamken cizim
   * kolun tamamini aday gosteriyor, ekip bosuna geziyordu.
   *
   * Kural ANA HATTAKIYLE ayni (ve harita ile ayni): son "gordum" diyen
   * cihazdan ilk "gormedim" diyene kadar. Alarmi olmayan cihaz "gormedim"
   * sayilir — sistemin her yerinde gecerli olan sozlesme budur.
   */
  const kolSinirlari = (
    lineId: number,
    girisKodu: string | null
  ): { red: string | null; green: string | null; girisTemiz: boolean } => {
    if (!alarmedDeviceCodes) return { red: null, green: null, girisTemiz: false };
    const kendiSeqleri = new Set(direkleriniAl(lineId).map((p) => p.sequence_no));
    // GIRIS CIHAZI HER ZAMAN ILK SIRADA: bag teli kolun basindan once gelir.
    const sirali = segmentleriniAl(lineId)
      .filter((sg) => (sg.device_code ?? "").trim())
      .filter(
        (sg) =>
          kendiSeqleri.has(sg.from_pole_seq ?? -1) && kendiSeqleri.has(sg.to_pole_seq ?? -1)
      )
      .slice()
      .sort((a, b) => (a.from_pole_seq ?? 0) - (b.from_pole_seq ?? 0));

    const zincir = girisKodu ? [girisKodu, ...sirali.map((sg) => (sg.device_code ?? "").trim())]
                             : sirali.map((sg) => (sg.device_code ?? "").trim());
    let red: string | null = null;
    let green: string | null = null;
    for (const kod of zincir) {
      if (alarmedDeviceCodes.has(kod)) {
        red = kod;
        green = null; // sonraki "gormedim" arayisi bu cihazdan SONRA baslar
      } else if (green === null) {
        green = kod;
      }
    }
    // Giris cihazi "gormedim" diyorsa arizanin kolda olmasi MUMKUN DEGIL:
    // supheli olan yalnizca bag telinin o cihaza kadarki parcasi.
    const girisTemiz = Boolean(girisKodu) && green === girisKodu;
    return {
      red: red === girisKodu ? null : red,
      green: green === girisKodu ? null : green,
      girisTemiz
    };
  };

  /** Kolun GIRIS segmenti: bir ucu ana hatta, digeri kolun ilk direginde. */
  const girisSegmenti = (lineId: number): BranchScanSegment | null => {
    const kendiSeqleri = new Set(direkleriniAl(lineId).map((p) => p.sequence_no));
    for (const sg of segmentleriniAl(lineId)) {
      if (!(sg.device_code ?? "").trim()) continue;
      const a = kendiSeqleri.has(sg.from_pole_seq ?? -1);
      const b = kendiSeqleri.has(sg.to_pole_seq ?? -1);
      if (a !== b) return sg;
    }
    return null;
  };

  /** Verilen direk kumesinden dallanan kollar. `null` = tum hat. */
  const araliktakiKollar = (lineId: number, bolge: Set<number> | null) => {
    const cikti: { line: BranchScanLine; pole: BranchScanPole }[] = [];
    for (const p of direkleriniAl(lineId)) {
      if (bolge && !bolge.has(p.sequence_no)) continue;
      for (const kol of cocukKollar.get(p.id) ?? []) cikti.push({ line: kol, pole: p });
    }
    return cikti;
  };

  // GENISLIK ONCELIKLI yuruyus: ust satirlar cizimde her zaman alt
  // satirlardan ONCE gelmeli (kol, bagli oldugu satira baglanacak).
  type Gorev = {
    line: BranchScanLine;
    pole: BranchScanPole;
    parentLineId: number | null;
  };
  const kuyruk: Gorev[] = araliktakiKollar(
    fault.line_id,
    bolgeDirekleri(fault.line_id, fault)
  ).map((k) => ({ line: k.line, pole: k.pole, parentLineId: null }));

  const gorulen = new Set<number>([fault.line_id]);
  const rows: StripBranchRow[] = [];
  let hidden = 0;

  while (kuyruk.length > 0) {
    const gorev = kuyruk.shift()!;
    if (gorulen.has(gorev.line.id)) continue; // dongusel topolojiye karsi
    gorulen.add(gorev.line.id);

    if (rows.length >= maxRows) {
      hidden += 1;
      continue;
    }

    const kolDirekleri = direkleriniAl(gorev.line.id);
    const kendiKaydi = openFaultByLine.get(gorev.line.id) ?? null;
    const giris = girisSegmenti(gorev.line.id);
    const girisKodu = giris ? (giris.device_code ?? "").trim() : null;
    const kolSiniri = kendiKaydi
      ? { red: null, green: null, girisTemiz: false }
      : kolSinirlari(gorev.line.id, girisKodu);
    // Ariza kaydi "bu giris cihazi gordu" diyorsa kol KESINLIKLE supheli:
    // canli alarm listesi gecikmis olsa bile kayit baglayicidir.
    if (girisKodu && fault.last_red_device_code === girisKodu) {
      kolSiniri.girisTemiz = false;
    }
    const ozet = kendiKaydi
      ? alarmOzeti(kendiKaydi)
      : { alarmsByDevice: undefined, faultPhases: [] };

    rows.push({
      lineId: gorev.line.id,
      name: gorev.line.name,
      parentLineId: gorev.parentLineId,
      atSeq: gorev.pole.sequence_no,
      atPoleName: gorev.pole.name ?? null,
      atPoleId: gorev.pole.id,
      poleSeqs: kolDirekleri.map((p) => p.sequence_no),
      poles: kolDirekleri.map((p) => ({
        seq: p.sequence_no,
        name: p.name ?? null,
        role: p.topology_role ?? null
      })),
      // Giris segmenti kolun ICINDE degil: cihazi bag telinin uzerinde
      // cizilir (`linkDevice`), kolun span'lerinden birine oturtulmaz.
      segments: segmentleriniAl(gorev.line.id).filter((sg) => sg !== giris),
      linkDevice: girisKodu
        ? {
            code: girisKodu,
            label: (giris?.device_name ?? "").trim() || girisKodu,
            // Once ARIZA KAYDI: bu ariza icin "gordum/gormedim" diyen
            // cihazlar orada yaziyor ve baglayici olan o. Kayitta gecmiyorsa
            // canli alarm durumuna bakilir.
            tone:
              fault.last_red_device_code === girisKodu
                ? "red"
                : fault.first_green_device_code === girisKodu
                  ? "green"
                  : !alarmedDeviceCodes
                    ? "idle"
                    : alarmedDeviceCodes.has(girisKodu)
                      ? "red"
                      : "green"
          }
        : null,
      fromSeq: kendiKaydi?.from_pole_seq ?? null,
      toSeq: kendiKaydi?.to_pole_seq ?? null,
      // Kendi kaydi yoksa sinirlar kolun KENDI cihazlarindan cikar.
      lastRedDeviceCode: kendiKaydi?.last_red_device_code ?? kolSiniri.red,
      firstGreenDeviceCode: kendiKaydi?.first_green_device_code ?? kolSiniri.green,
      zoneStartM: kendiKaydi?.zone_start_m ?? null,
      zoneEndM: kendiKaydi?.zone_end_m ?? null,
      faultPhases: ozet.faultPhases,
      alarmsByDevice: ozet.alarmsByDevice,
      confirmed: Boolean(kendiKaydi),
      // Giris cihazi "gormedim" dediyse kolun kendisi kesinlikle saglam;
      // yalnizca bag telinin o cihaza kadarki parcasi supheli kalir.
      clearedByLink: kolSiniri.girisTemiz
    });

    // ALT KOLLAR: kolun kendi kaydi varsa yalnizca o kaydin bolgesindekiler,
    // yoksa kolun tamami supheli oldugu icin hepsi.
    const altBolge = bolgeDirekleri(
      gorev.line.id,
      kendiKaydi ??
        (kolSiniri.red || kolSiniri.green
          ? {
              line_id: gorev.line.id,
              last_red_device_code: kolSiniri.red,
              first_green_device_code: kolSiniri.green
            }
          : null)
    );
    for (const alt of araliktakiKollar(gorev.line.id, altBolge)) {
      if (gorulen.has(alt.line.id)) continue;
      kuyruk.push({ line: alt.line, pole: alt.pole, parentLineId: gorev.line.id });
    }
  }

  return { rows, hidden };
}
