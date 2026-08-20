/**
 * DAKIKALIK SAAT — geri sayimlar icin tek zamanlayici.
 *
 * NEDEN DAKIKA, NEDEN SANIYE DEGIL
 * --------------------------------
 * Dial-In geri sayimi DAKIKA cinsinden gosterilir ("Sonraki Dial-In: 43 dk").
 * Saniyede bir tazeleyen bir zamanlayici bu ekranda hicbir sey kazandirmaz —
 * gosterilen sayi dakikada bir degisiyor — ama maliyeti gercektir: 200+
 * cihazlik listede saniyede bir React render'i, harita katmaninda saniyede
 * bir marker guncellemesi ve dizustunde bosuna donen bir CPU. Bu yuzden
 * periyot 60 saniyedir ve BU DOSYA DISINDA baska bir geri sayim zamanlayicisi
 * kurulmaz.
 *
 * SINIRA HIZALAMA
 * ---------------
 * Ilk tetik, duvar saatinin bir sonraki tam dakikasina hizalanir. Duz bir
 * `setInterval(fn, 60000)` sayfa acilis anina gore kayar; "43 dk" yazisi
 * ortalama 30 saniye gecikmeyle "42 dk"ya donerdi. Hizalama, ekrandaki
 * dakikanin gercek dakika ile ayni anda donmesini saglar.
 *
 * SEKME GORUNMEZKEN
 * -----------------
 * Arka plandaki sekmede tetik gerekmez; sekme one gelince ANINDA bir kez
 * tazelenir (bayat bir dakika gostermemek icin) ve periyoda donulur. Ayni
 * ilke `usePolling` icinde de var.
 */
import { useEffect, useState } from "react";

/** Geri sayim tazeleme periyodu (ms). SANIYE DEGIL — bkz. dosya basligi. */
export const MINUTE_TICK_MS = 60_000;

/**
 * Dakikada bir degisen "simdi" (ms).
 *
 * Donen deger dogrudan `dialInCountdown({ nowMs })` gibi saf fonksiyonlara
 * gecirilir; boylece zaman kaynagi tek yerde kalir ve testte disaridan
 * verilebilir.
 */
export function useMinuteTick(): number {
  const [now, setNow] = useState<number>(() => Date.now());

  useEffect(() => {
    let interval: number | null = null;
    let alignTimer: number | null = null;

    const tick = () => setNow(Date.now());

    const start = () => {
      if (interval !== null) return;
      // Bir sonraki TAM dakikaya kadar bekle, sonra periyoda gec.
      const kalan = MINUTE_TICK_MS - (Date.now() % MINUTE_TICK_MS);
      alignTimer = window.setTimeout(() => {
        alignTimer = null;
        tick();
        interval = window.setInterval(tick, MINUTE_TICK_MS);
      }, kalan);
    };

    const stop = () => {
      if (interval !== null) {
        window.clearInterval(interval);
        interval = null;
      }
      if (alignTimer !== null) {
        window.clearTimeout(alignTimer);
        alignTimer = null;
      }
    };

    const onVisibility = () => {
      if (document.hidden) {
        stop();
        return;
      }
      // Sekme one geldi: once bayat dakikayi duzelt, sonra hizalanip devam et.
      tick();
      start();
    };

    if (!document.hidden) start();
    document.addEventListener("visibilitychange", onVisibility);
    return () => {
      stop();
      document.removeEventListener("visibilitychange", onVisibility);
    };
  }, []);

  return now;
}
