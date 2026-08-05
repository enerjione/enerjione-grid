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
  deviceConfigDownloadUrl,
  fetchDeviceConfig,
  fetchDeviceConfigVersions,
  revertDeviceConfig,
  updateDeviceConfig,
  uploadDeviceConfig
} from "../../shared/api";
import type { ConfigCurrent, ConfigVersion } from "../../shared/types";
import { useToast } from "../../components/ToastProvider";

type Props = {
  deviceId: number;
  accessToken: string;
  /** Yalnizca engineer/installer duzenleyebilir. */
  canEdit: boolean;
};

const KAYNAK_ANAHTARI: Record<ConfigVersion["source"], string> = {
  sablon: "template",
  cihazdan_cekildi: "pulled",
  yuklendi: "uploaded",
  duzenlendi: "edited"
};

export function DeviceFtpConfigCard({ deviceId, accessToken, canEdit }: Props) {
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
        {canEdit ? <UploadButton onPick={upload} busy={busy} /> : null}
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
      </div>

      {/* Kaydetmek gondermek degildir — kullanici bunu BILMELI. */}
      <p className="device-config-hint">{t("deviceDetail.config.ftp.notSentHint")}</p>

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

      <div className="dev-ftp-table-wrap">
        <table className="dev-ftp-table">
          <thead>
            <tr>
              <th>{t("deviceDetail.config.ftp.col.setting")}</th>
              <th>{t("deviceDetail.config.ftp.col.value")}</th>
              <th>{t("deviceDetail.config.ftp.col.unit")}</th>
            </tr>
          </thead>
          <tbody>
            {current.rows.map((row) => {
              const editable = canEdit && row.valueInt !== null;
              const draft = drafts[row.catIndex];
              const degisti = changes[row.catIndex] !== undefined;
              return (
                <tr key={row.catIndex} className={degisti ? "is-changed" : ""}>
                  <td>
                    {/* Katalog yoksa ham CatIndex gosterilir — anlamsiz bir
                        satir bile GORUNMELI, gizlemek veri kaybi izlenimi
                        yaratirdi. */}
                    <span className="dev-ftp-name">{row.meaning ?? row.catIndex}</span>
                    <code className="dev-ftp-key">{row.catIndex}</code>
                  </td>
                  <td>
                    {editable ? (
                      <input
                        type="text"
                        inputMode="numeric"
                        value={draft ?? String(row.valueInt)}
                        disabled={busy}
                        onChange={(e) =>
                          setDrafts((d) => ({ ...d, [row.catIndex]: e.target.value }))
                        }
                      />
                    ) : (
                      <span className="dev-ftp-ro">
                        {row.valueText ?? row.valueInt ?? row.rawHex}
                      </span>
                    )}
                  </td>
                  <td>{row.unit ?? ""}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
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
