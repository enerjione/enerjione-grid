import { useState, type FormEvent } from "react";

import type { Gateway } from "../../shared/types";

type DownloadParams = {
  backendUrl: string;
  hostPort: number;
  fmt: "compose" | "env";
};

type Props = {
  gateways: Gateway[];
  onCreate: (payload: {
    code: string;
    name: string;
    host: string;
    listen_port: number;
    upstream_url: string;
    batch_interval_sec: number;
    max_devices: number;
    device_code_prefix?: string | null;
    token: string;
    is_active: boolean;
    control_host: string;
    control_port: number;
    initiating_port_count: number;
  }) => Promise<void>;
  onToggleActive: (gatewayCode: string, isActive: boolean) => Promise<void>;
  onDelete: (gatewayCode: string) => Promise<void>;
  onDownloadCompose: (gatewayCode: string, params: DownloadParams) => Promise<void>;
};

function defaultBackendUrl(): string {
  if (typeof window === "undefined") return "";
  const origin = window.location.origin.replace(/\/$/, "");
  return `${origin}/api/v1`;
}

export function GatewayManagementPanel({
  gateways,
  onCreate,
  onToggleActive,
  onDelete,
  onDownloadCompose
}: Props) {
  const [showCreateModal, setShowCreateModal] = useState(false);
  const [code, setCode] = useState("");
  const [name, setName] = useState("");
  const [host, setHost] = useState("");
  const [listenPort, setListenPort] = useState("20000");
  const [upstreamUrl, setUpstreamUrl] = useState("/api/v1/telemetry/gateway/{gateway_code}");
  const [batchIntervalSec, setBatchIntervalSec] = useState("5");
  const [maxDevices, setMaxDevices] = useState("200");
  const [deviceCodePrefix, setDeviceCodePrefix] = useState("");
  const [token, setToken] = useState("");
  const [controlHost, setControlHost] = useState("127.0.0.1");
  const [controlPort, setControlPort] = useState("8020");
  // Initiating cihaz portu sayisi (= max initiating cihaz). Default 0 cunku
  // listening modda gateway cihaza outbound TCP client olarak baglanir, port
  // acmaz. Initiating cihaz (4G/SIM, gateway'e dinleyen) eklenecekse artirilir.
  const [initiatingPortCount, setInitiatingPortCount] = useState("0");
  const [error, setError] = useState("");

  const [downloadFor, setDownloadFor] = useState<string | null>(null);
  const [downloadBackendUrl, setDownloadBackendUrl] = useState(defaultBackendUrl());
  const [downloadHostPort, setDownloadHostPort] = useState("8020");
  const [downloadFmt, setDownloadFmt] = useState<"compose" | "env">("compose");
  const [downloadError, setDownloadError] = useState("");
  const [downloadBusy, setDownloadBusy] = useState(false);

  const openDownloadModal = (gatewayCode: string) => {
    setDownloadFor(gatewayCode);
    setDownloadBackendUrl(defaultBackendUrl());
    setDownloadHostPort("8020");
    setDownloadFmt("compose");
    setDownloadError("");
  };

  const handleDownloadSubmit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!downloadFor) return;
    setDownloadError("");
    setDownloadBusy(true);
    try {
      await onDownloadCompose(downloadFor, {
        backendUrl: downloadBackendUrl.trim(),
        hostPort: Number(downloadHostPort) || 8020,
        fmt: downloadFmt
      });
      setDownloadFor(null);
    } catch (err) {
      setDownloadError(err instanceof Error ? err.message : "Dosya indirilemedi.");
    } finally {
      setDownloadBusy(false);
    }
  };

  const handleSubmit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setError("");
    const createdCode = code;
    try {
      await onCreate({
        code,
        name,
        host,
        listen_port: Number(listenPort),
        upstream_url: upstreamUrl,
        batch_interval_sec: Number(batchIntervalSec),
        max_devices: Number(maxDevices),
        device_code_prefix: deviceCodePrefix.trim() || null,
        token,
        is_active: true,
        control_host: controlHost.trim() || "127.0.0.1",
        control_port: Number(controlPort) || 0,
        initiating_port_count: Math.max(0, Math.min(1000, Number(initiatingPortCount) || 0))
      });
      setShowCreateModal(false);
      setCode("");
      setName("");
      setHost("");
      setListenPort("20000");
      setUpstreamUrl("/api/v1/telemetry/gateway/{gateway_code}");
      setBatchIntervalSec("5");
      setMaxDevices("200");
      setDeviceCodePrefix("");
      setToken("");
      setControlHost("127.0.0.1");
      setControlPort("8020");
      setInitiatingPortCount("0");
      openDownloadModal(createdCode);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Gateway oluşturulamadı.");
    }
  };

  const generateToken = () => {
    const len = 48;
    const chars = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789";
    const arr = new Uint32Array(len);
    (window.crypto || (window as unknown as { msCrypto: Crypto }).msCrypto).getRandomValues(arr);
    let out = "";
    for (let i = 0; i < len; i += 1) out += chars.charAt(arr[i] % chars.length);
    setToken(out);
  };

  return (
    <section className="tab-panel">
      <h3>Gateway Yönetimi</h3>
      <p className="helper-text">
        Gateway&apos;ler DNP3 verisini toplayıp HTTPS ile tek mesajda çatı yazılıma iletir. Yük dağıtımı için cihaz kapsamı
        ve kapasite limitleri bu ekrandan yönetilir.
      </p>

      <div className="users-panel-toolbar">
        <button className="primary-btn" onClick={() => setShowCreateModal(true)}>
          Gateway Ekle
        </button>
      </div>

      <div className="gateway-endpoint">
        DNP3 connector ingest endpoint: <code>/api/v1/telemetry/gateway/{"{gateway_code}"}</code> (Header:{" "}
        <code>X-Gateway-Token</code>)
        <br />
        Kontrol paneli bu tablodaki <code>Kontrol Adresi</code> alanını kullanarak uzaktaki collector servislerini
        izler ve <em>Aktifleştir/Pasifleştir</em> komutlarını yayınlar.
      </div>

      <table className="values-table">
        <thead>
          <tr>
            <th scope="col">Kod</th>
            <th scope="col">Ad</th>
            <th scope="col">DNP3 Host:Port</th>
            <th scope="col">Kontrol Adresi</th>
            <th scope="col">Kapsam</th>
            <th scope="col">Maks. Cihaz</th>
            <th scope="col">Batch (sn)</th>
            <th scope="col">Durum</th>
            <th scope="col">Son Görülme</th>
            <th scope="col">İşlem</th>
          </tr>
        </thead>
        <tbody>
          {gateways.map((gateway) => (
            <tr key={gateway.id}>
              <td>{gateway.code}</td>
              <td>{gateway.name}</td>
              <td>
                {gateway.host}:{gateway.listen_port}
              </td>
              <td>
                {gateway.control_host || "127.0.0.1"}
                {gateway.control_port ? `:${gateway.control_port}` : " (—)"}
              </td>
              <td>{gateway.device_code_prefix ? `${gateway.device_code_prefix}*` : "Tümü"}</td>
              <td>{gateway.max_devices}</td>
              <td>{gateway.batch_interval_sec}</td>
              <td>{gateway.is_active ? "Aktif" : "Pasif"}</td>
              <td>{gateway.last_seen_at ? new Date(gateway.last_seen_at).toLocaleString(undefined) : "-"}</td>
              <td className="actions-cell">
                <button
                  className="secondary-btn action-btn"
                  onClick={() => openDownloadModal(gateway.code)}
                  title="Bu gateway icin docker-compose YAML / .env dosyasi indir"
                >
                  Compose İndir
                </button>
                <button
                  className="secondary-btn action-btn"
                  onClick={() => void onToggleActive(gateway.code, !gateway.is_active)}
                >
                  {gateway.is_active ? "Pasifleştir" : "Aktifleştir"}
                </button>
                <button className="danger-btn action-btn" onClick={() => void onDelete(gateway.code)}>
                  Sil
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>

      {showCreateModal ? (
        <div className="settings-modal-backdrop">
          <form className="settings-modal" onSubmit={handleSubmit}>
            <h3>Gateway Ekle</h3>
            <label>
              Kod
              <input placeholder="gw-01" value={code} onChange={(event) => setCode(event.target.value)} required />
            </label>
            <label>
              Gateway Adı
              <input value={name} onChange={(event) => setName(event.target.value)} required />
            </label>
            <label>
              Host
              <input placeholder="10.10.10.20" value={host} onChange={(event) => setHost(event.target.value)} required />
            </label>
            <label>
              Port
              <input
                type="number"
                min={1}
                max={65535}
                value={listenPort}
                onChange={(event) => setListenPort(event.target.value)}
                required
              />
            </label>
            <label>
              Çatı API URL
              <input value={upstreamUrl} onChange={(event) => setUpstreamUrl(event.target.value)} required />
            </label>
            <label>
              Batch Aralığı (sn)
              <input
                type="number"
                min={1}
                max={3600}
                value={batchIntervalSec}
                onChange={(event) => setBatchIntervalSec(event.target.value)}
                required
              />
            </label>
            <label>
              Maksimum Cihaz Sayısı
              <input
                type="number"
                min={1}
                max={2000}
                value={maxDevices}
                onChange={(event) => setMaxDevices(event.target.value)}
                required
              />
            </label>
            <label>
              Cihaz Kod Ön Eki (opsiyonel)
              <input
                placeholder="örn: ist-1-"
                value={deviceCodePrefix}
                onChange={(event) => setDeviceCodePrefix(event.target.value)}
              />
            </label>
            <label>
              Gateway Token
              <div style={{ display: "flex", gap: "0.5rem" }}>
                <input
                  style={{ flex: 1 }}
                  value={token}
                  onChange={(event) => setToken(event.target.value)}
                  required
                  minLength={16}
                  placeholder="En az 16 karakter — 'Üret' butonu ile rastgele alabilirsiniz"
                />
                <button type="button" className="secondary-btn" onClick={generateToken}>
                  Üret
                </button>
              </div>
            </label>
            <label>
              Kontrol Host (Uzak Makina)
              <input
                placeholder="10.10.10.30"
                value={controlHost}
                onChange={(event) => setControlHost(event.target.value)}
              />
            </label>
            <label>
              Kontrol Port (collector health/port)
              <input
                type="number"
                min={0}
                max={65535}
                value={controlPort}
                onChange={(event) => setControlPort(event.target.value)}
              />
            </label>
            <label>
              Beklenen Initiating Cihaz Sayısı (4G/SIM cihazlar)
              <input
                type="number"
                min={0}
                max={1000}
                value={initiatingPortCount}
                onChange={(event) => setInitiatingPortCount(event.target.value)}
                placeholder="0"
              />
              <small className="helper-text" style={{ display: "block", marginTop: 4 }}>
                Cihazlara <b>siz bağlanıyorsanız</b> (Listening) <code>0</code> bırakın — port açılmaz.
                Cihaz size dışarıdan bağlanıyorsa (Initiating, 4G/SIM) cihaz sayısı kadar girin.
              </small>
            </label>
            {error ? <p className="error-text">{error}</p> : null}
            <div className="modal-actions">
              <button type="button" className="secondary-btn" onClick={() => setShowCreateModal(false)}>
                İptal
              </button>
              <button type="submit" className="primary-btn">
                Oluştur
              </button>
            </div>
          </form>
        </div>
      ) : null}

      {downloadFor ? (
        <div className="settings-modal-backdrop">
          <form className="settings-modal" onSubmit={handleDownloadSubmit}>
            <h3>Docker Compose İndir — {downloadFor}</h3>
            <p className="helper-text">
              Bu dosyayı sunucuya kopyalayıp aşağıdaki komutla başlatın. Docker hem Linux hem
              Windows&apos;ta (Docker Desktop) aynı şekilde çalışır:
              <br />
              <code>docker compose -f e1-gw-{downloadFor.toLowerCase()}.yml up -d</code>
            </p>
            <label>
              Çatı Yazılım Adresi (Backend URL)
              <input
                value={downloadBackendUrl}
                onChange={(event) => setDownloadBackendUrl(event.target.value)}
                placeholder="https://hsl.musteri.com/api/v1"
                required
              />
              <small className="helper-text">
                Gateway başka bir bilgisayarda çalışacaksa buraya çatı yazılımın dış adresini girin
                (örn. <code>http://192.168.1.50:8000/api/v1</code>). Aynı makinada çalışacaksa
                varsayılan değer (<code>{defaultBackendUrl()}</code>) yeterlidir. RabbitMQ adresi
                otomatik olarak aynı host&apos;tan türetilir.
              </small>
            </label>
            <label>
              Host Sağlık Portu
              <input
                type="number"
                min={1}
                max={65535}
                value={downloadHostPort}
                onChange={(event) => setDownloadHostPort(event.target.value)}
                required
              />
              <small className="helper-text">
                Aynı sunucuda birden fazla gateway varsa her biri için farklı port (8020, 8021, ...)
              </small>
            </label>
            <label>
              Format
              <select
                value={downloadFmt}
                onChange={(event) => setDownloadFmt(event.target.value as "compose" | "env")}
              >
                <option value="compose">docker-compose YAML (önerilen)</option>
                <option value="env">.env (Docker olmadan Python ile çalıştırma)</option>
              </select>
            </label>
            {downloadError ? <p className="error-text">{downloadError}</p> : null}
            <div className="modal-actions">
              <button
                type="button"
                className="secondary-btn"
                onClick={() => setDownloadFor(null)}
                disabled={downloadBusy}
              >
                Kapat
              </button>
              <button type="submit" className="primary-btn" disabled={downloadBusy}>
                {downloadBusy ? "İndiriliyor..." : "İndir"}
              </button>
            </div>
          </form>
        </div>
      ) : null}
    </section>
  );
}
