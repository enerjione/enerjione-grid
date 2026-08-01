/**
 * Olcum guvenilirligi — "yesil yalan"i onleyen tek kaynak.
 *
 * NEDEN VAR
 * ---------
 * Arayuz binary sinyalleri yalnizca `value === 1` ile degerlendiriyordu.
 * Bunun uc ayri sonucu vardi ve ucu de AYNI yesil "Normal" rozetini
 * uretiyordu:
 *
 *   value === null  -> HIC VERI YOK        -> "Normal" (yanlis)
 *   value === 0     -> gercekten normal    -> "Normal" (dogru)
 *   value === 0 ama kalite `comm_lost`     -> "Normal" (yanlis ve TEHLIKELI)
 *
 * Ucuncusu en agiri: haberlesmesi kopan bir cihaz icin gateway `comm_lost`
 * kalitesiyle 0.0 basiyor. Backend bu okumayi alarm degerlendirmesine
 * SOKMUYOR (`tag_engine_service.ALARM_BLOCKING_QUALITIES`) — yani sunucu
 * dogru davraniyor. Arayuz ise ayni degeri "taze" sanip yesil gosterip
 * sunucunun kararini GECERSIZ KILIYORDU.
 *
 * Bir ariza izleme urununde en agir hata sinifi budur: sistem BILMEDIGINI
 * "sorun yok" diye gosterir.
 *
 * BACKEND ILE HIZALI KALMALI
 * --------------------------
 * Asagidaki kume `apps/backend-api/app/services/tag_engine_service.py`
 * icindeki `ALARM_BLOCKING_QUALITIES` ile AYNI olmali; ayrisirsa arayuz ile
 * alarm motoru ayni okuma icin farkli seyler soyler.
 * `apps/backend-api/tests/test_ui_green_lie_guard.py` bunu dogruluyor
 * (`test_engelleyen_kaliteler_BACKEND_ILE_ayni`).
 */

/** Olcumun guvenilirligi. */
export type SignalTrust =
  /** Deger var ve guvenilir. */
  | "trusted"
  /** Deger YOK (hic telemetri gelmemis). */
  | "missing"
  /** Deger var ama GUVENILMEZ (haberlesme kopuk, zorlanmis, gecersiz). */
  | "untrusted";

/**
 * Alarm degerlendirmesini engelleyen kaliteler.
 *
 * `bad | offline | invalid | comm_lost | restart` haberlesme sorunudur;
 * `forced` ise operatorun noktayi ELLE zorlamasidir — cihaz ayaktadir ama
 * deger YAPAYDIR, dolayisiyla ondan "normal" sonucu cikarilamaz.
 */
export const BLOCKING_QUALITIES: ReadonlySet<string> = new Set([
  "bad",
  "offline",
  "invalid",
  "comm_lost",
  "restart",
  "forced"
]);

/**
 * Bir olcumun guvenilirligini belirler.
 *
 * @param value    Olcum degeri (null = hic veri yok).
 * @param quality  Olcum kalitesi (null/bos = bilinmiyor -> guvenilir sayilir;
 *                 eski kayitlarda kalite alani bos olabilir ve her sey
 *                 "guvenilmez" gostermek de yaniltici olurdu).
 * @param gatewayOnline Cihazin gateway'i ayakta mi. Kapaliysa gelen son
 *                 deger bayattir — kalite alani "good" dese bile.
 */
export function signalTrust(
  value: number | null | undefined,
  quality: string | null | undefined,
  gatewayOnline = true
): SignalTrust {
  if (value === null || value === undefined) return "missing";
  if (!gatewayOnline) return "untrusted";
  const q = (quality ?? "").trim().toLowerCase();
  if (q && BLOCKING_QUALITIES.has(q)) return "untrusted";
  return "trusted";
}

/** Rozet gosterilebilir mi — yani "Normal"/"Aktif" demeye hakkimiz var mi? */
export function isTrusted(
  value: number | null | undefined,
  quality: string | null | undefined,
  gatewayOnline = true
): boolean {
  return signalTrust(value, quality, gatewayOnline) === "trusted";
}
