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
import { useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import {
  Activity,
  ChevronRight,
  GitBranch,
  History,
  List,
  MapPin,
  Radio,
  Timer,
  TriangleAlert,
  UserPlus,
  User as UserIcon
} from "lucide-react";

import type { FaultEvent, FaultTriggerAlarm } from "../../shared/types";
import { formatDistanceM } from "../../shared/lineDistance";
import type { FaultRecurrence } from "./faultRecurrence";
import { FaultPoleStrip } from "./FaultPoleStrip";
import type {
  StripBranchRow,
  StripDeviceAlarms,
  StripPole,
  StripSegment
} from "./FaultPoleStrip";

type Props = {
  fault: FaultEvent;
  /** Hattin tum direk sira numaralari (sematik serit icin). */
  poleSeqs: number[];
  /** Direk ad/rol bilgisi — etiketlerde sira numarasi yerine AD gosterilir. */
  poles?: StripPole[];
  /** Ariza bolgesine denk gelen ADAY hat kesimleri (bransman kollari).
   *  Cizimde her biri AYRI BIR SATIR olarak tam hat halinde gosterilir. */
  branchRows?: StripBranchRow[];
  /** Sigmadigi icin cizilemeyen kol sayisi — cizimde "+N" notu. */
  hiddenBranchCount?: number;
  /** Hattin segmentleri — cihazlari TELIN UZERINDE cizmek icin. */
  segments: StripSegment[];
  localeTag: string;
  /** Canli sure sayaci icin ortak "now" (parent 30sn'de bir gunceller). */
  now: number;
  canAssign: boolean;
  /** Sebep etiketi (katalogdan cozulmus). `suggested` = cihaz onerisi,
   *  insan henuz onaylamadi. */
  cause?: { label: string; suggested: boolean } | null;
  /** AYNI HATTA gecmis arizalar — tekrar eden ariza baska bir istir. */
  history?: FaultRecurrence | null;
  /** Sebep katalogu — kartta SEBEP SECILEBILSIN diye. */
  causeOptions?: { code: string; label: string; group: string }[];
  /** Sebep secildiginde kaydet. Verilmezse alan salt okunur kalir. */
  onSaveCause?: (code: string | null) => void | Promise<void>;
  /** Cizimdeki cihaza tiklaninca cihaz sayfasini ac. */
  onOpenDevice?: (code: string) => void;
  onOpenDetail: () => void;
  onAssignClick: () => void;
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
  branchRows,
  hiddenBranchCount = 0,
  segments,
  localeTag,
  now,
  canAssign,
  cause,
  history,
  causeOptions,
  onSaveCause,
  onOpenDevice,
  onOpenDetail,
  onAssignClick
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

  /** ARIZALI FAZLAR — cizimde yalnizca bu fazlarin teli kirmizi cizilir.
   *
   *  Bir SN2 govdesindeki uc sensor hattin uc ayri fazina kelepcelenir;
   *  alarm hangi kaynaktan geldiyse ariza o fazdadir. Onceki cizim uc telin
   *  hepsini kirmiziya boyuyordu: "uc faz birden arizali" demek, tek fazli
   *  bir ariza icin YANLIS bir ifade ve ekibi gereksiz genis bir kontrole
   *  gonderiyor. Liste bos kalirsa (eski kayit) uc tel de vurgulanir —
   *  "bilmiyorum"u tek bir faza indirgemek daha kotu olurdu. */
  const faultPhases = useMemo(() => {
    const set = new Set<string>();
    for (const a of alarms) {
      if (a.signal_source) set.add(a.signal_source);
    }
    return Array.from(set);
  }, [alarms]);

  /** ARIZA KUNYESI — yalnizca DOLU alanlar.
   *
   *  Bos alan satiri hic cizilmez: "—" ile dolu bir tablo bilgi tasimadigi
   *  gibi, gercekten bilinen iki degeri de gorunmez kilar. Cihaz analiz
   *  alanlarini (tur/faz/yon/akim) hic doldurmadiysa bolum "veri gelmedi"
   *  der — bos birakip "sorun yok" izlenimi vermez. */
  const spec = useMemo(() => {
    const rows: { key: string; label: string; value: string; tone?: "red" | "green" }[] = [];
    const ekle = (
      key: string,
      label: string,
      value: string | null | undefined,
      tone?: "red" | "green"
    ) => {
      if (value == null || value === "") return;
      rows.push({ key, label, value, tone });
    };

    if (f.fault_kind) {
      ekle(
        "kind",
        t("faults.card.specKind"),
        t(`faults.card.kind.${f.fault_kind}`, { defaultValue: f.fault_kind }),
        f.fault_kind === "permanent" ? "red" : undefined
      );
    }
    if (f.phase) {
      // "abc" -> "A-B-C". Backend fazlari harf harf ve sirali yazar.
      ekle("phase", t("faults.card.specPhase"), f.phase.toUpperCase().split("").join("-"), "red");
    }
    // YON GOSTERILMIYOR (kullanici karari): cihazin "ileri/geri" bayragi
    // kelepcenin takilis yonune gore anlam degistiriyor ve sahada yanlis
    // tarafa yonlendirebiliyordu. Alan DB'de duruyor (analiz/rapor okuyabilir),
    // yalnizca kunyede yazilmiyor.
    if (f.fault_current_a != null) {
      ekle("ia", t("faults.card.specFaultCurrent"), `${f.fault_current_a.toFixed(1)} A`, "red");
    }
    if (f.load_current_before_a != null) {
      ekle("il", t("faults.card.specLoadCurrent"), `${f.load_current_before_a.toFixed(1)} A`);
    }
    if (f.conductor_temp_c != null) {
      ekle("temp", t("faults.card.specTemp"), `${f.conductor_temp_c.toFixed(0)} °C`);
    }
    // Bolgeyi ceviren iki cihaz: cizimde kirmizi/yesil olarak duruyor ama
    // ADI yalnizca burada yaziyor — telsizle "hangi cihaz" diye sorulur.
    ekle(
      "red",
      t("faults.card.specLastRed"),
      f.last_red_device_name ?? f.last_red_device_code ?? null,
      "red"
    );
    ekle(
      "green",
      t("faults.card.specFirstGreen"),
      f.first_green_device_name ?? f.first_green_device_code ?? t("faults.card.lineEnd"),
      "green"
    );
    if (f.zone_length_m != null) {
      ekle("span", t("faults.card.specSearchSpan"), formatDistanceM(f.zone_length_m));
    }
    // ARALIK KODU — ariza bir HATTIN degil, iki cihaz arasindaki ARALIGIN
    // olayidir. Telsizde/raporda "hangi ariza" sorusunun kisa cevabi budur
    // ve tekrar sayimi ile risk puani da bu kodla tutulur. Araya cihaz
    // eklenirse kod degisir: artik baska bir aralik konusuluyor.
    ekle("zone", t("faults.card.specZoneCode"), f.zone_code ?? null);
    if (f.measured_at) {
      ekle("at", t("faults.card.specMeasuredAt"), fmtClock(f.measured_at, localeTag));
    }
    return rows;
  }, [f, cause, t, localeTag]);

  const [causeSaving, setCauseSaving] = useState(false);
  const handleCause = async (code: string) => {
    if (!onSaveCause) return;
    setCauseSaving(true);
    try {
      await onSaveCause(code || null);
    } finally {
      setCauseSaving(false);
    }
  };

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
            {/* Kayit bir bransman kolundaysa hangi ana hattan ciktigi
                basliktan okunmali; yoksa "BR-4" tek basina nereye ait
                oldugunu soylemiyor. */}
            {f.is_branch_line && f.parent_line_name ? (
              <span className="fx-head-parent">
                {f.parent_line_name}
                <ChevronRight size={13} strokeWidth={2.6} />
              </span>
            ) : null}
            {f.line_name}
            <span className="fx-head-range">
              <ChevronRight size={16} strokeWidth={2.6} />
              {rangeText}
            </span>
          </h3>

          {/* DURUM SERIDI — kartin en cok bakilan satiri.
              Onceden durum rozeti sagda, butonlarin arasinda kucuk bir
              etiketti; "bu ariza acik mi, atandi mi, ne kadardir suruyor"
              sorusu ekranin uc ayri yerinden toplanmak zorundaydi. Artik
              hat adinin hemen altinda, tek bakista okunan bir serit. */}
          <div className="fx-head-state">
            <span className={`fx-state fx-state--${f.status}`}>
              <span className="fx-state-dot" aria-hidden="true" />
              {t(`faults.status.${f.status}`, { defaultValue: f.status })}
            </span>
            <span className="fx-state-time">
              <Timer size={13} strokeWidth={2.3} />
              <strong>{fmtElapsed(f.opened_at, now)}</strong>
              {t("faults.card.stateElapsed")}
            </span>
            {hasLocation ? (
              <span className="fx-state-tag">
                <MapPin size={11} strokeWidth={2.4} />
                {t("faults.card.locationFound")}
              </span>
            ) : (
              <span className="fx-state-tag fx-state-tag--warn">
                {t("faults.card.locationUnknown")}
              </span>
            )}
            {/* Ayni hat daha once de arizalandiysa bu bir tekrardir ve
                mudahalenin onceligini degistirir — basligin yaninda durur. */}
            {history && history.total > 0 ? (
              <span className="fx-state-tag fx-state-tag--repeat">
                <History size={11} strokeWidth={2.4} />
                {t("faults.card.repeatTag", { count: history.total + 1 })}
              </span>
            ) : null}
          </div>
        </div>

        <div className="fx-head-facts">
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

        {/* "Haritada Gor" KALDIRILDI: ayni modali aciyordu, yani ucuncu bir
            buton olarak yer kapliyor ama yeni bir sey yapmiyordu. Harita
            zaten Detay ekraninda. */}
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
        </div>
      </header>

      {/* ---------- 2. CIZIM + 3. KANIT PANELI ---------- */}
      <div className="fx-body">
        <section className="fx-zone">
          <div className="fx-zone-head">
            <span className="fx-zone-title">{t("faults.card.zoneTitle")}</span>
            {/* Ariza tek bir hat kesiminde olmayabilir: bolge bir dallanma
                diregini kapsiyorsa kol da adaydir. Kac aday oldugu cizime
                bakmadan once soylenir — ekip kac yer gezecegini bilsin. */}
            {(branchRows?.length ?? 0) > 0 ? (
              <span className="fx-zone-candidates">
                {t("faults.card.candidateSections", {
                  count: (branchRows?.length ?? 0) + 1
                })}
              </span>
            ) : null}
            {f.zone_length_m != null ? (
              <span className="fx-zone-span">
                {t("faults.card.uncertainty", {
                  span: formatDistanceM(f.zone_length_m)
                })}
              </span>
            ) : null}
          </div>
          <FaultPoleStrip
            lineName={f.line_name}
            poleSeqs={poleSeqs}
            poles={poles}
            branchRows={branchRows}
            hiddenBranchCount={hiddenBranchCount}
            segments={segments}
            fromSeq={f.from_pole_seq}
            toSeq={f.to_pole_seq}
            lastRedDeviceCode={f.last_red_device_code}
            firstGreenDeviceCode={f.first_green_device_code}
            zoneStartM={f.zone_start_m}
            zoneEndM={f.zone_end_m}
            alarmsByDevice={alarmsByDevice}
            faultPhases={faultPhases}
            onOpenDevice={onOpenDevice}
            active
          />
        </section>

        <aside className="fx-evidence">
          {/* --- 1. ARIZA KUNYESI ---
              Panel once "alarm + kollar + sinirlar" seklinde uc ayri kutuydu
              ve arizanin KENDISI hakkinda tek bir sey yazmiyordu: turu,
              fazi, akimi, yonu cihazdan geliyor ama hicbiri gorunmuyordu.
              Kunye bunlari tek bir okunur listede toplar; olmayan satir hic
              cizilmez — "—" ile dolu bir tablo bilgi tasimaz. */}
          <div className="fx-ev-block">
            <h4 className="fx-ev-title">
              <Activity size={13} strokeWidth={2.3} />
              {t("faults.card.specTitle")}
            </h4>
            {spec.length === 0 ? (
              <p className="fx-ev-empty">{t("faults.card.specEmpty")}</p>
            ) : (
              <dl className="fx-spec">
                {spec.map((row) => (
                  <div key={row.key} className="fx-spec-row">
                    <dt>{row.label}</dt>
                    <dd className={row.tone ? `fx-spec-val--${row.tone}` : undefined}>
                      {row.value}
                    </dd>
                  </div>
                ))}
              </dl>
            )}

            {/* SEBEP — SALT OKUNUR DEGIL.
                Kart, cihaz verisinden turetilen oneriyi ("Agac / dal temasi")
                gosteriyor ama degistirilemiyordu; sahada gercek sebebi goren
                kisi onu girmek icin detay ekranini acmak zorundaydi. Sebep
                analiz katmanina giren TEK insan etiketi oldugu icin girisi
                zorlastirmak dogrudan veri kaybi demek. */}
            <div className="fx-cause">
              <span className="fx-cause-label">{t("faults.card.specCause")}</span>
              {causeOptions && causeOptions.length > 0 && onSaveCause ? (
                <select
                  className="fx-cause-select"
                  value={f.cause_code ?? ""}
                  disabled={causeSaving}
                  onChange={(e) => void handleCause(e.target.value)}
                >
                  <option value="">{t("faults.card.causeNotSet")}</option>
                  {Array.from(new Set(causeOptions.map((c) => c.group))).map((grup) => (
                    <optgroup key={grup} label={t(`faults.causeGroup.${grup}`, { defaultValue: grup })}>
                      {causeOptions
                        .filter((c) => c.group === grup)
                        .map((c) => (
                          <option key={c.code} value={c.code}>
                            {c.label}
                          </option>
                        ))}
                    </optgroup>
                  ))}
                </select>
              ) : (
                <span className="fx-cause-plain">{cause?.label ?? "—"}</span>
              )}
              {/* Cihazin onerisi, insan henuz onaylamadiysa. Oneriyi tek
                  tikla kabul etmek, secim listesini acmaktan hizli. */}
              {!f.cause_code && cause?.suggested ? (
                <button
                  type="button"
                  className="fx-cause-suggest"
                  disabled={causeSaving || !onSaveCause}
                  onClick={() => void handleCause(f.auto_cause_code ?? "")}
                >
                  {t("faults.card.causeUseSuggestion", { cause: cause.label })}
                </button>
              ) : null}
            </div>
          </div>

          {/* --- 2. arizayi acan alarmlar --- */}
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

          {/* --- 3. BU HAT DAHA ONCE DE ARIZALANDI MI ---
              Tekrar eden ariza baska bir istir: gecici bir olay degil,
              cozulmemis bir kok sebep vardir (agac, izolator, kacak). Ekip
              sahaya giderken bunu bilmeli. */}
          {history ? (
            <div className="fx-ev-block">
              <h4 className="fx-ev-title">
                <History size={13} strokeWidth={2.3} />
                {t("faults.card.historyTitle")}
              </h4>
              {history.total === 0 ? (
                <p className="fx-ev-empty">{t("faults.card.historyNone")}</p>
              ) : (
                <div className="fx-repeat">
                  <div className="fx-repeat-head">
                    <strong>{history.total}</strong>
                    <span>
                      {t("faults.card.historyCount", { days: history.windowDays })}
                    </span>
                  </div>
                  <ul className="fx-repeat-list">
                    {history.sameSection > 0 ? (
                      <li className="fx-repeat-hit">
                        {t("faults.card.historySameSection", {
                          count: history.sameSection
                        })}
                      </li>
                    ) : null}
                    {history.lastAt ? (
                      <li>
                        {t("faults.card.historyLast", {
                          at: fmtDateTime(history.lastAt, localeTag)
                        })}
                      </li>
                    ) : null}
                  </ul>
                </div>
              )}
            </div>
          ) : null}

          {/* --- 4. ariza araliginda kalan bransman kollari ---
              Ana hattaki ariza bir dallanma diregini kapsiyorsa o kol da
              enerjisiz kalir; ekip sahaya ciktiginda kolu da kontrol
              etmelidir. Bu bilgi hicbir yerde gorunmuyordu. */}
          {/* KAYNAK: cizimin kendi aday listesi (`branchRows`), backend'in
              `affected_branches` alani DEGIL. Iki liste ayri hesaplardan
              geliyordu ve ayrisiyorlardi: cizimde olmayan bir kol panelde
              "kontrol edin" diye duruyordu. Panel ile cizim artik tanim
              geregi ayni seyi soyler; ic ice kollar da listeye girer. */}
          {(branchRows?.length ?? 0) > 0 ? (
            <div className="fx-ev-block">
              <h4 className="fx-ev-title">
                <GitBranch size={13} strokeWidth={2.3} />
                {t("faults.card.branchesTitle")}
              </h4>
              <ul className="fx-branch-list">
                {branchRows!.map((b) => (
                  <li
                    key={b.lineId}
                    className={`fx-branch${b.confirmed ? " is-confirmed" : ""}`}
                  >
                    <strong>{b.name}</strong>
                    <small>
                      {t("faults.card.branchAt", {
                        pole: b.atPoleName || `#${b.atSeq}`
                      })}
                    </small>
                    {b.confirmed ? (
                      <span className="fx-branch-tag">
                        {t("faults.card.branchConfirmed")}
                      </span>
                    ) : (
                      <span className="fx-branch-tag fx-branch-tag--check">
                        {t("faults.card.branchCheck")}
                      </span>
                    )}
                  </li>
                ))}
              </ul>
            </div>
          ) : null}

          {/* ARIZA BOLGESI SINIRLARI ayri bir kutu DEGIL artik: iki satirlik
              bir bilgi icin baslikli bir bolum acmak paneli sisiriyordu ve
              ayni bilgi cizimde zaten kirmizi/yesil cihaz olarak duruyor.
              Cihaz adlari kunyeye iki satir olarak tasindi. */}
        </aside>
      </div>
    </article>
  );
}
