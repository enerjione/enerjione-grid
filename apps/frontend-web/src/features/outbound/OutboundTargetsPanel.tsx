import { useState, type FormEvent } from "react";

import { ActiveSwitch } from "../../components/ActiveSwitch";
import type { OutboundTarget } from "../../shared/types";

type Props = {
  targets: OutboundTarget[];
  onCreate: (payload: {
    name: string;
    protocol: "rest" | "mqtt";
    endpoint: string;
    topic?: string | null;
    event_filter: "all" | "telemetry" | "alarm";
    auth_header?: string | null;
    auth_token?: string | null;
    qos: number;
    retain: boolean;
    is_active: boolean;
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
    }
  ) => Promise<void>;
  onDelete: (targetId: number) => Promise<void>;
};

export function OutboundTargetsPanel({ targets, onCreate, onUpdate, onDelete }: Props) {
  const [isCreateOpen, setCreateOpen] = useState(false);
  const [editing, setEditing] = useState<OutboundTarget | null>(null);
  const [error, setError] = useState("");

  const [name, setName] = useState("");
  const [protocol, setProtocol] = useState<"rest" | "mqtt">("rest");
  const [endpoint, setEndpoint] = useState("");
  const [topic, setTopic] = useState("");
  const [eventFilter, setEventFilter] = useState<"all" | "telemetry" | "alarm">("all");
  const [authHeader, setAuthHeader] = useState("Authorization");
  const [authToken, setAuthToken] = useState("");
  const [qos, setQos] = useState(0);
  const [retain, setRetain] = useState(false);
  const [isActive, setIsActive] = useState(true);

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
  };

  const handleCreate = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setError("");
    try {
      await onCreate({
        name,
        protocol,
        endpoint,
        topic: topic.trim() ? topic.trim() : null,
        event_filter: eventFilter,
        auth_header: authHeader.trim() ? authHeader.trim() : null,
        auth_token: authToken.trim() ? authToken.trim() : null,
        qos,
        retain,
        is_active: isActive
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
  };

  const handleEdit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!editing) return;
    setError("");
    try {
      await onUpdate(editing.id, {
        endpoint,
        topic: topic.trim() ? topic.trim() : null,
        event_filter: eventFilter,
        auth_header: authHeader.trim() ? authHeader.trim() : null,
        auth_token: authToken.trim() ? authToken.trim() : null,
        qos,
        retain,
        is_active: isActive
      });
      setEditing(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Outbound hedef güncellenemedi.");
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
                  <select value={protocol} onChange={(event) => setProtocol(event.target.value as "rest" | "mqtt")}>
                    <option value="rest">REST</option>
                    <option value="mqtt">MQTT</option>
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
            <th>Endpoint</th>
            <th>Filtre</th>
            <th>Aktif</th>
            <th className="actions-header">İşlem</th>
          </tr>
        </thead>
        <tbody>
          {targets.map((item) => (
            <tr key={item.id}>
              <td>{item.name}</td>
              <td>{item.protocol.toUpperCase()}</td>
              <td>{item.endpoint}</td>
              <td>{item.event_filter}</td>
              <td>{item.is_active ? "Evet" : "Hayır"}</td>
              <td className="actions-cell">
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
          ))}
        </tbody>
      </table>
    </section>
  );
}
