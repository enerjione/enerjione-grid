/**
 * nginx BACKEND ADRESINI CALISMA ANINDA COZMELI.
 *
 * YASANAN ARIZA (iki kez, ayni gun)
 * ---------------------------------
 * `backend-api` container'i yeniden yaratilinca yeni IP alir. nginx upstream
 * adresini yalnizca baslangicta cozup sakladigi icin OLU adrese gitmeye devam
 * eder ve arayuz `502 Bad Gateway` doner.
 *
 * Kullanici tarafindan gorunusu: "guncelleme sonrasi giremiyorum" / "her sey
 * gitti". Teshisi ZOR, cunku her sey saglikli gorunur: backend Up (healthy),
 * /health container icinden 200, loglarda hata YOK. Yalnizca nginx uzerinden
 * gecen istek 502.
 *
 * `update.sh`'a restart eklenmisti ama o YALNIZCA guncelleme akisini korur;
 * elle `docker compose up -d` calistiran biri ayni 502'yi alir. Bu yasandi.
 *
 * TESTIN KILITLEDIGI IKI SEY
 * --------------------------
 * 1. `resolver` tanimli ve proxy_pass DEGISKEN kullaniyor (calisma ani cozum).
 * 2. Degiskenli proxy_pass'te URI YAZILMIYOR. Bu ikincisi kritik: nginx
 *    degiskenli bicimde URI gorurse orijinal istek yolunu ATAR ve TUM
 *    istekleri o tek yola gonderir. Sessiz ve yikici bir hata olurdu —
 *    yapilandirma gecerli, sistem ayakta, ama her istek yanlis uca gider.
 */
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const conf = readFileSync(new URL("../nginx.conf", import.meta.url), "utf8");

test("resolver tanimli — adres calisma aninda cozulebilsin", () => {
  assert.match(conf, /^\s*resolver\s+127\.0\.0\.11\b/m,
    "Docker gomulu DNS'i (127.0.0.11) icin resolver yok; degiskenli " +
    "proxy_pass calismaz ve nginx baslarken hata verir");
  assert.match(conf, /resolver[^;]*valid=\d+s/,
    "resolver'da valid suresi yok — cozum sonsuza kadar onbelleklenir");
  assert.match(conf, /resolver[^;]*ipv6=off/,
    "ipv6=off yok; Docker DNS bazi kurulumlarda AAAA icin bos yanit doner " +
    "ve nginx cozumu basarisiz sayar");
});

test("proxy_pass SABIT adres kullanmiyor", () => {
  const sabit = [...conf.matchAll(/proxy_pass\s+https?:\/\/backend-api[^;]*;/g)];
  assert.equal(sabit.length, 0,
    `${sabit.length} adet sabit adresli proxy_pass var: ${sabit.map((m) => m[0]).join(", ")}\n` +
    "Sabit adres baslangicta cozulup saklanir; backend yeniden yaratilinca 502 doner.");
});

test("degiskenli proxy_pass'te URI YAZILMIYOR", () => {
  const hepsi = [...conf.matchAll(/proxy_pass\s+(\$[A-Za-z_][A-Za-z0-9_]*)([^;]*);/g)];
  assert.ok(hepsi.length > 0, "degiskenli proxy_pass bulunamadi");
  for (const [tam, , kalan] of hepsi) {
    assert.equal(kalan.trim(), "",
      `"${tam}" — degiskenli proxy_pass'e URI eklenmis. nginx bu durumda ` +
      "orijinal istek yolunu ATAR ve TUM istekleri bu tek yola gonderir.");
  }
});

test("her proxy_pass ayni degiskeni kullaniyor ve o degisken tanimli", () => {
  const adlar = new Set(
    [...conf.matchAll(/proxy_pass\s+\$([A-Za-z_][A-Za-z0-9_]*)/g)].map((m) => m[1]),
  );
  assert.equal(adlar.size, 1, `birden fazla upstream degiskeni: ${[...adlar].join(", ")}`);
  const ad = [...adlar][0];
  // Duz metin arama: bu dosyada bir kez regex kacisi kaybolup test yanlis
  // yere bakti. Aranan sey sabit oldugu icin regex gereksiz risk.
  assert.ok(
    conf.includes(`set $${ad} http://backend-api:8000;`),
    `$${ad} tanimlanmamis ya da backend-api:8000i gostermiyor`,
  );
});

/**
 * index.html ONBELLEKLENMEMELI.
 *
 * YASANAN ARIZA
 * -------------
 * Bundle dosyalari hash'li ve `immutable` (7 gun). Yeni surumu isaret eden
 * TEK dosya index.html; uzerinde acik bir Cache-Control yoktu, dolayisiyla
 * tarayici sezgisel onbellekleme uyguluyordu. Deploy sonrasi eski index.html
 * yeniden dogrulanmadan kullanilip ESKI bundle yukleniyor, eski arayuz YENI
 * backend ile konusuyordu.
 *
 * Bedeli sessiz degil ama teshisi zor: alanlar surumler arasinda yer
 * degistirdiginde (2.73.0'da top_rules/flapping_devices device-health'e
 * tasindi) eski arayuz olmayan alani okuyor ve ekran "Beklenmeyen bir hata"
 * ile dusuyor. Operator icin gorunusu "uygulama bozuldu".
 *
 * Bu test iki seyi kilitler: no-store var, ve blok guvenlik basliklarini
 * include ediyor (add_header iceren bir location server duzeyindekileri
 * MIRAS ALMAZ — bu tuzak yapilandirmanin baska yerinde de not edilmis).
 */
test("index.html no-store ile servis ediliyor", () => {
  const blok = conf.match(/location\s*=\s*\/index\.html\s*\{([^}]*)\}/);
  assert.ok(blok, "index.html icin ayri bir location blogu yok");
  assert.match(
    blok[1],
    /add_header\s+Cache-Control\s+"[^"]*no-store/,
    "index.html Cache-Control: no-store almiyor — deploy sonrasi eski bundle yuklenir"
  );
  assert.match(
    blok[1],
    /include\s+\/etc\/nginx\/security-headers\.conf/,
    "add_header iceren blok guvenlik basliklarini MIRAS ALMAZ; include zorunlu"
  );
});
