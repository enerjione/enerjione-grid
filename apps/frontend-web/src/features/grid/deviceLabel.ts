/**
 * Haritadaki cihaz etiketi — "hangi cihaz nerede" sorusunun cevabi.
 *
 * NEDEN AYRI VE SAF
 * -----------------
 * Ad yalnizca `devices` listesinden okunuyordu:
 *
 *     const dev = devices.find((d) => d.id === seg.device_id);
 *     {dev ? <Tooltip permanent>{dev.name}</Tooltip> : null}
 *
 * Liste bos ya da eksik oldugunda (sayfa cihazlar yuklenmeden acildi, kapsam
 * filtresi cihazi dusurdu, kayit sonradan geldi) harita cihazi ADSIZ bir elmas
 * olarak ciziyordu. Ekranda alti isaretci var ama hangisinin hangi cihaz
 * oldugu okunamiyor — haritanin varolma sebebi tam olarak bu soruydu.
 *
 * Oysa SEGMENT bu bilgiyi zaten tasiyor: `device_name` / `device_code` alanlari
 * hat detayiyla AYNI istekten gelir (bkz. backend `grid_topology` segment
 * ciktisi). Yani listeye hic bakmadan da etiket yazilabilir.
 *
 * Zincir bilincli olarak bu sirada: canli cihaz kaydi -> segmentin kopyasi ->
 * kod -> kimlik. En kotu durumda "#12" yazar; HICBIR SEY yazmamak en kotusuydu.
 */

export type EtiketCihaz = { name?: string | null; code?: string | null } | null | undefined;

export type EtiketSegment = {
  device_id?: number | null;
  device_name?: string | null;
  device_code?: string | null;
};

const kirp = (v: string | null | undefined): string => (v ?? "").trim();

/** Isaretcinin uzerinde yazacak ad. Her zaman DOLU bir metin doner. */
export function cihazEtiketi(dev: EtiketCihaz, seg: EtiketSegment): string {
  return (
    kirp(dev?.name) ||
    kirp(seg.device_name) ||
    kirp(dev?.code) ||
    kirp(seg.device_code) ||
    `#${seg.device_id ?? "?"}`
  );
}

/** Ipucunda gosterilecek kod. Ad ile AYNI ise bos doner (ayni metni iki kez
 *  yazmak bilgi tasimaz). */
export function cihazKodu(dev: EtiketCihaz, seg: EtiketSegment): string {
  const kod = kirp(dev?.code) || kirp(seg.device_code);
  return kod === cihazEtiketi(dev, seg) ? "" : kod;
}
