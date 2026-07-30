/**
 * Harita karo katmanlari — TEK KAYNAK.
 *
 * Onceden bu adresler dort ayri dosyada (DeviceMapTab, GridManagementPanel,
 * FaultDetailModal, DeviceSidebar) kopyalanmisti; biri degisince digerleri
 * unutuluyordu.
 *
 * CEVRIMDISI: Karolar artik dogrudan internetten degil BACKEND uzerinden
 * gelir. Backend once diske bakar, yoksa (ve internet varsa) yukari akistan
 * cekip diske yazar. Boylece:
 *   - kullanicinin gezdigi alan kendiliginden onbellege girer,
 *   - "alan indir" ozelligi bu onbellegi onden doldurur,
 *   - internet kesildiginde indirilmis alan calismaya devam eder.
 *
 * Kimlik dogrulama: karo istegi <img> ile ayni origin'e gider ve oturum
 * cookie'si (`e1_session`) kendiliginden tasinir; ek bir sey gerekmez.
 */

import { API_BASE_URL } from "./api";

export type MapLayerKey = "osm" | "satellite" | "topo" | "dark";

export type MapLayerDef = {
  key: MapLayerKey;
  /** LayersControl'de gorunen ad — i18n anahtari. */
  labelKey: string;
  attribution: string;
  maxZoom: number;
};

/** Backend'deki LAYERS kaydiyla ayni anahtarlar (map_tile_service.py). */
export const MAP_LAYERS: MapLayerDef[] = [
  {
    key: "osm",
    labelKey: "map.layers.street",
    attribution:
      '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>',
    maxZoom: 19
  },
  {
    key: "satellite",
    labelKey: "map.layers.satellite",
    attribution: "Tiles &copy; Esri — Source: Esri, Maxar, Earthstar Geographics",
    maxZoom: 19
  },
  {
    key: "topo",
    labelKey: "map.layers.topo",
    attribution:
      'Map data: &copy; OpenStreetMap, SRTM | Style: <a href="https://opentopomap.org">OpenTopoMap</a> (CC-BY-SA)',
    maxZoom: 17
  },
  {
    key: "dark",
    labelKey: "map.layers.dark",
    attribution:
      '&copy; <a href="https://www.openstreetmap.org/copyright">OSM</a> &copy; <a href="https://carto.com/attributions">CARTO</a>',
    maxZoom: 19
  }
];

/**
 * Leaflet TileLayer icin url sablonu. {z}/{x}/{y} yer tutuculari Leaflet
 * tarafindan doldurulur.
 *
 * NOT: Esri'nin {z}/{y}/{x} sirasi burada GORUNMEZ — o donusum backend'de
 * yapiliyor. Istemci acisindan tum katmanlar ayni semayi kullanir.
 */
export function tileUrl(layer: MapLayerKey): string {
  return `${API_BASE_URL}/map-tiles/${layer}/{z}/{x}/{y}.png`;
}

/** Varsayilan (ilk acilan) katman. */
export const DEFAULT_MAP_LAYER: MapLayerKey = "osm";
