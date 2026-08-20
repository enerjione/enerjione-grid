/**
 * BACKEND OKUMA UCU <-> ARAYUZ SOZLESMESI (`runtime_health`).
 *
 * NE KORUNUYOR
 * ------------
 * Bu ozellik bir kez tam olarak BURADAN kirildi: alim ucu calisiyor, tablo
 * doluyor, arayuz `item.runtime_health` okuyor — ama backend cihaz yanitina
 * o alani KOYMUYORDU. Sonuc sessiz: her cihaz `runtimeHealth = null` gorup
 * eski davranisa dusuyor, `smart_idle` / `recovering` / gecikme HIC
 * gorunmuyor ve hicbir yerde hata cikmiyor. Iki tarafi da tek basina test
 * eden bir paket bunu YAKALAYAMAZ; kirilan sey aradaki AD ESLESMESIDIR.
 *
 * Bu yuzden burada iki uc birlikte okunuyor. Backend kaynagini okuma
 * yontemi yeni degil: `dialInGatewayUyumluluk.test.ts` de gateway uyumluluk
 * tablosunu ayni sekilde okuyor (React kosucusu yok, bkz. tests/run.mjs).
 *
 * Sozlesme: `docs/gateway-contract/device-health-api-pr33.md`
 * (Gateway PR #33 — HENUZ ACIK).
 */
import assert from "node:assert/strict";
import { test } from "node:test";
import { readFileSync } from "node:fs";
import { join } from "node:path";

import { normalizeDeviceRuntime } from "../src/shared/deviceRuntimeState";
import type { DeviceRuntimeHealthRecord } from "../src/shared/deviceRuntimeState";

const oku = (...p: string[]) => readFileSync(join(process.cwd(), ...p), "utf8");

const BACKEND_SEMA = oku("..", "backend-api", "app", "schemas", "device.py");
const API_TS = oku("src", "shared", "api.ts");
const RUNTIME_TS = oku("src", "shared", "deviceRuntimeState.ts");

/** Bir python sinif govdesindeki `ad: tip` alanlarini cikar.
 *
 *  Yorumlar (`#:`), `model_config` ve validator'lar disarida kalir; sadece
 *  wire'a cikan alanlar sayilir. */
function pythonAlanlari(kaynak: string, sinif: string): string[] {
  const bas = kaynak.indexOf(`class ${sinif}(`);
  assert.ok(bas >= 0, `${sinif} backend semasinda bulunamadi`);
  const kalan = kaynak.slice(bas + 1);
  const son = kalan.search(/\nclass \w+\(/);
  const govde = son >= 0 ? kalan.slice(0, son) : kalan;
  const alanlar: string[] = [];
  for (const satir of govde.split("\n")) {
    // Tam 4 bosluk girinti = sinif govdesi (metot ici degil).
    const m = /^ {4}([a-z_][a-z0-9_]*)\s*:\s*[^=\s]/.exec(satir);
    if (!m) continue;
    if (m[1] === "model_config") continue;
    alanlar.push(m[1]);
  }
  return alanlar;
}

/** Bir TS `type` govdesindeki alan adlarini cikar. */
function tsAlanlari(kaynak: string, tip: string): string[] {
  const bas = kaynak.indexOf(`export type ${tip} = `);
  assert.ok(bas >= 0, `${tip} arayuz tipinde bulunamadi`);
  const govde = kaynak.slice(bas, kaynak.indexOf("};", bas));
  const alanlar: string[] = [];
  for (const satir of govde.split("\n")) {
    const m = /^\s{2}([a-z_][a-z0-9_]*)\??\s*:/.exec(satir);
    if (m) alanlar.push(m[1]);
  }
  return alanlar;
}

// ---------------------------------------------------------------------------
// 1. Alan kumeleri: backend'in YAYDIGI her alani arayuz TANIYOR mu
// ---------------------------------------------------------------------------

test("backend `DeviceRuntimeHealthRead` alanlarinin TAMAMINI arayuz taniyor", () => {
  const backend = pythonAlanlari(BACKEND_SEMA, "DeviceRuntimeHealthRead");
  const arayuz = new Set([
    ...tsAlanlari(RUNTIME_TS, "DeviceHealthWire"),
    // `DeviceRuntimeHealthRecord` = wire + backend'e ait iki alan.
    "gateway_code",
    "updated_at"
  ]);

  assert.ok(backend.length >= 15, `backend semasi cok kucuk: ${backend.length}`);
  const bilinmeyen = backend.filter((a) => !arayuz.has(a));
  assert.deepEqual(
    bilinmeyen,
    [],
    `backend bu alanlari yayiyor ama arayuz tanimiyor: ${bilinmeyen.join(", ")}`
  );
});

test("arayuzun bekledigi her alani backend GERCEKTEN yayiyor", () => {
  const backend = new Set(pythonAlanlari(BACKEND_SEMA, "DeviceRuntimeHealthRead"));
  const wire = tsAlanlari(RUNTIME_TS, "DeviceHealthWire");

  // Ters yon de onemli: arayuz `next_expected_report_epoch` bekleyip backend
  // gondermezse geri sayim SESSIZCE hic gorunmez — hicbir hata cikmadan.
  const eksik = wire.filter((a) => !backend.has(a));
  assert.deepEqual(
    eksik,
    [],
    `arayuz bu alanlari okuyor ama backend yaymiyor: ${eksik.join(", ")}`
  );
  for (const ad of ["gateway_code", "updated_at"]) {
    assert.ok(backend.has(ad), `backend '${ad}' yaymiyor`);
  }
});

test("gateway'in IC defteri arayuze SIZMIYOR", () => {
  // Bayat-yazma / uzlastirma alanlari cihazin durumu degil; ayrica
  // `gateway_instance_id` gateway'in kalici ic kimligi ve `/public` ucundan
  // disari cikmamali.
  const backend = new Set(pythonAlanlari(BACKEND_SEMA, "DeviceRuntimeHealthRead"));
  for (const ad of [
    "gateway_instance_id",
    "boot_id",
    "sequence",
    "snapshot_id",
    "snapshot_batch_index"
  ]) {
    assert.ok(!backend.has(ad), `ic defter alani '${ad}' okuma semasina girmis`);
  }
});

// ---------------------------------------------------------------------------
// 2. Kablo: `DeviceRead` alani tasiyor ve mapper onu okuyor
// ---------------------------------------------------------------------------

test("backend `DeviceRead` semasi `runtime_health` tasiyor", () => {
  const alanlar = pythonAlanlari(BACKEND_SEMA, "DeviceRead");
  assert.ok(
    alanlar.includes("runtime_health"),
    "DeviceRead `runtime_health` tasimiyor — arayuz sonsuza kadar eski davranista kalir"
  );
  assert.match(
    BACKEND_SEMA,
    /runtime_health:\s*DeviceRuntimeHealthRead\s*\|\s*None\s*=\s*None/,
    "alan zorunlu ya da farkli tipte: `annotate`dan gecmeyen uclar (or. /internal/devices) patlar"
  );
});

test("`fetchDevices` mapper'i `runtime_health` -> `runtimeHealth` esliyor", () => {
  assert.match(
    API_TS,
    /runtimeHealth:\s*item\.runtime_health\s*\?\?\s*null/,
    "mapper alani okumuyor"
  );
});

// ---------------------------------------------------------------------------
// 3. Temsili yukler: backend'in URETTIGI seklin TAMAMI normalizerden gecer
// ---------------------------------------------------------------------------

const SIMDI = Date.UTC(2026, 7, 20, 12, 0, 0);

/** Backend `DeviceRuntimeHealthRead` ciktisinin BIREBIR sekli.
 *
 *  Elle yazilmis bir "ornek" degil: alan kumesi yukaridaki testlerle backend
 *  semasina bagli. `updated_at` OFFSET TASIR — offset'siz gelseydi tarayici
 *  onu yerel saat sanardi (UTC+3'te 3 saat kayma) ve her gozlem surekli
 *  bayat sayilirdi. */
function backendYuku(
  over: Partial<DeviceRuntimeHealthRecord> = {}
): DeviceRuntimeHealthRecord {
  return {
    device_code: "SN2-001",
    gateway_code: "GW-1",
    connection_state: "online",
    connected: true,
    reachable: true,
    configured_session_policy: "auto",
    effective_session_policy: "smart",
    operation_mode: "smart",
    dial_in_interval_min: 720,
    next_expected_report_epoch: 1755691200,
    report_overdue_sec: 0,
    report_late: false,
    last_valid_contact_epoch: 1755690000,
    last_frame_epoch: 1755690000,
    ip_probe_status: "unknown",
    tcp_probe_status: "connecting",
    last_probe_epoch: null,
    ip_endpoint_type: "listening",
    updated_at: new Date(SIMDI - 30_000).toISOString(),
    ...over
  };
}

const durum = (rt: DeviceRuntimeHealthRecord | null, legacy: "online" | "offline" = "offline") =>
  normalizeDeviceRuntime({ runtime: rt, legacyStatus: legacy, nowMs: SIMDI });

test("A) online -> ONLINE / saglikli, kaynak gateway", () => {
  const d = durum(backendYuku({ connection_state: "online" }));
  assert.equal(d.key, "ONLINE");
  assert.equal(d.bucket, "healthy");
  assert.equal(d.source, "gateway");
  assert.equal(d.stale, false);
});

test("B) smart_idle + report_late=false -> SMART_IDLE / SAGLIKLI (mavi)", () => {
  // Telemetri sussa bile (`legacy = offline`) uyuyan cihaz ariza DEGIL.
  const d = durum(backendYuku({ connection_state: "smart_idle", connected: false, reachable: false }));
  assert.equal(d.key, "SMART_IDLE");
  assert.equal(d.bucket, "healthy");
  assert.equal(d.tone, "blue");
  assert.equal(d.reportLate, false);
});

test("C) smart_idle + report_late=true -> LATE, kanonik durum HALA smart_idle", () => {
  const d = durum(
    backendYuku({ connection_state: "smart_idle", report_late: true, report_overdue_sec: 360 })
  );
  assert.equal(d.key, "LATE");
  assert.equal(d.bucket, "degraded");
  assert.equal(d.reportLate, true);
  assert.equal(d.rawState, "smart_idle", "gecikme kanonik durumu ezdi");
});

test("D) recovering -> RECOVERING / bozulmus", () => {
  const d = durum(backendYuku({ connection_state: "recovering" }));
  assert.equal(d.key, "RECOVERING");
  assert.equal(d.bucket, "degraded");
});

test("E) lost -> COMM_LOST / arizali (legacy 'online' olsa bile)", () => {
  const d = durum(backendYuku({ connection_state: "lost" }), "online");
  assert.equal(d.key, "COMM_LOST");
  assert.equal(d.bucket, "unhealthy");
  assert.equal(d.source, "gateway", "gateway'in karari telemetriyle ezildi");
});

test("F) runtime_health = null -> ESKI davranis, uydurma durum YOK", () => {
  const d = durum(null, "online");
  assert.equal(d.key, "ONLINE");
  assert.equal(d.source, "legacy");
  assert.equal(d.rawState, null);
  assert.equal(d.reportLate, false);
});

test("null epoch 1970'e DONMEZ — geri sayim yoksa yok", () => {
  const d = durum(
    backendYuku({ connection_state: "smart_idle", next_expected_report_epoch: null })
  );
  assert.equal(d.key, "SMART_IDLE");
  // Normalizer epoch'u yorumlamaz; geri sayimi ureten taraf `null` gormeli.
  assert.equal(backendYuku({ next_expected_report_epoch: null }).next_expected_report_epoch, null);
});

test("offset TASIYAN `updated_at` bayat sayilmaz", () => {
  // Backend UTC-aware yaziyor; offset'siz gelseydi bu gozlem UTC+3'te 3 saat
  // eski gorunur ve arayuz kalici olarak eski davranisa duserdi.
  const taze = new Date(SIMDI - 60_000).toISOString();
  assert.ok(taze.endsWith("Z"));
  const d = durum(backendYuku({ connection_state: "smart_idle", updated_at: taze }));
  assert.equal(d.stale, false);
  assert.equal(d.key, "SMART_IDLE");
});
