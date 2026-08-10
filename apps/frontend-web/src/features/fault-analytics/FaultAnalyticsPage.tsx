/**
 * Ariza Analizi — hangi hat, hangi bolge, hangi sebep, ne kadar surede.
 *
 * BU EKRAN BAKIM BUTCESINI YONLENDIRECEK. O yuzden iki tasarim kurali var:
 *
 * 1. VERI KALITESI GIZLENMEZ. Sebep dagilimi, kayitlarin yalnizca %5'i
 *    etiketliyken de cizilebilir — ama boyle bir grafigi "en sik sebep agac
 *    temasi" diye okumak uydurma bir bulgudur. Etiketlenme orani dagilimin
 *    YANINDA, dusukse uyari seridiyle gosterilir.
 *
 * 2. BOS DURUM DURUSTCE SOYLENIR. Veri yokken bos grafik cizmek "ariza yok"
 *    gibi okunur; oysa dogru mesaj "henuz veri birikmedi"dir.
 *
 * CIZIM: echarts. Onceden inline SVG ile ciziliyordu ve gerekcesi "kutuphane
 * eklemek paket boyutunu buyutur"du. O gerekce artik gecerli degil — echarts
 * ZATEN bagimlilik (cihaz detay grafikleri kullaniyor) ve bu sayfa lazy
 * yukleniyor. Kazanc gorsel degil islevsel: her seride ipucu/hover
 * kendiliginden gelir, elde cizilen SVG'de bunlar yoktu.
 *
 * 3. BOLGE DAGILIMI EKLENDI. Backend `top_regions` uretiyordu ama ekran onu
 *    HIC gostermiyordu; hesaplanip atilan bir veriydi.
 */
import { useCallback, useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { AlertTriangle, Gauge, Map as MapIcon, MapPin, Repeat, TrendingUp } from "lucide-react";

import { EgilimGrafigi, FazGrafigi, SiralamaGrafigi } from "./FaultCharts";

import { fetchFaultAnalytics, fetchFaultCauses } from "../../shared/api";
import type { FaultAnalytics, FaultCauseCatalog } from "../../shared/types";
import { usePolling } from "../../shared/usePolling";

type Props = {
  accessToken: string;
};

/** Secilebilir pencereler. 365 varsayilan: mevsimselligi (yaz firtinasi /
 *  kis buzlanmasi) tam bir dongu olarak icerir. */
const WINDOWS = [30, 90, 365, 1095] as const;

/** Etiketlenme orani bunun altindaysa sebep dagilimi UYARIYLA sunulur.
 *  Kesin bir esik yok; amac "bu grafige dayanip karar verme" demek. */
const LOW_LABEL_RATIO = 0.4;

function yuzde(x: number): string {
  return `${Math.round(x * 100)}%`;
}

export function FaultAnalyticsPage({ accessToken }: Props) {
  const { t, i18n } = useTranslation();
  const [days, setDays] = useState<number>(365);
  const [data, setData] = useState<FaultAnalytics | null>(null);
  const [catalog, setCatalog] = useState<FaultCauseCatalog | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    try {
      const next = await fetchFaultAnalytics(accessToken, days);
      setData(next);
      setError(null);
    } catch (exc) {
      const msg = exc instanceof Error ? exc.message : t("common.errorOccurred");
      // 401 sentinel'i ekrana BASILMAZ: oturum yenilenirken gecici gorulebilir.
      if (msg !== "session_polling_401") setError(msg);
    } finally {
      setLoading(false);
    }
  }, [accessToken, days, t]);

  useEffect(() => {
    setLoading(true);
    void load();
  }, [load]);

  // Analiz penceresi gunler/aylar olceginde; sik yenilemenin anlami yok.
  usePolling({ enabled: true, intervalMs: 120_000, fn: load });

  useEffect(() => {
    let iptal = false;
    fetchFaultCauses(accessToken)
      .then((k) => {
        if (!iptal) setCatalog(k);
      })
      .catch(() => {
        // Katalog yoksa sebep kodlari HAM gosterilir; ekran calismaya devam.
        if (!iptal) setCatalog(null);
      });
    return () => {
      iptal = true;
    };
  }, [accessToken]);

  /** Sebep kodu -> okunabilir etiket. Katalog gelmediyse kodun kendisi. */
  const causeLabel = useCallback(
    (code: string) => {
      const c = catalog?.causes.find((x) => x.code === code);
      if (!c) return code;
      return i18n.language?.startsWith("tr") ? c.label_tr : c.label_en;
    },
    [catalog, i18n.language]
  );

  const ozet = data?.summary;
  const dusukEtiket =
    ozet !== undefined && ozet.total > 0 && ozet.labeled_ratio < LOW_LABEL_RATIO;

  /** Faz kodu -> okunabilir etiket ("a" -> "L1"). */
  const fazLabel = useCallback(
    (kod: string) =>
      ({ a: "L1", b: "L2", c: "L3", abc: t("faultAnalytics.allPhases") })[kod] ??
      kod.toUpperCase(),
    [t]
  );

  return (
    <section className="tab-panel fa-page">
      {/* ---- Ust serit: pencere secimi + ozet ---- */}
      <div className="net-access-bar fa-bar-top">
        <div className="net-access-item">
          <span className="net-access-icon">
            <TrendingUp size={16} />
          </span>
          <span className="net-access-body">
            <span className="net-access-label">{t("faultAnalytics.window")}</span>
            <select
              className="fa-window-select"
              value={days}
              onChange={(e) => setDays(Number(e.target.value))}
            >
              {WINDOWS.map((w) => (
                <option key={w} value={w}>
                  {t("faultAnalytics.windowDays", { days: w })}
                </option>
              ))}
            </select>
          </span>
        </div>

        <span className="net-access-sep" aria-hidden="true" />

        <div className="net-access-item">
          <span className="net-access-icon">
            <AlertTriangle size={16} />
          </span>
          <span className="net-access-body">
            <span className="net-access-label">{t("faultAnalytics.totalFaults")}</span>
            <strong className="net-access-value">
              {ozet?.total ?? "—"}
              {ozet && ozet.open > 0 ? (
                <em className="net-access-sub">
                  {t("faultAnalytics.stillOpen", { count: ozet.open })}
                </em>
              ) : null}
            </strong>
          </span>
        </div>

        <span className="net-access-sep" aria-hidden="true" />

        <div className="net-access-item">
          <span className="net-access-icon">
            <Gauge size={16} />
          </span>
          <span className="net-access-body">
            <span className="net-access-label">{t("faultAnalytics.mttr")}</span>
            <strong className="net-access-value">
              {ozet?.mttr_hours != null
                ? t("faultAnalytics.hours", { value: ozet.mttr_hours })
                : "—"}
              <em className="net-access-sub">{t("faultAnalytics.mttrClosedOnly")}</em>
            </strong>
          </span>
        </div>

        <span className="net-access-sep" aria-hidden="true" />

        <div className={`net-access-item ${dusukEtiket ? "is-warn" : ""}`}>
          <span className="net-access-icon">
            <MapPin size={16} />
          </span>
          <span className="net-access-body">
            <span className="net-access-label">{t("faultAnalytics.labeled")}</span>
            <strong className="net-access-value">
              {ozet ? `${ozet.labeled} / ${ozet.total}` : "—"}
              {ozet ? (
                <em className="net-access-sub">{yuzde(ozet.labeled_ratio)}</em>
              ) : null}
            </strong>
          </span>
        </div>
      </div>

      {error ? <p className="net-banner net-banner--bad">{error}</p> : null}

      {/* BOS DURUM: bos grafik cizmek "ariza yok" gibi okunur; dogru mesaj
          "henuz veri birikmedi"dir. */}
      {!loading && ozet && ozet.total === 0 ? (
        <p className="net-banner net-banner--info">
          <TrendingUp size={16} />
          {t("faultAnalytics.emptyWindow")}
        </p>
      ) : null}

      {/* Etiketlenme dusukse sebep dagilimi TEK BASINA okunmamali. */}
      {dusukEtiket ? (
        <p className="net-banner net-banner--warn">
          <AlertTriangle size={16} />
          {t("faultAnalytics.lowLabelWarning", {
            percent: yuzde(ozet!.labeled_ratio)
          })}
        </p>
      ) : null}

      <div className="fa-grid">
        {/* ---- Aylik egilim — EN USTTE ve tam genislikte.
             Once zaman baglami: "artiyor mu, azaliyor mu, mevsimsel mi".
             Alttaki siralama kartlari o baglamin icinde okunur. ---- */}
        <section className="rad-card fa-card fa-card--wide">
          <header className="rad-card-head">
            <h3>
              <TrendingUp size={16} />
              {t("faultAnalytics.monthlyTrend")}
            </h3>
            <small>{t("faultAnalytics.monthlyHint")}</small>
          </header>
          {data?.monthly_trend.length ? (
            <EgilimGrafigi
              points={data.monthly_trend}
              labelToplam={t("faultAnalytics.faultUnit")}
            />
          ) : (
            <p className="net-empty">{t("faultAnalytics.noData")}</p>
          )}
        </section>

        {/* ---- En cok ariza cikaran hatlar ---- */}
        <section className="rad-card fa-card">
          <header className="rad-card-head">
            <h3>{t("faultAnalytics.topLines")}</h3>
            <small>{t("faultAnalytics.topLinesHint")}</small>
          </header>
          {data?.top_lines.length ? (
            <SiralamaGrafigi
              items={data.top_lines.map((l) => ({ label: l.name, value: l.count }))}
              birim={t("faultAnalytics.faultUnit")}
            />
          ) : (
            <p className="net-empty">{t("faultAnalytics.noData")}</p>
          )}
        </section>

        {/* ---- Bolge dagilimi.
             Backend bunu zaten uretiyordu ama ekran GOSTERMIYORDU. Hat
             siralamasi "hangi hat" der; bolge siralamasi "hangi ekibin
             sahasi" der — bakim planlamasinda ikisi ayri sorudur. ---- */}
        <section className="rad-card fa-card">
          <header className="rad-card-head">
            <h3>
              <MapIcon size={16} />
              {t("faultAnalytics.topRegions")}
            </h3>
            <small>{t("faultAnalytics.topRegionsHint")}</small>
          </header>
          {data?.top_regions.length ? (
            <SiralamaGrafigi
              items={data.top_regions.map((r) => ({ label: r.name, value: r.count }))}
              birim={t("faultAnalytics.faultUnit")}
            />
          ) : (
            <p className="net-empty">{t("faultAnalytics.noData")}</p>
          )}
        </section>

        {/* ---- Sebep dagilimi ---- */}
        <section className="rad-card fa-card">
          <header className="rad-card-head">
            <h3>{t("faultAnalytics.causeDistribution")}</h3>
            {ozet ? (
              <small>{t("faultAnalytics.ofLabeled", { count: ozet.labeled })}</small>
            ) : null}
          </header>
          {data?.cause_distribution.length ? (
            <>
              {/* Etiketlenme dusukse grafigin YANINDA soylenir; kart tek
                  basina kopyalanip "en sik sebep bu" diye okunmasin. */}
              {dusukEtiket ? (
                <p className="fa-inline-warn">
                  <AlertTriangle size={13} />
                  {t("faultAnalytics.lowLabelInline", {
                    percent: yuzde(ozet!.labeled_ratio)
                  })}
                </p>
              ) : null}
              <SiralamaGrafigi
                items={data.cause_distribution.map((c) => ({
                  label: causeLabel(c.cause_code),
                  value: c.count
                }))}
                birim={t("faultAnalytics.faultUnit")}
              />
            </>
          ) : (
            <p className="net-empty">{t("faultAnalytics.noCauses")}</p>
          )}
        </section>

        {/* ---- Faz dagilimi ---- */}
        <section className="rad-card fa-card">
          <header className="rad-card-head">
            <h3>{t("faultAnalytics.phaseDistribution")}</h3>
            <small>{t("faultAnalytics.phaseHint")}</small>
          </header>
          {data?.phase_distribution.length ? (
            <FazGrafigi
              items={data.phase_distribution.map((p) => ({
                phase: p.phase,
                count: p.count,
                label: fazLabel(p.phase)
              }))}
              birim={t("faultAnalytics.faultUnit")}
            />
          ) : (
            <p className="net-empty">{t("faultAnalytics.noData")}</p>
          )}
        </section>

        {/* ---- Tekrarlayan aciklikar — bakim onceliklendirmesinin
             en dogrudan girdisi. Grafik DEGIL liste: burada aranan sey
             "hangi aciklik" ve "kac kez", ikisi de metin. ---- */}
        <section className="rad-card fa-card">
          <header className="rad-card-head">
            <h3>
              <Repeat size={16} />
              {t("faultAnalytics.repeatSpans")}
            </h3>
            <small>{t("faultAnalytics.repeatSpansHint")}</small>
          </header>
          {data?.repeat_spans.length ? (
            <ul className="fa-list fa-list--spans">
              {data.repeat_spans.map((s) => (
                <li key={`${s.from_pole_id}-${s.to_pole_id}`}>
                  <span className="fa-list-label">
                    {s.line_name}
                    <em>
                      {t("faultAnalytics.spanRange", {
                        from: s.from_pole_seq ?? "?",
                        to: s.to_pole_seq ?? "?"
                      })}
                    </em>
                  </span>
                  <strong className="fa-list-count fa-list-count--hot">{s.count}x</strong>
                </li>
              ))}
            </ul>
          ) : (
            <p className="net-empty">{t("faultAnalytics.noRepeats")}</p>
          )}
        </section>

        {/* ---- Kural isabeti — cikarim katmanina guvenmeden ONCE
             bakilmasi gereken sayi ---- */}
        <section className="rad-card fa-card">
          <header className="rad-card-head">
            <h3>
              <Gauge size={16} />
              {t("faultAnalytics.ruleAccuracy")}
            </h3>
            <small>{t("faultAnalytics.ruleAccuracyHint")}</small>
          </header>
          {data && data.rule_accuracy.accuracy !== null ? (
            <>
              <div className="fa-accuracy">
                <strong>{yuzde(data.rule_accuracy.accuracy)}</strong>
                <span>
                  {t("faultAnalytics.accuracyOf", {
                    agreed: data.rule_accuracy.agreed,
                    total: data.rule_accuracy.comparable
                  })}
                </span>
              </div>
              {data.rule_accuracy.top_mismatches.length ? (
                <ul className="fa-mismatch">
                  {data.rule_accuracy.top_mismatches.map((m) => (
                    <li key={`${m.suggested}->${m.actual}`}>
                      <span className="fa-mismatch-from">{causeLabel(m.suggested)}</span>
                      <span aria-hidden="true">-&gt;</span>
                      <span className="fa-mismatch-to">{causeLabel(m.actual)}</span>
                      <strong>{m.count}</strong>
                    </li>
                  ))}
                </ul>
              ) : null}
            </>
          ) : (
            <p className="net-empty">{t("faultAnalytics.noComparable")}</p>
          )}
        </section>
      </div>
    </section>
  );
}
