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
  /** Kullanicinin yaklasabilecegi EN YUKSEK zoom. */
  maxZoom: number;
  /** Saglayicidan GERCEKTEN karo istenecek en yuksek zoom. */
  maxNativeZoom: number;
};

/**
 * Kullanicinin yaklasabilecegi ust sinir. `maxNativeZoom` ustunde Leaflet yeni
 * karo ISTEMEZ, son gercek karoyu buyuterek gosterir: goruntu yumusar ama
 * direk/cihaz isaretleri ayrisir. Saha ekibi iki direk arasini ayirt etmek
 * icin bu yakinliga ihtiyac duyuyor.
 */
const MAX_ZOOM = 22;

/** Backend'deki LAYERS kaydiyla ayni anahtarlar (map_tile_service.py). */
export const MAP_LAYERS: MapLayerDef[] = [
  {
    key: "osm",
    labelKey: "map.layers.street",
    attribution:
      '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>',
    maxZoom: MAX_ZOOM,
    maxNativeZoom: 19
  },
  {
    key: "satellite",
    labelKey: "map.layers.satellite",
    attribution: "Tiles &copy; Esri — Source: Esri, Maxar, Earthstar Geographics",
    maxZoom: MAX_ZOOM,
    // 18 — Esri World Imagery'nin TURKIYE KIRSALINDA guvenle veri verdigi son
    // seviye. Ustunde Esri hata DONDURMEZ: uzerinde "Map data not yet
    // available" yazan gecerli bir PNG (HTTP 200) doner. Bu yuzden `tileerror`
    // hic tetiklenmez, yedek yola gecis calismaz ve karo onbellege bile
    // yazilirdi — kullanicinin sikayet ettigi gri "veri yok" karolari buydu.
    // Cozum sinirdan gecmemek: 18 ustu istenmiyor, goruntu buyutulerek
    // gosteriliyor.
    maxNativeZoom: 18
  },
  {
    key: "topo",
    labelKey: "map.layers.topo",
    attribution:
      'Map data: &copy; OpenStreetMap, SRTM | Style: <a href="https://opentopomap.org">OpenTopoMap</a> (CC-BY-SA)',
    maxZoom: MAX_ZOOM,
    maxNativeZoom: 17
  },
  {
    key: "dark",
    labelKey: "map.layers.dark",
    attribution:
      '&copy; <a href="https://www.openstreetmap.org/copyright">OSM</a> &copy; <a href="https://carto.com/attributions">CARTO</a>',
    maxZoom: MAX_ZOOM,
    maxNativeZoom: 20
  }
];

/** Katman tanimi — bulunamazsa OSM'ye duser (cagiran yerlerde null kontrolu yok). */
export function layerDef(key: MapLayerKey): MapLayerDef {
  return MAP_LAYERS.find((l) => l.key === key) ?? MAP_LAYERS[0];
}

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

/**
 * DOGRUDAN yukari akis adresleri — backend proxy'si calismadiginda kullanilir.
 *
 * NEDEN GEREKLI: karolari backend uzerinden gecirmek cevrimdisi onbellegi
 * mumkun kildi ama haritayi backend'e BAGIMLI hale getirdi. Proxy yolundaki
 * her ariza (nginx yonlendirmesi, oturum cerezi, backend kapali) haritayi
 * tamamen karartiyordu — internet varken bile. Kullanici beklentisi hakli:
 * internet varsa harita gelmeli.
 *
 * Bu yuzden ResilientTileLayer, proxy'den karo alamadigini anlayinca bu
 * adreslere duser. Cevrimdisi onbellegi hala birincil yoldur; burasi yalnizca
 * emniyet supabidir.
 *
 * NOT: Esri {z}/{y}/{x} sirasi kullanir (digerleri {z}/{x}/{y}). Leaflet duz
 * metin ikamesi yaptigi icin sablonu bu sirayla yazmak yeterli — backend'deki
 * donusumun istemci tarafi karsiligi budur.
 */
type UpstreamDef = { url: string; subdomains?: string };

const UPSTREAM: Record<MapLayerKey, UpstreamDef> = {
  osm: { url: "https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", subdomains: "abc" },
  satellite: {
    url: "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}"
  },
  topo: { url: "https://{s}.tile.opentopomap.org/{z}/{x}/{y}.png", subdomains: "abc" },
  dark: { url: "https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}.png", subdomains: "abcd" }
};

export function directTileUrl(layer: MapLayerKey): string {
  return UPSTREAM[layer].url;
}

/** Leaflet `subdomains` prop'u — {s} kullanmayan katmanlarda undefined. */
export function tileSubdomains(layer: MapLayerKey): string | undefined {
  return UPSTREAM[layer].subdomains;
}

/** Varsayilan (ilk acilan) katman. */
export const DEFAULT_MAP_LAYER: MapLayerKey = "osm";
