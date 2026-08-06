import { useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import type {
  DeviceModelOption,
  SignalCatalogRow,
  SignalDataType,
  SignalHistorianBulkPayload,
  SignalHistorianBulkResult,
  SignalSource,
  UserRole
} from "../../shared/types";
import { SignalEditModal } from "./SignalEditModal";
import {
  DATA_TYPES,
  DATA_TYPE_LABEL,
  DATA_TYPE_SHORT,
  IEC104_MONITOR_TYPES,
  SOURCE_LABEL,
  SOURCES,
  deadbandApplies,
  effectiveDeadband,
  isFaultSignal,
  isHistorized
} from "./signalCatalogConstants";

type Props = {
  role: UserRole;
  signals: SignalCatalogRow[];
  deviceModels: DeviceModelOption[];
  loading: boolean;
  error?: string;
  onUpdate: (signalKey: string, payload: Partial<Omit<SignalCatalogRow, "id" | "key">>) => Promise<void>;
  /** Toplu arsiv ayari. Tekil duzenleme de buradan gecer: tek kod yolu =
   *  tek kural yolu (ariza korumasi hem burada hem sunucuda ayni). */
  onBulkHistorian: (payload: SignalHistorianBulkPayload) => Promise<SignalHistorianBulkResult>;
};

type SourceFilter = "all" | SignalSource;
type TabKey = "all" | SignalDataType;

/** Onay bekleyen arsiv degisikligi. `faultKeys` bos degilse once uyari. */
type PendingHistorian = {
  keys: string[];
  faultKeys: string[];
  historize?: boolean;
  deadband?: number;
};

export function SignalsPage({
  role,
  signals,
  deviceModels,
  loading,
  error,
  onUpdate,
  onBulkHistorian
}: Props) {
  const { t } = useTranslation();
  const canEdit = role === "installer";
  const dataTypeLabel = (type: SignalDataType): string =>
    t(`engineering.liveValues.dataType.${type}`, { defaultValue: DATA_TYPE_LABEL[type] });
  const [activeTab, setActiveTab] = useState<TabKey>("all");
  const [selectedKey, setSelectedKey] = useState<string>("");
  const [sourceFilter, setSourceFilter] = useState<SourceFilter>("all");
  // Arsiv yonetimi ayri bir popup'ta: ozet + filtre + toplu islemler orada.
  // Sayfa duz kalir; kontroller goz onunde durmaz.
  const [arsivPanelAcik, setArsivPanelAcik] = useState(false);
  const [searchTerm, setSearchTerm] = useState("");
  const [editModalSignal, setEditModalSignal] = useState<SignalCatalogRow | null>(null);
  const [iec104Expanded, setIec104Expanded] = useState(false);
  const [modbusExpanded, setModbusExpanded] = useState(false);
  const [exportMenuOpen, setExportMenuOpen] = useState(false);
  const [bulkDeadband, setBulkDeadband] = useState("");
  const [detailDeadband, setDetailDeadband] = useState("");
  const [pending, setPending] = useState<PendingHistorian | null>(null);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState("");
  const [modelFilter, setModelFilter] = useState<string>(
    () => deviceModels[0]?.code ?? "horstmann_sn_2_0"
  );

  useEffect(() => {
    if (deviceModels.length === 0) return;
    if (!deviceModels.some((m) => m.code === modelFilter)) {
      setModelFilter(deviceModels[0].code);
    }
  }, [deviceModels, modelFilter]);

  const signalsForModel = useMemo(
    () => signals.filter((s) => s.model === modelFilter),
    [signals, modelFilter]
  );

  const countsByType = useMemo(() => {
    const map = new Map<SignalDataType, number>();
    DATA_TYPES.forEach((tp) => map.set(tp, 0));
    for (const sig of signalsForModel) {
      map.set(sig.data_type, (map.get(sig.data_type) ?? 0) + 1);
    }
    return map;
  }, [signalsForModel]);

  const filteredSignals = useMemo(() => {
    const q = searchTerm.trim().toLowerCase();
    return signalsForModel.filter((signal) => {
      if (activeTab !== "all" && signal.data_type !== activeTab) return false;
      if (sourceFilter !== "all" && signal.source !== sourceFilter) return false;
      if (!q) return true;
      return (
        signal.label.toLowerCase().includes(q) ||
        signal.key.toLowerCase().includes(q) ||
        (signal.description ?? "").toLowerCase().includes(q)
      );
    });
  }, [signalsForModel, activeTab, sourceFilter, searchTerm]);

  const selected = useMemo(
    () => signalsForModel.find((signal) => signal.key === selectedKey) ?? null,
    [signalsForModel, selectedKey]
  );

  // Secim degisince olu bant kutusunu o sinyalin degeriyle tazele.
  useEffect(() => {
    setDetailDeadband(selected ? String(selected.historize_deadband ?? 0) : "");
  }, [selected]);

  const totalCount = signalsForModel.length;
  const visibleCount = filteredSignals.length;

  // --- GORUNUR ETKI ---------------------------------------------------------
  // Kullanici kararinin sonucunu GORMEDEN karar veremez. Arsivlenen sinyal
  // sayisi, yazma yukunun dogrudan carpanidir: 600 cihazda her sinyal ayri
  // bir satir akisidir. Olu bandin etkisi statik olarak KESTIRILEMEZ (sinyalin
  // ne kadar oynadigina bagli), o yuzden ayri sayilir — tek bir "tahmini
  // satir/sn" rakami uydurmak, guvenilir gorunen bir yalan olurdu.
  const historianOzet = useMemo(() => {
    let archived = 0;
    let withDeadband = 0;
    for (const s of signalsForModel) {
      if (isHistorized(s)) {
        archived += 1;
        if (effectiveDeadband(s) > 0) withDeadband += 1;
      }
    }
    const pct = totalCount === 0 ? 0 : Math.round((archived / totalCount) * 100);
    return { archived, withDeadband, pct };
  }, [signalsForModel, totalCount]);

  // Filtredeki secim — toplu islem tam olarak buna uygulanir.
  const filteredFaultKeys = useMemo(
    () => filteredSignals.filter((s) => isFaultSignal(s.data_type)).map((s) => s.key),
    [filteredSignals]
  );
  const filteredAnalogCount = useMemo(
    () => filteredSignals.filter((s) => deadbandApplies(s.data_type)).length,
    [filteredSignals]
  );

  const downloadCsv = (filename: string, headers: string[], rows: (string | number)[][]) => {
    const escape = (v: string | number) => `"${String(v).replace(/"/g, '""')}"`;
    const csv = [headers, ...rows].map((row) => row.map(escape).join(",")).join("\r\n");
    const blob = new Blob(["﻿" + csv], { type: "text/csv;charset=utf-8;" });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = filename;
    link.click();
    URL.revokeObjectURL(url);
  };

  const exportIec104 = () => {
    const rows = signalsForModel
      .filter((s) => s.iec104_enabled !== false && s.iec104_type_id !== null && s.iec104_type_id !== undefined)
      .map((s) => [
        s.label,
        s.key,
        s.iec104_type_id ?? "",
        IEC104_MONITOR_TYPES.find((m) => m.id === s.iec104_type_id)?.code ?? "",
        s.iec104_ioa ?? "",
        SOURCE_LABEL[s.source]
      ]);
    downloadCsv(`iec104_${modelFilter}.csv`, ["Etiket", "Key", "Type ID", "ASDU", "IOA", "Kaynak"], rows);
    setExportMenuOpen(false);
  };

  const exportModbus = () => {
    const rows = signalsForModel
      .filter(
        (s) =>
          s.modbus_function !== null &&
          s.modbus_function !== undefined &&
          s.modbus_address !== null &&
          s.modbus_address !== undefined
      )
      .map((s) => [s.label, s.key, s.modbus_function ?? "", s.modbus_address ?? "", SOURCE_LABEL[s.source]]);
    downloadCsv(`modbus_${modelFilter}.csv`, ["Etiket", "Key", "Function Code", "Adres", "Kaynak"], rows);
    setExportMenuOpen(false);
  };

  // --- Arsiv ayari uygulama -------------------------------------------------
  // Tekil ve toplu ayni fonksiyondan gecer. Ariza sinyali varsa ve arsiv
  // KAPATILIYORSA once onay istenir (engelleme degil — gerekce: `binary_output`
  // komut noktalarinin arsivi zaten kapalidir ve sert engel sahadaki mevcut
  // durumu yonetilemez kilardi). Onay sunucuya da gider; kural orada da var.
  const uygula = async (istek: PendingHistorian) => {
    if (istek.keys.length === 0) return;
    setBusy(true);
    setNotice("");
    try {
      const sonuc = await onBulkHistorian({
        signal_keys: istek.keys,
        ...(istek.historize === undefined ? {} : { historize: istek.historize }),
        ...(istek.deadband === undefined ? {} : { historize_deadband: istek.deadband }),
        confirm_fault_signals: istek.faultKeys.length > 0
      });
      const parcalar = [
        sonuc.updated > 0
          ? t("engineering.signals.historian.applied", { count: sonuc.updated })
          : t("engineering.signals.historian.noChange")
      ];
      if (sonuc.skipped_deadband.length > 0) {
        parcalar.push(
          t("engineering.signals.historian.skipped", { count: sonuc.skipped_deadband.length })
        );
      }
      setNotice(parcalar.join(" "));
    } catch (err) {
      setNotice(err instanceof Error ? err.message : t("engineering.signals.form.saveFail"));
    } finally {
      setBusy(false);
      setPending(null);
    }
  };

  /** Onay gerekiyorsa uyari penceresini acar, gerekmiyorsa dogrudan uygular. */
  const istekBaslat = (istek: PendingHistorian) => {
    if (istek.keys.length === 0) return;
    if (istek.faultKeys.length > 0) {
      setPending(istek);
      return;
    }
    void uygula(istek);
  };

  const topluArsiv = (historize: boolean) => {
    const keys = filteredSignals.map((s) => s.key);
    istekBaslat({
      keys,
      // Arsivi ACMAK her zaman guvenli yon; onay yalnizca KAPATIRKEN.
      faultKeys: historize ? [] : filteredFaultKeys,
      historize
    });
  };

  const topluOluBant = () => {
    const deger = Number(bulkDeadband.trim());
    if (!Number.isFinite(deger) || deger < 0) return;
    istekBaslat({
      keys: filteredSignals.map((s) => s.key),
      faultKeys: [],
      deadband: deger
    });
  };

  const tekilArsiv = (signal: SignalCatalogRow, historize: boolean) => {
    istekBaslat({
      keys: [signal.key],
      faultKeys: historize || !isFaultSignal(signal.data_type) ? [] : [signal.key],
      historize
    });
  };

  const tekilOluBant = (signal: SignalCatalogRow) => {
    const deger = Number(detailDeadband.trim());
    if (!Number.isFinite(deger) || deger < 0) return;
    istekBaslat({ keys: [signal.key], faultKeys: [], deadband: deger });
  };

  const pendingFaultLabels = useMemo(() => {
    if (!pending) return [];
    const byKey = new Map(signalsForModel.map((s) => [s.key, s]));
    return pending.faultKeys.map((k) => byKey.get(k)?.label ?? k);
  }, [pending, signalsForModel]);

  return (
    <section className="tab-panel signals-panel signals-panel-modern">
      {/* Tek satirlik filtre cubugu: solda arama, saginda model/kaynak/tip
          filtreleri ve export. Eskiden iki ayri satirdi (header-row +
          toolbar) ve gereksiz dikey yer kapliyordu. */}
      <div className="signals-toolbar signals-toolbar--merged">
        <input
          className="signals-search"
          type="search"
          placeholder={t("engineering.signals.search")}
          value={searchTerm}
          onChange={(event) => setSearchTerm(event.target.value)}
        />
        <span className="signals-count-pill">
          {visibleCount} / {totalCount}
        </span>
        {!canEdit ? (
          <span className="helper-text signals-toolbar-readonly">{t("engineering.signals.readOnlyHint")}</span>
        ) : null}
        <div
          className="signals-export-wrap"
          onBlur={(event) => {
            const nextFocus = event.relatedTarget;
            if (!(nextFocus instanceof Node) || !event.currentTarget.contains(nextFocus)) {
              setExportMenuOpen(false);
            }
          }}
        >
          <button
            type="button"
            className="secondary-btn signals-export-btn"
            onClick={() => setExportMenuOpen((v) => !v)}
          >
            <span className="material-symbols-outlined" aria-hidden="true">download</span>
            {t("engineering.signals.export.button")}
          </button>
          {exportMenuOpen ? (
            <div className="signals-export-menu">
              <button type="button" onClick={exportIec104}>
                {t("engineering.signals.export.iec104")}
              </button>
              <button type="button" onClick={exportModbus}>
                {t("engineering.signals.export.modbus")}
              </button>
            </div>
          ) : null}
        </div>
      </div>

      {/* 2. satir: cihaz turu (model) + kaynak + veri tipi filtreleri birlikte. */}
      <div className="signals-filters-row">
        <label className="signals-model-label">
          <select
            className="signals-model-select"
            value={modelFilter}
            onChange={(event) => {
              setModelFilter(event.target.value);
              setSelectedKey("");
            }}
          >
            {deviceModels.length === 0 ? (
              <option value={modelFilter}>{modelFilter}</option>
            ) : (
              deviceModels.map((opt) => (
                <option key={opt.code} value={opt.code}>
                  {opt.label}
                </option>
              ))
            )}
          </select>
        </label>
        <div className="signals-source-chips">
          <button
            type="button"
            className={`chip ${sourceFilter === "all" ? "chip-active" : ""}`}
            onClick={() => setSourceFilter("all")}
          >
            {t("engineering.signals.allSources")}
          </button>
          {SOURCES.map((src) => (
            <button
              key={src}
              type="button"
              className={`chip chip-source-${src} ${sourceFilter === src ? "chip-active" : ""}`}
              onClick={() => setSourceFilter(src)}
            >
              {SOURCE_LABEL[src]}
            </button>
          ))}
        </div>
        <div className="signals-type-tabs signals-type-tabs--row">
          <button
            className={`signals-type-tab ${activeTab === "all" ? "active" : ""}`}
            onClick={() => setActiveTab("all")}
          >
            <span className="stt-label">{t("engineering.signals.all")}</span>
            <span className="stt-count">{totalCount}</span>
          </button>
          {DATA_TYPES.map((type) => (
            <button
              key={type}
              className={`signals-type-tab stt-${type} ${activeTab === type ? "active" : ""}`}
              onClick={() => setActiveTab(type)}
            >
              <span className="stt-label">{dataTypeLabel(type)}</span>
              <span className="stt-count">{countsByType.get(type) ?? 0}</span>
            </button>
          ))}
        </div>
        {/* ARSIV — kontroller dugmenin actigi popup'ta; dugme filtrelerin
            sagina yaslanir, ayri bir satir KAPLAMAZ. */}
        <div className="signals-arsiv-sag">
          <button
            type="button"
            className="secondary-btn signals-arsiv-ac-btn"
            onClick={() => setArsivPanelAcik(true)}
          >
            <span className="material-symbols-outlined" aria-hidden="true">database</span>
            {t("engineering.signals.historian.manage")}
          </button>
        </div>
      </div>

      {notice ? <p className="helper-text shb-notice">{notice}</p> : null}

      {arsivPanelAcik ? (
        <div className="settings-modal-backdrop" onClick={() => setArsivPanelAcik(false)}>
          <div
            className="settings-modal signals-arsiv-modal"
            role="dialog"
            aria-modal="true"
            onClick={(event) => event.stopPropagation()}
          >
            <header className="arsiv-modal-head">
              <span className="arsiv-modal-ikon material-symbols-outlined" aria-hidden="true">
                database
              </span>
              <div className="arsiv-modal-baslik">
                <h3>{t("engineering.signals.historian.manage")}</h3>
                <p>{t("engineering.signals.historian.manageSub")}</p>
              </div>
              <button
                type="button"
                className="gw-wizard-close"
                onClick={() => setArsivPanelAcik(false)}
                aria-label={t("common.close")}
              >
                ×
              </button>
            </header>

            <div className="arsiv-stat">
              <div className="arsiv-stat-sayi">
                <strong>{historianOzet.archived}</strong>
                <span>/ {totalCount}</span>
              </div>
              <div className="arsiv-stat-govde">
                <div className="arsiv-stat-etiket">
                  {/* Buyuk sayinin yanina ayni cumleyi bir daha yazmiyoruz;
                      kisa etiket yeter. */}
                  <span>{t("engineering.signals.historian.statLabel")}</span>
                </div>
                <div className="arsiv-metre" aria-hidden="true">
                  <div className="arsiv-metre-dolgu" style={{ width: `${historianOzet.pct}%` }} />
                </div>
                {historianOzet.withDeadband > 0 ? (
                  <span className="arsiv-stat-alt">
                    {t("engineering.signals.historian.summaryDeadband", {
                      count: historianOzet.withDeadband
                    })}
                  </span>
                ) : null}
              </div>
            </div>

            {canEdit ? (
              <section className="arsiv-bolum">
                <h4 className="arsiv-bolum-baslik">
                  {t("engineering.signals.historian.sectionBulk")}
                  <span className="arsiv-bolum-kapsam">
                    {t("engineering.signals.historian.bulkScope", { count: visibleCount })}
                  </span>
                </h4>
                <div className="arsiv-toplu">
                  <div className="arsiv-toplu-butonlar">
                    <button
                      type="button"
                      className="secondary-btn shb-bulk-btn"
                      disabled={busy || visibleCount === 0}
                      onClick={() => topluArsiv(true)}
                    >
                      <span className="material-symbols-outlined" aria-hidden="true">save</span>
                      {t("engineering.signals.historian.bulkOn")}
                    </button>
                    <button
                      type="button"
                      className="secondary-btn shb-bulk-btn shb-bulk-btn--off"
                      disabled={busy || visibleCount === 0}
                      onClick={() => topluArsiv(false)}
                    >
                      <span className="material-symbols-outlined" aria-hidden="true">block</span>
                      {t("engineering.signals.historian.bulkOff")}
                    </button>
                  </div>
                  <div className="arsiv-deadband-grup">
                    <input
                      type="number"
                      min={0}
                      step="0.01"
                      className="shb-deadband-input"
                      value={bulkDeadband}
                      onChange={(event) => setBulkDeadband(event.target.value)}
                      placeholder={t("engineering.signals.historian.bulkDeadbandPlaceholder")}
                      disabled={busy || filteredAnalogCount === 0}
                      title={
                        filteredAnalogCount === 0
                          ? t("engineering.signals.historian.noAnalogInFilter")
                          : t("engineering.signals.historian.bulkAnalogOnly", {
                              count: filteredAnalogCount
                            })
                      }
                    />
                    <button
                      type="button"
                      className="secondary-btn shb-bulk-btn"
                      disabled={busy || filteredAnalogCount === 0 || bulkDeadband.trim() === ""}
                      onClick={topluOluBant}
                    >
                      {t("engineering.signals.historian.bulkDeadbandApply")}
                    </button>
                  </div>
                </div>
              </section>
            ) : null}

            {notice ? <p className="helper-text shb-notice">{notice}</p> : null}
            {canEdit ? (
              <p className="arsiv-ipucu">
                <span className="material-symbols-outlined" aria-hidden="true">info</span>
                {t("engineering.signals.historian.effectDelay")}
              </p>
            ) : null}
          </div>
        </div>
      ) : null}

      <div className="signals-main-layout">
        <div className="signals-list-column">
          {loading ? <p className="helper-text">{t("common.loading")}</p> : null}
          <div className="signals-list-wrap">
            <table className="signals-list-table">
              <thead>
                <tr>
                  <th className="col-source-first">Cihaz</th>
                  <th className="col-type">Veri Tipi</th>
                  <th className="col-addr">Grup/Indeks</th>
                  <th className="col-label">Açıklama</th>
                  <th className="col-archive">{t("engineering.signals.historian.colArchive")}</th>
                  <th className="col-deadband">{t("engineering.signals.historian.colDeadband")}</th>
                  <th className={`col-proto ${iec104Expanded ? "col-proto--expanded" : ""}`}>
                    <span className="col-proto-head">
                      IEC104
                      <button
                        type="button"
                        className="col-proto-toggle"
                        title={iec104Expanded ? "Daralt" : "Genişlet"}
                        onClick={() => setIec104Expanded((v) => !v)}
                      >
                        <span className="material-symbols-outlined" aria-hidden="true">
                          {iec104Expanded ? "chevron_right" : "chevron_left"}
                        </span>
                      </button>
                    </span>
                  </th>
                  <th className={`col-proto ${modbusExpanded ? "col-proto--expanded" : ""}`}>
                    <span className="col-proto-head">
                      Modbus TCP
                      <button
                        type="button"
                        className="col-proto-toggle"
                        title={modbusExpanded ? "Daralt" : "Genişlet"}
                        onClick={() => setModbusExpanded((v) => !v)}
                      >
                        <span className="material-symbols-outlined" aria-hidden="true">
                          {modbusExpanded ? "chevron_right" : "chevron_left"}
                        </span>
                      </button>
                    </span>
                  </th>
                </tr>
              </thead>
              <tbody>
                {filteredSignals.map((signal) => {
                  const isActive = selectedKey === signal.key;
                  const iec104On =
                    signal.iec104_enabled !== false &&
                    signal.iec104_type_id !== null &&
                    signal.iec104_type_id !== undefined;
                  const iec104Off =
                    signal.iec104_type_id !== null &&
                    signal.iec104_type_id !== undefined &&
                    signal.iec104_enabled === false;
                  const modbusOn =
                    signal.modbus_function !== null &&
                    signal.modbus_function !== undefined &&
                    signal.modbus_address !== null &&
                    signal.modbus_address !== undefined;
                  const arsivli = isHistorized(signal);
                  const olculuBant = effectiveDeadband(signal);
                  return (
                    <tr
                      key={signal.key}
                      className={`signal-row ${isActive ? "signal-row-active" : ""} ${
                        signal.is_active ? "" : "signal-row-inactive"
                      }`}
                      onClick={() => setSelectedKey(signal.key)}
                    >
                      <td className="col-source-first">
                        <span className={`badge badge-source badge-source-${signal.source}`}>
                          {SOURCE_LABEL[signal.source]}
                        </span>
                      </td>
                      <td className="col-type">
                        <span className={`badge badge-${signal.data_type}`}>{DATA_TYPE_SHORT[signal.data_type]}</span>
                      </td>
                      <td className="col-addr">
                        <span className="mono">
                          G{signal.dnp3_object_group} · i{signal.dnp3_index}
                        </span>
                      </td>
                      <td className="col-label">
                        <span className="cell-strong">{signal.label}</span>
                        {!signal.is_active ? (
                          <span className="cell-inactive-hint">{t("engineering.signals.inactive")}</span>
                        ) : null}
                      </td>
                      <td className="col-archive">
                        {/* Kapali durum icin ekstra uyari ikonu YOK (kullanici
                            istegi, 2026-08-06): 133 kapali sinyalde uyari
                            seli anlamini yitiriyordu. Ariza korumasi kapatma
                            ANINDA onay penceresi olarak devam ediyor. */}
                        <span
                          className={`archive-chip ${arsivli ? "archive-chip--on" : "archive-chip--off"}`}
                          title={
                            arsivli
                              ? t("engineering.signals.historian.onTitle")
                              : t("engineering.signals.historian.offTitle")
                          }
                        >
                          <span className="material-symbols-outlined" aria-hidden="true">
                            {arsivli ? "database" : "block"}
                          </span>
                          {arsivli
                            ? t("engineering.signals.historian.on")
                            : t("engineering.signals.historian.off")}
                        </span>
                      </td>
                      <td className="col-deadband">
                        {!deadbandApplies(signal.data_type) ? (
                          // Olmayan bir ayari varmis gibi gostermiyoruz.
                          <span
                            className="deadband-cell deadband-cell--na"
                            title={t("engineering.signals.historian.deadbandNaTitle")}
                          >
                            —
                          </span>
                        ) : olculuBant > 0 ? (
                          <span className="deadband-cell mono">
                            ±{olculuBant}
                            {signal.unit ? <span className="deadband-unit">{signal.unit}</span> : null}
                          </span>
                        ) : (
                          <span className="deadband-cell deadband-cell--zero">
                            {t("engineering.signals.historian.deadbandOff")}
                          </span>
                        )}
                      </td>
                      <td className={`col-proto ${iec104Expanded ? "col-proto--expanded" : ""}`}>
                        {iec104On ? (
                          <span className="proto-check proto-check--on" title={t("engineering.signals.iec104OnTitle")}>
                            <span className="material-symbols-outlined" aria-hidden="true">check</span>
                            {iec104Expanded ? (
                              <span className="proto-check-code">
                                {IEC104_MONITOR_TYPES.find((m) => m.id === signal.iec104_type_id)?.code ?? "—"}
                              </span>
                            ) : null}
                            {signal.iec104_ioa ?? "—"}
                          </span>
                        ) : iec104Off ? (
                          <span className="proto-check proto-check--off" title={t("engineering.signals.iec104OffTitle")}>
                            <span className="material-symbols-outlined" aria-hidden="true">close</span>
                          </span>
                        ) : (
                          <span className="proto-check proto-check--empty">—</span>
                        )}
                      </td>
                      <td className={`col-proto ${modbusExpanded ? "col-proto--expanded" : ""}`}>
                        {modbusOn ? (
                          <span className="proto-check proto-check--on">
                            <span className="material-symbols-outlined" aria-hidden="true">check</span>
                            {modbusExpanded ? (
                              <span className="proto-check-code">FC{signal.modbus_function}</span>
                            ) : null}
                            {signal.modbus_address}
                          </span>
                        ) : (
                          <span className="proto-check proto-check--empty">—</span>
                        )}
                      </td>
                    </tr>
                  );
                })}
                {filteredSignals.length === 0 && !loading ? (
                  <tr>
                    <td className="signals-empty-cell" colSpan={8}>
                      {totalCount === 0 ? t("engineering.signals.noSignals") : t("engineering.signals.noResults")}
                    </td>
                  </tr>
                ) : null}
              </tbody>
            </table>
          </div>
        </div>

        <aside className="signals-detail-column">
          <div className="signals-detail-card">
            <div className="signals-detail-head">
              <div className="signals-detail-title">
                <h4>{selected ? selected.label : t("engineering.signals.selectHint")}</h4>
              </div>
              {selected ? (
                <div className="signals-detail-badges">
                  <span className={`badge badge-source badge-source-${selected.source}`}>
                    {SOURCE_LABEL[selected.source]}
                  </span>
                  <span className={`badge badge-${selected.data_type}`}>{DATA_TYPE_SHORT[selected.data_type]}</span>
                  {!selected.is_active ? (
                    <span className="cell-inactive-hint">{t("engineering.signals.inactive")}</span>
                  ) : null}
                </div>
              ) : null}
            </div>

            {!selected ? (
              <p className="helper-text signals-detail-empty">{t("engineering.signals.selectListHint")}</p>
            ) : (
              <div className="signals-detail-form-scroll">
                <dl className="signals-detail-readonly">
                  <div className="signals-detail-readonly-row">
                    <dt>{t("engineering.signals.labelDescription")}</dt>
                    <dd>{selected.description || "—"}</dd>
                  </div>
                  <div className="signals-detail-readonly-row">
                    <dt>{t("engineering.signals.labelDataType")}</dt>
                    <dd>{dataTypeLabel(selected.data_type)}</dd>
                  </div>
                  <div className="signals-detail-readonly-row">
                    <dt>Grup/Indeks</dt>
                    <dd>
                      G{selected.dnp3_object_group} · i{selected.dnp3_index}
                    </dd>
                  </div>
                  <div className="signals-detail-readonly-row">
                    <dt>{t("engineering.signals.labelSource")}</dt>
                    <dd>{SOURCE_LABEL[selected.source]}</dd>
                  </div>
                  <div className="signals-detail-readonly-row">
                    <dt>{t("engineering.signals.labelScale")}</dt>
                    <dd>
                      {selected.scale} / {selected.offset}
                    </dd>
                  </div>
                  <div className="signals-detail-readonly-row">
                    <dt>{t("engineering.signals.labelDnp3Class")}</dt>
                    <dd>{selected.dnp3_class}</dd>
                  </div>
                  {selected.unit ? (
                    <div className="signals-detail-readonly-row">
                      <dt>{t("engineering.signals.labelUnit")}</dt>
                      <dd>{selected.unit}</dd>
                    </div>
                  ) : null}
                  <div className="signals-detail-readonly-row">
                    <dt>IEC 104 Type ID</dt>
                    <dd>
                      {selected.iec104_enabled !== false &&
                      selected.iec104_type_id !== null &&
                      selected.iec104_type_id !== undefined
                        ? selected.iec104_type_id
                        : "—"}
                    </dd>
                  </div>
                  <div className="signals-detail-readonly-row">
                    <dt>IEC 104 IOA</dt>
                    <dd>
                      {selected.iec104_enabled !== false &&
                      selected.iec104_ioa !== null &&
                      selected.iec104_ioa !== undefined
                        ? selected.iec104_ioa
                        : "—"}
                    </dd>
                  </div>
                </dl>

                {/* ARSIV — burada DUZENLENEBILIR. Sinyalin arsive yazilip
                    yazilmayacagi ve olu bandi, sayfanin geri kalanindan
                    farkli olarak dogrudan diske yazilan satiri belirler. */}
                <div className="signals-historian-panel">
                  <h5 className="shp-title">
                    <span className="material-symbols-outlined" aria-hidden="true">database</span>
                    {t("engineering.signals.historian.title")}
                  </h5>

                  <div className="shp-row">
                    <span className="shp-label">{t("engineering.signals.historian.archiveLabel")}</span>
                    {canEdit ? (
                      <div className="shp-toggle-group" role="group">
                        <button
                          type="button"
                          className={`shp-toggle ${isHistorized(selected) ? "shp-toggle--on" : ""}`}
                          disabled={busy || isHistorized(selected)}
                          onClick={() => tekilArsiv(selected, true)}
                        >
                          {t("engineering.signals.historian.on")}
                        </button>
                        <button
                          type="button"
                          className={`shp-toggle ${!isHistorized(selected) ? "shp-toggle--off" : ""}`}
                          disabled={busy || !isHistorized(selected)}
                          onClick={() => tekilArsiv(selected, false)}
                        >
                          {t("engineering.signals.historian.off")}
                        </button>
                      </div>
                    ) : (
                      <span className="shp-value">
                        {isHistorized(selected)
                          ? t("engineering.signals.historian.on")
                          : t("engineering.signals.historian.off")}
                      </span>
                    )}
                  </div>

                  {isFaultSignal(selected.data_type) ? (
                    <p className="shp-fault-hint">
                      <span className="material-symbols-outlined" aria-hidden="true">warning</span>
                      {t("engineering.signals.historian.faultHint")}
                    </p>
                  ) : null}

                  <div className="shp-row">
                    <span className="shp-label">{t("engineering.signals.historian.colDeadband")}</span>
                    {!deadbandApplies(selected.data_type) ? (
                      // Duzenlenebilir OLMAMALI: motor bu tipte esigi yok sayar.
                      <span className="shp-value shp-value--na">
                        {t("engineering.signals.historian.notApplicable")}
                      </span>
                    ) : canEdit ? (
                      <span className="shp-deadband-edit">
                        <input
                          type="number"
                          min={0}
                          step="0.01"
                          value={detailDeadband}
                          onChange={(event) => setDetailDeadband(event.target.value)}
                          disabled={busy}
                        />
                        {selected.unit ? <span className="shp-unit">{selected.unit}</span> : null}
                        <button
                          type="button"
                          className="secondary-btn shp-apply"
                          disabled={
                            busy ||
                            detailDeadband.trim() === "" ||
                            Number(detailDeadband) === (selected.historize_deadband ?? 0)
                          }
                          onClick={() => tekilOluBant(selected)}
                        >
                          {t("engineering.signals.historian.apply")}
                        </button>
                      </span>
                    ) : (
                      <span className="shp-value mono">{selected.historize_deadband ?? 0}</span>
                    )}
                  </div>
                  <p className="helper-text shp-hint">
                    {deadbandApplies(selected.data_type)
                      ? t("engineering.signals.historian.deadbandHint")
                      : t("engineering.signals.historian.deadbandNaTitle")}
                  </p>
                </div>

                {canEdit ? (
                  <div className="signal-form-actions">
                    <button type="button" className="primary-btn" onClick={() => setEditModalSignal(selected)}>
                      {t("engineering.signals.editProperties")}
                    </button>
                  </div>
                ) : null}
              </div>
            )}
          </div>
        </aside>
      </div>

      {error ? <p className="error-text">{error}</p> : null}

      {editModalSignal ? (
        <SignalEditModal signal={editModalSignal} onSave={onUpdate} onClose={() => setEditModalSignal(null)} />
      ) : null}

      {/* ARIZA KORUMASI — engelleme degil ONAY.
          Gerekce: `binary_output` komut noktalarinin arsivi migration 0032'den
          beri KAPALI; sert engel sahadaki mevcut durumu yonetilemez kilardi ve
          yanlislikla acilan bir noktayi geri kapatmayi imkansizlastirirdi.
          Ayni kural sunucuda da var — bu pencere atlansa bile istek reddedilir. */}
      {pending ? (
        <div className="settings-modal-backdrop" onClick={() => setPending(null)}>
          <div
            className="settings-modal historian-warn-modal"
            role="alertdialog"
            aria-modal="true"
            onClick={(event) => event.stopPropagation()}
          >
            <h3 className="hwm-title">
              <span className="material-symbols-outlined" aria-hidden="true">warning</span>
              {t("engineering.signals.historian.faultWarnTitle")}
            </h3>
            <p className="hwm-body">{t("engineering.signals.historian.faultWarnBody")}</p>
            <p className="hwm-count">
              {t("engineering.signals.historian.faultWarnCount", { count: pending.faultKeys.length })}
            </p>
            <ul className="hwm-list">
              {pendingFaultLabels.slice(0, 12).map((label, i) => (
                <li key={pending.faultKeys[i]}>{label}</li>
              ))}
              {pendingFaultLabels.length > 12 ? (
                <li className="hwm-more">
                  {t("engineering.signals.historian.faultWarnMore", {
                    count: pendingFaultLabels.length - 12
                  })}
                </li>
              ) : null}
            </ul>
            <div className="signal-form-actions hwm-actions">
              <button type="button" className="secondary-btn" onClick={() => setPending(null)} disabled={busy}>
                {t("engineering.signals.historian.cancel")}
              </button>
              <button
                type="button"
                className="primary-btn hwm-confirm"
                disabled={busy}
                onClick={() => void uygula(pending)}
              >
                {t("engineering.signals.historian.faultWarnConfirm")}
              </button>
            </div>
          </div>
        </div>
      ) : null}
    </section>
  );
}
