/**
 * Ariza sicaklik haritasinin SAF cekirdegi — Leaflet'siz, DOM'suz.
 *
 * NEDEN YENI KUTUPHANE YOK
 * ------------------------
 * `leaflet.heat` hazir duruyor ama bu veriye uymuyor. O eklenti binlerce
 * noktali yogunluk bulutlari icin yazildi ve yogunlugu "en yogun hucre = 1.0"
 * diye kendi normalize eder. Bizde nokta sayisi ONLARCA (ariza gormus direk
 * sayisi kadar) ve agirliklar tam sayi ariza adedi. Onun normalizasyonu bu
 * olcekte iki bozuk sonuc uretir:
 *
 *   * Tek bir direkte 30 ariza varsa geri kalan HER SEY soguk gorunur —
 *     "ikinci en sorunlu bolge" ekranda yok olur.
 *   * Butun noktalar 1'er arizaysa hepsi kirmizi yanar — "her yer yaniyor"
 *     diye okunur, oysa hicbir yerde tekrar eden bir sorun yoktur.
 *
 * Ikisi de BAKIM BUTCESINI yanlis yonlendirir. Normalizasyon kararini
 * disariya birakamayacak kadar onemli, o yuzden burada acikca veriliyor.
 *
 * KARARLAR
 * --------
 * 1. Olcek KARAKOKLU. Lineerde tek bir uc deger (30 ariza) haritayi ezer;
 *    logaritmada 1 ile 2 ariza arasindaki fark abartilir. Karekok ikisinin
 *    arasi: 1 ariza gorunur kalir, 30 ariza baskin ama tek basina degildir.
 *
 * 2. Taban zemin (FLOOR) var. Sifira yakin alfa ile cizilen bir nokta hic
 *    cizilmemis gibi gorunur; "burada 1 ariza oldu" bilgisi kaybolur.
 *    En dusuk agirlik bile GORUNUR bir iz birakir.
 *
 * 3. Tekil bir nokta ASLA "en sicak" rengi almaz. Tek noktali (ya da tum
 *    agirliklari esit) bir veri kumesinde her sey max'a esitlenir ve harita
 *    baştan sona kirmizi olurdu. `spread` bunu yakalar: fark yoksa olcek
 *    orta banda sikistirilir.
 */

export type HeatPoint = {
  latitude: number;
  longitude: number;
  /** Ariza adedi. 0 ve negatif deger cizilmez. */
  weight: number;
};

/** En dusuk agirligin alacagi yogunluk. Altinda nokta gorunmez olur. */
export const HEAT_FLOOR = 0.28;

/** Agirliklar arasinda fark yokken kullanilan sabit yogunluk.
 *  Ne soguk (gorunmez) ne sicak (yanlis alarm) — notr orta band. */
export const HEAT_FLAT = 0.55;

/**
 * Agirliklari 0..1 yogunluga cevirir.
 *
 * Donen dizi girdiyle AYNI sirada ve AYNI uzunluktadir; gecersiz nokta
 * `null` doner (cagiran atlar). Filtreleme burada yapilmaz cunku cagiran
 * tarafin indeksi (tooltip verisi) girdiyle hizali kalmali.
 */
export function heatIntensities(points: readonly HeatPoint[]): (number | null)[] {
  const gecerli = points.filter(isGecerli);
  if (gecerli.length === 0) return points.map(() => null);

  const kokler = gecerli.map((p) => Math.sqrt(p.weight));
  const enAz = Math.min(...kokler);
  const enCok = Math.max(...kokler);
  const spread = enCok - enAz;

  return points.map((p) => {
    if (!isGecerli(p)) return null;
    // Tum agirliklar esit: "hepsi maksimum" yalanini uretmemek icin
    // herkes ayni notr banda oturur.
    if (spread <= 1e-9) return HEAT_FLAT;
    const oran = (Math.sqrt(p.weight) - enAz) / spread;
    return HEAT_FLOOR + oran * (1 - HEAT_FLOOR);
  });
}

function isGecerli(p: HeatPoint): boolean {
  return (
    Number.isFinite(p.latitude) &&
    Number.isFinite(p.longitude) &&
    Number.isFinite(p.weight) &&
    p.weight > 0
  );
}

/**
 * Yogunluk -> renk duraklari (soguk mavi -> sari -> kirmizi).
 *
 * Yesil BILEREK yok. Bu projede yesil "iyi/normal" demek (cihaz durumu,
 * alarm seviyeleri hep oyle). Bir isi haritasinda orta yogunlugu yesil
 * yapmak "orta = sorun yok" diye okunur; oysa orta band da arizadir.
 */
export const HEAT_STOPS: { at: number; color: string }[] = [
  { at: 0.0, color: "rgba(37, 99, 235, 0)" },
  { at: 0.25, color: "rgba(37, 99, 235, 0.55)" },
  { at: 0.5, color: "rgba(234, 179, 8, 0.72)" },
  { at: 0.75, color: "rgba(234, 88, 12, 0.82)" },
  { at: 1.0, color: "rgba(190, 18, 60, 0.92)" }
];

/**
 * Yogunluga karsilik gelen tekil renk — lejant ve nokta cemberi icin.
 * Canvas gradyaninin okudugu duraklarla AYNI tablodan turer ki lejant
 * haritayla uyusmasin diye ayri ayri elde ayarlanmasin.
 */
export function heatColor(intensity: number): string {
  // `Math.max(0, NaN)` NaN doner; kelepceleme tek basina yetmez. NaN bir
  // renk dizesine sizarsa Leaflet onu gecersiz sayip isaretciyi varsayilan
  // renkle cizer — nokta sicak gorunurken aslinda olcusuzdur.
  const x = Number.isFinite(intensity) ? Math.min(1, Math.max(0, intensity)) : 0;
  let alt = HEAT_STOPS[1];
  let ust = HEAT_STOPS[HEAT_STOPS.length - 1];
  for (let i = 1; i < HEAT_STOPS.length; i += 1) {
    if (x <= HEAT_STOPS[i].at) {
      alt = HEAT_STOPS[i - 1];
      ust = HEAT_STOPS[i];
      break;
    }
  }
  const genislik = ust.at - alt.at;
  const t = genislik <= 0 ? 0 : (x - alt.at) / genislik;
  return karistir(alt.color, ust.color, t);
}

function ayikla(rgba: string): [number, number, number, number] {
  const m = rgba.match(/rgba?\(([^)]+)\)/);
  if (!m) return [0, 0, 0, 1];
  const parts = m[1].split(",").map((s) => Number(s.trim()));
  return [parts[0] ?? 0, parts[1] ?? 0, parts[2] ?? 0, parts[3] ?? 1];
}

function karistir(a: string, b: string, t: number): string {
  const [ar, ag, ab, aa] = ayikla(a);
  const [br, bg, bb, ba] = ayikla(b);
  const yuvarla = (x: number, y: number) => Math.round(x + (y - x) * t);
  const alfa = Number((aa + (ba - aa) * t).toFixed(3));
  return `rgba(${yuvarla(ar, br)}, ${yuvarla(ag, bg)}, ${yuvarla(ab, bb)}, ${alfa})`;
}

/**
 * Sebeke cizgileri: direk cifti -> cizilebilir parca.
 *
 * NEDEN HARITADA CIZGI VAR
 * ------------------------
 * Bulanik bir leke "buralarda bir sorun var" der ama NEREDE oldugunu
 * soylemez. Bos bir zemin uzerindeki sicak nokta, operatore ancak koordinat
 * kadar sey anlatir; hat cizildiginde ise "su fiderin ortasinda, dallanmanin
 * hemen oncesinde" olur. Sahaya ekip gonderme karari bu ikinci cumleyle
 * verilir.
 *
 * TOPOLOJI DIREKLERDEN KURULUR, `line_segments`TEN DEGIL
 * ------------------------------------------------------
 * Onceki surum cizgileri `line_segments` kayitlarindan uretiyordu ve saha
 * haritasi BOS cikiyordu. Sebep: `LineSegment` bir CIHAZ YERLESIMI kaydidir
 * (bkz. models/grid_topology.py) — cihaz atanmamis bir aciklik icin satir
 * HIC olusmaz. Yani cizim, telin nerede oldugunu degil cihazin nereye
 * takildigini gosteriyordu.
 *
 * Dogru kaynak, anasayfa haritasinin kullandigi kaynakla AYNI olmali
 * (features/map/DeviceMapTab.tsx): hat basina direkler `sequence_no` ile
 * siralanir ve ardisik direkler birlestirilir. Iki ekranin ayni sebekeyi
 * farkli cizmesi, operatorun hangisine inanacagini bilememesi demekti.
 *
 * BRANSMANLAR da baglanir: `branched_from_pole_id` dolu bir hat o ana
 * direkten baslar. Bag cizilmezse kol havada asili durur ve "kopuk hat"
 * gibi okunur.
 *
 * Her aciklik AYRI bir parca olarak doner (zincir kurulmaz): tek bir
 * polyline yapmak, dallanma direginde olmayan bir teli cizerdi.
 */
export type GeoNokta = { latitude: number; longitude: number };

export type CizgiDirek = GeoNokta & {
  id: number;
  line_id: number;
  sequence_no: number;
};

export type CizgiHat = {
  id: number;
  branched_from_pole_id?: number | null;
};

export function sebekeCizgileri(
  poles: readonly CizgiDirek[],
  lines: readonly CizgiHat[] = []
): [number, number][][] {
  const parcalar: [number, number][][] = [];
  const direk = new Map(poles.map((p) => [p.id, p]));

  /** Bozuk koordinat parcayi DUSURUR: tek bir NaN, Leaflet'in TUM
   *  katmanini sessizce cizilmez yapar — sadece o parca degil, hepsi. */
  const ekle = (a: GeoNokta | undefined, b: GeoNokta | undefined) => {
    if (!a || !b) return;
    if (!kordinatGecerli(a) || !kordinatGecerli(b)) return;
    parcalar.push([
      [a.latitude, a.longitude],
      [b.latitude, b.longitude]
    ]);
  };

  // 1) Hat govdeleri: ardisik direkler (sequence_no sirali).
  const hattaGore = new Map<number, CizgiDirek[]>();
  for (const p of poles) {
    const arr = hattaGore.get(p.line_id);
    if (arr) arr.push(p);
    else hattaGore.set(p.line_id, [p]);
  }
  for (const arr of hattaGore.values()) {
    arr.sort((a, b) => a.sequence_no - b.sequence_no);
    for (let i = 0; i < arr.length - 1; i += 1) ekle(arr[i], arr[i + 1]);
  }

  // 2) Bransman baglantilari: ana direk -> kolun ILK diregi.
  for (const hat of lines) {
    if (!hat.branched_from_pole_id) continue;
    const ilk = hattaGore.get(hat.id)?.[0];
    if (!ilk) continue;
    ekle(direk.get(hat.branched_from_pole_id), ilk);
  }

  return parcalar;
}

function kordinatGecerli(p: GeoNokta): boolean {
  return Number.isFinite(p.latitude) && Number.isFinite(p.longitude);
}

/**
 * Nokta yaricapi (piksel) — zoom'a gore.
 *
 * Yaricap SABIT piksel olamaz: uzaklasinca komsu direkler tek bir dev
 * lekeye kaynar ve "burada devasa bir sorun var" diye okunur; oysa bunlar
 * ayri ayri direklerdir. Zoom arttikca yaricap buyur ki leke cografi
 * olarak yaklasik AYNI alani kaplasin.
 */
export function heatRadius(zoom: number): number {
  const z = Number.isFinite(zoom) ? zoom : 6;
  return Math.round(Math.min(52, Math.max(14, 14 + (z - 6) * 3.4)));
}
