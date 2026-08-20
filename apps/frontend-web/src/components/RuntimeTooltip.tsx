/**
 * DURUM IPUCU — "bu renk ne demek?" sorusunu ustune gelince cevaplar.
 *
 * NE COZUYOR
 * ----------
 * Listede, agacta ve haritada durum cogu zaman TEK BIR RENKLI NOKTA olarak
 * gorunuyor. Renk tek basina okunamaz: operator mavi bir nokta gorup
 * "arizali mi?" diye dusunur. Ozellikle `Smart Bekleme` MAVI ve SAGLIKLI —
 * yani renk sezgiye aykiri, cunku Horstmann Smart modda modemini BILEREK
 * kapatir.
 *
 * Eskiden ipucu tarayicinin `title` ozniteligiydi. Uc sorunu vardi:
 *   1. Yalnizca ADI gosteriyordu ("Smart Bekleme") — ANLAMINI degil.
 *   2. ~1 saniye gecikmeyle, isletim sisteminin kendi kutusunda cikiyordu;
 *      renk orada YOK, yani rengi acikladigi soylenemezdi.
 *   3. Kovayi (saglikli/uyari/ariza) hic soylemiyordu.
 *
 * Burada ucu birden cozuluyor: rengin kendisi, adi, HANGI KOVAYA girdigi ve
 * bir cumlelik anlami ayni kutuda.
 *
 * NEDEN DOM'A SARMALAYICI EKLEMIYOR
 * ---------------------------------
 * Nokta ve rozet mevcut yerlesimlerin ICINDE duruyor (`device-status-dot`,
 * `device-tree-dot`, flex bosluklari, mutlak konumlar). Etraflarina bir
 * `<span>` eklemek onlarca ekranda hizalamayi bozardi. Bu yuzden bilesen
 * degil KANCA: cagiran, doner degerdeki `triggerProps`u ZATEN VAR OLAN
 * elemana yayar; kutu ise `createPortal` ile `body`ye cizilir ve yerlesime
 * hic girmez.
 *
 * NEDEN PORTAL
 * ------------
 * Kutu `overflow: hidden` tasiyan liste govdelerinin ve Leaflet
 * panellerinin icinde kalirsa KIRPILIR. `body`ye cizip `position: fixed`
 * ile konumlamak, kirpilmayi yapisal olarak imkansiz kilar.
 */
import { useCallback, useEffect, useId, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { useTranslation } from "react-i18next";

import type { DeviceRuntimeState } from "../shared/deviceRuntimeState";
import { runtimeToneClass } from "./RuntimeStateChip";

/** Kutunun tetikleyiciye uzakligi (px). */
const BOSLUK = 10;

/** Ustte bu kadar yer yoksa kutu ASAGI acilir. */
const UST_ESIK = 150;

type Konum = { x: number; y: number; altta: boolean };

/**
 * Durum ipucu kancasi.
 *
 * @returns `ref` + `triggerProps` (var olan elemana yayilir) ve `tip`
 *          (portal dugumu; JSX'te kardes olarak birakilir, yerlesime girmez).
 */
export function useRuntimeTip(
  state: DeviceRuntimeState,
  { focusable = false }: { focusable?: boolean } = {}
) {
  const { t } = useTranslation();
  const ref = useRef<HTMLElement | null>(null);
  const [konum, setKonum] = useState<Konum | null>(null);
  const tipId = useId();

  const kapat = useCallback(() => setKonum(null), []);

  const ac = useCallback(() => {
    const el = ref.current;
    if (!el) return;
    const r = el.getBoundingClientRect();
    // Ustte yer yoksa asagi ac: aksi halde kutu ekranin disina tasar ve
    // kullanici hicbir sey gormez.
    const altta = r.top < UST_ESIK;
    setKonum({
      x: r.left + r.width / 2,
      y: altta ? r.bottom + BOSLUK : r.top - BOSLUK,
      altta
    });
  }, []);

  // ACIKKEN SAYFA KAYARSA KUTU KAPANIR. Konum `fixed` oldugu icin kayma
  // sirasinda tetikleyiciden AYRILIR; yanlis yerde duran bir aciklama,
  // hic olmamasindan daha kotudur (baska bir cihazi anlatiyor sanilir).
  // `Escape` de kapatir — klavye kullanicisi kutuda sikismasin.
  useEffect(() => {
    if (!konum) return;
    const kaydir = () => kapat();
    const tus = (e: KeyboardEvent) => {
      if (e.key === "Escape") kapat();
    };
    window.addEventListener("scroll", kaydir, true);
    window.addEventListener("resize", kaydir);
    window.addEventListener("keydown", tus);
    return () => {
      window.removeEventListener("scroll", kaydir, true);
      window.removeEventListener("resize", kaydir);
      window.removeEventListener("keydown", tus);
    };
  }, [konum, kapat]);

  // ODAKLANABILIRLIK VARSAYILAN OLARAK KAPALI — bilincli.
  //
  // Durum noktasi cogu ekranda BIR `<button>` ICINDE duruyor (cihaz satiri,
  // hat agaci satiri). Butonun icine odaklanabilir bir oge koymak hem
  // gecersiz HTML'dir hem de klavye gezintisini bozar; ustelik 600 cihazlik
  // bir listede 600 FAZLADAN durak yaratirdi — sekme ile gezen kullanici
  // icin arayuzu kullanilmaz hale getirir.
  //
  // Kayip degil: durum adi ekran okuyucuya `aria-label` ile zaten
  // veriliyor. Ipucu ANLAMI ekler, adi degil — yani ek bilgidir.
  // Tek basina duran ogeler (rozet) `focusable` ile acikca acilir.
  const triggerProps = {
    ref: ref as React.Ref<never>,
    onMouseEnter: ac,
    onMouseLeave: kapat,
    ...(focusable
      ? { onFocus: ac, onBlur: kapat, tabIndex: 0 }
      : null),
    "aria-describedby": konum ? tipId : undefined
  };

  const tip = konum
    ? createPortal(
        <RuntimeTipKutusu id={tipId} state={state} konum={konum} t={t} />,
        document.body
      )
    : null;

  return { triggerProps, tip };
}

function RuntimeTipKutusu({
  id,
  state,
  konum,
  t
}: {
  id: string;
  state: DeviceRuntimeState;
  konum: Konum;
  t: (k: string) => string;
}) {
  const ad = t(state.labelKey);
  // `labelKey` -> "deviceRuntime.state.smartIdle"; aciklama ayni son eki tasir.
  const aciklama = t(state.labelKey.replace(".state.", ".stateHint."));
  const kova = t(`deviceRuntime.kpi.${state.bucket}`);
  // UNKNOWN'da ad ve kova AYNI metin ("Bilinmiyor"); ikisini yan yana
  // basmak kullaniciya hicbir sey katmaz, gurultu olur.
  const kovaGoster = kova !== ad;

  return (
    <div
      id={id}
      role="tooltip"
      className={`runtime-tip ${runtimeToneClass(state)} ${
        konum.altta ? "runtime-tip--altta" : ""
      }`.trim()}
      style={{ left: konum.x, top: konum.y }}
    >
      <div className="runtime-tip__bas">
        <span className="runtime-tip__nokta" aria-hidden="true" />
        <span className="runtime-tip__ad">{ad}</span>
        {kovaGoster ? <span className="runtime-tip__kova">{kova}</span> : null}
      </div>
      <p className="runtime-tip__metin">{aciklama}</p>
      {/* KAYNAK NOTU — yalnizca durum gateway'in ANLIK karari DEGILSE.
          Tazeyken bir sey yazmak her ipucuna gereksiz satir eklerdi. */}
      {state.stale ? (
        <p className="runtime-tip__kaynak">{t("deviceRuntime.source.staleHint")}</p>
      ) : state.source !== "gateway" ? (
        <p className="runtime-tip__kaynak">{t("deviceRuntime.source.legacyHint")}</p>
      ) : null}
    </div>
  );
}

/**
 * Ipucu tasiyan `<span>` — nokta/rozet DISINDAKI yerler icin.
 *
 * Bazi ekranlar rengi kendi elemanina uyguluyor (`seg-menu-device-status`,
 * `device-sidebar-statusdot`, `device-comms-pill`...). Oralarda `useRuntimeTip`
 * DOGRUDAN CAGRILAMAZ: cogu bir `.map()` govdesinde ve kanca donguye girerse
 * React'in hook sirasi bozulur ("Rendered more hooks than during the previous
 * render", bkz. tests/hookSirasi.test.ts). Bilesen olarak sarmalamak bu sorunu
 * yapisal olarak ortadan kaldirir: her satir kendi bileseni, kendi kancasi.
 *
 * Ton sinifini KENDISI ekler — cagiran `runtimeToneClass` yazmayi unutursa
 * renksiz kalmasin.
 */
export function RuntimeTip({
  state,
  className = "",
  focusable = false,
  children,
  ...rest
}: {
  state: DeviceRuntimeState;
  className?: string;
  /** Sekme ile ulasilabilir olsun mu. Tek basina duran ogelerde `true`;
   *  bir `<button>` icinde duranlarda BIRAKILMAZ (bkz. `useRuntimeTip`). */
  focusable?: boolean;
  children?: React.ReactNode;
} & Omit<React.HTMLAttributes<HTMLSpanElement>, "children" | "className">) {
  const { triggerProps, tip } = useRuntimeTip(state, { focusable });
  return (
    <>
      <span
        {...rest}
        {...triggerProps}
        className={`${className} ${runtimeToneClass(state)}`.trim()}
      >
        {children}
      </span>
      {tip}
    </>
  );
}
