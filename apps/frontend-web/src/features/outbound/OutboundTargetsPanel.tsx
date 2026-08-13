import { useCallback, useEffect, useMemo, useRef, useState, type FormEvent } from "react";
import { asyncConfirm } from "../../components/ConfirmDialog";
import { useTranslation } from "react-i18next";

import { ActiveSwitch } from "../../components/ActiveSwitch";
import { useToast } from "../../components/ToastProvider";
import type { DeviceRow, Iec104RuntimeStatus, ModbusRuntimeStatus, OutboundTarget } from "../../shared/types";
import type { MqttPayloadFields, OutboundRuntimeStatus, OutboundAutoTopic } from "../../shared/api";
import { fetchOutboundRuntimeStatus, fetchOutboundAutoTopics, uploadMqttCert, testOutboundTarget } from "../../shared/api";
import { MqttTopicMappingModal } from "./MqttTopicMappingModal";
import { MqttCertUploader } from "./MqttCertUploader";
import { ModbusPlanModal } from "./ModbusPlanModal";
import { downloadModbusPointsCsv } from "../../shared/api";
import { usePolling } from "../../shared/usePolling";

type Protocol = "rest" | "mqtt" | "iec104" | "modbus";
const DEFAULT_PROTOCOLS: Protocol[] = ["rest", "mqtt", "iec104", "modbus"];

// Modbus TCP: SCADA'nin baglandigi port. Container'da 502/5020/5021
// yayinlaniyor (docker-compose.yml); baska port secilirse compose'a eklenmeli.
// Ilk 100 adres (0..99) sistem metrikleri icin rezerve; cihaz bloklari
// 100'un katlarindan baslar. Backend karsiligi:
// modbus_plan_service.SYSTEM_BLOCK_SIZE
const MODBUS_SYSTEM_BLOCK_SIZE = 100;
const MODBUS_DEFAULT_PORT = "502";
const IEC104_DEFAULT_PORT = "2404";

/** Server tipi protokollerin varsayilan dinleme portu. */
function defaultPortFor(protocol: Protocol): string {
  return protocol === "modbus" ? MODBUS_DEFAULT_PORT : IEC104_DEFAULT_PORT;
}

// IEC104 listen_host'u bu degerlerden biriyse "tum arayuzler" anlamina gelir;
// SCADA buraya dogrudan baglanamaz, tarayicinin host'unu gostermek gerekir.
const WILDCARD_HOSTS = new Set(["0.0.0.0", "::", "*", ""]);

// n8n / webhook formunda gosterilen ornek payload — backend'in test endpoint'i
// ve gercek alarm event'i ile ayni sekildir; operator n8n akisini buna gore kurar.
const WEBHOOK_SAMPLE_JSON = `{
  "message_id": "3f0c…",
  "event_kind": "alarm",
  "device_code": "DEV-001",
  "device_name": "Ornek Fider",
  "signal_key": "sat01.current_phase_a",
  "value": 245.7,
  "quality": "good",
  "severity": "critical",
  "message": "Faz A akimi esik ustunde",
  "timestamp": "2026-07-27T09:30:00+00:00"
}`;

export type OutboundTargetCreatePayload = {
  name: string;
  protocol: Protocol;
  endpoint: string;
  topic?: string | null;
  event_filter: "all" | "telemetry" | "alarm";
  auth_header?: string | null;
  auth_token?: string | null;
  qos: number;
  retain: boolean;
  is_active: boolean;
  listen_host?: string | null;
  listen_port?: number | null;
  iec104_common_address?: number | null;
  iec104_allowed_peers?: string | null;
  // Modbus TCP (protocol='modbus')
  modbus_mode?: "block" | "unit";
  modbus_unit_id?: number;
  modbus_value_format?: "int16" | "float32";
  modbus_word_order?: "big" | "little";
  modbus_block_stride?: number | null;
  modbus_base_address?: number;
  modbus_allowed_peers?: string | null;
} & MqttPayloadFields;

export type OutboundTargetUpdatePayload = {
  endpoint?: string;
  topic?: string | null;
  event_filter?: "all" | "telemetry" | "alarm";
  auth_header?: string | null;
  auth_token?: string | null;
  qos?: number;
  retain?: boolean;
  is_active?: boolean;
  listen_host?: string | null;
  listen_port?: number | null;
  iec104_common_address?: number | null;
  iec104_allowed_peers?: string | null;
  // Modbus TCP (protocol='modbus')
  modbus_mode?: "block" | "unit";
  modbus_unit_id?: number;
  modbus_value_format?: "int16" | "float32";
  modbus_word_order?: "big" | "little";
  modbus_block_stride?: number | null;
  modbus_base_address?: number;
  modbus_allowed_peers?: string | null;
} & MqttPayloadFields;

type Props = {
  targets: OutboundTarget[];
  devices?: DeviceRow[];
  accessToken: string;
  allowedProtocols?: Protocol[];
  titleKey?: string;
  newTargetKey?: string;
  /** true ise panel kendi filtre/ekleme cubugunu cizmez — buton ust
   *  bilesenin basliginda durur (Bildirim Ayarlari > Webhook). */
  hideToolbar?: boolean;
  /** Cubuk gizliyken ust bilesen "yeni hedef" akisini baslatabilsin diye
   *  `openCreate` fonksiyonunu disari verir. */
  onCreateHandlerReady?: (openCreate: () => void) => void;
  onCreate: (payload: OutboundTargetCreatePayload) => Promise<OutboundTarget | undefined>;
  onUpdate: (targetId: number, payload: OutboundTargetUpdatePayload) => Promise<void>;
  onDelete: (targetId: number) => Promise<void>;
  /** @deprecated CSV butonu arayuzden kaldirildi (Excel ile ayni listeyi
   *  indiriyordu). Backend ucu duruyor; geri eklenmek istenirse kullanilir. */
  onDownloadIec104Points?: (targetId: number, suggestedName: string) => Promise<void>;
  onDownloadIec104Xlsx?: (targetId: number, suggestedName: string) => Promise<void>;
  onUpdateDeviceCa?: (deviceCode: string, ca: number | null) => Promise<void>;
  onAutoAssignDeviceCa?: (targetId: number, overwrite: boolean) => Promise<void>;
  onFetchIec104Runtime?: (targetId: number) => Promise<Iec104RuntimeStatus>;
  onFetchModbusRuntime?: (targetId: number) => Promise<ModbusRuntimeStatus>;
};

export function OutboundTargetsPanel({
  targets,
  devices,
  accessToken,
  allowedProtocols,
  titleKey = "engineering.outbound.title",
  newTargetKey = "engineering.outbound.newTarget",
  hideToolbar = false,
  onCreateHandlerReady,
  onCreate,
  onUpdate,
  onDelete,
  onDownloadIec104Points,
  onDownloadIec104Xlsx,
  onUpdateDeviceCa,
  onAutoAssignDeviceCa,
  onFetchIec104Runtime,
  onFetchModbusRuntime
}: Props) {
  const { t } = useTranslation();
  const protocolKey = (allowedProtocols ?? DEFAULT_PROTOCOLS).join("|");
  const protocols = useMemo(
    () => (allowedProtocols ?? DEFAULT_PROTOCOLS).filter((protocol) => DEFAULT_PROTOCOLS.includes(protocol)),
    [allowedProtocols, protocolKey]
  );
  const firstProtocol = protocols[0] ?? "rest";
  const visibleTargets = useMemo(
    () => targets.filter((target) => protocols.includes(target.protocol as Protocol)),
    [targets, protocols]
  );
  // Sadece REST izinliyse (Bildirim Ayarlari > Webhook) genel outbound tablosu
  // yerine sade kart listesi gosterilir — SCADA/protokol sutunlari webhook'ta
  // anlamsiz, tablo cok bos gorunuyordu.
  const isWebhookMode = protocols.length === 1 && protocols[0] === "rest";
  // Diger sayfalardaki (alarm kurallari / kullanicilar / canli degerler)
  // filtre cubugunun outbound karsiligi: arama + protokol filtresi.
  // NOT: runtime polling'i `visibleTargets` uzerinden yapilir — filtre
  // sadece GORUNUMU daraltir, arka plandaki durum sorgusunu degil.
  const [search, setSearch] = useState("");
  const [protocolFilter, setProtocolFilter] = useState<"all" | Protocol>("all");
  const filteredTargets = useMemo(() => {
    const q = search.trim().toLowerCase();
    return visibleTargets.filter((target) => {
      if (protocolFilter !== "all" && target.protocol !== protocolFilter) return false;
      if (!q) return true;
      return [target.name, target.protocol, target.endpoint ?? "", target.topic ?? ""]
        .join(" ")
        .toLowerCase()
        .includes(q);
    });
  }, [visibleTargets, search, protocolFilter]);
  const [isCreateOpen, setCreateOpen] = useState(false);
  const [editing, setEditing] = useState<OutboundTarget | null>(null);
  const [error, setError] = useState("");
  // Hata mesaji panelin altinda satir olarak degil toast ile gosterilir.
  // Tek yerden yakalamak icin state korunuyor; `setError(...)` cagiran tum
  // akislar otomatik olarak toast uretir.
  const toast = useToast();
  useEffect(() => {
    if (error) toast.error(error);
  }, [error, toast]);

  // Cihaz ASDU adresleri popup
  const [asduModalTarget, setAsduModalTarget] = useState<OutboundTarget | null>(null);
  const [deviceCaDraft, setDeviceCaDraft] = useState<Record<string, string>>({});
  const [savingDeviceCode, setSavingDeviceCode] = useState<string | null>(null);
  const [caSearch, setCaSearch] = useState("");
  const [autoAssigning, setAutoAssigning] = useState(false);

  // Runtime status popup
  const [runtimeTarget, setRuntimeTarget] = useState<OutboundTarget | null>(null);
  const [runtime, setRuntime] = useState<Iec104RuntimeStatus | null>(null);
  const [runtimeLoading, setRuntimeLoading] = useState(false);

  const [runtimeBadges, setRuntimeBadges] = useState<Record<number, { running: boolean; clients: number }>>({});

  // Modbus runtime — rozet + teshis popup'i (IEC104 kalibiyla ayni akis)
  const [mbRuntimeTarget, setMbRuntimeTarget] = useState<OutboundTarget | null>(null);
  const [mbRuntime, setMbRuntime] = useState<ModbusRuntimeStatus | null>(null);
  const [mbRuntimeLoading, setMbRuntimeLoading] = useState(false);
  const [mbBadges, setMbBadges] = useState<Record<number, { running: boolean; clients: number; flowing: boolean }>>({});

  // REST/MQTT runtime status (Durum sutunu)
  const [outboundRuntime, setOutboundRuntime] = useState<Record<number, OutboundRuntimeStatus>>({});
  // Create mode'da TLS sertifikalari icin pending file state — kayit
  // basariyla atildiktan sonra new target id ile uploadMqttCert cagrilir.
  const [pendingCertCa, setPendingCertCa] = useState<File | null>(null);
  const [pendingCertCert, setPendingCertCert] = useState<File | null>(null);
  const [pendingCertKey, setPendingCertKey] = useState<File | null>(null);

  // Otomatik Topic'ler popup
  const [autoTopicsTarget, setAutoTopicsTarget] = useState<OutboundTarget | null>(null);
  const [autoTopics, setAutoTopics] = useState<OutboundAutoTopic[] | null>(null);
  const [autoTopicsLoading, setAutoTopicsLoading] = useState(false);
  const [autoTopicsSearch, setAutoTopicsSearch] = useState("");
  const [copiedTopic, setCopiedTopic] = useState<string | null>(null);
  const [copiedAll, setCopiedAll] = useState(false);

  // Form state
  const [name, setName] = useState("");
  const [protocol, setProtocol] = useState<Protocol>("rest");
  const [endpoint, setEndpoint] = useState("");
  const [topic, setTopic] = useState("");
  const [eventFilter, setEventFilter] = useState<"all" | "telemetry" | "alarm">("all");
  const [authHeader, setAuthHeader] = useState("Authorization");
  const [authToken, setAuthToken] = useState("");
  const [qos, setQos] = useState(0);
  const [retain, setRetain] = useState(false);
  const [isActive, setIsActive] = useState(true);
  const [listenHost, setListenHost] = useState("0.0.0.0");
  const [listenPort, setListenPort] = useState("2404");
  const [iec104Ca, setIec104Ca] = useState("1");
  // Whitelist artik liste olarak yonetilir; Save'de virgulle birlestirilir.
  const [allowedPeerList, setAllowedPeerList] = useState<string[]>([]);
  const [newPeerIp, setNewPeerIp] = useState("");
  const [peerError, setPeerError] = useState("");

  // Modbus TCP state (protocol=modbus formunda gosterilir)
  const [modbusMode, setModbusMode] = useState<"block" | "unit">("block");
  const [modbusUnitId, setModbusUnitId] = useState("1");
  const [modbusValueFormat, setModbusValueFormat] = useState<"int16" | "float32">("int16");
  const [modbusWordOrder, setModbusWordOrder] = useState<"big" | "little">("big");
  const [modbusStride, setModbusStride] = useState("");
  // 100: ilk 100 adres (0..99) sistem metrikleri icin rezerve; cihaz
  // bloklari 100'un katlarindan baslar (cihaz 1 -> 100, cihaz 2 -> 200...).
  const [modbusBaseAddress, setModbusBaseAddress] = useState("100");
  // Adres plani modal'i — hangi hedef icin acik?
  const [planTargetId, setPlanTargetId] = useState<number | null>(null);

  // MQTT-specific state (protocol=mqtt formunda gosterilir)
  const [mqttPort, setMqttPort] = useState<string>("");
  const [mqttUsername, setMqttUsername] = useState("");
  const [mqttPassword, setMqttPassword] = useState("");
  const [mqttClientId, setMqttClientId] = useState("");
  const [mqttTlsEnabled, setMqttTlsEnabled] = useState(false);
  const [mqttTlsInsecure, setMqttTlsInsecure] = useState(false);
  const [mqttTlsCaPath, setMqttTlsCaPath] = useState("");
  const [mqttTlsCertPath, setMqttTlsCertPath] = useState("");
  const [mqttTlsKeyPath, setMqttTlsKeyPath] = useState("");
  const [mqttKeepalive, setMqttKeepalive] = useState<string>("60");
  const [mqttConnectTimeout, setMqttConnectTimeout] = useState<string>("10");
  const [mqttPublishInterval, setMqttPublishInterval] = useState<string>("10");
  const [mqttTopicTemplate, setMqttTopicTemplate] = useState("");
  const [mqttTopicPrefix, setMqttTopicPrefix] = useState("e1");
  const [mqttCustomerId, setMqttCustomerId] = useState("");
  const [showMqttAdvanced, setShowMqttAdvanced] = useState(false);

  // MQTT formu adim adim (wizard): 0 Baglanti, 1 Guvenlik(TLS), 2 Yayin, 3 Topic.
  const [mqttStep, setMqttStep] = useState(0);

  // Custom Topic Mapping modal — hangi target icin acik
  const [mappingModalTarget, setMappingModalTarget] = useState<OutboundTarget | null>(null);

  // Webhook (n8n) test gonderimi — hangi target test ediliyor + sonuclar
  const [webhookTestingId, setWebhookTestingId] = useState<number | null>(null);
  const [webhookTestResults, setWebhookTestResults] = useState<
    Record<number, { ok: boolean; detail: string }>
  >({});

  const runWebhookTest = async (item: OutboundTarget) => {
    setWebhookTestingId(item.id);
    try {
      const res = await testOutboundTarget(accessToken, item.id);
      setWebhookTestResults((prev) => ({ ...prev, [item.id]: res }));
    } catch (err) {
      setWebhookTestResults((prev) => ({
        ...prev,
        [item.id]: { ok: false, detail: err instanceof Error ? err.message : t("common.errorOccurred") }
      }));
    } finally {
      setWebhookTestingId(null);
    }
  };

  const resetForm = () => {
    setName("");
    setProtocol(firstProtocol);
    setEndpoint("");
    setTopic("");
    setEventFilter("all");
    setAuthHeader("Authorization");
    setAuthToken("");
    setQos(0);
    setRetain(false);
    setIsActive(true);
    setListenHost("0.0.0.0");
    // Form sifirlanirken port, secili olacak protokolun varsayilani olsun.
    setListenPort(defaultPortFor(firstProtocol));
    setIec104Ca("1");
    setAllowedPeerList([]);
    setNewPeerIp("");
    setPeerError("");
    setModbusMode("block");
    setModbusUnitId("1");
    setModbusValueFormat("int16");
    setModbusWordOrder("big");
    setModbusStride("");
    setModbusBaseAddress(String(MODBUS_SYSTEM_BLOCK_SIZE));
    setMqttPort("");
    setMqttUsername("");
    setMqttPassword("");
    setMqttClientId("");
    setMqttTlsEnabled(false);
    setMqttTlsInsecure(false);
    setMqttTlsCaPath("");
    setMqttTlsCertPath("");
    setMqttTlsKeyPath("");
    setMqttKeepalive("60");
    setMqttConnectTimeout("10");
    setMqttPublishInterval("10");
    setMqttTopicTemplate("");
    setMqttTopicPrefix("e1");
    setMqttCustomerId("");
    setShowMqttAdvanced(false);
    setPendingCertCa(null);
    setPendingCertCert(null);
    setPendingCertKey(null);
  };

  // Yeni hedef ekleme: her acilista formu sifirla — boylece protokol izinli
  // ilk protokole (or. muhendislik'te MQTT) doner; aksi halde initial "rest"
  // state kaliyor ve MQTT/IEC104 sayfasinda yanlislikla webhook formu aciliyordu.
  const openCreate = () => {
    resetForm();
    setEditing(null);
    setError("");
    setMqttStep(0);
    setCreateOpen(true);
  };

  // Cubuk gizliyken ekleme butonu ust bilesenin basliginda durur; modali
  // acabilmesi icin openCreate'i disari veriyoruz. Ref uzerinden gecirmek
  // sart: openCreate her render'da yeniden olusuyor, dogrudan verirsek ust
  // bilesen bayat bir closure tutar ve resetForm eski state ile calisir.
  const openCreateRef = useRef(openCreate);
  openCreateRef.current = openCreate;
  useEffect(() => {
    onCreateHandlerReady?.(() => openCreateRef.current());
  }, [onCreateHandlerReady]);

  /** MQTT alanlarini payload'a ekle. createPayload'a ve updatePayload'a
   *  ortak helper. forceInclude=true ise protocol state'ini kontrol etmez
   *  (edit modunda editing.protocol === 'mqtt' iken kullanilir). */
  const collectMqttPayload = (forceInclude = false): MqttPayloadFields => {
    if (!forceInclude && protocol !== "mqtt") return {};
    const trimOrNull = (s: string): string | null => {
      const v = s.trim();
      return v === "" ? null : v;
    };
    const numOrNull = (s: string): number | null => {
      const v = s.trim();
      if (v === "") return null;
      const n = Number(v);
      return Number.isFinite(n) ? n : null;
    };
    const numOrUndef = (s: string, fallback: number): number => {
      const v = s.trim();
      if (v === "") return fallback;
      const n = Number(v);
      return Number.isFinite(n) ? n : fallback;
    };
    return {
      mqtt_port: numOrNull(mqttPort),
      mqtt_username: trimOrNull(mqttUsername),
      mqtt_password: trimOrNull(mqttPassword),
      mqtt_client_id: trimOrNull(mqttClientId),
      mqtt_tls_enabled: mqttTlsEnabled,
      mqtt_tls_insecure: mqttTlsInsecure,
      mqtt_tls_ca_path: trimOrNull(mqttTlsCaPath),
      mqtt_tls_cert_path: trimOrNull(mqttTlsCertPath),
      mqtt_tls_key_path: trimOrNull(mqttTlsKeyPath),
      mqtt_keepalive_sec: numOrUndef(mqttKeepalive, 60),
      mqtt_connect_timeout_sec: numOrUndef(mqttConnectTimeout, 10),
      mqtt_publish_interval_sec: numOrUndef(mqttPublishInterval, 10),
      mqtt_topic_template: trimOrNull(mqttTopicTemplate),
      mqtt_topic_prefix: mqttTopicPrefix.trim() || "e1",
      mqtt_customer_id: trimOrNull(mqttCustomerId),
    };
  };

  const handleCreate = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setError("");
    try {
      const isIec104 = protocol === "iec104";
      const isRest = protocol === "rest";
      const isMqtt = protocol === "mqtt";
      const isModbus = protocol === "modbus";
      // IEC104 ve Modbus ikisi de TCP sunucusudur; listen_host/port ikisinde de
      // anlamli, sadece varsayilan portlari farkli (2404 vs 502).
      const isServer = isIec104 || isModbus;
      const created = await onCreate({
        name,
        protocol,
        endpoint: isServer ? "" : endpoint,
        // Topic yalniz MQTT icin anlamli; REST/IEC104'te gonderme.
        topic: isMqtt && topic.trim() ? topic.trim() : null,
        event_filter: eventFilter,
        // Auth header/token yalniz REST icin; MQTT broker auth ayri akis,
        // IEC104'te zaten anlamsiz.
        auth_header: isRest && authHeader.trim() ? authHeader.trim() : null,
        auth_token: isRest && authToken.trim() ? authToken.trim() : null,
        // QoS + retain yalniz MQTT icin; REST/IEC104 sifir/false gonderir.
        qos: isMqtt ? qos : 0,
        retain: isMqtt ? retain : false,
        is_active: isActive,
        listen_host: isServer ? listenHost.trim() || "0.0.0.0" : null,
        listen_port: isServer
          ? Number(listenPort) || Number(defaultPortFor(protocol))
          : null,
        iec104_common_address: isIec104 ? Number(iec104Ca) || 1 : null,
        iec104_allowed_peers: isIec104 ? (allowedPeerList.join(",") || null) : null,
        ...(isModbus
          ? {
              modbus_mode: modbusMode,
              modbus_unit_id: Number(modbusUnitId) || 1,
              modbus_value_format: modbusValueFormat,
              modbus_word_order: modbusWordOrder,
              modbus_block_stride: modbusStride.trim() ? Number(modbusStride) : null,
              modbus_base_address: Number(modbusBaseAddress) || 0,
              modbus_allowed_peers: allowedPeerList.join(",") || null,
            }
          : {}),
        ...collectMqttPayload(),
      });
      // MQTT + TLS + pending sertifika dosyalari varsa: yeni target id ile
      // arka arkaya upload at. Hata olursa kullaniciya goster ama target zaten
      // olusturuldugu icin modal'i kapatma (operator edit'e gecip tekrar deneyebilir).
      if (isMqtt && mqttTlsEnabled && created) {
        const uploads: Promise<unknown>[] = [];
        if (pendingCertCa) uploads.push(uploadMqttCert(accessToken, created.id, "ca", pendingCertCa));
        if (pendingCertCert) uploads.push(uploadMqttCert(accessToken, created.id, "cert", pendingCertCert));
        if (pendingCertKey) uploads.push(uploadMqttCert(accessToken, created.id, "key", pendingCertKey));
        if (uploads.length > 0) {
          try {
            await Promise.all(uploads);
          } catch (certErr) {
            setError(
              certErr instanceof Error ? certErr.message : t("common.errorOccurred")
            );
            return;
          }
        }
      }
      resetForm();
      setCreateOpen(false);
    } catch (err) {
      setError(err instanceof Error ? err.message : t("common.errorOccurred"));
    }
  };

  const openEdit = (target: OutboundTarget) => {
    setEditing(target);
    setEndpoint(target.endpoint);
    setTopic(target.topic ?? "");
    setEventFilter(target.event_filter);
    setAuthHeader(target.auth_header ?? "Authorization");
    setAuthToken(target.auth_token ?? "");
    setQos(target.qos);
    setRetain(target.retain);
    setIsActive(target.is_active);
    setListenHost(target.listen_host ?? "0.0.0.0");
    setListenPort(
      target.listen_port !== null && target.listen_port !== undefined
        ? String(target.listen_port)
        : defaultPortFor(target.protocol as Protocol)
    );
    setIec104Ca(
      target.iec104_common_address !== null && target.iec104_common_address !== undefined
        ? String(target.iec104_common_address)
        : "1"
    );
    // Modbus alanlari
    setModbusMode(target.modbus_mode === "unit" ? "unit" : "block");
    setModbusUnitId(String(target.modbus_unit_id ?? 1));
    setModbusValueFormat(target.modbus_value_format === "float32" ? "float32" : "int16");
    setModbusWordOrder(target.modbus_word_order === "little" ? "little" : "big");
    setModbusStride(target.modbus_block_stride != null ? String(target.modbus_block_stride) : "");
    setModbusBaseAddress(String(target.modbus_base_address ?? MODBUS_SYSTEM_BLOCK_SIZE));
    // IP whitelist iki protokolde ayri kolonda tutulur; forma hangisi doluysa o gelir.
    const raw =
      (target.protocol === "modbus"
        ? target.modbus_allowed_peers
        : target.iec104_allowed_peers) ?? "";
    setAllowedPeerList(
      raw.split(",").map((p) => p.trim()).filter((p) => p.length > 0)
    );
    setNewPeerIp("");
    setPeerError("");
    // MQTT alanlarini target'tan yukle
    setMqttPort(target.mqtt_port != null ? String(target.mqtt_port) : "");
    setMqttUsername(target.mqtt_username ?? "");
    setMqttPassword(target.mqtt_password ?? "");
    setMqttClientId(target.mqtt_client_id ?? "");
    setMqttTlsEnabled(Boolean(target.mqtt_tls_enabled));
    setMqttTlsInsecure(Boolean(target.mqtt_tls_insecure));
    setMqttTlsCaPath(target.mqtt_tls_ca_path ?? "");
    setMqttTlsCertPath(target.mqtt_tls_cert_path ?? "");
    setMqttTlsKeyPath(target.mqtt_tls_key_path ?? "");
    setMqttKeepalive(String(target.mqtt_keepalive_sec ?? 60));
    setMqttConnectTimeout(String(target.mqtt_connect_timeout_sec ?? 10));
    setMqttPublishInterval(String(target.mqtt_publish_interval_sec ?? 10));
    setMqttTopicTemplate(target.mqtt_topic_template ?? "");
    setMqttTopicPrefix(target.mqtt_topic_prefix ?? "e1");
    setMqttCustomerId(target.mqtt_customer_id ?? "");
    setShowMqttAdvanced(false);
    setMqttStep(0);
  };

  const handleEdit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!editing) return;
    setError("");
    try {
      const isIec104 = editing.protocol === "iec104";
      const isRest = editing.protocol === "rest";
      const isMqtt = editing.protocol === "mqtt";
      const isModbus = editing.protocol === "modbus";
      const isServer = isIec104 || isModbus;
      // MQTT alanlari: editing.protocol === 'mqtt' ise gonder, degilse undefined.
      const mqttFields: MqttPayloadFields = isMqtt ? collectMqttPayload(true) : {};
      await onUpdate(editing.id, {
        endpoint: isServer ? "" : endpoint,
        // Topic yalniz MQTT'de anlamli.
        topic: isMqtt && topic.trim() ? topic.trim() : null,
        event_filter: eventFilter,
        // Auth yalniz REST.
        auth_header: isRest && authHeader.trim() ? authHeader.trim() : null,
        auth_token: isRest && authToken.trim() ? authToken.trim() : null,
        // QoS + retain yalniz MQTT.
        qos: isMqtt ? qos : 0,
        retain: isMqtt ? retain : false,
        is_active: isActive,
        listen_host: isServer ? listenHost.trim() || "0.0.0.0" : undefined,
        listen_port: isServer
          ? Number(listenPort) || (isModbus ? Number(MODBUS_DEFAULT_PORT) : 2404)
          : undefined,
        iec104_common_address: isIec104 ? Number(iec104Ca) || 1 : undefined,
        iec104_allowed_peers: isIec104 ? (allowedPeerList.join(",") || null) : undefined,
        ...(isModbus
          ? {
              modbus_mode: modbusMode,
              modbus_unit_id: Number(modbusUnitId) || 1,
              modbus_value_format: modbusValueFormat,
              modbus_word_order: modbusWordOrder,
              modbus_block_stride: modbusStride.trim() ? Number(modbusStride) : null,
              modbus_base_address: Number(modbusBaseAddress) || 0,
              modbus_allowed_peers: allowedPeerList.join(",") || null,
            }
          : {}),
        ...mqttFields,
      });
      setEditing(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : t("common.errorOccurred"));
    }
  };

  const handleDownloadXlsx = async (target: OutboundTarget) => {
    if (!onDownloadIec104Xlsx) return;
    const safeName = target.name.replace(/[^A-Za-z0-9._-]+/g, "_");
    const ts = new Date().toISOString().replace(/[:.]/g, "-").slice(0, 19);
    await onDownloadIec104Xlsx(target.id, `iec104-points-${safeName}-${ts}.xlsx`);
  };

  /** Modbus adres tablosunu CSV indir — SCADA'ya toplu tag girisi icin. */
  const handleDownloadModbusCsv = async (targetId: number, suggestedName: string) => {
    try {
      await downloadModbusPointsCsv(accessToken, targetId, suggestedName);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : t("common.errorOccurred"));
    }
  };

  const handleAutoAssign = async (target: OutboundTarget, overwrite: boolean) => {
    if (!onAutoAssignDeviceCa) return;
    if (overwrite && !await asyncConfirm(t("engineering.outbound.asdu.resetAllTitle") + " — " + t("common.confirm") + "?")) {
      return;
    }
    setAutoAssigning(true);
    setError("");
    try {
      await onAutoAssignDeviceCa(target.id, overwrite);
    } catch (err) {
      setError(err instanceof Error ? err.message : t("common.errorOccurred"));
    } finally {
      setAutoAssigning(false);
    }
  };

  // Whitelist IP yardimcilari — IPv4 dotted decimal validasyonu (CIDR yok).
  const isValidIpv4 = (ip: string): boolean => {
    const m = /^(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})$/.exec(ip);
    if (!m) return false;
    return [m[1], m[2], m[3], m[4]].every((p) => {
      const n = Number(p);
      return Number.isInteger(n) && n >= 0 && n <= 255;
    });
  };

  const handleAddPeerIp = () => {
    const ip = newPeerIp.trim();
    if (!ip) return;
    if (!isValidIpv4(ip)) {
      setPeerError(`Geçersiz IPv4: ${ip}`);
      return;
    }
    if (allowedPeerList.includes(ip)) {
      setPeerError(`${ip} zaten listede.`);
      return;
    }
    setAllowedPeerList((prev) => [...prev, ip]);
    setNewPeerIp("");
    setPeerError("");
  };

  const handleRemovePeerIp = (ip: string) => {
    setAllowedPeerList((prev) => prev.filter((p) => p !== ip));
    setPeerError("");
  };

  const isCreatingIec104 = protocol === "iec104";
  const isEditingIec104 = editing?.protocol === "iec104";
  const showModbusForm = editing ? editing.protocol === "modbus" : protocol === "modbus";
  // Cihaz basina ihtiyac: 75 analog + 6 sayac (2 word) = 87 word (int16) /
  // 162 word (float32). Otomatik blok bunun ustune pay birakir.
  const effectiveStride =
    Number(modbusStride) || (modbusValueFormat === "float32" ? 200 : 100);
  const modbusCapacity =
    modbusMode === "unit" ? 247 : Math.floor(65536 / Math.max(1, effectiveStride));
  // Aktif protokol — edit'te target.protocol (kilitli), create'de kullanici secimi.
  // Form alanlari hangi blokta gosterilecek diye bu degisken kullanilir; kullanici
  // REST sectiyse MQTT alanlari, MQTT sectiyse REST alanlari gizlenir.
  const activeProtocol: Protocol = editing ? (editing.protocol as Protocol) : protocol;
  const isMqttForm = activeProtocol === "mqtt";
  const isRestForm = activeProtocol === "rest";

  // Topic template card icin editing mode + canli preview
  const [topicTemplateEditing, setTopicTemplateEditing] = useState(false);
  // Cihaz-bazli topic: bir cihazin tum sinyalleri (event_filter'a uyan)
  // tek mesaj/JSON payload icinde topic'e gonderilir. {signal} variable'i
  // YOKTUR — sinyaller mesaj govdesindedir, topic'e degil.
  const DEFAULT_MQTT_TEMPLATE =
    "{prefix}/{customer}/{device}/{source}/{datatype}/telemetry";
  const effectiveTemplate = (mqttTopicTemplate || "").trim() || DEFAULT_MQTT_TEMPLATE;
  const topicPreview = useMemo(() => {
    const prefix = (mqttTopicPrefix || "e1").trim();
    const customer = (mqttCustomerId || "default").trim();
    const sampleDevice = (devices?.[0]?.code) || "DEV-001";
    return effectiveTemplate
      .replace(/\{prefix\}/g, prefix)
      .replace(/\{customer\}/g, customer)
      .replace(/\{device\}/g, sampleDevice)
      .replace(/\{source\}/g, "master")
      .replace(/\{datatype\}/g, "analog");
  }, [effectiveTemplate, mqttTopicPrefix, mqttCustomerId, devices]);

  useEffect(() => {
    if (!asduModalTarget) return;
    const draft: Record<string, string> = {};
    for (const d of devices ?? []) {
      const ca = d.iec104CommonAddress;
      draft[d.code] = ca !== null && ca !== undefined ? String(ca) : "";
    }
    setDeviceCaDraft(draft);
    setCaSearch("");
  }, [asduModalTarget, devices]);

  const handleSaveDeviceCa = async (deviceCode: string) => {
    if (!onUpdateDeviceCa) return;
    const raw = (deviceCaDraft[deviceCode] ?? "").trim();
    const value = raw === "" ? null : Number(raw);
    if (value !== null && (!Number.isFinite(value) || value < 0 || value > 65534)) {
      setError(`${deviceCode} için ASDU adresi geçersiz (0-65534 arası bir tam sayı veya boş).`);
      return;
    }
    setError("");
    setSavingDeviceCode(deviceCode);
    try {
      await onUpdateDeviceCa(deviceCode, value);
    } catch (err) {
      setError(err instanceof Error ? err.message : "ASDU adresi kaydedilemedi.");
    } finally {
      setSavingDeviceCode(null);
    }
  };

  const sortedDevices = useMemo(
    () => [...(devices ?? [])].sort((a, b) => a.code.localeCompare(b.code)),
    [devices]
  );

  const filteredCaDevices = useMemo(() => {
    const q = caSearch.trim().toLowerCase();
    if (!q) return sortedDevices;
    return sortedDevices.filter(
      (d) =>
        d.name.toLowerCase().includes(q) ||
        d.code.toLowerCase().includes(q) ||
        String(d.iec104CommonAddress ?? "").includes(q)
    );
  }, [sortedDevices, caSearch]);

  const assignedCount = sortedDevices.filter(
    (d) => d.iec104CommonAddress !== null && d.iec104CommonAddress !== undefined
  ).length;

  // Runtime modal — 5 sn'de bir refresh
  const pollRuntime = useCallback(async () => {
    if (!runtimeTarget || !onFetchIec104Runtime) return;
    setRuntimeLoading(true);
    try {
      const data = await onFetchIec104Runtime(runtimeTarget.id);
      setRuntime(data);
    } catch (err) {
      setError(err instanceof Error ? err.message : t("common.errorOccurred"));
    } finally {
      setRuntimeLoading(false);
    }
  }, [runtimeTarget, onFetchIec104Runtime, t]);

  usePolling({
    enabled: Boolean(runtimeTarget && onFetchIec104Runtime),
    intervalMs: 5000,
    fn: pollRuntime
  });

  // Liste rozetleri 10 sn'de bir
  const iec104Ids = useMemo(
    () => visibleTargets.filter((tg) => tg.protocol === "iec104").map((tg) => tg.id),
    [visibleTargets]
  );

  // Listede hic IEC104 hedefi kalmadiysa rozetleri temizle (poll de durur).
  useEffect(() => {
    if (iec104Ids.length === 0) setRuntimeBadges({});
  }, [iec104Ids]);

  const pollRuntimeBadges = useCallback(async () => {
    if (!onFetchIec104Runtime) return;
    const updates: Record<number, { running: boolean; clients: number }> = {};
    await Promise.all(
      iec104Ids.map(async (id) => {
        try {
          const r = await onFetchIec104Runtime(id);
          updates[id] = { running: r.server_running, clients: r.connected_clients.length };
        } catch {
          // ignore
        }
      })
    );
    setRuntimeBadges(updates);
  }, [iec104Ids, onFetchIec104Runtime]);

  usePolling({
    enabled: Boolean(onFetchIec104Runtime) && iec104Ids.length > 0,
    intervalMs: 10000,
    fn: pollRuntimeBadges
  });

  // ---- Modbus runtime: teshis popup'i 5 sn, liste rozetleri 10 sn ----
  const pollMbRuntime = useCallback(async () => {
    if (!mbRuntimeTarget || !onFetchModbusRuntime) return;
    setMbRuntimeLoading(true);
    try {
      setMbRuntime(await onFetchModbusRuntime(mbRuntimeTarget.id));
    } catch (err) {
      setError(err instanceof Error ? err.message : t("common.errorOccurred"));
    } finally {
      setMbRuntimeLoading(false);
    }
  }, [mbRuntimeTarget, onFetchModbusRuntime, t]);

  usePolling({
    enabled: Boolean(mbRuntimeTarget && onFetchModbusRuntime),
    intervalMs: 5000,
    fn: pollMbRuntime
  });

  const modbusIds = useMemo(
    () => visibleTargets.filter((tg) => tg.protocol === "modbus").map((tg) => tg.id),
    [visibleTargets]
  );

  useEffect(() => {
    if (modbusIds.length === 0) setMbBadges({});
  }, [modbusIds]);

  const pollMbBadges = useCallback(async () => {
    if (!onFetchModbusRuntime) return;
    const updates: Record<number, { running: boolean; clients: number; flowing: boolean }> = {};
    await Promise.all(
      modbusIds.map(async (id) => {
        try {
          const r = await onFetchModbusRuntime(id);
          updates[id] = {
            running: r.server_running,
            clients: r.connected_clients,
            // "Akis var" = register'lara gercekten deger yaziliyor. Iki kanal
            // da sayilir: canli telemetri VE son bilinen deger tazelemesi.
            // Sadece canliya bakmak, degismeyen sinyallerle beslenen (ama
            // SCADA'ya dogru veri veren) bir hedefi "akis yok" gosterirdi.
            flowing:
              r.updates_applied + r.snapshot.seeded + r.snapshot.refreshed > 0
          };
        } catch {
          // ignore
        }
      })
    );
    setMbBadges(updates);
  }, [modbusIds, onFetchModbusRuntime]);

  usePolling({
    enabled: Boolean(onFetchModbusRuntime) && modbusIds.length > 0,
    intervalMs: 10000,
    fn: pollMbBadges
  });

  // REST/MQTT runtime status — 'Durum' sutunu icin 5sn poll
  const hasRestMqttTarget = useMemo(
    () => visibleTargets.some((tg) => tg.protocol !== "iec104"),
    [visibleTargets]
  );

  useEffect(() => {
    if (!hasRestMqttTarget) setOutboundRuntime({});
  }, [hasRestMqttTarget]);

  const pollOutboundRuntime = useCallback(async () => {
    try {
      setOutboundRuntime(await fetchOutboundRuntimeStatus(accessToken));
    } catch {
      // sessiz: bildirim spamlamasin
    }
  }, [accessToken]);

  usePolling({
    enabled: hasRestMqttTarget,
    intervalMs: 5000,
    fn: pollOutboundRuntime
  });

  // Otomatik Topic'ler popup acildiginda backend'den cek
  useEffect(() => {
    if (!autoTopicsTarget) {
      setAutoTopics(null);
      setAutoTopicsSearch("");
      setCopiedTopic(null);
      setCopiedAll(false);
      return;
    }
    let cancelled = false;
    setAutoTopicsLoading(true);
    fetchOutboundAutoTopics(accessToken, autoTopicsTarget.id)
      .then((res) => {
        if (!cancelled) setAutoTopics(res.topics);
      })
      .catch(() => {
        if (!cancelled) setAutoTopics([]);
      })
      .finally(() => {
        if (!cancelled) setAutoTopicsLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [autoTopicsTarget, accessToken]);

  return (
    <section className="tab-panel outbound-panel">
      {/* Filtre cubugu — gorsel olarak alarm kurallari sayfasiyla ayni
          (bkz. styles.css ortak `.rules-v3-toolbar` seçici grubu). */}
      {/* Webhook modunda (Bildirim Ayarlari > Webhook) liste kisa ve bolumun
          kendi basligi zaten var — arama/filtre/sayac gosterilmez, sadece
          sag ustte ekleme butonu kalir. Genel outbound sayfasinda tam
          filtre cubugu calisir. */}
      {!hideToolbar ? (
        <div className="outbound-toolbar">
          <input
            type="search"
            className="outbound-search-input"
            value={search}
            onChange={(event) => setSearch(event.target.value)}
            placeholder={t("engineering.outbound.searchPlaceholder")}
          />
          {protocols.length > 1 ? (
            <div className="outbound-filter-group">
              <select
                value={protocolFilter}
                onChange={(event) => setProtocolFilter(event.target.value as typeof protocolFilter)}
              >
                <option value="all">{t("engineering.outbound.filterAllProtocols")}</option>
                {protocols.map((p) => (
                  <option key={p} value={p}>
                    {p.toUpperCase()}
                  </option>
                ))}
              </select>
            </div>
          ) : null}
          <span className="outbound-count-pill">
            {filteredTargets.length} / {visibleTargets.length}
          </span>
          <button className="add-user-btn outbound-new-btn" onClick={openCreate}>
            + {t(newTargetKey)}
          </button>
        </div>
      ) : null}
      {/* Modal labels */}
      {/* (i18n çevirileri aşağıdaki modal blok ile birlikte) */}

      {(isCreateOpen || editing) && (
        <div className="settings-modal-backdrop">
          <form
            className={`settings-modal ${(isCreatingIec104 || isEditingIec104) ? "iec104-edit-modal" : ""} ${activeProtocol === "mqtt" ? "mqtt-edit-modal" : ""} ${showModbusForm ? "modbus-edit-modal" : ""}`}
            onSubmit={editing ? handleEdit : handleCreate}
          >
            <div className="outbound-modal-head">
              <h3>{editing ? t("engineering.outbound.editTargetModal") : t("engineering.outbound.newTargetModal")}</h3>
              <ActiveSwitch checked={isActive} onChange={setIsActive} />
            </div>
            {/* Name + Protocol — kompakt yan yana iki sutun. IEC104 edit
                modali genisken altta gelen Server | Whitelist grid'i ile
                hizalanir; REST/MQTT modallarinda da daha az dikey alan kaplar. */}
            <div className="outbound-form-headrow">
              {!editing ? (
                <>
                  <label className="outbound-form-headcell">
                    {t("engineering.outbound.form.name")}
                    <input
                      value={name}
                      onChange={(event) => setName(event.target.value)}
                      required
                    />
                  </label>
                  <label className="outbound-form-headcell">
                    {t("engineering.outbound.form.protocol")}
                    <select
                      value={protocol}
                      onChange={(event) => {
                        const next = event.target.value as Protocol;
                        setProtocol(next);
                        setMqttStep(0);
                        // Port alani IEC 104 ile Modbus arasinda PAYLASILIYOR.
                        // Protokol degisince yeni protokolun varsayilanina
                        // cek (Modbus 502, IEC 104 2404) — aksi halde Modbus
                        // secildiginde alanda 2404 kaliyordu.
                        // Operator elle ozel bir port yazdiysa dokunma.
                        setListenPort((prev) =>
                          prev === "" ||
                          prev === MODBUS_DEFAULT_PORT ||
                          prev === IEC104_DEFAULT_PORT
                            ? defaultPortFor(next)
                            : prev
                        );
                      }}
                    >
                      {protocols.includes("rest") ? (
                        <option value="rest">{t("engineering.outbound.proto.rest")}</option>
                      ) : null}
                      {protocols.includes("mqtt") ? (
                        <option value="mqtt">{t("engineering.outbound.proto.mqtt")}</option>
                      ) : null}
                      {protocols.includes("iec104") ? (
                        <option value="iec104">{t("engineering.outbound.proto.iec104")}</option>
                      ) : null}
                      {protocols.includes("modbus") ? (
                        <option value="modbus">{t("engineering.outbound.proto.modbus")}</option>
                      ) : null}
                    </select>
                  </label>
                </>
              ) : (
                <>
                  <label className="outbound-form-headcell">
                    {t("engineering.outbound.form.name")}
                    <input value={editing.name} readOnly disabled />
                  </label>
                  <label className="outbound-form-headcell">
                    {t("engineering.outbound.form.protocol")}
                    <input value={editing.protocol.toUpperCase()} readOnly disabled />
                  </label>
                </>
              )}
            </div>

            {showModbusForm ? (
              <div className="modbus-form">
                {/* --- Adresleme modu: iki yol --- */}
                <div className="modbus-mode-cards">
                  <button
                    type="button"
                    className={`modbus-mode-card ${modbusMode === "block" ? "is-active" : ""}`}
                    onClick={() => setModbusMode("block")}
                  >
                    <strong>{t("engineering.outbound.modbus.modeBlock")}</strong>
                    <span>{t("engineering.outbound.modbus.modeBlockDesc")}</span>
                    <code>
                      {t("engineering.outbound.modbus.modeBlockExample", {
                        stride: effectiveStride,
                        second: effectiveStride,
                        third: effectiveStride * 2
                      })}
                    </code>
                  </button>
                  <button
                    type="button"
                    className={`modbus-mode-card ${modbusMode === "unit" ? "is-active" : ""}`}
                    onClick={() => setModbusMode("unit")}
                  >
                    <strong>{t("engineering.outbound.modbus.modeUnit")}</strong>
                    <span>{t("engineering.outbound.modbus.modeUnitDesc")}</span>
                    <code>{t("engineering.outbound.modbus.modeUnitExample")}</code>
                  </button>
                </div>

                <p className="modbus-capacity">
                  {t("engineering.outbound.modbus.capacity", { count: modbusCapacity })}
                  {modbusValueFormat === "int16" && effectiveStride <= 125 ? (
                    <span className="modbus-capacity-good">
                      {" · "}
                      {t("engineering.outbound.modbus.singleRead")}
                    </span>
                  ) : null}
                </p>

                <div className="modbus-form-grid">
                  <label>
                    {t("engineering.outbound.modbus.listenHost")}
                    <input
                      value={listenHost}
                      onChange={(event) => setListenHost(event.target.value)}
                      placeholder="0.0.0.0"
                    />
                  </label>
                  {/* Aciklama metinleri form icinde satir satir yer kapliyordu;
                      bilgi kaybolmasin diye title (hover) olarak tasindi. */}
                  <label title={t("engineering.outbound.modbus.portHint")}>
                    {t("engineering.outbound.modbus.listenPort")}
                    <input
                      type="number"
                      min={1}
                      max={65535}
                      value={listenPort}
                      onChange={(event) => setListenPort(event.target.value)}
                      placeholder={MODBUS_DEFAULT_PORT}
                    />
                  </label>
                  <label
                    title={
                      modbusValueFormat === "int16"
                        ? t("engineering.outbound.modbus.int16Hint")
                        : t("engineering.outbound.modbus.float32Hint")
                    }
                  >
                    {t("engineering.outbound.modbus.valueFormat")}
                    <select
                      value={modbusValueFormat}
                      onChange={(event) =>
                        setModbusValueFormat(event.target.value as "int16" | "float32")
                      }
                    >
                      <option value="int16">{t("engineering.outbound.modbus.int16")}</option>
                      <option value="float32">{t("engineering.outbound.modbus.float32")}</option>
                    </select>
                  </label>
                  {modbusValueFormat === "float32" ? (
                    <label>
                      {t("engineering.outbound.modbus.wordOrder")}
                      <select
                        value={modbusWordOrder}
                        onChange={(event) =>
                          setModbusWordOrder(event.target.value as "big" | "little")
                        }
                      >
                        <option value="big">{t("engineering.outbound.modbus.wordBig")}</option>
                        <option value="little">{t("engineering.outbound.modbus.wordLittle")}</option>
                      </select>
                    </label>
                  ) : null}
                  {modbusMode === "block" ? (
                    <>
                      <label>
                        {t("engineering.outbound.modbus.unitId")}
                        <input
                          type="number"
                          min={1}
                          max={247}
                          value={modbusUnitId}
                          onChange={(event) => setModbusUnitId(event.target.value)}
                        />
                      </label>
                      {/* Placeholder zaten otomatik degeri (100 / 200) gosteriyor;
                          ayri aciklama satirina gerek yok. */}
                      <label title={t("engineering.outbound.modbus.strideHint")}>
                        {t("engineering.outbound.modbus.stride")}
                        <input
                          type="number"
                          min={1}
                          max={4096}
                          value={modbusStride}
                          onChange={(event) => setModbusStride(event.target.value)}
                          placeholder={String(modbusValueFormat === "float32" ? 200 : 100)}
                        />
                      </label>
                      <label>
                        {t("engineering.outbound.modbus.baseAddress")}
                        <input
                          type="number"
                          min={0}
                          max={65535}
                          value={modbusBaseAddress}
                          onChange={(event) => setModbusBaseAddress(event.target.value)}
                        />
                      </label>
                    </>
                  ) : null}
                </div>

                <p className="modbus-readonly-note">
                  {t("engineering.outbound.modbus.readOnlyNote")}
                </p>

                {/* IP allowlist — Modbus'ta kimlik dogrulama yok, tek koruma bu. */}
                <div className="modbus-peers">
                  <div className="iec104-whitelist-head">
                    <h4 className="iec104-edit-col-title">
                      {t("engineering.outbound.iec104.whitelistTitle")}
                    </h4>
                    <span
                      className={`iec104-whitelist-status ${allowedPeerList.length > 0 ? "iec104-whitelist-status--active" : "iec104-whitelist-status--open"}`}
                    >
                      <span className="status-dot" />
                      {allowedPeerList.length > 0
                        ? t("engineering.outbound.iec104.whitelistActive", { count: allowedPeerList.length })
                        : t("engineering.outbound.iec104.whitelistOff")}
                    </span>
                  </div>
                  <p className="helper-text">
                    {t("engineering.outbound.modbus.whitelistHint")}
                  </p>
                  <div className="iec104-peer-input-row">
                    <input
                      type="text"
                      value={newPeerIp}
                      onChange={(event) => {
                        setNewPeerIp(event.target.value);
                        if (peerError) setPeerError("");
                      }}
                      onKeyDown={(event) => {
                        if (event.key === "Enter") {
                          event.preventDefault();
                          handleAddPeerIp();
                        }
                      }}
                      placeholder={t("engineering.outbound.iec104.ipPlaceholder")}
                    />
                    <button type="button" className="secondary-btn" onClick={handleAddPeerIp}>
                      {t("common.add")}
                    </button>
                  </div>
                  {peerError ? <p className="error-text">{peerError}</p> : null}
                  {allowedPeerList.length > 0 ? (
                    <ul className="iec104-peer-list">
                      {allowedPeerList.map((ip) => (
                        <li key={ip}>
                          <code>{ip}</code>
                          <button
                            type="button"
                            onClick={() =>
                              setAllowedPeerList((prev) => prev.filter((x) => x !== ip))
                            }
                            aria-label={t("common.delete")}
                          >
                            ×
                          </button>
                        </li>
                      ))}
                    </ul>
                  ) : null}
                </div>
              </div>
            ) : null}

            {/* Modbus formu yukarida cizildi; burada REST/MQTT/IEC104 kollari. */}
            {showModbusForm ? null : (isCreatingIec104 || isEditingIec104) ? (
              <div className="iec104-edit-grid">
                <div className="iec104-edit-col">
                  <h4 className="iec104-edit-col-title">{t("engineering.outbound.iec104.serverColTitle")}</h4>
                  <label>
                    {t("engineering.outbound.iec104.listenHost")}
                    <input
                      value={listenHost}
                      onChange={(event) => setListenHost(event.target.value)}
                      placeholder="0.0.0.0"
                    />
                  </label>
                  <label>
                    {t("engineering.outbound.iec104.listenPort")}
                    <input
                      type="number"
                      min={1}
                      max={65535}
                      value={listenPort}
                      onChange={(event) => setListenPort(event.target.value)}
                      placeholder="2404"
                    />
                  </label>
                  <p className="helper-text">
                    {t("engineering.outbound.iec104.deviceCaHint")}
                  </p>
                </div>

                <div className="iec104-edit-col iec104-whitelist-col">
                  <div className="iec104-whitelist-head">
                    <h4 className="iec104-edit-col-title">{t("engineering.outbound.iec104.whitelistTitle")}</h4>
                    <span
                      className={`iec104-whitelist-status ${allowedPeerList.length > 0 ? "iec104-whitelist-status--active" : "iec104-whitelist-status--open"}`}
                    >
                      <span className="status-dot" />
                      {allowedPeerList.length > 0
                        ? t("engineering.outbound.iec104.whitelistActive", { count: allowedPeerList.length })
                        : t("engineering.outbound.iec104.whitelistOff")}
                    </span>
                  </div>
                  <p className="helper-text">
                    {t("engineering.outbound.iec104.whitelistHint")}
                  </p>
                  <div className="iec104-peer-input-row">
                    <input
                      type="text"
                      value={newPeerIp}
                      onChange={(event) => {
                        setNewPeerIp(event.target.value);
                        if (peerError) setPeerError("");
                      }}
                      onKeyDown={(event) => {
                        if (event.key === "Enter") {
                          event.preventDefault();
                          handleAddPeerIp();
                        }
                      }}
                      placeholder={t("engineering.outbound.iec104.ipPlaceholder")}
                      pattern="^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}$"
                    />
                    <button
                      type="button"
                      className="primary-btn"
                      onClick={handleAddPeerIp}
                      disabled={!newPeerIp.trim()}
                    >
                      {t("engineering.outbound.iec104.ipAdd")}
                    </button>
                  </div>
                  {peerError ? <p className="error-text iec104-peer-error">{peerError}</p> : null}

                  <div className="iec104-peer-list">
                    {allowedPeerList.length === 0 ? (
                      <div className="iec104-peer-empty">
                        {t("engineering.outbound.iec104.ipEmpty")}
                      </div>
                    ) : (
                      allowedPeerList.map((ip, idx) => (
                        <div key={ip} className="iec104-peer-chip">
                          <span className="iec104-peer-chip-idx">{idx + 1}</span>
                          <code className="iec104-peer-chip-ip">{ip}</code>
                          <button
                            type="button"
                            className="iec104-peer-chip-remove"
                            onClick={() => handleRemovePeerIp(ip)}
                            title={t("engineering.outbound.iec104.ipRemoveTitle")}
                            aria-label={t("engineering.outbound.iec104.ipRemoveAria", { ip })}
                          >
                            ×
                          </button>
                        </div>
                      ))
                    )}
                  </div>
                </div>
              </div>
            ) : (
              <>
                {/* Endpoint: REST = base URL, MQTT = broker host. Her ikisi icin de gerekli. */}
                <label>
                  {t("engineering.outbound.form.endpoint")}
                  <input
                    value={endpoint}
                    onChange={(event) => setEndpoint(event.target.value)}
                    required
                    placeholder={
                      isMqttForm
                        ? "mqtt-broker.example.com"
                        : isWebhookMode
                        ? "https://n8n.ornek.com/webhook/xxxxxxxx"
                        : "https://musteri.example.com/webhook"
                    }
                  />
                </label>

                {/* Event filter — REST icin global. MQTT'de "Yayin Ayarlari"
                    panelinin altinda gosteriliyor, burada tekrarlamayalim. */}
                {!isMqttForm ? (
                  <label>
                    {t("engineering.outbound.form.eventFilter")}
                    <select
                      value={eventFilter}
                      onChange={(event) =>
                        setEventFilter(event.target.value as "all" | "telemetry" | "alarm")
                      }
                    >
                      <option value="all">{t("engineering.outbound.form.filterAll")}</option>
                      <option value="telemetry">{t("engineering.outbound.form.filterTelemetry")}</option>
                      <option value="alarm">{t("engineering.outbound.form.filterAlarm")}</option>
                    </select>
                  </label>
                ) : null}

                {/* REST'e ozel: auth header + token + receiver kod ornegi. */}
                {isRestForm ? (
                  <>
                    <label>
                      {t("engineering.outbound.form.authHeader")}
                      <input
                        value={authHeader}
                        onChange={(event) => setAuthHeader(event.target.value)}
                        placeholder={t("engineering.outbound.form.authHeaderPlaceholder")}
                      />
                    </label>
                    <label>
                      {t("engineering.outbound.form.authToken")}
                      <input
                        value={authToken}
                        onChange={(event) => setAuthToken(event.target.value)}
                      />
                    </label>
                    {isWebhookMode ? (
                      <div className="webhook-n8n-help">
                        <div className="webhook-n8n-help-head">
                          <span className="material-symbols-outlined">bolt</span>
                          <div>
                            <strong>{t("notifications.settings.webhook.n8nTitle")}</strong>
                            <small>{t("notifications.settings.webhook.n8nHint")}</small>
                          </div>
                        </div>
                        <div className="webhook-payload-sample">
                          <span className="webhook-payload-sample-label">
                            {t("notifications.settings.webhook.payloadTitle")}
                          </span>
                          <pre>{WEBHOOK_SAMPLE_JSON}</pre>
                        </div>
                      </div>
                    ) : null}
                  </>
                ) : null}

                {/* MQTT'ye ozel: 2-sutun panel + topic card + cert upload
                    YENI YAPI:
                    SOL: Baglanti + TLS + Sertifika (TLS aktifken kompakt cert area)
                    SAG: Yayin Ayarlari + Topic Sablonu (tek panel ust uste) */}
                {isMqttForm ? (
                  <div className="mqtt-wizard">
                    <ol className="mqtt-steps">
                      {[
                        t("engineering.outbound.mqtt.section.connection"),
                        t("engineering.outbound.mqtt.topicCard.title")
                      ].map((label, i) => (
                        <li key={i}>
                          <button
                            type="button"
                            className={`mqtt-step ${mqttStep === i ? "is-active" : ""} ${mqttStep > i ? "is-done" : ""}`}
                            onClick={() => setMqttStep(i)}
                          >
                            <span className="mqtt-step-num">
                              {mqttStep > i ? (
                                <span className="material-symbols-outlined">check</span>
                              ) : (
                                i + 1
                              )}
                            </span>
                            <span className="mqtt-step-label">{label}</span>
                          </button>
                        </li>
                      ))}
                    </ol>

                    <div className="mqtt-wizard-body">
                      {mqttStep === 0 ? (
                        <>
                        <div className="mqtt-sec-head">
                          <span className="material-symbols-outlined">link</span>
                          {t("engineering.outbound.mqtt.section.connection")}
                        </div>
                        <div className="mqtt-panel-grid">
                          <label>
                            {t("engineering.outbound.mqtt.port")}
                            <input
                              type="number"
                              min={1}
                              max={65535}
                              placeholder={mqttTlsEnabled ? "8883" : "1883"}
                              value={mqttPort}
                              onChange={(event) => setMqttPort(event.target.value)}
                            />
                          </label>
                          <label>
                            {t("engineering.outbound.mqtt.clientId")}
                            <input
                              value={mqttClientId}
                              onChange={(event) => setMqttClientId(event.target.value)}
                              placeholder={t("engineering.outbound.mqtt.clientIdPlaceholder")}
                            />
                          </label>
                          <label>
                            {t("engineering.outbound.mqtt.username")}
                            <input
                              value={mqttUsername}
                              onChange={(event) => setMqttUsername(event.target.value)}
                              autoComplete="off"
                            />
                          </label>
                          <label>
                            {t("engineering.outbound.mqtt.password")}
                            <input
                              type="password"
                              value={mqttPassword}
                              onChange={(event) => setMqttPassword(event.target.value)}
                              autoComplete="new-password"
                            />
                          </label>
                        </div>
                        <div className="mqtt-sec-head mqtt-sec-head--gap">
                          <span className="material-symbols-outlined">lock</span>
                          {t("engineering.outbound.mqtt.section.tls")}
                        </div>
                        <div className="mqtt-wizard-step-tls">
                          <label className="notify-option mqtt-tls-toggle">
                            <input
                              type="checkbox"
                              checked={mqttTlsEnabled}
                              onChange={(event) => setMqttTlsEnabled(event.target.checked)}
                            />
                            {t("engineering.outbound.mqtt.tlsEnabled")}
                          </label>
                          {mqttTlsEnabled ? (
                            <>
                              <label className="notify-option">
                                <input
                                  type="checkbox"
                                  checked={mqttTlsInsecure}
                                  onChange={(event) => setMqttTlsInsecure(event.target.checked)}
                                />
                                {t("engineering.outbound.mqtt.tlsInsecure")}
                              </label>
                              {editing ? (
                                <div className="mqtt-cert-upload-grid mqtt-cert-upload-grid--compact">
                                  <MqttCertUploader
                                    accessToken={accessToken}
                                    targetId={editing.id}
                                    kind="ca"
                                    currentPath={mqttTlsCaPath}
                                    label={t("engineering.outbound.mqtt.tlsCaPath")}
                                    onUploaded={(path) => setMqttTlsCaPath(path)}
                                    onDeleted={() => setMqttTlsCaPath("")}
                                  />
                                  <MqttCertUploader
                                    accessToken={accessToken}
                                    targetId={editing.id}
                                    kind="cert"
                                    currentPath={mqttTlsCertPath}
                                    label={t("engineering.outbound.mqtt.tlsCertPath")}
                                    onUploaded={(path) => setMqttTlsCertPath(path)}
                                    onDeleted={() => setMqttTlsCertPath("")}
                                  />
                                  <MqttCertUploader
                                    accessToken={accessToken}
                                    targetId={editing.id}
                                    kind="key"
                                    currentPath={mqttTlsKeyPath}
                                    label={t("engineering.outbound.mqtt.tlsKeyPath")}
                                    onUploaded={(path) => setMqttTlsKeyPath(path)}
                                    onDeleted={() => setMqttTlsKeyPath("")}
                                  />
                                </div>
                              ) : (
                                <div className="mqtt-cert-pending-grid">
                                  <PendingCertInput
                                    label={t("engineering.outbound.mqtt.tlsCaPath")}
                                    file={pendingCertCa}
                                    onChange={setPendingCertCa}
                                    t={t}
                                  />
                                  <PendingCertInput
                                    label={t("engineering.outbound.mqtt.tlsCertPath")}
                                    file={pendingCertCert}
                                    onChange={setPendingCertCert}
                                    t={t}
                                  />
                                  <PendingCertInput
                                    label={t("engineering.outbound.mqtt.tlsKeyPath")}
                                    file={pendingCertKey}
                                    onChange={setPendingCertKey}
                                    t={t}
                                  />
                                  <small className="mqtt-cert-pending-hint">
                                    {t("engineering.outbound.mqtt.certUploadOnSave")}
                                  </small>
                                </div>
                              )}
                            </>
                          ) : null}
                        </div>
                        <div className="mqtt-sec-head mqtt-sec-head--gap">
                          <span className="material-symbols-outlined">send</span>
                          {t("engineering.outbound.mqtt.section.publish")}
                        </div>
                          <div className="mqtt-panel-grid">
                            <label>
                              {t("engineering.outbound.mqtt.publishIntervalSec")}
                              <input
                                type="number"
                                min={0}
                                max={3600}
                                value={mqttPublishInterval}
                                onChange={(event) => setMqttPublishInterval(event.target.value)}
                              />
                            </label>
                            <label>
                              {t("engineering.outbound.form.qos")}
                              <input
                                type="number"
                                min={0}
                                max={2}
                                value={qos}
                                onChange={(event) => setQos(Number(event.target.value) || 0)}
                              />
                            </label>
                            <label>
                              {t("engineering.outbound.form.eventFilter")}
                              <select
                                value={eventFilter}
                                onChange={(event) =>
                                  setEventFilter(event.target.value as "all" | "telemetry" | "alarm")
                                }
                              >
                                <option value="all">{t("engineering.outbound.form.filterAll")}</option>
                                <option value="telemetry">{t("engineering.outbound.form.filterTelemetry")}</option>
                                <option value="alarm">{t("engineering.outbound.form.filterAlarm")}</option>
                              </select>
                            </label>
                            <label className="notify-option">
                              <input
                                type="checkbox"
                                checked={retain}
                                onChange={(event) => setRetain(event.target.checked)}
                              />
                              {t("engineering.outbound.form.retain")}
                            </label>
                          </div>
                          <button
                            type="button"
                            className="link-btn mqtt-advanced-toggle"
                            onClick={() => setShowMqttAdvanced((v) => !v)}
                          >
                            <span className="material-symbols-outlined">
                              {showMqttAdvanced ? "expand_less" : "expand_more"}
                            </span>
                            {showMqttAdvanced
                              ? t("engineering.outbound.mqtt.hideAdvanced")
                              : t("engineering.outbound.mqtt.showAdvanced")}
                          </button>
                          {showMqttAdvanced ? (
                            <div className="mqtt-panel-grid mqtt-advanced-grid">
                              <label>
                                {t("engineering.outbound.mqtt.keepaliveSec")}
                                <input
                                  type="number"
                                  min={5}
                                  max={3600}
                                  value={mqttKeepalive}
                                  onChange={(event) => setMqttKeepalive(event.target.value)}
                                />
                              </label>
                              <label>
                                {t("engineering.outbound.mqtt.connectTimeoutSec")}
                                <input
                                  type="number"
                                  min={1}
                                  max={120}
                                  value={mqttConnectTimeout}
                                  onChange={(event) => setMqttConnectTimeout(event.target.value)}
                                />
                              </label>
                            </div>
                          ) : null}
                        </>
                      ) : null}

                      {mqttStep === 1 ? (
                        <div className="mqtt-topic-card mqtt-topic-card--full">
                          <div className="mqtt-topic-card-head">
                            <div>
                              <div className="mqtt-topic-card-title">
                                <span className="material-symbols-outlined">topic</span>
                                {t("engineering.outbound.mqtt.topicCard.title")}
                              </div>
                              <small>{t("engineering.outbound.mqtt.topicCard.hint")}</small>
                            </div>
                            <button
                              type="button"
                              className="link-btn"
                              onClick={() => setTopicTemplateEditing((v) => !v)}
                            >
                              <span className="material-symbols-outlined">
                                {topicTemplateEditing ? "check" : "edit"}
                              </span>
                              {topicTemplateEditing ? t("common.done") : t("common.edit")}
                            </button>
                          </div>

                          <div className="mqtt-topic-card-body">
                            <div className="mqtt-topic-meta-grid">
                              <label>
                                {t("engineering.outbound.mqtt.topicPrefix")}
                                <input
                                  value={mqttTopicPrefix}
                                  onChange={(event) => setMqttTopicPrefix(event.target.value)}
                                  placeholder="e1"
                                />
                              </label>
                              <label>
                                {t("engineering.outbound.mqtt.customerId")}
                                <input
                                  value={mqttCustomerId}
                                  onChange={(event) => setMqttCustomerId(event.target.value)}
                                  placeholder="default"
                                />
                              </label>
                            </div>

                            {topicTemplateEditing ? (
                              <>
                                <label className="mqtt-topic-template-edit">
                                  {t("engineering.outbound.mqtt.topicTemplate")}
                                  <input
                                    value={mqttTopicTemplate}
                                    onChange={(event) => setMqttTopicTemplate(event.target.value)}
                                    placeholder={DEFAULT_MQTT_TEMPLATE}
                                  />
                                </label>
                                <div className="mqtt-topic-vars">
                                  {["{prefix}", "{customer}", "{device}", "{source}", "{datatype}"].map(
                                    (v) => (
                                      <button
                                        type="button"
                                        key={v}
                                        className="mqtt-topic-var-chip"
                                        onClick={() => {
                                          const base = (mqttTopicTemplate || "").trim();
                                          setMqttTopicTemplate(base ? `${base}/${v}` : v);
                                        }}
                                      >
                                        {v}
                                      </button>
                                    )
                                  )}
                                </div>
                              </>
                            ) : (
                              <div className="mqtt-topic-template-display">
                                <code>{effectiveTemplate}</code>
                              </div>
                            )}

                            <div className="mqtt-topic-preview">
                              <small>{t("engineering.outbound.mqtt.topicCard.previewLabel")}:</small>
                              <code>{topicPreview}</code>
                            </div>
                          </div>
                        </div>
                      ) : null}
                    </div>

                  </div>
                ) : null}
              </>
            )}
            <div className="settings-actions">
              <button type="button" onClick={() => (editing ? setEditing(null) : setCreateOpen(false))}>
                {t("engineering.outbound.form.cancel")}
              </button>
              <button type="submit">{editing ? t("engineering.outbound.form.update") : t("engineering.outbound.form.save")}</button>
            </div>
          </form>
        </div>
      )}

      {/* Cihaz ASDU adresleri popup'i — buyuk modal, modern tablo */}
      {asduModalTarget && onUpdateDeviceCa ? (
        <div className="settings-modal-backdrop">
          <div className="settings-modal asdu-modal">
            <div className="asdu-modal-head">
              <div>
                <h3 className="asdu-modal-title">{t("engineering.outbound.asdu.title")}</h3>
                <p className="asdu-modal-sub">
                  {t("engineering.outbound.asdu.subtitle", { name: asduModalTarget.name, count: sortedDevices.length, assigned: assignedCount })}
                </p>
              </div>
              <button
                type="button"
                className="asdu-modal-close"
                onClick={() => setAsduModalTarget(null)}
                aria-label={t("common.close")}
              >
                ×
              </button>
            </div>

            <div className="asdu-toolbar">
              <input
                className="asdu-search"
                type="search"
                placeholder={t("engineering.outbound.asdu.search")}
                value={caSearch}
                onChange={(event) => setCaSearch(event.target.value)}
              />
              {onAutoAssignDeviceCa ? (
                <div className="asdu-toolbar-actions">
                  <button
                    type="button"
                    className="secondary-btn"
                    disabled={autoAssigning}
                    onClick={() => void handleAutoAssign(asduModalTarget, false)}
                    title={t("engineering.outbound.asdu.autoFillTitle")}
                  >
                    {autoAssigning ? t("engineering.outbound.asdu.autoFillBusy") : t("engineering.outbound.asdu.autoFill")}
                  </button>
                  <button
                    type="button"
                    className="secondary-btn"
                    disabled={autoAssigning}
                    onClick={() => void handleAutoAssign(asduModalTarget, true)}
                    title={t("engineering.outbound.asdu.resetAllTitle")}
                  >
                    {t("engineering.outbound.asdu.resetAll")}
                  </button>
                </div>
              ) : null}
            </div>

            <div className="asdu-table-wrap">
              <table className="asdu-table">
                <thead>
                  <tr>
                    <th scope="col" style={{ width: 60 }}>#</th>
                    <th scope="col">{t("engineering.outbound.asdu.tableDevice")}</th>
                    <th scope="col">{t("engineering.outbound.asdu.tableCode")}</th>
                    <th scope="col" style={{ width: 160 }}>{t("engineering.outbound.asdu.tableAsdu")}</th>
                    <th scope="col" style={{ width: 110 }}>{t("engineering.outbound.asdu.tableActions")}</th>
                  </tr>
                </thead>
                <tbody>
                  {filteredCaDevices.length === 0 ? (
                    <tr>
                      <td colSpan={5} className="asdu-empty">
                        {sortedDevices.length === 0 ? t("engineering.outbound.asdu.noDevices") : t("engineering.outbound.asdu.noResults")}
                      </td>
                    </tr>
                  ) : null}
                  {filteredCaDevices.map((d, idx) => {
                    const draftValue = deviceCaDraft[d.code] ?? "";
                    const dbValue = d.iec104CommonAddress ?? null;
                    const dbStr = dbValue !== null ? String(dbValue) : "";
                    const dirty = draftValue !== dbStr;
                    const isSaving = savingDeviceCode === d.code;
                    const hasAddr = dbValue !== null;
                    return (
                      <tr key={d.code} className={hasAddr ? "asdu-row--assigned" : "asdu-row--unassigned"}>
                        <td className="asdu-row-index">{idx + 1}</td>
                        <td className="asdu-row-name">{d.name}</td>
                        <td><code>{d.code}</code></td>
                        <td>
                          <input
                            type="number"
                            min={0}
                            max={65534}
                            value={draftValue}
                            placeholder="—"
                            onChange={(event) =>
                              setDeviceCaDraft((prev) => ({
                                ...prev,
                                [d.code]: event.target.value
                              }))
                            }
                            disabled={isSaving}
                            className={dirty ? "asdu-input--dirty" : ""}
                          />
                        </td>
                        <td>
                          <button
                            type="button"
                            className="primary-btn asdu-save-btn"
                            disabled={!dirty || isSaving}
                            onClick={() => void handleSaveDeviceCa(d.code)}
                          >
                            {isSaving ? t("engineering.outbound.asdu.saving") : t("engineering.outbound.asdu.save")}
                          </button>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>

            <div className="asdu-modal-foot">
              <button type="button" className="secondary-btn" onClick={() => setAsduModalTarget(null)}>
                {t("engineering.outbound.asdu.close")}
              </button>
            </div>
          </div>
        </div>
      ) : null}

      {/* Runtime status popup'i */}
      {runtimeTarget ? (
        <div className="settings-modal-backdrop">
          <div className="settings-modal iec104-runtime-modal">
            <h3>{t("engineering.outbound.runtime.title", { name: runtimeTarget.name })}</h3>
            {runtimeLoading && !runtime ? <p className="helper-text">{t("engineering.outbound.runtime.loading")}</p> : null}
            {runtime ? (
              <>
                <div className="iec104-runtime-summary">
                  <div>
                    <span className={`status-dot ${runtime.server_running ? "status-dot--ok" : "status-dot--bad"}`} />
                    <strong>{t("engineering.outbound.runtime.server")}</strong>{" "}
                    {runtime.server_running ? t("engineering.outbound.runtime.running") : t("engineering.outbound.runtime.stopped")}
                  </div>
                  <div>
                    <strong>{t("engineering.outbound.runtime.whitelistLabel")}</strong>{" "}
                    {runtime.whitelist_active
                      ? t("engineering.outbound.runtime.whitelistActive", { count: runtime.allowed_peers.length })
                      : t("engineering.outbound.runtime.whitelistOff")}
                  </div>
                  <div>
                    <strong>{t("engineering.outbound.runtime.connected")}</strong> {runtime.connected_clients.length}
                  </div>
                </div>
                {runtime.whitelist_active ? (
                  <details className="iec104-runtime-allowed">
                    <summary>{t("engineering.outbound.runtime.allowedSummary", { count: runtime.allowed_peers.length })}</summary>
                    <ul>
                      {runtime.allowed_peers.map((ip) => (
                        <li key={ip}><code>{ip}</code></li>
                      ))}
                    </ul>
                  </details>
                ) : null}
                <h4 className="iec104-runtime-clients-title">{t("engineering.outbound.runtime.connected")}</h4>
                <table className="values-table">
                  <thead>
                    <tr>
                      <th scope="col">Peer</th>
                      <th scope="col">{t("common.status")}</th>
                      <th scope="col">{t("common.time")}</th>
                    </tr>
                  </thead>
                  <tbody>
                    {runtime.connected_clients.length === 0 ? (
                      <tr>
                        <td colSpan={3} className="helper-text">{t("common.noData")}</td>
                      </tr>
                    ) : null}
                    {runtime.connected_clients.map((c) => (
                      <tr key={c.peer}>
                        <td><code>{c.peer}</code></td>
                        <td>{c.started ? "STARTDT" : "—"}</td>
                        <td>
                          {c.connected_at
                            ? new Date(c.connected_at).toLocaleString()
                            : "—"}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </>
            ) : null}
            <div className="settings-actions">
              <button type="button" onClick={() => { setRuntimeTarget(null); setRuntime(null); }}>
                {t("engineering.outbound.asdu.close")}
              </button>
            </div>
          </div>
        </div>
      ) : null}

      {/* Modbus runtime + teshis popup'i */}
      {mbRuntimeTarget ? (
        <div className="settings-modal-backdrop">
          <div className="settings-modal iec104-runtime-modal">
            <h3>{t("engineering.outbound.mbRuntime.title", { name: mbRuntimeTarget.name })}</h3>
            {mbRuntimeLoading && !mbRuntime ? (
              <p className="helper-text">{t("engineering.outbound.runtime.loading")}</p>
            ) : null}
            {mbRuntime ? (
              <>
                {(() => {
                  // Sayaclardan tek cumlelik teshis — "deger neden yok"
                  // sorusunun cevabi operatorun onunde dursun.
                  const c = mbRuntime.consumer;
                  const snap = mbRuntime.snapshot;
                  // Register'a yazan IKI kanal var: canli akis (updates_applied)
                  // ve son bilinen deger tazelemesi (snapshot). "Hic deger yok"
                  // teshisi ikisinin TOPLAMINA bakmali; aksi halde tazeleme
                  // register'lari doldurmus olsa bile ekran "yazilmadi" derdi.
                  const yazilan = mbRuntime.updates_applied + snap.seeded + snap.refreshed;
                  let key = "ok";
                  let tone: "ok" | "warn" | "bad" = "ok";
                  if (!mbRuntime.worker_reachable) {
                    key = "workerDown"; tone = "bad";
                  } else if (!mbRuntime.server_running) {
                    key = "serverDown"; tone = "bad";
                  } else if (c.last_error) {
                    key = "consumerError"; tone = "bad";
                  } else if (yazilan === 0 && mbRuntime.updates_unmapped > 0) {
                    key = "unmapped"; tone = "warn";
                  } else if (yazilan === 0 && mbRuntime.updates_uncoercible > 0) {
                    key = "uncoercible"; tone = "warn";
                  } else if (yazilan === 0 && !snap.enabled) {
                    key = "snapshotDisabled"; tone = "warn";
                  } else if (yazilan === 0 && c.messages_processed === 0) {
                    key = "noTelemetry"; tone = "warn";
                  } else if (yazilan === 0) {
                    key = "noWrites"; tone = "warn";
                  } else if (mbRuntime.updates_applied === 0) {
                    key = "snapshotOnly"; tone = "warn";
                  } else if (mbRuntime.requests_served === 0) {
                    key = "noReads"; tone = "warn";
                  }
                  return (
                    <p className={`mb-runtime-diagnosis mb-runtime-diagnosis--${tone}`}>
                      {t(`engineering.outbound.mbRuntime.diag.${key}`)}
                      {key === "workerDown" && mbRuntime.worker_error ? (
                        <> <code>{mbRuntime.worker_error}</code></>
                      ) : null}
                      {key === "consumerError" && c.last_error ? (
                        <> <code>{c.last_error}</code></>
                      ) : null}
                    </p>
                  );
                })()}
                <div className="iec104-runtime-summary">
                  <div>
                    <span className={`status-dot ${mbRuntime.server_running ? "status-dot--ok" : "status-dot--bad"}`} />
                    <strong>{t("engineering.outbound.runtime.server")}</strong>{" "}
                    {mbRuntime.server_running
                      ? t("engineering.outbound.runtime.running")
                      : t("engineering.outbound.runtime.stopped")}
                    {mbRuntime.listen ? <> · <code>{mbRuntime.listen}</code></> : null}
                  </div>
                  <div>
                    <strong>{t("engineering.outbound.mbRuntime.pointsLabel")}</strong>{" "}
                    {t("engineering.outbound.mbRuntime.pointsValue", {
                      points: mbRuntime.points,
                      units: mbRuntime.units
                    })}
                  </div>
                  <div>
                    <strong>{t("engineering.outbound.runtime.connected")}</strong> {mbRuntime.connected_clients}
                  </div>
                </div>
                <table className="values-table mb-runtime-counters">
                  <tbody>
                    <tr>
                      <td>{t("engineering.outbound.mbRuntime.messagesProcessed")}</td>
                      <td>{mbRuntime.consumer.messages_processed.toLocaleString()}</td>
                    </tr>
                    <tr>
                      <td>{t("engineering.outbound.mbRuntime.updatesApplied")}</td>
                      <td>{mbRuntime.updates_applied.toLocaleString()}</td>
                    </tr>
                    <tr>
                      <td>{t("engineering.outbound.mbRuntime.snapshotWrites")}</td>
                      <td>
                        {mbRuntime.snapshot.enabled
                          ? (mbRuntime.snapshot.seeded + mbRuntime.snapshot.refreshed).toLocaleString()
                          : t("engineering.outbound.mbRuntime.snapshotOff")}
                      </td>
                    </tr>
                    <tr>
                      <td>{t("engineering.outbound.mbRuntime.updatesUnmapped")}</td>
                      <td>{mbRuntime.updates_unmapped.toLocaleString()}</td>
                    </tr>
                    <tr>
                      <td>{t("engineering.outbound.mbRuntime.requestsServed")}</td>
                      <td>{mbRuntime.requests_served.toLocaleString()}</td>
                    </tr>
                    {mbRuntime.rejected_peers > 0 ? (
                      <tr>
                        <td>{t("engineering.outbound.mbRuntime.rejectedPeers")}</td>
                        <td>{mbRuntime.rejected_peers.toLocaleString()}</td>
                      </tr>
                    ) : null}
                    {mbRuntime.updates_uncoercible > 0 ? (
                      <tr>
                        <td>{t("engineering.outbound.mbRuntime.updatesUncoercible")}</td>
                        <td>{mbRuntime.updates_uncoercible.toLocaleString()}</td>
                      </tr>
                    ) : null}
                    {mbRuntime.consumer.bad_quality_count > 0 ? (
                      <tr>
                        <td>{t("engineering.outbound.mbRuntime.badQualityCount")}</td>
                        <td>{mbRuntime.consumer.bad_quality_count.toLocaleString()}</td>
                      </tr>
                    ) : null}
                  </tbody>
                </table>
                {mbRuntime.consumer.last_sync_error ? (
                  <p className="helper-text">
                    {t("engineering.outbound.mbRuntime.lastSyncError")}: <code>{mbRuntime.consumer.last_sync_error}</code>
                  </p>
                ) : null}
              </>
            ) : null}
            <div className="settings-actions">
              <button type="button" onClick={() => { setMbRuntimeTarget(null); setMbRuntime(null); }}>
                {t("engineering.outbound.asdu.close")}
              </button>
            </div>
          </div>
        </div>
      ) : null}

      {isWebhookMode ? (
        <div className="webhook-list">
          {filteredTargets.length === 0 ? (
            <div className="webhook-empty">
              <span className="material-symbols-outlined">hub</span>
              <h4>{t(titleKey)}</h4>
              <p>{t("engineering.outbound.emptyHint")}</p>
              <button type="button" className="primary-btn" onClick={openCreate}>
                + {t(newTargetKey)}
              </button>
            </div>
          ) : (
            filteredTargets.map((item) => {
              const rt = outboundRuntime[item.id];
              const ok = !!rt?.last_success_at;
              const last = rt?.last_success_at || rt?.last_failure_at;
              const lastStr = last ? new Date(last).toLocaleTimeString() : null;
              return (
                <div
                  key={item.id}
                  className={`webhook-card ${item.is_active ? "" : "webhook-card--inactive"}`}
                >
                  <div className="webhook-card-icon">
                    <span className="material-symbols-outlined">hub</span>
                  </div>
                  <div className="webhook-card-info">
                    {/* Durum rozeti SADECE sag taraftaki runtime slotunda
                        gosterilir; burada tekrar etmiyoruz (eskiden ayni
                        kartta iki kez "Aktif" yaziyordu). */}
                    <div className="webhook-card-name">
                      <strong>{item.name}</strong>
                    </div>
                    <code className="webhook-card-url">{item.endpoint}</code>
                    {webhookTestResults[item.id] ? (
                      <span
                        className={`webhook-test-result ${webhookTestResults[item.id].ok ? "is-ok" : "is-fail"}`}
                      >
                        <span className="material-symbols-outlined">
                          {webhookTestResults[item.id].ok ? "check_circle" : "error"}
                        </span>
                        {webhookTestResults[item.id].detail}
                      </span>
                    ) : null}
                  </div>
                  {/* Tek durum rozeti: once konfigurasyon (Pasif), sonra
                      runtime (Hata / Aktif). Zaman damgasi son basarili ya da
                      son basarisiz denemenin saati. */}
                  <div className="webhook-card-runtime">
                    {!item.is_active ? (
                      <span className="outbound-runtime-pill outbound-runtime-pill--off">
                        <span className="status-dot" />
                        {t("common.inactive")}
                      </span>
                    ) : rt?.last_failure_at && !ok ? (
                      <span
                        className="outbound-runtime-pill outbound-runtime-pill--bad"
                        title={rt.last_error || undefined}
                      >
                        <span className="status-dot" />
                        {t("engineering.outbound.runtime.webhookFailed")}
                        {lastStr ? <small> · {lastStr}</small> : null}
                      </span>
                    ) : (
                      <span
                        className="outbound-runtime-pill outbound-runtime-pill--ok"
                        title={
                          lastStr
                            ? `${t("engineering.outbound.runtime.lastSuccess")}: ${lastStr}`
                            : undefined
                        }
                      >
                        <span className="status-dot" />
                        {t("common.active")}
                        {lastStr ? <small> · {lastStr}</small> : null}
                      </span>
                    )}
                  </div>
                  <div className="webhook-card-actions">
                    <button
                      type="button"
                      className="secondary-btn action-btn"
                      disabled={webhookTestingId === item.id}
                      onClick={() => void runWebhookTest(item)}
                      title={t("notifications.settings.webhook.testHint")}
                    >
                      {webhookTestingId === item.id
                        ? t("notifications.settings.webhook.testSending")
                        : t("notifications.settings.webhook.testBtn")}
                    </button>
                    <button type="button" className="edit-btn action-btn" onClick={() => openEdit(item)}>
                      {t("common.edit")}
                    </button>
                    <button
                      type="button"
                      className="danger-btn action-btn"
                      onClick={async () => {
                        if (await asyncConfirm(t("engineering.outbound.confirmDelete", { name: item.name }))) {
                          void onDelete(item.id).catch((err: unknown) => {
                            setError(err instanceof Error ? err.message : t("common.errorOccurred"));
                          });
                        }
                      }}
                    >
                      {t("common.delete")}
                    </button>
                  </div>
                </div>
              );
            })
          )}
        </div>
      ) : filteredTargets.length === 0 ? (
        <div className="outbound-empty">
          <span className="material-symbols-outlined">cell_tower</span>
          <h4>{t(titleKey)}</h4>
          <p>{t("engineering.outbound.emptyHint")}</p>
          <button type="button" className="primary-btn" onClick={openCreate}>
            + {t(newTargetKey)}
          </button>
        </div>
      ) : (
      <>
      {/* Modern liste tablosu — Aktif sütun en başta, "Filtre" kaldırıldı,
          "Endpoint / Listen" basitleştirildi (default CA ekrandan kaldırıldı). */}
      <div className="outbound-modern-table-wrap">
        <table className="values-table outbound-modern-table">
          <thead>
            <tr>
              {/* 1. sutun hedefin ACIK/KAPALI olmasini gosterir (is_active).
                  Eskiden yanlislikla `runtime.connected` ("Bagli SCADA:")
                  anahtarina baglanmisti. */}
              <th scope="col" style={{ width: 90 }}>{t("engineering.outbound.table.active")}</th>
              <th scope="col">{t("engineering.outbound.table.name")}</th>
              <th scope="col" style={{ width: 110 }}>{t("engineering.outbound.table.protocol")}</th>
              <th scope="col">{t("engineering.outbound.table.endpoint")}</th>
              {/* Calisma/baglanti durumu — 1. sutundan ayrilsin diye "Baglanti". */}
              <th scope="col" style={{ width: 130 }}>{t("engineering.outbound.table.connection")}</th>
              <th scope="col" className="actions-header">{t("engineering.outbound.table.actions")}</th>
            </tr>
          </thead>
          <tbody>
            {filteredTargets.map((item) => {
              const isIec = item.protocol === "iec104";
              // Modbus da IEC 104 gibi TCP SUNUCUSU — adres kolonunda
              // `endpoint` (bos) yerine dinlenen host:port gosterilmeli.
              const isServerRow = isIec || item.protocol === "modbus";
              // listen_host wildcard ise (0.0.0.0 / :: / bos) istemci buraya
              // dogrudan baglanamaz; tarayicinin eristigi host'u goster (ayni
              // sunucu). Operator spesifik bir IP girdiyse onu aynen birak.
              const rawHost = (item.listen_host ?? "0.0.0.0").trim();
              const isWildcardHost = WILDCARD_HOSTS.has(rawHost);
              const connectHost =
                isWildcardHost && typeof window !== "undefined"
                  ? window.location.hostname
                  : rawHost || "0.0.0.0";
              const endpointDisplay = isServerRow
                ? `${connectHost}:${
                    item.listen_port ?? Number(defaultPortFor(item.protocol as Protocol))
                  }`
                : item.endpoint;
              const showAddrHint = isServerRow && isWildcardHost;
              const badge = isIec ? runtimeBadges[item.id] : undefined;
              return (
                <tr key={item.id} className={item.is_active ? "" : "outbound-row--inactive"}>
                  <td>
                    <span
                      className={`outbound-active-pill ${item.is_active ? "outbound-active-pill--on" : "outbound-active-pill--off"}`}
                    >
                      <span className="status-dot" />
                      {item.is_active ? t("common.active") : t("common.inactive")}
                    </span>
                  </td>
                  <td className="outbound-name-cell">{item.name}</td>
                  <td>
                    <span className={`outbound-proto-badge outbound-proto-${item.protocol}`}>
                      {item.protocol.toUpperCase()}
                    </span>
                  </td>
                  <td className="outbound-endpoint-cell">
                    <code>{endpointDisplay}</code>
                    {showAddrHint ? (
                      <span
                        className="outbound-addr-hint"
                        title={t("engineering.outbound.table.iec104AddrHint")}
                        aria-label={t("engineering.outbound.table.iec104AddrHint")}
                      >
                        {" "}ⓘ
                      </span>
                    ) : null}
                  </td>
                  <td>
                    {isIec && badge ? (
                      <button
                        type="button"
                        className={`iec104-badge ${badge.running ? "iec104-badge--ok" : "iec104-badge--bad"}`}
                        onClick={() => onFetchIec104Runtime && setRuntimeTarget(item)}
                        title={t("engineering.outbound.scadaListTitle")}
                      >
                        <span className="status-dot" />
                        {badge.running
                          ? t("engineering.outbound.scadaConnected", { count: badge.clients })
                          : t("engineering.outbound.scadaOff")}
                      </button>
                    ) : item.protocol === "mqtt" ? (
                      (() => {
                        const rt = outboundRuntime[item.id];
                        const connected = !!rt?.connected;
                        const last = rt?.last_publish_at || rt?.last_success_at;
                        const lastStr = last
                          ? new Date(last).toLocaleTimeString()
                          : null;
                        return (
                          <span
                            className={`outbound-runtime-pill ${connected ? "outbound-runtime-pill--ok" : "outbound-runtime-pill--off"}`}
                            title={
                              rt?.last_error
                                ? `${t("engineering.outbound.runtime.lastError")}: ${rt.last_error}`
                                : lastStr
                                ? `${t("engineering.outbound.runtime.lastPublish")}: ${lastStr}`
                                : undefined
                            }
                          >
                            <span className="status-dot" />
                            {connected
                              ? t("engineering.outbound.runtime.mqttConnected")
                              : t("engineering.outbound.runtime.mqttDisconnected")}
                            {lastStr ? <small> · {lastStr}</small> : null}
                          </span>
                        );
                      })()
                    ) : item.protocol === "rest" ? (
                      (() => {
                        const rt = outboundRuntime[item.id];
                        const ok = !!rt?.last_success_at;
                        const last = rt?.last_success_at || rt?.last_failure_at;
                        const lastStr = last
                          ? new Date(last).toLocaleTimeString()
                          : null;
                        return ok ? (
                          <span
                            className="outbound-runtime-pill outbound-runtime-pill--ok"
                            title={`${t("engineering.outbound.runtime.lastSuccess")}: ${lastStr}`}
                          >
                            <span className="status-dot" />
                            {t("engineering.outbound.runtime.webhookOk")}
                            {lastStr ? <small> · {lastStr}</small> : null}
                          </span>
                        ) : rt?.last_failure_at ? (
                          <span
                            className="outbound-runtime-pill outbound-runtime-pill--bad"
                            title={rt.last_error || undefined}
                          >
                            <span className="status-dot" />
                            {t("engineering.outbound.runtime.webhookFailed")}
                          </span>
                        ) : (
                          <span className="helper-text">—</span>
                        );
                      })()
                    ) : item.protocol === "modbus" && onFetchModbusRuntime ? (
                      (() => {
                        const mb = mbBadges[item.id];
                        const cls = !mb
                          ? ""
                          : !mb.running
                          ? "iec104-badge--bad"
                          : mb.flowing
                          ? "iec104-badge--ok"
                          : "iec104-badge--warn";
                        const label = !mb
                          ? t("engineering.outbound.mbRuntime.badgeLoading")
                          : !mb.running
                          ? t("engineering.outbound.scadaOff")
                          : mb.flowing
                          ? t("engineering.outbound.scadaConnected", { count: mb.clients })
                          : t("engineering.outbound.mbRuntime.badgeNoFlow");
                        return (
                          <button
                            type="button"
                            className={`iec104-badge ${cls}`}
                            onClick={() => setMbRuntimeTarget(item)}
                            title={t("engineering.outbound.mbRuntime.title", { name: item.name })}
                          >
                            <span className="status-dot" />
                            {label}
                          </button>
                        );
                      })()
                    ) : (
                      <span className="helper-text">—</span>
                    )}
                  </td>
                  <td className="actions-cell">
                    {item.protocol === "modbus" ? (
                      <button
                        type="button"
                        className="secondary-btn action-btn"
                        title={t("engineering.outbound.modbus.planBtnTitle")}
                        onClick={() => setPlanTargetId(item.id)}
                      >
                        {t("engineering.outbound.modbus.planBtn")}
                      </button>
                    ) : null}
                    {isIec && onUpdateDeviceCa ? (
                      <button
                        type="button"
                        className="secondary-btn action-btn"
                        title={t("engineering.outbound.asduDevicesBtnTitle")}
                        onClick={() => setAsduModalTarget(item)}
                      >
                        {t("engineering.outbound.asduDevicesBtn")}
                      </button>
                    ) : null}
                    {isIec && onDownloadIec104Xlsx ? (
                      <button
                        type="button"
                        className="secondary-btn action-btn"
                        title={t("engineering.outbound.downloadXlsxTitle")}
                        onClick={() => void handleDownloadXlsx(item)}
                      >
                        {t("engineering.outbound.downloadXlsx")}
                      </button>
                    ) : null}
                    {/* Eski "CSV" butonu KALDIRILDI — ayni sinyal listesini
                        Excel butonu zaten indiriyordu, iki buton yan yana
                        duruyordu. CSV ucu (onDownloadIec104Points) backend'de
                        duruyor; gerekirse geri eklenebilir. */}
                    {item.protocol === "mqtt" ? (
                      <>
                        <button
                          type="button"
                          className="secondary-btn action-btn"
                          title={t("engineering.outbound.mqtt.autoTopicsBtnTitle")}
                          onClick={() => setAutoTopicsTarget(item)}
                        >
                          {t("engineering.outbound.mqtt.autoTopicsBtn")}
                        </button>
                        <button
                          type="button"
                          className="secondary-btn action-btn"
                          title={t("engineering.outbound.mqtt.mappingBtnTitle")}
                          onClick={() => setMappingModalTarget(item)}
                        >
                          {t("engineering.outbound.mqtt.mappingBtn")}
                        </button>
                      </>
                    ) : null}
                    <button className="edit-btn action-btn" onClick={() => openEdit(item)}>
                      {t("common.edit")}
                    </button>
                    <button
                      className="danger-btn action-btn"
                      onClick={async () => {
                        if (await asyncConfirm(t("engineering.outbound.confirmDelete", { name: item.name }))) {
                          void onDelete(item.id).catch((err: unknown) => {
                            setError(err instanceof Error ? err.message : t("common.errorOccurred"));
                          });
                        }
                      }}
                    >
                      {t("common.delete")}
                    </button>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      </>
      )}

      {/* MQTT Custom Topic Mapping modal — buton tiklayinca acilir */}
      {mappingModalTarget ? (
        <MqttTopicMappingModal
          accessToken={accessToken}
          target={mappingModalTarget}
          devices={devices ?? []}
          onClose={() => setMappingModalTarget(null)}
        />
      ) : null}

      {/* Modbus adres plani — hangi sinyal hangi adreste (SCADA'ya verilecek liste) */}
      {planTargetId !== null ? (
        <ModbusPlanModal
          accessToken={accessToken}
          targetId={planTargetId}
          targetName={targets.find((x) => x.id === planTargetId)?.name ?? ""}
          onClose={() => setPlanTargetId(null)}
          onDownloadCsv={handleDownloadModbusCsv}
        />
      ) : null}

      {/* Otomatik Topic'ler modal — MQTT target icin uretilecek topic'leri gosterir */}
      {autoTopicsTarget ? (() => {
        const allRows = autoTopics ?? [];
        const q = autoTopicsSearch.trim().toLowerCase();
        const filtered = q
          ? allRows.filter(
              (r) =>
                r.topic.toLowerCase().includes(q) ||
                r.device_code.toLowerCase().includes(q)
            )
          : allRows;
        const copyOne = async (topic: string) => {
          try {
            await navigator.clipboard.writeText(topic);
            setCopiedTopic(topic);
            window.setTimeout(() => setCopiedTopic(null), 1500);
          } catch {
            // sessiz — clipboard izni yoksa fallback yok (operator manuel select)
          }
        };
        const copyAll = async () => {
          try {
            const text = filtered.map((r) => r.topic).join("\n");
            await navigator.clipboard.writeText(text);
            setCopiedAll(true);
            window.setTimeout(() => setCopiedAll(false), 1500);
          } catch {
            // sessiz
          }
        };
        return (
        <div className="settings-modal-backdrop" onClick={() => setAutoTopicsTarget(null)}>
          <div
            className="settings-modal auto-topics-modal"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="auto-topics-head">
              <h3>
                <span className="material-symbols-outlined">topic</span>
                {t("engineering.outbound.mqtt.autoTopicsTitle", { name: autoTopicsTarget.name })}
                <span className="auto-topics-count-badge">{allRows.length}</span>
              </h3>
              <p className="helper-text">
                {t("engineering.outbound.mqtt.autoTopicsHint")}
              </p>
            </div>

            <div className="auto-topics-toolbar">
              <div className="auto-topics-search">
                <span className="material-symbols-outlined">search</span>
                <input
                  type="text"
                  placeholder={t("engineering.outbound.mqtt.autoTopicsSearchPlaceholder")}
                  value={autoTopicsSearch}
                  onChange={(e) => setAutoTopicsSearch(e.target.value)}
                  autoFocus
                />
                {autoTopicsSearch ? (
                  <button
                    type="button"
                    className="auto-topics-search-clear"
                    onClick={() => setAutoTopicsSearch("")}
                    title={t("common.clear")}
                  >
                    <span className="material-symbols-outlined">close</span>
                  </button>
                ) : null}
              </div>
              <button
                type="button"
                className="secondary-btn auto-topics-copy-all"
                onClick={() => void copyAll()}
                disabled={filtered.length === 0}
              >
                <span className="material-symbols-outlined">
                  {copiedAll ? "check" : "content_copy"}
                </span>
                {copiedAll
                  ? t("engineering.outbound.mqtt.autoTopicsCopied")
                  : t("engineering.outbound.mqtt.autoTopicsCopyAll", { count: filtered.length })}
              </button>
            </div>

            {autoTopicsLoading ? (
              <p className="helper-text" style={{ padding: "32px 0", textAlign: "center" }}>
                {t("common.loading")}
              </p>
            ) : allRows.length === 0 ? (
              <p className="helper-text" style={{ padding: "32px 0", textAlign: "center" }}>
                {t("engineering.outbound.mqtt.autoTopicsEmpty")}
              </p>
            ) : filtered.length === 0 ? (
              <p className="helper-text" style={{ padding: "32px 0", textAlign: "center" }}>
                {t("engineering.outbound.mqtt.autoTopicsNoMatch")}
              </p>
            ) : (
              <div className="auto-topics-scroll">
                <ul className="auto-topics-cards">
                  {filtered.map((row, idx) => {
                    const isCopied = copiedTopic === row.topic;
                    return (
                      <li
                        key={`${row.device_code}-${row.topic}-${idx}`}
                        className={`auto-topics-card ${row.is_custom ? "auto-topics-card--custom" : ""}`}
                      >
                        <div className="auto-topics-card-meta">
                          <span className="auto-topics-card-device">
                            <span className="material-symbols-outlined">router</span>
                            {row.device_code}
                          </span>
                          <span
                            className={
                              row.is_custom
                                ? "auto-topics-tag auto-topics-tag--custom"
                                : "auto-topics-tag"
                            }
                          >
                            {row.is_custom
                              ? t("engineering.outbound.mqtt.autoTopicsCustom")
                              : t("engineering.outbound.mqtt.autoTopicsDefault")}
                          </span>
                        </div>
                        <code className="auto-topics-card-topic" title={row.topic}>
                          {row.topic}
                        </code>
                        <button
                          type="button"
                          className={`auto-topics-copy-btn ${isCopied ? "is-copied" : ""}`}
                          onClick={() => void copyOne(row.topic)}
                          title={t("engineering.outbound.mqtt.autoTopicsCopyOne")}
                        >
                          <span className="material-symbols-outlined">
                            {isCopied ? "check" : "content_copy"}
                          </span>
                        </button>
                      </li>
                    );
                  })}
                </ul>
              </div>
            )}

            <div className="settings-actions">
              <button type="button" onClick={() => setAutoTopicsTarget(null)}>
                {t("common.close")}
              </button>
            </div>
          </div>
        </div>
        );
      })() : null}
    </section>
  );
}


/** Yeni-kayit (create) modunda kullanilan KOMPAKT sertifika dosyasi
 *  secici. Henuz target id olmadigi icin dosyayi state'te tutar; save
 *  basariyla atildiktan sonra parent uploadMqttCert(targetId, kind, file)
 *  ile yukler. */
function PendingCertInput({
  label,
  file,
  onChange,
  t,
}: {
  label: string;
  file: File | null;
  onChange: (f: File | null) => void;
  t: (k: string) => string;
}) {
  const inputId = `pending-cert-${label.replace(/\s+/g, "_")}`;
  return (
    <div className={`mqtt-cert-pending ${file ? "is-selected" : ""}`}>
      <label htmlFor={inputId} className="mqtt-cert-pending-label">
        {label}
      </label>
      <div className="mqtt-cert-pending-row">
        <input
          id={inputId}
          type="file"
          accept=".pem,.crt,.key,.cer"
          hidden
          onChange={(e) => {
            const f = e.target.files?.[0] ?? null;
            onChange(f);
            // Ayni dosya tekrar secilebilsin
            e.target.value = "";
          }}
        />
        {file ? (
          <>
            <span className="mqtt-cert-pending-name" title={file.name}>
              <span className="material-symbols-outlined">description</span>
              {file.name}
            </span>
            <button
              type="button"
              className="link-btn"
              onClick={() => onChange(null)}
            >
              {t("common.remove") || "Kaldır"}
            </button>
          </>
        ) : (
          <button
            type="button"
            className="mqtt-cert-pending-pick"
            onClick={() => document.getElementById(inputId)?.click()}
          >
            <span className="material-symbols-outlined">upload_file</span>
            {t("engineering.outbound.mqtt.certUploadCta")}
          </button>
        )}
      </div>
    </div>
  );
}

