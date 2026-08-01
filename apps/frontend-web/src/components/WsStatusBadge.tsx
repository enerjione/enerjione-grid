/**
 * Canli veri durum rozeti — header'da ve Sistem Durumu sayfasinda durur.
 *
 * Soket durumunu DEGIL, VERI AKISINI gosterir; karar mantigi ve gerekcesi
 * `shared/wsDataStatus.ts` icinde (orasi saf, testleri `tests/` altinda).
 * Bu dosya yalnizca durumu renge/metne cevirir.
 *
 *   live       -> yesil   "Canli"
 *   waiting    -> sari    "Veri bekleniyor"
 *   stale      -> sari    "Veri yok (N dk)"
 *   connecting -> sari    "Baglaniyor"
 *   error      -> kirmizi "Hata"
 *   offline    -> gri     "Kopuk"
 */

import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";

import type { WsConnectionState } from "../shared/useLiveValuesSocket";
import {
  DEFAULT_STALE_AFTER_MS,
  sureMetni,
  veriYasi,
  wsDataStatus,
  type WsDataStatus,
} from "../shared/wsDataStatus";

/** Rozetin kendini yeniden degerlendirme araligi — veri gelmese bile bayatlik
 *  gorunur olsun diye. 5 sn: rozet en gec 5 sn gecikmeyle sariya doner. */
const TICK_MS = 5_000;

const RENK: Record<WsDataStatus, { dot: string; text: string }> = {
  live: { dot: "#10b981", text: "#065f46" },
  waiting: { dot: "#f59e0b", text: "#92400e" },
  stale: { dot: "#f59e0b", text: "#92400e" },
  connecting: { dot: "#f59e0b", text: "#92400e" },
  error: { dot: "#dc2626", text: "#991b1b" },
  offline: { dot: "#9ca3af", text: "#4b5563" },
};

interface Props {
  state: WsConnectionState;
  /** Son TELEMETRI mesajinin zamani (ms). `null` = hic veri gelmedi. */
  lastDataAt?: number | null;
  staleAfterMs?: number;
}

export function WsStatusBadge({ state, lastDataAt, staleAfterMs = DEFAULT_STALE_AFTER_MS }: Props) {
  const { t } = useTranslation();

  // Veri gelmese de bayatlik ilerlesin diye periyodik yeniden degerlendirme.
  // Soket acik degilken zamana bagli bir sey gostermiyoruz; timer'a gerek yok.
  const [simdi, setSimdi] = useState(() => Date.now());
  useEffect(() => {
    if (state !== "open") return undefined;
    setSimdi(Date.now());
    const id = window.setInterval(() => setSimdi(Date.now()), TICK_MS);
    return () => window.clearInterval(id);
  }, [state]);

  const durum = wsDataStatus(state, lastDataAt, simdi, staleAfterMs);
  const renk = RENK[durum];

  let etiket = t(`ws.${durum}`);
  let ipucu = t(`ws.${durum}Title`);
  if (durum === "stale") {
    const sure = sureMetni(veriYasi(lastDataAt, simdi) ?? 0);
    etiket = `${etiket} (${sure})`;
    ipucu = `${ipucu} ${sure}`;
  }

  return (
    <div
      role="status"
      aria-live="polite"
      aria-label={`${t("ws.statusLabel")}: ${etiket}`}
      title={ipucu}
      style={{
        display: "inline-flex",
        alignItems: "center",
        gap: 6,
        padding: "4px 10px",
        background: "rgba(0,0,0,0.04)",
        borderRadius: 999,
        fontSize: 12,
        fontWeight: 500,
        color: renk.text,
        lineHeight: 1,
        whiteSpace: "nowrap",
      }}
    >
      <span
        style={{
          width: 8,
          height: 8,
          borderRadius: "50%",
          background: renk.dot,
          // Pulse animasyonu: "connecting" durumu icin ufak nabiz.
          animation: durum === "connecting" ? "ws-pulse 1.4s ease-in-out infinite" : undefined,
        }}
        aria-hidden="true"
      />
      <span>{etiket}</span>
      <style>{`@keyframes ws-pulse { 0%,100% { opacity: 1 } 50% { opacity: 0.3 } }`}</style>
    </div>
  );
}
