/**
 * Pole Master Kit — set (sanal alt cihaz) ile kit (fiziksel kayit) arasindaki
 * OKUMA kurallari. Tek kaynak; her ekran kendi kopyasini turetmesin.
 *
 * MODEL
 * -----
 * Bir kit TEK DNP3 outstation'dir: ortak bir RTU (`master`) ve ona bagli
 * dokuz uydu. Kullaniciya gosterilen "set" ise bu uydulardan ucunu gruplayan
 * SANAL bir kayittir — kendi modemi, kendi IP'si, kendi RTU pili YOKTUR.
 *
 * Bunu sinyal kataloglari da soyluyor:
 *
 *   horstmann_pole_master_kit : master.* (52) + sat01..sat09 (48'er)
 *   horstmann_pmk_set         : yalnizca sat01..sat03 (48'er)  <- master YOK
 *
 * Yani bir setin kaydinda `master.modem_rssi`, `master.info_ipv4_address`,
 * `master.info_part_no`, `master.firmware_version` HIC BULUNMAZ. Bu alanlari
 * setin kendi kaydindan okuyan her ekran onlari SONSUZA KADAR bos gosterir —
 * hata da vermez, sadece "—" yazar ve kullanici cihazin bu bilgiyi
 * uretmedigini saniyordu.
 *
 * KURAL
 * -----
 *   Fiziksel/haberlesme bilgisi  -> KIT kaydindan   (`saglikSahibi`)
 *   Olcum (uydu) bilgisi         -> SETIN kendinden (sat01..sat03)
 *
 * Amac: kullanici alt cihaza girse bile, gercekte haberlestigimiz cihazin
 * (master) durumunu ozet kisminda gorebilsin.
 */
import type { DeviceRow, SignalLiveRow } from "./types";

/**
 * Haberlesme/pil/modem bilgisinin GERCEK sahibi.
 *
 * Set ise fiziksel kit, degilse cihazin kendisi. Kit listede yoksa (kapsam
 * disi kalmis olabilir) cihazin kendine duseriz — bos ekran yerine eldeki
 * bilgi.
 */
export function saglikSahibi(
  device: DeviceRow,
  devices: readonly DeviceRow[]
): DeviceRow {
  const ustId = device.parentDeviceId ?? null;
  if (ustId == null) return device;
  return devices.find((d) => d.id === ustId) ?? device;
}

/** Cihaz bir Pole Master Kit seti mi (fiziksel kaydi olan sanal kayit)? */
export function setMi(device: DeviceRow | null | undefined): boolean {
  return (device?.parentDeviceId ?? null) !== null;
}

/**
 * `master.*` gibi RTU sinyallerini DOGRU cihazdan okur.
 *
 * Sette kit kaydina, sade cihazda kendi kaydina bakar. Deger yoksa
 * `undefined` — cagiran taraf "—" gosterir.
 */
export function rtuDegeri(
  key: string,
  device: DeviceRow,
  devices: readonly DeviceRow[],
  values: readonly SignalLiveRow[]
): SignalLiveRow | undefined {
  const sahip = saglikSahibi(device, devices);
  return values.find((r) => r.device_id === sahip.id && r.signal_key === key);
}
