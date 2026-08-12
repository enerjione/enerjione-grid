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

/** Bir sette kac uydu var — backend `SATELLITES_PER_SET` ile AYNI olmali. */
export const SETTEKI_UYDU_SAYISI = 3;

/**
 * Setin FIZIKSEL uydu numaralari — sirasiyla sat01/sat02/sat03'un karsiligi.
 *
 * KAYITLI ATAMA KAZANIR. Varsayilan yerlesim (set 1 -> 1/2/3, set 2 -> 4/5/6,
 * set 3 -> 7/8/9) yalnizca BASLANGIC noktasidir: uydulari kelepceyi takan kisi
 * baglar ve sira kite gore degil DIREGE gore olusur. Kurulumcu atamayi cihaz
 * ayarlarindan degistirebilir (`subunit_satellites`); sabit turetme kullanmak,
 * ikinci sete 2/7/9 baglanmis bir kurulumda YANLIS uydunun pilini dogru diye
 * gostermek olurdu — ekranda hicbir hata gorunmez, sadece yanlis sayi durur.
 *
 * Backend'deki `resolve_subunit_satellites` ile AYNI kurali uygular; oradaki
 * kural yazma tarafinin (telemetri bolme) kaynagidir, buradaki yalnizca
 * GOSTERIM icin. Ikisi ayrisirsa ekran ile telemetri farkli uydudan bahseder.
 */
export function fizikselUydular(device: DeviceRow | null | undefined): number[] {
  if (!device) return [];
  const kayitli = device.subunitSatellites;
  if (kayitli && kayitli.length === SETTEKI_UYDU_SAYISI) {
    const temiz = kayitli.map((n) => Number(n)).filter((n) => Number.isFinite(n) && n > 0);
    if (temiz.length === SETTEKI_UYDU_SAYISI) return temiz;
  }
  const sira = device.subunitIndex ?? 0;
  if (sira < 1) return [];
  const ilk = (sira - 1) * SETTEKI_UYDU_SAYISI + 1;
  return Array.from({ length: SETTEKI_UYDU_SAYISI }, (_, i) => ilk + i);
}

/** Fiziksel uydu numarasindan kaynak oneki: 4 -> "sat04". */
export function uyduKaynagi(no: number): string {
  return `sat${String(no).padStart(2, "0")}`;
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
