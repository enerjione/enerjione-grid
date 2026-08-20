/**
 * DURUM IPUCU — "bu renk ne demek?" her yerde cevaplanabilir olmali.
 *
 * NE KORUNUYOR
 * ------------
 * Durum cogu ekranda TEK BIR RENKLI NOKTA. Renk kendiliginden okunmaz ve bu
 * palette sezgiye AYKIRI bir uye var: `Smart Bekleme` MAVI ama SAGLIKLI
 * (Horstmann Smart modda modemini bilerek kapatir). Operator mavi noktayi
 * "ariza" sanarsa saglikli filoyu kovalar; tersi olursa gercek arizayi
 * kacirir. Ikisi de ekranda FARK EDILMEZ.
 *
 * Bu yuzden her durumun ustune gelince (a) adi, (b) hangi kovaya girdigi
 * (saglikli/bozulmus/arizali), (c) bir cumlelik anlami gorunmeli — ve bu
 * ekranlarin BIRINDE degil HEPSINDE gecerli olmali.
 *
 * React kosucusu yok (bkz. tests/run.mjs), o yuzden baglanti kaynak
 * metninden okunuyor: hangi ekran rengi kullaniyorsa ipucunu da tasimali.
 */
import assert from "node:assert/strict";
import { test } from "node:test";
import { readFileSync } from "node:fs";
import { join } from "node:path";

import { DEVICE_RUNTIME_KEYS, normalizeDeviceRuntime } from "../src/shared/deviceRuntimeState";
import type { DeviceRuntimeStateKey } from "../src/shared/deviceRuntimeState";

const oku = (...p: string[]) => readFileSync(join(process.cwd(), ...p), "utf8");
const okuSrc = (...p: string[]) => oku("src", ...p);

const TR = JSON.parse(oku("src", "shared", "i18n", "resources", "tr.json"));
const EN = JSON.parse(oku("src", "shared", "i18n", "resources", "en.json"));

/** Her durum icin normalizerden cikan gercek nesne (uydurma degil). */
function durumlar() {
  const wire: Record<DeviceRuntimeStateKey, Record<string, unknown> | null> = {
    ONLINE: { connection_state: "online" },
    SMART_IDLE: { connection_state: "smart_idle" },
    LATE: { connection_state: "smart_idle", report_late: true },
    RECOVERING: { connection_state: "recovering" },
    COMM_LOST: { connection_state: "lost" },
    LISTENER_ERROR: { connection_state: "listener_error" },
    UNKNOWN: { connection_state: "unknown" }
  };
  const now = Date.UTC(2026, 7, 20, 12, 0, 0);
  return DEVICE_RUNTIME_KEYS.map((k) => ({
    key: k,
    state: normalizeDeviceRuntime({
      runtime: { ...(wire[k] as object), updated_at: new Date(now - 30_000).toISOString() },
      legacyStatus: "offline",
      nowMs: now
    })
  }));
}

// ---------------------------------------------------------------------------
// 1. Metin butunlugu: hicbir durum "aciklamasiz renk" olarak kalmasin
// ---------------------------------------------------------------------------

test("her durumun IKI DILDE de bos olmayan aciklamasi var", () => {
  for (const { key, state } of durumlar()) {
    assert.equal(state.key, key, `${key} normalizerden farkli anahtar dondu`);
    const sonEk = state.labelKey.split(".").pop() as string;
    for (const [ad, sozluk] of [["tr", TR], ["en", EN]] as const) {
      const aciklama = sozluk.deviceRuntime.stateHint?.[sonEk];
      assert.ok(
        typeof aciklama === "string" && aciklama.trim().length > 12,
        `${ad}: ${key} icin stateHint eksik/cok kisa — renk aciklamasiz kalir`
      );
    }
  }
});

test("her KOVA icin iki dilde de etiket var (rengi anlama ceviren parca)", () => {
  // Kutudaki rozet bunu basiyor: mavi de yesil de "Saglikli" der. Eksik bir
  // kova, ipucunda BOS bir rozet ya da ham anahtar ("deviceRuntime.kpi...")
  // gorunmesi demekti.
  const kovalar = new Set(durumlar().map((d) => d.state.bucket));
  assert.ok(kovalar.size >= 4, `beklenen dort kova, gelen: ${[...kovalar].join(",")}`);
  for (const kova of kovalar) {
    for (const [ad, sozluk] of [["tr", TR], ["en", EN]] as const) {
      const etiket = sozluk.deviceRuntime.kpi?.[kova];
      assert.ok(
        typeof etiket === "string" && etiket.trim().length > 0,
        `${ad}: '${kova}' kovasinin etiketi yok`
      );
    }
  }
});

test("SMART_IDLE mavi ve SAGLIKLI — ipucunun duzeltmesi gereken sezgi", () => {
  const d = durumlar().find((x) => x.key === "SMART_IDLE")!.state;
  assert.equal(d.tone, "blue");
  assert.equal(d.bucket, "healthy");
  // Aciklama "ariza degildir" demeli; renk sezgiye aykiri oldugu icin
  // metnin bunu ACIKCA soylemesi gerekiyor.
  assert.match(TR.deviceRuntime.stateHint.smartIdle, /arıza değildir|sağlıklı/i);
  assert.match(EN.deviceRuntime.stateHint.smartIdle, /not a fault|healthy/i);
});

// ---------------------------------------------------------------------------
// 2. Kablo: ipucu ortak bilesende, ve rengi kullanan HER ekranda
// ---------------------------------------------------------------------------

test("nokta ve rozet ipucu kancasini kullaniyor", () => {
  const chip = okuSrc("components", "RuntimeStateChip.tsx");
  assert.match(chip, /useRuntimeTip/, "ortak bilesen ipucunu kullanmiyor");
  // Nokta EN YAYGIN oge; ipucusuz kalirsa listelerin tamami aciklamasiz olur.
  const nokta = chip.slice(chip.indexOf("export function RuntimeStateDot"));
  assert.match(nokta.slice(0, 700), /useRuntimeTip/, "RuntimeStateDot ipucusuz");
});

test("durum icin ham `title` ozniteligine DONULMEDI", () => {
  // Isletim sisteminin `title` kutusunda RENK YOK ve ~1sn gecikmeyle acilir;
  // "rengi acikliyor" denemez. Geri donulurse bu test dusmeli.
  const chip = okuSrc("components", "RuntimeStateChip.tsx");
  assert.doesNotMatch(
    chip,
    /title=\{[^}]*labelKey/,
    "durum adi yeniden ham `title` olarak veriliyor"
  );
});

test("rengi DOGRUDAN kullanan her ekran ipucunu da tasiyor", () => {
  // `runtimeToneClass` bir ogeye renk verir. Renk verip ipucu vermemek tam
  // olarak kullanicinin sikayet ettigi durum: "bu renk ama ne demek?".
  const ekranlar = [
    ["components", "HeaderSearch.tsx"],
    ["features", "device-detail", "DeviceSidebar.tsx"],
    ["features", "devices", "DeviceManagementPanel.tsx"],
    ["features", "grid", "GridManagementPanel.tsx"],
    ["features", "map", "DeviceMapTab.tsx"]
  ];
  for (const yol of ekranlar) {
    const kaynak = okuSrc(...yol);
    assert.match(
      kaynak,
      /RuntimeTip|RuntimeStateChip|RuntimeStateDot/,
      `${yol.join("/")}: renk var ama ipucu tasiyan bilesen yok`
    );
  }
});

test("ipucu kutusu portal ile ciziliyor (kirpilma yapisal olarak imkansiz)", () => {
  // Liste govdeleri ve Leaflet panelleri `overflow: hidden`; kutu iceride
  // kalirsa KIRPILIR ve tam da dar yerlerde okunamaz olur.
  const tip = okuSrc("components", "RuntimeTooltip.tsx");
  assert.match(tip, /createPortal/);
  assert.match(tip, /document\.body/);
});

test("ipucu klavye ile de acilir ve Escape ile kapanir", () => {
  const tip = okuSrc("components", "RuntimeTooltip.tsx");
  assert.match(tip, /onFocus/, "yalnizca fare ile aciliyor");
  assert.match(tip, /onBlur/);
  assert.match(tip, /Escape/, "klavye kullanicisi kutuda sikisir");
});

test("ipucu kutusu rengin KENDISINI tasiyor", () => {
  // Renksiz bir kutu adi soyler ama rengi ACIKLAMAZ; kullanicinin sorusu
  // "bu renk ne demek" oldugu icin renk kutuda gorunmeli.
  const css = oku("src", "styles.css");
  const blok = css.slice(css.indexOf(".runtime-tip {"), css.indexOf(".runtime-tip__metin"));
  assert.match(blok, /var\(--rt\)/, "kutu durumun tonunu hic kullanmiyor");
  assert.match(css, /\.runtime-tip__kova[\s\S]{0,300}?var\(--rt\)/, "kova rozeti renksiz");
});

test("ipucu modal ve toast'in USTUNDE", () => {
  // Rozet ve nokta modallerin icinde de var; altta kalirsa ipucu tam da
  // ihtiyac duyuldugu yerde gorunmez olurdu.
  const css = oku("src", "styles.css");
  const blok = css.slice(css.indexOf(".runtime-tip {"), css.indexOf(".runtime-tip--altta"));
  const z = /z-index:\s*(\d+)/.exec(blok);
  assert.ok(z, "runtime-tip icin z-index yok");
  assert.ok(Number(z![1]) > 10000, `z-index ${z![1]} — modal (10000) altinda kalir`);
});

test("ipucu FONT SUBSET'INE yeni ikon SOKMUYOR", () => {
  // Yeni bir ikon adi eklenirse font yeniden uretilmedigi surece ekranda
  // ikon yerine ADI duz metin olarak cikar (bkz. iconSubset.test.ts).
  const tip = okuSrc("components", "RuntimeTooltip.tsx");
  assert.doesNotMatch(tip, /material-symbols-outlined/, "ipucu ikon kullaniyor — subset tazelenmeli");
});

test("nokta ODAKLANABILIR DEGIL — bir `<button>` icinde duruyor", () => {
  // `DeviceRowButton` ve `DeviceLineTree` noktayi `<button>` ICINDE cizer.
  // Butonun icine odaklanabilir bir oge koymak gecersiz HTML'dir ve klavye
  // gezintisini bozar; ustelik 600 cihazlik listede 600 FAZLADAN tab duragi
  // yaratirdi. Durum adi zaten `aria-label` ile veriliyor.
  const tip = okuSrc("components", "RuntimeTooltip.tsx");
  assert.match(tip, /focusable = false/, "odaklanabilirlik varsayilan olarak ACIK");

  const chip = okuSrc("components", "RuntimeStateChip.tsx");
  const nokta = chip.slice(
    chip.indexOf("export function RuntimeStateDot"),
    chip.indexOf("export function RuntimeStateChip")
  );
  assert.doesNotMatch(nokta, /focusable:\s*true/, "nokta odaklanabilir yapilmis");
  assert.match(nokta, /aria-label/, "nokta ekran okuyucuya adini vermiyor");

  // Rozet TEK BASINA duruyor -> klavye ile ulasilabilir olmali.
  const rozet = chip.slice(chip.indexOf("export function RuntimeStateChip"));
  assert.match(rozet, /focusable:\s*true/, "rozet klavyeye kapali");
});

test("noktanin bulundugu satirlar hala tek bir buton", () => {
  // Regresyon kapisi: biri noktaya tekrar `tabIndex` eklerse burasi dusmeli.
  for (const yol of [
    ["features", "devices", "DeviceRowButton.tsx"],
    ["features", "devices", "DeviceLineTree.tsx"]
  ]) {
    const kaynak = okuSrc(...yol);
    assert.match(kaynak, /RuntimeStateDot/, `${yol.join("/")}: nokta yok`);
    assert.doesNotMatch(
      kaynak,
      /RuntimeStateDot[^/]*focusable/,
      `${yol.join("/")}: buton icindeki nokta odaklanabilir yapilmis`
    );
  }
});
