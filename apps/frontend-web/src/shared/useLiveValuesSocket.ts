/**
 * WebSocket-based canli telemetry hook.
 *
 * Backend `/api/v1/ws/live-values` endpoint'ine baglanir; her telemetri
 * mesajini anlik olarak alir ve `signalLiveValues` state'ini gunceller.
 * Polling'e gore gecikme ~10sn -> ~200ms.
 *
 * Davranis:
 *   - Auto-reconnect: bagi koparsa exponential backoff (1s, 2s, 4s, 8s, max 30s)
 *   - Heartbeat ping: server her 30sn ping; client beklemiyor disconnect tetikleyici
 *   - Snapshot fallback: WS bagli olsa bile periyodik (60sn) `/signals/live`
 *     cagirilir — yeni eklenen cihazlar veya ws drop ettigi mesajlar telafi
 *   - WS bagli degilken polling otomatik daha sik (5sn) yapilir
 */
import { useCallback, useEffect, useRef, useState } from "react";

import type { SignalLiveRow } from "./types";

type WsTelemetryMessage = {
  type: "telemetry";
  device_code: string;
  signal_key: string;
  signal_source?: string;
  signal_data_type?: string;
  value?: number | null;
  value_string?: string | null;
  quality?: string | null;
  source_timestamp?: string | null;
};

type WsHelloMessage = {
  type: "hello";
  user: string;
  filter: string | string[];
};

type WsPingMessage = {
  type: "ping";
};

type WsAnyMessage = WsTelemetryMessage | WsHelloMessage | WsPingMessage;

export type WsConnectionState = "connecting" | "open" | "closed" | "error";

/** Polling fetch'in mesaja donmus halinden bir signal row uretir.
 *  WS mesaji formatti backend telemetry payload'i ile ayni alanlari iceriyor;
 *  onu mevcut tablo formatina cevirip merge ediyoruz. */
function applyWsMessageToRows(
  prev: SignalLiveRow[],
  msg: WsTelemetryMessage
): SignalLiveRow[] {
  let updated = false;
  const out = prev.map((row) => {
    if (row.device_code !== msg.device_code || row.signal_key !== msg.signal_key) {
      return row;
    }
    updated = true;
    return {
      ...row,
      value: msg.value === undefined ? row.value : msg.value,
      value_string: msg.value_string === undefined ? row.value_string : msg.value_string,
      quality: msg.quality === undefined ? row.quality : msg.quality,
      source_timestamp:
        msg.source_timestamp === undefined ? row.source_timestamp : msg.source_timestamp
    };
  });
  // Mevcut listede yoksa eklemiyoruz — `/signals/live` snapshot daha guvenilir
  // (signal catalog × device kombinasyonu backend'de yapiliyor). WS sadece
  // mevcut row'un degerini guncellemek icin.
  return updated ? out : prev;
}

type Options = {
  token: string;
  /** Backend API base URL. WS URL'i bundan turetilir (http -> ws, https -> wss). */
  apiBaseUrl: string;
  /** Sadece bu cihazlarin telemetrisi gelir (Set<string>). Bos = hepsi. */
  deviceCodes?: Set<string>;
  /** Hook devre disi birakilirsa WS hic acilmaz. */
  enabled?: boolean;
};

function deriveWsUrl(apiBaseUrl: string, token: string, deviceCodes?: Set<string>): string {
  // apiBaseUrl: "http://x.com/api/v1" veya "/api/v1"
  let base = apiBaseUrl.trim();
  if (base.startsWith("/")) {
    // Same-origin: window.location bilgisi ile mutlaklastir
    if (typeof window === "undefined") {
      throw new Error("WS URL window.location bilgisi olmadan turetilemez");
    }
    const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
    base = `${proto}//${window.location.host}${base}`;
  } else {
    base = base.replace(/^http:/i, "ws:").replace(/^https:/i, "wss:");
  }
  base = base.replace(/\/+$/, "");
  const params = new URLSearchParams();
  params.set("token", token);
  if (deviceCodes && deviceCodes.size > 0) {
    params.set("devices", Array.from(deviceCodes).join(","));
  }
  return `${base}/ws/live-values?${params.toString()}`;
}

/**
 * Live values WebSocket hook.
 *
 * @returns
 *   - `connectionState`: WS durum gostergesi (UI'da rozet icin)
 *   - `applyMessage`: gelen mesajla state'i guncelleyen reducer fonksiyonu
 *     (caller setSignalLiveValues icine cagirir)
 *   - `lastEventAt`: en son mesaj epoch ms (UI "5sn once goruldu" gibi)
 */
export function useLiveValuesSocket(opts: Options): {
  connectionState: WsConnectionState;
  lastEventAt: number | null;
  /** Kullanici tarafindan caller'a verilen mesaj uygulayici. State sahibi
   *  setRows(prev => applyWsMessageToRows(prev, msg)) ile cagirir. */
  apply: (prev: SignalLiveRow[], msg: WsTelemetryMessage) => SignalLiveRow[];
  /** Frontend'in WS'ten gelen son mesajlari biriktirdigi callback'i set etmesi
   *  icin. Bir batch icinde gelen mesajlar tek render'a yansir. */
  registerHandler: (cb: (msg: WsTelemetryMessage) => void) => void;
} {
  const { token, apiBaseUrl, deviceCodes, enabled = true } = opts;
  const [connectionState, setConnectionState] = useState<WsConnectionState>("closed");
  const [lastEventAt, setLastEventAt] = useState<number | null>(null);
  const handlerRef = useRef<((msg: WsTelemetryMessage) => void) | null>(null);
  const wsRef = useRef<WebSocket | null>(null);
  const reconnectAttemptsRef = useRef(0);
  const reconnectTimerRef = useRef<number | null>(null);
  const explicitlyClosedRef = useRef(false);

  const registerHandler = useCallback(
    (cb: (msg: WsTelemetryMessage) => void) => {
      handlerRef.current = cb;
    },
    []
  );

  useEffect(() => {
    if (!enabled || !token) {
      return undefined;
    }

    explicitlyClosedRef.current = false;

    const connect = () => {
      if (explicitlyClosedRef.current) return;
      let url: string;
      try {
        url = deriveWsUrl(apiBaseUrl, token, deviceCodes);
      } catch {
        setConnectionState("error");
        return;
      }
      setConnectionState("connecting");
      let ws: WebSocket;
      try {
        ws = new WebSocket(url);
      } catch {
        setConnectionState("error");
        scheduleReconnect();
        return;
      }
      wsRef.current = ws;

      ws.onopen = () => {
        reconnectAttemptsRef.current = 0;
        setConnectionState("open");
      };

      ws.onmessage = (event) => {
        try {
          const data = JSON.parse(event.data) as WsAnyMessage;
          if (data.type === "telemetry") {
            setLastEventAt(Date.now());
            handlerRef.current?.(data);
          }
          // hello / ping mesajlari sadece connection-alive bilgisi; ignore.
        } catch {
          // Malformed mesaj, sessizce gec
        }
      };

      ws.onerror = () => {
        setConnectionState("error");
      };

      ws.onclose = () => {
        wsRef.current = null;
        setConnectionState("closed");
        if (!explicitlyClosedRef.current) {
          scheduleReconnect();
        }
      };
    };

    const scheduleReconnect = () => {
      if (explicitlyClosedRef.current) return;
      if (reconnectTimerRef.current !== null) return;
      const attempt = reconnectAttemptsRef.current;
      reconnectAttemptsRef.current = attempt + 1;
      // Exponential backoff: 1s, 2s, 4s, 8s, 16s, 30s cap
      const delay = Math.min(30_000, 1000 * Math.pow(2, attempt));
      reconnectTimerRef.current = window.setTimeout(() => {
        reconnectTimerRef.current = null;
        connect();
      }, delay);
    };

    connect();

    return () => {
      explicitlyClosedRef.current = true;
      if (reconnectTimerRef.current !== null) {
        window.clearTimeout(reconnectTimerRef.current);
        reconnectTimerRef.current = null;
      }
      const ws = wsRef.current;
      wsRef.current = null;
      if (ws && ws.readyState === WebSocket.OPEN) {
        try {
          ws.close(1000, "client_unmount");
        } catch {
          // ignore
        }
      }
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [token, apiBaseUrl, enabled, deviceCodes ? Array.from(deviceCodes).sort().join(",") : ""]);

  return {
    connectionState,
    lastEventAt,
    apply: applyWsMessageToRows,
    registerHandler
  };
}
