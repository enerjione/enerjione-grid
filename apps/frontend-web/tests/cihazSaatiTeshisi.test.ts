/**
 * CIHAZ SAATI TESHISI — bagli oldugu tek sey CIHAZ DAMGASINA GUVEN.
 *
 * YASANAN OLAY
 * ------------
 * Sahada bir Horstmann'in RTC'si 2066 yilina kaymisti ve bu Grid'de HIC
 * gorunmuyordu. Cihaz `online` idi, olcum gonderiyordu, komut kabul
 * ediyordu — ama urettigi her olay damgasi 40 yil ileriydi.
 *
 * BU DOSYANIN KILITLEDIGI EN ONEMLI SEY
 * -------------------------------------
 * Saat teshisi BAGLANTI DURUMU DEGILDIR. `invalid` gorup cihazi kopuk
 * saymak saglikli bir filoyu arizali gosterir.
 */
import assert from "node:assert/strict";
import { test } from "node:test";
import { readFileSync } from "node:fs";
import { join } from "node:path";

import TR from "../src/shared/i18n/resources/tr.json";
import EN from "../src/shared/i18n/resources/en.json";
import {
  clockGorunum,
  clockToneClass,
  offsetMetni
} from "../src/features/device-detail/deviceClockStatus";
import { normalizeDeviceRuntime } from "../src/shared/deviceRuntimeState";

const oku = (...y: string[]) => readFileSync(join(process.cwd(), ...y), "utf8");
const PANEL = oku("src", "features", "device-detail", "DeviceRuntimePanel.tsx");
const CSS = oku("src", "styles.css");
const MODUL = oku("src", "features", "device-detail", "deviceClockStatus.ts");

const rt = (over: Record<string, unknown> = {}) => ({
  connection_state: "online",
  connected: true,
  reachable: true,
  updated_at: new Date().toISOString(),
  ...over
});

// ---------------------------------------------------------------------------
// 1) SAAT != BAGLANTI DURUMU  (en kritik kural)
// ---------------------------------------------------------------------------

test("bozuk saat BAGLANTI DURUMUNU degistirmez", () => {
  const bozuk = normalizeDeviceRuntime({
    runtime: rt({ device_clock_status: "invalid", device_clock_offset_sec: 1.26e9 }) as never
  });
  const temiz = normalizeDeviceRuntime({ runtime: rt({ device_clock_status: "ok" }) as never });
  assert.equal(bozuk.key, temiz.key, "saat durumu baglanti durumunu ezmis");
  assert.equal(bozuk.key, "ONLINE");
  assert.equal(bozuk.bucket, temiz.bucket);
});

test("need_time de BAGLANTI DURUMUNU degistirmez", () => {
  const d = normalizeDeviceRuntime({
    runtime: rt({ device_clock_status: "need_time", need_time_iin: true }) as never
  });
  assert.equal(d.key, "ONLINE");
});

test("durum normalizasyonu saat alanlarina HIC bakmaz", () => {
  const kaynak = oku("src", "shared", "deviceRuntimeState.ts");
  const govde = kaynak.slice(kaynak.indexOf("export function normalizeDeviceRuntime"));
  for (const alan of ["device_clock_status", "need_time_iin", "device_clock_offset_sec"]) {
    assert.ok(
      !govde.includes(`runtime.${alan}`),
      `${alan} baglanti durumu kararina girmis`
    );
  }
});

// ---------------------------------------------------------------------------
// 2) TESHIS SINIFLARI
// ---------------------------------------------------------------------------

test("dort sozlesme degeri de kendi tonunu alir", () => {
  const beklenen: Record<string, string> = {
    ok: "ok",
    need_time: "uyari",
    invalid: "sorun",
    unknown: "bilinmiyor"
  };
  for (const [deger, ton] of Object.entries(beklenen)) {
    const g = clockGorunum(rt({ device_clock_status: deger }) as never);
    assert.equal(g?.tone, ton, `${deger} -> ${g?.tone}, beklenen ${ton}`);
  }
});

test("invalid, need_time'DAN daha ciddi gosterilir", () => {
  // DNP3 saat senkronizasyonu TALEP GUDUMLUDUR: master yalnizca cihaz IIN1.4
  // assert ettiginde saat yazar. `need_time` -> duzelir. `invalid` + cihaz
  // saat istemiyor -> KENDILIGINDEN DUZELMEZ. Sahada gorulen tam olarak bu.
  const invalid = clockGorunum(rt({ device_clock_status: "invalid" }) as never);
  const needTime = clockGorunum(rt({ device_clock_status: "need_time" }) as never);
  assert.equal(invalid?.tone, "sorun");
  assert.equal(needTime?.tone, "uyari");
  assert.notEqual(invalid?.tone, needTime?.tone);
});

test("TANIMADIGIMIZ deger 'saat dogru' SAYILMAZ", () => {
  const g = clockGorunum(rt({ device_clock_status: "gelecekteki_teshis" }) as never);
  assert.notEqual(g?.tone, "ok", "bilinmeyen teshis saglikli gosterilmis");
  assert.equal(g?.tone, "bilinmiyor");
});

test("alan HIC gelmemisse blok CIZILMEZ", () => {
  // 1.15.0 gateway. Olculmemis bir seyi "bilinmiyor" diye gostermek de
  // bir iddiadir.
  assert.equal(clockGorunum(rt() as never), null);
  assert.equal(clockGorunum(rt({ device_clock_status: null }) as never), null);
  assert.equal(clockGorunum(rt({ device_clock_status: "  " }) as never), null);
  assert.equal(clockGorunum(null), null);
});

// ---------------------------------------------------------------------------
// 3) need_time_iin UC DURUMLU
// ---------------------------------------------------------------------------

test("need_time_iin: null ile false AYNI SEY DEGIL", () => {
  const yok = clockGorunum(rt({ device_clock_status: "invalid" }) as never);
  const hayir = clockGorunum(
    rt({ device_clock_status: "invalid", need_time_iin: false }) as never
  );
  const evet = clockGorunum(
    rt({ device_clock_status: "invalid", need_time_iin: true }) as never
  );
  assert.equal(yok?.needTime, null, "hic IIN gorulmedi -> null olmali");
  assert.equal(hayir?.needTime, false);
  assert.equal(evet?.needTime, true);
});

test("panel null iken IIN satirini CIZMEZ", () => {
  assert.match(PANEL, /saat\.needTime !== null \?/);
});

// ---------------------------------------------------------------------------
// 4) OFFSET — isaret bilgi tasir
// ---------------------------------------------------------------------------

test("offset ISARETI korunur: yon bilgidir", () => {
  assert.match(offsetMetni(12.5) ?? "", /^\+/);
  assert.match(offsetMetni(-12.5) ?? "", /^−/);
});

test("SIFIR offset gecerli bir olcumdur", () => {
  // Epoch alanlarindaki "0 gelmez" gerekcesi bu OLCU alani icin GECERSIZ:
  // 0.0 tam senkron demektir.
  const m = offsetMetni(0);
  assert.ok(m !== null, "0 sn 'olculmemis' sayilmis");
  assert.match(m!, /0/);
});

test("olculmemis offset null doner", () => {
  assert.equal(offsetMetni(null), null);
  assert.equal(offsetMetni(undefined), null);
  assert.equal(offsetMetni(Number.NaN), null);
});

test("40 yillik kayma OKUNABILIR gosterilir", () => {
  // Sahadaki gercek olay: RTC 2066'da. Saniye olarak yazmak okunamaz.
  const m = offsetMetni(1_262_304_000);
  assert.match(m ?? "", /yil/);
  assert.match(m ?? "", /^\+/);
});

// ---------------------------------------------------------------------------
// 5) i18n + stil
// ---------------------------------------------------------------------------

test("tum metinler iki dilde var", () => {
  const anahtarlar = [
    "title", "ok", "okHint", "needTime", "needTimeHint", "invalid",
    "invalidHint", "unknown", "unknownHint", "offset", "offsetHint",
    "needTimeFlag", "noNeedTimeFlag"
  ];
  for (const [ad, sozluk] of [
    ["tr", (TR as any).deviceDetail.clock],
    ["en", (EN as any).deviceDetail.clock]
  ] as const) {
    for (const k of anahtarlar) {
      assert.ok(
        typeof sozluk[k] === "string" && sozluk[k].length > 2,
        `${ad}: deviceDetail.clock.${k} yok`
      );
    }
  }
});

test("invalid aciklamasi 'kendiliginden duzelmez' uyarisini TASIR", () => {
  // Operatorun bilmesi gereken sey bu: cihaz saat istemiyorsa beklemek
  // ise yaramaz.
  const tr = (TR as any).deviceDetail.clock.invalidHint as string;
  assert.match(tr, /kendiliğinden düzelmez/i, `uyari eksik: ${tr}`);
});

test("saat tonlari DURUM tonlarindan AYRI", () => {
  // Ayni gorsel dili paylassalardi operator bozuk saati "cihaz arizali"
  // diye okurdu.
  for (const t of ["ok", "uyari", "sorun", "bilinmiyor"] as const) {
    const sinif = clockToneClass(t);
    assert.match(sinif, /^device-clock--/);
    assert.ok(CSS.includes(`.${sinif}`), `${sinif} stili yok`);
    assert.ok(!sinif.includes("runtime-tone"), "durum tonuyla karisiyor");
  }
});

test("SORUN tonu en belirgin, OK tonu sakin", () => {
  const blok = (s: string) => {
    const i = CSS.indexOf(`.${s} {`);
    return i > 0 ? CSS.slice(i, CSS.indexOf("}", i)) : "";
  };
  // `sorun` kendi zemini/kenarligini alir; `ok` yalnizca metin rengi.
  assert.match(blok("device-clock--sorun"), /background:/);
  assert.equal(blok("device-clock--ok"), "", "dogru saat kutlanmis (kendi zemini var)");
});

test("panel gateway alani gondermezse blogu HIC cizmez", () => {
  assert.match(PANEL, /\{saat \? \(/);
  assert.match(PANEL, /clockGorunum\(rt\)/);
});

test("arayuz 'saati duzelt' dugmesi SUNMAZ", () => {
  // Gateway zorla saat yazmaz; duzeltme cihaz/saha isidir.
  assert.ok(!/forceTimeSync|zorlaSaat|syncClock/i.test(PANEL));
  assert.ok(!/forceTimeSync|zorlaSaat|syncClock/i.test(MODUL));
});
