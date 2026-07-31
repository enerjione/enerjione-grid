/**
 * WifiActionConfirm — WiFi islemleri icin TEK onay diyalogu.
 *
 * Govde `consequencesOf()` ciktisidir; liste bossa cagiran taraf diyalogu HIC
 * acmaz. Gereksiz onay, onay korlugu uretir: her tiklamada "emin misiniz?"
 * goren kullanici gercekten tehlikeli olani da okumadan onaylar.
 *
 * `WifiConsequences` disari acilir cunku ayni liste baglanma modalinin
 * govdesinde de kullanilir — tek kaynak, tek metin.
 */
import { useTranslation } from "react-i18next";
import { AlertTriangle, CheckCircle2, Info, ShieldAlert } from "lucide-react";

import type { Consequence } from "./networkAccess";

/** Sonuc listesi — ton basina ikon; renk TEK BASINA bilgi tasimaz, her
 *  satirin yazili karsiligi vardir. */
export function WifiConsequences({ items }: { items: Consequence[] }) {
  const { t } = useTranslation();
  if (items.length === 0) return null;
  return (
    <ul className="wifi-consequences">
      {items.map((item) => (
        <li key={item.key} className={`is-${item.tone}`}>
          {item.tone === "bad" ? (
            <ShieldAlert size={15} strokeWidth={2.2} />
          ) : item.tone === "warn" ? (
            <AlertTriangle size={15} strokeWidth={2.2} />
          ) : (
            <CheckCircle2 size={15} strokeWidth={2.2} />
          )}
          <span>{t(item.key, item.vars)}</span>
        </li>
      ))}
    </ul>
  );
}

type Props = {
  /** Baslik anahtari — `network.wifi.confirm.*` */
  titleKey: string;
  items: Consequence[];
  /** Bu islem oturumumuzu koparacaksa onay dugmesi "Yine de devam et" olur. */
  risky: boolean;
  busy: boolean;
  onCancel: () => void;
  onConfirm: () => void;
};

export function WifiActionConfirm({
  titleKey,
  items,
  risky,
  busy,
  onCancel,
  onConfirm
}: Props) {
  const { t } = useTranslation();
  return (
    <div className="settings-modal-backdrop" onClick={() => !busy && onCancel()}>
      <div
        className="settings-modal net-confirm"
        role="dialog"
        aria-modal="true"
        onClick={(e) => e.stopPropagation()}
      >
        <h3>
          <AlertTriangle size={20} />
          {t(titleKey)}
        </h3>

        <p className="wifi-consequences-lead">
          <Info size={15} />
          {t("network.wifi.confirm.effects")}
        </p>
        <WifiConsequences items={items} />

        <div className="net-confirm-actions">
          <button type="button" className="secondary-btn" disabled={busy} onClick={onCancel}>
            {t("common.cancel")}
          </button>
          <button
            type="button"
            className={risky ? "danger-btn" : "primary-btn"}
            disabled={busy}
            onClick={onConfirm}
          >
            {busy
              ? t("network.wifi.confirm.working")
              : risky
                ? t("network.wifi.confirm.proceedRisky")
                : t("network.wifi.confirm.proceed")}
          </button>
        </div>
      </div>
    </div>
  );
}
