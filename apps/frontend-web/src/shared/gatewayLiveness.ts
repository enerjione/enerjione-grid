/**
 * Gateway durum modeli — "BILMIYORUM" ile "ARIZA" ayni sey degildir.
 *
 * YASANAN SORUN
 * -------------
 * Durum yalnizca TELEMETRI YASINDAN cikariliyordu: son sinyal 60 saniyeden
 * eskiyse kirmizi "Bagli degil". Bu, birbirinden tamamen farkli uc olayi tek
 * bir kirmizi noktaya indiriyordu:
 *
 *   - operator gateway'i BILEREK durdurdu   -> ariza degil, karar
 *   - container ayakta ama veri gelmiyor    -> GERCEK ariza
 *   - gateway baska bir cihazda, haberimiz yok -> bilmiyoruz
 *
 * Panelden gateway durduran operator, ekranda "baglanti yok" gorup sahada
 * olmayan bir arizayi kovaliyordu.
 *
 * KAYNAK
 * ------
 * Host ajani (e1-gwd) container durumunu ZATEN raporluyor (`LocalGateway.state`:
 * running | exited | created | absent | unknown). Yeni bir veritabani alanina
 * gerek yok; eksik olan tek sey bu bilginin karara katilmasiydi.
 *
 * Ajan bu gateway'i tanimiyorsa (baska cihaza kurulu, ya da ajan yok)
 * `localState` null gelir ve telemetri eskiyse "unknown" denir — "offline"
 * DEGIL. Backend'in staleness watchdog'u da ayni ilkeyi uyguluyor
 * (bilinmiyorsa cihaz UNKNOWN'a cekilir, OFFLINE'a degil).
 *
 * React'tan bagimsiz saf fonksiyon: davranisi test edilebilsin diye.
 */

/** Son sinyal bundan eskiyse gateway'i "canli" saymayiz. */
export const GATEWAY_LIVE_SEC = 60;

export type GatewayLivenessState =
  /** Telemetri taze — calisiyor. */
  | "online"
  /** Ajan container'in durdugunu SOYLUYOR. Ariza degil. */
  | "stopped"
  /** Container ayakta ama telemetri eski — gercek ariza. */
  | "unreachable"
  /** Telemetri eski ve sebebini bilmiyoruz. */
  | "unknown"
  /** Yayin bayragi kapali (`is_active=false`) — kayit ayari. */
  | "inactive"
  /** Merkezle hic temas etmemis. */
  | "never";

export type GatewayLivenessInput = {
  isActive: boolean;
  lastSeenAt?: string | null;
  /**
   * Host ajaninin bildirdigi container durumu. Ajan bu gateway'i tanimiyorsa
   * (baska cihazda kurulu / ajan yok) null.
   */
  localState?: string | null;
  /**
   * Ajanin bildirdigi docker durum metni ("Up 4 seconds", "Exited (0) ...").
   * YALNIZCA "container ne zamandir ayakta" sorusu icin okunur; durum karari
   * `localState` ile verilir.
   */
  localStatus?: string | null;
  /**
   * Ajanin anlik goruntusu (state.json) ESKIMIS mi — backend'in verdigi
   * karar (`GatewayAgentStatus.reason === "state_stale"`).
   *
   * Esik backend'de (STALE_STATE_SECONDS); burada TEKRAR hesaplanmaz ki
   * "taze ne demek" sorusunun iki ayri cevabi olmasin.
   */
  localStale?: boolean;
  /** Testler icin; verilmezse `Date.now()`. */
  nowMs?: number;
};

/**
 * Ajanin "calismiyor" dedigi container durumlari.
 *
 * `absent` BILEREK DISARIDA: container silinmisse durduruldugunu degil,
 * kurulu OLMADIGINI biliyoruz — "durduruldu" demek uydurma olurdu.
 */
const DURMUS_DURUMLAR = new Set(["exited", "created", "paused", "dead"]);

/**
 * Container HENUZ AYAGA KALKTI mi? ("Up 4 seconds", "Up Less than a second",
 * "Up About a minute")
 *
 * NEDEN: "veri yok" olcusu GATEWAY_LIVE_SEC'lik bir PENCEREDIR. Container
 * o pencere kadar bile ayakta degilse pencere daha DOLMAMISTIR — "calisiyor
 * ama veri yok" demek olcmeden verilmis bir ariza iddiasi olur. Bu, tam da
 * yeni eklenen "Baslat" dugmesinin kendi urettigi bir yanlis durumdu:
 * durdurulmus gateway'in son sinyali cok eski oldugu icin, baslatildigi anda
 * kart kirmizi "Ulasilamiyor (gercek ariza)" gosteriyordu — ustelik "islem
 * tamamlandi" yazisiyla ayni anda.
 *
 * Docker'in sure metni yerellestirilmez (units.HumanDuration, hep Ingilizce);
 * yine de ayristirilamayan her metin "yeni degil" sayilir, yani bu kestirme
 * bir sey BOZAMAZ: en fazla eski davranisa duser.
 */
const YENI_KALKTI_RE = /^up\s+(less than a second|about a minute|\d+\s+seconds?)\b/;

function yeniAyagaKalkti(status: string | null | undefined): boolean {
  return YENI_KALKTI_RE.test((status ?? "").trim().toLowerCase());
}

export function gatewayLiveness(input: GatewayLivenessInput): GatewayLivenessState {
  // ESKIMIS ANLIK GORUNTU KESIN IDDIAYA DAYANAK OLAMAZ.
  //
  // Ajanin rapor zamanlayicisi durursa (timer kapali, docker takildi, cihaz
  // mesgul) state.json oldugu yerde kalir. O dosyadaki "exited" kaydi bir
  // olcum degil, ESKI bir olcumdur: container o gunden beri baslatilmis
  // olabilir (`update` akisi `up -d` ile baslatir, ya da host'ta elle).
  // Eskimis kaydi okuyup "Durduruldu" demek, veri AKARKEN gateway'i kapali
  // gostermek ve altindaki tum cihazlari gri yapmak demekti — bu dosyanin
  // ortadan kaldirmak icin yazildigi hatanin ta kendisi, yonu ters cevrilmis
  // hali. Ayni sekilde eskimis bir "running" kaydi da "Ulasilamiyor" gibi
  // GERCEK bir ariza iddiasini besleyemez.
  //
  // Eskimisse ajan hic konusmamis sayilir; karar telemetriye kalir ve en
  // kotu ihtimalle "bilinmiyor" denir. Telemetrinin TAZE olmasi ise dolayli
  // bir cikarim degil, dogrudan gozlemdir: veri geliyorsa geliyordur.
  const local = input.localStale ? "" : (input.localState ?? "").trim().toLowerCase();

  // 1) Ajan durdugunu soyluyorsa baska sinyale bakmaya gerek yok: telemetrinin
  //    neden kesildigini BILIYORUZ. Bu dal en basta olmali; aksi halde ayni
  //    gateway once "son sinyal eski" diye kirmiziya boyanirdi.
  if (DURMUS_DURUMLAR.has(local)) return "stopped";

  if (!input.isActive) return "inactive";
  if (!input.lastSeenAt) return "never";

  const at = new Date(input.lastSeenAt).getTime();
  // Bozuk tarih: "canli" demek icin gerekce yok, "ariza" demek icin de.
  if (!Number.isFinite(at)) return "unknown";

  const now = input.nowMs ?? Date.now();
  if ((now - at) / 1000 < GATEWAY_LIVE_SEC) return "online";

  // Telemetri eski. Sebebini soyleyebilir miyiz?
  //   container ayakta ve bir suredir ayakta -> evet: ulasamiyoruz, ariza.
  //   container YENI kalkti -> hayir: olcum penceresi daha dolmadi.
  //   ajan tanimiyor        -> hayir: bilmiyoruz.
  // Son iki durumda kirmizi gostermek uydurma olur.
  if (local === "running") {
    return yeniAyagaKalkti(input.localStatus) ? "unknown" : "unreachable";
  }
  return "unknown";
}

/**
 * Gateway durumunun ALTINDAKI CIHAZLARA yansimasi.
 *
 * Gateway calismiyorken cihazin kendi durumu platform tarafinda dogrulanamaz.
 * Onceden bu durumda tum cihazlar "offline" (kirmizi) gosteriliyordu; yani
 * gateway'i durdurmak butun agaci ariza rengine boyuyordu. Yalnizca gateway'in
 * ayakta oldugunu BILIP veri alamadigimizda "offline" demek dogru; kalan
 * durumlarda dogru cevap "unknown".
 */
export function deviceStatusUnderGateway(
  gatewayState: GatewayLivenessState,
  deviceStatus: "online" | "offline" | "unknown"
): "online" | "offline" | "unknown" {
  if (gatewayState === "online") return deviceStatus;
  if (gatewayState === "unreachable") return "offline";
  return "unknown";
}
