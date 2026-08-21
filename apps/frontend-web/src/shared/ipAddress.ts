/**
 * IPv4 dogrulamasi — ARAYUZ TARAFI.
 *
 * NEDEN VAR
 * ---------
 * Backend zaten reddediyor (`app/schemas/device.py: dogrula_ip`), ama
 * reddetme ancak "Olustur"a basildiktan SONRA, bir hata balonuyla
 * goruluyordu. Operator formu doldurup gonderiyor, geri donuyor, ne yazdigini
 * ariyor. Alan hatali oldugu ANDA belli olmali ve buton BASILAMAMALI.
 *
 * KURAL BACKEND ILE AYNI OLMAK ZORUNDA. Arayuz daha gevsek olursa kullanici
 * 422 yer; daha kati olursa backend'in kabul ettigi mesru bir adres
 * girilemez. Bu yuzden ayni uc kisit: IPv4, `0.0.0.0` yok, multicast yok.
 * Loopback BILEREK serbest — ayni makinedeki simulatore baglanmak mesru bir
 * kurulum.
 */

/** Kabul: dort parca, her biri 0-255, BASTA SIFIR YOK.
 *
 *  Bastaki sifir yasak cunku bazi cozumleyiciler `010`i sekizlik sayar
 *  (=8) — "010.0.0.1" iki farkli sey anlamina gelebilir. Backend de
 *  normalize ederek ayni belirsizligi kapatiyor. */
const IPV4 = /^(25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)(\.(25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)){3}$/;

/** Bos degeri GECERLI sayar: "zorunlu mu" ayri bir sorudur (`required`).
 *  Kullanici daha ilk harfi yazmadan kirmizi gormemeli. */
export function ipv4Gecerli(deger: string | null | undefined): boolean {
  const metin = (deger ?? "").trim();
  if (metin === "") return true;
  if (!IPV4.test(metin)) return false;
  const parcalar = metin.split(".").map(Number);
  // `0.0.0.0` bir cihaz adresi degil "herhangi bir arayuz" demektir.
  if (parcalar.every((p) => p === 0)) return false;
  // 224.0.0.0/4 multicast — tek bir outstation'i gosteremez.
  if (parcalar[0] >= 224) return false;
  return true;
}

/** Alan doldurulmus VE gecerli mi (form gonderme kapisi). */
export function ipv4Dolu(deger: string | null | undefined): boolean {
  return (deger ?? "").trim() !== "" && ipv4Gecerli(deger);
}
