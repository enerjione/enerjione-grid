import type { Dnp3ExtendedSettings } from "../../shared/types";

type Props = {
  value: Dnp3ExtendedSettings;
  onChange: (patch: Partial<Dnp3ExtendedSettings>) => void;
};

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

export function Dnp3SettingsForm({ value, onChange }: Props) {
  const v = value;
  const set = onChange;

  return (
    <div className="dnp3-settings-form">
      <h5 className="dnp3-settings-title">DNP3 oturum ayarları</h5>
      <div className="dnp3-settings-grid">
        <label className="dnp3-field">
          <span className="dnp3-label">
            Master IP adresi <Req />
          </span>
          <input
            value={v.master_ip_address}
            onChange={(e) => set({ master_ip_address: e.target.value })}
            placeholder="örn. ar01.ihost.zone"
          />
        </label>
        <label className="dnp3-field">
          <span className="dnp3-label">
            Master IP portu <Req />
          </span>
          <input
            type="number"
            min={1}
            max={65535}
            value={v.master_ip_port}
            onChange={(e) => set({ master_ip_port: Number(e.target.value) || 1 })}
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
