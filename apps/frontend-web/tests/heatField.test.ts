/**
 * Ariza sicaklik haritasinin NORMALIZASYONU.
 *
 * Bu ekran bakim butcesini yonlendirecek. Bir isi haritasinda yalan iki
 * yonde soylenir ve ikisi de pahalidir:
 *
 *   * "her yer kirmizi"  -> her yere ekip gonder (para yanar)
 *   * "hicbir yer sicak degil" -> tekrar eden ariza gozden kacar (musteri yanar)
 *
 * Renk secimi degil, OLCEKLEME kararlari test edilir; hata buradan cikar.
 */
import assert from "node:assert/strict";
import { test } from "node:test";

import {
  HEAT_FLAT,
  HEAT_FLOOR,
  heatColor,
  heatIntensities,
  heatRadius,
  sebekeCizgileri
} from "../src/features/fault-analytics/heatField";

const N = (lat: number, lon: number, w: number) => ({
  latitude: lat,
  longitude: lon,
  weight: w
});

test("dizi girdiyle AYNI uzunlukta ve AYNI sirada doner", () => {
  // Hizalama sozlesmesi: cagiran taraf ayni indeksten tooltip verisini
  // okuyor. Filtreleme yapilsa isaretciler yanlis adedi gosterirdi.
  const p = [N(39, 35, 3), N(39.1, 35.1, 0), N(39.2, 35.2, 7)];
  const out = heatIntensities(p);
  assert.equal(out.length, 3);
  assert.equal(out[1], null, "agirliksiz nokta cizilmemeli");
  assert.ok(out[0] !== null && out[2] !== null);
});

test("en dusuk agirlik bile GORUNUR kalir", () => {
  // Sifira yakin alfa ile cizilen nokta hic cizilmemis gibi durur;
  // "burada 1 ariza oldu" bilgisi kaybolurdu.
  const out = heatIntensities([N(39, 35, 1), N(40, 36, 40)]);
  assert.equal(out[0], HEAT_FLOOR);
  assert.ok(out[0]! >= 0.2, `taban cok dusuk: ${out[0]}`);
});

test("en yuksek agirlik olcegin tepesine oturur", () => {
  const out = heatIntensities([N(39, 35, 1), N(40, 36, 40)]);
  assert.equal(out[1], 1);
});

test("TEK nokta 'en sicak' gosterilmez", () => {
  // Tek noktali veride min=max olur; oran hesabi 0/0'dir. Onu 1.0'a
  // yuvarlamak "burasi felaket" demek olurdu — oysa kiyaslanacak hicbir sey
  // yok. Notr banda oturur.
  const out = heatIntensities([N(39, 35, 5)]);
  assert.equal(out[0], HEAT_FLAT);
  assert.ok(out[0]! < 1, "tek nokta maksimum sicaklikta yanmamali");
});

test("TUM agirliklar esitse harita bastan sona yanmaz", () => {
  const out = heatIntensities([N(39, 35, 2), N(40, 36, 2), N(41, 37, 2)]);
  assert.deepEqual(out, [HEAT_FLAT, HEAT_FLAT, HEAT_FLAT]);
});

test("olcek KAREKOKLU — tek uc deger haritayi ezmez", () => {
  // 1 / 4 / 100 ariza. Lineerde 4 ariza (0.03) neredeyse gorunmez olurdu;
  // burada belirgin bir orta band alir.
  const out = heatIntensities([N(39, 35, 1), N(40, 36, 4), N(41, 37, 100)]);
  const orta = out[1]!;
  assert.ok(orta > 0.35, `orta deger lineer gibi eziliyor: ${orta}`);
  assert.ok(orta < out[2]!, "orta deger tepeye esitlenmis");

  const lineer = (4 - 1) / (100 - 1); // 0.030
  assert.ok(orta > lineer + 0.2, "karekok olcegi uygulanmamis");
});

test("olcek MONOTON — cok arizali nokta daha soguk cikamaz", () => {
  const out = heatIntensities([N(39, 35, 1), N(40, 36, 9), N(41, 37, 25), N(42, 38, 50)]);
  for (let i = 1; i < out.length; i += 1) {
    assert.ok(out[i]! > out[i - 1]!, `${i}. nokta oncekinden soguk`);
  }
});

test("BOZUK koordinat cizime sizmaz", () => {
  // Tek bir NaN, canvas'ta tum gecisi bozar (ve fitBounds'u gecersiz kilar).
  const out = heatIntensities([
    N(Number.NaN, 35, 5),
    N(39, Number.NaN, 5),
    N(39, 35, Number.NaN),
    N(39, 35, 5)
  ]);
  assert.deepEqual(out.slice(0, 3), [null, null, null]);
  assert.ok(out[3] !== null);
});

test("negatif agirlik cizilmez", () => {
  assert.deepEqual(heatIntensities([N(39, 35, -3)]), [null]);
});

test("hic gecerli nokta yoksa hepsi null — patlamaz", () => {
  assert.deepEqual(heatIntensities([N(39, 35, 0), N(Number.NaN, 1, 2)]), [null, null]);
});

test("renk olcegi 0..1 disinda da kirilmaz", () => {
  for (const x of [-1, 0, 0.5, 1, 2, Number.NaN]) {
    const c = heatColor(x);
    assert.match(c, /^rgba\(\d+, \d+, \d+, [\d.]+\)$/, `bozuk renk: ${x} -> ${c}`);
  }
});

test("renk olcegi sicaklikla KIRMIZIYA gider", () => {
  const soguk = heatColor(HEAT_FLOOR);
  const sicak = heatColor(1);
  const kirmizi = (c: string) => Number(c.match(/rgba\((\d+)/)![1]);
  const mavi = (c: string) => Number(c.match(/rgba\(\d+, \d+, (\d+)/)![1]);
  assert.ok(kirmizi(sicak) > kirmizi(soguk), "sicak uc daha kirmizi degil");
  assert.ok(mavi(soguk) > mavi(sicak), "soguk uc daha mavi degil");
});

test("renk olceginde YESIL yok", () => {
  // Bu projede yesil "sorun yok" demek (cihaz durumu, alarm seviyeleri).
  // Isi haritasinda orta bandi yesil yapmak "orta = normal" diye okunur.
  for (let i = 0; i <= 10; i += 1) {
    const [r, g, b] = heatColor(i / 10)
      .match(/rgba\((\d+), (\d+), (\d+)/)!
      .slice(1)
      .map(Number);
    const yesilBaskin = g > r + 30 && g > b + 30;
    assert.ok(!yesilBaskin, `${i / 10} yogunlukta yesil baskin: ${r},${g},${b}`);
  }
});

test("yaricap zoom ile BUYUR — uzaklasinca direkler tek lekeye kaynamasin", () => {
  assert.ok(heatRadius(15) > heatRadius(8), "yakinlasinca yaricap buyumuyor");
  assert.ok(heatRadius(6) >= 12, "uzakta nokta gorunmeyecek kadar kucuk");
  assert.ok(heatRadius(19) <= 60, "yakinda leke tum ekrani kapliyor");
});

test("bozuk zoom degeri yaricapi kirmaz", () => {
  const r = heatRadius(Number.NaN);
  assert.ok(Number.isFinite(r) && r > 0, `gecersiz yaricap: ${r}`);
});

// ---------------------------------------------------------------------------
// Sebeke cizgileri — isi lekesinin ZEMINI
// ---------------------------------------------------------------------------
// Bos bir zemin uzerindeki sicak nokta operatore koordinat kadar sey anlatir;
// hat cizilince "su fiderin ortasinda" olur. Sahaya ekip gonderme karari bu
// ikinci cumleyle verilir.

/** Direk: artik `line_id` + `sequence_no` tasiyor — cizgiler
 *  `line_segments`ten degil DIREKLERDEN kuruluyor (bkz. heatField.ts). */
const D = (
  id: number,
  lat: number,
  lon: number,
  line_id = 1,
  sequence_no = id
) => ({ id, latitude: lat, longitude: lon, line_id, sequence_no });

test("hat govdesi ardisik direklerden kurulur", () => {
  // ONCEKI HATA: cizgiler `line_segments` kayitlarindan uretiliyordu.
  // `LineSegment` bir CIHAZ YERLESIMI kaydidir; cihaz atanmamis aciklik
  // icin satir hic olusmaz ve saha haritasi BOS cikiyordu. Tek bir
  // segment kaydi olmadan da hat cizilebilmeli.
  const parcalar = sebekeCizgileri([
    D(1, 39.0, 35.0),
    D(2, 39.1, 35.1),
    D(3, 39.2, 35.2)
  ]);
  assert.equal(parcalar.length, 2, "iki aciklik cizilmeliydi");
  parcalar.forEach((p) => assert.equal(p.length, 2, "parca iki noktali olmali"));
});

test("direkler SEQUENCE_NO sirasina gore baglanir", () => {
  // Dizideki sira degil hattaki sira onemli; API sirayi garanti etmiyor.
  // Yanlis sirada baglamak teli ileri geri zikzak cizdirirdi.
  const parcalar = sebekeCizgileri([
    D(3, 39.2, 35.2, 1, 3),
    D(1, 39.0, 35.0, 1, 1),
    D(2, 39.1, 35.1, 1, 2)
  ]);
  assert.deepEqual(parcalar[0], [[39.0, 35.0], [39.1, 35.1]]);
  assert.deepEqual(parcalar[1], [[39.1, 35.1], [39.2, 35.2]]);
});

test("AYRI hatlar birbirine baglanmaz", () => {
  // Iki hattin direklerini ucuca eklemek, olmayan bir teli cizerdi.
  const parcalar = sebekeCizgileri([
    D(1, 39.0, 35.0, 1, 1),
    D(2, 39.1, 35.1, 1, 2),
    D(3, 41.0, 29.0, 2, 1),
    D(4, 41.1, 29.1, 2, 2)
  ]);
  assert.equal(parcalar.length, 2, "her hat kendi icinde tek aciklik");
});

test("BRANSMAN ana direge baglanir", () => {
  // Bag cizilmezse kol havada asili durur ve "kopuk hat" gibi okunur.
  const parcalar = sebekeCizgileri(
    [
      D(1, 39.0, 35.0, 1, 1),
      D(2, 39.1, 35.1, 1, 2),
      D(9, 39.15, 35.3, 2, 1)
    ],
    [{ id: 2, branched_from_pole_id: 2 }]
  );
  // 1 govde aciklik (hat 1) + 1 bransman bagi.
  assert.equal(parcalar.length, 2);
  assert.deepEqual(parcalar[1], [[39.1, 35.1], [39.15, 35.3]]);
});

test("bransmanin ana diregi YOKSA bag atlanir", () => {
  const parcalar = sebekeCizgileri(
    [D(9, 39.15, 35.3, 2, 1)],
    [{ id: 2, branched_from_pole_id: 404 }]
  );
  assert.deepEqual(parcalar, []);
});

test("kordinatlar [lat, lon] sirasinda", () => {
  // Ters cevrilirse Leaflet cizgiyi denizin ortasina koyar ve kimse
  // koordinati okuyup fark etmez.
  const [parca] = sebekeCizgileri([D(1, 39, 35), D(2, 40, 36)]);
  assert.deepEqual(parca, [
    [39, 35],
    [40, 36]
  ]);
});

test("BOZUK koordinat cizgiyi sizdirmaz", () => {
  // Tek bir NaN, Leaflet'in TUM katmanini sessizce cizilmez yapar —
  // sadece o parca degil, hepsi kaybolur.
  const parcalar = sebekeCizgileri([
    D(1, Number.NaN, 35),
    D(2, 39.1, 35.1),
    D(3, 39.2, 35.2)
  ]);
  assert.equal(parcalar.length, 1, "bozuk parca disarida kalmali");
  parcalar.flat().forEach(([lat, lon]) => {
    assert.ok(Number.isFinite(lat) && Number.isFinite(lon));
  });
});

test("tek direk ya da bos veri — patlamaz", () => {
  assert.deepEqual(sebekeCizgileri([]), []);
  assert.deepEqual(sebekeCizgileri([D(1, 39, 35)]), []);
});
