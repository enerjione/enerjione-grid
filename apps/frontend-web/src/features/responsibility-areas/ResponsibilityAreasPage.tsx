import { useEffect, useMemo, useState, type FormEvent } from "react";
import { asyncConfirm } from "../../components/ConfirmDialog";
import { useTranslation } from "react-i18next";

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
  role: "operator" | "engineer" | "installer" | "ops_manager";
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
  const { t } = useTranslation();
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
  const [activeTab, setActiveTab] = useState<"general" | "users" | "regions" | "lines">("general");

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
    // Ekiplere yalnızca operator hesapları atanabilir; mühendis ve
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
      // Code alanini kullanicidan istemiyoruz — name'den otomatik turetiyoruz.
      // ASCII-slugify + 6 char hex suffix (cakisma engelleme; iki ayni isim
      // team olsa bile farkli code'larla unique kalir).
      const slug = createName
        .trim()
        .toLowerCase()
        .normalize("NFD")
        .replace(/[̀-ͯ]/g, "")
        .replace(/[^a-z0-9]+/g, "-")
        .replace(/^-+|-+$/g, "")
        .slice(0, 40) || "team";
      const suffix = Math.random().toString(36).slice(2, 8);
      const autoCode = `${slug}-${suffix}`;
      await onCreate({
        code: autoCode,
        name: createName.trim(),
        description: createDescription.trim() || null
      });
      setCreateCode("");
      setCreateName("");
      setCreateDescription("");
      setShowCreateModal(false);
    } catch (err) {
      setLocalError(err instanceof Error ? err.message : "Ekip oluşturulamadı.");
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
      setLocalError(err instanceof Error ? err.message : "Ekip güncellenemedi.");
    } finally {
      setBusy(false);
    }
  };

  const handleDelete = async () => {
    if (!detail) return;
    if (!await asyncConfirm(`"${detail.name}" ekip silinsin mi?`)) return;
    setBusy(true);
    setLocalError("");
    try {
      await onDelete(detail.id);
      setSelectedAreaId(null);
      setDetail(null);
    } catch (err) {
      setLocalError(err instanceof Error ? err.message : "Ekip silinemedi.");
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
            <h3>{t("engineering.responsibilityAreas.title")}</h3>
            {canEdit ? (
              <button
                type="button"
                className="primary-btn responsibility-new-btn"
                onClick={() => setShowCreateModal(true)}
              >
                + {t("engineering.responsibilityAreas.newButton")}
              </button>
            ) : null}
          </div>
          {loading ? <p className="helper-text">{t("common.loading")}</p> : null}
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
                    <span className="responsibility-pill responsibility-pill--off">{t("engineering.responsibilityAreas.passive")}</span>
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
                <div className="empty-state empty-state--compact">
                  <span className="material-symbols-outlined">groups</span>
                  <p><strong>Henüz ekip yok.</strong></p>
                  {canEdit ? <p className="helper-text">Sağdaki "+ Yeni" ile bir alan oluşturun.</p> : null}
                </div>
              </li>
            ) : null}
          </ul>
        </aside>

        {/* Sağ: detay paneli */}
        <main className="responsibility-detail">
          {detailLoading ? (
            <div className="panel-loading-overlay" aria-live="polite">
              <span className="panel-loading-spinner" aria-hidden="true" />
              <span>{t("engineering.responsibilityAreas.loading")}</span>
            </div>
          ) : null}
          {localError ? <p className="error-text">{localError}</p> : null}
          {!detail && !detailLoading ? (
            <div className="responsibility-empty-detail">
              <span className="material-symbols-outlined responsibility-empty-icon">folder_open</span>
              {/* ACIKLAMA KALDIRILDI. "Soldan bir ekip secin ya da yeni bir
                  alan olusturun..." metni, tam da soldaki listenin ve "Yeni"
                  butonunun yanibasinda duruyordu: ekranin kendisi zaten ne
                  yapilacagini gosteriyor. Baslik yeterli. */}
              <h3>{t("engineering.responsibilityAreas.selectHint")}</h3>
            </div>
          ) : null}
          {detail ? (
            <>
              <header className="responsibility-detail-header">
                <div className="responsibility-detail-titlebar">
                  <div className="responsibility-detail-title">
                    <h2>{detail.name || t("engineering.responsibilityAreas.newAreaName")}</h2>
                  </div>
                  {canEdit ? (
                    <div className="responsibility-detail-actions">
                      <ActiveSwitch
                        checked={editIsActive}
                        onChange={setEditIsActive}
                        disabled={busy}
                        title={t("engineering.responsibilityAreas.activeTitle")}
                      />
                      <button type="button" className="primary-btn" disabled={busy} onClick={() => void handleSaveEdits()}>
                        {t("engineering.responsibilityAreas.save")}
                      </button>
                      <button type="button" className="danger-btn" disabled={busy} onClick={() => void handleDelete()}>
                        {t("engineering.responsibilityAreas.delete")}
                      </button>
                    </div>
                  ) : (
                    <span className={`responsibility-pill ${detail.is_active ? "responsibility-pill--on" : "responsibility-pill--off"}`}>
                      {detail.is_active ? t("engineering.responsibilityAreas.active") : t("engineering.responsibilityAreas.passive")}
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
                    <span className="responsibility-tab-label">{t("engineering.responsibilityAreas.tabGeneral")}</span>
                  </button>
                  <button
                    type="button"
                    role="tab"
                    className={`responsibility-tab ${activeTab === "users" ? "active" : ""}`}
                    onClick={() => setActiveTab("users")}
                  >
                    <span className="material-symbols-outlined">group</span>
                    <span className="responsibility-tab-label">{t("engineering.responsibilityAreas.tabUsers")}</span>
                    <span className="responsibility-tab-count">{detail.users.length}</span>
                  </button>
                  <button
                    type="button"
                    role="tab"
                    className={`responsibility-tab ${activeTab === "regions" ? "active" : ""}`}
                    onClick={() => setActiveTab("regions")}
                  >
                    <span className="material-symbols-outlined">map</span>
                    <span className="responsibility-tab-label">{t("engineering.responsibilityAreas.tabRegions")}</span>
                    <span className="responsibility-tab-count">{detail.regions?.length ?? 0}</span>
                  </button>
                  <button
                    type="button"
                    role="tab"
                    className={`responsibility-tab ${activeTab === "lines" ? "active" : ""}`}
                    onClick={() => setActiveTab("lines")}
                  >
                    <span className="material-symbols-outlined">cable</span>
                    <span className="responsibility-tab-label">{t("engineering.responsibilityAreas.tabLines")}</span>
                    <span className="responsibility-tab-count">{detail.lines?.length ?? 0}</span>
                  </button>
                </nav>
              </header>

              <div className="responsibility-tab-content">
                {activeTab === "general" ? (
                  <div className="responsibility-section">
                    <div className="responsibility-detail-fields">
                      <label className="responsibility-field">
                        <span>{t("engineering.responsibilityAreas.fieldName")}</span>
                        <input value={editName} onChange={(e) => setEditName(e.target.value)} disabled={!canEdit || busy} />
                      </label>
                      <label className="responsibility-field responsibility-field--readonly">
                        <span>{t("engineering.responsibilityAreas.fieldCode")}</span>
                        <input value={detail.code} disabled readOnly />
                      </label>
                      <label className="responsibility-field responsibility-field--wide responsibility-field--description">
                        <span>{t("engineering.responsibilityAreas.fieldDescription")}</span>
                        <textarea
                          className="responsibility-textarea"
                          value={editDescription}
                          onChange={(e) => setEditDescription(e.target.value)}
                          disabled={!canEdit || busy}
                          placeholder={t("engineering.responsibilityAreas.descriptionPlaceholder")}
                          rows={5}
                        />
                      </label>
                    </div>
                  </div>
                ) : null}

                {activeTab === "users" ? (
                  <div className="responsibility-section">
                    <p className="helper-text responsibility-section-hint-block">
                      {t("engineering.responsibilityAreas.usersHint")}
                    </p>
                    <div className="responsibility-transfer-grid">
                  <div className="transfer-pane transfer-pane--source">
                    <div className="transfer-pane-titlebar">
                      <span className="transfer-pane-title">{t("engineering.responsibilityAreas.addable")}</span>
                      <span className="transfer-pane-count">{availableUsers.length}</span>
                    </div>
                    <input
                      className="transfer-search"
                      placeholder={t("engineering.responsibilityAreas.userSearch")}
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
                              title={t("engineering.responsibilityAreas.addToArea")}
                              aria-label={t("engineering.responsibilityAreas.addToArea")}
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
                          {searchUser ? t("engineering.responsibilityAreas.noUsersSearch") : t("engineering.responsibilityAreas.noUsersLeft")}
                        </li>
                      ) : null}
                    </ul>
                  </div>
                  <div className="transfer-pane transfer-pane--target">
                    <div className="transfer-pane-titlebar">
                      <span className="transfer-pane-title">{t("engineering.responsibilityAreas.inThisArea")}</span>
                      <span className="transfer-pane-count transfer-pane-count--target">{detail.users.length}</span>
                    </div>
                    <ul className="transfer-list">
                      {detail.users.map((u) => (
                        <li key={u.id} className="transfer-item transfer-item-in">
                          {canEdit ? (
                            <button
                              type="button"
                              className="icon-btn icon-btn-remove"
                              title={t("engineering.responsibilityAreas.removeFromArea")}
                              aria-label={t("engineering.responsibilityAreas.removeFromArea")}
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
                          {t("engineering.responsibilityAreas.emptyUsers")}
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
                    {t("engineering.responsibilityAreas.regionsHint")}
                  </p>
                  <div className="responsibility-transfer-grid">
                    <div className="transfer-pane transfer-pane--source">
                      <div className="transfer-pane-titlebar">
                        <span className="transfer-pane-title">{t("engineering.responsibilityAreas.addable")}</span>
                        <span className="transfer-pane-count">{availableRegions.length}</span>
                      </div>
                      <input
                        className="transfer-search"
                        placeholder={t("engineering.responsibilityAreas.regionSearch")}
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
                                title={t("engineering.responsibilityAreas.addToArea")}
                                aria-label={t("engineering.responsibilityAreas.addToArea")}
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
                            {searchRegion ? t("engineering.responsibilityAreas.noRegionsSearch") : t("engineering.responsibilityAreas.noRegionsLeft")}
                          </li>
                        ) : null}
                      </ul>
                    </div>
                    <div className="transfer-pane transfer-pane--target">
                      <div className="transfer-pane-titlebar">
                        <span className="transfer-pane-title">{t("engineering.responsibilityAreas.inThisArea")}</span>
                        <span className="transfer-pane-count transfer-pane-count--target">{detail.regions?.length ?? 0}</span>
                      </div>
                      <ul className="transfer-list">
                        {(detail.regions ?? []).map((r) => (
                          <li key={r.id} className="transfer-item transfer-item-in">
                            {canEdit ? (
                              <button
                                type="button"
                                className="icon-btn icon-btn-remove"
                                title={t("engineering.responsibilityAreas.removeFromArea")}
                                aria-label={t("engineering.responsibilityAreas.removeFromArea")}
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
                          <li className="transfer-empty">{t("engineering.responsibilityAreas.emptyRegions")}</li>
                        ) : null}
                      </ul>
                    </div>
                  </div>
                </div>
                ) : null}

                {activeTab === "lines" ? (
                <div className="responsibility-section">
                  <p className="helper-text responsibility-section-hint-block">
                    {t("engineering.responsibilityAreas.linesHint")}
                  </p>
                  <div className="responsibility-transfer-grid">
                    <div className="transfer-pane transfer-pane--source">
                      <div className="transfer-pane-titlebar">
                        <span className="transfer-pane-title">{t("engineering.responsibilityAreas.addable")}</span>
                        <span className="transfer-pane-count">{availableLines.length}</span>
                      </div>
                      <input
                        className="transfer-search"
                        placeholder={t("engineering.responsibilityAreas.lineSearch")}
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
                                title={t("engineering.responsibilityAreas.addToArea")}
                                aria-label={t("engineering.responsibilityAreas.addToArea")}
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
                            {searchLine ? t("engineering.responsibilityAreas.noLinesSearch") : t("engineering.responsibilityAreas.noLinesLeft")}
                          </li>
                        ) : null}
                      </ul>
                    </div>
                    <div className="transfer-pane transfer-pane--target">
                      <div className="transfer-pane-titlebar">
                        <span className="transfer-pane-title">{t("engineering.responsibilityAreas.inThisArea")}</span>
                        <span className="transfer-pane-count transfer-pane-count--target">{detail.lines?.length ?? 0}</span>
                      </div>
                      <ul className="transfer-list">
                        {(detail.lines ?? []).map((l) => (
                          <li key={l.id} className="transfer-item transfer-item-in">
                            {canEdit ? (
                              <button
                                type="button"
                                className="icon-btn icon-btn-remove"
                                title={t("engineering.responsibilityAreas.removeFromArea")}
                                aria-label={t("engineering.responsibilityAreas.removeFromArea")}
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
                          <li className="transfer-empty">{t("engineering.responsibilityAreas.emptyLines")}</li>
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
            <h3>{t("engineering.responsibilityAreas.newAreaModalTitle")}</h3>
            {/* Code alani kaldirildi — handleCreate icinde name'den otomatik
                slugify + random suffix ile uretiliyor. Operator manuel
                unique code uydurmak zorunda kalmasin. */}
            <label>
              {t("engineering.responsibilityAreas.fieldName")}
              <input value={createName} onChange={(e) => setCreateName(e.target.value)} required />
            </label>
            <label>
              {t("engineering.responsibilityAreas.fieldDescription")}
              <input value={createDescription} onChange={(e) => setCreateDescription(e.target.value)} />
            </label>
            {localError ? <p className="error-text">{localError}</p> : null}
            <div className="settings-actions">
              <button type="button" onClick={() => setShowCreateModal(false)} disabled={busy}>
                {t("engineering.responsibilityAreas.modalCancel")}
              </button>
              <button type="submit" className="primary-btn" disabled={busy}>
                {t("engineering.responsibilityAreas.modalCreate")}
              </button>
            </div>
          </form>
        </div>
      ) : null}
    </section>
  );
}
