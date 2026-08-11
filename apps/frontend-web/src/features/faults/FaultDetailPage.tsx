/**
 * Ariza detayi — TAM SAYFA (kendi sekmesinde).
 *
 * NEDEN MODAL DEGIL
 * -----------------
 * Eskiden modaldi ve modalin uc bedeli vardi:
 *
 *   1. Sekme sisteminde yeri yoktu. Tarayici yenilenince kayboluyordu;
 *      operator sahayla telefondayken sayfayi tazelemek acik arizayi
 *      kapatmak demekti.
 *   2. Iki arizayi karsilastirmak imkansizdi — modal tek ve modaldir.
 *      Oysa "ayni hatta iki ariza var mi" siradan bir soru.
 *   3. Icerik modal cercevesine sigmadigi icin uc dar kolona sikismisti.
 *
 * Artik `{ kind: "fault-detail", faultId }` rotasiyla acilan bir sekme.
 *
 * KENDI BASINA AYAKTA DURUR
 * -------------------------
 * Sekmeler localStorage'a yazildigi icin sayfa, ariza listesi HIC
 * yuklenmemisken de acilabilir. Kayit listede yoksa (kapanmis ariza,
 * gecmisten acilmisti) kendi cekiyor.
 *
 * DUZEN — 2026-08 yeniden tasarim
 * -------------------------------
 * Onceki duzen "solda dev harita + sagda 400px form rayi" idi. Uc somut
 * sikayet vardi:
 *
 *   1. Harita ekranin yarisini kapliyor, geri kalan her sey onun yaninda
 *      ikincil gorunuyordu. Harita degerli ama sayfanin TAMAMI degil.
 *   2. Atama ile durum ayni kartin icindeydi; ikisi farkli sorular
 *      ("kim gidiyor" / "is nerede") ve ayri okunmali.
 *   3. Yan yana duran kartlarin boylari tutmuyordu (grid `align-items:start`)
 *      — biri alcak biri yuksek, sayfa dagilmis gorunuyordu.
 *
 * Artik sayfa TAM GENISLIK SERITLERDEN olusur. Her serit bir grid satiri ve
 * satirdaki kartlar AYNI YUKSEKLIKTEDIR (stretch); uzun listeler kartin
 * icinde kayar, karti uzatmaz:
 *
 *   1) Atama            | Durum (adim seridi + zaman cizelgesi)
 *   2) Konum haritasi   | Ariza kunyesi (cihazdan gelen olcumler)
 *   3) Ariza bolgesi | Tetikleyen alarmlar | Direkler | Kollar | Hat gecmisi
 *   4) Cozum & notlar   | Saha raporu (yorumlar)
 *
 * HARITA VARSAYILAN UYDU: hat arizasinda sahada aranan sey agac teması,
 * dere yatagi, yol kenari — sokak gorunumu bunlarin hicbirini gostermez.
 * Bu ekranda uydu katmani ISE YARAYAN katmandir, o yuzden acilis katmani.
 *
 * GERI DUGMESI YOK: sayfa bir SEKME; geri donus sekme seridinden yapilir,
 * ekranin icinde ikinci bir gezinme ogesi gereksiz.
 */
import { useCallback, useEffect, useMemo, useState, type CSSProperties } from "react";
import { useTranslation } from "react-i18next";

import { useProjectSettings } from "../../components/ProjectSettingsProvider";
import {
  Activity,
  ArrowRight,
  CalendarClock,
  Check,
  CircleDot,
  FileDown,
  GitBranch,
  History,
  Lightbulb,
  MapPin,
  MessagesSquare,
  Radio,
  Route,
  Save,
  Send,
  Timer,
  TriangleAlert,
  UserRound,
  X,
  Zap
} from "lucide-react";
import { LayersControl, MapContainer, Marker, Polyline, Tooltip } from "react-leaflet";
import L from "leaflet";

import { buildFaultMapView } from "./faultMapView";
import { buildFaultRecurrence } from "./faultRecurrence";
import { FitFocus } from "./FaultMapFocus";

import { MapLayerSwitchFix } from "../../components/MapLayerSwitchFix";
import { ResilientTileLayer } from "../../components/ResilientTileLayer";
import { fetchFault, fetchFaultCauses, type GridSnapshot } from "../../shared/api";
import { formatDistanceM, formatDistanceRange } from "../../shared/lineDistance";
import { MAP_LAYERS } from "../../shared/mapTiles";
import type {
  AlarmEvent,
  DeviceRow,
  FaultCause,
  FaultCauseCatalog,
  FaultComment,
  FaultEvent,
  FaultTriggerAlarm,
  UserRead
} from "../../shared/types";

type Props = {
  faultId: number;
  /** Listeden gelen kayit. Yoksa sayfa kendi ceker. */
  faults: FaultEvent[];
  users: UserRead[];
  currentUsername: string;
  canAssign: boolean;
  accessToken: string;
  gridSnapshot?: GridSnapshot | null;
  devices?: DeviceRow[];
  alarms?: AlarmEvent[];
  onAssign: (faultId: number, username: string | null) => Promise<void>;
  /** `closed` icin `resolutionNote` ZORUNLU (backend de dogrular). */
  onUpdateStatus: (
    faultId: number,
    status: string,
    resolutionNote?: string | null
  ) => Promise<void>;
  onUpdateNote: (faultId: number, note: string | null) => Promise<void>;
  onUpdateCause: (
    faultId: number,
    payload: { cause_code: string | null; cause_detail?: string | null }
  ) => Promise<void>;
  onLoadComments: (faultId: number) => Promise<FaultComment[]>;
  onAddComment: (faultId: number, body: string) => Promise<void>;
};

const STATUS_COLOR: Record<string, string> = {
  open: "#ef4444",
  assigned: "#f59e0b",
  in_progress: "#3b82f6",
  resolved: "#10b981",
  closed: "#64748b"
};

const AKIS: readonly ["assigned", "in_progress", "resolved", "closed"] = [
  "assigned",
  "in_progress",
  "resolved",
  "closed"
];

function fmtDate(iso: string | null | undefined, localeTag: string): string {
  if (!iso) return "—";
  return new Date(iso).toLocaleString(localeTag);
}

function fmtClock(iso: string | null | undefined, localeTag: string): string {
  if (!iso) return "—";
  return new Date(iso).toLocaleTimeString(localeTag, { hour: "2-digit", minute: "2-digit" });
}

/** Ad-soyaddan iki harf. Kullanici adi yoksa "?" — uydurma bas harf yok. */
function bashafler(ad: string | null | undefined): string {
  const temiz = (ad ?? "").trim();
  if (!temiz) return "?";
  const parcalar = temiz.split(/\s+/);
  if (parcalar.length === 1) return parcalar[0].substring(0, 2).toUpperCase();
  return (parcalar[0][0] + parcalar[parcalar.length - 1][0]).toUpperCase();
}

/** Mini harita direk pini. */
const polePin = (label: string, isRed: boolean, isGreen: boolean) => {
  const color = isRed ? "#ef4444" : isGreen ? "#10b981" : "#475569";
  return L.divIcon({
    className: "fd-pole-icon-wrap",
    html: `<div class="fd-pole-icon" style="background:${color}">${label}</div>`,
    iconSize: [22, 22],
    iconAnchor: [11, 11]
  });
};

/** Cihaz isaretcisi — ariza algiladiysa kirmizi. */
const deviceIcon = (isRed: boolean) => {
  const color = isRed ? "#dc2626" : "#10b981";
  return L.divIcon({
    className: "fd-dev-icon-wrap",
    html: `
      <div class="fd-dev-icon" style="--c:${color}">
        <svg viewBox="0 0 24 24" width="11" height="11" aria-hidden="true">
          <path fill="#fff" d="M13 2 4 14h6l-1 8 9-12h-6z"/>
        </svg>
      </div>
    `,
    iconSize: [22, 22],
    iconAnchor: [11, 11]
  });
};

export function FaultDetailPage({
  faultId,
  faults,
  users,
  currentUsername,
  canAssign,
  accessToken,
  gridSnapshot,
  devices,
  alarms,
  onAssign,
  onUpdateStatus,
  onUpdateNote,
  onUpdateCause,
  onLoadComments,
  onAddComment
}: Props) {
  const { t, i18n } = useTranslation();
  const localeTag = i18n.language?.startsWith("tr") ? "tr-TR" : "en-US";

  // Listedeki kayit ONCELIKLI: App onu duzenli tazeliyor ve mutasyonlardan
  // sonra guncelliyor. `cekilen` yalnizca listede OLMAYAN ariza icin yedek
  // (kapanmis ariza, sekme yenilemeden sonra geri geldi).
  const listeKaydi = useMemo(
    () => faults.find((f) => f.id === faultId) ?? null,
    [faults, faultId]
  );
  const [cekilen, setCekilen] = useState<FaultEvent | null>(null);
  const [yukleniyor, setYukleniyor] = useState(false);
  const [yuklemeHatasi, setYuklemeHatasi] = useState("");
  const fault = listeKaydi ?? cekilen;

  const cek = useCallback(async () => {
    try {
      setCekilen(await fetchFault(accessToken, faultId));
      setYuklemeHatasi("");
    } catch (err) {
      const msg = err instanceof Error ? err.message : t("common.errorOccurred");
      if (msg !== "session_polling_401") setYuklemeHatasi(msg);
    }
  }, [accessToken, faultId, t]);

  useEffect(() => {
    if (listeKaydi) return; // listede var, cekmeye gerek yok
    setYukleniyor(true);
    void cek().finally(() => setYukleniyor(false));
  }, [listeKaydi, cek]);

  /** Sebep katalogu — sayfa KENDI ceker. Prop olsaydi sekme yenilemeden
   *  sonra (liste sayfasi acik degilken) sebep secimi olu gelirdi.
   *  Backend tek kaynak (`app/data/fault_causes.py`); frontend'e gomulseydi
   *  ikisi ayrisir ve secilen kod backend'de taninmaz olurdu. */
  const [causeCatalog, setCauseCatalog] = useState<FaultCauseCatalog | null>(null);
  useEffect(() => {
    let iptal = false;
    fetchFaultCauses(accessToken)
      .then((k) => {
        if (!iptal) setCauseCatalog(k);
      })
      .catch(() => {
        // Katalog alinamazsa sebep secimi devre disi kalir; sayfanin geri
        // kalani (harita, durum, yorumlar) etkilenmez.
        if (!iptal) setCauseCatalog(null);
      });
    return () => {
      iptal = true;
    };
  }, [accessToken]);

  const [comments, setComments] = useState<FaultComment[]>([]);
  const [commentDraft, setCommentDraft] = useState("");
  const [noteDraft, setNoteDraft] = useState("");
  // Kapanis gerekcesi. `note`dan AYRI: `note` acikken tutulan calisma
  // notudur ve degisir; bu ise arizanin nasil giderildiginin kalici cevabi.
  const [resolutionDraft, setResolutionDraft] = useState("");
  // Sebep girisi ve yorumlar POPUP'ta: sayfanin en altinda dururken
  // kullanici her islem icin oraya kaydiriyordu.
  const [islemModal, setIslemModal] = useState<null | "close" | "comments">(null);
  // Rapor basligi icin musteri logosu; EnerjiOne logosu sabit varliktan.
  const { settings: projeAyarlari } = useProjectSettings();
  const [causeDraft, setCauseDraft] = useState("");
  const [causeDetailDraft, setCauseDetailDraft] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [mapFocus, setMapFocus] = useState<"zone" | "line" | "grid">("zone");

  // Taslaklar ariza KIMLIGI degisince sifirlanir — her render'da degil.
  // Aksi halde kullanici yazarken liste tazelenip yazdigini silerdi.
  useEffect(() => {
    setNoteDraft("");
    setCauseDraft("");
    setCauseDetailDraft("");
    setCommentDraft("");
    setError("");
  }, [faultId]);

  // Sunucudaki deger ilk geldiginde taslaga yansisin (kullanici henuz
  // dokunmadiysa).
  const [taslakYuklendi, setTaslakYuklendi] = useState(false);
  useEffect(() => {
    if (!fault || taslakYuklendi) return;
    setNoteDraft(fault.note ?? "");
    setResolutionDraft(fault.resolution_note ?? "");
    setCauseDraft(fault.cause_code ?? "");
    setCauseDetailDraft(fault.cause_detail ?? "");
    setTaslakYuklendi(true);
  }, [fault, taslakYuklendi]);
  useEffect(() => setTaslakYuklendi(false), [faultId]);

  useEffect(() => {
    let iptal = false;
    void (async () => {
      try {
        const list = await onLoadComments(faultId);
        if (!iptal) setComments(list);
      } catch (err) {
        if (!iptal) {
          setError(err instanceof Error ? err.message : t("faults.detail.loadingComments"));
        }
      }
    })();
    return () => {
      iptal = true;
    };
  }, [faultId, onLoadComments, t]);

  // Canli sure sayaci.
  const [now, setNow] = useState<number>(() => Date.now());
  const isLive = fault ? fault.status !== "closed" && fault.status !== "resolved" : false;
  useEffect(() => {
    // Kapanmis arizada saniye saymanin anlami yok; bos yere her saniye
    // render etmeyelim.
    if (!isLive) return;
    const id = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(id);
  }, [isLive]);

  const elapsedText = useMemo(() => {
    if (!fault) return "—";
    const start = new Date(fault.opened_at).getTime();
    const end =
      fault.status === "closed" && fault.closed_at
        ? new Date(fault.closed_at).getTime()
        : fault.status === "resolved" && fault.resolved_at
          ? new Date(fault.resolved_at).getTime()
          : now;
    let sec = Math.max(0, Math.round((end - start) / 1000));
    const days = Math.floor(sec / 86400);
    sec -= days * 86400;
    const hours = Math.floor(sec / 3600);
    sec -= hours * 3600;
    const mins = Math.floor(sec / 60);
    sec -= mins * 60;
    if (days > 0) return `${days}g ${hours}sa ${mins}dk`;
    if (hours > 0) return `${hours}sa ${mins}dk ${sec}sn`;
    if (mins > 0) return `${mins}dk ${sec}sn`;
    return `${sec}sn`;
  }, [now, fault]);

  const userOptions = useMemo(
    () => [...users].sort((a, b) => a.full_name.localeCompare(b.full_name, localeTag)),
    [users, localeTag]
  );

  const canEdit = fault ? canAssign || fault.assigned_to_username === currentUsername : false;

  const alarmActiveDeviceIds = useMemo(() => {
    const s = new Set<number>();
    for (const a of alarms ?? []) if (!a.reset) s.add(a.device_id);
    return s;
  }, [alarms]);

  const mapView = useMemo(() => {
    if (!gridSnapshot || !fault) return null;
    return buildFaultMapView({
      poles: gridSnapshot.poles,
      segments: gridSnapshot.segments,
      lines: gridSnapshot.lines ?? [],
      fault,
      devices: devices ?? [],
      alarmActiveDeviceIds,
      poleFallback: t("faults.detail.tooltipPole"),
      deviceFallback: t("common.device")
    });
  }, [gridSnapshot, fault, devices, alarmActiveDeviceIds, t]);

  /** BU HAT DAHA ONCE DE ARIZALANDI MI — tekrar eden ariza baska bir istir:
   *  gecici bir olay degil, cozulmemis bir kok sebep vardir. */
  const recurrence = useMemo(
    () => (fault ? buildFaultRecurrence(fault, faults) : null),
    [fault, faults]
  );

  /** Mutasyon sarmalayicisi: hata mesajini tek yerde topla, listede olmayan
   *  bir arizada kendi kopyamizi tazele. */
  const calistir = useCallback(
    async (islem: () => Promise<void>, hataAnahtari: string) => {
      setSaving(true);
      setError("");
      try {
        await islem();
        if (!listeKaydi) await cek();
      } catch (err) {
        setError(err instanceof Error ? err.message : t(hataAnahtari));
      } finally {
        setSaving(false);
      }
    },
    [listeKaydi, cek, t]
  );

  const causeLabel = useCallback(
    (c: FaultCause) => (i18n.language?.startsWith("tr") ? c.label_tr : c.label_en),
    [i18n.language]
  );

  /** Aileye gore gruplanmis secim listesi — duz 19'luk liste taranmasi zor. */
  const causeGroups = useMemo<[string, FaultCause[]][]>(() => {
    if (!causeCatalog) return [];
    const harita = new Map<string, FaultCause[]>();
    for (const grup of causeCatalog.groups) harita.set(grup, []);
    for (const c of causeCatalog.causes) {
      const liste = harita.get(c.group);
      if (liste) liste.push(c);
      else harita.set(c.group, [c]);
    }
    return [...harita.entries()].filter(([, liste]) => liste.length > 0);
  }, [causeCatalog]);

  /** Kuralin onerdigi sebep. SECILI GELMEZ — operator onaylamadan bir etiket
   *  "girilmis" sayilirsa istatistik, kimsenin bakmadigi bir tahminle dolar. */
  const suggestedCause = useMemo(() => {
    if (!causeCatalog || !fault || fault.cause_code) return null;
    const kod = fault.auto_cause_code;
    if (!kod) return null;
    const c = causeCatalog.causes.find((x) => x.code === kod);
    return c ? { code: c.code, label: causeLabel(c) } : null;
  }, [causeCatalog, fault, causeLabel]);

  /** ARIZA KUNYESI — cihazdan gelen olcumler. Yalnizca DOLU alanlar: "—" ile
   *  dolu bir tablo bilgi tasimadigi gibi, gercekten bilinen iki degeri de
   *  gorunmez kilar. */
  const specRows = useMemo(() => {
    const rows: { key: string; label: string; value: string; tone?: "red" | "green" }[] = [];
    if (!fault) return rows;
    const ekle = (
      key: string,
      label: string,
      value: string | null | undefined,
      tone?: "red" | "green"
    ) => {
      if (value == null || value === "") return;
      rows.push({ key, label, value, tone });
    };

    if (fault.fault_kind) {
      ekle(
        "kind",
        t("faults.card.specKind"),
        t(`faults.card.kind.${fault.fault_kind}`, { defaultValue: fault.fault_kind }),
        fault.fault_kind === "permanent" ? "red" : undefined
      );
    }
    if (fault.phase) {
      // "abc" -> "A-B-C". Backend fazlari harf harf ve sirali yazar.
      ekle(
        "phase",
        t("faults.card.specPhase"),
        fault.phase.toUpperCase().split("").join("-"),
        "red"
      );
    }
    if (fault.fault_direction) {
      ekle(
        "dir",
        t("faults.card.specDirection"),
        t(`faults.card.direction.${fault.fault_direction}`, {
          defaultValue: fault.fault_direction
        })
      );
    }
    if (fault.fault_current_a != null) {
      ekle("ia", t("faults.card.specFaultCurrent"), `${fault.fault_current_a.toFixed(1)} A`, "red");
    }
    if (fault.load_current_before_a != null) {
      ekle(
        "il",
        t("faults.card.specLoadCurrent"),
        `${fault.load_current_before_a.toFixed(1)} A`
      );
    }
    if (fault.conductor_temp_c != null) {
      ekle("temp", t("faults.card.specTemp"), `${fault.conductor_temp_c.toFixed(0)} °C`);
    }
    if (fault.momentary_fault_count != null) {
      ekle("mc", t("faults.detail.specMomentaryCount"), String(fault.momentary_fault_count));
    }
    if (fault.permanent_fault_count != null) {
      ekle("pc", t("faults.detail.specPermanentCount"), String(fault.permanent_fault_count));
    }
    if (fault.measured_at) {
      ekle("at", t("faults.card.specMeasuredAt"), fmtClock(fault.measured_at, localeTag));
    }
    return rows;
  }, [fault, t, localeTag]);

  const triggerAlarms: FaultTriggerAlarm[] = fault?.trigger_alarms ?? [];
  const triggerSignals = fault?.trigger_signals ?? [];

  // ---- Yukleniyor / bulunamadi -------------------------------------------
  if (!fault) {
    return (
      <div className="fd-page fd-page--bare">
        <div className="fd-placeholder">
          {yukleniyor ? (
            <>
              <Timer size={26} strokeWidth={1.5} />
              {t("faults.detail.loading")}
            </>
          ) : (
            <>
              <TriangleAlert size={26} strokeWidth={1.5} />
              {yuklemeHatasi || t("faults.detail.notFound", { id: faultId })}
            </>
          )}
        </div>
      </div>
    );
  }

  const statusColor = STATUS_COLOR[fault.status] ?? "#64748b";
  const akisIndex = AKIS.indexOf(fault.status as (typeof AKIS)[number]);
  const assigneeName = fault.assigned_to_full_name ?? fault.assigned_to_username ?? null;
  const distanceText = formatDistanceRange(fault.zone_start_m, fault.zone_end_m);
  //: Araligin ORTA NOKTASI — sahaya cikan kisiye verilecek tek sayi.
  const tahminiMesafe = useMemo(() => {
    const a = fault.zone_start_m;
    const b = fault.zone_end_m;
    if (typeof a !== "number" && typeof b !== "number") return "—";
    const orta = typeof a === "number" && typeof b === "number" ? (a + b) / 2 : (a ?? b)!;
    return orta >= 1000 ? `~${(orta / 1000).toFixed(2)} km` : `~${Math.round(orta)} m`;
  }, [fault.zone_start_m, fault.zone_end_m]);

  /** Kaydedilmemis degisiklik var mi — Cozum kartinin kaydet dugmesi bunu
   *  okur. Sebep ve not TEK dugmede kaydedilir: operator ikisini birlikte
   *  yaziyor, iki ayri "Kaydet" biri unutuldugunda sessizce veri kaybiydi. */
  const causeDirty =
    (fault.cause_code ?? "") !== causeDraft ||
    (fault.cause_detail ?? "") !== causeDetailDraft.trim();
  const noteDirty = (fault.note ?? "") !== noteDraft.trim();
  const dirty = causeDirty || noteDirty;

  const zamanCizelgesi = [
    { key: "opened", label: t("faults.detail.timelineOpened"), at: fault.opened_at, color: "#ef4444" },
    {
      key: "assigned",
      label: t("faults.detail.timelineAssigned"),
      at: fault.assigned_at,
      color: "#f59e0b"
    },
    {
      key: "resolved",
      label: t("faults.detail.timelineResolved"),
      at: fault.resolved_at,
      color: "#10b981"
    },
    {
      key: "closed",
      label: t("faults.detail.timelineClosed"),
      at: fault.closed_at,
      color: "#64748b"
    }
  ].filter((s) => Boolean(s.at));

  return (
    <div className="fd-page">
      {/* YALNIZCA YAZDIRMADA: rapor basligi. Ekranda gorunmez. */}
      <div className="fd-print-head" aria-hidden="true">
        <img className="fd-print-logo" src="/logo.png" alt="" />
        <div className="fd-print-title">
          <strong>{t("faults.detail.reportTitle")}</strong>
          <span>
            {fault.region_name} / {fault.line_name} · #{fault.id}
          </span>
          <small>{fmtDate(fault.opened_at, localeTag)}</small>
        </div>
        {projeAyarlari.customer_logo ? (
          <img className="fd-print-logo" src={projeAyarlari.customer_logo} alt="" />
        ) : null}
      </div>

      {/* ---- Ust serit: kunye + olculer ---- */}
      <header className="fd-head">
        <div className="fd-head-top">
          <nav className="fd-breadcrumb" aria-label={t("faults.detail.mapTitle")}>
            <MapPin size={13} />
            <span>{fault.region_name}</span>
            <em>/</em>
            <span>{fault.line_name}</span>
            <em>/</em>
            <strong>
              {t("faults.card.rangeText", { from: fault.from_pole_seq, to: fault.to_pole_seq })}
            </strong>
          </nav>
          {/* Baslik ARTIK AYNI SATIRDA: kunye / baslik / durum uc ayri
              satirdayken ust bosluk olculerin yerini yiyordu. */}
          <h1 className="fd-title">
            {fault.line_name}
            <span className="fd-record">#{fault.id}</span>
          </h1>
        </div>

        <div className="fd-metrics">
          {/* DURUM ilk sirada: ekrana bakan kisinin ilk sorusu "bu ariza
              ne durumda". Once baslik satirindaki rozetteydi, orada
              kunyenin golgesinde kaliyordu. */}
          <Metric
            Icon={CircleDot}
            label={t("faults.detail.statusLabel")}
            value={t(`faults.status.${fault.status}`, { defaultValue: fault.status })}
            renk={statusColor}
          />
          <Metric
            Icon={Timer}
            label={t("faults.detail.duration")}
            value={elapsedText}
            canli={isLive}
            not={isLive ? t("faults.card.durationLive") : t("faults.card.durationFinal")}
          />
          <Metric
            Icon={UserRound}
            label={t("faults.detail.assignee")}
            value={assigneeName ?? t("faults.detail.assigneeEmpty")}
          />
          {/* TEK SAYI, aralik degil: sahaya cikan kisi "773 m - 1,24 km"
              araligiyla degil, gidecegi NOKTAYLA ilgileniyor. Belirsizlik
              zaten "Ariza Tespit Eden Cihazlar" kartinda yaziyor. */}
          <Metric
            Icon={Route}
            label={t("faults.detail.estimatedDistance")}
            value={tahminiMesafe}
          />

          {/* Eylemler seride BITISIK: sebep girisi ve yorum sayfanin en
              altindaydi, kullanici her defasinda oraya kaydiriyordu. */}
          <div className="fd-metric-actions">
            {/* PDF: ayri bir cizim katmani YOK — sayfanin kendisi
                yazdirilir. Harita zaten DOM'da; ayri bir raster uretmek
                ikinci bir dogruluk kaynagi yaratirdi (ekranda gorulen ile
                raporda cikan ayrisabilirdi). */}
            <button
              type="button"
              className="fd-ghost-btn"
              onClick={() => window.print()}
            >
              <FileDown size={14} />
              {t("faults.detail.exportPdf")}
            </button>
            <button
              type="button"
              className="fd-ghost-btn"
              onClick={() => setIslemModal("comments")}
            >
              <MessagesSquare size={14} />
              {t("faults.detail.commentsTitle")}
              {comments.length > 0 ? <span className="fd-count">{comments.length}</span> : null}
            </button>
            {canEdit && fault.status !== "closed" ? (
              <button
                type="button"
                className="fd-save fd-save--close"
                onClick={() => setIslemModal("close")}
              >
                <Check size={14} />
                {t("faults.detail.closeFault")}
              </button>
            ) : null}
          </div>
        </div>
      </header>

      {error ? <p className="fd-error">{error}</p> : null}

      {/* ================= 1) ATAMA | DURUM =================
          Ikisi eskiden tek kartta ("Sorumluluk") idi. Farkli iki soru:
          "kim gidiyor" ve "is nerede". Ayri kartlar. */}

      {/* ================= 2) HARITA | KUNYE ================= */}
      <div className="fd-row fd-row--map">
        <section className="fd-card fd-card--map">
          <header className="fd-card-head">
            <h2>
              <MapPin size={15} />
              {t("faults.detail.mapTitle")}
            </h2>
            {/* ODAK SECICI: ariza bolgesine zoom yapinca "bu hattin
                neresi?" belirsiz kaliyor, tum hatta bakinca ariza noktasi
                kayboluyordu. Ucu de ayni haritada. */}
            {mapView ? (
              <div className="fd-focus" role="group">
                {(["zone", "line", "grid"] as const).map((k) => (
                  <button
                    key={k}
                    type="button"
                    className={mapFocus === k ? "is-active" : undefined}
                    onClick={() => setMapFocus(k)}
                  >
                    {t(`faults.detail.focus.${k}`)}
                  </button>
                ))}
              </div>
            ) : null}
          </header>

          {mapView ? (
            <>
              <div className="fd-map-wrap">
                <MapContainer
                  center={mapView.center}
                  zoom={mapView.zoom}
                  className="fd-map"
                  scrollWheelZoom={false}
                  dragging
                  doubleClickZoom={false}
                  attributionControl={false}
                >
                  <LayersControl position="topright">
                    {/* UYDU ONCE ve `checked`: hat arizasinda aranan sey agac,
                        dere yatagi, yol kenari — sokak cizimi bunlari
                        gostermez. Sokak katmani ikinci sirada duruyor. */}
                    <LayersControl.BaseLayer checked name={t("map.layers.satellite")}>
                      <ResilientTileLayer
                        layer="satellite"
                        attribution={MAP_LAYERS[1].attribution}
                        maxZoom={MAP_LAYERS[1].maxZoom}
                      />
                    </LayersControl.BaseLayer>
                    <LayersControl.BaseLayer name={t("map.layers.street")}>
                      {/* maxZoom verilmezse Leaflet 18'e duser ve sokak
                          gorunumu uydudan (19) bir kademe geride kalir. */}
                      <ResilientTileLayer
                        layer="osm"
                        attribution={MAP_LAYERS[0].attribution}
                        maxZoom={MAP_LAYERS[0].maxZoom}
                      />
                    </LayersControl.BaseLayer>
                  </LayersControl>
                  <MapLayerSwitchFix />
                  <FitFocus
                    points={
                      mapFocus === "zone"
                        ? mapView.zoneBounds
                        : mapFocus === "line"
                          ? mapView.lineBounds
                          : mapView.gridBounds
                    }
                  />
                  {/* Tum sebeke gorunumunde komsu hatlar SOLUK — ariza
                      hatti one cikmaya devam etsin. */}
                  {mapFocus === "grid"
                    ? mapView.otherLines.map((l) => (
                        <Polyline
                          key={`ol-${l.lineId}`}
                          positions={l.path}
                          pathOptions={{ color: "#e2e8f0", weight: 2.5, opacity: 0.55 }}
                        >
                          <Tooltip>{l.name}</Tooltip>
                        </Polyline>
                      ))
                    : null}
                  {mapView.preGreen.length >= 2 ? (
                    <Polyline
                      positions={mapView.preGreen}
                      pathOptions={{ color: "#22c55e", weight: 4, opacity: 0.9 }}
                    />
                  ) : null}
                  {mapView.postGreen.length >= 2 ? (
                    <Polyline
                      positions={mapView.postGreen}
                      pathOptions={{ color: "#22c55e", weight: 4, opacity: 0.9 }}
                    />
                  ) : null}
                  {mapView.faultRed.length >= 2 ? (
                    <Polyline
                      positions={mapView.faultRed}
                      pathOptions={{
                        color: "#ef4444",
                        weight: 6,
                        opacity: 0.95,
                        dashArray: "10 6"
                      }}
                    />
                  ) : null}
                  {mapView.polesWithRole.map(({ p, isFromFault, isToFault, isInFaultRange }) => (
                    <Marker
                      key={`p-${p.id}`}
                      position={[p.latitude, p.longitude]}
                      icon={polePin(
                        String(p.sequence_no),
                        isFromFault,
                        isToFault || (isInFaultRange && !isFromFault)
                      )}
                    >
                      <Tooltip>
                        {p.name ?? `${t("faults.detail.tooltipPole")} #${p.sequence_no}`}
                        {isFromFault ? ` (${t("faults.detail.tooltipFaultStart")})` : ""}
                        {isToFault ? ` (${t("faults.detail.tooltipFaultEnd")})` : ""}
                      </Tooltip>
                    </Marker>
                  ))}
                  {mapView.deviceMarkers.map((d) => (
                    <Marker
                      key={`d-${d.deviceId}`}
                      position={[d.lat, d.lon]}
                      icon={deviceIcon(d.isRed)}
                    >
                      <Tooltip>
                        <strong>{d.name}</strong>
                        {d.code ? (
                          <>
                            <br />
                            <span style={{ opacity: 0.7 }}>{d.code}</span>
                          </>
                        ) : null}
                        <br />
                        <em style={{ color: d.isRed ? "#dc2626" : "#10b981" }}>
                          {d.isRed
                            ? t("faults.detail.deviceDetectedFault")
                            : t("faults.detail.deviceNoFault")}
                        </em>
                      </Tooltip>
                    </Marker>
                  ))}
                </MapContainer>
              </div>
              <div className="fd-legend">
                <span>
                  <i className="fd-legend-line" style={{ background: "#ef4444" }} />
                  {t("faults.detail.mapLegendFault")}
                </span>
                <span>
                  <i className="fd-legend-line" style={{ background: "#22c55e" }} />
                  {t("faults.detail.mapLegendOk")}
                </span>
                <span>
                  <i className="fd-legend-dot" style={{ background: "#dc2626" }} />
                  {t("faults.detail.mapLegendDeviceRed")}
                </span>
                <span>
                  <i className="fd-legend-dot" style={{ background: "#10b981" }} />
                  {t("faults.detail.mapLegendDeviceGreen")}
                </span>
              </div>
            </>
          ) : (
            <p className="fd-empty">{t("faults.detail.mapEmpty")}</p>
          )}
        </section>

        <div className="fd-col">
          {/* ARIZA KUNYESI — cihazin arizanin KENDISI hakkinda soyledikleri.
              Bu ekranda hic gorunmuyordu; operator ayni bilgiyi liste
              kartindan okumak icin geri donuyordu. */}
          <section className="fd-card">
            <header className="fd-card-head">
              <h2>
                <Activity size={15} />
                {t("faults.card.specTitle")}
              </h2>
              <small>{t("faults.detail.specHint")}</small>
            </header>

            {specRows.length === 0 ? (
              <p className="fd-empty">{t("faults.card.specEmpty")}</p>
            ) : (
              <dl className="fd-spec">
                {specRows.map((row) => (
                  <div key={row.key}>
                    <dt>{row.label}</dt>
                    <dd className={row.tone ? `is-${row.tone}` : undefined}>{row.value}</dd>
                  </div>
                ))}
              </dl>
            )}

            {triggerSignals.length > 0 ? (
              <>
                <span className="fd-label">{t("faults.detail.specSignals")}</span>
                <div className="fd-signals">
                  {triggerSignals.map((s) => (
                    <code key={s}>{s}</code>
                  ))}
                </div>
              </>
            ) : null}
          </section>

          {/* ATAMA — kunyenin ALTINDA. "Kim gidiyor" sorusu,
              arizanin kendi olcumlerinden SONRA okunur. Eskiden
              sayfanin en ustundeydi ve olculeri asagi itiyordu. */}
          <section className="fd-card">
            <header className="fd-card-head">
              <h2>
                <UserRound size={15} />
                {t("faults.detail.assignTitle")}
              </h2>
              <small>{t("faults.detail.ticketsHint")}</small>
            </header>

            <div className="fd-assignee">
              <span
                className={`fd-avatar ${assigneeName ? "" : "fd-avatar--empty"}`}
                aria-hidden="true"
              >
                {assigneeName ? bashafler(assigneeName) : "—"}
              </span>
              <div className="fd-assignee-body">
                <strong>{assigneeName ?? t("faults.detail.assigneeEmpty")}</strong>
                {/* Alt satir: adi VARSA kullanici adi (telsizde soylenen sey o),
                    atanmamissa uyari. Atanma zamani asagida kunyede duruyor —
                    burada tekrar edilmez. */}
                {fault.assigned_to_full_name && fault.assigned_to_username ? (
                  <small>{fault.assigned_to_username}</small>
                ) : assigneeName ? null : (
                  <small>{t("faults.detail.assignHint")}</small>
                )}
              </div>
            </div>

            {canAssign ? (
              <>
                <label className="fd-label" htmlFor="fd-assignee">
                  {t("faults.detail.changeAssignee")}
                </label>
                <div className="fd-assign-row">
                  <select
                    id="fd-assignee"
                    className="fd-select"
                    value={fault.assigned_to_username ?? ""}
                    onChange={(e) =>
                      void calistir(
                        () => onAssign(fault.id, e.target.value || null),
                        "alarms.errors.assignFailed"
                      )
                    }
                    disabled={saving}
                  >
                    <option value="">{t("faults.detail.assigneeUnset")}</option>
                    {userOptions.map((u) => (
                      <option key={u.id} value={u.username}>
                        {u.full_name} ({u.username})
                      </option>
                    ))}
                  </select>
                  {/* Sahadaki kisi arizayi kendi ustlenebilsin — listede kendi
                      adini aramak gereksiz bir adim. */}
                  {fault.assigned_to_username !== currentUsername ? (
                    <button
                      type="button"
                      className="fd-ghost-btn"
                      onClick={() =>
                        void calistir(
                          () => onAssign(fault.id, currentUsername),
                          "alarms.errors.assignFailed"
                        )
                      }
                      disabled={saving}
                    >
                      {t("faults.detail.assignToMe")}
                    </button>
                  ) : null}
                </div>
              </>
            ) : null}

            {fault.assigned_at ? (
              <dl className="fd-kv">
                <div>
                  <dt>{t("faults.detail.assignedAtLabel")}</dt>
                  <dd>{fmtDate(fault.assigned_at, localeTag)}</dd>
                </div>
              </dl>
            ) : null}
          </section>
        </div>
      </div>

      {/* ================= 3) BOLGE / ALARM / DIREK / KOL / GECMIS ========= */}
      <div className="fd-row fd-row--tiles">
        {/* Ariza tespit eden cihazlar + tel mesafesi: ikisi ayni soruyu
            yanitliyor (SAHADA NEREYE GIDILECEK), tek kartta. */}
        <section className="fd-card">
          <header className="fd-card-head">
            <h2>
              <Zap size={15} />
              {t("faults.detail.devicesTitle")}
            </h2>
            <small>{t("faults.detail.devicesHint")}</small>
          </header>
          <div className="fd-devices">
            <div className="fd-device fd-device--red">
              <span className="fd-device-dot" />
              <span className="fd-device-role">{t("faults.detail.deviceLastRedRole")}</span>
              <strong>{fault.last_red_device_name ?? "—"}</strong>
              {fault.last_red_device_code ? <small>{fault.last_red_device_code}</small> : null}
            </div>
            <ArrowRight className="fd-device-arrow" size={16} />
            <div className="fd-device fd-device--green">
              <span className="fd-device-dot" />
              <span className="fd-device-role">{t("faults.detail.deviceFirstGreenRole")}</span>
              <strong>
                {fault.first_green_device_name ?? t("faults.detail.deviceFirstGreenLineEnd")}
              </strong>
              {fault.first_green_device_code ? (
                <small>{fault.first_green_device_code}</small>
              ) : null}
            </div>
          </div>

          {/* Tel mesafesi — backend'de direk + cihaz koordinatlarindan hat
              boyunca hesaplanip kayda yazilir (line_distance_service). */}
          {distanceText ? (
            <dl className="fd-kv">
              <div>
                <dt>{t("faults.detail.distanceFromStart")}</dt>
                <dd>{distanceText}</dd>
              </div>
              {fault.zone_length_m != null ? (
                <div>
                  <dt>{t("faults.detail.distanceSpanLabel")}</dt>
                  <dd>{formatDistanceM(fault.zone_length_m)}</dd>
                </div>
              ) : null}
            </dl>
          ) : null}
        </section>

        {/* Arizayi acan alarmlar — "cihaz neyi gordu" sorusunun kanidi. */}
        <section className="fd-card">
          <header className="fd-card-head">
            <h2>
              <TriangleAlert size={15} />
              {t("faults.card.causeTitle")}
            </h2>
          </header>
          {triggerAlarms.length === 0 ? (
            <p className="fd-empty">{t("faults.card.causeEmpty")}</p>
          ) : (
            <ul className="fd-alarms">
              {triggerAlarms.map((a) => (
                <li key={a.id} className={`fd-alarm fd-alarm--${a.level}`}>
                  <div className="fd-alarm-top">
                    <strong>{a.title}</strong>
                    {a.signal_source ? (
                      <span className="fd-phase">
                        <Radio size={10} strokeWidth={2.6} />
                        {t(`faults.phase.${a.signal_source}`, {
                          defaultValue: a.signal_source
                        })}
                      </span>
                    ) : null}
                  </div>
                  <div className="fd-alarm-sub">
                    {a.device_name ?? a.device_code ?? "—"}
                    <span>·</span>
                    {fmtClock(a.created_at, localeTag)}
                    {a.acknowledged ? (
                      <>
                        <span>·</span>
                        {t("faults.card.alarmAcked")}
                      </>
                    ) : null}
                  </div>
                </li>
              ))}
            </ul>
          )}
        </section>

        {mapView && mapView.rangePoles.length > 0 ? (
          <section className="fd-card">
            <header className="fd-card-head">
              <h2>
                <CircleDot size={15} />
                {t("faults.detail.rangePolesTitle")}
              </h2>
            </header>
            <ul className="fd-poles">
              {mapView.rangePoles.map((rp) => (
                <li
                  key={rp.id}
                  className={rp.isStart ? "is-start" : rp.isEnd ? "is-end" : undefined}
                >
                  <span className="fd-pole-seq">#{rp.sequence_no}</span>
                  <strong>{rp.name}</strong>
                  {rp.isStart ? (
                    <span className="fd-tag fd-tag--red">{t("faults.detail.poleRangeStart")}</span>
                  ) : rp.isEnd ? (
                    <span className="fd-tag fd-tag--green">{t("faults.detail.poleRangeEnd")}</span>
                  ) : null}
                </li>
              ))}
            </ul>
          </section>
        ) : null}

        {/* Ana hattaki ariza bir dallanma diregini kapsiyorsa o kol da
            enerjisiz kalir; ekip sahada kolu da kontrol etmelidir. */}
        {(fault.affected_branches?.length ?? 0) > 0 ? (
          <section className="fd-card">
            <header className="fd-card-head">
              <h2>
                <GitBranch size={15} />
                {t("faults.card.branchesTitle")}
              </h2>
            </header>
            <ul className="fd-branches">
              {fault.affected_branches!.map((b) => (
                <li key={b.line_id} className={b.has_own_fault ? "is-confirmed" : undefined}>
                  <strong>{b.line_name}</strong>
                  <small>
                    {t("faults.card.branchAt", {
                      pole: b.branch_pole_name || `#${b.branch_pole_seq ?? "?"}`
                    })}
                  </small>
                  <span className={`fd-tag ${b.has_own_fault ? "fd-tag--red" : "fd-tag--amber"}`}>
                    {b.has_own_fault
                      ? t("faults.card.branchConfirmed")
                      : t("faults.card.branchCheck")}
                  </span>
                </li>
              ))}
            </ul>
          </section>
        ) : null}

        {/* Tekrar eden ariza baska bir istir: cozulmemis bir kok sebep
            vardir (agac, izolator, kacak). Ekip bunu bilerek gitsin. */}
        {recurrence ? (
          <section className="fd-card">
            <header className="fd-card-head">
              <h2>
                <History size={15} />
                {t("faults.card.historyTitle")}
              </h2>
            </header>
            {recurrence.total === 0 ? (
              <p className="fd-empty">{t("faults.card.historyNone")}</p>
            ) : (
              <div className="fd-repeat">
                <div className="fd-repeat-head">
                  <strong>{recurrence.total}</strong>
                  <span>{t("faults.card.historyCount", { days: recurrence.windowDays })}</span>
                </div>
                <ul>
                  {recurrence.sameSection > 0 ? (
                    <li className="is-hit">
                      {t("faults.card.historySameSection", { count: recurrence.sameSection })}
                    </li>
                  ) : null}
                  {recurrence.lastAt ? (
                    <li>
                      {t("faults.card.historyLast", {
                        at: fmtDate(recurrence.lastAt, localeTag)
                      })}
                    </li>
                  ) : null}
                </ul>
              </div>
            )}
          </section>
        ) : null}
      </div>

      {/* ================= 4) COZUM & NOTLAR | SAHA RAPORU ================= */}
      {/* ISLEM POPUP'I — sebep/cozum ve yorumlar.
          Bu iki kart sayfanin EN ALTINDAYDI: kullanici sebep girmek ya da
          yorum yazmak icin her seferinde asagi kaydiriyordu. Artik KPI
          seridindeki dugmelerden aciliyor; sayfa da kisaldi. */}
      {islemModal ? (
        <div
          className="fd-modal-backdrop"
          onClick={() => setIslemModal(null)}
          role="presentation"
        >
          <div
            className="fd-modal"
            role="dialog"
            aria-modal="true"
            onClick={(e) => e.stopPropagation()}
          >
            <button
              type="button"
              className="fd-modal-close"
              onClick={() => setIslemModal(null)}
              aria-label={t("common.close")}
            >
              <X size={16} />
            </button>
          <div className="fd-row fd-row--solve">
            <section className="fd-card fd-card--solve">
              <header className="fd-card-head">
                <h2>
                  <TriangleAlert size={15} />
                  {t("faults.detail.solveTitle")}
                </h2>
                <small>{t("faults.detail.causeHint")}</small>
              </header>

              {suggestedCause ? (
                <div className="fd-suggestion">
                  <Lightbulb size={14} />
                  <span>{t("faults.detail.causeSuggested", { cause: suggestedCause.label })}</span>
                  {canEdit && causeDraft !== suggestedCause.code ? (
                    <button type="button" onClick={() => setCauseDraft(suggestedCause.code)}>
                      {t("faults.detail.causeUseSuggestion")}
                    </button>
                  ) : null}
                </div>
              ) : null}

              {/* SEBEP: acilir liste degil ETIKET IZGARASI. 19 sebebi bir
                  dropdown'un icinde aramak, eldivenli elle telefonun basindayken
                  en yavas yoldu; grup grup duran etiketler tek dokunusla secilir.
                  Secili etikete tekrar basmak secimi GERI ALIR (yanlis secim
                  duzeltilebilmeli). */}
              {causeCatalog === null ? (
                <p className="fd-empty">{t("faults.detail.causeCatalogEmpty")}</p>
              ) : (
                <div className="fd-causes">
                  {causeGroups.map(([grup, liste]) => (
                    <div key={grup} className="fd-cause-group">
                      <span className="fd-cause-group-label">
                        {t(`faults.causeGroup.${grup}`, { defaultValue: grup })}
                      </span>
                      <div className="fd-chips">
                        {liste.map((c) => (
                          <button
                            key={c.code}
                            type="button"
                            className={`fd-chip ${causeDraft === c.code ? "is-on" : ""}`}
                            disabled={saving || !canEdit}
                            onClick={() => setCauseDraft(causeDraft === c.code ? "" : c.code)}
                          >
                            {causeDraft === c.code ? <Check size={12} strokeWidth={3} /> : null}
                            {causeLabel(c)}
                          </button>
                        ))}
                      </div>
                    </div>
                  ))}
                </div>
              )}

              <div className="fd-fields">
                <label className="fd-field">
                  <span className="fd-label">{t("faults.detail.causeDetailLabel")}</span>
                  <textarea
                    className="fd-textarea"
                    rows={3}
                    value={causeDetailDraft}
                    onChange={(e) => setCauseDetailDraft(e.target.value)}
                    disabled={saving || !canEdit}
                    placeholder={t("faults.detail.causeDetailPlaceholder")}
                  />
                </label>
                <label className="fd-field">
                  <span className="fd-label">{t("faults.detail.writeNote")}</span>
                  <textarea
                    className="fd-textarea"
                    rows={3}
                    value={noteDraft}
                    onChange={(e) => setNoteDraft(e.target.value)}
                    disabled={saving || !canEdit}
                    placeholder={t("faults.detail.writeNotePlaceholder")}
                  />
                  <small className="fd-field-hint">{t("faults.detail.writeNoteHint")}</small>
                </label>
              </div>

              {canEdit ? (
                <footer className="fd-solve-foot">
                  <span className={`fd-dirty ${dirty ? "is-on" : ""}`}>
                    {dirty ? t("faults.detail.unsaved") : t("faults.detail.saved")}
                  </span>
                  <button
                    type="button"
                    className="fd-save"
                    onClick={() =>
                      void calistir(async () => {
                        // Yalnizca DEGISENI gonder: her kaydette iki ucu birden
                        // cagirmak olay kaydini (record_event) sahte "guncellendi"
                        // satirlariyla doldururdu.
                        if (causeDirty) {
                          await onUpdateCause(fault.id, {
                            // Bos secim = sebebi GERI AL.
                            cause_code: causeDraft || null,
                            cause_detail: causeDetailDraft.trim() || null
                          });
                        }
                        if (noteDirty) await onUpdateNote(fault.id, noteDraft.trim() || null);
                      }, "common.errorOccurred")
                    }
                    disabled={saving || !dirty}
                  >
                    <Save size={14} />
                    {t("faults.detail.saveAll")}
                  </button>
                </footer>
              ) : null}

              {/* ---- KAPATMA ----
                  Ariza yalnizca SAHADA DUZELDIKTEN sonra kapatilabilir.
                  `resolved` gecisini cihaz belirler (alarm kalkinca otomatik);
                  kullanicinin isi duzelen arizayi raporlayip kapatmaktir.
                  Acik bir arizada kapatma YOK: aksi halde sahada devam eden is
                  ekrandan duser ve kimse ilgilenmedigi halde kapali gorunur. */}
              {canEdit && fault.status !== "closed" ? (
                <div className="fd-close-box">
                  {!fault.resolved_at ? (
                    <p className="fd-close-locked">
                      <CircleDot size={13} />
                      {t("faults.detail.closeLocked")}
                    </p>
                  ) : (
                    <>
                      <label className="fd-field">
                        <span className="fd-label">{t("faults.detail.resolutionNote")}</span>
                        <textarea
                          className="fd-textarea"
                          rows={2}
                          value={resolutionDraft}
                          onChange={(e) => setResolutionDraft(e.target.value)}
                          disabled={saving}
                          placeholder={t("faults.detail.resolutionNotePlaceholder")}
                        />
                        <small className="fd-field-hint">
                          {t("faults.detail.resolutionNoteHint")}
                        </small>
                      </label>
                      <button
                        type="button"
                        className="fd-save fd-save--close"
                        disabled={saving || !resolutionDraft.trim()}
                        onClick={() =>
                          void calistir(
                            () => onUpdateStatus(fault.id, "closed", resolutionDraft.trim()),
                            "common.errorOccurred"
                          )
                        }
                      >
                        <Check size={14} />
                        {t("faults.detail.closeFault")}
                      </button>
                    </>
                  )}
                </div>
              ) : null}
            </section>

            <section className="fd-card fd-card--talk">
              <header className="fd-card-head">
                <h2>
                  <MessagesSquare size={15} />
                  {t("faults.detail.commentsTitle")}
                  {comments.length > 0 ? <span className="fd-count">{comments.length}</span> : null}
                </h2>
                <small>{t("faults.detail.commentsAddPlaceholder")}</small>
              </header>
              <ul className="fd-comments">
                {comments.length === 0 ? (
                  <li className="fd-empty">{t("faults.detail.commentsHint")}</li>
                ) : (
                  comments.map((c) => (
                    <li
                      key={c.id}
                      className={`fd-comment ${c.author_username === currentUsername ? "is-mine" : ""}`}
                    >
                      <header>
                        <span className="fd-avatar fd-avatar--sm">
                          {bashafler(c.author_username)}
                        </span>
                        <strong>{c.author_username}</strong>
                        <time>{fmtDate(c.created_at, localeTag)}</time>
                      </header>
                      <p>{c.body}</p>
                    </li>
                  ))
                )}
              </ul>
              {canEdit ? (
                <div className="fd-comment-add">
                  <textarea
                    className="fd-textarea"
                    rows={2}
                    placeholder={t("faults.detail.commentPlaceholder")}
                    value={commentDraft}
                    onChange={(e) => setCommentDraft(e.target.value)}
                    disabled={saving}
                  />
                  <button
                    type="button"
                    className="fd-save fd-save--send"
                    onClick={() =>
                      void calistir(async () => {
                        const body = commentDraft.trim();
                        if (!body) return;
                        await onAddComment(fault.id, body);
                        setComments(await onLoadComments(fault.id));
                        setCommentDraft("");
                      }, "alarms.errors.commentFailed")
                    }
                    disabled={saving || !commentDraft.trim()}
                  >
                    <Send size={14} />
                    {t("faults.detail.addCommentBtn")}
                  </button>
                </div>
              ) : null}
            </section>
          </div>
          </div>
        </div>
      ) : null}
    </div>
  );
}

function Metric({
  Icon,
  label,
  value,
  not,
  canli,
  renk
}: {
  Icon: typeof Timer;
  label: string;
  value: string;
  not?: string;
  canli?: boolean;
  /** Durum metrigi icin vurgu rengi (rozet yerine gecer). */
  renk?: string;
}) {
  return (
    <div className={`fd-metric ${canli ? "is-live" : ""}`} title={not}>
      <span
        className="fd-metric-icon"
        style={renk ? { background: `${renk}18`, color: renk } : undefined}
      >
        <Icon size={16} />
      </span>
      <span className="fd-metric-body">
        <span className="fd-metric-label">{label}</span>
        <strong className="fd-metric-value" style={renk ? { color: renk } : undefined}>
          {canli ? <i className="fd-pulse" aria-hidden="true" /> : null}
          {value}
        </strong>
      </span>
    </div>
  );
}
