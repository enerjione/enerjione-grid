/**
 * IPUCU KONUMLANDIRMA — ortak ilkel.
 *
 * NEDEN AYRI DOSYA
 * ----------------
 * Iki ayri ipucu var ve ikisi de AYNI dort sorunu cozmek zorunda:
 *   * `useRuntimeTip` — "bu renk ne demek?" (durum noktalari)
 *   * `FieldHelp`     — "bu ayar ne ise yarar?" (form alanlari)
 *
 * Ortak olan sey ICERIK degil DAVRANIS: tetikleyiciyi olcmek, ustte yer
 * yoksa asagi acmak, `body`ye portal ile cizmek ve kaydirma/yeniden
 * boyutlandirma/Escape'te kapanmak. Bu mantigi ikinci kez yazmak, birinde
 * duzeltilen bir kenar durumunun otekinde acik kalmasi demekti.
 *
 * NEDEN PORTAL VE `fixed`
 * -----------------------
 * Kutu `overflow: hidden` tasiyan liste govdelerinin, modal panellerin ve
 * Leaflet katmanlarinin icinde kalirsa KIRPILIR. `body`ye cizip `fixed` ile
 * konumlamak kirpilmayi yapisal olarak imkansiz kilar.
 *
 * NEDEN KAYDIRMADA KAPANIR
 * ------------------------
 * Konum `fixed` oldugu icin sayfa kayarken kutu tetikleyiciden AYRILIR.
 * Yanlis yerde duran bir aciklama hic olmamasindan daha kotudur: baska bir
 * alani/cihazi anlattigi sanilir.
 */
import { useCallback, useEffect, useId, useRef, useState } from "react";

/** Kutunun tetikleyiciye uzakligi (px). */
export const IPUCU_BOSLUK = 10;

/** Ustte bu kadar yer yoksa kutu ASAGI acilir. */
export const IPUCU_UST_ESIK = 150;

export type IpucuKonum = { x: number; y: number; altta: boolean };

/**
 * @param focusable Sekme ile ulasilabilir olsun mu. Tek basina duran ogeler
 *   icin `true`; ZATEN bir `<button>` icinde duran ogeler icin `false`
 *   (gecersiz ic ice odak ve 600 cihazlik listede 600 fazladan durak).
 */
export function useIpucuKonum({ focusable = false }: { focusable?: boolean } = {}) {
  const ref = useRef<HTMLElement | null>(null);
  const [konum, setKonum] = useState<IpucuKonum | null>(null);
  const tipId = useId();

  const kapat = useCallback(() => setKonum(null), []);

  const ac = useCallback(() => {
    const el = ref.current;
    if (!el) return;
    const r = el.getBoundingClientRect();
    // Ustte yer yoksa asagi ac: aksi halde kutu ekranin disina tasar ve
    // kullanici hicbir sey gormez.
    const altta = r.top < IPUCU_UST_ESIK;
    setKonum({
      x: r.left + r.width / 2,
      y: altta ? r.bottom + IPUCU_BOSLUK : r.top - IPUCU_BOSLUK,
      altta
    });
  }, []);

  useEffect(() => {
    if (!konum) return;
    const kaydir = () => kapat();
    const tus = (e: KeyboardEvent) => {
      if (e.key === "Escape") kapat();
    };
    // `true`: yakalama evresi — ic kaydirma kaplarindaki kayma da yakalanir,
    // yoksa kutu yalnizca sayfa govdesi kayinca kapanirdi.
    // DISARI DOKUNUNCA KAPANIR. Dokunmatikte `mouseleave` GUVENILIR DEGIL:
    // parmak kalkinca cogu tarayicida hic uretilmez. Bu kapi olmadan sahada
    // tabletle acilan bir ipucu ekranda ASILI KALIYORDU.
    const disari = (e: Event) => {
      const el = ref.current;
      if (el && e.target instanceof Node && el.contains(e.target)) return;
      kapat();
    };
    window.addEventListener("scroll", kaydir, true);
    window.addEventListener("resize", kaydir);
    window.addEventListener("keydown", tus);
    document.addEventListener("pointerdown", disari, true);
    return () => {
      window.removeEventListener("scroll", kaydir, true);
      window.removeEventListener("resize", kaydir);
      window.removeEventListener("keydown", tus);
      document.removeEventListener("pointerdown", disari, true);
    };
  }, [konum, kapat]);

  const triggerProps = {
    ref: ref as React.Ref<never>,
    onMouseEnter: ac,
    onMouseLeave: kapat,
    ...(focusable ? { onFocus: ac, onBlur: kapat, tabIndex: 0 } : null),
    "aria-describedby": konum ? tipId : undefined
  };

  return { ref, konum, tipId, ac, kapat, triggerProps };
}
