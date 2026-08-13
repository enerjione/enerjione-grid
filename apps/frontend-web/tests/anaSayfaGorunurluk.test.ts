/**
 * "Hattan kaldirdim ama ana sayfada duruyor" regresyonu.
 *
 * Hat Yonetimi'nde "kaldir" islemi `line_segments` satirini siler. Cihaz
 * kaydi ve kurulumda girilmis KOORDINATI yerinde kalir; ana sayfa haritasi
 * konumu once segmentten turetip yoksa ham koordinata dustugu icin pin eski
 * yerinin yakininda durmaya devam ediyordu — ustelik bagli cihazlarla
 * birebir ayni cizilerek. Kullanicinin gozunde hat atamasi KALDIRILAMIYORDU.
 *
 * Kural bir kez ters cevrilip geri alindi (once gizleniyordu, sonra "hepsi
 * gozuksun" yapildi). Ucuncu kez donmemesi icin burada kilitli.
 */
import assert from "node:assert/strict";
import { test } from "node:test";

import {
  anaSayfadaGorunur,
  type AnaSayfaGorunurluk,
} from "../src/features/dashboard/dashboardVisibility";

const temel: AnaSayfaGorunurluk = {
  kit: false,
  topolojiYuklendi: true,
  hattaAtanmis: true,
  atanmamisIsteniyor: false,
};

test("hatta bagli cihaz gorunur", () => {
  assert.equal(anaSayfadaGorunur(temel), true);
});

test("hattan KALDIRILAN cihaz ana sayfadan da duser", () => {
  // Asil regresyon: kaldirma isleminin ana sayfada karsiligi olmali.
  assert.equal(anaSayfadaGorunur({ ...temel, hattaAtanmis: false }), false);
});

test("hatta EKLENEN cihaz geri gelir", () => {
  // Ayni kural iki yonlu calismali; kullanicinin bekledigi davranis bu.
  assert.equal(anaSayfadaGorunur({ ...temel, hattaAtanmis: true }), true);
});

test("filtrede 'Atanmamis' secildiyse eleme YAPILMAZ", () => {
  // Cihaz kaybolmuyor, sadece varsayilan gorunumde sebekeyle karismiyor.
  assert.equal(
    anaSayfadaGorunur({ ...temel, hattaAtanmis: false, atanmamisIsteniyor: true }),
    true,
  );
});

test("topoloji YUKLENMEDIYSE hicbir sey gizlenmez", () => {
  // Bilmedigimiz icin saklamak bos bir harita gostermek olurdu; bu urunde
  // "sistem bilmedigini yokmus gibi gosterdi" en agir hata sinifi.
  assert.equal(
    anaSayfadaGorunur({ ...temel, topolojiYuklendi: false, hattaAtanmis: false }),
    true,
  );
});

test("fiziksel kit kaydi HER DURUMDA gizli", () => {
  // Kit hicbir segmente baglanmaz; haritada kurulum koordinatinda, yani
  // yanlis yerde cikardi. Sahada izlenen sey onun SETLERI.
  for (const topolojiYuklendi of [true, false]) {
    for (const atanmamisIsteniyor of [true, false]) {
      assert.equal(
        anaSayfadaGorunur({
          kit: true,
          topolojiYuklendi,
          hattaAtanmis: true,
          atanmamisIsteniyor,
        }),
        false,
        `kit gorunur kalmis (topolojiYuklendi=${topolojiYuklendi}, atanmamisIsteniyor=${atanmamisIsteniyor})`,
      );
    }
  }
});
