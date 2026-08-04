/**
 * Gateway durum modeli — "durduruldu" ariza gibi gosterilmesin.
 *
 * YASANAN SORUN: durum yalnizca telemetri yasindan cikariliyordu. Panelden
 * gateway'i durduran operator ekranda kirmizi "Bagli degil" goruyor ve sahada
 * olmayan bir arizayi kovaliyordu. Ajanin bildirdigi container durumu
 * (`exited`) karara hic girmiyordu.
 *
 * Burada olculen sey KARARIN KENDISI: fonksiyon gercekten calistiriliyor.
 */

import assert from "node:assert/strict";
import { test } from "node:test";

import {
  deviceStatusUnderGateway,
  gatewayLiveness,
  GATEWAY_LIVE_SEC,
} from "../src/shared/gatewayLiveness";

const SIMDI = Date.parse("2026-08-04T12:00:00Z");
const taze = new Date(SIMDI - 5_000).toISOString();
const eski = new Date(SIMDI - (GATEWAY_LIVE_SEC + 120) * 1000).toISOString();

const temel = { isActive: true, nowMs: SIMDI };

// ---------------------------------------------------------------------------
// Dort durum birbirinden AYRI
// ---------------------------------------------------------------------------

test("telemetri tazeyse calisiyor", () => {
  assert.equal(gatewayLiveness({ ...temel, lastSeenAt: taze, localState: "running" }), "online");
});

test("ajan 'exited' diyorsa DURDURULDU — ariza degil", () => {
  // Sikayetin ta kendisi: veri gelmiyor ama sebebini BILIYORUZ.
  assert.equal(
    gatewayLiveness({ ...temel, lastSeenAt: eski, localState: "exited" }),
    "stopped",
  );
});

test("container ayakta ama veri yoksa ULASILAMIYOR (gercek ariza)", () => {
  assert.equal(
    gatewayLiveness({ ...temel, lastSeenAt: eski, localState: "running" }),
    "unreachable",
  );
});

test("ajan bu gateway'i tanimiyorsa BILINMIYOR — 'bagli degil' DEGIL", () => {
  // Gateway baska cihazda olabilir. Kirmizi gostermek, sormadan verilmis bir
  // iddia olurdu.
  assert.equal(gatewayLiveness({ ...temel, lastSeenAt: eski, localState: null }), "unknown");
  assert.equal(gatewayLiveness({ ...temel, lastSeenAt: eski }), "unknown");
});

test("durduruldu ile ulasilamiyor AYNI durum degil", () => {
  const durduruldu = gatewayLiveness({ ...temel, lastSeenAt: eski, localState: "exited" });
  const ulasilamiyor = gatewayLiveness({ ...temel, lastSeenAt: eski, localState: "running" });
  const bilinmiyor = gatewayLiveness({ ...temel, lastSeenAt: eski, localState: null });
  assert.equal(new Set([durduruldu, ulasilamiyor, bilinmiyor]).size, 3);
});

// ---------------------------------------------------------------------------
// Durdurulmus gateway her kosulda "durduruldu" der
// ---------------------------------------------------------------------------

test("durdurulmus gateway telemetri TAZE olsa bile durduruldu sayilir", () => {
  // Son olcum 5 saniye once gelmis olabilir (durdurma yeni yapildi); yesil
  // "calisiyor" demek 60 saniye boyunca yanlis bilgi olurdu.
  assert.equal(
    gatewayLiveness({ ...temel, lastSeenAt: taze, localState: "exited" }),
    "stopped",
  );
});

test("created / paused / dead da durmus sayilir", () => {
  for (const durum of ["created", "paused", "dead", "EXITED", " exited "]) {
    assert.equal(
      gatewayLiveness({ ...temel, lastSeenAt: eski, localState: durum }),
      "stopped",
      `${durum} durmus sayilmadi`,
    );
  }
});

test("'absent' durduruldu SAYILMAZ — container yok, kurulu degil", () => {
  // Silinmis bir container icin "durduruldu" demek uydurma olurdu.
  assert.notEqual(
    gatewayLiveness({ ...temel, lastSeenAt: eski, localState: "absent" }),
    "stopped",
  );
});

// ---------------------------------------------------------------------------
// ESKIMIS ajan raporu kesin iddiaya dayanak OLAMAZ
//
// Zamanlayici durursa state.json oldugu yerde kalir; icindeki "exited" eski
// bir olcumdur, container o gunden beri baslatilmis olabilir.
// ---------------------------------------------------------------------------

test("eskimis 'exited' + TAZE telemetri -> calisiyor, 'durduruldu' DEGIL", () => {
  // En kotu hali: veri AKIYOR ama ekran gateway'i kapali gosteriyordu ve
  // altindaki butun cihazlari griye boyuyordu.
  assert.equal(
    gatewayLiveness({ ...temel, lastSeenAt: taze, localState: "exited", localStale: true }),
    "online",
  );
});

test("eskimis 'exited' + eski telemetri -> BILINMIYOR", () => {
  assert.equal(
    gatewayLiveness({ ...temel, lastSeenAt: eski, localState: "exited", localStale: true }),
    "unknown",
  );
});

test("eskimis 'running' ULASILAMIYOR (ariza) iddiasini besleyemez", () => {
  // "Container ayakta ama veri yok" bir ariza iddiasidir; ayakta oldugunu
  // artik BILMIYORUZ.
  assert.equal(
    gatewayLiveness({ ...temel, lastSeenAt: eski, localState: "running", localStale: true }),
    "unknown",
  );
});

test("rapor TAZEYKEN eski davranis aynen surer", () => {
  assert.equal(
    gatewayLiveness({ ...temel, lastSeenAt: eski, localState: "exited", localStale: false }),
    "stopped",
  );
  assert.equal(
    gatewayLiveness({ ...temel, lastSeenAt: eski, localState: "running", localStale: false }),
    "unreachable",
  );
});

test("eskimis rapor 'pasif' / 'hic temas yok' kararlarini bozmaz", () => {
  assert.equal(
    gatewayLiveness({
      isActive: false,
      lastSeenAt: taze,
      localState: "exited",
      localStale: true,
      nowMs: SIMDI,
    }),
    "inactive",
  );
  assert.equal(
    gatewayLiveness({ ...temel, lastSeenAt: null, localState: "exited", localStale: true }),
    "never",
  );
});

// ---------------------------------------------------------------------------
// Mevcut durumlar korunuyor
// ---------------------------------------------------------------------------

test("yayin bayragi kapaliysa pasif", () => {
  assert.equal(
    gatewayLiveness({ isActive: false, lastSeenAt: taze, nowMs: SIMDI }),
    "inactive",
  );
});

test("hic temas etmemisse never", () => {
  assert.equal(gatewayLiveness({ ...temel, lastSeenAt: null }), "never");
});

test("bozuk tarih 'canli' de 'ariza' da saymaz", () => {
  assert.equal(gatewayLiveness({ ...temel, lastSeenAt: "olmayan-tarih" }), "unknown");
});

test("esik siniri: 60 sn altinda canli, ustunde degil", () => {
  const sinirAlti = new Date(SIMDI - (GATEWAY_LIVE_SEC - 1) * 1000).toISOString();
  const sinirUstu = new Date(SIMDI - (GATEWAY_LIVE_SEC + 1) * 1000).toISOString();
  assert.equal(gatewayLiveness({ ...temel, lastSeenAt: sinirAlti }), "online");
  assert.notEqual(gatewayLiveness({ ...temel, lastSeenAt: sinirUstu }), "online");
});

// ---------------------------------------------------------------------------
// YENI BASLATILAN container: olcum penceresi daha dolmadi
//
// "Baslat"a basildiginda durdurulmus gateway'in son sinyali cok eskidir.
// Container ayaga kalkar kalkmaz "calisiyor ama veri yok" demek, dugmenin
// kendi urettigi bir ARIZA IDDIASI olurdu — ustelik "islem tamamlandi"
// yazisiyla ayni anda ekranda.
// ---------------------------------------------------------------------------

test("yeni kalkan container ARIZA sayilmaz", () => {
  for (const metin of ["Up 4 seconds", "Up Less than a second", "Up About a minute"]) {
    assert.equal(
      gatewayLiveness({ ...temel, lastSeenAt: eski, localState: "running", localStatus: metin }),
      "unknown",
      `${metin} icin ariza iddia edildi`,
    );
  }
});

test("bir suredir ayakta olup veri gelmiyorsa ULASILAMIYOR — grace sonsuz degil", () => {
  for (const metin of ["Up 3 minutes", "Up 2 hours", "Up 5 days"]) {
    assert.equal(
      gatewayLiveness({ ...temel, lastSeenAt: eski, localState: "running", localStatus: metin }),
      "unreachable",
      `${metin} icin gercek ariza gizlendi`,
    );
  }
});

test("ayristirilamayan sure metni eski davranisi BOZMAZ", () => {
  // Kestirme yalnizca EMIN oldugumuzda calisir; taninmayan metin ariza
  // kararini degistirmemeli.
  for (const metin of [undefined, null, "", "Restarting (1) 3 seconds ago", "bilinmeyen"]) {
    assert.equal(
      gatewayLiveness({ ...temel, lastSeenAt: eski, localState: "running", localStatus: metin }),
      "unreachable",
      `${String(metin)} icin karar degisti`,
    );
  }
});

test("yeni kalkan container TAZE veri veriyorsa calisiyor", () => {
  // Grace bir sey GIZLEMEZ: veri geliyorsa dogrudan gozlem kazanir.
  assert.equal(
    gatewayLiveness({ ...temel, lastSeenAt: taze, localState: "running", localStatus: "Up 4 seconds" }),
    "online",
  );
});

test("yeni kalkan container 'durduruldu' demez", () => {
  // Durmus durumlar hala oncelikli: "Up ..." metni yalnizca running dalinda
  // okunur.
  assert.equal(
    gatewayLiveness({
      ...temel,
      lastSeenAt: eski,
      localState: "exited",
      localStatus: "Up 4 seconds",
    }),
    "stopped",
  );
});

// ---------------------------------------------------------------------------
// Alt cihazlar: gateway durdurulunca tum agac kirmizi olmasin
// ---------------------------------------------------------------------------

test("gateway durdurulmussa cihaz durumu BILINMIYOR", () => {
  assert.equal(deviceStatusUnderGateway("stopped", "online"), "unknown");
  assert.equal(deviceStatusUnderGateway("stopped", "offline"), "unknown");
});

test("gateway ULASILAMIYORSA cihaz offline — bu gercek bir ariza", () => {
  assert.equal(deviceStatusUnderGateway("unreachable", "online"), "offline");
});

test("gateway calisiyorsa cihazin kendi durumu gecerli", () => {
  assert.equal(deviceStatusUnderGateway("online", "offline"), "offline");
  assert.equal(deviceStatusUnderGateway("online", "online"), "online");
  assert.equal(deviceStatusUnderGateway("online", "unknown"), "unknown");
});

test("gateway durumu bilinmiyorsa cihaz da bilinmiyor", () => {
  assert.equal(deviceStatusUnderGateway("unknown", "online"), "unknown");
  assert.equal(deviceStatusUnderGateway("inactive", "online"), "unknown");
  assert.equal(deviceStatusUnderGateway("never", "online"), "unknown");
});
