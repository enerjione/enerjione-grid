/**
 * Periyodik veri cekme hook'u — sayfa gecidi + sekme gorunurlugu ile.
 *
 * NEDEN: App.tsx'te alti adet neredeyse ayni polling efekti vardi ve hepsi
 * `if (!session) return` disinda KOSULSUZ calisiyordu:
 *
 *   - Kullanici Ayarlar sayfasinda dururken bile alarmlar, arizalar, olaylar,
 *     grid topolojisi ve cihaz listesi 5 SANIYEDE BIR cekiliyordu. Sekme basina
 *     ~1 istek/sn; hepsi sayfalanmamis tam-tablo sorgulari.
 *   - Sekme arka planda / ekran kilitliyken de devam ediyordu.
 *   - Bazilari ilk veriyi cekmek icin bir interval periyodu BEKLIYORDU
 *     (kullanici sayfaya girer, 5 sn bos ekran gorur).
 *
 * Bu hook ucunu birlikte cozer:
 *   1. `enabled` false ise hic calismaz (cagiran tarafta pageMode kontrolu).
 *   2. Sekme gorunmez oldugunda DURUR, geri gorunur olunca ANINDA bir kez
 *      ceker ve devam eder (arka plandaki sekme sunucuyu yormaz, one gelince
 *      kullanici bayat veri gormez).
 *   3. `enabled` true olunca ANINDA bir kez ceker (ilk periyodu beklemez).
 *
 * Hata yonetimi: `fn` icindeki hata YUTULUR (gecici ag hatasi polling'i
 * durdurmamali — mevcut davranis korunuyor). Cagiran taraf kendi
 * try/catch'inde kullaniciya gostermek isterse gosterir.
 */
import { useEffect, useRef } from "react";

type Options = {
  /** false ise polling hic kurulmaz. Sayfa gecidi burada verilir. */
  enabled: boolean;
  /** Periyot (ms). */
  intervalMs: number;
  /** Cekme fonksiyonu. Her tetikte cagrilir; hatasi yutulur. */
  fn: () => void | Promise<void>;
  /**
   * false ise `enabled` true olunca ANINDA cekmez, ilk periyodu bekler.
   * Varsayilan true — sayfaya girildiginde veri hemen gelsin.
   */
  immediate?: boolean;
};

export function usePolling({ enabled, intervalMs, fn, immediate = true }: Options): void {
  // `fn` her render'da yeni bir closure olur; ref ile tutuyoruz ki interval
  // her render'da yeniden kurulmasin (aksi halde periyot hic dolmaz ve
  // polling fiilen "her render'da bir istek"e donerdi).
  const fnRef = useRef(fn);
  fnRef.current = fn;

  useEffect(() => {
    if (!enabled) return undefined;

    let stopped = false;
    let timer: number | null = null;

    const run = () => {
      if (stopped) return;
      try {
        const maybePromise = fnRef.current();
        if (maybePromise && typeof maybePromise.then === "function") {
          // Gecici ag hatasi polling'i durdurmasin.
          void maybePromise.catch(() => undefined);
        }
      } catch {
        // senkron hata — ayni sekilde yutuluyor
      }
    };

    const start = () => {
      if (timer !== null || stopped) return;
      timer = window.setInterval(run, intervalMs);
    };

    const stop = () => {
      if (timer !== null) {
        window.clearInterval(timer);
        timer = null;
      }
    };

    const onVisibilityChange = () => {
      if (document.hidden) {
        stop();
        return;
      }
      // Sekme one geldi: bayat veriyi hemen tazele, sonra periyoda don.
      run();
      start();
    };

    if (!document.hidden) {
      if (immediate) run();
      start();
    }
    document.addEventListener("visibilitychange", onVisibilityChange);

    return () => {
      stopped = true;
      stop();
      document.removeEventListener("visibilitychange", onVisibilityChange);
    };
    // `fn` KASITLA bagimlilik degil (ref ile okunuyor) — aksi halde her
    // render'da interval sifirlanirdi.
  }, [enabled, intervalMs, immediate]);
}
