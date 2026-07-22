/**
 * HeaderSearch — global cihaz + bolge aramasi (header ortasi).
 *
 * Kullanici cihaz adi/kodu ya da bolge adi yazar; acilir panelde iki grup
 * (Cihazlar / Bolgeler) gosterilir. Cihaz secilince detay sekmesi acilir,
 * bolge secilince ana sayfaya gecip o bolge filtrelenir (caller karar verir).
 * Klavye: cmd/ctrl+K odaklar, ArrowUp/Down gezinir, Enter secer, Esc kapatir.
 */
import { useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { Search, Router, MapPin, GitBranch } from "lucide-react";

import type { CommunicationStatus, DeviceRow, Line, Region } from "../shared/types";

// Cihaz id -> topoloji (bolge adi etiketi icin). App.tsx deviceTopologyInfo.
type DeviceTopology = Map<number, { regionId: number; regionName: string; lineId: number; lineName: string }>;

type Props = {
  devices: DeviceRow[];
  regions: Region[];
  lines: Line[];
  deviceTopology: DeviceTopology;
  onOpenDevice: (deviceId: number) => void;
  onSelectRegion: (regionId: number) => void;
  onSelectLine: (lineId: number) => void;
};

const MAX_PER_GROUP = 6;

// Duz "sonuc" listesi — klavye gezinmesi tek index uzerinden yurusun diye
// cihaz + hat + bolge tek diziye serilir (grup basliklari render'da eklenir).
type Result =
  | { kind: "device"; id: number; name: string; code: string; region: string; comm: CommunicationStatus; alarm: boolean }
  | { kind: "line"; id: number; name: string; code: string }
  | { kind: "region"; id: number; name: string };

export function HeaderSearch({ devices, regions, lines, deviceTopology, onOpenDevice, onSelectRegion, onSelectLine }: Props) {
  const { t } = useTranslation();
  const [query, setQuery] = useState("");
  const [open, setOpen] = useState(false);
  const [activeIndex, setActiveIndex] = useState(0);
  const wrapRef = useRef<HTMLDivElement | null>(null);
  const inputRef = useRef<HTMLInputElement | null>(null);

  // Dis tik -> kapat.
  useEffect(() => {
    const onDown = (e: MouseEvent) => {
      if (!wrapRef.current?.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", onDown);
    return () => document.removeEventListener("mousedown", onDown);
  }, []);

  // cmd/ctrl+K -> input focus.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        inputRef.current?.focus();
        setOpen(true);
      }
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, []);

  const { deviceResults, lineResults, regionResults, flat } = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return { deviceResults: [] as Result[], lineResults: [] as Result[], regionResults: [] as Result[], flat: [] as Result[] };
    const dev: Result[] = devices
      .filter((d) => d.name.toLowerCase().includes(q) || d.code.toLowerCase().includes(q))
      .slice(0, MAX_PER_GROUP)
      .map((d) => ({
        kind: "device",
        id: d.id,
        name: d.name,
        code: d.code,
        region: deviceTopology.get(d.id)?.regionName ?? "",
        comm: d.communicationStatus,
        alarm: d.alarmActive,
      }));
    const ln: Result[] = lines
      .filter((l) => l.name.toLowerCase().includes(q) || l.code.toLowerCase().includes(q))
      .slice(0, MAX_PER_GROUP)
      .map((l) => ({ kind: "line", id: l.id, name: l.name, code: l.code }));
    const reg: Result[] = regions
      .filter((r) => r.name.toLowerCase().includes(q))
      .slice(0, MAX_PER_GROUP)
      .map((r) => ({ kind: "region", id: r.id, name: r.name }));
    return { deviceResults: dev, lineResults: ln, regionResults: reg, flat: [...dev, ...ln, ...reg] };
  }, [query, devices, lines, regions, deviceTopology]);

  // Sorgu degisince ilk sonuca sar + aktif index sifirla.
  useEffect(() => {
    setActiveIndex(0);
    setOpen(query.trim().length > 0);
  }, [query]);

  const choose = (r: Result) => {
    if (r.kind === "device") onOpenDevice(r.id);
    else if (r.kind === "line") onSelectLine(r.id);
    else onSelectRegion(r.id);
    setQuery("");
    setOpen(false);
    inputRef.current?.blur();
  };

  const onKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === "Escape") {
      setOpen(false);
      inputRef.current?.blur();
      return;
    }
    if (!open || flat.length === 0) return;
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setActiveIndex((i) => (i + 1) % flat.length);
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setActiveIndex((i) => (i - 1 + flat.length) % flat.length);
    } else if (e.key === "Enter") {
      e.preventDefault();
      const r = flat[activeIndex];
      if (r) choose(r);
    }
  };

  const renderRow = (r: Result, flatIdx: number) => {
    const active = flatIdx === activeIndex;
    return (
      <button
        key={`${r.kind}-${r.id}`}
        type="button"
        className={`header-search-item${active ? " active" : ""}${r.kind === "device" && r.alarm ? " has-alarm" : ""}`}
        onMouseEnter={() => setActiveIndex(flatIdx)}
        onClick={() => choose(r)}
      >
        {r.kind === "device" ? (
          // Sol: haberlesme durumu noktasi (online/offline/unknown) + alarm rengi
          <span
            className={`header-search-status status-${r.comm}${r.alarm ? " has-alarm" : ""}`}
            title={r.alarm ? t("header.searchAlarmTag") : t(`common.${r.comm}`)}
          />
        ) : r.kind === "line" ? (
          <GitBranch size={16} />
        ) : (
          <MapPin size={16} />
        )}
        {r.kind === "device" ? <Router size={16} className="header-search-kind-icon" /> : null}
        <span className="header-search-item-main">{r.name}</span>
        {r.kind === "device" ? (
          <span className="header-search-item-meta">
            {r.code}
            {r.region ? ` · ${r.region}` : ""}
          </span>
        ) : r.kind === "line" ? (
          <span className="header-search-item-meta">{r.code} · {t("header.searchLineTag")}</span>
        ) : (
          <span className="header-search-item-meta">{t("header.searchRegionTag")}</span>
        )}
      </button>
    );
  };

  return (
    <div className="header-search" ref={wrapRef}>
      <span className="header-search-icon" aria-hidden="true">
        <Search size={17} />
      </span>
      <input
        ref={inputRef}
        className="header-search-input"
        placeholder={t("header.searchPlaceholder")}
        value={query}
        onChange={(e) => setQuery(e.target.value)}
        onFocus={() => query.trim() && setOpen(true)}
        onKeyDown={onKeyDown}
      />
      <span className="header-search-kbd" aria-hidden="true">⌘K</span>

      {open ? (
        <div className="header-search-panel">
          {flat.length === 0 ? (
            <div className="header-search-empty">{t("header.searchEmpty")}</div>
          ) : (
            <>
              {deviceResults.length > 0 ? (
                <div className="header-search-group">
                  <div className="header-search-group-title">{t("header.searchDevices")}</div>
                  {deviceResults.map((r, i) => renderRow(r, i))}
                </div>
              ) : null}
              {lineResults.length > 0 ? (
                <div className="header-search-group">
                  <div className="header-search-group-title">{t("header.searchLines")}</div>
                  {lineResults.map((r, i) => renderRow(r, deviceResults.length + i))}
                </div>
              ) : null}
              {regionResults.length > 0 ? (
                <div className="header-search-group">
                  <div className="header-search-group-title">{t("header.searchRegions")}</div>
                  {regionResults.map((r, i) => renderRow(r, deviceResults.length + lineResults.length + i))}
                </div>
              ) : null}
            </>
          )}
        </div>
      ) : null}
    </div>
  );
}
