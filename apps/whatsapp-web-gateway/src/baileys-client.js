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

function toJid(rawPhone) {
  const digits = String(rawPhone || "").replace(/[^0-9]/g, "");
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
      const loggedOut = statusCode === DisconnectReason.loggedOut;
      state.status = "disconnected";
      state.qrDataUrl = null;
      state.phoneNumber = null;
      if (!loggedOut) {
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

async function logout() {
  if (state.sock) {
    try {
      await state.sock.logout();
    } catch (err) {
      // Zaten kopuk olabilir — session'i yine de temizle.
    }
  }
  fs.rmSync(SESSION_DIR, { recursive: true, force: true });
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

module.exports = { connect, sendMessage, logout, getStatus, getQr };
