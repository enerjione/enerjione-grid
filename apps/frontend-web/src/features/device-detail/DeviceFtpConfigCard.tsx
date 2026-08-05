/**
 * DeviceFtpConfigCard — cihazin `<seri>_Configuration.csv` dosyasini goruntule,
 * duzenle, surumler arasinda gezin.
 *
 * TASARIM KARARLARI
 * -----------------
 * 1. KAYDETMEK GONDERMEK DEGILDIR. Bu ekran yalnizca yeni bir SURUM yaratir.
 *    Dosyanin cihaza ulasmasi icin FTP'ye yazilmasi ve DNP3 komutuyla
 *    tetiklenmesi gerekir. Ikisini tek butonda birlestirmek "kaydettim, demek
 *    ki gitti" yanilgisini uretirdi — sahada en pahali yanilgi turu.
 *
 * 2. YALNIZCA DEGISENLER gonderilir. Tum tabloyu gondermek, dokunulmamis
 *    alanlarda da yuvarlama/bicim farki riski dogururdu.
 *
 * 3. Uzun alanlar (metin) SALT-OKUNUR. Uzunluk sabit oldugu icin metni
 *    degistirmek bayt sayisini tutturmayi gerektirir; sayisal alanlarda bu
 *    sorun yok. Metin duzenleme, gercek ihtiyac dogunca ayrica ele alinmali.
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";

import {
  applyDeviceConfig,
  deviceConfigDownloadUrl,
  fetchDeviceConfig,
  fetchDeviceConfigVersions,
  revertDeviceConfig,
  sendDeviceCommand,
  updateDeviceConfig,
  uploadDeviceConfig
} from "../../shared/api";
import type { ConfigCurrent, ConfigVersion } from "../../shared/types";
import { useToast } from "../../components/ToastProvider";

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

export function DeviceFtpConfigCard({ deviceId, deviceCode, accessToken, canEdit, canCommand }: Props) {
  const { t } = useTranslation();
  const toast = useToast();

  const [current, setCurrent] = useState<ConfigCurrent | null>(null);
  const [versions, setVersions] = useState<ConfigVersion[]>([]);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [historyOpen, setHistoryOpen] = useState(false);
  /** CatIndex -> kullanicinin yazdigi ham metin. Sayiya cevirme KAYDETMEDE
   *  yapilir; yazarken cevirmek "12" yazarken "1"i reddetmek olurdu. */
  const [drafts, setDrafts] = useState<Record<string, string>>({});

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

  /** Yalnizca SAYISAL alanlar duzenlenebilir; metin alanlari sabit genislikte
   *  oldugu icin bayt sayisini tutturmayi gerektirir. Duzenlenemeyen satirlari
   *  GOSTERMIYORUZ — kullanici istegi ve dogru karar: degistirilemeyen bir
   *  satir ekranda yalnizca gurultu. */
  const duzenlenebilir = useMemo(
    () => (current?.rows ?? []).filter((r) => r.valueInt !== null),
    [current]
  );

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

  async function save() {
    if (changeCount === 0) return;
    setBusy(true);
    try {
      await updateDeviceConfig(accessToken, deviceId, changes);
      toast.success(t("deviceDetail.config.ftp.saved", { count: changeCount }));
      await load();
    } catch (exc) {
      toast.error(exc instanceof Error ? exc.message : String(exc));
    } finally {
      setBusy(false);
    }
  }

  async function upload(file: File) {
    // Dosya adindaki seri, cihazin kendi serisiyle uyusmuyorsa UYAR ama
    // ENGELLEME: baska bir cihazin dosyasini sablon olarak kullanmak mesru
    // bir ihtiyac. Sessizce kabul etmek ise yanlis cihaza yanlis ayar
    // gondermeye kadar gider ve sonucu ancak sahada fark edilir.
    //
    // Guncel surum yoksa `current` null olur ve beklenen adi bilemeyiz; o
    // durumda kontrol ATLANIR — backend uyusmazligi yine denetim kaydina
    // yazar, yani kontrol tek noktaya bagli degil.
    if (current && file.name !== current.filename) {
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
      await load();
    } catch (exc) {
      toast.error(exc instanceof Error ? exc.message : String(exc));
    } finally {
      setBusy(false);
    }
  }

  /** Cihaza DNP3 komutu gonderir. Komut KUYRUGA alinir; cihaz onu ancak bir
   *  sonraki DNP oturumunda alabilir (gunluk planli cagri ya da olay). Bu
   *  yuzden mesaj "gonderildi" degil "kuyruga alindi" der — aksi halde
   *  kullanici islemin bittigini sanardi. */
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

  /** "Cihaza uygula" ZINCIRI: backend dosyayi FTP'ye yazar, sonra
   *  `config_update` komutunu kuyruga alir. Eskiden yalnizca komut gidiyordu
   *  ve dosyayi FTP'ye kullanici elle koymak zorundaydi — koymayi unutmak
   *  cihaza ESKI dosyayi okutuyordu. */
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

  async function revert(version: number) {
    setBusy(true);
    try {
      const v = await revertDeviceConfig(accessToken, deviceId, version);
      toast.success(t("deviceDetail.config.ftp.reverted", { from: version, to: v.version }));
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

  // Henuz surum yok: bu bir HATA degil, olagan baslangic durumu.
  if (!current) {
    return (
      <section className="device-config-section">
        <h4 className="device-config-title">
          <span className="material-symbols-outlined">folder_shared</span>
          {t("deviceDetail.config.ftpTitle")}
        </h4>
        <p className="device-config-hint">{t("deviceDetail.config.ftp.empty")}</p>
        <div className="dev-ftp-actions">
          {/* Config YOKKEN "cihazdan cek" tam da ihtiyac duyulan islem;
              bos durumda gizlemek kullaniciyi Komutlar sekmesine gonderirdi. */}
          {canCommand ? (
            <button
              type="button"
              className="dev-ftp-btn is-primary"
              disabled={busy}
              onClick={() => void komut("start_csv_file_upload")}
            >
              <span className="material-symbols-outlined">cloud_download</span>
              {t("deviceDetail.config.ftp.cmdPull")}
            </button>
          ) : null}
          {canEdit ? <UploadButton onPick={upload} busy={busy} /> : null}
        </div>
      </section>
    );
  }

  const v = current.version;

  return (
    <section className="device-config-section dev-ftp">
      <h4 className="device-config-title">
        <span className="material-symbols-outlined">folder_shared</span>
        {t("deviceDetail.config.ftpTitle")}
        <span className="device-config-badge is-muted">v{v.version}</span>
        {/* Checksum durumu: gecerli olan sessiz kalir, sorunlu olan gorunur. */}
        {v.checksumValid === false ? (
          <span className="device-config-badge is-bad">
            {t("deviceDetail.config.ftp.checksumBad")}
          </span>
        ) : v.checksumValid === null ? (
          <span className="device-config-badge">
            {t("deviceDetail.config.ftp.checksumUnknown")}
          </span>
        ) : null}
      </h4>

      <div className="dev-ftp-meta">
        <code>{current.filename}</code>
        <span>{t(`deviceDetail.config.ftp.source.${KAYNAK_ANAHTARI[v.source]}`)}</span>
        <span>{new Date(v.createdAt).toLocaleString()}</span>
        {v.createdBy ? <span>{v.createdBy}</span> : null}
        {/* Bu surum cihazin okuyacagi yere kondu mu? Dolu = FTP'ye yazildi ve
            komut kuyruga alindi (cihazin okudugu an DEGIL — o, FTP indirme
            olayindan izlenir). */}
        {v.appliedAt ? (
          <span className="dev-ftp-applied">
            {t("deviceDetail.config.ftp.appliedAt", {
              date: new Date(v.appliedAt).toLocaleString()
            })}
          </span>
        ) : null}
      </div>

      {/* Kaydetmek gondermek degildir — kullanici bunu BILMELI. */}
      <p className="device-config-hint">{t("deviceDetail.config.ftp.notSentHint")}</p>

      {/* CIHAZ KOMUTLARI — asil dongu burada kapanir.
          "Cihazdan cek": binary output 3, cihaz kendi config'ini FTP'ye yazar;
          backend olayi yakalayip yeni surum olusturur.
          "Cihaza uygula": once dosya FTP'ye YAZILIR (backend, mod ayarina
          gore gomulu volume ya da harici sunucu), sonra binary output 0
          kuyruga alinir — cihaz dosyayi indirip uygular. Iki adim tek ucta:
          dosyayi elle FTP'ye koymayi unutmak cihaza eski dosyayi okutuyordu.
          Komutlar sekmesine gitmek zorunda kalmak, akisi ikiye boluyordu. */}
      {canCommand ? (
        <div className="dev-ftp-cmds">
          <button
            type="button"
            className="dev-ftp-btn is-primary"
            disabled={busy}
            onClick={() => void komut("start_csv_file_upload")}
          >
            <span className="material-symbols-outlined">cloud_download</span>
            {t("deviceDetail.config.ftp.cmdPull")}
          </button>
          <button
            type="button"
            className="dev-ftp-btn"
            disabled={busy}
            onClick={() => void uygula()}
          >
            <span className="material-symbols-outlined">cloud_upload</span>
            {t("deviceDetail.config.ftp.cmdApply")}
          </button>
          <span className="dev-ftp-cmds-hint">{t("deviceDetail.config.ftp.cmdHint")}</span>
        </div>
      ) : null}

      <div className="dev-ftp-actions">
        <a className="dev-ftp-btn" href={deviceConfigDownloadUrl(deviceId)} download>
          <span className="material-symbols-outlined">file_download</span>
          {t("deviceDetail.config.ftp.download")}
        </a>
        {canEdit ? <UploadButton onPick={upload} busy={busy} /> : null}
        <button
          type="button"
          className="dev-ftp-btn"
          onClick={() => setHistoryOpen((o) => !o)}
        >
          <span className="material-symbols-outlined">history</span>
          {t("deviceDetail.config.ftp.history", { count: versions.length })}
        </button>
      </div>

      {historyOpen ? (
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
      ) : null}

      {/* IKI SUTUNLU IZGARA — tablo degil.
          Tabloda "Birim" ayri bir sutundu ve ekranin en saginda kaliyordu;
          goz deger ile birimi eslestirmek icin ekrani bastan sona tariyordu.
          Burada birim degerin HEMEN yaninda.

          DEGISTIRILEMEYEN satirlar GOSTERILMIYOR (kullanici istegi): metin
          alanlari sabit genislikte oldugu icin duzenlenemiyor ve
          "[not configured]" gibi kayitlar ekrani doldurup asil ayarlari
          gormeyi zorlastiriyordu. */}
      <div className="dev-ftp-grid">
        {duzenlenebilir.map((row) => {
          const draft = drafts[row.catIndex];
          const degisti = changes[row.catIndex] !== undefined;
          return (
            <label
              key={row.catIndex}
              className={`dev-ftp-item ${degisti ? "is-changed" : ""}`}
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
                    olanlarda basmak, birimsiz alanlarin kutusunu saga kaydirip
                    kolonlari tirtikli gosteriyordu. */}
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
    </section>
  );
}

function UploadButton({ onPick, busy }: { onPick: (f: File) => void; busy: boolean }) {
  const { t } = useTranslation();
  return (
    <label className={`dev-ftp-btn ${busy ? "is-disabled" : ""}`}>
      <span className="material-symbols-outlined">upload_file</span>
      {t("deviceDetail.config.ftp.upload")}
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
