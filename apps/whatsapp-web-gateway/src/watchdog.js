"use strict";

/**
 * Asili kalan servisi kendi kendine sonlandiran bekci (Node surumu).
 *
 * NEDEN GEREKLI
 * -------------
 * `restart: unless-stopped` yalnizca CIKIS yapan sureci geri kaldirir. Olay
 * dongusu kilitlenen bir servis "calisiyor" gorunur: container ayakta, surec
 * ayakta, ama hicbir istek islenmiyor. Docker healthcheck tek basina yetmez:
 * unhealthy bir container'i yeniden BASLATMAZ, yalnizca isaretler.
 *
 * NEDEN worker_threads — setInterval YETMEZ
 * -----------------------------------------
 * Python worker'larinda bekci ayri bir OS ipliginde kosuyor. Node tek
 * ipliklidir: bekciyi `setInterval` ile ayni olay dongusune koysaydik,
 * dongu kilitlendiginde BEKCININ KENDISI de donmaz ve yakalamasi gereken
 * tek arizayi yakalayamazdi. Yani calisiyormus gibi gorunen, gercekte hicbir
 * sey yapmayan bir koruma olurdu — korumasizliktan daha kotusu, cunku
 * guven verir.
 *
 * Bu yuzden sayac AYRI BIR IPLIKTE izlenir. worker_threads ayni SURECTE
 * calistigi icin `process.kill(process.pid, "SIGKILL")` tum sureci indirir;
 * SIGTERM degil, cunku kilitlenmis bir dongu sinyal isleyicisini de
 * calistiramaz.
 *
 * ATIS NEYE BAGLANMALI
 * --------------------
 * "Mesaj gonderdim"e DEGIL, "olay dongum hala calisiyor"a. Aksi halde mesaj
 * gonderilmeyen sakin bir gecede saglikli servis oldurulurdu — ve bu,
 * cozdugumuz sorundan daha kotu bir ariza olurdu. Ayrica WhatsApp'in
 * BAGLI OLMAMASI da ariza sayilmaz: kopma rutin (ag dalgalanmasi, WA sunucu
 * rotasyonu, ilk kurulumda QR bekleme) ve yeniden baglanma zinciri bunu
 * zaten kendi ele aliyor (bkz. baileys-client.js).
 *
 * SAAT KULLANILMAZ
 * ----------------
 * Atis bir ZAMAN DAMGASI degil, artan bir SAYAC. Zaman damgasi olsaydi
 * NTP'nin ileri atmasi sahte bir "takildi" uretip saglikli servisi
 * oldururdu. Sayac karsilastirmasi saatten tamamen bagimsizdir.
 */

const { Worker } = require("worker_threads");

//: Ana dongunun atissiz kalabilecegi en uzun sure.
const ESIK_SN = 60;
//: Kalp atis araligi. Esik/atis = kacirilmasi gereken atis sayisi (12).
const ATIS_SN = 5;

const paylasim = new SharedArrayBuffer(4);
const sayac = new Int32Array(paylasim);

/** Ana dongu tarafindan cagrilir: "hala donuyorum". */
function kalpAt() {
  Atomics.add(sayac, 0, 1);
}

/** Healthcheck ucu icin: en son atistan bu yana sayac degisti mi. */
function okuSayac() {
  return Atomics.load(sayac, 0);
}

/**
 * Bekciyi baslatir.
 *
 * @param {object} [opts]
 * @param {number} [opts.esikSn]  Atissiz gecebilecek en uzun sure (sn).
 * @param {number} [opts.atisSn]  Atis araligi (sn).
 * @param {string} [opts.servis]  Log'da gorunecek servis adi.
 * @returns {{worker: Worker, durdur: () => void}}
 */
function baslat(opts) {
  const ayar = opts || {};
  const esikSn = ayar.esikSn || ESIK_SN;
  const atisSn = ayar.atisSn || ATIS_SN;
  const servis = ayar.servis || "whatsapp-web-gateway";

  // Ana dongu atiyor. Dongu kilitlenirse bu timer da calismaz — izleyen
  // iplik sayacin dondugunu gorur.
  const timer = setInterval(kalpAt, atisSn * 1000);
  // Bekci yuzunden surec acik kalmasin: tek is bu timer olsaydi Node
  // kapanmayi beklerken sonsuza kadar ayakta kalirdi.
  if (typeof timer.unref === "function") timer.unref();
  kalpAt();

  const izleyiciKodu = `
    const { parentPort, workerData } = require("worker_threads");
    const sayac = new Int32Array(workerData.paylasim);
    const { atisSn, esikSn, servis } = workerData;
    // Kac ardisik kontrolde sayac hic degismezse takilmis sayilir.
    const tolerans = Math.max(2, Math.ceil(esikSn / atisSn));
    let sonDeger = Atomics.load(sayac, 0);
    let degismeyen = 0;
    setInterval(() => {
      const simdi = Atomics.load(sayac, 0);
      if (simdi === sonDeger) {
        degismeyen += 1;
        if (degismeyen >= tolerans) {
          console.error(
            "[" + servis + "] ana dongu " + (degismeyen * atisSn) +
            " sn'dir atis yapmadi (esik " + esikSn + " sn) — surec " +
            "sonlandiriliyor, restart politikasi geri kaldiracak."
          );
          // worker_threads ayni SURECTE calisir: bu, tum sureci indirir.
          // SIGKILL, cunku kilitlenmis dongu SIGTERM isleyicisini calistiramaz.
          process.kill(process.pid, "SIGKILL");
        }
      } else {
        sonDeger = simdi;
        degismeyen = 0;
      }
    }, atisSn * 1000);
  `;

  const worker = new Worker(izleyiciKodu, {
    eval: true,
    workerData: { paylasim, atisSn, esikSn, servis },
  });
  // Izleyici surecin kapanmasini engellemesin.
  worker.unref();

  console.log(
    `[${servis}] bekci calisiyor (ayri iplik), esik ${esikSn} sn, atis ${atisSn} sn.`
  );

  return {
    worker,
    durdur() {
      clearInterval(timer);
      worker.terminate();
    },
  };
}

module.exports = { baslat, kalpAt, okuSayac, ESIK_SN, ATIS_SN };
