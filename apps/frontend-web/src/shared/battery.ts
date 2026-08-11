/**
 * Batarya voltaj -> yuzde donusumunun TEK kaynagi.
 *
 * NEDEN TEK DOSYA: donusum dort ayri yerde kopyalanmisti ve ikisi sabit
 * `3.2V=%0 / 4.2V=%100` kullaniyordu — backend'in 3.40/3.71'i ile hic
 * uyusmuyordu. Ayni cihazin bataryasi cihaz listesinde, haritada ve detay
 * sayfasinda FARKLI yuzde gosterebiliyordu. Esikler artik cihaz TURU
 * seviyesinde tanimli (bkz. Proje Ayarlari > Cihaz profilleri) ve buradan
 * cozuluyor.
 */

/** Backend `device_profile_service` ile AYNI kod varsayilanlari olmali. */
export const DEFAULT_BATTERY_VOLTAGE_LOW = 3.4;
export const DEFAULT_BATTERY_VOLTAGE_FULL = 3.71;

/** Bataryayi tasiyan sinyalin slug'i (kaynak oneki haric). */
export const BATTERY_SIGNAL_SLUG = "battery_voltage_satellite";

/** Pole Master Kit setinin model kodu — uc unitesi de uydudur. */
const PMK_SET_MODEL = "horstmann_pmk_set";

export type BatteryThresholds = { low: number; full: number };

export const DEFAULT_BATTERY_THRESHOLDS: BatteryThresholds = {
  low: DEFAULT_BATTERY_VOLTAGE_LOW,
  full: DEFAULT_BATTERY_VOLTAGE_FULL
};

/**
 * Modelin cihaz-seviyesi bataryasini hangi unite(ler) tasir.
 *
 * SN 2.0'da RTU'yu besleyen hucre `master` unitesindedir. Pole Master Kit
 * SETINDE `master` YOKTUR — uc uydunun her birinin ayri bataryasi vardir.
 */
export function batteryUnitsFor(model: string | null | undefined): string[] {
  return model === PMK_SET_MODEL ? ["sat01", "sat02", "sat03"] : ["master"];
}

export function batterySignalKeysFor(model: string | null | undefined): string[] {
  return batteryUnitsFor(model).map((u) => `${u}.${BATTERY_SIGNAL_SLUG}`);
}

/** Voltaji yuzdeye cevirir: <=low -> 0, >=full -> 100, arasi lineer. */
export function voltageToPercent(
  volts: number | null | undefined,
  thresholds: BatteryThresholds = DEFAULT_BATTERY_THRESHOLDS
): number | undefined {
  if (volts == null || !Number.isFinite(volts)) return undefined;
  const { low, full } = thresholds;
  // Ters/sifir aralik yuzdeyi anlamsiz kilar; koda geri dus.
  const span = full - low;
  if (!(span > 0)) return undefined;
  const pct = ((volts - low) / span) * 100;
  return Math.max(0, Math.min(100, Math.round(pct)));
}

/**
 * Cok bataryali modellerde cihazin yuzdesi EN DUSUK uniteden cikar: ekip
 * direge en zayif hucre bittiginde cikar, ortalama bunu gizler.
 * Degeri olmayan unite hesaba katilmaz.
 */
export function batteryPercentFromUnits(
  voltsByUnit: Array<number | null | undefined>,
  thresholds: BatteryThresholds = DEFAULT_BATTERY_THRESHOLDS
): number | undefined {
  const gecerli = voltsByUnit.filter(
    (v): v is number => v != null && Number.isFinite(v)
  );
  if (gecerli.length === 0) return undefined;
  return voltageToPercent(Math.min(...gecerli), thresholds);
}
