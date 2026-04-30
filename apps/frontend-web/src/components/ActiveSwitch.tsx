/** Aktif/Pasif anahtarı — alarm kuralı header'ında kullanılan iOS-tarzı slide
 *  switch'in tüm uygulamada paylaşılan formu. Pasif görünüm için aynı renk
 *  paletini kullanır (yeşil = aktif, gri = pasif). */
type Props = {
  checked: boolean;
  onChange: (next: boolean) => void;
  disabled?: boolean;
  /** İsteğe bağlı özel etiket; verilmezse "Aktif" / "Pasif" gösterilir. */
  activeLabel?: string;
  inactiveLabel?: string;
  /** Hover/long-press ipucu metni. */
  title?: string;
};

export function ActiveSwitch({
  checked,
  onChange,
  disabled,
  activeLabel = "Aktif",
  inactiveLabel = "Pasif",
  title
}: Props) {
  return (
    <label
      className={`rule-active-switch ${checked ? "rule-active-switch-on" : ""} ${
        disabled ? "rule-active-switch-disabled" : ""
      }`}
      title={title ?? (checked ? activeLabel : inactiveLabel)}
    >
      <input
        type="checkbox"
        checked={checked}
        onChange={(event) => onChange(event.target.checked)}
        disabled={disabled}
      />
      <span className="rule-active-switch-track">
        <span className="rule-active-switch-thumb" />
      </span>
      <span className="rule-active-switch-label">
        {checked ? activeLabel : inactiveLabel}
      </span>
    </label>
  );
}
