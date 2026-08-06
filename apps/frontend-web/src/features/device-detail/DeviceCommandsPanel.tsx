/**
 * DeviceCommandsPanel — cihaz komutlari, mantiksal gruplu.
 *
 * Komut listesi backend SignalCatalog binary_output (master) sinyallerinden
 * gelir; burada slug -> grup + ikon eslemesi yapilir. Gruplar:
 *   - general     : genel komutlar (config download, firmware, boost...)
 *   - alarm_reset : alarm/ariza reset (Reset all FCIs=Index7, tamper, sayac...)
 *   - config      : config degistirme (installer-only, canConfig gate)
 *   - danger      : yikici (Software Reset) — ayri, uyari tonlu
 *
 * RBAC: canCommand (engineer+installer) general+alarm_reset+danger'i acar;
 * config grubu ayrica canConfig (installer) ister. Backend de ayni siniri
 * uygular (devices.py _CONFIG_COMMAND_SLUGS) — bu UI ikincil savunma.
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";

import { fetchDeviceCommands } from "../../shared/api";
import { signalLabel } from "../../shared/signalLabel";
import { formatDateTime, formatRelative } from "../../shared/format";
import type { DeviceCommandRow, SignalCatalogRow } from "../../shared/types";
import { usePolling } from "../../shared/usePolling";

type CmdGroup = "general" | "alarm_reset" | "config" | "danger";

type CmdMeta = { icon: string; group: CmdGroup };

// slug -> UI meta. Backend'de olmayan slug'lar "general"a duser (default meta).
// Referans: memory/dnp3-command-list.md index'leri.
const CMD_META: Record<string, CmdMeta> = {
  // Genel
  trigger_config_download: { icon: "download", group: "general" },
  trigger_firmware_download: { icon: "system_update", group: "general" },
  firmware_update: { icon: "system_update_alt", group: "general" },
  modem_firmware_update_ota: { icon: "cell_tower", group: "general" },
  boost_mode: { icon: "rocket_launch", group: "general" },
  upload_debug_file: { icon: "bug_report", group: "general" },
  enable_local_communication: { icon: "lan", group: "general" },
  enable_password: { icon: "password", group: "general" },
  // Alarm / ariza reset
  reset_all_fcis: { icon: "restart_alt", group: "alarm_reset" },
  reset_tamper_alarm: { icon: "gpp_maybe", group: "alarm_reset" },
  clear_counters: { icon: "delete_sweep", group: "alarm_reset" },
  auto_reset_fault_values: { icon: "cyclone", group: "alarm_reset" },
  clear_dnp3_buffer: { icon: "clear_all", group: "alarm_reset" },
  // Config (installer-only)
  config_update: { icon: "settings", group: "config" },
  dnp3_config_update: { icon: "settings_ethernet", group: "config" },
  trigger_dnp3_config_download: { icon: "cloud_download", group: "config" },
  start_csv_file_upload: { icon: "upload_file", group: "config" },
  // Yikici
  software_reset: { icon: "power_settings_new", group: "danger" },
};

const DEFAULT_META: CmdMeta = { icon: "bolt", group: "general" };

// Grup gorunum sirasi + i18n baslik anahtari + ikon.
const GROUP_ORDER: { key: CmdGroup; icon: string }[] = [
  { key: "general", icon: "terminal" },
  { key: "alarm_reset", icon: "notifications_off" },
  { key: "config", icon: "tune" },
  { key: "danger", icon: "warning" },
];

type CommandItem = { slug: string; label: string; icon: string; group: CmdGroup; order: number };

type Props = {
  deviceCode: string;
  signals: SignalCatalogRow[];
  canCommand: boolean;
  canConfig: boolean;
  /** Komut gonderme handler'i (confirm + toast App.tsx'te merkezi). */
  onDeviceCommand: (deviceCode: string, command: string, label: string) => Promise<void>;
  token: string;
};

export function DeviceCommandsPanel({
  deviceCode,
  signals,
  canCommand,
  canConfig,
  onDeviceCommand,
  token,
}: Props) {
  const { t } = useTranslation();
  const [busyCmd, setBusyCmd] = useState<string | null>(null);
  const [cmdHistory, setCmdHistory] = useState<DeviceCommandRow[]>([]);
  // Accordion: varsayilan alarm_reset acik (en cok kullanilan), digerleri kapali.
  const [openGroups, setOpenGroups] = useState<Record<string, boolean>>({ alarm_reset: true });
  const toggleGroup = (k: string) => setOpenGroups((s) => ({ ...s, [k]: !s[k] }));

  // Komut listesi: master binary_output sinyalleri -> grup meta ile zenginlestir.
  const commands = useMemo<CommandItem[]>(() => {
    return signals
      .filter((s) => s.data_type === "binary_output" && s.source === "master" && s.is_active)
      .map((s) => {
        const slug = s.key.replace(/^master\./, "");
        const meta = CMD_META[slug] ?? DEFAULT_META;
        return { slug, label: signalLabel(s.key, s.label), icon: meta.icon, group: meta.group, order: s.display_order };
      })
      .sort((a, b) => a.order - b.order);
  }, [signals]);

  const byGroup = useMemo(() => {
    const m: Record<CmdGroup, CommandItem[]> = {
      general: [], alarm_reset: [], config: [], danger: [],
    };
    for (const c of commands) m[c.group].push(c);
    return m;
  }, [commands]);

  const reloadCommands = useCallback(async () => {
    if (!token || !deviceCode) return;
    try {
      setCmdHistory(await fetchDeviceCommands(token, deviceCode, 30));
    } catch {
      // sessiz — komut gecmisi ikincil
    }
  }, [token, deviceCode]);

  useEffect(() => {
    void reloadCommands();
  }, [reloadCommands]);

  // pending/sent komut varken 10sn'de bir yenile; terminal olunca dur.
  const hasOpenCmd = useMemo(
    () => cmdHistory.some((c) => c.status === "pending" || c.status === "sent"),
    [cmdHistory]
  );
  usePolling({
    enabled: hasOpenCmd,
    intervalMs: 10000,
    fn: reloadCommands,
    immediate: false
  });

  const runCommand = async (slug: string, label: string) => {
    setBusyCmd(slug);
    try {
      await onDeviceCommand(deviceCode, slug, label);
      await reloadCommands();
    } finally {
      setBusyCmd(null);
    }
  };

  const labelForSlug = useCallback(
    (slug: string) => commands.find((c) => c.slug === slug)?.label ?? slug,
    [commands]
  );

  if (!canCommand) {
    return (
      <div className="device-detail-empty">
        <span className="material-symbols-outlined">lock</span>
        <p className="helper-text">{t("deviceDetail.commands.noPermission")}</p>
      </div>
    );
  }

  return (
    <div className="device-cmd-panel">
      {GROUP_ORDER.map(({ key, icon }) => {
        const items = byGroup[key];
        if (items.length === 0) return null;
        // config grubu installer-only: yetkisizse gizle.
        if (key === "config" && !canConfig) return null;
        const open = !!openGroups[key];
        return (
          <section key={key} className={`device-cmd-accordion is-${key}${open ? " is-open" : ""}`}>
            <button
              type="button"
              className="device-cmd-accordion-head"
              onClick={() => toggleGroup(key)}
              aria-expanded={open}
            >
              <span className="material-symbols-outlined device-cmd-accordion-icon">{icon}</span>
              <span className="device-cmd-accordion-title">{t(`deviceDetail.commands.group.${key}`)}</span>
              <span className="device-cmd-accordion-count">{items.length}</span>
              <span className="material-symbols-outlined device-cmd-accordion-chevron">
                {open ? "expand_less" : "expand_more"}
              </span>
            </button>
            {open ? (
              <div className="device-cmd-accordion-body">
                {items.map((c) => (
                  <button
                    key={c.slug}
                    type="button"
                    className={`device-cmd-btn${key === "danger" ? " is-danger" : ""}`}
                    disabled={busyCmd != null}
                    aria-busy={busyCmd === c.slug}
                    onClick={() => void runCommand(c.slug, c.label)}
                    title={c.label}
                  >
                    {busyCmd === c.slug ? (
                      <span className="btn-spinner" aria-hidden="true" />
                    ) : (
                      <span className="material-symbols-outlined">{c.icon}</span>
                    )}
                    <span className="device-cmd-btn-label">{c.label}</span>
                  </button>
                ))}
              </div>
            ) : null}
          </section>
        );
      })}

      {cmdHistory.length > 0 ? (
        <section className="device-cmd-history-section">
          <h4 className="device-cmd-group-title">
            <span className="material-symbols-outlined">history</span>
            {t("deviceDetail.commands.historyTitle")}
            <span className="device-cmd-history-count">{cmdHistory.length}</span>
          </h4>
          <p className="device-cmd-async-note">
            <span className="material-symbols-outlined">info</span>
            {t("deviceDetail.commands.asyncNote")}
          </p>
          <div className="device-cmd-history-box">
            {cmdHistory.map((c) => (
              <div key={c.id} className={`device-cmd-row is-${c.status}`}>
                <span className={`device-cmd-row-dot tone-${c.status}`} aria-hidden="true" />
                <div className="device-cmd-row-main">
                  <span className="device-cmd-row-label">{labelForSlug(c.command)}</span>
                  <span className="device-cmd-row-meta">
                    <span className="device-cmd-row-actor">
                      <span className="material-symbols-outlined">person</span>
                      {c.actor_username ?? t("deviceDetail.events.system")}
                    </span>
                    <span className="device-cmd-row-time" title={formatRelative(c.created_at)}>
                      {formatDateTime(c.created_at)}
                    </span>
                  </span>
                  {c.status === "failed" && c.result_error ? (
                    <span className="device-cmd-row-err" title={c.result_error}>
                      {c.result_error}
                    </span>
                  ) : null}
                </div>
                <span className={`device-cmd-row-status status-${c.status}`}>
                  {t(`deviceDetail.commands.status.${c.status}`)}
                </span>
              </div>
            ))}
          </div>
        </section>
      ) : null}
    </div>
  );
}
