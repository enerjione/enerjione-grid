/**
 * nginx'in YEDEK blogu, backend router'inin GERCEK yoluna bakmali.
 *
 * YASANAN ARIZA (denetim 2026-08-13)
 * ----------------------------------
 * Yedek yukleme/indirme icin buyuk govde + uzun timeout ayarlarini tasiyan
 * blok `location ^~ /api/v1/backups/` olarak yazilmisti. Ama router prefix'i
 * `/admin/backups`, `api_prefix` ise `/api/v1` — yani gercek yol
 * `/api/v1/admin/backups/`. Blok HIC ESLESMIYORDU.
 *
 * Sonuc: istekler genel `^~ /api/` blogundan gecti, server duzeyindeki
 * `client_max_body_size 10m` gecerli oldu ve 800 MB'lik bir yedek dosyasi
 * HTML govdeli 413 ile reddedildi. Backend'in 2 GiB'lik streaming kontrolu
 * hic calismadi. Yani "felaket kurtarmanin tek arayuz adimi" duzeltilmis
 * SANILIYORDU — duzeltme yanlis yola yazilmisti.
 *
 * IKINCI ARIZA (2026-08-18) — SONDAKI SLASH
 * -----------------------------------------
 * Yukaridaki duzeltme yolu dogrulttu ama blogu `^~ /api/v1/admin/backups/`
 * (SONDA SLASH) olarak yazdi. nginx, prefix'i slash ile biten ve proxy_pass'e
 * giden bir location varsa slash'siz istege KENDILIGINDEN 301 uretir. Liste
 * ucu (`@router.get("")`) tam olarak slash'siz yolda oturdugu icin:
 *     GET /api/v1/admin/backups   -> nginx 301   -> .../backups/
 *     GET /api/v1/admin/backups/  -> FastAPI 307 -> .../backups
 * ...sonsuz dongu. nginx'in 301'i mutlak URL + ic port (8080) yazdigindan
 * hedef baska bir origin oluyor, CSP `connect-src 'self'` engelliyor ve
 * arayuz "Failed to fetch" diyor. Yedek yonetimi sayfasi HIC ACILMIYORDU.
 *
 * Yani bu blok iki kosulu AYNI ANDA saglamali: dogru yol + slash'siz prefix.
 *
 * BU TEST NEDEN SABIT METIN KULLANMIYOR
 * -------------------------------------
 * Yolun iki ucu iki ayri dilde, iki ayri dosyada yasiyor: prefix Python
 * router'inda, location nginx conf'unda. Sabit bir dize yazmak ayni hatayi
 * tekrar mumkun kilardi. Bu yuzden prefix `backups.py`'DEN OKUNUR ve nginx
 * conf'unda o yolun gercekten bulundugu dogrulanir; router yarin
 * `/admin/yedekler`e tasinirsa test kirmizi olur.
 */
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const conf = readFileSync(new URL("../nginx.conf", import.meta.url), "utf8");
const backupsPy = readFileSync(
  new URL("../../backend-api/app/api/backups.py", import.meta.url),
  "utf8"
);

/** `APIRouter(prefix="...")` degerini kaynaktan cikarir. */
function routerPrefix(src) {
  const m = src.match(/APIRouter\([^)]*prefix\s*=\s*["']([^"']+)["']/s);
  assert.ok(m, "backups.py icinde APIRouter prefix'i bulunamadi");
  return m[1];
}

/** Yorum satirlari ATILMIS conf — aciklamalar testi yanlis yere goturmesin. */
const confKod = conf
  .split("\n")
  .filter((satir) => !satir.trim().startsWith("#"))
  .join("\n");

/** conf'taki tum `location ^~ <yol>` prefix'leri. */
function prefixLocationlari() {
  return [...confKod.matchAll(/location\s+\^~\s+(\S+)\s*\{/g)].map((m) => m[1]);
}

test("yedek blogu router'in GERCEK yoluna bakiyor", () => {
  const prefix = routerPrefix(backupsPy); // ornek: /admin/backups
  const beklenen = `/api/v1${prefix}`;

  const locations = prefixLocationlari();

  assert.ok(
    locations.includes(beklenen),
    `nginx.conf icinde "location ^~ ${beklenen}" YOK.\n` +
      `Bulunan location'lar: ${JSON.stringify(locations)}\n` +
      "Yedek yukleme bu blok eslesmezse genel /api/ blogundaki 10 MB " +
      "limitine takilir ve felaket kurtarma calismaz."
  );
});

test("yedek blogunun prefix'i slash ile BITMIYOR (301 dongusu)", () => {
  const prefix = routerPrefix(backupsPy);
  const slashli = `/api/v1${prefix}/`;

  assert.ok(
    !prefixLocationlari().includes(slashli),
    `nginx.conf icinde "location ^~ ${slashli}" var — SONDAKI SLASH KALKMALI.\n` +
      "nginx, prefix'i slash ile biten ve proxy_pass'e giden bir location " +
      "icin slash'siz istege KENDILIGINDEN 301 uretir; FastAPI da slash'liyi " +
      "307 ile geri gonderir. Liste ucu (@router.get(\"\")) slash'siz yolda " +
      "oldugu icin sayfa sonsuz donguye girer ve 'Failed to fetch' verir. " +
      "Slash'siz prefix zaten alt yollari da kapsar."
  );
});

test("nginx urettigi yonlendirmelerde ic portu yazmiyor", () => {
  // Container 8080 dinler, disariya 80 olarak cikar. nginx mutlak URL'e
  // $server_port koyarsa yonlendirme baska bir origin'e gider ve CSP
  // `connect-src 'self'` fetch'i keser.
  assert.match(
    confKod,
    /port_in_redirect\s+off\s*;/,
    "server blogunda `port_in_redirect off;` yok — nginx'in urettigi her " +
      "301 ic portu (:8080) sizdirir ve tarayicida cross-origin olur."
  );
});

test("yedek blogu buyuk govde + tamponsuz akis ayarlarini tasiyor", () => {
  const prefix = routerPrefix(backupsPy);
  const beklenen = `/api/v1${prefix}`;
  // Blogun govdesini al: location satirindan ilk kapanan suslu parantezin
  // oncesine kadar (ic blok yok, bu yeterli).
  const i = confKod.indexOf(`location ^~ ${beklenen}`);
  assert.ok(i >= 0, "yedek blogu bulunamadi");
  const govde = confKod.slice(i, confKod.indexOf("\n    }", i));

  // Limitin kendisi: backend tavani ile ayni buyuklukte olmali.
  assert.match(
    govde,
    /client_max_body_size\s+2g\s*;/,
    "client_max_body_size 2g yok — zincirdeki en dusuk deger kazanir."
  );
  // read_only container + 64 MiB tmpfs: govde diske tamponlanamaz.
  assert.match(
    govde,
    /proxy_request_buffering\s+off\s*;/,
    "proxy_request_buffering off yok — 64 MiB tmpfs'te buyuk yukleme patlar."
  );
  assert.match(
    govde,
    /proxy_buffering\s+off\s*;/,
    "proxy_buffering off yok — indirme yonunde proxy temp dosyasi yazilir."
  );
});
