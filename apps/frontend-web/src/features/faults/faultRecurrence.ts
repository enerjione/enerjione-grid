/**
 * TEKRAR EDEN ARIZA — "bu hat daha once de arizalandi mi?"
 *
 * NEDEN: bir hat ayni yerden ikinci kez arizalaniyorsa bu gecici bir olay
 * degildir; cozulmemis bir kok sebep vardir (dala degen iletken, catlak
 * izolator, kacak). Mudahalenin onceligi ve sahada ne aranacagi degisir.
 * Kart bu bilgiyi hic gostermiyordu: operator gecmis sekmesine gecip elle
 * aramadan "ilk mi, besinci mi" bilemiyordu.
 *
 * React'ten AYRI: tarih penceresi ve aralik kesisimi kolay yanlis yazilir,
 * node:test ile dogrulanabilsin diye saf tutuldu.
 */

export type RecurrenceFault = {
  id: number;
  line_id: number;
  opened_at: string;
  from_pole_seq?: number | null;
  to_pole_seq?: number | null;
};

export type FaultRecurrence = {
  /** Pencere icinde AYNI HATTA acilmis ONCEKI kayit sayisi. */
  total: number;
  /** Kac gunluk pencereye bakildi. */
  windowDays: number;
  /** Bunlardan kacinin direk araligi bu arizayla KESISIYOR. */
  sameSection: number;
  /** En son ne zaman arizalandi (bu kayittan onceki). */
  lastAt: string | null;
};

const GUN_MS = 86_400_000;

/** Iki direk araligi kesisiyor mu? Ucu bilinmiyorsa kesismis SAYILMAZ —
 *  "bilmiyorum"u "ayni yer" diye okumak yaniltir. */
function kesisiyorMu(
  a1: number | null | undefined,
  a2: number | null | undefined,
  b1: number | null | undefined,
  b2: number | null | undefined
): boolean {
  if (a1 == null || a2 == null || b1 == null || b2 == null) return false;
  const [aLo, aHi] = a1 <= a2 ? [a1, a2] : [a2, a1];
  const [bLo, bHi] = b1 <= b2 ? [b1, b2] : [b2, b1];
  return aLo <= bHi && bLo <= aHi;
}

export function buildFaultRecurrence(
  fault: RecurrenceFault,
  all: RecurrenceFault[],
  windowDays = 90
): FaultRecurrence {
  const bu = new Date(fault.opened_at).getTime();
  // Gecersiz tarih: sayi uydurmak yerine "gecmis yok" de.
  if (!Number.isFinite(bu)) {
    return { total: 0, windowDays, sameSection: 0, lastAt: null };
  }
  const sinir = bu - windowDays * GUN_MS;

  let total = 0;
  let sameSection = 0;
  let lastAt: string | null = null;
  let lastMs = -Infinity;

  for (const other of all) {
    if (other.id === fault.id) continue;
    if (other.line_id !== fault.line_id) continue;
    const t = new Date(other.opened_at).getTime();
    if (!Number.isFinite(t)) continue;
    // YALNIZCA ONCEKILER: ayni anda acilmis kardes bolgeler (bir hatta iki
    // ayri ariza) "tekrar" degildir, ayni olayin parcalaridir.
    if (t >= bu) continue;
    if (t < sinir) continue;
    total += 1;
    if (
      kesisiyorMu(
        fault.from_pole_seq,
        fault.to_pole_seq,
        other.from_pole_seq,
        other.to_pole_seq
      )
    ) {
      sameSection += 1;
    }
    if (t > lastMs) {
      lastMs = t;
      lastAt = other.opened_at;
    }
  }

  return { total, windowDays, sameSection, lastAt };
}
