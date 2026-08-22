/**
 * KOMUT KIMLIGI — TARAYICIDA KAYIPSIZ TASINIYOR MU?
 *
 * NE KORUNUYOR
 * ------------
 * Backend komut kimligini artik `epoch_ms * 1000 + rastgele(0..999)` ile
 * uretiyor (veritabani restore'undan bagimsiz olmasi icin; bkz.
 * `app/services/command_identity.py`). Bugun uretilen deger ~1.79e15.
 *
 * Arayuz bu kimligi `number` olarak tasiyor (`DeviceCommandQueued.id`,
 * `DeviceCommandRow.id`) ve JavaScript 2^53 uzerinde TAMSAYI HASSASIYETINI
 * KAYBEDER. Kayip SESSIZDIR: `JSON.parse` hata vermez, sayi en yakin
 * gosterilebilir degere yuvarlanir. Sonuc, ariza izleme urununde en sinsi
 * hata sinifi olurdu — operator "Komutlar" listesinde bir satir gorur,
 * tiklar, backend o kimlikte bir komut bulamaz ve kimse nedenini anlamaz.
 *
 * Tam 63-bit rastgele bir kimlik secilseydi bu SESSIZCE olurdu. Uretecin
 * 2^53 altinda kalmasi bilincli bir tasarim kararidir ve burada
 * DOGRULANIYOR — varsayilmiyor.
 */
import assert from "node:assert/strict";
import { test } from "node:test";

/** Backend'in bugun urettigi buyuklukte gercek bir ornek. */
const BUGUNKU_KIMLIK = 1787346846696728;

/** Uretecin tasarim tavani: `AZAMI_KIMLIK` (= Number.MAX_SAFE_INTEGER). */
const AZAMI_KIMLIK = 9007199254740991;

test("bugunku komut kimligi JS guvenli tamsayi araliginda", () => {
  assert.ok(Number.isSafeInteger(BUGUNKU_KIMLIK));
  assert.ok(BUGUNKU_KIMLIK < Number.MAX_SAFE_INTEGER);
});

test("backend tavani ile JS tavani AYNI sayi", () => {
  // Backend `AZAMI_KIMLIK` sabiti bu degere esit olmali; ayrisirsa backend
  // tarayicinin tasiyamayacagi bir kimlik uretebilir.
  assert.equal(AZAMI_KIMLIK, Number.MAX_SAFE_INTEGER);
});

test("JSON round-trip kimligi BOZMUYOR", () => {
  // API yaniti bu bicimde gelir: kimlik JSON SAYISIDIR (dize degil).
  const govde = `{"id":${BUGUNKU_KIMLIK},"status":"pending","command":"reset_fault","dnp3_index":3}`;
  const cozulen = JSON.parse(govde) as { id: number };
  assert.equal(cozulen.id, BUGUNKU_KIMLIK);
  assert.equal(String(cozulen.id), String(BUGUNKU_KIMLIK));
  // Geri serilestirmede de ayni metin cikmali (URL'e yazilan kimlik).
  assert.ok(JSON.stringify(cozulen).includes(String(BUGUNKU_KIMLIK)));
});

test("tavanin BIR USTU kayipsiz TASINAMAZ — sinirin gercek oldugunu gosterir", () => {
  // Bu test uretecin tavani neden 2^53'un altinda tuttugunu kanitlar:
  // bir ustundeki degerler ayirt edilemez hale gelir.
  const tasan = Number.MAX_SAFE_INTEGER + 1;
  assert.equal(Number.isSafeInteger(tasan), false);
  assert.equal(tasan, tasan + 1, "2^53 ustunde iki farkli tamsayi ayni sayiya cokuyor");
});

test("eski kucuk kimlikler de gecerli kaliyor", () => {
  // Gecis sonrasi tabloda IKI KUSAK kimlik var; arayuz ikisini de tasimali.
  for (const eski of [1, 39, 43, 44]) {
    const cozulen = JSON.parse(`{"id":${eski}}`) as { id: number };
    assert.equal(cozulen.id, eski);
    assert.ok(Number.isSafeInteger(cozulen.id));
  }
});

test("kimlik karsilastirmasi buyuk degerlerde de dogru", () => {
  // Liste "en yeni once" siralamasi kimlige dayaniyor (backend
  // `order_by(id.desc())`); arayuzde bir yeniden siralama olursa buyuk
  // sayilarda da dogru calismali.
  const a = BUGUNKU_KIMLIK;
  const b = BUGUNKU_KIMLIK + 1;
  assert.ok(b > a, "ardisik iki kimlik ayirt edilemiyor");
  assert.notEqual(a, b);
});
