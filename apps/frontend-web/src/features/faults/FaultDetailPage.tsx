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
 *
 * ISLEMLER SAYFADA DEGIL, KENDI EKRANLARINDA
 * ------------------------------------------
 * Sebep girisi ve saha yorumlari once sayfanin dibinde iki kartti, sonra
 * ikisi TEK popup'a konuldu. Ikincisi de yanlisti: sebep bir siniflandirma,
 * yorum ise serbest metinli bir akis; yan yana durunca hangisinin kalici
 * kayit oldugu belirsizdi. Artik ust seritteki eylem cubugundan acilan iki
 * ayri ekran var:
 *
 *   FaultResolveModal      -> sebep + cozum, ve KAPATMA
 *   FaultFieldReportModal  -> saha raporu (yorum akisi)
 *
 * KAPATMA NORMALE DONUSE BAGLI: ariza sahada duzelmeden (`resolved_at`)
 * kapatilamaz — dugme kilitli durur ve neden kilitli oldugunu yazar.
 * Backend de ayni kurali dogrular (409). Acik bir arizanin kapatilabilmesi,
 * sahada devam eden isin ekrandan dusmesi demekti.
 *
 * KAPATILMIS ARIZA SALT OKUNUR: rapor alinabilir, yorum/sebep degistirilemez.
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

import {
  Activity,
  ArrowRight,
  Check,
  CircleDot,
  FileDown,
  GitBranch,
  History,
  Lock,
  MapPin,
  MessagesSquare,
  Radio,
  Route,
  Timer,
  TriangleAlert,
  UserRound,
  Wrench,
  Zap
} from "lucide-react";
import { LayersControl, MapContainer, Marker, Polyline, Tooltip } from "react-leaflet";
import L from "leaflet";

import { buildFaultMapView } from "./faultMapView";
import { buildFaultRecurrence } from "./faultRecurrence";
import { FitFocus } from "./FaultMapFocus";
import { FaultResolveModal } from "./FaultResolveModal";
import { bashafler, FaultFieldReportModal } from "./FaultFieldReportModal";

import { MapLayerSwitchFix } from "../../components/MapLayerSwitchFix";
import { ResilientTileLayer } from "../../components/ResilientTileLayer";
import {
  downloadFaultReport,
  fetchFault,
  fetchFaultCauses,
  type GridSnapshot
} from "../../shared/api";
import { formatDistanceM, formatDistanceRange } from "../../shared/lineDistance";
import { MAP_LAYERS } from "../../shared/mapTiles";
import type {
  AlarmEvent,
  DeviceRow,
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

function fmtDate(iso: string | null | undefined, localeTag: string): string {
  if (!iso) return "—";
  return new Date(iso).toLocaleString(localeTag);
}

function fmtClock(iso: string | null | undefined, localeTag: string): string {
  if (!iso) return "—";
  return new Date(iso).toLocaleTimeString(localeTag, { hour: "2-digit", minute: "2-digit" });
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
  /** Acik islem ekrani. Taslaklarin tamami o ekranlarin ICINDE tutulur;
   *  ekran unmount oldugunda kendiliginden temizlenir ve arka planda liste
   *  tazelendiginde kullanicinin yazdigi silinmez. */
  const [islemModal, setIslemModal] = useState<null | "close" | "solve" | "comments">(
    null
  );
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [mapFocus, setMapFocus] = useState<"zone" | "line" | "grid">("zone");
  /** PDF sunucuda uretilir (uydu karolari + reportlab); bir kac saniye
   *  surebilir, o yuzden dugme beklemede kilitlenir. */
  const [raporUretiliyor, setRaporUretiliyor] = useState(false);

  // Baska bir ariza sekmesine gecildiyse acik islem ekrani kapansin —
  // aksi halde diyalog onceki kaydin taslagiyla acik kalirdi.
  useEffect(() => {
    setIslemModal(null);
    setError("");
  }, [faultId]);

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

  /** ARIZA RAPORU (PDF) — sunucuda uretilir, burada indirilir.
   *
   *  Tarayicinin yazdirma diyalogu YOK: rapor artik gercek bir belge
   *  (`services/fault_report_service.py`) ve dosya adi da sunucudan gelir. */
  const raporIndir = useCallback(async () => {
    setRaporUretiliyor(true);
    setError("");
    try {
      const { blob, filename } = await downloadFaultReport(accessToken, faultId);
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = filename;
      anchor.click();
      URL.revokeObjectURL(url);
    } catch (err) {
      setError(err instanceof Error ? err.message : t("faults.detail.exportPdfFailed"));
    } finally {
      setRaporUretiliyor(false);
    }
  }, [accessToken, faultId, t]);

  /** ISLEM EKRANLARININ UCLARI — hata FIRLATIRLAR.
   *
   *  `calistir` hatayi yutup sayfaya yaziyor; diyalog icinde ise hata
   *  eylemin YANINDA gorunmeli ve diyalog acik kalmali (kullanici yazdigini
   *  kaybetmesin). Bu yuzden bunlar ayri. */
  const sebepKaydet = useCallback(
    async (kod: string | null, ayrinti: string | null) => {
      await onUpdateCause(faultId, { cause_code: kod, cause_detail: ayrinti });
      if (!listeKaydi) await cek();
    },
    [faultId, listeKaydi, cek, onUpdateCause]
  );

  const notKaydet = useCallback(
    async (not: string | null) => {
      await onUpdateNote(faultId, not);
      if (!listeKaydi) await cek();
    },
    [faultId, listeKaydi, cek, onUpdateNote]
  );

  const arizayiKapat = useCallback(
    async (cozumNotu: string) => {
      await onUpdateStatus(faultId, "closed", cozumNotu);
      if (!listeKaydi) await cek();
    },
    [faultId, listeKaydi, cek, onUpdateStatus]
  );

  const yorumEkle = useCallback(
    async (body: string) => {
      await onAddComment(faultId, body);
      setComments(await onLoadComments(faultId));
    },
    [faultId, onAddComment, onLoadComments]
  );

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
    // YON GOSTERILMIYOR — kart kunyesiyle ayni karar (bkz. ActiveFaultCard):
    // "ileri/geri" bayragi kelepcenin takilis yonune gore anlam degistiriyor.
    // Alan DB'de duruyor, yalnizca kunyede yazilmiyor.
    ekle("zone", t("faults.card.specZoneCode"), fault.zone_code ?? null);
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

  /** Araligin ORTA NOKTASI — sahaya cikan kisiye verilecek tek sayi.
   *
   *  BU HOOK ERKEN `return`DEN ONCE OLMAK ZORUNDA.
   *
   *  Asagida "ariza bulunamadi" dali var ve kayit listede yokken (sekme
   *  yenilenmis, kapanmis ariza, kayit silinmis) ILK render oradan donuyor.
   *  Bu hook o return'un ALTINDAYDI: ilk render'da calismiyor, kayit
   *  gelince calisiyordu. React icin hook SAYISI degismis oluyor ve
   *  "Rendered more hooks than during the previous render" hatasi RENDER
   *  sirasinda firliyordu — ErrorBoundary tum uygulamayi yutuyor, ekran
   *  kitleniyor, tek care sayfayi yenilemek oluyordu.
   *
   *  Kural: kosullu return'den once TUM hook'lar cagrilmali; `fault` null
   *  olabilecegi icin govde de null'a dayanikli. */
  const tahminiMesafe = useMemo(() => {
    const a = fault?.zone_start_m;
    const b = fault?.zone_end_m;
    if (typeof a !== "number" && typeof b !== "number") return "—";
    const orta = typeof a === "number" && typeof b === "number" ? (a + b) / 2 : (a ?? b)!;
    return orta >= 1000 ? `~${(orta / 1000).toFixed(2)} km` : `~${Math.round(orta)} m`;
  }, [fault?.zone_start_m, fault?.zone_end_m]);

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
  const assigneeName = fault.assigned_to_full_name ?? fault.assigned_to_username ?? null;
  const distanceText = formatDistanceRange(fault.zone_start_m, fault.zone_end_m);
  // `tahminiMesafe` YUKARIDA, erken return'den ONCE hesaplaniyor (hook
  // sirasi sabit kalsin diye); burada yeniden tanimlanmaz.

  /** KAPATILMIS ARIZA SALT OKUNUR: rapor alinir, kayit degistirilmez.
   *  Kapanmis bir kayda sonradan yorum/sebep eklenmesi, arsivlenen raporun
   *  sessizce degismesi demek olurdu. */
  const kapali = fault.status === "closed";
  /** Kapatma yalnizca ariza SAHADA DUZELDIKTEN sonra. `resolved_at`i cihaz
   *  yazar (alarm kalkinca otomatik); backend de ayni kurali dogrular. */
  const normaleDondu = Boolean(fault.resolved_at);
  const islemYapilabilir = canEdit && !kapali;

  return (
    <div className="fd-page">
      {/* ---- Ust serit: kunye + olculer ---- */}
      <header className="fd-head">
        <div className="fd-head-top">
          {/* ---- KIMLIK ----
              Onceki hali: kirinti ("BOLGE / HAT / Direk #1 — Direk #2") ile
              baslik AYNI satirda, ayni taban cizgisindeydi. Uc sorun:

                1. Hat adi IKI KERE geciyordu (kirintida ve baslikta).
                2. Tek bir dizi gibi okunuyordu — "Direk #1 — Direk #2 BR-4 #4"
                   nerede yol bitip baslik basliyor belirsizdi.
                3. Kayit bir BRANSMAN KOLU ise hangi ana hattin kolu oldugu
                   hicbir yerde yazmiyordu; "BR-4 nerede" sorusu ekranda
                   cevapsizdi.

              Artik iki kademe: ustte BASLIK (hat adi + arizanin kesimi + kayit
              no), altinda konum yolu (bolge, kol ise ana hat). Hat adi ve
              kesim ayni satirda ama AYNI AGIRLIKTA DEGIL — kesim daha soluk,
              boylece tek satir olmasina ragmen iki ayri bilgi gibi okunur. */}
          <div className="fd-head-id">
            <h1 className="fd-title">
              <span className="fd-title-line">{fault.line_name}</span>
              {fault.from_pole_seq != null && fault.to_pole_seq != null ? (
                <>
                  <em className="fd-title-sep" aria-hidden="true">
                    ·
                  </em>
                  <span className="fd-title-span">
                    {t("faults.card.rangeText", {
                      from: fault.from_pole_seq,
                      to: fault.to_pole_seq
                    })}
                  </span>
                </>
              ) : null}
              <span className="fd-record">#{fault.id}</span>
            </h1>
            <nav className="fd-breadcrumb" aria-label={t("faults.detail.locationLabel")}>
              <MapPin size={13} />
              <span>{fault.region_name}</span>
              {fault.is_branch_line && fault.parent_line_name ? (
                <>
                  <em aria-hidden="true">›</em>
                  <span>
                    {t("faults.detail.branchOfLine", { line: fault.parent_line_name })}
                  </span>
                </>
              ) : null}
            </nav>
          </div>

          {/* ---- EYLEM CUBUGU ----
              Once olcum seridinin ICINDE bir hucreydi: dugmeler kartlarin
              arasinda ikinci satira tasiyor, kutu icinde kutu gibi
              duruyordu. Artik basligin sag ucunda kendi seridinde —
              once bilgilendirici (rapor/okuma), sonra ayirac, sonra
              kayda dokunan eylemler. */}
          <div className="fd-actions">
            {/* PDF: BACKEND uretir (`/faults/{id}/report.pdf`). Eskiden
                `window.print()` cagriliyordu; cikan sey bir belge degil
                EKRANIN kagida dokulmus haliydi ve kapanmis arizada harita
                bos cikiyordu (kirmizi bolge canli alarm durumundan
                turetiliyor). Kapatilmis arizada da calisir: rapor her zaman
                alinabilmeli. */}
            <button
              type="button"
              className="fd-act"
              onClick={() => void raporIndir()}
              disabled={raporUretiliyor}
            >
              <FileDown size={15} />
              {raporUretiliyor ? t("faults.detail.exportPdfBusy") : t("faults.detail.exportPdf")}
            </button>
            <button
              type="button"
              className="fd-act"
              onClick={() => setIslemModal("comments")}
            >
              <MessagesSquare size={15} />
              {t("faults.detail.actionFieldReport")}
              {comments.length > 0 ? (
                <span className="fd-act-count">{comments.length}</span>
              ) : null}
            </button>

            {islemYapilabilir ? (
              <>
                <span className="fd-act-sep" aria-hidden="true" />
                <button
                  type="button"
                  className="fd-act"
                  onClick={() => setIslemModal("solve")}
                >
                  <Wrench size={15} />
                  {t("faults.detail.actionSolve")}
                </button>
                {/* KAPATMA KILITLI: ariza sahada duzelmeden (`resolved_at`)
                    kapatilamaz. Dugmeyi gizlemek yerine KILITLI gostermek,
                    "kapatma nerede" sorusunu ekranda cevaplar. */}
                <button
                  type="button"
                  className={`fd-act fd-act--go ${normaleDondu ? "" : "is-locked"}`}
                  onClick={() => setIslemModal("close")}
                  disabled={!normaleDondu}
                  title={normaleDondu ? undefined : t("faults.detail.closeLocked")}
                >
                  {normaleDondu ? (
                    <Check size={15} strokeWidth={2.8} />
                  ) : (
                    <Lock size={14} />
                  )}
                  {normaleDondu
                    ? t("faults.detail.closeFault")
                    : t("faults.detail.closeWaiting")}
                </button>
              </>
            ) : null}

            {kapali ? (
              <span className="fd-act-note" title={t("faults.detail.closedReadOnlyHint")}>
                <Lock size={13} />
                {t("faults.detail.closedReadOnly")}
              </span>
            ) : null}
          </div>
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

      {/* ---- ISLEM EKRANLARI ----
          Sebep/cozum ve saha raporu AYRI iki ekran. Once ikisi ayni popup'in
          icinde yan yanaydi; sebep bir siniflandirma, yorum ise serbest
          metinli bir akis oldugu icin ayni kutuda hangisinin kalici kayit
          oldugu belirsiz kaliyordu. */}
      {islemModal === "solve" || islemModal === "close" ? (
        <FaultResolveModal
          fault={fault}
          mod={islemModal === "close" ? "kapat" : "duzenle"}
          catalog={causeCatalog}
          onKapat={() => setIslemModal(null)}
          onSebepKaydet={sebepKaydet}
          onNotKaydet={notKaydet}
          onArizayiKapat={arizayiKapat}
        />
      ) : null}

      {islemModal === "comments" ? (
        <FaultFieldReportModal
          fault={fault}
          comments={comments}
          currentUsername={currentUsername}
          canComment={islemYapilabilir}
          localeTag={localeTag}
          onKapat={() => setIslemModal(null)}
          onYorumEkle={yorumEkle}
        />
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
