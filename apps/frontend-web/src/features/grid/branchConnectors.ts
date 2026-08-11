/**
 * Bransman baglantilari — saf hesap (React'siz, Leaflet'siz).
 *
 * YASANAN SORUN
 * -------------
 * Bir bransman kolu AYRI bir hattir; ana hatta baglandigi yer
 * `Line.branched_from_pole_id` ile tutulur. Ama bu baglantinin kendisi bir
 * `line_segment` DEGILDIR — kol olusturulunca otomatik yaratilmiyor.
 * Sonuc, Hat Yonetimi haritasinda "Diger hatlar" acikken soyleydi:
 *
 *   * TEK direkli kol (BR-2, BR-3, BR-5): polyline en az iki nokta ister,
 *     dolayisiyla HIC cizilmiyordu. Haritada yalnizca yalniz bir gri nokta
 *     kaliyor; neye bagli oldugu gorunmuyordu.
 *   * IKI direkli kol (Z-12_BRS): kendi iki diregi arasi ciziliyor ama ana
 *     hatta baglanmadigi icin havada asili duruyordu.
 *
 * Ikisi de ayni yanilgiyi uretiyor: "bu kol sisteme bagli degil". Oysa kol
 * baglidir; eksik olan yalnizca CIZIM. Ustelik dallanma noktasi sahanin en
 * kritik olcum yerlerinden biri — dala giden akimi goren cihaz, arizanin ana
 * hatta mi kolda mi oldugunu ayirt eder.
 *
 * BU MODUL baglanti parcalarini uretir; cizim ve tiklama davranisi
 * `GridManagementPanel` icinde.
 *
 * NOT: baglanti segmenti backend'de KURULABILIR — `_validate_segment_endpoints`
 * icinde bunun icin acik bir istisna var (from = dallanma diregi). Kol
 * secilip slota cihaz eklenince segment kendiliginde yaratilir. Buradaki
 * `hasSegment` alani "kuruldu mu" sorusunu haritada gosterebilmek icin.
 */

export type Konum = { latitude: number; longitude: number };

export type DirekGirdi = Konum & {
  id: number;
  line_id: number;
  sequence_no: number;
};

export type HatGirdi = {
  id: number;
  name: string;
  branched_from_pole_id?: number | null;
};

export type SegmentGirdi = {
  from_pole_id: number;
  to_pole_id: number;
  device_id?: number | null;
};

export type BransmanBaglantisi = {
  lineId: number;
  lineName: string;
  /** Ana hattaki dallanma diregi. */
  from: [number, number];
  /** Kolun ilk diregi (en kucuk `sequence_no`). */
  to: [number, number];
  parentPoleId: number;
  firstPoleId: number;
  /** Baglanti segmenti kurulmus mu — kurulmadan cihaz baglanamaz. */
  hasSegment: boolean;
  /** Baglanti uzerinde cihaz var mi. */
  hasDevice: boolean;
};

/**
 * Verilen hatlarin bransman baglanti parcalarini uretir.
 *
 * `poles` TUM direkleri icermeli (baglantinin bir ucu BASKA hattadir);
 * yalnizca kolun kendi direkleri verilirse dallanma diregi bulunamaz ve
 * baglanti sessizce dusera.
 */
export function branchConnectors(input: {
  lines: readonly HatGirdi[];
  poles: readonly DirekGirdi[];
  segments: readonly SegmentGirdi[];
}): BransmanBaglantisi[] {
  const { lines, poles, segments } = input;
  const direkler = new Map(poles.map((p) => [p.id, p]));

  // Her hattin ILK diregi: `sequence_no` en kucuk olan. Dizi sirasina
  // guvenmek yanlis olurdu — backend siralamayi garanti etmiyor ve topoloji
  // duzenlenince sira numaralari yeniden atanabiliyor.
  const ilkDirek = new Map<number, DirekGirdi>();
  for (const p of poles) {
    const mevcut = ilkDirek.get(p.line_id);
    if (!mevcut || p.sequence_no < mevcut.sequence_no) ilkDirek.set(p.line_id, p);
  }

  const sonuc: BransmanBaglantisi[] = [];
  for (const hat of lines) {
    const parentId = hat.branched_from_pole_id;
    if (parentId === null || parentId === undefined) continue;

    const parent = direkler.get(parentId);
    const ilk = ilkDirek.get(hat.id);
    // Dallanma diregi silinmis (FK `SET NULL` olmadan once) ya da kolun hic
    // diregi yok: cizilecek bir parca yok.
    if (!parent || !ilk) continue;
    // Kolun ilk diregi dallanma diregiyle AYNI ise cizilecek bir aciklik
    // yok; sifir uzunluklu bir cizgi haritada nokta gibi gorunur ve
    // "burada bir sey var" yanilgisi uretir.
    if (parent.id === ilk.id) continue;
    if (!gecerli(parent) || !gecerli(ilk)) continue;

    const baglantilar = segments.filter(
      (s) => s.from_pole_id === parent.id && s.to_pole_id === ilk.id
    );
    sonuc.push({
      lineId: hat.id,
      lineName: hat.name,
      from: [parent.latitude, parent.longitude],
      to: [ilk.latitude, ilk.longitude],
      parentPoleId: parent.id,
      firstPoleId: ilk.id,
      hasSegment: baglantilar.length > 0,
      hasDevice: baglantilar.some((s) => s.device_id != null)
    });
  }
  return sonuc;
}

function gecerli(p: Konum): boolean {
  // Tek bir NaN, Leaflet'in TUM katmanini sessizce cizilmez yapar.
  return Number.isFinite(p.latitude) && Number.isFinite(p.longitude);
}
