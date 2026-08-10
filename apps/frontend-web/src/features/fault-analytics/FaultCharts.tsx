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
import { BarChart, LineChart } from "echarts/charts";
import { GridComponent, TooltipComponent } from "echarts/components";
import { CanvasRenderer } from "echarts/renderers";

import {
  DURUM,
  FAZ_RENK,
  TEK_SERI,
  cubuk,
  degerEkseni,
  ipucu,
  izgara,
  kategoriEkseni
} from "./faultChartTheme";

echarts.use([BarChart, LineChart, GridComponent, TooltipComponent, CanvasRenderer]);

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
