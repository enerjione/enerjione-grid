import { useEffect, useRef, useState } from "react";
import { TileLayer } from "react-leaflet";

import {
  directTileUrl,
  layerDef,
  tileSubdomains,
  tileUrl,
  type MapLayerKey
} from "../shared/mapTiles";

/**
 * Karo katmani — backend proxy'si calismazsa DOGRUDAN internete duser.
 *
 * NEDEN VAR:
 *   Karolar cevrimdisi onbellegi icin backend uzerinden geciyor. Bu dogru bir
 *   karar ama haritayi backend yoluna BAGIMLI hale getirdi: nginx yonlendirmesi,
 *   oturum cerezi ya da backend'in kendisi bozuldugunda harita tamamen
 *   kararıyor — tarayicida internet olsa bile. Sahada bu, "sistem bozuk"
 *   gorunumu veriyor; oysa yalnizca onbellek yolu bozuk.
 *
 *   Bu bilesen proxy'den karo gelmedigini olcup yukari akisa geciyor. Boylece:
 *     - internet varsa harita HER ZAMAN gelir (kullanici beklentisi budur),
 *     - internet yoksa yine proxy/onbellek yolu calisir (birincil yol odur).
 *
 * ESIK NEDEN 3:
 *   Tek bir karo gecici olarak patlayabilir (kapsama disi zoom, anlik ag
 *   hatasi). Ucuncu hatada artik "sans eseri" degil, yol bozuk demektir.
 *
 * KARAR OTURUM GENELINDE PAYLASILIR:
 *   Bir harita proxy'nin bozuk oldugunu ogrenince digerleri ayni hatalari
 *   bastan yasamasin diye modul seviyesinde isaretliyoruz. Aksi halde her
 *   harita ekraninda kullanici ayni bos-karo anini tekrar gorurdu.
 */
let proxyBroken = false;
const listeners = new Set<() => void>();

function markProxyBroken(): void {
  if (proxyBroken) return;
  proxyBroken = true;
  // Konsolda TEK satir: operator/destek nedenini gorebilsin.
  console.warn(
    "[harita] Karo proxy'sinden (/api/v1/map-tiles) karo alinamadi; " +
      "dogrudan yukari akisa gecildi. Cevrimdisi harita onbellegi bu oturumda kullanilamaz."
  );
  listeners.forEach((fn) => fn());
}

/** Testler ve elle kurtarma icin: bir sonraki denemede proxy tekrar denensin. */
export function resetTileFallback(): void {
  proxyBroken = false;
  listeners.forEach((fn) => fn());
}

type Props = {
  layer: MapLayerKey;
  attribution?: string;
  /** Ust sinir override'i; verilmezse katman tanimindaki deger kullanilir. */
  maxZoom?: number;
};

export function ResilientTileLayer({ layer, attribution, maxZoom }: Props) {
  const [direct, setDirect] = useState(proxyBroken);
  const errorCount = useRef(0);
  const def = layerDef(layer);

  useEffect(() => {
    const onChange = () => setDirect(proxyBroken);
    listeners.add(onChange);
    return () => {
      listeners.delete(onChange);
    };
  }, []);

  return (
    <TileLayer
      // `key`: url sablonu degisince Leaflet katmani bastan kurulsun. Aksi
      // halde basarisiz karolar onbellekte "hatali" olarak kalir ve yeni
      // adresle tekrar denenmez. Katman anahtarini da iceriyor ki katman
      // degistiginde (sokak -> uydu) eski karolar ekranda kalmasin.
      key={`${layer}-${direct ? "direct" : "proxy"}`}
      url={direct ? directTileUrl(layer) : tileUrl(layer)}
      subdomains={direct ? (tileSubdomains(layer) ?? "abc") : "abc"}
      attribution={attribution}
      maxZoom={maxZoom ?? def.maxZoom}
      // KRITIK: bu olmadan Leaflet, saglayicida OLMAYAN zoom seviyeleri icin
      // de karo ister. Esri o istege hata degil, uzerinde "Map data not yet
      // available" yazan gecerli bir PNG dondurur — yani sorun sessizce
      // GORUNTUYE karisir, hicbir hata yolu tetiklenmez. `maxNativeZoom` ile
      // o sinirin ustunde karo istenmez; Leaflet son gercek karoyu buyutur.
      maxNativeZoom={def.maxNativeZoom}
      eventHandlers={{
        tileerror: () => {
          if (direct) return; // zaten son caredeyiz; tekrar gecis yok
          errorCount.current += 1;
          if (errorCount.current >= 3) markProxyBroken();
        },
        // Proxy'den karo GELDIYSE sayaci sifirla: tek tuk hata birikip
        // saglikli bir kurulumu yanlislikla yedek yola dusurmesin.
        tileload: () => {
          if (!direct) errorCount.current = 0;
        }
      }}
    />
  );
}
