import { useEffect, useState, type FormEvent } from "react";
import { asyncConfirm } from "../../components/ConfirmDialog";
import { useTranslation } from "react-i18next";

import type { UserNotificationPreferences, UserRead, UserRole } from "../../shared/types";

type NotificationPrefs = {
  web: boolean;
  email: boolean;
  sms: boolean;
  telegram: boolean;
};

type InviteResult = {
  user_id: number;
  username: string;
  setup_url: string;
  expires_at: string;
  email_sent: boolean;
};

type Props = {
  users: UserRead[];
  /** false: mühendis — sadece operatör/mühendis oluşturulabilir; kurulumcu rolü atanamaz */
  allowInstallerRole?: boolean;
  /** true: ops_manager — SADECE operator rolu yaratip/silebilir/listeleyebilir. */
  restrictToOperator?: boolean;
  currentUserId?: number;
  onCreate: (payload: {
    username: string;
    email: string;
    phone_number?: string | null;
    full_name: string;
    password: string;
    role: UserRole;
  }) => Promise<void>;
  /** Davet akisi — sifre belirlemeden user yarat, token uretilir. */
  onInvite?: (payload: {
    username: string;
    email: string;
    phone_number?: string | null;
    full_name: string;
    role: UserRole;
    send_email: boolean;
  }) => Promise<InviteResult | null>;
  /** Pending davet token'ini yenile + mail tekrar gonder. */
  onResendInvite?: (userId: number) => Promise<InviteResult | null>;
  onDelete: (userId: number) => Promise<void>;
  onUpdate: (
    userId: number,
    payload: { email: string; phone_number?: string | null; full_name: string; role: UserRole }
  ) => Promise<void>;
  onResetPassword: (userId: number, newPassword: string) => Promise<void>;
  /** Bir kullanicinin bildirim tercihlerini admin API'sinden cek. */
  onFetchUserPrefs?: (userId: number) => Promise<UserNotificationPreferences>;
  /** Bir kullanicinin bildirim tercihlerini admin API'sinden guncelle. */
  onUpdateUserPrefs?: (
    userId: number,
    payload: Partial<Omit<UserNotificationPreferences, "user_id">>
  ) => Promise<UserNotificationPreferences>;
};

export function UserManagementPanel({
  users,
  allowInstallerRole = true,
  restrictToOperator = false,
  currentUserId,
  onCreate,
  onInvite,
  onResendInvite,
  onDelete,
  onUpdate,
  onResetPassword,
  onFetchUserPrefs,
  onUpdateUserPrefs
}: Props) {
  const { t } = useTranslation();
  const [isCreateModalOpen, setCreateModalOpen] = useState(false);
  // Davet akisi state'i
  const [isInviteModalOpen, setInviteModalOpen] = useState(false);
  const [inviteUsername, setInviteUsername] = useState("");
  const [inviteEmail, setInviteEmail] = useState("");
  const [inviteFullName, setInviteFullName] = useState("");
  const [invitePhone, setInvitePhone] = useState("");
  const [inviteRole, setInviteRole] = useState<UserRole>("operator");
  const [inviteSendEmail, setInviteSendEmail] = useState(true);
  const [inviteSubmitting, setInviteSubmitting] = useState(false);
  const [inviteError, setInviteError] = useState("");
  const [inviteResult, setInviteResult] = useState<InviteResult | null>(null);
  const [inviteUrlCopied, setInviteUrlCopied] = useState(false);
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
  // Form modali icindeki tercih toggle'lari (sadece UI state — kaydederken
  // backend admin endpoint'ine PUT'lanir).
  const [webNotify, setWebNotify] = useState(true);
  const [emailNotify, setEmailNotify] = useState(true);
  const [smsNotify, setSmsNotify] = useState(false);
  const [telegramNotify, setTelegramNotify] = useState(false);
  const [submitError, setSubmitError] = useState("");
  const [search, setSearch] = useState("");
  // Tablodaki rozet ikonlari icin tum kullanicilarin son bilinen tercihleri.
  // Liste yuklenince paralel olarak doldurulur; toggle degisince guncellenir.
  const [notificationPrefs, setNotificationPrefs] = useState<Record<number, NotificationPrefs>>({});

  // Liste her degistiginde tum kullanicilarin tercihlerini paralel cek.
  // Backend admin endpoint default deger uretip dondurur, ek yuk yok.
  useEffect(() => {
    if (!onFetchUserPrefs) return;
    let cancelled = false;
    void (async () => {
      const next: Record<number, NotificationPrefs> = {};
      await Promise.all(
        users.map(async (u) => {
          try {
            const p = await onFetchUserPrefs(u.id);
            next[u.id] = {
              web: Boolean(p.web_enabled),
              email: Boolean(p.email_enabled),
              sms: Boolean(p.sms_enabled),
              telegram: Boolean(p.telegram_enabled)
            };
          } catch {
            // Yukleme hatasini sessizce yut — default goster
            next[u.id] = { web: true, email: true, sms: false, telegram: false };
          }
        })
      );
      if (!cancelled) setNotificationPrefs(next);
    })();
    return () => {
      cancelled = true;
    };
  }, [users, onFetchUserPrefs]);

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
      setWebNotify(true);
      setEmailNotify(true);
      setSmsNotify(false);
      setTelegramNotify(false);
      setCreateModalOpen(false);
    } catch (error) {
      setSubmitError(error instanceof Error ? error.message : t("common.errorOccurred"));
    }
  };

  const startEdit = (user: UserRead) => {
    setEditingUserId(user.id);
    setEditUsername(user.username);
    setEmail(user.email);
    setFullName(user.full_name);
    setPhoneNumber(user.phone_number ?? "");
    setRole(user.role);
    const prefs =
      notificationPrefs[user.id] ?? { web: true, email: true, sms: false, telegram: false };
    setWebNotify(prefs.web);
    setEmailNotify(prefs.email);
    setSmsNotify(prefs.sms);
    setTelegramNotify(prefs.telegram);
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
      // Bildirim tercihleri admin API'sine yazilir — eskiden sadece
      // localStorage'a yaziliyordu ve alarm dispatch bunu bilmiyordu.
      if (onUpdateUserPrefs) {
        try {
          await onUpdateUserPrefs(editingUserId, {
            web_enabled: webNotify,
            email_enabled: emailNotify,
            sms_enabled: smsNotify,
            telegram_enabled: telegramNotify
          });
        } catch (err) {
          // Backend kaydetme hatasi olursa formu kapatma, kullaniciya
          // gosterelim ki tekrar denesin.
          setSubmitError(
            err instanceof Error ? err.message : t("common.errorOccurred")
          );
          return;
        }
      }
      // Tabloda yansisin diye lokal state'i de guncelle.
      setNotificationPrefs((prev) => ({
        ...prev,
        [editingUserId]: {
          web: webNotify,
          email: emailNotify,
          sms: smsNotify,
          telegram: telegramNotify
        }
      }));
      setEditingUserId(null);
      setEditUsername("");
      setEmail("");
      setFullName("");
      setPhoneNumber("");
      setRole("operator");
      setWebNotify(true);
      setEmailNotify(true);
      setSmsNotify(false);
      setTelegramNotify(false);
    } catch (error) {
      setSubmitError(error instanceof Error ? error.message : t("common.errorOccurred"));
    }
  };

  const getRoleLabel = (value: UserRead["role"]) => {
    if (value === "engineer") return t("engineering.users.roleNames.engineer");
    if (value === "installer") return t("engineering.users.roleNames.installer");
    if (value === "ops_manager") return t("engineering.users.roleNames.ops_manager");
    return t("engineering.users.roleNames.operator");
  };

  const handleDeleteClick = async (user: UserRead) => {
    const approved = await asyncConfirm(
      t("engineering.users.delete.confirm", { name: user.full_name })
    );
    if (!approved) return;
    setSubmitError("");
    try {
      await onDelete(user.id);
    } catch (error) {
      setSubmitError(error instanceof Error ? error.message : t("engineering.users.delete.fail"));
    }
  };

  const openResetPasswordModal = (user: UserRead) => {
    setPasswordResetUser(user);
    setResetPassword("");
    setResetPasswordConfirm("");
    setSubmitError("");
  };

  const filteredUsers = users.filter((user) => {
    const q = search.trim().toLowerCase();
    if (!q) return true;
    return [user.full_name, user.username, user.email, user.phone_number ?? "", getRoleLabel(user.role)]
      .join(" ")
      .toLowerCase()
      .includes(q);
  });

  const handleResetPasswordSubmit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!passwordResetUser) return;
    if (resetPassword.length < 8) {
      setSubmitError(t("engineering.users.resetPasswordMinLen"));
      return;
    }
    if (resetPassword !== resetPasswordConfirm) {
      setSubmitError(t("engineering.users.resetPasswordMismatch"));
      return;
    }
    setSubmitError("");
    try {
      await onResetPassword(passwordResetUser.id, resetPassword);
      setPasswordResetUser(null);
      setResetPassword("");
      setResetPasswordConfirm("");
    } catch (error) {
      setSubmitError(error instanceof Error ? error.message : t("engineering.users.resetPasswordFail"));
    }
  };

  return (
    <section className="tab-panel users-management-panel">
      <div className="users-toolbar">
        <input
          type="search"
          className="users-search-input"
          value={search}
          onChange={(event) => setSearch(event.target.value)}
          placeholder={t("engineering.users.searchPlaceholder", { defaultValue: "Kullanıcı ara..." })}
        />
        <button
          className="add-user-btn"
          onClick={() => {
            setSubmitError("");
            setCreateModalOpen(true);
          }}
        >
          {t("engineering.users.newUser")}
        </button>
      </div>

      {/* Davet modali */}
      {isInviteModalOpen ? (
        <div className="settings-modal-backdrop">
          <form
            className="settings-modal"
            onSubmit={async (e) => {
              e.preventDefault();
              if (!onInvite) return;
              setInviteError("");
              setInviteSubmitting(true);
              try {
                const result = await onInvite({
                  username: inviteUsername.trim(),
                  email: inviteEmail.trim(),
                  full_name: inviteFullName.trim(),
                  phone_number: invitePhone.trim() || null,
                  role: inviteRole,
                  send_email: inviteSendEmail,
                });
                if (result) {
                  setInviteResult(result);
                } else {
                  setInviteModalOpen(false);
                }
              } catch (err) {
                setInviteError(err instanceof Error ? err.message : "Davet gonderilemedi");
              } finally {
                setInviteSubmitting(false);
              }
            }}
          >
            <h3>{t("engineering.users.invite", { defaultValue: "Kullanici Davet Et" })}</h3>

            {!inviteResult ? (
              <>
                <p style={{ fontSize: "0.85rem", color: "#6b7280", marginTop: 0 }}>
                  {t("engineering.users.inviteInfo", {
                    defaultValue: "Kullanici sifre belirlemeden olusturulur. Davet linki uretilir; SMTP yapilandirilmissa otomatik mail gider, aksi halde link panelde gosterilir.",
                  })}
                </p>
                <label>
                  {t("engineering.users.form.usernameLow", { defaultValue: "Kullanici adi" })}
                  <input value={inviteUsername} onChange={(e) => setInviteUsername(e.target.value)} required minLength={3} />
                </label>
                <label>
                  {t("engineering.users.form.email", { defaultValue: "E-posta" })}
                  <input type="email" value={inviteEmail} onChange={(e) => setInviteEmail(e.target.value)} required />
                </label>
                <label>
                  {t("engineering.users.form.fullName", { defaultValue: "Ad Soyad" })}
                  <input value={inviteFullName} onChange={(e) => setInviteFullName(e.target.value)} required />
                </label>
                <label>
                  {t("engineering.users.form.phone", { defaultValue: "Telefon (opsiyonel)" })}
                  <input value={invitePhone} onChange={(e) => setInvitePhone(e.target.value)} />
                </label>
                <label>
                  {t("engineering.users.form.role", { defaultValue: "Rol" })}
                  <select value={inviteRole} onChange={(e) => setInviteRole(e.target.value as UserRole)}>
                    {!restrictToOperator ? (
                      <>
                        <option value="operator">operator</option>
                        <option value="engineer">engineer</option>
                        {allowInstallerRole ? <option value="installer">installer</option> : null}
                        <option value="ops_manager">ops_manager</option>
                      </>
                    ) : (
                      <option value="operator">operator</option>
                    )}
                  </select>
                </label>
                <label style={{ display: "flex", alignItems: "center", gap: 8 }}>
                  <input type="checkbox" checked={inviteSendEmail} onChange={(e) => setInviteSendEmail(e.target.checked)} />
                  <span>{t("engineering.users.sendEmail", { defaultValue: "E-posta ile davet linki gonder (SMTP aktif olmali)" })}</span>
                </label>
                {inviteError ? <p className="error-text">{inviteError}</p> : null}
                <div style={{ display: "flex", gap: 8, justifyContent: "flex-end", marginTop: 12 }}>
                  <button type="button" onClick={() => setInviteModalOpen(false)} disabled={inviteSubmitting}>
                    {t("common.cancel", { defaultValue: "Iptal" })}
                  </button>
                  <button type="submit" disabled={inviteSubmitting}>
                    {inviteSubmitting ? t("common.loading", { defaultValue: "Gonderiliyor..." }) : t("engineering.users.invite", { defaultValue: "Davet Et" })}
                  </button>
                </div>
              </>
            ) : (
              <>
                <p style={{ color: inviteResult.email_sent ? "#065f46" : "#92400e", background: inviteResult.email_sent ? "#d1fae5" : "#fef3c7", padding: "0.5rem 0.75rem", borderRadius: 6, fontSize: "0.875rem" }}>
                  {inviteResult.email_sent
                    ? t("engineering.users.inviteSentMail", { defaultValue: "Davet maili gonderildi. Kullanici link uzerinden sifre belirleyince hesap aktif olur." })
                    : t("engineering.users.inviteSentNoMail", { defaultValue: "Davet linki olusturuldu. SMTP aktif degil; asagidaki linki kopyalayip kullaniciya iletin." })}
                </p>
                <label>
                  {t("engineering.users.setupUrl", { defaultValue: "Setup linki (tek kullanim, 7 gun gecerli)" })}
                  <textarea readOnly value={inviteResult.setup_url} rows={3} onClick={(e) => (e.target as HTMLTextAreaElement).select()} style={{ fontFamily: "monospace", fontSize: "0.8rem" }} />
                </label>
                <div style={{ display: "flex", gap: 8, justifyContent: "flex-end", marginTop: 12 }}>
                  <button
                    type="button"
                    onClick={async () => {
                      try {
                        await navigator.clipboard.writeText(inviteResult.setup_url);
                        setInviteUrlCopied(true);
                        setTimeout(() => setInviteUrlCopied(false), 2000);
                      } catch {
                        // fallback yok; kullanici elle kopyalar
                      }
                    }}
                  >
                    {inviteUrlCopied ? t("common.copied", { defaultValue: "Kopyalandi!" }) : t("common.copyLink", { defaultValue: "Linki Kopyala" })}
                  </button>
                  <button type="button" onClick={() => { setInviteModalOpen(false); setInviteResult(null); }}>
                    {t("common.close", { defaultValue: "Kapat" })}
                  </button>
                </div>
              </>
            )}
          </form>
        </div>
      ) : null}
      {/* Modal kapalıyken (toplu işlem hataları için) panelde gösterilir.
          Modal açıkken modalin içinde de gösteriliyor. */}
      {submitError && !isCreateModalOpen && editingUserId === null && !passwordResetUser ? (
        <p className="error-text">{submitError}</p>
      ) : null}

      {isCreateModalOpen ? (
        <div className="settings-modal-backdrop">
          <form className="settings-modal" onSubmit={handleSubmit}>
            <h3>{t("engineering.users.newUserModal")}</h3>
            <label>
              {t("engineering.users.form.usernameLow")}
              <input value={username} onChange={(event) => setUsername(event.target.value)} required />
            </label>
            <label>
              {t("engineering.users.form.email")}
              <input value={email} onChange={(event) => setEmail(event.target.value)} required />
            </label>
            <label>
              {t("engineering.users.form.fullName")}
              <input value={fullName} onChange={(event) => setFullName(event.target.value)} required />
            </label>
            <label>
              {t("engineering.users.form.phone")}
              <input
                type="tel"
                value={phoneNumber}
                onChange={(event) => setPhoneNumber(event.target.value)}
                placeholder={t("engineering.users.phonePlaceholder")}
              />
            </label>
            <label>
              {t("engineering.users.form.password")}
              <input value={password} onChange={(event) => setPassword(event.target.value)} required />
            </label>
            <label>
              {t("engineering.users.form.role")}
              <select
                value={role}
                onChange={(event) => setRole(event.target.value as UserRole)}
                disabled={restrictToOperator}
              >
                <option value="operator">{t("engineering.users.roleNames.operator")}</option>
                {!restrictToOperator ? (
                  <option value="engineer">{t("engineering.users.roleNames.engineer")}</option>
                ) : null}
                {!restrictToOperator ? (
                  <option value="ops_manager">{t("engineering.users.roleNames.ops_manager")}</option>
                ) : null}
                {!restrictToOperator && allowInstallerRole ? (
                  <option value="installer">{t("engineering.users.roleNames.installerSuper")}</option>
                ) : null}
              </select>
            </label>
            <fieldset className="notify-group notify-group--4">
              <legend>{t("engineering.users.notifPrefs")}</legend>
              <p className="rule-hint" style={{ margin: "0 0 8px" }}>
                {t("engineering.users.notifPrefsHint")}
              </p>
              <label className="notify-option">
                <input type="checkbox" checked={webNotify} onChange={(event) => setWebNotify(event.target.checked)} />
                <span className="material-symbols-outlined">notifications</span>
                {t("engineering.users.notifWeb")}
              </label>
              <label className="notify-option">
                <input type="checkbox" checked={emailNotify} onChange={(event) => setEmailNotify(event.target.checked)} />
                <span className="material-symbols-outlined">mail</span>
                {t("engineering.users.notifEmail")}
              </label>
              <label className="notify-option">
                <input type="checkbox" checked={smsNotify} onChange={(event) => setSmsNotify(event.target.checked)} />
                <span className="material-symbols-outlined">sms</span>
                {t("engineering.users.notifSms")}
              </label>
              <label className="notify-option">
                <input type="checkbox" checked={telegramNotify} onChange={(event) => setTelegramNotify(event.target.checked)} />
                <span className="material-symbols-outlined">send</span>
                {t("engineering.users.notifTelegram")}
              </label>
            </fieldset>
            {submitError ? <p className="error-text modal-error">{submitError}</p> : null}
            <div className="settings-actions">
              <button
                type="button"
                onClick={() => {
                  setCreateModalOpen(false);
                  setSubmitError("");
                }}
              >
                {t("engineering.users.cancel")}
              </button>
              <button type="submit">{t("engineering.users.saveBtn")}</button>
            </div>
          </form>
        </div>
      ) : null}

      {editingUserId !== null ? (
        <div className="settings-modal-backdrop">
          <form className="settings-modal" onSubmit={handleEditSubmit}>
            <h3>{t("engineering.users.editUserModal")}</h3>
            <label>
              {t("engineering.users.form.username")}
              <input value={editUsername} disabled readOnly />
            </label>
            <label>
              {t("engineering.users.form.email")}
              <input value={email} onChange={(event) => setEmail(event.target.value)} required />
            </label>
            <label>
              {t("engineering.users.form.fullName")}
              <input value={fullName} onChange={(event) => setFullName(event.target.value)} required />
            </label>
            <label>
              {t("engineering.users.form.phone")}
              <input
                type="tel"
                value={phoneNumber}
                onChange={(event) => setPhoneNumber(event.target.value)}
                placeholder={t("engineering.users.phonePlaceholder")}
              />
            </label>
            <label>
              {t("engineering.users.form.role")}
              <select
                value={role}
                onChange={(event) => setRole(event.target.value as UserRole)}
                disabled={restrictToOperator}
              >
                <option value="operator">{t("engineering.users.roleNames.operator")}</option>
                {!restrictToOperator ? (
                  <option value="engineer">{t("engineering.users.roleNames.engineer")}</option>
                ) : null}
                {!restrictToOperator ? (
                  <option value="ops_manager">{t("engineering.users.roleNames.ops_manager")}</option>
                ) : null}
                {!restrictToOperator && allowInstallerRole ? (
                  <option value="installer">{t("engineering.users.roleNames.installerSuper")}</option>
                ) : null}
              </select>
            </label>
            <fieldset className="notify-group notify-group--4">
              <legend>{t("engineering.users.notifPrefs")}</legend>
              <p className="rule-hint" style={{ margin: "0 0 8px" }}>
                {t("engineering.users.notifPrefsHint")}
              </p>
              <label className="notify-option">
                <input type="checkbox" checked={webNotify} onChange={(event) => setWebNotify(event.target.checked)} />
                <span className="material-symbols-outlined">notifications</span>
                {t("engineering.users.notifWeb")}
              </label>
              <label className="notify-option">
                <input type="checkbox" checked={emailNotify} onChange={(event) => setEmailNotify(event.target.checked)} />
                <span className="material-symbols-outlined">mail</span>
                {t("engineering.users.notifEmail")}
              </label>
              <label className="notify-option">
                <input type="checkbox" checked={smsNotify} onChange={(event) => setSmsNotify(event.target.checked)} />
                <span className="material-symbols-outlined">sms</span>
                {t("engineering.users.notifSms")}
              </label>
              <label className="notify-option">
                <input type="checkbox" checked={telegramNotify} onChange={(event) => setTelegramNotify(event.target.checked)} />
                <span className="material-symbols-outlined">send</span>
                {t("engineering.users.notifTelegram")}
              </label>
            </fieldset>
            {submitError ? <p className="error-text modal-error">{submitError}</p> : null}
            <div className="settings-actions">
              <button type="button" onClick={() => setEditingUserId(null)}>
                {t("engineering.users.cancel")}
              </button>
              <button type="submit">{t("engineering.users.updateBtn")}</button>
            </div>
          </form>
        </div>
      ) : null}

      {passwordResetUser ? (
        <div className="settings-modal-backdrop">
          <form className="settings-modal" onSubmit={handleResetPasswordSubmit}>
            <h3>{t("engineering.users.resetPasswordModal")}</h3>
            <p className="helper-text">{t("engineering.users.resetPasswordHint", { name: passwordResetUser.full_name })}</p>
            <label>
              {t("engineering.users.form.newPassword")}
              <input
                type="password"
                value={resetPassword}
                onChange={(event) => setResetPassword(event.target.value)}
                minLength={8}
                required
              />
            </label>
            <label>
              {t("engineering.users.newPasswordRepeat")}
              <input
                type="password"
                value={resetPasswordConfirm}
                onChange={(event) => setResetPasswordConfirm(event.target.value)}
                minLength={8}
                required
              />
            </label>
            {submitError ? <p className="error-text modal-error">{submitError}</p> : null}
            <div className="settings-actions">
              <button type="button" onClick={() => setPasswordResetUser(null)}>
                {t("engineering.users.cancel")}
              </button>
              <button type="submit">{t("engineering.users.resetBtn")}</button>
            </div>
          </form>
        </div>
      ) : null}

      <div className="users-table-wrap">
        <table className="values-table user-table">
        <thead>
          <tr>
            <th scope="col">{t("engineering.users.table.fullName")}</th>
            <th scope="col">{t("engineering.users.table.username")}</th>
            <th scope="col">{t("engineering.users.table.role")}</th>
            <th scope="col">{t("engineering.users.table.email")}</th>
            <th scope="col">{t("engineering.users.table.phone")}</th>
            <th scope="col" className="notify-col">{t("engineering.users.notifCols.web")}</th>
            <th scope="col" className="notify-col">{t("engineering.users.notifCols.email")}</th>
            <th scope="col" className="notify-col">{t("engineering.users.notifCols.sms")}</th>
            <th scope="col" className="notify-col">{t("engineering.users.notifCols.telegram")}</th>
            <th scope="col" className="actions-header">{t("engineering.users.table.actions")}</th>
          </tr>
        </thead>
        <tbody>
          {filteredUsers.map((user) => {
            const prefs =
              notificationPrefs[user.id] ?? { web: true, email: true, sms: false, telegram: false };
            const cell = (on: boolean, channel: "web" | "email" | "sms" | "telegram") => (
              <td className="notify-cell">
                <span
                  className={`notif-dot ${on ? "is-on" : "is-off"}`}
                  title={
                    on
                      ? t("engineering.users.notifOn")
                      : t("engineering.users.notifOff")
                  }
                  aria-label={t(`engineering.users.notifAria.${channel}`, {
                    state: on
                      ? t("engineering.users.notifOn")
                      : t("engineering.users.notifOff"),
                    defaultValue: on ? "On" : "Off"
                  })}
                >
                  <span className="material-symbols-outlined">
                    {on ? "check_circle" : "do_disturb_on"}
                  </span>
                </span>
              </td>
            );
            return (
            <tr key={user.id}>
              <td>{user.full_name}</td>
              <td>{user.username}</td>
              <td>{getRoleLabel(user.role)}</td>
              <td>{user.email}</td>
              <td>{user.phone_number ?? "-"}</td>
              {cell(prefs.web, "web")}
              {cell(prefs.email, "email")}
              {cell(prefs.sms, "sms")}
              {cell(prefs.telegram, "telegram")}
              <td className="actions-cell">
                <button className="edit-btn action-btn" onClick={() => startEdit(user)}>
                  {t("engineering.users.edit")}
                </button>
                <button className="secondary-btn action-btn" onClick={() => openResetPasswordModal(user)}>
                  {t("engineering.users.resetPwd")}
                </button>
                <button
                  className="danger-btn action-btn"
                  onClick={() => void handleDeleteClick(user)}
                  disabled={currentUserId === user.id}
                  title={currentUserId === user.id ? t("engineering.users.selfDeleteHint") : t("engineering.users.deleteTooltip")}
                >
                  {t("common.delete")}
                </button>
              </td>
            </tr>
            );
          })}
          {filteredUsers.length === 0 ? (
            <tr>
              <td colSpan={10} className="helper-text users-empty-cell">
                {t("engineering.users.noSearchResults", { defaultValue: "Aramaya uygun kullanıcı yok." })}
              </td>
            </tr>
          ) : null}
        </tbody>
      </table>
      </div>
    </section>
  );
}
