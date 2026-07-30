/**
 * ActiveFaultCard — "Aktif Arıza" sekmesindeki tek ariza karti.
 *
 * Bir hatta birden fazla bagimsiz ariza bolgesi olabildigi icin (bkz.
 * backend `fault_recompute_service._compute_line_zones`) bu kart her BOLGE
 * icin bir kez render edilir; ayni hattan iki kart yan yana cikabilir.
 * O yuzden basligin altinda hat adinin yaninda direk araligi da vurgulu
 * gosterilir — iki karti birbirinden ayiran sey odur.
 *
 * Ikonografi: lucide-react (material-symbols DEGIL) — sematik direk seridi
 * cizgisel oldugu icin ayni gorsel dil.
 */
import { useTranslation } from "react-i18next";
import {
  CalendarDays,
  CircleDot,
  List,
  MapPin,
  Map as MapIcon,
  Ruler,
  Timer,
  UserPlus,
  User as UserIcon,
  Zap,
  ZapOff
} from "lucide-react";

import type { FaultEvent } from "../../shared/types";
import { formatDistanceM, formatDistanceRange } from "../../shared/lineDistance";
import { FaultPoleStrip } from "./FaultPoleStrip";

type Props = {
  fault: FaultEvent;
  /** Hattin tum direk sira numaralari (sematik serit icin). */
  poleSeqs: number[];
  localeTag: string;
  /** Canli sure sayaci icin ortak "now" (parent 30sn'de bir gunceller). */
  now: number;
  canAssign: boolean;
  onOpenDetail: () => void;
  onAssignClick: () => void;
  onShowOnMap: () => void;
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

function fmtElapsed(fromIso: string, endMs: number): string {
  let sec = Math.max(0, Math.round((endMs - new Date(fromIso).getTime()) / 1000));
  const days = Math.floor(sec / 86400);
  sec -= days * 86400;
  const hours = Math.floor(sec / 3600);
  sec -= hours * 3600;
  const mins = Math.floor(sec / 60);
  if (days > 0) return `${days}g ${hours}sa`;
  if (hours > 0) return `${hours}sa ${mins}dk`;
  if (mins > 0) return `${mins}dk`;
  return "<1dk";
}

export function ActiveFaultCard({
  fault: f,
  poleSeqs,
  localeTag,
  now,
  canAssign,
  onOpenDetail,
  onAssignClick,
  onShowOnMap
}: Props) {
  const { t } = useTranslation();
  const assignee = f.assigned_to_full_name ?? f.assigned_to_username ?? null;
  const hasLocation = f.from_pole_seq != null && f.to_pole_seq != null;
  // Tel mesafesi backend'de hesaplanip kayda yazilir (bkz.
  // line_distance_service). Eski kayitlarda / koordinatsiz topolojide NULL
  // gelebilir — o durumda mesafe blogu hic cizilmez.
  const distanceRange = formatDistanceRange(f.zone_start_m, f.zone_end_m);

  return (
    <article className={`fx-card fx-card--${f.status}`}>
      {/* ---- SOL: kimlik ---- */}
      <div className="fx-card-ident">
        <div className="fx-card-region">
          <MapPin size={14} strokeWidth={2.2} />
          {f.region_name}
        </div>
        <h3 className="fx-card-line">{f.line_name}</h3>

        <span className="fx-card-range-label">{t("faults.card.rangeTag")}</span>
        <div className="fx-card-range">
          {t("faults.card.rangeText", {
            from: f.from_pole_seq ?? "?",
            to: f.to_pole_seq ?? "?"
          })}
        </div>

        {distanceRange ? (
          <div className="fx-card-distance">
            <span className="fx-card-distance-head">
              <Ruler size={13} strokeWidth={2.2} />
              {t("faults.card.distanceTag")}
            </span>
            <strong className="fx-card-distance-value">{distanceRange}</strong>
            <small className="fx-card-distance-hint">
              {t("faults.card.distanceFromStart")}
              {f.zone_length_m != null
                ? ` · ${t("faults.card.distanceSpan", {
                    span: formatDistanceM(f.zone_length_m)
                  })}`
                : ""}
            </small>
          </div>
        ) : null}

        <div className="fx-card-badges">
          <span className={`fx-badge fx-badge--status-${f.status}`}>
            <CircleDot size={12} strokeWidth={2.6} />
            {t(`faults.status.${f.status}`, { defaultValue: f.status })}
          </span>
          {hasLocation ? (
            <span className="fx-badge fx-badge--located">
              <MapPin size={12} strokeWidth={2.4} />
              {t("faults.card.locationFound")}
            </span>
          ) : null}
        </div>

        <dl className="fx-card-meta">
          <div>
            <dt>
              <CalendarDays size={13} strokeWidth={2} />
              {t("faults.card.openedAt")}
            </dt>
            <dd>{fmtDateTime(f.opened_at, localeTag)}</dd>
          </div>
          <div>
            <dt>
              <Timer size={13} strokeWidth={2} />
              {t("faults.card.duration")}
            </dt>
            <dd className="fx-card-meta-live">
              <span className="fx-live-dot" aria-hidden="true" />
              {fmtElapsed(f.opened_at, now)}
            </dd>
          </div>
          <div>
            <dt>
              <UserIcon size={13} strokeWidth={2} />
              {t("faults.card.assignedTo")}
            </dt>
            <dd>
              {assignee ?? (
                <em className="fx-dim">{t("faults.card.noAssignee")}</em>
              )}
            </dd>
          </div>
        </dl>
      </div>

      {/* ---- ORTA: sematik ariza bolgesi + cihazlar ---- */}
      <div className="fx-card-zone">
        <div className="fx-card-zone-title">{t("faults.card.zoneTitle")}</div>
        <FaultPoleStrip
          poleSeqs={poleSeqs}
          fromSeq={f.from_pole_seq}
          toSeq={f.to_pole_seq}
          active
        />
        <div className="fx-card-devices">
          <div className="fx-dev fx-dev--red">
            <span className="fx-dev-icon">
              <Zap size={16} strokeWidth={2.2} />
            </span>
            <span className="fx-dev-text">
              <small>{t("faults.card.lastRedLabel")}</small>
              <strong>{f.last_red_device_name ?? f.last_red_device_code ?? "—"}</strong>
              {f.last_red_device_code ? <code>{f.last_red_device_code}</code> : null}
              {/* Bu cihaz arizanin YUKARI sinirindir: ariza cihazdan sonraki
                  0..zone_length_m araliginda bir yerdedir. */}
              {f.zone_length_m != null ? (
                <em className="fx-dev-dist">
                  {t("faults.card.distanceAhead", {
                    span: formatDistanceM(f.zone_length_m)
                  })}
                </em>
              ) : null}
            </span>
          </div>
          <div className="fx-dev fx-dev--green">
            <span className="fx-dev-icon">
              <ZapOff size={16} strokeWidth={2.2} />
            </span>
            <span className="fx-dev-text">
              <small>{t("faults.card.firstGreenLabel")}</small>
              <strong>
                {f.first_green_device_name ??
                  f.first_green_device_code ??
                  t("faults.card.lineEnd")}
              </strong>
              {f.first_green_device_code ? <code>{f.first_green_device_code}</code> : null}
            </span>
          </div>
        </div>
      </div>

      {/* ---- SAG: aksiyonlar ---- */}
      <div className="fx-card-actions">
        {canAssign ? (
          <button type="button" className="fx-btn fx-btn--primary" onClick={onAssignClick}>
            <UserPlus size={16} strokeWidth={2.1} />
            {t("faults.card.assignAction")}
          </button>
        ) : null}
        <button type="button" className="fx-btn" onClick={onOpenDetail}>
          <List size={16} strokeWidth={2.1} />
          {t("faults.card.detailAction")}
        </button>
        <button type="button" className="fx-btn" onClick={onShowOnMap}>
          <MapIcon size={16} strokeWidth={2.1} />
          {t("faults.card.mapAction")}
        </button>
      </div>
    </article>
  );
}
