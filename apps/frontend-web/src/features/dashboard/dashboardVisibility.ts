/**
 * Ana sayfada bir cihaz gorunur mu — YAPISAL karar.
 *
 * "Yapisal" derken: kullanicinin sectigi durum/arama/bolge filtrelerinden
 * ONCE gelen, cihazin sebekedeki YERI ile ilgili karar. Bu kural bir kez ters
 * cevrilip geri alindi ve arada Hat Yonetimi'nden "hattan kaldir" islemi ana
 * sayfada hicbir sey degistirmez oldu; o yuzden ayri bir modulde ve testli.
 *
 * KURALLAR
 * --------
 *  1. Fiziksel kit kaydi gorunmez. Horstmann Pole Master Kit tek DNP3
 *     outstation'dir; sahada izlenen sey onun SETLERIDIR. Kitin kendisi
 *     hicbir hat segmentine baglanmaz ve haritada kurulumda girilmis sabit
 *     koordinatta, yani YANLIS yerde cikardi.
 *
 *  2. Topoloji henuz yuklenmediyse HICBIR SEY gizlenmez. Bilmedigimiz icin
 *     saklamak, bos bir harita gostermek olurdu — bu urunde en agir hata
 *     sinifi "sistem bilmedigini yokmus gibi gosterdi".
 *
 *  3. Filtrede "Atanmamis" secildiyse eleme YAPILMAZ; kullanici zaten tam
 *     olarak onlari istiyor. Daraltmayi bolge/hat filtresi kendisi yapar.
 *
 *  4. Geri kalan durumda: hicbir hat segmentine bagli OLMAYAN cihaz ana
 *     sayfada gorunmez. Kaldirma islemi `line_segments` satirini siler ama
 *     cihazin kurulumda girilmis koordinati yerinde kalir; pin eski yerinin
 *     yakininda durmaya devam ederdi ve bagli cihazlarla BIREBIR AYNI
 *     ciziliyordu. Liste "Hatta atanmadi" derken harita onu sebekenin
 *     parcasi gibi gosteriyordu.
 *
 * Cihaz KAYBOLMUYOR: Bolge/Hat filtresinde "Atanmamis" secilince yalnizca
 * onlar listelenir, Muhendislik > Cihazlar'da da her zaman durur.
 */
export type AnaSayfaGorunurluk = {
  /** Fiziksel Pole Master Kit kaydi mi (setleri degil, kitin kendisi). */
  kit: boolean;
  /** `/grid/snapshot` geldi mi? Gelmediyse atama bilgisi BILINMIYOR demektir. */
  topolojiYuklendi: boolean;
  /** Cihaz bir hat segmentine bagli mi (deviceTopologyInfo'da var mi). */
  hattaAtanmis: boolean;
  /** Kullanici Bolge ya da Hat filtresinde "Atanmamis" sectiyse true. */
  atanmamisIsteniyor: boolean;
};

export function anaSayfadaGorunur(d: AnaSayfaGorunurluk): boolean {
  if (d.kit) return false;
  if (!d.topolojiYuklendi) return true;
  if (d.atanmamisIsteniyor) return true;
  return d.hattaAtanmis;
}
