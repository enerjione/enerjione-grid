/**
 * GATEWAY UYUMLULUK KAPISI + DIAL-IN "ISTENEN vs DOGRULANAN" DURUMU
 *
 * NE KORUNUYOR
 * ------------
 * Iki ozellik de tek bir hata sinifini kapatmak icin var: arayuzun, DOGRU
 * OLDUGUNU BILMEDIGI bir seyi "sahada gecerli" gibi gostermesi. Bu sinif
 * sessizdir -- kayit basarili doner, ekran yesil kalir, cihaz eski ayarla
 * calismaya devam eder. Ariza ancak sahaya gidildiginde anlasilir.
 *
 * BU DOSYANIN ODAGI (kardes dosya `dialInGatewayUyumluluk.test.ts` ile
 * bilerek ayrisir; o dosya matrisin BACKEND AYNASI olmasini kilitler):
 *
 *   1. SURUM KARSILASTIRMASI SAYISAL OLMALI. En sinsi tuzak burada: metin
 *      karsilastirmasinda "1.9.0" > "1.14.0" cikar. Leksikografik bir
 *      karsilastirici 1.9.0'i "yeterli" sayar, uyari HIC cizilmez ve
 *      operator hicbir sey gormeden 1.14.0 gerektiren bir ayari kaydeder.
 *   2. UC DURUMLU KANIT (uygulandi / bekliyor / bilinmiyor) ekranda GERCEKTEN
 *      uce ayrilmali; ikisi ayni metne duserse "kaydettim" ile "cihazda
 *      gecerli" ayirt edilemez.
 *   3. KANIT YOKKEN hicbir metin gecerlilik iddia etmemeli; okunan deger
 *      yoksa tire basilmali (bos ya da 0 "ayar yok" gibi okunur).
 *   4. Ayar formu bir DURUM ekrani DEGILDIR: gateway'den gelen anlik oturum
 *      ifadeleri (Smart Bekleme / Gecikmis / recovering / next_expected)
 *      buraya sizmamali -- verileri backend'de yok, gosterilirse uydurma olur.
 *
 * NEDEN KAYNAK METNI DE OKUNUYOR
 * ------------------------------
 * Riskli mantigin karar veren kismi saf fonksiyonlarda ve onlar GERCEKTEN
 * calistiriliyor. Formun React tarafinda ise cerceve kurmadan yalnizca
 * sozlesme dogrulanabilir; kosucu React render etmiyor (bkz. tests/run.mjs).
 * Bu yuzden formdaki iki karar (durum etiketi ve ayrisma) burada AYNEN
 * yeniden kuruluyor, calistiriliyor VE kaynak metniyle karsilastiriliyor --
 * biri degisirse test duser, kopya sessizce ayrisamaz.
 */
import assert from "node:assert/strict";
import { test } from "node:test";
import { readFileSync } from "node:fs";
import { join } from "node:path";

import {
  V114_MIN,
  missingCapabilities,
  requiredVersion,
  supportsCapability
} from "../src/shared/gatewayCapabilities";
import type { DialInReadbackStatus, IpEndpointType, SessionPolicy } from "../src/shared/types";

const oku = (...p: string[]): string => readFileSync(join(process.cwd(), ...p), "utf8");

const FORM = oku("src", "features", "devices", "Dnp3SettingsForm.tsx");
const PANEL = oku("src", "features", "devices", "DeviceManagementPanel.tsx");
const TR = JSON.parse(oku("src", "shared", "i18n", "resources", "tr.json"));
const EN = JSON.parse(oku("src", "shared", "i18n", "resources", "en.json"));

type Sozluk = Record<string, string>;
const DILLER: ReadonlyArray<readonly [string, Sozluk]> = [
  ["tr", TR.engineering.dnp3 as Sozluk],
  ["en", EN.engineering.dnp3 as Sozluk]
];

/** Kaynaktan iki isaret arasindaki kesiti alir. Buyuk dosyalarda TUM metni
 *  taramak testi yanlis yere dusurur: panelin baska yerlerinde `last_seen`
 *  MESRU olarak vardir (cihaz canlilik satiri), bu isle eklenen kablolamada
 *  ise olmamali. */
function kesit(metin: string, bas: string, son: string): string {
  const i = metin.indexOf(bas);
  assert.ok(i > -1, `kesit baslangici bulunamadi: ${bas}`);
  const j = metin.indexOf(son, i + bas.length);
  assert.ok(j > i, `kesit sonu bulunamadi: ${son}`);
  return metin.slice(i, j);
}

/** Formdaki Dial-In durum blogu (istenen / dogrulanan / durum satirlari).
 *  Blok bir sonraki ayar alaninda (tolerans) biter. */
const dialInBloku = (): string =>
  kesit(FORM, "{dialInDurumu ? (", "engineering.dnp3.communicationGrace");

// ===========================================================================
// 1) YETENEK KAPISI -- hangi kombinasyon hangi surumde uyari uretir
// ===========================================================================

type KapiDurumu = {
  politika: SessionPolicy;
  uc: IpEndpointType;
  surum: string | null;
  beklenen: string[];
  neden: string;
};

const KAPI_TABLOSU: KapiDurumu[] = [
  // --- listening + smart: uc tipi kisiti v1.14.0'da kalkti ---
  {
    politika: "smart",
    uc: "listening",
    surum: "1.13",
    beklenen: ["smart_listening"],
    neden: "1.13 sabit IP'li cihazi Smart calistiramaz; uyari CIKMALI"
  },
  {
    politika: "smart",
    uc: "listening",
    surum: "1.13.9",
    beklenen: ["smart_listening"],
    neden: "1.14.0'in hemen altindaki her surum eksik"
  },
  {
    politika: "smart",
    uc: "listening",
    surum: "1.14.0",
    beklenen: [],
    neden: "TAM alt sinir -- sinirda uyari cikarsa guncel gateway bosuna suclanir"
  },
  {
    politika: "smart",
    uc: "listening",
    surum: "1.15.2",
    beklenen: [],
    neden: "alt sinirin ustu"
  },
  // --- listening + auto: IKI yetenek birden ---
  {
    politika: "auto",
    uc: "listening",
    surum: "1.13",
    beklenen: ["smart_auto", "smart_listening"],
    neden: "hem `auto` degeri hem listening+uyku kombinasyonu 1.14.0 ister"
  },
  // --- kapiya HIC girmeyen kombinasyonlar ---
  {
    politika: "smart",
    uc: "initiating",
    surum: "1.12",
    beklenen: [],
    neden:
      "1.12.0'dan beri calisiyor ve backend de kapiya sokmuyor; uyari cikarsa " +
      "SAHADA CALISAN kurulumlar yanlis yere suphelendirilir"
  },
  {
    politika: "continuous",
    uc: "initiating",
    surum: "1.11",
    beklenen: [],
    neden: "surekli mod hicbir yeni yetenek istemez"
  },
  {
    politika: "continuous",
    uc: "listening",
    surum: "1.11",
    beklenen: [],
    neden: "surekli mod uc tipinden de bagimsiz"
  },
  // --- surum BILINMIYOR: guvenli taraf = EKSIK say ---
  {
    politika: "smart",
    uc: "listening",
    surum: null,
    beklenen: ["smart_listening"],
    neden: "bildirmemis gateway'e ozelligi gondermek TUM config'i reddettirebilir"
  },
  {
    politika: "auto",
    uc: "listening",
    surum: null,
    beklenen: ["smart_auto", "smart_listening"],
    neden: "bilinmeyen surumde iki yetenek de iddia edilemez"
  },
  {
    politika: "smart",
    uc: "initiating",
    surum: null,
    beklenen: [],
    neden:
      "kapiya girmeyen kombinasyon bilinmeyen surumde de SESSIZ kalmali; aksi " +
      "halde surumunu bildirmeyen her gateway'de kalici bir uyari yanar"
  }
];

test("yetenek kapisi: politika x uc tipi x surum tablosu", () => {
  for (const d of KAPI_TABLOSU) {
    assert.deepEqual(
      missingCapabilities(d.politika, d.uc, d.surum),
      d.beklenen,
      `${d.uc}+${d.politika} @${d.surum ?? "yok"}: ${d.neden}`
    );
  }
});

test("uyari yalnizca eksik yetenek varken cizilir, surumu TURETIR", () => {
  // Formdaki kosul `uyumGerekliSurum !== null`. Eksik yetenek yokken null
  // donmezse ekranda sebepsiz bir uyari kalir; varken yanlis surum yazilirsa
  // operator gereksiz bir yukseltmeye yonlendirilir.
  for (const d of KAPI_TABLOSU) {
    const gerekli = requiredVersion(missingCapabilities(d.politika, d.uc, d.surum));
    if (d.beklenen.length === 0) {
      assert.equal(
        gerekli,
        null,
        `${d.uc}+${d.politika} @${d.surum ?? "yok"}: sebepsiz uyari cizilir`
      );
    } else {
      assert.equal(
        gerekli,
        V114_MIN,
        `${d.uc}+${d.politika} @${d.surum ?? "yok"}: uyaridaki surum yanlis`
      );
    }
  }
});

// ===========================================================================
// 2) SURUM KARSILASTIRMASI SAYISAL OLMALI -- leksikografik TUZAK
// ===========================================================================

test("1.9.0 surumu 1.14.0 ALTINDA sayilmali (leksikografik tuzak)", () => {
  // Once tuzagin gercek oldugunu gosteriyoruz ki iddia korlesmesin: metin
  // karsilastirmasinda "9" > "1" oldugu icin 1.9.0 DAHA YENI gorunur.
  const eski: string = "1.9.0";
  const gerekli: string = "1.14.0";
  assert.ok(
    eski > gerekli,
    "leksikografik tuzak artik gecerli degil -- bu testin varsayimi bozulmus"
  );
  assert.equal(gerekli, V114_MIN, "alt sinir degismis -- tuzak ornegi guncellensin");

  // ASIL IDDIA: kod SAYISAL karsilastirmali.
  assert.equal(
    supportsCapability("smart_listening", eski),
    false,
    "1.9.0 yeterli sayildi -- karsilastirma metin uzerinden yapiliyor"
  );
  assert.deepEqual(
    missingCapabilities("smart", "listening", eski),
    ["smart_listening"],
    "1.9.0'da uyari cizilmiyor -- eski gateway'e 1.14.0 ayari sessizce gider"
  );
});

test("1.2.0 surumu de 1.14.0 ALTINDA (ayni tuzagin ikinci ornegi)", () => {
  const eski: string = "1.2.0";
  assert.ok(eski > "1.14.0", "tuzak varsayimi bozulmus");
  assert.deepEqual(missingCapabilities("auto", "listening", eski), [
    "smart_auto",
    "smart_listening"
  ]);
});

test("1.100.0 surumu 1.14.0 USTUNDE -- tuzagin TERS yonu", () => {
  // Ters yon en az digeri kadar zararli: guncel bir gateway "eski" sayilirsa
  // operator olmayan bir sorun icin yukseltme yapmaya calisir ve kaydettigi
  // ayarin gecerliligenden supheye duser.
  const yeni: string = "1.100.0";
  assert.ok(yeni < "1.14.0", "ters yon tuzagi artik gecerli degil");
  assert.deepEqual(
    missingCapabilities("auto", "listening", yeni),
    [],
    "1.100.0 eksik sayildi -- iki basamakli minor metin olarak okunuyor"
  );
});

/** Sayisal olarak ARTAN merdiven; ikinci alan "yeterli mi". */
const SURUM_MERDIVENI: ReadonlyArray<readonly [string, boolean]> = [
  ["0.9.9", false],
  ["1.2.0", false], // leksikografik olarak "1.14.0"dan BUYUK gorunur
  ["1.9.0", false], // leksikografik olarak "1.14.0"dan BUYUK gorunur
  ["1.13.9", false],
  ["1.14", true], // eksik bilesen 0 sayilir: 1.14 == 1.14.0
  ["1.14.0", true],
  ["1.15.2", true],
  ["1.100.0", true], // leksikografik olarak "1.14.0"dan KUCUK gorunur
  ["2.0.0", true]
];

test("merdiven: yeterlilik tam BIR KEZ ve dogru basamakta doner", () => {
  const sonuclar = SURUM_MERDIVENI.map(
    ([surum]) => missingCapabilities("auto", "listening", surum).length === 0
  );

  for (const [i, [surum, beklenen]] of SURUM_MERDIVENI.entries()) {
    assert.equal(sonuclar[i], beklenen, `${surum}: yeterlilik karari yanlis`);
  }

  // Monotonluk: bir kez "yeterli" dedikten sonra daha yeni bir surumde geri
  // donulemez. Geri donus, karsilastirmanin sirali olmadiginin kanitidir.
  const donusler = sonuclar.filter((deger, i) => i > 0 && deger !== sonuclar[i - 1]).length;
  assert.equal(
    donusler,
    1,
    `yeterlilik ${donusler} kez yon degistirdi: ${SURUM_MERDIVENI.map(
      ([s], i) => `${s}=${sonuclar[i]}`
    ).join(" ")}`
  );
});

// ===========================================================================
// 3) FORMUN KAPISI -- undefined (sorulmadi) ile null (bildirmedi) AYRI
// ===========================================================================

/** Formdaki kapinin AYNISI (Dnp3SettingsForm.tsx); asagida kaynak metniyle
 *  karsilastiriliyor. */
const formunKapisi = (
  politika: SessionPolicy,
  uc: IpEndpointType,
  gatewayVersion: string | null | undefined
): string[] =>
  gatewayVersion === undefined ? [] : missingCapabilities(politika, uc, gatewayVersion);

test("surum HENUZ SORULMADIYSA uyari cizilmez; SORULUP bildirilmediyse cizilir", () => {
  // Bu iki durumu ayni saymak iki ayri yanlis uretir: undefined'i eksik
  // saymak panel acilir acilmaz her cihazda uyari yakar; null'i yeterli
  // saymak bildirmeyen gateway'i sessizce "guncel" ilan eder.
  assert.deepEqual(formunKapisi("auto", "listening", undefined), [], "sorulmadan uyari yanmis");
  assert.deepEqual(
    formunKapisi("auto", "listening", null),
    ["smart_auto", "smart_listening"],
    "bildirmeyen gateway yeterli sayilmis"
  );
  assert.equal(supportsCapability("smart_auto", null), null, "uc durumluluk kaybolmus");

  assert.match(
    FORM,
    /gatewayVersion === undefined\s*\?\s*\[\]\s*:\s*missingCapabilities\(\s*v\.session_policy,\s*v\.ip_endpoint_type,\s*gatewayVersion\s*\)/,
    "formun kapisi degismis -- buradaki kopya artik ayni seyi sinamiyor"
  );
});

test("uyari metninde bilinmeyen surum 'yok' yazar, ham deger DEGIL", () => {
  assert.match(
    FORM,
    /current:\s*gatewayVersion \?\? t\("engineering\.dnp3\.gatewayVersionUnknown"\)/,
    "bilinmeyen surum ekrana ham deger olarak duser"
  );
  for (const [ad, s] of DILLER) {
    assert.ok(
      typeof s.gatewayVersionUnknown === "string" && s.gatewayVersionUnknown.trim().length > 0,
      `${ad}: gatewayVersionUnknown bos`
    );
  }
});

// ===========================================================================
// 4) DIAL-IN UC DURUMU -- uygulandi / bekliyor / bilinmiyor
// ===========================================================================

const DURUMLAR: DialInReadbackStatus[] = ["eslesiyor", "farkli", "yok"];

/** Formdaki etiket secimi (dialInDurumEtiketi) AYNEN. */
const durumEtiketi = (durum: DialInReadbackStatus | undefined, s: Sozluk): string =>
  durum === "eslesiyor"
    ? s.dialInStatusMatched
    : durum === "farkli"
      ? s.dialInStatusDiffers
      : s.dialInStatusNone;

test("uc durum da iki dilde DOLU ve birbirinden AYRI", () => {
  for (const [ad, s] of DILLER) {
    const etiketler = DURUMLAR.map((d) => durumEtiketi(d, s));
    for (const [i, metin] of etiketler.entries()) {
      assert.equal(typeof metin, "string", `${ad}: ${DURUMLAR[i]} etiketi yok`);
      assert.ok(metin.trim().length > 0, `${ad}: ${DURUMLAR[i]} etiketi bos`);
      assert.ok(
        !metin.startsWith("engineering."),
        `${ad}: ${DURUMLAR[i]} ekrana ham anahtar dusuruyor`
      );
    }
    assert.equal(
      new Set(etiketler).size,
      3,
      `${ad}: uc durum ayni metne dusuyor -> ${etiketler.join(" | ")}`
    );
  }
});

test("TANIMADIGIMIZ durum 'Uygulandi'ya DUSMEZ (guvenli taraf)", () => {
  // Backend bu alani duz `str` doner; yarin yeni bir deger eklenirse arayuz
  // onu "sahada gecerli" saymamali.
  const bilinmeyenDurum = "yeni_bir_durum" as unknown as DialInReadbackStatus;
  for (const [ad, s] of DILLER) {
    assert.equal(
      durumEtiketi(bilinmeyenDurum, s),
      s.dialInStatusNone,
      `${ad}: bilinmeyen durum guvenli tarafa dusmuyor`
    );
    assert.equal(
      durumEtiketi(undefined, s),
      s.dialInStatusNone,
      `${ad}: durum hic yokken 'eslesiyor' gibi gosteriliyor`
    );
  }
});

test("formun etiket secimi ayni SIRAYLA yapilir (varsayilan = dogrulanmadi)", () => {
  const blok = /const dialInDurumEtiketi =([\s\S]*?);/.exec(FORM);
  assert.ok(blok, "etiket secimi formdan kalkmis -- yukaridaki kopya korlesti");
  assert.match(blok[1], /dialInDurumu\?\.status === "eslesiyor"/, "'eslesiyor' dali yok");
  assert.match(blok[1], /dialInDurumu\?\.status === "farkli"/, "'farkli' dali yok");
  assert.deepEqual(
    [...blok[1].matchAll(/dialInStatus(Matched|Differs|None)/g)].map((m) => m[1]),
    ["Matched", "Differs", "None"],
    "etiket sirasi degismis -- son (varsayilan) dal artik 'dogrulanmadi' degil"
  );
});

// ===========================================================================
// 5) ISTENEN != DOGRULANAN -- "aktif" DENMEZ
// ===========================================================================

/** Formdaki `dialInFarkli` karari AYNEN. */
const farkliMi = (
  istenen: number | null,
  durum: { readbackMin: number | null } | undefined
): boolean =>
  durum !== undefined &&
  istenen !== null &&
  durum.readbackMin !== null &&
  istenen !== durum.readbackMin;

test("ayrisma yalnizca IKI TARAF DA BILINIYORKEN 'farkli' der", () => {
  assert.equal(farkliMi(240, { readbackMin: 60 }), true, "gercek ayrisma yakalanmiyor");
  assert.equal(farkliMi(240, { readbackMin: 240 }), false, "esit degerler ayrisma sayiliyor");
  assert.equal(
    farkliMi(240, { readbackMin: null }),
    false,
    "kanit YOKLUGU ayrisma gibi gosteriliyor -- operator olmayan bir soruna yonlendirilir"
  );
  assert.equal(farkliMi(null, { readbackMin: 240 }), false, "istenen yokken ayrisma denemez");
  assert.equal(farkliMi(240, undefined), false, "kanit gelmemisken blok zaten cizilmez");

  const karar = /const dialInFarkli =([\s\S]*?);/.exec(FORM);
  assert.ok(karar, "ayrisma karari formdan kalkmis -- yukaridaki kopya korlesti");
  for (const parca of [
    /dialInDurumu !== undefined/,
    /dialIn !== null/,
    /dialInDurumu\.readbackMin !== null/,
    /dialIn !== dialInDurumu\.readbackMin/
  ]) {
    assert.match(karar[1], parca, `ayrisma karari eksik: ${parca}`);
  }
});

/** "Ayar sahada gecerli / aktif" iddiasi tasiyan sozcukler (tr + en). */
const AKTIF_IDDIA =
  /aktif|active|y[uü]r[uü]rl[uü]kte|in effect|ge[cç]erli|applied|uyguland[iı]/i;
/** Iddiayi geri alan ya da erteleyen sozcukler. */
const IDDIAYI_GERI_ALAN = /de[gğ]il|\bnot\b|hen[uü]z|\byet\b|bekle|waiting/i;

/** Kanit YOKKEN ya da kanit ISTENENDEN FARKLIYKEN ekranda duran metinler. */
const KANITSIZ_ANAHTARLAR = [
  "dialInConfigured",
  "dialInStatusDiffers",
  "dialInStatusNone",
  "dialInMismatch"
];

test("kanit yokken/ayrisirken hicbir metin ayarin gecerli oldugunu SOYLEMEZ", () => {
  for (const [ad, s] of DILLER) {
    for (const k of KANITSIZ_ANAHTARLAR) {
      const metin = s[k];
      assert.equal(typeof metin, "string", `${ad}: ${k} eksik`);
      if (AKTIF_IDDIA.test(metin)) {
        assert.match(
          metin,
          IDDIAYI_GERI_ALAN,
          `${ad}.${k}: gecerlilik iddiasi geri alinmamis -> ${metin}`
        );
      }
      assert.notEqual(
        metin,
        s.dialInStatusMatched,
        `${ad}.${k}: "Uygulandi" ile AYNI metin -- kanit seviyeleri ayrisamiyor`
      );
    }
    // Ayrisma notu bir SORUN anlatir: iddiayi geri alan bir ifade TASIMALI.
    assert.match(
      s.dialInMismatch,
      IDDIAYI_GERI_ALAN,
      `${ad}: ayrisma notu ayarin gecerli OLMADIGINI soylemiyor`
    );
    // "Istenen" sutun basligi bir durum iddiasi DEGILDIR.
    assert.ok(
      !AKTIF_IDDIA.test(s.dialInConfigured),
      `${ad}: "istenen" basligi gecerlilik ima ediyor -> ${s.dialInConfigured}`
    );
  }
});

test("ayrisma dalinda 'Uygulandi' rozeti CIZILMEZ", () => {
  const blok = dialInBloku();
  const farkliDal = /\{dialInFarkli \? \(([\s\S]*?)\) : null\}/.exec(blok);
  assert.ok(farkliDal, "ayrisma dali bulunamadi");
  assert.match(
    farkliDal[1],
    /engineering\.dnp3\.dialInMismatch/,
    "ayrisma dali baska bir metin gosteriyor"
  );
  assert.ok(
    !/dialInStatusMatched/.test(farkliDal[1]),
    "ayrisma anlatilirken 'Uygulandi' da yaziliyor"
  );
  // Ayrisma gorsel olarak da isaretlenir; sessiz kalmasi kanit gostermemekle
  // ayni yere duserdi.
  assert.match(
    blok,
    /dialInFarkli \? " dnp3-status-block--warn" : ""/,
    "ayrisan blok isaretlenmiyor"
  );
});

test("'cihazdan dogrulanan' satiri FORM degerini degil CIHAZ degerini basar", () => {
  const blok = dialInBloku();
  const satir = kesit(blok, "engineering.dnp3.dialInVerified", "engineering.dnp3.dialInStatus");
  assert.match(
    satir,
    /dialInDurumu\.readbackMin/,
    "dogrulanan satiri cihazdan gelen degeri okumuyor"
  );
  assert.ok(
    !/\bdialIn\b(?!Durumu|Etiket)/.test(satir),
    "dogrulanan satirinda formun secili degeri kullanilmis -- istenen, dogrulanmis gibi gosterilir"
  );
});

// ===========================================================================
// 6) OKUNAN DEGER YOKKEN TIRE
// ===========================================================================

/** Em dash (U+2014). Kaynakta ASCII "-" ya da cift-kodlanmis bir dizi
 *  olsaydi ekranda baska bir sey cikardi. */
const TIRE = "—";

test("dogrulanan deger yokken tire basilir -- bos ya da 0 DEGIL", () => {
  const tanim = /const DEGER_YOK = "([^"]*)";/.exec(FORM);
  assert.ok(tanim, "DEGER_YOK sabiti bulunamadi");
  assert.equal(
    tanim[1],
    TIRE,
    `kanitsiz alan gosterimi ${JSON.stringify(tanim[1])} -- bos metin ve "0" ` +
      '"ayar yok" gibi okunur, tire acikca "bilmiyoruz" der'
  );
  assert.equal(tanim[1].length, 1, "tire cift-kodlanmis (mojibake) gorunuyor");

  const blok = dialInBloku();
  assert.match(
    blok,
    /dialInDurumu\.readbackMin === null\s*\?\s*DEGER_YOK/,
    "kanit yokken tire basilmiyor"
  );
  assert.match(
    blok,
    /:\s*dialInEtiket\(dialInDurumu\.readbackMin\)/,
    "kanit varken cihazdan okunan deger basilmiyor"
  );
});

test("kanit HIC gelmediyse blok cizilmez (tire bile gosterilmez)", () => {
  // `undefined` = istek atilmadi / basarisiz / cihazda dosya yok. Bos bir
  // durum blogu cizmek, "sorduk ve cevap yok" izlenimi verirdi.
  assert.match(
    FORM,
    /\{dialInDurumu \? \(/,
    "durum blogu kosulsuz render ediliyor -- veri yokken uydurma dogrulama gosterilir"
  );
  assert.match(
    PANEL,
    /cfg \? \{ readbackMin: cfg\.dialInReadbackMin, status: cfg\.dialInReadbackStatus \} : undefined/,
    "yapilandirma yokken (404) durum undefined'a dusmuyor"
  );
});

// ===========================================================================
// 7) RUNTIME OTURUM DURUMU SIZMADI (ayar formu bir DURUM ekrani degildir)
// ===========================================================================

const RUNTIME_IFADELERI: ReadonlyArray<readonly [RegExp, string]> = [
  [/Smart Bekleme/i, "gateway'in anlik uyku durumu"],
  [/Gecikmi[sş]/i, "planli haberlesmenin gecikmesi"],
  [/\boverdue\b/i, "planli haberlesmenin gecikmesi (en)"],
  [/\brecovering\b/i, "oturum toparlanma durumu"],
  [/next_?expected|nextExpected/i, "bir sonraki beklenen haberlesme ani"],
  [/\bawaiting\b/i, "bekleme durumu"],
  [/session_state|sessionState/, "oturum durum makinesi"],
  [/comm_lost/, "haberlesme kaybi bayragi"],
  [/last_?seen|lastSeen/i, "son gorulme damgasi"]
];

test("ayar formuna runtime oturum durumu SIZMADI", () => {
  // Bu alanlarin verisi backend'de YOK; gosterilseydi uydurma olurdu. Ayrica
  // ayar formunda gorunmeleri operatore kaydedilebilir bir sey sanmasina yol
  // acardi (ve kaydet'e basinca hicbir sey degismezdi).
  for (const [desen, ne] of RUNTIME_IFADELERI) {
    assert.ok(
      !desen.test(FORM),
      `Dnp3SettingsForm.tsx ${ne} ifadesi tasiyor (${desen}) -- form yalnizca AYAR yazar`
    );
  }
});

test("yeni metinlerin hicbiri runtime durumu ANLATMIYOR", () => {
  const METINLER = KANITSIZ_ANAHTARLAR.concat([
    "dialInVerified",
    "dialInStatus",
    "dialInStatusMatched",
    "gatewayCompatWarn",
    "gatewayVersionUnknown",
    "gatewayUpdateAction"
  ]);
  for (const [ad, s] of DILLER) {
    for (const k of METINLER) {
      for (const [desen, ne] of RUNTIME_IFADELERI) {
        assert.ok(!desen.test(s[k]), `${ad}.${k}: ${ne} ima ediyor (${desen}) -> ${s[k]}`);
      }
    }
  }
});

test("panelin YENI kablolamasi da runtime durumu tasimiyor", () => {
  // Yalnizca bu isle eklenen bolgeler taranir: panelin geri kalaninda
  // `last_seen` MESRU olarak vardir (cihaz canlilik satiri) ve tum dosyayi
  // taramak testi yanlis yere dusururdu.
  const bolgeler = [
    kesit(PANEL, "const [dialInDurumu", "const applySelectedDeviceToForm"),
    kesit(PANEL, "dialInDurumu={dialInDurumu}", "usedMasterPorts")
  ];
  for (const bolge of bolgeler) {
    for (const [desen, ne] of RUNTIME_IFADELERI) {
      assert.ok(!desen.test(bolge), `panelin yeni kablolamasinda ${ne} var (${desen})`);
    }
  }
});

// ===========================================================================
// 8) I18N -- iki dil AYRISMASIN
// ===========================================================================

const YENI_ANAHTARLAR = [
  "dialInConfigured",
  "dialInVerified",
  "dialInStatus",
  "dialInStatusMatched",
  "dialInStatusDiffers",
  "dialInStatusNone",
  "dialInMismatch",
  "gatewayCompatWarn",
  "gatewayVersionUnknown",
  "gatewayUpdateAction"
];

test("yeni anahtarlar iki dilde de DOLU", () => {
  for (const [ad, s] of DILLER) {
    for (const k of YENI_ANAHTARLAR) {
      assert.equal(typeof s[k], "string", `${ad}.json: engineering.dnp3.${k} eksik`);
      assert.ok(s[k].trim().length > 0, `${ad}.json: engineering.dnp3.${k} bos`);
    }
  }
});

test("engineering.dnp3 anahtar kumeleri tr/en AYNI", () => {
  // Ayrisirsa eksik dilde ekrana ham anahtar duser. `fallbackLng` de "tr"
  // oldugu icin TR'de eksik olan bir anahtar HICBIR yerden karsilanmaz.
  assert.deepEqual(
    Object.keys(TR.engineering.dnp3).sort(),
    Object.keys(EN.engineering.dnp3).sort(),
    "engineering.dnp3 altinda iki dil ayrismis"
  );
});

test("formun kullandigi her dnp3 anahtari iki dilde de var", () => {
  const kullanilan = [...FORM.matchAll(/t\("engineering\.dnp3\.([A-Za-z0-9_]+)"/g)].map(
    (m) => m[1]
  );
  assert.ok(kullanilan.length > 0, "formda hic ceviri anahtari bulunamadi -- desen kaydi");
  for (const k of new Set(kullanilan)) {
    for (const [ad, s] of DILLER) {
      assert.ok(
        typeof s[k] === "string" && s[k].trim().length > 0,
        `formda kullanilan ${k} ${ad}.json'da yok -- ekrana ham anahtar duser`
      );
    }
  }
});
