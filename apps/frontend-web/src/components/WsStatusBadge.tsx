import { useTranslation } from "react-i18next";

import type { WsConnectionState } from "../shared/useLiveValuesSocket";

/**
 * WS bağlantı durum göstergesi — header'da sabit duran rozet.
 *
 * "open"      → yeşil "Canlı" (telemetri akıyor)
 * "connecting"→ sarı "Bağlanıyor"
 * "closed"    → gri "Kopuk"
 * "error"     → kırmızı "Hata"
 *
 * Operatör panoyu açtığında telemetri akmıyor mu fark etsin diye sabit görünür.
 * Tooltip ile detaylı durum + son güncelleme zamanı verilebilir (genişletilebilir).
 */

interface Props {
  state: WsConnectionState;
}

interface Style {
  dot: string;
  text: string;
  label: string;
}

function styleFor(state: WsConnectionState, labels: Record<string, string>): Style {
  switch (state) {
    case "open":
      return { dot: "#10b981", text: "#065f46", label: labels.live };
    case "connecting":
      return { dot: "#f59e0b", text: "#92400e", label: labels.connecting };
    case "error":
      return { dot: "#dc2626", text: "#991b1b", label: labels.error };
    case "closed":
    default:
      return { dot: "#9ca3af", text: "#4b5563", label: labels.offline };
  }
}

export function WsStatusBadge({ state }: Props) {
  const { t } = useTranslation();
  const labels = {
    live: t("ws.live", "Canlı"),
    connecting: t("ws.connecting", "Bağlanıyor"),
    error: t("ws.error", "Hata"),
    offline: t("ws.offline", "Kopuk"),
  };
  const s = styleFor(state, labels);
  return (
    <div
      role="status"
      aria-live="polite"
      aria-label={`${t("ws.statusLabel", "Canlı veri durumu")}: ${s.label}`}
      title={s.label}
      style={{
        display: "inline-flex",
        alignItems: "center",
        gap: 6,
        padding: "4px 10px",
        background: "rgba(0,0,0,0.04)",
        borderRadius: 999,
        fontSize: 12,
        fontWeight: 500,
        color: s.text,
        lineHeight: 1,
      }}
    >
      <span
        style={{
          width: 8,
          height: 8,
          borderRadius: "50%",
          background: s.dot,
          // Pulse animasyonu: "connecting" durumu için ufak nabız.
          animation: state === "connecting" ? "ws-pulse 1.4s ease-in-out infinite" : undefined,
        }}
        aria-hidden="true"
      />
      <span>{s.label}</span>
      <style>{`@keyframes ws-pulse { 0%,100% { opacity: 1 } 50% { opacity: 0.3 } }`}</style>
    </div>
  );
}
