/**
 * Toast susturma + konum — DAVRANIS testleri.
 *
 * EN KRITIK KURAL
 * ---------------
 * Susturma YALNIZCA kendiliginden gelen bildirimleri (or. "2 yeni alarm
 * olustu") kapsar. Kullanicinin kendi eyleminin sonucu olan toast
 * ("kaydedildi", "kaydedilemedi", yetki/dogrulama hatasi) SUSTURULAMAZ:
 * susturulsaydi operator Kaydet'e basip hata aldigini HIC ogrenemez ve bu
 * sessiz veri kaybi gibi davranirdi.
 *
 * Bu dosya kaynakta metin ARAMIYOR: gercek ToastProvider derlenip
 * calistiriliyor ve URETTIGI AGAC inceleniyor (bkz. toastHarness.ts).
 */

import assert from "node:assert/strict";
import { test } from "node:test";

import { metinler, sinifBul, yukleToastModulu, type Dugum } from "./toastHarness";

// ToastProvider `window.setTimeout` kullaniyor (otomatik kapanma). Testte
// gercek zamanlayici istemiyoruz — surec acik kalmasin.
(globalThis as unknown as { window: unknown }).window = {
  setTimeout: () => 0,
  clearTimeout: () => undefined,
};

const M = await yukleToastModulu();

type Api = ReturnType<typeof M.useToast>;

/** Provider'i verilen proje ayarlariyla kurar; toast API'sini geri verir. */
function kur(ayarlar: Record<string, unknown>): { api: () => Api; kutu: () => Dugum | null } {
  M.sifirla();
  M.ayarlariKur(ayarlar);
  const yakalanan: { api?: Api } = {};
  const Cocuk = () => {
    yakalanan.api = M.useToast();
    return null;
  };
  M.kur(M.h(M.ToastProvider, null, M.h(Cocuk, null)));
  return {
    api: () => {
      assert.ok(yakalanan.api, "useToast context'ten cozulemedi");
      return yakalanan.api!;
    },
    kutu: () => sinifBul(M.agac(), "toast-container"),
  };
}

/** Ekranda duran toast sayisi. */
function toastSayisi(kutu: Dugum | null): number {
  if (!kutu) return 0;
  return kutu.children.filter((c) => {
    const s = typeof c.props.className === "string" ? c.props.className : "";
    return s.split(/\s+/).includes("toast");
  }).length;
}

// ---------------------------------------------------------------------------
// Susturmanin KAPSAMI
// ---------------------------------------------------------------------------

test("susturma ACIKKEN kullanici eyleminin sonucu olan toast HALA gosterilir", () => {
  // Bu testin dusmesi, kullanicinin "Kaydet" hatasini hic gormemesi demektir.
  const p = kur({ toast_muted: true });
  p.api().error("Proje ayarlari kaydedilemedi.");
  const kutu = p.kutu();
  assert.equal(
    toastSayisi(kutu),
    1,
    "susturma kullanici eyleminin sonucunu YUTTU — sessiz veri kaybi",
  );
  assert.ok(
    metinler(kutu).includes("Proje ayarlari kaydedilemedi."),
    `hata metni ekranda yok: ${JSON.stringify(metinler(kutu))}`,
  );
});

test("susturma ACIKKEN dogrulama/yetki uyarilari da gosterilir", () => {
  const p = kur({ toast_muted: true });
  p.api().warning("Yetkiniz yok.");
  p.api().success("Sinyal eklendi.");
  assert.equal(toastSayisi(p.kutu()), 2);
});

test("susturma ACIKKEN kendiliginden gelen bildirim GOSTERILMEZ", () => {
  const p = kur({ toast_muted: true });
  p.api().warning("2 yeni alarm olustu.", { spontaneous: true });
  assert.equal(toastSayisi(p.kutu()), 0, "susturma kendiliginden geleni durduramadi");
});

test("susturma KAPALIYKEN kendiliginden gelen bildirim gosterilir", () => {
  const p = kur({ toast_muted: false });
  p.api().warning("2 yeni alarm olustu.", { spontaneous: true });
  assert.equal(toastSayisi(p.kutu()), 1);
});

test("ayar HIC gelmemisken hicbir sey susturulmaz (mevcut davranis korunur)", () => {
  // Guncelleyen sahada kolonlar NULL doner; hicbir sey degismemeli.
  const p = kur({});
  p.api().warning("2 yeni alarm olustu.", { spontaneous: true });
  p.api().error("Kaydedilemedi.");
  assert.equal(toastSayisi(p.kutu()), 2);
});

test("susturma acikken bile spontaneous=false acikca verilirse gosterilir", () => {
  const p = kur({ toast_muted: true });
  p.api().info("Yedek geri yuklendi.", { spontaneous: false });
  assert.equal(toastSayisi(p.kutu()), 1);
});

// ---------------------------------------------------------------------------
// Konum
// ---------------------------------------------------------------------------

test("varsayilan konum sag-alt (mevcut davranis)", () => {
  for (const ayar of [{}, { toast_position: null }, { toast_position: "" }]) {
    const p = kur(ayar);
    const sinif = String(p.kutu()?.props.className ?? "");
    assert.ok(
      sinif.split(/\s+/).includes("toast-container--bottom-right"),
      `${JSON.stringify(ayar)} -> ${sinif}`,
    );
  }
});

test("taninmayan konum degeri varsayilana duser (ekran disina kacmasin)", () => {
  const p = kur({ toast_position: "orta-yer" });
  const sinif = String(p.kutu()?.props.className ?? "");
  assert.ok(sinif.includes("toast-container--bottom-right"), sinif);
  assert.ok(!sinif.includes("orta-yer"), `bilinmeyen deger sinifa sizdi: ${sinif}`);
});

test("ayarlanan konum kapsayici sinifina yansir", () => {
  for (const konum of M.TOAST_POSITIONS) {
    const p = kur({ toast_position: konum });
    const sinif = String(p.kutu()?.props.className ?? "");
    assert.ok(
      sinif.split(/\s+/).includes(`toast-container--${konum}`),
      `${konum} -> ${sinif}`,
    );
  }
});

test("konum ayari toast'in GOSTERILMESINI etkilemez", () => {
  // Konum ile susturma birbirine karismamali.
  for (const konum of M.TOAST_POSITIONS) {
    const p = kur({ toast_position: konum });
    p.api().info("mesaj");
    assert.equal(toastSayisi(p.kutu()), 1, `${konum} konumunda toast kayboldu`);
  }
});

// ---------------------------------------------------------------------------
// Uretilen sinifin CSS'te GERCEKTEN karsiligi var mi
// ---------------------------------------------------------------------------
// Provider gecerli bir sinif basip stylesheet'te karsiligi olmazsa toast
// tabandaki alt-sag konumunda kalir: ayar sessizce ise yaramaz.

function kuralGovdesi(css: string, secici: string): string | null {
  const kacisli = secici.replace(/[.*+?^${}()|[\]\\-]/g, "\\$&");
  const m = css.match(new RegExp(`${kacisli}\\s*\\{([^}]*)\\}`));
  return m ? m[1] : null;
}

test("her konum sinifinin CSS kurali var ve IKI EKSENI de sifirliyor", async () => {
  const { readFileSync } = await import("node:fs");
  const { transformSync } = (await import(process.env.E1_ESBUILD!)) as {
    transformSync: (k: string, o: Record<string, unknown>) => { code: string };
  };
  const { code } = transformSync(
    readFileSync(`${process.cwd()}/src/styles.css`, "utf8"),
    { loader: "css" },
  );

  for (const konum of M.TOAST_POSITIONS) {
    const govde = kuralGovdesi(code, `.toast-container--${konum}`);
    assert.ok(govde, `.toast-container--${konum} icin CSS kurali YOK — ayar etkisiz kalir`);
    // Taban `.toast-container` bottom+right veriyor. Varyant yalnizca tek
    // ekseni yazarsa digeri SIZAR ve toast iki kosede birden konumlanir.
    for (const eksen of ["top", "bottom", "left", "right"]) {
      assert.match(
        govde!,
        new RegExp(`(^|[;\\s])${eksen}\\s*:`),
        `.toast-container--${konum} '${eksen}' yazmiyor — taban deger sizar: ${govde}`,
      );
    }
  }
});

/** Verilen seciciyi iceren TUM kurallarin govdesini dondurur. */
function govdeler(css: string, secici: string): string[] {
  const cikti: string[] = [];
  let i = css.indexOf(secici);
  while (i >= 0) {
    const acilis = css.indexOf("{", i);
    const kapanis = css.indexOf("}", acilis);
    if (acilis > 0 && kapanis > acilis) cikti.push(css.slice(acilis + 1, kapanis));
    i = css.indexOf(secici, i + secici.length);
  }
  return cikti;
}

test("ust konumlarda giris animasyonu yukaridan gelmeli", async () => {
  const { readFileSync } = await import("node:fs");
  const stil = readFileSync(`${process.cwd()}/src/styles.css`, "utf8");
  assert.ok(
    /@keyframes\s+toast-slide-in-top/.test(stil),
    "ust konumlar icin ters yonlu animasyon tanimlanmamis",
  );

  // Alarm toast'i (`.toast-error.toast--actionable`) `animation` KISAYOLU ile
  // slide-in + nabiz tanimliyor. Ust varyantta yalnizca `animation-name`
  // ezilirse nabiz DUSER; kompozit kuralin yeniden yazilmis olmasi gerekir.
  // Ayni secici hareket-azaltma blogunda da geciyor (orada `animation: none`),
  // bu yuzden "en az bir kuralda nabiz var mi" diye bakiyoruz.
  const alarmKurallari = govdeler(stil, ".toast-container--top-right .toast-error.toast--actionable");
  assert.ok(alarmKurallari.length > 0, "ust varyantta alarm toast'i icin kural yok");
  assert.ok(
    alarmKurallari.some((g) => /toast-attention/.test(g)),
    "ust varyantta kompozit animasyon kurali yok — alarm toast'inin nabzi duser",
  );

  // Hareket azaltma tercihi varyant kurallarini da EZMELI (varyant secicileri
  // daha yuksek oncelikli). Toast'a ait reduced-motion blogunu bul.
  const azaltBas = stil.indexOf(
    "@media (prefers-reduced-motion: reduce)",
    stil.indexOf("@keyframes toast-attention"),
  );
  assert.ok(azaltBas > 0, "toast'a ait hareket azaltma blogu bulunamadi");
  const azaltBlok = stil.slice(azaltBas, azaltBas + 900);
  for (const secici of [
    ".toast-container--top-right .toast,",
    ".toast-container--top-left .toast,",
    ".toast-container--top-right .toast-error.toast--actionable,",
  ]) {
    assert.ok(
      azaltBlok.includes(secici),
      `hareket azaltma blogu '${secici}' kapsamiyor — tercih ezilir`,
    );
  }
});

// ---------------------------------------------------------------------------
// Kablolama: alarm bildirimi kendiliginden GELEN olarak isaretli mi
// ---------------------------------------------------------------------------
// NOT: bu testler kaynak metnine bakar — App.tsx'i calistirmak tum uygulamayi
// (harita, WS, i18n) Node'a tasimayi gerektirirdi. Zayif halka oldugu icin
// bilincli olarak DAR tutuldu: yalnizca alarm toast'inin secenek nesneleri.
//
// IKI YONU DE cakiliyoruz:
//   - info/warning isaretli DEGILSE susturma sessizce ise yaramaz hale gelir.
//   - critical/error isaretli ISE kritik ariza bildirimi susturulur; ayardan
//     haberi olmayan operator kritik arizayi ekranda hic gormez.
// Test paketi ESM olarak bundle'lanir; `require` YOK, dinamik import sart.
async function appKaynagi(): Promise<string> {
  const { readFileSync } = await import("node:fs");
  return readFileSync(`${process.cwd()}/src/app/App.tsx`, "utf8");
}

test("info/warning alarmi 'spontaneous' isaretli — yoksa susturma etkisiz kalir", async () => {
  const kaynak = await appKaynagi();
  const bas = kaynak.indexOf("const susturulabilir =");
  assert.ok(bas > 0, "susturulabilir varyant bulunamadi — alarm toast'i tasinmis olabilir");
  assert.match(
    kaynak.slice(bas, kaynak.indexOf(";", bas)),
    /spontaneous:\s*true/,
    "susturulabilir varyant isaretlenmemis; susturma hicbir sey yapmaz",
  );
});

test("KRITIK alarm 'spontaneous' isaretli DEGIL — susturma kritigi gizleyemez", async () => {
  const kaynak = await appKaynagi();
  const bas = kaynak.indexOf("const alarmAction = {");
  assert.ok(bas > 0, "alarmAction bulunamadi — alarm toast'i tasinmis olabilir");
  const govde = kaynak.slice(bas, kaynak.indexOf("};", bas));
  assert.doesNotMatch(
    govde,
    /spontaneous/,
    "kritik alarm susturulabilir isaretlenmis; kurulum geneli susturma kritik arizayi ekrandan kaldirir",
  );
  // Secim gercekten seviyeye bagli mi — tekil ve coklu yollarin ikisi de.
  assert.match(
    kaynak,
    /kritik\s*\?\s*alarmAction\s*:\s*susturulabilir/,
    "tekil alarm yolu seviyeye gore secim yapmiyor",
  );
  assert.match(
    kaynak,
    /hasCritical\s*\?\s*alarmAction\s*:\s*susturulabilir/,
    "coklu alarm ozeti seviyeye gore secim yapmiyor",
  );
});
