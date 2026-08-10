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
 * Cizim harici kutuphane KULLANMAZ: mevcut bagimliliklarla (CSS + inline SVG)
 * yapiliyor. Bir grafik kutuphanesi eklemek paket boyutunu ve saha cihazinda
 * yuklenme suresini bu ekranin degerinden fazla buyuturdu.
 */
import { useCallback, useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { AlertTriangle, Gauge, MapPin, Repeat, TrendingUp } from "lucide-react";

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

/** Yatay cubuk — en buyuk degere gore olceklenir. */
function Bar({ value, max, tone }: { value: number; max: number; tone?: string }) {
  const pct = max > 0 ? Math.max(2, (value / max) * 100) : 0;
  return (
    <span className="fa-bar">
      <span
        className={`fa-bar-fill${tone ? ` fa-bar-fill--${tone}` : ""}`}
        style={{ width: `${pct}%` }}
      />
    </span>
  );
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

  const maxCause = useMemo(
    () => Math.max(1, ...(data?.cause_distribution ?? []).map((c) => c.count)),
    [data]
  );
  const maxLine = useMemo(
    () => Math.max(1, ...(data?.top_lines ?? []).map((c) => c.count)),
    [data]
  );
  const maxMonth = useMemo(
    () => Math.max(1, ...(data?.monthly_trend ?? []).map((c) => c.count)),
    [data]
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
        {/* ---- En cok ariza cikaran hatlar ---- */}
        <section className="rad-card fa-card">
          <header className="rad-card-head">
            <h3>{t("faultAnalytics.topLines")}</h3>
          </header>
          {data?.top_lines.length ? (
            <ul className="fa-list">
              {data.top_lines.map((l) => (
                <li key={l.line_id}>
                  <span className="fa-list-label" title={l.code}>
                    {l.name}
                  </span>
                  <Bar value={l.count} max={maxLine} />
                  <strong className="fa-list-count">{l.count}</strong>
                </li>
              ))}
            </ul>
          ) : (
            <p className="net-empty">{t("faultAnalytics.noData")}</p>
          )}
        </section>

        {/* ---- Tekrarlayan aciklikar — bakim onceliklendirmesinin
             en dogrudan girdisi ---- */}
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
                  <strong className="fa-list-count fa-list-count--hot">{s.count}×</strong>
                </li>
              ))}
            </ul>
          ) : (
            <p className="net-empty">{t("faultAnalytics.noRepeats")}</p>
          )}
        </section>

        {/* ---- Sebep dagilimi ---- */}
        <section className="rad-card fa-card">
          <header className="rad-card-head">
            <h3>{t("faultAnalytics.causeDistribution")}</h3>
            {ozet ? <small>{t("faultAnalytics.ofLabeled", { count: ozet.labeled })}</small> : null}
          </header>
          {data?.cause_distribution.length ? (
            <ul className="fa-list">
              {data.cause_distribution.map((c) => (
                <li key={c.cause_code}>
                  <span className="fa-list-label">{causeLabel(c.cause_code)}</span>
                  <Bar value={c.count} max={maxCause} tone="cause" />
                  <strong className="fa-list-count">{c.count}</strong>
                </li>
              ))}
            </ul>
          ) : (
            <p className="net-empty">{t("faultAnalytics.noCauses")}</p>
          )}
        </section>

        {/* ---- Kural isabeti — LLM eklemeden ONCE bakilmasi gereken sayi ---- */}
        <section className="rad-card fa-card">
          <header className="rad-card-head">
            <h3>{t("faultAnalytics.ruleAccuracy")}</h3>
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
                      <span aria-hidden="true">→</span>
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

        {/* ---- Faz dagilimi ---- */}
        <section className="rad-card fa-card">
          <header className="rad-card-head">
            <h3>{t("faultAnalytics.phaseDistribution")}</h3>
            <small>{t("faultAnalytics.phaseHint")}</small>
          </header>
          {data?.phase_distribution.length ? (
            <ul className="fa-chips">
              {data.phase_distribution.map((p) => (
                <li key={p.phase}>
                  <span className="fa-chip-phase">{p.phase.toUpperCase()}</span>
                  <strong>{p.count}</strong>
                </li>
              ))}
            </ul>
          ) : (
            <p className="net-empty">{t("faultAnalytics.noData")}</p>
          )}
        </section>

        {/* ---- Aylik egilim ---- */}
        <section className="rad-card fa-card fa-card--wide">
          <header className="rad-card-head">
            <h3>{t("faultAnalytics.monthlyTrend")}</h3>
            <small>{t("faultAnalytics.monthlyHint")}</small>
          </header>
          {data?.monthly_trend.length ? (
            <div className="fa-trend">
              {data.monthly_trend.map((m) => (
                <div className="fa-trend-col" key={m.month} title={`${m.month}: ${m.count}`}>
                  <span
                    className="fa-trend-fill"
                    style={{ height: `${Math.max(4, (m.count / maxMonth) * 100)}%` }}
                  />
                  <small>{m.month.slice(5)}</small>
                </div>
              ))}
            </div>
          ) : (
            <p className="net-empty">{t("faultAnalytics.noData")}</p>
          )}
        </section>
      </div>
    </section>
  );
}
