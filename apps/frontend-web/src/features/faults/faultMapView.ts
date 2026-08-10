/**
 * Ariza detay haritasinin SAF cekirdegi — React'siz, Leaflet'siz.
 *
 * NE YAPAR
 * --------
 * Sebeke anlik goruntusu + ariza kaydindan haritanin cizecegi her seyi
 * turetir: hattin uc parcasi (saglikli / arizali / saglikli), direk ve cihaz
 * isaretcileri, uc ayri odak kutusu.
 *
 * NEDEN AYRI DOSYA
 * ----------------
 * Bu hesap 260 satirdi ve bir React bileseninin `useMemo`'su icinde
 * yasiyordu; yani CALISTIRILARAK dogrulanamiyordu. Oysa tasidigi karar
 * agir: kirmizi parca sahada hangi iki direk arasina gidilecegini soyler.
 * Bir direk kaymasi, ekibi yanlis acikliga gonderir.
 *
 * UC PARCA MANTIGI
 * ----------------
 *   hat basi -> son ariza ALGILAYAN cihaz   : saglikli
 *   son algilayan -> ilk ALGILAMAYAN cihaz  : ARIZALI (kirmizi kesik)
 *   ilk algilamayan -> hat ucu              : saglikli
 *
 * Ariza, akimi goren son cihaz ile gormeyen ilk cihazin ARASINDADIR; bu
 * arizanin yerini daraltmanin tek dogru yoludur.
 */

export type Nokta = { latitude: number; longitude: number };
export type LatLon = [number, number];

export type DirekGirdi = Nokta & {
  id: number;
  line_id: number;
  sequence_no: number;
  name?: string | null;
};

export type SegmentGirdi = {
  line_id: number;
  from_pole_id: number;
  to_pole_id: number;
  device_id?: number | null;
  device_position_t?: number | null;
};

export type HatGirdi = { id: number; name: string };

export type ArizaGirdi = {
  line_id: number;
  from_pole_id?: number | null;
  to_pole_id?: number | null;
  from_pole_seq?: number | null;
  to_pole_seq?: number | null;
};

export type CihazGirdi = { id: number; name?: string | null; code?: string | null };

export type HaritaGorunumu = {
  preGreen: LatLon[];
  faultRed: LatLon[];
  postGreen: LatLon[];
  center: LatLon;
  zoom: number;
  /** Odak kutulari: ariza bolgesi / tum hat / tum sebeke. */
  zoneBounds: LatLon[];
  lineBounds: LatLon[];
  gridBounds: LatLon[];
  otherLines: { lineId: number; name: string; path: LatLon[] }[];
  polesWithRole: {
    p: DirekGirdi;
    isFromFault: boolean;
    isToFault: boolean;
    isInFaultRange: boolean;
  }[];
  rangePoles: {
    id: number;
    sequence_no: number;
    name: string;
    latitude: number;
    longitude: number;
    isStart: boolean;
    isEnd: boolean;
  }[];
  deviceMarkers: {
    deviceId: number;
    lat: number;
    lon: number;
    isRed: boolean;
    name: string;
    code?: string | null;
  }[];
};

/** Iki nokta arasinda dogrusal ara deger. */
export function lerp(a: Nokta, b: Nokta, t: number): LatLon {
  return [
    a.latitude + (b.latitude - a.latitude) * t,
    a.longitude + (b.longitude - a.longitude) * t
  ];
}

/**
 * Polyline'i verilen noktada IKIYE boler.
 *
 * Nokta genelde cizginin tam ustunde degildir (cihaz iki direk arasinda bir
 * `t` oraninda durur), bu yuzden en yakin kenara DIK PROJEKSIYON alinir ve
 * bolme o izdusum noktasindan yapilir. Boylece hattin gercek geometrisi
 * korunur — eski "from -> to duz cizgi" yaklasimi kivrimli bir hatta
 * arizayi tarlanin ortasinda gosteriyordu.
 */
export function bolPolyline(
  polyline: LatLon[],
  splitAt: LatLon
): { pre: LatLon[]; post: LatLon[] } {
  if (polyline.length < 2) return { pre: polyline, post: [] };
  let bestK = 0;
  let bestD = Infinity;
  let bestProj: LatLon = polyline[0];
  for (let k = 0; k < polyline.length - 1; k += 1) {
    const a = polyline[k];
    const b = polyline[k + 1];
    const dx = b[0] - a[0];
    const dy = b[1] - a[1];
    const len2 = dx * dx + dy * dy;
    if (len2 === 0) continue;
    const t = Math.max(
      0,
      Math.min(1, ((splitAt[0] - a[0]) * dx + (splitAt[1] - a[1]) * dy) / len2)
    );
    const projX = a[0] + t * dx;
    const projY = a[1] + t * dy;
    const ddx = projX - splitAt[0];
    const ddy = projY - splitAt[1];
    const d = ddx * ddx + ddy * ddy;
    if (d < bestD) {
      bestD = d;
      bestK = k;
      bestProj = [projX, projY];
    }
  }
  return {
    pre: [...polyline.slice(0, bestK + 1), bestProj],
    post: [bestProj, ...polyline.slice(bestK + 1)]
  };
}

/**
 * Sifir uzunluklu parcayi ELER.
 *
 * Bolme noktasi hattin tam ucundaysa (son kirmizidan sonra yesil cihaz yok
 * -> bolme son direkte) geriye iki AYNI noktadan olusan bir parca kalir.
 * Cagiran taraf "en az iki nokta var" diye cizer ve yuvarlak uclu 4 piksellik
 * bir cizgi, hattin ucunde kucuk bir YESIL NOKTA birakir. Orada saglikli bir
 * cihaz yok; operator onu oyle okur.
 */
function anlamli(parca: LatLon[]): LatLon[] {
  if (parca.length < 2) return [];
  const [ilkLat, ilkLon] = parca[0];
  const farkliVar = parca.some(
    ([lat, lon]) => Math.abs(lat - ilkLat) > 1e-9 || Math.abs(lon - ilkLon) > 1e-9
  );
  return farkliVar ? parca : [];
}

/** Yayilima gore makul bir baslangic yakinligi. */
export function zoomFor(span: number): number {
  if (span < 0.003) return 16;
  if (span < 0.01) return 15;
  if (span < 0.03) return 14;
  if (span < 0.1) return 13;
  return 11;
}

export function buildFaultMapView(input: {
  poles: readonly DirekGirdi[];
  segments: readonly SegmentGirdi[];
  lines: readonly HatGirdi[];
  fault: ArizaGirdi;
  devices: readonly CihazGirdi[];
  /** Su an alarmi RESETLENMEMIS cihazlar — "ariza algiladi" demek. */
  alarmActiveDeviceIds: ReadonlySet<number>;
  /** Adi olmayan direk/cihaz icin yedek etiketler (i18n cagiranda). */
  poleFallback: string;
  deviceFallback: string;
}): HaritaGorunumu | null {
  const { poles, segments, lines, fault, devices, alarmActiveDeviceIds } = input;

  const polesById = new Map(poles.map((p) => [p.id, p]));
  const linePoles = poles
    .filter((p) => p.line_id === fault.line_id)
    .sort((a, b) => a.sequence_no - b.sequence_no);
  if (linePoles.length === 0) return null;

  // --- Cihazli segmentleri slot (direk cifti) bazinda topla ---------------
  type SegRec = {
    deviceId: number;
    t: number; // slot icindeki konum 0..1
    isRed: boolean;
  };
  const slotSegs = new Map<string, SegRec[]>();
  for (const seg of segments) {
    if (seg.line_id !== fault.line_id || !seg.device_id) continue;
    if (!polesById.has(seg.from_pole_id) || !polesById.has(seg.to_pole_id)) continue;
    const t =
      seg.device_position_t !== null && seg.device_position_t !== undefined
        ? seg.device_position_t
        : 0.5;
    const key = `${seg.from_pole_id}|${seg.to_pole_id}`;
    const arr = slotSegs.get(key) ?? [];
    arr.push({ deviceId: seg.device_id, t, isRed: alarmActiveDeviceIds.has(seg.device_id) });
    slotSegs.set(key, arr);
  }
  for (const [, arr] of slotSegs) {
    arr.sort((a, b) => a.t - b.t);
    // Hepsinin t'si varsayilan 0.5 ise (elle ayarlanmamis) esit dagit;
    // aksi halde ayni slottaki cihazlar TEK noktada ust uste binerdi.
    if (arr.length > 1 && arr.every((r) => r.t === 0.5)) {
      const n = arr.length;
      arr.forEach((r, i) => {
        r.t = (i + 1) / (n + 1);
      });
    }
  }

  // --- Hattaki cihazlari bastan sona sirala -------------------------------
  type DevPoint = SegRec & { lat: number; lon: number };
  const lineDevices: DevPoint[] = [];
  for (let i = 0; i < linePoles.length - 1; i += 1) {
    const a = linePoles[i];
    const b = linePoles[i + 1];
    for (const r of slotSegs.get(`${a.id}|${b.id}`) ?? []) {
      const [lat, lon] = lerp(a, b, r.t);
      lineDevices.push({ ...r, lat, lon });
    }
  }

  // Son ariza ALGILAYAN ve ondan sonraki ilk ALGILAMAYAN cihaz.
  let lastRedIdx = -1;
  let firstGreenAfterRedIdx = -1;
  for (let i = 0; i < lineDevices.length; i += 1) {
    if (lineDevices[i].isRed) lastRedIdx = i;
  }
  if (lastRedIdx >= 0) {
    for (let i = lastRedIdx + 1; i < lineDevices.length; i += 1) {
      if (!lineDevices[i].isRed) {
        firstGreenAfterRedIdx = i;
        break;
      }
    }
  }

  const fullLine: LatLon[] = linePoles.map((p) => [p.latitude, p.longitude]);

  let splitA: LatLon | null = null;
  let splitB: LatLon | null = null;
  if (lastRedIdx >= 0) {
    splitA = [lineDevices[lastRedIdx].lat, lineDevices[lastRedIdx].lon];
    if (firstGreenAfterRedIdx >= 0) {
      splitB = [lineDevices[firstGreenAfterRedIdx].lat, lineDevices[firstGreenAfterRedIdx].lon];
    } else {
      // Son kirmizidan sonra yesil cihaz YOK -> ariza hat ucuna kadar
      // suruyor olabilir. Daraltacak bir cihaz olmadigi icin son direk.
      const last = linePoles[linePoles.length - 1];
      splitB = [last.latitude, last.longitude];
    }
  }

  let preGreen: LatLon[] = fullLine; // varsayilan: tamami saglikli
  let faultRed: LatLon[] = [];
  let postGreen: LatLon[] = [];
  if (splitA && splitB) {
    const a = bolPolyline(fullLine, splitA);
    preGreen = a.pre;
    const b = bolPolyline(a.post, splitB);
    faultRed = b.pre;
    postGreen = b.post;
  } else if (splitA) {
    const a = bolPolyline(fullLine, splitA);
    preGreen = a.pre;
    faultRed = a.post;
  }

  // --- Ariza araligindaki direkler ---------------------------------------
  const fromSeq = fault.from_pole_seq ?? null;
  const toSeq = fault.to_pole_seq ?? null;
  const lo = fromSeq != null && toSeq != null ? Math.min(fromSeq, toSeq) : null;
  const hi = fromSeq != null && toSeq != null ? Math.max(fromSeq, toSeq) : null;
  const focusPoles =
    lo !== null && hi !== null
      ? linePoles.filter((p) => p.sequence_no >= lo && p.sequence_no <= hi)
      : linePoles;
  // Aralik disari dusmus olabilir (topoloji duzenlendi): bos kalirsa
  // merkez NaN olur ve harita hicbir yere gitmez.
  const merkezPoles = focusPoles.length > 0 ? focusPoles : linePoles;
  const lats = merkezPoles.map((p) => p.latitude);
  const lons = merkezPoles.map((p) => p.longitude);
  const center: LatLon = [
    lats.reduce((a, b) => a + b, 0) / lats.length,
    lons.reduce((a, b) => a + b, 0) / lons.length
  ];
  const span = Math.max(
    Math.max(...lats) - Math.min(...lats),
    Math.max(...lons) - Math.min(...lons)
  );

  const deviceMarkers = lineDevices.map((d) => {
    const dev = devices.find((dx) => dx.id === d.deviceId);
    return {
      deviceId: d.deviceId,
      lat: d.lat,
      lon: d.lon,
      isRed: d.isRed,
      name: dev?.name ?? `${input.deviceFallback} #${d.deviceId}`,
      code: dev?.code
    };
  });

  const rangePoles = merkezPoles.map((p) => ({
    id: p.id,
    sequence_no: p.sequence_no,
    name: p.name ?? `${input.poleFallback} #${p.sequence_no}`,
    latitude: p.latitude,
    longitude: p.longitude,
    isStart: p.id === fault.from_pole_id,
    isEnd: p.id === fault.to_pole_id
  }));

  // --- Odak kutulari -------------------------------------------------------
  // Tek bir gorunum yetmiyordu: ariza araligina zoom yapinca operator "bu
  // hattin neresi?" diye soruyor, tum hatta bakinca ariza noktasi igne ucu
  // kadar kaliyor. Ucu de ayni haritada, secilebilir.
  const otherLines: { lineId: number; name: string; path: LatLon[] }[] = [];
  for (const ln of lines) {
    if (ln.id === fault.line_id) continue;
    const pts = poles
      .filter((p) => p.line_id === ln.id)
      .sort((a, b) => a.sequence_no - b.sequence_no)
      .map((p) => [p.latitude, p.longitude] as LatLon);
    if (pts.length >= 2) otherLines.push({ lineId: ln.id, name: ln.name, path: pts });
  }

  return {
    // Sifir uzunluklu parcalar elenir: hattin ucunde sahte bir yesil nokta
    // birakiyorlardi (bkz. `anlamli`).
    preGreen: anlamli(preGreen),
    faultRed: anlamli(faultRed),
    postGreen: anlamli(postGreen),
    center,
    zoom: zoomFor(span),
    zoneBounds: anlamli(faultRed).length >= 2 ? anlamli(faultRed) : fullLine,
    lineBounds: fullLine,
    gridBounds: [...fullLine, ...otherLines.flatMap((l) => l.path)],
    otherLines,
    polesWithRole: linePoles.map((p) => ({
      p,
      isFromFault: p.id === fault.from_pole_id,
      isToFault: p.id === fault.to_pole_id,
      isInFaultRange:
        lo !== null && hi !== null && p.sequence_no >= lo && p.sequence_no <= hi
    })),
    rangePoles,
    deviceMarkers
  };
}
