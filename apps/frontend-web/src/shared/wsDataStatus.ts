/**
 * Canli veri durumu — SOKET durumu ile VERI AKISI ayrimi.
 *
 * NEDEN AYRI DOSYA
 * ----------------
 * Bu karar saf bir fonksiyon; React'tan bagimsiz oldugu icin GERCEKTEN
 * calistirilarak test edilebilir (bkz. `tests/`). Rozet bileseni yalnizca
 * buradan gelen durumu renge/metne cevirir.
 *
 * NEDEN VAR
 * ---------
 * Sunucu 30 saniyede bir `ping` atiyor. Soket, gateway tamamen sussa bile
 * ACIK kalir. Rozet yalnizca soket durumuna baksaydi -- eski hali oyleydi --
 * gateway'i olmus bir sahada operatore yesil "Canli" gosterirdi. Bir ariza
 * izleme urununde en agir hata sinifi budur: sistem BILMEDIGINI "sorun yok"
 * diye gosterir.
 */

import type { WsConnectionState } from "./useLiveValuesSocket";

export type WsDataStatus =
  /** Soket acik VE telemetri taze. Yesil gostermeye hakkimiz olan tek durum. */
  | "live"
  /** Soket acik ama baglantidan beri hic telemetri gelmedi. */
  | "waiting"
  /** Soket acik ama son telemetrinin uzerinden esik kadar sure gecti. */
  | "stale"
  | "connecting"
  | "error"
  /** Soket kapali. */
  | "offline";

/**
 * Varsayilan bayatlik esigi (ms).
 *
 * Cihazlar varsayilan `poll_interval_sec = 2` ile taranir ve telemetri
 * deadband'siz yayinlanir; saglikli sistemde veri surekli akar. 30 sn =
 * ~15 tarama periyodu, yani gecici bir gecikme rozeti dusurmez.
 */
export const DEFAULT_STALE_AFTER_MS = 30_000;

/**
 * @param lastDataAt Son TELEMETRI mesajinin zamani (ms). `null` = hic gelmedi.
 *                   ping/hello BILEREK sayilmaz — sayilsaydi gateway sussa
 *                   bile rozet yesil kalirdi.
 * @param simdi      Simdiki zaman (ms). Disaridan gecilir: saf kalsin, test
 *                   edilebilsin.
 */
export function wsDataStatus(
  state: WsConnectionState,
  lastDataAt: number | null | undefined,
  simdi: number,
  staleAfterMs: number = DEFAULT_STALE_AFTER_MS
): WsDataStatus {
  if (state === "connecting") return "connecting";
  if (state === "error") return "error";
  if (state !== "open") return "offline";

  // Soket ACIK. Buradan sonrasi VERIYE bakar, sokete degil.
  if (lastDataAt == null) return "waiting";
  if (simdi - lastDataAt > staleAfterMs) return "stale";
  return "live";
}

/** Son veriden bu yana gecen sure (ms); bilinmiyorsa `null`. */
export function veriYasi(lastDataAt: number | null | undefined, simdi: number): number | null {
  if (lastDataAt == null) return null;
  return Math.max(0, simdi - lastDataAt);
}

/** "45 sn" / "3 dk" / "2 sa" — rozet dar oldugu icin tek birim yeter. */
export function sureMetni(ms: number): string {
  const sn = Math.max(0, Math.round(ms / 1000));
  if (sn < 60) return `${sn} sn`;
  const dk = Math.round(sn / 60);
  if (dk < 60) return `${dk} dk`;
  return `${Math.round(dk / 60)} sa`;
}
