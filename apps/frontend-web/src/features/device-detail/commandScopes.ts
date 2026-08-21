/**
 * Komut yetki kumeleri — BAGIMSIZ modul, hicbir sey import etmez.
 *
 * NEDEN AYRI DOSYA
 * ----------------
 * Bu sabit hem panelde hem testte lazim. Panelden import etmek, test
 * kosucusunda tum modul zincirini (api.ts -> `import.meta.env`) surukleyip
 * Node'da patlatiyordu. Sabitin kendi dosyasi olunca test onu tek basina
 * okuyabiliyor.
 */

/** Backend `_CONFIG_COMMAND_SLUGS` AYNASI (`app/api/devices.py`).
 *
 *  NEDEN GRUBA DEGIL SLUG'A BAKILIYOR
 *  ----------------------------------
 *  Kilit gerekcesi UI grubundan turetilseydi YALAN SOYLERDI:
 *  `trigger_config_download` backend'de installer-only ama arayuzde
 *  "general" grubunda duruyor. Engineer onu ACIK gorur, basar ve 403 yer —
 *  "acik olan basilabilir" vaadi ilk tiklamada cokerdi.
 *
 *  Ayrisma testle kilitli (tests/komutYetkisi.test.ts). */
export const CONFIG_ONLY_SLUGS: ReadonlySet<string> = new Set([
  "config_update",
  "dnp3_config_update",
  "trigger_config_download",
  "trigger_dnp3_config_download",
  "start_csv_file_upload"
]);
