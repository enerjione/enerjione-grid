/**
 * DeviceFtpConfigCard — cihazin ayar dosyasini goruntule, duzenle, gonder.
 *
 * TASARIM KARARLARI
 * -----------------
 * 1. UST BOLUM MINIMAL (kullanici istegi, 2026-08-06): tek baslik satiri —
 *    solda ad + surum rozeti, sagda islemler. FTP mekanigi anlatilmaz;
 *    kullanicinin isi "cihazdan cek / duzenle / cihaza uygula"dir. Uzun
 *    ipucu paragraflari kaldirildi; sistem zaten dogru olani yapiyor
 *    (kaydedilen dosya cihazin okuyacagi dosyadir).
 *
 * 2. SURUM GECMISI POPUP'ta — sayfa ici acilir liste ayarlari asagi itiyordu;
 *    diger sayfalarin kaliplariyla (dcfg-modal) ayni.
 *
 * 3. KAYDETMEK != GONDERMEK. Kayit yeni surum yaratir ve dosyayi FTP'de
 *    gunceller; cihazin UYGULAMASI icin "Cihaza uygula" komutu gerekir.
 *
 * 4. YALNIZCA DEGISENLER gonderilir; metin alanlari salt-okunur (sabit
 *    genislik) ve GOSTERILMEZ — degistirilemeyen satir yalnizca gurultu.
 *
 * 5. Seri numarasi cozulemeyen cihazda kart YINE ACILIR (filename=null);
 *    dosya uretilemeyecegi acikca soylenir, gonderme kapali kalir.
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";

import {
  applyDeviceConfig,
  deviceConfigDownloadUrl,
  fetchDeviceConfig,
  fetchDeviceConfigVersions,
  initDeviceConfigFromTemplate,
  pullDeviceConfigFromFtp,
  revertDeviceConfig,
  sendDeviceCommand,
  updateDeviceConfig,
  uploadDeviceConfig
} from "../../shared/api";
import type { ConfigCurrent, ConfigVersion } from "../../shared/types";
import { useToast } from "../../components/ToastProvider";
import { applyGorunum, applyToneClass } from "./configApplyState";

type Props = {
  deviceId: number;
  /** Komut gonderme cihaz KODU ile yapilir (id ile degil). */
  deviceCode: string;
  accessToken: string;
  /** Yalnizca engineer/installer duzenleyebilir. */
  canEdit: boolean;
  /** Cihaza komut gonderme yetkisi. Duzenleme yetkisinden AYRI: dosyayi
   *  duzenlemek yerel bir istir, cihaza komut gondermek sahayi etkiler. */
  canCommand: boolean;
};

const KAYNAK_ANAHTARI: Record<ConfigVersion["source"], string> = {
  sablon: "template",
  cihazdan_cekildi: "pulled",
  yuklendi: "uploaded",
  duzenlendi: "edited"
};

/** "Cihazdan cek" (start_csv_upload) DNP3 komutu SIMDILIK GIZLI: sahadaki
 *  cihazda update-CSV akisi calismiyor (2026-08-06). Sistem bunun yerine
 *  FTP'de dosya var mi diye KENDISI sorguluyor. Komut cihazda dogrulaninca
 *  bu bayrak acilarak geri getirilir. */
const CIHAZDAN_CEK_GORUNUR = false;

export function DeviceFtpConfigCard({ deviceId, deviceCode, accessToken, canEdit, canCommand }: Props) {
  const { t } = useTranslation();
  const toast = useToast();

  const [current, setCurrent] = useState<ConfigCurrent | null>(null);
  const [versions, setVersions] = useState<ConfigVersion[]>([]);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [historyOpen, setHistoryOpen] = useState(false);
  /** FTP otomatik sorgusu: null=yapilmadi, "aranıyor", "yok" (FTP'de dosya
   *  bulunamadi — yukle/sablon secenekleri gosterilir). */
  const [probe, setProbe] = useState<null | "searching" | "absent">(null);
  /** CatIndex -> kullanicinin yazdigi ham metin. Sayiya cevirme KAYDETMEDE
   *  yapilir; yazarken cevirmek "12" yazarken "1"i reddetmek olurdu. */
  const [drafts, setDrafts] = useState<Record<string, string>>({});
  /** Ayar arama metni — 60 satirlik izgarada gozle taramak yavasti. */
  const [ara, setAra] = useState("");

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [cfg, vs] = await Promise.all([
        fetchDeviceConfig(accessToken, deviceId),
        fetchDeviceConfigVersions(accessToken, deviceId)
      ]);
      setCurrent(cfg);
      setVersions(vs);
      setDrafts({});
    } catch (exc) {
      toast.error(exc instanceof Error ? exc.message : String(exc));
    } finally {
      setLoading(false);
    }
  }, [accessToken, deviceId, toast]);

  useEffect(() => {
    void load();
  }, [load]);

  /** Yapilandirma yoksa sistem SORMADAN FTP'ye bakar: dosya cihaz tarafindan
   *  zaten yazilmis olabilir ("Cihazdan cek" komutu su an calismadigi icin
   *  tek otomatik kaynak bu). Bulursa surume cevirir; bulamazsa kullaniciya
   *  yukleme/sablon secenekleri kalir. Cihaz basina BIR kez denenir. */
  useEffect(() => {
    if (loading || current !== null || probe !== null) return;
    let iptal = false;
    setProbe("searching");
    void (async () => {
      try {
        const v = await pullDeviceConfigFromFtp(accessToken, deviceId);
        if (iptal) return;
        if (v) {
          toast.success(t("deviceDetail.config.ftp.ftpFound", { version: v.version }));
          await load();
        } else {
          setProbe("absent");
        }
      } catch {
        // Erisim sorunu vb. — sessiz: bos durum secenekleri zaten gorunur,
        // elle "FTP'den sorgula" denenirse gercek hata toast'ta gosterilir.
        if (!iptal) setProbe("absent");
      }
    })();
    return () => {
      iptal = true;
    };
  }, [loading, current, probe, accessToken, deviceId, load, t, toast]);

  /** Elle FTP sorgusu — hata bu kez GOSTERILIR. */
  async function ftpSorgula() {
    setBusy(true);
    try {
      const v = await pullDeviceConfigFromFtp(accessToken, deviceId);
      if (v) {
        toast.success(t("deviceDetail.config.ftp.ftpFound", { version: v.version }));
        await load();
      } else {
        toast.error(t("deviceDetail.config.ftp.ftpNotFound"));
      }
    } catch (exc) {
      toast.error(exc instanceof Error ? exc.message : String(exc));
    } finally {
      setBusy(false);
    }
  }

  /** Yalnizca GERCEKTEN degisenler. Ayni degeri tekrar yazmak surum
   *  uretmemeli — gecmis anlamsiz kayitlarla dolardi. */
  const changes = useMemo(() => {
    const out: Record<string, number> = {};
    if (!current) return out;
    for (const row of current.rows) {
      const draft = drafts[row.catIndex];
      if (draft === undefined || draft.trim() === "") continue;
      const n = Number(draft);
      if (!Number.isInteger(n) || n < 0) continue;
      if (row.valueInt !== null && n !== row.valueInt) out[row.catIndex] = n;
    }
    return out;
  }, [current, drafts]);

  const duzenlenebilir = useMemo(
    () => (current?.rows ?? []).filter((r) => r.valueInt !== null),
    [current]
  );

  /** Arama: ad, CatIndex ve aciklama uzerinde; Turkce kucuk harf duyarli. */
  const gorunen = useMemo(() => {
    const q = ara.trim().toLocaleLowerCase("tr");
    if (!q) return duzenlenebilir;
    return duzenlenebilir.filter((r) =>
      [r.meaning, r.catIndex, r.description]
        .filter((s): s is string => Boolean(s))
        .some((s) => s.toLocaleLowerCase("tr").includes(q))
    );
  }, [duzenlenebilir, ara]);

  const changeCount = Object.keys(changes).length;
  const invalidDraft = useMemo(
    () =>
      Object.entries(drafts).some(([, v]) => {
        if (v.trim() === "") return false;
        const n = Number(v);
        return !Number.isInteger(n) || n < 0;
      }),
    [drafts]
  );

  /** Kayit FTP'deki dosyayi da gunceller; yazma basarisizsa surum yine
   *  kaydedilmistir ama kullanici UYARILIR. */
  function ftpUyar(v: { ftpWritten: boolean | null }) {
    if (v.ftpWritten === false) {
      toast.error(t("deviceDetail.config.ftp.syncFailed"));
    }
  }

  async function save() {
    if (changeCount === 0) return;
    setBusy(true);
    try {
      const v = await updateDeviceConfig(accessToken, deviceId, changes);
      toast.success(t("deviceDetail.config.ftp.saved", { count: changeCount }));
      ftpUyar(v);
      await load();
    } catch (exc) {
      toast.error(exc instanceof Error ? exc.message : String(exc));
    } finally {
      setBusy(false);
    }
  }

  async function upload(file: File) {
    // Dosya adi cihazin serisiyle uyusmuyorsa UYAR ama ENGELLEME (baska
    // cihazin dosyasini sablon gibi kullanmak mesru). Seri bilinmiyorsa
    // (filename null) kontrol atlanir — backend denetim kaydini yine tutar.
    if (current?.filename && file.name !== current.filename) {
      const onay = window.confirm(
        t("deviceDetail.config.ftp.serialMismatch", {
          expected: current.filename,
          got: file.name
        })
      );
      if (!onay) return;
    }
    setBusy(true);
    try {
      const v = await uploadDeviceConfig(accessToken, deviceId, file);
      toast.success(t("deviceDetail.config.ftp.uploaded", { version: v.version }));
      ftpUyar(v);
      await load();
    } catch (exc) {
      toast.error(exc instanceof Error ? exc.message : String(exc));
    } finally {
      setBusy(false);
    }
  }

  /** "Cihazdan cek": cihaz kendi dosyasini FTP'ye yazar, sistem yakalayip
   *  yeni surum yapar. Komut kuyruga alinir; cihaz sonraki oturumda uygular. */
  async function komut(slug: string) {
    setBusy(true);
    try {
      await sendDeviceCommand(accessToken, deviceCode, slug);
      toast.success(t("deviceDetail.config.ftp.cmdQueued"));
    } catch (exc) {
      toast.error(exc instanceof Error ? exc.message : String(exc));
    } finally {
      setBusy(false);
    }
  }

  /** "Cihaza uygula": dosya cihazin okuyacagi yere yazilir + guncelleme
   *  komutu kuyruga alinir. */
  async function uygula() {
    setBusy(true);
    try {
      await applyDeviceConfig(accessToken, deviceId);
      toast.success(t("deviceDetail.config.ftp.applied"));
      await load();
    } catch (exc) {
      toast.error(exc instanceof Error ? exc.message : String(exc));
    } finally {
      setBusy(false);
    }
  }

  /** Yapilandirmasi olmayan cihaz: varsayilan sablondan ilk surum. */
  async function sablondan() {
    setBusy(true);
    try {
      const v = await initDeviceConfigFromTemplate(accessToken, deviceId);
      toast.success(t("deviceDetail.config.ftp.fromTemplateDone", { version: v.version }));
      ftpUyar(v);
      await load();
    } catch (exc) {
      toast.error(exc instanceof Error ? exc.message : String(exc));
    } finally {
      setBusy(false);
    }
  }

  async function revert(version: number) {
    setBusy(true);
    try {
      const v = await revertDeviceConfig(accessToken, deviceId, version);
      toast.success(t("deviceDetail.config.ftp.reverted", { from: version, to: v.version }));
      ftpUyar(v);
      await load();
    } catch (exc) {
      toast.error(exc instanceof Error ? exc.message : String(exc));
    } finally {
      setBusy(false);
    }
  }

  if (loading) {
    return (
      <section className="device-config-section">
        <h4 className="device-config-title">
          <span className="material-symbols-outlined">folder_shared</span>
          {t("deviceDetail.config.ftpTitle")}
        </h4>
        <p className="device-config-hint">{t("common.loading")}</p>
      </section>
    );
  }

  // Henuz surum yok: bu bir HATA degil, olagan baslangic durumu. Sistem once
  // FTP'ye KENDISI bakti (ust effect); bulamadiysa kullaniciyi yonlendirir:
  // FTP'den sorgula (elle tekrar) / dosya yukle / sablondan olustur.
  if (!current) {
    return (
      <section className="device-config-section">
        <h4 className="device-config-title">
          <span className="material-symbols-outlined">folder_shared</span>
          {t("deviceDetail.config.ftpTitle")}
        </h4>
        <p className="device-config-hint">
          {probe === "searching"
            ? t("deviceDetail.config.ftp.ftpSearching")
            : probe === "absent"
              ? t("deviceDetail.config.ftp.ftpNotFound")
              : t("deviceDetail.config.ftp.empty")}
        </p>
        <div className="dev-ftp-actions">
          <button
            type="button"
            className="dev-ftp-btn is-primary"
            disabled={busy || probe === "searching"}
            onClick={() => void ftpSorgula()}
          >
            <span className="material-symbols-outlined">cloud_download</span>
            {t("deviceDetail.config.ftp.queryFtp")}
          </button>
          {CIHAZDAN_CEK_GORUNUR && canCommand ? (
            <button
              type="button"
              className="dev-ftp-btn"
              disabled={busy}
              onClick={() => void komut("start_csv_file_upload")}
            >
              <span className="material-symbols-outlined">cloud_download</span>
              {t("deviceDetail.config.ftp.cmdPull")}
            </button>
          ) : null}
          {canEdit ? <UploadButton onPick={upload} busy={busy} /> : null}
          {canEdit ? (
            <button
              type="button"
              className="dev-ftp-btn"
              disabled={busy}
              onClick={() => void sablondan()}
            >
              <span className="material-symbols-outlined">description</span>
              {t("deviceDetail.config.ftp.fromTemplate")}
            </button>
          ) : null}
        </div>
      </section>
    );
  }

  const v = current.version;
  // Uygulama sureci gorunumu — saf, test edilebilir bir modulden.
  const uygulama = applyGorunum(current.application);

  return (
    <section className="device-config-section dev-ftp">
      {/* TEK satir baslik: solda kimlik, sagda islemler. */}
      <div className="dev-ftp-head">
        <h4 className="device-config-title">
          <span className="material-symbols-outlined">folder_shared</span>
          {t("deviceDetail.config.ftpTitle")}
          <span className="device-config-badge is-muted">v{v.version}</span>
          {/* Dosya adi BASLIK satirinda — ikinci satirda tek basina sarkmasi
              sikayet konusuydu. */}
          {current.filename ? (
            <code className="dev-ftp-file">{current.filename}</code>
          ) : null}
          {v.checksumValid === false ? (
            <span className="device-config-badge is-bad">
              {t("deviceDetail.config.ftp.checksumBad")}
            </span>
          ) : null}
          {current.filename === null ? (
            <span className="device-config-badge is-bad">
              {t("deviceDetail.config.ftp.noSerial")}
            </span>
          ) : null}
        </h4>
        <div className="dev-ftp-head-actions">
          {/* Arama dugmelerin yaninda — ayri satirda tek basina durmasi
              yerlesimi bolup dagitiyordu (kullanici istegi). */}
          <input
            type="search"
            className="dev-ftp-search"
            value={ara}
            onChange={(e) => setAra(e.target.value)}
            placeholder={t("deviceDetail.config.ftp.searchPlaceholder")}
          />
          {CIHAZDAN_CEK_GORUNUR && canCommand ? (
            <button
              type="button"
              className="dev-ftp-btn"
              disabled={busy}
              onClick={() => void komut("start_csv_file_upload")}
            >
              <span className="material-symbols-outlined">cloud_download</span>
              {t("deviceDetail.config.ftp.cmdPull")}
            </button>
          ) : null}
          {/* Bekleyen degisiklik varken kaydetme BASLIKTA da durur — uzun
              izgarada asagidaki cubuga inmek gerekmesin (kullanici istegi). */}
          {canEdit && changeCount > 0 ? (
            <button
              type="button"
              className="dev-ftp-btn is-primary"
              disabled={busy || invalidDraft}
              onClick={() => void save()}
            >
              <span className="material-symbols-outlined">save</span>
              {t("deviceDetail.config.ftp.saveCount", { count: changeCount })}
            </button>
          ) : null}
          {canCommand ? (
            <button
              type="button"
              className={`dev-ftp-btn ${changeCount === 0 ? "is-primary" : ""}`}
              disabled={busy || current.filename === null}
              title={
                current.filename === null
                  ? t("deviceDetail.config.ftp.noSerialHint")
                  : undefined
              }
              onClick={() => void uygula()}
            >
              <span className="material-symbols-outlined">cloud_upload</span>
              {t("deviceDetail.config.ftp.cmdApply")}
            </button>
          ) : null}
          {/* Ikincil isler IKON olarak — genis dugme seridi ayarlarin yerini
              yiyordu. Ad, tooltip'te. */}
          <a
            className="dev-ftp-btn is-icon"
            href={deviceConfigDownloadUrl(deviceId)}
            download
            title={t("deviceDetail.config.ftp.download")}
          >
            <span className="material-symbols-outlined">file_download</span>
          </a>
          {canEdit ? <UploadButton onPick={upload} busy={busy} iconOnly /> : null}
          <button
            type="button"
            className="dev-ftp-btn is-icon"
            title={t("deviceDetail.config.ftp.history", { count: versions.length })}
            onClick={() => setHistoryOpen(true)}
          >
            <span className="material-symbols-outlined">history</span>
          </button>
        </div>
      </div>

      {/* Durum satiri — yalnizca soyleyecek bir sey varsa basilir.

          ESKIDEN BURASI YALAN SOYLEYEBILIYORDU: tek satir vardi ve
          `appliedAt`e bakiyordu; o alan ise komut kuyruga girer girmez
          doluyordu. Uyuyan bir Horstmann'da "Cihaza gonderildi" yaziyor ama
          cihaz hala eski yapilandirmayla calisiyordu.

          Artik surecin asamasi `application`dan gelir ve `appliedAt`
          YALNIZCA cihazin kendi kaniti goruldugunde dolar. */}
      {current.filename === null ||
      uygulama !== null ||
      v.appliedAt ||
      current.deviceLastUpdate ? (
        <div className="dev-ftp-meta">
          {current.filename === null ? (
            <span>{t("deviceDetail.config.ftp.noSerialHint")}</span>
          ) : null}
          {uygulama ? (
            <span
              className={`dev-ftp-apply ${applyToneClass(uygulama.tone)}`}
              title={uygulama.hintKey ? t(uygulama.hintKey) : undefined}
            >
              {t(uygulama.labelKey)}
              {/* Deneme sayisi yalnizca BIRDEN FAZLA denendiyse yazilir;
                  "1. deneme" her zaman gorunse gurultu olurdu. */}
              {current.application && current.application.attempt > 1
                ? ` · ${t("deviceDetail.config.ftp.apply.attempt", {
                    count: current.application.attempt
                  })}`
                : null}
            </span>
          ) : null}
          {uygulama?.hintKey ? (
            <span className="dev-ftp-apply-hint">{t(uygulama.hintKey)}</span>
          ) : null}
          {v.appliedAt ? (
            <span className="dev-ftp-applied">
              {t("deviceDetail.config.ftp.appliedAt", {
                date: new Date(v.appliedAt).toLocaleString()
              })}
            </span>
          ) : null}
          {current.deviceLastUpdate ? (
            <span
              className="dev-ftp-device-update"
              title={t("deviceDetail.config.ftp.deviceUpdateHint")}
            >
              {t("deviceDetail.config.ftp.deviceUpdate", {
                date: current.deviceLastUpdate
              })}
            </span>
          ) : null}
        </div>
      ) : null}

      <div className="dev-ftp-grid">
        {gorunen.map((row) => {
          const draft = drafts[row.catIndex];
          const degisti = changes[row.catIndex] !== undefined;
          return (
            <label
              key={row.catIndex}
              className={`dev-ftp-item ${degisti ? "is-changed" : ""}`}
              // Aciklama tooltip'i: manuel kaynakli metin varsa o; yoksa
              // ad + kod (en azindan kimlik dogrulanabilir olsun).
              title={
                row.description ??
                (row.meaning ? `${row.meaning} (${row.catIndex})` : row.catIndex)
              }
            >
              <span className="dev-ftp-item-name">
                {row.meaning ?? row.catIndex}
                <code>{row.catIndex}</code>
              </span>
              <span className="dev-ftp-item-input">
                <input
                  type="text"
                  inputMode="numeric"
                  value={draft ?? String(row.valueInt)}
                  disabled={busy || !canEdit}
                  onChange={(e) =>
                    setDrafts((d) => ({ ...d, [row.catIndex]: e.target.value }))
                  }
                />
                {/* Birim yuvasi HEP basilir (bos da olsa): yalnizca birimi
                    olanlarda basmak kolonlari tirtikli gosteriyordu. */}
                <em>{row.unit ?? ""}</em>
              </span>
            </label>
          );
        })}
      </div>

      {canEdit ? (
        <div className="dev-ftp-save">
          <span>
            {changeCount > 0
              ? t("deviceDetail.config.ftp.pending", { count: changeCount })
              : t("deviceDetail.config.ftp.noChange")}
          </span>
          <button
            type="button"
            className="dev-ftp-btn is-primary"
            disabled={busy || changeCount === 0 || invalidDraft}
            onClick={() => void save()}
          >
            {t("deviceDetail.config.ftp.save")}
          </button>
        </div>
      ) : null}

      {/* ---- Surum gecmisi POPUP (diger sayfalarin kalibi) ---- */}
      {historyOpen ? (
        <div className="dcfg-modal-backdrop" role="dialog" aria-modal="true">
          <div className="dcfg-modal">
            <div className="dcfg-modal-head">
              <h4>
                <span className="material-symbols-outlined">history</span>
                {t("deviceDetail.config.ftp.history", { count: versions.length })}
              </h4>
              <button
                type="button"
                className="dcfg-btn is-small"
                onClick={() => setHistoryOpen(false)}
              >
                <span className="material-symbols-outlined">close</span>
              </button>
            </div>
            <ol className="dev-ftp-history">
              {versions.map((h) => (
                <li key={h.id} className={h.version === v.version ? "is-current" : ""}>
                  <strong>v{h.version}</strong>
                  <span>{t(`deviceDetail.config.ftp.source.${KAYNAK_ANAHTARI[h.source]}`)}</span>
                  <span>{new Date(h.createdAt).toLocaleString()}</span>
                  {h.note ? <em>{h.note}</em> : null}
                  {canEdit && h.version !== v.version ? (
                    <button type="button" disabled={busy} onClick={() => void revert(h.version)}>
                      {t("deviceDetail.config.ftp.revert")}
                    </button>
                  ) : null}
                </li>
              ))}
            </ol>
          </div>
        </div>
      ) : null}
    </section>
  );
}

function UploadButton({
  onPick,
  busy,
  iconOnly
}: {
  onPick: (f: File) => void;
  busy: boolean;
  iconOnly?: boolean;
}) {
  const { t } = useTranslation();
  return (
    <label
      className={`dev-ftp-btn ${iconOnly ? "is-icon" : ""} ${busy ? "is-disabled" : ""}`}
      title={iconOnly ? t("deviceDetail.config.ftp.upload") : undefined}
    >
      <span className="material-symbols-outlined">upload_file</span>
      {iconOnly ? null : t("deviceDetail.config.ftp.upload")}
      <input
        type="file"
        accept=".csv"
        hidden
        disabled={busy}
        onChange={(e) => {
          const f = e.target.files?.[0];
          // Ayni dosyayi tekrar secebilmek icin input sifirlanir; aksi halde
          // ikinci secim `change` uretmez ve buton "bozuk" gorunur.
          e.target.value = "";
          if (f) onPick(f);
        }}
      />
    </label>
  );
}
