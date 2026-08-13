/**
 * ROL ERISIM SOZLESMESI — menu ile sayfa ayni seyi soylemek zorunda.
 *
 * Bir sayfanin gorunurlugu bu depoda UC yerde birden yasiyor: backend'in rol
 * listesi, `EngineeringNav.canSee` ve buradaki `tabModel` listeleri. Uclu
 * tekrar bilincli (her kati kendi kapisini tutar) ama sessiz de: biri
 * unutulunca menude gorunen bir sayfa acilinca 403 aliyor ya da tam tersi
 * yetki sizinti gibi duruyor. Testin isi o sapmayi gurultulu yapmak.
 */
import assert from "node:assert/strict";
import { test } from "node:test";

import {
  canAccessRoute,
  defaultEngineeringPage,
  visibleTabs,
  HOME_KEY,
  type Tab,
} from "../src/features/tabs/tabModel";

const eng = (page: string) =>
  ({ kind: "engineering", page } as unknown as Parameters<typeof canAccessRoute>[0]);

test("guvenlik duvari YALNIZCA installer — gorme de dahil", () => {
  // Kural listesi cihazin ag yuzeyi (hangi port disariya acik, hangi adres
  // gecebiliyor). Tek basina bir kesif haritasi; "sadece bakiyor" diye
  // dagitilmaz. Ag Ayarlari da yalnizca installer'da.
  assert.equal(canAccessRoute(eng("firewall"), "installer"), true);
  assert.equal(canAccessRoute(eng("firewall"), "engineer"), false);
  assert.equal(canAccessRoute(eng("firewall"), "ops_manager"), false);
  assert.equal(canAccessRoute(eng("firewall"), "operator"), false);
});

test("Operasyon Yoneticisi muhendislige girince KULLANICILAR acilir", () => {
  // Onceden sabit "devices" aciliyordu; ops_manager o sayfayi hic goremedigi
  // icin sekme `visibleTabs` tarafindan aninda eleniyor ve menuye basmak
  // hicbir sey yapmiyor gibi gorunuyordu.
  const sayfa = defaultEngineeringPage("ops_manager");
  assert.equal(sayfa, "users");
  assert.equal(
    canAccessRoute(eng(sayfa), "ops_manager"),
    true,
    "acilis sayfasi rolun ERISEBILDIGI bir sayfa olmak zorunda",
  );
});

test("acilis sayfasi her rol icin erisilebilir olmali", () => {
  for (const rol of ["installer", "engineer", "ops_manager"] as const) {
    const sayfa = defaultEngineeringPage(rol);
    assert.equal(
      canAccessRoute(eng(sayfa), rol),
      true,
      `${rol} icin acilis sayfasi "${sayfa}" erisilemez — sekme aninda elenir`,
    );
  }
});

test("Operasyon Yoneticisi kullanicilar sayfasini gorur", () => {
  // Isi ekip yonetimi: operator hesaplarini gorup olusturmali.
  assert.equal(canAccessRoute(eng("users"), "ops_manager"), true);
  assert.equal(canAccessRoute(eng("responsibility-areas"), "ops_manager"), true);
});

test("elenen sekme sonrasi ana sayfa her zaman kalir", () => {
  // ops_manager'in gizli bir sekmesi (firewall) persist'ten gelirse liste
  // bosalabilir; ana sayfa ayakta kalmali yoksa arayuz bos ekrana duser.
  const tabs = [
    { key: "eng:firewall", route: { kind: "engineering", page: "firewall" } },
  ] as unknown as Tab[];
  const gorunur = visibleTabs(tabs, "ops_manager");
  assert.equal(gorunur.some((t) => t.key === HOME_KEY), true);
  assert.equal(
    gorunur.some((t) => t.key === "eng:firewall"),
    false,
    "guvenlik duvari sekmesi ops_manager'da elenmemis",
  );
});
