/**
 * Modbus adres plani modal'i — "hangi sinyal hangi adreste" sorusunun cevabi.
 *
 * Plan backend'de uretilir (`modbus_plan_service`); burada gosterilen adres,
 * modbus-outbound servisinin sahada yayinladigi adresin TA KENDISIDIR — iki
 * taraf ayni endpoint'ten beslenir, ayrisma olamaz.
 *
 * Saha muhendisi bu tabloyu SCADA'ya girer; CSV disa aktarim ayni satirlari
 * Modicon gosterimiyle (40001 tarzi) birlikte verir.
 */
import { useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";

import { fetchModbusPlan } from "../../shared/api";
import type { ModbusPlan } from "../../shared/types";
import { useModalDialog } from "../../shared/useModalDialog";

type Props = {
  accessToken: string;
  targetId: number;
  targetName: string;
  onClose: () => void;
  onDownloadCsv: (targetId: number, suggestedName: string) => Promise<void>;
};

// Modicon (klasik) gosterim tabanlari — SCADA'larin cogu bu numaralandirmayi
// kullanir: coil 1, discrete input 10001, input register 30001, holding 40001.
const MODICON_BASE: Record<number, number> = { 1: 1, 2: 10001, 3: 40001, 4: 30001 };

const FUNCTION_LABEL: Record<number, string> = {
  1: "FC1 coil",
  2: "FC2 discrete",
  3: "FC3 holding",
  4: "FC4 input"
};

export function ModbusPlanModal({
  accessToken,
  targetId,
  targetName,
  onClose,
  onDownloadCsv
}: Props) {
  const { t } = useTranslation();
  // ESC ile kapanma + odak tuzagi (modal disina Tab ile cikilamasin).
  const dialogRef = useModalDialog<HTMLDivElement>(onClose);
  const [plan, setPlan] = useState<ModbusPlan | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [search, setSearch] = useState("");
  const [deviceFilter, setDeviceFilter] = useState("");
  const [downloading, setDownloading] = useState(false);
  // Sayfalama — plan cihaz x sinyal carpimi oldugu icin hizla on binlerce
  // satira cikar (100 cihaz x 160 nokta = 16.000). Hepsini birden DOM'a
  // basmak sayfayi kilitliyordu; sayfa sayfa render ediyoruz.
  const [page, setPage] = useState(0);
  const [pageSize, setPageSize] = useState(100);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    fetchModbusPlan(accessToken, targetId)
      .then((data) => {
        if (!cancelled) {
          setPlan(data);
          setError(null);
        }
      })
      .catch((exc) => {
        if (!cancelled) {
          setError(exc instanceof Error ? exc.message : t("common.errorOccurred"));
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [accessToken, targetId, t]);

  const filtered = useMemo(() => {
    if (!plan) return [];
    const needle = search.trim().toLowerCase();
    return plan.points.filter((p) => {
      if (deviceFilter && p.device_code !== deviceFilter) return false;
      if (!needle) return true;
      return (
        p.signal_key.toLowerCase().includes(needle) ||
        p.label.toLowerCase().includes(needle) ||
        String(p.address).includes(needle)
      );
    });
  }, [plan, search, deviceFilter]);

  const pageCount = Math.max(1, Math.ceil(filtered.length / pageSize));
  // Filtre/arama degisince gecerli sayfa aralik disinda kalabilir — basa al.
  useEffect(() => {
    setPage(0);
  }, [search, deviceFilter, pageSize]);
  const safePage = Math.min(page, pageCount - 1);
  const pageStart = safePage * pageSize;
  const pageRows = useMemo(
    () => filtered.slice(pageStart, pageStart + pageSize),
    [filtered, pageStart, pageSize]
  );

  const handleDownload = async () => {
    setDownloading(true);
    try {
      await onDownloadCsv(targetId, `modbus-points-${targetName}.csv`);
    } finally {
      setDownloading(false);
    }
  };

  return (
    <div className="settings-modal-backdrop" onClick={onClose}>
      <div
        className="settings-modal modbus-plan-modal"
        role="dialog"
        aria-modal="true"
        onClick={(e) => e.stopPropagation()}
        ref={dialogRef}
      >
        <header className="modbus-plan-head">
          <div>
            <h3>{t("engineering.outbound.modbus.planTitle")}</h3>
            <p className="helper-text">{targetName}</p>
          </div>
          <button type="button" className="secondary-btn" onClick={onClose}>
            {t("common.close")}
          </button>
        </header>

        {loading ? <p className="helper-text">{t("common.loading")}</p> : null}
        {error ? <p className="error-text">{error}</p> : null}

        {plan ? (
          <>
            {/* Kapasite ozeti — kullanicinin ilk sordugu soru: kac cihaz siger? */}
            <div className="modbus-plan-stats">
              <div className="modbus-plan-stat">
                <span>{t("engineering.outbound.modbus.statMode")}</span>
                <strong>
                  {plan.mode === "unit"
                    ? t("engineering.outbound.modbus.modeUnit")
                    : t("engineering.outbound.modbus.modeBlock")}
                </strong>
              </div>
              <div className="modbus-plan-stat">
                <span>{t("engineering.outbound.modbus.statDevices")}</span>
                <strong>
                  {plan.capacity.device_count}
                  <em> / {plan.capacity.max_devices}</em>
                </strong>
              </div>
              <div className="modbus-plan-stat">
                <span>{t("engineering.outbound.modbus.statPerDevice")}</span>
                <strong>
                  {plan.summary.register_words} <em>word</em>
                </strong>
              </div>
              <div className="modbus-plan-stat">
                <span>{t("engineering.outbound.modbus.statBits")}</span>
                <strong>
                  {plan.summary.discrete_bits + plan.summary.coil_bits} <em>bit</em>
                </strong>
              </div>
              <div className="modbus-plan-stat">
                <span>{t("engineering.outbound.modbus.statPoints")}</span>
                <strong>{plan.points.length}</strong>
              </div>
            </div>

            {/* Kapasiteye SIGMAYAN cihazlar. `remaining: 0` tek basina "tam"
                gibi okunuyordu; bu cihazlar SCADA'ya HIC yayinlanmiyor ve
                operator onlari "sakin" saniyor. Cozum tavani buyutmek degil,
                yuku ikinci bir hedefe bolmek. */}
            {plan.capacity.dropped_device_count > 0 ? (
              <p className="net-banner net-banner--bad">
                <span className="material-symbols-outlined">error</span>
                {t("engineering.outbound.modbus.droppedDevices", {
                  count: plan.capacity.dropped_device_count,
                  codes: plan.capacity.dropped_device_codes.join(", ")
                })}
              </p>
            ) : null}

            {/* Olcegi genisletilen sinyaller — SCADA tarafinda eski katsayiyla
                kurulmus bir esleme varsa guncellenmeli. */}
            {plan.summary.rescaled_count > 0 ? (
              <p className="net-banner net-banner--warn">
                <span className="material-symbols-outlined">straighten</span>
                {t("engineering.outbound.modbus.rescaledNote", {
                  count: plan.summary.rescaled_count
                })}
              </p>
            ) : null}

            <p className="modbus-plan-note">
              {plan.capacity.single_read_per_device
                ? t("engineering.outbound.modbus.noteSingleRead", { stride: plan.stride })
                : t("engineering.outbound.modbus.noteMultiRead", {
                    words: plan.summary.register_words
                  })}
              {plan.summary.excluded_string_count > 0
                ? ` · ${t("engineering.outbound.modbus.noteStrings", {
                    count: plan.summary.excluded_string_count
                  })}`
                : ""}
            </p>

            <div className="modbus-plan-toolbar">
              <input
                type="search"
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                placeholder={t("engineering.outbound.modbus.searchPlaceholder")}
              />
              <select
                value={deviceFilter}
                onChange={(e) => setDeviceFilter(e.target.value)}
              >
                <option value="">{t("engineering.outbound.modbus.allDevices")}</option>
                {plan.devices.map((d) => (
                  <option key={d.device_id} value={d.device_code}>
                    {d.device_code}
                    {plan.mode === "unit"
                      ? ` — unit ${d.unit_id}`
                      : ` — ${d.block_start}`}
                  </option>
                ))}
              </select>
              <button
                type="button"
                className="primary-btn"
                onClick={() => void handleDownload()}
                disabled={downloading}
              >
                {downloading ? t("common.loading") : t("engineering.outbound.modbus.downloadCsv")}
              </button>
            </div>

            <div className="modbus-plan-table-wrap">
              <table className="modbus-plan-table">
                <thead>
                  <tr>
                    <th>{t("engineering.outbound.modbus.colDevice")}</th>
                    <th>Unit</th>
                    <th>{t("engineering.outbound.modbus.colFunction")}</th>
                    <th>{t("engineering.outbound.modbus.colAddress")}</th>
                    <th>Modicon</th>
                    <th>{t("engineering.outbound.modbus.colSignal")}</th>
                    <th>{t("engineering.outbound.modbus.colScale")}</th>
                  </tr>
                </thead>
                <tbody>
                  {pageRows.map((p) => (
                    <tr key={`${p.device_code}-${p.signal_key}`}>
                      <td>{p.device_code}</td>
                      <td className="mono">{p.unit_id}</td>
                      <td>
                        <span className={`modbus-fc modbus-fc--${p.function}`}>
                          {FUNCTION_LABEL[p.function] ?? p.function}
                        </span>
                      </td>
                      <td className="mono">
                        {p.address}
                        {p.word_count > 1 ? `–${p.address + p.word_count - 1}` : ""}
                      </td>
                      <td className="mono">
                        {(MODICON_BASE[p.function] ?? 0) + p.address}
                      </td>
                      <td>
                        <strong>{p.label}</strong>
                        <br />
                        <code className="inline-code">{p.signal_key}</code>
                      </td>
                      <td className="mono">
                        {p.function >= 3
                          ? `×${p.scale}${p.offset ? ` +${p.offset}` : ""}`
                          : "—"}
                        {p.unit ? ` ${p.unit}` : ""}
                        {p.rescaled ? (
                          <span
                            className="modbus-rescaled-mark"
                            title={t("engineering.outbound.modbus.rescaledHint", {
                              max: (32767 * p.scale).toLocaleString(),
                              unit: p.unit ?? ""
                            })}
                          >
                            ⤴
                          </span>
                        ) : null}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>

            {/* Sayfalama — filtrelenmis satirlar uzerinde. Tum plan tek
                seferde DOM'a basilmiyor; buyuk kurulumlarda (binlerce nokta)
                modal bu sayede aciliyor. */}
            <div className="modbus-plan-pager">
              <span className="modbus-plan-pager-info">
                {filtered.length === 0
                  ? t("engineering.outbound.modbus.pagerEmpty")
                  : t("engineering.outbound.modbus.pagerRange", {
                      from: pageStart + 1,
                      to: Math.min(pageStart + pageSize, filtered.length),
                      total: filtered.length
                    })}
              </span>

              <label className="modbus-plan-pager-size">
                {t("engineering.outbound.modbus.pagerPageSize")}
                <select
                  value={pageSize}
                  onChange={(event) => setPageSize(Number(event.target.value))}
                >
                  <option value={50}>50</option>
                  <option value={100}>100</option>
                  <option value={250}>250</option>
                  <option value={500}>500</option>
                </select>
              </label>

              <div className="modbus-plan-pager-nav">
                <button
                  type="button"
                  onClick={() => setPage(0)}
                  disabled={safePage === 0}
                  title={t("engineering.outbound.modbus.pagerFirst")}
                >
                  «
                </button>
                <button
                  type="button"
                  onClick={() => setPage((p) => Math.max(0, p - 1))}
                  disabled={safePage === 0}
                >
                  ‹ {t("engineering.outbound.modbus.pagerPrev")}
                </button>
                <span className="modbus-plan-pager-page">
                  {t("engineering.outbound.modbus.pagerPageOf", {
                    page: safePage + 1,
                    total: pageCount
                  })}
                </span>
                <button
                  type="button"
                  onClick={() => setPage((p) => Math.min(pageCount - 1, p + 1))}
                  disabled={safePage >= pageCount - 1}
                >
                  {t("engineering.outbound.modbus.pagerNext")} ›
                </button>
                <button
                  type="button"
                  onClick={() => setPage(pageCount - 1)}
                  disabled={safePage >= pageCount - 1}
                  title={t("engineering.outbound.modbus.pagerLast")}
                >
                  »
                </button>
              </div>
            </div>
          </>
        ) : null}
      </div>
    </div>
  );
}
