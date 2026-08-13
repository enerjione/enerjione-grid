/** Baslik yanindaki bilgi ikonu — aciklama satirda yer kaplamaz, ustune
 *  gelince (ya da klavyeyle odaklaninca) balon olarak cikar.
 *
 *  NEDEN VAR: her kartin basliginin altinda iki-uc satirlik bir aciklama
 *  duruyordu. Ayari ILK KEZ kuran icin gerekli, her gun bakan icin gurultu:
 *  kartlari uzatiyor, asil is olan alanlari asagi itiyor ve uc kartin
 *  yuksekligini birbirinden ayirdigi icin izgarayi da bozuyordu.
 *
 *  ERISILEBILIRLIK: ikon bir BUTON ve erisilebilir adi aciklamanin tam
 *  metni — ekran okuyucu balonu beklemeden okur, klavye kullanicisi Tab ile
 *  odaklandiginda balon gorunur. Balonun kendisi `aria-hidden`, yoksa ayni
 *  metin iki kez okunurdu.
 */

type Props = {
  /** Balon metni. Dizi verilirse her eleman ayri paragraf olur. */
  text: string | string[];
  /** Balonun yaslanacagi kenar. Kartin sag ucundaki bir ikonda "sag"
   *  verilmezse balon panelin disina tasar ve kirpilir. */
  yon?: "sol" | "sag";
};

export function PsInfo({ text, yon = "sol" }: Props) {
  const paragraflar = Array.isArray(text) ? text : [text];
  return (
    <button type="button" className={`ps-info ps-info--${yon}`} aria-label={paragraflar.join(" ")}>
      <span className="material-symbols-outlined" aria-hidden="true">
        info
      </span>
      <span className="ps-info-tip" aria-hidden="true">
        {paragraflar.map((p) => (
          <span key={p}>{p}</span>
        ))}
      </span>
    </button>
  );
}
