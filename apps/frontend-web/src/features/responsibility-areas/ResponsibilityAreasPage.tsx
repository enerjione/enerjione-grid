import { useEffect, useMemo, useState, type FormEvent } from "react";

import { ActiveSwitch } from "../../components/ActiveSwitch";
import type {
  DeviceRow,
  Line,
  Region,
  ResponsibilityAreaDetail,
  ResponsibilityAreaRow,
  UserRead
} from "../../shared/types";

type Props = {
  role: "operator" | "engineer" | "installer";
  areas: ResponsibilityAreaRow[];
  users: UserRead[];
  devices: DeviceRow[];
  regions: Region[];
  lines: Line[];
  loading?: boolean;
  error?: string;
  onLoadDetail: (areaId: number) => Promise<ResponsibilityAreaDetail>;
  onCreate: (payload: { code: string; name: string; description?: string | null }) => Promise<void>;
  onUpdate: (areaId: number, payload: { name?: string; description?: string | null; is_active?: boolean }) => Promise<void>;
  onDelete: (areaId: number) => Promise<void>;
  onAddUser: (areaId: number, userId: number) => Promise<void>;
  onRemoveUser: (areaId: number, userId: number) => Promise<void>;
  onAddDevice: (areaId: number, deviceId: number) => Promise<void>;
  onRemoveDevice: (areaId: number, deviceId: number) => Promise<void>;
  onAddRegion: (areaId: number, regionId: number) => Promise<void>;
  onRemoveRegion: (areaId: number, regionId: number) => Promise<void>;
  onAddLine: (areaId: number, lineId: number) => Promise<void>;
  onRemoveLine: (areaId: number, lineId: number) => Promise<void>;
};

export function ResponsibilityAreasPage({
  role,
  areas,
  users,
  devices,
  regions,
  lines,
  loading,
  error,
  onLoadDetail,
  onCreate,
  onUpdate,
  onDelete,
  onAddUser,
  onRemoveUser,
  onAddDevice,
  onRemoveDevice,
  onAddRegion,
  onRemoveRegion,
  onAddLine,
  onRemoveLine
}: Props) {
  const canEdit = role === "engineer" || role === "installer";
  const [selectedAreaId, setSelectedAreaId] = useState<number | null>(null);
  const [detail, setDetail] = useState<ResponsibilityAreaDetail | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [localError, setLocalError] = useState("");
  const [showCreateModal, setShowCreateModal] = useState(false);
  const [createCode, setCreateCode] = useState("");
  const [createName, setCreateName] = useState("");
  const [createDescription, setCreateDescription] = useState("");
  const [editName, setEditName] = useState("");
  const [editDescription, setEditDescription] = useState("");
  const [editIsActive, setEditIsActive] = useState(true);
  const [searchUser, setSearchUser] = useState("");
  const [searchDevice, setSearchDevice] = useState("");
  const [busy, setBusy] = useState(false);
  const [activeTab, setActiveTab] = useState<"general" | "users" | "regions" | "lines" | "devices">("general");

  useEffect(() => {
    if (selectedAreaId === null && areas.length > 0) {
      setSelectedAreaId(areas[0].id);
    }
    if (selectedAreaId !== null && !areas.some((a) => a.id === selectedAreaId)) {
      setSelectedAreaId(areas[0]?.id ?? null);
    }
  }, [areas, selectedAreaId]);

  const reloadDetail = async (areaId: number) => {
    setDetailLoading(true);
    setLocalError("");
    try {
      const fetched = await onLoadDetail(areaId);
      setDetail(fetched);
      setEditName(fetched.name);
      setEditDescription(fetched.description ?? "");
      setEditIsActive(fetched.is_active);
    } catch (err) {
      setLocalError(err instanceof Error ? err.message : "Alan detayı alınamadı.");
    } finally {
      setDetailLoading(false);
    }
  };

  useEffect(() => {
    if (selectedAreaId === null) {
      setDetail(null);
      return;
    }
    void reloadDetail(selectedAreaId);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedAreaId]);

  const userIdsInArea = useMemo(
    () => new Set(detail?.users.map((u) => u.id) ?? []),
    [detail]
  );
  const regionIdsInArea = useMemo(
    () => new Set(detail?.regions?.map((r) => r.id) ?? []),
    [detail]
  );
  const lineIdsInArea = useMemo(
    () => new Set(detail?.lines?.map((l) => l.id) ?? []),
    [detail]
  );
  const deviceIdsInArea = useMemo(
    () => new Set(detail?.devices.map((d) => d.id) ?? []),
    [detail]
  );

  const availableUsers = useMemo(() => {
    const q = searchUser.trim().toLowerCase();
    // Sorumluluk alanlarına yalnızca operator hesapları atanabilir; mühendis ve
    // kurulumcu zaten tüm cihazlara erişir, alana atamak anlam taşımaz.
    return users.filter(
      (u) =>
        u.role === "operator" &&
        !userIdsInArea.has(u.id) &&
        (!q ||
          u.username.toLowerCase().includes(q) ||
          u.full_name.toLowerCase().includes(q) ||
          u.email.toLowerCase().includes(q))
    );
  }, [users, userIdsInArea, searchUser]);

  const availableDevices = useMemo(() => {
    const q = searchDevice.trim().toLowerCase();
    return devices.filter(
      (d) =>
        !deviceIdsInArea.has(d.id) &&
        (!q ||
          d.code.toLowerCase().includes(q) ||
          d.name.toLowerCase().includes(q))
    );
  }, [devices, deviceIdsInArea, searchDevice]);

  const handleCreate = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setBusy(true);
    setLocalError("");
    try {
      await onCreate({
        code: createCode.trim(),
        name: createName.trim(),
        description: createDescription.trim() || null
      });
      setCreateCode("");
      setCreateName("");
      setCreateDescription("");
      setShowCreateModal(false);
    } catch (err) {
      setLocalError(err instanceof Error ? err.message : "Sorumluluk alanı oluşturulamadı.");
    } finally {
      setBusy(false);
    }
  };

  const handleSaveEdits = async () => {
    if (!detail) return;
    setBusy(true);
    setLocalError("");
    try {
      await onUpdate(detail.id, {
        name: editName.trim(),
        description: editDescription.trim() || null,
        is_active: editIsActive
      });
      await reloadDetail(detail.id);
    } catch (err) {
      setLocalError(err instanceof Error ? err.message : "Sorumluluk alanı güncellenemedi.");
    } finally {
      setBusy(false);
    }
  };

  const handleDelete = async () => {
    if (!detail) return;
    if (!window.confirm(`"${detail.name}" sorumluluk alanı silinsin mi?`)) return;
    setBusy(true);
    setLocalError("");
    try {
      await onDelete(detail.id);
      setSelectedAreaId(null);
      setDetail(null);
    } catch (err) {
      setLocalError(err instanceof Error ? err.message : "Sorumluluk alanı silinemedi.");
    } finally {
      setBusy(false);
    }
  };

  const handleAddUser = async (userId: number) => {
    if (!detail) return;
    setBusy(true);
    setLocalError("");
    try {
      await onAddUser(detail.id, userId);
      await reloadDetail(detail.id);
    } catch (err) {
      setLocalError(err instanceof Error ? err.message : "Kullanıcı eklenemedi.");
    } finally {
      setBusy(false);
    }
  };

  const handleRemoveUser = async (userId: number) => {
    if (!detail) return;
    setBusy(true);
    setLocalError("");
    try {
      await onRemoveUser(detail.id, userId);
      await reloadDetail(detail.id);
    } catch (err) {
      setLocalError(err instanceof Error ? err.message : "Kullanıcı çıkarılamadı.");
    } finally {
      setBusy(false);
    }
  };

  const handleAddDevice = async (deviceId: number) => {
    if (!detail) return;
    setBusy(true);
    setLocalError("");
    try {
      await onAddDevice(detail.id, deviceId);
      await reloadDetail(detail.id);
    } catch (err) {
      setLocalError(err instanceof Error ? err.message : "Cihaz eklenemedi.");
    } finally {
      setBusy(false);
    }
  };

  const handleRemoveDevice = async (deviceId: number) => {
    if (!detail) return;
    setBusy(true);
    setLocalError("");
    try {
      await onRemoveDevice(detail.id, deviceId);
      await reloadDetail(detail.id);
    } catch (err) {
      setLocalError(err instanceof Error ? err.message : "Cihaz çıkarılamadı.");
    } finally {
      setBusy(false);
    }
  };

  const handleAddRegion = async (regionId: number) => {
    if (!detail) return;
    setBusy(true);
    setLocalError("");
    try {
      await onAddRegion(detail.id, regionId);
      await reloadDetail(detail.id);
    } catch (err) {
      setLocalError(err instanceof Error ? err.message : "Bölge eklenemedi.");
    } finally {
      setBusy(false);
    }
  };

  const handleRemoveRegion = async (regionId: number) => {
    if (!detail) return;
    setBusy(true);
    setLocalError("");
    try {
      await onRemoveRegion(detail.id, regionId);
      await reloadDetail(detail.id);
    } catch (err) {
      setLocalError(err instanceof Error ? err.message : "Bölge çıkarılamadı.");
    } finally {
      setBusy(false);
    }
  };

  const handleAddLine = async (lineId: number) => {
    if (!detail) return;
    setBusy(true);
    setLocalError("");
    try {
      await onAddLine(detail.id, lineId);
      await reloadDetail(detail.id);
    } catch (err) {
      setLocalError(err instanceof Error ? err.message : "Hat eklenemedi.");
    } finally {
      setBusy(false);
    }
  };

  const handleRemoveLine = async (lineId: number) => {
    if (!detail) return;
    setBusy(true);
    setLocalError("");
    try {
      await onRemoveLine(detail.id, lineId);
      await reloadDetail(detail.id);
    } catch (err) {
      setLocalError(err instanceof Error ? err.message : "Hat çıkarılamadı.");
    } finally {
      setBusy(false);
    }
  };

  const [searchRegion, setSearchRegion] = useState("");
  const [searchLine, setSearchLine] = useState("");
  const availableRegions = useMemo(() => {
    const q = searchRegion.trim().toLowerCase();
    return regions.filter((r) => {
      if (regionIdsInArea.has(r.id)) return false;
      if (!q) return true;
      return r.name.toLowerCase().includes(q) || r.code.toLowerCase().includes(q);
    });
  }, [regions, regionIdsInArea, searchRegion]);
  const availableLines = useMemo(() => {
    const q = searchLine.trim().toLowerCase();
    return lines.filter((l) => {
      if (lineIdsInArea.has(l.id)) return false;
      if (!q) return true;
      return l.name.toLowerCase().includes(q) || l.code.toLowerCase().includes(q);
    });
  }, [lines, lineIdsInArea, searchLine]);

  return (
    <section className="responsibility-page">
      <div className="responsibility-layout">
        {/* Sol: alanlar listesi */}
        <aside className="responsibility-sidebar">
          <div className="responsibility-sidebar-header">
            <h3>Sorumluluk Alanları</h3>
            {canEdit ? (
              <button
                type="button"
                className="primary-btn responsibility-new-btn"
                onClick={() => setShowCreateModal(true)}
              >
                + Yeni
              </button>
            ) : null}
          </div>
          {loading ? <p className="helper-text">Yükleniyor...</p> : null}
          {error ? <p className="error-text">{error}</p> : null}
          <ul className="responsibility-list">
            {areas.map((area) => (
              <li
                key={area.id}
                className={`responsibility-list-item ${selectedAreaId === area.id ? "active" : ""} ${area.is_active ? "" : "inactive"}`}
                onClick={() => setSelectedAreaId(area.id)}
              >
                <div className="responsibility-list-top">
                  <span className="responsibility-list-name">{area.name}</span>
                  {!area.is_active ? (
                    <span className="responsibility-pill responsibility-pill--off">Pasif</span>
                  ) : null}
                </div>
                <div className="responsibility-list-code">{area.code}</div>
                <div className="responsibility-list-stats">
                  <span className="responsibility-stat">
                    <span className="material-symbols-outlined">group</span>
                    {area.user_count}
                  </span>
                  <span className="responsibility-stat">
                    <span className="material-symbols-outlined">devices</span>
                    {area.device_count}
                  </span>
                </div>
              </li>
            ))}
            {areas.length === 0 && !loading ? (
              <li className="responsibility-empty-list">
                <p>Henüz sorumluluk alanı yok.</p>
                {canEdit ? <p className="helper-text">Sağdaki "+ Yeni" ile bir alan oluşturun.</p> : null}
              </li>
            ) : null}
          </ul>
        </aside>

        {/* Sağ: detay paneli */}
        <main className="responsibility-detail">
          {detailLoading ? (
            <div className="panel-loading-overlay" aria-live="polite">
              <span className="panel-loading-spinner" aria-hidden="true" />
              <span>Yükleniyor…</span>
            </div>
          ) : null}
          {localError ? <p className="error-text">{localError}</p> : null}
          {!detail && !detailLoading ? (
            <div className="responsibility-empty-detail">
              <span className="material-symbols-outlined responsibility-empty-icon">folder_open</span>
              <h3>Bir alan seçin</h3>
              <p className="helper-text">
                Soldan bir sorumluluk alanı seçin ya da yeni bir alan oluşturun. Seçilen alana
                kullanıcı ve cihaz ekleyebilirsiniz.
              </p>
            </div>
          ) : null}
          {detail ? (
            <>
              <header className="responsibility-detail-header">
                <div className="responsibility-detail-titlebar">
                  <div className="responsibility-detail-title">
                    <h2>{detail.name || "Yeni Alan"}</h2>
                  </div>
                  {canEdit ? (
                    <div className="responsibility-detail-actions">
                      <ActiveSwitch
                        checked={editIsActive}
                        onChange={setEditIsActive}
                        disabled={busy}
                        title="Pasif yapıldığında bu alan listelerde görünmeye devam eder; yeni atamalara kapatılır."
                      />
                      <button type="button" className="primary-btn" disabled={busy} onClick={() => void handleSaveEdits()}>
                        Kaydet
                      </button>
                      <button type="button" className="danger-btn" disabled={busy} onClick={() => void handleDelete()}>
                        Sil
                      </button>
                    </div>
                  ) : (
                    <span className={`responsibility-pill ${detail.is_active ? "responsibility-pill--on" : "responsibility-pill--off"}`}>
                      {detail.is_active ? "Aktif" : "Pasif"}
                    </span>
                  )}
                </div>
                <nav className="responsibility-tabs" role="tablist">
                  <button
                    type="button"
                    role="tab"
                    className={`responsibility-tab ${activeTab === "general" ? "active" : ""}`}
                    onClick={() => setActiveTab("general")}
                  >
                    <span className="material-symbols-outlined">tune</span>
                    <span className="responsibility-tab-label">Genel</span>
                  </button>
                  <button
                    type="button"
                    role="tab"
                    className={`responsibility-tab ${activeTab === "users" ? "active" : ""}`}
                    onClick={() => setActiveTab("users")}
                  >
                    <span className="material-symbols-outlined">group</span>
                    <span className="responsibility-tab-label">Kullanıcılar</span>
                    <span className="responsibility-tab-count">{detail.users.length}</span>
                  </button>
                  <button
                    type="button"
                    role="tab"
                    className={`responsibility-tab ${activeTab === "regions" ? "active" : ""}`}
                    onClick={() => setActiveTab("regions")}
                  >
                    <span className="material-symbols-outlined">map</span>
                    <span className="responsibility-tab-label">Bölgeler</span>
                    <span className="responsibility-tab-count">{detail.regions?.length ?? 0}</span>
                  </button>
                  <button
                    type="button"
                    role="tab"
                    className={`responsibility-tab ${activeTab === "lines" ? "active" : ""}`}
                    onClick={() => setActiveTab("lines")}
                  >
                    <span className="material-symbols-outlined">cable</span>
                    <span className="responsibility-tab-label">Hatlar</span>
                    <span className="responsibility-tab-count">{detail.lines?.length ?? 0}</span>
                  </button>
                  <button
                    type="button"
                    role="tab"
                    className={`responsibility-tab ${activeTab === "devices" ? "active" : ""}`}
                    onClick={() => setActiveTab("devices")}
                  >
                    <span className="material-symbols-outlined">devices</span>
                    <span className="responsibility-tab-label">Cihazlar</span>
                    <span className="responsibility-tab-count">{detail.devices.length}</span>
                  </button>
                </nav>
              </header>

              <div className="responsibility-tab-content">
                {activeTab === "general" ? (
                  <div className="responsibility-section">
                    <div className="responsibility-detail-fields">
                      <label className="responsibility-field">
                        <span>Alan Adı</span>
                        <input value={editName} onChange={(e) => setEditName(e.target.value)} disabled={!canEdit || busy} />
                      </label>
                      <label className="responsibility-field responsibility-field--readonly">
                        <span>Kod</span>
                        <input value={detail.code} disabled readOnly />
                      </label>
                      <label className="responsibility-field responsibility-field--wide responsibility-field--description">
                        <span>Açıklama</span>
                        <textarea
                          className="responsibility-textarea"
                          value={editDescription}
                          onChange={(e) => setEditDescription(e.target.value)}
                          disabled={!canEdit || busy}
                          placeholder="Bu alanın amacını kısaca yazın..."
                          rows={5}
                        />
                      </label>
                    </div>
                  </div>
                ) : null}

                {activeTab === "users" ? (
                  <div className="responsibility-section">
                    <p className="helper-text responsibility-section-hint-block">
                      Solda eklenebilir, sağda bu alana atanmış kullanıcılar.
                    </p>
                    <div className="responsibility-transfer-grid">
                  <div className="transfer-pane transfer-pane--source">
                    <div className="transfer-pane-titlebar">
                      <span className="transfer-pane-title">Eklenebilir</span>
                      <span className="transfer-pane-count">{availableUsers.length}</span>
                    </div>
                    <input
                      className="transfer-search"
                      placeholder="Kullanıcı ara..."
                      value={searchUser}
                      onChange={(e) => setSearchUser(e.target.value)}
                    />
                    <ul className="transfer-list">
                      {availableUsers.map((u) => (
                        <li key={u.id} className="transfer-item">
                          <div className="transfer-item-avatar">{(u.full_name || u.username).slice(0, 1).toUpperCase()}</div>
                          <div className="transfer-item-text">
                            <strong>{u.full_name}</strong>
                            <span>{u.username}</span>
                          </div>
                          {canEdit ? (
                            <button
                              type="button"
                              className="icon-btn icon-btn-add"
                              title="Alana ekle"
                              aria-label="Alana ekle"
                              disabled={busy}
                              onClick={() => void handleAddUser(u.id)}
                            >
                              <span className="material-symbols-outlined">add</span>
                            </button>
                          ) : null}
                        </li>
                      ))}
                      {availableUsers.length === 0 ? (
                        <li className="transfer-empty">
                          {searchUser ? "Aramaya uygun kullanıcı yok." : "Eklenebilecek kullanıcı kalmadı."}
                        </li>
                      ) : null}
                    </ul>
                  </div>
                  <div className="transfer-pane transfer-pane--target">
                    <div className="transfer-pane-titlebar">
                      <span className="transfer-pane-title">Bu Alanda</span>
                      <span className="transfer-pane-count transfer-pane-count--target">{detail.users.length}</span>
                    </div>
                    <ul className="transfer-list">
                      {detail.users.map((u) => (
                        <li key={u.id} className="transfer-item transfer-item-in">
                          {canEdit ? (
                            <button
                              type="button"
                              className="icon-btn icon-btn-remove"
                              title="Alandan çıkar"
                              aria-label="Alandan çıkar"
                              disabled={busy}
                              onClick={() => void handleRemoveUser(u.id)}
                            >
                              <span className="material-symbols-outlined">remove</span>
                            </button>
                          ) : null}
                          <div className="transfer-item-avatar transfer-item-avatar--in">{(u.full_name || u.username).slice(0, 1).toUpperCase()}</div>
                          <div className="transfer-item-text">
                            <strong>{u.full_name}</strong>
                            <span>{u.username}</span>
                          </div>
                        </li>
                      ))}
                      {detail.users.length === 0 ? (
                        <li className="transfer-empty">
                          Henüz kullanıcı yok. Soldan + butonu ile ekleyin.
                        </li>
                      ) : null}
                    </ul>
                  </div>
                </div>
                  </div>
                ) : null}

                {activeTab === "regions" ? (
                <div className="responsibility-section">
                  <p className="helper-text responsibility-section-hint-block">
                    Bölge eklendiğinde o bölgenin tüm hatları ve cihazları otomatik kapsama girer.
                  </p>
                  <div className="responsibility-transfer-grid">
                    <div className="transfer-pane transfer-pane--source">
                      <div className="transfer-pane-titlebar">
                        <span className="transfer-pane-title">Eklenebilir</span>
                        <span className="transfer-pane-count">{availableRegions.length}</span>
                      </div>
                      <input
                        className="transfer-search"
                        placeholder="Bölge ara..."
                        value={searchRegion}
                        onChange={(e) => setSearchRegion(e.target.value)}
                      />
                      <ul className="transfer-list">
                        {availableRegions.map((r) => (
                          <li key={r.id} className="transfer-item">
                            <div className="transfer-item-icon"><span className="material-symbols-outlined">map</span></div>
                            <div className="transfer-item-text">
                              <strong>{r.name}</strong>
                              <span>{r.code}</span>
                            </div>
                            {canEdit ? (
                              <button
                                type="button"
                                className="icon-btn icon-btn-add"
                                title="Alana ekle"
                                aria-label="Alana ekle"
                                disabled={busy}
                                onClick={() => void handleAddRegion(r.id)}
                              >
                                ＋
                              </button>
                            ) : null}
                          </li>
                        ))}
                        {availableRegions.length === 0 ? (
                          <li className="transfer-empty">
                            {searchRegion ? "Aramaya uygun bölge yok." : "Eklenebilecek bölge kalmadı."}
                          </li>
                        ) : null}
                      </ul>
                    </div>
                    <div className="transfer-pane transfer-pane--target">
                      <div className="transfer-pane-titlebar">
                        <span className="transfer-pane-title">Bu Alanda</span>
                        <span className="transfer-pane-count transfer-pane-count--target">{detail.regions?.length ?? 0}</span>
                      </div>
                      <ul className="transfer-list">
                        {(detail.regions ?? []).map((r) => (
                          <li key={r.id} className="transfer-item transfer-item-in">
                            {canEdit ? (
                              <button
                                type="button"
                                className="icon-btn icon-btn-remove"
                                title="Alandan çıkar"
                                aria-label="Alandan çıkar"
                                disabled={busy}
                                onClick={() => void handleRemoveRegion(r.id)}
                              >
                                −
                              </button>
                            ) : null}
                            <div className="transfer-item-icon"><span className="material-symbols-outlined">map</span></div>
                            <div className="transfer-item-text">
                              <strong>{r.name}</strong>
                              <span>{r.code}</span>
                            </div>
                          </li>
                        ))}
                        {(detail.regions?.length ?? 0) === 0 ? (
                          <li className="transfer-empty">Bu alana henüz bölge atanmadı.</li>
                        ) : null}
                      </ul>
                    </div>
                  </div>
                </div>
                ) : null}

                {activeTab === "lines" ? (
                <div className="responsibility-section">
                  <p className="helper-text responsibility-section-hint-block">
                    Hat eklendiğinde o hattaki tüm cihazlar otomatik kapsama girer (bölgeden daha spesifik).
                  </p>
                  <div className="responsibility-transfer-grid">
                    <div className="transfer-pane transfer-pane--source">
                      <div className="transfer-pane-titlebar">
                        <span className="transfer-pane-title">Eklenebilir</span>
                        <span className="transfer-pane-count">{availableLines.length}</span>
                      </div>
                      <input
                        className="transfer-search"
                        placeholder="Hat ara..."
                        value={searchLine}
                        onChange={(e) => setSearchLine(e.target.value)}
                      />
                      <ul className="transfer-list">
                        {availableLines.map((l) => (
                          <li key={l.id} className="transfer-item">
                            <div className="transfer-item-icon"><span className="material-symbols-outlined">cable</span></div>
                            <div className="transfer-item-text">
                              <strong>{l.name}</strong>
                              <span>{l.code}</span>
                            </div>
                            {canEdit ? (
                              <button
                                type="button"
                                className="icon-btn icon-btn-add"
                                title="Alana ekle"
                                aria-label="Alana ekle"
                                disabled={busy}
                                onClick={() => void handleAddLine(l.id)}
                              >
                                ＋
                              </button>
                            ) : null}
                          </li>
                        ))}
                        {availableLines.length === 0 ? (
                          <li className="transfer-empty">
                            {searchLine ? "Aramaya uygun hat yok." : "Eklenebilecek hat kalmadı."}
                          </li>
                        ) : null}
                      </ul>
                    </div>
                    <div className="transfer-pane transfer-pane--target">
                      <div className="transfer-pane-titlebar">
                        <span className="transfer-pane-title">Bu Alanda</span>
                        <span className="transfer-pane-count transfer-pane-count--target">{detail.lines?.length ?? 0}</span>
                      </div>
                      <ul className="transfer-list">
                        {(detail.lines ?? []).map((l) => (
                          <li key={l.id} className="transfer-item transfer-item-in">
                            {canEdit ? (
                              <button
                                type="button"
                                className="icon-btn icon-btn-remove"
                                title="Alandan çıkar"
                                aria-label="Alandan çıkar"
                                disabled={busy}
                                onClick={() => void handleRemoveLine(l.id)}
                              >
                                −
                              </button>
                            ) : null}
                            <div className="transfer-item-icon"><span className="material-symbols-outlined">cable</span></div>
                            <div className="transfer-item-text">
                              <strong>{l.name}</strong>
                              <span>{l.code}</span>
                            </div>
                          </li>
                        ))}
                        {(detail.lines?.length ?? 0) === 0 ? (
                          <li className="transfer-empty">Bu alana henüz hat atanmadı.</li>
                        ) : null}
                      </ul>
                    </div>
                  </div>
                </div>
                ) : null}

                {activeTab === "devices" ? (
                <div className="responsibility-section">
                  <p className="helper-text responsibility-section-hint-block">
                    Solda eklenebilir, sağda bu alana atanmış cihazlar. (Alan; bölge ve hat üzerinden de cihazlara dolaylı sahiptir.)
                  </p>
                  <div className="responsibility-transfer-grid">
                    <div className="transfer-pane transfer-pane--source">
                      <div className="transfer-pane-titlebar">
                        <span className="transfer-pane-title">Eklenebilir</span>
                        <span className="transfer-pane-count">{availableDevices.length}</span>
                      </div>
                      <input
                        className="transfer-search"
                        placeholder="Cihaz ara..."
                        value={searchDevice}
                        onChange={(e) => setSearchDevice(e.target.value)}
                      />
                      <ul className="transfer-list">
                        {availableDevices.map((d) => (
                          <li key={d.id} className="transfer-item">
                            <div className="transfer-item-icon"><span className="material-symbols-outlined">router</span></div>
                            <div className="transfer-item-text">
                              <strong>{d.name}</strong>
                              <span>{d.code}</span>
                            </div>
                            {canEdit ? (
                              <button
                                type="button"
                                className="icon-btn icon-btn-add"
                                title="Alana ekle"
                                aria-label="Alana ekle"
                                disabled={busy}
                                onClick={() => void handleAddDevice(d.id)}
                              >
                                ＋
                              </button>
                            ) : null}
                          </li>
                        ))}
                        {availableDevices.length === 0 ? (
                          <li className="transfer-empty">
                            {searchDevice ? "Aramaya uygun cihaz yok." : "Eklenebilecek cihaz kalmadı."}
                          </li>
                        ) : null}
                      </ul>
                    </div>
                    <div className="transfer-pane transfer-pane--target">
                      <div className="transfer-pane-titlebar">
                        <span className="transfer-pane-title">Bu Alanda</span>
                        <span className="transfer-pane-count transfer-pane-count--target">{detail.devices.length}</span>
                      </div>
                      <ul className="transfer-list">
                        {detail.devices.map((d) => (
                          <li key={d.id} className="transfer-item transfer-item-in">
                            {canEdit ? (
                              <button
                                type="button"
                                className="icon-btn icon-btn-remove"
                                title="Alandan çıkar"
                                aria-label="Alandan çıkar"
                                disabled={busy}
                                onClick={() => void handleRemoveDevice(d.id)}
                              >
                                −
                              </button>
                            ) : null}
                            <div className="transfer-item-icon"><span className="material-symbols-outlined">router</span></div>
                            <div className="transfer-item-text">
                              <strong>{d.name}</strong>
                              <span>{d.code}</span>
                            </div>
                          </li>
                        ))}
                        {detail.devices.length === 0 ? (
                          <li className="transfer-empty">
                            Henüz cihaz yok. Soldan + butonu ile ekleyin.
                          </li>
                        ) : null}
                      </ul>
                    </div>
                  </div>
                </div>
              ) : null}
              </div>
            </>
          ) : null}
        </main>
      </div>

      {showCreateModal ? (
        <div className="settings-modal-backdrop">
          <form className="settings-modal" onSubmit={handleCreate}>
            <h3>Yeni Sorumluluk Alanı</h3>
            <label>
              Kod (kısaltma, benzersiz)
              <input value={createCode} onChange={(e) => setCreateCode(e.target.value)} required />
            </label>
            <label>
              Alan Adı
              <input value={createName} onChange={(e) => setCreateName(e.target.value)} required />
            </label>
            <label>
              Açıklama
              <input value={createDescription} onChange={(e) => setCreateDescription(e.target.value)} />
            </label>
            {localError ? <p className="error-text">{localError}</p> : null}
            <div className="settings-actions">
              <button type="button" onClick={() => setShowCreateModal(false)} disabled={busy}>
                İptal
              </button>
              <button type="submit" className="primary-btn" disabled={busy}>
                Oluştur
              </button>
            </div>
          </form>
        </div>
      ) : null}
    </section>
  );
}
