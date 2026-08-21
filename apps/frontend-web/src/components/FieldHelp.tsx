/**
 * ALAN YARDIMI — "bu ayar ne ise yarar?" sorusunu ustune gelince cevaplar.
 *
 * NE COZUYOR
 * ----------
 * Haberlesme ayarlari formunda her alanin ALTINDA kalici bir `<small>`
 * aciklama duruyordu. Yedi alanda birden olunca form bir AYAR EKRANI degil
 * bir METIN BLOGU haline geliyordu: goz once paragraflari tariyor, alanlari
 * sonra buluyordu. Aciklamalar tek tek dogruydu ama hepsinin ayni anda
 * ekranda durmasi GEREKMIYORDU — bir ayari ilk kez kuran okur, her gun
 * kullanan okumaz.
 *
 * Aciklama silinmedi, ISTEK UZERINE gosterilir hale getirildi.
 *
 * NEDEN TARAYICININ `title`i DEGIL
 * --------------------------------
 * `title` ~1 saniye gecikmeyle acilir, uzun metni kirpar, klavyeyle hic
 * acilmaz ve dokunmatikte yoktur. Ayar aciklamalari cogu zaman iki
 * cumleliktir; `title` onlari okunamaz hale getiriyordu.
 *
 * NEDEN GERCEK BIR `<button>`
 * ---------------------------
 * SAHADA TABLET KULLANILIYOR ve dokunmatikte HOVER YOKTUR: yalnizca fareyle
 * acilan bir yardim, o cihazlarda bilgiyi tamamen erisilmez birakirdi.
 * Dokunmak da acar. `role="button"` tasiyan ama tiklaninca hicbir sey
 * yapmayan bir `<span>` ise ekran okuyucuya tutamayacagi bir soz verirdi.
 *
 * `<label>` ICINDE DURUYOR: varsayilan davranista butona tiklamak etiketin
 * bagli oldugu alani da tetikler (select acilir, input odaklanir). `?`
 * isaretine basinca acilan bir liste kullaniciyi sasirtirdi; bu yuzden olay
 * hem `preventDefault` hem `stopPropagation` ile durdurulur.
 *
 * ODAKLANABILIR — `useRuntimeTip`in aksine.
 * Durum noktalari bir `<button>` ICINDE duruyor ve odaklanabilir olmalari hem
 * gecersiz HTML hem de 600 cihazlik listede 600 fazladan sekme duragi
 * demekti. Alan yardimi ise formda TEK BASINA durur ve tasidigi bilgi baska
 * hicbir yerde yoktur: klavye kullanicisinin ona ulasamamasi bilgiyi
 * tamamen kaybettirirdi.
 */
import { createPortal } from "react-dom";

import { useIpucuKonum } from "./tipKonum";

/**
 * @param metin Gosterilecek aciklama. BOS/tanimsizsa isaret HIC cizilmez —
 *   bos bir kutu acan bir dugme kullaniciya "burada bir sey var" der ve
 *   yalan soyler.
 * @param label Isaretin ekran okuyucudaki adi; hangi alani anlattigi.
 */
export function FieldHelp({ metin, label }: { metin?: string | null; label: string }) {
  const { konum, tipId, ac, triggerProps } = useIpucuKonum({ focusable: true });
  const temiz = (metin ?? "").trim();
  if (!temiz) return null;

  return (
    <>
      <button
        {...triggerProps}
        type="button"
        className="field-help"
        aria-label={label}
        aria-expanded={konum ? true : false}
        onClick={(e) => {
          // Etiketin bagli oldugu alani tetiklemesin.
          e.preventDefault();
          e.stopPropagation();
          // KAPATMAZ, yalnizca acar. Dokunmatikte tek dokunus cogu tarayicida
          // once sentetik `mouseenter` (acar) sonra `click` uretir; burada
          // "degistir" deseydik ipucu acilir acilmaz kapanirdi. Kapatmayi
          // disari dokunma / Escape / kaydirma yurutur.
          ac();
        }}
      >
        {/* Ikon FONT DEGIL METIN: `material-symbols` subset'i bu ekranda
            yuklu olmayabilir ve eksik glif bos kare olarak cizilir. */}
        <span aria-hidden="true">?</span>
      </button>
      {konum
        ? createPortal(
            <div
              id={tipId}
              role="tooltip"
              className={`field-help-tip${konum.altta ? " field-help-tip--altta" : ""}`}
              style={{ left: konum.x, top: konum.y }}
            >
              {temiz}
            </div>,
            document.body
          )
        : null}
    </>
  );
}
