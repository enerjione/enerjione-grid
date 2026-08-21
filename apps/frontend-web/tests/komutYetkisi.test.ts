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

test("komutlar HERKESE gorunur (Baglanti ve Komutlar sekmesinde)", () => {
  // Komutlar artik AYRI bir sekme DEGIL: `Baglanti` sekmesiyle birlesti.
  // "Cihaz su an konusuyor mu" ile "ona ne yollayabilirim" ayni anin
  // sorulari; ikisi arasinda sekme degistirmek gerekiyordu.
  //
  // DEGISMEYEN SEY: gorunurluk yetkiye bagli DEGIL. Sekmeyi (ya da paneli)
  // gizlemek "boyle bir sey yok" demekti; operator cihaza ne
  // yapilabilecegini bilmeden calisiyordu.
  assert.match(
    SAYFA,
    /\{ key: "connection", icon: "wifi", show: true \}/,
    "birlesik sekme yetkiye bagli gizleniyor"
  );
  assert.doesNotMatch(
    SAYFA,
    /key: "commands", icon: "terminal"/,
    "ayri komut sekmesi hala duruyor"
  );
});

test("icerik kapisi yetkisizde de paneli cizer", () => {
  // Kapi YALNIZCA `token` — yetki DEGIL. Panel her zaman cizilir, butonlar
  // kilitli olur ve neden kilitli oldugu panelde YAZAR.
  const i = SAYFA.indexOf('activeTab === "connection"');
  assert.ok(i > 0, "birlesik sekme dali yok");
  const blok = SAYFA.slice(i, i + 700);
  assert.match(blok, /\{token \? \(\s*<DeviceCommandsPanel/, "komut paneli cizilmiyor");
  assert.doesNotMatch(
    blok,
    /canCommand \? \(\s*<DeviceCommandsPanel/,
    "panel yetkiye bagli gizleniyor"
  );
  // Yetki bayraklari panele GECIRILIR (kilit orada cozulur).
  assert.match(blok, /canCommand=\{canCommand\}/);
  assert.match(blok, /canConfig=\{canConfig\}/);
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
  // Komutlar ARTIK AYRI SEKME DEGIL — `connection` ile birlesti.
  assert.ok(!sira.includes("commands"), "ayri komut sekmesi geri gelmis");
  assert.ok(sira.indexOf("connection") < sira.indexOf("config"), "sekme sirasi bozulmus");
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

// ---------------------------------------------------------------------------
// YERLESIM — solda "ne yapabilirim", sagda "ne oldu"
// ---------------------------------------------------------------------------

test("komut gruplari KAPALI baslar", () => {
  // Onceden `alarm_reset` acik geliyordu ve panel acilir acilmaz butonlarla
  // doluyordu; operatorun ilk gordugu sey taranabilir bir liste degil, bir
  // buton yiginiydi.
  assert.match(
    PANEL,
    /useState<Record<string, boolean>>\(\{\}\)/,
    "bir grup hala acik basliyor"
  );
});

test("gecmis SAG KOLONDA — komut listesinin altinda DEGIL", () => {
  // Onceden gonderilen komutun sonucunu gormek icin sayfayi asagi
  // kaydirmak gerekiyordu; gruplar acilinca mesafe daha da uzuyordu.
  assert.match(PANEL, /device-cmd-cols/, "iki kolon sarmalayicisi yok");
  const iEylem = PANEL.indexOf("device-cmd-col-actions");
  const iLog = PANEL.indexOf("device-cmd-col-log");
  assert.ok(iEylem > 0 && iLog > iEylem, "kolon sirasi bozuk");
  assert.ok(
    PANEL.indexOf("device-cmd-history-section") > iLog,
    "gecmis sag kolonun icinde degil"
  );
});

test("KALICI aciklama notu kaldirildi", () => {
  // Dort satirlik teknik metin her acilista listenin ustunde duruyordu;
  // her satirin durum rozeti ayni bilgiyi TEK KELIMEYLE veriyor.
  assert.ok(!PANEL.includes("asyncNote"), "not blogu hala ciziliyor");
  for (const lang of ["tr", "en"]) {
    const d = JSON.parse(
      readFileSync(join(process.cwd(), "src", "shared", "i18n", "resources", `${lang}.json`), "utf8")
    );
    assert.ok(
      !("asyncNote" in d.deviceDetail.commands),
      `${lang}: kullanilmayan asyncNote anahtari duruyor`
    );
  }
});

test("dar ekranda TEK kolona duser", () => {
  // Yan yana sikistirmak iki tarafi da okunmaz yapar.
  const css = readFileSync(join(process.cwd(), "src", "styles.css"), "utf8");
  const i = css.indexOf(".device-cmd-cols {");
  assert.ok(i > 0, "kolon stili yok");
  assert.match(
    css.slice(i, i + 900),
    /@media \(max-width: 1200px\)[\s\S]*device-cmd-cols \{ grid-template-columns: 1fr/,
    "dar ekran kirilma noktasi yok"
  );
});
