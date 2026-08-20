import { useEffect } from "react";
import { useTranslation } from "react-i18next";

import type { Dnp3ExtendedSettings, SessionPolicy } from "../../shared/types";
import {
  SMART_MAX_SILENCE_MAX_SEC,
  SMART_MAX_SILENCE_MIN_SEC,
  sessionPolicyForEndpoint
} from "../../shared/types";

type Props = {
  value: Dnp3ExtendedSettings;
  onChange: (patch: Partial<Dnp3ExtendedSettings>) => void;
  /** Diger cihazlarda kullanilan master_ip_port'lar — initiating modunda
   *  otomatik atama bunlari hariç tutar. */
  usedMasterPorts?: number[];
  hideConnectionFields?: boolean;
};

const INITIATING_PORT_RANGE_START = 20100;
const INITIATING_PORT_RANGE_END = 20700;

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

export function Dnp3SettingsForm({ value, onChange, usedMasterPorts = [], hideConnectionFields = false }: Props) {
  const { t } = useTranslation();
  const v = value;
  const set = onChange;

  const isInitiating = v.ip_endpoint_type === "initiating";
  const isSmart = v.session_policy === "smart";

  // Uc nokta tipi `listening`e cevrildiginde akilli oturum anlamini yitirir:
  // uykudaki cihaza gateway BAGLANAMAZ. Secimi burada geri aliyoruz ki
  // kullanici formda "Akilli" gorurken kaydet'e basip backend'den 422
  // yemesin. Backend ayrica derinlemesine savunma olarak reddeder.
  useEffect(() => {
    const gecerli = sessionPolicyForEndpoint(v.ip_endpoint_type, v.session_policy);
    if (gecerli !== v.session_policy) {
      set({ session_policy: gecerli });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [v.ip_endpoint_type, v.session_policy]);

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
            <label className="dnp3-field">
              <span className="dnp3-label">
                {t("engineering.dnp3.sessionPolicy")} <Req />
              </span>
              <select
                value={v.session_policy}
                onChange={(e) => set({ session_policy: e.target.value as SessionPolicy })}
              >
                <option value="continuous">{t("engineering.dnp3.sessionPolicyContinuous")}</option>
                {/* Akilli oturum yalnizca `initiating` ile secilebilir —
                    secenek listening modunda HIC render edilmez. */}
                {isInitiating ? (
                  <option value="smart">{t("engineering.dnp3.sessionPolicySmart")}</option>
                ) : null}
              </select>
              <small className="dnp3-help">
                {isSmart
                  ? t("engineering.dnp3.sessionPolicySmartHelp")
                  : t("engineering.dnp3.sessionPolicyContinuousHelp")}
              </small>
            </label>
            {isSmart ? (
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
            ) : null}
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
    </div>
  );
}
