/**
 * Ariza Analizi grafikleri — echarts sarmalayicilari.
 *
 * NEDEN ECHARTS: sayfa onceden inline SVG ile ciziyordu ve gerekcesi
 * "kutuphane eklemek paket boyutunu buyutur"du. O gerekce artik gecerli
 * degil — echarts zaten bagimlilik (cihaz detay grafikleri kullaniyor) ve bu
 * sayfa lazy yukleniyor. Kazanc: her seride ipucu/hover kendiliginden gelir;
 * elde cizilen SVG'de bunlar ya yoktu ya da her grafikte yeniden yazilacakti.
 *
 * SARMALAYICI SINIRI: bilesenler yalnizca VERIYI cizer. "Veri yok" ve
 * "guvenilmez veri" kararlari sayfaya aittir; grafik bos veriyle cagrilmaz.
 */
import { useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import ReactECharts from "echarts-for-react/lib/core";
import * as echarts from "echarts/core";
import {
  BarChart,
  HeatmapChart,
  LineChart,
  PieChart,
  SankeyChart,
  ScatterChart
} from "echarts/charts";
import {
  CalendarComponent,
  GridComponent,
  LegendComponent,
  TooltipComponent,
  VisualMapComponent
} from "echarts/components";
import { CanvasRenderer } from "echarts/renderers";

import {
  DURUM,
  FAZ_RENK,
  HABERLESME_RENK,
  KATEGORIK,
  TAKVIM_BOS,
  TAKVIM_RAMPA,
  TEK_SERI,
  cubuk,
  degerEkseni,
  ipucu,
  izgara,
  kategoriEkseni
} from "./faultChartTheme";

echarts.use([
  BarChart,
  LineChart,
  // Sankey echarts'ta YERLESIK — yeni bir kutuphane gerekmedi.
  SankeyChart,
  // Cihaz x zaman alarm yogunlugu; cografi isi haritasi (Leaflet canvas)
  // ile karistirilmasin — bu matris, o cografya.
  HeatmapChart,
  // Filo dagilimlari: haberlesme durumu (halka) ve sinyal/alarm sacilimi.
  PieChart,
  ScatterChart,
  GridComponent,
  // Takvim yerlesimi (GitHub katki gorunumu) echarts'in kendi bileseni;
  // hafta/gun izgarasini elle hesaplamak gerekmiyor.
  CalendarComponent,
  LegendComponent,
  TooltipComponent,
  VisualMapComponent,
  CanvasRenderer
]);

/** Uzun hat/bolge adlari ekseni tasmasin. */
function kisalt(s: string, n = 18): string {
  return s.length > n ? `${s.slice(0, n - 1)}…` : s;
}

/**
 * Kabin olculen boyutu. Takvim hucrelerini kaba gore olceklemek icin.
 *
 * NEDEN OLCUM GEREKIYOR: takvimin dogal yuksekligi GENISLIKTEN turer —
 * hucreler KARE olmak zorunda ve 53 haftalik bir izgara yatayda ne kadar
 * yer bulursa dikeyde de o kadar kaplar. "Yuksekligi %100 yap" demek
 * hucreleri dikey dikdortgene cevirirdi; GitHub gorunumu tam da karelige
 * dayaniyor. Bu yuzden hucre kenari iki kisittan KUCUK olanina baglanir.
 *
 * `size-sensor` echarts'in kendi yeniden cizimini zaten hallediyor; bu
 * olcum yalnizca hucre kenarini secmek icin.
 */
function useKapOlcusu(): [React.RefObject<HTMLDivElement>, { w: number; h: number }] {
  const ref = useRef<HTMLDivElement>(null);
  const [olcu, setOlcu] = useState({ w: 0, h: 0 });

  useEffect(() => {
    const el = ref.current;
    if (!el || typeof ResizeObserver === "undefined") return;
    const gozlemci = new ResizeObserver(([giris]) => {
      const r = giris.contentRect;
      // Tam sayiya yuvarla: alt piksel degisimleri sonsuz dongu uretmesin.
      setOlcu((o) => {
        const w = Math.round(r.width);
        const h = Math.round(r.height);
        return o.w === w && o.h === h ? o : { w, h };
      });
    });
    gozlemci.observe(el);
    return () => gozlemci.disconnect();
  }, []);

  return [ref, olcu];
}

type SiralamaProps = {
  /** En buyukten kucuge sirali gelmeli. */
  items: { label: string; value: number; color?: string }[];
  /** Cubugun sagindaki birim ("ariza"). */
  birim: string;
  yukseklik?: number;
};

/**
 * Yatay siralama cubugu — "hangisi en cok" sorusunun formu.
 *
 * Yatay: hat/bolge adlari uzun; dikey cubukta etiketler egilir ya da kirpilir.
 * Deger cubugun UCUNDA dogrudan yazili — okuyucu eksene gidip geri donmesin.
 */
export function SiralamaGrafigi({ items, birim, yukseklik }: SiralamaProps) {
  const option = useMemo(() => {
    // echarts kategori eksenini asagidan yukari dizer; en buyuk USTTE olsun.
    const ters = [...items].reverse();
    return {
      grid: izgara(126, 6, 46, 6),
      tooltip: {
        ...ipucu,
        trigger: "item",
        formatter: (p: { name: string; value: number }) =>
          `<b>${p.name}</b><br/>${p.value} ${birim}`
      },
      xAxis: { ...degerEkseni, axisLabel: { show: false }, splitLine: { show: false } },
      yAxis: {
        ...kategoriEkseni,
        data: ters.map((x) => kisalt(x.label)),
        axisLine: { show: false }
      },
      series: [
        {
          ...cubuk,
          data: ters.map((x) => ({
            value: x.value,
            itemStyle: { color: x.color ?? TEK_SERI, borderRadius: [0, 4, 4, 0] }
          })),
          // Dogrudan etiket: az sayida cubuk var, hepsi etiketlenebilir.
          label: {
            show: true,
            position: "right",
            color: "#334155",
            fontSize: 11,
            fontWeight: 600
          }
        }
      ]
    };
  }, [items, birim]);

  return (
    <ReactECharts
      echarts={echarts}
      option={option}
      style={{ height: yukseklik ?? Math.max(120, items.length * 30 + 16) }}
      opts={{ renderer: "canvas" }}
    />
  );
}

type EgilimProps = {
  points: { month: string; count: number }[];
  labelToplam: string;
};

/**
 * Aylik egilim — zaman ekseni oldugu icin CIZGI (cubuk degil).
 *
 * Alan dolgusu egimi okunur kilar; imlec cizgisi (crosshair) ile ay ay
 * deger okunabilir.
 */
export function EgilimGrafigi({ points, labelToplam }: EgilimProps) {
  const option = useMemo(
    () => ({
      grid: izgara(38, 14, 18, 26),
      tooltip: {
        ...ipucu,
        trigger: "axis",
        axisPointer: { type: "line", lineStyle: { color: "#cbd5e1" } },
        formatter: (ps: { name: string; value: number }[]) =>
          `<b>${ps[0]?.name}</b><br/>${ps[0]?.value} ${labelToplam}`
      },
      xAxis: {
        ...kategoriEkseni,
        data: points.map((p) => p.month),
        boundaryGap: false,
        axisLabel: { color: "#94a3b8", fontSize: 10.5 }
      },
      yAxis: { ...degerEkseni, minInterval: 1 },
      series: [
        {
          type: "line",
          smooth: 0.35,
          symbol: "circle",
          symbolSize: 8,
          data: points.map((p) => p.count),
          lineStyle: { width: 2, color: TEK_SERI },
          itemStyle: { color: TEK_SERI, borderColor: "#fff", borderWidth: 2 },
          areaStyle: {
            color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
              { offset: 0, color: "rgba(37,99,235,0.22)" },
              { offset: 1, color: "rgba(37,99,235,0.02)" }
            ])
          }
        }
      ]
    }),
    [points, labelToplam]
  );

  return (
    <ReactECharts
      echarts={echarts}
      option={option}
      style={{ height: 210 }}
      opts={{ renderer: "canvas" }}
    />
  );
}

type FazProps = {
  items: { phase: string; count: number; label: string }[];
  birim: string;
};

/**
 * Faz dagilimi — kategorik, sabit renk.
 *
 * Renk fazin KIMLIGINI tasir (L1/L2/L3), buyuklugunu degil; bu yuzden
 * siralama degisse bile renkler yer degistirmez.
 */
export function FazGrafigi({ items, birim }: FazProps) {
  return (
    <SiralamaGrafigi
      items={items.map((x) => ({
        label: x.label,
        value: x.count,
        color: FAZ_RENK[x.phase] ?? DURUM.uyari
      }))}
      birim={birim}
    />
  );
}


/** Dugum adi "B:Merkez" -> ekranda "Merkez". Onek yalnizca benzersizlik icin
 *  var (echarts dugumleri ADA gore eslestirir), kullaniciya gosterilmez. */
function dugumEtiketi(ad: string): string {
  const i = ad.indexOf(":");
  return i === -1 ? ad : ad.slice(i + 1);
}

/** Kademe cizim sirasi. echarts dugumleri `data` sirasina gore dizer. */
const KADEME_SIRA: Record<string, number> = { region: 0, line: 1, phase: 2 };

type SankeyProps = {
  nodes: { name: string; tier: string }[];
  links: { source: string; target: string; value: number }[];
  /** Ekranda gorunecek ad. Faz dugumleri sayfanin geri kalaniyla ayni dili
   *  konussun diye disaridan verilir ("F:A" -> "L1"). */
  etiketle?: (ad: string, kademe: string) => string;
  birim: string;
  yukseklik?: number;
  /** Kart kalan dikey alani dolduruyorsa akis da doldursun. Sankey yerden
   *  en cok kazanan grafik: dugumler dikeyde ayrildikca kenarlarin nereye
   *  aktigi okunur hale geliyor. */
  dolduran?: boolean;
};

/**
 * Bolge -> Hat -> Faz akisi.
 *
 * NEDEN SANKEY: "hangi hatta cok ariza var" tek basina bir sayidir ve cubuk
 * grafigi bunu zaten veriyor. Sankey'in kattigi sey AKISIN NEREYE GITTIGI —
 * "su bolgedeki arizalarin cogu tek bir hatta ve o hattin da A fazinda
 * toplaniyor" deseni uc ayri cubuk grafiginde GORUNMEZ.
 *
 * Renk kademeye gore: bolge notr, hat tek-seri mavisi, faz ise faz rengi
 * (operator ayni kodu cihaz ekraninda zaten ogrendi).
 */
export function SankeyGrafigi({
  nodes,
  links,
  etiketle,
  birim,
  yukseklik = 360,
  dolduran
}: SankeyProps) {
  const option = useMemo(() => {
    const ad = (n: string, kademe: string) =>
      etiketle ? etiketle(n, kademe) : dugumEtiketi(n);

    const kademeOf = new Map(nodes.map((n) => [n.name, n.tier]));
    const gosterilen = (n: string) => ad(n, kademeOf.get(n) ?? "");

    // Dugumun tasidigi toplam akis. Gelen kenar varsa o, yoksa (ilk kademe)
    // giden kenarlar.
    const agirlik = new Map<string, number>();
    links.forEach((l) => {
      agirlik.set(l.target, (agirlik.get(l.target) ?? 0) + l.value);
    });
    nodes.forEach((n) => {
      if (agirlik.has(n.name)) return;
      const giden = links
        .filter((l) => l.source === n.name)
        .reduce((s, l) => s + l.value, 0);
      agirlik.set(n.name, giden);
    });

    const renk = (n: { name: string; tier: string }) => {
      if (n.tier === "phase") {
        const kod = dugumEtiketi(n.name).toLowerCase();
        return FAZ_RENK[kod] ?? KATEGORIK[3];
      }
      if (n.tier === "line") return TEK_SERI;
      return "#64748b";
    };

    // Siralama BURADA yapilir, `layoutIterations: 0` ile birlikte.
    // echarts'in kendi yerlesim gevsetmesi capraz kenar sayisini azaltir ama
    // dugumlerin dikey sirasi veri az degisince ziplayabiliyor; 120 sn'de bir
    // tazelenen bir ekranda bu "akis degisti" gibi okunur. Onun yerine sabit
    // ve okunabilir bir sira: kademe icinde EN AGIR USTTE.
    const sirali = [...nodes].sort((a, b) => {
      const k = (KADEME_SIRA[a.tier] ?? 9) - (KADEME_SIRA[b.tier] ?? 9);
      if (k !== 0) return k;
      return (agirlik.get(b.name) ?? 0) - (agirlik.get(a.name) ?? 0);
    });

    return {
      tooltip: {
        ...ipucu,
        trigger: "item",
        triggerOn: "mousemove",
        formatter: (p: { dataType: string; data: Record<string, unknown> }) => {
          if (p.dataType === "edge") {
            const d = p.data as { source: string; target: string; value: number };
            return `${gosterilen(d.source)} → ${gosterilen(d.target)}<br/><b>${d.value}</b> ${birim}`;
          }
          const d = p.data as { name: string };
          return `${gosterilen(d.name)}<br/><b>${agirlik.get(d.name) ?? 0}</b> ${birim}`;
        }
      },
      series: [
        {
          type: "sankey",
          left: 8,
          right: 88,
          top: 10,
          bottom: 10,
          layoutIterations: 0,
          nodeGap: 10,
          nodeWidth: 12,
          emphasis: { focus: "adjacency" },
          data: sirali.map((n) => ({
            name: n.name,
            itemStyle: { color: renk(n), borderWidth: 0 }
          })),
          links,
          label: {
            color: "#334155",
            fontSize: 11.5,
            formatter: (p: { name: string }) => gosterilen(p.name)
          },
          lineStyle: { color: "gradient", opacity: 0.42, curveness: 0.5 }
        }
      ]
    };
  }, [nodes, links, etiketle, birim]);

  return (
    <ReactECharts
      echarts={echarts}
      option={option}
      style={{ height: dolduran ? "100%" : yukseklik, width: "100%" }}
      opts={{ renderer: "canvas" }}
      notMerge
    />
  );
}

type SaatProfiliProps = {
  points: { hour_utc: number; avg_dbm: number; worst_dbm: number | null }[];
  /** UTC -> yerel saat kaymasi (saat). Sinyal deseni DUVAR SAATIYLE
   *  yorumlanir: "her aksam 19'da dusuyor" ancak yerel saatte anlamlidir. */
  utcOffsetHours: number;
  yukseklik?: number;
};

/**
 * Gunun saatine gore sinyal kalitesi (dBm).
 *
 * Iki seri: ortalama ve DIP. Dip onemli — ortalamasi iyi ama belirli
 * saatlerde dibe vuran bir sebeke, surekli orta seviyede olandan daha cok
 * kopma uretir; kopmalar o dip anlarinda olur.
 *
 * RSSI negatiftir ve 0'a yakin GUCLUDUR; eksen bu yuzden ters okunur,
 * asagi = kotu.
 */
export function SaatProfiliGrafigi({
  points,
  utcOffsetHours,
  yukseklik = 220
}: SaatProfiliProps) {
  // Seri adlari ipucunda GORUNUR (trigger: "axis"), yani cevrilmek zorunda;
  // sabit Turkce birakildiginda EN arayuzde karisik dil cikiyordu.
  const { t } = useTranslation();
  const option = useMemo(() => {
    const yerel = points
      .map((p) => ({
        saat: (((p.hour_utc + utcOffsetHours) % 24) + 24) % 24,
        ort: p.avg_dbm,
        dip: p.worst_dbm
      }))
      .sort((a, b) => a.saat - b.saat);
    return {
      grid: izgara(46, 10, 16, 26),
      tooltip: {
        ...ipucu,
        trigger: "axis",
        valueFormatter: (v: number) => (v == null ? "—" : `${v} dBm`)
      },
      xAxis: {
        ...kategoriEkseni,
        data: yerel.map((p) => `${String(p.saat).padStart(2, "0")}:00`)
      },
      yAxis: { ...degerEkseni, axisLabel: { ...degerEkseni.axisLabel, formatter: "{value}" } },
      series: [
        {
          type: "line",
          name: t("faultAnalytics.rssiAvg"),
          smooth: true,
          symbol: "none",
          data: yerel.map((p) => p.ort),
          lineStyle: { width: 2.2, color: TEK_SERI },
          areaStyle: { color: TEK_SERI, opacity: 0.1 }
        },
        {
          type: "line",
          name: t("faultAnalytics.rssiWorst"),
          smooth: true,
          symbol: "none",
          data: yerel.map((p) => p.dip),
          lineStyle: { width: 1.6, color: DURUM.uyari, type: "dashed" }
        }
      ]
    };
  }, [points, utcOffsetHours, t]);

  return (
    <ReactECharts
      echarts={echarts}
      option={option}
      style={{ height: yukseklik, width: "100%" }}
      opts={{ renderer: "canvas" }}
      notMerge
    />
  );
}

// ---------------------------------------------------------------------------
// Alarm takvimi — GitHub katki gorunumu
// ---------------------------------------------------------------------------

type TakvimProps = {
  /** Kronolojik ve KESINTISIZ gunler; bos gun de 0 ile gelir. */
  days: { date: string; count: number }[];
  /** `YYYY-MM-DD` — takvim araligi. */
  start: string;
  end: string;
  max: number;
  birim: string;
  /** Kart kalan dikey alani dolduruyorsa hucreler o alana gore buyur. */
  dolduran?: boolean;
};

/** Takvim yerlesim sabitleri — hucre kenari hesabinda kullanilir. */
const TAKVIM = {
  /** Ay etiketi (ust) + gun etiketi sutunu (sol) icin ayrilan yer. */
  ustBosluk: 26,
  solBosluk: 34,
  sagBosluk: 8,
  altBosluk: 6,
  /** Hucre kenari sinirlari. Alt sinir okunabilirlik, ust sinir ise
   *  "GitHub gibi" gorunumun bozulmamasi icin — 40 pikseli asan kareler
   *  takvim degil isi matrisi gibi okunuyor. */
  enKucuk: 11,
  enBuyuk: 40
} as const;

/** Sayiyi 5 kademeye ayirir: 0 ayri, kalani rampanin dort adimi.
 *
 *  Esikler MAKSIMUMA gore orantili; sabit esik (1/3/5/10) bir sahada tum
 *  kareleri en koyu, digerinde hepsini en acik yapardi. Orantili esik
 *  "bu sahanin kendi olceginde yogun mu" sorusunu cevaplar. */
function takvimKademeleri(max: number): { min: number; max?: number; color: string }[] {
  const t = Math.max(1, max);
  const k1 = Math.max(1, Math.ceil(t * 0.25));
  const k2 = Math.max(k1 + 1, Math.ceil(t * 0.5));
  const k3 = Math.max(k2 + 1, Math.ceil(t * 0.75));
  return [
    // "Alarm yok" rampanin en acik adimi DEGIL, ayri bir notr renk:
    // sessiz bir gun ile "en az alarmli" gun ayni gorunmemeli.
    { min: 0, max: 0, color: TAKVIM_BOS },
    { min: 1, max: k1, color: TAKVIM_RAMPA[0] },
    { min: k1 + 1, max: k2, color: TAKVIM_RAMPA[1] },
    { min: k2 + 1, max: k3, color: TAKVIM_RAMPA[2] },
    { min: k3 + 1, color: TAKVIM_RAMPA[3] }
  ];
}

/**
 * Gun gun alarm sikligi — GitHub'in katki takvimi bicimi.
 *
 * NEDEN BU BICIM: sorulan sey "SAHA NE ZAMAN GURULTULUYDU". Cubuk grafigi
 * bunu gune indirir ama haftalik/aylik ritmi (her pazartesi, her ayin ilk
 * haftasi) gostermez; takvim izgarasi ritmi konumdan okutur. Bos gun de
 * kare acar — sessiz gecen bir hafta grafikte gercekten bir hafta
 * genisligindedir, veri olmadigi icin kaybolmaz.
 *
 * ETKILESIM: her kare kendi ipucunu tasir (tarih + kesin adet). Koyuluk
 * deseni verir, isaretci SAYIYI verir; goz kestirmek zorunda kalmaz.
 */
export function AlarmTakvimi({ days, start, end, max, birim, dolduran }: TakvimProps) {
  const { i18n } = useTranslation();
  const [kapRef, kap] = useKapOlcusu();

  /** Hucre kenari — KARE kalmak zorunda, bu yuzden iki kisittan kucugu.
   *  Genislik kisiti: 53 hafta yan yana sigmali. Yukseklik kisiti: 7 gun
   *  alt alta sigmali. Olcum daha gelmediyse eski sabit (14) kullanilir;
   *  ilk kare sonrasi zaten yeniden cizilir. */
  const hucre = useMemo(() => {
    if (!dolduran || kap.w === 0 || kap.h === 0) return 14;
    const hafta = Math.max(1, Math.ceil(days.length / 7) + 1);
    const genislikten = (kap.w - TAKVIM.solBosluk - TAKVIM.sagBosluk) / hafta;
    const yukseklikten = (kap.h - TAKVIM.ustBosluk - TAKVIM.altBosluk) / 7;
    return Math.max(
      TAKVIM.enKucuk,
      Math.min(TAKVIM.enBuyuk, Math.floor(Math.min(genislikten, yukseklikten)))
    );
  }, [dolduran, kap.w, kap.h, days.length]);

  const option = useMemo(() => {
    const kademeler = takvimKademeleri(max);
    const tr = i18n.language?.startsWith("tr");
    // echarts `nameMap` dizileri PAZAR'dan baslar; `firstDay: 1` yalnizca
    // cizim sirasini kaydirir, dizinin sirasini degil.
    const gunAdlari = tr
      ? ["Paz", "Pzt", "Sal", "Çar", "Per", "Cum", "Cmt"]
      : ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
    const ayAdlari = tr
      ? ["Oca", "Şub", "Mar", "Nis", "May", "Haz", "Tem", "Ağu", "Eyl", "Eki", "Kas", "Ara"]
      : ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
    return {
      tooltip: {
        ...ipucu,
        trigger: "item",
        formatter: (p: { value: [string, number] }) => {
          const [tarih, adet] = p.value;
          const d = new Date(`${tarih}T00:00:00`);
          const bicim = Number.isNaN(d.getTime())
            ? tarih
            : d.toLocaleDateString(i18n.language, {
                day: "2-digit",
                month: "short",
                year: "numeric"
              });
          return `<b>${bicim}</b><br/>${adet} ${birim}`;
        }
      },
      visualMap: {
        type: "piecewise",
        // Kendi seridimizi HTML'de ciziyoruz (GitHub'daki "az -> cok"
        // serridi gibi); echarts'inki dikey yer kapliyor ve takvimi
        // daraltiyor.
        show: false,
        pieces: kademeler,
        // Veride olmayan gun (aralik disi) hic boyanmaz.
        outOfRange: { color: "transparent" }
      },
      calendar: {
        top: TAKVIM.ustBosluk,
        left: TAKVIM.solBosluk,
        right: TAKVIM.sagBosluk,
        bottom: TAKVIM.altBosluk,
        // KARE hucre: iki eksende de ayni kenar. `["auto", h]` verilseydi
        // genis kartta hucreler yatay dikdortgene uzar ve GitHub gorunumu
        // bozulurdu.
        cellSize: [hucre, hucre],
        range: [start, end],
        splitLine: { show: false },
        itemStyle: { color: "transparent", borderWidth: 3, borderColor: "#fff" },
        yearLabel: { show: false },
        dayLabel: {
          nameMap: gunAdlari,
          color: "#94a3b8",
          fontSize: 10,
          // Hafta PAZARTESI baslar — saha vardiyasi da oyle.
          firstDay: 1
        },
        monthLabel: {
          nameMap: ayAdlari,
          color: "#94a3b8",
          fontSize: 10.5
        }
      },
      series: [
        {
          type: "heatmap",
          coordinateSystem: "calendar",
          data: days.map((g) => [g.date, g.count]),
          itemStyle: { borderRadius: 2, borderColor: "#fff", borderWidth: 2 },
          emphasis: { itemStyle: { borderColor: "#0f172a", borderWidth: 1.5 } }
        }
      ]
    };
  }, [days, start, end, max, birim, i18n.language, hucre]);

  // Dikeyde her zaman 7 satir; yukseklik hucre kenarindan turer. Dolduran
  // kartta kap yuksekligini olcup hucreyi buyutuyoruz — cizim alani yine
  // TAM olarak 7 satir kapliyor, artan yer kabin ortalamasina gidiyor.
  const yukseklik = dolduran
    ? hucre * 7 + TAKVIM.ustBosluk + TAKVIM.altBosluk
    : 168;

  return (
    <div ref={kapRef} className={dolduran ? "fa-cal-wrap" : undefined}>
      <ReactECharts
        echarts={echarts}
        option={option}
        style={{ height: yukseklik, width: "100%" }}
        opts={{ renderer: "canvas" }}
        notMerge
      />
    </div>
  );
}

/** Takvimin altindaki "az -> cok" seridi. GitHub'daki ile ayni is: koyuluk
 *  olceginin ne anlama geldigini renk kodunu ezberletmeden soyler. */
export function TakvimSeridi({ max, azLabel, cokLabel }: {
  max: number;
  azLabel: string;
  cokLabel: string;
}) {
  const kademeler = takvimKademeleri(max);
  return (
    <div className="fa-cal-legend">
      <span>{azLabel}</span>
      {kademeler.map((k) => (
        <i key={k.color} style={{ background: k.color }} aria-hidden="true" />
      ))}
      <span>{cokLabel}</span>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Filo dagilimlari
// ---------------------------------------------------------------------------

type HalkaProps = {
  items: { label: string; value: number; color: string }[];
  birim: string;
  /** Ortada gosterilecek toplam. */
  toplamLabel: string;
};

/**
 * Haberlesme durumu dagilimi — halka.
 *
 * NEDEN HALKA, CUBUK DEGIL: burada sorulan sey siralama degil PAY. "Filonun
 * ne kadari su an ayakta" bir butun-parca sorusudur ve uc dilimde halka bunu
 * cubuktan daha dogrudan okutur. Uc kategoriyi asmaz — asaydi cubuga
 * gecmek gerekirdi.
 *
 * Kimlik renge TEK BASINA birakilmaz: her dilimin adi ve adedi lejantta
 * yazili (bkz. HABERLESME_RENK notu).
 */
export function HalkaGrafigi({ items, birim, toplamLabel }: HalkaProps) {
  const option = useMemo(() => {
    const toplam = items.reduce((s, x) => s + x.value, 0);
    return {
      tooltip: {
        ...ipucu,
        trigger: "item",
        formatter: (p: { name: string; value: number; percent: number }) =>
          `<b>${p.name}</b><br/>${p.value} ${birim} · %${p.percent}`
      },
      legend: {
        orient: "vertical",
        right: 4,
        top: "middle",
        itemWidth: 9,
        itemHeight: 9,
        icon: "circle",
        textStyle: { color: "#475569", fontSize: 11.5 },
        formatter: (ad: string) => {
          const x = items.find((i) => i.label === ad);
          return x ? `${ad}  ${x.value}` : ad;
        }
      },
      series: [
        {
          type: "pie",
          radius: ["58%", "82%"],
          center: ["32%", "50%"],
          avoidLabelOverlap: true,
          // Dilim etiketleri halkanin ORTASINDA toplaniyor: kucuk dilimlerde
          // disari tasan etiket cizgileri kartin yarisini yerdi.
          label: {
            show: true,
            position: "center",
            formatter: () => `{a|${toplam}}\n{b|${toplamLabel}}`,
            rich: {
              a: { fontSize: 22, fontWeight: 700, color: "#0f172a" },
              b: { fontSize: 10.5, color: "#94a3b8", padding: [3, 0, 0, 0] }
            }
          },
          emphasis: { label: { show: true }, scaleSize: 4 },
          labelLine: { show: false },
          // 2px zemin bosugu: bitisik dilimler birbirine yapismasin.
          itemStyle: { borderColor: "#fff", borderWidth: 2 },
          data: items.map((x) => ({
            name: x.label,
            value: x.value,
            itemStyle: { color: x.color }
          }))
        }
      ]
    };
  }, [items, birim, toplamLabel]);

  return (
    <ReactECharts
      echarts={echarts}
      option={option}
      style={{ height: 190, width: "100%" }}
      opts={{ renderer: "canvas" }}
      notMerge
    />
  );
}

type DagilimProps = {
  /** Kova etiketi + o kovaya dusen cihaz sayisi. */
  bins: { label: string; count: number }[];
  eksenLabel: string;
  birim: string;
};

/**
 * Filo dagilimi — histogram.
 *
 * NEDEN LISTE YETMIYOR: "en zayif 10 cihaz" listesi her zaman doludur; saha
 * mukemmel olsa bile bir "en kotu 10" vardir. Bu liste tek basina okundugunda
 * her kurulum sorunlu gorunur. Histogram sorunun SEKLINI verir: filo tek
 * tepede mi toplanmis (tekil arizali cihazlar), yoksa iki tepeye mi ayrilmis
 * (sistematik bir bolge sorunu).
 */
export function DagilimGrafigi({ bins, eksenLabel, birim }: DagilimProps) {
  const option = useMemo(
    () => ({
      grid: izgara(40, 14, 14, 40),
      tooltip: {
        ...ipucu,
        trigger: "axis",
        axisPointer: { type: "shadow" },
        formatter: (ps: { name: string; value: number }[]) =>
          `<b>${ps[0]?.name}</b> ${eksenLabel}<br/>${ps[0]?.value} ${birim}`
      },
      xAxis: {
        ...kategoriEkseni,
        data: bins.map((b) => b.label),
        axisLabel: { color: "#94a3b8", fontSize: 10, hideOverlap: true }
      },
      yAxis: { ...degerEkseni, minInterval: 1 },
      series: [
        {
          type: "bar",
          data: bins.map((b) => b.count),
          barMaxWidth: 44,
          // Histogramda kovalar BITISIKTIR (araliklar sureklidir); 2px'lik
          // zemin bosugu ayrimi verir, buyuk bosluk "kategorik" yalani soyler.
          barCategoryGap: "2%",
          itemStyle: { color: TEK_SERI, borderRadius: [4, 4, 0, 0] }
        }
      ]
    }),
    [bins, eksenLabel, birim]
  );

  return (
    <ReactECharts
      echarts={echarts}
      option={option}
      style={{ height: 200, width: "100%" }}
      opts={{ renderer: "canvas" }}
      notMerge
    />
  );
}

type SacilimProps = {
  points: {
    code: string;
    name: string;
    x: number;
    y: number;
    status: string;
  }[];
  xLabel: string;
  yLabel: string;
  /** Durum kodu -> ekran metni (lejant ve ipucu icin). */
  durumLabel: (kod: string) => string;
};

/**
 * Sinyal x alarm sacilimi — CAPRAZ soru.
 *
 * Ekrandaki listeler tek olcude "en kotu"yu verir ve aralarinda baglanti
 * kurdurmaz. Asil karar ise capraz sorudan cikar: sinyali zayif cihazlar
 * ayni zamanda cok mu alarm uretiyor?
 *   * Evet (sol ust yogunlasma) -> sorun ESIKTE degil ANTEN/MODEMDE.
 *   * Hayir (dagimik)           -> iki ayri is emri gerekir.
 * Bu deseni iki ayri siralama listesi HIC gostermez.
 *
 * X ekseni RSSI: negatif ve 0'a yakini iyi. Sola gidildikce kotulesir,
 * yani "sol ust" en sorunlu koseyi verir.
 */
export function SacilimGrafigi({ points, xLabel, yLabel, durumLabel }: SacilimProps) {
  const option = useMemo(() => {
    const durumlar = [...new Set(points.map((p) => p.status))].sort();
    return {
      grid: izgara(44, 14, 18, 44),
      tooltip: {
        ...ipucu,
        trigger: "item",
        formatter: (p: { data: { d: SacilimProps["points"][number] } }) => {
          const d = p.data.d;
          return `<b>${d.name}</b><br/>${d.code} · ${durumLabel(d.status)}<br/>${
            d.x
          } dBm · ${d.y} ${yLabel}`;
        }
      },
      legend: {
        top: 0,
        right: 0,
        itemWidth: 9,
        itemHeight: 9,
        icon: "circle",
        textStyle: { color: "#475569", fontSize: 11 }
      },
      xAxis: {
        ...degerEkseni,
        name: xLabel,
        nameLocation: "middle",
        nameGap: 26,
        nameTextStyle: { color: "#94a3b8", fontSize: 10.5 },
        scale: true
      },
      yAxis: {
        ...degerEkseni,
        name: yLabel,
        nameLocation: "middle",
        nameGap: 30,
        nameTextStyle: { color: "#94a3b8", fontSize: 10.5 },
        minInterval: 1
      },
      series: durumlar.map((durum) => ({
        type: "scatter",
        name: durumLabel(durum),
        // >=8px isaretci: daha kucugu dokunmatikte hedeflenemiyor.
        symbolSize: 10,
        data: points
          .filter((p) => p.status === durum)
          .map((p) => ({ value: [p.x, p.y], d: p })),
        itemStyle: {
          color: HABERLESME_RENK[durum] ?? "#94a3b8",
          // 2px zemin halkasi: ust uste binen noktalar birbirinden ayrilsin.
          borderColor: "#fff",
          borderWidth: 1.5,
          opacity: 0.92
        }
      }))
    };
  }, [points, xLabel, yLabel, durumLabel]);

  return (
    <ReactECharts
      echarts={echarts}
      option={option}
      style={{ height: 260, width: "100%" }}
      opts={{ renderer: "canvas" }}
      notMerge
    />
  );
}

type AlarmIsiProps = {
  /** Kronolojik kova etiketleri — sutunlar. */
  buckets: string[];
  /** Satirlar; en cok alarm ureten en USTTE gorunecek. */
  devices: { code: string; name: string; total: number }[];
  /** `[sutun, satir, adet]`. Bos kovalar listede YOKTUR. */
  cells: number[][];
  max: number;
  /** "day" | "hour" — eksen etiketi bicimini belirler. */
  bucket: string;
  birim: string;
  /** Kart kalan dikey alani dolduruyorsa matris de doldursun: satirlar
   *  yukseldikce hucreler okunur olur, bu grafik yerden KAZANIR. */
  dolduran?: boolean;
};

/** "2026-08-07" -> "07.08" · "2026-08-07 14" -> "07.08 14:00" */
function kovaEtiketi(k: string, gunluk: boolean): string {
  const [tarih, saat] = k.split(" ");
  const p = tarih.split("-");
  if (p.length !== 3) return k;
  const gun = `${p[2]}.${p[1]}`;
  return gunluk ? gun : `${gun} ${saat ?? "00"}:00`;
}

/**
 * Cihaz x zaman alarm yogunlugu.
 *
 * NEDEN ISI HARITASI, LISTE DEGIL: "en cok alarm ureten cihazlar" listesi
 * ZAMANI duzler ve iki bambaska durumu ayni sayiya indirir —
 *   * uc ay boyunca her gun 2 alarm (kronik: esik yanlis / montaj sorunlu),
 *   * tek bir gunde 180 alarm (o gun sahada bir olay olmus).
 * Ikisi de "180". Isi haritasi bunlari bakista ayirir. Ustelik AYNI sutunda
 * birden cok cihaz kararmissa sorun cihazlarda degil o gun yasanan ortak
 * olaydadir (besleme, sebeke, gateway).
 *
 * BOS HUCRE ILE SIFIR AYNI DEGIL: gonderilmeyen hucre hic cizilmez (zemin
 * rengi kalir), 0 alarmli bir kova ise zaten gonderilmez. Yani zemin
 * "alarm yok" demektir — uydurma bir "dusuk yogunluk" tonu degil.
 */
export function AlarmIsiHaritasi({
  buckets,
  devices,
  cells,
  max,
  bucket,
  birim,
  dolduran
}: AlarmIsiProps) {
  const gunluk = bucket !== "hour";
  const option = useMemo(() => {
    // echarts kategori eksenini asagidan yukari dizer; en cok alarm ureten
    // cihaz USTTE olsun diye satirlar ters cevrilir (hucre satir indeksi de).
    const sonSatir = devices.length - 1;
    return {
      grid: { left: 128, right: 16, top: 8, bottom: 46, containLabel: false },
      tooltip: {
        ...ipucu,
        position: "top",
        formatter: (p: { value: [number, number, number] }) => {
          const [sutun, satir, adet] = p.value;
          const d = devices[sonSatir - satir];
          return `<b>${d?.name ?? ""}</b><br/>${kovaEtiketi(
            buckets[sutun] ?? "",
            gunluk
          )}<br/>${adet} ${birim}`;
        }
      },
      xAxis: {
        type: "category",
        data: buckets.map((b) => kovaEtiketi(b, gunluk)),
        splitArea: { show: false },
        axisTick: { show: false },
        axisLine: { lineStyle: { color: "#e2e8f0" } },
        axisLabel: {
          color: "#94a3b8",
          fontSize: 10,
          // Cok sutunda etiketleri egmek okunurlugu artirmaz; echarts
          // kendi seyreltmesini yapsin, egim sabit kalsin.
          rotate: buckets.length > 24 ? 45 : 0,
          hideOverlap: true
        }
      },
      yAxis: {
        type: "category",
        data: [...devices].reverse().map((d) => d.name),
        axisTick: { show: false },
        axisLine: { show: false },
        splitArea: { show: false },
        axisLabel: {
          color: "#475569",
          fontSize: 11,
          width: 118,
          overflow: "truncate"
        }
      },
      visualMap: {
        min: 0,
        max: Math.max(1, max),
        calculable: false,
        orient: "horizontal",
        left: "center",
        bottom: 0,
        itemWidth: 11,
        itemHeight: 90,
        text: [String(Math.max(1, max)), "1"],
        textStyle: { color: "#94a3b8", fontSize: 10 },
        // ISI HARITASI PALETI heatField.ts HEAT_STOPS ile ayni ailede:
        // kullanici cografi isi haritasindan buraya gecerken renk kodunu
        // yeniden ogrenmesin. Yesil YOK — bu projede yesil "sorun yok".
        inRange: { color: ["#dbeafe", "#93c5fd", "#facc15", "#f97316", "#be123c"] }
      },
      series: [
        {
          type: "heatmap",
          data: cells.map(([sutun, satir, adet]) => [sutun, sonSatir - satir, adet]),
          progressive: 2000,
          itemStyle: { borderColor: "#fff", borderWidth: 1 },
          emphasis: { itemStyle: { borderColor: "#0f172a", borderWidth: 1.5 } }
        }
      ]
    };
  }, [buckets, devices, cells, max, gunluk, birim]);

  return (
    <ReactECharts
      echarts={echarts}
      option={option}
      // Dolduran kartta kalan alanin TAMAMI: satirlar yukseldikce hucreler
      // okunur olur. Aksi halde satir basina sabit yukseklik — 25 cihazda
      // kutucuklar okunabilir kalsin, 3 cihazda grafik gereksiz uzamasin.
      style={
        dolduran
          ? { height: "100%", width: "100%" }
          : { height: Math.min(560, Math.max(180, devices.length * 22 + 84)) }
      }
      opts={{ renderer: "canvas" }}
      notMerge
    />
  );
}
