/** Proje ayarlari (logo + isimler) icin context.
 *
 * App.tsx'in tepesine sariliyor; cocuklar `useProjectSettings()` ile okur.
 * Login ekrani da uygulamadan once Provider'in ic state'ini cektigi icin
 * logoyu gorur. Bir admin Proje Ayarlari'nda kaydederse `refresh()` cagrilir
 * ve butun komponentler yeni logoyu otomatik yansitir.
 */
import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";

import { fetchProjectSettings } from "../shared/api";
import type { ProjectSettings } from "../shared/types";

type ContextShape = {
  settings: ProjectSettings;
  loading: boolean;
  refresh: () => Promise<void>;
  applyLocal: (next: ProjectSettings) => void;
};

const EMPTY: ProjectSettings = {};

const Ctx = createContext<ContextShape>({
  settings: EMPTY,
  loading: false,
  refresh: async () => {},
  applyLocal: () => {}
});

export function ProjectSettingsProvider({ children }: { children: ReactNode }) {
  const [settings, setSettings] = useState<ProjectSettings>(EMPTY);
  const [loading, setLoading] = useState(true);

  const refresh = useCallback(async () => {
    setLoading(true);
    try {
      const data = await fetchProjectSettings();
      setSettings(data);
    } catch {
      // Backend cevap vermezse logo bos kalir, fallback PNG'ler devreye girer.
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  // site_title degistiginde document.title'a uygula. Kaydedildiginde
  // anlik olarak sekme baslıgı yansır; sayfa yeniden yuklenmesine gerek yok.
  // Bos string gelirse fallback default kullanilir.
  useEffect(() => {
    const fallback = "Horstmann Smart Logger";
    const t = (settings.site_title ?? "").trim();
    document.title = t.length > 0 ? t : fallback;
  }, [settings.site_title]);

  // favicon degistiginde <link rel="icon"> elementini guncelle. data URL veya
  // public path olabilir. Ayar bos ise public/favicon.ico fallback'i gerekir.
  useEffect(() => {
    const href = (settings.favicon ?? "").trim();
    // Mevcut ikon link'lerini topla (multiple olabilir: shortcut, apple-touch).
    let link = document.querySelector<HTMLLinkElement>("link[rel~='icon']");
    if (!link) {
      link = document.createElement("link");
      link.rel = "icon";
      document.head.appendChild(link);
    }
    // Bos ise default favicon.ico'ya don.
    link.href = href.length > 0 ? href : "/favicon.ico";
  }, [settings.favicon]);

  const applyLocal = useCallback((next: ProjectSettings) => {
    setSettings(next);
  }, []);

  const value = useMemo<ContextShape>(
    () => ({ settings, loading, refresh, applyLocal }),
    [settings, loading, refresh, applyLocal]
  );

  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useProjectSettings(): ContextShape {
  return useContext(Ctx);
}
