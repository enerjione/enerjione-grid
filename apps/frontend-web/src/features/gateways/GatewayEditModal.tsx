/**
 * GatewayEditModal — mevcut bir gateway'in ayarlari.
 *
 * ESKI FORMDAN FARK: "Host" ve "Port" alanlari KALDIRILDI. Gateway artik DNP3
 * master rolunde calisiyor; outstation adresi cihazin kendi `ip_address`
 * alanindan okunuyor. Bu iki alan create akisinda backend semasini doldurmak
 * icin gonderilen placeholder'lardi ("auto" / 0) ve kullaniciya gercek bir
 * ayarmis gibi gorunuyordu.
 *
 * Token artik duz bir input degil: okunur kutu + kopyala + yeniden uret.
 * Yeniden uretmek CALISAN gateway'i keser (compose icindeki eski token
 * gecersizlesir), bu yuzden sonucu acikca yaziyoruz.
 */
import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import {
  AlertTriangle,
  ArrowUpCircle,
  CheckCircle2,
  Loader2,
  RefreshCw,
  Trash2,
  X
} from "lucide-react";

import {
  fetchGatewayAgentStatus,
  updateGatewayLocally,
  fetchGatewayToken,
  installGatewayLocally,
  removeGatewayLocally
} from "../../shared/api";
import type { Gateway, GatewayAgentStatus, LocalGateway } from "../../shared/types";
import { CopyButton, generateToken } from "./gatewayShared";
import { useModalDialog } from "../../shared/useModalDialog";

type Props = {
  accessToken: string;
  gateway: Gateway;
  onSave: (payload: {
    name: string;
    token: string;
    publish_dnp3_quality: boolean;
  }) => Promise<void>;
  onClose: () => void;
};

/** Ajan durumunu yoklama araligi. Guncelleme saniyeler surer; 1.5 sn hem
 *  akici gorunur hem backend'i gereksiz yormaz. */
const IZLEME_ARALIK_MS = 1500;

/** Yoklama tavani. Asilirsa "bilmiyorum" denir — "basarili" DENMEZ.
 *  Saha kosullarinda (4G) imaj cekme dakikalar surebilir, bu yuzden cömert. */
const IZLEME_TAVANI_MS = 5 * 60 * 1000;

/** Ekranda gosterilen guncelleme ilerlemesi. */
type IslemDurumu = {
  /** gonderiliyor | uygulaniyor | validate | pull | up | done | bitti | zaman_asimi */
  asama: string;
  basarili?: boolean;
  mesaj?: string;
  detay?: string;
};

export function GatewayEditModal({ accessToken, gateway, onSave, onClose }: Props) {
  const { t } = useTranslation();
  // ESC ile kapanma + odak tuzagi (modal disina Tab ile cikilamasin).
  const dialogRef = useModalDialog<HTMLDivElement>(onClose);
  const [name, setName] = useState(gateway.name);
  // Token listede DONMUYOR (operator'a acik bir uctu ve telemetri
  // enjeksiyonuna izin veriyordu); modal acilinca installer'a ozel uctan
  // ayrica cekilir. `orijinalToken` "degisti mi" karsilastirmasi icin.
  const [token, setToken] = useState("");
  const [orijinalToken, setOrijinalToken] = useState("");
  const [kaliteYayini, setKaliteYayini] = useState<boolean>(
    gateway.publish_dnp3_quality ?? false
  );
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  const [agent, setAgent] = useState<GatewayAgentStatus | null>(null);
  const [localBusy, setLocalBusy] = useState(false);
  const [islem, setIslem] = useState<IslemDurumu | null>(null);
  // Modal kapandiktan sonra yoklama SURMEMELI: aksi halde sokulmus bilesene
  // yazilir ve 5 dakika boyunca bosuna istek atilir.
  const canli = useRef(true);

  // Token henuz cekilmediyse "degisti" sayma; aksi halde modal acilir acilmaz
  // bos deger yuzunden "token degisti" uyarisi cikardi.
  const tokenChanged = orijinalToken !== "" && token !== orijinalToken;

  const loadAgent = async () => {
    setAgent(await fetchGatewayAgentStatus(accessToken));
  };

  useEffect(() => {
    canli.current = true;
    return () => {
      canli.current = false;
    };
  }, []);

  /** UZAK SURUM SORGUSU ARKA PLANDA KOSAR — cevap gelince kendiliginden tazele.
   *
   *  Backend kayit defterine ag beklemeden soruyor (istek icinde beklemek,
   *  guncelleme sirasinda saniyede bir yoklanan bu ucu kilitlerdi) ve ilk
   *  cevapta `remote_pending` doner. Tazelemeseydik operator "sorgulanıyor"
   *  yazisinda kalir, surumu gormek icin modali kapatip yeniden acardi.
   *
   *  Deneme TAVANLI: sorgu kalici olarak basarisizsa (cihaz da sunucu da
   *  internete cikmiyor) modal acik kaldigi surece sonsuz yoklama yapmasin. */
  const surumYoklama = useRef(0);
  useEffect(() => {
    const bekleyen = (agent?.gateways ?? []).some((g) => g.remote_pending);
    if (!bekleyen || surumYoklama.current >= 4) return;
    const id = window.setTimeout(() => {
      surumYoklama.current += 1;
      if (canli.current) void loadAgent();
    }, 2500);
    return () => window.clearTimeout(id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [agent]);

  useEffect(() => {
    void loadAgent();
    // Token ayri uctan gelir; basarisiz olursa (yetki yok) kutu bos kalir ve
    // kaydetme token'a dokunmaz — modalin geri kalani calismaya devam eder.
    void (async () => {
      try {
        const mevcut = await fetchGatewayToken(accessToken, gateway.code);
        setToken(mevcut);
        setOrijinalToken(mevcut);
      } catch {
        setToken("");
        setOrijinalToken("");
      }
    })();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [accessToken, gateway.code]);

  const handleLocalUpdate = async () => {
    // KESINTIYI SOR. Gateway yeniden baslarken ona bagli cihazlardan
    // telemetri gelmez; operatorun bunu bilmeden tetiklememesi gerekir.
    if (!window.confirm(t("engineering.gateways.editForm.updateConfirm"))) return;
    setLocalBusy(true);
    setError("");
    setIslem({ asama: "gonderiliyor" });
    try {
      await updateGatewayLocally(accessToken, gateway.code);
      // ESKIDEN: tek bir `setTimeout(..., 3000)` vardi ve is orada bitiyordu.
      // Guncelleme imaj cekmeyi iceriyor; olculen sure 12 sn, sahada (4G)
      // dakikalar surebilir. Sonuc: operator butona basiyor, ekranda hicbir
      // sey degismiyor, "basti mi basmadi mi" belli olmuyordu.
      //
      // Artik ajan bitene kadar YOKLANIYOR ve her asama (indiriliyor ->
      // baslatiliyor -> bitti) ekranda gorunuyor.
      setIslem({ asama: "uygulaniyor" });
      void izleIslem();
    } catch (err) {
      setIslem(null);
      setError(err instanceof Error ? err.message : String(err));
      setLocalBusy(false);
    }
  };

  /** Ajan istegi bitene kadar durumu yoklar. */
  const izleIslem = async () => {
    const basla = Date.now();
    while (canli.current && Date.now() - basla < IZLEME_TAVANI_MS) {
      await new Promise((r) => window.setTimeout(r, IZLEME_ARALIK_MS));
      if (!canli.current) return;
      let durum: GatewayAgentStatus | null = null;
      try {
        durum = await fetchGatewayAgentStatus(accessToken);
      } catch {
        // Guncelleme sirasinda backend'e erisim kisa sure kesilebilir;
        // tek bir basarisiz yoklama izlemeyi BITIRMEZ.
        continue;
      }
      setAgent(durum);

      const sonuc = durum?.last_apply ?? null;
      const bizim = sonuc?.code === gateway.code && sonuc?.action === "update";

      // Ajan hala calisiyorsa asamayi yansit (pull / up ...).
      if (durum?.pending || sonuc?.running) {
        if (bizim && sonuc?.stage) setIslem({ asama: sonuc.stage });
        continue;
      }
      // Istek kuyrukta yok ve son sonuc bize ait: is bitti.
      if (bizim) {
        setIslem({
          asama: "bitti",
          basarili: sonuc?.ok === true,
          mesaj: sonuc?.message ?? undefined,
          detay: sonuc?.ok === false ? (sonuc?.detail ?? undefined) : undefined
        });
        setLocalBusy(false);
        return;
      }
    }
    if (!canli.current) return;
    // Tavana carpildi: "bilmiyorum" de, "basarili" DEME.
    setIslem({ asama: "zaman_asimi" });
    setLocalBusy(false);
  };

  const local: LocalGateway | null =
    agent?.gateways.find((g) => g.code === gateway.code) ?? null;

  const handleSave = async () => {
    setError("");
    setBusy(true);
    try {
      const kaliteDegisti = kaliteYayini !== (gateway.publish_dnp3_quality ?? false);
      await onSave({
        name: name.trim(),
        token,
        publish_dnp3_quality: kaliteYayini
      });
      // Token degistiyse ve gateway BU cihazda kuruluysa compose'daki eski
      // token gecersiz kaldi — kurulumu yeni token ile tazele. Aksi halde
      // kullanici "kaydettim ama gateway offline oldu" ile bas basa kalir.
      // Token VEYA kalite bayragi degistiyse compose'daki deger eskidi —
      // kurulumu tazele. Aksi halde ayar kaydedilmis gorunur ama gateway
      // hala eski davranisla calisir (sessiz yalan).
      if ((tokenChanged || kaliteDegisti) && local) {
        await installGatewayLocally(accessToken, gateway.code);
      }
      onClose();
    } catch (err) {
      setError(err instanceof Error ? err.message : t("common.errorOccurred"));
    } finally {
      setBusy(false);
    }
  };

  const handleLocalRemove = async () => {
    setError("");
    setLocalBusy(true);
    try {
      await removeGatewayLocally(accessToken, gateway.code);
      // Ajan isi asenkron yapar; kisa bir bekleme sonrasi durumu tazele.
      window.setTimeout(() => void loadAgent(), 2500);
    } catch (err) {
      setError(err instanceof Error ? err.message : t("common.errorOccurred"));
    } finally {
      setLocalBusy(false);
    }
  };

  return (
    <div className="settings-modal-backdrop">
      <div className="settings-modal gw-wizard" role="dialog" aria-modal="true" ref={dialogRef}>
        <header className="gw-wizard-head">
          <div>
            <h3>{t("engineering.gateways.editGatewayModal")}</h3>
            <p>{gateway.code}</p>
          </div>
          <button
            type="button"
            className="gw-wizard-close"
            onClick={onClose}
            aria-label={t("common.close")}
          >
            <X size={18} strokeWidth={2.2} />
          </button>
        </header>

        <div className="gw-wizard-body">
          <label className="gw-field">
            <span>{t("engineering.gateways.form.name")}</span>
            <input value={name} onChange={(e) => setName(e.target.value)} autoFocus />
          </label>

          <div className="gw-token-box">
            <div className="gw-token-head">
              <span>{t("engineering.gateways.wizard.tokenTitle")}</span>
              <div className="gw-token-actions">
                <button
                  type="button"
                  className="gw-copy-btn"
                  onClick={() => setToken(generateToken())}
                  title={t("engineering.gateways.wizard.tokenRegenerate")}
                  aria-label={t("engineering.gateways.wizard.tokenRegenerate")}
                >
                  <RefreshCw size={15} strokeWidth={2.2} />
                </button>
                <CopyButton value={token} label={t("engineering.gateways.compose.copy")} />
              </div>
            </div>
            <code className="gw-token-value">{token}</code>
            {tokenChanged ? (
              <p className="gw-token-warn">
                <AlertTriangle size={14} strokeWidth={2.2} />
                <span>
                  {local
                    ? t("engineering.gateways.editForm.tokenWarnLocal")
                    : t("engineering.gateways.editForm.tokenWarnRemote")}
                </span>
              </p>
            ) : null}
          </div>

          {/* DNP3 KALITE BAYRAKLARI — gateway bazinda anahtar.
              Kapaliyken her okuma "good" gider: outstation CT referansini
              kaybedip 0 A raporlasa bile SCADA bunu gecerli olcum sanar.
              Acmak saha davranisini degistirdigi icin (kotu olcumler alarm
              degerlendirmesinden bloke olur) filo geneli tek anahtar
              YAPILMADI; tek tek gateway'de denenebilsin. */}
          <label className="gw-switch-row">
            <input
              type="checkbox"
              checked={kaliteYayini}
              onChange={(e) => setKaliteYayini(e.target.checked)}
              disabled={busy || localBusy}
            />
            <span className="gw-switch-body">
              <strong>{t("engineering.gateways.editForm.qualityFlags")}</strong>
              <small>{t("engineering.gateways.editForm.qualityFlagsHint")}</small>
            </span>
          </label>

          {/* Bu cihazdaki kurulum — yalnizca host ajani gateway'i goruyorsa. */}
          {local ? (
            <div className="gw-local-box">
              <div className="gw-local-head">
                <span
                  className={`gw-local-dot ${local.state === "running" ? "is-on" : "is-off"}`}
                  aria-hidden="true"
                />
                <div>
                  <strong>{t("engineering.gateways.editForm.localTitle")}</strong>
                  <small>{local.status || local.state}</small>
                </div>
                <button
                  type="button"
                  className="gw-danger-btn"
                  onClick={() => void handleLocalRemove()}
                  disabled={localBusy || busy}
                >
                  {localBusy ? (
                    <Loader2 size={14} strokeWidth={2.2} className="net-spin" />
                  ) : (
                    <Trash2 size={14} strokeWidth={2.2} />
                  )}
                  {t("engineering.gateways.editForm.localRemove")}
                </button>
              </div>
              {/* SURUM DURUMU — uc durumlu.
                  `update_available` null/undefined ise BILINMIYOR (kayit
                  defterine ulasilamadi). Bunu "guncel" gostermek, sormadan
                  verilmis bir iddia olurdu ve operator eski surumde
                  kaldigini fark etmezdi. */}
              <div className="gw-local-version">
                {/* Calisan surum HER DURUMDA gosterilir (biliniyorsa):
                    "guncel" derken bile operator hangi surumde oldugunu
                    gormeli. */}
                {local.local_version ? (
                  <span
                    className="gw-ver-now"
                    title={
                      // Hangi etiket izleniyor — "neden guncelleme cikmiyor"
                      // sorusunun cevabi burada. Surume sabitlenmis bir
                      // etiket (or. `:1.5.0`) izleniyorsa yeni surum
                      // yayinlansa bile guncelleme GORUNMEZ.
                      local.tracked_image
                        ? t("engineering.gateways.editForm.trackedImage", {
                            image: local.tracked_image
                          })
                        : t("engineering.gateways.editForm.currentVersion")
                    }
                  >
                    {local.local_version}
                  </span>
                ) : null}
                {local.update_available === true ? (
                  <span
                    className="gw-ver-badge is-new"
                    // Surum kayit defterinden geldiyse bunu SAKLAMA: backend
                    // yeni surumu gorse de cihazin o imaji cekebilecegi ayri
                    // bir sorudur (cihaz internete cikmiyor olabilir).
                    title={
                      local.remote_source === "registry"
                        ? t("engineering.gateways.editForm.remoteFromRegistry")
                        : undefined
                    }
                  >
                    <ArrowUpCircle size={13} strokeWidth={2.2} />
                    {/* Hedef surum BILINIYORSA yaz: "0.6.0 -> 0.7.0".
                        Bilinmiyorsa (etiket yok / kayit defteri okunamadi)
                        eski genel ifadeye dusulur — uydurma yapilmaz. */}
                    {local.remote_version && local.remote_version !== local.local_version
                      ? t("engineering.gateways.editForm.updateAvailableTo", {
                          version: local.remote_version
                        })
                      : t("engineering.gateways.editForm.updateAvailable")}
                  </span>
                ) : local.update_available === false ? (
                  <span className="gw-ver-badge is-current">
                    {t("engineering.gateways.editForm.upToDate")}
                  </span>
                ) : local.remote_pending ? (
                  // Sorgu ARKA PLANDA suruyor: "bilinmiyor" demek yanlis olur,
                  // birkac saniye sonra cevap gelecek (asagida tazeleniyor).
                  <span className="gw-ver-badge is-unknown">
                    <Loader2 size={13} strokeWidth={2.2} className="net-spin" />
                    {t("engineering.gateways.editForm.versionChecking")}
                  </span>
                ) : (
                  <span
                    className="gw-ver-badge is-unknown"
                    // SEBEBI goster: "bilinmiyor" tek basina operatore neyi
                    // duzeltecegini soylemiyor. `remote_error` backend'in
                    // kayit defteri denemesinden gelir; yoksa cihazda buildx
                    // olmadigi icin sorgu hic yapilamamis olabilir.
                    title={
                      local.remote_error ||
                      (agent?.buildx_available === false
                        ? t("engineering.gateways.editForm.versionUnknownBuildx")
                        : t("engineering.gateways.editForm.versionUnknownHint"))
                    }
                  >
                    {t("engineering.gateways.editForm.versionUnknown")}
                  </span>
                )}

                {/* GUNCELLE HER ZAMAN ERISILEBILIR.
                    Eskiden bu buton YALNIZCA `update_available === true` iken
                    ciziliyordu. Karsilastirma `docker buildx` gerektiriyor;
                    buildx kurulu degilse (Docker Engine'de siklikla oyle) ya
                    da kayit defterine ulasilamazsa durum "bilinmiyor" olur ve
                    operator yeni bir surum yayinlanmis olsa bile GUNCELLEME
                    YAPAMIYORDU — tespit edilemeyen bir sey eylemi de
                    kilitliyordu.

                    Zaten guncel olan bir gateway'de `pull` bir sey indirmez;
                    en kotu ihtimalle kisa bir yeniden baslatma olur. Yani
                    butonun acik olmasinin bedeli, kilitli olmasinin
                    bedelinden cok daha dusuk. */}
                <button
                  type="button"
                  className={`gw-update-btn ${
                    local.update_available === true ? "primary-btn" : "secondary-btn"
                  }`}
                  onClick={() => void handleLocalUpdate()}
                  disabled={localBusy || busy}
                  title={
                    local.update_available === true
                      ? t("engineering.gateways.editForm.updateNow")
                      : t("engineering.gateways.editForm.updateAnywayHint")
                  }
                >
                  {localBusy ? (
                    <Loader2 size={14} strokeWidth={2.2} className="net-spin" />
                  ) : (
                    <ArrowUpCircle size={14} strokeWidth={2.2} />
                  )}
                  {local.update_available === true
                    ? t("engineering.gateways.editForm.updateNow")
                    : t("engineering.gateways.editForm.updateAnyway")}
                </button>
              </div>
              {/* ILERLEME — butona basildigi andan bitene kadar gorunur.
                  Sessiz kalmak en kotu secenekti: operator basip basmadigini
                  bilemiyordu ve tekrar basiyordu (ikinci istek reddediliyor). */}
              {islem ? (
                <div
                  className={`gw-islem ${
                    islem.asama === "bitti"
                      ? islem.basarili
                        ? "is-ok"
                        : "is-fail"
                      : islem.asama === "zaman_asimi"
                        ? "is-unknown"
                        : "is-busy"
                  }`}
                  role="status"
                  aria-live="polite"
                >
                  <span className="gw-islem-ikon">
                    {islem.asama === "bitti" ? (
                      islem.basarili ? (
                        <CheckCircle2 size={15} strokeWidth={2.2} />
                      ) : (
                        <AlertTriangle size={15} strokeWidth={2.2} />
                      )
                    ) : islem.asama === "zaman_asimi" ? (
                      <AlertTriangle size={15} strokeWidth={2.2} />
                    ) : (
                      <Loader2 size={15} strokeWidth={2.2} className="net-spin" />
                    )}
                  </span>
                  <div className="gw-islem-govde">
                    <strong>
                      {t(`engineering.gateways.editForm.progress.${islem.asama}`, {
                        defaultValue: t("engineering.gateways.editForm.progress.uygulaniyor")
                      })}
                    </strong>
                    {islem.mesaj ? <small>{islem.mesaj}</small> : null}
                    {/* Docker ciktisinin son satirlari — SADECE hatada.
                        Basarida gostermek gurultu olurdu. */}
                    {islem.detay ? <pre className="gw-islem-detay">{islem.detay}</pre> : null}
                  </div>
                </div>
              ) : null}
              <small className="gw-local-hint">{t("engineering.gateways.editForm.localHint")}</small>
            </div>
          ) : null}

          {error ? <p className="error-text">{error}</p> : null}

          <div className="modal-actions">
            <button type="button" className="secondary-btn" onClick={onClose} disabled={busy}>
              {t("common.cancel")}
            </button>
            <button
              type="button"
              className="primary-btn"
              onClick={() => void handleSave()}
              disabled={busy || name.trim().length === 0}
            >
              {busy ? t("common.saving") : t("engineering.gateways.form.save")}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
