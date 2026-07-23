/**
 * TabBar — Chrome tarzi sekme seridi. Header ile icerik arasinda global durur.
 *
 * - Tikla: sekmeye gec. Orta-tik veya x: kapat (home haric).
 * - Surukle-birak: native HTML5 DnD ile sira degistir (yeni bagimlilik yok).
 * - Klavye: ok tuslari ile gez, Enter/Space ile aktive, Delete ile kapat.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";

import {
  HOME_KEY,
  isClosable,
  tabIcon,
  tabLabel,
  type Tab,
} from "./tabModel";

type Props = {
  tabs: Tab[];
  activeKey: string;
  onActivate: (key: string) => void;
  onClose: (key: string) => void;
  onCloseOthers: (key: string) => void;
  onCloseToRight: (key: string) => void;
  onReorder: (fromKey: string, toKey: string) => void;
  /** device-detail sekmeleri icin cihaz kodu/adi cozumu. */
  deviceLookup?: (id: number) => { code?: string; name?: string } | undefined;
};

type CtxMenu = { key: string; x: number; y: number };

export function TabBar({
  tabs,
  activeKey,
  onActivate,
  onClose,
  onCloseOthers,
  onCloseToRight,
  onReorder,
  deviceLookup,
}: Props) {
  const { t } = useTranslation();
  const [dragKey, setDragKey] = useState<string | null>(null);
  const [dragOverKey, setDragOverKey] = useState<string | null>(null);
  const [ctxMenu, setCtxMenu] = useState<CtxMenu | null>(null);
  const [canScrollLeft, setCanScrollLeft] = useState(false);
  const [canScrollRight, setCanScrollRight] = useState(false);
  const listRef = useRef<HTMLDivElement | null>(null);

  const updateScrollState = useCallback(() => {
    const el = listRef.current;
    if (!el) return;
    setCanScrollLeft(el.scrollLeft > 1);
    setCanScrollRight(el.scrollLeft + el.clientWidth < el.scrollWidth - 1);
  }, []);

  useEffect(() => {
    const el = listRef.current;
    if (!el) return;
    updateScrollState();
    const observer = new ResizeObserver(updateScrollState);
    observer.observe(el);
    el.addEventListener("scroll", updateScrollState, { passive: true });
    return () => {
      observer.disconnect();
      el.removeEventListener("scroll", updateScrollState);
    };
  }, [tabs, updateScrollState]);

  useEffect(() => {
    const active = listRef.current?.querySelector<HTMLElement>(
      `[data-tab-key="${cssEscape(activeKey)}"]`
    );
    active?.scrollIntoView({ behavior: "smooth", block: "nearest", inline: "nearest" });
  }, [activeKey]);

  const scrollTabs = (direction: -1 | 1) => {
    const el = listRef.current;
    if (!el) return;
    el.scrollBy({ left: direction * Math.max(240, el.clientWidth * 0.65), behavior: "smooth" });
  };

  // Sag-tik menusunu disari tiklama / Esc / scroll ile kapat.
  useEffect(() => {
    if (!ctxMenu) return;
    const close = () => setCtxMenu(null);
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setCtxMenu(null);
    };
    window.addEventListener("click", close);
    window.addEventListener("resize", close);
    window.addEventListener("keydown", onKey);
    return () => {
      window.removeEventListener("click", close);
      window.removeEventListener("resize", close);
      window.removeEventListener("keydown", onKey);
    };
  }, [ctxMenu]);

  const handleKeyDown = (event: React.KeyboardEvent, key: string) => {
    const idx = tabs.findIndex((tab) => tab.key === key);
    if (event.key === "ArrowRight" || event.key === "ArrowLeft") {
      event.preventDefault();
      const dir = event.key === "ArrowRight" ? 1 : -1;
      const next = tabs[idx + dir];
      if (next) {
        onActivate(next.key);
        // Odagi da tasi.
        const el = listRef.current?.querySelector<HTMLElement>(
          `[data-tab-key="${cssEscape(next.key)}"]`
        );
        el?.focus();
      }
    } else if (event.key === "Delete") {
      event.preventDefault();
      if (isClosable(tabs[idx].route)) onClose(key);
    }
  };

  return (
    <div className="tab-bar-shell">
      <button
        type="button"
        className="tab-bar-scroll tab-bar-scroll--left"
        aria-label={t("common.previous")}
        disabled={!canScrollLeft}
        onClick={() => scrollTabs(-1)}
      >
        <span className="material-symbols-outlined" aria-hidden="true">chevron_left</span>
      </button>
      <div className="tab-bar" role="tablist" ref={listRef}>
        {tabs.map((tab) => {
        const active = tab.key === activeKey;
        const closable = isClosable(tab.route);
        const isDragOver = dragOverKey === tab.key && dragKey !== tab.key;
        return (
          <div
            key={tab.key}
            data-tab-key={tab.key}
            role="tab"
            tabIndex={active ? 0 : -1}
            aria-selected={active}
            className={[
              "tab-bar-item",
              active ? "active" : "",
              dragKey === tab.key ? "dragging" : "",
              isDragOver ? "drag-over" : "",
            ]
              .filter(Boolean)
              .join(" ")}
            title={tabLabel(tab.route, t, deviceLookup)}
            draggable
            onClick={() => onActivate(tab.key)}
            onContextMenu={(e) => {
              e.preventDefault();
              setCtxMenu({ key: tab.key, x: e.clientX, y: e.clientY });
            }}
            onKeyDown={(e) => handleKeyDown(e, tab.key)}
            onAuxClick={(e) => {
              // Orta tik (button===1) ile kapat.
              if (e.button === 1 && closable) {
                e.preventDefault();
                onClose(tab.key);
              }
            }}
            onDragStart={(e) => {
              setDragKey(tab.key);
              e.dataTransfer.effectAllowed = "move";
              // Firefox surukleme icin veri sart.
              e.dataTransfer.setData("text/plain", tab.key);
            }}
            onDragOver={(e) => {
              e.preventDefault();
              e.dataTransfer.dropEffect = "move";
              if (dragOverKey !== tab.key) setDragOverKey(tab.key);
            }}
            onDrop={(e) => {
              e.preventDefault();
              const from = dragKey ?? e.dataTransfer.getData("text/plain");
              if (from && from !== tab.key) onReorder(from, tab.key);
              setDragKey(null);
              setDragOverKey(null);
            }}
            onDragEnd={() => {
              setDragKey(null);
              setDragOverKey(null);
            }}
          >
            <span className="material-symbols-outlined tab-bar-item-icon" aria-hidden="true">
              {tabIcon(tab.route)}
            </span>
            <span className="tab-bar-item-label">
              {tabLabel(tab.route, t, deviceLookup)}
            </span>
            {closable ? (
              <button
                type="button"
                className="tab-bar-item-close"
                aria-label={t("tabs.close")}
                title={t("tabs.close")}
                onClick={(e) => {
                  e.stopPropagation();
                  onClose(tab.key);
                }}
                // Kapat butonundan surukleme baslamasin.
                draggable={false}
                onMouseDown={(e) => e.stopPropagation()}
              >
                <span className="material-symbols-outlined" aria-hidden="true">
                  close
                </span>
              </button>
            ) : (
              // Home: kapat yerine sabit pin ikonu (kapatilamaz oldugu belli).
              tab.key === HOME_KEY ? (
                <span className="tab-bar-item-pin material-symbols-outlined" aria-hidden="true">
                  push_pin
                </span>
              ) : null
            )}
          </div>
        );
        })}
      </div>
      <button
        type="button"
        className="tab-bar-scroll tab-bar-scroll--right"
        aria-label={t("common.next")}
        disabled={!canScrollRight}
        onClick={() => scrollTabs(1)}
      >
        <span className="material-symbols-outlined" aria-hidden="true">chevron_right</span>
      </button>
      {ctxMenu ? (
        <TabContextMenu
          menu={ctxMenu}
          tabs={tabs}
          onClose={onClose}
          onCloseOthers={onCloseOthers}
          onCloseToRight={onCloseToRight}
          dismiss={() => setCtxMenu(null)}
          t={t}
        />
      ) : null}
    </div>
  );
}

/** Sekme sag-tik baglam menusu. position:fixed ile imlecin oldugu yerde acilir;
 *  scroll container tarafindan kirpilmaz. */
function TabContextMenu({
  menu,
  tabs,
  onClose,
  onCloseOthers,
  onCloseToRight,
  dismiss,
  t,
}: {
  menu: CtxMenu;
  tabs: Tab[];
  onClose: (key: string) => void;
  onCloseOthers: (key: string) => void;
  onCloseToRight: (key: string) => void;
  dismiss: () => void;
  t: (key: string) => string;
}) {
  const idx = tabs.findIndex((tab) => tab.key === menu.key);
  const self = tabs[idx];
  if (!self) return null;
  const selfClosable = isClosable(self.route);
  // Sagda kapatilabilir sekme var mi?
  const hasClosableRight = tabs
    .slice(idx + 1)
    .some((tab) => isClosable(tab.route));
  // Bu sekme haric kapatilabilir baska sekme var mi?
  const hasOtherClosable = tabs.some(
    (tab) => tab.key !== menu.key && isClosable(tab.route)
  );

  const run = (fn: () => void) => (e: React.MouseEvent) => {
    e.stopPropagation();
    fn();
    dismiss();
  };

  return (
    <div
      className="tab-ctx-menu"
      style={{ top: menu.y, left: menu.x }}
      role="menu"
      onClick={(e) => e.stopPropagation()}
    >
      <button
        type="button"
        role="menuitem"
        disabled={!selfClosable}
        onClick={run(() => onClose(menu.key))}
      >
        <span className="material-symbols-outlined" aria-hidden="true">close</span>
        {t("tabs.close")}
      </button>
      <button
        type="button"
        role="menuitem"
        disabled={!hasOtherClosable}
        onClick={run(() => onCloseOthers(menu.key))}
      >
        <span className="material-symbols-outlined" aria-hidden="true">
          close_fullscreen
        </span>
        {t("tabs.closeOthers")}
      </button>
      <button
        type="button"
        role="menuitem"
        disabled={!hasClosableRight}
        onClick={run(() => onCloseToRight(menu.key))}
      >
        <span className="material-symbols-outlined" aria-hidden="true">
          keyboard_tab
        </span>
        {t("tabs.closeToRight")}
      </button>
    </div>
  );
}

// CSS attribute selector'da kullanmak icin key'i guvenli hale getir. device:42
// gibi key'lerde ':' sorun cikarmasin. CSS.escape varsa onu kullan.
function cssEscape(value: string): string {
  if (typeof CSS !== "undefined" && typeof CSS.escape === "function") {
    return CSS.escape(value);
  }
  return value.replace(/["\\]/g, "\\$&");
}
