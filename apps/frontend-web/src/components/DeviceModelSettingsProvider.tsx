/** Model bazli cihaz ayarlari (batarya esikleri) icin context.
 *
 * Esikler cihaz TURU seviyesindedir: SN 2.0'in lityum hucresi ile Pole
 * Master Kit'in bataryasi ayni voltaj araliginda calismaz. Bu provider
 * `/device-models/settings` cevabini bir kez ceker ve tum ekranlar ayni
 * esikleri kullanir — onceden her ekran kendi sabitini tasiyordu ve ayni
 * cihaz farkli yerlerde farkli yuzde gosterebiliyordu.
 *
 * Provider YOKSA ya da veri henuz gelmediyse `DEFAULT_BATTERY_THRESHOLDS`
 * doner; hicbir ekran bu yuzden bos kalmaz.
 */
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode
} from "react";

import { fetchDeviceModelSettings, type DeviceModelSettingsRow } from "../shared/api";
import { DEFAULT_BATTERY_THRESHOLDS, type BatteryThresholds } from "../shared/battery";

type ContextShape = {
  /** Modelin cozulmus (model -> proje -> kod) batarya esikleri. */
  thresholdsFor: (model: string | null | undefined) => BatteryThresholds;
  rows: DeviceModelSettingsRow[];
  refresh: () => Promise<void>;
};

const Ctx = createContext<ContextShape>({
  thresholdsFor: () => DEFAULT_BATTERY_THRESHOLDS,
  rows: [],
  refresh: async () => {}
});

export function DeviceModelSettingsProvider({
  token,
  children
}: {
  token: string | null | undefined;
  children: ReactNode;
}) {
  const [rows, setRows] = useState<DeviceModelSettingsRow[]>([]);

  const refresh = useCallback(async () => {
    if (!token) return;
    try {
      setRows(await fetchDeviceModelSettings(token));
    } catch {
      // Ayar cekilemezse varsayilanlarla devam — batarya gostergesi
      // yanlis olmaktansa varsayilan esikle calissin.
    }
  }, [token]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const value = useMemo<ContextShape>(() => {
    const harita = new Map<string, BatteryThresholds>();
    for (const r of rows) {
      harita.set(r.model, {
        low: r.resolved_battery_voltage_low,
        full: r.resolved_battery_voltage_full
      });
    }
    return {
      rows,
      refresh,
      thresholdsFor: (model) =>
        (model ? harita.get(model) : undefined) ?? DEFAULT_BATTERY_THRESHOLDS
    };
  }, [rows, refresh]);

  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useDeviceModelSettings(): ContextShape {
  return useContext(Ctx);
}
