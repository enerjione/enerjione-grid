"use strict";

const fs = require("fs");
const path = require("path");
const QRCode = require("qrcode");
const {
  default: makeWASocket,
  useMultiFileAuthState,
  DisconnectReason,
  fetchLatestBaileysVersion,
} = require("@whiskeysockets/baileys");

const SESSION_DIR = process.env.WHATSAPP_SESSION_DIR || "/data/session";

// state.status: "disconnected" | "qr_pending" | "connected"
const state = {
  sock: null,
  status: "disconnected",
  qrDataUrl: null,
  phoneNumber: null,
  // Ust uste kac denemedir bir kez bile "open" goremedik. "open"da sifirlanir.
  failCount: 0,
  reconnectTimer: null,
};

//: Bu kadar denemede bir kez bile baglanamazsak credentials GERCEKTEN olu
//: sayilir ve QR akisina donulur. Gecici kopmalar (WA soketi kapatir, ag
//: gider gelir, VDS yeniden baslar) bu sayiya asla ulasmaz; 8 deneme
//: ~4 dakikalik kesintiye dayanir.
const RECONNECT_MAX_TRY = 8;

function backoffMs(tryNo) {
  // 2sn, 4, 8, 16, 32, 60, 60... — WA'yi doverek rate-limit yemenin
  // (403/forbidden) onune gecer.
  return Math.min(60000, 2000 * Math.pow(2, Math.max(0, tryNo - 1)));
}

/**
 * Kapanma kodu YENIDEN ESLESME (QR) gerektiriyor mu?
 *
 * Yalnizca `loggedOut` (401) gerektirir: kullanici telefondan "Bagli
 * cihazlar"dan bu oturumu silmistir, elimizdeki credentials artik hicbir
 * sekilde calismaz.
 *
 * DIGER HER KOD GECICIDIR — connectionClosed (428), connectionLost (408),
 * timedOut (408), restartRequired (515), connectionReplaced (440),
 * unavailableService (503), forbidden (403) — ve AYNI credentials ile
 * yeniden baglanilir.
 */
function needsRepair(statusCode) {
  return statusCode === DisconnectReason.loggedOut;
}

function clearSession() {
  // Dizinin kendisini degil, icindeki dosyalari siliyoruz — VDS'te session
  // dizininin ust sahipligi container user'indan farkli olabiliyor, bu da
  // rmdir'i EACCES ile patlatip Node process'ini crash ediyordu. Dosya
  // silme (unlink) genelde calisir cunku dizin container user'a yazilabilir,
  // sadece dizinin kendisi baska sahipte kalmis olabilir.
  let entries = [];
  try {
    entries = fs.readdirSync(SESSION_DIR);
  } catch (err) {
    return;
  }
  for (const entry of entries) {
    try {
      fs.rmSync(path.join(SESSION_DIR, entry), { recursive: true, force: true });
    } catch (err) {
      console.error("[whatsapp-web-gateway] session dosyasi silinemedi:", entry, err.message);
    }
  }
}

function toJid(rawPhone) {
  const value = String(rawPhone || "").trim();
  if (!value) throw new Error("Gecersiz telefon numarasi.");
  // Zaten tam JID (kisi veya grup) ise dokunma — grup JID'lerinde tire ve
  // harf gecebilir (orn "123456-789@g.us"), digit-strip bunu bozar.
  if (value.endsWith("@g.us") || value.endsWith("@s.whatsapp.net")) return value;
  const digits = value.replace(/[^0-9]/g, "");
  if (!digits) throw new Error("Gecersiz telefon numarasi.");
  return `${digits}@s.whatsapp.net`;
}

/** Gecici hatayi isler: sayaci artirir, gerekirse session'i bir kez sifirlar. */
function noteFailure(sebep) {
  state.failCount += 1;
  if (state.failCount >= RECONNECT_MAX_TRY) {
    // Bu kadar denemede bir kez bile "open" goremedik. Buraya kadar geldiyse
    // credentials gercekten kullanilamaz durumda; sifirlayip QR uretiyoruz.
    // Bu esik olmasa bozuk bir session'da sonsuza kadar sessizce donerdik.
    console.error(
      `[whatsapp-web-gateway] ${state.failCount} denemede baglanilamadi (${sebep}); ` +
      "session sifirlaniyor, QR gerekecek."
    );
    clearSession();
    state.failCount = 0;
    scheduleReconnect(0);
    return;
  }
  const bekleme = backoffMs(state.failCount);
  console.warn(
    `[whatsapp-web-gateway] ${sebep}; ${Math.round(bekleme / 1000)}sn sonra ` +
    `yeniden denenecek (${state.failCount}/${RECONNECT_MAX_TRY}).`
  );
  scheduleReconnect(bekleme);
}

/** Tek bir bekleyen yeniden baglanma tutar — ust uste timer birikmesin. */
function scheduleReconnect(delayMs) {
  if (state.reconnectTimer) clearTimeout(state.reconnectTimer);
  state.reconnectTimer = setTimeout(() => {
    state.reconnectTimer = null;
    // connect() KENDISI de patlayabilir (acilista ag yok gibi). Onceden
    // burada yalnizca log vardi ve zincir olurdu; simdi tekrar deniyoruz.
    connect().catch((err) => noteFailure(`yeniden baglanma hatasi: ${err.message}`));
  }, delayMs);
}

async function connect() {
  // Onceki socket hala aciksa event listener'lari birakma — gec gelen bir
  // event (eski sock'tan) yeni sock'un state'ini ezebilir (stale closure).
  if (state.sock) {
    state.sock.ev.removeAllListeners();
    try {
      state.sock.end(undefined);
    } catch (err) {
      // Zaten kopuk olabilir, yok say.
    }
    state.sock = null;
  }

  fs.mkdirSync(SESSION_DIR, { recursive: true });
  const { state: authState, saveCreds } = await useMultiFileAuthState(SESSION_DIR);
  // Bu ag cagrisidir. Sistem yeni acilirken DNS/ag hazir olmayabilir ve
  // burada atilan hata TUM baglanti zincirini oldururdu: reconnect yok,
  // QR yok, status sonsuza kadar "disconnected". Paketle gelen surum
  // fazlasiyla yeterli — bu cagri yalnizca bir iyilestirmedir.
  let version;
  try {
    ({ version } = await fetchLatestBaileysVersion());
  } catch (err) {
    console.warn(
      "[whatsapp-web-gateway] surum bilgisi alinamadi, paketteki surumle devam:",
      err.message
    );
  }

  const sock = makeWASocket({
    ...(version ? { version } : {}),
    auth: authState,
    printQRInTerminal: false,
    syncFullHistory: false,
  });
  state.sock = sock;

  sock.ev.on("creds.update", saveCreds);

  sock.ev.on("connection.update", async (update) => {
    if (state.sock !== sock) return; // eski socket'in gecikmis event'i

    const { connection, lastDisconnect, qr } = update;

    if (qr) {
      state.qrDataUrl = await QRCode.toDataURL(qr);
      state.status = "qr_pending";
    }

    if (connection === "open") {
      state.failCount = 0;
      state.status = "connected";
      state.qrDataUrl = null;
      state.phoneNumber = sock.user && sock.user.id ? sock.user.id.split(":")[0].split("@")[0] : null;
    }

    if (connection === "close") {
      const statusCode = lastDisconnect && lastDisconnect.error && lastDisconnect.error.output
        ? lastDisconnect.error.output.statusCode
        : null;
      state.status = "disconnected";
      state.qrDataUrl = null;
      state.phoneNumber = null;

      // ONCEDEN: `restartRequired` DISINDAKI HER kapanmada session
      // siliniyordu. Ama WA soketi rutin olarak kapanir (ag dalgalanmasi,
      // WA sunucu rotasyonu, VDS yeniden baslarken ag henuz hazir degil) ve
      // bunlarin hicbiri credentials'i gecersiz kilmaz. Sonuc: sistem her
      // yeniden basladiginda oturum siliniyor ve QR yeniden isteniyordu.
      // Artik yalnizca telefondan cihaz kaldirildiginda (loggedOut) siliyoruz.
      if (needsRepair(statusCode)) {
        console.warn("[whatsapp-web-gateway] oturum telefondan kaldirilmis; QR gerekiyor.");
        clearSession();
        state.failCount = 0;
        scheduleReconnect(0);
        return;
      }

      // Ilk QR taramasinin hemen ardindan gelen 515 eslesmenin normal bir
      // parcasi; beklemeden ve sayaci artirmadan devam.
      if (statusCode === DisconnectReason.restartRequired) {
        scheduleReconnect(0);
        return;
      }

      noteFailure(`baglanti kapandi (kod: ${statusCode})`);
    }
  });
}

async function sendMessage(to, message) {
  if (state.status !== "connected" || !state.sock) {
    throw new Error("WhatsApp Web bagli degil.");
  }
  const jid = toJid(to);
  await state.sock.sendMessage(jid, { text: message || "" });
}

/**
 * Gorsel + alt yazi gonderir (hat arizasi harita gorseli icin).
 *
 * NEDEN: ariza bildiriminde metin tek basina "nerede" sorusunu tam
 * cevaplamiyor — koordinat linki tiklamak, uygulama degistirmek gerekiyor.
 * Harita gorseli sohbette ANINDA gorunur; ekip mesaja bakip yola cikar.
 *
 * `imageBase64` ham base64 (data: oneki OLMADAN). Metin gonderimiyle ayni
 * baglanti/oturum kullanilir.
 */
async function sendImage(to, imageBase64, caption) {
  if (state.status !== "connected" || !state.sock) {
    throw new Error("WhatsApp Web bagli degil.");
  }
  if (!imageBase64) {
    throw new Error("Gorsel icerigi bos.");
  }
  const jid = toJid(to);
  await state.sock.sendMessage(jid, {
    image: Buffer.from(imageBase64, "base64"),
    caption: caption || ""
  });
}

async function logout() {
  if (state.sock) {
    try {
      await state.sock.logout();
    } catch (err) {
      // Zaten kopuk olabilir — session'i yine de temizle.
    }
  }
  clearSession();
  state.status = "disconnected";
  state.qrDataUrl = null;
  state.phoneNumber = null;
  // Elle cikista sayac ve bekleyen timer sifirlanir: kullanici QR'i HEMEN
  // gormeli, onceki hatalarin backoff'unu beklememeli.
  state.failCount = 0;
  if (state.reconnectTimer) {
    clearTimeout(state.reconnectTimer);
    state.reconnectTimer = null;
  }
  scheduleReconnect(0);
}

function getStatus() {
  return { status: state.status, phone_number: state.phoneNumber };
}

function getQr() {
  return { qr: state.qrDataUrl };
}

async function listGroups() {
  if (state.status !== "connected" || !state.sock) return [];
  const groups = await state.sock.groupFetchAllParticipating();
  return Object.values(groups).map((g) => ({
    jid: g.id,
    name: g.subject || g.id,
    participants: Array.isArray(g.participants) ? g.participants.length : 0,
  }));
}

module.exports = { connect, sendMessage, sendImage, logout, getStatus, getQr, listGroups };
