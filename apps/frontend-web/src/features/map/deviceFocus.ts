/**
 * Ana haritada CIHAZ secilince kamera nereye gitsin — saf karar (Leaflet'siz).
 *
 * YASANAN SORUN
 * -------------
 * Cihaz secildiginde `map.flyTo(target, 13)` cagriliyordu: SABIT zoom 13.
 * Kullanici direk seviyesinde (zoom 16-17) calisirken bir cihaza tikladiginda
 * harita UZAKLASIYORDU. Yani "cihazi goster" eylemi, kullanicinin kurdugu
 * yakinligi bozup onu tekrar yakinlastirmaya zorluyordu.
 *
 * DOGRU DAVRANIS: cihazin bagli oldugu HAT ekrana sigsin. Operatorun
 * cihaza tiklarken sordugu soru "bu cihaz hattin neresinde" — cevabi hattin
 * tamami gorunurken verilir. Sabit bir zoom bu soruyu ne kucuk ne buyuk
 * hatta dogru cevaplar.
 *
 * Karar Leaflet cagrilarindan AYRILDI: birkac ince kosul tasiyor (bozuk
 * koordinat, hatti bilinmeyen cihaz, tek noktali hat) ve React bileseni
 * icinde kaldigi surece calistirilarak dogrulanamiyordu.
 */

export type FocusPoint = { latitude: number; longitude: number };

export type DeviceFocusPlan =
  | { kind: "skip" }
  /** Hat bilinmiyor ya da tek nokta: cihaza sabit yakinlikla git. */
  | { kind: "point"; key: string; latitude: number; longitude: number; zoom: number }
  /** Hattin tamamini kapsayan kutuya sigdir. */
  | { kind: "bounds"; key: string; points: FocusPoint[] };

/**
 * Hatti bilinmeyen (ya da tek noktali) cihazda kullanilan yakinlik.
 *
 * 15, eski 13'ten DAHA YAKIN: 13 sokak duzeninin bile zor secildigi bir
 * olcekti ve "cihazi goster" isteginin cevabi olamazdi. Yalnizca sigdirilacak
 * bir hat YOKSA devreye girer.
 */
export const SINGLE_DEVICE_ZOOM = 15;

function gecerli(p: FocusPoint): boolean {
  // Tek bir NaN, Leaflet'in bounds hesabini sessizce gecersiz kilar ve
  // harita hicbir yere gitmez.
  return Number.isFinite(p.latitude) && Number.isFinite(p.longitude);
}

export function planDeviceFocus(input: {
  /** Secili cihaz — yoksa odaklanma yok. */
  device: { id: number; latitude: number; longitude: number } | null | undefined;
  /** Cihazin bagli oldugu hattin TUM noktalari (direkler + hattaki cihazlar). */
  linePoints: readonly FocusPoint[];
  /** En son uygulanan planin anahtari (yoksa bos string). */
  lastKey: string;
}): DeviceFocusPlan {
  const { device, linePoints, lastKey } = input;
  if (!device) return { kind: "skip" };
  if (!gecerli(device)) return { kind: "skip" };

  const key = String(device.id);
  // Ayni cihaz icin iki kez odaklanma: polling her 5 sn'de veriyi
  // tazeliyor, her tazelemede kamerayi geri almak kullanicinin elle
  // yaptigi kaydirmayi imkansiz kilardi.
  if (key === lastKey) return { kind: "skip" };

  const valid = linePoints.filter(gecerli);

  // Hat bilinmiyor ya da tek noktadan ibaret: sigdirilacak bir sey yok,
  // cihaza makul bir yakinlikla git.
  if (valid.length < 2) {
    return {
      kind: "point",
      key,
      latitude: device.latitude,
      longitude: device.longitude,
      zoom: SINGLE_DEVICE_ZOOM
    };
  }

  // Secili cihaz hattin nokta listesinde olmayabilir (koordinati topolojiden
  // ayrilmis olabilir); kutuya EKLENIR ki secilen sey mutlaka gorunsun.
  return {
    kind: "bounds",
    key,
    points: [...valid, { latitude: device.latitude, longitude: device.longitude }]
  };
}
