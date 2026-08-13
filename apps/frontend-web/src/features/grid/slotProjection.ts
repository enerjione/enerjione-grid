/**
 * Bir cihazin iki direk arasindaki (slot) konumunu ORAN olarak hesaplar.
 *
 * Hat Yonetimi'nde cihaz isaretcisi suruklenip birakildiginda, birakildigi
 * nokta hattin uzerine izdusurulur ve slot boyunca 0..1 arasi bir oran
 * (`device_position_t`) uretilir. Backend ayni orani kullanarak cihazin
 * koordinatini yeniden hesaplar (bkz. `grid_topology._resync_slot`).
 *
 * NEDEN AYRI MODUL: hesap iki kez yanlisti ve ikisi de SESSIZ hataydi —
 * ekranda bir sey ciziliyor, yalnizca yanlis yerde.
 *
 * ENLEM/BOYLAM DUZ KOORDINAT DEGILDIR
 * -----------------------------------
 * Izdusum once ham derecelerle yapiliyordu: `(dLat, dLon)` sanki birbirine
 * dik ve ayni olcekteymis gibi. Degil. Haritada (Web Mercator) 39. enlemde
 * bir boylam derecesi bir enlem derecesinden ~%22 KISA gorunur (cos 39° ≈
 * 0,777). Yani "hattin uzerindeki en yakin nokta" ekranda gorulen nokta
 * DEGILDI; cihaz birakildigi yerden kaymis bir noktaya oturuyordu. Sapma
 * hat ne kadar capraz ise o kadar buyuk.
 *
 * Duzeltme: boylam farki `cos(enlem)` ile olceklenip hesap duzlemde yapilir.
 * Kucuk mesafelerde (bir direk araligi, en fazla birkac yuz metre) bu
 * yaklasim yeterlidir; tam jeodezik hesap gereksiz karmasik olurdu.
 */
export type Konum = { latitude: number; longitude: number };

/**
 * `nokta`nin, `from` -> `to` dogru parcasi uzerindeki oranı (0..1).
 *
 * Parca disina dusen izdusumler uclara KIRPILIR: cihaz slot'un disina
 * tasamaz, kendi araligindan cikan bir cihaz ariza hesabini bozar.
 * Iki direk ayni noktadaysa oran 0 doner (bolme yok).
 */
export function slotOrani(from: Konum, to: Konum, nokta: Konum): number {
  const k = Math.cos((from.latitude * Math.PI) / 180);
  const ex = to.latitude - from.latitude;
  const ey = (to.longitude - from.longitude) * k;
  const uzunluk2 = ex * ex + ey * ey;
  if (!(uzunluk2 > 0)) return 0;
  const px = nokta.latitude - from.latitude;
  const py = (nokta.longitude - from.longitude) * k;
  const t = (px * ex + py * ey) / uzunluk2;
  return Math.max(0, Math.min(1, t));
}

/** Orandan koordinat: slot boyunca dogrusal ara deger. Backend ile ayni formul. */
export function slotNoktasi(from: Konum, to: Konum, t: number): [number, number] {
  return [
    from.latitude + (to.latitude - from.latitude) * t,
    from.longitude + (to.longitude - from.longitude) * t
  ];
}
