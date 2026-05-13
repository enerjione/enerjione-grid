/**
 * react-leaflet LayersControl ile katman degistirildiginde tile'larin
 * gec yuklenmesi/krem ekran sorunu icin yardimci bilesen.
 *
 * Eklenmesi: <MapContainer> alt agacinda bir kez yer alir.
 * `baselayerchange` event'ini dinler; arka planda invalidateSize + minik
 * zoom-pulse ile Leaflet'in tile request akisini yeniden tetikler.
 */
import { useEffect } from "react";
import { useMap } from "react-leaflet";

export function MapLayerSwitchFix() {
  const map = useMap();
  useEffect(() => {
    const onBaseLayerChange = () => {
      window.setTimeout(() => {
        try {
          map.invalidateSize();
          const z = map.getZoom();
          map.setZoom(z, { animate: false });
        } catch {
          /* ignore */
        }
      }, 80);
    };
    map.on("baselayerchange", onBaseLayerChange);
    return () => {
      map.off("baselayerchange", onBaseLayerChange);
    };
  }, [map]);
  return null;
}
