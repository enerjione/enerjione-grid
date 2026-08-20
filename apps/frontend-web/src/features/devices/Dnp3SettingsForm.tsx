import { useEffect } from "react";
import { useTranslation } from "react-i18next";

import type { DialInReadbackStatus, Dnp3ExtendedSettings, SessionPolicy } from "../../shared/types";
import {
  COMMUNICATION_GRACE_MIN_DEFAULT,
  COMMUNICATION_GRACE_MIN_MAX,
  COMMUNICATION_GRACE_MIN_MIN,
  SMART_LISTEN_RECONNECT_MAX_SEC,
  SMART_LISTEN_RECONNECT_MIN_SEC,
  SMART_MAX_SILENCE_MAX_SEC,
  SMART_MAX_SILENCE_MIN_SEC,
  derivedMaxSilenceSec,
  dialInValidValues
} from "../../shared/types";
import { missingCapabilities, requiredVersion } from "../../shared/gatewayCapabilities";

type Props = {
  value: Dnp3ExtendedSettings;
  onChange: (patch: Partial<Dnp3ExtendedSettings>) => void;
  /** Diger cihazlarda kullanilan master_ip_port'lar — initiating modunda
   *  otomatik atama bunlari hariç tutar. */
  usedMasterPorts?: number[];
  hideConnectionFields?: boolean;
  /** Dial-In'in cihazda GECERLI olup olmadigi. Form bunu kendisi CEKMEZ:
   *  saf kalir, veriyi paneli ceker ve prop olarak gecer. Tanimsizsa
   *  (istek basarisiz / cihazda dosya yok) blok HIC cizilmez — eksik veriyi
   *  "eslesiyor" gibi gostermektense hic gostermemek dogru taraftir. */
  dialInDurumu?: { readbackMin: number | null; status: DialInReadbackStatus };
  /** Gateway'in bildirdigi surum.
   *
   *  UC DURUM: `undefined` = henuz sorulmadi (uyari cizilmez), `null` =
   *  soruldu ama gateway surumunu bildirmedi (backend gibi EKSIK sayilir ve
   *  uyari "bilinmiyor" der), metin = bildirilen surum. */
  gatewayVersion?: string | null;
  /** Mevcut gateway guncelleme arayuzunu acar. Verilmezse baglanti cizilmez;
   *  form kendi basina yeni bir guncelleme yolu ACMAZ. */
  onGatewayUpdate?: () => void;
};

const INITIATING_PORT_RANGE_START = 20100;
const INITIATING_PORT_RANGE_END = 20700;

/** Kaniti olmayan alanin gosterimi. Bos birakmak "0" ya da "yok" gibi
 *  okunabiliyordu; tire acikca "bilmiyoruz" der. */
const DEGER_YOK = "—";

/** Cihazin kabul ettigi Dial-In degerleri — sozlesmeden TURETILIR. */
const DIAL_IN_OPTIONS = dialInValidValues();

/** Tolerans secenekleri 5 dk adimla turetilir; sinirlar sozlesmeden gelir ki
 *  aralik degisirse liste elle bakim istemeden takip etsin. */
const GRACE_OPTIONS = ((): number[] => {
  const degerler: number[] = [];
  for (let dk = COMMUNICATION_GRACE_MIN_MIN; dk <= COMMUNICATION_GRACE_MIN_MAX; dk += 5) {
    degerler.push(dk);
  }
  return degerler;
})();

function pickFreeInitiatingPort(used: number[]): number {
  const taken = new Set(used);
  for (let p = INITIATING_PORT_RANGE_START; p <= INITIATING_PORT_RANGE_END; p += 1) {
    if (!taken.has(p)) return p;
  }
  return INITIATING_PORT_RANGE_START; // tüm range dolu (600 cihaz aşıldı) — fallback
}

function Req() {
  return <span className="field-req" aria-hidden="true">*</span>;
}

function BoolSelect({
  id,
  label,
  value,
  onChange
}: {
  id: string;
  label: string;
  value: boolean;
  onChange: (v: boolean) => void;
}) {
  const { t } = useTranslation();
  return (
    <label className="dnp3-field" htmlFor={id}>
      <span className="dnp3-label">{label}</span>
      <select
        id={id}
        value={value ? "1" : "0"}
        onChange={(e) => onChange(e.target.value === "1")}
      >
        <option value="1">{t("engineering.dnp3.on")}</option>
        <option value="0">{t("engineering.dnp3.off")}</option>
      </select>
    </label>
  );
}

export function Dnp3SettingsForm({
  value,
  onChange,
  usedMasterPorts = [],
  hideConnectionFields = false,
  dialInDurumu,
  gatewayVersion,
  onGatewayUpdate
}: Props) {
  const { t } = useTranslation();
  const v = value;
  const set = onChange;

  const isInitiating = v.ip_endpoint_type === "initiating";
  // Dial-In ve tolerans yalnizca gateway'in uyku karari verdigi modlarda bir
  // sey ifade eder. Surekli modda GIZLENIR ama degerler state'te DURUR:
  // politikayi deneyip geri donen operator girdiklerini kaybetmesin.
  const oturumUykulu = v.session_policy === "smart" || v.session_policy === "auto";
  const dialIn = v.dial_in_interval_min;
  const esikSn = derivedMaxSilenceSec(dialIn, v.communication_grace_min);

  // Initiating moda gecildiginde veya port range disinda kalan bir deger
  // varsa otomatik olarak ilk bos port'a sabitle. Boylece cihazi kaydeden
  // kullanici manuel port girmek/sectigi sayiyi kontrol etmek zorunda kalmaz
  // ve iki cihaz ayni port'u almaz.
  useEffect(() => {
    if (!isInitiating) return;
    const current = Number(v.master_ip_port);
    const inRange =
      Number.isFinite(current) &&
      current >= INITIATING_PORT_RANGE_START &&
      current <= INITIATING_PORT_RANGE_END;
    const conflicts = usedMasterPorts.includes(current);
    if (!inRange || conflicts) {
      const picked = pickFreeInitiatingPort(usedMasterPorts);
      if (picked !== current) {
        set({ master_ip_port: picked });
      }
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isInitiating, v.master_ip_port, usedMasterPorts.join(",")]);

  // 60 dk / 2 saat gibi okunakli etiket. Tam saate bolunmeyen degerler (72,
  // 90, 288...) DAKIKA kalir: "1,5 saat" yazilsaydi operator ayni ayari
  // cihazin kendi ekraninda arayamazdi.
  const dialInEtiket = (dk: number): string =>
    dk >= 120 && dk % 60 === 0
      ? `${dk / 60} ${t("engineering.dnp3.hours")}`
      : `${dk} ${t("engineering.dnp3.minutes")}`;

  // Dial-In secildiginde ACIKCA yazilmis sessizlik esigi temizlenir. Backend
  // cozum sirasinda explicit deger turetilmisi EZER; ikisi birden dururken
  // ekranda "75 dk" yazip gateway'in eski esige gore alarm uretmesi sessiz
  // bir yalan olurdu. Temizleme yalnizca kullanicinin ACIK seciminde olur —
  // mount'ta calisan bir effect eski kayitlari kendiliginden bozardi.
  const dialInDegistir = (dk: number | null) => {
    if (dk === null) {
      set({ dial_in_interval_min: null });
      return;
    }
    set({ dial_in_interval_min: dk, smart_max_silence_sec: null });
  };

  const politikaYardim =
    v.session_policy === "auto"
      ? t("engineering.dnp3.sessionPolicyAutoHelp")
      : v.session_policy === "smart"
        ? t("engineering.dnp3.sessionPolicySmartHelp")
        : t("engineering.dnp3.sessionPolicyContinuousHelp");

  // ISTENEN ile CIHAZDAN DOGRULANAN ayrisiyor mu? Yalnizca IKI TARAF DA
  // BILINIYORKEN "farkli" denir: dogrulanan yoksa bu bir ayrisma degil,
  // kanit eksikligidir ve durum satiri zaten onu soyler.
  const dialInFarkli =
    dialInDurumu !== undefined &&
    dialIn !== null &&
    dialInDurumu.readbackMin !== null &&
    dialIn !== dialInDurumu.readbackMin;

  // Anahtarlar SABIT yazili: dinamik olarak uretilen ceviri anahtarlari,
  // dil dosyasindan biri dustugunde ekrana ham anahtar dusurur ve hicbir
  // test bunu yakalayamaz.
  const dialInDurumEtiketi =
    dialInDurumu?.status === "eslesiyor"
      ? t("engineering.dnp3.dialInStatusMatched")
      : dialInDurumu?.status === "farkli"
        ? t("engineering.dnp3.dialInStatusDiffers")
        : t("engineering.dnp3.dialInStatusNone");

  // Gateway yetenek kapisi. Kural backend `gateway_compatibility.py`nin
  // aynasidir (bkz. shared/gatewayCapabilities.ts); surum henuz sorulmadiysa
  // (`undefined`) hicbir sey iddia etmeyiz.
  const eksikYetenekler =
    gatewayVersion === undefined
      ? []
      : missingCapabilities(v.session_policy, v.ip_endpoint_type, gatewayVersion);
  const uyumGerekliSurum = requiredVersion(eksikYetenekler);

  return (
    <div className="dnp3-settings-form">
      <h5 className="dnp3-settings-title">{t("engineering.dnp3.title")}</h5>
      <div className="dnp3-settings-grid">
        {!hideConnectionFields ? (
          <>
            <label className="dnp3-field">
              <span className="dnp3-label">
                {t("engineering.dnp3.endpointType")} <Req />
              </span>
              <select
                value={v.ip_endpoint_type}
                onChange={(e) => set({ ip_endpoint_type: e.target.value as "listening" | "initiating" })}
              >
                <option value="listening">{t("engineering.dnp3.modeListening")}</option>
                <option value="initiating">{t("engineering.dnp3.modeInitiating")}</option>
              </select>
            </label>
            <label className="dnp3-field">
              <span className="dnp3-label">
                {t("engineering.dnp3.masterIp")} <Req />
              </span>
              <input
                value={v.master_ip_address}
                onChange={(e) => set({ master_ip_address: e.target.value })}
                placeholder={isInitiating ? t("engineering.dnp3.masterIpInitPlaceholder") : t("engineering.dnp3.masterIpPlaceholder")}
              />
            </label>
            <label className="dnp3-field">
              <span className="dnp3-label">
                {t("engineering.dnp3.masterPort")} {isInitiating ? null : <Req />}
              </span>
              <input
                type="number"
                min={1}
                max={65535}
                value={v.master_ip_port}
                onChange={(e) => set({ master_ip_port: Number(e.target.value) || 1 })}
                disabled={isInitiating}
                title={isInitiating ? t("engineering.dnp3.masterPortInitTooltip") : undefined}
              />
            </label>
            <label className="dnp3-field">
              <span className="dnp3-label">
                {t("engineering.dnp3.masterAddr")} <Req />
              </span>
              <input
                type="number"
                min={0}
                max={65535}
                value={v.master_address ?? ""}
                placeholder={t("engineering.dnp3.masterAddrAuto")}
                title={t("engineering.dnp3.masterAddrHelp")}
                onChange={(e) =>
                  set({
                    master_address:
                      e.target.value.trim() === "" ? null : Number(e.target.value)
                  })
                }
              />
              <small className="dnp3-help">{t("engineering.dnp3.masterAddrHelp")}</small>
            </label>
          </>
        ) : null}
        {/* Oturum politikasi bir BAGLANTI alani DEGILDIR: `hideConnectionFields`
            ile birlikte gizlendigi surece cihaz duzenleme panelinde hicbir
            yerden ulasilamiyordu — panel kendi IP/port alanlarini ciziyor ve
            bu blogu da kapatiyordu. */}
        <label className="dnp3-field">
          <span className="dnp3-label">
            {t("engineering.dnp3.sessionPolicy")} <Req />
          </span>
          <select
            value={v.session_policy}
            onChange={(e) => set({ session_policy: e.target.value as SessionPolicy })}
          >
            {/* Uc tipi ile mod v1.14.0'dan beri ORTOGONAL: listening bir cihaz
                da Smart calisabilir, secenek kisitlanmaz. */}
            <option value="continuous">{t("engineering.dnp3.sessionPolicyContinuous")}</option>
            <option value="smart">{t("engineering.dnp3.sessionPolicySmart")}</option>
            <option value="auto">{t("engineering.dnp3.sessionPolicyAuto")}</option>
          </select>
          <small className="dnp3-help">{politikaYardim}</small>
        </label>
        {/* UYUMLULUK UYARISI — REDDETME DEGIL. Kayit kabul edilir (mesru akis
            "once cihazi yapilandir, sonra gateway'i guncelle"dir) ama ayarin
            sahada gecerli oldugu SOYLENMEZ: eski gateway bu alanlari ya yok
            sayar ya da tum config'i reddeder. */}
        {uyumGerekliSurum !== null ? (
          <div className="dnp3-compat-warn" role="status">
            <span>
              {t("engineering.dnp3.gatewayCompatWarn", {
                version: uyumGerekliSurum,
                current: gatewayVersion ?? t("engineering.dnp3.gatewayVersionUnknown")
              })}
            </span>
            {onGatewayUpdate ? (
              <button type="button" className="dnp3-compat-action" onClick={onGatewayUpdate}>
                {t("engineering.dnp3.gatewayUpdateAction")}
              </button>
            ) : null}
          </div>
        ) : null}
        {oturumUykulu ? (
          <>
            <label className="dnp3-field">
              <span className="dnp3-label">{t("engineering.dnp3.dialInInterval")}</span>
              <select
                value={dialIn === null ? "" : String(dialIn)}
                onChange={(e) =>
                  dialInDegistir(e.target.value === "" ? null : Number(e.target.value))
                }
              >
                <option value="">{t("engineering.dnp3.dialInIntervalUnset")}</option>
                {DIAL_IN_OPTIONS.map((dk) => (
                  <option key={dk} value={dk}>
                    {dialInEtiket(dk)}
                  </option>
                ))}
              </select>
              <small className="dnp3-help">{t("engineering.dnp3.dialInIntervalHelp")}</small>
            </label>
            {/* ISTENEN vs CIHAZDAN DOGRULANAN. Secili deger yalnizca "istenen"
                sutununda durur; "cihazda aktif" iddiasi CIHAZIN KENDI dosyasi
                okunmadan yapilmaz. Veri gelmediyse (prop tanimsiz) blok hic
                cizilmez — uydurma bir dogrulama gostermektense sessiz kal. */}
            {dialInDurumu ? (
              <div
                className={`dnp3-status-block${dialInFarkli ? " dnp3-status-block--warn" : ""}`}
              >
                <div className="dnp3-status-row">
                  <span className="dnp3-status-key">{t("engineering.dnp3.dialInConfigured")}</span>
                  <span className="dnp3-status-val">
                    {dialIn === null
                      ? t("engineering.dnp3.dialInIntervalUnset")
                      : dialInEtiket(dialIn)}
                  </span>
                </div>
                <div className="dnp3-status-row">
                  <span className="dnp3-status-key">{t("engineering.dnp3.dialInVerified")}</span>
                  <span className="dnp3-status-val">
                    {dialInDurumu.readbackMin === null
                      ? DEGER_YOK
                      : dialInEtiket(dialInDurumu.readbackMin)}
                  </span>
                </div>
                <div className="dnp3-status-row">
                  <span className="dnp3-status-key">{t("engineering.dnp3.dialInStatus")}</span>
                  <span className="dnp3-status-val">{dialInDurumEtiketi}</span>
                </div>
                {dialInFarkli ? (
                  <p className="dnp3-status-note">{t("engineering.dnp3.dialInMismatch")}</p>
                ) : null}
              </div>
            ) : null}
            <label className="dnp3-field">
              <span className="dnp3-label">{t("engineering.dnp3.communicationGrace")}</span>
              <select
                value={v.communication_grace_min === null ? "" : String(v.communication_grace_min)}
                onChange={(e) =>
                  set({
                    communication_grace_min:
                      e.target.value === "" ? null : Number(e.target.value)
                  })
                }
              >
                <option value="">
                  {t("engineering.dnp3.communicationGraceDefault", {
                    minutes: COMMUNICATION_GRACE_MIN_DEFAULT
                  })}
                </option>
                {GRACE_OPTIONS.map((dk) => (
                  <option key={dk} value={dk}>
                    {`${dk} ${t("engineering.dnp3.minutes")}`}
                  </option>
                ))}
              </select>
              <small className="dnp3-help">{t("engineering.dnp3.communicationGraceHelp")}</small>
            </label>
            {esikSn !== null ? (
              <label className="dnp3-field">
                <span className="dnp3-label">{t("engineering.dnp3.commLossThreshold")}</span>
                {/* Hesaplanan deger, elle girilmez. Girdiler eksikken HIC
                    gosterilmez ki uydurma bir esik okunmasin. */}
                <input
                  value={`${esikSn / 60} ${t("engineering.dnp3.minutes")}`}
                  disabled
                  readOnly
                />
                <small className="dnp3-help">{t("engineering.dnp3.commLossThresholdHelp")}</small>
              </label>
            ) : (
              // Dial-In yokken eski (v1.13 ve oncesi) kayitlarin ham esigi hala
              // duzenlenebilir kalir; Dial-In secilir secilmez bu alan yerini
              // turetilmis esige birakir.
              <label className="dnp3-field dnp3-field-with-unit">
                <span className="dnp3-label">{t("engineering.dnp3.smartMaxSilence")}</span>
                <span className="dnp3-input-unit">
                  <input
                    type="number"
                    min={SMART_MAX_SILENCE_MIN_SEC}
                    max={SMART_MAX_SILENCE_MAX_SEC}
                    value={v.smart_max_silence_sec ?? ""}
                    placeholder={t("engineering.dnp3.smartMaxSilenceAuto")}
                    onChange={(e) =>
                      set({
                        smart_max_silence_sec:
                          e.target.value.trim() === "" ? null : Number(e.target.value)
                      })
                    }
                  />
                  <span className="dnp3-unit">{t("engineering.dnp3.seconds")}</span>
                </span>
                <small className="dnp3-help">{t("engineering.dnp3.smartMaxSilenceHelp")}</small>
              </label>
            )}
          </>
        ) : null}
        <BoolSelect
          id="dnp3-unsol"
          label={t("engineering.dnp3.unsolicited")}
          value={v.unsolicited_reporting}
          onChange={(b) => set({ unsolicited_reporting: b })}
        />
        <BoolSelect
          id="dnp3-unsol-start"
          label={t("engineering.dnp3.unsolicitedStartup")}
          value={v.unsolicited_on_startup}
          onChange={(b) => set({ unsolicited_on_startup: b })}
        />
        <label className="dnp3-field">
          <span className="dnp3-label">
            {t("engineering.dnp3.unsolicitedClassMask")} <Req />
          </span>
          <input
            type="number"
            min={0}
            max={255}
            value={v.unsolicited_class_mask_id}
            onChange={(e) => set({ unsolicited_class_mask_id: Number(e.target.value) || 0 })}
          />
        </label>
        <label className="dnp3-field dnp3-field-with-unit">
          <span className="dnp3-label">{t("engineering.dnp3.linkStatus")}</span>
          <span className="dnp3-input-unit">
            <input
              type="number"
              min={0}
              value={v.link_status_period_min}
              onChange={(e) => set({ link_status_period_min: Number(e.target.value) || 0 })}
            />
            <span className="dnp3-unit">{t("engineering.dnp3.minutes")}</span>
          </span>
        </label>
        <BoolSelect
          id="dnp3-self-addr"
          label={t("engineering.dnp3.selfAddress")}
          value={v.enable_self_address}
          onChange={(b) => set({ enable_self_address: b })}
        />
        <BoolSelect
          id="dnp3-val-src"
          label={t("engineering.dnp3.validateSrc")}
          value={v.validate_source_address}
          onChange={(b) => set({ validate_source_address: b })}
        />
        <label className="dnp3-field dnp3-field-with-unit">
          <span className="dnp3-label">{t("engineering.dnp3.sessionTimeout")}</span>
          <span className="dnp3-input-unit">
            <input
              type="number"
              min={1}
              max={86400}
              value={v.session_timeout_listening_sec}
              onChange={(e) => set({ session_timeout_listening_sec: Number(e.target.value) || 1 })}
            />
            <span className="dnp3-unit">{t("engineering.dnp3.seconds")}</span>
          </span>
        </label>
        <label className="dnp3-field dnp3-field-with-unit">
          <span className="dnp3-label">{t("engineering.dnp3.socketTimeout")}</span>
          <span className="dnp3-input-unit">
            <input
              type="number"
              min={1}
              max={86400}
              value={v.socket_listening_timeout_sec}
              onChange={(e) => set({ socket_listening_timeout_sec: Number(e.target.value) || 1 })}
            />
            <span className="dnp3-unit">{t("engineering.dnp3.seconds")}</span>
          </span>
        </label>
      </div>
      <h5 className="dnp3-settings-title dnp3-settings-title--advanced">
        {t("engineering.dnp3.advanced")}
      </h5>
      <div className="dnp3-settings-grid">
        <label className="dnp3-field dnp3-field-with-unit">
          <span className="dnp3-label">{t("engineering.dnp3.smartListenReconnectMax")}</span>
          <span className="dnp3-input-unit">
            <input
              type="number"
              min={SMART_LISTEN_RECONNECT_MIN_SEC}
              max={SMART_LISTEN_RECONNECT_MAX_SEC}
              value={v.smart_listen_reconnect_max_sec ?? ""}
              placeholder={t("engineering.dnp3.smartListenReconnectMaxAuto")}
              onChange={(e) =>
                set({
                  smart_listen_reconnect_max_sec:
                    e.target.value.trim() === "" ? null : Number(e.target.value)
                })
              }
            />
            <span className="dnp3-unit">{t("engineering.dnp3.seconds")}</span>
          </span>
          <small className="dnp3-help">{t("engineering.dnp3.smartListenReconnectMaxHelp")}</small>
        </label>
      </div>
    </div>
  );
}
