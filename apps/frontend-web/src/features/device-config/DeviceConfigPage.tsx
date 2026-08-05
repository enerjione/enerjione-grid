/**
 * Muhendislik > Cihaz Yapilandirma — cihaz odakli yerlesim.
 *
 *   SOL: cihaz listesi. Her satirda guncel surum rozeti (v3 · cihazdan
 *        cekildi) ve toplu secim kutusu. Satira tiklamak cihazi acar.
 *   SAG: secili cihazin config karti — cihaz detayindaki DeviceFtpConfigCard
 *        oldugu gibi YENIDEN KULLANILIR (duzenleme, surum gecmisi, cihazdan
 *        cek / cihaza uygula). Ayni is icin ikinci bir ekran yazilmaz.
 *
 * FTP ayarlari POPUP'tadir, sayfa degil: gunde bir kez bakilan bir ayar,
 * her gun kullanilan cihaz gezgininin yerini isgal etmemeli. Popup ayrica
 * BAGLANTI DURUMUNU gosterir: gomulu sunucu ayakta mi, su an hangi kimligi
 * kabul ediyor (kimlik degisimi ~30 sn'de yansir — o pencerede "senkron
 * bekleniyor" gorunur) ve son FTP hareketleri (kim baglandi, hangi dosya).
 *
 * Sablon yonetimi de popup; toplu uygulama sol listedeki secimden beslenir.
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";

import {
  applyTemplateToDevices,
  fetchConfigTemplates,
  fetchDeviceConfigSummaries,
  fetchFtpSettings,
  fetchFtpStatus,
  generateFtpPassword,
  setDefaultConfigTemplate,
  testFtpSettings,
  updateFtpSettings,
  uploadConfigTemplate
} from "../../shared/api";
import type {
  BulkApplyResult,
  ConfigTemplate,
  DeviceConfigSummary,
  DeviceRow,
  FtpEventRow as FtpEventRowT,
  FtpMode,
  FtpSettings,
  FtpStatus,
  FtpTestResult
} from "../../shared/types";
import { DEFAULT_DEVICE_MODEL } from "../../shared/types";
import { useToast } from "../../components/ToastProvider";
import { DeviceFtpConfigCard } from "../device-detail/DeviceFtpConfigCard";

type Props = {
  accessToken: string;
  devices: DeviceRow[];
};

/** Cihaz ekrani sinirlari — Horstmann SN2 FTP ekrani alan genislikleri. */
const MAX_FTP_USER = 29;
const MAX_FTP_PASS = 19;

const KAYNAK_ANAHTARI: Record<DeviceConfigSummary["source"], string> = {
  sablon: "template",
  cihazdan_cekildi: "pulled",
  yuklendi: "uploaded",
  duzenlendi: "edited"
};

export function DeviceConfigPage({ accessToken, devices }: Props) {
  const { t } = useTranslation();
  const toast = useToast();

  const [selectedId, setSelectedId] = useState<number | null>(devices[0]?.id ?? null);
  const [checked, setChecked] = useState<Set<number>>(new Set());
  const [search, setSearch] = useState("");
  const [summaries, setSummaries] = useState<Map<number, DeviceConfigSummary>>(new Map());
  const [templates, setTemplates] = useState<ConfigTemplate[]>([]);

  const [ftpOpen, setFtpOpen] = useState(false);
  const [templatesOpen, setTemplatesOpen] = useState(false);
  const [bulkOpen, setBulkOpen] = useState(false);

  const loadSummaries = useCallback(async () => {
    try {
      const list = await fetchDeviceConfigSummaries(accessToken);
      setSummaries(new Map(list.map((s) => [s.deviceId, s])));
    } catch {
      // Rozetler bir zenginliktir; alinamamasi listeyi engellememeli.
    }
  }, [accessToken]);

  const loadTemplates = useCallback(async () => {
    try {
      setTemplates(await fetchConfigTemplates(accessToken));
    } catch (exc) {
      toast.error(exc instanceof Error ? exc.message : String(exc));
    }
  }, [accessToken, toast]);

  useEffect(() => {
    void loadSummaries();
    void loadTemplates();
  }, [loadSummaries, loadTemplates]);

  const filtered = useMemo(() => {
    const q = search.trim().toLowerCase();
    if (!q) return devices;
    return devices.filter(
      (d) => d.name.toLowerCase().includes(q) || d.code.toLowerCase().includes(q)
    );
  }, [devices, search]);

  const selected = devices.find((d) => d.id === selectedId) ?? null;

  function toggleChecked(id: number) {
    setChecked((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  return (
    <section className="tab-panel dcfg-page">
      <header className="dcfg-toolbar">
        <input
          type="search"
          className="dcfg-search"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          placeholder={t("engineering.deviceConfig.list.search")}
        />
        <span className="dcfg-toolbar-spacer" />
        {checked.size > 0 ? (
          <button type="button" className="dcfg-btn is-primary" onClick={() => setBulkOpen(true)}>
            <span className="material-symbols-outlined">checklist</span>
            {t("engineering.deviceConfig.bulk.open", { count: checked.size })}
          </button>
        ) : null}
        <button type="button" className="dcfg-btn" onClick={() => setTemplatesOpen(true)}>
          <span className="material-symbols-outlined">description</span>
          {t("engineering.deviceConfig.templates.title")}
        </button>
        <button type="button" className="dcfg-btn" onClick={() => setFtpOpen(true)}>
          <span className="material-symbols-outlined">tune</span>
          {t("engineering.deviceConfig.ftp.title")}
        </button>
      </header>

      <div className="dcfg-layout">
        <aside className="dcfg-devlist" aria-label={t("engineering.deviceConfig.list.aria")}>
          {filtered.length === 0 ? (
            <p className="dcfg-empty">{t("engineering.deviceConfig.list.empty")}</p>
          ) : (
            filtered.map((d) => {
              const s = summaries.get(d.id);
              return (
                <div
                  key={d.id}
                  className={`dcfg-devrow ${d.id === selectedId ? "is-active" : ""}`}
                >
                  {/* Kutu TOPLU SECIM icindir, satiri acmaz — iki isin tek
                      tiklamada karismasi yanlis cihaza sablon basmakla biter. */}
                  <input
                    type="checkbox"
                    checked={checked.has(d.id)}
                    onChange={() => toggleChecked(d.id)}
                    onClick={(e) => e.stopPropagation()}
                  />
                  <button type="button" className="dcfg-devrow-main" onClick={() => setSelectedId(d.id)}>
                    <span className="dcfg-devrow-name">
                      {d.name}
                      <code>{d.code}</code>
                    </span>
                    <span className="dcfg-devrow-badge">
                      {s
                        ? `v${s.version} · ${t(`deviceDetail.config.ftp.source.${KAYNAK_ANAHTARI[s.source]}`)}`
                        : t("engineering.deviceConfig.list.noConfig")}
                    </span>
                  </button>
                </div>
              );
            })
          )}
        </aside>

        <main className="dcfg-detail">
          {selected ? (
            // key: cihaz degisince kart SIFIRDAN kurulur — onceki cihazin
            // taslak degisiklikleri yenisine tasinmasin.
            <DeviceFtpConfigCard
              key={selected.id}
              deviceId={selected.id}
              deviceCode={selected.code}
              accessToken={accessToken}
              canEdit
              canCommand
            />
          ) : (
            <p className="dcfg-empty">{t("engineering.deviceConfig.list.pickOne")}</p>
          )}
        </main>
      </div>

      {ftpOpen ? (
        <FtpSettingsModal
          accessToken={accessToken}
          toast={toast}
          onClose={() => setFtpOpen(false)}
        />
      ) : null}

      {templatesOpen ? (
        <TemplatesModal
          accessToken={accessToken}
          devices={devices}
          templates={templates}
          toast={toast}
          onChanged={() => void loadTemplates()}
          onClose={() => setTemplatesOpen(false)}
        />
      ) : null}

      {bulkOpen ? (
        <BulkApplyModal
          accessToken={accessToken}
          devices={devices}
          templates={templates}
          checked={checked}
          summaries={summaries}
          toast={toast}
          onDone={() => {
            setChecked(new Set());
            void loadSummaries();
          }}
          onClose={() => setBulkOpen(false)}
        />
      ) : null}
    </section>
  );
}

// ===== FTP ayarlari + baglanti durumu popup'i ==============================
function FtpSettingsModal({
  accessToken,
  toast,
  onClose
}: {
  accessToken: string;
  toast: ReturnType<typeof useToast>;
  onClose: () => void;
}) {
  const { t } = useTranslation();
  const [loaded, setLoaded] = useState<FtpSettings | null>(null);
  const [mode, setMode] = useState<FtpMode>("gomulu");
  // DAHILI ve HARICI kimlikler AYRI durumdadir — mod degistirmek digerinin
  // alanlarina dokunmaz. Tek set varken harici sunucu bilgileri dahili
  // sunucuya siziyordu (cihazlar bir anda giremez olmustu).
  const [embUser, setEmbUser] = useState("device");
  const [embPass, setEmbPass] = useState("");
  const [extHost, setExtHost] = useState("");
  const [extPort, setExtPort] = useState("21");
  const [extUser, setExtUser] = useState("");
  const [extPass, setExtPass] = useState("");
  const [directory, setDirectory] = useState("/SN20/FOTA/");
  const [pollInterval, setPollInterval] = useState("300");
  const [busy, setBusy] = useState(false);
  const [testResult, setTestResult] = useState<FtpTestResult | null>(null);
  const [status, setStatus] = useState<FtpStatus | null>(null);

  const load = useCallback(async () => {
    try {
      const s = await fetchFtpSettings(accessToken);
      setLoaded(s);
      setMode(s.mode);
      setEmbUser(s.embeddedUsername);
      setEmbPass(s.embeddedPassword ?? "");
      setExtHost(s.host ?? "");
      setExtPort(String(s.port));
      setExtUser(s.username);
      setExtPass(s.password ?? "");
      setDirectory(s.directory);
      setPollInterval(String(s.pollIntervalSec));
    } catch (exc) {
      toast.error(exc instanceof Error ? exc.message : String(exc));
    }
  }, [accessToken, toast]);

  /** PASV/cihaz ekrani adresi — kullanicidan IP ISTENMEZ: operator sisteme
   *  hangi adresten ulasiyorsa cihazlar da ayni LAN adresinden ulasir.
   *  localhost ile gelistirme durumunda anlamsiz olacagi icin gonderilmez. */
  function autoEmbeddedHost(): string | null {
    const h = window.location.hostname;
    if (!h || h === "localhost" || h.startsWith("127.")) return null;
    return h;
  }

  const loadStatus = useCallback(async () => {
    try {
      setStatus(await fetchFtpStatus(accessToken));
    } catch {
      setStatus(null);
    }
  }, [accessToken]);

  useEffect(() => {
    void load();
    void loadStatus();
    // Kimlik degisimi sunucuya ~30 sn'de yansir; panel acikken durumu tazele
    // ki kullanici "senkron bekleniyor -> senkron" gecisini GORSUN.
    const timer = window.setInterval(() => void loadStatus(), 10_000);
    return () => window.clearInterval(timer);
  }, [load, loadStatus]);

  // Aktif modun alanlari dogrulanir; diger modun alanlari forma GIRMEZ.
  const aktifUser = mode === "gomulu" ? embUser : extUser;
  const aktifPass = mode === "gomulu" ? embPass : extPass;
  const userTooLong = aktifUser.length > MAX_FTP_USER;
  const passTooLong = aktifPass.length > MAX_FTP_PASS;
  const passTooShort = aktifPass.length > 0 && aktifPass.length < 6;
  // Kimlik degisim uyarisi yalniz DAHILI modda: cihaz ekranlari elle
  // guncellenmek zorunda. Harici modda parola musterinin sunucusunundur.
  const credentialsChanged =
    loaded !== null &&
    mode === "gomulu" &&
    (embUser !== loaded.embeddedUsername || embPass !== (loaded.embeddedPassword ?? ""));

  const dirty =
    loaded !== null &&
    (mode !== loaded.mode ||
      directory !== loaded.directory ||
      (mode === "gomulu"
        ? embUser !== loaded.embeddedUsername || embPass !== (loaded.embeddedPassword ?? "")
        : extHost !== (loaded.host ?? "") ||
          extPort !== String(loaded.port) ||
          extUser !== loaded.username ||
          extPass !== (loaded.password ?? "") ||
          pollInterval !== String(loaded.pollIntervalSec)));

  async function generate() {
    setBusy(true);
    try {
      setEmbPass(await generateFtpPassword(accessToken));
    } catch (exc) {
      toast.error(exc instanceof Error ? exc.message : String(exc));
    } finally {
      setBusy(false);
    }
  }

  async function save() {
    if (userTooLong || passTooLong || passTooShort) return;
    setBusy(true);
    try {
      let s: FtpSettings;
      if (mode === "gomulu") {
        const otoAdres = autoEmbeddedHost();
        s = await updateFtpSettings(accessToken, {
          mode,
          embeddedUsername: embUser.trim(),
          // Bos parola GONDERILMEZ: mevcut parolayi silmek degil, dokunmamak
          // demektir. Parola degistirmenin tek yolu yeni deger yazmaktir.
          ...(embPass ? { embeddedPassword: embPass } : {}),
          // PASV adresi otomatik — kullanicidan IP istemek sacmaydi.
          ...(otoAdres ? { embeddedHost: otoAdres } : {}),
          directory: directory.trim() || "/"
        });
      } else {
        const portNum = Number(extPort);
        const pollNum = Number(pollInterval);
        if (!Number.isInteger(portNum) || portNum < 1 || portNum > 65535) {
          toast.error(t("engineering.deviceConfig.ftp.portInvalid"));
          return;
        }
        if (!extHost.trim()) {
          toast.error(t("engineering.deviceConfig.ftp.hostRequired"));
          return;
        }
        s = await updateFtpSettings(accessToken, {
          mode,
          host: extHost.trim(),
          port: portNum,
          username: extUser.trim(),
          ...(extPass ? { password: extPass } : {}),
          directory: directory.trim() || "/",
          ...(Number.isInteger(pollNum) && pollNum >= 60 ? { pollIntervalSec: pollNum } : {})
        });
      }
      setLoaded(s);
      setTestResult(null);
      toast.success(t("engineering.deviceConfig.ftp.saved"));
      void loadStatus();
    } catch (exc) {
      toast.error(exc instanceof Error ? exc.message : String(exc));
    } finally {
      setBusy(false);
    }
  }

  async function test() {
    setBusy(true);
    setTestResult(null);
    try {
      setTestResult(await testFtpSettings(accessToken));
    } catch (exc) {
      toast.error(exc instanceof Error ? exc.message : String(exc));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="dcfg-modal-backdrop" role="dialog" aria-modal="true">
      {/* SABIT boyutlu, iki sutunlu popup: solda baglanti durumu + log
          listesi (kendi icinde kayar), sagda ayar formu. Loglar govdeye
          eklendikce popup'in boyu UZAMAZ — eski tek sutunlu halin sikayet
          edilen kusuru buydu. */}
      <div className="dcfg-modal dcfg-modal--ftp">
        <div className="dcfg-modal-head">
          <h4>
            <span className="material-symbols-outlined">dns</span>
            {t("engineering.deviceConfig.ftp.title")}
          </h4>
          <button type="button" className="dcfg-btn is-small" onClick={onClose}>
            <span className="material-symbols-outlined">close</span>
          </button>
        </div>

        <div className="dcfg-ftp-body">
          <aside className="dcfg-ftp-side">
            <FtpStatusPanel status={status} expectedUser={loaded?.embeddedUsername ?? null} />
          </aside>

          <div className="dcfg-ftp-main">
        <div className="dcfg-mode-row" role="radiogroup">
          {(["gomulu", "harici"] as const).map((m) => (
            <label key={m} className={`dcfg-mode ${mode === m ? "is-active" : ""}`}>
              <input
                type="radio"
                name="ftp-mode"
                checked={mode === m}
                onChange={() => setMode(m)}
                disabled={busy}
              />
              <strong>{t(`engineering.deviceConfig.ftp.mode.${m}`)}</strong>
              <small>{t(`engineering.deviceConfig.ftp.modeDesc.${m}`)}</small>
            </label>
          ))}
        </div>

        <div className="dcfg-grid">
          {/* DAHILI modda adres/port ALANI YOK: adres tarayicinin kullandigi
              adresten otomatik alinir (kullanicidan kendi IP'sini istemek
              sacmaydi), port sabit 21. Degerler cihaz ekranina girilecek
              bilgi kutusunda gosterilir. */}
          {mode === "harici" ? (
            <div className="dcfg-host-row">
              <label>
                {t("engineering.deviceConfig.ftp.host")}
                <input
                  type="text"
                  value={extHost}
                  disabled={busy}
                  onChange={(e) => setExtHost(e.target.value)}
                  placeholder="ftp.example.com"
                />
              </label>
              <label className="dcfg-port">
                {t("engineering.deviceConfig.ftp.port")}
                <input
                  type="text"
                  inputMode="numeric"
                  value={extPort}
                  disabled={busy}
                  onChange={(e) => setExtPort(e.target.value)}
                />
              </label>
            </div>
          ) : null}
          <label>
            {t("engineering.deviceConfig.ftp.username")}
            {/* Sinir ipucu YOK: maxLength zaten yazmayi durduruyor. */}
            <input
              type="text"
              value={mode === "gomulu" ? embUser : extUser}
              disabled={busy}
              maxLength={MAX_FTP_USER}
              onChange={(e) =>
                mode === "gomulu" ? setEmbUser(e.target.value) : setExtUser(e.target.value)
              }
            />
          </label>
          <label>
            {t("engineering.deviceConfig.ftp.password")}
            {/* IKI MODUN PAROLASI FARKLI SEYDIR:
                - Dahili: BIZIM sunucumuzun kimligi. Kullanici onu okuyup
                  cihaz ekranina elle gececek -> acik gosterilir ve "Uret"
                  okunabilir parola onerir.
                - Harici: MUSTERININ sunucusunun parolasi. Bizim uretecegimiz
                  bir sey degil -> normal parola alani gibi maskeli girilir,
                  "Uret" dugmesi GOSTERILMEZ. */}
            {mode === "gomulu" ? (
              <span className="dcfg-pass-row">
                <input
                  type="text"
                  value={embPass}
                  disabled={busy}
                  maxLength={MAX_FTP_PASS}
                  onChange={(e) => setEmbPass(e.target.value)}
                  className={passTooShort ? "is-invalid" : ""}
                />
                <button type="button" className="dcfg-btn" disabled={busy} onClick={() => void generate()}>
                  <span className="material-symbols-outlined">refresh</span>
                  {t("engineering.deviceConfig.ftp.generate")}
                </button>
              </span>
            ) : (
              <input
                type="password"
                autoComplete="new-password"
                value={extPass}
                disabled={busy}
                maxLength={MAX_FTP_PASS}
                onChange={(e) => setExtPass(e.target.value)}
                className={passTooShort ? "is-invalid" : ""}
              />
            )}
            {passTooShort ? (
              <small className="dcfg-hint">{t("engineering.deviceConfig.ftp.passTooShort")}</small>
            ) : mode === "gomulu" ? (
              <small className="dcfg-hint">
                {t("engineering.deviceConfig.ftp.passLimit", { max: MAX_FTP_PASS })}
              </small>
            ) : null}
          </label>
          <label>
            {t("engineering.deviceConfig.ftp.directory")}
            <input
              type="text"
              value={directory}
              disabled={busy}
              onChange={(e) => setDirectory(e.target.value)}
              placeholder="/SN20/FOTA/"
            />
          </label>
          {mode === "harici" ? (
            <label>
              {t("engineering.deviceConfig.ftp.pollInterval")}
              <input
                type="text"
                inputMode="numeric"
                value={pollInterval}
                disabled={busy}
                onChange={(e) => setPollInterval(e.target.value)}
              />
              <small className="dcfg-hint">{t("engineering.deviceConfig.ftp.pollHint")}</small>
            </label>
          ) : null}
        </div>

        {/* Cihaz FTP ekranina girilecek degerler — tek bakista. Adres,
            kaydedilmis embeddedHost; henuz yoksa tarayicinin adresi. */}
        {mode === "gomulu" ? (
          <p className="dcfg-note">
            {t("engineering.deviceConfig.ftp.deviceScreenInfo", {
              host: loaded?.embeddedHost ?? autoEmbeddedHost() ?? "—",
              dir: directory || "/"
            })}
          </p>
        ) : null}

        {/* KIMLIK DEGISIM UYARISI: sunucu yeni kimligi ~30 sn'de alir ama
            cihazlar ESKI kimlikle gelmeye devam eder — her cihazin FTP
            ekrani elle guncellenene kadar girisleri REDDEDILIR. Bunu
            soylememek, "parolayi degistirdim, saha koptu" surpriziyle biter. */}
        {credentialsChanged ? (
          <p className="dcfg-note is-warning">
            {t("engineering.deviceConfig.ftp.credentialWarning")}
          </p>
        ) : null}

        {testResult ? (
          <p className={`dcfg-test-result ${testResult.ok ? "is-ok" : "is-bad"}`}>
            <span className="material-symbols-outlined">
              {testResult.ok ? "check_circle" : "error"}
            </span>
            {testResult.detail}
            {testResult.configFiles !== null
              ? ` ${t("engineering.deviceConfig.ftp.testFiles", { count: testResult.configFiles })}`
              : ""}
          </p>
        ) : null}

        <div className="dcfg-actions">
          {mode === "harici" ? (
            <button
              type="button"
              className="dcfg-btn"
              disabled={busy || dirty}
              onClick={() => void test()}
              title={dirty ? t("engineering.deviceConfig.ftp.testNeedsSave") : undefined}
            >
              <span className="material-symbols-outlined">cable</span>
              {t("engineering.deviceConfig.ftp.test")}
            </button>
          ) : null}
          <button
            type="button"
            className="dcfg-btn is-primary"
            disabled={busy || !dirty || userTooLong || passTooLong || passTooShort}
            onClick={() => void save()}
          >
            {t("engineering.deviceConfig.ftp.save")}
          </button>
        </div>
          </div>
        </div>
      </div>
    </div>
  );
}

/** Baglanti durumu: sunucu sagligi + aktif kimlik + son FTP hareketleri.

    Popup'in SOL sutununu doldurur; olay listesi kendi icinde kayar, popup'i
    uzatmaz. */
function FtpStatusPanel({
  status,
  expectedUser
}: {
  status: FtpStatus | null;
  expectedUser: string | null;
}) {
  const { t } = useTranslation();

  return (
    <div className="dcfg-status">
      <h5>{t("engineering.deviceConfig.status.title")}</h5>

      {!status ? (
        <p className="dcfg-empty">{t("common.loading")}</p>
      ) : (
        <>
          {status.mode === "gomulu" && status.server ? (
            status.server.reachable ? (
              <p className={`dcfg-status-line ${status.server.synced === false ? "is-warn" : "is-ok"}`}>
                <span className="material-symbols-outlined">
                  {status.server.synced === false ? "hourglass_top" : "check_circle"}
                </span>
                {status.server.synced === false
                  ? t("engineering.deviceConfig.status.pendingSync", {
                      active: status.server.username ?? "?",
                      expected: expectedUser ?? "?"
                    })
                  : t("engineering.deviceConfig.status.serverUp", {
                      user: status.server.username ?? "?",
                      count: status.server.connections ?? 0
                    })}
              </p>
            ) : (
              <p className="dcfg-status-line is-bad">
                <span className="material-symbols-outlined">error</span>
                {t("engineering.deviceConfig.status.serverDown")}
              </p>
            )
          ) : null}

          {status.events.length === 0 ? (
            <p className="dcfg-empty">{t("engineering.deviceConfig.status.eventsEmpty")}</p>
          ) : (
            <ul className="dcfg-status-events">
              {status.events.map((e, i) => (
                <FtpEventItem key={i} event={e} />
              ))}
            </ul>
          )}
        </>
      )}
    </div>
  );
}

/** Olay tipi -> ikon + baslik anahtari. Bilinmeyen tip ham `message` ile
 *  duser — yeni bir olay turu eklendiginde liste BOS gorunmez. */
const FTP_EVT: Record<string, { icon: string; warn?: boolean }> = {
  ftp_login: { icon: "person" },
  ftp_upload: { icon: "upload_file" },
  ftp_download: { icon: "file_download" },
  ftp_upload_incomplete: { icon: "warning", warn: true },
  ftp_settings_updated: { icon: "tune" },
  ftp_poll_ingested: { icon: "cloud_download" },
  ftp_poll_unreachable: { icon: "link_off", warn: true },
  ftp_poll_recovered: { icon: "link" },
  ftp_poll_error: { icon: "warning", warn: true },
  ftp_sync_failed: { icon: "warning", warn: true }
};

/** Ayar alanlarinin okunur adlari (ftp_settings_updated ayrintisi icin). */
const FTP_FIELD_KEY: Record<string, string> = {
  mode: "engineering.deviceConfig.ftp.modeLabel",
  host: "engineering.deviceConfig.ftp.host",
  port: "engineering.deviceConfig.ftp.port",
  username: "engineering.deviceConfig.ftp.username",
  password: "engineering.deviceConfig.ftp.password",
  directory: "engineering.deviceConfig.ftp.directory",
  poll_interval_sec: "engineering.deviceConfig.ftp.pollInterval"
};

/** Tek log satiri: ikon + baslik + kisa ayrinti + sagda saat.

    Ham denetim metni ("mode=harici, host=..., password(degisti)") teknik bir
    dokumdu; burada olay tipine gore DERLI satir kurulur, ham metin yalnizca
    bilinmeyen tiplerde yedek. */
function FtpEventItem({ event }: { event: FtpEventRowT }) {
  const { t } = useTranslation();
  const bilgi = FTP_EVT[event.eventType];
  const meta = event.metadata ?? {};
  const d = new Date(event.createdAt);
  // Ayni gunun olayinda tarih tekrari gurultu; yalnizca saat yeter.
  const zaman =
    d.toDateString() === new Date().toDateString()
      ? d.toLocaleTimeString()
      : d.toLocaleString();

  let baslik: string;
  let ayrinti: string | null = null;
  if (!bilgi) {
    baslik = event.message;
  } else {
    baslik = t(`engineering.deviceConfig.status.evt.${event.eventType}`);
    switch (event.eventType) {
      case "ftp_login":
        ayrinti = typeof meta.remote_ip === "string" ? meta.remote_ip : null;
        break;
      case "ftp_upload":
      case "ftp_download":
      case "ftp_upload_incomplete":
      case "ftp_poll_ingested":
        ayrinti = typeof meta.filename === "string" ? meta.filename : null;
        if (ayrinti && typeof meta.version === "number") ayrinti += ` (v${meta.version})`;
        break;
      case "ftp_settings_updated":
        ayrinti = Array.isArray(meta.fields)
          ? (meta.fields as string[])
              .map((f) => (FTP_FIELD_KEY[f] ? t(FTP_FIELD_KEY[f]) : f))
              .join(", ")
          : null;
        break;
      case "ftp_poll_unreachable":
      case "ftp_poll_error":
        ayrinti = typeof meta.error === "string" ? meta.error : null;
        break;
      case "ftp_sync_failed":
        ayrinti = [meta.filename, meta.error]
          .filter((s): s is string => typeof s === "string")
          .join(" — ") || null;
        break;
      default:
        break;
    }
  }

  return (
    <li className={bilgi?.warn || event.severity === "warning" ? "is-warn" : ""}>
      <span className="material-symbols-outlined dcfg-evt-icon">
        {bilgi?.icon ?? "info"}
      </span>
      <span className="dcfg-evt-body">
        <span className="dcfg-evt-head">
          <strong>{baslik}</strong>
          <time>{zaman}</time>
        </span>
        {ayrinti ? <span className="dcfg-evt-detail">{ayrinti}</span> : null}
      </span>
    </li>
  );
}

// ===== Sablon yonetimi popup'i =============================================
function TemplatesModal({
  accessToken,
  devices,
  templates,
  toast,
  onChanged,
  onClose
}: {
  accessToken: string;
  devices: DeviceRow[];
  templates: ConfigTemplate[];
  toast: ReturnType<typeof useToast>;
  onChanged: () => void;
  onClose: () => void;
}) {
  const { t } = useTranslation();
  const [busy, setBusy] = useState(false);
  const [upName, setUpName] = useState("");
  const [upModel, setUpModel] = useState(DEFAULT_DEVICE_MODEL);
  const [upDefault, setUpDefault] = useState(true);
  const [upFile, setUpFile] = useState<File | null>(null);

  const models = useMemo(() => {
    const set = new Set<string>(devices.map((d) => d.model));
    set.add(DEFAULT_DEVICE_MODEL);
    return [...set].sort();
  }, [devices]);

  async function upload() {
    if (!upFile || !upName.trim()) return;
    setBusy(true);
    try {
      await uploadConfigTemplate(
        accessToken,
        { name: upName.trim(), deviceModel: upModel, isDefault: upDefault },
        upFile
      );
      toast.success(t("engineering.deviceConfig.templates.uploaded", { name: upName.trim() }));
      setUpName("");
      setUpFile(null);
      onChanged();
    } catch (exc) {
      toast.error(exc instanceof Error ? exc.message : String(exc));
    } finally {
      setBusy(false);
    }
  }

  async function makeDefault(id: number) {
    setBusy(true);
    try {
      await setDefaultConfigTemplate(accessToken, id);
      onChanged();
      toast.success(t("engineering.deviceConfig.templates.defaultSet"));
    } catch (exc) {
      toast.error(exc instanceof Error ? exc.message : String(exc));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="dcfg-modal-backdrop" role="dialog" aria-modal="true">
      <div className="dcfg-modal dcfg-modal--wide">
        <div className="dcfg-modal-head">
          <h4>
            <span className="material-symbols-outlined">description</span>
            {t("engineering.deviceConfig.templates.title")}
          </h4>
          <button type="button" className="dcfg-btn is-small" onClick={onClose}>
            <span className="material-symbols-outlined">close</span>
          </button>
        </div>

        {templates.length === 0 ? (
          <p className="dcfg-empty">{t("engineering.deviceConfig.templates.empty")}</p>
        ) : (
          <table className="dcfg-table">
            <thead>
              <tr>
                <th>{t("engineering.deviceConfig.templates.name")}</th>
                <th>{t("engineering.deviceConfig.templates.model")}</th>
                <th>{t("engineering.deviceConfig.templates.file")}</th>
                <th>{t("engineering.deviceConfig.templates.created")}</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {templates.map((s) => (
                <tr key={s.id}>
                  <td>
                    {s.name}
                    {s.isDefault ? (
                      <span className="dcfg-badge">{t("engineering.deviceConfig.templates.default")}</span>
                    ) : null}
                  </td>
                  <td><code>{s.deviceModel}</code></td>
                  <td>
                    {s.sourceFilename ?? "—"}
                    <small className="dcfg-dim"> ({s.sizeBytes} B)</small>
                  </td>
                  <td>
                    {new Date(s.createdAt).toLocaleString()}
                    {s.createdBy ? <small className="dcfg-dim"> {s.createdBy}</small> : null}
                  </td>
                  <td>
                    {!s.isDefault ? (
                      <button
                        type="button"
                        className="dcfg-btn is-small"
                        disabled={busy}
                        onClick={() => void makeDefault(s.id)}
                      >
                        {t("engineering.deviceConfig.templates.makeDefault")}
                      </button>
                    ) : null}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}

        <div className="dcfg-upload-row">
          <label>
            {t("engineering.deviceConfig.templates.name")}
            <input
              type="text"
              value={upName}
              disabled={busy}
              maxLength={120}
              onChange={(e) => setUpName(e.target.value)}
              placeholder={t("engineering.deviceConfig.templates.namePlaceholder")}
            />
          </label>
          <label>
            {t("engineering.deviceConfig.templates.model")}
            <select value={upModel} disabled={busy} onChange={(e) => setUpModel(e.target.value)}>
              {models.map((m) => (
                <option key={m} value={m}>{m}</option>
              ))}
            </select>
          </label>
          <label className="dcfg-check">
            <input
              type="checkbox"
              checked={upDefault}
              disabled={busy}
              onChange={(e) => setUpDefault(e.target.checked)}
            />
            {t("engineering.deviceConfig.templates.markDefault")}
          </label>
          <label className="dcfg-btn dcfg-file">
            <span className="material-symbols-outlined">upload_file</span>
            {upFile ? upFile.name : t("engineering.deviceConfig.templates.pickFile")}
            <input
              type="file"
              accept=".csv"
              hidden
              disabled={busy}
              onChange={(e) => {
                const f = e.target.files?.[0];
                e.target.value = "";
                if (f) setUpFile(f);
              }}
            />
          </label>
          <button
            type="button"
            className="dcfg-btn is-primary"
            disabled={busy || !upFile || !upName.trim()}
            onClick={() => void upload()}
          >
            {t("engineering.deviceConfig.templates.upload")}
          </button>
        </div>
        <p className="dcfg-note">{t("engineering.deviceConfig.templates.checksumNote")}</p>
      </div>
    </div>
  );
}

// ===== Toplu uygulama popup'i ==============================================
function BulkApplyModal({
  accessToken,
  devices,
  templates,
  checked,
  summaries,
  toast,
  onDone,
  onClose
}: {
  accessToken: string;
  devices: DeviceRow[];
  templates: ConfigTemplate[];
  checked: Set<number>;
  summaries: Map<number, DeviceConfigSummary>;
  toast: ReturnType<typeof useToast>;
  onDone: () => void;
  onClose: () => void;
}) {
  const { t } = useTranslation();
  const [busy, setBusy] = useState(false);
  const [templateId, setTemplateId] = useState<number | null>(
    templates.length === 1 ? templates[0].id : null
  );
  const [result, setResult] = useState<BulkApplyResult | null>(null);

  const template = templates.find((s) => s.id === templateId) ?? null;
  const checkedDevices = devices.filter((d) => checked.has(d.id));
  // Model uyusmayanlar UYGULANMAZ; backend de reddeder ama onay ekraninda
  // gostermek "500 cihaza bastim sandim, 40'i atlandi" surprizini onler.
  const eligible = template
    ? checkedDevices.filter((d) => d.model === template.deviceModel)
    : [];
  const ineligible = template
    ? checkedDevices.filter((d) => d.model !== template.deviceModel)
    : [];

  const deviceName = (id: number) => devices.find((d) => d.id === id)?.name ?? `#${id}`;

  async function apply() {
    if (!template || eligible.length === 0) return;
    setBusy(true);
    try {
      const r = await applyTemplateToDevices(
        accessToken,
        template.id,
        eligible.map((d) => d.id)
      );
      setResult(r);
      if (r.failed.length === 0) {
        toast.success(t("engineering.deviceConfig.bulk.done", { count: r.applied.length }));
      } else {
        toast.error(
          t("engineering.deviceConfig.bulk.doneWithFailures", {
            ok: r.applied.length,
            failed: r.failed.length
          })
        );
      }
      onDone();
    } catch (exc) {
      toast.error(exc instanceof Error ? exc.message : String(exc));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="dcfg-modal-backdrop" role="dialog" aria-modal="true">
      <div className="dcfg-modal">
        <div className="dcfg-modal-head">
          <h4>
            <span className="material-symbols-outlined">checklist</span>
            {t("engineering.deviceConfig.bulk.confirmTitle")}
          </h4>
          <button type="button" className="dcfg-btn is-small" onClick={onClose}>
            <span className="material-symbols-outlined">close</span>
          </button>
        </div>

        {result ? (
          <>
            {result.failed.length > 0 ? (
              <div className="dcfg-failed">
                <strong>
                  {t("engineering.deviceConfig.bulk.failedTitle", { count: result.failed.length })}
                </strong>
                <ul>
                  {result.failed.map((f) => (
                    <li key={f.device_id}>
                      {deviceName(f.device_id)} —{" "}
                      {t(`engineering.deviceConfig.bulk.reason.${f.reason}`, f.detail ?? f.reason)}
                    </li>
                  ))}
                </ul>
              </div>
            ) : (
              <p className="dcfg-status-line is-ok">
                <span className="material-symbols-outlined">check_circle</span>
                {t("engineering.deviceConfig.bulk.done", { count: result.applied.length })}
              </p>
            )}
            <div className="dcfg-actions">
              <button type="button" className="dcfg-btn is-primary" onClick={onClose}>
                {t("engineering.deviceConfig.bulk.close")}
              </button>
            </div>
          </>
        ) : (
          <>
            <label className="dcfg-modal-field">
              {t("engineering.deviceConfig.bulk.template")}
              <select
                value={templateId ?? ""}
                disabled={busy}
                onChange={(e) => setTemplateId(e.target.value ? Number(e.target.value) : null)}
              >
                <option value="">{t("engineering.deviceConfig.bulk.pickTemplate")}</option>
                {templates.map((s) => (
                  <option key={s.id} value={s.id}>
                    {s.name} ({s.deviceModel})
                  </option>
                ))}
              </select>
            </label>

            {template ? (
              <>
                <p className="dcfg-confirm-summary">
                  {t("engineering.deviceConfig.bulk.confirmSummary", {
                    count: eligible.length,
                    template: template.name,
                    model: template.deviceModel
                  })}
                </p>
                <ul className="dcfg-confirm-list">
                  {eligible.map((d) => {
                    const v = summaries.get(d.id)?.version ?? null;
                    return (
                      <li key={d.id}>
                        {d.name}:{" "}
                        {v !== null
                          ? t("engineering.deviceConfig.bulk.versionChange", { from: v, to: v + 1 })
                          : t("engineering.deviceConfig.bulk.versionNew")}
                      </li>
                    );
                  })}
                </ul>
                {ineligible.length > 0 ? (
                  <p className="dcfg-note is-warning">
                    {t("engineering.deviceConfig.bulk.ineligibleNote", {
                      count: ineligible.length,
                      model: template.deviceModel
                    })}
                  </p>
                ) : null}
                <p className="dcfg-note">{t("engineering.deviceConfig.bulk.confirmNote")}</p>
              </>
            ) : null}

            <div className="dcfg-actions">
              <button type="button" className="dcfg-btn" disabled={busy} onClick={onClose}>
                {t("engineering.deviceConfig.bulk.cancel")}
              </button>
              <button
                type="button"
                className="dcfg-btn is-danger"
                disabled={busy || !template || eligible.length === 0}
                onClick={() => void apply()}
              >
                {t("engineering.deviceConfig.bulk.confirm", { count: eligible.length })}
              </button>
            </div>
          </>
        )}
      </div>
    </div>
  );
}
