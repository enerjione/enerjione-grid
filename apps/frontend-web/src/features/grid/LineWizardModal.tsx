/**
 * LineWizardModal — Hat Sihirbazı.
 *
 * Adım 0: yol seçimi — Excel ile toplu / Soru-cevap ile tek hat.
 * Excel yolu: şablon (mevcut veri dolu, ağaç görünüm; Hizli_Yapistir sayfasıyla
 * toplu koordinat) indir → doldur → yükle → önizleme (dry-run) → onay (commit).
 * Soru-cevap yolu: bölge → hat → koordinat listesi (tek kutuya yapıştır) →
 * özet → oluştur. Segmentler otomatik; hat mevcutsa direkler SONUNA eklenir.
 */
import { useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { CircleMarker, MapContainer, Polyline, useMap } from "react-leaflet";
import type { LatLngBoundsExpression } from "leaflet";

import { ResilientTileLayer } from "../../components/ResilientTileLayer";
import { MAP_LAYERS } from "../../shared/mapTiles";

import {
  commitGridImport,
  createGridWizardLine,
  downloadGridImportTemplate,
  fetchGridSnapshot,
  fetchRegions,
  previewGridImport,
  type GridImportPreview,
  type GridSnapshot,
} from "../../shared/api";
import type { Region } from "../../shared/types";

/** Iki koordinat arasi mesafe (metre) — haversine. Bransman tahmini icin
 *  hassasiyet fazlasiyla yeterli. */
function metreMesafe(lat1: number, lon1: number, lat2: number, lon2: number): number {
  const R = 6371000;
  const rad = Math.PI / 180;
  const dLat = (lat2 - lat1) * rad;
  const dLon = (lon2 - lon1) * rad;
  const a =
    Math.sin(dLat / 2) ** 2 +
    Math.cos(lat1 * rad) * Math.cos(lat2 * rad) * Math.sin(dLon / 2) ** 2;
  return 2 * R * Math.asin(Math.sqrt(a));
}

/** Bransman tahmini esigi (m): ilk direk mevcut bir direge bundan yakinsa
 *  "bu hattan dallaniyor olabilir" onerilir. SADECE TAHMIN — karar kullanicinin. */
const BRANSMAN_ESIK_M = 200;

/** Yapistirilan direkler degistikce haritayi onlara sigdir. */
function FitToPoles({ poles }: { poles: Array<{ lat: number; lon: number }> }) {
  const map = useMap();
  useEffect(() => {
    if (poles.length === 0) return;
    if (poles.length === 1) {
      map.setView([poles[0].lat, poles[0].lon], 15);
      return;
    }
    const bounds: LatLngBoundsExpression = poles.map((p) => [p.lat, p.lon] as [number, number]);
    map.fitBounds(bounds, { padding: [24, 24] });
  }, [map, poles]);
  return null;
}

/** Sihirbaz onizleme haritasi: yeni hat TURUNCU, mevcut topoloji SOLUK gri,
 *  bransman adayi MAVI halka. Salt gorsel — tiklama/duzenleme yok; ince ayar
 *  olusturduktan sonra Duzenleme Modu'nda. */
function QaPreviewMap({
  poles,
  snapshot,
  branchGuess,
}: {
  poles: Array<{ lat: number; lon: number }>;
  snapshot: GridSnapshot | null;
  branchGuess: { lat: number; lon: number } | null;
}) {
  // Mevcut hatlarin cizgileri (line_id -> sirali koordinatlar).
  const existing = useMemo(() => {
    if (!snapshot) return [];
    const byLine = new Map<number, Array<{ seq: number; lat: number; lon: number }>>();
    for (const p of snapshot.poles) {
      const arr = byLine.get(p.line_id) ?? [];
      arr.push({ seq: p.sequence_no, lat: p.latitude, lon: p.longitude });
      byLine.set(p.line_id, arr);
    }
    return [...byLine.values()].map((arr) =>
      arr.sort((a, b) => a.seq - b.seq).map((p) => [p.lat, p.lon] as [number, number])
    );
  }, [snapshot]);

  const yeni = poles.map((p) => [p.lat, p.lon] as [number, number]);

  return (
    <MapContainer
      center={yeni[0] ?? [39.0, 35.0]}
      zoom={6}
      className="grid-qa-map"
      scrollWheelZoom
      attributionControl={false}
    >
      <ResilientTileLayer layer="osm" maxZoom={MAP_LAYERS[0].maxZoom} />
      <FitToPoles poles={poles} />
      {existing.map((pts, i) =>
        pts.length >= 2 ? (
          <Polyline key={i} positions={pts} pathOptions={{ color: "#94a3b8", weight: 2, opacity: 0.6 }} />
        ) : null
      )}
      {yeni.length >= 2 ? (
        <Polyline positions={yeni} pathOptions={{ color: "#ea9010", weight: 4, opacity: 0.9 }} />
      ) : null}
      {poles.map((p, i) => (
        <CircleMarker
          key={i}
          center={[p.lat, p.lon]}
          radius={i === 0 ? 6 : 4.5}
          pathOptions={{
            color: i === 0 ? "#15803d" : "#ea9010",
            fillColor: "#ffffff",
            fillOpacity: 1,
            weight: 2.5,
          }}
        />
      ))}
      {branchGuess ? (
        <CircleMarker
          center={[branchGuess.lat, branchGuess.lon]}
          radius={9}
          pathOptions={{ color: "#1d4ed8", fillOpacity: 0, weight: 3, dashArray: "4 3" }}
        />
      ) : null}
    </MapContainer>
  );
}
import { useToast } from "../../components/ToastProvider";
import { useModalDialog } from "../../shared/useModalDialog";

/** "enlem, boylam" satirini coz — backend _parse_coord_pair ile ayni akil:
 *  virgul/bosluk/noktali virgul ayirici, ondalik virgul, ters yapistirmada
 *  otomatik takas. Gecersizse null. */
function parseCoordLine(satir: string): { lat: number; lon: number } | null {
  const s = satir.trim();
  if (!s || s.toLowerCase().startsWith("ornek") || s.toLowerCase().startsWith("örnek")) return null;
  const parcalar = s.split(/[;,\t ]+/).filter(Boolean);
  const say = (metin: string): number | null => {
    const n = Number(metin.replace(",", "."));
    return Number.isFinite(n) ? n : null;
  };
  let lat: number | null = null;
  let lon: number | null = null;
  if (parcalar.length === 2) {
    lat = say(parcalar[0]);
    lon = say(parcalar[1]);
  } else if (parcalar.length === 4) {
    // "39,92 32,85" — ondalik virgul boslukla ayrilmis.
    lat = say(`${parcalar[0]}.${parcalar[1]}`);
    lon = say(`${parcalar[2]}.${parcalar[3]}`);
  }
  if (lat === null || lon === null) return null;
  const gecerli = (la: number, lo: number) => Math.abs(la) <= 90 && Math.abs(lo) <= 180;
  if (gecerli(lat, lon)) return { lat, lon };
  if (gecerli(lon, lat)) return { lat: lon, lon: lat };
  return null;
}

type Props = {
  accessToken: string;
  onClose: () => void;
  onImported: () => void;
};

type Method = null | "excel" | "qa";

export function LineWizardModal({ accessToken, onClose, onImported }: Props) {
  const { t } = useTranslation();
  // ESC ile kapanma + odak tuzagi (modal disina Tab ile cikilamasin).
  const dialogRef = useModalDialog<HTMLDivElement>(onClose);
  const toast = useToast();
  const fileInputRef = useRef<HTMLInputElement | null>(null);

  const [method, setMethod] = useState<Method>(null);
  const [file, setFile] = useState<File | null>(null);
  const [preview, setPreview] = useState<GridImportPreview | null>(null);
  const [downloading, setDownloading] = useState(false);
  const [previewing, setPreviewing] = useState(false);
  const [committing, setCommitting] = useState(false);
  const [error, setError] = useState("");

  // ---- Soru-cevap (QA) durumu ----
  const [qaStep, setQaStep] = useState(0); // 0: bolge, 1: hat, 2: direkler, 3: ozet
  const [regions, setRegions] = useState<Region[]>([]);
  const [qaRegionCode, setQaRegionCode] = useState("");
  const [qaNewRegion, setQaNewRegion] = useState("");
  const [qaLineName, setQaLineName] = useState("");
  const [qaLineCode, setQaLineCode] = useState("");
  const [qaCodeTouched, setQaCodeTouched] = useState(false);
  const [qaCoordText, setQaCoordText] = useState("");
  const [qaNamePrefix, setQaNamePrefix] = useState("d");
  const [snapshot, setSnapshot] = useState<GridSnapshot | null>(null);
  /** Bransman tahmini kabul edildi mi? null = kullanici henuz dokunmadi
   *  (varsayilan: cok yakinsa isaretli gelir). */
  const [qaBranchAccepted, setQaBranchAccepted] = useState<boolean | null>(null);

  useEffect(() => {
    if (method !== "qa" || regions.length > 0) return;
    void fetchRegions(accessToken)
      .then(setRegions)
      .catch(() => {
        /* bolge listesi gelmezse yeni bolge alani yine calisir */
      });
    // Bransman tahmini icin mevcut topoloji (tek istek, sessiz hata).
    void fetchGridSnapshot(accessToken)
      .then(setSnapshot)
      .catch(() => {});
  }, [method, regions.length, accessToken]);

  /** Hat adindan kod onerisi: "Merkez TR-3 Hattı" -> "MERKEZ-TR-3-HATTI".
   *  Kullanici koda elle dokunduysa oneri EZMEZ. */
  useEffect(() => {
    if (qaCodeTouched) return;
    const kod = qaLineName
      .trim()
      .toLocaleUpperCase("tr")
      .replace(/[ÇĞİIÖŞÜ]/g, (c) => ({ "Ç": "C", "Ğ": "G", "İ": "I", "I": "I", "Ö": "O", "Ş": "S", "Ü": "U" }[c] ?? c))
      .replace(/[^A-Z0-9]+/g, "-")
      .replace(/^-+|-+$/g, "")
      .slice(0, 24);
    setQaLineCode(kod);
  }, [qaLineName, qaCodeTouched]);

  /** Yapistirilan koordinat metninin canli cozumu: gecerli direkler + ilk
   *  hatali satirlar. Kullanici daha gondermeden ne olacagini gorur. */
  const qaParsed = useMemo(() => {
    const satirlar = qaCoordText.split(/\r?\n/);
    const poles: Array<{ lat: number; lon: number }> = [];
    const badLines: number[] = [];
    for (let i = 0; i < satirlar.length; i++) {
      const s = satirlar[i].trim();
      if (!s) continue;
      const p = parseCoordLine(s);
      if (p) poles.push(p);
      else if (!s.toLowerCase().startsWith("ornek") && !s.toLowerCase().startsWith("örnek")) {
        badLines.push(i + 1);
      }
    }
    return { poles, badLines };
  }, [qaCoordText]);

  const qaRegion = qaRegionCode === "__new__" ? qaNewRegion.trim() : qaRegionCode;
  const qaRegionOk = qaRegion.length > 0;
  const qaLineOk = qaLineCode.trim().length > 0;

  /** BRANSMAN TAHMINI (sadece tahmin): yeni hattin ILK diregi, mevcut bir
   *  hattin diregine BRANSMAN_ESIK_M'den yakinsa en yakin adayi oner.
   *  Ayni kodlu hat (ekleme senaryosu) aday sayilmaz. */
  const qaBranchGuess = useMemo(() => {
    if (!snapshot || qaParsed.poles.length === 0) return null;
    const ilk = qaParsed.poles[0];
    const lineById = new Map(snapshot.lines.map((l) => [l.id, l]));
    let enIyi:
      | { lineCode: string; lineName: string; seq: number; mesafe: number; lat: number; lon: number }
      | null = null;
    for (const p of snapshot.poles) {
      const hat = lineById.get(p.line_id);
      if (!hat || hat.code === qaLineCode.trim()) continue;
      const d = metreMesafe(ilk.lat, ilk.lon, p.latitude, p.longitude);
      if (d <= BRANSMAN_ESIK_M && (enIyi === null || d < enIyi.mesafe)) {
        enIyi = {
          lineCode: hat.code, lineName: hat.name, seq: p.sequence_no,
          mesafe: d, lat: p.latitude, lon: p.longitude,
        };
      }
    }
    return enIyi;
  }, [snapshot, qaParsed.poles, qaLineCode]);

  // Cok yakin aday (<=60 m) varsayilan ISARETLI gelir; yine de kullanici
  // kapatabilir. Uzak aday yalnizca oneri olarak sunulur.
  const qaBranchOn =
    qaBranchGuess !== null &&
    (qaBranchAccepted ?? qaBranchGuess.mesafe <= 60);

  const handleQaCreate = async () => {
    setCommitting(true);
    setError("");
    try {
      const secili = regions.find((r) => r.code === qaRegionCode);
      const result = await createGridWizardLine(accessToken, {
        region_code: qaRegion,
        region_name: qaRegionCode === "__new__" ? qaNewRegion.trim() : secili?.name ?? qaRegion,
        line_code: qaLineCode.trim(),
        line_name: qaLineName.trim() || qaLineCode.trim(),
        poles: qaParsed.poles.map((p, i) => ({
          latitude: p.lat,
          longitude: p.lon,
          name: qaNamePrefix.trim() ? `${qaNamePrefix.trim()}${i + 1}` : null,
        })),
        ...(qaBranchOn && qaBranchGuess
          ? {
              branch_line_code: qaBranchGuess.lineCode,
              branch_pole_seq: qaBranchGuess.seq,
            }
          : {}),
      });
      toast.success(
        t("engineering.grid.wizard.qaSuccessToast", {
          line: qaLineCode.trim(),
          poles: result.poles_created,
        })
      );
      onImported();
      onClose();
    } catch (err) {
      setError(err instanceof Error ? err.message : t("engineering.grid.wizard.qaCreateFail"));
    } finally {
      setCommitting(false);
    }
  };

  const handleDownload = async () => {
    setDownloading(true);
    setError("");
    try {
      await downloadGridImportTemplate(accessToken);
    } catch (err) {
      setError(err instanceof Error ? err.message : t("engineering.grid.import.downloadFail"));
    } finally {
      setDownloading(false);
    }
  };

  const handlePickFile = () => fileInputRef.current?.click();

  const handleFileChange = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const selected = e.target.files?.[0];
    e.target.value = "";
    if (!selected) return;
    setFile(selected);
    setPreview(null);
    setError("");
    setPreviewing(true);
    try {
      const result = await previewGridImport(accessToken, selected);
      setPreview(result);
    } catch (err) {
      setFile(null);
      setError(err instanceof Error ? err.message : t("engineering.grid.import.previewFail"));
    } finally {
      setPreviewing(false);
    }
  };

  const handleCommit = async () => {
    if (!file) return;
    setCommitting(true);
    setError("");
    try {
      const result = await commitGridImport(accessToken, file);
      toast.success(
        t("engineering.grid.import.successToast", {
          regions: result.regions_created,
          lines: result.lines_created,
          poles: result.poles_created,
          devices: result.segments_created,
        })
      );
      onImported();
      onClose();
    } catch (err) {
      setError(err instanceof Error ? err.message : t("engineering.grid.import.commitFail"));
    } finally {
      setCommitting(false);
    }
  };

  const hasErrors = (preview?.errors.length ?? 0) > 0;
  const hasValidRows = (preview?.poles ?? 0) > 0;

  return (
    <div className="settings-modal-backdrop" onClick={onClose}>
      <div
        className="settings-modal grid-wizard-modal"
        onClick={(e) => e.stopPropagation()}
        role="dialog"
        aria-modal="true"
        ref={dialogRef}
      >
        <div className="grid-wizard-head">
          <h3>{t("engineering.grid.wizard.title")}</h3>
          {method ? (
            <button className="grid-wizard-back" onClick={() => setMethod(null)}>
              <span className="material-symbols-outlined">arrow_back</span>
              {t("engineering.grid.wizard.back")}
            </button>
          ) : null}
        </div>

        {/* Adım 0 — Yol seçimi */}
        {method === null ? (
          <div className="grid-wizard-methods">
            <p className="helper-text">{t("engineering.grid.wizard.chooseMethod")}</p>
            <div className="grid-wizard-cards">
              <button className="grid-wizard-card" onClick={() => setMethod("excel")}>
                <span className="material-symbols-outlined grid-wizard-card-icon">table_view</span>
                <strong>{t("engineering.grid.wizard.excelMethod")}</strong>
                <span className="grid-wizard-card-desc">
                  {t("engineering.grid.wizard.excelMethodDesc")}
                </span>
              </button>
              <button className="grid-wizard-card" onClick={() => setMethod("qa")}>
                <span className="material-symbols-outlined grid-wizard-card-icon">forum</span>
                <strong>{t("engineering.grid.wizard.qaMethod")}</strong>
                <span className="grid-wizard-card-desc">
                  {t("engineering.grid.wizard.qaMethodDesc")}
                </span>
              </button>
            </div>
          </div>
        ) : null}

        {/* Soru-cevap yolu — adim adim tek hat */}
        {method === "qa" ? (
          <div className="grid-wizard-qa">
            {/* Adim gostergesi */}
            <div className="grid-qa-steps">
              {[0, 1, 2, 3].map((s) => (
                <span
                  key={s}
                  className={`grid-qa-step-dot${qaStep === s ? " is-active" : qaStep > s ? " is-done" : ""}`}
                >
                  {qaStep > s ? (
                    <span className="material-symbols-outlined">check</span>
                  ) : (
                    s + 1
                  )}
                </span>
              ))}
              <span className="grid-qa-step-label">
                {t(`engineering.grid.wizard.qaStep${qaStep}` as const)}
              </span>
            </div>

            {/* Adim 0 — Bolge */}
            {qaStep === 0 ? (
              <div className="grid-qa-body">
                <p className="helper-text">{t("engineering.grid.wizard.qaRegionHint")}</p>
                <div className="grid-qa-region-list">
                  {regions.map((r) => (
                    <button
                      key={r.id}
                      className={`grid-qa-chip${qaRegionCode === r.code ? " is-active" : ""}`}
                      onClick={() => setQaRegionCode(r.code)}
                    >
                      {r.name}
                    </button>
                  ))}
                  <button
                    className={`grid-qa-chip grid-qa-chip--new${qaRegionCode === "__new__" ? " is-active" : ""}`}
                    onClick={() => setQaRegionCode("__new__")}
                  >
                    <span className="material-symbols-outlined">add</span>
                    {t("engineering.grid.wizard.qaNewRegion")}
                  </button>
                </div>
                {qaRegionCode === "__new__" ? (
                  <input
                    className="grid-qa-input"
                    value={qaNewRegion}
                    autoFocus
                    placeholder={t("engineering.grid.wizard.qaNewRegionPlaceholder")}
                    onChange={(e) => setQaNewRegion(e.target.value)}
                  />
                ) : null}
              </div>
            ) : null}

            {/* Adim 1 — Hat adi/kodu */}
            {qaStep === 1 ? (
              <div className="grid-qa-body">
                <label className="grid-qa-field">
                  <span>{t("engineering.grid.wizard.qaLineName")}</span>
                  <input
                    className="grid-qa-input"
                    value={qaLineName}
                    autoFocus
                    placeholder={t("engineering.grid.wizard.qaLineNamePlaceholder")}
                    onChange={(e) => setQaLineName(e.target.value)}
                  />
                </label>
                <label className="grid-qa-field">
                  <span>{t("engineering.grid.wizard.qaLineCode")}</span>
                  <input
                    className="grid-qa-input grid-qa-input--code"
                    value={qaLineCode}
                    placeholder="MERKEZ-TR-3"
                    onChange={(e) => {
                      setQaCodeTouched(true);
                      setQaLineCode(e.target.value.toUpperCase());
                    }}
                  />
                  <small className="helper-text">{t("engineering.grid.wizard.qaLineCodeHint")}</small>
                </label>
              </div>
            ) : null}

            {/* Adim 2 — Koordinatlar */}
            {qaStep === 2 ? (
              <div className="grid-qa-body">
                <p className="helper-text">{t("engineering.grid.wizard.qaCoordHint")}</p>
                <textarea
                  className="grid-qa-coords"
                  value={qaCoordText}
                  autoFocus
                  spellCheck={false}
                  placeholder={"39.92042, 32.85411\n39.92180, 32.85602\n39.92311, 32.85795"}
                  onChange={(e) => setQaCoordText(e.target.value)}
                />
                <div className="grid-qa-coord-status">
                  <span className={qaParsed.poles.length >= 2 ? "is-ok" : ""}>
                    <span className="material-symbols-outlined">location_on</span>
                    {t("engineering.grid.wizard.qaCoordCount", { count: qaParsed.poles.length })}
                  </span>
                  {qaParsed.badLines.length > 0 ? (
                    <span className="is-bad">
                      <span className="material-symbols-outlined">warning</span>
                      {t("engineering.grid.wizard.qaCoordBad", {
                        lines: qaParsed.badLines.slice(0, 5).join(", "),
                        count: qaParsed.badLines.length,
                      })}
                    </span>
                  ) : null}
                </div>

                {/* CANLI HARITA ONIZLEME: yapistirdikca hat haritada cizilir;
                    mevcut topoloji soluk gri baglam olarak gorunur. */}
                {qaParsed.poles.length > 0 ? (
                  <QaPreviewMap
                    poles={qaParsed.poles}
                    snapshot={snapshot}
                    branchGuess={qaBranchGuess}
                  />
                ) : null}
                <label className="grid-qa-field grid-qa-field--inline">
                  <span>{t("engineering.grid.wizard.qaNamePrefix")}</span>
                  <input
                    className="grid-qa-input grid-qa-input--short"
                    value={qaNamePrefix}
                    onChange={(e) => setQaNamePrefix(e.target.value)}
                  />
                  <small className="helper-text">
                    {qaNamePrefix.trim()
                      ? t("engineering.grid.wizard.qaNamePreview", {
                          first: `${qaNamePrefix.trim()}1`,
                          last: `${qaNamePrefix.trim()}${Math.max(qaParsed.poles.length, 1)}`,
                        })
                      : t("engineering.grid.wizard.qaNameNone")}
                  </small>
                </label>
              </div>
            ) : null}

            {/* Adim 3 — Ozet */}
            {qaStep === 3 ? (
              <div className="grid-qa-body">
                {/* Cizilecek hattin son hali haritada. */}
                {qaParsed.poles.length > 0 ? (
                  <QaPreviewMap
                    poles={qaParsed.poles}
                    snapshot={snapshot}
                    branchGuess={qaBranchOn ? qaBranchGuess : null}
                  />
                ) : null}
                <ul className="net-confirm-summary">
                  <li>
                    <span>{t("engineering.grid.wizard.qaSumRegion")}</span>
                    <strong>{qaRegionCode === "__new__" ? `${qaNewRegion.trim()} (${t("engineering.grid.wizard.qaSumNew")})` : regions.find((r) => r.code === qaRegionCode)?.name ?? qaRegion}</strong>
                  </li>
                  <li>
                    <span>{t("engineering.grid.wizard.qaSumLine")}</span>
                    <strong>{qaLineName.trim() || qaLineCode} ({qaLineCode})</strong>
                  </li>
                  <li>
                    <span>{t("engineering.grid.wizard.qaSumPoles")}</span>
                    <strong>{qaParsed.poles.length}</strong>
                  </li>
                  <li>
                    <span>{t("engineering.grid.wizard.qaSumSegments")}</span>
                    <strong>{Math.max(qaParsed.poles.length - 1, 0)}</strong>
                  </li>
                </ul>

                {/* BRANSMAN TAHMINI — sadece oneri; onay kullanicinin. */}
                {qaBranchGuess ? (
                  <label className="grid-qa-branch">
                    <input
                      type="checkbox"
                      checked={qaBranchOn}
                      onChange={(e) => setQaBranchAccepted(e.target.checked)}
                    />
                    <span className="material-symbols-outlined">account_tree</span>
                    <span className="grid-qa-branch-text">
                      <strong>{t("engineering.grid.wizard.qaBranchTitle")}</strong>
                      {t("engineering.grid.wizard.qaBranchText", {
                        line: qaBranchGuess.lineName || qaBranchGuess.lineCode,
                        seq: qaBranchGuess.seq,
                        m: Math.round(qaBranchGuess.mesafe),
                      })}
                    </span>
                  </label>
                ) : null}

                <p className="helper-text">{t("engineering.grid.wizard.qaSumHint")}</p>
              </div>
            ) : null}

            {error ? <p className="error-text">{error}</p> : null}

            <div className="settings-actions grid-import-actions">
              <button
                className="grid-import-btn-cancel"
                onClick={() => (qaStep === 0 ? setMethod(null) : setQaStep(qaStep - 1))}
                disabled={committing}
              >
                {qaStep === 0 ? t("common.cancel") : t("engineering.grid.wizard.qaBack")}
              </button>
              {qaStep < 3 ? (
                <button
                  className="grid-import-btn-primary"
                  disabled={
                    (qaStep === 0 && !qaRegionOk) ||
                    (qaStep === 1 && !qaLineOk) ||
                    (qaStep === 2 && qaParsed.poles.length < 2)
                  }
                  onClick={() => setQaStep(qaStep + 1)}
                >
                  {t("engineering.grid.wizard.qaNext")}
                  <span className="material-symbols-outlined">chevron_right</span>
                </button>
              ) : (
                <button
                  className="grid-import-btn-primary"
                  disabled={committing || qaParsed.poles.length < 2}
                  onClick={handleQaCreate}
                >
                  <span className="material-symbols-outlined">check</span>
                  {committing
                    ? t("engineering.grid.wizard.qaCreating")
                    : t("engineering.grid.wizard.qaCreate")}
                </button>
              )}
            </div>
          </div>
        ) : null}

        {/* Excel yolu */}
        {method === "excel" ? (
          <>
            <div className="grid-import-step">
              <div className="grid-import-step-num">1</div>
              <div className="grid-import-step-body">
                <strong>{t("engineering.grid.import.step1Title")}</strong>
                <p className="helper-text">{t("engineering.grid.import.step1Hint")}</p>
                <button className="grid-import-btn-soft" onClick={handleDownload} disabled={downloading}>
                  <span className="material-symbols-outlined">download</span>
                  {downloading
                    ? t("engineering.grid.import.downloading")
                    : t("engineering.grid.import.downloadTemplate")}
                </button>
              </div>
            </div>

            <div className="grid-import-step">
              <div className="grid-import-step-num">2</div>
              <div className="grid-import-step-body">
                <strong>{t("engineering.grid.import.step2Title")}</strong>
                <p className="helper-text">{t("engineering.grid.import.step2Hint")}</p>
                <input
                  ref={fileInputRef}
                  type="file"
                  accept=".xlsx"
                  style={{ display: "none" }}
                  onChange={handleFileChange}
                />
                <button
                  className={`grid-import-filepick ${file ? "has-file" : ""}`}
                  onClick={handlePickFile}
                  disabled={previewing}
                >
                  <span className="material-symbols-outlined">upload_file</span>
                  {file ? file.name : t("engineering.grid.import.selectFile")}
                </button>
                {previewing ? (
                  <p className="helper-text">{t("engineering.grid.import.analyzing")}</p>
                ) : null}
              </div>
            </div>

            {preview ? (
              <div className="grid-import-preview">
                <div className="grid-import-summary">
                  <SummaryCard value={preview.regions} label={t("engineering.grid.import.regions")} />
                  <SummaryCard value={preview.lines} label={t("engineering.grid.import.lines")} />
                  <SummaryCard value={preview.poles} label={t("engineering.grid.import.poles")} />
                  <SummaryCard value={preview.devices} label={t("engineering.grid.import.devices")} />
                </div>
                {hasErrors ? (
                  <div className="grid-import-errors">
                    <strong>
                      {t("engineering.grid.import.errorsTitle", { count: preview.errors.length })}
                    </strong>
                    <ul>
                      {preview.errors.slice(0, 50).map((e, i) => (
                        <li key={i}>
                          <span className="grid-import-error-row">
                            {t("engineering.grid.import.rowLabel", { row: e.row })}
                          </span>
                          {e.message}
                        </li>
                      ))}
                    </ul>
                    {preview.errors.length > 50 ? (
                      <p className="helper-text">
                        {t("engineering.grid.import.moreErrors", { count: preview.errors.length - 50 })}
                      </p>
                    ) : null}
                  </div>
                ) : (
                  <p className="grid-import-ok">
                    <span className="material-symbols-outlined">check_circle</span>
                    {t("engineering.grid.import.noErrors")}
                  </p>
                )}
              </div>
            ) : null}

            {error ? <p className="error-text">{error}</p> : null}

            <div className="settings-actions grid-import-actions">
              <button className="grid-import-btn-cancel" onClick={onClose} disabled={committing}>
                {t("common.cancel")}
              </button>
              <button
                className="grid-import-btn-primary"
                onClick={handleCommit}
                disabled={!file || !hasValidRows || committing || previewing}
                title={hasErrors ? t("engineering.grid.import.commitWithErrorsHint") : undefined}
              >
                <span className="material-symbols-outlined">table_view</span>
                {committing
                  ? t("engineering.grid.import.importing")
                  : hasErrors
                    ? t("engineering.grid.import.importValidOnly")
                    : t("engineering.grid.import.import")}
              </button>
            </div>
          </>
        ) : null}
      </div>
    </div>
  );
}

function SummaryCard({ value, label }: { value: number; label: string }) {
  return (
    <div className="grid-import-summary-card">
      <strong>{value}</strong>
      <span>{label}</span>
    </div>
  );
}
