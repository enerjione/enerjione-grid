/**
 * Ariza Analizi grafiklerinin ORTAK dili — renk, eksen, ipucu.
 *
 * NEDEN AYRI DOSYA: bes ayri grafik var ve her biri kendi eksen/ipucu
 * ayarlarini tasisaydi zamanla ayrisirlardi (biri kilavuz cizgili, digeri
 * degil; biri binlik ayracli, digeri degil). Ekranin "tek sistem" gibi
 * okunmasi bu ortak katmana bagli.
 *
 * PALET — DOGRULANMIS
 * -------------------
 * Kategorik sira SABIT ve donguye SOKULMAZ: mavi -> yesil -> mor -> turuncu.
 * Sira keyfi degil; bu dizilim CVD (renk korlugu) ayrimi kontrolunden gecen
 * dizilim. Ilk denenen mavi/yesil/turuncu/mor sirasinda turuncu ile yesil
 * KOMSU dusuyor ve deuteranopia'da ayirt edilemiyordu (dE 7.3). Mor'u araya
 * almak komsulugu bozuyor ve en kotu komsu cift dE 30.3'e cikiyor.
 *
 * Renkler cihaz detay ekranindaki kaynak renkleriyle ayni: operator ayni
 * kodu bir kez ogreniyor (mavi = master/L1, yesil = sat01/L2, turuncu =
 * sat02/L3).
 */

/** Kategorik hue sirasi — ASLA dongude tekrarlanmaz. */
export const KATEGORIK = ["#2563eb", "#16a34a", "#7c3aed", "#c2410c"] as const;

/** Tek serili magnitude grafiklerinin rengi (siralama cubuklari). */
export const TEK_SERI = "#2563eb";

/** Durum renkleri — kategorik paletle KARISTIRILMAZ. */
export const DURUM = {
  acik: "#dc2626",
  cozulen: "#16a34a",
  uyari: "#b45309",
} as const;

/** Faz kodu -> renk. Cihaz ekranindaki kaynak renkleriyle ayni. */
export const FAZ_RENK: Record<string, string> = {
  a: "#2563eb",
  b: "#16a34a",
  c: "#c2410c",
  abc: "#7c3aed",
};

const INK = "#334155";
const MUTED = "#94a3b8";
const GRID = "#eef2f7";

/** Tum grafiklerde ortak ipucu kutusu. */
export const ipucu = {
  backgroundColor: "#0f172a",
  borderWidth: 0,
  padding: [8, 11] as [number, number],
  textStyle: { color: "#e2e8f0", fontSize: 12 },
  extraCssText: "border-radius:9px;box-shadow:0 10px 28px rgba(15,23,42,.3);",
};

/** Deger ekseni — kilavuz cizgiler GERI PLANDA kalir. */
export const degerEkseni = {
  type: "value" as const,
  splitLine: { lineStyle: { color: GRID } },
  axisLine: { show: false },
  axisTick: { show: false },
  axisLabel: { color: MUTED, fontSize: 11 },
};

/** Kategori ekseni. */
export const kategoriEkseni = {
  type: "category" as const,
  axisLine: { lineStyle: { color: GRID } },
  axisTick: { show: false },
  axisLabel: { color: INK, fontSize: 11 },
};

/** Yatay siralama cubugu — uc SABIT: 4px yuvarlak, tabana yapisik. */
export const cubuk = {
  type: "bar" as const,
  barMaxWidth: 18,
  itemStyle: { borderRadius: [0, 4, 4, 0] as [number, number, number, number] },
};

/** Grafik alani — sol taraf uzun etiketler icin cagrilanda ayarlanir. */
export function izgara(sol: number, ust = 12, sag = 22, alt = 24) {
  return { left: sol, right: sag, top: ust, bottom: alt, containLabel: false };
}
