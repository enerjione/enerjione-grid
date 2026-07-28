/**
 * Hat Agaci — Bolge > Hat > Cihaz kirilimli sidebar gorunumu.
 *
 * Topoloji: bir cihaz iki DIREK arasindaki segment'e baglidir
 * (`segment.device_id`). Agacta cihaz satirinin alt bilgisi bu yuzden
 * "Direk 3 ↔ Direk 4" olur; hangi iki direk arasinda oldugu sahada aranan
 * ilk bilgi.
 *
 * Cihaz satiri BILEREK duz listedeki karttan farkli: agacta amac hizli
 * tarama, o yuzden tek satirlik ince satir. Ayrinti isteyen cihaza tiklar,
 * sag taraftaki harita/detay zaten aciliyor.
 *
 * Ikonlar lucide-react (ust menu ve sekme seridiyle ayni set).
 *
 * Performans: hatlar VARSAYILAN KAPALI gelir. 500 cihazli kurulumda sadece
 * bolge/hat basliklari DOM'a girer; cihaz satirlari ancak hat acildiginda
 * cizilir.
 *
 * Hat gizleme: goz ikonu hattı "gizli" isaretler. Cihazlar agacta KALIR
 * (soluk gosterilir), haritada ise hat/direk/marker griye doner.
 */
import { useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import {
  Cable,
  ChevronDown,
  ChevronRight,
  Eye,
  EyeOff,
  MapPin,
  TriangleAlert,
  Unlink,
  WifiOff,
} from "lucide-react";

import type { GridSnapshot } from "../../shared/api";
import type { DeviceRow } from "../../shared/types";
import type { DeviceAlarmState } from "./DeviceRowButton";

type Props = {
  devices: DeviceRow[];
  selectedId: number;
  onSelect: (id: number) => void;
  gridSnapshot: GridSnapshot | null | undefined;
  alarmStateOf: (deviceId: number) => DeviceAlarmState;
  batteryOf: (device: DeviceRow) => number | null;
  /** Hat arizasi ureten aktif alarmi olan cihazlar (harita ile ayni kriter). */
  faultDeviceIds: Set<number>;
  hiddenLineIds: Set<number>;
  onToggleLineHidden: (lineId: number) => void;
};

/**
 * Hat icerigi bir ZAMAN CIZELGESI gibi cizilir: direkler eksen uzerinde
 * dugum, cihazlar iki dugum ARASINDAKI baglantida durur — sahadaki fiziksel
 * dizilimin aynisi. Boylece "Direk 2 ↔ Direk 3" gibi bir metne gerek kalmaz,
 * konum gozle okunur.
 *
 * Sadece CIHAZLI segmentler cizilir; aradaki cihazsiz direkler "gap" ile
 * kisaltilir. Aksi halde 30 direkli bir hatta 3 cihaz icin 30 satir cikardi.
 */
type TimelineItem =
  | { kind: "pole"; key: string; label: string; fault: boolean }
  | { kind: "gap"; key: string }
  | { kind: "devices"; key: string; devices: DeviceRow[]; fault: boolean };

type TreeLine = {
  id: number;
  name: string;
  code: string;
  timeline: TimelineItem[];
  deviceCount: number;
  onlineCount: number;
  alarmCount: number;
  /** Hatta arizali cihaz var — baslik kirmizi + nabiz. */
  faultCount: number;
  /** Haberlesmesi kopuk cihaz sayisi (offline/unknown). */
  commLostCount: number;
};
type TreeRegion = { id: number; name: string; lines: TreeLine[]; deviceCount: number };

/** Agac icindeki ince cihaz satiri — tek satir, kart degil.
 *  Konum bilgisi satirda YAZMAZ; timeline'daki yeri zaten soyluyor. */
function DeviceTreeRow({
  device,
  locationLabel,
  selected,
  onSelect,
  alarmState,
  batteryPercent,
  dimmed
}: {
  device: DeviceRow;
  locationLabel: string;
  selected: boolean;
  onSelect: (id: number) => void;
  alarmState: DeviceAlarmState;
  batteryPercent: number | null;
  dimmed: boolean;
}) {
  const isOnline = device.communicationStatus === "online";
  return (
    <button
      type="button"
      className={[
        "device-tree-row",
        selected ? "selected" : "",
        isOnline ? "is-online" : "is-offline",
        alarmState ? `has-alarm has-alarm--${alarmState}` : "",
        dimmed ? "is-dimmed" : ""
      ]
        .filter(Boolean)
        .join(" ")}
      onClick={() => onSelect(device.id)}
      title={`${device.name} (${device.code}) · ${locationLabel}`}
    >
      <span className={`device-tree-dot ${isOnline ? "online" : "offline"}`} />
      {/* Seri no satirda YAZMAZ — dar sidebar'da gurultu yapiyordu; tam
          kunye (ad, kod, konum) tooltip'te. */}
      <span className="device-tree-row-name">{device.name}</span>
      {alarmState ? (
        <TriangleAlert size={13} strokeWidth={2.4} className="device-tree-row-alarm" />
      ) : null}
      <span className="device-tree-row-batt">
        {batteryPercent !== null ? `%${Math.round(batteryPercent)}` : "—"}
      </span>
    </button>
  );
}

export function DeviceLineTree({
  devices,
  selectedId,
  onSelect,
  gridSnapshot,
  alarmStateOf,
  batteryOf,
  faultDeviceIds,
  hiddenLineIds,
  onToggleLineHidden
}: Props) {
  const { t } = useTranslation();
  // Varsayilan kapali: sadece acilan hatlarin id'si burada tutulur.
  const [openLineIds, setOpenLineIds] = useState<Set<number>>(new Set());
  const [closedRegionIds, setClosedRegionIds] = useState<Set<number>>(new Set());

  const { regions, unassigned } = useMemo(() => {
    const deviceById = new Map(devices.map((d) => [d.id, d]));
    const assigned = new Set<number>();
    const result: TreeRegion[] = [];

    if (gridSnapshot) {
      const poleById = new Map(gridSnapshot.poles.map((p) => [p.id, p]));
      const linesByRegion = new Map<number, typeof gridSnapshot.lines>();
      for (const line of gridSnapshot.lines) {
        const list = linesByRegion.get(line.region_id) ?? [];
        list.push(line);
        linesByRegion.set(line.region_id, list);
      }
      // Segmentleri hat bazinda grupla — cihaz bunlara bagli.
      const segmentsByLine = new Map<number, typeof gridSnapshot.segments>();
      for (const seg of gridSnapshot.segments) {
        const list = segmentsByLine.get(seg.line_id) ?? [];
        list.push(seg);
        segmentsByLine.set(seg.line_id, list);
      }

      const poleLabel = (poleId: number): string => {
        const pole = poleById.get(poleId);
        if (!pole) return "?";
        return pole.name?.trim() || t("dashboard.tree.poleSeq", { seq: pole.sequence_no });
      };

      const poleSeq = (poleId: number): number => poleById.get(poleId)?.sequence_no ?? 0;

      for (const region of gridSnapshot.regions) {
        const treeLines: TreeLine[] = [];
        for (const line of linesByRegion.get(region.id) ?? []) {
          // Yalnizca uzerinde GORUNUR cihaz olan segmentler; ust filtreye
          // takilan cihazin segmenti timeline'a hic girmez.
          const withDevices: { seg: (typeof gridSnapshot.segments)[number]; devices: DeviceRow[] }[] = [];
          const seenSlot = new Map<string, DeviceRow[]>();
          for (const seg of segmentsByLine.get(line.id) ?? []) {
            if (!seg.device_id) continue;
            const device = deviceById.get(seg.device_id);
            if (!device) continue;
            assigned.add(device.id);
            // Ayni iki direk arasinda birden fazla cihaz olabilir — tek
            // dugumde topla ki timeline'da ayni bosluk iki kez cizilmesin.
            const slot = `${seg.from_pole_id}-${seg.to_pole_id}`;
            const bucket = seenSlot.get(slot);
            if (bucket) {
              bucket.push(device);
              continue;
            }
            const devices = [device];
            seenSlot.set(slot, devices);
            withDevices.push({ seg, devices });
          }
          if (withDevices.length === 0) continue;
          withDevices.sort(
            (a, b) =>
              (a.seg.from_pole_seq ?? poleSeq(a.seg.from_pole_id)) -
              (b.seg.from_pole_seq ?? poleSeq(b.seg.from_pole_id))
          );

          // Timeline: direk -> (cihazlar) -> direk -> ... Ardisik olmayan
          // segmentler arasina "gap" konur (aradaki cihazsiz direkler).
          // Arizali cihazin bulundugu aralik ve onu SINIRLAYAN iki direk
          // kirmizi isaretlenir — arizanin yeri gozle okunsun.
          const timeline: TimelineItem[] = [];
          let prevToPoleId: number | null = null;
          for (const { seg, devices } of withDevices) {
            const spanFault = devices.some((d) => faultDeviceIds.has(d.id));
            if (prevToPoleId !== seg.from_pole_id) {
              if (prevToPoleId !== null) timeline.push({ kind: "gap", key: `gap-${seg.id}` });
              timeline.push({
                kind: "pole",
                key: `p-${seg.id}-from`,
                label: poleLabel(seg.from_pole_id),
                fault: spanFault
              });
            } else if (spanFault) {
              // Onceki segmentin kapanis diregi bu araligin da baslangici —
              // ariza varsa onu da kirmiziya cevir.
              const last = timeline[timeline.length - 1];
              if (last?.kind === "pole") last.fault = true;
            }
            timeline.push({ kind: "devices", key: `d-${seg.id}`, devices, fault: spanFault });
            timeline.push({
              kind: "pole",
              key: `p-${seg.id}-to`,
              label: poleLabel(seg.to_pole_id),
              fault: spanFault
            });
            prevToPoleId = seg.to_pole_id;
          }

          const allDevices = withDevices.flatMap((s) => s.devices);
          treeLines.push({
            id: line.id,
            name: line.name,
            code: line.code,
            timeline,
            deviceCount: allDevices.length,
            onlineCount: allDevices.filter((d) => d.communicationStatus === "online").length,
            alarmCount: allDevices.filter((d) => alarmStateOf(d.id) !== null).length,
            faultCount: allDevices.filter((d) => faultDeviceIds.has(d.id)).length,
            commLostCount: allDevices.filter((d) => d.communicationStatus !== "online").length
          });
        }
        if (treeLines.length === 0) continue;
        result.push({
          id: region.id,
          name: region.name,
          lines: treeLines,
          deviceCount: treeLines.reduce((sum, l) => sum + l.deviceCount, 0)
        });
      }
    }

    return { regions: result, unassigned: devices.filter((d) => !assigned.has(d.id)) };
  }, [devices, gridSnapshot, alarmStateOf, faultDeviceIds, t]);

  const toggleLine = (lineId: number) =>
    setOpenLineIds((prev) => {
      const next = new Set(prev);
      if (next.has(lineId)) next.delete(lineId);
      else next.add(lineId);
      return next;
    });

  const toggleRegion = (regionId: number) =>
    setClosedRegionIds((prev) => {
      const next = new Set(prev);
      if (next.has(regionId)) next.delete(regionId);
      else next.add(regionId);
      return next;
    });

  if (regions.length === 0 && unassigned.length === 0) {
    return <p className="device-list-empty">{t("dashboard.sidebar.noDevices")}</p>;
  }

  return (
    <div className="device-tree">
      {regions.map((region) => {
        const regionOpen = !closedRegionIds.has(region.id);
        return (
          <div key={region.id} className="device-tree-region">
            <button
              type="button"
              className="device-tree-node device-tree-node--region"
              onClick={() => toggleRegion(region.id)}
              aria-expanded={regionOpen}
            >
              {regionOpen ? (
                <ChevronDown size={15} strokeWidth={2.2} className="device-tree-caret" />
              ) : (
                <ChevronRight size={15} strokeWidth={2.2} className="device-tree-caret" />
              )}
              <MapPin size={15} strokeWidth={2} className="device-tree-icon device-tree-icon--region" />
              <span className="device-tree-label">{region.name}</span>
              <span className="device-tree-count">{region.deviceCount}</span>
            </button>

            {regionOpen
              ? region.lines.map((line) => {
                  const lineOpen = openLineIds.has(line.id);
                  const hidden = hiddenLineIds.has(line.id);
                  return (
                    <div key={line.id} className="device-tree-line">
                      <div
                        className={[
                          "device-tree-node device-tree-node--line",
                          hidden ? "is-hidden-line" : "",
                          // Gizli hatta nabiz attirmiyoruz — kullanici zaten
                          // bilerek geri plana atmis, dikkat cekmesi yanlis olur.
                          line.faultCount > 0 && !hidden ? "is-fault" : ""
                        ]
                          .filter(Boolean)
                          .join(" ")}
                      >
                        <button
                          type="button"
                          className="device-tree-node-main"
                          onClick={() => toggleLine(line.id)}
                          aria-expanded={lineOpen}
                        >
                          {lineOpen ? (
                            <ChevronDown size={15} strokeWidth={2.2} className="device-tree-caret" />
                          ) : (
                            <ChevronRight size={15} strokeWidth={2.2} className="device-tree-caret" />
                          )}
                          <Cable size={15} strokeWidth={2} className="device-tree-icon" />
                          <span className="device-tree-label" title={`${line.name} (${line.code})`}>
                            {line.name}
                          </span>
                          {line.faultCount > 0 ? (
                            <span
                              className="device-tree-badge device-tree-badge--fault"
                              title={t("dashboard.tree.faultCount", { count: line.faultCount })}
                            >
                              <TriangleAlert size={11} strokeWidth={2.6} />
                              {line.faultCount}
                            </span>
                          ) : line.alarmCount > 0 ? (
                            <span
                              className="device-tree-badge device-tree-badge--alarm"
                              title={t("dashboard.tree.alarmCount", { count: line.alarmCount })}
                            >
                              {line.alarmCount}
                            </span>
                          ) : null}
                          {line.commLostCount > 0 ? (
                            <span
                              className="device-tree-badge device-tree-badge--comm"
                              title={t("dashboard.tree.commLostCount", {
                                count: line.commLostCount
                              })}
                            >
                              <WifiOff size={11} strokeWidth={2.4} />
                              {line.commLostCount}
                            </span>
                          ) : null}
                          <span
                            className="device-tree-count"
                            title={t("dashboard.tree.onlineOfTotal", {
                              online: line.onlineCount,
                              total: line.deviceCount
                            })}
                          >
                            {line.onlineCount}/{line.deviceCount}
                          </span>
                        </button>
                        <button
                          type="button"
                          className={`device-tree-eye ${hidden ? "is-off" : ""}`}
                          onClick={() => onToggleLineHidden(line.id)}
                          title={hidden ? t("dashboard.tree.showOnMap") : t("dashboard.tree.hideOnMap")}
                          aria-label={hidden ? t("dashboard.tree.showOnMap") : t("dashboard.tree.hideOnMap")}
                          aria-pressed={hidden}
                        >
                          {hidden ? (
                            <EyeOff size={15} strokeWidth={2} />
                          ) : (
                            <Eye size={15} strokeWidth={2} />
                          )}
                        </button>
                      </div>

                      {lineOpen ? (
                        <div className="device-tree-timeline">
                          {line.timeline.map((item) => {
                            if (item.kind === "pole") {
                              return (
                                <div
                                  key={item.key}
                                  className={`device-tl-pole ${
                                    item.fault && !hidden ? "is-fault" : ""
                                  }`}
                                >
                                  <span className="device-tl-pole-node" aria-hidden="true" />
                                  <span className="device-tl-pole-label">{item.label}</span>
                                </div>
                              );
                            }
                            if (item.kind === "gap") {
                              return (
                                <div
                                  key={item.key}
                                  className="device-tl-gap"
                                  title={t("dashboard.tree.gapHint")}
                                  aria-hidden="true"
                                />
                              );
                            }
                            return (
                              <div
                                key={item.key}
                                className={`device-tl-span ${
                                  item.fault && !hidden ? "is-fault" : ""
                                }`}
                                title={
                                  item.fault ? t("dashboard.tree.faultHere") : undefined
                                }
                              >
                                {item.devices.map((device) => (
                                  <DeviceTreeRow
                                    key={device.id}
                                    device={device}
                                    locationLabel={line.name}
                                    selected={selectedId === device.id}
                                    onSelect={onSelect}
                                    alarmState={alarmStateOf(device.id)}
                                    batteryPercent={batteryOf(device)}
                                    dimmed={hidden}
                                  />
                                ))}
                              </div>
                            );
                          })}
                        </div>
                      ) : null}
                    </div>
                  );
                })
              : null}
          </div>
        );
      })}

      {unassigned.length > 0 ? (
        <div className="device-tree-region">
          <div className="device-tree-node device-tree-node--region device-tree-node--unassigned">
            <Unlink size={15} strokeWidth={2} className="device-tree-icon" />
            <span className="device-tree-label">{t("dashboard.sidebar.unassigned")}</span>
            <span className="device-tree-count">{unassigned.length}</span>
          </div>
          <div className="device-tree-children">
            {unassigned.map((device) => (
              <DeviceTreeRow
                key={device.id}
                device={device}
                locationLabel={t("dashboard.sidebar.unassigned")}
                selected={selectedId === device.id}
                onSelect={onSelect}
                alarmState={alarmStateOf(device.id)}
                batteryPercent={batteryOf(device)}
                dimmed={false}
              />
            ))}
          </div>
        </div>
      ) : null}
    </div>
  );
}
