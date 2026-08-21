/**
 * Ana haritada CIHAZ secilince kamera nereye gitsin — saf karar (Leaflet'siz).
 *
 * KARAR: CIHAZA YAKINLAS
 * ----------------------
 * Soldaki listeden bir cihaz secildiginde harita O CIHAZA yakinlasir.
 *
 * Onceki surum hattin TAMAMINI ekrana sigdiriyordu; gerekcesi "operatorun
 * sordugu soru 'bu cihaz hattin neresinde'" idi. Sahada bunun tersi cikti:
 * uzun bir hatta sigdirma, secilen cihazi haritanin bir kosesinde nokta
 * boyutunda birakiyor ve "hangisini sectim" sorusunu cevapsiz birakiyordu.
 * Hattin butunu zaten hicbir sey secili degilken gorunuyor.
 *
 * ESKI TUZAK TEKRARLANMIYOR. Ilk surumde `flyTo(hedef, 13)` vardi: SABIT
 * zoom. Kullanici direk seviyesinde (16-17) calisirken bir cihaza
 * tikladiginda harita UZAKLASIYORDU — "cihazi goster" eylemi kullanicinin
 * kurdugu yakinligi bozuyordu. Bu yuzden hedef yakinlik ASLA MEVCUDUN
 * ALTINA DUSMEZ: `max(mevcutZoom, DEVICE_ZOOM)`.
 *
 * Karar Leaflet cagrilarindan AYRILDI: birkac ince kosul tasiyor (bozuk
 * koordinat, hatti bilinmeyen cihaz, tek noktali hat) ve React bileseni
 * icinde kaldigi surece calistirilarak dogrulanamiyordu.
 */

export type FocusPoint = { latitude: number; longitude: number };

export type DeviceFocusPlan =
  | { kind: "skip" }
  /** Cihaza yakinlas. `zoom` mevcut yakinligin ALTINA dusmez. */
  | { kind: "point"; key: string; latitude: number; longitude: number; zoom: number };

/**
 * Cihaza odaklanirken hedeflenen yakinlik.
 *
 * 17 = direk/bina seviyesi: secilen cihaz ve komsulari ayirt edilir. 15
 * mahalle olcegiydi ve iki komsu direk hala ayni noktada gorunuyordu.
 *
 * TAVAN DEGIL TABAN: kullanici daha yakindaysa oldugu yerde kalir
 * (bkz. `hedefZoom`).
 */
export const DEVICE_ZOOM = 17;

/** Geriye donuk ad — eski cagiranlar kirilmasin. */
export const SINGLE_DEVICE_ZOOM = DEVICE_ZOOM;

/**
 * Uygulanacak yakinlik: hedef ile MEVCUT yakinligin BUYUGU.
 *
 * "Cihazi goster" eylemi hicbir kosulda UZAKLASTIRMAMALI — ilk surumde
 * tam olarak bu yasandi ve kullanici her secimde yeniden yakinlasmak
 * zorunda kaliyordu.
 */
export function hedefZoom(mevcut: number | null | undefined): number {
  if (mevcut == null || !Number.isFinite(mevcut)) return DEVICE_ZOOM;
  return Math.max(mevcut, DEVICE_ZOOM);
}

function gecerli(p: FocusPoint): boolean {
  // Tek bir NaN, Leaflet'in bounds hesabini sessizce gecersiz kilar ve
  // harita hicbir yere gitmez.
  return Number.isFinite(p.latitude) && Number.isFinite(p.longitude);
}

export function planDeviceFocus(input: {
  /** Secili cihaz — yoksa odaklanma yok. */
  device: { id: number; latitude: number; longitude: number } | null | undefined;
  /** En son uygulanan planin anahtari (yoksa bos string). */
  lastKey: string;
  /** Haritanin SU ANKI yakinligi; hedef bunun altina dusmez. */
  currentZoom?: number | null;
}): DeviceFocusPlan {
  const { device, lastKey, currentZoom } = input;
  if (!device) return { kind: "skip" };
  if (!gecerli(device)) return { kind: "skip" };

  const key = String(device.id);
  // Ayni cihaz icin iki kez odaklanma: polling her 5 sn'de veriyi
  // tazeliyor, her tazelemede kamerayi geri almak kullanicinin elle
  // yaptigi kaydirmayi imkansiz kilardi.
  if (key === lastKey) return { kind: "skip" };

  // HAT NOKTALARI ARTIK KULLANILMIYOR. Hatti sigdirmak, secilen cihazi
  // uzun bir hatta nokta boyutunda birakiyordu; hattin butunu zaten
  // hicbir sey secili degilken gorunuyor.
  return {
    kind: "point",
    key,
    latitude: device.latitude,
    longitude: device.longitude,
    zoom: hedefZoom(currentZoom)
  };
}
