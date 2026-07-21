/**
 * Sparkline — KPI kartinda kucuk trend cizgisi (eksen/legend/tooltip YOK).
 *
 * Historian'dan (fetchDeviceHistory bucket=raw) son N saatlik veriyi ceker,
 * Chart.js Line ile ~40px yuksekliginde minimal cizgi cizer. Veri yoksa bos.
 */

import { useEffect, useMemo, useState } from "react";
import {
  CategoryScale,
  Chart as ChartJS,
  LinearScale,
  LineElement,
  PointElement,
  Filler,
  type ChartOptions,
} from "chart.js";
import { Line } from "react-chartjs-2";

import { fetchDeviceHistory } from "../../shared/api";
import type { TelemetryHistoryPoint } from "../../shared/types";

ChartJS.register(CategoryScale, LinearScale, PointElement, LineElement, Filler);

type Props = {
  token: string;
  deviceCode: string;
  signalKey: string;
  hours?: number;
  color?: string;
};

const SPARK_OPTIONS: ChartOptions<"line"> = {
  responsive: true,
  maintainAspectRatio: false,
  animation: false,
  plugins: { legend: { display: false }, tooltip: { enabled: false } },
  scales: { x: { display: false }, y: { display: false } },
  elements: { line: { borderWidth: 1.5 }, point: { radius: 0 } },
};

export function Sparkline({
  token,
  deviceCode,
  signalKey,
  hours = 6,
  color = "#0ea5e9",
}: Props) {
  const [points, setPoints] = useState<number[]>([]);

  useEffect(() => {
    if (!token || !signalKey) return;
    let cancelled = false;
    const since = new Date(Date.now() - hours * 3600_000).toISOString();
    fetchDeviceHistory(token, deviceCode, signalKey, { bucket: "raw", since, limit: 2000 })
      .then((rows) => {
        if (cancelled) return;
        // raw noktalar; aggregate degil (bucket=raw). value dizisi.
        const vals = (rows as TelemetryHistoryPoint[])
          .map((r) => r.value)
          .filter((v): v is number => v != null);
        setPoints(vals);
      })
      .catch(() => {
        if (!cancelled) setPoints([]);
      });
    return () => {
      cancelled = true;
    };
  }, [token, deviceCode, signalKey, hours]);

  const data = useMemo(
    () => ({
      labels: points.map((_, i) => i),
      datasets: [
        {
          data: points,
          borderColor: color,
          backgroundColor: "transparent",
          fill: false,
          tension: 0.35,
        },
      ],
    }),
    [points, color]
  );

  if (points.length < 2) return <div className="device-spark is-empty" aria-hidden="true" />;
  return (
    <div className="device-spark">
      <Line data={data} options={SPARK_OPTIONS} />
    </div>
  );
}
