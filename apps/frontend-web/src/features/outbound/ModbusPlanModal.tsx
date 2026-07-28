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
  const [plan, setPlan] = useState<ModbusPlan | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [search, setSearch] = useState("");
  const [deviceFilter, setDeviceFilter] = useState("");
  const [downloading, setDownloading] = useState(false);

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
                  {filtered.slice(0, 500).map((p) => (
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
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
              {filtered.length > 500 ? (
                <p className="helper-text modbus-plan-truncated">
                  {t("engineering.outbound.modbus.truncated", {
                    shown: 500,
                    total: filtered.length
                  })}
                </p>
              ) : null}
            </div>
          </>
        ) : null}
      </div>
    </div>
  );
}
