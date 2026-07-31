/**
 * useModalDialog — modal diyaloglar icin klavye davranisi.
 *
 * NEDEN: yedi modalin yalnizca biri (FaultDetailModal) ESC ile kapaniyordu ve
 * HICBIRINDE odak tuzagi yoktu. Tab'a basan kullanici modalin ARKASINDAKI
 * forma dusuyor, gormedigi alanlari degistirebiliyordu. Eldivenli/klavye ile
 * calisan saha kullanicisi icin pratik bir sorun.
 *
 * NE YAPAR:
 *   - ESC ile kapatir.
 *   - Tab / Shift+Tab odagi modal icinde dondurur (focus trap).
 *   - Acilista odagi modale tasir, kapanista onceki elemana geri verir.
 *
 * Dinleyici WINDOW'a degil modal KAPSAYICISINA baglanir: ic ice modalda
 * (ornegin modal uzerinde acilan onay diyalogu) ESC yalnizca ustteki
 * diyalogu kapatsin, ikisini birden degil.
 *
 * KULLANIM:
 *   const dialogRef = useModalDialog<HTMLDivElement>(onClose);
 *   <div className="settings-modal-backdrop">
 *     <div className="settings-modal" role="dialog" aria-modal="true" ref={dialogRef}>
 *
 * Gorsel etki yok: kapsayiciya programatik odak verilir, bu `:focus-visible`
 * tetiklemedigi icin ekranda odak halkasi cikmaz.
 */
import { useEffect, useRef, type RefObject } from "react";

/** Odaklanabilir eleman secicisi — disabled ve gizli olanlar haric. */
const FOCUSABLE =
  'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), ' +
  'textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

export function useModalDialog<T extends HTMLElement>(
  onClose: () => void
): RefObject<T> {
  // useRef<T>(null) -> RefObject<T>; JSX `ref` prop'unun bekledigi tip bu.
  const containerRef = useRef<T>(null);

  // onClose her render'da yeni closure olabilir; ref ile okuyoruz ki efekt
  // (ve dolayisiyla odak tasima) her render'da yeniden kurulmasin.
  const onCloseRef = useRef(onClose);
  onCloseRef.current = onClose;

  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;

    // Modal kapaninca odagi geri verecegimiz eleman.
    const previouslyFocused = document.activeElement as HTMLElement | null;

    // Odagi modale tasi. Kapsayici normalde odaklanamaz; gecici olarak
    // odaklanabilir yapiyoruz (icerideki ilk alani otomatik SECMIYORUZ —
    // kullanici bir input'a istemeden yazmaya baslamasin).
    if (!container.hasAttribute("tabindex")) container.setAttribute("tabindex", "-1");
    container.focus({ preventScroll: true });

    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.stopPropagation();
        onCloseRef.current();
        return;
      }
      if (event.key !== "Tab") return;

      const focusable = Array.from(
        container.querySelectorAll<HTMLElement>(FOCUSABLE)
      ).filter((el) => el.offsetParent !== null || el === document.activeElement);
      if (focusable.length === 0) {
        // Odaklanacak bir sey yoksa Tab modalin disina cikmasin.
        event.preventDefault();
        return;
      }

      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      const active = document.activeElement;

      if (event.shiftKey && (active === first || active === container)) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && active === last) {
        event.preventDefault();
        first.focus();
      }
    };

    container.addEventListener("keydown", handleKeyDown);
    return () => {
      container.removeEventListener("keydown", handleKeyDown);
      // Modal kapandi: odak tetikleyen butona geri donsun ki klavye
      // kullanicisi listenin basina savrulmasin.
      previouslyFocused?.focus?.({ preventScroll: true });
    };
  }, []);

  return containerRef;
}
