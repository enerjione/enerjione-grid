import { useEffect, useMemo, useState, type FormEvent } from "react";

import { ActiveSwitch } from "../../components/ActiveSwitch";
import type { DeviceRow, OutboundTarget } from "../../shared/types";

type Protocol = "rest" | "mqtt" | "iec104";

type Props = {
  targets: OutboundTarget[];
  /** IEC 104 hedefi düzenlerken cihaz başına CA atayabilmek için. */
  devices?: DeviceRow[];
  onCreate: (payload: {
    name: string;
    protocol: Protocol;
    endpoint: string;
    topic?: string | null;
    event_filter: "all" | "telemetry" | "alarm";
    auth_header?: string | null;
    auth_token?: string | null;
    qos: number;
    retain: boolean;
    is_active: boolean;
    listen_host?: string | null;
    listen_port?: number | null;
    iec104_common_address?: number | null;
  }) => Promise<void>;
  onUpdate: (
    targetId: number,
    payload: {
      endpoint?: string;
      topic?: string | null;
      event_filter?: "all" | "telemetry" | "alarm";
      auth_header?: string | null;
      auth_token?: string | null;
      qos?: number;
      retain?: boolean;
      is_active?: boolean;
      listen_host?: string | null;
      listen_port?: number | null;
      iec104_common_address?: number | null;
    }
  ) => Promise<void>;
  onDelete: (targetId: number) => Promise<void>;
  /** IEC 104 hedefi icin point list CSV indir. */
  onDownloadIec104Points?: (targetId: number, suggestedName: string) => Promise<void>;
  /** Tek bir cihazin iec104 CA'sini kaydet. NULL = default'a don. */
  onUpdateDeviceCa?: (deviceCode: string, ca: number | null) => Promise<void>;
};

export function OutboundTargetsPanel({
  targets,
  devices,
  onCreate,
  onUpdate,
  onDelete,
  onDownloadIec104Points,
  onUpdateDeviceCa
}: Props) {
  const [isCreateOpen, setCreateOpen] = useState(false);
  const [editing, setEditing] = useState<OutboundTarget | null>(null);
  const [error, setError] = useState("");
  // Cihaz CA tablosu icin local edit buffer: code -> input string ("" = default)
  const [deviceCaDraft, setDeviceCaDraft] = useState<Record<string, string>>({});
  const [savingDeviceCode, setSavingDeviceCode] = useState<string | null>(null);

  const [name, setName] = useState("");
  const [protocol, setProtocol] = useState<Protocol>("rest");
  const [endpoint, setEndpoint] = useState("");
  const [topic, setTopic] = useState("");
  const [eventFilter, setEventFilter] = useState<"all" | "telemetry" | "alarm">("all");
  const [authHeader, setAuthHeader] = useState("Authorization");
  const [authToken, setAuthToken] = useState("");
  const [qos, setQos] = useState(0);
  const [retain, setRetain] = useState(false);
  const [isActive, setIsActive] = useState(true);
  // IEC 104 alanlari
  const [listenHost, setListenHost] = useState("0.0.0.0");
  const [listenPort, setListenPort] = useState("2404");
  const [iec104Ca, setIec104Ca] = useState("1");

  const resetForm = () => {
    setName("");
    setProtocol("rest");
    setEndpoint("");
    setTopic("");
    setEventFilter("all");
    setAuthHeader("Authorization");
    setAuthToken("");
    setQos(0);
    setRetain(false);
    setIsActive(true);
    setListenHost("0.0.0.0");
    setListenPort("2404");
    setIec104Ca("1");
  };

  const handleCreate = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setError("");
    try {
      const isIec104 = protocol === "iec104";
      await onCreate({
        name,
        protocol,
        // IEC 104'te endpoint anlamsiz; placeholder gonder
        endpoint: isIec104 ? "" : endpoint,
        topic: topic.trim() ? topic.trim() : null,
        event_filter: eventFilter,
        auth_header: !isIec104 && authHeader.trim() ? authHeader.trim() : null,
        auth_token: !isIec104 && authToken.trim() ? authToken.trim() : null,
        qos,
        retain,
        is_active: isActive,
        listen_host: isIec104 ? listenHost.trim() || "0.0.0.0" : null,
        listen_port: isIec104 ? Number(listenPort) || 2404 : null,
        iec104_common_address: isIec104 ? Number(iec104Ca) || 1 : null
      });
      resetForm();
      setCreateOpen(false);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Outbound hedef eklenemedi.");
    }
  };

  const openEdit = (target: OutboundTarget) => {
    setEditing(target);
    setEndpoint(target.endpoint);
    setTopic(target.topic ?? "");
    setEventFilter(target.event_filter);
    setAuthHeader(target.auth_header ?? "Authorization");
    setAuthToken(target.auth_token ?? "");
    setQos(target.qos);
    setRetain(target.retain);
    setIsActive(target.is_active);
    setListenHost(target.listen_host ?? "0.0.0.0");
    setListenPort(target.listen_port !== null && target.listen_port !== undefined ? String(target.listen_port) : "2404");
    setIec104Ca(
      target.iec104_common_address !== null && target.iec104_common_address !== undefined
        ? String(target.iec104_common_address)
        : "1"
    );
  };

  const handleEdit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!editing) return;
    setError("");
    try {
      const isIec104 = editing.protocol === "iec104";
      await onUpdate(editing.id, {
        endpoint: isIec104 ? "" : endpoint,
        topic: topic.trim() ? topic.trim() : null,
        event_filter: eventFilter,
        auth_header: !isIec104 && authHeader.trim() ? authHeader.trim() : null,
        auth_token: !isIec104 && authToken.trim() ? authToken.trim() : null,
        qos,
        retain,
        is_active: isActive,
        listen_host: isIec104 ? listenHost.trim() || "0.0.0.0" : undefined,
        listen_port: isIec104 ? Number(listenPort) || 2404 : undefined,
        iec104_common_address: isIec104 ? Number(iec104Ca) || 1 : undefined
      });
      setEditing(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Outbound hedef güncellenemedi.");
    }
  };

  const handleDownloadCsv = async (target: OutboundTarget) => {
    if (!onDownloadIec104Points) return;
    const safeName = target.name.replace(/[^A-Za-z0-9._-]+/g, "_");
    const ts = new Date().toISOString().replace(/[:.]/g, "-").slice(0, 19);
    await onDownloadIec104Points(target.id, `iec104-points-${safeName}-${ts}.csv`);
  };

  const isCreatingIec104 = protocol === "iec104";
  const isEditingIec104 = editing?.protocol === "iec104";

  // editing degistiginde cihaz CA buffer'ini DB'deki degerlerle senkronize et.
  useEffect(() => {
    if (!editing || editing.protocol !== "iec104") return;
    const draft: Record<string, string> = {};
    for (const d of devices ?? []) {
      const ca = d.iec104CommonAddress;
      draft[d.code] = ca !== null && ca !== undefined ? String(ca) : "";
    }
    setDeviceCaDraft(draft);
  }, [editing, devices]);

  const sortedDevices = useMemo(
    () => [...(devices ?? [])].sort((a, b) => a.code.localeCompare(b.code)),
    [devices]
  );

  const handleSaveDeviceCa = async (deviceCode: string) => {
    if (!onUpdateDeviceCa) return;
    const raw = (deviceCaDraft[deviceCode] ?? "").trim();
    const value = raw === "" ? null : Number(raw);
    if (value !== null && (!Number.isFinite(value) || value < 0 || value > 65534)) {
      setError(`${deviceCode} için CA geçersiz (0-65534 arası bir tam sayı veya boş).`);
      return;
    }
    setError("");
    setSavingDeviceCode(deviceCode);
    try {
      await onUpdateDeviceCa(deviceCode, value);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Cihaz CA kaydedilemedi.");
    } finally {
      setSavingDeviceCode(null);
    }
  };

  return (
    <section className="tab-panel">
      <div className="panel-head">
        <h3>Outbound Hedefleri</h3>
        <button className="add-user-btn" onClick={() => setCreateOpen(true)}>
          + Hedef Ekle
        </button>
      </div>
      {error ? <p className="error-text">{error}</p> : null}

      {(isCreateOpen || editing) && (
        <div className="settings-modal-backdrop">
          <form className="settings-modal" onSubmit={editing ? handleEdit : handleCreate}>
            <h3>{editing ? "Hedef Düzenle" : "Yeni Outbound Hedef"}</h3>
            {!editing ? (
              <>
                <label>
                  Hedef Adı
                  <input value={name} onChange={(event) => setName(event.target.value)} required />
                </label>
                <label>
                  Protokol
                  <select value={protocol} onChange={(event) => setProtocol(event.target.value as Protocol)}>
                    <option value="rest">REST</option>
                    <option value="mqtt">MQTT</option>
                    <option value="iec104">IEC 60870-5-104</option>
                  </select>
                </label>
              </>
            ) : (
              <>
                <label>
                  Hedef Adı
                  <input value={editing.name} readOnly disabled />
                </label>
                <label>
                  Protokol
                  <input value={editing.protocol.toUpperCase()} readOnly disabled />
                </label>
              </>
            )}

            {(isCreatingIec104 || isEditingIec104) ? (
              <>
                <label>
                  Listen Host
                  <input
                    value={listenHost}
                    onChange={(event) => setListenHost(event.target.value)}
                    placeholder="0.0.0.0"
                  />
                </label>
                <label>
                  Listen Port
                  <input
                    type="number"
                    min={1}
                    max={65535}
                    value={listenPort}
                    onChange={(event) => setListenPort(event.target.value)}
                    placeholder="2404"
                  />
                </label>
                <label>
                  Default ASDU Common Address
                  <input
                    type="number"
                    min={0}
                    max={65534}
                    value={iec104Ca}
                    onChange={(event) => setIec104Ca(event.target.value)}
                  />
                </label>
                <p className="helper-text">
                  Cihazların kendi <code>iec104_common_address</code> alanı NULL olanlar
                  bu default CA'yi kullanır. Aynı TCP yayınında farklı CA'lı ASDU'lar
                  birlikte yayılır.
                </p>
                {isEditingIec104 && onUpdateDeviceCa ? (
                  <div className="iec104-device-ca-section">
                    <h4 className="iec104-device-ca-title">Cihaz Başına ASDU CA</h4>
                    <p className="helper-text">
                      Boş bırakılan cihazlar yukarıdaki default CA'yi kullanır.
                      Her satırda Kaydet ile o cihaz için yeniler.
                    </p>
                    <div className="iec104-device-ca-table-wrap">
                      <table className="values-table iec104-device-ca-table">
                        <thead>
                          <tr>
                            <th>Cihaz</th>
                            <th>Kod</th>
                            <th style={{ width: 120 }}>ASDU CA</th>
                            <th style={{ width: 90 }}>İşlem</th>
                          </tr>
                        </thead>
                        <tbody>
                          {sortedDevices.length === 0 ? (
                            <tr>
                              <td colSpan={4} className="helper-text">Cihaz bulunamadı.</td>
                            </tr>
                          ) : null}
                          {sortedDevices.map((d) => {
                            const draftValue = deviceCaDraft[d.code] ?? "";
                            const dbValue = d.iec104CommonAddress ?? null;
                            const dbStr = dbValue !== null ? String(dbValue) : "";
                            const dirty = draftValue !== dbStr;
                            const isSaving = savingDeviceCode === d.code;
                            return (
                              <tr key={d.code}>
                                <td>{d.name}</td>
                                <td><code>{d.code}</code></td>
                                <td>
                                  <input
                                    type="number"
                                    min={0}
                                    max={65534}
                                    value={draftValue}
                                    placeholder="(default)"
                                    onChange={(event) =>
                                      setDeviceCaDraft((prev) => ({
                                        ...prev,
                                        [d.code]: event.target.value
                                      }))
                                    }
                                    disabled={isSaving}
                                  />
                                </td>
                                <td>
                                  <button
                                    type="button"
                                    className="secondary-btn action-btn"
                                    disabled={!dirty || isSaving}
                                    onClick={() => void handleSaveDeviceCa(d.code)}
                                  >
                                    {isSaving ? "..." : "Kaydet"}
                                  </button>
                                </td>
                              </tr>
                            );
                          })}
                        </tbody>
                      </table>
                    </div>
                  </div>
                ) : null}
              </>
            ) : (
              <>
                <label>
                  Endpoint
                  <input value={endpoint} onChange={(event) => setEndpoint(event.target.value)} required />
                </label>
                <label>
                  Topic (MQTT)
                  <input value={topic} onChange={(event) => setTopic(event.target.value)} placeholder="horstman/events" />
                </label>
                <label>
                  Event Filtresi
                  <select value={eventFilter} onChange={(event) => setEventFilter(event.target.value as "all" | "telemetry" | "alarm")}>
                    <option value="all">Tümü</option>
                    <option value="telemetry">Telemetry</option>
                    <option value="alarm">Alarm</option>
                  </select>
                </label>
                <label>
                  Auth Header
                  <input value={authHeader} onChange={(event) => setAuthHeader(event.target.value)} placeholder="Authorization" />
                </label>
                <label>
                  Auth Token
                  <input value={authToken} onChange={(event) => setAuthToken(event.target.value)} />
                </label>
                <label>
                  MQTT QoS
                  <input
                    type="number"
                    min={0}
                    max={2}
                    value={qos}
                    onChange={(event) => setQos(Number(event.target.value) || 0)}
                  />
                </label>
                <label className="notify-option">
                  <input type="checkbox" checked={retain} onChange={(event) => setRetain(event.target.checked)} />
                  MQTT Retain
                </label>
              </>
            )}
            <ActiveSwitch checked={isActive} onChange={setIsActive} />
            <div className="settings-actions">
              <button type="button" onClick={() => (editing ? setEditing(null) : setCreateOpen(false))}>
                Vazgeç
              </button>
              <button type="submit">{editing ? "Güncelle" : "Kaydet"}</button>
            </div>
          </form>
        </div>
      )}

      <table className="values-table user-table">
        <thead>
          <tr>
            <th>Ad</th>
            <th>Protokol</th>
            <th>Endpoint / Listen</th>
            <th>Filtre</th>
            <th>Aktif</th>
            <th className="actions-header">İşlem</th>
          </tr>
        </thead>
        <tbody>
          {targets.map((item) => {
            const isIec = item.protocol === "iec104";
            const endpointDisplay = isIec
              ? `${item.listen_host ?? "0.0.0.0"}:${item.listen_port ?? 2404} · CA=${item.iec104_common_address ?? 1}`
              : item.endpoint;
            return (
              <tr key={item.id}>
                <td>{item.name}</td>
                <td>{item.protocol.toUpperCase()}</td>
                <td>{endpointDisplay}</td>
                <td>{item.event_filter}</td>
                <td>{item.is_active ? "Evet" : "Hayır"}</td>
                <td className="actions-cell">
                  {isIec && onDownloadIec104Points ? (
                    <button
                      type="button"
                      className="secondary-btn action-btn"
                      title="Bu hedefin point listesini SCADA'ya yüklemek için CSV indir"
                      onClick={() => void handleDownloadCsv(item)}
                    >
                      Point List CSV
                    </button>
                  ) : null}
                  <button className="edit-btn action-btn" onClick={() => openEdit(item)}>
                    Düzenle
                  </button>
                  <button
                    className="danger-btn action-btn"
                    onClick={() => {
                      if (window.confirm(`${item.name} hedefi silinsin mi?`)) {
                        void onDelete(item.id).catch((err: unknown) => {
                          setError(err instanceof Error ? err.message : "Outbound hedef silinemedi.");
                        });
                      }
                    }}
                  >
                    Sil
                  </button>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </section>
  );
}
