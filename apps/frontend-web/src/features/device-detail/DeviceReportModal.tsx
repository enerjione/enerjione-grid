/**
 * DeviceReportModal — PDF raporun hangi bolumleri iceresin.
 *
 * NEDEN SECIM VAR
 * ---------------
 * Rapor kime gittigine gore degisiyor. Sahaya cikan ekibe konum ve olcum
 * kanallari yetiyor; musteriye giden ekte baglanti ayrintisi (IP, DNP3
 * adresi) hic istenmiyor; arizayi inceleyen muhendis ise sinyal tablolarini
 * ve olaylari istiyor. Tek bir sabit belge bu uc ihtiyacin hicbirini tam
 * karsilamiyor, en genisini basip gerisini okuyucuya atlatiyordu.
 *
 * LISTE CIHAZ TURUNE GORE
 * -----------------------
 * Bir Horstmann SN 2.0 icin "Bagli Setler", bir Pole Master Kit icin
 * "Olcum Kanallari" diye bir sey YOK (kit olcum yapmaz, uydulari setlere
 * yonlendirilir). Secilemeyecek bir kutucuk gostermek, kullaniciya olmayan
 * bir bolumu isaretletip raporda bulamamasina yol acardi.
 *
 * SECIM HATIRLANIR
 * ----------------
 * Ayni kisi genelde ayni raporu aliyor. Secim tarayicida saklanir
 * (`localStorage`), boylece her seferinde bastan isaretlemek gerekmez.
 * Kayit BOZUKSA ya da o gunku cihaz turunde artik gecerli olmayan bir
 * bolum iceriyorsa sessizce varsayilana (hepsi) dusulur — kaydedilmis bir
 * tercih yuzunden bos rapor cikmasi kabul edilemez.
 */
import { useCallback, useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { FileDown, X } from "lucide-react";

import { useModalDialog } from "../../shared/useModalDialog";
import { isKitModel } from "../../shared/types";
import type { DeviceRow } from "../../shared/types";

/** Backend `REPORT_SECTIONS` ile AYNI anahtarlar ve AYNI sira. */
export type ReportSection =
  | "ozet"
  | "konum"
  | "kunye"
  | "baglanti"
  | "kanallar"
  | "sinyaller"
  | "haberlesme"
  | "setler"
  | "alarmlar"
  | "olaylar";

const TUM_BOLUMLER: ReportSection[] = [
  "ozet",
  "konum",
  "kunye",
  "baglanti",
  "kanallar",
  "sinyaller",
  "haberlesme",
  "setler",
  "alarmlar",
  "olaylar"
];

const DEPO_ANAHTARI = "e1.deviceReport.sections";

/** Bu cihaz turunde ANLAMLI olan bolumler. */
export function bolumlerFor(device: DeviceRow): ReportSection[] {
  const kit = isKitModel(device.model);
  return TUM_BOLUMLER.filter((bolum) => {
    // Kit OLCUM YAPMAZ: dokuz uydusu setlere yonlendirilir ve kendi
    // kaydinda yalnizca ortak RTU degerleri kalir.
    if ((bolum === "kanallar" || bolum === "sinyaller") && kit) return false;
    // "Bagli Setler" yalnizca fiziksel kit kaydinin konusu.
    if (bolum === "setler" && !kit) return false;
    return true;
  });
}

function kayittanOku(gecerli: ReportSection[]): ReportSection[] {
  try {
    const ham = window.localStorage.getItem(DEPO_ANAHTARI);
    if (!ham) return gecerli;
    const okunan = JSON.parse(ham);
    if (!Array.isArray(okunan)) return gecerli;
    const secili = gecerli.filter((b) => okunan.includes(b));
    // Hicbiri kalmadiysa (baska turde bir cihaz icin kaydedilmis secim)
    // varsayilana don: bos rapor uretmek kaydedilmis tercihe uymaktan kotu.
    return secili.length > 0 ? secili : gecerli;
  } catch {
    return gecerli;
  }
}

type Props = {
  device: DeviceRow;
  busy: boolean;
  onKapat: () => void;
  /** Secilen bolumlerle indirmeyi baslatir. */
  onIndir: (sections: ReportSection[]) => void;
};

export function DeviceReportModal({ device, busy, onKapat, onIndir }: Props) {
  const { t } = useTranslation();
  const kutuRef = useModalDialog<HTMLDivElement>(onKapat);
  const gecerli = useMemo(() => bolumlerFor(device), [device]);
  const [secili, setSecili] = useState<ReportSection[]>(() => kayittanOku(gecerli));

  // Cihaz degisirse (sekmeler arasi gecis) gecersiz kalan bolumler dusulur.
  useEffect(() => {
    setSecili((onceki) => {
      const kalan = onceki.filter((b) => gecerli.includes(b));
      return kalan.length > 0 ? kalan : gecerli;
    });
  }, [gecerli]);

  const degistir = useCallback((bolum: ReportSection) => {
    setSecili((onceki) =>
      onceki.includes(bolum) ? onceki.filter((b) => b !== bolum) : [...onceki, bolum]
    );
  }, []);

  const indir = useCallback(() => {
    // Sira BACKEND'DEKI sira olsun: kullanici kutucuklara hangi sirayla
    // tikladiysa o sirayla gonderirsek, ayni secim farkli URL uretir ve
    // tarayici onbellegi bosuna atlanir.
    const sirali = gecerli.filter((b) => secili.includes(b));
    try {
      window.localStorage.setItem(DEPO_ANAHTARI, JSON.stringify(sirali));
    } catch {
      // Depolama kapali/dolu olabilir — secim yine de calisir.
    }
    onIndir(sirali);
  }, [gecerli, secili, onIndir]);

  const hepsiSecili = secili.length === gecerli.length;

  return (
    <div className="drm-backdrop" role="presentation" onClick={onKapat}>
      <div
        className="drm"
        role="dialog"
        aria-modal="true"
        aria-labelledby="drm-baslik"
        ref={kutuRef}
        onClick={(e) => e.stopPropagation()}
      >
        <header className="drm-head">
          <span className="drm-ico">
            <FileDown size={17} strokeWidth={2.2} />
          </span>
          <div className="drm-head-body">
            <h2 id="drm-baslik">{t("deviceDetail.report.title")}</h2>
            <p>{t("deviceDetail.report.sub", { name: device.name })}</p>
          </div>
          <button
            type="button"
            className="drm-x"
            onClick={onKapat}
            aria-label={t("common.close")}
          >
            <X size={16} />
          </button>
        </header>

        <div className="drm-body">
          <button
            type="button"
            className="drm-toggle-all"
            onClick={() => setSecili(hepsiSecili ? [] : gecerli)}
          >
            {hepsiSecili
              ? t("deviceDetail.report.clearAll")
              : t("deviceDetail.report.selectAll")}
          </button>
          <ul className="drm-list">
            {gecerli.map((bolum) => (
              <li key={bolum}>
                <label className="drm-item">
                  <input
                    type="checkbox"
                    checked={secili.includes(bolum)}
                    onChange={() => degistir(bolum)}
                  />
                  <span className="drm-item-label">
                    {t(`deviceDetail.report.sections.${bolum}`)}
                  </span>
                </label>
              </li>
            ))}
          </ul>
        </div>

        <footer className="drm-foot">
          {/* Hicbir bolum secilmemisken indirme KAPALI: backend bos secimi
              "hepsi" sayar (eski baglantilar bozulmasin diye) ve kullanici
              tam tersini bekledigi bir belge indirirdi. */}
          <span className="drm-count">
            {t("deviceDetail.report.count", { count: secili.length })}
          </span>
          <div className="drm-foot-actions">
            <button type="button" className="drm-btn" onClick={onKapat}>
              {t("common.cancel")}
            </button>
            <button
              type="button"
              className="drm-btn is-primary"
              onClick={indir}
              disabled={busy || secili.length === 0}
              aria-busy={busy}
            >
              {busy ? (
                <span className="btn-spinner" aria-hidden="true" />
              ) : (
                <FileDown size={15} />
              )}
              {busy
                ? t("deviceDetail.exportPdfBusy")
                : t("deviceDetail.report.download")}
            </button>
          </div>
        </footer>
      </div>
    </div>
  );
}
