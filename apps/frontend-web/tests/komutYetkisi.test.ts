/**
 * KOMUTLAR SEKMESI — gorunur ama yetkisizde KILITLI.
 *
 * ONCEDEN: sekme `show: canCommand` ile diziden DUSUYORDU, yani operator
 * cihazda hangi komutlarin oldugunu HIC gormuyordu. Panelin icindeki
 * "yetkiniz yok" bloku de bu yuzden OLU KODDU — oraya hicbir zaman
 * gelinmiyordu.
 *
 * KILIT GEREKCESI SLUG BAZINDA. Gruba bakan bir kural YALAN SOYLERDI:
 * `trigger_config_download` backend'de installer-only ama bu dosyada
 * "general" grubunda duruyor. Engineer onu ACIK gorur, basar ve 403 yer —
 * "acik olan basilabilir" vaadi ilk tiklamada cokerdi.
 */
import assert from "node:assert/strict";
import { test } from "node:test";
import { readFileSync } from "node:fs";
import { join } from "node:path";

import { CONFIG_ONLY_SLUGS } from "../src/features/device-detail/commandScopes";

const KOK = join(process.cwd(), "..", "..");
const PANEL = readFileSync(
  join(process.cwd(), "src", "features", "device-detail", "DeviceCommandsPanel.tsx"),
  "utf8"
);
const SAYFA = readFileSync(
  join(process.cwd(), "src", "features", "device-detail", "DeviceDetailPage.tsx"),
  "utf8"
);

test("installer-only slug kumesi BACKEND ile birebir ayni", () => {
  // Ayrisirsa kilit gerekcesi yalan soyler: kullanici acik bir buton gorup
  // 403 yer. Kaynak: api/devices.py `_CONFIG_COMMAND_SLUGS`.
  const py = readFileSync(
    join(KOK, "apps", "backend-api", "app", "api", "devices.py"),
    "utf8"
  );
  const blok = py.slice(
    py.indexOf("_CONFIG_COMMAND_SLUGS = frozenset("),
    py.indexOf(")", py.indexOf("_CONFIG_COMMAND_SLUGS = frozenset("))
  );
  const backend = new Set([...blok.matchAll(/"([a-z0-9_]+)"/g)].map((m) => m[1]));
  assert.ok(backend.size >= 5, `backend kumesi okunamadi (${backend.size})`);
  assert.deepEqual(
    [...CONFIG_ONLY_SLUGS].sort(),
    [...backend].sort(),
    "frontend aynasi backend ile ayrismis"
  );
});

test("KRITIK: trigger_config_download kilitli sayilir", () => {
  // Bu slug frontend'de "general" grubunda; gruba bakan bir kural onu ACIK
  // birakirdi. Regresyon kapisi.
  assert.ok(CONFIG_ONLY_SLUGS.has("trigger_config_download"));
  assert.match(PANEL, /group: "general" \}/, "grup yapisi degismis");
});

test("kilit karari SLUG'a gore, gruba gore DEGIL", () => {
  assert.match(PANEL, /CONFIG_ONLY_SLUGS\.has\(slug\)/, "slug bazli kontrol yok");
  assert.doesNotMatch(
    PANEL,
    /key === "config" && !canConfig\) return null/,
    "config grubu hala gizleniyor"
  );
});

test("sekme HERKESE gorunur", () => {
  assert.match(
    SAYFA,
    /key: "commands", icon: "terminal", show: true/,
    "komut sekmesi hala yetkiye bagli gizleniyor"
  );
});

test("icerik kapisi yetkisizde de paneli cizer", () => {
  assert.match(SAYFA, /activeTab === "commands" && token \?/);
  assert.doesNotMatch(SAYFA, /activeTab === "commands" && canCommand/);
});

test("CONFIG sekmesi DEGISMEDI (kapsam kaymasi yok)", () => {
  // Bu istekte yalnizca KOMUTLAR sekmesi acildi. Config sekmesini de acmak
  // icerik kapisini (`activeTab === "config" && canConfig`) bozar ve
  // engineer BOS bir sekme gorurdu.
  assert.match(SAYFA, /\{ key: "config", icon: "tune", show: canConfig \}/);
  assert.match(SAYFA, /activeTab === "config" && canConfig/);
});

test("SEKME SIRASI korundu", () => {
  // `show:` sarti SEKME dizisine daraltir; dosyada ayrica sinyal grubu
  // dizisi de var ({ key: "protection", icon: "shield" } gibi) ve onu da
  // yakalayan bir desen yanlis siralama raporlardi.
  const sira = [...SAYFA.matchAll(/\{\s*key: "([a-zA-Z]+)", icon: "[a-z_]+", show:/g)].map(
    (m) => m[1]
  );
  assert.deepEqual(
    sira.slice(0, 3),
    ["overview", "connection", "all"],
    `sekme sirasi degismis: ${sira.join(",")}`
  );
  // Komutlar sekmesi yerinde mi (config'ten once)?
  assert.ok(sira.indexOf("commands") < sira.indexOf("config"), "komut/config sirasi bozulmus");
});

test("yetkisizde GORUNUR gerekce var (title'a guvenilmiyor)", () => {
  assert.match(PANEL, /device-cmd-locked/);
  assert.match(PANEL, /deviceDetail\.commands\.readOnly/);
  const css = readFileSync(join(process.cwd(), "src", "styles.css"), "utf8");
  assert.match(css, /\.device-cmd-locked\s*\{/, "banner CSS'i yok");
  assert.match(css, /\.device-cmd-btn\.is-locked\s*\{/, "kilitli buton stili yok");
});

test("runCommand'da IKINCI kapi duruyor", () => {
  // `disabled` DOM'dan silinebilir; ucuncu ve gercek kapi backend.
  assert.match(PANEL, /if \(!canCommand \|\| onDeviceCommand == null\) return;/);
});

test("iki dilde metin var", () => {
  for (const lang of ["tr", "en"]) {
    const d = JSON.parse(
      readFileSync(join(process.cwd(), "src", "shared", "i18n", "resources", `${lang}.json`), "utf8")
    );
    for (const k of ["readOnly", "lockedInstaller"]) {
      assert.ok(
        typeof d.deviceDetail.commands[k] === "string" && d.deviceDetail.commands[k].length > 10,
        `${lang}: commands.${k} yok`
      );
    }
  }
});
