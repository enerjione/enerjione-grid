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
 *
 * LISTE KATALOGDAN, DEGER TELEMETRIDEN
 * ------------------------------------
 * Once yalnizca CANLI SATIRLAR listeleniyordu; kit henuz hicbir deger
 * gondermediyse (yeni kurulum, haberlesme kopuk) sekme tek satirlik bir
 * "veri gelmedi" notundan ibaretti. Operator o ekrana bakip kitin hangi
 * bilgileri gonderdigini bile ogrenemiyordu.
 *
 * Artik satirlarin ISKELETI sinyal katalogundan gelir (kitin MODELINE ait,
 * aktif `master.*` sinyalleri); canli deger varsa yazilir, yoksa "Veri yok"
 * rozeti durur. Ekranin geri kalaniyla ayni davranis (bkz. Mevcut Durum).
 */

import { useMemo } from "react";
import { useTranslation } from "react-i18next";

import { signalLabel } from "../../shared/signalLabel";
import type { DeviceRow, SignalCatalogRow, SignalLiveRow } from "../../shared/types";

type Props = {
  /** Fiziksel kit kaydi. Yoksa sekme hic gosterilmez. */
  parent: DeviceRow;
  /** Tum canli degerler — bu bilesen kendi filtresini uygular. */
  values: SignalLiveRow[];
  /** Sinyal katalogu — satir iskeleti buradan (deger gelmese de gorunur). */
  signals: SignalCatalogRow[];
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

/** Ekranda tek bir satir: etiket + (varsa) canli deger. */
type Satir = { key: string; label: string; row?: SignalLiveRow };

export function PoleMasterTab({ parent, values, signals }: Props) {
  const { t } = useTranslation();

  /** Kitin CANLI master satirlari (anahtar -> satir). */
  const canli = useMemo(() => {
    const m = new Map<string, SignalLiveRow>();
    for (const r of values) {
      if (r.device_id !== parent.id || r.source !== "master") continue;
      m.set(r.signal_key, r);
    }
    return m;
  }, [values, parent.id]);

  const gruplar = useMemo(() => {
    // KOMUT NOKTALARI HARIC: `binary_output` bir olcum degil, bir dugmedir;
    // yeri Komutlar sekmesi. Deger olarak gostermek "Firmware Update = 0"
    // gibi anlamsiz satirlar uretirdi.
    const satirlar = new Map<string, Satir>();

    // 1) Iskelet: kitin MODELINE ait aktif master sinyalleri.
    for (const s of signals) {
      if (s.model !== parent.model || s.source !== "master") continue;
      if (!s.is_active || s.data_type === "binary_output") continue;
      satirlar.set(s.key, { key: s.key, label: signalLabel(s.key, s.label) });
    }

    // 2) Canli deger: katalogda olmayan bir nokta gelirse (firmware yeni bir
    //    sey gonderiyor) satiri yine de acilir — gizlemek, gelen veriyi
    //    gorunmez kilardi.
    for (const [key, row] of canli) {
      if (row.data_type === "binary_output") continue;
      const mevcut = satirlar.get(key);
      if (mevcut) mevcut.row = row;
      else satirlar.set(key, { key, label: signalLabel(key, row.signal_label), row });
    }

    const out = new Map<string, Satir[]>();
    for (const satir of satirlar.values()) {
      const grup = groupOf(suffixOf(satir.key));
      const list = out.get(grup);
      if (list) list.push(satir);
      else out.set(grup, [satir]);
    }
    for (const list of out.values()) {
      // Degeri OLAN satirlar once: kit yeni kurulmussa dolu olan birkac satir
      // yuzlerce "Veri yok"un arasinda kaybolmasin.
      list.sort((a, b) => {
        if (!!a.row !== !!b.row) return a.row ? -1 : 1;
        return a.label.localeCompare(b.label, "tr");
      });
    }
    return out;
  }, [signals, canli, parent.model]);

  const sirali = [...GROUPS.map((g) => g.key), "other"].filter((k) => gruplar.has(k));

  return (
    <div className="pole-master-panel">
      {/* Ust kisimda ne aciklama ne DOLULUK SAYACI var (kullanici istegi):
          "52/36 dolu" bir is degeri tasimiyordu — okuyan kisi icin anlamli
          olan, asagidaki kartlarda hangi degerin geldigi. Veri hic gelmiyorsa
          bunu zaten `pole-master-empty` metni soyluyor. */}
      {sirali.length === 0 ? (
        <p className="pole-master-empty">{t("deviceDetail.poleMaster.empty")}</p>
      ) : (
        <div className="pole-master-grid">
          {sirali.map((key) => {
            const meta = GROUPS.find((g) => g.key === key);
            const liste = gruplar.get(key) ?? [];
            const dolu = liste.filter((s) => s.row).length;
            return (
              <section className="device-card pole-master-card" key={key}>
                <h3 className="device-card-title">
                  <span className="material-symbols-outlined">
                    {meta?.icon ?? "more_horiz"}
                  </span>
                  {t(`deviceDetail.poleMaster.groups.${key}`)}
                  <span className="pole-master-count">
                    {dolu}/{liste.length}
                  </span>
                </h3>
                <dl className="pole-master-list">
                  {liste.map((satir) => (
                    <div className="pole-master-row" key={satir.key}>
                      <dt>{satir.label}</dt>
                      {satir.row ? (
                        <dd>{displayValue(satir.row)}</dd>
                      ) : (
                        <dd className="is-nodata">{t("deviceDetail.status.noData")}</dd>
                      )}
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
