/**
 * CIHAZ SAATI TESHISI — sade, ve BAGLANTI DURUMUNDAN AYRI.
 *
 * NE COZUYOR
 * ----------
 * Sahada bir Horstmann'in RTC'si **2066** yilina kaymisti ve bu bilgi
 * Grid'de HIC gorunmuyordu. Cihaz `online` idi, olcum gonderiyordu, komut
 * kabul ediyordu — ama urettigi her olay damgasi 40 yil ileriydi. Gateway
 * 1.15.1 bu boslugu `device_clock_status` ile kapatiyor.
 *
 * EN ONEMLI KURAL: BU BIR BAGLANTI DURUMU DEGILDIR
 * ------------------------------------------------
 * `device_clock_status = "invalid"` gorup cihazi kopuk saymak saglikli bir
 * filoyu arizali gosterir. Etkilenen tek sey CIHAZIN KENDI OLAY DAMGASINA
 * duyulan guvendir. Bu yuzden burasi `connection_state`e HIC dokunmaz ve
 * dondugu ton, durum rozetlerinin tonundan AYRI bir gorsel siniftir.
 *
 * `need_time` NEDEN `invalid`DEN DAHA IYI HABER
 * ---------------------------------------------
 * DNP3 saat senkronizasyonu TALEP GUDUMLUDUR: master yalnizca cihaz IIN1.4
 * assert ettiginde saat yazar. Yani:
 *   * `need_time` -> cihaz saat ISTIYOR, senkronizasyonla DUZELIR.
 *   * `invalid`   -> saat yanlis AMA cihaz saat ISTEMIYOR; durum
 *                    KENDILIGINDEN DUZELMEZ. Sahada gorulen tam olarak bu.
 * Sozlesmedeki oncelik de bu yuzden `invalid` > `need_time` > `ok`.
 *
 * ZORLA SENKRONIZASYON YOK: gateway yalnizca gorunur kilar, duzeltme
 * cihaz/saha isidir. Arayuz de bu yuzden bir "saati duzelt" dugmesi SUNMAZ.
 */
import type { DeviceRuntimeHealthRecord } from "../../shared/deviceRuntimeState";

/** Teshis tonu — DURUM tonlarindan bilerek AYRI isimlendirildi. */
export type ClockTone = "ok" | "uyari" | "sorun" | "bilinmiyor";

export type ClockGorunum = {
  /** i18n anahtari (deviceDetail.clock.* altinda). */
  labelKey: string;
  tone: ClockTone;
  /** Bir cumlelik aciklama anahtari. */
  hintKey: string;
  /** Saat farki metni; olculemediyse null. */
  offsetText: string | null;
  /** Cihaz saat ISTIYOR mu (IIN1.4). `null` = hic IIN gorulmedi. */
  needTime: boolean | null;
};

const K = "deviceDetail.clock.";

/** Saniyeyi okunur farka cevirir. ISARET KORUNUR: yon bilgi tasiyor. */
export function offsetMetni(sn: number | null | undefined): string | null {
  if (sn == null || !Number.isFinite(sn)) return null;
  const yon = sn >= 0 ? "+" : "−";
  const m = Math.abs(sn);
  // Esikler okunabilirlik icin: 90 sn'yi "1 dk" yapmak bilgi kaybi degil,
  // ama 40 yillik kaymayi saniye olarak yazmak okunamaz.
  if (m < 90) return `${yon}${m.toFixed(m < 10 ? 1 : 0)} sn`;
  if (m < 5400) return `${yon}${Math.round(m / 60)} dk`;
  if (m < 172800) return `${yon}${Math.round(m / 3600)} sa`;
  if (m < 63072000) return `${yon}${Math.round(m / 86400)} gun`;
  return `${yon}${(m / 31536000).toFixed(1)} yil`;
}

/**
 * Saat teshisini ekran gorunumune cevirir.
 *
 * `null` doner = gateway bu alani HIC gondermedi (1.15.0 ve oncesi).
 * O durumda arayuz satiri HIC cizmez: "bilinmiyor" yazmak, olculmemis bir
 * seyi olculmus gibi sunmak olurdu.
 */
export function clockGorunum(
  runtime: DeviceRuntimeHealthRecord | null | undefined
): ClockGorunum | null {
  if (!runtime) return null;
  const ham = typeof runtime.device_clock_status === "string"
    ? runtime.device_clock_status.trim().toLowerCase()
    : null;
  if (!ham) return null;

  const ortak = {
    offsetText: offsetMetni(runtime.device_clock_offset_sec),
    needTime:
      typeof runtime.need_time_iin === "boolean" ? runtime.need_time_iin : null
  };

  switch (ham) {
    case "ok":
      return { labelKey: K + "ok", tone: "ok", hintKey: K + "okHint", ...ortak };
    case "need_time":
      // Cihaz saat ISTIYOR — senkronizasyonla duzelir. Uyari, ariza degil.
      return {
        labelKey: K + "needTime",
        tone: "uyari",
        hintKey: K + "needTimeHint",
        ...ortak
      };
    case "invalid":
      // Saat KANITLANABILIR sekilde yanlis ve cihaz saat istemiyorsa
      // KENDILIGINDEN DUZELMEZ. En ciddi teshis bu.
      return {
        labelKey: K + "invalid",
        tone: "sorun",
        hintKey: K + "invalidHint",
        ...ortak
      };
    case "unknown":
      return {
        labelKey: K + "unknown",
        tone: "bilinmiyor",
        hintKey: K + "unknownHint",
        ...ortak
      };
    default:
      // TANIMADIGIMIZ DEGER "iyi" SAYILMAZ. Gateway ileride yeni bir teshis
      // eklerse arayuz notr gosterir; sessizce `ok` varsaymak, bozuk bir
      // saati saglikli gostermek olurdu.
      return {
        labelKey: K + "unknown",
        tone: "bilinmiyor",
        hintKey: K + "unknownHint",
        ...ortak
      };
  }
}

/** Ton -> CSS sinifi. Tek yerden, ki panel ile test ayrismasin. */
export function clockToneClass(tone: ClockTone): string {
  return `device-clock--${tone}`;
}
