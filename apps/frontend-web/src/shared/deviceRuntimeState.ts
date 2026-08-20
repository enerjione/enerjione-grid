/**
 * CIHAZ CALISMA-ZAMANI DURUMU — TEK NORMALIZER.
 *
 * Sozlesme: `device_health_v1`, Gateway PR #33 (HENUZ ACIK).
 * Vendor kopyasi: `docs/gateway-contract/device-health-api-pr33.md`
 * (kaynak: enerjione-grid-dnp3-gateway, commit bd502c49,
 * `docs/GRID_DEVICE_HEALTH_API.md`).
 *
 * NEDEN TEK DOSYA
 * ---------------
 * Sozlesme HENUZ ACIK bir PR'da. Wire <-> model eslemesi tek bir adaptorde
 * (`normalizeDeviceRuntime`) toplanir; PR birlesirken alan adi ya da enum
 * degeri degisirse degisecek yer BURASIDIR. Liste, kart, harita, KPI ve
 * detay ekrani ham alanlara DOKUNMAZ, hepsi buradan cikan `DeviceRuntimeState`
 * nesnesini okur. Backend tarafi da ayni ilkeyi uyguluyor
 * (`app/services/device_runtime_health_service.py`, `_wire_to_model`).
 *
 * Tipler `shared/types.ts`e ALINMADI: sozlesmeyi iki dosyaya yaymak
 * "degisirse tek nokta" garantisini bozardi (backend de pydantic sema
 * dosyasi acmadi, ayni gerekceyle). `types.ts` buradan IMPORT eder; ters
 * yon yoktur, boylece dongusel bagimlilik da olusmaz.
 *
 * BU DOSYANIN KORUDUGU DORT SESSIZ HATA
 * -------------------------------------
 * 1. SAGLIKLI UYKUYU ARIZA SAYMAK. Horstmann Smart modda modemini KAPATIR;
 *    `smart_idle` SAGLIKLI bir durumdur (sozlesme bolum 5). `lost` ile ayni
 *    kovaya konursa uyuyan filo SCADA'da arizali gorunur ve gercek ariza
 *    gurultunun icinde kaybolur. Bu yuzden ayri bir anahtar, ayri bir RENK
 *    (MAVI) ve `healthy` kovasi.
 *
 * 2. BAYRAGI DURUM SANMAK. `report_late` bir DURUM DEGIL, bayraktir;
 *    `connection_state` HALA `smart_idle`dir. Ekranda ayri bir kova
 *    ("Gecikmis") olarak gosterilir ama cihaz IKI KOVADA BIRDEN sayilmaz —
 *    `LATE` anahtari `SMART_IDLE`in YERINE gecer, yanina degil.
 *
 * 3. GATEWAY'IN KARARINI EZMEK. Baglanti karari YALNIZCA
 *    `connection_state`indir. Telemetri tazeligine bakip "aslinda ayakta"
 *    demek, ya da sonda sonucundan ("ip_probe_status = unreachable")
 *    "cihaz oldu" cikarmak filonun yarisini yanlis gosterir: ICMP saha
 *    aglarinda sikca engellidir ve Smart bir modem MESRU olarak uykudadir.
 *    Gateway 1.14'teki bilinen recovery salinim hatasi da BURADA TELAFI
 *    EDILMEZ; oldugu gibi gosterilir, yoksa iki taraf birbirini duzeltmeye
 *    calisir ve gercek davranis hicbir ekranda gorunmez.
 *
 * 4. VERI YOKKEN DURUM UYDURMAK. Gateway 1.15.0 oncesi bu tasiyici YOKTUR.
 *    Runtime kaydi gelmiyorsa eski (telemetri turevli) davranisa dusulur;
 *    uydurma `smart_idle` / `recovering` / gecikme URETILMEZ.
 */

/** Sozlesme bolum 4 — cihaz kaydinin BELGELENMIS alanlari.
 *
 *  Hepsi istege bagli okunur. Iki sebep: (a) ileri uyumluluk — sozlesme acik
 *  ve alan eklemek geriye uyumludur, (b) eski gateway hicbirini gondermez.
 *  Bilinmeyen alanlar sessizce YOK SAYILIR.
 *
 *  Enum alanlari `string` olarak tutuluyor, dar birlesim olarak DEGIL:
 *  gelen yeni bir degeri tip sistemiyle bastirmak, onu sessizce yutmak
 *  olurdu. Tanimadigimiz deger `UNKNOWN`a duser ama ham hali `rawState`te
 *  KALIR, yani ekranda ve teshiste gorunur. */
export type DeviceHealthWire = {
  device_code?: string | null;
  /** `online` | `smart_idle` | `recovering` | `lost` | `listener_error` | `unknown`
   *  BAGLANTI KARARININ TEK KAYNAGI. */
  connection_state?: string | null;
  /** TCP link acik mi. */
  connected?: boolean | null;
  /** Komut gonderilebilir mi. Uyuyan (Smart) cihazda `false` — SAGLIKLI. */
  reachable?: boolean | null;
  /** `continuous` | `smart` | `auto` — operatorun YAPILANDIRDIGI. */
  configured_session_policy?: string | null;
  /** `continuous` | `smart` | `unknown` — sahada ETKIN olan (`auto` cozulunce). */
  effective_session_policy?: string | null;
  /** `smart` | `boost` | `unknown`. */
  operation_mode?: string | null;
  dial_in_interval_min?: number | null;
  /** Unix epoch (saniye, UTC). `null` = HIC OLMADI (gateway 0 gondermez). */
  next_expected_report_epoch?: number | null;
  report_overdue_sec?: number | null;
  /** UYARI BAYRAGI — durum degil. */
  report_late?: boolean | null;
  last_valid_contact_epoch?: number | null;
  last_frame_epoch?: number | null;
  /** SALT TESHIS. `unreachable` NORMALDIR (sozlesme bolum 5). */
  ip_probe_status?: string | null;
  /** SALT TESHIS. `open` | `connecting` | `unknown`. */
  tcp_probe_status?: string | null;
  last_probe_epoch?: number | null;
  /** `listening` | `initiating`. */
  ip_endpoint_type?: string | null;
};

/** Backend'in sakladigi satir: wire kaydi + BACKEND'e ait iki alan.
 *
 *  `updated_at` gateway wire kaydinda YOKTUR; backend'in kendi saatidir
 *  ("ne zaman haber aldik", bkz. `models/device_runtime_health.py`). Gateway
 *  saatine guvenilmez — sahada RTC'si bos acilan cihazlar ve NTP sicramalari
 *  gercektir. Bayatlik karari (asagida) YALNIZCA bu alandan verilir. */
export type DeviceRuntimeHealthRecord = DeviceHealthWire & {
  gateway_code?: string | null;
  /** ISO-8601, backend saati. */
  updated_at?: string | null;
};

/** Ekranin bildigi durum kumesi.
 *
 *  `LATE` sozlesmede bir `connection_state` DEGILDIR: `smart_idle` +
 *  `report_late` bilesiminin ekrandaki karsiligidir. Ayri bir anahtar
 *  olmasinin sebebi cift sayimi yapisal olarak imkansiz kilmak — bir cihaz
 *  tek bir anahtar tasir. */
export type DeviceRuntimeStateKey =
  | "ONLINE"
  | "SMART_IDLE"
  | "LATE"
  | "RECOVERING"
  | "COMM_LOST"
  | "LISTENER_ERROR"
  | "UNKNOWN";

/** Renk tonu. `blue` YALNIZCA `SMART_IDLE` icindir ve baska hicbir durumda
 *  kullanilmaz: operator mavi noktayi "uyuyor, saglikli" diye okuyacak. */
export type DeviceRuntimeTone = "green" | "blue" | "orange" | "amber" | "red" | "slate";

/** Saglik kovasi — KPI bunun uzerinden toplanir. */
export type DeviceRuntimeBucket = "healthy" | "degraded" | "unhealthy" | "unknown";

/** Durum bilgisi NEREDEN geldi?
 *
 *  `gateway` : `device_health_v1` kaydi var ve TAZE — otorite odur.
 *  `legacy`  : runtime kaydi yok ya da bayat; eski telemetri turevli
 *              `communication_status` kullanildi.
 *  `none`    : ikisi de yok. */
export type DeviceRuntimeSource = "gateway" | "legacy" | "none";

export type DeviceRuntimeState = {
  key: DeviceRuntimeStateKey;
  tone: DeviceRuntimeTone;
  bucket: DeviceRuntimeBucket;
  /** i18n anahtari (`deviceRuntime.state.*`). Metin BURADA URETILMEZ. */
  labelKey: string;
  /** material-symbols adi. Ikon subset'inde bulunan adlar (bkz.
   *  tests/iconSubset.test.ts); kosullu ifade yerine sabit tablo. */
  icon: string;
  source: DeviceRuntimeSource;
  /** `report_late` bayragi. TEK BASINA durum degildir; `LATE` anahtari
   *  zaten bunu tasiyor, bu alan detay ekraninin bayragi ayrica
   *  gosterebilmesi icin duruyor. Bayat gozlemden TASINMAZ (asagida). */
  reportLate: boolean;
  /** Runtime kaydi var ama gozlem eskimis. Durum karari legacy'ye dustu. */
  stale: boolean;
  /** Gateway'in bildirdigi ham `connection_state` — teshis/etiket icin.
   *  Tanimadigimiz bir deger geldiginde de burada DURUR. */
  rawState: string | null;
};

/** Durum -> gorunum tablosu. Tek yerde durur ki liste/kart/harita ayni
 *  cihaza farkli renk vermesin. */
const GORUNUM: Readonly<
  Record<DeviceRuntimeStateKey, { tone: DeviceRuntimeTone; bucket: DeviceRuntimeBucket; icon: string }>
> = {
  // Gateway ile konusuyor.
  ONLINE: { tone: "green", bucket: "healthy", icon: "wifi" },
  // Modem KAPALI ve bu BEKLENEN durum. Kirmizi/gri OLAMAZ.
  SMART_IDLE: { tone: "blue", bucket: "healthy", icon: "pending" },
  // Rapor gecikti ama sessizlik esigi dolmadi -> uyari, alarm degil.
  LATE: { tone: "orange", bucket: "degraded", icon: "hourglass_top" },
  // Gateway toparlanmaya calisiyor.
  RECOVERING: { tone: "amber", bucket: "degraded", icon: "autorenew" },
  // Sessizlik esigi asildi — GERCEK haberlesme kaybi.
  COMM_LOST: { tone: "red", bucket: "unhealthy", icon: "sensors_off" },
  // Gateway'in dinleyicisi ayaga kalkamadi (port cakismasi vb.).
  LISTENER_ERROR: { tone: "red", bucket: "unhealthy", icon: "error" },
  // Bilmiyoruz. "Sorun yok" DEMEK DEGIL.
  UNKNOWN: { tone: "slate", bucket: "unknown", icon: "help" }
};

/** Anahtar -> i18n son eki. */
const ETIKET: Readonly<Record<DeviceRuntimeStateKey, string>> = {
  ONLINE: "online",
  SMART_IDLE: "smartIdle",
  LATE: "late",
  RECOVERING: "recovering",
  COMM_LOST: "commLost",
  LISTENER_ERROR: "listenerError",
  UNKNOWN: "unknown"
};

/** Tum anahtarlar — KPI ve testler icin sabit sira. */
export const DEVICE_RUNTIME_KEYS: readonly DeviceRuntimeStateKey[] = [
  "ONLINE",
  "SMART_IDLE",
  "LATE",
  "RECOVERING",
  "COMM_LOST",
  "LISTENER_ERROR",
  "UNKNOWN"
];

/**
 * Gozlem bundan eskiyse runtime kaydina GUVENILMEZ (ms).
 *
 * Sozlesme bolum 9: gateway varsayilan olarak 300 saniyede bir UZLASTIRMA
 * snapshot'i gonderir — delta kaybolsa bile backend en gec o surede
 * hizalanir. Esik o araligin UC KATI: bolum 7'deki yeniden deneme geri
 * cekilmesi 120 saniyeye kadar cikabildigi icin tek bir kacan snapshot
 * rozeti dusurmemeli. Uc ust uste kacirilmis uzlastirma ise artik "gecici
 * gecikme" degildir.
 *
 * Bayatlik BAYRAGI dusurmez, KARARI dusurur: eski bir "online" kaydi ekranda
 * yesil kalirsa, gateway'in kendisi susmusken filo saglikli gorunur.
 */
export const RUNTIME_STALE_AFTER_MS = 900_000;

/** Eski (telemetri turevli) haberlesme durumu. `shared/types.ts`teki
 *  `CommunicationStatus` ile AYNI birlesim; buradan import EDILMIYOR ki bu
 *  modul kendi kendine yetsin ve `types.ts` ile dongu olusmasin. */
type LegacyCommStatus = "online" | "offline" | "unknown";

function metin(deger: unknown): string | null {
  if (typeof deger !== "string") return null;
  const kirpik = deger.trim();
  return kirpik.length > 0 ? kirpik : null;
}

function durumNesnesi(
  key: DeviceRuntimeStateKey,
  source: DeviceRuntimeSource,
  ekstra: { reportLate?: boolean; stale?: boolean; rawState?: string | null } = {}
): DeviceRuntimeState {
  const g = GORUNUM[key];
  return {
    key,
    tone: g.tone,
    bucket: g.bucket,
    icon: g.icon,
    labelKey: `deviceRuntime.state.${ETIKET[key]}`,
    source,
    reportLate: ekstra.reportLate ?? false,
    stale: ekstra.stale ?? false,
    rawState: ekstra.rawState ?? null
  };
}

/** Eski davranis: telemetri turevli `communication_status`.
 *
 *  Gateway 1.15.0 oncesi TEK bilgi kaynagi buydu ve oyle kalmali. Buradan
 *  `smart_idle` / `recovering` / gecikme URETILMEZ — o durumlarin varligini
 *  ancak gateway bilebilir. */
function legacyDurum(
  status: LegacyCommStatus | null | undefined,
  ekstra: { stale?: boolean; rawState?: string | null }
): DeviceRuntimeState {
  if (status === "online") return durumNesnesi("ONLINE", "legacy", ekstra);
  if (status === "offline") return durumNesnesi("COMM_LOST", "legacy", ekstra);
  if (status === "unknown") return durumNesnesi("UNKNOWN", "legacy", ekstra);
  return durumNesnesi("UNKNOWN", "none", ekstra);
}

/**
 * TEK NORMALIZER — wire kaydini ekranin okudugu duruma cevirir.
 *
 * `smart_idle` icin telemetri yasina, sonda sonucuna ya da `connected` /
 * `reachable` alanlarina BAKILMAZ. `connected=false, reachable=false` uyuyan
 * bir Horstmann icin BEKLENEN degerlerdir; onlara bakip durumu "daha kotu"
 * ya da "daha iyi" yapmak gateway'in kararini ezmek olurdu.
 *
 * @param runtime      backend'in sakladigi `device_health_v1` satiri
 * @param legacyStatus runtime yoksa/bayatsa kullanilacak eski durum
 * @param nowMs        simdiki zaman; disaridan gecilir (saf kalsin)
 */
export function normalizeDeviceRuntime(input: {
  runtime?: DeviceRuntimeHealthRecord | null;
  legacyStatus?: LegacyCommStatus | null;
  nowMs?: number;
  staleAfterMs?: number;
}): DeviceRuntimeState {
  const { runtime, legacyStatus } = input;
  const ham = metin(runtime?.connection_state);

  // Runtime kaydi hic yok (eski gateway ya da bu cihaz icin henuz rapor
  // gelmedi) -> eski davranis. UYDURMA durum uretilmez.
  if (!runtime || ham === null) {
    return legacyDurum(legacyStatus, { rawState: null });
  }

  // Gozlem eskimis mi? `updated_at` YOKSA bayatlik iddia EDILMEZ: olcemedigim
  // bir seyi "eski" ilan etmek de bir uydurmadir.
  const now = input.nowMs ?? Date.now();
  const esik = input.staleAfterMs ?? RUNTIME_STALE_AFTER_MS;
  const gozlem = runtime.updated_at ? new Date(runtime.updated_at).getTime() : Number.NaN;
  const bayat = Number.isFinite(gozlem) && now - gozlem > esik;
  if (bayat) {
    // BAYRAK TASINMAZ: eski bir gozlemdeki `report_late`, "su anda gecikmis"
    // iddiasini destekleyemez. Ham durum yine de tasinir ki detay ekrani
    // "son bilinen durum" diyebilsin.
    return legacyDurum(legacyStatus, { stale: true, rawState: ham });
  }

  const gec = runtime.report_late === true;
  const ortak = { reportLate: gec, rawState: ham };

  switch (ham.toLowerCase()) {
    case "lost":
      return durumNesnesi("COMM_LOST", "gateway", ortak);
    case "listener_error":
      return durumNesnesi("LISTENER_ERROR", "gateway", ortak);
    case "smart_idle":
      // Bayrak durumun YERINE gecer, yanina degil: cihaz tek kovada sayilir.
      return durumNesnesi(gec ? "LATE" : "SMART_IDLE", "gateway", ortak);
    case "recovering":
      return durumNesnesi("RECOVERING", "gateway", ortak);
    case "online":
      return durumNesnesi("ONLINE", "gateway", ortak);
    case "unknown":
      return durumNesnesi("UNKNOWN", "gateway", ortak);
    default:
      // Sozlesme acik; yeni bir durum eklenebilir. Tanimadigimizi legacy'ye
      // DUSURMEYIZ (gateway otoritedir ve konusuyor), "bilmiyorum" deriz ve
      // ham degeri gorunur birakiriz.
      return durumNesnesi("UNKNOWN", "gateway", ortak);
  }
}

/** `DeviceRow` benzeri bir cihazdan durum. Bilesenler bunu cagirir. */
export function deviceRuntimeStateOf(
  device: {
    runtimeHealth?: DeviceRuntimeHealthRecord | null;
    communicationStatus?: LegacyCommStatus | null;
  },
  nowMs?: number
): DeviceRuntimeState {
  return normalizeDeviceRuntime({
    runtime: device.runtimeHealth ?? null,
    legacyStatus: device.communicationStatus ?? null,
    nowMs
  });
}

// ---------------------------------------------------------------------------
// GERI SAYIM — DAKIKA CINSINDEN
// ---------------------------------------------------------------------------

/**
 * Sonraki Dial-In geri sayimi.
 *
 * `none`    : gosterilecek bir sey yok (epoch `null` = HIC OLMADI, ya da
 *             gateway otoritesi yok).
 * `dueIn`   : gelecekte — "Sonraki Dial-In: 43 dk".
 * `overdue` : gecti ama haberlesme kaybi degil — "Dial-In gecikmesi: 6 dk".
 * `lost`    : sessizlik esigi asildi — "Beklenen Dial-In asildi".
 */
export type DialInCountdown =
  | { kind: "none" }
  | { kind: "dueIn"; minutes: number }
  | { kind: "overdue"; minutes: number }
  | { kind: "lost" };

/**
 * Geri sayimi coz.
 *
 * `next_expected_report_epoch` OTORITEDIR. `last_valid_contact + interval`
 * ile YENIDEN HESAPLANMAZ: gateway Dial-In penceresini kendi kaymasiyla
 * birlikte biliyor ve iki taraf ayri hesap yaparsa ekrandaki dakika sahadaki
 * pencereyle tutmaz — ustelik fark tam da gecikme aninda buyur.
 *
 * SANIYE URETMEZ. Ekranda saniye gostermek, dakikada bir tazelenen bir
 * degeri saniyede bir tazelemeye ve 200+ cihazlik listede saniyede bir
 * yeniden cizime zorlardi; kazanc yok, maliyet gercek.
 */
export function dialInCountdown(input: {
  runtime?: DeviceRuntimeHealthRecord | null;
  state: DeviceRuntimeState;
  nowMs?: number;
}): DialInCountdown {
  const { runtime, state } = input;
  // Otorite gateway'dir. Legacy/bayat kayitta geri sayim GOSTERILMEZ:
  // eski bir epoch'tan dakika uretmek uydurma bir kesinliktir.
  if (state.source !== "gateway") return { kind: "none" };

  const epoch = runtime?.next_expected_report_epoch;
  // `null` = "hic olmadi" (sozlesme bolum 4). Hicbir sey gosterme.
  if (typeof epoch !== "number" || !Number.isFinite(epoch)) return { kind: "none" };

  if (state.key === "COMM_LOST") return { kind: "lost" };

  const now = input.nowMs ?? Date.now();
  const farkMs = epoch * 1000 - now;
  if (farkMs > 0) return { kind: "dueIn", minutes: Math.ceil(farkMs / 60_000) };
  // En az 1 dk: "0 dk gecikme" ekranda kendi kendini yalanlayan bir ifade.
  return { kind: "overdue", minutes: Math.max(1, Math.ceil(-farkMs / 60_000)) };
}

// ---------------------------------------------------------------------------
// KPI — HER CIHAZ TEK KEZ
// ---------------------------------------------------------------------------

export type RuntimeCounts = Record<DeviceRuntimeStateKey, number> & { total: number };

/**
 * Durum bazli sayim. Bir cihaz TEK anahtarda sayilir; gecikmis bir Smart
 * cihaz "Gecikmis" kovasindadir ve "Smart Bekleme"de AYRICA yer almaz.
 * Toplam, kovalarin toplamina esittir (testte dogrulanir).
 */
export function runtimeCounts(states: readonly DeviceRuntimeState[]): RuntimeCounts {
  const sayim = {
    ONLINE: 0,
    SMART_IDLE: 0,
    LATE: 0,
    RECOVERING: 0,
    COMM_LOST: 0,
    LISTENER_ERROR: 0,
    UNKNOWN: 0,
    total: states.length
  } as RuntimeCounts;
  for (const s of states) sayim[s.key] += 1;
  return sayim;
}

export type RuntimeBucketCounts = Record<DeviceRuntimeBucket, number> & { total: number };

/** Kova bazli sayim (saglikli / bozulmus / arizali / bilinmiyor). */
export function runtimeBucketCounts(states: readonly DeviceRuntimeState[]): RuntimeBucketCounts {
  const sayim = {
    healthy: 0,
    degraded: 0,
    unhealthy: 0,
    unknown: 0,
    total: states.length
  } as RuntimeBucketCounts;
  for (const s of states) sayim[s.bucket] += 1;
  return sayim;
}

// ---------------------------------------------------------------------------
// TESHIS ALANLARI — DURUM BELIRLEMEZLER
// ---------------------------------------------------------------------------

/**
 * Sonda (probe) sonuclari SALT TESHISTIR (sozlesme bolum 5).
 *
 * `ip_probe_status = "unreachable"` gormek NORMALDIR: ICMP saha aglarinda ve
 * APN'lerde sikca engellidir, ustelik Smart bir modem mesru olarak uykudadir.
 * Bu yuzden sondanin ekranda bir RENGI yoktur — notr gosterilir ve
 * "Teshis" basligi altinda durur. Ayri bir fonksiyon olmasinin sebebi,
 * birinin ileride sonda degerine gore renk vermeye kalkismasini zorlastirmak.
 */
export function probeIsDiagnosticOnly(): true {
  return true;
}

/** Sozlesmede belgelenmis enum degerleri — i18n anahtari uretmek icin.
 *  Listede olmayan bir deger geldiginde cagiran taraf HAM degeri gosterir;
 *  uydurma bir etiket yerine gercek veri ekranda kalir. */
export const SESSION_POLICY_VALUES = ["continuous", "smart", "auto", "unknown"] as const;
export const OPERATION_MODE_VALUES = ["smart", "boost", "unknown"] as const;
export const IP_PROBE_VALUES = ["reachable", "unreachable", "unsupported", "unknown"] as const;
export const TCP_PROBE_VALUES = ["open", "connecting", "unknown"] as const;
export const IP_ENDPOINT_VALUES = ["listening", "initiating"] as const;

/** `group` + deger -> i18n anahtari; deger belgelenmemisse `null`.
 *  `null` donunce cagiran ham degeri basar (bkz. DeviceRuntimePanel). */
export function runtimeEnumKey(
  group: "sessionPolicy" | "operationMode" | "ipProbe" | "tcpProbe" | "ipEndpoint",
  value: string | null | undefined
): string | null {
  const v = metin(value)?.toLowerCase() ?? null;
  if (v === null) return null;
  const izin: Readonly<Record<string, readonly string[]>> = {
    sessionPolicy: SESSION_POLICY_VALUES,
    operationMode: OPERATION_MODE_VALUES,
    ipProbe: IP_PROBE_VALUES,
    tcpProbe: TCP_PROBE_VALUES,
    ipEndpoint: IP_ENDPOINT_VALUES
  };
  return izin[group].includes(v) ? `deviceRuntime.${group}.${v}` : null;
}

/** Yapilandirilan ile ETKIN oturum politikasi ayrisiyor mu?
 *
 *  `auto` HENUZ COZULMEMIS demektir, ayrisma DEGIL: gateway `auto`yu sahada
 *  `continuous` ya da `smart` olarak cozer. `unknown` da bir ayrisma degil,
 *  "daha bilinmiyor"dur. Bunlari ayrisma sayan bir uyari, dogru calisan her
 *  `auto` cihazda kalici bir kirmizi rozet gosterirdi. */
export function sessionPolicyMismatch(
  configured: string | null | undefined,
  effective: string | null | undefined
): boolean {
  const c = metin(configured)?.toLowerCase() ?? null;
  const e = metin(effective)?.toLowerCase() ?? null;
  if (c === null || e === null) return false;
  if (c === "auto" || e === "unknown") return false;
  return c !== e;
}

/** Epoch (saniye) -> Date; `null`/gecersizse `null`.
 *  `0` GECERLI BIR AN DEGILDIR bu sozlesmede: gateway "hic olmadi" icin
 *  `null` gonderir, 0 gondermez (panelde 1970 tarihi cikmasin diye). */
export function epochToDate(epoch: number | null | undefined): Date | null {
  if (typeof epoch !== "number" || !Number.isFinite(epoch) || epoch <= 0) return null;
  return new Date(epoch * 1000);
}
