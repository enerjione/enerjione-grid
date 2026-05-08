import { useTranslation } from "react-i18next";

type Props = {
  checked: boolean;
  onChange: (next: boolean) => void;
  disabled?: boolean;
  activeLabel?: string;
  inactiveLabel?: string;
  title?: string;
};

export function ActiveSwitch({
  checked,
  onChange,
  disabled,
  activeLabel,
  inactiveLabel,
  title
}: Props) {
  const { t } = useTranslation();
  const onLabel = activeLabel ?? t("common.active");
  const offLabel = inactiveLabel ?? t("common.inactive");
  return (
    <label
      className={`rule-active-switch ${checked ? "rule-active-switch-on" : ""} ${
        disabled ? "rule-active-switch-disabled" : ""
      }`}
      title={title ?? (checked ? onLabel : offLabel)}
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
        {checked ? onLabel : offLabel}
      </span>
    </label>
  );
}
