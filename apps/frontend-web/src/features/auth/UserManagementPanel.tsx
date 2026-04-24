import { useEffect, useState, type FormEvent } from "react";

import type { UserRead, UserRole } from "../../shared/types";

type Props = {
  users: UserRead[];
  /** false: mühendis — sadece operatör/mühendis oluşturulabilir; kurulumcu rolü atanamaz */
  allowInstallerRole?: boolean;
  currentUserId?: number;
  onCreate: (payload: {
    username: string;
    email: string;
    phone_number?: string | null;
    full_name: string;
    password: string;
    role: UserRole;
  }) => Promise<void>;
  onDelete: (userId: number) => Promise<void>;
  onUpdate: (
    userId: number,
    payload: { email: string; phone_number?: string | null; full_name: string; role: UserRole }
  ) => Promise<void>;
  onResetPassword: (userId: number, newPassword: string) => Promise<void>;
};

type NotificationPrefs = {
  email: boolean;
  sms: boolean;
};

const NOTIFICATION_PREFS_STORAGE_KEY = "hsl-user-notification-prefs";

export function UserManagementPanel({
  users,
  allowInstallerRole = true,
  currentUserId,
  onCreate,
  onDelete,
  onUpdate,
  onResetPassword
}: Props) {
  const [isCreateModalOpen, setCreateModalOpen] = useState(false);
  const [editingUserId, setEditingUserId] = useState<number | null>(null);
  const [passwordResetUser, setPasswordResetUser] = useState<UserRead | null>(null);
  const [username, setUsername] = useState("");
  const [email, setEmail] = useState("");
  const [fullName, setFullName] = useState("");
  const [phoneNumber, setPhoneNumber] = useState("");
  const [password, setPassword] = useState("");
  const [editUsername, setEditUsername] = useState("");
  const [resetPassword, setResetPassword] = useState("");
  const [resetPasswordConfirm, setResetPasswordConfirm] = useState("");
  const [role, setRole] = useState<UserRole>("operator");
  const [emailNotify, setEmailNotify] = useState(true);
  const [smsNotify, setSmsNotify] = useState(false);
  const [submitError, setSubmitError] = useState("");
  const [notificationPrefs, setNotificationPrefs] = useState<Record<number, NotificationPrefs>>({});

  useEffect(() => {
    try {
      const raw = localStorage.getItem(NOTIFICATION_PREFS_STORAGE_KEY);
      if (!raw) return;
      const parsed = JSON.parse(raw) as Record<string, NotificationPrefs>;
      const normalized: Record<number, NotificationPrefs> = {};
      Object.entries(parsed).forEach(([key, value]) => {
        const id = Number(key);
        if (!Number.isNaN(id) && value && typeof value === "object") {
          normalized[id] = {
            email: Boolean(value.email),
            sms: Boolean(value.sms)
          };
        }
      });
      setNotificationPrefs(normalized);
    } catch {
      setNotificationPrefs({});
    }
  }, []);

  useEffect(() => {
    setNotificationPrefs((prev) => {
      const next: Record<number, NotificationPrefs> = { ...prev };
      users.forEach((user) => {
        if (!next[user.id]) {
          next[user.id] = { email: true, sms: false };
        }
      });
      localStorage.setItem(NOTIFICATION_PREFS_STORAGE_KEY, JSON.stringify(next));
      return next;
    });
  }, [users]);

  const handleSubmit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setSubmitError("");
    try {
      await onCreate({
        username,
        email,
        phone_number: phoneNumber.trim() ? phoneNumber.trim() : null,
        full_name: fullName,
        password,
        role
      });
      setUsername("");
      setEmail("");
      setFullName("");
      setPhoneNumber("");
      setPassword("");
      setRole("operator");
      setEmailNotify(true);
      setSmsNotify(false);
      setCreateModalOpen(false);
    } catch (error) {
      setSubmitError(error instanceof Error ? error.message : "Kullanıcı oluşturulamadı.");
    }
  };

  const startEdit = (user: UserRead) => {
    setEditingUserId(user.id);
    setEditUsername(user.username);
    setEmail(user.email);
    setFullName(user.full_name);
    setPhoneNumber(user.phone_number ?? "");
    setRole(user.role);
    const prefs = notificationPrefs[user.id] ?? { email: true, sms: false };
    setEmailNotify(prefs.email);
    setSmsNotify(prefs.sms);
  };

  const handleEditSubmit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (editingUserId === null) return;
    setSubmitError("");
    try {
      await onUpdate(editingUserId, {
        email,
        phone_number: phoneNumber.trim() ? phoneNumber.trim() : null,
        full_name: fullName,
        role
      });
      setNotificationPrefs((prev) => {
        const next = {
          ...prev,
          [editingUserId]: {
            email: emailNotify,
            sms: smsNotify
          }
        };
        localStorage.setItem(NOTIFICATION_PREFS_STORAGE_KEY, JSON.stringify(next));
        return next;
      });
      setEditingUserId(null);
      setEditUsername("");
      setEmail("");
      setFullName("");
      setPhoneNumber("");
      setRole("operator");
      setEmailNotify(true);
      setSmsNotify(false);
    } catch (error) {
      setSubmitError(error instanceof Error ? error.message : "Kullanıcı güncellenemedi.");
    }
  };

  const getRoleLabel = (value: UserRead["role"]) => {
    if (value === "engineer") return "Mühendis";
    if (value === "installer") return "Kurulumcu";
    return "Operatör";
  };

  const handleDeleteClick = async (user: UserRead) => {
    const approved = window.confirm(
      `${user.full_name} adlı kullanıcıyı silmek istediğinize emin misiniz? Bu işlem geri alınamaz.`
    );
    if (!approved) return;
    setSubmitError("");
    try {
      await onDelete(user.id);
    } catch (error) {
      setSubmitError(error instanceof Error ? error.message : "Kullanıcı silinemedi.");
    }
  };

  const openResetPasswordModal = (user: UserRead) => {
    setPasswordResetUser(user);
    setResetPassword("");
    setResetPasswordConfirm("");
    setSubmitError("");
  };

  const handleResetPasswordSubmit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!passwordResetUser) return;
    if (resetPassword.length < 8) {
      setSubmitError("Şifre en az 8 karakter olmalıdır.");
      return;
    }
    if (resetPassword !== resetPasswordConfirm) {
      setSubmitError("Şifre tekrarı eşleşmiyor.");
      return;
    }
    setSubmitError("");
    try {
      await onResetPassword(passwordResetUser.id, resetPassword);
      setPasswordResetUser(null);
      setResetPassword("");
      setResetPasswordConfirm("");
    } catch (error) {
      setSubmitError(error instanceof Error ? error.message : "Şifre sıfırlanamadı.");
    }
  };

  return (
    <section className="tab-panel">
      <div className="panel-head">
        <div>
          <h3>Kullanıcı Yönetimi</h3>
          {!allowInstallerRole ? (
            <p className="helper-text" style={{ margin: "6px 0 0", maxWidth: "42rem" }}>
              Bu ekranda yalnızca <strong>operatör</strong> ve <strong>mühendis</strong> hesapları listelenir ve
              yönetilir. Kurulumcu hesapları yalnızca kurulumcu tarafından yönetilir; mühendis kurulumcu rolü
              atayamaz.
            </p>
          ) : null}
        </div>
        <button className="add-user-btn" onClick={() => setCreateModalOpen(true)}>
          + Kullanıcı Ekle
        </button>
      </div>
      {submitError ? <p className="error-text">{submitError}</p> : null}

      {isCreateModalOpen ? (
        <div className="settings-modal-backdrop">
          <form className="settings-modal" onSubmit={handleSubmit}>
            <h3>Yeni Kullanıcı</h3>
            <label>
              Kullanıcı adı
              <input value={username} onChange={(event) => setUsername(event.target.value)} required />
            </label>
            <label>
              E-posta
              <input value={email} onChange={(event) => setEmail(event.target.value)} required />
            </label>
            <label>
              Ad Soyad
              <input value={fullName} onChange={(event) => setFullName(event.target.value)} required />
            </label>
            <label>
              Telefon Numarası
              <input
                type="tel"
                value={phoneNumber}
                onChange={(event) => setPhoneNumber(event.target.value)}
                placeholder="+90 5xx xxx xx xx"
              />
            </label>
            <label>
              Şifre
              <input value={password} onChange={(event) => setPassword(event.target.value)} required />
            </label>
            <label>
              Rol
              <select value={role} onChange={(event) => setRole(event.target.value as UserRole)}>
                <option value="operator">Operatör</option>
                <option value="engineer">Mühendis</option>
                {allowInstallerRole ? <option value="installer">Kurulumcu (Süper Admin)</option> : null}
              </select>
            </label>
            <fieldset className="notify-group">
              <legend>Bildirim Tercihleri</legend>
              <label className="notify-option">
                <input type="checkbox" checked={emailNotify} onChange={(event) => setEmailNotify(event.target.checked)} />
                E-posta bildirimi
              </label>
              <label className="notify-option">
                <input type="checkbox" checked={smsNotify} onChange={(event) => setSmsNotify(event.target.checked)} />
                SMS bildirimi
              </label>
            </fieldset>
            <div className="settings-actions">
              <button type="button" onClick={() => setCreateModalOpen(false)}>
                Vazgeç
              </button>
              <button type="submit">Kaydet</button>
            </div>
          </form>
        </div>
      ) : null}

      {editingUserId !== null ? (
        <div className="settings-modal-backdrop">
          <form className="settings-modal" onSubmit={handleEditSubmit}>
            <h3>Kullanıcı Düzenle</h3>
            <label>
              Kullanıcı Adı
              <input value={editUsername} disabled readOnly />
            </label>
            <label>
              E-posta
              <input value={email} onChange={(event) => setEmail(event.target.value)} required />
            </label>
            <label>
              Ad Soyad
              <input value={fullName} onChange={(event) => setFullName(event.target.value)} required />
            </label>
            <label>
              Telefon Numarası
              <input
                type="tel"
                value={phoneNumber}
                onChange={(event) => setPhoneNumber(event.target.value)}
                placeholder="+90 5xx xxx xx xx"
              />
            </label>
            <label>
              Rol
              <select value={role} onChange={(event) => setRole(event.target.value as UserRole)}>
                <option value="operator">Operatör</option>
                <option value="engineer">Mühendis</option>
                {allowInstallerRole ? <option value="installer">Kurulumcu (Süper Admin)</option> : null}
              </select>
            </label>
            <fieldset className="notify-group">
              <legend>Bildirim Tercihleri</legend>
              <label className="notify-option">
                <input type="checkbox" checked={emailNotify} onChange={(event) => setEmailNotify(event.target.checked)} />
                E-posta bildirimi
              </label>
              <label className="notify-option">
                <input type="checkbox" checked={smsNotify} onChange={(event) => setSmsNotify(event.target.checked)} />
                SMS bildirimi
              </label>
            </fieldset>
            <div className="settings-actions">
              <button type="button" onClick={() => setEditingUserId(null)}>
                Vazgeç
              </button>
              <button type="submit">Güncelle</button>
            </div>
          </form>
        </div>
      ) : null}

      {passwordResetUser ? (
        <div className="settings-modal-backdrop">
          <form className="settings-modal" onSubmit={handleResetPasswordSubmit}>
            <h3>Şifre Sıfırla</h3>
            <p className="helper-text">{passwordResetUser.full_name} için yeni şifre belirleyin.</p>
            <label>
              Yeni Şifre
              <input
                type="password"
                value={resetPassword}
                onChange={(event) => setResetPassword(event.target.value)}
                minLength={8}
                required
              />
            </label>
            <label>
              Yeni Şifre Tekrar
              <input
                type="password"
                value={resetPasswordConfirm}
                onChange={(event) => setResetPasswordConfirm(event.target.value)}
                minLength={8}
                required
              />
            </label>
            <div className="settings-actions">
              <button type="button" onClick={() => setPasswordResetUser(null)}>
                Vazgeç
              </button>
              <button type="submit">Şifreyi Sıfırla</button>
            </div>
          </form>
        </div>
      ) : null}

      <table className="values-table user-table">
        <thead>
          <tr>
            <th>Kullanıcı</th>
            <th>Kullanıcı Adı</th>
            <th>Rol</th>
            <th>E-posta</th>
            <th>Telefon</th>
            <th>E-posta Bildirim</th>
            <th>SMS Bildirim</th>
            <th className="actions-header">İşlem</th>
          </tr>
        </thead>
        <tbody>
          {users.map((user) => (
            <tr key={user.id}>
              <td>{user.full_name}</td>
              <td>{user.username}</td>
              <td>{getRoleLabel(user.role)}</td>
              <td>{user.email}</td>
              <td>{user.phone_number ?? "-"}</td>
              <td className="notify-cell">
                <span
                  className={`status-toggle ${notificationPrefs[user.id]?.email ? "on" : "off"}`}
                  title={notificationPrefs[user.id]?.email ? "Açık" : "Kapalı"}
                  aria-label={`E-posta bildirimi ${notificationPrefs[user.id]?.email ? "açık" : "kapalı"}`}
                >
                  {notificationPrefs[user.id]?.email ? "⏽" : "⭘"}
                </span>
              </td>
              <td className="notify-cell">
                <span
                  className={`status-toggle ${notificationPrefs[user.id]?.sms ? "on" : "off"}`}
                  title={notificationPrefs[user.id]?.sms ? "Açık" : "Kapalı"}
                  aria-label={`SMS bildirimi ${notificationPrefs[user.id]?.sms ? "açık" : "kapalı"}`}
                >
                  {notificationPrefs[user.id]?.sms ? "⏽" : "⭘"}
                </span>
              </td>
              <td className="actions-cell">
                <button className="edit-btn action-btn" onClick={() => startEdit(user)}>
                  Düzenle
                </button>
                <button className="secondary-btn action-btn" onClick={() => openResetPasswordModal(user)}>
                  Şifre Sıfırla
                </button>
                <button
                  className="danger-btn action-btn"
                  onClick={() => void handleDeleteClick(user)}
                  disabled={currentUserId === user.id}
                  title={currentUserId === user.id ? "Kendinizi silemezsiniz" : "Kullanıcıyı sil"}
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
