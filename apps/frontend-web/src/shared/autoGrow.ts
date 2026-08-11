/**
 * Metin alaninin ICERIGE GORE buyumesi — saf hesap (DOM'suz, React'siz).
 *
 * NEDEN VAR
 * ---------
 * Cihaz Ozellikleri'ndeki "Aciklama" alani `rows={2}` ile yazilmisti ama CSS
 * `min-height: 220px` + `flex: 1` dayatiyordu. Sonuc: aciklama BOSKEN bile
 * ekranin dortte birini kaplayan bir kutu duruyor, panel tasiyor ve
 * FAZLADAN bir kaydirma cubugu cikiyordu — icerideki alan zaten kaydirilabilir
 * oldugu icin kullanici hangi cubugun neyi kaydirdigini bilemiyordu.
 *
 * Yukseklik artik metnin kendisinden geliyor. Iki sinir var ve ikisi de
 * gerekli:
 *
 *   MIN — bos alan "buraya yazilabilir" gorunmeli; sifir yukseklikte bir
 *         kutu tiklanabilir bir hedef degildir.
 *   MAX — cok uzun bir aciklama sayfayi sonsuza kadar uzatmamali; o noktadan
 *         sonra alanin KENDISI kaydirilir (tek ve anlasilir bir cubuk).
 */

/** Bos alanin yuksekligi (piksel) — iki satirlik yazi alani. */
export const AUTO_GROW_MIN_PX = 52;

/** Bundan sonrasi alanin kendi icinde kaydirilir. */
export const AUTO_GROW_MAX_PX = 260;

/**
 * `scrollHeight` -> uygulanacak yukseklik.
 *
 * Cagiran once `height = "auto"` yapmali; aksi halde `scrollHeight` mevcut
 * yuksekligi asla kucultmez ve alan yalnizca BUYUR (metin silinince geri
 * gelmez).
 */
export function autoGrowHeight(
  scrollHeight: number,
  min: number = AUTO_GROW_MIN_PX,
  max: number = AUTO_GROW_MAX_PX
): number {
  // Gizli bir sekmede olculen alan 0 doner; MIN'e sabitlemek, sekme
  // acildiginda dogru olcumun yapilmasini engellemez ama bu arada
  // kutunun yok olmasini onler.
  if (!Number.isFinite(scrollHeight) || scrollHeight <= 0) return min;
  return Math.min(max, Math.max(min, Math.ceil(scrollHeight)));
}

/** Tavana dayandi mi — alanin kendi kaydirmasi gerekli. */
export function autoGrowScrolls(
  scrollHeight: number,
  max: number = AUTO_GROW_MAX_PX
): boolean {
  return Number.isFinite(scrollHeight) && scrollHeight > max;
}
