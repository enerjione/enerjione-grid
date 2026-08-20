/**
 * DeviceSidebar — cihaz detay sol sabit panel.
 *
 * Cihaz kimlik (kod solunda CALISMA-ZAMANI durum noktasi) + birlesik BILGILER
 * (bolge/hat/IP/pil) + mini harita (topoloji konumu) + kanal secimi.
 * activeSource sidebar'dan kontrol edilir.
 *
 * Durum noktasi ARTIK IKILI DEGIL: renk/etiket `shared/deviceRuntimeState.ts`
 * icindeki tek normalizerden gelir. Ikili "online/offline" karari uyuyan bir
 * Horstmann'i (`smart_idle`) ariza rengine boyuyordu.
 */

import { useTranslation } from "react-i18next";
import { MapContainer, Marker, Tooltip } from "react-leaflet";
import { ResilientTileLayer } from "../../components/ResilientTileLayer";
import { MAP_LAYERS } from "../../shared/mapTiles";
import L from "leaflet";

import { formatRelative } from "../../shared/format";
import { RuntimeStateChip, runtimeToneClass } from "../../components/RuntimeStateChip";
import { RuntimeTip } from "../../components/RuntimeTooltip";
import { deviceRuntimeStateOf } from "../../shared/deviceRuntimeState";
import { sourceLabel, sourceTone } from "../signals/signalCatalogConstants";
import { sinyalKalitesi } from "./modemStatus";
import type { DeviceRow, SignalSource } from "../../shared/types";

// Cihaz pin ikonu (Leaflet divIcon).
const DEVICE_PIN = L.divIcon({
  className: "device-map-pin",
  html: '<span class="device-map-pin-dot"></span>',
  iconSize: [18, 18],
  iconAnchor: [9, 9],
});

type TopologyInfo =
  | { regionName: string; lineName: string; latitude?: number; longitude?: number }
  | undefined;

type Props = {
  device: DeviceRow;
  /** FIZIKSEL kit kaydi — cihaz bir Pole Master Kit SETI ise dolu.
   *
   *  Setin kendi haberlesmesi, modemi ve RTU pili YOKTUR: uc set tek bir
   *  DNP3 outstation'in (kitin) arkasindadir. Bu yuzden durum/son iletisim/
   *  pil/sebeke sinyali KIT kaydindan okunur; setin kendi kaydindaki
   *  degerler kitten kopyalanmis ya da hic dolmamis alanlardi ve sahada
   *  "set cevrimici ama kit degil" gibi imkansiz durumlar gosteriyordu. */
  parentDevice?: DeviceRow;
  topologyInfo?: TopologyInfo;
  /** Sebeke alim seviyesi (dBm) - modemin ham yanitindan cozulur
   *  (`master.info_network_rf_status_information`; bkz. modemStatus.ts). */
  rssi?: number;
  /** Operator adi - ayni ham yanittan; `info_network_operator` bos geliyor. */
  networkOperator?: string;
  /** Master IP (master.ipv4_address). */
  ip?: string;
  /** Part No (master.info_part_no). */
  partNo?: string;
  /** Firmware surumu (master.firmware_version / info_fw_version). */
  firmware?: string;
  /** Cihazin calisma modu — `master.operation_mode` binary sinyalinden.
   *
   *  `undefined` = BILINMIYOR (deger yok ya da kalite guvenilmez). O zaman
   *  satir hic cizilmez: iki modun ikisi de gecerli oldugu icin, bilmedigimiz
   *  bir durumu ikisinden biri gibi gostermek dogrudan yanlis bilgi olurdu
   *  (bkz. DeviceDetailPage, `sidebarOperationMode`). */
  operationMode?: "smart" | "boost";
  /** Gercek alarm listesinden: aktif (giderilmemis) alarm var mi. */
  hasAlarm?: boolean;
  /** Alarm listesi cekilemediyse dolu — kart "bilinmiyor"a gecer. */
  alarmError?: string;
  /** Kanal seri no'lari (master/sat01/sat02 serial_number). */
  channelSerials?: Partial<Record<SignalSource, string>>;
  /** Kanal pil yuzdeleri (0..100) — her cihazin ayri pil seviyesi. */
  channelBattery?: Partial<Record<SignalSource, number>>;
  /** Setin sat01/02/03 kanallarinin FIZIKSEL uydu numarasi (set 2 -> 4/5/6).
   *
   *  Atamayi kurulumcu degistirebiliyor; hangi kanalin hangi uyduya bagli
   *  oldugu ekranda GORUNUR olmali, yoksa yanlis atama ancak sahada fark
   *  edilir. */
  channelSatelliteNo?: Partial<Record<SignalSource, number>>;
  activeSource: SignalSource;
  onSourceChange: (s: SignalSource) => void;
  /** Her kaynaktaki sinyal sayisi (0 ise kanal disabled). */
  sourceCounts: Record<SignalSource, number>;
  /** KITIN KENDI sayfasinda: bu kite bagli setler (sanal alt cihazlar).
   *  Set sayfasinda BOS gecilir — orada gosterilecek bir alt cihaz yok. */
  sets?: readonly DeviceRow[];
  /** Set id -> pil yuzdesi. Setin pili kendi uydularindan gelir. */
  setBattery?: Record<number, number | undefined>;
  /** Sete tiklaninca o setin detay sayfasini ac. */
  onOpenSet?: (deviceId: number) => void;
};

/** Cihazin OLCUM YAPAN unite kanallari — MODELE gore.
 *
 * Horstmann SN 2.0'da ucuncu unite ANA unitedir (`master` olcum yapar).
 * Pole Master Kit'in bir SETINDE ise ucu de uydudur: kitin `master`i ortak
 * RTU'dur, bir faza kelepcelenmez ve setin telemetrisinde `master.*` HIC
 * YOKTUR. Sabit liste kullansaydik set acildiginda hep bos bir "Master"
 * kanali gorunur, gercek ucuncu unite (`sat03`) ise hic gorunmezdi.
 * Kit seviyesindeki degerler ayri bir "Pole Master" sekmesinde.
 */
function channelsFor(device: DeviceRow): { key: SignalSource; label: string; tone: string }[] {
  const isSet = (device.parentDeviceId ?? null) !== null;
  const keys: SignalSource[] = isSet
    ? ["sat01", "sat02", "sat03"]
    : ["master", "sat01", "sat02"];
  return keys.map((key) => ({ key, label: sourceLabel(key), tone: sourceTone(key) }));
}

// `rssiQuality` KALDIRILDI — sebeke sinyali satiriyla birlikte. Modem
// olculeri "Pole Master" sekmesinde duruyor.

// Pil % -> renk sinifi (ana sayfa ile ayni esikler).
function batteryClass(pct: number | null): string {
  // null = bilinmiyor. Ne "ok" (yesil) ne "critical" — notr kalir; yesil
  // gostermek, veri yokken saglikli oldugunu iddia etmek olurdu.
  if (pct === null) return "device-battery--unknown";
  if (pct <= 20) return "device-battery--critical";
  if (pct <= 50) return "device-battery--low";
  return "device-battery--ok";
}

export function DeviceSidebar({
  device,
  parentDevice,
  rssi,
  networkOperator,
  topologyInfo,
  ip,
  partNo,
  firmware,
  operationMode,
  hasAlarm = false,
  alarmError = "",
  channelSerials,
  channelBattery,
  channelSatelliteNo,
  activeSource,
  onSourceChange,
  sourceCounts,
  sets,
  setBattery,
  onOpenSet,
}: Props) {
  const { t } = useTranslation();
  // Haberlesme/pil/sinyal SAHIBI cihaz: sette kit, sade cihazda kendisi.
  const health = parentDevice ?? device;
  // CALISMA-ZAMANI DURUMU tek normalizerden gelir. Eskiden burada
  // `communicationStatus === "online"` ikili karari vardi; `smart_idle`
  // (uyuyan, SAGLIKLI Horstmann) o karara "online degil" diye giriyor ve
  // panelin en ustundeki nokta gri/kirmizi yaniyordu. Zamanlayici YOK:
  // sayfa zaten polling ile tazeleniyor, geri sayim ise dakikalik saatiyle
  // `DeviceRuntimePanel` icinde.
  const runtime = deviceRuntimeStateOf(health);
  const quality = sinyalKalitesi(rssi);
  // Konum: cihazin kendi lat/lon'u yoksa topoloji (hat/segment) konumu.
  const validSelf =
    Number.isFinite(device.latitude) &&
    Number.isFinite(device.longitude) &&
    !(device.latitude === 0 && device.longitude === 0);
  const lat = validSelf ? device.latitude : topologyInfo?.latitude;
  const lon = validSelf ? device.longitude : topologyInfo?.longitude;
  const hasGeo =
    lat != null && lon != null && Number.isFinite(lat) && Number.isFinite(lon) && !(lat === 0 && lon === 0);

  return (
    <aside className="device-sidebar">
      {/* ---- Cihaz kimlik (kod solunda durum noktasi) ---- */}
      <section className="device-sidebar-section">
        <div className="device-sidebar-idrow">
          <RuntimeTip
            state={runtime}
            focusable
            className="device-sidebar-statusdot"
            aria-label={t(runtime.labelKey)}
            role="img"
          />
          <h2 className="device-sidebar-code">{device.name}</h2>
        </div>
        <div className="device-sidebar-name">{device.code}</div>

        {/* Genel alarm durum karti — UC durum: alarm var / temiz / BILINMIYOR.
            Ucuncusu olmadan, alarm listesi cekilemedigi anda kart yesile
            donuyordu: cihazda acik alarm varken ekran "Alarm Yok" diyordu. */}
        <div
          className={`device-sidebar-alarmcard ${
            alarmError ? "is-unknown" : hasAlarm ? "is-alarm" : "is-ok"
          }`}
        >
          <span className="device-sidebar-alarmcard-icon">
            <span className="material-symbols-outlined">
              {alarmError ? "help" : hasAlarm ? "notification_important" : "check_circle"}
            </span>
          </span>
          <div className="device-sidebar-alarmcard-body">
            <span className="device-sidebar-alarmcard-title">
              {alarmError
                ? t("deviceDetail.sidebar.alarmUnknown")
                : hasAlarm
                  ? t("deviceDetail.sidebar.alarmActive")
                  : t("deviceDetail.sidebar.alarmClear")}
            </span>
            <span className="device-sidebar-alarmcard-sub" title={alarmError || undefined}>
              {alarmError
                ? t("deviceDetail.sidebar.alarmUnknownSub")
                : hasAlarm
                  ? t("deviceDetail.sidebar.alarmActiveSub")
                  : t("deviceDetail.sidebar.alarmClearSub")}
            </span>
          </div>
          {hasAlarm && !alarmError ? (
            <span className="device-sidebar-alarmcard-pulse" aria-hidden="true" />
          ) : null}
        </div>
      </section>

      {/* ---- Birlesik BILGILER (durum ozeti + bilgiler tek yerde) ---- */}
      <section className="device-sidebar-section">
        <span className="device-sidebar-kicker">
          {t("deviceDetail.sidebar.info")}
          {/* Degerlerin KIME ait oldugu yazmazsa "set cevrimici" sanilir;
              oysa haberlesen taraf kitin RTU'sudur. */}
          {parentDevice ? (
            <span className="device-sidebar-kicker-tag">
              {t("deviceDetail.sidebar.fromKit", { name: parentDevice.name })}
            </span>
          ) : null}
        </span>
        <ul className="device-sidebar-info">
          {/* UST CIHAZ — set tek basina bir sey ifade etmiyor; hangi kitin
              parcasi oldugu buraya, diger bilgilerle ayni bicimde yazilir.
              BAGLANTI DEGIL: kitin kendi detay sayfasi yok, kit seviyesindeki
              her sey zaten "Pole Master" sekmesinde. */}
          {parentDevice ? (
            <InfoRow
              icon="dns"
              label={t("deviceDetail.sidebar.parentDevice")}
              value={parentDevice.name}
            />
          ) : null}
          {/* DURUM SATIRI — ikili "Cevrimici/Cevrimdisi" DEGIL. Alti durumun
              hepsi kendi rengiyle gorunur; ozellikle `smart_idle` MAVI ve
              SAGLIKLI, `report_late` ise ayri bir turuncu ("Gecikmis")
              kovadir. Rozet ortak bilesenden gelir ki liste/harita/detay
              ayni cihaza farkli renk vermesin. */}
          <li className="device-sidebar-info-row">
            <span className="material-symbols-outlined">wifi</span>
            <span className="device-sidebar-info-label">
              {t("deviceDetail.sidebar.deviceStatus")}
            </span>
            <RuntimeStateChip state={runtime} withIcon={false} className="runtime-chip--sm" />
          </li>
          <InfoRow
            icon="schedule"
            label={t("deviceDetail.sidebar.lastCommShort")}
            value={health.lastUpdateAt ? formatRelative(health.lastUpdateAt) : "—"}
          />
          {topologyInfo?.regionName ? (
            <InfoRow icon="map" label={t("deviceDetail.meta.region")} value={topologyInfo.regionName} />
          ) : null}
          {topologyInfo?.lineName ? (
            <InfoRow icon="timeline" label={t("deviceDetail.meta.line")} value={topologyInfo.lineName} />
          ) : null}
          {ip ? <InfoRow icon="router" label="IP" value={ip} /> : null}
          {partNo ? <InfoRow icon="qr_code_2" label={t("deviceDetail.sidebar.partNo")} value={partNo} /> : null}
          {firmware ? <InfoRow icon="memory" label={t("deviceDetail.sidebar.firmware")} value={firmware} /> : null}
          {/* CALISMA MODU — Akilli / Boost. Deger guvenilmezse ust katman
              `undefined` gecer ve satir hic cizilmez; "bilmiyorum"u
              modlardan biri gibi gostermeyiz. */}
          {operationMode ? (
            <InfoRow
              // IKON SABIT — moda gore DEGISMIYOR. Iki sebep:
              //   1) `icon={kosul ? "a" : "b"}` bicimini iconSubset testi
              //      TARAMIYOR (yalnizca `icon="ad"` literalini okur), yani
              //      fontta olmayan bir ikon sessizce gecerdi. Ilk deneme
              //      `auto_mode`di ve subset'te YOKTU: sahada ikon yerine
              //      "auto_mode" yazisi cikardi (bkz. solar_power vakasi).
              //   2) Diger BILGILER satirlarinda da satir basina tek sabit
              //      ikon var; modu zaten renk (yesil/amber) ve metin ayiriyor.
              icon="tune"
              label={t("deviceDetail.sidebar.operationMode")}
              value={t(`deviceDetail.sidebar.operationMode_${operationMode}`)}
              tone={operationMode === "smart" ? "green" : "amber"}
            />
          ) : null}

          {/* Pil — ana sayfa batarya sembolu (% ye gore renkli) */}
          <li className="device-sidebar-info-row">
            <span className="material-symbols-outlined">battery_full</span>
            <span className="device-sidebar-info-label">{t("deviceDetail.meta.battery")}</span>
            {/* null = cihaz henuz batarya bildirmedi. Bos bir cubuk + "—"
                gosterilir; eskiden varsayilan %100 yuzunden hic veri
                gondermemis cihaz DOLU batarya gosteriyordu. */}
            <span className={`device-sidebar-battery ${batteryClass(health.batteryPercent)}`}>
              <span className="device-battery-icon" aria-hidden="true">
                <span
                  className="device-battery-fill"
                  style={{
                    width:
                      health.batteryPercent === null
                        ? "0%"
                        : `${Math.max(0, Math.min(100, health.batteryPercent))}%`
                  }}
                />
              </span>
              <span className="device-sidebar-battery-text">
                {health.batteryPercent === null ? "—" : `%${Math.round(health.batteryPercent)}`}
              </span>
            </span>
          </li>

          {/* SEBEKE SINYALI — modemin ham yanitindan cozulur.
              Once sayisal bir `master.modem_rssi` araniyordu; cihaz oyle bir
              nokta YAYINLAMIYOR, seviye `info_network_rf_status_information`
              metninin icinde geliyor (bkz. modemStatus.ts). Bu yuzden satir
              hep bostu. Cozulemezse cubuk cizilmez ve "—" yazar; uydurma bir
              cubuk, zayif sinyali "iyi" gostermekten kotudur. */}
          <li className="device-sidebar-info-row">
            <span className="material-symbols-outlined">signal_cellular_alt</span>
            <span className="device-sidebar-info-label">{t("deviceDetail.sidebar.networkSignal")}</span>
            <span className={`device-sidebar-signal sig-${quality.key}`}>
              <span className="device-sidebar-signal-bars" aria-hidden="true">
                {[1, 2, 3, 4].map((b) => (
                  <span key={b} className={`bar${b <= quality.bars ? " on" : ""}`} />
                ))}
              </span>
              <span className="device-sidebar-signal-text">
                {rssi != null ? `${Math.round(rssi)} dBm` : "—"}
              </span>
            </span>
          </li>

          {/* OPERATOR — `info_network_operator` sahada BOS geliyor; ad modemin
              ham yanitinda duruyor ve orada gercekten var. */}
          {networkOperator ? (
            <InfoRow
              icon="cell_tower"
              label={t("deviceDetail.sidebar.networkOperator")}
              value={networkOperator}
            />
          ) : null}
        </ul>
      </section>

      {/* ---- SETLER (yalnizca KITIN KENDI sayfasinda) ----
           Kit tek DNP3 outstation'dir ama sahada is goren sey onun
           SETLERIDIR: her set ayri bir noktaya kelepcelenir, kendi
           arizasini uretir, kendi detay sayfasi vardir. Kitin sayfasi
           bunlari hic gostermiyordu — kullanici kaca set ekledigini
           buradan goremiyor, her birine ulasmak icin cihaz listesine
           donmek zorunda kaliyordu.
           Sayi baslikta: "uc set ekledim ama ikisi gorunuyor" ancak
           sayilabildiginde fark edilir. */}
      {sets && sets.length > 0 ? (
        <section className="device-sidebar-section">
          <span className="device-sidebar-kicker">
            {t("deviceDetail.sidebar.sets")}
            <span className="device-sets-count">{sets.length}</span>
          </span>
          <ul className="device-sidebar-sets device-sidebar-sets">
            {sets.map((s) => {
              const batt = setBattery?.[s.id];
              // Setin durumu da tek normalizerden. Kit `smart_idle` iken
              // setleri gri gostermek, uyuyan saglikli bir kiti ariza gibi
              // okuturdu.
              const setDurum = deviceRuntimeStateOf(s);
              return (
                <li key={s.id}>
                  <button
                    type="button"
                    className="device-set-row"
                    onClick={() => onOpenSet?.(s.id)}
                    disabled={!onOpenSet}
                    title={s.code}
                  >
                    <RuntimeTip
                      state={setDurum}
                      className="device-set-dot"
                      aria-label={t(setDurum.labelKey)}
                      role="img"
                    />
                    <span className="device-set-body">
                      <span className="device-set-name">{s.name}</span>
                      <span className="device-set-code">{s.code}</span>
                    </span>
                    {batt != null ? (
                      <span className={`device-set-batt ${batteryClass(batt)}`}>
                        %{Math.round(batt)}
                      </span>
                    ) : null}
                    <span className="material-symbols-outlined device-set-go">
                      chevron_right
                    </span>
                  </button>
                </li>
              );
            })}
          </ul>
        </section>
      ) : null}

      {/* ---- Kanal secimi (seri no'lu) ---- */}
      <section className="device-sidebar-section">
        <span className="device-sidebar-kicker">{t("deviceDetail.sidebar.channel")}</span>
        <ul className="device-sidebar-channels">
          {channelsFor(device).map((ch) => {
            const n = sourceCounts[ch.key] ?? 0;
            const active = activeSource === ch.key;
            const sn = channelSerials?.[ch.key];
            const batt = channelBattery?.[ch.key];
            const uyduNo = channelSatelliteNo?.[ch.key];
            return (
              <li key={ch.key}>
                <button
                  type="button"
                  className={`device-channel tone-${ch.tone}${active ? " active" : ""}`}
                  onClick={() => onSourceChange(ch.key)}
                  disabled={n === 0}
                  title={sn ? `SN ${sn}` : undefined}
                >
                  {batt != null ? (
                    <span className={`device-channel-batt ${batteryClass(batt)}`} title={`%${Math.round(batt)}`}>
                      <span className="device-battery-icon" aria-hidden="true">
                        <span
                          className="device-battery-fill"
                          style={{ width: `${Math.max(0, Math.min(100, batt))}%` }}
                        />
                      </span>
                      <span className="device-channel-batt-text">%{Math.round(batt)}</span>
                    </span>
                  ) : null}
                  <span className="device-channel-label">{ch.label}</span>
                  {/* UYDU NUMARASI ROZETI (U1/U2/U3) KALDIRILDI — kullanici
                      karari. Kanal secerken sorulan sey "hangi unite"; ayni
                      satirda hem etiket, hem numara, hem seri no durunca uc
                      ayri kimlik gibi okunuyordu. Fiziksel uydu atamasi
                      Cihaz Ayarlari'nda duruyor. */}
                  <span className="device-channel-serial">{sn ?? (n === 0 ? "—" : "")}</span>
                </button>
              </li>
            );
          })}
        </ul>
      </section>

      {/* ---- Konum + mini harita (en alta yapisik) ---- */}
      <section className="device-sidebar-mapsection">
        <div className="device-sidebar-maphead">
          <span className="device-sidebar-kicker">{t("deviceDetail.meta.location")}</span>
          {hasGeo ? (
            <span className="device-sidebar-coords">
              <span className="material-symbols-outlined">my_location</span>
              {(lat as number).toFixed(5)}, {(lon as number).toFixed(5)}
            </span>
          ) : null}
        </div>
        {hasGeo ? (
          <div className="device-sidebar-map">
            <MapContainer
              center={[lat as number, lon as number]}
              zoom={14}
              zoomControl={true}
              dragging={true}
              scrollWheelZoom={true}
              doubleClickZoom={true}
              attributionControl={false}
              style={{ height: "100%", width: "100%" }}
            >
              <ResilientTileLayer layer="osm" maxZoom={MAP_LAYERS[0].maxZoom} />
              <Marker position={[lat as number, lon as number]} icon={DEVICE_PIN}>
                <Tooltip permanent direction="top" offset={[0, -10]} className="device-map-label">
                  {device.code}
                </Tooltip>
              </Marker>
            </MapContainer>
          </div>
        ) : (
          <div className="device-sidebar-nomap">
            <span className="material-symbols-outlined">location_off</span>
            {t("deviceDetail.sidebar.noLocation")}
          </div>
        )}
      </section>
    </aside>
  );
}

function InfoRow({
  icon,
  label,
  value,
  tone,
}: {
  icon: string;
  label: string;
  value: string;
  tone?: "green" | "amber" | "slate";
}) {
  return (
    <li className="device-sidebar-info-row">
      <span className="material-symbols-outlined">{icon}</span>
      <span className="device-sidebar-info-label">{label}</span>
      <span className={`device-sidebar-info-value${tone ? ` tone-${tone}` : ""}`} title={value}>
        {tone ? <span className={`device-sidebar-info-dot dot-${tone}`} aria-hidden="true" /> : null}
        {value}
      </span>
    </li>
  );
}
