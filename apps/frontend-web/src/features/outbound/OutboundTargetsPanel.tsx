import { useEffect, useMemo, useState, type FormEvent } from "react";
import { useTranslation } from "react-i18next";

import { ActiveSwitch } from "../../components/ActiveSwitch";
import type { DeviceRow, Iec104RuntimeStatus, OutboundTarget } from "../../shared/types";
import type { MqttPayloadFields } from "../../shared/api";
import { MqttTopicMappingModal } from "./MqttTopicMappingModal";
import { MqttCertUploader } from "./MqttCertUploader";

type Protocol = "rest" | "mqtt" | "iec104";

type CreatePayload = {
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
} & MqttPayloadFields;

type UpdatePayload = {
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
} & MqttPayloadFields;

type Props = {
  targets: OutboundTarget[];
  devices?: DeviceRow[];
  accessToken: string;
  onCreate: (payload: CreatePayload) => Promise<void>;
  onUpdate: (targetId: number, payload: UpdatePayload) => Promise<void>;
  onDelete: (targetId: number) => Promise<void>;
  onDownloadIec104Points?: (targetId: number, suggestedName: string) => Promise<void>;
  onDownloadIec104Xlsx?: (targetId: number, suggestedName: string) => Promise<void>;
  onUpdateDeviceCa?: (deviceCode: string, ca: number | null) => Promise<void>;
  onAutoAssignDeviceCa?: (targetId: number, overwrite: boolean) => Promise<void>;
  onFetchIec104Runtime?: (targetId: number) => Promise<Iec104RuntimeStatus>;
};

export function OutboundTargetsPanel({
  targets,
  devices,
  accessToken,
  onCreate,
  onUpdate,
  onDelete,
  onDownloadIec104Points,
  onDownloadIec104Xlsx,
  onUpdateDeviceCa,
  onAutoAssignDeviceCa,
  onFetchIec104Runtime
}: Props) {
  const { t } = useTranslation();
  const [isCreateOpen, setCreateOpen] = useState(false);
  const [editing, setEditing] = useState<OutboundTarget | null>(null);
  const [error, setError] = useState("");

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

  // Custom Topic Mapping modal — hangi target icin acik
  const [mappingModalTarget, setMappingModalTarget] = useState<OutboundTarget | null>(null);

  const resetForm = () => {
    setName("");
    setProtocol("rest");
    setEndpoint("");
    setTopic("");
    setEventFilter("all");
    setAuthHeader("Authorization");
    setAuthToken("");
    setQos(0);
    setRetain(false);
    setIsActive(true);
    setListenHost("0.0.0.0");
    setListenPort("2404");
    setIec104Ca("1");
    setAllowedPeerList([]);
    setNewPeerIp("");
    setPeerError("");
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
  };

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
      await onCreate({
        name,
        protocol,
        endpoint: isIec104 ? "" : endpoint,
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
        listen_host: isIec104 ? listenHost.trim() || "0.0.0.0" : null,
        listen_port: isIec104 ? Number(listenPort) || 2404 : null,
        iec104_common_address: isIec104 ? Number(iec104Ca) || 1 : null,
        iec104_allowed_peers: isIec104 ? (allowedPeerList.join(",") || null) : null,
        ...collectMqttPayload(),
      });
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
    setListenPort(target.listen_port !== null && target.listen_port !== undefined ? String(target.listen_port) : "2404");
    setIec104Ca(
      target.iec104_common_address !== null && target.iec104_common_address !== undefined
        ? String(target.iec104_common_address)
        : "1"
    );
    const raw = target.iec104_allowed_peers ?? "";
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
  };

  const handleEdit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!editing) return;
    setError("");
    try {
      const isIec104 = editing.protocol === "iec104";
      const isRest = editing.protocol === "rest";
      const isMqtt = editing.protocol === "mqtt";
      // MQTT alanlari: editing.protocol === 'mqtt' ise gonder, degilse undefined.
      const mqttFields: MqttPayloadFields = isMqtt ? collectMqttPayload(true) : {};
      await onUpdate(editing.id, {
        endpoint: isIec104 ? "" : endpoint,
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
        listen_host: isIec104 ? listenHost.trim() || "0.0.0.0" : undefined,
        listen_port: isIec104 ? Number(listenPort) || 2404 : undefined,
        iec104_common_address: isIec104 ? Number(iec104Ca) || 1 : undefined,
        iec104_allowed_peers: isIec104 ? (allowedPeerList.join(",") || null) : undefined,
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

  const handleDownloadCsv = async (target: OutboundTarget) => {
    if (!onDownloadIec104Points) return;
    const safeName = target.name.replace(/[^A-Za-z0-9._-]+/g, "_");
    const ts = new Date().toISOString().replace(/[:.]/g, "-").slice(0, 19);
    await onDownloadIec104Points(target.id, `iec104-points-${safeName}-${ts}.csv`);
  };

  const handleAutoAssign = async (target: OutboundTarget, overwrite: boolean) => {
    if (!onAutoAssignDeviceCa) return;
    if (overwrite && !window.confirm(t("engineering.outbound.asdu.resetAllTitle") + " — " + t("common.confirm") + "?")) {
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
  // Aktif protokol — edit'te target.protocol (kilitli), create'de kullanici secimi.
  // Form alanlari hangi blokta gosterilecek diye bu degisken kullanilir; kullanici
  // REST sectiyse MQTT alanlari, MQTT sectiyse REST alanlari gizlenir.
  const activeProtocol: Protocol = editing ? (editing.protocol as Protocol) : protocol;
  const isMqttForm = activeProtocol === "mqtt";
  const isRestForm = activeProtocol === "rest";

  // Topic template card icin editing mode + canli preview
  const [topicTemplateEditing, setTopicTemplateEditing] = useState(false);
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
      .replace(/\{datatype\}/g, "analog")
      .replace(/\{signal\}/g, "master.voltage_a");
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
  useEffect(() => {
    if (!runtimeTarget || !onFetchIec104Runtime) return;
    let cancelled = false;
    const tick = async () => {
      setRuntimeLoading(true);
      try {
        const data = await onFetchIec104Runtime(runtimeTarget.id);
        if (!cancelled) setRuntime(data);
      } catch (err) {
        if (!cancelled) setError(err instanceof Error ? err.message : t("common.errorOccurred"));
      } finally {
        if (!cancelled) setRuntimeLoading(false);
      }
    };
    void tick();
    const interval = window.setInterval(() => void tick(), 5000);
    return () => {
      cancelled = true;
      window.clearInterval(interval);
    };
  }, [runtimeTarget, onFetchIec104Runtime]);

  // Liste rozetleri 10 sn'de bir
  useEffect(() => {
    if (!onFetchIec104Runtime) return;
    const iec104Ids = targets.filter((t) => t.protocol === "iec104").map((t) => t.id);
    if (iec104Ids.length === 0) {
      setRuntimeBadges({});
      return;
    }
    let cancelled = false;
    const tick = async () => {
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
      if (!cancelled) setRuntimeBadges(updates);
    };
    void tick();
    const interval = window.setInterval(() => void tick(), 10000);
    return () => {
      cancelled = true;
      window.clearInterval(interval);
    };
  }, [targets, onFetchIec104Runtime]);

  return (
    <section className="tab-panel">
      <div className="panel-head">
        <h3>{t("engineering.outbound.title")}</h3>
        <button className="add-user-btn" onClick={() => setCreateOpen(true)}>
          + {t("engineering.outbound.newTarget")}
        </button>
      </div>
      {/* Modal labels */}
      {/* (i18n çevirileri aşağıdaki modal blok ile birlikte) */}
      {error ? <p className="error-text">{error}</p> : null}

      {(isCreateOpen || editing) && (
        <div className="settings-modal-backdrop">
          <form
            className={`settings-modal ${(isCreatingIec104 || isEditingIec104) ? "iec104-edit-modal" : ""}`}
            onSubmit={editing ? handleEdit : handleCreate}
          >
            <h3>{editing ? t("engineering.outbound.editTargetModal") : t("engineering.outbound.newTargetModal")}</h3>
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
                      onChange={(event) => setProtocol(event.target.value as Protocol)}
                    >
                      <option value="rest">{t("engineering.outbound.proto.rest")}</option>
                      <option value="mqtt">{t("engineering.outbound.proto.mqtt")}</option>
                      <option value="iec104">{t("engineering.outbound.proto.iec104")}</option>
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

            {(isCreatingIec104 || isEditingIec104) ? (
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
                  </>
                ) : null}

                {/* MQTT'ye ozel: 2-sutun panel + topic card + cert upload */}
                {isMqttForm ? (
                  <div className="mqtt-form-v2">
                    {/* SOL PANEL: Baglanti */}
                    <div className="mqtt-form-v2-panel">
                      <div className="mqtt-panel-heading">
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
                    </div>

                    {/* SAG PANEL: TLS + Yayin Ayarlari (event filter dahil) */}
                    <div className="mqtt-form-v2-panel">
                      <div className="mqtt-panel-heading">
                        <span className="material-symbols-outlined">lock</span>
                        {t("engineering.outbound.mqtt.section.tls")}
                      </div>
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
                            <div className="mqtt-cert-upload-grid">
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
                            <div className="mqtt-cert-upload-hint">
                              <span className="material-symbols-outlined">info</span>
                              {t("engineering.outbound.mqtt.certUploadAfterSave")}
                            </div>
                          )}
                        </>
                      ) : null}

                      {/* Yayin Ayarlari */}
                      <div className="mqtt-panel-heading mqtt-panel-heading--with-margin">
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
                    </div>

                    {/* TOPIC TEMPLATE CARD (alt satir, tam genislik) */}
                    <div className="mqtt-topic-card">
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
                          {topicTemplateEditing
                            ? t("common.done")
                            : t("common.edit")}
                        </button>
                      </div>

                      <div className="mqtt-topic-card-body">
                        {/* Prefix + Customer (small inputs) */}
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
                              {["{prefix}", "{customer}", "{device}", "{source}", "{datatype}", "{signal}"].map(
                                (v) => (
                                  <button
                                    type="button"
                                    key={v}
                                    className="mqtt-topic-var-chip"
                                    onClick={() => {
                                      // Mevcut template'in sonuna ekle
                                      const base = (mqttTopicTemplate || "").trim();
                                      setMqttTopicTemplate(
                                        base ? `${base}/${v}` : v
                                      );
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

                        {/* Canli preview */}
                        <div className="mqtt-topic-preview">
                          <small>{t("engineering.outbound.mqtt.topicCard.previewLabel")}:</small>
                          <code>{topicPreview}</code>
                        </div>
                      </div>
                    </div>

                    {/* Gelismis (collapsible) */}
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
                  </div>
                ) : null}
              </>
            )}
            <ActiveSwitch checked={isActive} onChange={setIsActive} />
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
                    <th style={{ width: 60 }}>#</th>
                    <th>{t("engineering.outbound.asdu.tableDevice")}</th>
                    <th>{t("engineering.outbound.asdu.tableCode")}</th>
                    <th style={{ width: 160 }}>{t("engineering.outbound.asdu.tableAsdu")}</th>
                    <th style={{ width: 110 }}>{t("engineering.outbound.asdu.tableActions")}</th>
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
                      <th>Peer</th>
                      <th>{t("common.status")}</th>
                      <th>{t("common.time")}</th>
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

      {/* Modern liste tablosu — Aktif sütun en başta, "Filtre" kaldırıldı,
          "Endpoint / Listen" basitleştirildi (default CA ekrandan kaldırıldı). */}
      <div className="outbound-modern-table-wrap">
        <table className="values-table outbound-modern-table">
          <thead>
            <tr>
              <th style={{ width: 70 }}>{t("engineering.outbound.runtime.connected")}</th>
              <th>{t("engineering.outbound.table.name")}</th>
              <th style={{ width: 110 }}>{t("engineering.outbound.table.protocol")}</th>
              <th>{t("engineering.outbound.table.endpoint")}</th>
              <th style={{ width: 130 }}>{t("engineering.outbound.table.status")}</th>
              <th className="actions-header">{t("engineering.outbound.table.actions")}</th>
            </tr>
          </thead>
          <tbody>
            {targets.map((item) => {
              const isIec = item.protocol === "iec104";
              const endpointDisplay = isIec
                ? `${item.listen_host ?? "0.0.0.0"}:${item.listen_port ?? 2404}`
                : item.endpoint;
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
                  <td className="outbound-endpoint-cell">{endpointDisplay}</td>
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
                    ) : (
                      <span className="helper-text">—</span>
                    )}
                  </td>
                  <td className="actions-cell">
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
                    {isIec && onDownloadIec104Points ? (
                      <button
                        type="button"
                        className="secondary-btn action-btn"
                        title={t("engineering.outbound.downloadCsvTitle")}
                        onClick={() => void handleDownloadCsv(item)}
                      >
                        CSV
                      </button>
                    ) : null}
                    {item.protocol === "mqtt" ? (
                      <button
                        type="button"
                        className="secondary-btn action-btn"
                        title={t("engineering.outbound.mqtt.mappingBtnTitle")}
                        onClick={() => setMappingModalTarget(item)}
                      >
                        {t("engineering.outbound.mqtt.mappingBtn")}
                      </button>
                    ) : null}
                    <button className="edit-btn action-btn" onClick={() => openEdit(item)}>
                      {t("common.edit")}
                    </button>
                    <button
                      className="danger-btn action-btn"
                      onClick={() => {
                        if (window.confirm(t("engineering.outbound.confirmDelete", { name: item.name }))) {
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
            {targets.length === 0 ? (
              <tr>
                <td colSpan={6} className="helper-text" style={{ padding: 24, textAlign: "center" }}>
                  {t("engineering.outbound.emptyHint")}
                </td>
              </tr>
            ) : null}
          </tbody>
        </table>
      </div>

      {/* MQTT Custom Topic Mapping modal — buton tiklayinca acilir */}
      {mappingModalTarget ? (
        <MqttTopicMappingModal
          accessToken={accessToken}
          target={mappingModalTarget}
          devices={devices ?? []}
          onClose={() => setMappingModalTarget(null)}
        />
      ) : null}
    </section>
  );
}

