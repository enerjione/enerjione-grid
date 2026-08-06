/**
 * Sinyal adi cevirisi.
 *
 * Sinyal ANAHTARLARI sabittir ve degistirilemez (`master.fault_current`,
 * `sat01.voltage_loss`...); katalogdaki `label` alani ise Ingilizce girilmis
 * durumda. Ceviri anahtari, cihaz onekinden arindirilmis SONEK uzerinden
 * bulunur (`signals.fault_current`) — boylece master ve 9 uydu ayni cevirivi
 * paylasir ve yeni cihaz kaynaklari kendiliginden kapsanir.
 *
 * SOZLUKTE OLMAYAN sinyal KIRILMAZ: katalogdaki ada (o da yoksa sonekin
 * kendisine) dusulur. Yani sonradan eklenen/ozel sinyaller ceviri beklemeden
 * calismaya devam eder; cevirisi eklendigi anda Turkce gorunur.
 */
import i18n from "./i18n";

export function signalLabel(signalKey: string, fallback?: string | null): string {
  const nokta = signalKey.indexOf(".");
  const sonek = nokta >= 0 ? signalKey.slice(nokta + 1) : signalKey;
  const anahtar = `signals.${sonek}`;
  return i18n.exists(anahtar) ? i18n.t(anahtar) : fallback || sonek;
}
