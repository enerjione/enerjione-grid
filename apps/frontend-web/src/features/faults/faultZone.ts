/**
 * ARIZA BOLGESI — "sahada nereye gidilecek" sorusunun TEK cevabi.
 *
 * NEDEN BU DOSYA VAR
 * ------------------
 * Ayni karar UC ayri yerde, UC ayri kodla veriliyordu:
 *
 *   faultMapView.buildFaultMapView   -> haritadaki kirmizi kesik
 *   faultStripGeometry.buildStrip... -> sematik cizimdeki kirmizi parca
 *   branchRows.kolSinirlari          -> aday kollarin sinirlari
 *
 * Kurallar kagit uzerinde aynidir ama kopyalar sessizce ayristi ve sonuc
 * SAHADA goruldu: ayni ariza icin harita DEMO-3'un USTUNU, sema ALTINI
 * isaretliyordu. Ekip yanlis acikliga gidiyordu ve iki ekrandan hangisinin
 * dogru oldugunu soyleyen hicbir sey yoktu.
 *
 * Ayrismanin kok nedeni tek bir tanimdi: "bu cihaz arizayi gordu mu?"
 *
 *   harita : alarm reset edilmemis  YA DA  kayittaki `last_red_device`
 *   sema   : alarm reset edilmemis  VE     `produces_fault !== false`
 *
 * `produces_fault === false` olan bir alarm haritada kirmizi, semada
 * yesildi. Kayittaki cihaz haritada garanti kirmizi, semada yalnizca canli
 * alarmi varsa. Ayni cihaz, ayni an, iki farkli renk.
 *
 * Artik hem TANIM hem KARAR burada. Cagiran taraf yalnizca "hattaki
 * cihazlar sirali olarak sunlar" der.
 *
 * KURAL
 * -----
 * Ariza, akimi GOREN son cihaz ile GORMEYEN ilk cihazin ARASINDADIR. Bu,
 * arizanin yerini daraltmanin tek dogru yoludur:
 *
 *   hat basi -> son goren        : saglikli (akim gecti)
 *   son goren -> ilk gormeyen    : ARIZALI  (akim burada kesildi)
 *   ilk gormeyen -> hat ucu      : saglikli (akim hic gelmedi... degil —
 *                                  bkz. asagidaki not)
 *
 * "Ilk gormeyen"in ALTI neden saglikli sayilir: o cihazdan fault akimi
 * GECMEDIGI icin arizanin ondan asagida olmasi fiziksel olarak mumkun
 * degildir. Ustteki vaka tam olarak buydu — kolun giris cihazi "gormedim"
 * derken cizim kolun tamamini kirmiziya boyuyordu.
 *
 * ONCE KAYIT, SONRA CANLI ALARM
 * -----------------------------
 * Kaydin `last_red` / `first_green` alanlari arizanin ACILDIGI ANDAKI
 * gercektir ve degismez. Canli alarmdan turetmek yalnizca ariza ACIKKEN
 * dogru sonuc verir; alarm normale donunce kirmizi parca kaybolur ve
 * gecmis bir kaydin ekrani hattin tamamini saglam gosterir. Kayitta cihaz
 * yoksa (ya da o cihaz artik bu hatta degilse — topoloji duzenlendi) canli
 * alarm yoluna dusulur.
 */

/** Hat boyunca SIRALI tek cihaz. `key` cagiranin kimlik tipidir (id/kod). */
export type BolgeCihazi<K> = {
  key: K;
  /** O AN alarmi var mi (reset edilmemis). Kayit bilgisi burada DEGIL. */
  canliAlarm: boolean;
  /**
   * Bu cihaz bolge SINIRI tanimlayabilir mi?
   *
   * Bransman girisindeki cihaz ANA HAT uzerinde sinir tanimlamaz: gordugu
   * ariza kolun asagisindadir, ana telde degil. Kirmizi bir ana hat parcasi
   * cizmek ekibi yanlis acikliga gonderirdi.
   */
  sinirTanimlar?: boolean;
};

/** Bolgenin cihaz-indeksi cinsinden sinirlari. */
export type BolgeSiniri = {
  /** Son "gordum" diyen cihazin indeksi. */
  redIndex: number;
  /** Bolgeyi kapatan ilk "gormedim" cihazi; null ise ariza HAT UCUNA kadar. */
  greenIndex: number | null;
  /** Sinirlar kayittan mi geldi (true) yoksa canli alarmdan mi (false)? */
  kayittan: boolean;
};

function esit<K>(a: K, b: K | null | undefined): boolean {
  return b != null && a === b;
}

/**
 * Bolge sinirlarini cozer. `null` = bu hatta cizilecek bir ariza YOK.
 *
 * `null` donmesi bir eksiklik degil bir CEVAPTIR: hattin uzerinde arizayi
 * gordugunu soyleyen hicbir cihaz yoksa o hat saglamdir. Eskiden bu durumda
 * kaba direk araligina dusulup hat boyanabiliyordu; harita ise ayni arizada
 * hicbir sey cizmiyordu.
 */
export function bolgeSiniriCoz<K>(opts: {
  /** Hat boyunca SIRALI cihazlar. */
  cihazlar: readonly BolgeCihazi<K>[];
  /** Kayittaki "son arizayi goren" cihaz. */
  kayitliKirmizi: K | null;
  /** Kayittaki "ilk arizayi gormeyen" cihaz. */
  kayitliYesil: K | null;
}): BolgeSiniri | null {
  const { cihazlar, kayitliKirmizi, kayitliYesil } = opts;

  const sinirOlabilir = (i: number) => cihazlar[i].sinirTanimlar !== false;

  // --- 1) KAYIT YOLU ---------------------------------------------------
  let redIndex = -1;
  if (kayitliKirmizi != null) {
    redIndex = cihazlar.findIndex(
      (d, i) => sinirOlabilir(i) && esit(d.key, kayitliKirmizi)
    );
  }
  if (redIndex >= 0) {
    let greenIndex: number | null = null;
    if (kayitliYesil != null) {
      const y = cihazlar.findIndex(
        (d, i) => sinirOlabilir(i) && esit(d.key, kayitliYesil)
      );
      // Yesil cihaz kirmizidan SONRA olmali; degilse yok say — ters
      // siralanmis bir parca hattin uzerine yanlis yerde kirmizi cizerdi.
      if (y > redIndex) greenIndex = y;
    }
    return { redIndex, greenIndex, kayittan: true };
  }

  // --- 2) CANLI ALARM YOLU ---------------------------------------------
  // Kayitta cihaz yok ya da o cihaz artik bu hatta degil.
  for (let i = 0; i < cihazlar.length; i += 1) {
    if (cihazlar[i].canliAlarm && sinirOlabilir(i)) redIndex = i;
  }
  if (redIndex < 0) return null; // "gordum" diyen yok -> hat saglam

  let greenIndex: number | null = null;
  for (let i = redIndex + 1; i < cihazlar.length; i += 1) {
    if (!cihazlar[i].canliAlarm && sinirOlabilir(i)) {
      greenIndex = i;
      break;
    }
  }
  return { redIndex, greenIndex, kayittan: false };
}

/**
 * "GORDUM DIYEN YOK AMA GORMEDIM DIYEN VAR" — aday kollarin tipik durumu.
 *
 * Kolun kendi ariza kaydi yoktur ama uzerinde alarm vermeyen bir cihaz
 * durur. O cihazdan fault akimi GECMEDIGI icin arizanin ondan asagida
 * olmasi mumkun degildir: supheli alan hat basi ile o cihaz arasidir.
 *
 * Bu kural olmadan kol bastan sona kirmizi ciziliyordu — cihazin "gormedim"
 * demesi yok sayiliyor, kesinlikle saglam olan alt kisim da aday
 * gosteriliyordu. Ekranda gordugunuz DEMO-3 vakasi buydu.
 *
 * Donus: bolgeyi kapatan cihazin indeksi, yoksa null.
 */
export function ilkGormeyenIndeksi<K>(cihazlar: readonly BolgeCihazi<K>[]): number | null {
  for (let i = 0; i < cihazlar.length; i += 1) {
    if (!cihazlar[i].canliAlarm && cihazlar[i].sinirTanimlar !== false) return i;
  }
  return null;
}
