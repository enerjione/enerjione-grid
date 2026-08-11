/**
 * FaultHistoryTable — "Geçmiş Arızalar" sekmesi.
 *
 * Kapatilmis ariza kayitlari. Tablo SALT OKUNUR bir arsivdir.
 *
 * NEDEN ACILIR SATIR YOK
 * ----------------------
 * Onceden satira tiklayinca iki panel aciliyordu: yorum ekleme kutusu ve
 * "Çözümü Düzenle". Iki sorunu vardi:
 *
 *   1. Kapanmis kayda yorum/cozum yazmak, arsivlenen raporun sonradan
 *      sessizce degismesi demekti. Kapanis raporu neyse odur.
 *   2. Ayni bilgi (yorumlar, cozum, harita, kunye) arizanin kendi detay
 *      sayfasinda ZATEN var. Satirin icinde ikinci, eksik bir kopyasini
 *      tutmak iki ayri gercek kaynagi yaratiyordu.
 *
 * Artik satir dogrudan DETAY SEKMESINI acar; PDF rapor oradan alinir.
 * Yorum ekleme ve cozum duzenleme kapatilmis arizada hicbir yerde yok
 * (backend de reddeder).
 */
import { useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { CheckCircle2, ChevronRight, FilterX, Search } from "lucide-react";

import type { FaultEvent } from "../../shared/types";

type Props = {
  faults: FaultEvent[];
  localeTag: string;
  /** Kaydin detay sekmesini acar — islem ve PDF rapor orada. */
  onOpenFault: (faultId: number) => void;
};

/** Tarih araligi on ayarlari — tam takvim secici yerine sade preset. */
type RangeKey = "7d" | "30d" | "90d" | "all";
const RANGE_DAYS: Record<RangeKey, number | null> = {
  "7d": 7,
  "30d": 30,
  "90d": 90,
  all: null
};

function fmtDateTime(iso: string | null | undefined, localeTag: string): string {
  if (!iso) return "—";
  return new Date(iso).toLocaleString(localeTag, {
    day: "2-digit",
    month: "2-digit",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit"
  });
}

function fmtDuration(f: FaultEvent): string {
  const end = f.closed_at ?? f.resolved_at;
  if (!end) return "—";
  let sec = Math.max(
    0,
    Math.round((new Date(end).getTime() - new Date(f.opened_at).getTime()) / 1000)
  );
  const days = Math.floor(sec / 86400);
  sec -= days * 86400;
  const hours = Math.floor(sec / 3600);
  sec -= hours * 3600;
  const mins = Math.floor(sec / 60);
  if (days > 0) return `${days}g ${hours}sa`;
  if (hours > 0) return `${hours}sa ${String(mins).padStart(2, "0")}dk`;
  if (mins > 0) return `${mins}dk`;
  return "<1dk";
}

export function FaultHistoryTable({ faults, localeTag, onOpenFault }: Props) {
  const { t } = useTranslation();
  const [search, setSearch] = useState("");
  const [range, setRange] = useState<RangeKey>("30d");
  const [statusFilter, setStatusFilter] = useState<"all" | "resolved" | "closed">("all");

  const filtered = useMemo(() => {
    const q = search.trim().toLowerCase();
    const days = RANGE_DAYS[range];
    const cutoff = days != null ? Date.now() - days * 86400_000 : null;
    return faults
      .filter((f) => {
        if (statusFilter !== "all" && f.status !== statusFilter) return false;
        if (cutoff != null && new Date(f.opened_at).getTime() < cutoff) return false;
        if (!q) return true;
        const hay = `${f.line_name} ${f.region_name} ${f.from_pole_seq ?? ""} ${
          f.to_pole_seq ?? ""
        } ${f.last_red_device_name ?? ""} ${f.last_red_device_code ?? ""} ${
          f.assigned_to_full_name ?? ""
        } ${f.assigned_to_username ?? ""} ${f.note ?? ""} ${
          f.resolution_note ?? ""
        }`.toLowerCase();
        return hay.includes(q);
      })
      .sort(
        (a, b) =>
          new Date(b.closed_at ?? b.resolved_at ?? b.opened_at).getTime() -
          new Date(a.closed_at ?? a.resolved_at ?? a.opened_at).getTime()
      );
  }, [faults, search, range, statusFilter]);

  const clearFilters = () => {
    setSearch("");
    setRange("30d");
    setStatusFilter("all");
  };

  const filtersDirty = search !== "" || range !== "30d" || statusFilter !== "all";

  return (
    <section className="fx-history">
      <header className="fx-history-head">
        <h3>{t("faults.history.title")}</h3>
        <div className="fx-history-filters">
          <label className="fx-search">
            <Search size={15} strokeWidth={2.1} />
            <input
              type="search"
              placeholder={t("faults.history.searchPlaceholder")}
              value={search}
              onChange={(e) => setSearch(e.target.value)}
            />
          </label>
          <select value={range} onChange={(e) => setRange(e.target.value as RangeKey)}>
            <option value="7d">{t("faults.history.range7d")}</option>
            <option value="30d">{t("faults.history.range30d")}</option>
            <option value="90d">{t("faults.history.range90d")}</option>
            <option value="all">{t("faults.history.rangeAll")}</option>
          </select>
          <select
            value={statusFilter}
            onChange={(e) => setStatusFilter(e.target.value as typeof statusFilter)}
          >
            <option value="all">{t("faults.history.statusAll")}</option>
            <option value="resolved">{t("faults.status.resolved")}</option>
            <option value="closed">{t("faults.status.closed")}</option>
          </select>
          <button
            type="button"
            className="fx-btn fx-btn--ghost"
            onClick={clearFilters}
            disabled={!filtersDirty}
          >
            <FilterX size={15} strokeWidth={2.1} />
            {t("faults.history.clearFilters")}
          </button>
        </div>
      </header>

      {filtered.length === 0 ? (
        <div className="fx-empty">
          <CheckCircle2 size={44} strokeWidth={1.6} />
          <h4>{t("faults.history.emptyHeading")}</h4>
          <p>
            {filtersDirty
              ? t("faults.history.emptyNoMatch")
              : t("faults.history.emptyNone")}
          </p>
        </div>
      ) : (
        <div className="fx-history-table-wrap">
          <table className="fx-history-table">
            <thead>
              <tr>
                <th className="fx-col-no">{t("faults.history.colNo")}</th>
                <th>{t("faults.history.colLine")}</th>
                <th>{t("faults.card.rangeTag")}</th>
                <th>{t("faults.history.colDate")}</th>
                <th>{t("faults.history.colDuration")}</th>
                <th>{t("faults.history.colStatus")}</th>
                <th>{t("faults.card.assignedTo")}</th>
                <th className="fx-col-go" />
              </tr>
            </thead>
            <tbody>
              {filtered.map((f) => (
                <tr
                  key={f.id}
                  className="fx-history-row"
                  onClick={() => onOpenFault(f.id)}
                  title={t("faults.history.openDetail")}
                >
                  <td className="fx-col-no">
                    <span className="fx-fault-no">#{f.id}</span>
                  </td>
                  <td>
                    <strong className="fx-history-line">{f.line_name}</strong>
                    <small className="fx-history-region">{f.region_name}</small>
                  </td>
                  <td className="fx-history-range">
                    {t("faults.card.rangeText", {
                      from: f.from_pole_seq ?? "?",
                      to: f.to_pole_seq ?? "?"
                    })}
                  </td>
                  <td className="fx-history-date">
                    {fmtDateTime(f.closed_at ?? f.resolved_at ?? f.opened_at, localeTag)}
                  </td>
                  <td className="fx-history-duration">{fmtDuration(f)}</td>
                  <td>
                    <span className={`fx-badge fx-badge--status-${f.status}`}>
                      <CheckCircle2 size={12} strokeWidth={2.4} />
                      {t(`faults.status.${f.status}`, { defaultValue: f.status })}
                    </span>
                  </td>
                  <td>
                    {f.assigned_to_full_name ?? f.assigned_to_username ?? (
                      <em className="fx-dim">{t("faults.card.noAssignee")}</em>
                    )}
                  </td>
                  {/* Satirin tamami tiklanabilir; bu hucre yalnizca nereye
                      gidildigini gosteren isaret. */}
                  <td className="fx-col-go">
                    <span className="fx-go" aria-hidden="true">
                      <ChevronRight size={16} strokeWidth={2.2} />
                    </span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}
