/**
 * FaultResolveModal — ariza sebebi / cozumu ve KAPATMA diyalogu.
 *
 * NEDEN AYRI BIR EKRAN
 * --------------------
 * Once bu alanlar detay sayfasinin en altinda iki kart halinde duruyordu;
 * sonra "islem popup'i" adiyla sebep + saha yorumlari AYNI kutuya konuldu.
 * Ikisi de yanlisti:
 *
 *   1. Sebep bir SINIFLANDIRMADIR (analiz icin, kapali kume), saha yorumu
 *      ise SERBEST metinli bir sohbet akisidir. Yan yana durunca kullanici
 *      hangisinin kalici kayit oldugunu ayirt edemiyordu.
 *   2. Kapatma, sonucu geri alinamayan bir karardir; sayfanin dibindeki bir
 *      kutuya sikistirilacak is degil.
 *
 * Artik: sebep + cozum BURADA, saha yorumlari FaultFieldReportModal'da.
 *
 * IKI KIP
 * -------
 *   "duzenle" : sebep + kisa not kaydedilir. Ariza henuz normale donmemis
 *               olabilir; kapatma dugmesi yoktur, bunun yerine kapatmanin
 *               neden kilitli oldugu yazar.
 *   "kapat"   : sebep + COZUM NOTU. Cozum notu ZORUNLU (backend de dogrular)
 *               ve kapanisla birlikte kalici kayda gecer.
 *
 * TASLAKLAR ACILISTA BIR KEZ ALINIR: diyalog kapaninca unmount olur, bu
 * yuzden ayrica sifirlamak gerekmez. Arka planda ariza listesi tazelense de
 * kullanicinin yazdigi silinmez.
 */
import { useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { Check, Lightbulb, Lock, Save, Wrench, X } from "lucide-react";

import { useModalDialog } from "../../shared/useModalDialog";
import type { FaultCause, FaultCauseCatalog, FaultEvent } from "../../shared/types";

type Props = {
  fault: FaultEvent;
  /** "kapat" = kapanis akisi (cozum notu zorunlu), "duzenle" = yalniz kayit. */
  mod: "kapat" | "duzenle";
  /** Backend katalogu. null ise sebep secimi devre disi kalir. */
  catalog: FaultCauseCatalog | null;
  onKapat: () => void;
  /** Hepsi HATA FIRLATIR; diyalog hatayi kendi icinde gosterir ve acik kalir. */
  onSebepKaydet: (kod: string | null, ayrinti: string | null) => Promise<void>;
  onNotKaydet: (not: string | null) => Promise<void>;
  onArizayiKapat: (cozumNotu: string) => Promise<void>;
};

export function FaultResolveModal({
  fault,
  mod,
  catalog,
  onKapat,
  onSebepKaydet,
  onNotKaydet,
  onArizayiKapat
}: Props) {
  const { t, i18n } = useTranslation();
  const trDili = Boolean(i18n.language?.startsWith("tr"));
  const kutuRef = useModalDialog<HTMLDivElement>(onKapat);
  const kapatmaModu = mod === "kapat";

  const [sebep, setSebep] = useState(fault.cause_code ?? "");
  const [ayrinti, setAyrinti] = useState(fault.cause_detail ?? "");
  const [kisaNot, setKisaNot] = useState(fault.note ?? "");
  const [cozumNotu, setCozumNotu] = useState(fault.resolution_note ?? "");
  const [calisiyor, setCalisiyor] = useState(false);
  const [hata, setHata] = useState("");

  const etiket = (c: FaultCause) => (trDili ? c.label_tr : c.label_en);

  /** Aileye gore gruplanmis secim listesi — duz 19'luk liste taranmasi zor. */
  const gruplar = useMemo<[string, FaultCause[]][]>(() => {
    if (!catalog) return [];
    const harita = new Map<string, FaultCause[]>();
    for (const grup of catalog.groups) harita.set(grup, []);
    for (const c of catalog.causes) {
      const liste = harita.get(c.group);
      if (liste) liste.push(c);
      else harita.set(c.group, [c]);
    }
    return [...harita.entries()].filter(([, liste]) => liste.length > 0);
  }, [catalog]);

  /** Kuralin onerdigi sebep. SECILI GELMEZ — operator onaylamadan bir etiket
   *  "girilmis" sayilirsa istatistik, kimsenin bakmadigi bir tahminle dolar. */
  const oneri = useMemo(() => {
    if (!catalog || fault.cause_code) return null;
    const kod = fault.auto_cause_code;
    if (!kod) return null;
    const c = catalog.causes.find((x) => x.code === kod);
    return c ? { code: c.code, label: etiket(c) } : null;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [catalog, fault.cause_code, fault.auto_cause_code, trDili]);

  const sebepKirli =
    (fault.cause_code ?? "") !== sebep || (fault.cause_detail ?? "") !== ayrinti.trim();
  const notKirli = (fault.note ?? "") !== kisaNot.trim();

  const gonderilebilir = kapatmaModu
    ? Boolean(cozumNotu.trim())
    : sebepKirli || notKirli;

  const yurut = async () => {
    setCalisiyor(true);
    setHata("");
    try {
      // Yalnizca DEGISENI gonder: her kaydette iki ucu birden cagirmak olay
      // kaydini (record_event) sahte "guncellendi" satirlariyla doldururdu.
      if (sebepKirli) await onSebepKaydet(sebep || null, ayrinti.trim() || null);
      if (kapatmaModu) await onArizayiKapat(cozumNotu.trim());
      else if (notKirli) await onNotKaydet(kisaNot.trim() || null);
      onKapat();
    } catch (err) {
      setHata(err instanceof Error ? err.message : t("common.errorOccurred"));
      setCalisiyor(false);
    }
  };

  return (
    <div className="fdm-backdrop" role="presentation" onClick={onKapat}>
      <div
        className="fdm"
        role="dialog"
        aria-modal="true"
        aria-labelledby="fdm-baslik"
        ref={kutuRef}
        onClick={(e) => e.stopPropagation()}
      >
        <header className="fdm-head">
          <span className={`fdm-ico ${kapatmaModu ? "is-close" : ""}`}>
            {kapatmaModu ? (
              <Check size={17} strokeWidth={2.8} />
            ) : (
              <Wrench size={17} strokeWidth={2.2} />
            )}
          </span>
          <div className="fdm-head-body">
            <h2 id="fdm-baslik">
              {kapatmaModu ? t("faults.detail.closeFault") : t("faults.detail.solveTitle")}
            </h2>
            <p>
              {kapatmaModu
                ? t("faults.detail.closeDialogSub")
                : t("faults.detail.solveDialogSub")}
            </p>
          </div>
          <span className="fdm-rec">
            {fault.line_name}
            <em>#{fault.id}</em>
          </span>
          <button
            type="button"
            className="fdm-x"
            onClick={onKapat}
            aria-label={t("common.close")}
          >
            <X size={16} />
          </button>
        </header>

        <div className="fdm-body">
          {/* ---- 1) SEBEP ---- */}
          <section className="fdm-sec">
            <header className="fdm-sec-head">
              <span className="fdm-sec-no">1</span>
              <h3>{t("faults.detail.causeTitle")}</h3>
              <span className="fdm-badge">{t("faults.detail.optional")}</span>
            </header>
            <p className="fdm-sec-hint">{t("faults.detail.causeHint")}</p>

            {oneri ? (
              <div className="fd-suggestion">
                <Lightbulb size={14} />
                <span>{t("faults.detail.causeSuggested", { cause: oneri.label })}</span>
                {sebep !== oneri.code ? (
                  <button type="button" onClick={() => setSebep(oneri.code)}>
                    {t("faults.detail.causeUseSuggestion")}
                  </button>
                ) : null}
              </div>
            ) : null}

            {/* SEBEP: acilir liste degil ETIKET IZGARASI. 19 sebebi bir
                dropdown icinde aramak, eldivenli elle telefonun basindayken en
                yavas yoldu. Secili etikete tekrar basmak secimi GERI ALIR. */}
            {catalog === null ? (
              <p className="fd-empty">{t("faults.detail.causeCatalogEmpty")}</p>
            ) : (
              <div className="fd-causes">
                {gruplar.map(([grup, liste]) => (
                  <div key={grup} className="fd-cause-group">
                    <span className="fd-cause-group-label">
                      {t(`faults.causeGroup.${grup}`, { defaultValue: grup })}
                    </span>
                    <div className="fd-chips">
                      {liste.map((c) => (
                        <button
                          key={c.code}
                          type="button"
                          className={`fd-chip ${sebep === c.code ? "is-on" : ""}`}
                          disabled={calisiyor}
                          onClick={() => setSebep(sebep === c.code ? "" : c.code)}
                        >
                          {sebep === c.code ? <Check size={12} strokeWidth={3} /> : null}
                          {etiket(c)}
                        </button>
                      ))}
                    </div>
                  </div>
                ))}
              </div>
            )}

            <label className="fd-field">
              <span className="fd-label">{t("faults.detail.causeDetailLabel")}</span>
              <textarea
                className="fd-textarea"
                rows={2}
                value={ayrinti}
                onChange={(e) => setAyrinti(e.target.value)}
                disabled={calisiyor}
                placeholder={t("faults.detail.causeDetailPlaceholder")}
              />
            </label>
          </section>

          {/* ---- 2) COZUM NOTU (kapatma) / KISA NOT (duzenleme) ---- */}
          <section className="fdm-sec">
            <header className="fdm-sec-head">
              <span className="fdm-sec-no">2</span>
              <h3>
                {kapatmaModu
                  ? t("faults.detail.resolutionNote")
                  : t("faults.detail.writeNote")}
              </h3>
              <span className={`fdm-badge ${kapatmaModu ? "is-req" : ""}`}>
                {kapatmaModu ? t("faults.detail.required") : t("faults.detail.optional")}
              </span>
            </header>
            <p className="fdm-sec-hint">
              {kapatmaModu
                ? t("faults.detail.resolutionNoteHint")
                : t("faults.detail.writeNoteHint")}
            </p>
            <textarea
              className="fd-textarea"
              rows={kapatmaModu ? 4 : 3}
              value={kapatmaModu ? cozumNotu : kisaNot}
              onChange={(e) =>
                kapatmaModu ? setCozumNotu(e.target.value) : setKisaNot(e.target.value)
              }
              disabled={calisiyor}
              placeholder={
                kapatmaModu
                  ? t("faults.detail.resolutionNotePlaceholder")
                  : t("faults.detail.writeNotePlaceholder")
              }
            />
          </section>

          {hata ? <p className="fd-error">{hata}</p> : null}
        </div>

        <footer className="fdm-foot">
          {/* Kapatma neden yapilamiyor — dugmenin kayip olmasini aciklar. */}
          {!kapatmaModu && !fault.resolved_at && fault.status !== "closed" ? (
            <span className="fdm-lock">
              <Lock size={13} />
              {t("faults.detail.closeLocked")}
            </span>
          ) : (
            <span />
          )}
          <div className="fdm-foot-btns">
            <button
              type="button"
              className="fd-ghost-btn"
              onClick={onKapat}
              disabled={calisiyor}
            >
              {t("common.cancel")}
            </button>
            <button
              type="button"
              className={`fd-save ${kapatmaModu ? "fd-save--close" : ""}`}
              onClick={() => void yurut()}
              disabled={calisiyor || !gonderilebilir}
            >
              {kapatmaModu ? <Check size={14} strokeWidth={2.8} /> : <Save size={14} />}
              {kapatmaModu ? t("faults.detail.closeFault") : t("common.save")}
            </button>
          </div>
        </footer>
      </div>
    </div>
  );
}
