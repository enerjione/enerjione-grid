import { useEffect, useMemo, useRef, useState, type FormEvent } from "react";
import { asyncConfirm } from "../../components/ConfirmDialog";
import { useTranslation } from "react-i18next";
import i18n from "../../shared/i18n";
import type { DeviceModelOption, DeviceRow, Dnp3ExtendedSettings, Gateway, LicenseStatus } from "../../shared/types";
import { DEFAULT_DNP3_EXTENDED, mergeDnp3Extended } from "../../shared/types";
import { Dnp3SettingsForm } from "./Dnp3SettingsForm";

/** Son sinyal bundan eskiyse yeşil değil, kırmızı (kapalı/erişilemez collector için pencere) */
const GATEWAY_LIVE_SEC = 60;

type GatewayLiveness = {
  className: "inactive" | "never" | "online" | "offline";
  title: string;
};

function formatTrRel(iso: string): string {
  const d = new Date(iso);
  const s = Math.round((Date.now() - d.getTime()) / 1000);
  const localeTag = i18n.language?.startsWith("tr") ? "tr-TR" : "en-US";
  if (s < 5) return i18n.t("common.now");
  if (s < 60) return i18n.t("common.secondsAgoShort", { count: s });
  if (s < 3600) return i18n.t("common.minutesAgoShort", { count: Math.round(s / 60) });
  if (s < 86400) return i18n.t("common.hoursAgoShort", { count: Math.round(s / 3600) });
  return d.toLocaleString(localeTag);
}

function getGatewayLiveness(gateway: Gateway): GatewayLiveness {
  if (!gateway.is_active) {
    return { className: "inactive", title: i18n.t("engineering.gatewayLive.inactive") };
  }
  if (!gateway.last_seen_at) {
    return { className: "never", title: i18n.t("engineering.gatewayLive.never") };
  }
  const sec = (Date.now() - new Date(gateway.last_seen_at).getTime()) / 1000;
  if (sec < GATEWAY_LIVE_SEC) {
    return { className: "online", title: i18n.t("engineering.gatewayLive.online") };
  }
  return { className: "offline", title: i18n.t("engineering.gatewayLive.offline") };
}

function deviceCommDotClass(status: DeviceRow["communicationStatus"]): "online" | "offline" {
  return status === "online" ? "online" : "offline";
}

const DEVICE_MODEL_IMAGES: Record<string, string> = {
  horstmann_sn_2_0: "/sn20.png"
};
const INITIATING_PORT_BASE_DEFAULT = 20100;

function deviceImageSrc(modelCode: string): string {
  return DEVICE_MODEL_IMAGES[modelCode] ?? "/sn20.png";
}

/** Gateway down ise altindaki cihazlar da offline gozukmeli — collector ayakta
 * degilken cihaz sinyali fiziksel olarak gelse bile platform tarafinda yoktur. */
function effectiveCommStatus(
  device: DeviceRow,
  gateways: Gateway[]
): DeviceRow["communicationStatus"] {
  const gw = gateways.find((g) => g.code === device.gatewayCode);
  if (gw && getGatewayLiveness(gw).className !== "online") {
    return "offline";
  }
  return device.communicationStatus;
}

type DevicePropsTab = "system" | "comms";

type Props = {
  role: "operator" | "engineer" | "installer" | "ops_manager";
  gateways: Gateway[];
  devices: DeviceRow[];
  unassignedCount: number;
  deviceModels: DeviceModelOption[];
  inventoryError: string;
  licenseStatus: LicenseStatus | null;
  onSelectGateway: (gatewayCode: string) => Promise<void>;
  onCreateGateway: (payload: {
    code: string;
    name: string;
    host: string;
    listen_port: number;
    upstream_url: string;
    batch_interval_sec: number;
    max_devices: number;
    device_code_prefix?: string | null;
    token: string;
    is_active: boolean;
    control_host: string;
    control_port: number;
    initiating_port_count: number;
  }) => Promise<void>;
  onUpdateGateway: (
    gatewayCode: string,
    payload: { name?: string; host?: string; listen_port?: number; token?: string }
  ) => Promise<void>;
  onDeleteGateway: (gatewayCode: string) => Promise<void>;
  /** Gateway'e "tum cihazlara sorgu at" tetigi. */
  onRefreshGatewayAll?: (gatewayCode: string) => Promise<void>;
  onDownloadCompose: (
    gatewayCode: string,
    params: { backendUrl: string; hostPort: number; fmt: "compose" | "env" }
  ) => Promise<void>;
  onCreate: (payload: {
    code: string;
    name: string;
    description?: string | null;
    model: string;
    installation_date?: string | null;
    gateway_code?: string | null;
    ip_address: string;
    dnp3_outstation_port: number;
    dnp3_address: number;
    dnp3_extended?: Dnp3ExtendedSettings | null;
    poll_interval_sec: number;
    timeout_ms: number;
    retry_count: number;
    signal_profile: string;
    latitude: number;
    longitude: number;
  }) => Promise<void>;
  onUpdate: (
    deviceCode: string,
    payload: {
      name?: string;
      description?: string | null;
      model?: string;
      installation_date?: string | null;
      gateway_code?: string | null;
      ip_address?: string;
      dnp3_outstation_port?: number;
      dnp3_address?: number;
      dnp3_extended?: Dnp3ExtendedSettings;
      poll_interval_sec?: number;
      timeout_ms?: number;
      retry_count?: number;
      latitude?: number;
      longitude?: number;
      iec104_common_address?: number | null;
    }
  ) => Promise<void>;
  onDelete: (deviceCode: string) => Promise<void>;
};

export function DeviceManagementPanel({
  role,
  gateways,
  devices,
  unassignedCount,
  deviceModels,
  inventoryError,
  licenseStatus,
  onSelectGateway,
  onCreateGateway,
  onUpdateGateway,
  onDeleteGateway,
  onRefreshGatewayAll,
  onDownloadCompose,
  onCreate,
  onUpdate,
  onDelete
}: Props) {
  const { t } = useTranslation();
  const canManageGateways = role === "installer";
  // DNP3 adresleri (outstation port + dnp3_address + advanced master/IP) sadece
  // installer'a goz onunde. Engineer cihaz ekleyip kaldirabilir ama DNP3
  // adres detaylarini goremez/duzenleyemez. (Backend tarafinda da yazma
  // yetkisi `_EDIT_ROLES`'a esit oldugu icin engineer bir POST yaparsa
  // alanlar default/mevcut deger ile kalir.)
  const canSeeDnp3 = role === "installer";
  const [selectedGatewayCode, setSelectedGatewayCode] = useState(gateways[0]?.code ?? "");
  const [selectedDeviceCode, setSelectedDeviceCode] = useState("");
  const [showCreateModal, setShowCreateModal] = useState(false);
  const [showGatewayCreateModal, setShowGatewayCreateModal] = useState(false);
  const [showGatewayEditModal, setShowGatewayEditModal] = useState(false);
  const [error, setError] = useState("");

  const defaultBackendIp = (() => {
    if (typeof window === "undefined") return "";
    return window.location.hostname || "";
  })();
  const [composeFor, setComposeFor] = useState<string | null>(null);
  const [composeBackendIp, setComposeBackendIp] = useState(defaultBackendIp);
  const [composeError, setComposeError] = useState("");
  const [composeBusy, setComposeBusy] = useState(false);
  const [composeCopied, setComposeCopied] = useState(false);
  // Gateway silme islemi sirasinda hangi gateway kodunu sildigimizi tutar.
  // Bu degisken hem butonu disable etmeye hem overlay'i gostermeye yarar.
  const [deletingGatewayCode, setDeletingGatewayCode] = useState<string | null>(null);

  const openComposeModal = (gwCode: string) => {
    setComposeFor(gwCode);
    setComposeBackendIp(defaultBackendIp);
    setComposeError("");
  };

  const handleDownloadComposeSubmit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!composeFor) return;
    setComposeError("");
    setComposeBusy(true);
    try {
      const ip = composeBackendIp.trim();
      // Kullanici sadece IP ya da hostname yazar; backend URL'i sabit sema +
      // /api/v1 ile tamamlanir. Port DEFAULT 80 (http) — nginx reverse proxy
      // `/api/*` isteklerini backend-api:8000'e forward eder. 8000 public
      // expose edilmez, dogrudan baglanti calismaz.
      // Kullanici full URL girdiyse (http:// veya https:// ile basliyorsa)
      // oldugu gibi kullan; aksi halde 'http://<ip>/api/v1' formatla.
      const backendUrl =
        ip.startsWith("http://") || ip.startsWith("https://")
          ? ip.replace(/\/+$/, "") + (ip.includes("/api/v1") ? "" : "/api/v1")
          : `http://${ip}/api/v1`;
      await onDownloadCompose(composeFor, {
        backendUrl,
        // hostPort verilmezse backend gateway sirasina gore 8020/8021/... atar.
        hostPort: 0,
        fmt: "compose"
      });
      // Modal acik kalir — kullanici docker komutunu kopyalamak isteyebilir.
    } catch (err) {
      setComposeError(err instanceof Error ? err.message : t("common.errorOccurred"));
    } finally {
      setComposeBusy(false);
    }
  };

  const generateGatewayToken = () => {
    const len = 48;
    const chars = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789";
    const arr = new Uint32Array(len);
    window.crypto.getRandomValues(arr);
    let out = "";
    for (let i = 0; i < len; i += 1) out += chars.charAt(arr[i] % chars.length);
    setGatewayToken(out);
  };

  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [model, setModel] = useState("horstmann_sn_2_0");
  const [installationDate, setInstallationDate] = useState("");
  const [ipAddress, setIpAddress] = useState("");
  const [dnp3OutstationPort, setDnp3OutstationPort] = useState("20001");
  const [dnp3Address, setDnp3Address] = useState("1");
  const [dnp3Ext, setDnp3Ext] = useState<Dnp3ExtendedSettings>(() => ({ ...DEFAULT_DNP3_EXTENDED }));
  const [pollIntervalSec, setPollIntervalSec] = useState("5");
  const [timeoutMs, setTimeoutMs] = useState("3000");
  const [retryCount, setRetryCount] = useState("2");
  // IEC 60870-5-104 ASDU Common Address (cihaz bazli). NULL/"" → outbound
  // hedefin default CA'si kullanilir.
  const [iec104CommonAddress, setIec104CommonAddress] = useState("");
  const [latitude, setLatitude] = useState("0");
  const [longitude, setLongitude] = useState("0");
  // Cihazi baska gateway altina tasimak icin secili hedef gateway kodu.
  // Initially seçili cihazın mevcut gateway'i; kullanıcı dropdown'dan farklı
  // bir gateway seçerse Kaydet'te cihaz oraya tasinir.
  const [deviceGatewayCode, setDeviceGatewayCode] = useState("");

  const [createCode, setCreateCode] = useState("");
  const [createName, setCreateName] = useState("");
  const [createModel, setCreateModel] = useState("horstmann_sn_2_0");
  const [createInstallationDate, setCreateInstallationDate] = useState("");
  const [createIpAddress, setCreateIpAddress] = useState("");
  const [createDnp3OutstationPort, setCreateDnp3OutstationPort] = useState("20001");
  const [createDnp3Address, setCreateDnp3Address] = useState("1");
  const [createDnp3Ext, setCreateDnp3Ext] = useState<Dnp3ExtendedSettings>(() => ({ ...DEFAULT_DNP3_EXTENDED }));
  const [createPollIntervalSec, setCreatePollIntervalSec] = useState("5");
  const [createTimeoutMs, setCreateTimeoutMs] = useState("3000");
  const [createRetryCount, setCreateRetryCount] = useState("2");
  const [createLatitude, setCreateLatitude] = useState("0");
  const [createLongitude, setCreateLongitude] = useState("0");
  const [gatewayCode, setGatewayCode] = useState("");
  const [gatewayName, setGatewayName] = useState("");
  const [gatewayToken, setGatewayToken] = useState("");
  const [editGatewayCode, setEditGatewayCode] = useState("");
  const [editGatewayName, setEditGatewayName] = useState("");
  const [editGatewayHost, setEditGatewayHost] = useState("");
  const [editGatewayPort, setEditGatewayPort] = useState("20000");
  const [editGatewayToken, setEditGatewayToken] = useState("");
  const [devicePropsTab, setDevicePropsTab] = useState<DevicePropsTab>("system");

  const lastDeviceRef = useRef<DeviceRow | null>(null);

  const selectedGateway = useMemo(
    () => gateways.find((g) => g.code === selectedGatewayCode) ?? null,
    [gateways, selectedGatewayCode]
  );

  const selectedDevice = useMemo(() => {
    if (!selectedDeviceCode) return null;
    const inList = devices.find((item) => item.code === selectedDeviceCode);
    if (inList) {
      return inList;
    }
    if (lastDeviceRef.current && lastDeviceRef.current.code === selectedDeviceCode) {
      return lastDeviceRef.current;
    }
    return null;
  }, [devices, selectedDeviceCode]);

  const selectedDeviceGateway = useMemo(
    () => gateways.find((g) => g.code === (selectedDevice?.gatewayCode ?? selectedGatewayCode)) ?? selectedGateway,
    [gateways, selectedDevice, selectedGateway, selectedGatewayCode]
  );

  const nextInitiatingMasterPort = useMemo(() => {
    const base = selectedGateway?.initiating_port_base ?? INITIATING_PORT_BASE_DEFAULT;
    const count = devices.filter((d) => d.dnp3Extended?.ip_endpoint_type === "initiating").length;
    return base + count;
  }, [devices, selectedGateway]);

  const selectedInitiatingMasterPort = useMemo(() => {
    if (!selectedDevice) return nextInitiatingMasterPort;
    if (dnp3Ext.master_ip_port) return dnp3Ext.master_ip_port;
    const base = selectedDeviceGateway?.initiating_port_base ?? INITIATING_PORT_BASE_DEFAULT;
    const initiatingDevices = devices
      .filter((d) => d.gatewayCode === selectedDevice.gatewayCode && d.dnp3Extended?.ip_endpoint_type === "initiating")
      .sort((a, b) => a.id - b.id);
    const idx = Math.max(0, initiatingDevices.findIndex((d) => d.code === selectedDevice.code));
    return base + idx;
  }, [devices, dnp3Ext.master_ip_port, nextInitiatingMasterPort, selectedDevice, selectedDeviceGateway]);

  useEffect(() => {
    if (!selectedDeviceCode) {
      lastDeviceRef.current = null;
      return;
    }
    const m = devices.find((d) => d.code === selectedDeviceCode);
    if (m) {
      lastDeviceRef.current = m;
    }
  }, [devices, selectedDeviceCode]);

  useEffect(() => {
    setDevicePropsTab("system");
  }, [selectedDeviceCode]);

  useEffect(() => {
    if (!gateways.length) {
      setSelectedGatewayCode("");
      setSelectedDeviceCode("");
      return;
    }
    const exists = gateways.some((item) => item.code === selectedGatewayCode);
    if (selectedGatewayCode && !exists) {
      setSelectedDeviceCode("");
      const nextGatewayCode = gateways[0].code;
      setSelectedGatewayCode(nextGatewayCode);
      void onSelectGateway(nextGatewayCode);
    }
    if (!selectedGatewayCode && unassignedCount === 0) {
      const nextGatewayCode = gateways[0].code;
      setSelectedGatewayCode(nextGatewayCode);
      void onSelectGateway(nextGatewayCode);
    }
  }, [gateways, selectedGatewayCode, onSelectGateway, unassignedCount]);

  const applySelectedDeviceToForm = (device: DeviceRow) => {
    setName(device.name);
    setDescription(device.description ?? "");
    setModel(device.model ?? "horstmann_sn_2_0");
    setInstallationDate(device.installationDate ?? "");
    setIpAddress(device.ipAddress ?? "");
    setDnp3OutstationPort(String(device.dnp3OutstationPort ?? 20001));
    setDnp3Address(String(device.dnp3Address ?? 1));
    setDnp3Ext(mergeDnp3Extended(device.dnp3Extended));
    setPollIntervalSec(String(device.pollIntervalSec ?? 5));
    setTimeoutMs(String(device.timeoutMs ?? 3000));
    setRetryCount(String(device.retryCount ?? 2));
    setIec104CommonAddress(
      device.iec104CommonAddress !== null && device.iec104CommonAddress !== undefined
        ? String(device.iec104CommonAddress)
        : ""
    );
    setLatitude(String(device.latitude ?? 0));
    setLongitude(String(device.longitude ?? 0));
    setDeviceGatewayCode(device.gatewayCode ?? "");
  };

  const handleGatewaySelect = async (gatewayCode: string) => {
    setSelectedGatewayCode(gatewayCode);
    setSelectedDeviceCode("");
    lastDeviceRef.current = null;
    setError("");
    await onSelectGateway(gatewayCode);
  };

  const handleDeviceSelect = (device: DeviceRow) => {
    lastDeviceRef.current = device;
    setSelectedDeviceCode(device.code);
    setDevicePropsTab("system");
    applySelectedDeviceToForm(device);
  };

  const handleSaveDevice = async () => {
    if (!selectedDevice) return;
    setError("");
    const targetGateway = deviceGatewayCode || null;
    const movedToAnotherGateway =
      targetGateway !== null && targetGateway !== (selectedDevice.gatewayCode ?? null);
    try {
      const caTrimmed = iec104CommonAddress.trim();
      const caValue = caTrimmed === "" ? null : Number(caTrimmed);
      await onUpdate(selectedDevice.code, {
        name,
        description: description.trim() || null,
        model,
        installation_date: installationDate || null,
        gateway_code: targetGateway,
        ip_address: ipAddress,
        dnp3_outstation_port: Number(dnp3OutstationPort),
        dnp3_address: Number(dnp3Address),
        dnp3_extended: {
          ...dnp3Ext,
          master_ip_address:
            dnp3Ext.ip_endpoint_type === "initiating"
              ? selectedDeviceGateway?.control_host || "127.0.0.1"
              : dnp3Ext.master_ip_address,
          master_ip_port:
            dnp3Ext.ip_endpoint_type === "initiating"
              ? selectedInitiatingMasterPort
              : dnp3Ext.master_ip_port
        },
        poll_interval_sec: Number(pollIntervalSec),
        timeout_ms: Number(timeoutMs),
        retry_count: Number(retryCount),
        iec104_common_address: caValue,
        latitude: Number(latitude),
        longitude: Number(longitude)
      });
      // Cihaz baska gateway altina tasindiysa, sol panelin secimini yeni
      // gateway'e cevir ki kullanici tasidigi cihazi anlik olarak yerinde gorsun.
      if (movedToAnotherGateway && targetGateway) {
        setSelectedGatewayCode(targetGateway);
        await onSelectGateway(targetGateway);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : t("common.errorOccurred"));
    }
  };

  const handleDeleteDevice = async () => {
    if (!selectedDevice) return;
    if (!await asyncConfirm(`"${selectedDevice.name}" cihazı silinsin mi?`)) return;
    setError("");
    try {
      await onDelete(selectedDevice.code);
      setSelectedDeviceCode("");
    } catch (err) {
      setError(err instanceof Error ? err.message : t("common.errorOccurred"));
    }
  };

  const handleCreateDevice = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setError("");
    try {
      const endpointType = createDnp3Ext.ip_endpoint_type;
      await onCreate({
        code: createCode,
        name: createName,
        model: createModel,
        installation_date: createInstallationDate || null,
        gateway_code: selectedGatewayCode || null,
        // Cihazin DNP3 outstation IP adresi. Gateway listening modunda buraya
        // bagdir. Bos olursa form 'required' kuralina takilir; bu nedenle
        // explicit default vermeye gerek yok.
        ip_address: endpointType === "initiating" ? "0.0.0.0" : createIpAddress.trim(),
        dnp3_outstation_port: endpointType === "initiating" ? nextInitiatingMasterPort : Number(createDnp3OutstationPort),
        dnp3_address: endpointType === "initiating" ? createDnp3Ext.master_address : Number(createDnp3Address),
        dnp3_extended: {
          ...createDnp3Ext,
          master_ip_address: selectedGateway?.control_host || "127.0.0.1",
          master_ip_port: nextInitiatingMasterPort
        },
        poll_interval_sec: Number(createPollIntervalSec),
        timeout_ms: Number(createTimeoutMs),
        retry_count: Number(createRetryCount),
        signal_profile: "horstmann_sn2_fixed",
        latitude: Number(createLatitude),
        longitude: Number(createLongitude)
      });
      setShowCreateModal(false);
      setCreateCode("");
      setCreateName("");
      setCreateModel("horstmann_sn_2_0");
      setCreateInstallationDate("");
      setCreateIpAddress("");
      setCreateDnp3OutstationPort("20001");
      setCreateDnp3Address("1");
      setCreateDnp3Ext({ ...DEFAULT_DNP3_EXTENDED });
      setCreatePollIntervalSec("5");
      setCreateTimeoutMs("3000");
      setCreateRetryCount("2");
      setCreateLatitude("0");
      setCreateLongitude("0");
    } catch (err) {
      setError(err instanceof Error ? err.message : t("common.errorOccurred"));
    }
  };

  const handleCreateGateway = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setError("");
    const createdCode = gatewayCode.trim();
    const enteredName = gatewayName.trim() || createdCode;
    try {
      await onCreateGateway({
        code: createdCode,
        name: enteredName,
        // Host/listen_port artik anlamli degil — gateway DNP3 master rolünde,
        // outstation cihazlari device.ip_address'ten okuyor. Backend sema'da
        // alan zorunlu oldugu icin placeholder gonderiyoruz.
        host: "auto",
        listen_port: 0,
        upstream_url: "/api/v1/telemetry/gateway/{gateway_code}",
        batch_interval_sec: 5,
        max_devices: 200,
        device_code_prefix: null,
        token: gatewayToken,
        is_active: true,
        control_host: "127.0.0.1",
        control_port: 0,
        initiating_port_count: 0
      });
      setShowGatewayCreateModal(false);
      setGatewayCode("");
      setGatewayName("");
      setGatewayToken("");
      openComposeModal(createdCode);
    } catch (err) {
      setError(err instanceof Error ? err.message : t("common.errorOccurred"));
    }
  };

  const handleDeleteGateway = async (gatewayCode?: string) => {
    const codeToDelete = gatewayCode ?? selectedGatewayCode;
    if (!codeToDelete) return;
    const gateway = gateways.find((item) => item.code === codeToDelete);
    if (!gateway) return;
    if (deletingGatewayCode) return; // ayni anda baska bir silme suruyor
    // Onay diyalogu App.tsx tarafinda gosteriliyor — burada cifte sormaya gerek yok.
    setError("");
    setDeletingGatewayCode(codeToDelete);
    try {
      await onDeleteGateway(codeToDelete);
      setSelectedGatewayCode("");
      setSelectedDeviceCode("");
    } catch (err) {
      setError(err instanceof Error ? err.message : t("common.errorOccurred"));
    } finally {
      setDeletingGatewayCode(null);
    }
  };

  const handleStartGatewayEdit = (gateway: Gateway) => {
    setEditGatewayCode(gateway.code);
    setEditGatewayName(gateway.name);
    setEditGatewayHost(gateway.host);
    setEditGatewayPort(String(gateway.listen_port));
    setEditGatewayToken(gateway.token);
    setShowGatewayEditModal(true);
  };

  const handleUpdateGateway = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!editGatewayCode) return;
    setError("");
    try {
      await onUpdateGateway(editGatewayCode, {
        name: editGatewayName,
        host: editGatewayHost,
        listen_port: Number(editGatewayPort),
        token: editGatewayToken
      });
      setShowGatewayEditModal(false);
      if (selectedGatewayCode === editGatewayCode) {
        await onSelectGateway(editGatewayCode);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : t("common.errorOccurred"));
    }
  };

  return (
    <section className="tab-panel device-management-panel">
      <div className="device-management-layout">
        <div className="device-management-left">
          <div className="device-panel-heading">
            <h4>{t("engineering.gateways.title")}</h4>
            {canManageGateways ? (
              <button
                type="button"
                className="device-add-plus-btn"
                onClick={() => setShowGatewayCreateModal(true)}
                title={t("engineering.gateways.addGateway")}
                aria-label={t("engineering.gateways.addGateway")}
              >
                +
              </button>
            ) : null}
          </div>
          <div className="device-group-list">
            {unassignedCount > 0 ? (
              <div
                className={`device-group-item gateway-item ${selectedGatewayCode === "" ? "active" : ""}`}
              >
                <button
                  type="button"
                  className="device-group-main gateway-select-main"
                  onClick={() => void handleGatewaySelect("")}
                >
                  <div className="gateway-title-row">
                    <div className="gateway-name-with-status">
                      <span className="gateway-status never" aria-hidden="true">
                        <span className="gateway-status-dot" />
                      </span>
                      <strong className="gateway-name-only">
                        {t("engineering.devicesPanel.unassigned", { count: unassignedCount })}
                      </strong>
                    </div>
                  </div>
                </button>
              </div>
            ) : null}
            {gateways.map((gateway) => {
              const gLive = getGatewayLiveness(gateway);
              const isDeletingThis = deletingGatewayCode === gateway.code;
              const anotherDeleting = Boolean(deletingGatewayCode) && !isDeletingThis;
              return (
              <div
                key={gateway.id}
                className={`device-group-item gateway-item ${selectedGatewayCode === gateway.code ? "active" : ""} ${isDeletingThis ? "is-deleting" : ""}`}
              >
                <div className="gateway-item-body">
                  <button
                    type="button"
                    className="device-group-main gateway-select-main"
                    onClick={() => void handleGatewaySelect(gateway.code)}
                    disabled={isDeletingThis}
                  >
                    <div className="gateway-title-row">
                      <div className="gateway-name-with-status">
                        <span className={`gateway-status ${gLive.className}`} title={gLive.title}>
                          <span className="gateway-status-dot" aria-hidden="true" />
                        </span>
                        <strong className="gateway-name-only">{gateway.name}</strong>
                      </div>
                    </div>
                  </button>
                  {canManageGateways ? (
                    <div className="item-actions inline-actions gateway-item-actions">
                      <button
                        type="button"
                        className="secondary-btn action-btn"
                        onClick={() => openComposeModal(gateway.code)}
                        title={t("engineering.gateways.downloadCompose")}
                        aria-label={t("engineering.gateways.downloadComposeShort")}
                        disabled={isDeletingThis || anotherDeleting}
                      >
                        <svg viewBox="0 0 24 24" width="18" height="18" aria-hidden="true">
                          <path
                            fill="currentColor"
                            d="M5 20h14v-2H5v2zM19 9h-4V3H9v6H5l7 7 7-7z"
                          />
                        </svg>
                      </button>
                      <button
                        type="button"
                        className="secondary-btn action-btn"
                        onClick={() => handleStartGatewayEdit(gateway)}
                        title={t("engineering.gateways.edit")}
                        aria-label={t("engineering.gateways.edit")}
                        disabled={isDeletingThis || anotherDeleting}
                      >
                        <svg viewBox="0 0 24 24" width="18" height="18" aria-hidden="true">
                          <path
                            fill="currentColor"
                            d="M3 17.25V21h3.75L17.81 9.94l-3.75-3.75zM20.71 7.04a1 1 0 0 0 0-1.41L18.37 3.29a1 1 0 0 0-1.41 0l-1.83 1.83 3.75 3.75z"
                          />
                        </svg>
                      </button>
                      {onRefreshGatewayAll ? (
                        <button
                          type="button"
                          className="secondary-btn action-btn"
                          onClick={() => void onRefreshGatewayAll(gateway.code)}
                          title={t("engineering.gateways.refreshAll")}
                          aria-label={t("engineering.gateways.refreshAllShort")}
                          disabled={isDeletingThis || anotherDeleting}
                        >
                          {/* Inline SVG — Material Icons font yuklenmezse de gozukur.
                              VDS HTTP ortaminda Google Fonts CDN bloklanabiliyor. */}
                          <svg viewBox="0 0 24 24" width="18" height="18" aria-hidden="true">
                            <path
                              fill="currentColor"
                              d="M17.65 6.35A7.958 7.958 0 0 0 12 4c-4.42 0-7.99 3.58-7.99 8s3.57 8 7.99 8c3.73 0 6.84-2.55 7.73-6h-2.08A5.99 5.99 0 0 1 12 18c-3.31 0-6-2.69-6-6s2.69-6 6-6c1.66 0 3.14.69 4.22 1.78L13 11h7V4l-2.35 2.35z"
                            />
                          </svg>
                        </button>
                      ) : null}
                      <button
                        type="button"
                        className={`danger-btn action-btn gateway-delete-btn ${isDeletingThis ? "is-busy" : ""}`}
                        onClick={() => void handleDeleteGateway(gateway.code)}
                        title={isDeletingThis ? t("engineering.gateways.deleting") : t("engineering.gateways.delete")}
                        aria-label={t("engineering.gateways.delete")}
                        aria-busy={isDeletingThis || undefined}
                        disabled={isDeletingThis || anotherDeleting}
                      >
                        {isDeletingThis ? (
                          <span className="btn-spinner" aria-hidden="true" />
                        ) : (
                          <svg viewBox="0 0 24 24" width="18" height="18" aria-hidden="true">
                            <path
                              fill="currentColor"
                              d="M6 19a2 2 0 0 0 2 2h8a2 2 0 0 0 2-2V7H6zm3.46-7.12 1.41-1.41L12 11.59l1.12-1.12 1.41 1.41L13.41 13l1.12 1.12-1.41 1.41L12 14.41l-1.12 1.12-1.41-1.41L10.59 13zM15.5 4l-1-1h-5l-1 1H5v2h14V4z"
                            />
                          </svg>
                        )}
                      </button>
                    </div>
                  ) : null}
                </div>
                {isDeletingThis ? (
                  <div className="gateway-item-deleting-overlay" role="status" aria-live="polite">
                    <span className="btn-spinner gateway-deleting-spinner" aria-hidden="true" />
                    <span className="gateway-deleting-text">{t("engineering.gateways.deleting")}</span>
                  </div>
                ) : null}
              </div>
            );
            })}
          </div>

        </div>

        <div className="device-management-middle">
          <div className="device-panel-heading">
            <h4>{t("engineering.devicesPanel.title")}</h4>
            <button
              type="button"
              className="device-add-plus-btn device-add-plus-btn--primary"
              onClick={() => setShowCreateModal(true)}
              disabled={!selectedGatewayCode || !licenseStatus?.can_add_device}
              title={
                licenseStatus?.can_add_device
                  ? t("engineering.devicesPanel.addDevice")
                  : t("engineering.devicesPanel.licenseBlocked")
              }
              aria-label={t("engineering.devicesPanel.addDevice")}
            >
              +
            </button>
          </div>
          {!licenseStatus?.can_add_device ? (
            <div className="device-license-warning" role="alert">
              <span className="material-symbols-outlined" aria-hidden="true">warning</span>
              <span>
                {licenseStatus?.is_valid && licenseStatus.device_limit > 0
                  ? t("engineering.devicesPanel.licenseLimitWarning", {
                      used: licenseStatus.device_count,
                      limit: licenseStatus.device_limit
                    })
                  : t("engineering.devicesPanel.licenseUnavailableWarning")}
              </span>
            </div>
          ) : null}
          <div className="device-group-list">
            {devices.map((device) => {
              const effStatus = effectiveCommStatus(device, gateways);
              return (
              <button
                key={device.id}
                className={`device-group-item device-item ${selectedDeviceCode === device.code ? "active" : ""}`}
                onClick={() => handleDeviceSelect(device)}
              >
                <div className="device-title-row">
                  <div className="device-name-with-status">
                    <span className={`device-status-dot ${deviceCommDotClass(effStatus)}`} />
                    <strong>{device.name}</strong>
                  </div>
                  <span className="device-status-sr-only">
                    {effStatus === "online" ? t("engineering.devicesPanel.commOnline") : t("engineering.devicesPanel.commOffline")}
                  </span>
                </div>
                <div className="device-meta-row">
                  <span>{device.code}</span>
                  <span className="device-ip-text">
                    {device.ipAddress ?? "-"}
                    {canSeeDnp3 ? `:${device.dnp3OutstationPort ?? 20001}` : ""}
                  </span>
                </div>
              </button>
              );
            })}
            {devices.length === 0 ? <p className="helper-text">{t("engineering.devicesPanel.noDevicesUnderGateway")}</p> : null}
          </div>
        </div>

        <div className="device-management-right">
          <h4>{t("engineering.devicesPanel.properties")}</h4>
          {!selectedDevice ? (
            <div className="device-empty-state">
              <div className="device-empty-copy">
                <span className="material-symbols-outlined" aria-hidden="true">touch_app</span>
                <h5>{t("engineering.devicesPanel.selectHintTitle")}</h5>
                <p>{t("engineering.devicesPanel.selectHint")}</p>
              </div>
            </div>
          ) : (
            <div className="device-detail-form device-detail-form--tabbed">
              <div className="device-detail-form-fixed-header">
                <div className="device-props-tabs" role="tablist" aria-label={t("engineering.devicesPanel.tabsAria")}>
                  <button
                    type="button"
                    role="tab"
                    id="device-tab-system"
                    aria-selected={devicePropsTab === "system"}
                    className={devicePropsTab === "system" ? "active" : ""}
                    onClick={() => setDevicePropsTab("system")}
                  >
                    {t("engineering.devicesPanel.tabSystem")}
                  </button>
                  <button
                    type="button"
                    role="tab"
                    id="device-tab-comms"
                    aria-selected={devicePropsTab === "comms"}
                    className={devicePropsTab === "comms" ? "active" : ""}
                    onClick={() => setDevicePropsTab("comms")}
                  >
                    {t("engineering.devicesPanel.tabComms")}
                  </button>
                </div>
              </div>

              {devicePropsTab === "system" ? (
                <div
                  className="device-props-panel device-props-panel--system"
                  role="tabpanel"
                  id="device-panel-system"
                  aria-labelledby="device-tab-system"
                >
                  <div className="device-system-layout">
                    <div className="device-system-top-row">
                      <div className="device-info-card">
                        <label>
                          {t("engineering.devicesPanel.form.serialNo")}
                          <input value={selectedDevice.code} disabled readOnly />
                        </label>
                        <label>
                          {t("engineering.devicesPanel.form.name")}
                          <input value={name} onChange={(event) => setName(event.target.value)} />
                        </label>
                        <label>
                          {t("engineering.devicesPanel.form.deviceType")}
                          <select value={model} onChange={(event) => setModel(event.target.value)}>
                            {deviceModels.length === 0 ? (
                              <option value={model}>{model}</option>
                            ) : (
                              deviceModels.map((opt) => (
                                <option key={opt.code} value={opt.code}>
                                  {opt.label}
                                </option>
                              ))
                            )}
                          </select>
                        </label>
                        <label>
                          {t("engineering.devicesPanel.form.installationDate")}
                          <input
                            type="date"
                            value={installationDate}
                            onChange={(event) => setInstallationDate(event.target.value)}
                          />
                        </label>
                      </div>
                      <div className="device-visual-card">
                        <img src={deviceImageSrc(model)} alt={t("engineering.devicesPanel.deviceImageAlt")} />
                      </div>
                    </div>
                    <label className="device-description-field">
                      {t("engineering.devicesPanel.form.description")}
                      <textarea
                        value={description}
                        onChange={(event) => setDescription(event.target.value)}
                        rows={6}
                      />
                    </label>
                  </div>
                </div>
              ) : (
                <div
                  className="device-props-panel device-props-panel--comms"
                  role="tabpanel"
                  id="device-panel-comms"
                  aria-labelledby="device-tab-comms"
                >
                  <div className="device-props-comms-scroll">
                    <div className="device-comms-connection-grid">
                      {canSeeDnp3 ? (
                        <label>
                          {t("engineering.dnp3.endpointType")}
                          <select
                            value={dnp3Ext.ip_endpoint_type}
                            onChange={(event) => setDnp3Ext((prev) => ({
                              ...prev,
                              ip_endpoint_type: event.target.value as "listening" | "initiating"
                            }))}
                          >
                            <option value="listening">{t("engineering.dnp3.modeListening")}</option>
                            <option value="initiating">{t("engineering.dnp3.modeInitiating")}</option>
                          </select>
                        </label>
                      ) : null}
                      {dnp3Ext.ip_endpoint_type === "initiating" ? (
                        <>
                          <label>
                            {t("engineering.dnp3.masterIp")}
                            <input value={selectedDeviceGateway?.control_host || "127.0.0.1"} disabled readOnly />
                          </label>
                          <label>
                            {t("engineering.dnp3.masterPort")}
                            <input value={selectedInitiatingMasterPort} disabled readOnly />
                          </label>
                          <label>
                            {t("engineering.dnp3.masterAddr")}
                            <input value={dnp3Ext.master_address} disabled readOnly />
                          </label>
                        </>
                      ) : (
                        <>
                          <label>
                            {t("engineering.devicesPanel.form.ipShort")}
                            <input
                              value={ipAddress}
                              onChange={(event) => setIpAddress(event.target.value)}
                              required
                            />
                          </label>
                          {canSeeDnp3 ? (
                            <>
                              <label>
                                {t("engineering.devicesPanel.form.port")}
                                <input
                                  type="number"
                                  min={1}
                                  max={65535}
                                  value={dnp3OutstationPort}
                                  onChange={(event) => setDnp3OutstationPort(event.target.value)}
                                />
                              </label>
                              <label>
                                {t("engineering.devicesPanel.form.dnp3Address")}
                                <input
                                  type="number"
                                  value={dnp3Address}
                                  onChange={(event) => setDnp3Address(event.target.value)}
                                />
                              </label>
                            </>
                          ) : null}
                        </>
                      )}
                    </div>
                    <div className="device-detail-form-grid device-detail-form-grid--runtime">
                      <label>
                        {t("engineering.devicesPanel.form.pollInterval")}
                        <input
                          type="number"
                          min={1}
                          max={3600}
                          value={pollIntervalSec}
                          onChange={(event) => setPollIntervalSec(event.target.value)}
                        />
                      </label>
                      <label>
                        {t("engineering.devicesPanel.form.timeout")}
                        <input
                          type="number"
                          min={100}
                          max={60000}
                          value={timeoutMs}
                          onChange={(event) => setTimeoutMs(event.target.value)}
                        />
                      </label>
                      <label>
                        {t("engineering.devicesPanel.form.retry")}
                        <input
                          type="number"
                          min={0}
                          max={10}
                          value={retryCount}
                          onChange={(event) => setRetryCount(event.target.value)}
                        />
                      </label>
                    </div>
                    {canSeeDnp3 ? (
                      <Dnp3SettingsForm
                        value={dnp3Ext}
                        onChange={(patch) => setDnp3Ext((prev) => ({ ...prev, ...patch }))}
                        hideConnectionFields
                        usedMasterPorts={devices
                          .filter(
                            (x) =>
                              x.code !== selectedDevice?.code &&
                              x.dnp3Extended?.ip_endpoint_type === "initiating"
                          )
                          .map((x) => Number(x.dnp3Extended?.master_ip_port) || 0)
                          .filter((p) => p > 0)}
                      />
                    ) : null}
                  </div>
                </div>
              )}

              <div className="device-form-footer-bar">
                <div className="device-comms-footer-subtle">
                  {(() => {
                    const eff = effectiveCommStatus(selectedDevice, gateways);
                    const gwOffline =
                      eff === "offline" && selectedDevice.communicationStatus === "online";
                    return (
                      <span className={`device-comms-pill device-comms-pill--${deviceCommDotClass(eff)}`}>
                        {eff === "online"
                          ? t("engineering.devicesPanel.footer.ok")
                          : gwOffline
                            ? t("engineering.devicesPanel.footer.gwDisconnected")
                            : t("engineering.devicesPanel.footer.stale")}
                      </span>
                    );
                  })()}
                  {selectedDevice.lastUpdateAt ? (
                    <span className="device-comms-meta"> · {t("engineering.devicesPanel.footer.lastData")} {formatTrRel(selectedDevice.lastUpdateAt)}</span>
                  ) : null}
                  {selectedGateway ? <span className="device-comms-meta"> · {selectedGateway.name}</span> : null}
                </div>
                <div className="device-form-actions">
                  <button type="button" className="primary-btn" onClick={() => void handleSaveDevice()}>
                    {t("engineering.devicesPanel.save")}
                  </button>
                  <button type="button" className="danger-btn" onClick={() => void handleDeleteDevice()}>
                    {t("engineering.devicesPanel.delete")}
                  </button>
                </div>
              </div>
            </div>
          )}
        </div>
      </div>

      {inventoryError ? <p className="error-text">{inventoryError}</p> : null}
      {error ? <p className="error-text">{error}</p> : null}

      {showGatewayCreateModal ? (
        <div className="settings-modal-backdrop">
          <form className="settings-modal" onSubmit={handleCreateGateway}>
            <h3>{t("engineering.gateways.newGatewayModal")}</h3>
            <label>
              {t("engineering.gateways.form.code")}
              <input
                value={gatewayCode}
                onChange={(event) => setGatewayCode(event.target.value)}
                required
                placeholder={t("engineering.gateways.form.codePlaceholder")}
              />
            </label>
            <label>
              {t("engineering.gateways.form.name")}
              <input
                value={gatewayName}
                onChange={(event) => setGatewayName(event.target.value)}
                required
                placeholder={t("engineering.gateways.form.namePlaceholder")}
              />
            </label>
            <label>
              {t("engineering.gateways.form.token")}
              <div style={{ display: "flex", gap: "0.5rem" }}>
                <input
                  style={{ flex: 1 }}
                  value={gatewayToken}
                  onChange={(event) => setGatewayToken(event.target.value)}
                  required
                  minLength={16}
                  placeholder={t("engineering.gateways.form.tokenPlaceholder")}
                />
                <button type="button" className="secondary-btn" onClick={generateGatewayToken}>
                  {t("engineering.gateways.form.generate")}
                </button>
              </div>
            </label>
            <div className="modal-actions">
              <button type="button" className="secondary-btn" onClick={() => setShowGatewayCreateModal(false)}>
                {t("engineering.gateways.form.cancel")}
              </button>
              <button type="submit" className="primary-btn">
                {t("engineering.gateways.form.create")}
              </button>
            </div>
          </form>
        </div>
      ) : null}

      {composeFor
        ? (() => {
            const composeGw = gateways.find((g) => g.code === composeFor);
            const composeLive = composeGw ? getGatewayLiveness(composeGw) : null;
            const composeCmd = `docker compose -f e1-gw-${composeFor.toLowerCase()}.yml up -d`;
            const copyCmd = async () => {
              // Iki katmanli copy: modern Clipboard API (HTTPS gerekir) ve eski
              // execCommand fallback'i (HTTP ortamlari + IE/eski tarayicilar icin).
              // VDS HTTPS'siz erisimde Clipboard API silent fail eder; fallback kritik.
              let ok = false;
              try {
                if (navigator.clipboard && window.isSecureContext) {
                  await navigator.clipboard.writeText(composeCmd);
                  ok = true;
                }
              } catch {
                ok = false;
              }
              if (!ok) {
                // Fallback: hidden textarea + execCommand("copy"). Deprecated
                // ama hala butun major tarayicilarda calisir.
                try {
                  const ta = document.createElement("textarea");
                  ta.value = composeCmd;
                  ta.style.position = "fixed";
                  ta.style.top = "0";
                  ta.style.left = "0";
                  ta.style.opacity = "0";
                  document.body.appendChild(ta);
                  ta.focus();
                  ta.select();
                  ok = document.execCommand("copy");
                  document.body.removeChild(ta);
                } catch {
                  ok = false;
                }
              }
              if (ok) {
                setComposeCopied(true);
                window.setTimeout(() => setComposeCopied(false), 1500);
              } else {
                // Kullaniciya sessiz kalmayalim — manuel kopyalamasi icin uyari
                window.alert("Kopyalama basarisiz. Asagidaki komutu elle secip kopyalayin:\n\n" + composeCmd);
              }
            };
            return (
              <div className="settings-modal-backdrop">
                <form className="settings-modal" onSubmit={handleDownloadComposeSubmit}>
                  <h3>{t("engineering.gateways.compose.title", { code: composeFor })}</h3>
                  <div className="compose-cmd-row">
                    <code className="compose-cmd-text">{composeCmd}</code>
                    <button
                      type="button"
                      className="secondary-btn compose-cmd-copy"
                      onClick={() => void copyCmd()}
                    >
                      {composeCopied ? t("engineering.gateways.compose.copied") : t("engineering.gateways.compose.copy")}
                    </button>
                  </div>
                  <label>
                    {t("engineering.gateways.compose.backendIp")}
                    <input
                      value={composeBackendIp}
                      onChange={(event) => setComposeBackendIp(event.target.value)}
                      placeholder={t("engineering.gateways.compose.backendIpPlaceholder")}
                      required
                    />
                  </label>
                  {composeLive ? (
                    <div className={`compose-gw-status compose-gw-status--${composeLive.className}`}>
                      <span className="compose-gw-status-dot" aria-hidden="true" />
                      <span>
                        {t("engineering.gateways.compose.gwStatus")} <strong>{composeLive.title}</strong>
                        {composeGw?.last_seen_at ? (
                          <span className="compose-gw-status-meta">
                            {" "}· {t("engineering.gateways.compose.lastSignal", { at: formatTrRel(composeGw.last_seen_at) })}
                          </span>
                        ) : null}
                      </span>
                    </div>
                  ) : null}
                  {composeError ? <p className="error-text">{composeError}</p> : null}
                  <div className="modal-actions">
                    <button
                      type="button"
                      className="secondary-btn"
                      onClick={() => setComposeFor(null)}
                      disabled={composeBusy}
                    >
                      {t("engineering.gateways.compose.close")}
                    </button>
                    <button type="submit" className="primary-btn" disabled={composeBusy}>
                      {composeBusy ? t("engineering.gateways.compose.downloading") : t("engineering.gateways.compose.download")}
                    </button>
                  </div>
                </form>
              </div>
            );
          })()
        : null}

      {showCreateModal ? (
        <div className="settings-modal-backdrop">
          <form className="settings-modal device-create-modal device-create-modal--visual" onSubmit={handleCreateDevice}>
            <h3>{t("engineering.devicesPanel.newDeviceModal")}</h3>
            <div className="device-create-system-layout">
              <div className="device-system-top-row">
                <div className="device-info-card">
                  <label>
                    {t("engineering.devicesPanel.form.serialNo")}
                    <input value={createCode} onChange={(event) => setCreateCode(event.target.value)} required />
                  </label>
                  <label>
                    {t("engineering.devicesPanel.form.name")}
                    <input value={createName} onChange={(event) => setCreateName(event.target.value)} required />
                  </label>
                  <label>
                    {t("engineering.devicesPanel.form.deviceType")}
                    <select value={createModel} onChange={(event) => setCreateModel(event.target.value)} required>
                      {deviceModels.length === 0 ? (
                        <option value={createModel}>{createModel}</option>
                      ) : (
                        deviceModels.map((opt) => (
                          <option key={opt.code} value={opt.code}>
                            {opt.label}
                          </option>
                        ))
                      )}
                    </select>
                  </label>
                  <label>
                    {t("engineering.devicesPanel.form.installationDate")}
                    <input
                      type="date"
                      value={createInstallationDate}
                      onChange={(event) => setCreateInstallationDate(event.target.value)}
                    />
                  </label>
                </div>
                <div className="device-visual-card">
                  <img src={deviceImageSrc(createModel)} alt={t("engineering.devicesPanel.deviceImageAlt")} />
                </div>
              </div>
            </div>
            <div className="device-create-comms-grid">
              {canSeeDnp3 ? (
                <label>
                  {t("engineering.dnp3.endpointType")}
                  <select
                    value={createDnp3Ext.ip_endpoint_type}
                    onChange={(event) => setCreateDnp3Ext((prev) => ({
                      ...prev,
                      ip_endpoint_type: event.target.value as "listening" | "initiating"
                    }))}
                  >
                    <option value="listening">{t("engineering.dnp3.modeListening")}</option>
                    <option value="initiating">{t("engineering.dnp3.modeInitiating")}</option>
                  </select>
                </label>
              ) : null}
              {createDnp3Ext.ip_endpoint_type === "initiating" ? (
                <>
                  <label>
                    {t("engineering.dnp3.masterIp")}
                    <input value={selectedGateway?.control_host || "127.0.0.1"} disabled readOnly />
                  </label>
                  <label>
                    {t("engineering.dnp3.masterPort")}
                    <input value={nextInitiatingMasterPort} disabled readOnly />
                  </label>
                  <label>
                    {t("engineering.dnp3.masterAddr")}
                    <input value={createDnp3Ext.master_address} disabled readOnly />
                  </label>
                </>
              ) : (
                <>
                  <label>
                    {t("engineering.devicesPanel.form.ipAddress")}
                    <input
                      value={createIpAddress}
                      onChange={(event) => setCreateIpAddress(event.target.value)}
                      placeholder="192.168.1.50"
                      required
                    />
                  </label>
                  {canSeeDnp3 ? (
                    <>
                      <label>
                        {t("engineering.devicesPanel.form.port")}
                        <input
                          type="number"
                          min={1}
                          max={65535}
                          value={createDnp3OutstationPort}
                          onChange={(event) => setCreateDnp3OutstationPort(event.target.value)}
                          required
                        />
                      </label>
                      <label>
                        {t("engineering.devicesPanel.form.dnp3Address")}
                        <input
                          type="number"
                          min={1}
                          value={createDnp3Address}
                          onChange={(event) => setCreateDnp3Address(event.target.value)}
                          required
                        />
                      </label>
                    </>
                  ) : null}
                </>
              )}
            </div>
            <p className="helper-text">{t("engineering.devicesPanel.form.createMinimalHint")}</p>
            <div className="modal-actions">
              <button type="button" className="secondary-btn" onClick={() => setShowCreateModal(false)}>
                {t("engineering.devicesPanel.form.cancel")}
              </button>
              <button type="submit" className="primary-btn">
                {t("engineering.devicesPanel.form.create")}
              </button>
            </div>
          </form>
        </div>
      ) : null}

      {showGatewayEditModal ? (
        <div className="settings-modal-backdrop">
          <form className="settings-modal" onSubmit={handleUpdateGateway}>
            <h3>{t("engineering.gateways.editGatewayModal")}</h3>
            <label>
              {t("engineering.gateways.form.code")}
              <input value={editGatewayCode} disabled readOnly />
            </label>
            <label>
              {t("engineering.gateways.form.name")}
              <input value={editGatewayName} onChange={(event) => setEditGatewayName(event.target.value)} required />
            </label>
            <label>
              {t("engineering.gateways.form.host")}
              <input value={editGatewayHost} onChange={(event) => setEditGatewayHost(event.target.value)} required />
            </label>
            <label>
              {t("engineering.gateways.form.port")}
              <input
                type="number"
                min={1}
                max={65535}
                value={editGatewayPort}
                onChange={(event) => setEditGatewayPort(event.target.value)}
                required
              />
            </label>
            <label>
              {t("engineering.gateways.form.token")}
              <input value={editGatewayToken} onChange={(event) => setEditGatewayToken(event.target.value)} required />
            </label>
            <div className="modal-actions">
              <button type="button" className="secondary-btn" onClick={() => setShowGatewayEditModal(false)}>
                {t("engineering.gateways.form.cancel")}
              </button>
              <button type="submit" className="primary-btn">
                {t("engineering.gateways.form.save")}
              </button>
            </div>
          </form>
        </div>
      ) : null}

    </section>
  );
}
