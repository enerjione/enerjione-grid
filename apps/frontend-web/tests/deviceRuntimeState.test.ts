/**
 * CIHAZ CALISMA-ZAMANI DURUMU — `device_health_v1` normalizeri.
 *
 * NE KORUNUYOR
 * ------------
 * Bu ekranin en agir hata sinifi "saglikli uyuyan cihazi ariza gostermek"tir.
 * Horstmann Smart modda modemini KAPATIR; `connected=false, reachable=false,
 * ip_probe_status=unreachable` o cihaz icin BEKLENEN degerlerdir. Naif bir
 * okuma ("baglanti yok -> offline") filonun yarisini kirmiziya boyar ve
 * gercek ariza o gurultunun icinde kaybolur. Ters yon de ayni derecede kotu:
 * gercekten kopmus bir cihazi "uyuyor" saymak.
 *
 * Ikisi de ekranda FARK EDILMEZ — iki durum da gecerli oldugu icin operator
 * yanlisi sorgulamaz. Bu yuzden kural saf bir fonksiyonda toplandi ve burada
 * GERCEKTEN CALISTIRILARAK sinaniyor.
 *
 * Sozlesme: `docs/gateway-contract/device-health-api-pr33.md`
 * (Gateway 1.15.0+; kanonik artifact infra/gateway-contract/v1.15.1.json).
 *
 * Kaynak metni okunan uc kural var (React kosucusu yok, bkz. tests/run.mjs):
 *   1. Hicbir yerde 1 SANIYELIK geri sayim zamanlayicisi kurulmuyor.
 *   2. `boost_mode_enabled` normalizerin karar girdisi DEGIL.
 *   3. Iki dilin `deviceRuntime` anahtar kumesi AYNI.
 */
import assert from "node:assert/strict";
import { test } from "node:test";
import { readFileSync } from "node:fs";
import { join } from "node:path";

import {
  DEVICE_RUNTIME_KEYS,
  deviceRuntimeStateOf,
  dialInCountdown,
  epochToDate,
  normalizeDeviceRuntime,
  runtimeBucketCounts,
  runtimeCounts,
  runtimeEnumKey,
  RUNTIME_STALE_AFTER_MS,
  sessionPolicyMismatch
} from "../src/shared/deviceRuntimeState";
import type {
  DeviceRuntimeHealthRecord,
  DeviceRuntimeStateKey
} from "../src/shared/deviceRuntimeState";

const oku = (...p: string[]) => readFileSync(join(process.cwd(), ...p), "utf8");
const okuSrc = (...p: string[]) => oku("src", ...p);

const SIMDI = Date.UTC(2026, 7, 20, 12, 0, 0); // 2026-08-20T12:00:00Z
const TAZE = new Date(SIMDI - 30_000).toISOString();

/** Sozlesme bolum 4 sekline uygun kayit uret. */
function kayit(over: Partial<DeviceRuntimeHealthRecord> = {}): DeviceRuntimeHealthRecord {
  return {
    device_code: "SN2-001",
    connection_state: "online",
    connected: true,
    reachable: true,
    configured_session_policy: "auto",
    effective_session_policy: "smart",
    operation_mode: "smart",
    dial_in_interval_min: 720,
    next_expected_report_epoch: null,
    report_overdue_sec: 0,
    report_late: false,
    last_valid_contact_epoch: SIMDI / 1000 - 600,
    last_frame_epoch: SIMDI / 1000 - 600,
    ip_probe_status: "unknown",
    tcp_probe_status: "connecting",
    last_probe_epoch: null,
    ip_endpoint_type: "listening",
    gateway_code: "GW-001",
    updated_at: TAZE,
    ...over
  };
}

const durum = (over: Partial<DeviceRuntimeHealthRecord> = {}, legacy: "online" | "offline" | "unknown" | null = null) =>
  normalizeDeviceRuntime({ runtime: kayit(over), legacyStatus: legacy, nowMs: SIMDI });

// ---------------------------------------------------------------------------
// 1) ALTI DURUM
// ---------------------------------------------------------------------------

test("alti connection_state degeri dogru anahtara/renge/kovaya duser", () => {
  const beklenen: [string, DeviceRuntimeStateKey, string, string][] = [
    ["online", "ONLINE", "green", "healthy"],
    ["smart_idle", "SMART_IDLE", "blue", "healthy"],
    ["recovering", "RECOVERING", "amber", "degraded"],
    ["lost", "COMM_LOST", "red", "unhealthy"],
    ["listener_error", "LISTENER_ERROR", "red", "unhealthy"],
    ["unknown", "UNKNOWN", "slate", "unknown"]
  ];
  for (const [wire, key, tone, bucket] of beklenen) {
    const d = durum({ connection_state: wire });
    assert.equal(d.key, key, `${wire} -> ${key} olmali`);
    assert.equal(d.tone, tone, `${wire} tonu ${tone} olmali`);
    assert.equal(d.bucket, bucket, `${wire} kovasi ${bucket} olmali`);
    assert.equal(d.source, "gateway");
  }
});

test("smart_idle SAGLIKLIDIR — offline/gri/kirmizi DEGIL", () => {
  // Asil korunan hata. Uyuyan Horstmann'in BEKLENEN alanlari:
  const d = durum({
    connection_state: "smart_idle",
    connected: false,
    reachable: false,
    ip_probe_status: "unreachable",
    tcp_probe_status: "unknown"
  });
  assert.equal(d.key, "SMART_IDLE");
  assert.equal(d.bucket, "healthy");
  assert.equal(d.tone, "blue");
  assert.notEqual(d.tone, "slate");
  assert.notEqual(d.tone, "red");
  assert.notEqual(d.key, "COMM_LOST");
});

test("sonda sonucu durumu DEGISTIRMEZ — salt teshis", () => {
  // `ip_probe_status = unreachable` gormek normaldir (sozlesme bolum 5).
  for (const probe of ["reachable", "unreachable", "unsupported", "unknown"]) {
    assert.equal(durum({ connection_state: "online", ip_probe_status: probe }).key, "ONLINE");
    assert.equal(
      durum({ connection_state: "smart_idle", ip_probe_status: probe }).key,
      "SMART_IDLE"
    );
  }
  for (const tcp of ["open", "connecting", "unknown"]) {
    assert.equal(durum({ connection_state: "lost", tcp_probe_status: tcp }).key, "COMM_LOST");
  }
});

test("bilinmeyen bir connection_state legacy'ye DUSMEZ, UNKNOWN olur ve ham deger korunur", () => {
  // Sozlesme acik; yeni bir durum eklenebilir. Sessizce yutmak, yeni davranisi
  // hicbir ekranda gorunmez kilardi.
  const d = durum({ connection_state: "handshaking" }, "online");
  assert.equal(d.key, "UNKNOWN");
  assert.equal(d.source, "gateway");
  assert.equal(d.rawState, "handshaking");
});

// ---------------------------------------------------------------------------
// 2) report_late — BAYRAK, DURUM DEGIL
// ---------------------------------------------------------------------------

test("smart_idle + report_late -> LATE (turuncu, degraded)", () => {
  const d = durum({ connection_state: "smart_idle", report_late: true });
  assert.equal(d.key, "LATE");
  assert.equal(d.tone, "orange");
  assert.equal(d.bucket, "degraded");
  assert.equal(d.reportLate, true);
  // Gateway durumu HALA smart_idle; ham deger ezilmedi.
  assert.equal(d.rawState, "smart_idle");
});

test("report_late `lost` YAPMAZ", () => {
  // Gecikme cok sik iyi huyludur; `lost` sayilirsa gunluk sahte alarm uretir.
  const d = durum({ connection_state: "smart_idle", report_late: true });
  assert.notEqual(d.key, "COMM_LOST");
  assert.notEqual(d.bucket, "unhealthy");
});

test("KPI: gecikmis cihaz TEK kez sayilir — Smart Bekleme'de AYRICA yok", () => {
  const states = [
    durum({ connection_state: "smart_idle" }),
    durum({ connection_state: "smart_idle", report_late: true }),
    durum({ connection_state: "smart_idle", report_late: true }),
    durum({ connection_state: "online" }),
    durum({ connection_state: "lost" })
  ];
  const c = runtimeCounts(states);
  assert.equal(c.total, 5);
  assert.equal(c.SMART_IDLE, 1, "gecikmis olanlar Smart Bekleme'de sayilmamali");
  assert.equal(c.LATE, 2);
  assert.equal(c.ONLINE, 1);
  assert.equal(c.COMM_LOST, 1);
  // BOLUNTU: alt toplamlarin toplami tam olarak toplam eder — cift sayim
  // yapisal olarak imkansiz.
  const toplam = DEVICE_RUNTIME_KEYS.reduce((acc, k) => acc + c[k], 0);
  assert.equal(toplam, c.total);
});

test("kova sayimi da boluntudur", () => {
  const states = [
    durum({ connection_state: "online" }),
    durum({ connection_state: "smart_idle" }),
    durum({ connection_state: "smart_idle", report_late: true }),
    durum({ connection_state: "recovering" }),
    durum({ connection_state: "lost" }),
    durum({ connection_state: "listener_error" }),
    durum({ connection_state: "unknown" })
  ];
  const b = runtimeBucketCounts(states);
  assert.deepEqual(
    { healthy: b.healthy, degraded: b.degraded, unhealthy: b.unhealthy, unknown: b.unknown },
    { healthy: 2, degraded: 2, unhealthy: 2, unknown: 1 }
  );
  assert.equal(b.healthy + b.degraded + b.unhealthy + b.unknown, b.total);
});

// ---------------------------------------------------------------------------
// 3) GATEWAY OTORITEDIR
// ---------------------------------------------------------------------------

test("telemetri turevli legacy durum gateway'i EZMEZ", () => {
  // Legacy "online" derken gateway "lost" diyorsa gateway hakli.
  assert.equal(durum({ connection_state: "lost" }, "online").key, "COMM_LOST");
  // Ters yon: legacy "offline" derken gateway "smart_idle" diyorsa MAVI kalir.
  assert.equal(durum({ connection_state: "smart_idle" }, "offline").key, "SMART_IDLE");
  assert.equal(durum({ connection_state: "recovering" }, "online").key, "RECOVERING");
  assert.equal(durum({ connection_state: "online" }, "offline").key, "ONLINE");
});

test("recovering salinimi TELAFI EDILMEZ — gateway ne diyorsa o", () => {
  // Gateway 1.14'te bilinen bir recovery salinim hatasi var. Grid onu
  // duzeltmeye kalkarsa gercek davranis hicbir ekranda gorunmez olur.
  const a = durum({ connection_state: "recovering" }, "online");
  const b = durum({ connection_state: "recovering", connected: true, reachable: true }, "online");
  assert.equal(a.key, "RECOVERING");
  assert.equal(b.key, "RECOVERING", "connected=true 'aslinda online' anlamina getirilmemeli");
});

test("boost_mode_enabled RUNTIME OTORITESI DEGIL", () => {
  // Sozlesme bolum 5: bir YETENEKTIR (konfigurasyon), calisma-zamani durumu
  // degildir; bu kanalda GONDERILMEZ ve siniflandirmaya GIRMEZ.
  const yalin = durum({ connection_state: "smart_idle" });
  const bulasik = normalizeDeviceRuntime({
    runtime: { ...kayit({ connection_state: "smart_idle" }), boost_mode_enabled: true } as never,
    nowMs: SIMDI
  });
  assert.equal(bulasik.key, yalin.key);
  assert.equal(bulasik.tone, yalin.tone);
  assert.equal(bulasik.bucket, yalin.bucket);

  // Kaynak metninde de gecmemeli: gecerse birileri onu karara sokmus demektir.
  const kaynak = okuSrc("shared", "deviceRuntimeState.ts");
  assert.ok(
    !/boost_mode_enabled/.test(kaynak),
    "normalizer `boost_mode_enabled` okuyor — o bir yetenek, calisma-zamani durumu degil"
  );
});

test("operation_mode durumu belirlemez, ayri bir alandir", () => {
  for (const mod of ["smart", "boost", "unknown"]) {
    assert.equal(durum({ connection_state: "smart_idle", operation_mode: mod }).key, "SMART_IDLE");
    assert.equal(durum({ connection_state: "lost", operation_mode: mod }).key, "COMM_LOST");
  }
  // Etiketi ise belgelenmis kumeden gelir.
  assert.equal(runtimeEnumKey("operationMode", "boost"), "deviceRuntime.operationMode.boost");
  assert.equal(runtimeEnumKey("operationMode", "satellite"), null, "belgelenmemis deger ham kalmali");
});

// ---------------------------------------------------------------------------
// 4) VERI YOK / BAYAT -> LEGACY, UYDURMA YOK
// ---------------------------------------------------------------------------

test("runtime kaydi yoksa eski davranis; uydurma smart_idle/late/recovering URETILMEZ", () => {
  const a = normalizeDeviceRuntime({ legacyStatus: "online", nowMs: SIMDI });
  assert.equal(a.key, "ONLINE");
  assert.equal(a.source, "legacy");
  assert.equal(a.reportLate, false);

  const b = normalizeDeviceRuntime({ legacyStatus: "offline", nowMs: SIMDI });
  assert.equal(b.key, "COMM_LOST");
  assert.equal(b.source, "legacy");

  const c = normalizeDeviceRuntime({ legacyStatus: "unknown", nowMs: SIMDI });
  assert.equal(c.key, "UNKNOWN");

  // Hicbir kaynak yok: "bilmiyorum" — "sorun yok" degil.
  const d = normalizeDeviceRuntime({ nowMs: SIMDI });
  assert.equal(d.key, "UNKNOWN");
  assert.equal(d.source, "none");

  for (const s of [a, b, c, d]) {
    assert.ok(
      !["SMART_IDLE", "LATE", "RECOVERING"].includes(s.key),
      "eski gateway'de bu durumlar UYDURULAMAZ"
    );
  }
});

test("connection_state bos/eksikse legacy'ye dusulur", () => {
  assert.equal(
    normalizeDeviceRuntime({ runtime: kayit({ connection_state: "   " }), legacyStatus: "online", nowMs: SIMDI })
      .source,
    "legacy"
  );
  assert.equal(
    normalizeDeviceRuntime({ runtime: kayit({ connection_state: null }), legacyStatus: "offline", nowMs: SIMDI })
      .key,
    "COMM_LOST"
  );
});

test("BAYAT gozlem karari tasimaz ama gorunur kalir", () => {
  const eski = new Date(SIMDI - RUNTIME_STALE_AFTER_MS - 60_000).toISOString();
  const d = normalizeDeviceRuntime({
    runtime: kayit({ connection_state: "online", report_late: true, updated_at: eski }),
    legacyStatus: "offline",
    nowMs: SIMDI
  });
  assert.equal(d.stale, true, "bayatlik bayragi gorunmeli");
  assert.equal(d.source, "legacy", "bayat kayit karar veremez");
  assert.equal(d.key, "COMM_LOST", "karar legacy'den gelmeli");
  assert.equal(d.rawState, "online", "son bilinen durum teshiste kalmali");
  assert.equal(d.reportLate, false, "bayat gozlemden BAYRAK tasinmaz");
});

test("esigin altindaki gozlem bayat DEGIL", () => {
  const sinirda = new Date(SIMDI - RUNTIME_STALE_AFTER_MS + 1_000).toISOString();
  const d = normalizeDeviceRuntime({
    runtime: kayit({ connection_state: "smart_idle", updated_at: sinirda }),
    legacyStatus: "offline",
    nowMs: SIMDI
  });
  assert.equal(d.stale, false);
  assert.equal(d.key, "SMART_IDLE");
});

test("updated_at yoksa BAYATLIK IDDIA EDILMEZ", () => {
  // Olcemedigim seyi "eski" ilan etmek de bir uydurmadir.
  const d = normalizeDeviceRuntime({
    runtime: kayit({ connection_state: "smart_idle", updated_at: null }),
    legacyStatus: "offline",
    nowMs: SIMDI
  });
  assert.equal(d.stale, false);
  assert.equal(d.key, "SMART_IDLE");
  assert.equal(d.source, "gateway");
});

// ---------------------------------------------------------------------------
// 5) GERI SAYIM — DAKIKA, SANIYE YOK
// ---------------------------------------------------------------------------

test("gelecekteki Dial-In dakikaya YUVARLANIR (yukari)", () => {
  const s = durum({
    connection_state: "smart_idle",
    next_expected_report_epoch: SIMDI / 1000 + 43 * 60
  });
  const geri = dialInCountdown({ runtime: kayit({ next_expected_report_epoch: SIMDI / 1000 + 43 * 60 }), state: s, nowMs: SIMDI });
  assert.deepEqual(geri, { kind: "dueIn", minutes: 43 });

  // 42 dk 10 sn -> 43 dk (yukari yuvarlama: "42" yazmak erken bir soz olurdu)
  const rec = kayit({ next_expected_report_epoch: SIMDI / 1000 + 42 * 60 + 10 });
  const s2 = normalizeDeviceRuntime({ runtime: { ...rec, connection_state: "smart_idle" }, nowMs: SIMDI });
  assert.deepEqual(dialInCountdown({ runtime: rec, state: s2, nowMs: SIMDI }), {
    kind: "dueIn",
    minutes: 43
  });
});

test("gecmis Dial-In gecikme olarak gosterilir ve EN AZ 1 dk'dir", () => {
  const rec = kayit({
    connection_state: "smart_idle",
    report_late: true,
    next_expected_report_epoch: SIMDI / 1000 - 6 * 60
  });
  const s = normalizeDeviceRuntime({ runtime: rec, nowMs: SIMDI });
  assert.equal(s.key, "LATE");
  assert.deepEqual(dialInCountdown({ runtime: rec, state: s, nowMs: SIMDI }), {
    kind: "overdue",
    minutes: 6
  });

  // Yeni gecmis: "0 dk gecikme" ekranda kendi kendini yalanlar.
  const rec2 = kayit({
    connection_state: "smart_idle",
    report_late: true,
    next_expected_report_epoch: SIMDI / 1000 - 5
  });
  const s2 = normalizeDeviceRuntime({ runtime: rec2, nowMs: SIMDI });
  assert.deepEqual(dialInCountdown({ runtime: rec2, state: s2, nowMs: SIMDI }), {
    kind: "overdue",
    minutes: 1
  });
});

test("lost -> geri sayim degil, 'beklenen Dial-In asildi'", () => {
  const rec = kayit({ connection_state: "lost", next_expected_report_epoch: SIMDI / 1000 - 9000 });
  const s = normalizeDeviceRuntime({ runtime: rec, nowMs: SIMDI });
  assert.deepEqual(dialInCountdown({ runtime: rec, state: s, nowMs: SIMDI }), { kind: "lost" });
});

test("epoch null ise geri sayim HIC gosterilmez", () => {
  // Sozlesme bolum 4: `null` = HIC OLMADI. Gateway 0 gondermez; biz de
  // olmayan bir randevuyu varmis gibi gostermeyiz.
  for (const state of ["smart_idle", "online", "lost", "recovering"]) {
    const rec = kayit({ connection_state: state, next_expected_report_epoch: null });
    const s = normalizeDeviceRuntime({ runtime: rec, nowMs: SIMDI });
    assert.deepEqual(
      dialInCountdown({ runtime: rec, state: s, nowMs: SIMDI }),
      { kind: "none" },
      `${state} + epoch null -> geri sayim olmamali`
    );
  }
});

test("gateway otoritesi yoksa (legacy/bayat) geri sayim gosterilmez", () => {
  const legacy = normalizeDeviceRuntime({ legacyStatus: "online", nowMs: SIMDI });
  assert.deepEqual(
    dialInCountdown({ runtime: kayit({ next_expected_report_epoch: SIMDI / 1000 + 600 }), state: legacy, nowMs: SIMDI }),
    { kind: "none" }
  );
});

test("geri sayim SANIYE URETMEZ", () => {
  // Donen tip yalnizca `minutes` tasir; saniye alani olsaydi ekrana da duserdi.
  const rec = kayit({
    connection_state: "smart_idle",
    next_expected_report_epoch: SIMDI / 1000 + 1234
  });
  const s = normalizeDeviceRuntime({ runtime: rec, nowMs: SIMDI });
  const geri = dialInCountdown({ runtime: rec, state: s, nowMs: SIMDI }) as { kind: string };
  assert.deepEqual(Object.keys(geri).sort(), ["kind", "minutes"]);
});

test("geri sayim `next_expected_report_epoch`tan gelir — yeniden HESAPLANMAZ", () => {
  // Tuzak: `last_valid_contact + dial_in_interval` ile hesaplayan bir kod
  // burada 720 dk cikarirdi; gateway ise 43 dk diyor. Otorite gateway.
  const rec = kayit({
    connection_state: "smart_idle",
    dial_in_interval_min: 720,
    last_valid_contact_epoch: SIMDI / 1000,
    next_expected_report_epoch: SIMDI / 1000 + 43 * 60
  });
  const s = normalizeDeviceRuntime({ runtime: rec, nowMs: SIMDI });
  assert.deepEqual(dialInCountdown({ runtime: rec, state: s, nowMs: SIMDI }), {
    kind: "dueIn",
    minutes: 43
  });
  // Kaynak metninde de turetme olmamali.
  const kaynak = okuSrc("shared", "deviceRuntimeState.ts");
  assert.ok(
    !/last_valid_contact_epoch\s*\+/.test(kaynak),
    "geri sayim `last_valid_contact + interval` ile turetiliyor — epoch otoritesi bozulmus"
  );
});

test("epoch 0 tarih uretmez (panelde 1970 cikmasin)", () => {
  assert.equal(epochToDate(null), null);
  assert.equal(epochToDate(0), null);
  assert.equal(epochToDate(undefined), null);
  assert.equal(epochToDate(SIMDI / 1000)?.getTime(), SIMDI);
});

// ---------------------------------------------------------------------------
// 6) SANIYELIK ZAMANLAYICI YOK
// ---------------------------------------------------------------------------

test("geri sayim DAKIKADA bir tazelenir; 1 saniyelik interval YOK", () => {
  const tick = okuSrc("shared", "useMinuteTick.ts");
  assert.match(tick, /MINUTE_TICK_MS\s*=\s*60_?000/, "tazeleme periyodu 60 sn olmali");

  // Geri sayima dokunan dosyalarda 1000/1_000 ms'lik bir zamanlayici olmamali.
  const dosyalar = [
    ["shared", "useMinuteTick.ts"],
    ["shared", "deviceRuntimeState.ts"],
    ["components", "DialInCountdown.tsx"],
    ["features", "device-detail", "DeviceRuntimePanel.tsx"]
  ];
  const kotu = /set(?:Interval|Timeout)\s*\([^,]+,\s*(?:1000|1_000)\s*\)/;
  for (const yol of dosyalar) {
    const metin = okuSrc(...yol);
    assert.ok(
      !kotu.test(metin),
      `${yol.join("/")} icinde 1 saniyelik zamanlayici var — geri sayim DAKIKA gosteriyor`
    );
  }

  // Geri sayim bileseni saati kendi kurmaz, ortak hook'u kullanir.
  assert.match(okuSrc("components", "DialInCountdown.tsx"), /useMinuteTick/);
});

test("geri sayim metni saniye birimi TASIMAZ", () => {
  const tr = JSON.parse(oku("src", "shared", "i18n", "resources", "tr.json"));
  const en = JSON.parse(oku("src", "shared", "i18n", "resources", "en.json"));
  for (const g of [tr.deviceRuntime.countdown, en.deviceRuntime.countdown]) {
    for (const metin of Object.values(g) as string[]) {
      assert.ok(!/\{\{seconds\}\}/.test(metin), `geri sayim metninde saniye var: ${metin}`);
    }
  }
  assert.match(tr.deviceRuntime.countdown.dueIn, /\{\{minutes\}\}/);
  assert.match(en.deviceRuntime.countdown.dueIn, /\{\{minutes\}\}/);
});

// ---------------------------------------------------------------------------
// 7) YAPILANDIRILAN vs ETKIN
// ---------------------------------------------------------------------------

test("auto COZULMEMIS demektir, ayrisma DEGIL", () => {
  // `auto` gateway'de `continuous`/`smart` olarak cozulur. Ayrisma sayilsaydi
  // dogru calisan her auto cihazda kalici bir uyari rozeti yanardi.
  assert.equal(sessionPolicyMismatch("auto", "smart"), false);
  assert.equal(sessionPolicyMismatch("auto", "continuous"), false);
  assert.equal(sessionPolicyMismatch("smart", "unknown"), false, "'henuz bilinmiyor' ayrisma degil");
  assert.equal(sessionPolicyMismatch(null, "smart"), false);
  assert.equal(sessionPolicyMismatch("smart", null), false);
});

test("gercek ayrisma yakalanir", () => {
  assert.equal(sessionPolicyMismatch("smart", "continuous"), true);
  assert.equal(sessionPolicyMismatch("continuous", "smart"), true);
  assert.equal(sessionPolicyMismatch("SMART", "smart"), false, "buyuk/kucuk harf fark etmemeli");
});

test("detay karti yapilandirma ile calisma zamanini AYRI cizer", () => {
  const panel = okuSrc("features", "device-detail", "DeviceRuntimePanel.tsx");
  assert.match(panel, /deviceRuntime\.panel\.configTitle/);
  assert.match(panel, /deviceRuntime\.panel\.runtimeTitle/);
  assert.match(panel, /deviceRuntime\.panel\.diagnosticsTitle/);
  // Yapilandirilan ve ETKIN oturum ayri satir.
  assert.match(panel, /deviceRuntime\.panel\.sessionPolicyConfigured/);
  assert.match(panel, /deviceRuntime\.panel\.sessionPolicyEffective/);
  // Probe'lar TESHIS basligi altinda ve renksiz: `tone` verilmemeli.
  const diagBolum = panel.slice(panel.indexOf("diagnosticsTitle"));
  assert.ok(
    !/ipProbe[\s\S]{0,220}tone=/.test(diagBolum),
    "sonda satirina renk verilmis — sonda sonucu durum belirlemez"
  );
});

// ---------------------------------------------------------------------------
// 8) SAHA SENARYOLARI
// ---------------------------------------------------------------------------

test("SAHA: online -> Smart uykuya gecis MAVI kalir ve geri sayim gosterir", () => {
  // Cihaz raporunu verdi, modemini kapatti. `connected`/`reachable` false,
  // sonraki Dial-In 43 dk sonra. Beklenen: MAVI "Smart Bekleme" + geri sayim.
  const once = durum({ connection_state: "online", connected: true, reachable: true });
  assert.equal(once.key, "ONLINE");

  const rec = kayit({
    connection_state: "smart_idle",
    connected: false,
    reachable: false,
    ip_probe_status: "unreachable",
    tcp_probe_status: "unknown",
    report_late: false,
    next_expected_report_epoch: SIMDI / 1000 + 43 * 60
  });
  const sonra = normalizeDeviceRuntime({ runtime: rec, legacyStatus: "offline", nowMs: SIMDI });
  assert.equal(sonra.key, "SMART_IDLE");
  assert.equal(sonra.tone, "blue");
  assert.equal(sonra.bucket, "healthy");
  assert.deepEqual(dialInCountdown({ runtime: rec, state: sonra, nowMs: SIMDI }), {
    kind: "dueIn",
    minutes: 43
  });
});

test("SAHA: kurtarma zinciri lost -> recovering -> online -> smart_idle", () => {
  const zincir: [string, DeviceRuntimeStateKey, string][] = [
    ["lost", "COMM_LOST", "unhealthy"],
    ["recovering", "RECOVERING", "degraded"],
    ["online", "ONLINE", "healthy"],
    ["smart_idle", "SMART_IDLE", "healthy"]
  ];
  for (const [wire, key, bucket] of zincir) {
    // Legacy telemetri boyunca "offline" diyor: zincirin hicbir adiminda
    // gateway'in karari ezilmemeli.
    const d = durum({ connection_state: wire }, "offline");
    assert.equal(d.key, key, `${wire} -> ${key}`);
    assert.equal(d.bucket, bucket);
    assert.equal(d.source, "gateway");
  }
});

test("deviceRuntimeStateOf DeviceRow benzeri nesneden calisir", () => {
  const d = deviceRuntimeStateOf(
    { runtimeHealth: kayit({ connection_state: "smart_idle" }), communicationStatus: "offline" },
    SIMDI
  );
  assert.equal(d.key, "SMART_IDLE");
  const eski = deviceRuntimeStateOf({ communicationStatus: "online" }, SIMDI);
  assert.equal(eski.key, "ONLINE");
  assert.equal(eski.source, "legacy");
});

// ---------------------------------------------------------------------------
// 9) EKRANLAR TEK NORMALIZERI KULLANIYOR
// ---------------------------------------------------------------------------

test("liste/kart/harita/KPI ham connection_state'e BAKMIYOR", () => {
  // Ayri ayri `if smart_idle` yazilmasi, bir ekranin digerlerinden ayrismasi
  // demek: ayni cihaz listede mavi, haritada gri olur.
  const ekranlar = [
    ["app", "App.tsx"],
    ["features", "devices", "DeviceRowButton.tsx"],
    ["features", "devices", "DeviceLineTree.tsx"],
    ["features", "map", "DeviceMapTab.tsx"],
    ["features", "dashboard", "GridOverviewPage.tsx"],
    ["features", "device-detail", "DeviceSidebar.tsx"]
  ];
  for (const yol of ekranlar) {
    const metin = okuSrc(...yol);
    assert.ok(
      !/["']smart_idle["']/.test(metin),
      `${yol.join("/")} ham 'smart_idle' okuyor — karar tek normalizerde olmali`
    );
    assert.ok(
      !/connection_state/.test(metin),
      `${yol.join("/")} ham 'connection_state' okuyor — karar tek normalizerde olmali`
    );
    assert.match(
      metin,
      /deviceRuntimeStateOf|RuntimeState(?:Dot|Chip)/,
      `${yol.join("/")} normalizeri kullanmiyor`
    );
  }
});

test("harita SMART_IDLE icin MAVI kullaniyor", () => {
  const harita = okuSrc("features", "map", "DeviceMapTab.tsx");
  // Ton -> renk tablosu; mavi girdisi dusserse smart_idle gri marker'a doner
  // ve operator "olu cihaz" okur.
  assert.match(harita, /blue:\s*"#3b82f6"/, "harita ton tablosunda mavi yok");
  assert.match(harita, /TON_RENK\[tone\]/, "marker rengi ton tablosundan gelmiyor");
});

// ---------------------------------------------------------------------------
// 10) I18N — IKI DIL, AYNI ANAHTAR KUMESI
// ---------------------------------------------------------------------------

function anahtarlar(nesne: unknown, on = ""): string[] {
  if (typeof nesne !== "object" || nesne === null) return [on];
  return Object.entries(nesne as Record<string, unknown>).flatMap(([k, v]) =>
    anahtarlar(v, on ? `${on}.${k}` : k)
  );
}

test("deviceRuntime anahtar kumesi iki dilde AYNI ve hicbiri bos degil", () => {
  const tr = JSON.parse(oku("src", "shared", "i18n", "resources", "tr.json"));
  const en = JSON.parse(oku("src", "shared", "i18n", "resources", "en.json"));
  assert.ok(tr.deviceRuntime && en.deviceRuntime, "deviceRuntime bolumu yok");
  assert.deepEqual(
    anahtarlar(tr.deviceRuntime).sort(),
    anahtarlar(en.deviceRuntime).sort(),
    "ayrisan dilde ekrana ham anahtar duser"
  );
  for (const [dil, kok] of [
    ["tr", tr.deviceRuntime],
    ["en", en.deviceRuntime]
  ] as const) {
    for (const yol of anahtarlar(kok)) {
      const deger = yol.split(".").reduce<any>((acc, k) => acc?.[k], kok);
      assert.equal(typeof deger, "string", `${dil}: ${yol} metin degil`);
      assert.ok(deger.trim().length > 0, `${dil}: ${yol} bos`);
    }
  }
});

test("alti durumun etiketi iki dilde de var", () => {
  const tr = JSON.parse(oku("src", "shared", "i18n", "resources", "tr.json"));
  const en = JSON.parse(oku("src", "shared", "i18n", "resources", "en.json"));
  for (const key of DEVICE_RUNTIME_KEYS) {
    const durumNesnesi = normalizeDeviceRuntime({
      runtime: kayit({
        connection_state:
          key === "LATE" ? "smart_idle" : key.toLowerCase() === "comm_lost" ? "lost" : key.toLowerCase(),
        report_late: key === "LATE"
      }),
      nowMs: SIMDI
    });
    if (durumNesnesi.key !== key) continue;
    const yol = durumNesnesi.labelKey.split(".");
    assert.equal(typeof yol.reduce<any>((a, k) => a?.[k], tr), "string", `tr: ${durumNesnesi.labelKey}`);
    assert.equal(typeof yol.reduce<any>((a, k) => a?.[k], en), "string", `en: ${durumNesnesi.labelKey}`);
  }
});

test("kullanilan her deviceRuntime anahtari sozlukte var", () => {
  const tr = JSON.parse(oku("src", "shared", "i18n", "resources", "tr.json"));
  const kaynaklar = [
    okuSrc("components", "RuntimeStateChip.tsx"),
    okuSrc("components", "DialInCountdown.tsx"),
    okuSrc("features", "device-detail", "DeviceRuntimePanel.tsx"),
    okuSrc("features", "dashboard", "DashboardFilterBar.tsx")
  ].join("\n");
  const bulunan = [...kaynaklar.matchAll(/"(deviceRuntime\.[a-zA-Z0-9_.]+)"/g)].map((m) => m[1]);
  assert.ok(bulunan.length > 0, "hic anahtar bulunamadi — desen kaydi");
  for (const anahtar of new Set(bulunan)) {
    const deger = anahtar.split(".").reduce<any>((a, k) => a?.[k], tr);
    assert.equal(typeof deger, "string", `${anahtar} tr.json'da yok`);
  }
});
