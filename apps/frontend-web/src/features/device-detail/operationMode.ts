/**
 * Calisma modu — `<kaynak>.operation_mode` binary sinyalinin okunmasi.
 *
 * Cihaz sozlesmesi: 1 (true) = Akilli Mod, 0 (false) = Boost Mod.
 *
 * NEDEN AYRI VE SAF BIR FONKSIYON
 * -------------------------------
 * Kural tek satirlik gorunuyor ama icinde bir tuzak var (asagida) ve
 * arayuzun icine gomuldugunde test edilemez hale geliyordu. Burada saf
 * durdugu icin dogrudan kosuluyor (bkz. tests/operationMode.test.ts).
 */

import { signalTrust } from "../../shared/signalQuality";

export type OperationMode = "smart" | "boost";

/**
 * Modu coz; GUVENILIR DEGILSE `undefined` don.
 *
 * TUZAK — "0" iki farkli sey demek olabilir:
 *
 *   a) cihaz gercekten Boost modda,
 *   b) haberlesme koptu ve gateway `comm_lost` kalitesiyle 0.0 basiyor.
 *
 * Yalnizca `value === 1` bakan naif bir okuma ikisini AYNI sayar ve (b)
 * durumunda ekranda "Boost Mod" yazar — cihaz akilli modda calisirken
 * arayuz tam TERSINI iddia eder. Iki modun da gecerli bir durum olmasi bu
 * hatayi gorunmez kilar: operator yanlis bilgiyi sorgulamaz.
 *
 * Bu yuzden kalite kapisi ZORUNLU ve sonuc UC DURUMLU. `undefined` =
 * "bilmiyorum"; cagiran taraf satiri hic cizmez.
 *
 * @param value    sinyalin ham degeri (1/0), yoksa null/undefined
 * @param quality  DNP3 kalite bayragi (`comm_lost`, `restart`, ...)
 * @param gatewayOnline gateway ayakta mi — kopukken hicbir deger taze degil
 */
export function operationModeOf(
  value: number | null | undefined,
  quality: string | null | undefined,
  gatewayOnline: boolean
): OperationMode | undefined {
  if (signalTrust(value, quality, gatewayOnline) !== "trusted") return undefined;
  return value === 1 ? "smart" : "boost";
}
