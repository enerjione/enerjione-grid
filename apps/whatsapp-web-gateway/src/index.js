"use strict";

const http = require("http");
const crypto = require("crypto");
const baileys = require("./baileys-client");

const PORT = parseInt(process.env.WORKER_HEALTH_PORT || "8016", 10);
const SERVICE_TOKEN = process.env.SERVICE_TOKEN || "";

// FAIL-FAST: token yoksa servis HIC AYAGA KALKMAZ.
//
// Eskiden fail-OPEN'di: `SERVICE_TOKEN` bos oldugunda `expected.length === 0`
// ve header gonderilmediginde `actual.length === 0` olur, `timingSafeEqual`
// iki bos tamponu ESIT sayar -> checkToken TRUE doner ve butun uclar
// (mesaj gonderme dahil) herkese acilir. Ustelik servis hicbir uyari
// vermeden calismaya devam ettigi icin bu durum sessizdi.
//
// Sessizce acik kalmak yerine gurultuyle durmak dogru davranis: eksik
// yapilandirma kurulumda fark edilir, sahada degil.
if (!SERVICE_TOKEN) {
  console.error(
    "whatsapp-web-gateway: SERVICE_TOKEN tanimli degil. Kimlik dogrulama " +
      "yapilamayacagi icin servis BASLATILMIYOR. .env icinde SERVICE_TOKEN " +
      "tanimlayin."
  );
  process.exit(1);
}

function checkToken(req) {
  // Ikinci savunma: token bos ise hicbir istek gecmez (yukaridaki fail-fast
  // bu duruma dusmemeyi saglar, ama kontrol burada da acikca dursun).
  if (!SERVICE_TOKEN) return false;
  const provided = req.headers["x-service-token"] || "";
  const expected = Buffer.from(SERVICE_TOKEN);
  const actual = Buffer.from(String(provided));
  if (expected.length !== actual.length) return false;
  return crypto.timingSafeEqual(expected, actual);
}

function sendJson(res, statusCode, body) {
  const data = JSON.stringify(body);
  res.writeHead(statusCode, { "Content-Type": "application/json" });
  res.end(data);
}

function readBody(req) {
  return new Promise((resolve, reject) => {
    let raw = "";
    req.on("data", (chunk) => {
      raw += chunk;
    });
    req.on("end", () => {
      if (!raw) return resolve({});
      try {
        resolve(JSON.parse(raw));
      } catch (err) {
        reject(err);
      }
    });
    req.on("error", reject);
  });
}

const server = http.createServer(async (req, res) => {
  if (req.method === "GET" && req.url === "/health") {
    sendJson(res, 200, { ok: true });
    return;
  }

  if (!checkToken(req)) {
    sendJson(res, 401, { error: "unauthorized" });
    return;
  }

  try {
    if (req.method === "GET" && req.url === "/status") {
      sendJson(res, 200, baileys.getStatus());
      return;
    }
    if (req.method === "GET" && req.url === "/qr") {
      sendJson(res, 200, baileys.getQr());
      return;
    }
    if (req.method === "GET" && req.url === "/groups") {
      sendJson(res, 200, { groups: await baileys.listGroups() });
      return;
    }
    if (req.method === "POST" && req.url === "/send") {
      const body = await readBody(req);
      await baileys.sendMessage(body.to, body.message);
      sendJson(res, 200, { ok: true });
      return;
    }
    if (req.method === "POST" && req.url === "/send-image") {
      // Ariza harita gorseli. `image` ham base64 (data: oneki olmadan);
      // govde buyuk olabilecegi icin readBody sinirini asmamak adina
      // backend PNG'yi makul cozunurlukte uretir.
      const body = await readBody(req);
      await baileys.sendImage(body.to, body.image, body.caption);
      sendJson(res, 200, { ok: true });
      return;
    }
    if (req.method === "POST" && req.url === "/logout") {
      await baileys.logout();
      sendJson(res, 200, { ok: true });
      return;
    }
    sendJson(res, 404, { error: "not_found" });
  } catch (err) {
    sendJson(res, 200, { ok: false, error: err.message || String(err) });
  }
});

server.listen(PORT, () => {
  console.log(`[whatsapp-web-gateway] listening on :${PORT}`);
});

baileys.connect().catch((err) => {
  console.error("[whatsapp-web-gateway] baglanti baslatilamadi:", err);
});
