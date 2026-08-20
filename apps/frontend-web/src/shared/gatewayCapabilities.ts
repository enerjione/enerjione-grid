/**
 * Gateway yetenek kapisi — backend `app/services/gateway_compatibility.py`
 * dosyasinin AYNASIDIR.
 *
 * TEK OTORITE BACKEND'DIR
 * -----------------------
 * Buradaki kurallar, ayni karari arayuzde ONCEDEN gosterebilmek icin var.
 * Karari VERMEZLER: payload'i gateway'e gonderip gondermeme karari backend'in
 * `eksik_yetenekler` fonksiyonunda alinir. Iki taraf ayrisirsa en kotu bicimde
 * ayrisir: arayuz "destekleniyor" derken backend ilgili alanlari sessizce
 * dusurur, gateway guvenli tarafta (surekli mod) calisir ve operator ayarin
 * sahada gecerli oldugunu SANIR. Bu modul tam olarak o sessiz ayrismayi
 * gorunur kilmak icin duruyor; backend matrisi degisirse burasi da degismeli.
 *
 * KAPIYA NE GIRER, NE GIRMEZ
 * --------------------------
 * Kapi yalnizca gateway v1.14.0 ILE GELEN yetenekler icindir:
 *   - `smart_auto`      : `session_policy = auto` degeri (1.13.0 ve oncesi bu
 *                         degeri tanimaz ve TUM config'i reddeder).
 *   - `smart_listening` : `smart`/`auto` + `listening` kombinasyonu (uc tipi
 *                         kisiti 1.14.0'da kalkti).
 *
 * `smart_session` (1.12.0) BILEREK LISTEDE YOK — backend de sokmuyor.
 * `initiating` + `smart` bugune kadar hicbir surum kapisindan gecmeden
 * calisiyordu; simdi kapiya sokmak, surumunu bildirmemis (cok yaygin) her
 * gateway'de SAHADA CALISAN kurulumlari yanlis yere suphelendirirdi.
 *
 * UYARIR, ENGELLEMEZ
 * ------------------
 * Eksik yetenek kaydetmeyi durdurmaz (urun karari): mesru akis "once cihazi
 * yapilandir, sonra gateway'i guncelle"dir. Ama gorunmez de birakilmaz.
 */

/** v1.14.0 ile gelen yeteneklerin ortak alt siniri. */
export const V114_MIN = "1.14.0";

/**
 * Yetenek -> gerektirdigi EN DUSUK gateway surumu.
 *
 * Anahtar YETENEK ADIDIR, surum numarasi degil: yeni bir gateway surumu
 * cikmasi mevcut bir yetenegin minimumunu YUKARI TASIMAZ. Minimum, ozelligin
 * gercekten calismaya basladigi surumdur ve bir kez saptandiktan sonra sabit
 * kalir; yeni ozellik gelirse buraya YENI BIR SATIR eklenir.
 */
const YETENEK_EN_DUSUK_SURUM: Readonly<Record<string, string>> = {
  smart_auto: V114_MIN,
  smart_listening: V114_MIN
};

/** Gateway'in uyku karari verdigi politikalar (backend: SMART_CAPABLE_POLICIES). */
const UYKULU_POLITIKALAR = ["smart", "auto"];

/** "v1.14.0" -> [1,14,0];  "1.14" -> [1,14];  bos/bozuk -> []
 *
 *  Backend `parse_version` ile ayni: yalnizca sayilara bakar, "-rc1" gibi
 *  ekleri yok sayar, en fazla dort bilesen alir. */
function surumDemeti(ham: string | null | undefined): number[] {
  if (!ham) return [];
  const parcalar = String(ham).match(/\d+/g);
  if (!parcalar) return [];
  return parcalar.slice(0, 4).map((p) => Number(p));
}

/** mevcut >= gerekli mi? Eksik bilesenler 0 sayilir (1.14 == 1.14.0). */
function enAz(mevcut: number[], gerekli: number[]): boolean {
  const boy = Math.max(mevcut.length, gerekli.length);
  for (let i = 0; i < boy; i += 1) {
    const a = mevcut[i] ?? 0;
    const b = gerekli[i] ?? 0;
    if (a !== b) return a > b;
  }
  return true;
}

/** Bu yetenegin gerektirdigi en dusuk surum; matriste yoksa null (kisitsiz). */
export function capabilityMinVersion(capability: string): string | null {
  return YETENEK_EN_DUSUK_SURUM[capability] ?? null;
}

/**
 * Bu gateway surumu yetenegi destekliyor mu?
 *
 * UC DURUMLU ve bu bilincli: `null` = BILINMIYOR (gateway surumunu henuz
 * bildirmedi). `false` ile ayni saymak "desteklemiyor" iddiasinda bulunmak
 * olurdu; bildirmemis bir gateway pekala guncel olabilir.
 */
export function supportsCapability(
  capability: string,
  gatewayVersion: string | null
): boolean | null {
  const gerekli = capabilityMinVersion(capability);
  // Matriste olmayan yetenek kisitsizdir: bilmedigimiz bir sey icin
  // "desteklenmiyor" demeyiz.
  if (gerekli === null) return true;
  const mevcut = surumDemeti(gatewayVersion);
  if (mevcut.length === 0) return null;
  return enAz(mevcut, surumDemeti(gerekli));
}

/**
 * Bu politika + uc tipi kombinasyonunun gateway'den istedigi YENI yetenekler.
 *
 * Surekli modda kombinasyon hicbir yeni yetenek istemez — uyari da cikmaz.
 */
export function requiredCapabilities(policy: string, endpoint: string): string[] {
  const politika = (policy || "continuous").trim().toLowerCase();
  const uc = (endpoint || "listening").trim().toLowerCase();
  if (!UYKULU_POLITIKALAR.includes(politika)) return [];
  const gerekli: string[] = [];
  if (politika === "auto") gerekli.push("smart_auto");
  if (uc === "listening") gerekli.push("smart_listening");
  return gerekli;
}

/**
 * Gateway'in KARSILAYAMADIGI yetenekler. Bos dizi = uyari yok.
 *
 * BILINMEYEN SURUM EKSIK SAYILIR — bilincli ve backend ile ayni. Surumunu
 * bildirmemis bir gateway'e `auto` gondermek TUM config'i reddettirebilir;
 * "bilmiyorum" durumunda guvenli taraf ozelligi GONDERMEMEK ve operatore
 * acikca soylemektir. Ters yon tek bir cihaz ayari yuzunden butun sahayi
 * susturur.
 */
export function missingCapabilities(
  policy: string,
  endpoint: string,
  gatewayVersion: string | null
): string[] {
  return requiredCapabilities(policy, endpoint).filter(
    (yetenek) => supportsCapability(yetenek, gatewayVersion) !== true
  );
}

/**
 * Verilen yetenek kumesinin toplu alt siniri (en yuksek minimum).
 *
 * Uyari metnindeki surum numarasi buradan gelir; formda sabit yazilsaydi
 * matrise farkli minimumlu bir yetenek eklendigi gun ekranda yanlis surum
 * yazardi. Kume bos ya da hepsi kisitsizsa null.
 */
export function requiredVersion(capabilities: string[]): string | null {
  let enYuksek: string | null = null;
  for (const yetenek of capabilities) {
    const gerekli = capabilityMinVersion(yetenek);
    if (gerekli === null) continue;
    if (enYuksek === null || !enAz(surumDemeti(enYuksek), surumDemeti(gerekli))) {
      enYuksek = gerekli;
    }
  }
  return enYuksek;
}
