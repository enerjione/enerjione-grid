import { useEffect, useMemo, useRef, useState, type FormEvent } from "react";
import { asyncConfirm } from "../../components/ConfirmDialog";
import { useTranslation } from "react-i18next";
import i18n from "../../shared/i18n";
import type {
  DeviceModelOption,
  DeviceRow,
  Dnp3ExtendedSettings,
  Gateway,
  GatewayAgentStatus,
  LicenseStatus,
  LocalGateway
} from "../../shared/types";
import { DEFAULT_DNP3_EXTENDED, mergeDnp3Extended } from "../../shared/types";
import {
  deviceStatusUnderGateway,
  gatewayLiveness,
  type GatewayLivenessState
} from "../../shared/gatewayLiveness";
import {
  fetchGatewayAgentStatus,
  restartGatewayLocally,
  startGatewayLocally,
  stopGatewayLocally
} from "../../shared/api";
import { Dnp3SettingsForm } from "./Dnp3SettingsForm";
import { GatewayCreateModal } from "../gateways/GatewayCreateModal";
import { GatewayEditModal } from "../gateways/GatewayEditModal";

/** Host ajani durumunun tazelenme araligi. Gateway listesi zaten ~12 sn'de
 *  bir cekiliyor; container durumu daha yavas degisir. */
const AJAN_YOKLAMA_MS = 20000;
/** Islem surerken (durdur/baslat/yeniden baslat) hizli yoklama. */
const ISLEM_YOKLAMA_MS = 1500;
/** Yoklama tavani. Bu islemler saniyeler surer; asilirsa "bilmiyorum" denir,
 *  "basarili" DENMEZ. */
const ISLEM_TAVANI_MS = 120000;

/** Gateway kartinda suren yasam dongusu islemi. */
type GatewayIslem = {
  code: string;
  action: "stop" | "start" | "restart";
  /** gonderiliyor | uygulaniyor | stop | start | restart | done | bitti | zaman_asimi */
  asama: string;
  basarili?: boolean;
  mesaj?: string;
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

/** Durum -> `engineering.gatewayLive.*` etiketi. Karar mantigi
 *  `shared/gatewayLiveness.ts` icinde (React'tan bagimsiz, test edilir). */
function gatewayStateLabel(state: GatewayLivenessState): string {
  return i18n.t(`engineering.gatewayLive.${state}`);
}

function deviceCommDotClass(
  status: DeviceRow["communicationStatus"]
): "online" | "offline" | "unknown" {
  if (status === "online") return "online";
  // "unknown" ARTIK "offline"a katlanmiyor: gateway durdurulmusken cihazin
  // durumunu bilmiyoruz, ariza rengi gostermek yanlis bilgi olur.
  return status === "unknown" ? "unknown" : "offline";
}

const DEVICE_MODEL_IMAGES: Record<string, string> = {
  horstmann_sn_2_0: "/sn20.png"
};
const INITIATING_PORT_BASE_DEFAULT = 20100;

function deviceImageSrc(modelCode: string): string {
  return DEVICE_MODEL_IMAGES[modelCode] ?? "/sn20.png";
}

/** Gateway calismiyorken cihaz sinyali fiziksel olarak gelse bile platform
 * tarafinda yoktur. Ama "yok" ile "ariza" ayni sey degil: yalnizca gateway'in
 * AYAKTA oldugunu bilip veri alamadigimizda kirmizi "offline" dogru; gateway
 * durdurulmussa ya da durumu bilinmiyorsa cihaz da "unknown" olmali
 * (bkz. shared/gatewayLiveness.ts). */
function effectiveCommStatus(
  device: DeviceRow,
  gatewayStates: Map<string, GatewayLivenessState>
): DeviceRow["communicationStatus"] {
  const state = device.gatewayCode ? gatewayStates.get(device.gatewayCode) : undefined;
  if (!state) return device.communicationStatus;
  return deviceStatusUnderGateway(state, device.communicationStatus);
}

type DevicePropsTab = "system" | "comms";

type Props = {
  role: "operator" | "engineer" | "installer" | "ops_manager";
  /** Gateway kurulum sihirbazi host ajanina dogrudan sorgu attigi icin gerekli. */
  accessToken: string;
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
    payload: {
      name?: string;
      host?: string;
      listen_port?: number;
      token?: string;
      publish_dnp3_quality?: boolean;
    }
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
  accessToken,
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
  // Ajan durumunu OKUMA yetkisi durdurma yetkisinden GENIS: backend
  // `GET /gateways/local-agent` icin engineer'a da izin veriyor. Yoklamayi
  // `canManageGateways` ile kisitlarsak engineer her duran gateway'i
  // "Durum bilinmiyor" gorur — oysa sahada "veri neden gelmiyor" sorusunu
  // once o sorar. Dugmeler yine yalnizca installer'da.
  const canReadAgentStatus = role === "installer" || role === "engineer";
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

  // --- Host ajani (e1-gwd) durumu ---------------------------------------
  // Gateway kartinin "durduruldu" ile "ulasilamiyor"u ayirt edebilmesi icin
  // container durumu gerekiyor; bu bilgi DB'de degil, ajanin state.json'inda.
  const [agent, setAgent] = useState<GatewayAgentStatus | null>(null);
  const [gwIslem, setGwIslem] = useState<GatewayIslem | null>(null);
  // Bilesen sokulduktan sonra yoklama SURMEMELI.
  const canli = useRef(true);

  const localByCode = useMemo(() => {
    const harita = new Map<string, LocalGateway>();
    for (const item of agent?.gateways ?? []) harita.set(item.code, item);
    return harita;
  }, [agent]);

  /** Her gateway icin tek karar noktasi; hem nokta/rozet hem cihaz listesi
   *  bunu kullanir ki ikisi asla farkli sey soylemesin. */
  const gatewayStates = useMemo(() => {
    // Ajan raporu eskiyse (zamanlayici durmus) icindeki container durumu
    // ARTIK BIR OLCUM DEGIL. Karari backend veriyor; burada esik tekrar
    // hesaplanmaz (bkz. shared/gatewayLiveness.ts `localStale`).
    const ajanEskimis = agent?.reason === "state_stale";
    const harita = new Map<string, GatewayLivenessState>();
    for (const gw of gateways) {
      harita.set(
        gw.code,
        gatewayLiveness({
          isActive: gw.is_active,
          lastSeenAt: gw.last_seen_at,
          localState: localByCode.get(gw.code)?.state ?? null,
          // "Up 4 seconds": container yeni kalktiysa veri penceresi daha
          // dolmadi, "ulasilamiyor" denemez (bkz. gatewayLiveness).
          localStatus: localByCode.get(gw.code)?.status ?? null,
          localStale: ajanEskimis
        })
      );
    }
    return harita;
  }, [gateways, localByCode, agent?.reason]);

  useEffect(() => {
    canli.current = true;
    return () => {
      canli.current = false;
    };
  }, []);

  const loadAgent = async () => {
    // `fetchGatewayAgentStatus` ajan yoksa throw ETMEZ, available=false doner.
    const durum = await fetchGatewayAgentStatus(accessToken);
    if (canli.current) setAgent(durum);
    return durum;
  };

  useEffect(() => {
    // Yetkisi olmayan rollerde 403 alacagimiz bir istegi hic atmayalim;
    // onlarda durum eskisi gibi yalnizca telemetriden okunur.
    if (!canReadAgentStatus) return;
    void loadAgent();
    const timer = window.setInterval(() => void loadAgent(), AJAN_YOKLAMA_MS);
    return () => window.clearInterval(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [accessToken, canReadAgentStatus]);

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
  // Duzenlenen gateway'in KODU tutulur, kopyasi degil: liste tazelendiginde
  // (ornegin kurulum bitip last_seen_at guncellendiginde) modal eski veriyi
  // gostermesin.
  const [editingGatewayCode, setEditingGatewayCode] = useState<string | null>(null);
  const [devicePropsTab, setDevicePropsTab] = useState<DevicePropsTab>("system");

  const lastDeviceRef = useRef<DeviceRow | null>(null);

  const editingGateway = useMemo(
    () => gateways.find((g) => g.code === editingGatewayCode) ?? null,
    [gateways, editingGatewayCode]
  );

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

  /** Sihirbazdan gelen kayit istegi.
   *
   *  Hata YUTULMAZ — sihirbaz kendi adiminda gostersin diye yeniden firlatilir;
   *  yutulursa kullanici "olustu" saniyor ve bir sonraki adima gecmis oluyordu.
   */
  const handleCreateGateway = async (payload: { code: string; name: string; token: string }) => {
    setError("");
    const createdCode = payload.code.trim();
    const enteredName = payload.name.trim() || createdCode;
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
        token: payload.token,
        is_active: true,
        control_host: "127.0.0.1",
        control_port: 0,
        initiating_port_count: 0
      });
    } catch (err) {
      setError(err instanceof Error ? err.message : t("common.errorOccurred"));
      throw err;
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
    setEditingGatewayCode(gateway.code);
  };

  /** Ajan istegi bitene kadar durumu yoklar ve asamayi ekrana yansitir.
   *
   *  Bu islemler saniyeler surer; yine de sessiz kalmak en kotu secenek
   *  olurdu — operator bastigini goremeyip tekrar basar, ikinci istek
   *  reddedilir. (Ayni desen GatewayEditModal.izleIslem'de.)
   *
   *  `requestId` ile eslesiyoruz, yalnizca kod+eylem ile DEGIL: ayni gateway
   *  ikinci kez durdurulursa status.json'da bir ONCEKI durdurmanin "ok"
   *  sonucu duruyor olabilir ve onu bu istegin sonucu sanardik. (Bugun
   *  `pending` bayragi bu yarisi kapatiyor -- ajan istegi ancak sonucu
   *  yazdiktan SONRA siliyor -- ama o siralamaya bagli kalmak kirilgan;
   *  kimlik karsilastirmasi sirayla ilgilenmiyor.) */
  const izleGatewayIslem = async (
    code: string,
    action: GatewayIslem["action"],
    requestId: string
  ) => {
    const basla = Date.now();
    while (canli.current && Date.now() - basla < ISLEM_TAVANI_MS) {
      await new Promise((r) => window.setTimeout(r, ISLEM_YOKLAMA_MS));
      if (!canli.current) return;
      let durum: GatewayAgentStatus | null = null;
      try {
        durum = await loadAgent();
      } catch {
        // Tek bir basarisiz yoklama izlemeyi BITIRMEZ.
        continue;
      }
      const sonuc = durum?.last_apply ?? null;
      // Eski ajan `id` yazmiyor olabilir; o zaman kod+eylemle yetiniyoruz.
      const bizim =
        sonuc?.code === code &&
        sonuc?.action === action &&
        (!sonuc?.id || sonuc.id === requestId);
      if (durum?.pending || sonuc?.running) {
        if (bizim && sonuc?.stage) {
          setGwIslem({ code, action, asama: sonuc.stage });
        }
        continue;
      }
      if (bizim) {
        setGwIslem({
          code,
          action,
          asama: "bitti",
          basarili: sonuc?.ok === true,
          mesaj: sonuc?.message ?? undefined
        });
        // Basarili sonuc kisa sure gorunup kaybolur; HATA ekranda kalir ki
        // fark edilmeden gecmesin.
        if (sonuc?.ok === true) {
          window.setTimeout(() => {
            if (canli.current) setGwIslem((o) => (o && o.code === code ? null : o));
          }, 4000);
        }
        return;
      }
    }
    if (!canli.current) return;
    // Tavana carpildi: "bilmiyorum" de, "basarili" DEME.
    setGwIslem({ code, action, asama: "zaman_asimi" });
  };

  /** Gateway durdur / baslat / yeniden baslat.
   *
   *  DURDURMA ONAY ISTER ve sonucu acikca yazar: bu gateway'e bagli
   *  cihazlardan telemetri gelmeyecek. Baslatma onay istemez (veri akisini
   *  geri getirir); yeniden baslatma kisa bir kesinti oldugu icin ister. */
  const handleGatewayLifecycle = async (
    gateway: Gateway,
    action: GatewayIslem["action"]
  ) => {
    if (gwIslem && gwIslem.asama !== "bitti" && gwIslem.asama !== "zaman_asimi") return;
    if (action !== "start") {
      const onaylandi = await asyncConfirm(
        t(
          action === "stop"
            ? "engineering.gateways.lifecycle.stopConfirm"
            : "engineering.gateways.lifecycle.restartConfirm",
          { name: gateway.name }
        )
      );
      if (!onaylandi) return;
    }
    setError("");
    setGwIslem({ code: gateway.code, action, asama: "gonderiliyor" });
    try {
      const kabul =
        action === "stop"
          ? await stopGatewayLocally(accessToken, gateway.code)
          : action === "start"
            ? await startGatewayLocally(accessToken, gateway.code)
            : await restartGatewayLocally(accessToken, gateway.code);
      setGwIslem({ code: gateway.code, action, asama: "uygulaniyor" });
      void izleGatewayIslem(gateway.code, action, kabul.request_id);
    } catch (err) {
      setGwIslem(null);
      setError(err instanceof Error ? err.message : t("common.errorOccurred"));
    }
  };

  /** Host/listen_port GONDERILMEZ: gateway DNP3 master rolunde, bu iki alan
   *  create akisindaki placeholder'lardi ("auto"/0) ve duzenlenebilir birer
   *  ayar degil. Yalnizca gercekten degistirilebilen alanlar PATCH edilir. */
  const handleUpdateGateway = async (payload: {
    name: string;
    token: string;
    publish_dnp3_quality: boolean;
  }) => {
    const targetCode = editingGatewayCode;
    if (!targetCode) return;
    setError("");
    try {
      await onUpdateGateway(targetCode, {
        name: payload.name,
        token: payload.token,
        publish_dnp3_quality: payload.publish_dnp3_quality
      });
      if (selectedGatewayCode === targetCode) {
        await onSelectGateway(targetCode);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : t("common.errorOccurred"));
      throw err; // modal kendi hata satirinda gostersin ve acik kalsin
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
              const gState = gatewayStates.get(gateway.code) ?? "unknown";
              const gLabel = gatewayStateLabel(gState);
              const isDeletingThis = deletingGatewayCode === gateway.code;
              const anotherDeleting = Boolean(deletingGatewayCode) && !isDeletingThis;
              // Yasam dongusu dugmeleri yalnizca BU cihazda kurulu gateway'ler
              // icin anlamli; baska makinedeki container'a buradan erisilemez.
              const local = localByCode.get(gateway.code) ?? null;
              const islem = gwIslem && gwIslem.code === gateway.code ? gwIslem : null;
              const islemSuruyor = Boolean(
                gwIslem && gwIslem.asama !== "bitti" && gwIslem.asama !== "zaman_asimi"
              );
              const buIslemSuruyor = Boolean(
                islem && islem.asama !== "bitti" && islem.asama !== "zaman_asimi"
              );
              const dugmeKapali = isDeletingThis || anotherDeleting || islemSuruyor;
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
                        <span className={`gateway-status ${gState}`} title={gLabel}>
                          <span className="gateway-status-dot" aria-hidden="true" />
                        </span>
                        <strong className="gateway-name-only">{gateway.name}</strong>
                      </div>
                    </div>
                    {/* DURUM ROZETI — yalnizca "calisiyor" DISINDAKI durumlarda.
                        Normalde liste sade kalsin; bir sey normal disiysa
                        operator SEBEBINI okusun ("Durduruldu" / "Bilinmiyor"),
                        hepsi ayni kirmiziya boyanmasin. */}
                    {gState !== "online" ? (
                      <span className={`gateway-state-badge is-${gState}`}>{gLabel}</span>
                    ) : null}
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
                      {/* YASAM DONGUSU — yalnizca bu cihazda kurulu gateway'de.
                          Durmussa "Baslat", calisiyorsa "Durdur" + "Yeniden
                          baslat" gosterilir; ikisini ayni anda gostermek
                          "hangisi gecerli" sorusunu operatore birakirdi. */}
                      {local ? (
                        gState === "stopped" ? (
                          <button
                            type="button"
                            className={`secondary-btn action-btn gateway-start-btn ${buIslemSuruyor ? "is-busy" : ""}`}
                            onClick={() => void handleGatewayLifecycle(gateway, "start")}
                            title={t("engineering.gateways.lifecycle.start")}
                            aria-label={t("engineering.gateways.lifecycle.start")}
                            aria-busy={buIslemSuruyor || undefined}
                            disabled={dugmeKapali}
                          >
                            {buIslemSuruyor ? (
                              <span className="btn-spinner" aria-hidden="true" />
                            ) : (
                              <svg viewBox="0 0 24 24" width="18" height="18" aria-hidden="true">
                                <path fill="currentColor" d="M8 5v14l11-7z" />
                              </svg>
                            )}
                          </button>
                        ) : (
                          <>
                            <button
                              type="button"
                              className={`secondary-btn action-btn gateway-stop-btn ${buIslemSuruyor && islem?.action === "stop" ? "is-busy" : ""}`}
                              onClick={() => void handleGatewayLifecycle(gateway, "stop")}
                              title={t("engineering.gateways.lifecycle.stop")}
                              aria-label={t("engineering.gateways.lifecycle.stop")}
                              aria-busy={(buIslemSuruyor && islem?.action === "stop") || undefined}
                              disabled={dugmeKapali}
                            >
                              {buIslemSuruyor && islem?.action === "stop" ? (
                                <span className="btn-spinner" aria-hidden="true" />
                              ) : (
                                <svg viewBox="0 0 24 24" width="18" height="18" aria-hidden="true">
                                  <path fill="currentColor" d="M6 6h12v12H6z" />
                                </svg>
                              )}
                            </button>
                            <button
                              type="button"
                              className={`secondary-btn action-btn gateway-restart-btn ${buIslemSuruyor && islem?.action === "restart" ? "is-busy" : ""}`}
                              onClick={() => void handleGatewayLifecycle(gateway, "restart")}
                              title={t("engineering.gateways.lifecycle.restart")}
                              aria-label={t("engineering.gateways.lifecycle.restart")}
                              aria-busy={(buIslemSuruyor && islem?.action === "restart") || undefined}
                              disabled={dugmeKapali}
                            >
                              {buIslemSuruyor && islem?.action === "restart" ? (
                                <span className="btn-spinner" aria-hidden="true" />
                              ) : (
                                /* Inline SVG — "yenile" okuyla karismasin diye
                                   restart_alt kalibi secildi. */
                                <svg viewBox="0 0 24 24" width="18" height="18" aria-hidden="true">
                                  <path
                                    fill="currentColor"
                                    d="M12 5V2L8 6l4 4V7c3.31 0 6 2.69 6 6 0 2.97-2.17 5.43-5 5.91v2.02c3.95-.49 7-3.85 7-7.93 0-4.42-3.58-8-8-8zm-6 8c0-1.65.67-3.15 1.76-4.24L6.34 7.34A7.94 7.94 0 0 0 4 13c0 4.08 3.05 7.44 7 7.93v-2.02c-2.83-.48-5-2.94-5-5.91z"
                                  />
                                </svg>
                              )}
                            </button>
                          </>
                        )
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
                {/* ILERLEME — butona basildigi andan sonuca kadar gorunur.
                    Islem saniyeler surse de sessiz kalmak operatoru "bastim
                    mi" ikilemde birakir ve ikinci istege iter. */}
                {islem ? (
                  <div
                    className={`gateway-islem ${
                      islem.asama === "bitti"
                        ? islem.basarili
                          ? "is-ok"
                          : "is-fail"
                        : islem.asama === "zaman_asimi"
                          ? "is-unknown"
                          : "is-busy"
                    }`}
                    role="status"
                    aria-live="polite"
                  >
                    {buIslemSuruyor ? (
                      <span className="btn-spinner" aria-hidden="true" />
                    ) : null}
                    <span className="gateway-islem-metin">
                      {t(`engineering.gateways.lifecycle.progress.${islem.asama}`, {
                        defaultValue: t("engineering.gateways.lifecycle.progress.uygulaniyor")
                      })}
                      {islem.asama === "bitti" && islem.mesaj ? ` — ${islem.mesaj}` : ""}
                    </span>
                  </div>
                ) : null}
              </div>
            );
            })}
            {gateways.length === 0 ? (
              <div className="empty-state empty-state--compact">
                <span className="material-symbols-outlined">lan</span>
                <p>{t("engineering.devicesPanel.noGateways")}</p>
              </div>
            ) : null}
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
              const effStatus = effectiveCommStatus(device, gatewayStates);
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
                    {effStatus === "online"
                      ? t("engineering.devicesPanel.commOnline")
                      : effStatus === "unknown"
                        ? t("engineering.devicesPanel.commUnknown")
                        : t("engineering.devicesPanel.commOffline")}
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
            {devices.length === 0 ? (
              <div className="empty-state empty-state--compact">
                <span className="material-symbols-outlined">developer_board</span>
                <p>{t("engineering.devicesPanel.noDevicesUnderGateway")}</p>
              </div>
            ) : null}
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
                    const eff = effectiveCommStatus(selectedDevice, gatewayStates);
                    const gwState = selectedDevice.gatewayCode
                      ? gatewayStates.get(selectedDevice.gatewayCode)
                      : undefined;
                    const gwOffline =
                      eff === "offline" && selectedDevice.communicationStatus === "online";
                    return (
                      <span className={`device-comms-pill device-comms-pill--${deviceCommDotClass(eff)}`}>
                        {eff === "online"
                          ? t("engineering.devicesPanel.footer.ok")
                          : gwState === "stopped"
                            ? /* Sebep BILINIYOR: gateway durduruldu. "Veri eski"
                                 demek operatoru olmayan bir arizaya yonlendirirdi. */
                              t("engineering.devicesPanel.footer.gwStopped")
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
        <GatewayCreateModal
          accessToken={accessToken}
          existingCodes={gateways.map((g) => g.code)}
          onCreate={handleCreateGateway}
          onClose={() => setShowGatewayCreateModal(false)}
        />
      ) : null}

      {composeFor
        ? (() => {
            const composeGw = gateways.find((g) => g.code === composeFor);
            // Compose modali da AYNI karar noktasindan beslenir; kartta
            // "Durduruldu" yazarken burada "Bagli degil" yazmasin.
            const composeState = composeGw ? gatewayStates.get(composeGw.code) ?? "unknown" : null;
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
                  {composeState ? (
                    <div className={`compose-gw-status compose-gw-status--${composeState}`}>
                      <span className="compose-gw-status-dot" aria-hidden="true" />
                      <span>
                        {t("engineering.gateways.compose.gwStatus")}{" "}
                        <strong>{gatewayStateLabel(composeState)}</strong>
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

      {editingGateway ? (
        <GatewayEditModal
          accessToken={accessToken}
          gateway={editingGateway}
          onSave={handleUpdateGateway}
          onClose={() => setEditingGatewayCode(null)}
        />
      ) : null}

    </section>
  );
}
