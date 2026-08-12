/**
 * ActiveFaultCard â€” "Aktif ArÄ±za" sekmesindeki tek ariza karti.
 *
 * Bir hatta birden fazla bagimsiz ariza bolgesi olabildigi icin (bkz.
 * backend `fault_recompute_service._compute_line_zones`) bu kart her BOLGE
 * icin bir kez render edilir; ayni hattan iki kart yan yana cikabilir.
 * O yuzden basligin yaninda direk araligi da vurgulu gosterilir â€” iki karti
 * birbirinden ayiran sey odur.
 *
 * DUZEN (yeniden tasarim)
 * -----------------------
 * Onceki surum uc esit sutundu: solda etiket/deger listesi, ortada kucuk bir
 * cizim, sagda butonlar. Cizim dar kaliyor, mesafe ve cihaz bilgisi cizimin
 * ALTINDA ayri kutucuklarda tekrar ediliyordu â€” operator ayni bilgiyi uc
 * yerde okuyup kafasinda birlestirmek zorundaydi.
 *
 * Simdi bilgi TEK YONDE akiyor:
 *   1. Ust serit  â€” NEREDE ve NE DURUMDA (hat, aralik, durum, sure)
 *   2. Cizim      â€” arizanin fiziksel yeri, olcusu, hangi cihazlar arasinda
 *   3. Yan panel  â€” NEDEN acildi: arizayi doguran ALARMLAR + faz + sinirlar
 *
 * Ikonografi: lucide-react (material-symbols DEGIL) â€” sematik direk seridi
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
  User as UserIcon,
  UsersRound
} from "lucide-react";

import type { FaultEvent, FaultTriggerAlarm } from "../../shared/types";
import { formatDistanceM } from "../../shared/lineDistance";
import type { FaultRecurrence } from "./faultRecurrence";
import { bashafler } from "./FaultFieldReportModal";
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
  /** Direk ad/rol bilgisi â€” etiketlerde sira numarasi yerine AD gosterilir. */
  poles?: StripPole[];
  /** Ariza bolgesine denk gelen ADAY hat kesimleri (bransman kollari).
   *  Cizimde her biri AYRI BIR SATIR olarak tam hat halinde gosterilir. */
  branchRows?: StripBranchRow[];
  /** Sigmadigi icin cizilemeyen kol sayisi â€” cizimde "+N" notu. */
  hiddenBranchCount?: number;
  /** Hattin segmentleri â€” cihazlari TELIN UZERINDE cizmek icin. */
  segments: StripSegment[];
  localeTag: string;
  /** Canli sure sayaci icin ortak "now" (parent 30sn'de bir gunceller). */
  now: number;
  canAssign: boolean;
  /** Sebep etiketi (katalogdan cozulmus). `suggested` = cihaz onerisi,
   *  insan henuz onaylamadi. */
  cause?: { label: string; suggested: boolean } | null;
  /** AYNI HATTA gecmis arizalar â€” tekrar eden ariza baska bir istir. */
  history?: FaultRecurrence | null;
  /** Sebep katalogu â€” kartta SEBEP SECILEBILSIN diye. */
  causeOptions?: { code: string; label: string; group: string }[];
  /** Sebep secildiginde kaydet. Verilmezse alan salt okunur kalir. */
  onSaveCause?: (code: string | null) => void | Promise<void>;
  /** Cizimdeki cihaza tiklaninca cihaz sayfasini ac. */
  onOpenDevice?: (code: string) => void;
  onOpenDetail: () => void;
  onAssignClick: () => void;
};

function fmtDateTime(iso: string | null | undefined, localeTag: string): string {
  if (!iso) return "â€”";
  return new Date(iso).toLocaleString(localeTag, {
    day: "2-digit",
    month: "2-digit",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit"
  });
}

function fmtClock(iso: string | null | undefined, localeTag: string): string {
  if (!iso) return "â€”";
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
  // EKIP ATAMASI kisi atamasindan ONCE okunur: ikisi ayni anda dolu olmaz
  // (backend 400 doner) ama eski bir kayit iki alani da tasiyorsa ekip
  // kazanir — sorumluluk kurumsaldir, kisi degisebilir.
  const ekipAdi = f.assigned_to_area_name ?? null;
  const assignee = ekipAdi ?? f.assigned_to_full_name ?? f.assigned_to_username ?? null;
  const alarms: FaultTriggerAlarm[] = f.trigger_alarms ?? [];

  /** SAYAC ARIZA NORMALE DONUNCE DURUR.
   *
   *  Onceden sure her zaman `now`a kadar sayiyordu: ariza saat 14:01'de
   *  acilip 14:07'de normale donmus olsa bile kart "6dk suredir acik"
   *  demeye devam ediyor, ertesi gun ayni kayda bakan kisi "1g 3sa
   *  suredir acik" goruyordu. Sahada duzelmis bir arizanin hala aciyor
   *  gorunmesi mudahale onceligini yanlis gosterir.
   *
   *  Bitis: `resolved_at` (cihaz alarmi kalkinca yazar). Kayit kapatilmis
   *  ama nedense `resolved_at` bossa `closed_at`e duseriz — sonucta
   *  sayilan sey ARIZANIN SURDUGU zamandir, ekranin acik kaldigi degil. */
  const bitisMs = useMemo(() => {
    const iso = f.resolved_at ?? f.closed_at ?? null;
    if (!iso) return null;
    const ms = new Date(iso).getTime();
    return Number.isFinite(ms) ? ms : null;
  }, [f.resolved_at, f.closed_at]);
  const sureDurdu = bitisMs != null;
  const sureText = fmtElapsed(f.opened_at, bitisMs ?? now);

  // Cihaz koduna gore alarm ozeti â€” cizimdeki faz noktalari ve tooltip
  // bunu okur. Ayni cihazda birden fazla faz alarmi olabilir.
  /** Direk araligi basligi: direklerin ADI varsa onu kullan, yoksa "#3 â€” #4".
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

  /** ARIZALI FAZLAR â€” cizimde yalnizca bu fazlarin teli kirmizi cizilir.
   *
   *  Bir SN2 govdesindeki uc sensor hattin uc ayri fazina kelepcelenir;
   *  alarm hangi kaynaktan geldiyse ariza o fazdadir. Onceki cizim uc telin
   *  hepsini kirmiziya boyuyordu: "uc faz birden arizali" demek, tek fazli
   *  bir ariza icin YANLIS bir ifade ve ekibi gereksiz genis bir kontrole
   *  gonderiyor. Liste bos kalirsa (eski kayit) uc tel de vurgulanir â€”
   *  "bilmiyorum"u tek bir faza indirgemek daha kotu olurdu. */
  const faultPhases = useMemo(() => {
    const set = new Set<string>();
    for (const a of alarms) {
      if (a.signal_source) set.add(a.signal_source);
    }
    return Array.from(set);
  }, [alarms]);

  /** ARIZA KUNYESI â€” yalnizca DOLU alanlar.
   *
   *  Bos alan satiri hic cizilmez: "â€”" ile dolu bir tablo bilgi tasimadigi
   *  gibi, gercekten bilinen iki degeri de gorunmez kilar. Cihaz analiz
   *  alanlarini (tur/faz/yon/akim) hic doldurmadiysa bolum "veri gelmedi"
   *  der â€” bos birakip "sorun yok" izlenimi vermez. */
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
      ekle("temp", t("faults.card.specTemp"), `${f.conductor_temp_c.toFixed(0)} Â°C`);
    }
    // Bolgeyi ceviren iki cihaz: cizimde kirmizi/yesil olarak duruyor ama
    // ADI yalnizca burada yaziyor â€” telsizle "hangi cihaz" diye sorulur.
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
    // ARALIK KODU KUNYEDE YAZILMIYOR (kullanici karari): "L13/D21>D10" bir
    // ic anahtar; sahadaki kisiye bir sey soylemiyor, ustelik ayni bilgi
    // baslikta okunur haliyle ("G_Ckl14 â€” G_Ckl21") zaten duruyor. Alan
    // DB'de kaliyor â€” tekrar sayimi ve risk puani hala bu kodla tutulur.
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
          {/* ONCE DURUM, SONRA YER.
              Seride once konum yazip durumu sona birakmak, en cok sorulan
              soruyu ("bu ariza acik mi, ne kadardir") en sona koyuyordu.
              Simdi rozet seridin basinda; okuma soldan saga "ne durumda ->
              nerede" diye ilerliyor. */}
          <div className="fx-head-state">
            <span className={`fx-state fx-state--${f.status}`}>
              <span className="fx-state-dot" aria-hidden="true" />
              {t(`faults.status.${f.status}`, { defaultValue: f.status })}
            </span>
          </div>

          {/* YER = TEK BIR KIRINTI YOLU: bolge -> (ana hat) -> hat -> aralik.
              Ucu de ayni hiyerarsinin kademeleri; ayri bicimlerde (kucuk
              buyuk harf ustte, baslik altta) yazilinca aralarindaki
              iliski gorunmuyordu. Kademeler ayni ayracla ilerliyor ve
              vurgu sona, yani sahada gidilecek ARALIGA dogru artiyor.

              "Konum tespit edildi" ve "N. kez" etiketleri KALDIRILDI:
              birincisi aralik zaten yaziliyken bilgi tasimiyordu, ikincisi
              ayni sayiyi kartin alt bolumundeki "bu hattin gecmisi"
              blogunda tekrar ediyordu. */}
          <nav className="fx-head-path" aria-label={t("faults.card.pathLabel")}>
            <span className="fx-head-region">
              <MapPin size={12} strokeWidth={2.3} />
              {f.region_name}
            </span>
            <ChevronRight size={14} strokeWidth={2.6} aria-hidden="true" />
            {/* Kayit bir bransman kolundaysa hangi ana hattan ciktigi
                basliktan okunmali; yoksa "BR-4" tek basina nereye ait
                oldugunu soylemiyor. */}
            {f.is_branch_line && f.parent_line_name ? (
              <>
                <span className="fx-head-parent">{f.parent_line_name}</span>
                <ChevronRight size={14} strokeWidth={2.6} aria-hidden="true" />
              </>
            ) : null}
            <h3 className="fx-head-line">{f.line_name}</h3>
            <ChevronRight size={14} strokeWidth={2.6} aria-hidden="true" />
            <span className="fx-head-range">{rangeText}</span>
          </nav>
        </div>

        {/* ATANAN = KART. Once serit icinde "ATANAN Fikret Safak" diye duz
            metindi; yanindaki "Arizayi Ata" dugmesinin etiketiymis gibi
            okunuyor, kimsenin atanmadigi hal ise soluk bir kelime olarak
            gozden kaciyordu. Kart olarak bas harf rozetiyle duruyor:
            atanmissa kim oldugu bir bakista, atanmamissa kesikli cerceve
            "burada bir eksik var" der. */}
        <div className="fx-head-facts">
          <div className={`fx-assignee ${assignee ? "" : "fx-assignee--empty"}`}>
            {/* KISIYE atandiysa YUZ (varsa fotograf, yoksa bas harf), EKIBE
                atandiysa ekip ikonu — ekibin yuzu olmaz. */}
            {ekipAdi ? (
              <span className="fx-assignee-avatar" aria-hidden="true">
                <UsersRound size={14} strokeWidth={2.2} />
              </span>
            ) : f.assigned_to_avatar_url ? (
              <img
                className="fx-assignee-avatar fx-assignee-avatar--photo"
                src={f.assigned_to_avatar_url}
                alt=""
              />
            ) : (
              <span className="fx-assignee-avatar" aria-hidden="true">
                {assignee ? bashafler(assignee) : <UserIcon size={14} strokeWidth={2.2} />}
              </span>
            )}
            <span className="fx-assignee-body">
              <span className="fx-assignee-key">{t("faults.card.assignedTo")}</span>
              <span className="fx-assignee-val">
                {assignee ?? t("faults.card.noAssignee")}
              </span>
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
                bakmadan once soylenir â€” ekip kac yer gezecegini bilsin. */}
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
          {/* --- 0. SURE ---
              Ust seritte "1sa 40dk suredir acik" bir ETIKET gibi duruyordu;
              oysa mudahale onceligini belirleyen sayi budur ve bakildiginda
              okunacak yer kanit panelidir. Baslangic saati de yanina alindi:
              "ne zaman basladi" ile "ne kadardir suruyor" ayni sorunun iki
              yarisi ve serit ile panel arasinda bolunmusti. */}
          <div className="fx-ev-block">
            <h4 className="fx-ev-title">
              <Timer size={13} strokeWidth={2.3} />
              {sureDurdu ? t("faults.card.elapsedTitleDone") : t("faults.card.elapsedTitle")}
            </h4>
            <p className={`fx-elapsed ${sureDurdu ? "fx-elapsed--done" : ""}`}>
              <strong>{sureText}</strong>
              <span>
                {sureDurdu ? t("faults.card.stateElapsedDone") : t("faults.card.stateElapsed")}
              </span>
            </p>
            <p className="fx-elapsed-start">
              <span>{t("faults.card.openedAt")}</span>
              <time dateTime={f.opened_at}>{fmtDateTime(f.opened_at, localeTag)}</time>
            </p>
            {/* BITIS SAATI yalnizca sayac durduysa: "ne kadar surdu"nun
                yaninda "ne zaman bitti" olmadan sure havada kaliyor. */}
            {sureDurdu && (f.resolved_at ?? f.closed_at) ? (
              <p className="fx-elapsed-start">
                <span>{t("faults.card.resolvedAt")}</span>
                <time dateTime={(f.resolved_at ?? f.closed_at) as string}>
                  {fmtDateTime(f.resolved_at ?? f.closed_at, localeTag)}
                </time>
              </p>
            ) : null}
          </div>

          {/* --- 1. ARIZA KUNYESI ---
              Panel once "alarm + kollar + sinirlar" seklinde uc ayri kutuydu
              ve arizanin KENDISI hakkinda tek bir sey yazmiyordu: turu,
              fazi, akimi, yonu cihazdan geliyor ama hicbiri gorunmuyordu.
              Kunye bunlari tek bir okunur listede toplar; olmayan satir hic
              cizilmez â€” "â€”" ile dolu bir tablo bilgi tasimaz. */}
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

            {/* SEBEP â€” SALT OKUNUR DEGIL.
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
                <span className="fx-cause-plain">{cause?.label ?? "â€”"}</span>
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
                      {a.device_name ?? a.device_code ?? "â€”"}
                      <span className="fx-alarm-dot">Â·</span>
                      {fmtClock(a.created_at, localeTag)}
                      {a.acknowledged ? (
                        <>
                          <span className="fx-alarm-dot">Â·</span>
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
