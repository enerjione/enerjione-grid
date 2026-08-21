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
import { createPortal } from "react-dom";
import { useTranslation } from "react-i18next";

import type { DeviceRuntimeState } from "../shared/deviceRuntimeState";
import { runtimeToneClass } from "./RuntimeStateChip";
import { useIpucuKonum, type IpucuKonum } from "./tipKonum";

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
  // Olcum/portal/kapanma davranisi `tipKonum`da; burada yalnizca ICERIK var.
  const { konum, tipId, triggerProps } = useIpucuKonum({ focusable });

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
  konum: IpucuKonum;
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
