/**
 * Modem/şebeke durumu — kitin RTU'sundan gelen METİN sinyallerinin çözümü.
 *
 * NEDEN AYRI DOSYA: sol paneldeki "Şebeke Sinyali" satırı boş kalıyordu çünkü
 * `master.modem_rssi` diye SAYISAL bir sinyal aranıyordu; cihaz böyle bir
 * nokta yayınlamıyor. Gerçek değer, modemin ham yanıtını taşıyan bir STRING
 * sinyalin içinde geliyor:
 *
 *   master.info_network_rf_status_information
 *   NWS: "286 01",1651,-91,-57,-12,2120,,128,19,1,00E1A0C,
 *        "286016681396681","Turkcell",3,3,131
 *
 * Alanların tamamının anlamı modem üreticisinin belgesinde; burada YALNIZCA
 * iki şey okunuyor ve ikisi de biçimden bağımsız kurallarla:
 *
 *   sinyal (dBm) : listedeki İLK negatif sayı. Modem yanıtında seviye
 *                  ölçüleri (RSSI/RSCP/EC-IO) hep negatiftir ve ilki genel
 *                  alım seviyesidir; sonraki negatifler daha dar tanımlı
 *                  ölçülerdir. Aralık dışı (-140..-30) değerler ELENİR —
 *                  o zaman alan başka bir şeydir ve "bilmiyorum" demek,
 *                  uydurma bir çubuk çizmekten iyidir.
 *   operatör     : tırnak içindeki alanlardan RAKAM OLMAYAN ilki
 *                  ("286 01" ve IMSI benzeri uzun sayılar elenir).
 *
 * Konum bazlı okuma (5. alan gibi) BİLEREK yapılmadı: aynı ürün ailesinde
 * modem değişince alan sırası kayıyor ve sessizce yanlış bir sayı gösterilir.
 * Sıra kaysa bile "ilk negatif dBm" kuralı doğru büyüklüğü bulur.
 */

export type ModemSebekeDurumu = {
  /** Alım seviyesi (dBm). Çözülemezse undefined. */
  dbm?: number;
  /** Operatör adı ("Turkcell"). Çözülemezse undefined. */
  operator?: string;
};

/** Makul bir alım seviyesi mi? Dışındaki değerler başka bir alandır. */
function dbmGecerli(n: number): boolean {
  return Number.isFinite(n) && n <= -30 && n >= -140;
}

export function modemDurumuCoz(ham: string | undefined | null): ModemSebekeDurumu {
  const metin = (ham ?? "").trim();
  if (!metin) return {};

  // "NWS: ..." öneki varsa at; kalan virgülle ayrılmış alanlar.
  const govde = metin.replace(/^[A-Za-z^]+\s*:\s*/, "");
  const alanlar = govde.split(",").map((p) => p.trim());

  let dbm: number | undefined;
  let operator: string | undefined;
  for (const alan of alanlar) {
    const tirnakli = /^"(.*)"$/.exec(alan);
    if (tirnakli) {
      const icerik = tirnakli[1].trim();
      // "286 01" (MCC/MNC) ve "286016681396681" (IMSI) sayısaldır; operatör
      // adı değildir.
      if (operator === undefined && icerik && !/^[\d\s]+$/.test(icerik)) {
        operator = icerik;
      }
      continue;
    }
    if (dbm === undefined && /^-\d+$/.test(alan)) {
      const n = Number(alan);
      if (dbmGecerli(n)) dbm = n;
    }
  }
  return { dbm, operator };
}

/** dBm -> 4 kademeli çubuk + renk sınıfı. Ana sayfadaki eşiklerle aynı. */
export function sinyalKalitesi(dbm: number | undefined): {
  key: "good" | "fair" | "poor" | "none";
  bars: number;
} {
  if (dbm == null || !dbmGecerli(dbm)) return { key: "none", bars: 0 };
  if (dbm >= -70) return { key: "good", bars: 4 };
  if (dbm >= -85) return { key: "fair", bars: 3 };
  if (dbm >= -100) return { key: "poor", bars: 2 };
  return { key: "poor", bars: 1 };
}
