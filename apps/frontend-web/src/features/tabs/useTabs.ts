/**
 * useTabs — sekme state yonetimi hook'u.
 *
 * Acik sekmeler + aktif sekme; localStorage'dan yuklenir, degisince kaydedilir.
 * Rol degisince (login/logout) gorunmez sekmeler elenir. App.tsx aktif route'tan
 * pageMode/engineeringPage'i turetir.
 */

import { useCallback, useEffect, useMemo, useState } from "react";

import type { UserRole } from "../../shared/types";
import {
  HOME_KEY,
  HOME_ROUTE,
  isClosable,
  loadTabs,
  makeTab,
  routeKey,
  saveTabs,
  visibleTabs,
  type Tab,
  type TabRoute,
} from "./tabModel";

export type UseTabs = {
  tabs: Tab[];
  activeKey: string;
  activeRoute: TabRoute;
  openTab: (route: TabRoute) => void;
  closeTab: (key: string) => void;
  /** Verilen sekme haric kapatilabilir tum sekmeleri kapat (home hep kalir). */
  closeOthers: (key: string) => void;
  /** Verilen sekmenin SAGINDAKI kapatilabilir sekmeleri kapat. */
  closeToRight: (key: string) => void;
  activateTab: (key: string) => void;
  reorderTabs: (fromKey: string, toKey: string) => void;
};

export function useTabs(role: UserRole): UseTabs {
  const [tabs, setTabs] = useState<Tab[]>(() => {
    const loaded = loadTabs();
    return visibleTabs(loaded.tabs, role);
  });
  const [activeKey, setActiveKey] = useState<string>(() => {
    const loaded = loadTabs();
    const visible = visibleTabs(loaded.tabs, role);
    return visible.some((t) => t.key === loaded.activeKey)
      ? loaded.activeKey
      : HOME_KEY;
  });

  // Rol degisince (yeni login) gorunmez sekmeleri ele. Aktif sekme elenmisse
  // home'a dus. Tek setTabs functional update icinde hem filtrele hem aktif
  // sekmeyi duzelt — stale closure olmasin.
  useEffect(() => {
    setTabs((prev) => {
      const next = visibleTabs(prev, role);
      if (next.length !== prev.length) {
        setActiveKey((prevActive) =>
          next.some((t) => t.key === prevActive) ? prevActive : HOME_KEY
        );
        return next;
      }
      return prev;
    });
  }, [role]);

  // Persist.
  useEffect(() => {
    saveTabs(tabs, activeKey);
  }, [tabs, activeKey]);

  const openTab = useCallback((route: TabRoute) => {
    const key = routeKey(route);
    setTabs((prev) =>
      prev.some((t) => t.key === key) ? prev : [...prev, makeTab(route)]
    );
    setActiveKey(key);
  }, []);

  const activateTab = useCallback((key: string) => {
    setActiveKey(key);
  }, []);

  const closeTab = useCallback((key: string) => {
    if (key === HOME_KEY) return; // home pinned
    setTabs((prev) => {
      const idx = prev.findIndex((t) => t.key === key);
      if (idx === -1) return prev;
      const next = prev.filter((t) => t.key !== key);
      // Aktif sekme kapaniyorsa komsuya gec (once sagdaki, yoksa soldaki).
      setActiveKey((curActive) => {
        if (curActive !== key) return curActive;
        const neighbor = prev[idx + 1] ?? prev[idx - 1];
        return neighbor ? neighbor.key : HOME_KEY;
      });
      return next.length > 0 ? next : [makeTab(HOME_ROUTE)];
    });
  }, []);

  // "Digerlerini kapat" — verilen sekme + kapatilamayan (home) kalir. Aktif
  // sekme kapananlar arasindaysa korunan sekmeye gec.
  const closeOthers = useCallback((key: string) => {
    setTabs((prev) => {
      const next = prev.filter((t) => t.key === key || !isClosable(t.route));
      if (next.length === prev.length) return prev;
      setActiveKey((curActive) =>
        next.some((t) => t.key === curActive) ? curActive : key
      );
      return next;
    });
  }, []);

  // "Sagdakileri kapat" — verilen sekmeden sonraki kapatilabilir sekmeler gider.
  const closeToRight = useCallback((key: string) => {
    setTabs((prev) => {
      const idx = prev.findIndex((t) => t.key === key);
      if (idx === -1) return prev;
      const next = prev.filter(
        (t, i) => i <= idx || !isClosable(t.route)
      );
      if (next.length === prev.length) return prev;
      setActiveKey((curActive) =>
        next.some((t) => t.key === curActive) ? curActive : key
      );
      return next;
    });
  }, []);

  const reorderTabs = useCallback((fromKey: string, toKey: string) => {
    if (fromKey === toKey) return;
    setTabs((prev) => {
      const fromIdx = prev.findIndex((t) => t.key === fromKey);
      const toIdx = prev.findIndex((t) => t.key === toKey);
      if (fromIdx === -1 || toIdx === -1) return prev;
      const next = [...prev];
      const [moved] = next.splice(fromIdx, 1);
      next.splice(toIdx, 0, moved);
      return next;
    });
  }, []);

  const activeRoute = useMemo<TabRoute>(() => {
    const found = tabs.find((t) => t.key === activeKey);
    return found ? found.route : HOME_ROUTE;
  }, [tabs, activeKey]);

  return {
    tabs,
    activeKey,
    activeRoute,
    openTab,
    closeTab,
    closeOthers,
    closeToRight,
    activateTab,
    reorderTabs,
  };
}
