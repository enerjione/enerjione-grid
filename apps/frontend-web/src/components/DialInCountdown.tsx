/**
 * "Sonraki Dial-In: 43 dk" satiri.
 *
 * DAKIKA — SANIYE DEGIL. Deger `shared/deviceRuntimeState.ts` icindeki
 * `dialInCountdown` ile cozulur (orada saniye URETILMEZ) ve saat
 * `useMinuteTick` ile DAKIKADA BIR ilerler. Saniyelik bir zamanlayici bu
 * ekranda gorunur hicbir sey degistirmez ama 200+ cihazlik listede saniyede
 * bir yeniden cizim demektir.
 *
 * `next_expected_report_epoch` OTORITEDIR: sure `last_valid_contact +
 * interval` ile yeniden hesaplanmaz. `null` ise ("hic olmadi") bu bilesen
 * HICBIR SEY cizmez — bos bir geri sayim, olmayan bir randevuyu varmis gibi
 * gosterir.
 */
import { useTranslation } from "react-i18next";

import { dialInCountdown } from "../shared/deviceRuntimeState";
import type { DeviceRuntimeHealthRecord, DeviceRuntimeState } from "../shared/deviceRuntimeState";
import { useMinuteTick } from "../shared/useMinuteTick";

type Props = {
  runtime?: DeviceRuntimeHealthRecord | null;
  state: DeviceRuntimeState;
  className?: string;
  /** `inline` (varsayilan): tek rozet — panel basligi gibi serbest yerlerde.
   *
   *  `row`: kenar cubugu/popup bilgi listesinin KENDI satirini cizer
   *  (ikon | etiket | deger). Neden bilesen satirin tamamini uretiyor:
   *  geri sayim yokken (`next_expected_report_epoch = null`) satirin HIC
   *  olmamasi gerekiyor. Cagiran tarafta `<li>` acip icini bu bilesene
   *  birakmak BOS bir satir birakiyordu; `:empty` ile gizlemek de etiket
   *  eklenince calismaz hale gelirdi. */
  variant?: "inline" | "row";
};

export function DialInCountdown({
  runtime,
  state,
  className = "",
  variant = "inline"
}: Props) {
  const { t } = useTranslation();
  // Hook KOSULSUZ cagrilir: erken return'den sonra hook kalmasi React'i
  // render sirasinda firlatir (bkz. tests/hookSirasi.test.ts).
  const now = useMinuteTick();
  const geri = dialInCountdown({ runtime, state, nowMs: now });

  if (geri.kind === "none") return null;

  const kayip = geri.kind === "lost";
  const gecikmis = geri.kind === "overdue";
  const ikon = kayip ? "sensors_off" : gecikmis ? "hourglass_top" : "schedule";
  const ipucu = kayip ? t("deviceRuntime.countdown.lostHint") : undefined;

  // SATIR BICIMINDE etiket ve deger AYRILIR. Tek parca metin
  // ("Sonraki Dial-In: 57 dk") bilgi listesinin dar etiket sutununa
  // dusuyor ve uc satira bolunuyordu.
  if (variant === "row") {
    const deger = kayip
      ? t("deviceRuntime.countdown.lostShort")
      : t("deviceRuntime.countdown.minutes", { minutes: geri.minutes });
    return (
      <li className="device-sidebar-info-row" title={ipucu}>
        <span className="material-symbols-outlined" aria-hidden="true">
          {ikon}
        </span>
        <span className="device-sidebar-info-label">
          {gecikmis ? t("deviceRuntime.countdown.labelOverdue") : t("deviceRuntime.countdown.label")}
        </span>
        <span
          className={`device-sidebar-info-value runtime-countdown-value${
            kayip ? " runtime-countdown-value--lost" : gecikmis ? " runtime-countdown-value--overdue" : ""
          }`}
        >
          {deger}
        </span>
      </li>
    );
  }

  return (
    <span
      className={`runtime-countdown ${kayip ? "runtime-countdown--lost" : ""} ${
        gecikmis ? "runtime-countdown--overdue" : ""
      } ${className}`.replace(/\s+/g, " ").trim()}
      title={ipucu}
    >
      <span className="material-symbols-outlined" aria-hidden="true">
        {ikon}
      </span>
      {kayip
        ? t("deviceRuntime.countdown.lost")
        : gecikmis
          ? t("deviceRuntime.countdown.overdue", { minutes: geri.minutes })
          : t("deviceRuntime.countdown.dueIn", { minutes: geri.minutes })}
    </span>
  );
}
