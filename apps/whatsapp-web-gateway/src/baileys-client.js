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
};

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
  const { version } = await fetchLatestBaileysVersion();

  const sock = makeWASocket({
    version,
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
      state.status = "connected";
      state.qrDataUrl = null;
      state.phoneNumber = sock.user && sock.user.id ? sock.user.id.split(":")[0].split("@")[0] : null;
    }

    if (connection === "close") {
      const statusCode = lastDisconnect && lastDisconnect.error && lastDisconnect.error.output
        ? lastDisconnect.error.output.statusCode
        : null;
      // restartRequired (515) ilk QR taramasindan hemen sonra normal —
      // Baileys ayni session ile tek seferlik yeniden baglanma ister.
      // Bunun disindaki her kapanma (badSession, loggedOut, connectionLost,
      // WA'nin cihazi reddetmesi vb.) bozuk/geçersiz session anlamina gelir;
      // session silinmezse ayni credentials ile sonsuz retry doner ve QR
      // hicbir zaman uretilmez. Bu yuzden restartRequired disinda session'i
      // sifirlayip taze QR akisina donuyoruz.
      const restartOnly = statusCode === DisconnectReason.restartRequired;
      state.status = "disconnected";
      state.qrDataUrl = null;
      state.phoneNumber = null;
      if (restartOnly) {
        connect().catch((err) => console.error("[whatsapp-web-gateway] reconnect basarisiz:", err));
      } else {
        clearSession();
        connect().catch((err) => console.error("[whatsapp-web-gateway] reconnect basarisiz:", err));
      }
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
  connect().catch((err) => console.error("[whatsapp-web-gateway] yeniden baglanma basarisiz:", err));
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
