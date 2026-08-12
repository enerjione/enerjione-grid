/**
 * WebSocket-based canli telemetry hook.
 *
 * Backend `/api/v1/ws/live-values` endpoint'ine baglanir; her telemetri
 * mesajini anlik olarak alir ve `signalLiveValues` state'ini gunceller.
 * Polling'e gore gecikme ~10sn -> ~200ms.
 *
 * Davranis:
 *   - Auto-reconnect: bagi koparsa exponential backoff (1s, 2s, 4s, 8s, max 30s)
 *   - Heartbeat ping: server her 30sn ping
 *   - BEKCI (watchdog): 45 sn'dir PING BILE gelmiyorsa soket "acik" gorunse de
 *     olmus sayilir, kapatilir ve yeniden baglanilir. Tarayici her kopmayi
 *     bildirmez (uyku, ag degisimi, vekil dusurmesi -> yari acik TCP:
 *     `readyState` OPEN ama `onclose` hic gelmez). Eskiden bu durumda ekran
 *     son degerlerde DONUYOR ve tek care sayfayi yenilemekti.
 *   - UYANMA: sekme one geldiginde / pencere odaga girdiginde / ag geri
 *     geldiginde backoff sifirlanir ve HEMEN baglanilir (arka plandaki sekmede
 *     zamanlayicilar kisildigi icin aksi halde 30 sn'ye kadar beklenirdi).
 *   - Snapshot fallback: WS bagli olsa bile periyodik (30sn) `/signals/live`
 *     cagirilir — yeni eklenen cihazlar veya ws drop ettigi mesajlar telafi.
 *     Kopus SONRASI da bir kez cagrilir (App.tsx): WS yalnizca bundan sonraki
 *     degisimleri gonderir, kopukluk sirasinda degisen deger ekranda eski kalirdi.
 *   - WS bagli degilken polling otomatik daha sik (5sn) yapilir
 *
 * OLCEK — mesajlar BATCH'lenir (bkz. FLUSH_INTERVAL_MS):
 *   Onceki tasarim her WS mesajinda handler'i cagiriyordu ve handler her
 *   cagride tum satir dizisini `prev.map()` ile geziyordu. Maliyet
 *   O(mesaj x satir): 600 cihaz x 193 sinyal = 115.800 satir, 100 msg/sn ->
 *   saniyede ~11,5 milyon karsilastirma + 100 adet 115.800'luk dizi tahsisi.
 *   Tarayici sekmesi kilitleniyordu.
 *
 *   Simdi mesajlar bir tamponda birikir ve 250ms'de bir TEK handler cagrisi
 *   ile teslim edilir. `applyMessages` de tum batch icin satir dizisini
 *   YALNIZCA BIR KEZ gezer (mesajlar (device_code, signal_key) anahtarli bir
 *   Map'e cevrilir). Maliyet O(satir + mesaj) / flush.
 */
import { useCallback, useEffect, useRef, useState } from "react";

import type { SignalLiveRow } from "./types";
import { connectionIsDead } from "./wsDataStatus";

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
  /** Cihaz saati durumu. Backend'in DEGERLENDIRDIGI deger gelir (gateway'in
   *  ham bildirimi degil), boylece WS ile `/signals/live` snapshot'i ayni
   *  satir icin ayni seyi gosterir. */
  timestamp_quality?: string | null;
  device_event_at?: string | null;
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

/** Bir satiri/mesaji tanimlayan bilesik anahtar. */
function rowKey(deviceCode: string, signalKey: string): string {
  return `${deviceCode}\u0000${signalKey}`;
}

/**
 * Bir BATCH mesaji mevcut satir dizisine uygular.
 *
 * Dizi yalnizca BIR KEZ gezilir: mesajlar once (device_code, signal_key)
 * anahtarli bir Map'e indirilir (ayni cift icin son mesaj kazanir), sonra tek
 * `map()` gecisinde eslesen satirlar guncellenir. Mesaj basina tam gecis yapan
 * eski surumun O(mesaj x satir) maliyeti O(satir + mesaj)'a duser.
 *
 * Hicbir mesaj mevcut bir satirla eslesmezse `prev` AYNEN dondurulur —
 * referans degismedigi icin React gereksiz render yapmaz.
 */
function applyWsMessagesToRows(
  prev: SignalLiveRow[],
  msgs: readonly WsTelemetryMessage[]
): SignalLiveRow[] {
  if (msgs.length === 0 || prev.length === 0) return prev;

  // Son deger kazanir: ayni (cihaz, sinyal) icin batch'te birden fazla mesaj
  // olabilir; tabloda yalnizca en yenisi anlamli.
  const latest = new Map<string, WsTelemetryMessage>();
  for (const msg of msgs) {
    latest.set(rowKey(msg.device_code, msg.signal_key), msg);
  }

  let updated = false;
  const out = prev.map((row) => {
    const msg = latest.get(rowKey(row.device_code, row.signal_key));
    if (msg === undefined) return row;
    updated = true;
    return {
      ...row,
      value: msg.value === undefined ? row.value : msg.value,
      value_string: msg.value_string === undefined ? row.value_string : msg.value_string,
      quality: msg.quality === undefined ? row.quality : msg.quality,
      source_timestamp:
        msg.source_timestamp === undefined ? row.source_timestamp : msg.source_timestamp,
      // `undefined` = alan gelmedi -> mevcut degeri KORU (eski backend).
      // `null` = geldi ve "bilgi yok" -> temizle. Ikisini ayirmak sart:
      // aksi halde saati duzelen bir cihazin uyarisi ekranda asili kalirdi.
      timestamp_quality:
        msg.timestamp_quality === undefined ? row.timestamp_quality : msg.timestamp_quality,
      device_event_at:
        msg.device_event_at === undefined ? row.device_event_at : msg.device_event_at
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
  /** Batch teslim periyodu (ms). Varsayilan 250 — insan gozu icin anlik,
   *  buna karsilik saniyede 4 render tavani koyar. */
  flushIntervalMs?: number;
};

/** Batch teslim periyodu (ms). Kullanici gozunde "anlik" kalan en buyuk deger:
 *  250ms'de bir render, 600 cihazda bile tarayiciyi rahat birakir. */
const FLUSH_INTERVAL_MS = 250;

/**
 * Tampon ust siniri. Sekme arka planda oldugunda tarayici timer'lari kistigi
 * icin flush seyrekleşir; tampon sinirsiz olsa bellek sisirdi. Sinir asilinca
 * EN ESKI mesajlar atilir:
 *   - Canli deger tablosu icin kayip yok (zaten son deger kazaniyor),
 *   - Grafik (append) tarafinda ara noktalar seyrekleşir; kabul edilebilir,
 *     snapshot/refetch telafi ediyor.
 */
const MAX_BUFFERED_MESSAGES = 5000;

/** Bekci ne siklikta bakar. Esik 45 sn oldugu icin 10 sn yeterince sik. */
const WATCHDOG_CHECK_MS = 10_000;

function deriveWsUrl(
  apiBaseUrl: string,
  authParam: { ticket: string } | { token: string },
  deviceCodes?: Set<string>,
): string {
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
  // Yeni yol: ticket query'de — JWT URL'de DEGIL. Ticket 30sn TTL + tek
  // kullanim oldugu icin access log'a sizsa bile pratik degeri kalmaz.
  // Legacy `token` icin geri uyumluluk yine destekleniyor.
  if ("ticket" in authParam) {
    params.set("ticket", authParam.ticket);
  } else {
    params.set("token", authParam.token);
  }
  if (deviceCodes && deviceCodes.size > 0) {
    params.set("devices", Array.from(deviceCodes).join(","));
  }
  return `${base}/ws/live-values?${params.toString()}`;
}

/** WS ticket — POST /auth/ws-ticket'tan alinir, 30sn TTL + tek kullanim.
 *  Cookie auth (e1_session) varsa kullanilir; yoksa Authorization header. */
async function fetchWsTicket(apiBaseUrl: string, token: string): Promise<string> {
  const url = `${apiBaseUrl.replace(/\/+$/, "")}/auth/ws-ticket`;
  const headers: Record<string, string> = { "Content-Type": "application/json" };
  if (token) headers["Authorization"] = `Bearer ${token}`;
  const resp = await fetch(url, {
    method: "POST",
    headers,
    credentials: "include",  // HttpOnly cookie de gelsin
  });
  if (!resp.ok) {
    throw new Error(`ws_ticket_fetch_failed status=${resp.status}`);
  }
  const data = (await resp.json()) as { ticket: string; expires_in_sec: number };
  return data.ticket;
}

/**
 * Live values WebSocket hook.
 *
 * @returns
 *   - `connectionState`: WS durum gostergesi (UI'da rozet icin)
 *   - `applyMessages`: bir BATCH mesajla satir dizisini guncelleyen reducer
 *     (caller setSignalLiveValues icine cagirir)
 *   - `registerHandler`: batch teslim callback'i
 *
 * NOT: eskiden `lastEventAt` (son mesaj epoch ms) da donuluyordu ama hicbir
 * tuketicisi yoktu ve her mesajda `setState` cagirdigi icin TUM App agacini
 * gereksiz yeniden render ediyordu. Boyle bir gostergeye ihtiyac dogarsa
 * render tetiklemeyen bir ref ile eklenmeli.
 */
export function useLiveValuesSocket(opts: Options): {
  connectionState: WsConnectionState;
  /** State sahibi `setRows(prev => applyMessages(prev, msgs))` ile cagirir.
   *  Dizi tek gecisde guncellenir; bkz. applyWsMessagesToRows. */
  applyMessages: (prev: SignalLiveRow[], msgs: readonly WsTelemetryMessage[]) => SignalLiveRow[];
  /** WS mesajlarinin BATCH olarak teslim edilecegi callback. Hook mesajlari
   *  `flushIntervalMs` boyunca tamponlar ve tek cagride verir — mesaj basina
   *  render tetiklenmez. */
  registerHandler: (cb: (msgs: readonly WsTelemetryMessage[]) => void) => void;
  /** Son TELEMETRI mesajinin alindigi an (ms). `null` = baglantidan beri hic
   *  veri gelmedi. Soket durumundan AYRIDIR: soket acik olup veri akmiyor
   *  olabilir. */
  lastDataAt: number | null;
  /** BEKLENMEDIK kopus sonrasi baglanti geri geldiginde artar (sayfa gecisiyle
   *  yapilan bilincli kapatmalarda ARTMAZ). Cagiran taraf bunu izleyip anlik
   *  goruntuyu tazeler: WS yalnizca bundan sonraki degisimleri gonderir,
   *  kopukluk sirasinda degisen deger ekranda eski kalirdi. */
  recoveryTick: number;
} {
  const { token, apiBaseUrl, deviceCodes, enabled = true, flushIntervalMs = FLUSH_INTERVAL_MS } = opts;
  const [connectionState, setConnectionState] = useState<WsConnectionState>("closed");
  const [lastDataAt, setLastDataAt] = useState<number | null>(null);
  // BEKLENMEDIK kopus sonrasi kurulan her baglantida artar. Sayfa gecisinde
  // soket BILEREK kapatilir (baska sekmede canli veri gerekmiyor); onu kopus
  // saymak, her sekme gecisinde fazladan bir anlik goruntu istegi demekti.
  const [recoveryTick, setRecoveryTick] = useState(0);
  const unexpectedDropRef = useRef(false);
  const handlerRef = useRef<((msgs: readonly WsTelemetryMessage[]) => void) | null>(null);
  const wsRef = useRef<WebSocket | null>(null);
  const reconnectAttemptsRef = useRef(0);
  const reconnectTimerRef = useRef<number | null>(null);
  const explicitlyClosedRef = useRef(false);
  // Ayni anda iki soket acilmasini engeller: `connect` async (once bilet
  // aliniyor) ve bekci/uyanma/backoff yollarinin ucu de onu cagirabiliyor.
  const connectingRef = useRef(false);
  //: SON MESAJ zamani — ping/hello DAHIL. `lastDataAt`ten (yalnizca telemetri)
  //  ayri tutulmali: gateway sussa bile sunucu ping atmaya devam eder, yani
  //  ping'lerin kesilmesi SOKETIN oldugunu soyler. Ikisini tek sayaca
  //  baglamak, gateway sustugunda saglam bir baglantiyi bosuna kapatirdi.
  const lastMessageAtRef = useRef<number | null>(null);
  // Flush'a kadar biriken mesajlar. Ham liste tutuluyor (dedup YAPILMIYOR)
  // cunku grafik tuketicisi her noktayi append etmek istiyor; tablo tarafinda
  // dedup zaten applyWsMessagesToRows icinde yapiliyor.
  const bufferRef = useRef<WsTelemetryMessage[]>([]);

  const registerHandler = useCallback(
    (cb: (msgs: readonly WsTelemetryMessage[]) => void) => {
      handlerRef.current = cb;
    },
    []
  );

  // Flush dongusu — WS baglantisindan BAGIMSIZ yasar ki reconnect sirasinda
  // tamponda kalan mesajlar da teslim edilsin.
  useEffect(() => {
    if (!enabled) return undefined;
    const flush = () => {
      const buffered = bufferRef.current;
      if (buffered.length === 0) return;
      // Tamponu ATOMIK devret: handler icinde yeni mesaj gelirse kaybolmasin.
      bufferRef.current = [];
      // Son TELEMETRI zamani burada isaretlenir, `onmessage` icinde DEGIL:
      // mesaj basina setState, tamponlamanin onledigi render zincirini geri
      // getirirdi. Flush zaten bos tamponda erken donuyor, dolayisiyla bu
      // setState yalnizca gercekten veri geldiginde calisir.
      //
      // `ping`/`hello` BILEREK sayilmaz: sunucu 30sn'de bir ping atar, onu
      // tazelik sayarsak gateway sussa bile rozet yesil kalir — kapatmaya
      // calistigimiz yanilmanin ta kendisi.
      setLastDataAt(Date.now());
      handlerRef.current?.(buffered);
    };
    const id = window.setInterval(flush, Math.max(50, flushIntervalMs));
    return () => {
      window.clearInterval(id);
      // Unmount'ta kalan mesajlari da teslim et (son deger kaybolmasin).
      flush();
    };
  }, [enabled, flushIntervalMs]);

  useEffect(() => {
    if (!enabled || !token) {
      return undefined;
    }

    explicitlyClosedRef.current = false;

    const connect = async () => {
      if (explicitlyClosedRef.current || connectingRef.current) return;
      connectingRef.current = true;
      setConnectionState("connecting");
      // Ticket akisi: WS handshake oncesi short-lived bilet al (30sn TTL,
      // tek kullanim). JWT URL'de gorunmez → nginx access log + Referer
      // sizinti yok. Eski client'lar icin fallback olarak token query'sini
      // koruyoruz; backend ikisini de kabul ediyor.
      let url: string;
      try {
        const ticket = await fetchWsTicket(apiBaseUrl, token);
        url = deriveWsUrl(apiBaseUrl, { ticket }, deviceCodes);
      } catch {
        // Ticket alinamadi (cookie auth eksik, 401, network) → legacy token
        // ile fallback. Bu yol bir sonraki release'te kaldirilacak.
        try {
          url = deriveWsUrl(apiBaseUrl, { token }, deviceCodes);
        } catch {
          connectingRef.current = false;
          setConnectionState("error");
          return;
        }
      }
      let ws: WebSocket;
      try {
        ws = new WebSocket(url);
      } catch {
        connectingRef.current = false;
        setConnectionState("error");
        scheduleReconnect();
        return;
      }
      wsRef.current = ws;

      ws.onopen = () => {
        connectingRef.current = false;
        reconnectAttemptsRef.current = 0;
        if (unexpectedDropRef.current) {
          unexpectedDropRef.current = false;
          // Kopukluk sirasinda degisen degerler icin WS yeni mesaj
          // gondermeyebilir; cagiran taraf anlik goruntuyu tazelesin.
          setRecoveryTick((n) => n + 1);
        }
        // Bekci sayaci sifirdan baslar: yeni baglantiyi "45 sn'dir sessiz"
        // sanip aninda kapatmayalim.
        lastMessageAtRef.current = Date.now();
        setConnectionState("open");
      };

      ws.onmessage = (event) => {
        // HER mesaj (ping/hello dahil) baglantinin yasadiginin kanitidir.
        lastMessageAtRef.current = Date.now();
        try {
          const data = JSON.parse(event.data) as WsAnyMessage;
          if (data.type === "telemetry") {
            // Handler'i BURADA cagirmiyoruz: tampona yaz, flush timer'i
            // 250ms'de bir topluca teslim etsin. Mesaj basina setState ->
            // mesaj basina render zincirini kiran yer burasi.
            const buffered = bufferRef.current;
            buffered.push(data);
            if (buffered.length > MAX_BUFFERED_MESSAGES) {
              // En eskileri at — sinir asimi sekme arka plandayken olur.
              buffered.splice(0, buffered.length - MAX_BUFFERED_MESSAGES);
            }
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
        connectingRef.current = false;
        wsRef.current = null;
        lastMessageAtRef.current = null;
        setConnectionState("closed");
        if (!explicitlyClosedRef.current) {
          unexpectedDropRef.current = true;
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
        void connect();
      }, delay);
    };

    /**
     * BEKCI — sessizce olmus baglantiyi yakalar.
     *
     * Tarayici her kopmayi bildirmez: dizustu uykuya girdiginde, ag
     * degistiginde ya da aradaki vekil baglantiyi dusurdugunde TCP "yari
     * acik" kalir — `readyState` OPEN, `onclose` HIC tetiklenmez. Eski halde
     * bu durumda yeniden baglanma yolu HIC calismiyordu: ekran son gelen
     * degerlerde donuyor ve tek care sayfayi yenilemek oluyordu.
     *
     * Sunucu 30 sn'de bir ping attigi icin 45 sn sessizlik "bu soket olmus"
     * demektir. Kapatmak `onclose` -> `scheduleReconnect` zincirini tetikler.
     */
    const watchdog = window.setInterval(() => {
      const ws = wsRef.current;
      if (!ws || ws.readyState !== WebSocket.OPEN) return;
      if (!connectionIsDead("open", lastMessageAtRef.current, Date.now())) return;
      try {
        ws.close(4000, "watchdog_silence");
      } catch {
        // ignore
      }
      // Bazi tarayicilarda `onclose` gecikebiliyor; durumu HEMEN dusur ki
      // polling hizli periyoda gecsin ve rozet dogruyu soylesin.
      wsRef.current = null;
      lastMessageAtRef.current = null;
      connectingRef.current = false;
      unexpectedDropRef.current = true;
      setConnectionState("closed");
      scheduleReconnect();
    }, WATCHDOG_CHECK_MS);

    /**
     * UYANMA — kullanici ekrana geri dondugunde BEKLETME.
     *
     * Arka plandaki sekmede tarayici zamanlayicilari kisar (Chrome 5 dakika
     * sonra dakikada bire indirir, sekmeyi tamamen dondurabilir). Kopmus bir
     * baglantinin backoff'u da bu yuzden 30 sn'ye kadar cikmis olabilir.
     * Kullanici sekmeye dondugunde 30 saniye daha beklemek, "acinca hemen
     * son degerleri gostersin" beklentisinin tam tersi.
     *
     * Bu yuzden geri donuste backoff SIFIRLANIR ve hemen baglanilir. Soket
     * zaten acikken hicbir sey yapilmaz — gereksiz yeniden baglanma, akan
     * veride bosluk demek olurdu.
     */
    const wakeUp = () => {
      if (explicitlyClosedRef.current) return;
      const ws = wsRef.current;
      if (ws && ws.readyState === WebSocket.OPEN) return;
      if (connectingRef.current) return;
      if (reconnectTimerRef.current !== null) {
        window.clearTimeout(reconnectTimerRef.current);
        reconnectTimerRef.current = null;
      }
      reconnectAttemptsRef.current = 0;
      void connect();
    };

    const onVisibility = () => {
      if (!document.hidden) wakeUp();
    };

    document.addEventListener("visibilitychange", onVisibility);
    // `focus`: pencere baska bir UYGULAMANIN arkasindayken `document.hidden`
    // FALSE'tur, yani gorunurluk olayi hic tetiklenmez. Kullanici baska bir
    // programdan geri dondugunde yakalayan tek olay budur.
    window.addEventListener("focus", wakeUp);
    // Ag geri geldiginde backoff'un dolmasini bekleme.
    window.addEventListener("online", wakeUp);

    void connect();

    return () => {
      explicitlyClosedRef.current = true;
      window.clearInterval(watchdog);
      document.removeEventListener("visibilitychange", onVisibility);
      window.removeEventListener("focus", wakeUp);
      window.removeEventListener("online", wakeUp);
      connectingRef.current = false;
      lastMessageAtRef.current = null;
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
    lastDataAt,
    recoveryTick,
    applyMessages: applyWsMessagesToRows,
    registerHandler
  };
}
