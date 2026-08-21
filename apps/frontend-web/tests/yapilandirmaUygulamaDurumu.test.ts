/**
 * YAPILANDIRMA UYGULAMA DURUMU — arayuz kanitsiz basari IDDIA ETMEZ.
 *
 * YASANAN HATA
 * ------------
 * Kart tek bir sey soyluyordu: "Cihaza gonderildi: {tarih}". O metin
 * `version.appliedAt`e bakiyordu ve o alan komut kuyruga girer girmez
 * doluyordu. Uyuyan bir Horstmann'da:
 *
 *   dosya bir FTP sunucusuna kondu, komut 120 saniye sonra oldu,
 *   cihaz hala ESKI yapilandirmayla calisiyordu — ekran "gonderildi" dedi.
 *
 * Bu dosya, o yalanin geri gelmesini engelleyen kurallari kilitler.
 */
import assert from "node:assert/strict";
import { test } from "node:test";
import { readFileSync } from "node:fs";
import { join } from "node:path";

import TR from "../src/shared/i18n/resources/tr.json";
import EN from "../src/shared/i18n/resources/en.json";
import type { ConfigApplication } from "../src/shared/types";
import {
  DURUM,
  applyGorunum,
  applyToneClass
} from "../src/features/device-detail/configApplyState";

const oku = (...yol: string[]) => readFileSync(join(process.cwd(), ...yol), "utf8");
const KART = oku("src", "features", "device-detail", "DeviceFtpConfigCard.tsx");
const CSS = oku("src", "styles.css");

function app(over: Partial<ConfigApplication> = {}): ConfigApplication {
  return {
    state: DURUM.BEKLIYOR,
    version: 3,
    requestedAt: "2026-08-21T10:00:00Z",
    requestedBy: "muh",
    reason: null,
    queuedAt: null,
    deliveredAt: null,
    verifiedAt: null,
    verifiedBy: null,
    failureReason: null,
    attempt: 0,
    ...over
  };
}

// ---------------------------------------------------------------------------
// 1) KANITSIZ BASARI YOK
// ---------------------------------------------------------------------------

test("YALNIZCA 'dogrulandi' kanitli sayilir", () => {
  const kanitli = [
    DURUM.BEKLIYOR,
    DURUM.KUYRUKTA,
    DURUM.ILETILDI,
    DURUM.BASARISIZ,
    DURUM.GECERSIZ
  ].filter((s) => applyGorunum(app({ state: s }))?.kanitli);
  assert.deepEqual(kanitli, [], `kanitsiz durumlar basari sayiliyor: ${kanitli}`);
  assert.equal(applyGorunum(app({ state: DURUM.DOGRULANDI }))?.kanitli, true);
});

test("ILETILDI basari DEGIL ve bunu ACIKCA soyler", () => {
  // Gateway yalnizca komutu cihaza ILETTIGINI bilir; cihazin dosyayi
  // yukledigini DEGIL. Bu ayrimi kaybetmek, eski yalanin geri donmesi olurdu.
  const g = applyGorunum(app({ state: DURUM.ILETILDI }));
  assert.equal(g?.kanitli, false);
  assert.equal(g?.tone, "ilerliyor");
  assert.match(g?.hintKey ?? "", /deliveredHint$/, "iletildi icin uyari metni yok");
});

test("TANIMADIGIMIZ durum basari SAYILMAZ", () => {
  // Backend ileride yeni bir durum eklerse arayuz notr gostermeli;
  // "dogrulandi" varsaymak en tehlikeli yanlis olurdu.
  const g = applyGorunum(app({ state: "gelecekteki_durum" }));
  assert.equal(g?.kanitli, false);
  assert.equal(g?.tone, "notr");
  assert.match(g?.labelKey ?? "", /unknown$/);
});

test("uygulama YOKSA satir HIC cizilmez", () => {
  // Bos bir "durum yok" satiri gurultudur.
  assert.equal(applyGorunum(null), null);
});

// ---------------------------------------------------------------------------
// 2) DOGRULAMA KANIT SINIFI GORUNUR
// ---------------------------------------------------------------------------

test("KESIN ve ZAYIF kanit AYRI metinlerle anlatilir", () => {
  // "dogrulandi" yazip nedenini gizlemek, sonradan guvenilirligi
  // tartisilamaz hale getirirdi.
  const kesin = applyGorunum(app({ state: DURUM.DOGRULANDI, verifiedBy: "cihaz_dosyasi" }));
  const zayif = applyGorunum(app({ state: DURUM.DOGRULANDI, verifiedBy: "damga_degisti" }));
  assert.match(kesin?.hintKey ?? "", /verifiedStrong$/);
  assert.match(zayif?.hintKey ?? "", /verifiedWeak$/);
  assert.notEqual(kesin?.hintKey, zayif?.hintKey);
});

test("ZAYIF kanit metni 'hangi surum' belirsizligini SOYLER", () => {
  const tr = (TR as any).deviceDetail.config.ftp.apply.verifiedWeak as string;
  assert.match(tr, /hangi sürüm/i, `zayif kanit metni belirsizligi gizliyor: ${tr}`);
});

// ---------------------------------------------------------------------------
// 3) BEKLEME BIR ARIZA DEGILDIR
// ---------------------------------------------------------------------------

test("uyuyan cihaz HATA tonunda gosterilmez", () => {
  // Smart modda modem BILEREK kapali; kirmizi gostermek saglikli bir filoyu
  // arizali gibi okuturdu.
  const g = applyGorunum(app({ state: DURUM.BEKLIYOR, reason: "uykuda" }));
  assert.equal(g?.tone, "bekleme");
  assert.notEqual(g?.tone, "hata");
});

test("her hazirlik gerekcesinin BIR ACIKLAMASI var", () => {
  const gerekceler = [
    "uykuda",
    "erisilemez",
    "bayat_gozlem",
    "temas_yok",
    "yeni_kanit_bekleniyor",
    "eski_kanit_cevrimdisi"
  ];
  for (const r of gerekceler) {
    const g = applyGorunum(app({ state: DURUM.BEKLIYOR, reason: r }));
    assert.ok(g?.hintKey, `${r} icin aciklama anahtari yok`);
    for (const [ad, sozluk] of [
      ["tr", (TR as any).deviceDetail.config.ftp.apply],
      ["en", (EN as any).deviceDetail.config.ftp.apply]
    ] as const) {
      const anahtar = g!.hintKey!.split(".").pop()!;
      assert.ok(
        typeof sozluk[anahtar] === "string" && sozluk[anahtar].length > 10,
        `${ad}: ${anahtar} metni yok`
      );
    }
  }
});

test("bilinmeyen gerekce UYDURULMAZ", () => {
  const g = applyGorunum(app({ state: DURUM.BEKLIYOR, reason: "hic_bilinmeyen" }));
  assert.equal(g?.hintKey, null, "tanimadigi gerekceye aciklama uydurmus");
});

// ---------------------------------------------------------------------------
// 4) SOZLESME PARITESI — backend sabitleriyle
// ---------------------------------------------------------------------------

test("durum sabitleri BACKEND ile birebir ayni", () => {
  // Ayrisirsa arayuz gecerli bir durumu "bilinmiyor" gosterir ve operator
  // surecin neresinde oldugunu goremez.
  const py = readFileSync(
    join(process.cwd(), "..", "..", "apps", "backend-api", "app", "models",
         "device_config_application.py"),
    "utf8"
  );
  const m = /DURUMLAR = \(([^)]+)\)/.exec(py);
  assert.ok(m, "backend durum listesi okunamadi");
  const adlar = [...py.matchAll(/^([A-Z_]+) = "([a-z_]+)"$/gm)].reduce<Record<string, string>>(
    (acc, [, ad, deger]) => ({ ...acc, [ad]: deger }),
    {}
  );
  assert.equal(DURUM.BEKLIYOR, adlar.BEKLIYOR);
  assert.equal(DURUM.KUYRUKTA, adlar.KUYRUKTA);
  assert.equal(DURUM.ILETILDI, adlar.ILETILDI);
  assert.equal(DURUM.DOGRULANDI, adlar.DOGRULANDI);
  assert.equal(DURUM.BASARISIZ, adlar.BASARISIZ);
  assert.equal(DURUM.GECERSIZ, adlar.GECERSIZ);
});

test("gerekce sabitleri BACKEND ile ayni", () => {
  const py = readFileSync(
    join(process.cwd(), "..", "..", "apps", "backend-api", "app", "services",
         "device_session_readiness.py"),
    "utf8"
  );
  for (const r of ["uykuda", "erisilemez", "bayat_gozlem", "temas_yok",
                   "yeni_kanit_bekleniyor", "eski_kanit_cevrimdisi"]) {
    assert.ok(py.includes(`"${r}"`), `backend'de ${r} gerekcesi yok`);
  }
});

// ---------------------------------------------------------------------------
// 5) KART
// ---------------------------------------------------------------------------

test("kart uygulama durumunu ciziyor", () => {
  assert.match(KART, /applyGorunum\(current\.application\)/);
  assert.match(KART, /applyToneClass\(uygulama\.tone\)/);
});

test("'Cihaza gonderildi' metni TEK BASINA kalmadi", () => {
  // Eski satir hala var (appliedAt artik gercek kanit demek) ama yanindaki
  // uygulama rozeti olmadan cizilmiyor olmali.
  const i = KART.indexOf("dev-ftp-meta");
  const blok = KART.slice(i, i + 2200);
  assert.match(blok, /uygulama \?/, "uygulama rozeti kartta yok");
  assert.match(blok, /appliedAt/, "kanitli damga satiri kaybolmus");
});

test("ILERLIYOR ve BASARILI ayni renk DEGIL", () => {
  // Ikisini ayni gostermek "kanitsiz basari" izlenimini geri getirirdi.
  const oku1 = (sinif: string) => {
    const i = CSS.indexOf(`.${sinif} {`);
    assert.ok(i > 0, `${sinif} stili yok`);
    return CSS.slice(i, CSS.indexOf("}", i));
  };
  const ilerliyor = oku1(applyToneClass("ilerliyor").trim());
  const basarili = oku1(applyToneClass("basarili").trim());
  const renk = (b: string) => /background:\s*(#[0-9a-f]{3,6})/i.exec(b)?.[1]?.toLowerCase();
  assert.ok(renk(ilerliyor) && renk(basarili));
  assert.notEqual(renk(ilerliyor), renk(basarili));
});

test("BEKLEME tonu HATA renginde degil", () => {
  const i = CSS.indexOf(`.${applyToneClass("bekleme")} {`);
  const blok = CSS.slice(i, CSS.indexOf("}", i)).toLowerCase();
  assert.ok(!/#fee2e2|#dc2626|#991b1b/.test(blok), "uyuyan cihaz kirmizi gosteriliyor");
});

test("her ton icin CSS sinifi var", () => {
  for (const t of ["notr", "bekleme", "ilerliyor", "basarili", "hata"] as const) {
    assert.ok(CSS.includes(`.${applyToneClass(t)} {`), `${t} icin stil yok`);
  }
});
