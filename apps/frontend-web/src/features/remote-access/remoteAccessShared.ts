/**
 * Uzaktan bakim izni — sayfa disinda da gereken kucuk parcalar.
 *
 * Sayfanin kendisi (RemoteAccessPage) lazy yuklenir; ust bardaki uyari rozeti
 * ise oturum acilir acilmaz gerektigi icin burasi ANA bundle'da durur ve
 * bilerek kucuktur (React + api disinda bagimlilik yok).
 *
 * NEDEN ROZET: bu ozelligin en buyuk riski "acik unutmak". Kullanici baska
 * sayfaya gecince erisimin acik oldugunu unutmamali; rozet her sayfada durur
 * ve tiklaninca bu sayfaya goturur.
 */
import { useCallback, useEffect, useRef, useState } from "react";

import { fetchRemoteAccessStatus, REMOTE_ACCESS_FORBIDDEN } from "../../shared/api";
import type { UserRole } from "../../shared/types";
import { usePolling } from "../../shared/usePolling";

type TFn = (key: string, opts?: Record<string, unknown>) => string;

/** Durumu GOREBILEN roller — backend `_VIEW_ROLES` ile birebir ayni olmali
 *  (apps/backend-api/app/api/remote_access.py). Operator bu ucta 403 alir,
 *  o yuzden onun tarayicisindan hic istek cikmasin. */
export const REMOTE_ACCESS_VIEW_ROLES: UserRole[] = [
  "installer",
  "engineer",
  "ops_manager"
];

export function canViewRemoteAccess(role: UserRole | undefined): boolean {
  return role !== undefined && REMOTE_ACCESS_VIEW_ROLES.includes(role);
}

/** Sayfa izin verdi/kapatti -> rozet 60 sn beklemeden tazelensin. */
export const REMOTE_ACCESS_CHANGED_EVENT = "e1:remote-access-changed";

export function notifyRemoteAccessChanged(): void {
  if (typeof window === "undefined") return;
  window.dispatchEvent(new CustomEvent(REMOTE_ACCESS_CHANGED_EVENT));
}

/**
 * Kalan sure metni: "3 sa 12 dk" / "12 dk 40 sn" / "48 sn".
 *
 * Kullanicinin guven karari verdigi TEK sayi budur; belirsiz birakma, iki
 * birime kadar in. Saniye alani son bir dakikada tek basina kalir ki
 * "bitiyor" hissi net olsun.
 */
export function formatRemaining(seconds: number | null, t: TFn): string {
  if (seconds === null || !Number.isFinite(seconds) || seconds < 0) return "—";
  const total = Math.floor(seconds);
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = total % 60;
  if (h > 0) return t("remoteAccess.fmt.hm", { h, m });
  if (m > 0) return t("remoteAccess.fmt.ms", { m, s });
  return t("remoteAccess.fmt.s", { s });
}

/** Sure secimi metni: "30 dakika" / "2 saat" / "1 sa 30 dk".
 *  `count` ile cagriliyor cunku Ingilizce'de tekil/cogul ayrimi var
 *  ("1 hour" / "2 hours"); i18next `_one`/`_other` ile cozuyor. */
export function formatDurationMinutes(minutes: number, t: TFn): string {
  const total = Math.max(0, Math.round(minutes));
  const h = Math.floor(total / 60);
  const m = total % 60;
  if (h > 0 && m > 0) return t("remoteAccess.fmt.durHm", { h, m });
  if (h > 0) return t("remoteAccess.fmt.durH", { count: h });
  return t("remoteAccess.fmt.durM", { count: total });
}

/** Yerel saat gosterimi ("14:30"). Kullanici bitis saatini kendi saatiyle
 *  dogrulayabilsin diye BILEREK yerel; kalan sure ise sunucudan gelir. */
export function formatClockShort(iso: string | null | undefined): string {
  if (!iso) return "—";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "—";
  return date.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" });
}

/** Tam tarih + saat. */
export function formatClockFull(iso: string | null | undefined): string {
  if (!iso) return "—";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "—";
  return date.toLocaleString(undefined, {
    day: "2-digit",
    month: "2-digit",
    hour: "2-digit",
    minute: "2-digit"
  });
}

export type RemoteAccessBadgeState = {
  active: boolean;
  remainingSeconds: number | null;
};

const IDLE: RemoteAccessBadgeState = { active: false, remainingSeconds: null };

/**
 * Ust bardaki uyari rozeti icin dusuk frekansli durum yoklamasi (60 sn).
 *
 * Appliance OLMAYAN kurulumda (VPS) ilk cevaptan sonra KALICI olarak susar —
 * her oturumda bosuna istek atmanin anlami yok. 403 (rol yetkisiz) de ayni
 * sekilde kalici susturur.
 */
export function useRemoteAccessBadge(
  token: string,
  role: UserRole | undefined
): RemoteAccessBadgeState {
  const [state, setState] = useState<RemoteAccessBadgeState>(IDLE);
  const stoppedRef = useRef(false);
  const enabled = Boolean(token) && canViewRemoteAccess(role);

  const poll = useCallback(async () => {
    if (!enabled || stoppedRef.current) return;
    try {
      const status = await fetchRemoteAccessStatus(token);
      if (!status.available) {
        // Bu kurulumda uzaktan bakim ajani yok — bir daha sorma.
        stoppedRef.current = true;
        setState(IDLE);
        return;
      }
      setState({
        active: Boolean(status.access.open),
        remainingSeconds: status.access.remaining_seconds ?? null
      });
    } catch (exc) {
      if (exc instanceof Error && exc.message === REMOTE_ACCESS_FORBIDDEN) {
        stoppedRef.current = true;
        setState(IDLE);
      }
      // Gecici ag/oturum hatasi: son bilinen durumu KORU. Rozetin
      // kaybolmasi "kapandi" gibi okunurdu ve bu yanlis olurdu.
    }
  }, [enabled, token]);

  usePolling({ enabled, intervalMs: 60000, fn: poll });

  // Sayfa izin verdiginde/kapattiginda 60 sn bekleme.
  useEffect(() => {
    if (!enabled) return undefined;
    const onChanged = () => void poll();
    window.addEventListener(REMOTE_ACCESS_CHANGED_EVENT, onChanged);
    return () => window.removeEventListener(REMOTE_ACCESS_CHANGED_EVENT, onChanged);
  }, [enabled, poll]);

  // Rozetteki sayac yerelde de ilerlesin; her yoklamada sunucuyla senkronlanir.
  useEffect(() => {
    if (!state.active) return undefined;
    const id = window.setInterval(() => {
      setState((prev) =>
        prev.remainingSeconds === null
          ? prev
          : { ...prev, remainingSeconds: Math.max(0, prev.remainingSeconds - 1) }
      );
    }, 1000);
    return () => window.clearInterval(id);
  }, [state.active]);

  // Yerel sayac sifira dustu: ekranda "acik" yazili kalmasin, sunucuya sor.
  useEffect(() => {
    if (state.active && state.remainingSeconds === 0) void poll();
  }, [state.active, state.remainingSeconds, poll]);

  return state;
}
