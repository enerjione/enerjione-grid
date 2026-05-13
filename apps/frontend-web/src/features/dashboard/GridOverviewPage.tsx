/** Anasayfa "Tablo" görünümü — Sebeke özeti.
 *
 * 4-seviyeli ağaç: Bolge -> Hat -> Direk (segment) -> Cihaz.
 * Arızası olan direk + bölge + hat satırları renkli vurgulanır (kırmızı band)
 * — operator hangi noktada problem oldugunu hızlıca ayırır.
 * Liste daraltilabilir/genisletilebilir. Filtre cubuğundan gelen Bolge/Hat/durum
 * filtrelerine saygi gosterir (devices listesi App tarafindan filtrelenmis).
 */
import { useMemo, useState } from "react";
import { useTranslation } from "react-i18next";

import type { AlarmEvent, DeviceRow } from "../../shared/types";
import type { GridSnapshot } from "../../shared/api";

type Props = {
  devices: DeviceRow[];
  alarms?: AlarmEvent[];
  gridSnapshot?: GridSnapshot | null;
  onSelectDevice?: (id: number) => void;
  selectedDeviceId?: number;
};

type DeviceFault = {
  level: string;
  title: string;
  signalKey?: string | null;
};

export function GridOverviewPage({
  devices,
  alarms,
  gridSnapshot,
  onSelectDevice,
  selectedDeviceId
}: Props) {
  const { t, i18n } = useTranslation();
  const localeTag = i18n.language?.startsWith("tr") ? "tr-TR" : "en-US";
  // Arızalı cihazların aktif alarm bilgisi (en güncel onaylanmamış)
  const deviceFaults = useMemo<Map<number, DeviceFault[]>>(() => {
    const m = new Map<number, DeviceFault[]>();
    for (const a of alarms ?? []) {
      if (a.reset) continue;
      const arr = m.get(a.device_id) ?? [];
      arr.push({ level: a.level, title: a.title, signalKey: a.signal_key });
      m.set(a.device_id, arr);
    }
    return m;
  }, [alarms]);

  // Cihazları bölge → hat altına grupla
  const grouped = useMemo(() => {
    type LineGroup = {
      lineId: number;
      lineName: string;
      lineCode: string;
      lineColor: string | null;
      devices: { device: DeviceRow; segmentLabel: string | null }[];
    };
    type RegionGroup = {
      regionId: number | null;
      regionName: string;
      regionColor: string | null;
      lines: Map<number | "none", LineGroup>;
    };

    const regions = new Map<number | "none", RegionGroup>();
    const ensureRegion = (
      id: number | null,
      name: string,
      color: string | null
    ): RegionGroup => {
      const key: number | "none" = id ?? "none";
      let r = regions.get(key);
      if (!r) {
        r = { regionId: id, regionName: name, regionColor: color, lines: new Map() };
        regions.set(key, r);
      }
      return r;
    };

    // Cihaz id -> {region, line, segmentLabel} lookup
    const lineById = new Map((gridSnapshot?.lines ?? []).map((l) => [l.id, l]));
    const regionById = new Map((gridSnapshot?.regions ?? []).map((r) => [r.id, r]));
    const deviceMeta = new Map<
      number,
      {
        regionId: number | null;
        regionName: string;
        regionColor: string | null;
        lineId: number | null;
        lineName: string | null;
        lineCode: string | null;
        lineColor: string | null;
        segmentLabel: string | null;
      }
    >();

    for (const seg of gridSnapshot?.segments ?? []) {
      if (!seg.device_id) continue;
      const line = lineById.get(seg.line_id);
      const region = line ? regionById.get(line.region_id) : undefined;
      const segLabel =
        seg.from_pole_seq !== null && seg.to_pole_seq !== null
          ? t("dashboard.overview.segmentLabel", { from: seg.from_pole_seq, to: seg.to_pole_seq })
          : null;
      deviceMeta.set(seg.device_id, {
        regionId: region?.id ?? null,
        regionName: region?.name ?? t("dashboard.overview.noRegionGroup"),
        regionColor: region?.color ?? null,
        lineId: line?.id ?? null,
        lineName: line?.name ?? null,
        lineCode: line?.code ?? null,
        lineColor: line?.color ?? region?.color ?? null,
        segmentLabel: segLabel
      });
    }

    for (const dev of devices) {
      const meta = deviceMeta.get(dev.id);
      const region = ensureRegion(
        meta?.regionId ?? null,
        meta?.regionName ?? t("dashboard.overview.noRegionGroup"),
        meta?.regionColor ?? null
      );
      const lineKey: number | "none" = meta?.lineId ?? "none";
      let line = region.lines.get(lineKey);
      if (!line) {
        line = {
          lineId: meta?.lineId ?? -1,
          lineName: meta?.lineName ?? t("dashboard.overview.noLineGroup"),
          lineCode: meta?.lineCode ?? "",
          lineColor: meta?.lineColor ?? null,
          devices: []
        };
        region.lines.set(lineKey, line);
      }
      line.devices.push({ device: dev, segmentLabel: meta?.segmentLabel ?? null });
    }

    // Map → sıralı array (bölgesiz en sona düşsün)
    const sortedRegions: RegionGroup[] = Array.from(regions.values()).sort((a, b) => {
      if (a.regionId === null) return 1;
      if (b.regionId === null) return -1;
      return a.regionName.localeCompare(b.regionName, localeTag);
    });
    return sortedRegions.map((r) => ({
      ...r,
      lines: Array.from(r.lines.values()).sort((a, b) => {
        if (a.lineId === -1) return 1;
        if (b.lineId === -1) return -1;
        return a.lineName.localeCompare(b.lineName, localeTag);
      })
    }));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [devices, gridSnapshot, i18n.language]);

  // Hangi bölge/hat açık (default hepsi açık)
  const [collapsedRegions, setCollapsedRegions] = useState<Set<number | "none">>(new Set());
  const [collapsedLines, setCollapsedLines] = useState<Set<string>>(new Set()); // "regionId-lineId"

  const toggleRegion = (key: number | "none") => {
    setCollapsedRegions((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  };

  const toggleLine = (regionKey: number | "none", lineKey: number | "none") => {
    const k = `${regionKey}-${lineKey}`;
    setCollapsedLines((prev) => {
      const next = new Set(prev);
      if (next.has(k)) next.delete(k);
      else next.add(k);
      return next;
    });
  };

  // Toplam istatistikler
  const stats = useMemo(() => {
    let total = 0;
    let alarmCount = 0;
    let onlineCount = 0;
    for (const r of grouped) {
      for (const l of r.lines) {
        for (const item of l.devices) {
          total++;
          if (item.device.communicationStatus === "online") onlineCount++;
          if ((deviceFaults.get(item.device.id)?.length ?? 0) > 0) alarmCount++;
        }
      }
    }
    return { total, alarmCount, onlineCount };
  }, [grouped, deviceFaults]);

  if (devices.length === 0) {
    return (
      <section className="grid-overview-empty">
        <span className="material-symbols-outlined">inbox</span>
        <h3>{t("dashboard.overview.noDevices")}</h3>
        <p className="helper-text">{t("dashboard.overview.noDevicesHint")}</p>
      </section>
    );
  }

  return (
    <section className="grid-overview-page">
      <div className="grid-overview-summary">
        <div className="grid-overview-summary-card">
          <span className="grid-overview-summary-label">{t("dashboard.overview.totalDevices")}</span>
          <strong>{stats.total}</strong>
        </div>
        <div className="grid-overview-summary-card grid-overview-summary-card--ok">
          <span className="grid-overview-summary-label">{t("dashboard.overview.online")}</span>
          <strong>{stats.onlineCount}</strong>
        </div>
        <div className="grid-overview-summary-card grid-overview-summary-card--alarm">
          <span className="grid-overview-summary-label">{t("dashboard.overview.activeFaults")}</span>
          <strong>{stats.alarmCount}</strong>
        </div>
      </div>

      <div className="grid-overview-tree">
        {grouped.map((region) => {
          const regionKey: number | "none" = region.regionId ?? "none";
          const regionCollapsed = collapsedRegions.has(regionKey);
          const regionDeviceCount = region.lines.reduce((s, l) => s + l.devices.length, 0);
          const regionAlarmCount = region.lines.reduce(
            (s, l) =>
              s +
              l.devices.filter(
                (d) => (deviceFaults.get(d.device.id)?.length ?? 0) > 0
              ).length,
            0
          );
          return (
            <div key={String(regionKey)} className="grid-overview-region">
              <button
                type="button"
                className="grid-overview-region-head"
                onClick={() => toggleRegion(regionKey)}
              >
                <span className="grid-overview-chevron">
                  {regionCollapsed ? "▶" : "▼"}
                </span>
                {region.regionColor ? (
                  <span
                    className="grid-overview-color-dot"
                    style={{ background: region.regionColor }}
                  />
                ) : null}
                <strong>{region.regionName}</strong>
                <span className="grid-overview-region-stats">
                  {t("dashboard.overview.regionStats", { lines: region.lines.length, devices: regionDeviceCount })}
                  {regionAlarmCount > 0 ? (
                    <span className="grid-overview-alarm-pill">{t("dashboard.overview.faultCount", { count: regionAlarmCount })}</span>
                  ) : null}
                </span>
              </button>

              {!regionCollapsed
                ? region.lines.map((line) => {
                    const lineKey: number | "none" = line.lineId === -1 ? "none" : line.lineId;
                    const lineCollapsed = collapsedLines.has(`${regionKey}-${lineKey}`);
                    const lineAlarmCount = line.devices.filter(
                      (d) => (deviceFaults.get(d.device.id)?.length ?? 0) > 0
                    ).length;
                    return (
                      <div key={String(lineKey)} className="grid-overview-line">
                        <button
                          type="button"
                          className="grid-overview-line-head"
                          onClick={() => toggleLine(regionKey, lineKey)}
                        >
                          <span className="grid-overview-chevron">
                            {lineCollapsed ? "▶" : "▼"}
                          </span>
                          <span
                            className="grid-overview-line-marker"
                            style={{ background: line.lineColor || "#94a3b8" }}
                          />
                          <span className="grid-overview-line-name">
                            {line.lineName}
                            {line.lineCode ? (
                              <code className="grid-overview-line-code">{line.lineCode}</code>
                            ) : null}
                          </span>
                          <span className="grid-overview-line-stats">
                            {t("dashboard.overview.lineDeviceCount", { count: line.devices.length })}
                            {lineAlarmCount > 0 ? (
                              <span className="grid-overview-alarm-pill">
                                {t("dashboard.overview.faultCount", { count: lineAlarmCount })}
                              </span>
                            ) : null}
                          </span>
                        </button>

                        {!lineCollapsed ? (
                          <table className="grid-overview-table">
                            <thead>
                              <tr>
                                <th style={{ width: 40 }}>{t("dashboard.overview.tableStatus")}</th>
                                <th>{t("dashboard.overview.tableDevice")}</th>
                                <th>{t("dashboard.overview.tableSegment")}</th>
                                <th>{t("dashboard.overview.tableFault")}</th>
                                <th style={{ width: 90 }}>{t("dashboard.overview.tableBattery")}</th>
                                <th style={{ width: 140 }}>{t("dashboard.overview.tableLastData")}</th>
                              </tr>
                            </thead>
                            <tbody>
                              {line.devices.map(({ device, segmentLabel }) => {
                                const faults = deviceFaults.get(device.id) ?? [];
                                const isOnline = device.communicationStatus === "online";
                                const isAlarmed = faults.length > 0;
                                const isSelected = selectedDeviceId === device.id;
                                return (
                                  <tr
                                    key={device.id}
                                    className={`grid-overview-row ${
                                      isAlarmed ? "is-alarm" : ""
                                    } ${isSelected ? "is-selected" : ""}`}
                                    onClick={() => onSelectDevice?.(device.id)}
                                  >
                                    <td className="grid-overview-status">
                                      <span
                                        className={`grid-overview-status-dot ${
                                          isAlarmed
                                            ? "is-alarm"
                                            : isOnline
                                              ? "is-online"
                                              : "is-offline"
                                        }`}
                                        title={
                                          isAlarmed
                                            ? t("dashboard.overview.tooltipFault")
                                            : isOnline
                                              ? t("dashboard.overview.tooltipOnline")
                                              : t("dashboard.overview.tooltipOffline")
                                        }
                                      />
                                    </td>
                                    <td>
                                      <div className="grid-overview-device">
                                        <strong>{device.name}</strong>
                                        <code>{device.code}</code>
                                      </div>
                                    </td>
                                    <td className="grid-overview-segment">
                                      {segmentLabel ?? <span className="helper-text">—</span>}
                                    </td>
                                    <td>
                                      {faults.length > 0 ? (
                                        <div className="grid-overview-faults">
                                          {faults.slice(0, 2).map((f, idx) => (
                                            <span
                                              key={idx}
                                              className={`grid-overview-fault grid-overview-fault--${f.level.toLowerCase()}`}
                                              title={f.signalKey ?? ""}
                                            >
                                              {f.title}
                                            </span>
                                          ))}
                                          {faults.length > 2 ? (
                                            <span className="grid-overview-fault-more">
                                              +{faults.length - 2}
                                            </span>
                                          ) : null}
                                        </div>
                                      ) : (
                                        <span className="helper-text">—</span>
                                      )}
                                    </td>
                                    <td>
                                      {typeof device.batteryPercent === "number" ? (
                                        <span className="grid-overview-battery">
                                          %{Math.round(device.batteryPercent)}
                                        </span>
                                      ) : (
                                        <span className="helper-text">—</span>
                                      )}
                                    </td>
                                    <td className="grid-overview-last">
                                      {device.lastUpdateAt
                                        ? formatRelative(device.lastUpdateAt, localeTag, t)
                                        : <span className="helper-text">—</span>}
                                    </td>
                                  </tr>
                                );
                              })}
                            </tbody>
                          </table>
                        ) : null}
                      </div>
                    );
                  })
                : null}
            </div>
          );
        })}
      </div>
    </section>
  );
}

function formatRelative(
  iso: string,
  localeTag: string,
  t: (key: string, opts?: Record<string, unknown>) => string
): string {
  const sec = Math.round((Date.now() - new Date(iso).getTime()) / 1000);
  if (sec < 5) return t("common.now");
  if (sec < 60) return t("common.secondsAgoShort", { count: sec });
  if (sec < 3600) return t("common.minutesAgoShort", { count: Math.round(sec / 60) });
  if (sec < 86400) return t("common.hoursAgoShort", { count: Math.round(sec / 3600) });
  return new Date(iso).toLocaleString(localeTag);
}
