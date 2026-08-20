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
};

export function DialInCountdown({ runtime, state, className = "" }: Props) {
  const { t } = useTranslation();
  // Hook KOSULSUZ cagrilir: erken return'den sonra hook kalmasi React'i
  // render sirasinda firlatir (bkz. tests/hookSirasi.test.ts).
  const now = useMinuteTick();
  const geri = dialInCountdown({ runtime, state, nowMs: now });

  if (geri.kind === "none") return null;

  if (geri.kind === "lost") {
    return (
      <span
        className={`runtime-countdown runtime-countdown--lost ${className}`.trim()}
        title={t("deviceRuntime.countdown.lostHint")}
      >
        <span className="material-symbols-outlined" aria-hidden="true">
          sensors_off
        </span>
        {t("deviceRuntime.countdown.lost")}
      </span>
    );
  }

  const gecikmis = geri.kind === "overdue";
  return (
    <span
      className={`runtime-countdown ${gecikmis ? "runtime-countdown--overdue" : ""} ${className}`.trim()}
    >
      <span className="material-symbols-outlined" aria-hidden="true">
        {gecikmis ? "hourglass_top" : "schedule"}
      </span>
      {gecikmis
        ? t("deviceRuntime.countdown.overdue", { minutes: geri.minutes })
        : t("deviceRuntime.countdown.dueIn", { minutes: geri.minutes })}
    </span>
  );
}
