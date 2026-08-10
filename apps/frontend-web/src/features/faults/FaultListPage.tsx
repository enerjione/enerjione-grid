/**
 * Hat Arizalari sayfasi.
 *
 * Duzen:
 *   1. KPI seridi        — Aktif Ariza / Bugun Cozulen / Ort. Cozum / Gecmis
 *   2. Sekmeler          — "Aktif Ariza" | "Gecmis Arizalar"
 *   3. Aktif sekmesi     — her ARIZA BOLGESI icin bir kart (ActiveFaultCard)
 *      Gecmis sekmesi    — tablo + acilir yorum/cozum panelleri
 *
 * ONEMLI — aktif/gecmis ayrimi:
 *   Ana ekranda YALNIZCA gercekten devam eden arizalar gorunur:
 *     aktif  = open | assigned | in_progress
 *     gecmis = resolved | closed
 *   Backend ariza normale donunce status'u otomatik "resolved" yapar
 *   (fault_recompute_service._resolve_fault), yani kayit kendiliginden
 *   Gecmis sekmesine gecer; kullanicinin elle isaretlemesi gerekmez.
 *
 * Bir hatta birden fazla bagimsiz ariza bolgesi olabilir (backend
 * _compute_line_zones her RED blogu icin ayri FaultEvent uretir); bu yuzden
 * aktif sekmesi tek kart degil KART LISTESI render eder.
 */
import { useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { CheckCircle2, Clock, FileText, RefreshCw, TriangleAlert } from "lucide-react";

import type { GridSnapshot } from "../../shared/api";
import { fetchFaultCauses } from "../../shared/api";
import type {
  AlarmEvent,
  DeviceRow,
  FaultCauseCatalog,
  FaultComment,
  FaultEvent,
  FaultStats,
  UserRead
} from "../../shared/types";
import { ActiveFaultCard } from "./ActiveFaultCard";
import type { StripBranch, StripPole } from "./FaultPoleStrip";
import { FaultDetailModal } from "./FaultDetailModal";
import { FaultHistoryTable } from "./FaultHistoryTable";

type Props = {
  faults: FaultEvent[];
  /** Backend'den gelen ozet istatistikler (avg_resolution_seconds vb).
   * null ise henuz yuklenmedi/erisilmedi — chip'te "—" gosterilir. */
  stats?: FaultStats | null;
  users: UserRead[];
  currentUsername: string;
  canAssign: boolean; // engineer/installer
  loading?: boolean;
  /** Son cekim basarisiz olduysa mesaj.
   *
   *  NEDEN VAR: bu sayfa hata durumunu HIC bilmiyordu. `pollFaults` hatayi
   *  `catch { }` ile yutuyor, `loading` ise sabit `false` geciliyordu.
   *  Sonuc: istemci veriyi HIC alamamis olsa bile ekranda yesil tik ve
   *  "Aktif ariza yok — Sistem temiz" yaziyordu.
   *
   *  Nobetci operator telefonla "X hattinda ariza var mi?" sorusunu alir,
   *  sekmeyi acar, yesil tik gorur ve "yok" der. Bir ariza izleme urununde
   *  en agir hata sinifi budur: sistem BILMEDIGINI "sorun yok" diye
   *  gosteriyor. */
  error?: string;
  gridSnapshot?: GridSnapshot | null;
  devices?: DeviceRow[];
  alarms?: AlarmEvent[];
  onAssign: (faultId: number, username: string | null) => Promise<void>;
  onUpdateStatus: (faultId: number, status: string) => Promise<void>;
  onUpdateNote: (faultId: number, note: string | null) => Promise<void>;
  /** Ariza sebebi — analiz katmaninin ogrenecegi tek insan etiketi. */
  onUpdateCause: (
    faultId: number,
    payload: { cause_code: string | null; cause_detail?: string | null }
  ) => Promise<void>;
  /** Oturum token'i — sebep katalogunu BIR KEZ cekmek icin. */
  accessToken: string;
  onLoadComments: (faultId: number) => Promise<FaultComment[]>;
  onAddComment: (faultId: number, body: string) => Promise<void>;
};

/** Devam eden ariza statusleri — ana ekranda yalnizca bunlar gorunur. */
const ACTIVE_STATUSES = new Set(["open", "assigned", "in_progress"]);

function fmtDurationSeconds(totalSec: number): string {
  let sec = Math.max(0, Math.round(totalSec));
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

export function FaultListPage({
  faults,
  stats: backendStats,
  users,
  currentUsername,
  canAssign,
  loading,
  error,
  gridSnapshot,
  devices,
  alarms,
  onAssign,
  onUpdateStatus,
  onUpdateNote,
  onUpdateCause,
  accessToken,
  onLoadComments,
  onAddComment
}: Props) {
  const { t, i18n } = useTranslation();
  const localeTag = i18n.language?.startsWith("tr") ? "tr-TR" : "en-US";
  const [tab, setTab] = useState<"active" | "history">("active");
  const [openFaultId, setOpenFaultId] = useState<number | null>(null);

  // Canli sure sayaci — kartlardaki "x sa y dk" guncel kalsin. Kart sayisi
  // az, 30sn'lik tick yeterli (ms hassasiyet anlamsiz).
  const [now, setNow] = useState<number>(() => Date.now());
  useEffect(() => {
    const id = window.setInterval(() => setNow(Date.now()), 30_000);
    return () => window.clearInterval(id);
  }, []);

  const activeFaults = useMemo(
    () =>
      faults
        .filter((f) => ACTIVE_STATUSES.has(f.status))
        .sort((a, b) => new Date(b.opened_at).getTime() - new Date(a.opened_at).getTime()),
    [faults]
  );

  const historyFaults = useMemo(
    () => faults.filter((f) => f.status === "resolved" || f.status === "closed"),
    [faults]
  );

  /** Sebep katalogu — sayfa basina BIR KEZ. Backend tek kaynak
   *  (`app/data/fault_causes.py`); frontend'e gomulseydi ikisi ayrisir ve
   *  arayuzde secilen kod backend'de taninmaz olurdu. */
  const [causeCatalog, setCauseCatalog] = useState<FaultCauseCatalog | null>(null);
  useEffect(() => {
    let iptal = false;
    fetchFaultCauses(accessToken)
      .then((k) => {
        if (!iptal) setCauseCatalog(k);
      })
      .catch(() => {
        // Katalog alinamazsa sebep secimi devre disi kalir; sayfanin geri
        // kalani (ariza listesi) etkilenmez.
        if (!iptal) setCauseCatalog(null);
      });
    return () => {
      iptal = true;
    };
  }, [accessToken]);

  /** line_id -> hattin tum direk sira numaralari (sematik serit icin). */
  const poleSeqsByLine = useMemo(() => {
    const m = new Map<number, number[]>();
    for (const p of gridSnapshot?.poles ?? []) {
      const arr = m.get(p.line_id);
      if (arr) arr.push(p.sequence_no);
      else m.set(p.line_id, [p.sequence_no]);
    }
    for (const arr of m.values()) arr.sort((a, b) => a - b);
    return m;
  }, [gridSnapshot]);

  /** line_id -> direk AD ve ROL bilgisi. Cizimde sira numarasi yerine ad
   *  gosterilir (saha ekibi direkleri adiyla taniyor) ve bransman direkleri
   *  ayri bir sembolle isaretlenir. */
  const polesByLine = useMemo(() => {
    const m = new Map<number, StripPole[]>();
    for (const p of gridSnapshot?.poles ?? []) {
      const item: StripPole = {
        seq: p.sequence_no,
        name: p.name ?? null,
        role: p.topology_role ?? null
      };
      const arr = m.get(p.line_id);
      if (arr) arr.push(item);
      else m.set(p.line_id, [item]);
    }
    for (const arr of m.values()) arr.sort((a, b) => a.seq - b.seq);
    return m;
  }, [gridSnapshot]);

  /** line_id -> hattin segmentleri. Cihazlar TELIN UZERINDE bunlardan cizilir
   *  (`device_position_t` segment icindeki gercek konumu verir). */
  const segmentsByLine = useMemo(() => {
    type Seg = NonNullable<typeof gridSnapshot>["segments"][number];
    const m = new Map<number, Seg[]>();
    for (const s of gridSnapshot?.segments ?? []) {
      const arr = m.get(s.line_id);
      if (arr) arr.push(s);
      else m.set(s.line_id, [s]);
    }
    return m;
  }, [gridSnapshot]);

  /** line_id -> o hattan ayrilan bransman kollari (cizimde dal olarak
   *  gosterilir). Kol AYRI bir Line'dir ve `branched_from_pole_id` ile ana
   *  hattaki dallanma diregine baglanir. */
  const branchesByLine = useMemo(() => {
    const m = new Map<number, StripBranch[]>();
    // Kolda kendi ariza kaydi varsa cizimde kirmizi gorunmeli: ana hattaki
    // ariza araligina girmese de kol arizali olabilir.
    const aktifArizaliHatlar = new Set(
      faults.filter((f) => ACTIVE_STATUSES.has(f.status)).map((f) => f.line_id)
    );
    const poleById = new Map((gridSnapshot?.poles ?? []).map((p) => [p.id, p]));
    for (const ln of gridSnapshot?.lines ?? []) {
      if (!ln.branched_from_pole_id) continue;
      const anchor = poleById.get(ln.branched_from_pole_id);
      if (!anchor) continue;
      const kolDirekleri = (gridSnapshot?.poles ?? [])
        .filter((p) => p.line_id === ln.id)
        .sort((a, b) => a.sequence_no - b.sequence_no);
      const kol: StripBranch = {
        lineId: ln.id,
        name: ln.name,
        atSeq: anchor.sequence_no,
        poleCount: kolDirekleri.length,
        // Kolun KENDI direkleri — dal kati bunlari cizer, kol tek bir
        // cizgi ucu degil kendi hattidir.
        poles: kolDirekleri.map((p) => ({
          seq: p.sequence_no,
          name: p.name ?? null,
          role: p.topology_role ?? null
        })),
        // Kolda O AN acik bir ariza var mi — varsa dal kirmizi cizilir.
        hasFault: aktifArizaliHatlar.has(ln.id)
      };
      const arr = m.get(anchor.line_id);
      if (arr) arr.push(kol);
      else m.set(anchor.line_id, [kol]);
    }
    return m;
  }, [gridSnapshot, faults]);

  /** Sekmelerde secili aktif ariza. Kayit listeden dusunce (cozuldu) ilk
   *  siradakine duser — bos ekran gostermek yerine. */
  const [activeFaultId, setActiveFaultId] = useState<number | null>(null);
  const shownFault = useMemo(
    () =>
      activeFaults.find((f) => f.id === activeFaultId) ?? activeFaults[0] ?? null,
    [activeFaults, activeFaultId]
  );

  const openFault = useMemo(
    () => (openFaultId !== null ? faults.find((f) => f.id === openFaultId) ?? null : null),
    [faults, openFaultId]
  );

  const avgText =
    backendStats && backendStats.avg_resolution_seconds != null
      ? fmtDurationSeconds(backendStats.avg_resolution_seconds)
      : "—";

  const kpis = [
    {
      key: "active",
      tone: "bad",
      icon: TriangleAlert,
      label: t("faults.kpi.active"),
      value: String(activeFaults.length)
    },
    {
      key: "today",
      tone: "ok",
      icon: CheckCircle2,
      label: t("faults.kpi.resolvedToday"),
      value: String(backendStats?.resolved_today_count ?? 0)
    },
    {
      key: "avg",
      tone: "info",
      icon: Clock,
      label: t("faults.kpi.avgResolution"),
      value: avgText
    },
    {
      key: "history",
      tone: "muted",
      icon: FileText,
      label: t("faults.kpi.history"),
      value: String(historyFaults.length)
    }
  ] as const;

  return (
    <div className="faults-page">
      {/* ---- KPI seridi ---- */}
      <div className="fx-kpis">
        {kpis.map((k) => {
          const Icon = k.icon;
          return (
            <article key={k.key} className={`fx-kpi fx-kpi--${k.tone}`}>
              <span className="fx-kpi-icon">
                <Icon size={19} strokeWidth={2.1} />
              </span>
              <span className="fx-kpi-body">
                <span className="fx-kpi-label">{k.label}</span>
                <strong className="fx-kpi-value">{k.value}</strong>
              </span>
            </article>
          );
        })}
        <div className="fx-kpi-updated" title={t("faults.kpi.updatedHint")}>
          <RefreshCw size={14} strokeWidth={2.1} className={loading ? "fx-spin" : undefined} />
          <span>
            <small>{t("faults.kpi.lastUpdate")}</small>
            <strong>{new Date(now).toLocaleTimeString(localeTag)}</strong>
          </span>
        </div>
      </div>

      {/* ---- Sekmeler ---- */}
      <div className="fx-tabs" role="tablist">
        <button
          type="button"
          role="tab"
          aria-selected={tab === "active"}
          className={`fx-tab ${tab === "active" ? "is-active" : ""}`}
          onClick={() => setTab("active")}
        >
          <TriangleAlert size={16} strokeWidth={2.1} />
          {t("faults.tab.active")}
          <span className="fx-tab-count">{activeFaults.length}</span>
        </button>
        <button
          type="button"
          role="tab"
          aria-selected={tab === "history"}
          className={`fx-tab ${tab === "history" ? "is-active" : ""}`}
          onClick={() => setTab("history")}
        >
          <FileText size={16} strokeWidth={2.1} />
          {t("faults.tab.history")}
          <span className="fx-tab-count">{historyFaults.length}</span>
        </button>
      </div>

      {/* ---- Aktif sekmesi ---- */}
      {tab === "active" ? (
        loading && activeFaults.length === 0 ? (
          <div className="fx-empty">
            <RefreshCw size={40} strokeWidth={1.6} className="fx-spin" />
            <p>{t("faults.empty.loading")}</p>
          </div>
        ) : error && activeFaults.length === 0 ? (
          // HATA DALI — "yesil yalan"in kapatildigi yer.
          //
          // Veri alinamadiginda BILMIYORUZ demek zorundayiz. Onceden bu dal
          // yoktu ve akis dogrudan asagidaki yesil "Sistem temiz" ekranina
          // dusuyordu; yani istemci veriyi hic alamamis olsa bile operator
          // "ariza yok" goruyordu.
          <div className="fx-empty fx-empty--error">
            <TriangleAlert size={48} strokeWidth={1.6} />
            <h4>{t("faults.empty.errorTitle")}</h4>
            <p>{error}</p>
          </div>
        ) : activeFaults.length === 0 || !shownFault ? (
          <div className="fx-empty fx-empty--ok">
            <CheckCircle2 size={48} strokeWidth={1.6} />
            <h4>{t("faults.empty.noActive")}</h4>
            <p>{t("faults.empty.systemClean")}</p>
          </div>
        ) : (
          /* ARIZA SEKMELERI
             Once yatay kaydirmali bir desteydi; kaydirma sirasinda iki ariza
             ayni anda yarim gorunuyor ve hangisine baktigin belirsizlesiyordu.
             Ariza karti tek basina bir ekran dolusu bilgi — ayni anda YALNIZ
             BIRI gorunmeli. Sekmeler bunu kesin yapar: secilen ariza tam
             alani kaplar, digerleri gorunmez (yarim de olsa). */
          <div className="fx-tabs-wrap">
            {activeFaults.length > 1 ? (
              <div className="fx-fault-tabs" role="tablist">
                {activeFaults.map((f) => (
                  <button
                    key={f.id}
                    type="button"
                    role="tab"
                    aria-selected={f.id === shownFault.id}
                    className={`fx-fault-tab${
                      f.id === shownFault.id ? " is-active" : ""
                    } fx-fault-tab--${f.status}`}
                    onClick={() => setActiveFaultId(f.id)}
                  >
                    <span className="fx-fault-tab-line">{f.line_name}</span>
                    <span className="fx-fault-tab-range">
                      {t("faults.card.rangeText", {
                        from: f.from_pole_seq ?? "?",
                        to: f.to_pole_seq ?? "?"
                      })}
                    </span>
                  </button>
                ))}
              </div>
            ) : null}
            <ActiveFaultCard
              key={shownFault.id}
              fault={shownFault}
              poleSeqs={poleSeqsByLine.get(shownFault.line_id) ?? []}
              poles={polesByLine.get(shownFault.line_id) ?? []}
              branches={branchesByLine.get(shownFault.line_id) ?? []}
              segments={segmentsByLine.get(shownFault.line_id) ?? []}
              localeTag={localeTag}
              now={now}
              canAssign={canAssign}
              onOpenDetail={() => setOpenFaultId(shownFault.id)}
              onAssignClick={() => setOpenFaultId(shownFault.id)}
              onShowOnMap={() => setOpenFaultId(shownFault.id)}
            />
          </div>
        )
      ) : (
        <FaultHistoryTable
          faults={historyFaults}
          localeTag={localeTag}
          onLoadComments={onLoadComments}
          onAddComment={onAddComment}
          onUpdateNote={onUpdateNote}
        />
      )}

      {openFault ? (
        <FaultDetailModal
          fault={openFault}
          users={users}
          currentUsername={currentUsername}
          canAssign={canAssign}
          gridSnapshot={gridSnapshot}
          devices={devices}
          alarms={alarms}
          onClose={() => setOpenFaultId(null)}
          onAssign={onAssign}
          onUpdateStatus={onUpdateStatus}
          onUpdateNote={onUpdateNote}
          onUpdateCause={onUpdateCause}
          causeCatalog={causeCatalog}
          onLoadComments={onLoadComments}
          onAddComment={onAddComment}
        />
      ) : null}
    </div>
  );
}
