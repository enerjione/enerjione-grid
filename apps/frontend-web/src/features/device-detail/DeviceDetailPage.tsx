/**
 * DeviceDetailPage — cihaz detay sayfasi (profesyonel dashboard duzeni).
 *
 * Duzen: sol sabit sidebar (DeviceSidebar) + ust sekmeler + icerik.
 * Sekmeler: Genel Bakis (KPI serit + Mevcut Durum + Ariza Yonu + Son Olaylar) /
 * Trendler (grafikler) / Olaylar (tam liste) / Komutlar / Yapilandirma.
 *
 * Veri: signalLiveValues (SignalLiveRow[]) + historian (grafik/sparkline) +
 * sistem olaylari/komut gecmisi (Son Olaylar). activeSource sidebar'dan kontrol.
 */

import { useCallback, useEffect, useMemo, useState, type ReactNode } from "react";
import { useTranslation } from "react-i18next";

import { useDeviceModelSettings } from "../../components/DeviceModelSettingsProvider";
import { voltageToPercent } from "../../shared/battery";
import { fizikselUydular, setMi, uyduKaynagi } from "../../shared/deviceKit";

import { downloadDeviceReport, fetchAlarmEvents } from "../../shared/api";
import { SOURCES, sourceLabel } from "../signals/signalCatalogConstants";
import { PoleMasterTab } from "./PoleMasterTab";
import { signalLabel } from "../../shared/signalLabel";
import { signalTrust } from "../../shared/signalQuality";
import type { AlarmEvent } from "../../shared/types";
import { isKitModel } from "../../shared/types";

import type {
  DeviceRow,
  Gateway,
  SignalCatalogRow,
  SignalDataType,
  SignalLiveRow,
  SignalSource,
} from "../../shared/types";
import { DeviceAlarmsCard } from "./DeviceAlarmsCard";
import { DeviceAllSignalsTab } from "./DeviceAllSignalsTab";
import { DeviceCommandsPanel } from "./DeviceCommandsPanel";
import { DeviceChartsPanel } from "./DeviceChartsPanel";
import { DeviceConfigPanel } from "./DeviceConfigPanel";
import { DeviceEventsTable } from "./DeviceEventsTable";
import { DeviceReportModal, type ReportSection } from "./DeviceReportModal";
import { DeviceRuntimePanel } from "./DeviceRuntimePanel";
import { DeviceSidebar } from "./DeviceSidebar";
import { modemDurumuCoz } from "./modemStatus";
import { operationModeOf } from "./operationMode";
import { Sparkline } from "./Sparkline";

type TopologyInfo =
  | { regionName: string; lineName: string; latitude?: number; longitude?: number }
  | undefined;

type TabKey = "overview" | "all" | "poleMaster" | "trends" | "events" | "commands" | "config";

type Props = {
  deviceId: number;
  devices: DeviceRow[];
  values: SignalLiveRow[];
  signals: SignalCatalogRow[];
  gateways: Gateway[];
  topologyInfo?: TopologyInfo;
  canCommand?: boolean;
  canConfig?: boolean;
  onDeviceCommand?: (deviceCode: string, command: string, label: string) => Promise<void>;
  token?: string;
  /** Baska bir cihazin detay sayfasini ac. Kitin sayfasindaki SET listesi
   *  bunu kullanir; verilmezse setler listelenir ama tiklanmaz. */
  onOpenDevice?: (deviceId: number) => void;
};

// ---- Kategori tanimi (source-agnostic suffix -> kategori + TR etiket) --------
type CatKey = "measure" | "fault" | "status" | "direction";
type SigDef = { suffix: string; label: string; cat: CatKey };

const SIGNALS: SigDef[] = [
  { suffix: "actual_current", label: "Akım", cat: "measure" },
  { suffix: "actual_voltage", label: "Gerilim", cat: "measure" },
  { suffix: "average_current", label: "Ort. Akım", cat: "measure" },
  { suffix: "maximum_current", label: "Max. Akım", cat: "measure" },
  { suffix: "conductor_temperature", label: "İletken Sıc.", cat: "measure" },
  { suffix: "device_temperature", label: "Cihaz Sıc.", cat: "measure" },
  { suffix: "fault_current", label: "Arıza Akımı", cat: "fault" },
  { suffix: "fault_duration", label: "Arıza Süresi", cat: "fault" },
  { suffix: "last_good_known_current", label: "Son İyi Akım", cat: "fault" },
  { suffix: "minimum_current", label: "Min. Akım", cat: "fault" },
  { suffix: "overcurrent_tripped", label: "Aşırı akım", cat: "status" },
  { suffix: "delta_i_delta_t_tripped", label: "ΔI/Δt", cat: "status" },
  { suffix: "voltage_loss", label: "Gerilim kaybı", cat: "status" },
  { suffix: "current_loss", label: "Akım kaybı", cat: "status" },
  { suffix: "battery_status", label: "Pil durumu", cat: "status" },
  { suffix: "permanent_fault", label: "Kalıcı arıza", cat: "status" },
  { suffix: "momentary_fault", label: "Geçici arıza", cat: "status" },
];

// Durum satiri gorsel meta (ikon).
const STATUS_ICONS: Record<string, string> = {
  overcurrent_tripped: "bolt",
  delta_i_delta_t_tripped: "show_chart",
  voltage_loss: "power_off",
  current_loss: "flash_off",
  battery_status: "battery_alert",
  permanent_fault: "warning",
  momentary_fault: "error_outline",
};

// ---- Mevcut Durum: sinyalleri kategori gruplarina ayir (suffix pattern) ----
// Kategori sirasi = gorunum sirasi. Her sinyal bir gruba duser; bilgi/komut
// sinyalleri (IP/serial/firmware, binary_output) HARIC (baska sekmelerde).
type GroupKey = "protection" | "measure" | "status" | "counter";

const GROUP_ORDER: { key: GroupKey; icon: string }[] = [
  { key: "protection", icon: "shield" },
  { key: "measure", icon: "monitoring" },
  { key: "status", icon: "toggle_on" },
  { key: "counter", icon: "pin" },
];

// Bilgi/altyapi sinyalleri Mevcut Durum'da GOSTERILMEZ (sidebar/Tumu'de).
// "info_" ile baslayan tum sinyaller bilgi kabul edilir (NWS/RF/modem vb dahil).
const INFO_SUFFIX_RE =
  /^info_|(serial_number|ipv4_address|ip_address|firmware|fw_version|modem|imei|sim_serial|gps|latitude|longitude|hardware_revision|part_no|rtu_status|network|operation_mode|device_position|test_point_level|comm_library|dial_in)/;

function groupOfSuffix(suffix: string, dataType: string | undefined): GroupKey {
  const s = suffix.toLowerCase();
  // Koruma / ariza yonu / trip / alarm esikleri + ariza olcum degerleri.
  // "_alarm" iceren tum sinyaller (conductor_temperature_alarm vb) koruma.
  if (
    /(overcurrent|delta_i_delta_t|fault_direction|load_flow|_tripped|voltage_loss|current_loss|tamper|pick_up|_alarm|permanent_fault$|momentary_fault$|fault_current|fault_duration|last_good|minimum_current|minimum_voltage|maximum_current|maximum_voltage|trip_level)/.test(
      s
    )
  ) {
    return "protection";
  }
  // Sayaclar
  if (dataType === "counter" || /_counter$/.test(s)) return "counter";
  // Olcumler (analog)
  if (dataType === "analog" || /(current|voltage|temperature|phase_angle|pitch_angle|nominal)/.test(s)) {
    return "measure";
  }
  // Kalan binary/durum
  return "status";
}

const CNT_PERMANENT = "permanent_fault_counter";
const CNT_MOMENTARY = "momentary_fault_counter";

const GATEWAY_LIVE_SEC = 60;
const NUMBER_FORMATTER = new Intl.NumberFormat("tr-TR", {
  minimumFractionDigits: 0,
  maximumFractionDigits: 6,
  useGrouping: false,
});

function suffixOf(signalKey: string): string {
  const i = signalKey.indexOf(".");
  return i >= 0 ? signalKey.slice(i + 1) : signalKey;
}

function isGatewayOnline(gw: Gateway | undefined): boolean {
  if (!gw || !gw.is_active || !gw.last_seen_at) return false;
  return (Date.now() - new Date(gw.last_seen_at).getTime()) / 1000 < GATEWAY_LIVE_SEC;
}

function fmt(
  value: number | null,
  dataType: SignalDataType | string | undefined | null,
  unit?: string | null
): string {
  if (value === null || value === undefined) return "—";
  if (dataType === "binary") return value ? "AKTİF" : "PASİF";
  if (!Number.isFinite(value)) return String(value);
  const text = dataType === "counter" ? Math.round(value).toString() : NUMBER_FORMATTER.format(value);
  return unit ? `${text} ${unit}` : text;
}

type Row = SignalLiveRow & { effQuality: string | null; effType?: string | null };

export function DeviceDetailPage({
  deviceId,
  devices,
  values,
  signals,
  gateways,
  topologyInfo,
  canCommand = false,
  canConfig = false,
  onDeviceCommand,
  token,
  onOpenDevice,
}: Props) {
  const { t } = useTranslation();
  const [activeTab, setActiveTab] = useState<TabKey>("overview");
  const [activeSource, setActiveSource] = useState<SignalSource>("master");
  const [busyReset, setBusyReset] = useState(false);
  const [deviceAlarms, setDeviceAlarms] = useState<AlarmEvent[]>([]);
  // Alarm cekimi basarisizsa dolu. Bos liste ile "bilmiyoruz" ayni sey degil:
  // sidebar bos listeyi yesil "Alarm Yok" karti olarak gosteriyordu.
  const [deviceAlarmsError, setDeviceAlarmsError] = useState("");
  /** PDF sunucuda uretilir (katalog + telemetri + uydu karolari); bir kac
   *  saniye surebilir, o yuzden dugme beklemede kilitlenir. */
  const [raporUretiliyor, setRaporUretiliyor] = useState(false);
  const [raporHatasi, setRaporHatasi] = useState("");
  /** Bolum secim penceresi acik mi. Dugme dogrudan indirmiyor: rapor kime
   *  gittigine gore degisiyor ve secim her seferinde sorulmali (secilen
   *  kume tarayicida hatirlaniyor, bkz. DeviceReportModal). */
  const [raporSecimi, setRaporSecimi] = useState(false);

  const device = useMemo(() => devices.find((d) => d.id === deviceId), [devices, deviceId]);

  // FIZIKSEL KIT KAYDI (varsa). Pole Master Kit'in kit seviyesindeki
  // olcumleri — modem, GPS, sebeke, solar/AC besleme, cihaz sicakligi —
  // burada durur ve "Pole Master" sekmesinde gosterilir. Telemetri
  // COGALTILMAZ (bkz. PoleMasterTab docstring'i), okuma tarafinda devralinir.
  const parentDevice = useMemo(
    () =>
      device?.parentDeviceId != null
        ? devices.find((d) => d.id === device.parentDeviceId)
        : undefined,
    [devices, device]
  );

  /** Acik olan kayit KITIN KENDISI mi (setlerinden biri degil)? */
  const kitMi = device != null && !setMi(device) && isKitModel(device.model);

  /** Bu kite bagli setler — kitin sayfasinda solda listelenir.
   *
   *  Kod sirasi (PMK-001-S1, -S2, -S3) sahadaki set numarasiyla ayni; id
   *  sirasi degil, cunku set kayitlari sonradan silinip yeniden
   *  yaratilabiliyor ve o zaman numaralar karisik gorunurdu. */
  const setler = useMemo(
    () =>
      device && kitMi
        ? devices
            .filter((d) => d.parentDeviceId === device.id)
            .sort((a, b) => a.code.localeCompare(b.code, "tr"))
        : [],
    [devices, device, kitMi]
  );

  /** KIT SEVIYESI KAYIT: sette ust cihaz, kitin kendi sayfasinda kendisi.
   *  "Pole Master" verileri her iki durumda da AYNI yerden okunur. */
  const kitKaydi = parentDevice ?? (kitMi ? device : undefined);

  // Bir Pole Master Kit setinde olcum yapan uc unitenin ucu de uydudur;
  // kitin `master`i ortak RTU'dur. Bu yuzden set acildiginda varsayilan
  // kanal `sat01` olmali — `master` secili kalsaydi kullanici bos bir
  // kanal gorurdu (sette `master.*` telemetrisi YOK).
  useEffect(() => {
    if (device?.parentDeviceId != null) {
      setActiveSource((prev) => (prev === "master" ? "sat01" : prev));
    }
  }, [device?.parentDeviceId]);

  // Bu cihazin alarmlari — hem sidebar durum karti hem Alarmlar karti kullanir
  // (tek fetch). Aktif alarm = reset EDILMEMIS (giderilmemis) alarm.
  const loadAlarms = useCallback(async () => {
    if (!token || !device) return;
    try {
      // `.catch(() => [])` KALDIRILDI: hata bos listeye donusuyordu ve
      // sidebar yesil "Alarm Yok" karti gosteriyordu. Cihazda acik alarm
      // olsa bile ekran temiz gorunuyordu — istemci veriyi hic alamamisken.
      const all = await fetchAlarmEvents(token);
      setDeviceAlarms(all.filter((a) => a.device_id === device.id));
      setDeviceAlarmsError("");
    } catch (err) {
      // Liste BOSALTILMAZ (varsa eski veri kalsin), hata gorunur olur.
      setDeviceAlarmsError(
        err instanceof Error ? err.message : t("common.errorOccurred")
      );
    }
  }, [token, device, t]);

  useEffect(() => {
    void loadAlarms();
  }, [loadAlarms]);

  const hasActiveAlarm = useMemo(
    () => deviceAlarms.some((a) => !a.reset),
    [deviceAlarms]
  );

  /** CIHAZ DURUM RAPORU (PDF) — sunucuda uretilir, burada indirilir.
   *
   *  Tarayicinin yazdirma diyalogu YOK: sayfa bes sekmeye yayilmis bir pano
   *  ve `window.print()` yalnizca ACIK sekmeyi, secili kanalla, pencere
   *  genisligine bagli bicimde kagida dokerdi. Rapor gercek bir belge
   *  (`services/device_report_service.py`); iskeleti cihaz turune gore
   *  sunucuda kuruluyor ve dosya adi da oradan geliyor. */
  const raporIndir = useCallback(async (sections: ReportSection[]) => {
    if (!token || !device) return;
    setRaporUretiliyor(true);
    setRaporHatasi("");
    try {
      const { blob, filename } = await downloadDeviceReport(token, device.code, sections);
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = filename;
      // Chromium bazi proxy'lerin arkasinda DOM'a eklenmemis anchor'un
      // click'ini yutuyor (bkz. shared/api.ts indirme yardimcilari).
      document.body.appendChild(anchor);
      anchor.click();
      anchor.remove();
      // Hemen revoke etmek bazi tarayicilarda indirmeyi "network error"a
      // dusuruyor: click asenkron baslatiyor.
      window.setTimeout(() => URL.revokeObjectURL(url), 1000);
      // Pencere yalnizca BASARIDA kapanir: hata olursa kullanici secimini
      // kaybetmeden yeniden deneyebilsin.
      setRaporSecimi(false);
    } catch (err) {
      setRaporHatasi(
        err instanceof Error ? err.message : t("deviceDetail.exportPdfFailed")
      );
    } finally {
      setRaporUretiliyor(false);
    }
  }, [token, device, t]);

  const dataTypeByKey = useMemo(() => {
    const m = new Map<string, SignalDataType>();
    for (const s of signals) m.set(s.key, s.data_type);
    return m;
  }, [signals]);

  const gwOnline = useMemo(() => {
    if (!device?.gatewayCode) return true;
    // GATEWAY LISTESI HENUZ YUKLENMEDIYSE HUKUM VERME.
    //
    // Bos listede `find` undefined doner ve asagidaki kontrol "offline"
    // der; o da cihazin TUM okumalarini "Güvenilmez" yapar. Oysa bildigimiz
    // tek sey listenin daha gelmedigi. Bilmedigini "sorun var" diye
    // gostermek, "sorun yok" diye gostermenin aynadaki hali — ikisi de
    // operatoru yaniltir. (Ayni gerekce asagidaki katalog suzgecinde de
    // var: "Katalog HENUZ YUKLENMEMISSE suzgec uygulanmaz".)
    if (gateways.length === 0) return true;
    return isGatewayOnline(gateways.find((g) => g.code === device.gatewayCode));
  }, [gateways, device]);

  /** Bu cihazin MODELINE ait katalog anahtarlari.
   *
   *  Ekranda YALNIZCA sinyal listesinde gercekten olan noktalar gorunmeli.
   *  Pole Master Kit setinin sinyal listesi SN 2.0'inkinden farklidir (48
   *  uydu noktasi; SN2'nin master kanali ve o kanala ozgu noktalar kitin
   *  RTU'suna aittir, sete degil) — yabanci modelin satirlarini set
   *  ekraninda gostermek yaniltir.
   *
   *  Canli deger dizisi istemcide BIRIKTIRILEREK tutuluyor: bir cihazin
   *  modeli degistiginde (kit -> set) eski modelin satirlari tam bir
   *  tazelemeye kadar dizide kaliyor. Bu suzgec onlari da eler.
   *
   *  Katalog HENUZ YUKLENMEMISSE (bos dizi) suzgec uygulanmaz — aksi halde
   *  sayfa acilisinda ekran bir an tamamen bosalirdi. */
  const modelSignalKeys = useMemo(() => {
    const s = new Set<string>();
    if (!device) return s;
    for (const sig of signals) if (sig.model === device.model) s.add(sig.key);
    return s;
  }, [signals, device]);

  const katalogda = useCallback(
    (signalKey: string) => modelSignalKeys.size === 0 || modelSignalKeys.has(signalKey),
    [modelSignalKeys]
  );

  /** Bu cihazin canli satirlari (tum kaynaklar), model suzgecinden gecmis. */
  const deviceValues = useMemo(
    () =>
      device
        ? values.filter((r) => r.device_id === device.id && katalogda(r.signal_key))
        : [],
    [values, device, katalogda]
  );

  // Secili kaynaga gore satirlar.
  const rows = useMemo<Row[]>(() => {
    if (!device) return [];
    return values
      .filter(
        (r) =>
          r.device_id === device.id && r.source === activeSource && katalogda(r.signal_key)
      )
      .map((r) => ({
        ...r,
        effQuality: gwOnline ? r.quality : "bad",
        effType: (r.data_type as string | undefined) ?? dataTypeByKey.get(r.signal_key),
      }));
  }, [values, device, activeSource, gwOnline, dataTypeByKey, katalogda]);

  const rowBySuffix = useMemo(() => {
    const m = new Map<string, Row>();
    for (const r of rows) m.set(suffixOf(r.signal_key), r);
    return m;
  }, [rows]);

  // Tum kaynaklardan key->deger (sidebar RSSI + KPI icin). Model suzgeci
  // burada da gecerli (bkz. `modelSignalKeys`).
  const valueByKey = useMemo(() => {
    const m = new Map<string, SignalLiveRow>();
    if (device) {
      for (const r of values) {
        if (r.device_id === device.id && katalogda(r.signal_key)) m.set(r.signal_key, r);
      }
    }
    return m;
  }, [values, device, katalogda]);

  /** RTU (ana govde) degerleri.
   *
   *  Bir Pole Master Kit SETINDE `master.*` telemetrisi HIC YOKTUR: modem,
   *  IP, firmware, GPS ve besleme kitin ORTAK RTU'sundadir. Sol panel bu
   *  degerleri setin kendi kaydindan okuduğu icin sette IP, part no,
   *  firmware ve sebeke sinyali hep bos kaliyordu. Artik set sayfasinda bu
   *  alanlar KIT kaydindan okunur; sade cihazda (SN 2.0) kaynak degismez. */
  const rtuValueByKey = useMemo(() => {
    const kaynak = parentDevice ?? device;
    const m = new Map<string, SignalLiveRow>();
    if (kaynak) for (const r of values) if (r.device_id === kaynak.id) m.set(r.signal_key, r);
    return m;
  }, [values, parentDevice, device]);

  // Katalog birimleri (kullanici Sinyaller sayfasindan degistirebilir).
  const unitByKey = useMemo(() => {
    const m = new Map<string, string>();
    for (const s of signals) if (s.unit) m.set(s.key, s.unit);
    return m;
  }, [signals]);

  const numVal = (key: string): number | undefined => {
    const v = valueByKey.get(key)?.value;
    return v == null ? undefined : v;
  };
  const strVal = (key: string): string | undefined => {
    const s = (valueByKey.get(key)?.value_string ?? "").trim();
    return s.length > 0 ? s : undefined;
  };
  // Birim: canli deger unit'i, yoksa katalog unit'i (sabit "mA" DEGIL).
  const unitOf = (key: string): string | undefined =>
    valueByKey.get(key)?.unit || unitByKey.get(key) || undefined;

  // RTU (kit) tarafindaki bilgi alanlari — sette kitin kaydindan okunur.
  const rtuNum = (key: string): number | undefined => {
    const v = rtuValueByKey.get(key)?.value;
    return v == null ? undefined : v;
  };
  const rtuStr = (key: string): string | undefined => {
    const s = (rtuValueByKey.get(key)?.value_string ?? "").trim();
    return s.length > 0 ? s : undefined;
  };

  // Sidebar: master IP + kanal (master/sat01/sat02) seri no'lari.
  // IP: G110 string (info_ipv4_address). Serial: analog (group 30) -> sayi,
  //   value_string DEGIL value; string variant (info_serial_number) fallback.
  const sidebarIp = rtuStr("master.info_ipv4_address") ?? rtuStr("master.info_modem_ip_address");
  const sidebarPartNo = rtuStr("master.info_part_no");
  // SEBEKE SINYALI + OPERATOR — modemin ham yanitindan.
  //
  // Onceki surum sayisal `master.modem_rssi` ariyordu; cihaz oyle bir nokta
  // YAYINLAMIYOR ve satir hep bos kaliyordu. Gercek deger
  // `info_network_rf_status_information` metninin icinde geliyor
  // (NWS: "286 01",1651,-91,...,"Turkcell",...). Cozum KONUMA degil
  // BICIME dayali (bkz. modemStatus.ts): modem degisip alan sirasi
  // kayarsa sessizce yanlis sayi gostermektense bos gostermek dogru.
  const modem = modemDurumuCoz(rtuStr("master.info_network_rf_status_information"));
  const sidebarRssi = rtuNum("master.modem_rssi") ?? modem.dbm;
  const sidebarOperator = rtuStr("master.info_network_operator") ?? modem.operator;
  // Firmware: cihaz ham deger olarak 2338 gonderir, gercek surum "2.338".
  // Ham deger >= 1000 ise X.YYY formatina cevir (2338 -> 2.338); string
  // variant (info_fw_version) varsa onu tercih et.
  const fwStr = rtuStr("master.info_fw_version");
  const fwNum = rtuNum("master.firmware_version");
  const fmtFirmware = (n: number): string => {
    if (n >= 1000) return (n / 1000).toFixed(3); // 2338 -> "2.338"
    return String(n);
  };
  const sidebarFirmware =
    fwStr ?? (fwNum != null && Number.isFinite(fwNum) ? fmtFirmware(fwNum) : undefined);
  /** CALISMA MODU — `master.operation_mode` (binary): 1 = Akilli, 0 = Boost.
   *
   *  GUVENILIRLIK KAPISI, sayfanin geri kalaniyla AYNI kural: gateway kopuk
   *  ya da kalite bloklayici ise mod HIC gosterilmez. Iki durumun ikisi de
   *  gecerli bir mod oldugu icin burada "veri yok" ile "Boost" ayrimi
   *  ozellikle onemli: haberlesme koptugunda gateway `comm_lost` kalitesiyle
   *  0.0 basiyor ve naif bir okuma bunu "Boost Mode" diye gosterirdi — yani
   *  cihaz akilli modda calisirken ekran tam TERSINI iddia ederdi. */
  const opModeRow = rtuValueByKey.get("master.operation_mode");
  const sidebarOperationMode = operationModeOf(
    opModeRow?.value,
    opModeRow?.quality,
    gwOnline
  );
  /** Bu cihazda OLCUM YAPAN uniteler.
   *
   *  SN 2.0'da ucuncu unite `master`dir; Pole Master Kit setinde ucu de
   *  uydudur (sat01/sat02/sat03) ve o kayitta `master.*` telemetrisi HIC
   *  yoktur. Sabit uclu kullanmak, sette bos bir "Master" kanali gosterip
   *  gercek ucuncu uniteyi (Satellite 03) tamamen gizliyordu — pil rozeti,
   *  seri no ve "Tumu" karti dahil.
   */
  const measuringSources = useMemo<SignalSource[]>(
    () =>
      (device?.parentDeviceId ?? null) !== null
        ? ["sat01", "sat02", "sat03"]
        : ["master", "sat01", "sat02"],
    [device]
  );

  const serialOf = (src: SignalSource): string | undefined => {
    const n = numVal(`${src}.serial_number`);
    if (n != null && Number.isFinite(n) && n > 0) return String(Math.round(n));
    return strVal(`${src}.info_serial_number`);
  };
  const channelSerials = useMemo<Partial<Record<SignalSource, string>>>(() => {
    const out: Partial<Record<SignalSource, string>> = {};
    for (const src of measuringSources) {
      const seri = serialOf(src);
      if (seri) out[src] = seri;
    }
    return out;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [valueByKey, measuringSources]);

  // Kanal pil yuzdeleri. Master: device.batteryPercent (hesaplanmis).
  // Satellite: kendi batarya geriliminden.
  //
  // ESIK ARTIK CIHAZ TURUNDEN GELIYOR. Burada sabit 3.2V=%0 / 4.2V=%100
  // kullaniliyordu; backend 3.40/3.71 ile hesapliyordu ve ayni cihaz detay
  // sayfasinda listedekinden farkli yuzde gosteriyordu.
  const { thresholdsFor } = useDeviceModelSettings();
  const voltToPct = useCallback(
    (v: number | undefined, unite?: string): number | undefined =>
      voltageToPercent(v, thresholdsFor(device?.model, unite)),
    [thresholdsFor, device?.model]
  );
  /** Setin sat01/sat02/sat03 kanallarinin FIZIKSEL uydu karsiligi.
   *
   *  Kurulumcu bu atamayi cihaz ayarlarindan degistirebilir; varsayilan
   *  yerlesim (set 2 -> 4/5/6) yalnizca baslangic noktasidir. */
  const setUydulari = useMemo(
    () => (device && setMi(device) ? fizikselUydular(device) : []),
    [device]
  );

  /** SETLERIN PIL YUZDESI — kitin sayfasindaki set listesi icin.
   *
   *  Setin yuzdesi UC uydusunun EN DUSUGUNDEN cikar: ekip direge en zayif
   *  hucre bittiginde cikar, ortalama bunu gizler (ayni kural
   *  `shared/battery.ts` icinde).
   *
   *  Gerilim KIT kaydinin telemetrisinde durur — kit tek outstation'dir ve
   *  veri cogaltilmaz; setin fiziksel uydu numaralari `fizikselUydular` ile
   *  cozulur (set 2 -> sat04..06, atama degistirildiyse ne secildiyse o). */
  const setPilleri = useMemo<Record<number, number | undefined>>(() => {
    const out: Record<number, number | undefined> = {};
    if (!device || setler.length === 0) return out;
    const kitSatirlari = new Map<string, number | null>();
    for (const r of values) {
      if (r.device_id === device.id) kitSatirlari.set(r.signal_key, r.value ?? null);
    }
    for (const s of setler) {
      const yuzdeler: number[] = [];
      for (const no of fizikselUydular(s)) {
        const kaynak = uyduKaynagi(no);
        const volt = kitSatirlari.get(`${kaynak}.battery_voltage_satellite`);
        const pct = voltageToPercent(volt ?? undefined, thresholdsFor(s.model, kaynak));
        if (pct != null) yuzdeler.push(pct);
      }
      out[s.id] = yuzdeler.length > 0 ? Math.min(...yuzdeler) : undefined;
    }
    return out;
  }, [device, setler, values, thresholdsFor]);

  const channelBattery = useMemo<Partial<Record<SignalSource, number>>>(() => {
    const out: Partial<Record<SignalSource, number>> = {};
    measuringSources.forEach((src, i) => {
      // `master` kanalinin pili cihaz kaydindan gelir (hesaplanmis yuzde);
      // uydularinki kendi batarya gerilimlerinden turetilir. Kit setinde
      // `master` bir olcum unitesi DEGIL, o yuzden listede de yok.
      if (src === "master") {
        // `batteryPercent` artik null olabilir (cihaz henuz bildirmedi).
        // Number.isFinite null'i eler ama TS bunu daraltmadigi icin degeri
        // yerel bir degiskene alip acikca kontrol ediyoruz.
        const pct = device?.batteryPercent;
        if (typeof pct === "number" && Number.isFinite(pct)) out.master = pct;
        return;
      }
      // SETTE PIL FIZIKSEL UYDUDAN OKUNUR.
      //
      // Setin kaydindaki `sat01..sat03` telemetri bolmesinin URETTIGI sanal
      // adlardir; batarya gerilimi orada bos/sifir kalabiliyor. Uydunun
      // kendisi ise KIT kaydinda gercek numarasiyla durur (set 2 -> sat04..06,
      // atama degistirildiyse ne secildiyse o). Once oradan okunur; kit
      // erisilemezse setin kendi degerine dusulur.
      let pct: number | undefined;
      if (setUydulari.length > i) {
        const kaynak = uyduKaynagi(setUydulari[i]);
        pct = voltToPct(rtuNum(`${kaynak}.battery_voltage_satellite`), kaynak);
      }
      if (pct == null) pct = voltToPct(numVal(`${src}.battery_voltage_satellite`), src);
      if (pct != null) out[src] = pct;
    });
    return out;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [valueByKey, rtuValueByKey, device, measuringSources, setUydulari]);

  /** Kanal -> fiziksel uydu numarasi. Sade cihazda (SN 2.0) bos. */
  const channelSatelliteNo = useMemo<Partial<Record<SignalSource, number>>>(() => {
    const out: Partial<Record<SignalSource, number>> = {};
    measuringSources.forEach((src, i) => {
      if (setUydulari.length > i) out[src] = setUydulari[i];
    });
    return out;
  }, [measuringSources, setUydulari]);

  const sourceCounts = useMemo<Record<SignalSource, number>>(() => {
    const c = Object.fromEntries(
      SOURCES.map((s) => [s, 0])
    ) as Record<SignalSource, number>;
    if (device) {
      for (const r of values) {
        if (r.device_id === device.id && r.source in c && katalogda(r.signal_key)) {
          c[r.source as SignalSource] += 1;
        }
      }
    }
    return c;
  }, [values, device, katalogda]);

  if (!device) {
    return (
      <div className="device-detail-empty">
        <span className="material-symbols-outlined">search_off</span>
        <p className="helper-text">{t("deviceDetail.notFound")}</p>
      </div>
    );
  }

  const runResetAlarm = async () => {
    if (!onDeviceCommand) return;
    const label = signals.find((s) => s.key === "master.reset_all_fcis")?.label ?? "reset_all_fcis";
    setBusyReset(true);
    try {
      await onDeviceCommand(device.code, "reset_all_fcis", label);
    } finally {
      setBusyReset(false);
    }
  };
  const hasResetCmd = signals.some(
    (s) => s.key === "master.reset_all_fcis" && s.data_type === "binary_output" && s.is_active
  );

  const permCount = rowBySuffix.get(CNT_PERMANENT)?.value ?? numVal("master.permanent_fault_counter");
  const momCount = rowBySuffix.get(CNT_MOMENTARY)?.value ?? numVal("master.momentary_fault_counter");
  const curNow = numVal(`${activeSource}.actual_current`);
  const voltNow = numVal(`${activeSource}.actual_voltage`);
  const tempNow = numVal(`${activeSource}.device_temperature`) ?? numVal(`${activeSource}.conductor_temperature`);

  /** KPI seridi icin olcum guvenilirligi — `signalTrust` ile, `rows`
   *  yolundakiyle AYNI kural.
   *
   *  `numVal` yalnizca `value` okuyor; ne `quality` ne `gwOnline`. Bu yuzden
   *  KPI seridi gateway kopukken bile degeri TAZE OLCUM gibi gosteriyordu —
   *  ustelik ayni ekranin alt bolumu (StatusTable) ayni sinyal icin
   *  "Güvenilmez" diyordu. Tek ekranda iki farkli cevap, en kotu hali. */
  const untrustedOf = (key: string): boolean => {
    const row = valueByKey.get(key);
    if (row === undefined) return false; // deger yok -> "veri yok" zaten yazilir
    return signalTrust(row.value ?? null, row.quality, gwOnline) === "untrusted";
  };
  const curUntrusted = untrustedOf(`${activeSource}.actual_current`);
  const voltUntrusted = untrustedOf(`${activeSource}.actual_voltage`);
  const tempUntrusted =
    numVal(`${activeSource}.device_temperature`) != null
      ? untrustedOf(`${activeSource}.device_temperature`)
      : untrustedOf(`${activeSource}.conductor_temperature`);
  // Sayaclar cihazin kendi kayitlari: gateway kopukken bayat kabul edilir.
  const countersUntrusted = !gwOnline;

  const tabs: { key: TabKey; icon: string; show: boolean }[] = [
    { key: "overview", icon: "dashboard", show: true },
    { key: "all", icon: "table_rows", show: true },
    // KIT SEVIYESI: yalnizca bir Pole Master Kit setinde gorunur. Kitin
    // modem/GPS/besleme olcumleri uc setin ORTAK varligidir; setin kendi
    // sinyalleriyle ayni listede gostermek hangi degerin nereye ait
    // oldugunu karistirirdi.
    // Ikon `dns` (RTU/sunucu govdesi) — "Tumu" sekmesindeki master kartiyla
    // AYNI ikon. Onceden `solar_power` idi: hem kiti gunes paneli gibi
    // gosteriyordu hem de ikon subset fontunda olmadigi icin sekme basliginda
    // duz metin ("solar_") olarak cikiyordu.
    // KITIN KENDI SAYFASINDA DA GORUNUR. Onceden kosul `parentDevice != null`
    // idi, yani sekme YALNIZCA setlerde aciliyordu; kitin kendi sayfasini acan
    // kullanici — ki o sayfa tam olarak Pole Master'in sayfasi — modem, GPS,
    // besleme, sicaklik, kurcalama olculerinin HICBIRINI goremiyordu. Ustelik
    // o sayfada Genel Bakis da bosa dusuyordu: kitin akim/gerilim/ariza
    // sinyali yoktur, kartlar sonsuza kadar "Veri yok" yaziyordu.
    { key: "poleMaster", icon: "dns", show: kitKaydi != null },
    { key: "trends", icon: "show_chart", show: true },
    { key: "events", icon: "history", show: true },
    { key: "commands", icon: "terminal", show: canCommand },
    { key: "config", icon: "tune", show: canConfig },
  ];

  const showReset = canCommand && hasResetCmd && onDeviceCommand;

  return (
    <div className="device-detail-shell">
      <DeviceSidebar
        device={device}
        parentDevice={parentDevice}
        topologyInfo={topologyInfo}
        rssi={sidebarRssi}
        networkOperator={sidebarOperator}
        ip={sidebarIp}
        partNo={sidebarPartNo}
        firmware={sidebarFirmware}
        operationMode={sidebarOperationMode}
        hasAlarm={hasActiveAlarm}
        alarmError={deviceAlarmsError}
        channelSerials={channelSerials}
        channelBattery={channelBattery}
        channelSatelliteNo={channelSatelliteNo}
        activeSource={activeSource}
        onSourceChange={setActiveSource}
        sourceCounts={sourceCounts}
        sets={setler}
        setBattery={setPilleri}
        onOpenSet={onOpenDevice}
      />

      <div className="device-detail-main">
        <div className="device-detail-topbar">
          <nav className="device-detail-tabs" role="tablist">
            {tabs.filter((tb) => tb.show).map((tb) => (
              <button
                key={tb.key}
                type="button"
                role="tab"
                aria-selected={activeTab === tb.key}
                className={`device-detail-tab${activeTab === tb.key ? " active" : ""}`}
                onClick={() => setActiveTab(tb.key)}
              >
                <span className="material-symbols-outlined">{tb.icon}</span>
                {t(`deviceDetail.tabs.${tb.key}`)}
              </button>
            ))}
          </nav>
          <div className="device-detail-topbar-actions">
            {/* VERI AKISI ROZETI YOK (kullanici istegi): ust seritte alarm
                reset dugmesinin yaninda ikinci bir durum etiketi gurultu
                yapiyordu. Bayat veri korumasi KALDIRILMADI — bekci hala
                caliyor ve deger satirlari kendi tazeligini gosteriyor
                (bkz. useLiveValuesSocket / wsDataStatus). Rozet Canli
                Degerler ekraninda duruyor. */}
            {device.alarmActive ? (
              <span className="device-alarm-badge">
                <span className="device-alarm-pulse" aria-hidden="true" />
                {t("deviceDetail.alarmActive")}
              </span>
            ) : null}
            {/* PDF: BACKEND uretir (`/devices/{code}/report.pdf`). Sayfanin
                tamaminin — kunye, kanallar, sinyal durumu, kit seviyesi
                degerler, alarmlar, olaylar — tek belgeye dokulmus hali.
                Sekmeden BAGIMSIZ: hangi sekmede olursaniz olun ayni belge
                cikar, cunku rapor ekrandan degil KAYITTAN uretiliyor. */}
            {raporHatasi ? (
              // Hata dugmenin YANINDA durur: `title` ipucu dokunmatik
              // ekranda hic gorunmuyor ve indirme sessizce basarisiz
              // olmus gibi okunuyordu.
              <span className="device-report-error" role="alert">
                <span className="material-symbols-outlined">error</span>
                {raporHatasi}
              </span>
            ) : null}
            {token ? (
              <button
                type="button"
                className="device-report-btn"
                onClick={() => {
                  setRaporHatasi("");
                  setRaporSecimi(true);
                }}
                disabled={raporUretiliyor}
                aria-busy={raporUretiliyor}
              >
                {raporUretiliyor ? (
                  <span className="btn-spinner" aria-hidden="true" />
                ) : (
                  <span className="material-symbols-outlined">picture_as_pdf</span>
                )}
                {raporUretiliyor
                  ? t("deviceDetail.exportPdfBusy")
                  : t("deviceDetail.exportPdf")}
              </button>
            ) : null}
            {showReset ? (
              <button
                type="button"
                className="device-reset-btn"
                onClick={() => void runResetAlarm()}
                disabled={busyReset}
                aria-busy={busyReset}
              >
                {busyReset ? (
                  <span className="btn-spinner" aria-hidden="true" />
                ) : (
                  <span className="material-symbols-outlined">restart_alt</span>
                )}
                {t("deviceDetail.quick.reset")}
              </button>
            ) : null}
          </div>
        </div>

        {/* BAGLANTI VE OTURUM — yapilandirma / calisma zamani / teshis ayrimi.
            Genel bakisin USTUNDE duruyor: "cihaz su an ne durumda" sorusu
            olcum degerlerinden ONCE cevaplanmali. `smart_idle` bir cihazda
            olcumlerin bayat olmasi ARIZA DEGIL, uykunun sonucudur; bu kart
            olmadan operator ayni ekrandan ters sonucu cikariyordu. */}
        {activeTab === "overview" ? <DeviceRuntimePanel device={device} /> : null}

        {activeTab === "overview" ? (
          <OverviewTab
            token={token ?? ""}
            device={device}
            activeSource={activeSource}
            rowBySuffix={rowBySuffix}
            allRows={rows}
            deviceAlarms={deviceAlarms}
            curNow={curNow}
            voltNow={voltNow}
            tempNow={tempNow}
            curUntrusted={curUntrusted}
            voltUntrusted={voltUntrusted}
            tempUntrusted={tempUntrusted}
            countersUntrusted={countersUntrusted}
            curUnit={unitOf(`${activeSource}.actual_current`)}
            voltUnit={unitOf(`${activeSource}.actual_voltage`)}
            tempUnit={unitOf(`${activeSource}.device_temperature`) ?? unitOf(`${activeSource}.conductor_temperature`)}
            permCount={permCount}
            momCount={momCount}
            kitMi={kitMi}
            onViewAllEvents={() => setActiveTab("events")}
            t={t}
          />
        ) : null}

        {activeTab === "all" ? (
          <DeviceAllSignalsTab
            device={device}
            // Model suzgecinden gecmis satirlar: "Tumu" karti da yalnizca bu
            // cihazin sinyal listesindeki noktalari gostersin.
            values={deviceValues}
            gwOnline={gwOnline}
            sourceCounts={sourceCounts}
            sources={measuringSources}
          />
        ) : null}

        {activeTab === "poleMaster" && kitKaydi ? (
          <PoleMasterTab parent={kitKaydi} values={values} signals={signals} />
        ) : null}

        {activeTab === "trends" && token ? (
          <div className="device-trend-panel">
            <DeviceChartsPanel
              deviceCode={device.code}
              activeSource={activeSource}
              signals={signals}
              token={token}
            />
          </div>
        ) : null}

        {activeTab === "events" && token ? (
          <div className="device-events-fullpanel">
            <section className="device-card is-events-full">
              <h3 className="device-card-title">{t("deviceDetail.overview.recentEvents")}</h3>
              <DeviceEventsTable token={token} deviceCode={device.code} variant="full" />
            </section>
          </div>
        ) : null}

        {activeTab === "commands" && canCommand && onDeviceCommand && token ? (
          <DeviceCommandsPanel
            deviceCode={device.code}
            signals={signals}
            canCommand={canCommand}
            canConfig={canConfig}
            onDeviceCommand={onDeviceCommand}
            token={token}
          />
        ) : null}

        {activeTab === "config" && canConfig ? (
          <DeviceConfigPanel
            device={device}
            token={token ?? ""}
            canConfig={canConfig}
            canCommand={canCommand}
          />
        ) : null}
      </div>

      {raporSecimi ? (
        <DeviceReportModal
          device={device}
          busy={raporUretiliyor}
          onKapat={() => setRaporSecimi(false)}
          onIndir={(sections) => void raporIndir(sections)}
        />
      ) : null}
    </div>
  );
}

// ============================ Genel Bakis sekmesi ============================

function OverviewTab({
  token,
  device,
  activeSource,
  rowBySuffix,
  allRows,
  deviceAlarms,
  curNow,
  voltNow,
  tempNow,
  curUntrusted,
  voltUntrusted,
  tempUntrusted,
  countersUntrusted,
  curUnit,
  voltUnit,
  tempUnit,
  permCount,
  momCount,
  kitMi,
  onViewAllEvents,
  t,
}: {
  token: string;
  device: DeviceRow;
  activeSource: SignalSource;
  rowBySuffix: Map<string, Row>;
  allRows: Row[];
  deviceAlarms: AlarmEvent[];
  curNow?: number;
  voltNow?: number;
  tempNow?: number;
  /** Olcum guvenilmez mi (gateway kopuk ya da engelleyici kalite).
   *  KPI seridi bunu OKUMUYORDU: ayni ekranin alt bolumu sinyali
   *  "Güvenilmez" gosterirken serit ayni degeri taze olcum gibi
   *  sunuyordu. Bkz. shared/signalQuality.ts. */
  curUntrusted?: boolean;
  voltUntrusted?: boolean;
  tempUntrusted?: boolean;
  /** Ariza sayaclari cihazin kendi kayitlari; gateway kopukken bunlar da
   *  bayat/guvenilmezdir. */
  countersUntrusted?: boolean;
  curUnit?: string;
  voltUnit?: string;
  tempUnit?: string;
  permCount?: number;
  momCount?: number;
  /** Acik kayit KITIN KENDISI mi — KPI seridi buna gore degisir. */
  kitMi?: boolean;
  onViewAllEvents: () => void;
  t: (key: string, opts?: Record<string, unknown>) => string;
}) {
  // Canli deger yoksa (cihaz offline) historian son degeri (sparkline'dan) fallback.
  const [lastCur, setLastCur] = useState<number | null>(null);
  const [lastVolt, setLastVolt] = useState<number | null>(null);
  const [lastTemp, setLastTemp] = useState<number | null>(null);
  const curVal = curNow ?? lastCur ?? null;
  const voltVal = voltNow ?? lastVolt ?? null;
  const tempVal = tempNow ?? lastTemp ?? null;
  const stale = (live: number | undefined, fb: number | null) => live == null && fb != null;
  const kpiStaleTitle = t("deviceDetail.kpi.staleHint");
  const kpiUntrustedTitle = t("deviceDetail.kpi.untrustedHint");
  // Ucluk elle yazilmisti ve `sat03` "Satellite 02" gorunuyordu: Pole Master
  // Kit setinin UCUNCU unitesi hep yanlis adla anilirdi. Tek kaynak:
  // `sourceLabel` (signalCatalogConstants).
  const srcLabel = sourceLabel(activeSource);

  /** KURCALAMA — "Normal" demeden ONCE guvenilirlik.
   *
   *  Bir sayi ciplak sayidir ama "Normal" bir HUKUMDUR: haberlesmesi kopmus
   *  bir cihaz icin gateway `comm_lost` kalitesiyle 0.0 basar ve kart bunu
   *  "sorun yok" diye gosterirse sunucunun kararini gecersiz kilar. Bu, bu
   *  urundeki en agir hata sinifi (bkz. shared/signalQuality.ts).
   *
   *  Kontrol KPI seridinden once burada duruyor; `test_ui_green_lie_guard`
   *  bunu ayrica dogruluyor ("guvenilirlik kontrolu ROZETTEN once"). */
  const kurcalamaSatiri = rowBySuffix.get("tamper_detection");
  const kurcalama = {
    trust: signalTrust(kurcalamaSatiri?.value, kurcalamaSatiri?.effQuality, true),
    aktif: kurcalamaSatiri?.value === 1,
  };

  return (
    <div className="device-overview">
      <div className="device-overview-srchint">
        <span className="material-symbols-outlined">tune</span>
        {t("deviceDetail.overview.sourceHint", { source: srcLabel })}
      </div>
      {/* KPI serit */}
      {kitMi ? (
        /* KIT SERIDI — kitin GERCEKTEN olctugu seyler.
         *
         * Kit tek DNP3 outstation'dir ve akim/gerilim/ariza sinyali YOKTUR;
         * onlar uydularda, yani setlerde. Sabit SN 2.0 seridi burada bes
         * kartin dordunu sonsuza kadar "Veri yok" yapiyordu — ekran
         * dolu gorunuyor ama hicbir sey soylemiyordu. Kitin kendi konulari:
         * besleme (solar/AC/batarya), RTU sicakligi, modem ve kurcalama. */
        <div className="device-overview-kpis">
          <KpiCard
            emptyText={t("deviceDetail.status.noData")}
            icon="device_thermostat"
            tone="rose"
            label={t("deviceDetail.kpi.temperature")}
            value={fmt(tempVal, "analog", tempUnit)}
            stale={stale(tempNow, lastTemp)}
          >
            {token ? (
              <Sparkline
                token={token}
                deviceCode={device.code}
                signalKey={`${activeSource}.device_temperature`}
                color="#f43f5e"
                onLastValue={setLastTemp}
              />
            ) : null}
          </KpiCard>
          <KpiCard
            emptyText={t("deviceDetail.status.noData")}
            icon="battery_full"
            tone="green"
            label={t("deviceDetail.kpi.battery")}
            value={fmt(rowBySuffix.get("battery_voltage_satellite")?.value ?? null, "analog", "V")}
          />
          <KpiCard
            emptyText={t("deviceDetail.status.noData")}
            icon="cell_tower"
            tone="blue"
            label={t("deviceDetail.kpi.network")}
            value={fmt(rowBySuffix.get("modem_rssi")?.value ?? null, "analog", "dBm")}
          />
          <KpiCard
            emptyText={t("deviceDetail.status.noData")}
            icon="power"
            tone="amber"
            label={t("deviceDetail.kpi.supply")}
            value={(() => {
              // Iki ayri binary: gunes paneli ve sebeke. Ikisi de bilinmiyorsa
              // "—" kalir; "yok" demek olcumu olmayan bir sey uydurmak olurdu.
              const solar = rowBySuffix.get("solar_power")?.value;
              const ac = rowBySuffix.get("ac_power")?.value;
              if (solar == null && ac == null) return "—";
              const kaynaklar: string[] = [];
              if (solar === 1) kaynaklar.push(t("deviceDetail.kpi.supplySolar"));
              if (ac === 1) kaynaklar.push(t("deviceDetail.kpi.supplyAc"));
              return kaynaklar.length > 0
                ? kaynaklar.join(" + ")
                : t("deviceDetail.kpi.supplyBattery");
            })()}
          />
          <KpiCard
            emptyText={t("deviceDetail.status.noData")}
            icon="security"
            tone={kurcalama.trust !== "trusted" ? "slate" : kurcalama.aktif ? "red" : "green"}
            label={t("deviceDetail.kpi.tamper")}
            value={
              kurcalama.trust !== "trusted"
                ? "—"
                : kurcalama.aktif
                  ? t("deviceDetail.status.active")
                  : t("deviceDetail.status.normal")
            }
          />
        </div>
      ) : (
      <div className="device-overview-kpis">
        <KpiCard emptyText={t("deviceDetail.status.noData")} staleTitle={kpiStaleTitle} untrustedTitle={kpiUntrustedTitle} icon="bolt" tone="amber" label={t("deviceDetail.kpi.current")} value={fmt(curVal, "analog", curUnit)} stale={stale(curNow, lastCur)} untrusted={curUntrusted}>
          {token ? (
            <Sparkline token={token} deviceCode={device.code} signalKey={`${activeSource}.actual_current`} color="#f59e0b" onLastValue={setLastCur} />
          ) : null}
        </KpiCard>
        <KpiCard emptyText={t("deviceDetail.status.noData")} staleTitle={kpiStaleTitle} untrustedTitle={kpiUntrustedTitle} icon="electric_bolt" tone="blue" label={t("deviceDetail.kpi.voltage")} value={fmt(voltVal, "analog", voltUnit)} stale={stale(voltNow, lastVolt)} untrusted={voltUntrusted}>
          {token ? (
            <Sparkline token={token} deviceCode={device.code} signalKey={`${activeSource}.actual_voltage`} color="#3b82f6" onLastValue={setLastVolt} />
          ) : null}
        </KpiCard>
        <KpiCard emptyText={t("deviceDetail.status.noData")} staleTitle={kpiStaleTitle} untrustedTitle={kpiUntrustedTitle} icon="device_thermostat" tone="rose" label={t("deviceDetail.kpi.temperature")} value={fmt(tempVal, "analog", tempUnit)} stale={stale(tempNow, lastTemp)} untrusted={tempUntrusted}>
          {token ? (
            <Sparkline token={token} deviceCode={device.code} signalKey={`${activeSource}.device_temperature`} color="#f43f5e" onLastValue={setLastTemp} />
          ) : null}
        </KpiCard>
        <KpiCard emptyText={t("deviceDetail.status.noData")} staleTitle={kpiStaleTitle} untrustedTitle={kpiUntrustedTitle} icon="report" tone="red" label={t("deviceDetail.permanentFaults")} value={fmt(permCount ?? null, "counter")} untrusted={countersUntrusted} />
        <KpiCard emptyText={t("deviceDetail.status.noData")} staleTitle={kpiStaleTitle} untrustedTitle={kpiUntrustedTitle} icon="flash_on" tone="orange" label={t("deviceDetail.momentaryFaults")} value={fmt(momCount ?? null, "counter")} untrusted={countersUntrusted} />
      </div>
      )}

      {/* 2 kolon: sol Mevcut Durum (2 kolonlu), sag Son Olaylar */}
      <div className="device-overview-grid">
        <div className="device-overview-col">
          <section className="device-card">
            <h3 className="device-card-title">{t("deviceDetail.overview.currentStatus")}</h3>
            <StatusTable rows={allRows} rowBySuffix={rowBySuffix} t={t} />
          </section>
        </div>

        <div className="device-overview-col">
          {/* Alarmlar — sadece bu cihaz + aktif kaynak */}
          <section className="device-card">
            <h3 className="device-card-title">
              <span className="material-symbols-outlined device-card-title-icon">notifications_active</span>
              {t("deviceDetail.alarms.title", { source: srcLabel })}
            </h3>
            <DeviceAlarmsCard alarms={deviceAlarms} activeSource={activeSource} />

          </section>
          {/* Son Olaylar */}
          <section className="device-card is-events">
            <div className="device-card-titlerow">
              <h3 className="device-card-title">{t("deviceDetail.overview.recentEvents")}</h3>
              <button type="button" className="device-card-viewall" onClick={onViewAllEvents}>
                {t("deviceDetail.overview.viewAllEvents")}
                <span className="material-symbols-outlined">chevron_right</span>
              </button>
            </div>
            {token ? (
              <DeviceEventsTable token={token} deviceCode={device.code} limit={6} />
            ) : null}
          </section>
        </div>
      </div>
    </div>
  );
}

function KpiCard({
  icon,
  tone,
  label,
  value,
  stale,
  untrusted,
  emptyText,
  staleTitle,
  untrustedTitle,
  children,
}: {
  icon: string;
  tone: string;
  label: string;
  value: string;
  stale?: boolean;
  /** Olcum GUVENILMEZ: gateway kopuk ya da kalite engelleyici
   *  (comm_lost/invalid/forced...). Deger GIZLENMEZ — urun karari son degeri
   *  gostermek — ama "taze olcum" gibi sunulamaz. Bu isaret olmadan KPI
   *  seridi, ayni ekranin alt bolumu "Güvenilmez" derken ayni sinyali
   *  duz bir sayi olarak gosteriyordu: tek ekranda iki farkli cevap. */
  untrusted?: boolean;
  /** Deger yokken yazilacak metin ("Veri yok"). Tek basina duran koca bir
   *  tire, kartin bozuk mu yoksa bos mu oldugunu soylemiyordu. */
  emptyText?: string;
  staleTitle?: string;
  untrustedTitle?: string;
  children?: ReactNode;
}) {
  const bos = value === "—";
  // Guvenilmezlik bayatliktan AGIR: ikisi birdeyse guvenilmez gosterilir.
  const isaret = untrusted ? " is-untrusted" : stale ? " is-stale" : "";
  return (
    <div className={`device-kpi tone-${tone}${bos ? " is-empty" : ""}`}>
      <div className="device-kpi-head">
        <span className="device-kpi-label">{label}</span>
        <span className="device-kpi-icon material-symbols-outlined">{icon}</span>
      </div>
      <div
        className={`device-kpi-value${isaret}`}
        title={untrusted ? untrustedTitle : stale ? staleTitle : undefined}
      >
        {bos && emptyText ? <span className="device-kpi-nodata">{emptyText}</span> : value}
        {untrusted && !bos ? (
          <span className="device-kpi-untrusted-mark" aria-hidden="true">
            ?
          </span>
        ) : null}
      </div>
      {children ? <div className="device-kpi-spark">{children}</div> : null}
    </div>
  );
}

// Mevcut Durum — aktif kaynagin TUM sinyalleri, kategori gruplu.
// binary/binary_output -> durum rozeti (Aktif/Normal); analog/counter -> deger.
function StatusItem({
  row,
  t,
}: {
  row: Row;
  t: (key: string, opts?: Record<string, unknown>) => string;
}) {
  const suffix = suffixOf(row.signal_key);
  // Katalog adi Ingilizce girildigi icin ceviri SONEK uzerinden yapilir;
  // sozlukte olmayan sinyal katalog adiyla gorunmeye devam eder.
  const label = signalLabel(row.signal_key, row.signal_label);
  const dt = (row.effType as string | undefined) ?? (row.data_type as string | undefined);
  const isBinary = dt === "binary" || dt === "binary_output";

  if (isBinary) {
    const icon = STATUS_ICONS[suffix] ?? "toggle_on";

    // UC DURUM — eskiden yalnizca `value === 1` bakiliyordu ve "veri yok" ile
    // "gercekten normal" AYNI yesil rozeti uretiyordu.
    //
    // En tehlikelisi ucuncu hal: haberlesmesi kopan cihaz icin gateway
    // `comm_lost` kalitesiyle 0.0 basiyor. Backend bu okumayi alarm
    // degerlendirmesine SOKMUYOR; arayuz ise onu "taze" sanip yesil
    // gosterip sunucunun kararini gecersiz kiliyordu.
    const trust = signalTrust(row.value, row.effQuality, true);

    // DEGER HIC YOKSA gosterilecek bir sey de yok.
    if (trust === "missing") {
      return (
        <div className="device-status-item">
          <span className="device-status-name" title={row.signal_key}>
            <span className="material-symbols-outlined">{icon}</span>
            {label}
          </span>
          <span className="device-status-badge is-unknown">
            <span className="material-symbols-outlined">help</span>
            {t("deviceDetail.status.noData")}
          </span>
        </div>
      );
    }

    // GUVENILMEZ OLCUM DEGERI GIZLEMEZ, DAMGALAR.
    //
    // Onceden kalite engelleyiciyse satir yalnizca "Güvenilmez" yaziyor ve
    // SON DEGER kayboluyordu: operator sinyalin Normal mi Aktif mi oldugunu
    // goremiyordu. Oysa Canli Degerler sayfasi ayni veriyi hep gosteriyor —
    // orada deger ve kalite AYRI sutunlar. Ayni cihaz, ayni okuma, iki
    // ekranda iki farkli cevap.
    //
    // Kural yine de korunuyor: bayat bir okuma duz yesil "Normal" olarak
    // GOSTERILMEZ (bkz. shared/signalQuality.ts — yesil yalan). Deger
    // notr renkte ve "son bilinen" etiketiyle cikiyor; boylece operator
    // hem son durumu gorur hem de bunun taze olmadigini bilir. Ayni desen
    // bu dosyadaki KPI kartlarinda zaten var (`is-stale`, "son bilinen
    // deger").
    const active = row.value === 1;
    const bayat = trust !== "trusted";
    const durumMetni = active
      ? t("deviceDetail.status.active")
      : t("deviceDetail.status.normal");
    return (
      <div className="device-status-item">
        <span className="device-status-name" title={row.signal_key}>
          <span className="material-symbols-outlined">{icon}</span>
          {label}
        </span>
        <span
          className={`device-status-badge ${
            bayat ? "is-stale" : active ? "is-active" : "is-normal"
          }`}
          title={
            bayat
              ? t("deviceDetail.status.lastKnownHint", {
                  quality: row.effQuality ?? "?"
                })
              : undefined
          }
        >
          <span className="material-symbols-outlined">
            {bayat ? "history" : active ? "warning" : "check_circle"}
          </span>
          {durumMetni}
          {bayat ? (
            <em className="device-status-stale">{t("deviceDetail.status.lastKnown")}</em>
          ) : null}
        </span>
      </div>
    );
  }
  // analog / counter -> deger
  const val =
    dt === "string"
      ? (row.value_string ?? "").trim() || "—"
      : fmt(row.value ?? null, dt, row.unit);
  return (
    <div className="device-status-item is-value">
      <span className="device-status-name" title={row.signal_key}>
        {label}
      </span>
      <span className="device-status-itemval">{val}</span>
    </div>
  );
}

function StatusTable({
  rows,
  t,
}: {
  rows: Row[];
  rowBySuffix: Map<string, Row>;
  t: (key: string, opts?: Record<string, unknown>) => string;
}) {
  // Sinyalleri gruplara ayir (bilgi/altyapi HARIC). Grup ici: binary'ler once,
  // sonra label'a gore.
  const byGroup = useMemo(() => {
    const m = new Map<GroupKey, Row[]>();
    for (const g of GROUP_ORDER) m.set(g.key, []);
    for (const r of rows) {
      const suffix = suffixOf(r.signal_key);
      if (INFO_SUFFIX_RE.test(suffix)) continue; // IP/serial/firmware -> sidebar/Tumu
      const dt = (r.effType as string | undefined) ?? (r.data_type as string | undefined);
      if (dt === "binary_output") continue; // komut -> Komutlar sekmesi
      const g = groupOfSuffix(suffix, dt);
      m.get(g)?.push(r);
    }
    // Grup ici siralama: once binary (durum rozetli), sonra analog/counter
    // (deger). Her tip kendi icinde label'a gore — analoglar EN ALTTA toplansin.
    const isBinaryRow = (r: Row): boolean => {
      const dt = (r.effType as string | undefined) ?? (r.data_type as string | undefined);
      return dt === "binary" || dt === "binary_output";
    };
    for (const arr of m.values()) {
      arr.sort((a, b) => {
        const ba = isBinaryRow(a) ? 0 : 1;
        const bb = isBinaryRow(b) ? 0 : 1;
        if (ba !== bb) return ba - bb; // binary once, analog sonra
        return (a.signal_label || a.signal_key).localeCompare(b.signal_label || b.signal_key);
      });
    }
    return m;
  }, [rows]);

  // Dolu gruplar (bos olanlar tab'da gorunmez).
  const activeGroups = GROUP_ORDER.filter((g) => (byGroup.get(g.key)?.length ?? 0) > 0);
  const [tab, setTab] = useState<GroupKey>(activeGroups[0]?.key ?? "protection");
  // Aktif tab bos kaldiysa (kaynak degisti) ilk dolu gruba dus.
  const curTab = activeGroups.some((g) => g.key === tab) ? tab : activeGroups[0]?.key;

  if (activeGroups.length === 0) {
    return (
      <div className="device-events-empty">
        <span className="material-symbols-outlined">sensors_off</span>
        <p>{t("deviceDetail.noSignals", { source: "" })}</p>
      </div>
    );
  }

  const items = curTab ? byGroup.get(curTab) ?? [] : [];
  // Binary (durum rozetli) ve analog (deger) ayri bloklar — analoglar EN ALTTA
  // toplu (grid akisinda araya karismasin).
  const isBin = (r: Row): boolean => {
    const dt = (r.effType as string | undefined) ?? (r.data_type as string | undefined);
    return dt === "binary" || dt === "binary_output";
  };
  const binItems = items.filter(isBin);
  const valItems = items.filter((r) => !isBin(r));

  return (
    <div className="device-status">
      {/* Alt-sekmeler (kategori) — scroll yerine tab */}
      <div className="device-status-tabs" role="tablist">
        {activeGroups.map((g) => (
          <button
            key={g.key}
            type="button"
            role="tab"
            aria-selected={curTab === g.key}
            className={`device-status-tab${curTab === g.key ? " active" : ""}`}
            onClick={() => setTab(g.key)}
          >
            <span className="material-symbols-outlined">{g.icon}</span>
            {t(`deviceDetail.groups.${g.key}`)}
            <span className="device-status-tab-count">{byGroup.get(g.key)?.length ?? 0}</span>
          </button>
        ))}
      </div>
      {/* Analoglar (deger) USTTE, binary (durum) ALTTA */}
      {valItems.length > 0 ? (
        <div className="device-status-grid is-values">
          {valItems.map((r) => (
            <StatusItem key={r.signal_key} row={r} t={t} />
          ))}
        </div>
      ) : null}
      {binItems.length > 0 ? (
        <div className={`device-status-grid${valItems.length > 0 ? " has-sep" : ""}`}>
          {binItems.map((r) => (
            <StatusItem key={r.signal_key} row={r} t={t} />
          ))}
        </div>
      ) : null}
    </div>
  );
}

