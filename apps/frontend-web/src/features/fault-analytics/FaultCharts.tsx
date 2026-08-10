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
import { useMemo } from "react";
import ReactECharts from "echarts-for-react/lib/core";
import * as echarts from "echarts/core";
import { BarChart, LineChart, SankeyChart } from "echarts/charts";
import { GridComponent, TooltipComponent } from "echarts/components";
import { CanvasRenderer } from "echarts/renderers";

import {
  DURUM,
  FAZ_RENK,
  KATEGORIK,
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
  GridComponent,
  TooltipComponent,
  CanvasRenderer
]);

/** Uzun hat/bolge adlari ekseni tasmasin. */
function kisalt(s: string, n = 18): string {
  return s.length > n ? `${s.slice(0, n - 1)}…` : s;
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
  yukseklik = 360
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
      style={{ height: yukseklik, width: "100%" }}
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
          name: "Ortalama",
          smooth: true,
          symbol: "none",
          data: yerel.map((p) => p.ort),
          lineStyle: { width: 2.2, color: TEK_SERI },
          areaStyle: { color: TEK_SERI, opacity: 0.1 }
        },
        {
          type: "line",
          name: "Dip",
          smooth: true,
          symbol: "none",
          data: yerel.map((p) => p.dip),
          lineStyle: { width: 1.6, color: DURUM.uyari, type: "dashed" }
        }
      ]
    };
  }, [points, utcOffsetHours]);

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
