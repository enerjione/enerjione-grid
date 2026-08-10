/**
 * Ariza Analizi — hangi hat, hangi bolge, hangi sebep, ne kadar surede;
 * ustune sistemin ve cihazlarin kendi sagligi.
 *
 * BU EKRAN BAKIM BUTCESINI YONLENDIRECEK. O yuzden uc tasarim kurali var:
 *
 * 1. VERI KALITESI GIZLENMEZ. Sebep dagilimi, kayitlarin yalnizca %5'i
 *    etiketliyken de cizilebilir — ama boyle bir grafigi "en sik sebep agac
 *    temasi" diye okumak uydurma bir bulgudur. Etiketlenme orani dagilimin
 *    YANINDA, dusukse uyari seridiyle gosterilir.
 *
 * 2. BOS DURUM DURUSTCE SOYLENIR. Veri yokken bos grafik cizmek "ariza yok"
 *    gibi okunur; oysa dogru mesaj "henuz veri birikmedi"dir.
 *
 * 3. HER SAYI TIKLANABILIR BIR OLCUYE DAYANIR. Isi haritasindaki leke deseni
 *    gosterir, ustundeki isaretci KESIN adedi verir.
 *
 * NEDEN SEKMELI
 * -------------
 * Once tek bir kart izgarasiydi; ustune sistem sagligi, cihaz sagligi, isi
 * haritasi ve akis diyagrami gelince 15+ kartlik bir duvara donusuyordu.
 * Sekmeler yalnizca duzen tercihi degil, PERFORMANS karari: harita ve akis
 * yalnizca kendi sekmesi acilinca MONTE EDILIR, sistem/cihaz sagligi da
 * yalnizca o an CEKILIR. 600 cihazli bir sahada bu sorgular ucuz degil ve
 * kimse hepsine ayni anda bakmiyor.
 *
 * CIZIM: echarts. Onceden inline SVG ile ciziliyordu ve gerekcesi "kutuphane
 * eklemek paket boyutunu buyutur"du. O gerekce artik gecerli degil — echarts
 * ZATEN bagimlilik (cihaz detay grafikleri kullaniyor) ve bu sayfa lazy
 * yukleniyor. Sankey de echarts'in icinde: yeni kutuphane gerekmedi.
 */
import { Suspense, lazy, useCallback, useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import {
  Activity,
  AlertTriangle,
  BatteryLow,
  BellRing,
  Gauge,
  Map as MapIcon,
  MapPin,
  Radio,
  Repeat,
  Share2,
  SignalLow,
  TrendingUp,
  Unplug
} from "lucide-react";

import {
  EgilimGrafigi,
  FazGrafigi,
  SaatProfiliGrafigi,
  SankeyGrafigi,
  SiralamaGrafigi
} from "./FaultCharts";

import {
  fetchDeviceHealth,
  fetchFaultAnalytics,
  fetchFaultCauses,
  fetchSystemHealth
} from "../../shared/api";
import type {
  DeviceHealth,
  FaultAnalytics,
  FaultCauseCatalog,
  SystemHealth
} from "../../shared/types";
import { usePolling } from "../../shared/usePolling";

/** Harita Leaflet + karo katmani getirir; yalnizca kendi sekmesi acilinca
 *  yuklensin. Analiz sayfasinin ilk acilisi bunu odemesin. */
const FaultHeatMap = lazy(() =>
  import("./FaultHeatMap").then((m) => ({ default: m.FaultHeatMap }))
);

type Props = {
  accessToken: string;
};

/** Secilebilir pencereler. 365 varsayilan: mevsimselligi (yaz firtinasi /
 *  kis buzlanmasi) tam bir dongu olarak icerir. */
const WINDOWS = [30, 90, 365, 1095] as const;

/** Etiketlenme orani bunun altindaysa sebep dagilimi UYARIYLA sunulur.
 *  Kesin bir esik yok; amac "bu grafige dayanip karar verme" demek. */
const LOW_LABEL_RATIO = 0.4;

type Sekme = "faults" | "map" | "system" | "devices";

const SEKMELER: { key: Sekme; labelKey: string; Icon: typeof TrendingUp }[] = [
  { key: "faults", labelKey: "faultAnalytics.tabFaults", Icon: TrendingUp },
  { key: "map", labelKey: "faultAnalytics.tabMap", Icon: MapIcon },
  { key: "system", labelKey: "faultAnalytics.tabSystem", Icon: Activity },
  { key: "devices", labelKey: "faultAnalytics.tabDevices", Icon: Radio }
];

function yuzde(x: number): string {
  return `${Math.round(x * 100)}%`;
}

/** Tarayicinin UTC'ye gore kaymasi (saat). Backend saat profilini UTC
 *  kovalarinda veriyor; "her aksam 19'da dusuyor" ancak YEREL saatte
 *  anlamlidir. */
function utcKaymasiSaat(): number {
  return -new Date().getTimezoneOffset() / 60;
}

function kisaTarih(iso: string | null, dil: string): string {
  if (!iso) return "";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  return d.toLocaleString(dil, {
    day: "2-digit",
    month: "2-digit",
    hour: "2-digit",
    minute: "2-digit"
  });
}

export function FaultAnalyticsPage({ accessToken }: Props) {
  const { t, i18n } = useTranslation();
  const [days, setDays] = useState<number>(365);
  const [sekme, setSekme] = useState<Sekme>("faults");
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

      {/* ---- Sekmeler ---- */}
      <div className="fa-tabs" role="tablist">
        {SEKMELER.map(({ key, labelKey, Icon }) => (
          <button
            key={key}
            type="button"
            role="tab"
            aria-selected={sekme === key}
            className={`fa-tab ${sekme === key ? "is-active" : ""}`}
            onClick={() => setSekme(key)}
          >
            <Icon size={15} />
            {t(labelKey)}
          </button>
        ))}
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
      {dusukEtiket && sekme === "faults" ? (
        <p className="net-banner net-banner--warn">
          <AlertTriangle size={16} />
          {t("faultAnalytics.lowLabelWarning", {
            percent: yuzde(ozet!.labeled_ratio)
          })}
        </p>
      ) : null}

      {sekme === "faults" ? (
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
      ) : null}

      {sekme === "map" ? (
        <HaritaVeAkis
          accessToken={accessToken}
          days={days}
          analytics={data}
          fazLabel={fazLabel}
        />
      ) : null}

      {sekme === "system" ? <SistemSagligi accessToken={accessToken} days={days} /> : null}

      {sekme === "devices" ? <CihazSagligi accessToken={accessToken} days={days} /> : null}
    </section>
  );
}

// ---------------------------------------------------------------------------
// Harita & Akis
// ---------------------------------------------------------------------------

function HaritaVeAkis({
  accessToken,
  days,
  analytics,
  fazLabel
}: {
  accessToken: string;
  days: number;
  analytics: FaultAnalytics | null;
  fazLabel: (kod: string) => string;
}) {
  const { t } = useTranslation();
  const { veri, hata, yukleniyor } = useBolumVerisi(
    useCallback(() => fetchDeviceHealth(accessToken, days), [accessToken, days])
  );

  /** Sankey dugum adi -> ekran metni. Faz dugumleri sayfanin geri kalaniyla
   *  ayni dili konussun: faz dagilimi grafigi "L1" diyorsa akis da "L1"
   *  demeli, "A" degil. */
  const dugumEtiket = useCallback(
    (ad: string, kademe: string) => {
      const i = ad.indexOf(":");
      const saf = i === -1 ? ad : ad.slice(i + 1);
      return kademe === "phase" ? fazLabel(saf.toLowerCase()) : saf;
    },
    [fazLabel]
  );

  const noktalar = veri?.fault_heatmap ?? [];
  const sankey = analytics?.sankey;

  return (
    <div className="fa-grid">
      <section className="rad-card fa-card fa-card--wide">
        <header className="rad-card-head">
          <h3>
            <MapIcon size={16} />
            {t("faultAnalytics.heatmap")}
          </h3>
          <small>{t("faultAnalytics.heatmapHint")}</small>
        </header>
        {yukleniyor ? (
          <p className="net-empty">{t("faultAnalytics.loading")}</p>
        ) : hata ? (
          <p className="net-banner net-banner--bad">{hata}</p>
        ) : noktalar.length ? (
          <Suspense fallback={<p className="net-empty">{t("faultAnalytics.loading")}</p>}>
            <FaultHeatMap points={noktalar} />
          </Suspense>
        ) : (
          <p className="net-empty">{t("faultAnalytics.noHeat")}</p>
        )}
      </section>

      <section className="rad-card fa-card fa-card--wide">
        <header className="rad-card-head">
          <h3>
            <Share2 size={16} />
            {t("faultAnalytics.sankey")}
          </h3>
          <small>{t("faultAnalytics.sankeyHint")}</small>
        </header>
        {sankey && sankey.links.length ? (
          <SankeyGrafigi
            nodes={sankey.nodes}
            links={sankey.links}
            etiketle={dugumEtiket}
            birim={t("faultAnalytics.faultUnit")}
          />
        ) : (
          <p className="net-empty">{t("faultAnalytics.noSankey")}</p>
        )}
      </section>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Sistem Sagligi
// ---------------------------------------------------------------------------

function SistemSagligi({ accessToken, days }: { accessToken: string; days: number }) {
  const { t, i18n } = useTranslation();
  const { veri, hata, yukleniyor } = useBolumVerisi(
    useCallback(() => fetchSystemHealth(accessToken, days), [accessToken, days])
  );

  if (yukleniyor) return <p className="net-empty">{t("faultAnalytics.loading")}</p>;
  if (hata) return <p className="net-banner net-banner--bad">{hata}</p>;
  if (!veri) return null;

  const a = veri.alarm_summary;

  return (
    <>
      <div className="net-access-bar fa-bar-sub">
        <KpiKutu Icon={BellRing} label={t("faultAnalytics.alarmTotal")} value={a.total} />
        <span className="net-access-sep" aria-hidden="true" />
        <KpiKutu
          Icon={Gauge}
          label={t("faultAnalytics.alarmAck")}
          value={a.acknowledged}
          alt={a.total > 0 ? yuzde(a.ack_ratio) : undefined}
        />
        <span className="net-access-sep" aria-hidden="true" />
        <KpiKutu Icon={Unplug} label={t("faultAnalytics.commOutages")} value={a.comm_outages} />
        {/* Siniflandirilmamis kayitlar HABERLESME sayisini eksik gosterir.
            Sifirsa gosterilmez; sifir degilse SUSULMAZ. */}
        {a.unclassified > 0 ? (
          <>
            <span className="net-access-sep" aria-hidden="true" />
            <KpiKutu
              Icon={AlertTriangle}
              label={t("faultAnalytics.unclassified")}
              value={a.unclassified}
              uyari
            />
          </>
        ) : null}
      </div>

      {a.unclassified > 0 ? (
        <p className="net-banner net-banner--info">
          <AlertTriangle size={16} />
          {t("faultAnalytics.unclassifiedHint")}
        </p>
      ) : null}

      <div className="fa-grid">
        <section className="rad-card fa-card">
          <header className="rad-card-head">
            <h3>
              <BellRing size={16} />
              {t("faultAnalytics.topRules")}
            </h3>
            <small>{t("faultAnalytics.topRulesHint")}</small>
          </header>
          {veri.top_rules.length ? (
            <ul className="fa-list fa-list--pairs">
              {veri.top_rules.map((r) => (
                <li key={`${r.rule_name}-${r.level}`}>
                  <span className="fa-list-label">
                    {r.rule_name}
                    <em>
                      {/* Hic onaylanmamis kural, "cok tetikliyor" kadar
                          onemli bir sinyal — ayri bir dille soylenir. */}
                      {r.acknowledged === 0
                        ? t("faultAnalytics.neverAcked")
                        : t("faultAnalytics.ackOf", {
                            acknowledged: r.acknowledged,
                            count: r.count
                          })}
                      {r.last_at ? ` · ${t("faultAnalytics.lastAt", { value: kisaTarih(r.last_at, i18n.language) })}` : ""}
                    </em>
                  </span>
                  <strong
                    className={`fa-list-count ${r.acknowledged === 0 ? "fa-list-count--hot" : ""}`}
                  >
                    {r.count}
                  </strong>
                </li>
              ))}
            </ul>
          ) : (
            <p className="net-empty">{t("faultAnalytics.noRules")}</p>
          )}
        </section>

        <section className="rad-card fa-card">
          <header className="rad-card-head">
            <h3>
              <Unplug size={16} />
              {t("faultAnalytics.flapping")}
            </h3>
            <small>{t("faultAnalytics.flappingHint")}</small>
          </header>
          {veri.flapping_devices.length ? (
            <ul className="fa-list fa-list--pairs">
              {veri.flapping_devices.map((d) => (
                <li key={d.device_id}>
                  <span className="fa-list-label">
                    {d.name}
                    <em>
                      {d.code}
                      {d.last_at
                        ? ` · ${t("faultAnalytics.lastAt", { value: kisaTarih(d.last_at, i18n.language) })}`
                        : ""}
                    </em>
                  </span>
                  <strong className="fa-list-count fa-list-count--hot">
                    {d.outages}
                    <em>{t("faultAnalytics.outageUnit")}</em>
                  </strong>
                </li>
              ))}
            </ul>
          ) : (
            <p className="net-empty">{t("faultAnalytics.noFlapping")}</p>
          )}
        </section>
      </div>
    </>
  );
}

// ---------------------------------------------------------------------------
// Cihaz Sagligi
// ---------------------------------------------------------------------------

function CihazSagligi({ accessToken, days }: { accessToken: string; days: number }) {
  const { t } = useTranslation();
  const kayma = useMemo(utcKaymasiSaat, []);
  const { veri, hata, yukleniyor } = useBolumVerisi(
    useCallback(() => fetchDeviceHealth(accessToken, days), [accessToken, days])
  );

  if (yukleniyor) return <p className="net-empty">{t("faultAnalytics.loading")}</p>;
  if (hata) return <p className="net-banner net-banner--bad">{hata}</p>;
  if (!veri) return null;

  return (
    <div className="fa-grid">
      <section className="rad-card fa-card">
        <header className="rad-card-head">
          <h3>
            <BatteryLow size={16} />
            {t("faultAnalytics.battery")}
          </h3>
          <small>{t("faultAnalytics.batteryHint")}</small>
        </header>
        {veri.battery_drain.length ? (
          <ul className="fa-list fa-list--pairs">
            {veri.battery_drain.map((b) => (
              <li key={b.device_id}>
                <span className="fa-list-label">
                  {b.name}
                  <em>
                    {b.first_v} V → {b.last_v} V ·{" "}
                    {t("faultAnalytics.observed", {
                      days: b.observed_days,
                      samples: b.samples
                    })}
                  </em>
                </span>
                <strong className="fa-list-count fa-list-count--hot">
                  {t("faultAnalytics.perDay", { value: b.drop_per_day_v })}
                  <em>
                    {/* Kalan gun TAHMINDIR; egim ihmal edilebilirse sayi
                        UYDURULMAZ, bunu acikca yaziyoruz. */}
                    {b.days_to_low != null
                      ? t("faultAnalytics.daysToLow", { count: b.days_to_low })
                      : t("faultAnalytics.noDaysToLow")}
                  </em>
                </strong>
              </li>
            ))}
          </ul>
        ) : (
          <p className="net-empty">{t("faultAnalytics.noBattery")}</p>
        )}
      </section>

      <section className="rad-card fa-card">
        <header className="rad-card-head">
          <h3>
            <SignalLow size={16} />
            {t("faultAnalytics.weakSignal")}
          </h3>
          <small>{t("faultAnalytics.weakSignalHint")}</small>
        </header>
        {veri.weak_signal.length ? (
          <ul className="fa-list fa-list--pairs">
            {veri.weak_signal.map((s) => (
              <li key={s.device_id}>
                <span className="fa-list-label">
                  {s.name}
                  <em>
                    {s.code}
                    {s.worst_dbm != null
                      ? ` · ${t("faultAnalytics.worstDbm", { value: s.worst_dbm })}`
                      : ""}
                  </em>
                </span>
                <strong className="fa-list-count">{s.avg_dbm} dBm</strong>
              </li>
            ))}
          </ul>
        ) : (
          <p className="net-empty">{t("faultAnalytics.noWeakSignal")}</p>
        )}
      </section>

      <section className="rad-card fa-card fa-card--wide">
        <header className="rad-card-head">
          <h3>
            <Activity size={16} />
            {t("faultAnalytics.hourProfile")}
          </h3>
          <small>{t("faultAnalytics.hourProfileHint")}</small>
        </header>
        {veri.signal_by_hour.length ? (
          <SaatProfiliGrafigi points={veri.signal_by_hour} utcOffsetHours={kayma} />
        ) : (
          <p className="net-empty">{t("faultAnalytics.noHourProfile")}</p>
        )}
      </section>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Ortak parcalar
// ---------------------------------------------------------------------------

function KpiKutu({
  Icon,
  label,
  value,
  alt,
  uyari
}: {
  Icon: typeof TrendingUp;
  label: string;
  value: number;
  alt?: string;
  uyari?: boolean;
}) {
  return (
    <div className={`net-access-item ${uyari ? "is-warn" : ""}`}>
      <span className="net-access-icon">
        <Icon size={16} />
      </span>
      <span className="net-access-body">
        <span className="net-access-label">{label}</span>
        <strong className="net-access-value">
          {value}
          {alt ? <em className="net-access-sub">{alt}</em> : null}
        </strong>
      </span>
    </div>
  );
}

/**
 * Sekme icerigini ceker. Sekme kapaninca bilesen sokulur, sokulduktan sonra
 * gelen yanit state'e YAZILMAZ (React uyarisi ve bayat veri).
 *
 * Burada polling YOK: analiz penceresi gunler olceginde ve bu sorgular 600
 * cihazli sahada ucuz degil. Kullanici sekmeye her gelisinde tazelenir.
 */
function useBolumVerisi<T>(getir: () => Promise<T>) {
  const { t } = useTranslation();
  const [veri, setVeri] = useState<T | null>(null);
  const [hata, setHata] = useState<string | null>(null);
  const [yukleniyor, setYukleniyor] = useState(true);

  useEffect(() => {
    let iptal = false;
    setYukleniyor(true);
    getir()
      .then((d) => {
        if (iptal) return;
        setVeri(d);
        setHata(null);
      })
      .catch((exc: unknown) => {
        if (iptal) return;
        const msg = exc instanceof Error ? exc.message : t("common.errorOccurred");
        if (msg !== "session_polling_401") setHata(msg);
      })
      .finally(() => {
        if (!iptal) setYukleniyor(false);
      });
    return () => {
      iptal = true;
    };
  }, [getir, t]);

  return { veri, hata, yukleniyor };
}
