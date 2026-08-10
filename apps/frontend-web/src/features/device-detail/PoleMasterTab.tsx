/**
 * PoleMasterTab — Pole Master Kit'in KIT SEVIYESI verileri.
 *
 * NEDEN AYRI SEKME
 * ----------------
 * Bir Horstmann Pole Master Kit tek DNP3 outstation'dir; uzerindeki 9 uydu
 * ucerli setler halinde sahada BAGIMSIZ noktalara kelepcelenir ve her set
 * kullaniciya ayri bir cihaz olarak gorunur. Ama kitin KENDI olcumleri
 * (modem sinyali, GPS, sebeke bilgisi, solar/AC besleme, boost modu, cihaz
 * sicakligi, kurcalama) uc setin ORTAK varligidir.
 *
 * Bunlari setin kendi sinyalleriyle ayni listede gostermek iki seyi birden
 * bozardi: hangi degerin SETE hangisinin KITE ait oldugu karisir ve ucuncu
 * setin "Solar Power" satiri, birinci setinkinden farkli bir sey sanilir.
 * Bu yuzden ayri bir sekme: her sette gorunur, ama acikca "Pole Master"
 * basligi altinda.
 *
 * VERI NEREDEN GELIYOR
 * --------------------
 * FIZIKSEL kit kaydindan (`device.parentDeviceId`). Kit telemetrisi
 * COGALTILMAZ — uc sanal cihaza da yazmak, telemetri tuketicisindeki
 * (consumer, message_id) tekil kisitini ihlal eder ve mesajin sonsuza kadar
 * yeniden teslim edilmesine yol acardi. Bu yuzden veri tek yerde durur ve
 * burada OKUMA TARAFINDA devralinir.
 */

import { useMemo } from "react";
import { useTranslation } from "react-i18next";

import { signalLabel } from "../../shared/signalLabel";
import type { DeviceRow, SignalLiveRow } from "../../shared/types";

type Props = {
  /** Fiziksel kit kaydi. Yoksa sekme hic gosterilmez. */
  parent: DeviceRow;
  /** Tum canli degerler — bu bilesen kendi filtresini uygular. */
  values: SignalLiveRow[];
};

/** Kit seviyesindeki sinyalleri okunabilir gruplara ayirir.
 *
 * Sira = gorunum sirasi. Bir sinyal ilk eslesen gruba duser; hicbirine
 * uymayan "Diger"e gider — GIZLENMEZ. Gizlemek, kitin yeni firmware ile
 * gonderdigi bir noktanin arayuzde hic gorunmemesi demek olurdu.
 */
const GROUPS: { key: string; icon: string; test: RegExp }[] = [
  // Besleme: solar panel, sebeke (AC) ve batarya.
  { key: "power", icon: "bolt", test: /(solar_power|ac_power|battery|boost_mode)/ },
  // Haberlesme: modem, sebeke operatoru, IP, IMEI, SIM.
  {
    key: "comm",
    icon: "cell_tower",
    test: /(modem|rssi|network|sim_serial|ipv4|ip_address|imei|dial_in|comm_library|rtu_status)/,
  },
  // Konum ve kimlik.
  {
    key: "identity",
    icon: "badge",
    test: /(gps|latitude|longitude|serial|part_no|firmware|fw_version|hardware_revision|last_configuration)/,
  },
  // Cihaz durumu: sicaklik, kurcalama, calisma modu, parola/yerel iletisim.
  {
    key: "status",
    icon: "monitor_heart",
    test: /(temperature|tamper|operation_mode|password|local_comm|fast_curve|test|voltage_loss_all_units)/,
  },
];

const NUMBER_FORMATTER = new Intl.NumberFormat("tr-TR", {
  minimumFractionDigits: 0,
  maximumFractionDigits: 3,
  useGrouping: false,
});

function suffixOf(signalKey: string): string {
  const i = signalKey.indexOf(".");
  return i < 0 ? signalKey : signalKey.slice(i + 1);
}

function groupOf(suffix: string): string {
  const s = suffix.toLowerCase();
  for (const g of GROUPS) {
    if (g.test.test(s)) return g.key;
  }
  return "other";
}

function displayValue(row: SignalLiveRow): string {
  if (row.data_type === "string") return row.value_string?.trim() || "—";
  if (row.value == null) return "—";
  if (row.data_type === "binary" || row.data_type === "binary_output") {
    return row.value ? "1" : "0";
  }
  const num = NUMBER_FORMATTER.format(row.value);
  return row.unit ? `${num} ${row.unit}` : num;
}

export function PoleMasterTab({ parent, values }: Props) {
  const { t } = useTranslation();

  const gruplar = useMemo(() => {
    // KOMUT NOKTALARI HARIC: `binary_output` bir olcum degil, bir dugmedir;
    // yeri Komutlar sekmesi. Deger olarak gostermek "Firmware Update = 0"
    // gibi anlamsiz satirlar uretirdi.
    const rows = values.filter(
      (r) =>
        r.device_id === parent.id &&
        r.source === "master" &&
        r.data_type !== "binary_output"
    );
    const out = new Map<string, SignalLiveRow[]>();
    for (const r of rows) {
      const key = groupOf(suffixOf(r.signal_key));
      const list = out.get(key);
      if (list) list.push(r);
      else out.set(key, [r]);
    }
    for (const list of out.values()) {
      list.sort((a, b) => a.signal_label.localeCompare(b.signal_label, "tr"));
    }
    return out;
  }, [values, parent.id]);

  const sirali = [...GROUPS.map((g) => g.key), "other"].filter((k) => gruplar.has(k));

  return (
    <div className="pole-master-panel">
      <p className="pole-master-note">
        {t("deviceDetail.poleMaster.note", { code: parent.code })}
      </p>

      {sirali.length === 0 ? (
        <p className="pole-master-empty">{t("deviceDetail.poleMaster.empty")}</p>
      ) : (
        <div className="pole-master-grid">
          {sirali.map((key) => {
            const meta = GROUPS.find((g) => g.key === key);
            return (
              <section className="device-card pole-master-card" key={key}>
                <h3 className="device-card-title">
                  <span className="material-symbols-outlined">
                    {meta?.icon ?? "more_horiz"}
                  </span>
                  {t(`deviceDetail.poleMaster.groups.${key}`)}
                </h3>
                <dl className="pole-master-list">
                  {(gruplar.get(key) ?? []).map((row) => (
                    <div className="pole-master-row" key={row.signal_key}>
                      <dt>{signalLabel(row.signal_key, row.signal_label)}</dt>
                      <dd>{displayValue(row)}</dd>
                    </div>
                  ))}
                </dl>
              </section>
            );
          })}
        </div>
      )}
    </div>
  );
}
