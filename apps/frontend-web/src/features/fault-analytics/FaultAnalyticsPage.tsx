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
 * SEKME DUZENI — HER SEKME TEK SORU
 * ---------------------------------
 * Onceki duzende "Harita & Akis" iki bambaska soruyu tek sekmeye koyuyordu
 * (NEREDE ve NEREYE AKIYOR) ve ikisi de yariya kirpiliyordu; sistem sagligi
 * ise dort kartla "hangi kural / hangi cihaz / ne zaman"i ayni anda
 * soruyordu. Simdi dort sekme var ve her biri tek soruya bakiyor:
 *
 *   Arizalar            -> ne oldu, nerede yogunlasti
 *   Hat Ariza Yogunlugu -> yogunlasma NEREDE / NE ZAMAN / HANGI CIHAZDA
 *   Ariza Akisi         -> bolge -> hat -> faz, NEREYE akiyor
 *   Cihaz Sagligi       -> hangi cihaz, ve cihazlar BIRBIRINE GORE nasil
 *
 * "Yogunluk" sekmesi UC KESIT tasiyor ve aralarinda anahtarla gecilir:
 * harita (cografya), takvim (gun gun) ve cihaz x zaman matrisi. Ucu ayri
 * sekme olsaydi kullanici ayni soruyu uc yerde arardi; ustelik uc grafigin
 * ucu birden monte edilirdi. Anahtar yalnizca SECILI kesiti cizer.
 *
 * Sekmeler yalnizca duzen tercihi degil PERFORMANS karari: harita ve akis
 * yalnizca kendi sekmesi acilinca MONTE EDILIR, alarm/cihaz verisi de
 * yalnizca o an CEKILIR. 600 cihazli bir sahada bu sorgular ucuz degil ve
 * kimse hepsine ayni anda bakmiyor.
 *
 * CIZIM: echarts. Onceden inline SVG ile ciziliyordu ve gerekcesi "kutuphane
 * eklemek paket boyutunu buyutur"du. O gerekce artik gecerli degil — echarts
 * ZATEN bagimlilik (cihaz detay grafikleri kullaniyor) ve bu sayfa lazy
 * yukleniyor. Sankey ve takvim de echarts'in icinde: yeni kutuphane gerekmedi.
 */
import { Suspense, lazy, useCallback, useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import {
  Activity,
  AlertTriangle,
  ArrowDownUp,
  BatteryLow,
  BellRing,
  CalendarDays,
  CalendarRange,
  Gauge,
  GitBranch,
  Grid3x3,
  Loader,
  Map as MapIcon,
  MapPin,
  Radio,
  Repeat,
  Share2,
  SignalLow,
  Tags,
  TrendingUp,
  Unplug,
  Wifi,
  Zap
} from "lucide-react";

import {
  AlarmIsiHaritasi,
  AlarmTakvimi,
  DagilimGrafigi,
  EgilimGrafigi,
  FazGrafigi,
  HalkaGrafigi,
  SacilimGrafigi,
  SaatProfiliGrafigi,
  SankeyGrafigi,
  SiralamaGrafigi,
  TakvimSeridi
} from "./FaultCharts";
import { HABERLESME_RENK } from "./faultChartTheme";
import { sebekeCizgileri } from "./heatField";

import {
  fetchDeviceHealth,
  fetchFaultAnalytics,
  fetchFaultCauses,
  fetchGridSnapshot,
  fetchSystemHealth
} from "../../shared/api";
import type {
  DeviceHealth,
  FaultAnalytics,
  FaultCauseCatalog,
  SystemHealth
} from "../../shared/types";
import { usePolling } from "../../shared/usePolling";
import { voltageToPercent } from "../../shared/battery";
import { useDeviceModelSettings } from "../../components/DeviceModelSettingsProvider";

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

type Sekme = "faults" | "density" | "flow" | "devices";

const SEKMELER: { key: Sekme; labelKey: string; Icon: typeof TrendingUp }[] = [
  { key: "faults", labelKey: "faultAnalytics.tabFaults", Icon: TrendingUp },
  { key: "density", labelKey: "faultAnalytics.tabDensity", Icon: MapIcon },
  { key: "flow", labelKey: "faultAnalytics.tabFlow", Icon: Share2 },
  { key: "devices", labelKey: "faultAnalytics.tabDevices", Icon: Radio }
];

/** "Hat Ariza Yogunlugu" sekmesindeki kesitler. Ucu de AYNI soruyu farkli
 *  eksenden soruyor — yogunlasma NEREDE, NE ZAMAN, HANGI CIHAZDA — bu
 *  yuzden ayri sekmeler degil tek sekmede anahtar. */
type Gorunum = "map" | "calendar" | "matrix";

const GORUNUMLER: { key: Gorunum; labelKey: string; Icon: typeof TrendingUp }[] = [
  { key: "map", labelKey: "faultAnalytics.viewMap", Icon: MapIcon },
  { key: "calendar", labelKey: "faultAnalytics.viewCalendar", Icon: CalendarDays },
  { key: "matrix", labelKey: "faultAnalytics.viewMatrix", Icon: Grid3x3 }
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

  // TEK GRAFIKLI SEKMELER EKRANI DOLDURUR ve sayfa kaymaz.
  //
  // Yogunluk sekmesinin uc kesiti de, akis sekmesi de kartlarinda TEK bir
  // grafik tasiyor. Sabit piksel yuksekligiyle bu grafikler kartin altinda
  // yarim ekranlik bos beyaz alan birakiyordu: ekranin en cok bakilan
  // kartlari en kucuk kartlari oluyordu. Cok kartli sekmeler (Arizalar,
  // Cihaz Sagligi) izgarada kalir ve normal akisinda kayar.
  const [gorunum, setGorunum] = useState<Gorunum>("map");
  const dolduran = sekme === "density" || sekme === "flow";

  return (
    <section className={`tab-panel fa-page ${dolduran ? "fa-page--fill" : ""}`}>
      {/* ---- Ust serit: pencere secimi + uc ozet olcu ----
           KOMPAKT: bu serit ekranin baglami, ana icerigi degil. Onceki
           surumde 34px ikon kutusu + 1.35rem sayi ile serit 64px'e cikiyor
           ve asil grafikleri kati asagi itiyordu. Ikon artik etiketin
           icinde (12px), sayi ile serh AYNI satirda. */}
      <div className="fa-kpis">
        <div className="fa-kpi fa-kpi--window">
          <span className="fa-kpi-body">
            <span className="fa-kpi-label">
              <CalendarRange size={12} />
              {t("faultAnalytics.window")}
            </span>
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

        <Kpi
          Icon={AlertTriangle}
          label={t("faultAnalytics.totalFaults")}
          value={ozet?.total ?? "—"}
          note={
            ozet && ozet.open > 0
              ? t("faultAnalytics.stillOpen", { count: ozet.open })
              : undefined
          }
        />

        <Kpi
          Icon={Gauge}
          label={t("faultAnalytics.mttr")}
          value={
            ozet?.mttr_hours != null
              ? t("faultAnalytics.hours", { value: ozet.mttr_hours })
              : "—"
          }
          note={t("faultAnalytics.mttrClosedOnly")}
        />

        <Kpi
          Icon={MapPin}
          label={t("faultAnalytics.labeled")}
          value={ozet ? `${ozet.labeled} / ${ozet.total}` : "—"}
          note={ozet ? yuzde(ozet.labeled_ratio) : undefined}
          uyari={dusukEtiket}
        />
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
               Alttaki siralama kartlari o baglamin icinde okunur.

               VERI YETMIYORSA KART HIC CIZILMEZ. Once tek nokta cizilip
               "bozuk grafik" gorunuyordu; sonra yerine aciklama konuldu ama
               o da ekranin en ustunde tam genislikte bir serit kaplayip
               ASIL kartlari asagi itiyordu. Iki aylik veri birikince
               kendiliginden geri gelir; o zamana kadar yer kaplamasinin
               bir karsiligi yok. ---- */}
          {(data?.monthly_trend.length ?? 0) >= 2 ? (
            <Kart
              genislik="wide"
              Icon={TrendingUp}
              baslik={t("faultAnalytics.monthlyTrend")}
              ipucu={t("faultAnalytics.monthlyHint")}
            >
              <EgilimGrafigi
                points={data!.monthly_trend}
                labelToplam={t("faultAnalytics.faultUnit")}
              />
            </Kart>
          ) : null}

          <Kart
            Icon={GitBranch}
            baslik={t("faultAnalytics.topLines")}
            ipucu={t("faultAnalytics.topLinesHint")}
          >
            {data?.top_lines.length ? (
              <SiralamaGrafigi
                items={data.top_lines.map((l) => ({ label: l.name, value: l.count }))}
                birim={t("faultAnalytics.faultUnit")}
              />
            ) : (
              <Bos Icon={GitBranch}>{t("faultAnalytics.noData")}</Bos>
            )}
          </Kart>

          {/* Hat siralamasi "hangi hat" der; bolge siralamasi "hangi ekibin
              sahasi" der — bakim planlamasinda ikisi ayri sorudur. */}
          <Kart
            Icon={MapIcon}
            baslik={t("faultAnalytics.topRegions")}
            ipucu={t("faultAnalytics.topRegionsHint")}
          >
            {data?.top_regions.length ? (
              <SiralamaGrafigi
                items={data.top_regions.map((r) => ({ label: r.name, value: r.count }))}
                birim={t("faultAnalytics.faultUnit")}
              />
            ) : (
              <Bos Icon={MapIcon}>{t("faultAnalytics.noData")}</Bos>
            )}
          </Kart>

          <Kart
            genislik="half"
            Icon={Tags}
            baslik={t("faultAnalytics.causeDistribution")}
            ipucu={
              ozet ? t("faultAnalytics.ofLabeled", { count: ozet.labeled }) : undefined
            }
          >
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
              <Bos Icon={Tags}>{t("faultAnalytics.noCauses")}</Bos>
            )}
          </Kart>

          <Kart
            genislik="half"
            Icon={Zap}
            baslik={t("faultAnalytics.phaseDistribution")}
            ipucu={t("faultAnalytics.phaseHint")}
          >
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
              <Bos Icon={Zap}>{t("faultAnalytics.noData")}</Bos>
            )}
          </Kart>

          {/* Tekrarlayan aciklikar — bakim onceliklendirmesinin en dogrudan
              girdisi. Grafik DEGIL liste: burada aranan sey "hangi aciklik"
              ve "kac kez", ikisi de metin. */}
          <Kart
            Icon={Repeat}
            baslik={t("faultAnalytics.repeatSpans")}
            ipucu={t("faultAnalytics.repeatSpansHint")}
          >
            {data?.repeat_spans.length ? (
              <ul className="fa-list">
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
              <Bos Icon={Repeat}>{t("faultAnalytics.noRepeats")}</Bos>
            )}
          </Kart>

          {/* ---- ARALIK RISK PUANI ---------------------------------------
               "Tekrarlayan aciklikar" SAYAR; bu liste TARTAR. Bir aralikta
               dort ariza da 11 ay onceyse orasi bugun sorunlu degildir;
               ucu son iki haftada olan aralik ise ekip bekliyor demektir.
               Puan tazelik (90 gun yari omur) ve ariza turuyle agirliklidir
               ve MUTLAKTIR — esik koyup anomali kurali yazilabilsin diye
               kume icinde normalize EDILMEZ.

               Anahtar CIHAZ ARALIGI (zone_code): ariza bir hattin degil,
               iki cihaz arasindaki araligin olayidir; bakim ekibi de hatta
               degil o araliga gider. ---- */}
          <Kart
            Icon={Gauge}
            baslik={t("faultAnalytics.zoneScores")}
            ipucu={t("faultAnalytics.zoneScoresHint")}
          >
            {data?.zone_scores.length ? (
              <ul className="fa-list">
                {data.zone_scores.map((z) => (
                  <li key={z.zone_code}>
                    <span className="fa-list-label">
                      {z.line_name ?? z.zone_code}
                      <em>
                        {/* Aralik CIHAZ adlariyla yazilir: telsizde
                            konusulan sey direk numarasi degil cihaz kodu. */}
                        {z.last_red_device_code ?? "?"} →{" "}
                        {z.first_green_device_code ?? t("faults.card.lineEnd")}
                        {" · "}
                        {t("faultAnalytics.zoneFaultCount", { count: z.count })}
                      </em>
                    </span>
                    <strong
                      className={`fa-list-count ${
                        z.score >= 50 ? "fa-list-count--hot" : ""
                      }`}
                    >
                      {z.score.toFixed(0)}
                    </strong>
                  </li>
                ))}
              </ul>
            ) : (
              <Bos Icon={Gauge}>{t("faultAnalytics.noData")}</Bos>
            )}
          </Kart>
        </div>
      ) : null}

      {sekme === "density" ? (
        <HatArizaYogunlugu
          accessToken={accessToken}
          days={days}
          analytics={data}
          gorunum={gorunum}
          setGorunum={setGorunum}
        />
      ) : null}

      {sekme === "flow" ? <ArizaAkisi analytics={data} fazLabel={fazLabel} /> : null}

      {sekme === "devices" ? <CihazSagligi accessToken={accessToken} days={days} /> : null}
    </section>
  );
}

// ---------------------------------------------------------------------------
// Hat Ariza Yogunlugu — TEK SORU, UC EKSEN
// ---------------------------------------------------------------------------
//
//   Harita       -> yogunlasma NEREDE (cografya)
//   Alarm sikligi-> NE ZAMAN (gun gun takvim)
//   Cihaz x zaman-> HANGI CIHAZDA, ne zaman (matris)
//
// Ucu ayri sekme olsaydi kullanici ayni soruyu uc yerde arardi. Anahtar
// (switch) uc kesiti tek yerde tutuyor ve yalnizca SECILI olani ciziyor —
// echarts ornekleri ve Leaflet karolari bosuna monte edilmiyor.

function HatArizaYogunlugu({
  accessToken,
  days,
  analytics,
  gorunum,
  setGorunum
}: {
  accessToken: string;
  days: number;
  analytics: FaultAnalytics | null;
  gorunum: Gorunum;
  setGorunum: (g: Gorunum) => void;
}) {
  const { t } = useTranslation();

  // ISTEKLER GEREKTIGINDE, BIR KEZ.
  //
  // Iki bayrak da YAPISKAN: bir kez acildiktan sonra kapanmaz. Dogrudan
  // `gorunum === "map"` yazsaydik, kullanici harita <-> takvim arasinda her
  // gecisinde ayni istek yeniden atilirdi. Takvim ve matris AYNI yanittan
  // (`/faults/system-health`) geldigi icin aralarindaki gecis zaten
  // bedava.
  const [topolojiIstendi, setTopolojiIstendi] = useState(gorunum === "map");
  const [alarmIstendi, setAlarmIstendi] = useState(gorunum !== "map");

  useEffect(() => {
    if (gorunum === "map") setTopolojiIstendi(true);
    else setAlarmIstendi(true);
  }, [gorunum]);

  /**
   * Sebeke topolojisi — isi haritasinin ZEMINI.
   *
   * Bos bir zemin uzerindeki sicak nokta operatore koordinat kadar sey
   * anlatir; hat cizilince "su fiderin ortasinda" olur. Ayri bir istek
   * cunku topoloji ariza penceresinden BAGIMSIZ (30 gun de secilse hat
   * aynidir) ve degismiyorsa bosuna yeniden hesaplanmasin.
   */
  const topoloji = useBolumVerisi(
    useCallback(() => fetchGridSnapshot(accessToken), [accessToken]),
    topolojiIstendi
  );

  const alarm = useBolumVerisi<SystemHealth>(
    useCallback(() => fetchSystemHealth(accessToken, days), [accessToken, days]),
    alarmIstendi
  );

  const cizgiler = useMemo(
    () =>
      // Cizgiler DIREKLERDEN kuruluyor, `segments`ten degil: `LineSegment`
      // bir cihaz yerlesimi kaydi ve cihazsiz acikliklar icin satir hic
      // olusmuyor — harita bos cikiyordu. Anasayfa haritasiyla ayni kaynak
      // (bkz. heatField.sebekeCizgileri).
      topoloji.veri ? sebekeCizgileri(topoloji.veri.poles, topoloji.veri.lines) : [],
    [topoloji.veri]
  );

  // Isi noktalari ARIZA ANALIZI yanitindan geliyor (eskiden cihaz sagligi
  // ucundan cekiliyordu). Harita artik yalnizca topoloji icin istek atiyor;
  // 600 cihazlik karsilastirma tablosunu ve iki agir telemetri sorgusunu
  // bosuna odemiyor.
  const noktalar = analytics?.fault_heatmap ?? [];
  const haritaGosterilir = noktalar.length > 0 || cizgiler.length > 0;

  const secili = GORUNUMLER.find((g) => g.key === gorunum);
  const ozet = alarm.veri?.alarm_summary;

  const anahtar = (
    <div className="fa-switch" role="tablist" aria-label={t("faultAnalytics.tabDensity")}>
      {GORUNUMLER.map(({ key, labelKey, Icon }) => (
        <button
          key={key}
          type="button"
          role="tab"
          aria-selected={gorunum === key}
          className={`fa-switch-btn ${gorunum === key ? "is-active" : ""}`}
          onClick={() => setGorunum(key)}
        >
          <Icon size={13} />
          {t(labelKey)}
        </button>
      ))}
    </div>
  );

  return (
    <div className="fa-fill">
      <Kart
        genislik="fill"
        Icon={secili?.Icon ?? MapIcon}
        baslik={t(`faultAnalytics.${gorunum}Title`)}
        ipucu={t(`faultAnalytics.${gorunum}Hint`)}
        sag={anahtar}
      >
        {gorunum === "map" ? (
          topoloji.yukleniyor ? (
            <Bos Icon={Loader}>{t("faultAnalytics.loading")}</Bos>
          ) : topoloji.hata ? (
            <p className="net-banner net-banner--bad">{topoloji.hata}</p>
          ) : haritaGosterilir ? (
            <Suspense fallback={<Bos Icon={Loader}>{t("faultAnalytics.loading")}</Bos>}>
              <FaultHeatMap points={noktalar} lines={cizgiler} />
            </Suspense>
          ) : (
            <Bos Icon={MapPin}>{t("faultAnalytics.noHeat")}</Bos>
          )
        ) : alarm.yukleniyor ? (
          <Bos Icon={Loader}>{t("faultAnalytics.loading")}</Bos>
        ) : alarm.hata ? (
          <p className="net-banner net-banner--bad">{alarm.hata}</p>
        ) : !alarm.veri ? null : gorunum === "calendar" ? (
          <Takvim veri={alarm.veri} ozet={ozet} />
        ) : (
          <Matris veri={alarm.veri} />
        )}
      </Kart>
    </div>
  );
}

/** Gun gun alarm sikligi. Pencereyi IZLER. */
function Takvim({
  veri,
  ozet
}: {
  veri: SystemHealth;
  ozet: SystemHealth["alarm_summary"] | undefined;
}) {
  const { t } = useTranslation();
  const takvim = veri.alarm_calendar;

  return (
    <>
      {/* Alarm ozeti takvimin BAGLAMI. Basligin saginda duruyordu; orayi
          kesit anahtari aldi. Ayri KPI kartlarina cikarmak, tek grafikli
          bir kesitte grafikten cok yer kaplardi. */}
      {ozet ? (
        <span className="fa-head-stats fa-head-stats--row">
          <b>{ozet.total}</b> {t("faultAnalytics.alarmUnit")}
          <i aria-hidden="true">·</i>
          <b>{ozet.total > 0 ? yuzde(ozet.ack_ratio) : "—"}</b>{" "}
          {t("faultAnalytics.ackShort")}
          <i aria-hidden="true">·</i>
          <b>{ozet.comm_outages}</b> {t("faultAnalytics.outageUnit")}
        </span>
      ) : null}

      {/* Siniflandirilmamis kayitlar HABERLESME sayisini eksik gosterir.
          Sifirsa gosterilmez; sifir degilse SUSULMAZ. */}
      {ozet && ozet.unclassified > 0 ? (
        <p className="fa-inline-warn">
          <AlertTriangle size={13} />
          {t("faultAnalytics.unclassifiedHint")}
        </p>
      ) : null}

      {takvim.truncated ? (
        <p className="fa-inline-warn">
          <AlertTriangle size={13} />
          {t("faultAnalytics.calendarTruncated", { days: takvim.days.length })}
        </p>
      ) : null}

      {takvim.days.length ? (
        <>
          {/* `ilkVeriGunu`: izleme baslamadan onceki gunler "0 alarm" DEGIL
              "veri yok". Ikisi ayni renkte cizilince takvim, kayit
              tutulmayan bir donemi "sorunsuz gecti" diye okutuyordu. */}
          <AlarmTakvimi
            days={takvim.days}
            start={takvim.start}
            end={takvim.end}
            max={takvim.max}
            birim={t("faultAnalytics.alarmUnit")}
            ilkVeriGunu={takvim.first_alarm_at?.slice(0, 10) ?? null}
            veriYokLabel={t("faultAnalytics.calendarNoData")}
            dolduran
          />
          <TakvimSeridi
            max={takvim.max}
            azLabel={t("faultAnalytics.calendarLess")}
            cokLabel={t("faultAnalytics.calendarMore")}
          />
          {/* Pencerede HIC alarm yoksa bos bir takvim "her sey yolunda" gibi
              de okunabilir, "veri gelmiyor" gibi de. Ayrimi SOYLUYORUZ. */}
          {takvim.total === 0 ? (
            <p className="fa-cal-note">{t("faultAnalytics.calendarAllQuiet")}</p>
          ) : null}
        </>
      ) : (
        <Bos Icon={CalendarDays}>{t("faultAnalytics.noData")}</Bos>
      )}
    </>
  );
}

/** Cihaz x zaman alarm yogunlugu. Pencereyi IZLEMEZ — hep son 30 gun. */
function Matris({ veri }: { veri: SystemHealth }) {
  const { t } = useTranslation();
  const isi = veri.alarm_heatmap;

  return (
    <>
      {/* Kesilen satirlar SESSIZCE atilmaz: "listede yok" ile "alarm
          uretmemis" karistirilmasin. */}
      {isi.truncated ? (
        <p className="fa-inline-warn">
          <AlertTriangle size={13} />
          {t("faultAnalytics.alarmHeatmapTruncated", {
            shown: isi.devices.length,
            total: isi.device_total
          })}
        </p>
      ) : null}

      {isi.cells.length ? (
        <AlarmIsiHaritasi
          buckets={isi.buckets}
          devices={isi.devices}
          cells={isi.cells}
          max={isi.max}
          bucket={isi.bucket}
          birim={t("faultAnalytics.alarmUnit")}
          dolduran
        />
      ) : (
        <Bos Icon={Grid3x3}>{t("faultAnalytics.noAlarmHeatmap")}</Bos>
      )}
    </>
  );
}

// ---------------------------------------------------------------------------
// Ariza Akisi — TEK SORU: bolge -> hat -> faz, nereye akiyor
// ---------------------------------------------------------------------------

function ArizaAkisi({
  analytics,
  fazLabel
}: {
  analytics: FaultAnalytics | null;
  fazLabel: (kod: string) => string;
}) {
  const { t } = useTranslation();

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

  const sankey = analytics?.sankey;

  return (
    <div className="fa-fill">
      <Kart
        genislik="fill"
        Icon={Share2}
        baslik={t("faultAnalytics.sankey")}
        ipucu={t("faultAnalytics.sankeyHint")}
      >
        {sankey && sankey.links.length ? (
          // Kendi sekmesinde TEK BASINA ve EKRANI DOLDURUYOR: onceki
          // duzende haritanin altinda sikisiyor, kademe etiketleri ust uste
          // biniyordu. Sankey yerden en cok kazanan grafik — dugumler
          // dikeyde ayrildikca kenarlarin nereye aktigi okunur hale gelir.
          <SankeyGrafigi
            nodes={sankey.nodes}
            links={sankey.links}
            etiketle={dugumEtiket}
            birim={t("faultAnalytics.faultUnit")}
            dolduran
          />
        ) : (
          <Bos Icon={Share2}>{t("faultAnalytics.noSankey")}</Bos>
        )}
      </Kart>
    </div>
  );
}


// ---------------------------------------------------------------------------
// Cihaz Sagligi — hangi cihaz, ve cihazlar BIRBIRINE GORE nasil
// ---------------------------------------------------------------------------

/** RSSI kovalari (dBm). SABIT ve anlamli: otomatik kova, filo daraldiginda
 *  esikleri kaydirir ve iki pencere arasindaki grafik karsilastirilamaz
 *  olurdu. Sinirlar modem kalite esiklerinden geliyor. */
const RSSI_KOVALARI: { alt: number; ust: number; label: string }[] = [
  { alt: -Infinity, ust: -105, label: "≤ -105" },
  { alt: -105, ust: -95, label: "-105…-95" },
  { alt: -95, ust: -85, label: "-95…-85" },
  { alt: -85, ust: -75, label: "-85…-75" },
  { alt: -75, ust: -65, label: "-75…-65" },
  { alt: -65, ust: Infinity, label: "> -65" }
];

/** Batarya dusus hizi kovalari (mV/gun). `drop_per_day_v` V/gun geliyor;
 *  sahadaki konusma dili milivolt: "gunde 4 milivolt dusuyor". */
const BATARYA_KOVALARI: { alt: number; ust: number; label: string }[] = [
  { alt: -Infinity, ust: 0, label: "≤ 0" },
  { alt: 0, ust: 1, label: "0–1" },
  { alt: 1, ust: 3, label: "1–3" },
  { alt: 3, ust: 5, label: "3–5" },
  { alt: 5, ust: 10, label: "5–10" },
  { alt: 10, ust: Infinity, label: "> 10" }
];

function kovala(
  degerler: number[],
  kovalar: { alt: number; ust: number; label: string }[]
): { label: string; count: number }[] {
  return kovalar.map((k) => ({
    label: k.label,
    count: degerler.filter((v) => v > k.alt && v <= k.ust).length
  }));
}

type Siralama = "alarms" | "outages" | "faults" | "avg_dbm" | "battery_pct";

/** Sutun -> siralanacak SAYI.
 *
 *  Dogrudan `c[anahtar]` okunmuyor cunku batarya sutununda GOSTERILEN sey
 *  yuzde, satirda duran sey voltaj. Esikler cihaz TURUNE bagli oldugundan
 *  (bkz. `shared/battery.ts`) karisik modelli bir filoda voltaja gore
 *  siralamak ekrandaki yuzde sirasiyla ayrisirdi: 3,45 V bir modelde %20,
 *  digerinde %60 olabilir. Siralama her zaman GORULEN degere gore. */
type KiyasSatiri = DeviceHealth["device_comparison"][number];
type OlcuHaritasi = Record<Siralama, (c: KiyasSatiri) => number | null>;

/** Tabloda gosterilen satir sayisi. Tamami (600'e kadar) grafiklerde ve
 *  dagilimlarda zaten var; tablo KARAR icin, tarama icin degil. */
const TABLO_SATIR = 20;

function CihazSagligi({ accessToken, days }: { accessToken: string; days: number }) {
  const { t, i18n } = useTranslation();
  const kayma = useMemo(utcKaymasiSaat, []);
  const [sirala, setSirala] = useState<Siralama>("alarms");
  const { veri, hata, yukleniyor } = useBolumVerisi<DeviceHealth>(
    useCallback(() => fetchDeviceHealth(accessToken, days), [accessToken, days])
  );

  const durumLabel = useCallback(
    (kod: string) => t(`faultAnalytics.comm_${kod}`, { defaultValue: kod }),
    [t]
  );

  const kiyas = useMemo(() => veri?.device_comparison ?? [], [veri]);

  // Voltaj -> yuzde donusumu TEK kaynakta (`shared/battery.ts`) ve esikler
  // cihaz turunden cozuluyor; boylece bu tablo ile cihaz listesi/harita ayni
  // bataryaya ayni yuzdeyi yaziyor. Olculen sinyal master unitesinin
  // bataryasi (backend `BATTERY_SIGNAL`), o yuzden unite sabit "master".
  const { thresholdsFor } = useDeviceModelSettings();
  const bataryaYuzdesi = useCallback(
    (c: KiyasSatiri) => voltageToPercent(c.battery_v, thresholdsFor(c.model, "master")),
    [thresholdsFor]
  );

  const olcu = useMemo<OlcuHaritasi>(
    () => ({
      alarms: (c) => c.alarms,
      outages: (c) => c.outages,
      faults: (c) => c.faults,
      avg_dbm: (c) => c.avg_dbm,
      battery_pct: (c) => bataryaYuzdesi(c) ?? null
    }),
    [bataryaYuzdesi]
  );

  const rssiKovalari = useMemo(
    () =>
      kovala(
        kiyas.map((c) => c.avg_dbm).filter((v): v is number => v != null),
        RSSI_KOVALARI
      ),
    [kiyas]
  );

  const bataryaKovalari = useMemo(
    () =>
      kovala(
        kiyas
          .map((c) => c.drop_per_day_v)
          .filter((v): v is number => v != null)
          .map((v) => v * 1000),
        BATARYA_KOVALARI
      ),
    [kiyas]
  );

  const sacilim = useMemo(
    () =>
      kiyas
        .filter((c) => c.avg_dbm != null)
        .map((c) => ({
          code: c.code,
          name: c.name,
          x: c.avg_dbm as number,
          y: c.alarms,
          status: c.comm_status
        })),
    [kiyas]
  );

  const siraliKiyas = useMemo(() => {
    // dBm ve BATARYA YUZDESINDE kucuk olan kotudur; digerlerinde buyuk olan
    // kotu. Tablo her zaman "en kotu ustte" okunmali, yoksa sutun degistikce
    // anlam terse doner.
    const yon = sirala === "avg_dbm" || sirala === "battery_pct" ? 1 : -1;
    const deger = olcu[sirala];
    return [...kiyas]
      .sort((a, b) => {
        const av = deger(a);
        const bv = deger(b);
        // Olcusu olmayan cihaz listenin SONUNA duser; basa koymak "en kotu"
        // sutununu bilinmeyenlerle doldururdu.
        if (av == null && bv == null) return 0;
        if (av == null) return 1;
        if (bv == null) return -1;
        return (av - bv) * yon;
      })
      .slice(0, TABLO_SATIR);
  }, [kiyas, sirala, olcu]);

  const durumlar = useMemo(
    () =>
      (veri?.comm_status ?? [])
        .map((d) => ({
          label: durumLabel(d.status),
          value: d.count,
          color: HABERLESME_RENK[d.status] ?? "#94a3b8"
        }))
        .sort((a, b) => b.value - a.value),
    [veri, durumLabel]
  );

  if (yukleniyor) return <Bos Icon={Loader}>{t("faultAnalytics.loading")}</Bos>;
  if (hata) return <p className="net-banner net-banner--bad">{hata}</p>;
  if (!veri) return null;

  return (
    <div className="fa-grid">
      {/* ---- 1) FILO SU AN NASIL ---- */}
      <Kart
        genislik="third"
        Icon={Wifi}
        baslik={t("faultAnalytics.commStatus")}
        ipucu={t("faultAnalytics.commStatusHint")}
      >
        {durumlar.length ? (
          <HalkaGrafigi
            items={durumlar}
            birim={t("faultAnalytics.deviceUnit")}
            toplamLabel={t("faultAnalytics.deviceUnit")}
          />
        ) : (
          <Bos Icon={Wifi}>{t("faultAnalytics.noData")}</Bos>
        )}
      </Kart>

      {/* ---- 2) CAPRAZ SORU: zayif sinyal cok alarm uretiyor mu ---- */}
      <Kart
        genislik="twothird"
        Icon={Activity}
        baslik={t("faultAnalytics.scatter")}
        ipucu={t("faultAnalytics.scatterHint")}
      >
        {sacilim.length ? (
          <SacilimGrafigi
            points={sacilim}
            xLabel={t("faultAnalytics.rssiAxis")}
            yLabel={t("faultAnalytics.alarmUnit")}
            durumLabel={durumLabel}
          />
        ) : (
          <Bos Icon={Activity}>{t("faultAnalytics.noSignalData")}</Bos>
        )}
      </Kart>

      {/* ---- 3) FILO DAGILIMLARI — "bu cihaz mi kotu, filo mu" ---- */}
      <Kart
        genislik="half"
        Icon={SignalLow}
        baslik={t("faultAnalytics.signalDistribution")}
        ipucu={t("faultAnalytics.signalDistributionHint")}
      >
        {rssiKovalari.some((k) => k.count > 0) ? (
          <DagilimGrafigi
            bins={rssiKovalari}
            eksenLabel={t("faultAnalytics.rssiAxis")}
            birim={t("faultAnalytics.deviceUnit")}
          />
        ) : (
          <Bos Icon={SignalLow}>{t("faultAnalytics.noSignalData")}</Bos>
        )}
      </Kart>

      <Kart
        genislik="half"
        Icon={BatteryLow}
        baslik={t("faultAnalytics.batteryDistribution")}
        ipucu={t("faultAnalytics.batteryDistributionHint")}
      >
        {bataryaKovalari.some((k) => k.count > 0) ? (
          <DagilimGrafigi
            bins={bataryaKovalari}
            eksenLabel={t("faultAnalytics.batteryAxis")}
            birim={t("faultAnalytics.deviceUnit")}
          />
        ) : (
          <Bos Icon={BatteryLow}>{t("faultAnalytics.noBattery")}</Bos>
        )}
      </Kart>

      {/* ---- 4) KARSILASTIRMA TABLOSU ----
           Grafikler deseni verir; karar icin gereken KESIN sayilar burada.
           Ayni zamanda grafiklerin tablo karsiligi (erisilebilirlik: renkle
           kodlanan her sey burada metin olarak da var). */}
      <Kart
        genislik="wide"
        Icon={ArrowDownUp}
        baslik={t("faultAnalytics.comparison")}
        ipucu={t("faultAnalytics.comparisonHint")}
      >
        {kiyas.length ? (
          <div className="fa-table-wrap">
            <table className="fa-table">
              <thead>
                <tr>
                  <th scope="col">{t("faultAnalytics.colDevice")}</th>
                  <th scope="col">{t("faultAnalytics.colStatus")}</th>
                  {(
                    [
                      ["alarms", t("faultAnalytics.colAlarms")],
                      ["outages", t("faultAnalytics.colOutages")],
                      ["faults", t("faultAnalytics.colFaults")],
                      ["avg_dbm", t("faultAnalytics.colRssi")],
                      ["battery_pct", t("faultAnalytics.colBattery")]
                    ] as [Siralama, string][]
                  ).map(([anahtar, baslik]) => (
                    <th key={anahtar} scope="col" className="fa-th-num">
                      <button
                        type="button"
                        className={`fa-sort ${sirala === anahtar ? "is-active" : ""}`}
                        onClick={() => setSirala(anahtar)}
                        aria-pressed={sirala === anahtar}
                      >
                        {baslik}
                        <ArrowDownUp size={11} aria-hidden="true" />
                      </button>
                    </th>
                  ))}
                  {/* Gerilim SIRALANMAZ: batarya sutunuyla ayni olcunun iki
                      gosterimi, ikisini de siralanabilir yapmak ayni islem
                      icin iki dugme demekti. */}
                  <th scope="col" className="fa-th-num">
                    {t("faultAnalytics.colVoltage")}
                  </th>
                </tr>
              </thead>
              <tbody>
                {siraliKiyas.map((c) => (
                  <tr key={c.device_id}>
                    <td>
                      <span className="fa-td-name">{c.name}</span>
                      <em className="fa-td-sub">{c.code}</em>
                    </td>
                    <td>
                      {/* Kimlik renge TEK BASINA birakilmaz: nokta + metin. */}
                      <span className="fa-chip">
                        <i style={{ background: HABERLESME_RENK[c.comm_status] }} />
                        {durumLabel(c.comm_status)}
                      </span>
                    </td>
                    <td className="fa-td-num">{c.alarms}</td>
                    <td className="fa-td-num">{c.outages}</td>
                    <td className="fa-td-num">{c.faults}</td>
                    {/* Olcu yoksa "—": 0 dBm "mukemmel sinyal" demektir ve
                        tam ters okunurdu. Birim YAZILIYOR: ciplak "-31.2"
                        hangi olcek oldugunu soylemiyordu. */}
                    <td className="fa-td-num">
                      {c.avg_dbm != null ? `${c.avg_dbm} dBm` : "—"}
                    </td>
                    <td className="fa-td-num">
                      {bataryaYuzdesi(c) != null ? `%${bataryaYuzdesi(c)}` : "—"}
                    </td>
                    {/* Ham voltaj yuzdenin YANINDA durur: esik cihaz turune
                        gore degistigi icin "%40" tek basina hangi hucrede ne
                        demek oldugunu soylemiyor; saha ekibi voltaji okuyor. */}
                    <td className="fa-td-num">
                      {c.battery_v != null ? `${c.battery_v.toFixed(2)} V` : "—"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            {/* Kesilen satir SESSIZCE atilmaz. */}
            {kiyas.length > siraliKiyas.length ? (
              <p className="fa-cal-note">
                {t("faultAnalytics.comparisonShown", {
                  shown: siraliKiyas.length,
                  total: kiyas.length
                })}
              </p>
            ) : null}
          </div>
        ) : (
          <Bos Icon={ArrowDownUp}>{t("faultAnalytics.noData")}</Bos>
        )}
      </Kart>

      {/* ---- 5) KIM: kural ve cihaz listeleri (sistem sagligindan tasindi) ---- */}
      <Kart
        Icon={BellRing}
        baslik={t("faultAnalytics.topRules")}
        ipucu={t("faultAnalytics.topRulesHint")}
      >
        {veri.top_rules.length ? (
          <ul className="fa-list">
            {veri.top_rules.map((r) => (
              <li key={`${r.rule_name}-${r.level}`}>
                <span className="fa-list-label">
                  {r.rule_name}
                  <em>
                    {/* Hic onaylanmamis kural, "cok tetikliyor" kadar onemli
                        bir sinyal — ayri bir dille soylenir. */}
                    {r.acknowledged === 0
                      ? t("faultAnalytics.neverAcked")
                      : t("faultAnalytics.ackOf", {
                          acknowledged: r.acknowledged,
                          count: r.count
                        })}
                    {r.last_at
                      ? ` · ${t("faultAnalytics.lastAt", { value: kisaTarih(r.last_at, i18n.language) })}`
                      : ""}
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
          <Bos Icon={BellRing}>{t("faultAnalytics.noRules")}</Bos>
        )}
      </Kart>

      <Kart
        Icon={Unplug}
        baslik={t("faultAnalytics.flapping")}
        ipucu={t("faultAnalytics.flappingHint")}
      >
        {veri.flapping_devices.length ? (
          <ul className="fa-list">
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
          <Bos Icon={Unplug}>{t("faultAnalytics.noFlapping")}</Bos>
        )}
      </Kart>


      {/* ---- 7) OLCUM AYRINTISI ---- */}
      <Kart
        Icon={BatteryLow}
        baslik={t("faultAnalytics.battery")}
        ipucu={t("faultAnalytics.batteryHint")}
      >
        {veri.battery_drain.length ? (
          <ul className="fa-list">
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
          <Bos Icon={BatteryLow}>{t("faultAnalytics.noBattery")}</Bos>
        )}
      </Kart>

      <Kart
        Icon={SignalLow}
        baslik={t("faultAnalytics.weakSignal")}
        ipucu={t("faultAnalytics.weakSignalHint")}
      >
        {veri.weak_signal.length ? (
          <ul className="fa-list">
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
          <Bos Icon={SignalLow}>{t("faultAnalytics.noWeakSignal")}</Bos>
        )}
      </Kart>

      <Kart
        genislik="wide"
        Icon={Activity}
        baslik={t("faultAnalytics.hourProfile")}
        ipucu={t("faultAnalytics.hourProfileHint")}
      >
        {veri.signal_by_hour.length ? (
          <SaatProfiliGrafigi points={veri.signal_by_hour} utcOffsetHours={kayma} />
        ) : (
          <Bos Icon={Activity}>{t("faultAnalytics.noHourProfile")}</Bos>
        )}
      </Kart>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Ortak parcalar
// ---------------------------------------------------------------------------

/**
 * Kart kabugu — baslik seridi + govde.
 *
 * NEDEN BILESEN: on ucten fazla kart ayni basligi ELLE kuruyordu ve zamanla
 * ayrisiyorlardi (kiminde ikon vardi kiminde yoktu, ipucu kiminde vardi).
 * Baslik cizgisi, ikon kutusu ve bosluklar tek yerde tanimli olunca ekran
 * "tek sistem" gibi okunuyor — ve yeni bir kart eklemek bir satir.
 */
function Kart({
  Icon,
  baslik,
  ipucu,
  genislik,
  sag,
  children
}: {
  Icon: typeof TrendingUp;
  baslik: string;
  ipucu?: string;
  /** Izgara genisligi. Varsayilan yarim (6/12). `fill` = kalan dikey alani
   *  DOLDURUR (harita kesiti); izgara disinda, kendi kabinde. */
  genislik?: "wide" | "twothird" | "half" | "third" | "fill";
  /** Basligin sagindaki serh — sayi seridi gibi. */
  sag?: React.ReactNode;
  children: React.ReactNode;
}) {
  const sinif = genislik ? ` fa-card--${genislik}` : "";
  return (
    <section className={`fa-card${sinif}`}>
      <header className="fa-card-head">
        <span className="fa-card-icon" aria-hidden="true">
          <Icon size={15} />
        </span>
        <span className="fa-card-titles">
          <h3>{baslik}</h3>
          {ipucu ? <small>{ipucu}</small> : null}
        </span>
        {sag ?? null}
      </header>
      {children}
    </section>
  );
}

/** Tek olcu kutusu. Sayi tabular, etiket kucuk ve sessiz — bakis once
 *  olcuye gitsin; serh (`note`) ondan gorsel olarak ayri dursun. */
function Kpi({
  Icon,
  label,
  value,
  note,
  uyari
}: {
  Icon: typeof TrendingUp;
  label: string;
  value: number | string;
  note?: string;
  uyari?: boolean;
}) {
  return (
    <div className={`fa-kpi ${uyari ? "fa-kpi--warn" : ""}`}>
      <span className="fa-kpi-body">
        <span className="fa-kpi-label">
          <Icon size={12} />
          {label}
        </span>
        {/* Sayi ile serh AYNI satirda: alt alta olduklarinda serit iki kat
            yer kapliyordu ve serh sayiyla ayni agirlikta okunuyordu. */}
        <span className="fa-kpi-line">
          <strong className="fa-kpi-value">{value}</strong>
          {note ? <em className="fa-kpi-note">{note}</em> : null}
        </span>
      </span>
    </div>
  );
}

/**
 * Bos durum. Bos bir grafik "ariza yok" gibi okunur; dogru mesaj "henuz veri
 * birikmedi"dir. Ikon + metin ve en az bir yukseklik: kartlar ayni satirda
 * hizali kalsin diye.
 */
function Bos({ Icon, children }: { Icon: typeof TrendingUp; children: React.ReactNode }) {
  return (
    <p className="fa-empty">
      <Icon size={22} strokeWidth={1.5} />
      {children}
    </p>
  );
}

/**
 * Sekme icerigini ceker. Sekme kapaninca bilesen sokulur, sokulduktan sonra
 * gelen yanit state'e YAZILMAZ (React uyarisi ve bayat veri).
 *
 * Burada polling YOK: analiz penceresi gunler olceginde ve bu sorgular 600
 * cihazli sahada ucuz degil. Kullanici sekmeye her gelisinde tazelenir.
 *
 * `etkin=false`: istek ATILMAZ ve ELDEKI veri de silinmez. Yogunluk
 * sekmesindeki anahtar bunu kullaniyor — harita kesitindeyken alarm
 * istegi hic atilmaz, ama takvimden haritaya gecip geri donuldugunde veri
 * yeniden cekilmez. Bayrak cagiran tarafta YAPISKAN tutulur; burada
 * true->false gecisi yalnizca "cekme" demektir, "unut" demez.
 */
function useBolumVerisi<T>(getir: () => Promise<T>, etkin = true) {
  const { t } = useTranslation();
  const [veri, setVeri] = useState<T | null>(null);
  const [hata, setHata] = useState<string | null>(null);
  const [yukleniyor, setYukleniyor] = useState(etkin);

  useEffect(() => {
    if (!etkin) return;
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
  }, [getir, etkin, t]);

  return { veri, hata, yukleniyor };
}
