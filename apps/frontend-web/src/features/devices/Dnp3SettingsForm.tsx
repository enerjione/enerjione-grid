import { useEffect } from "react";

import type { Dnp3ExtendedSettings } from "../../shared/types";

type Props = {
  value: Dnp3ExtendedSettings;
  onChange: (patch: Partial<Dnp3ExtendedSettings>) => void;
  /** Diger cihazlarda kullanilan master_ip_port'lar — initiating modunda
   *  otomatik atama bunlari hariç tutar. */
  usedMasterPorts?: number[];
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
  return (
    <label className="dnp3-field" htmlFor={id}>
      <span className="dnp3-label">{label}</span>
      <select
        id={id}
        value={value ? "1" : "0"}
        onChange={(e) => onChange(e.target.value === "1")}
      >
        <option value="1">Açık</option>
        <option value="0">Kapalı</option>
      </select>
    </label>
  );
}

export function Dnp3SettingsForm({ value, onChange, usedMasterPorts = [] }: Props) {
  const v = value;
  const set = onChange;

  const isInitiating = v.ip_endpoint_type === "initiating";

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
      <h5 className="dnp3-settings-title">DNP3 oturum ayarları</h5>
      <div className="dnp3-settings-grid">
        <label className="dnp3-field">
          <span className="dnp3-label">
            Bağlantı modu <Req />
          </span>
          <select
            value={v.ip_endpoint_type}
            onChange={(e) => set({ ip_endpoint_type: e.target.value as "listening" | "initiating" })}
          >
            <option value="listening">Listening (cihaz dinler, gateway bağlanır)</option>
            <option value="initiating">Initiating (cihaz gateway'e bağlanır — 4G/SIM)</option>
          </select>
        </label>
        <label className="dnp3-field">
          <span className="dnp3-label">
            Master IP adresi <Req />
          </span>
          <input
            value={v.master_ip_address}
            onChange={(e) => set({ master_ip_address: e.target.value })}
            placeholder={isInitiating ? "Çatı yazılım sunucu IP" : "0.0.0.0"}
          />
        </label>
        <label className="dnp3-field">
          <span className="dnp3-label">
            Master IP portu {isInitiating ? null : <Req />}
          </span>
          <input
            type="number"
            min={1}
            max={65535}
            value={v.master_ip_port}
            onChange={(e) => set({ master_ip_port: Number(e.target.value) || 1 })}
            disabled={isInitiating}
            title={isInitiating ? "Initiating modunda port otomatik atanır" : undefined}
          />
        </label>
        <label className="dnp3-field">
          <span className="dnp3-label">
            Master adres <Req />
          </span>
          <input
            type="number"
            min={0}
            max={65535}
            value={v.master_address}
            onChange={(e) => set({ master_address: Number(e.target.value) || 0 })}
          />
        </label>
        <BoolSelect
          id="dnp3-unsol"
          label="İstenmeyen raporlama (Unsolicited)"
          value={v.unsolicited_reporting}
          onChange={(b) => set({ unsolicited_reporting: b })}
        />
        <BoolSelect
          id="dnp3-unsol-start"
          label="Başlangıçta istenmeyen raporlama"
          value={v.unsolicited_on_startup}
          onChange={(b) => set({ unsolicited_on_startup: b })}
        />
        <label className="dnp3-field">
          <span className="dnp3-label">
            İstenmeyen sınıf maskesi ID <Req />
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
          <span className="dnp3-label">DNP3 bağlantı durumu periyodu</span>
          <span className="dnp3-input-unit">
            <input
              type="number"
              min={0}
              value={v.link_status_period_min}
              onChange={(e) => set({ link_status_period_min: Number(e.target.value) || 0 })}
            />
            <span className="dnp3-unit">dk</span>
          </span>
        </label>
        <BoolSelect
          id="dnp3-self-addr"
          label="Self adresi etkinleştir"
          value={v.enable_self_address}
          onChange={(b) => set({ enable_self_address: b })}
        />
        <BoolSelect
          id="dnp3-val-src"
          label="Kaynak adresini doğrula"
          value={v.validate_source_address}
          onChange={(b) => set({ validate_source_address: b })}
        />
        <label className="dnp3-field dnp3-field-with-unit">
          <span className="dnp3-label">Oturum zaman aşımı (dinleyen uç)</span>
          <span className="dnp3-input-unit">
            <input
              type="number"
              min={1}
              max={86400}
              value={v.session_timeout_listening_sec}
              onChange={(e) => set({ session_timeout_listening_sec: Number(e.target.value) || 1 })}
            />
            <span className="dnp3-unit">s</span>
          </span>
        </label>
        <label className="dnp3-field dnp3-field-with-unit">
          <span className="dnp3-label">Soket dinleme zaman aşımı</span>
          <span className="dnp3-input-unit">
            <input
              type="number"
              min={1}
              max={86400}
              value={v.socket_listening_timeout_sec}
              onChange={(e) => set({ socket_listening_timeout_sec: Number(e.target.value) || 1 })}
            />
            <span className="dnp3-unit">s</span>
          </span>
        </label>
      </div>
    </div>
  );
}
