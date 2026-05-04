import { useEffect, useMemo, useRef, useState, type FormEvent } from "react";
import { MapContainer, Marker, TileLayer, useMapEvents } from "react-leaflet";
import L from "leaflet";
import type { DeviceModelOption, DeviceRow, Dnp3ExtendedSettings, Gateway } from "../../shared/types";
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
  if (s < 5) return "şimdi";
  if (s < 60) return `${s} sn önce`;
  if (s < 3600) return `${Math.round(s / 60)} dk önce`;
  if (s < 86400) return `${Math.round(s / 3600)} sa önce`;
  return d.toLocaleString("tr-TR");
}

function getGatewayLiveness(gateway: Gateway): GatewayLiveness {
  if (!gateway.is_active) {
    return { className: "inactive", title: "Pasif (yayın kapalı)" };
  }
  if (!gateway.last_seen_at) {
    return { className: "never", title: "Merkezle temas yok" };
  }
  const sec = (Date.now() - new Date(gateway.last_seen_at).getTime()) / 1000;
  if (sec < GATEWAY_LIVE_SEC) {
    return { className: "online", title: "Çevrimiçi" };
  }
  return { className: "offline", title: "Bağlı değil (son sinyal eski)" };
}

function deviceCommDotClass(status: DeviceRow["communicationStatus"]): "online" | "offline" {
  return status === "online" ? "online" : "offline";
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
  role: "operator" | "engineer" | "installer";
  gateways: Gateway[];
  devices: DeviceRow[];
  deviceModels: DeviceModelOption[];
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
  }) => Promise<void>;
  onUpdateGateway: (
    gatewayCode: string,
    payload: { name?: string; host?: string; listen_port?: number; token?: string }
  ) => Promise<void>;
  onDeleteGateway: (gatewayCode: string) => Promise<void>;
  onDownloadCompose: (
    gatewayCode: string,
    params: { backendUrl: string; hostPort: number; fmt: "compose" | "env" }
  ) => Promise<void>;
  onCreate: (payload: {
    code: string;
    name: string;
    description?: string | null;
    model: string;
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
  deviceModels,
  onSelectGateway,
  onCreateGateway,
  onUpdateGateway,
  onDeleteGateway,
  onDownloadCompose,
  onCreate,
  onUpdate,
  onDelete
}: Props) {
  const canManageGateways = role === "installer";
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
      // varsayilan port (8000) + /api/v1 ile tamamlanir. Boylece kullanici port
      // ve path bilgileriyle ugrasmaz.
      const backendUrl = `http://${ip}:8000/api/v1`;
      await onDownloadCompose(composeFor, {
        backendUrl,
        // hostPort verilmezse backend gateway sirasina gore 8020/8021/... atar.
        hostPort: 0,
        fmt: "compose"
      });
      // Modal acik kalir — kullanici docker komutunu kopyalamak isteyebilir.
    } catch (err) {
      setComposeError(err instanceof Error ? err.message : "Dosya indirilemedi.");
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
  const [createDescription, setCreateDescription] = useState("");
  const [createModel, setCreateModel] = useState("horstmann_sn_2_0");
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
  const [showMapPicker, setShowMapPicker] = useState(false);
  const [pickerLat, setPickerLat] = useState(39);
  const [pickerLon, setPickerLon] = useState(35);
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
    if (!selectedGatewayCode || !exists) {
      setSelectedDeviceCode("");
      const nextGatewayCode = gateways[0].code;
      setSelectedGatewayCode(nextGatewayCode);
      void onSelectGateway(nextGatewayCode);
    }
  }, [gateways, selectedGatewayCode, onSelectGateway]);

  const applySelectedDeviceToForm = (device: DeviceRow) => {
    setName(device.name);
    setDescription(device.description ?? "");
    setModel(device.model ?? "horstmann_sn_2_0");
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
        gateway_code: targetGateway,
        ip_address: ipAddress,
        dnp3_outstation_port: Number(dnp3OutstationPort),
        dnp3_address: Number(dnp3Address),
        dnp3_extended: { ...dnp3Ext, ip_endpoint_type: "listening" },
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
      setError(err instanceof Error ? err.message : "Cihaz güncellenemedi.");
    }
  };

  const handleDeleteDevice = async () => {
    if (!selectedDevice) return;
    if (!window.confirm(`"${selectedDevice.name}" cihazı silinsin mi?`)) return;
    setError("");
    try {
      await onDelete(selectedDevice.code);
      setSelectedDeviceCode("");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Cihaz silinemedi.");
    }
  };

  const handleCreateDevice = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setError("");
    try {
      await onCreate({
        code: createCode,
        name: createName,
        description: createDescription.trim() || null,
        model: createModel,
        gateway_code: selectedGatewayCode || null,
        // Cihazin DNP3 outstation IP adresi. Gateway listening modunda buraya
        // bagdir. Bos olursa form 'required' kuralina takilir; bu nedenle
        // explicit default vermeye gerek yok.
        ip_address: createIpAddress.trim(),
        dnp3_outstation_port: Number(createDnp3OutstationPort),
        dnp3_address: Number(createDnp3Address),
        dnp3_extended: { ...createDnp3Ext, ip_endpoint_type: "listening" },
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
      setCreateDescription("");
      setCreateModel("horstmann_sn_2_0");
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
      setError(err instanceof Error ? err.message : "Cihaz oluşturulamadı.");
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
        control_port: 0
      });
      setShowGatewayCreateModal(false);
      setGatewayCode("");
      setGatewayName("");
      setGatewayToken("");
      openComposeModal(createdCode);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Gateway oluşturulamadı.");
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
      setError(err instanceof Error ? err.message : "Gateway silinemedi.");
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
      setError(err instanceof Error ? err.message : "Gateway güncellenemedi.");
    }
  };

  const handleOpenMapPicker = () => {
    setPickerLat(Number(latitude) || 39);
    setPickerLon(Number(longitude) || 35);
    setShowMapPicker(true);
  };

  const handleApplyMapLocation = () => {
    setLatitude(String(pickerLat));
    setLongitude(String(pickerLon));
    setShowMapPicker(false);
  };

  const mapPickerIcon = L.divIcon({
    className: "device-pin-wrapper",
    html: `<span class="device-pin" style="background:#2563eb"></span>`,
    iconSize: [20, 20],
    iconAnchor: [10, 10]
  });

  function LocationPicker() {
    useMapEvents({
      click(event) {
        setPickerLat(Number(event.latlng.lat.toFixed(6)));
        setPickerLon(Number(event.latlng.lng.toFixed(6)));
      }
    });
    return <Marker position={[pickerLat, pickerLon]} icon={mapPickerIcon} />;
  }

  // Yeni cihaz oluşturma modalındaki gömülü harita için: tıklama lat/lon
  // string state'lerini doğrudan günceller.
  function CreateModalLocationPicker() {
    const lat = Number(createLatitude) || 0;
    const lon = Number(createLongitude) || 0;
    useMapEvents({
      click(event) {
        setCreateLatitude(event.latlng.lat.toFixed(6));
        setCreateLongitude(event.latlng.lng.toFixed(6));
      }
    });
    return <Marker position={[lat, lon]} icon={mapPickerIcon} />;
  }

  return (
    <section className="tab-panel device-management-panel">
      <div className="device-management-layout">
        <div className="device-management-left">
          <h4>Gatewayler</h4>
          {canManageGateways ? (
            <div className="section-actions">
              <button
                className="secondary-btn action-btn full-width-btn"
                onClick={() => setShowGatewayCreateModal(true)}
              >
                Gateway Ekle
              </button>
            </div>
          ) : null}
          <div className="device-group-list">
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
                        title="Docker Compose / .env indir"
                        aria-label="Docker Compose indir"
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
                        title="Gateway Düzenle"
                        aria-label="Gateway Düzenle"
                        disabled={isDeletingThis || anotherDeleting}
                      >
                        <svg viewBox="0 0 24 24" width="18" height="18" aria-hidden="true">
                          <path
                            fill="currentColor"
                            d="M3 17.25V21h3.75L17.81 9.94l-3.75-3.75zM20.71 7.04a1 1 0 0 0 0-1.41L18.37 3.29a1 1 0 0 0-1.41 0l-1.83 1.83 3.75 3.75z"
                          />
                        </svg>
                      </button>
                      <button
                        type="button"
                        className={`danger-btn action-btn gateway-delete-btn ${isDeletingThis ? "is-busy" : ""}`}
                        onClick={() => void handleDeleteGateway(gateway.code)}
                        title={isDeletingThis ? "Siliniyor..." : "Gateway Sil"}
                        aria-label="Gateway Sil"
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
                    <span className="gateway-deleting-text">Gateway siliniyor...</span>
                  </div>
                ) : null}
              </div>
            );
            })}
          </div>

        </div>

        <div className="device-management-middle">
          <h4>Cihazlar</h4>
          <div className="section-actions">
            <button className="add-user-btn full-width-btn" onClick={() => setShowCreateModal(true)} disabled={!selectedGatewayCode}>
              Cihaz Ekle
            </button>
          </div>
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
                    {effStatus === "online" ? "Haberleşme Var" : "Haberleşme Yok veya yoklama yok"}
                  </span>
                </div>
                <div className="device-meta-row">
                  <span>{device.code}</span>
                  <span className="device-ip-text">
                    {device.ipAddress ?? "-"}:{device.dnp3OutstationPort ?? 20001}
                  </span>
                </div>
              </button>
              );
            })}
            {devices.length === 0 ? <p className="helper-text">Bu gateway altında henüz cihaz yok.</p> : null}
          </div>
        </div>

        <div className="device-management-right">
          <h4>Cihaz Özellikleri</h4>
          {!selectedDevice ? (
            <p className="helper-text">Sağ panelde düzenlemek için soldan bir cihaz seçin.</p>
          ) : (
            <div className="device-detail-form device-detail-form--tabbed">
              <div className="device-detail-form-fixed-header">
                <div className="device-props-tabs" role="tablist" aria-label="Cihaz özellik sekmeleri">
                  <button
                    type="button"
                    role="tab"
                    id="device-tab-system"
                    aria-selected={devicePropsTab === "system"}
                    className={devicePropsTab === "system" ? "active" : ""}
                    onClick={() => setDevicePropsTab("system")}
                  >
                    Sistem
                  </button>
                  <button
                    type="button"
                    role="tab"
                    id="device-tab-comms"
                    aria-selected={devicePropsTab === "comms"}
                    className={devicePropsTab === "comms" ? "active" : ""}
                    onClick={() => setDevicePropsTab("comms")}
                  >
                    Haberleşme
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
                  <p className="device-props-panel-hint">
                    Cihaz tanıtımı, görünen ad, konum: merkez listeleri ve harita bu bilgileri kullanır.
                  </p>
                  <div className="device-detail-form-grid">
                    <label>
                      Cihaz Kodu
                      <input value={selectedDevice.code} disabled readOnly />
                    </label>
                    <label>
                      İsim
                      <input value={name} onChange={(event) => setName(event.target.value)} />
                    </label>
                    <label>
                      Gateway
                      <select
                        value={deviceGatewayCode}
                        onChange={(event) => setDeviceGatewayCode(event.target.value)}
                      >
                        {gateways.length === 0 ? (
                          <option value="">— Gateway yok —</option>
                        ) : (
                          gateways.map((gw) => (
                            <option key={gw.code} value={gw.code}>
                              {gw.name} ({gw.code})
                            </option>
                          ))
                        )}
                      </select>
                    </label>
                    <label>
                      Model
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
                      Açıklama
                      <input value={description} onChange={(event) => setDescription(event.target.value)} />
                    </label>
                    <label>
                      Enlem
                      <input value={latitude} onChange={(event) => setLatitude(event.target.value)} />
                    </label>
                    <label>
                      Boylam
                      <input value={longitude} onChange={(event) => setLongitude(event.target.value)} />
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
                    <div className="device-detail-form-grid">
                      <label>
                        Outstation IP
                        <input
                          value={ipAddress}
                          onChange={(event) => setIpAddress(event.target.value)}
                          required
                        />
                      </label>
                      <label>
                        Outstation port
                        <input
                          type="number"
                          min={1}
                          max={65535}
                          value={dnp3OutstationPort}
                          onChange={(event) => setDnp3OutstationPort(event.target.value)}
                        />
                      </label>
                      <label>
                        DNP3 Outstation adresi
                        <input
                          type="number"
                          value={dnp3Address}
                          onChange={(event) => setDnp3Address(event.target.value)}
                        />
                      </label>
                      <label>
                        Poll aralığı (sn)
                        <input
                          type="number"
                          min={1}
                          max={3600}
                          value={pollIntervalSec}
                          onChange={(event) => setPollIntervalSec(event.target.value)}
                        />
                      </label>
                      <label>
                        Timeout (ms)
                        <input
                          type="number"
                          min={100}
                          max={60000}
                          value={timeoutMs}
                          onChange={(event) => setTimeoutMs(event.target.value)}
                        />
                      </label>
                      <label>
                        Retry
                        <input
                          type="number"
                          min={0}
                          max={10}
                          value={retryCount}
                          onChange={(event) => setRetryCount(event.target.value)}
                        />
                      </label>
                    </div>
                    <Dnp3SettingsForm
                      value={dnp3Ext}
                      onChange={(patch) => setDnp3Ext((prev) => ({ ...prev, ...patch }))}
                      usedMasterPorts={devices
                        .filter(
                          (x) =>
                            x.code !== selectedDevice?.code &&
                            x.dnp3Extended?.ip_endpoint_type === "initiating"
                        )
                        .map((x) => Number(x.dnp3Extended?.master_ip_port) || 0)
                        .filter((p) => p > 0)}
                    />
                    <div className="device-iec104-section">
                      <h5 className="device-iec104-section-title">IEC 60870-5-104 (Outbound)</h5>
                      <div className="device-detail-form-grid">
                        <label>
                          ASDU Common Address (CA)
                          <input
                            type="number"
                            min={0}
                            max={65534}
                            placeholder="örn. 1, 2, 3..."
                            value={iec104CommonAddress}
                            onChange={(event) => setIec104CommonAddress(event.target.value)}
                          />
                        </label>
                      </div>
                      <p className="helper-text">
                        Bu cihazın IEC 104 ASDU Common Address'i. Boş bırakılırsa
                        outbound hedefin default CA'si kullanılır. Aynı TCP yayınında
                        farklı cihazlar farklı CA ile yayınlanır.
                      </p>
                    </div>
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
                          ? "OK"
                          : gwOffline
                            ? "Gateway bağlı değil"
                            : "Kesik / bekleniyor"}
                      </span>
                    );
                  })()}
                  {selectedDevice.lastUpdateAt ? (
                    <span className="device-comms-meta"> · Son veri: {formatTrRel(selectedDevice.lastUpdateAt)}</span>
                  ) : null}
                  {selectedGateway ? <span className="device-comms-meta"> · {selectedGateway.name}</span> : null}
                </div>
                <div className="device-form-actions">
                  <button type="button" className="secondary-btn" onClick={handleOpenMapPicker}>
                    Haritadan Seç
                  </button>
                  <button type="button" className="primary-btn" onClick={() => void handleSaveDevice()}>
                    Kaydet
                  </button>
                  <button type="button" className="danger-btn" onClick={() => void handleDeleteDevice()}>
                    Sil
                  </button>
                </div>
              </div>
            </div>
          )}
        </div>
      </div>

      {error ? <p className="error-text">{error}</p> : null}

      {showGatewayCreateModal ? (
        <div className="settings-modal-backdrop">
          <form className="settings-modal" onSubmit={handleCreateGateway}>
            <h3>Yeni Gateway Ekle</h3>
            <label>
              Gateway Kodu
              <input
                value={gatewayCode}
                onChange={(event) => setGatewayCode(event.target.value)}
                required
                placeholder="GW-001"
              />
            </label>
            <label>
              Gateway Adı
              <input
                value={gatewayName}
                onChange={(event) => setGatewayName(event.target.value)}
                required
                placeholder="Örn: Saha A SCADA"
              />
            </label>
            <label>
              Token
              <div style={{ display: "flex", gap: "0.5rem" }}>
                <input
                  style={{ flex: 1 }}
                  value={gatewayToken}
                  onChange={(event) => setGatewayToken(event.target.value)}
                  required
                  minLength={16}
                  placeholder="En az 16 karakter (Üret butonu rastgele 48 karakter üretir)"
                />
                <button type="button" className="secondary-btn" onClick={generateGatewayToken}>
                  Üret
                </button>
              </div>
            </label>
            <div className="modal-actions">
              <button type="button" className="secondary-btn" onClick={() => setShowGatewayCreateModal(false)}>
                İptal
              </button>
              <button type="submit" className="primary-btn">
                Oluştur
              </button>
            </div>
          </form>
        </div>
      ) : null}

      {composeFor
        ? (() => {
            const composeGw = gateways.find((g) => g.code === composeFor);
            const composeLive = composeGw ? getGatewayLiveness(composeGw) : null;
            const composeCmd = `docker compose -f hsl-gw-${composeFor.toLowerCase()}.yml up -d`;
            const copyCmd = async () => {
              try {
                await navigator.clipboard.writeText(composeCmd);
                setComposeCopied(true);
                window.setTimeout(() => setComposeCopied(false), 1500);
              } catch {
                /* clipboard yoksa sessiz gec */
              }
            };
            return (
              <div className="settings-modal-backdrop">
                <form className="settings-modal" onSubmit={handleDownloadComposeSubmit}>
                  <h3>Docker Compose İndir — {composeFor}</h3>
                  <div className="compose-cmd-row">
                    <code className="compose-cmd-text">{composeCmd}</code>
                    <button
                      type="button"
                      className="secondary-btn compose-cmd-copy"
                      onClick={() => void copyCmd()}
                    >
                      {composeCopied ? "Kopyalandı" : "Kopyala"}
                    </button>
                  </div>
                  <label>
                    Çatı Yazılımın IP Adresi
                    <input
                      value={composeBackendIp}
                      onChange={(event) => setComposeBackendIp(event.target.value)}
                      placeholder="192.168.1.50"
                      required
                    />
                  </label>
                  {composeLive ? (
                    <div className={`compose-gw-status compose-gw-status--${composeLive.className}`}>
                      <span className="compose-gw-status-dot" aria-hidden="true" />
                      <span>
                        Gateway durumu: <strong>{composeLive.title}</strong>
                        {composeGw?.last_seen_at ? (
                          <span className="compose-gw-status-meta">
                            {" "}· son sinyal {formatTrRel(composeGw.last_seen_at)}
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
                      Kapat
                    </button>
                    <button type="submit" className="primary-btn" disabled={composeBusy}>
                      {composeBusy ? "İndiriliyor..." : "İndir"}
                    </button>
                  </div>
                </form>
              </div>
            );
          })()
        : null}

      {showCreateModal ? (
        <div className="settings-modal-backdrop">
          <form className="settings-modal device-create-modal" onSubmit={handleCreateDevice}>
            <h3>Yeni Cihaz Ekle</h3>
            <label>
              Cihaz Kodu
              <input value={createCode} onChange={(event) => setCreateCode(event.target.value)} required />
            </label>
            <label>
              Cihaz Adı
              <input value={createName} onChange={(event) => setCreateName(event.target.value)} required />
            </label>
            <label>
              Model
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
              Açıklama
              <input value={createDescription} onChange={(event) => setCreateDescription(event.target.value)} />
            </label>
            <label>
              Outstation IP adresi
              <input
                value={createIpAddress}
                onChange={(event) => setCreateIpAddress(event.target.value)}
                placeholder="192.168.1.50"
                required
              />
            </label>
            <label>
              Outstation port
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
              DNP3 Outstation adresi
              <input
                type="number"
                min={1}
                value={createDnp3Address}
                onChange={(event) => setCreateDnp3Address(event.target.value)}
                required
              />
            </label>
            <Dnp3SettingsForm
              value={createDnp3Ext}
              onChange={(patch) => setCreateDnp3Ext((prev) => ({ ...prev, ...patch }))}
              usedMasterPorts={devices
                .filter((x) => x.dnp3Extended?.ip_endpoint_type === "initiating")
                .map((x) => Number(x.dnp3Extended?.master_ip_port) || 0)
                .filter((p) => p > 0)}
            />
            <label>
              Poll aralığı (sn)
              <input
                type="number"
                min={1}
                max={3600}
                value={createPollIntervalSec}
                onChange={(event) => setCreatePollIntervalSec(event.target.value)}
                required
              />
            </label>
            <label>
              Timeout (ms)
              <input
                type="number"
                min={100}
                max={60000}
                value={createTimeoutMs}
                onChange={(event) => setCreateTimeoutMs(event.target.value)}
                required
              />
            </label>
            <label>
              Retry
              <input
                type="number"
                min={0}
                max={10}
                value={createRetryCount}
                onChange={(event) => setCreateRetryCount(event.target.value)}
                required
              />
            </label>
            <div className="device-create-location">
              <div className="device-create-location-header">
                <strong>Konum</strong>
                <span className="helper-text">Haritaya tıklayarak veya değerleri elle girerek konumu belirleyin.</span>
              </div>
              <div className="device-create-location-coords">
                <label>
                  Enlem
                  <input
                    value={createLatitude}
                    onChange={(event) => setCreateLatitude(event.target.value)}
                    required
                  />
                </label>
                <label>
                  Boylam
                  <input
                    value={createLongitude}
                    onChange={(event) => setCreateLongitude(event.target.value)}
                    required
                  />
                </label>
              </div>
              <div className="device-create-location-map">
                <MapContainer
                  className="world-map"
                  center={[Number(createLatitude) || 39, Number(createLongitude) || 35]}
                  zoom={Number(createLatitude) && Number(createLongitude) ? 13 : 6}
                  scrollWheelZoom
                >
                  <TileLayer url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png" />
                  <CreateModalLocationPicker />
                </MapContainer>
              </div>
            </div>
            <div className="modal-actions">
              <button type="button" className="secondary-btn" onClick={() => setShowCreateModal(false)}>
                İptal
              </button>
              <button type="submit" className="primary-btn">
                Oluştur
              </button>
            </div>
          </form>
        </div>
      ) : null}

      {showGatewayEditModal ? (
        <div className="settings-modal-backdrop">
          <form className="settings-modal" onSubmit={handleUpdateGateway}>
            <h3>Gateway Düzenle</h3>
            <label>
              Gateway Kodu
              <input value={editGatewayCode} disabled readOnly />
            </label>
            <label>
              Gateway Adı
              <input value={editGatewayName} onChange={(event) => setEditGatewayName(event.target.value)} required />
            </label>
            <label>
              Host
              <input value={editGatewayHost} onChange={(event) => setEditGatewayHost(event.target.value)} required />
            </label>
            <label>
              Port
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
              Token
              <input value={editGatewayToken} onChange={(event) => setEditGatewayToken(event.target.value)} required />
            </label>
            <div className="modal-actions">
              <button type="button" className="secondary-btn" onClick={() => setShowGatewayEditModal(false)}>
                İptal
              </button>
              <button type="submit" className="primary-btn">
                Kaydet
              </button>
            </div>
          </form>
        </div>
      ) : null}

      {showMapPicker ? (
        <div className="settings-modal-backdrop">
          <div className="settings-modal map-picker-modal">
            <h3>Haritadan Konum Seç</h3>
            <p className="helper-text">Haritaya tıklayarak cihaz konumunu belirleyin.</p>
            <div className="map-picker-shell">
              <MapContainer className="world-map" center={[pickerLat, pickerLon]} zoom={7} scrollWheelZoom>
                <TileLayer url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png" />
                <LocationPicker />
              </MapContainer>
            </div>
            <div className="map-picker-coords">
              <span>Enlem: {pickerLat}</span>
              <span>Boylam: {pickerLon}</span>
            </div>
            <div className="modal-actions">
              <button type="button" className="secondary-btn" onClick={() => setShowMapPicker(false)}>
                İptal
              </button>
              <button type="button" className="primary-btn" onClick={handleApplyMapLocation}>
                Konumu Uygula
              </button>
            </div>
          </div>
        </div>
      ) : null}
    </section>
  );
}
