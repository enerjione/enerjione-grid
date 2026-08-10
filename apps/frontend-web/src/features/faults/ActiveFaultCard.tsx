/**
 * ActiveFaultCard — "Aktif Arıza" sekmesindeki tek ariza karti.
 *
 * Bir hatta birden fazla bagimsiz ariza bolgesi olabildigi icin (bkz.
 * backend `fault_recompute_service._compute_line_zones`) bu kart her BOLGE
 * icin bir kez render edilir; ayni hattan iki kart yan yana cikabilir.
 * O yuzden basligin yaninda direk araligi da vurgulu gosterilir — iki karti
 * birbirinden ayiran sey odur.
 *
 * DUZEN (yeniden tasarim)
 * -----------------------
 * Onceki surum uc esit sutundu: solda etiket/deger listesi, ortada kucuk bir
 * cizim, sagda butonlar. Cizim dar kaliyor, mesafe ve cihaz bilgisi cizimin
 * ALTINDA ayri kutucuklarda tekrar ediliyordu — operator ayni bilgiyi uc
 * yerde okuyup kafasinda birlestirmek zorundaydi.
 *
 * Simdi bilgi TEK YONDE akiyor:
 *   1. Ust serit  — NEREDE ve NE DURUMDA (hat, aralik, durum, sure)
 *   2. Cizim      — arizanin fiziksel yeri, olcusu, hangi cihazlar arasinda
 *   3. Yan panel  — NEDEN acildi: arizayi doguran ALARMLAR + faz + sinirlar
 *
 * Ikonografi: lucide-react (material-symbols DEGIL) — sematik direk seridi
 * cizgisel oldugu icin ayni gorsel dil.
 */
import { useMemo } from "react";
import { useTranslation } from "react-i18next";
import {
  ChevronRight,
  List,
  MapPin,
  Map as MapIcon,
  Radio,
  Timer,
  TriangleAlert,
  UserPlus,
  User as UserIcon,
  Zap,
  ZapOff
} from "lucide-react";

import type { FaultEvent, FaultTriggerAlarm } from "../../shared/types";
import { formatDistanceM } from "../../shared/lineDistance";
import { FaultPoleStrip } from "./FaultPoleStrip";
import type { StripDeviceAlarms, StripPole, StripSegment } from "./FaultPoleStrip";

type Props = {
  fault: FaultEvent;
  /** Hattin tum direk sira numaralari (sematik serit icin). */
  poleSeqs: number[];
  /** Direk ad/rol bilgisi — etiketlerde sira numarasi yerine AD gosterilir. */
  poles?: StripPole[];
  /** Hattin segmentleri — cihazlari TELIN UZERINDE cizmek icin. */
  segments: StripSegment[];
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

function fmtClock(iso: string | null | undefined, localeTag: string): string {
  if (!iso) return "—";
  return new Date(iso).toLocaleTimeString(localeTag, {
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
  poles,
  segments,
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
  const alarms: FaultTriggerAlarm[] = f.trigger_alarms ?? [];

  // Cihaz koduna gore alarm ozeti — cizimdeki faz noktalari ve tooltip
  // bunu okur. Ayni cihazda birden fazla faz alarmi olabilir.
  /** Direk araligi basligi: direklerin ADI varsa onu kullan, yoksa "#3 — #4".
   *  Saha ekibi direkleri sira numarasiyla degil adiyla taniyor. */
  const rangeText = useMemo(() => {
    const adOf = (seq: number | null | undefined): string | null => {
      if (seq == null) return null;
      const ad = (poles?.find((p) => p.seq === seq)?.name ?? "").trim();
      return ad || null;
    };
    const fromAd = adOf(f.from_pole_seq);
    const toAd = adOf(f.to_pole_seq);
    if (fromAd && toAd) {
      return t("faults.card.rangeTextNamed", { from: fromAd, to: toAd });
    }
    return t("faults.card.rangeText", {
      from: f.from_pole_seq ?? "?",
      to: f.to_pole_seq ?? "?"
    });
  }, [poles, f.from_pole_seq, f.to_pole_seq, t]);

  const alarmsByDevice = useMemo(() => {
    const map: Record<string, StripDeviceAlarms> = {};
    for (const a of alarms) {
      const code = a.device_code;
      if (!code) continue;
      const entry = (map[code] ??= { sources: [], titles: [] });
      if (a.signal_source && !entry.sources.includes(a.signal_source)) {
        entry.sources.push(a.signal_source);
      }
      if (!entry.titles.includes(a.title)) entry.titles.push(a.title);
    }
    return map;
  }, [alarms]);

  return (
    <article className={`fx-card fx-card--${f.status}`}>
      {/* ---------- 1. UST SERIT: nerede, ne durumda ---------- */}
      <header className="fx-head">
        <div className="fx-head-id">
          <div className="fx-head-region">
            <MapPin size={13} strokeWidth={2.3} />
            {f.region_name}
          </div>
          <h3 className="fx-head-line">
            {f.line_name}
            <span className="fx-head-range">
              <ChevronRight size={16} strokeWidth={2.6} />
              {rangeText}
            </span>
          </h3>
        </div>

        <div className="fx-head-facts">
          <div className="fx-fact fx-fact--live">
            <span className="fx-fact-key">
              <Timer size={12} strokeWidth={2.2} />
              {t("faults.card.duration")}
            </span>
            <span className="fx-fact-val">
              <span className="fx-live-dot" aria-hidden="true" />
              {fmtElapsed(f.opened_at, now)}
            </span>
          </div>
          <div className="fx-fact">
            <span className="fx-fact-key">{t("faults.card.openedAt")}</span>
            <span className="fx-fact-val">{fmtDateTime(f.opened_at, localeTag)}</span>
          </div>
          <div className="fx-fact">
            <span className="fx-fact-key">
              <UserIcon size={12} strokeWidth={2.2} />
              {t("faults.card.assignedTo")}
            </span>
            <span className="fx-fact-val">
              {assignee ?? <em className="fx-dim">{t("faults.card.noAssignee")}</em>}
            </span>
          </div>
        </div>

        <div className="fx-head-badges">
          <span className={`fx-badge fx-badge--status-${f.status}`}>
            {t(`faults.status.${f.status}`, { defaultValue: f.status })}
          </span>
          {hasLocation ? (
            <span className="fx-badge fx-badge--located">
              <MapPin size={11} strokeWidth={2.4} />
              {t("faults.card.locationFound")}
            </span>
          ) : null}
        </div>

        <div className="fx-head-actions">
          {canAssign ? (
            <button type="button" className="fx-btn fx-btn--primary" onClick={onAssignClick}>
              <UserPlus size={15} strokeWidth={2.1} />
              {t("faults.card.assignAction")}
            </button>
          ) : null}
          <button type="button" className="fx-btn" onClick={onOpenDetail}>
            <List size={15} strokeWidth={2.1} />
            {t("faults.card.detailAction")}
          </button>
          <button type="button" className="fx-btn" onClick={onShowOnMap}>
            <MapIcon size={15} strokeWidth={2.1} />
            {t("faults.card.mapAction")}
          </button>
        </div>
      </header>

      {/* ---------- 2. CIZIM + 3. KANIT PANELI ---------- */}
      <div className="fx-body">
        <section className="fx-zone">
          <div className="fx-zone-head">
            <span className="fx-zone-title">{t("faults.card.zoneTitle")}</span>
            {f.zone_length_m != null ? (
              <span className="fx-zone-span">
                {t("faults.card.uncertainty", {
                  span: formatDistanceM(f.zone_length_m)
                })}
              </span>
            ) : null}
          </div>
          <FaultPoleStrip
            poleSeqs={poleSeqs}
            poles={poles}
            segments={segments}
            fromSeq={f.from_pole_seq}
            toSeq={f.to_pole_seq}
            lastRedDeviceCode={f.last_red_device_code}
            firstGreenDeviceCode={f.first_green_device_code}
            zoneStartM={f.zone_start_m}
            zoneEndM={f.zone_end_m}
            alarmsByDevice={alarmsByDevice}
            active
          />
        </section>

        <aside className="fx-evidence">
          {/* --- arizayi acan alarmlar --- */}
          <div className="fx-ev-block">
            <h4 className="fx-ev-title">
              <TriangleAlert size={13} strokeWidth={2.3} />
              {t("faults.card.causeTitle")}
            </h4>
            {alarms.length === 0 ? (
              <p className="fx-ev-empty">{t("faults.card.causeEmpty")}</p>
            ) : (
              <ul className="fx-alarm-list">
                {alarms.slice(0, 4).map((a) => (
                  <li key={a.id} className={`fx-alarm fx-alarm--${a.level}`}>
                    <span className="fx-alarm-top">
                      <strong className="fx-alarm-title">{a.title}</strong>
                      {a.signal_source ? (
                        <span className={`fx-phase fx-phase--${a.signal_source}`}>
                          <Radio size={10} strokeWidth={2.6} />
                          {t(`faults.phase.${a.signal_source}`, {
                            defaultValue: a.signal_source
                          })}
                        </span>
                      ) : null}
                    </span>
                    <span className="fx-alarm-sub">
                      {a.device_name ?? a.device_code ?? "—"}
                      <span className="fx-alarm-dot">·</span>
                      {fmtClock(a.created_at, localeTag)}
                      {a.acknowledged ? (
                        <>
                          <span className="fx-alarm-dot">·</span>
                          {t("faults.card.alarmAcked")}
                        </>
                      ) : null}
                    </span>
                  </li>
                ))}
                {alarms.length > 4 ? (
                  <li className="fx-alarm-more">
                    {t("faults.card.alarmMore", { count: alarms.length - 4 })}
                  </li>
                ) : null}
              </ul>
            )}
          </div>

          {/* --- ariza bolgesinin sinirlari --- */}
          <div className="fx-ev-block">
            <h4 className="fx-ev-title">{t("faults.card.boundsTitle")}</h4>
            <div className="fx-bound fx-bound--red">
              <Zap size={14} strokeWidth={2.3} />
              <span>
                <small>{t("faults.card.lastRedLabel")}</small>
                <strong>{f.last_red_device_name ?? f.last_red_device_code ?? "—"}</strong>
                {f.zone_length_m != null ? (
                  <em>
                    {t("faults.card.distanceAhead", {
                      span: formatDistanceM(f.zone_length_m)
                    })}
                  </em>
                ) : null}
              </span>
            </div>
            <div className="fx-bound fx-bound--green">
              <ZapOff size={14} strokeWidth={2.3} />
              <span>
                <small>{t("faults.card.firstGreenLabel")}</small>
                <strong>
                  {f.first_green_device_name ??
                    f.first_green_device_code ??
                    t("faults.card.lineEnd")}
                </strong>
              </span>
            </div>
          </div>
        </aside>
      </div>
    </article>
  );
}
