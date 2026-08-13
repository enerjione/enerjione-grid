/**
 * Ariza araliginin BASLIK METNI — "nerede" sorusunun tek satirlik cevabi.
 *
 * NEDEN AYRI DOSYA
 * ----------------
 * Ayni metin dort ekranda uretiliyordu (aktif kart, detay sayfasi, gecmis
 * tablosu, detay penceresi) ve hepsi ayni varsayimi tasiyordu: "iki numara,
 * tek hat". BAGLANTI TELI arizasinda bu varsayim YANLIS:
 *
 *     hat=BR-2   direk 7 -> direk 1
 *
 * Buradaki 7 ANA HATTIN, 1 ise KOLUN diregidir. Ekranda "Direk #7 — Direk #1"
 * yaziyordu: geriye giden, anlamsiz bir aralik. Sahaya cikan ekip hangi
 * acikliga gidecegini okuyamiyor. Uc bunu `is_link_span` ile isaretliyor;
 * metin de iki UCLU bir baglanti olarak yazilir:
 *
 *     ANA HAT #7 ↔ BR-2 #1
 */
import type { StripPole } from "./faultStripGeometry";

type AralikArizasi = {
  from_pole_seq?: number | null;
  to_pole_seq?: number | null;
  line_name?: string | null;
  is_link_span?: boolean;
  from_pole_line_name?: string | null;
};

/** i18n `t` — cagiran taraftan gelir (modul React'a bagli kalmasin). */
type Ceviri = (key: string, params?: Record<string, unknown>) => string;

export function aralikMetni(
  fault: AralikArizasi,
  poles: readonly StripPole[] | undefined,
  t: Ceviri
): string {
  const from = fault.from_pole_seq;
  const to = fault.to_pole_seq;

  // BAGLANTI TELI: iki ayri hattin numarasi — aralik gibi yazilamaz.
  if (fault.is_link_span) {
    return t("faults.card.rangeTextLink", {
      fromLine: fault.from_pole_line_name ?? t("common.line"),
      from: from ?? "?",
      toLine: fault.line_name ?? t("common.line"),
      to: to ?? "?"
    });
  }

  // Saha ekibi direkleri sira numarasiyla degil ADIYLA taniyor.
  const adOf = (seq: number | null | undefined): string | null => {
    if (seq == null) return null;
    const ad = (poles?.find((p) => p.seq === seq)?.name ?? "").trim();
    return ad || null;
  };
  const fromAd = adOf(from);
  const toAd = adOf(to);
  if (fromAd && toAd) return t("faults.card.rangeTextNamed", { from: fromAd, to: toAd });
  return t("faults.card.rangeText", { from: from ?? "?", to: to ?? "?" });
}
