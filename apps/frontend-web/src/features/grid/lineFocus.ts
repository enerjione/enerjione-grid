/**
 * Haritayi secili hatta odaklama KARARI — saf mantik (Leaflet'siz, React'siz).
 *
 * Neden ayri dosya: karar birkac ince kosul tasiyor ve hepsi sahada birer
 * hataya karsilik geliyor (bkz. asagidaki maddeler). React bileseni icinde
 * kaldigi surece calistirilarak dogrulanamiyordu; burada saf oldugu icin
 * `tests/lineFocus.test.ts` gercekten kosuyor.
 *
 * Kurallar:
 *  1. Hat secili degilse veya KULLANILABILIR koordinat yoksa bir sey yapma.
 *     "Yapildi" isareti de KOYMA — direkler yuklendiginde odaklanmali.
 *  2. Bozuk koordinat (null/NaN) atilir: tek bir NaN, Leaflet'in bounds
 *     hesabini sessizce gecersiz kilar ve harita hicbir yere gitmez.
 *  3. Ayni (hat, istek) cifti icin iki kez odaklanma — kullanicinin elle
 *     yaptigi zoom/kaydirma her veri tazelemesinde geri alinmasin.
 *  4. `nonce` (listede hatta tiklama sayaci) artarsa AYNI hat icin de
 *     yeniden odaklan: kullanici haritayi kaydirdiktan sonra hatta tiklayip
 *     geri donebilmeli.
 *  5. Tek direk varsa sabit yakinlik; birden fazlaysa hepsini kapsayan kutu.
 */

export type FocusPoint = { latitude: number; longitude: number };

export type FocusPlan =
  | { kind: "skip" }
  /** Tek direk: sabit zoom ile ortala. */
  | { kind: "point"; key: string; latitude: number; longitude: number; zoom: number }
  /** Birden fazla direk: hepsini kapsayan kutuya sigdir. */
  | { kind: "bounds"; key: string; points: FocusPoint[] };

/** Tek direkte kullanilan yakinlik. Direk seviyesinde calisma icin uygun. */
export const SINGLE_POLE_ZOOM = 16;

export function planLineFocus(input: {
  lineId: number | null;
  poles: readonly FocusPoint[];
  /** Listede hatta her tiklamada artan sayac. */
  nonce: number;
  /** En son uygulanan planin anahtari (yoksa bos string). */
  lastKey: string;
}): FocusPlan {
  const { lineId, poles, nonce, lastKey } = input;
  if (lineId === null) return { kind: "skip" };

  const valid = poles.filter(
    (p) => Number.isFinite(p.latitude) && Number.isFinite(p.longitude)
  );
  // Kural 1: direkler henuz gelmediyse BEKLE (isaret koymadan).
  if (valid.length === 0) return { kind: "skip" };

  const key = `${lineId}:${nonce}`;
  if (key === lastKey) return { kind: "skip" };

  if (valid.length === 1) {
    return {
      kind: "point",
      key,
      latitude: valid[0].latitude,
      longitude: valid[0].longitude,
      zoom: SINGLE_POLE_ZOOM
    };
  }
  return { kind: "bounds", key, points: valid };
}
