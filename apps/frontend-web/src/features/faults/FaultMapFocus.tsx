/**
 * Haritayi secilen odak noktalarina cerceveler.
 *
 * Ayri bir bilesen cunku `useMap` yalnizca `MapContainer`in ICINDE calisir.
 */
import { useEffect } from "react";
import L from "leaflet";
import { useMap } from "react-leaflet";

export function FitFocus({ points }: { points: [number, number][] }) {
  const map = useMap();
  // Anahtar: nokta sayisi + ilk/son nokta. Tum diziyi bagimlilik yapmak her
  // render'da yeni referans uretip sonsuz `fitBounds` dongusu kurardi.
  const anahtar = points.length
    ? `${points.length}|${points[0].join()}|${points[points.length - 1].join()}`
    : "bos";

  useEffect(() => {
    if (points.length === 0) return;
    // Kap yeni acilmis olabilir (sekmeye ilk gecis): olcum tazelenmezse
    // Leaflet 0x0 kaba sigdirmaya calisir ve harita bos gorunur.
    map.invalidateSize({ animate: false });
    if (points.length === 1) {
      map.setView(points[0], 16, { animate: true });
      return;
    }
    map.fitBounds(L.latLngBounds(points), { padding: [28, 28], animate: true });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [map, anahtar]);

  return null;
}
