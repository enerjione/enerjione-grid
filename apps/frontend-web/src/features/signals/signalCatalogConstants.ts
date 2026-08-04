import type { SignalDataType, SignalSource } from "../../shared/types";

// IEC 60870-5-104 monitor-direction ASDU Type ID'leri.
// Kullanici dropdown'dan secsin diye standart kataloga referans.
export const IEC104_MONITOR_TYPES: { id: number; code: string; desc: string; dataTypes: SignalDataType[] }[] = [
  { id: 1,  code: "M_SP_NA_1", desc: "Single point",                   dataTypes: ["binary"] },
  { id: 3,  code: "M_DP_NA_1", desc: "Double point",                   dataTypes: ["binary"] },
  { id: 9,  code: "M_ME_NA_1", desc: "Normalized measured value",      dataTypes: ["analog"] },
  { id: 11, code: "M_ME_NB_1", desc: "Scaled measured value",          dataTypes: ["analog"] },
  { id: 13, code: "M_ME_NC_1", desc: "Short floating point",           dataTypes: ["analog"] },
  { id: 15, code: "M_IT_NA_1", desc: "Integrated total (counter)",     dataTypes: ["counter"] },
  { id: 30, code: "M_SP_TB_1", desc: "Single point with CP56Time2a",   dataTypes: ["binary"] },
  { id: 31, code: "M_DP_TB_1", desc: "Double point with CP56Time2a",   dataTypes: ["binary"] },
  { id: 34, code: "M_ME_TD_1", desc: "Normalized + CP56Time2a",        dataTypes: ["analog"] },
  { id: 35, code: "M_ME_TE_1", desc: "Scaled + CP56Time2a",            dataTypes: ["analog"] },
  { id: 36, code: "M_ME_TF_1", desc: "Short float + CP56Time2a",       dataTypes: ["analog"] },
  { id: 37, code: "M_IT_TB_1", desc: "Counter + CP56Time2a",           dataTypes: ["counter"] }
];

// IEC 104 type id <-> CP56Time2a variant mapping. Operator "Zaman etiketi
// gonder" toggle'ini acinca sec ilgili Type ID otomatik time-tag varyantina
// donusur; kapatinca tekrar non-time-tag varyantina.
const TYPE_ID_TIME_TAG_MAP: Record<number, number> = {
  1: 30,   // M_SP_NA_1 -> M_SP_TB_1
  3: 31,   // M_DP_NA_1 -> M_DP_TB_1
  9: 34,   // M_ME_NA_1 -> M_ME_TD_1
  11: 35,  // M_ME_NB_1 -> M_ME_TE_1
  13: 36,  // M_ME_NC_1 -> M_ME_TF_1
  15: 37,  // M_IT_NA_1 -> M_IT_TB_1
};
const TYPE_ID_NO_TIME_TAG_MAP: Record<number, number> = Object.fromEntries(
  Object.entries(TYPE_ID_TIME_TAG_MAP).map(([k, v]) => [v, Number(k)])
);

export function convertTypeIdForTimeTag(currentTypeId: number | null, withTimeTag: boolean): number | null {
  if (currentTypeId === null) return null;
  if (withTimeTag) {
    return TYPE_ID_TIME_TAG_MAP[currentTypeId] ?? currentTypeId;
  } else {
    return TYPE_ID_NO_TIME_TAG_MAP[currentTypeId] ?? currentTypeId;
  }
}

// Bos string veya invalid sayi icin null doner.
export function parseIntOrNullModule(v: string): number | null {
  const t = v.trim();
  if (!t) return null;
  const n = Number(t);
  return Number.isFinite(n) ? Math.round(n) : null;
}

export const DATA_TYPES: SignalDataType[] = [
  "analog",
  "binary",
  "counter",
  // String (G110 Octet String): SIM CCID, IMEI, IPv4, GPS, seri no, FW version...
  "string",
  "analog_output",
  // Binary Output = DNP3 CROB komut kanali (Trigger Download, Reset...).
  "binary_output"
];

export const SOURCES: SignalSource[] = ["master", "sat01", "sat02"];

export const SOURCE_LABEL: Record<SignalSource, string> = {
  master: "Master",
  sat01: "Satellite 01",
  sat02: "Satellite 02"
};

export const DATA_TYPE_LABEL: Record<SignalDataType, string> = {
  analog: "Analog Input",
  binary: "Binary Input",
  counter: "Counter",
  string: "String",
  binary_output: "Binary Output (Komut)",
  analog_output: "Analog Output"
};

export const DATA_TYPE_SHORT: Record<SignalDataType, string> = {
  analog: "Analog",
  binary: "Binary",
  counter: "Counter",
  string: "String",
  binary_output: "Komut",
  analog_output: "AO"
};

// --- Historian (arsiv) politikasi -----------------------------------------
//
// Bu uc sabit backend'deki kararlarin AYNASIDIR:
//   OLU_BANT_TIPLERI     <- app/services/historian_policy.py
//   ARIZA_TIPLERI        <- app/services/signal_catalog_service.py
//   OLU_BANT_UST_SINIR   <- app/models/signal_catalog.py
// Arayuz burada YETKILI DEGIL: son soz sunucuda: `POST /signals/historian/bulk`
// ve `PATCH /signals/{key}` ayni kurallari yeniden uygular. Buradaki kopyanin
// isi, kullanicinin calismayacak bir ayari girmesini ONCEDEN engellemek.

/** Olu bandin GERCEKTEN uygulandigi veri tipleri. Digerlerinde motor esigi
 *  yok sayar: `binary`de 0 -> 1 bir olaydir, "yakin deger" diye bir sey yok. */
export const DEADBAND_DATA_TYPES: SignalDataType[] = ["analog"];

/** Ariza gecisi tasiyan tipler — arsivi kapatmak gecmis incelemesini
 *  imkansiz kilar; arayuz bunu acikca uyarmadan gecirmez. */
export const FAULT_DATA_TYPES: SignalDataType[] = ["binary", "binary_output"];

/** Olu bandin ust siniri (sinyalin kendi biriminde).
 *
 *  Katalogdaki birimler A / mA / V / °C / ° / dBm / ms; hicbirinde bir
 *  milyonluk esik "suzgec" degildir, "hic arsivleme" demektir — ve bunu olu
 *  bantla yapmak SESSIZDIR: sinyal listede "Arsivleniyor" gorunmeye devam
 *  eder. Niyet buysa arsiv acikca kapatilmali (o yol uyari + denetim kaydi
 *  uretir). Sunucu da ayni siniri uygular. */
export const DEADBAND_MAX = 1_000_000;

/** Girilen esik kabul edilebilir mi? `Number.isFinite` NaN'i da eler:
 *  `Number("")` 0, `Number("abc")` NaN doner ve NaN her karsilastirmayi
 *  sessizce gecerdi. */
export function isValidDeadband(value: number): boolean {
  return Number.isFinite(value) && value >= 0 && value <= DEADBAND_MAX;
}

export function deadbandApplies(type: SignalDataType): boolean {
  return DEADBAND_DATA_TYPES.includes(type);
}

export function isFaultSignal(type: SignalDataType): boolean {
  return FAULT_DATA_TYPES.includes(type);
}

/** Arsiv acik mi? Eski backend alani hic gondermiyor; `undefined` = acik. */
export function isHistorized(signal: { historize?: boolean }): boolean {
  return signal.historize !== false;
}

/** Sinyalde ETKIN bir olu bant var mi? Tip analog degilse deger tasinsa bile
 *  etkisizdir; "var" demek kullaniciyi yanıltırdı. */
export function effectiveDeadband(signal: {
  historize_deadband?: number;
  data_type: SignalDataType;
}): number {
  if (!deadbandApplies(signal.data_type)) return 0;
  const d = signal.historize_deadband ?? 0;
  return Number.isFinite(d) && d > 0 ? d : 0;
}

// DNP3 standart nesne grubu - veri tipine göre 1-1 eşlesir.
export const DNP3_GROUP_BY_TYPE: Record<SignalDataType, number> = {
  analog: 30,
  binary: 1,
  counter: 20,
  string: 110,
  binary_output: 10,
  analog_output: 40
};
