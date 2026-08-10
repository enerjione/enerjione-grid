/**
 * Profil sayfasi (ust sagdaki kullanici menusu > Profil).
 *
 * ONCEDEN MODALDI ve iki ayri isi TEK "Kaydet" dugmesine bagliyordu:
 * once `PATCH /auth/me` (ad + e-posta), sonra `POST /auth/me/change-password`.
 * Bu duzenin uc somut arizasi vardi:
 *
 *   1. PROFIL HATASI SIFREYI ENGELLIYORDU. Ilk cagri patlarsa (or. e-posta
 *      alani bos/gecersiz -> 422, ya da baskasinda kayitli -> 409) ikinci
 *      cagri HIC yapilmiyordu. Yalnizca sifresini degistirmek isteyen
 *      kullanici "Profil guncellenemedi" hatasi aliyor ve sifresinin neden
 *      degismedigini anlamiyordu.
 *   2. EKSIK ALAN SESSIZCE GECIYORDU. Kosul `mevcut && yeni` idi; kullanici
 *      yalnizca "yeni sifre"yi doldurursa hicbir cagri yapilmiyor, modal
 *      "kaydedildi" gibi kapaniyordu. Kullanici sifresini degistirdigini
 *      SANIYOR, bir sonraki giriste eski sifre calisiyordu.
 *   3. GERCEK HATA GIZLENIYORDU. API yardimcilari sabit metin firlatiyordu
 *      ("Sifre degistirilemedi."); backend'in soyledigi sebep ("Mevcut sifre
 *      yanlis", "Yeni sifre eskisiyle ayni olamaz", 429 hiz siniri) ekrana
 *      hic ulasmiyordu.
 *
 * Bu yuzden sayfa UC BAGIMSIZ KART: her birinin kendi kaydet dugmesi ve kendi
 * geri bildirimi var. Bir bolumun hatasi digerini bloklamaz.
 *
 * Gorsel dil: diger muhendislik sekmeleriyle ayni (`rad-card`, `net-banner`).
 */
import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { KeyRound, Mail, ShieldCheck, UserCog } from "lucide-react";

import { useToast } from "../../components/ToastProvider";
import { changeMyPassword, updateMyProfile } from "../../shared/api";
import type { SupportedLanguage } from "../../shared/i18n";
import { LANGUAGE_LABELS, SUPPORTED_LANGUAGES, isSupportedLanguage } from "../../shared/i18n";
import type { UserNotificationPreferences, UserRead } from "../../shared/types";

/** Bu sayfada anahtari olan kanallar. Tipi YERELDE tanimlamak yerine
 *  `UserNotificationPreferences`ten turetiyoruz: alan adlari orada
 *  degisirse burasi derlenmez, sessizce ayrismaz. */
type NotifPrefs = UserNotificationPreferences;
type NotifChannel = "web_enabled" | "email_enabled" | "sms_enabled" | "whatsapp_web_enabled";

type Props = {
  accessToken: string;
  currentUser: UserRead | null;
  onUserUpdated: (user: UserRead) => void;
  language: string | null | undefined;
  onChangeLanguage: (code: SupportedLanguage) => void | Promise<void>;
  notifPrefs: NotifPrefs | null;
  notifPrefsSaving: boolean;
  onToggleNotifPref: (key: NotifChannel) => void | Promise<void>;
};

/** Asgari sifre uzunlugu — backend `SelfPasswordChangeRequest` bir sinir
 *  dayatmiyor; bu istemci tarafi bir NEZAKET kontrolu, guvenlik siniri degil.
 *  Amaci sunucuya bos/tek karakterlik istek gondermemek ve kullaniciyi
 *  gonderdikten SONRA degil, yazarken uyarmak. */
const MIN_PASSWORD_LENGTH = 8;

export function ProfilePage({
  accessToken,
  currentUser,
  onUserUpdated,
  language,
  onChangeLanguage,
  notifPrefs,
  notifPrefsSaving,
  onToggleNotifPref
}: Props) {
  const { t } = useTranslation();
  const toast = useToast();

  // ---- Kart 1: kimlik bilgileri ----
  const [fullName, setFullName] = useState("");
  const [email, setEmail] = useState("");
  const [profileSaving, setProfileSaving] = useState(false);
  const [profileError, setProfileError] = useState("");

  // ---- Kart 2: sifre ----
  const [currentPassword, setCurrentPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [repeatPassword, setRepeatPassword] = useState("");
  const [passwordSaving, setPasswordSaving] = useState(false);
  const [passwordError, setPasswordError] = useState("");

  useEffect(() => {
    if (!currentUser) return;
    setFullName(currentUser.full_name ?? "");
    setEmail(currentUser.email ?? "");
  }, [currentUser]);

  const profileDirty =
    currentUser !== null &&
    (fullName !== (currentUser.full_name ?? "") || email !== (currentUser.email ?? ""));

  const handleSaveProfile = async () => {
    setProfileError("");
    if (!fullName.trim()) {
      setProfileError(t("userSettings.errors.fullNameRequired"));
      return;
    }
    if (!email.trim()) {
      setProfileError(t("userSettings.errors.emailRequired"));
      return;
    }
    setProfileSaving(true);
    try {
      const updated = await updateMyProfile(accessToken, {
        full_name: fullName.trim(),
        email: email.trim()
      });
      onUserUpdated(updated);
      toast.success(t("userSettings.profileSaved"));
    } catch (err) {
      setProfileError(err instanceof Error ? err.message : t("userSettings.errors.profileSave"));
    } finally {
      setProfileSaving(false);
    }
  };

  const handleChangePassword = async () => {
    setPasswordError("");
    // Eksik alanda SESSIZ GECME YOK — eski davranistaki en sinsi hata buydu.
    if (!currentPassword) {
      setPasswordError(t("userSettings.errors.currentPasswordRequired"));
      return;
    }
    if (!newPassword) {
      setPasswordError(t("userSettings.errors.newPasswordRequired"));
      return;
    }
    if (newPassword.length < MIN_PASSWORD_LENGTH) {
      setPasswordError(t("userSettings.errors.tooShort", { min: MIN_PASSWORD_LENGTH }));
      return;
    }
    if (newPassword !== repeatPassword) {
      setPasswordError(t("userSettings.errors.repeatMismatch"));
      return;
    }
    if (newPassword === currentPassword) {
      setPasswordError(t("userSettings.errors.sameAsOld"));
      return;
    }
    setPasswordSaving(true);
    try {
      await changeMyPassword(accessToken, {
        current_password: currentPassword,
        new_password: newPassword
      });
      setCurrentPassword("");
      setNewPassword("");
      setRepeatPassword("");
      toast.success(t("userSettings.passwordSaved"));
    } catch (err) {
      // Backend'in gercek sebebi ("Mevcut sifre yanlis", hiz siniri, ...)
      // artik oldugu gibi gosteriliyor.
      setPasswordError(err instanceof Error ? err.message : t("userSettings.errors.passwordSave"));
    } finally {
      setPasswordSaving(false);
    }
  };

  const notifRows: { key: NotifChannel; label: string; hint: string; missingText?: string }[] = [
    {
      key: "web_enabled",
      label: t("userSettings.notifPrefs.web"),
      hint: t("userSettings.notifPrefs.webHint")
    },
    {
      key: "email_enabled",
      label: t("userSettings.notifPrefs.email"),
      hint: t("userSettings.notifPrefs.emailHint"),
      missingText: currentUser?.email ? "" : t("userSettings.notifPrefs.emailMissing")
    },
    {
      key: "sms_enabled",
      label: t("userSettings.notifPrefs.sms"),
      hint: t("userSettings.notifPrefs.smsHint"),
      missingText: currentUser?.phone_number ? "" : t("userSettings.notifPrefs.smsMissing")
    },
    {
      key: "whatsapp_web_enabled",
      label: t("userSettings.notifPrefs.whatsapp"),
      hint: t("userSettings.notifPrefs.whatsappHint"),
      missingText: currentUser?.phone_number ? "" : t("userSettings.notifPrefs.whatsappMissing")
    }
  ];

  return (
    <section className="tab-panel profile-page">
      <div className="profile-page-columns">
        {/* ---- Kimlik bilgileri ---- */}
        <section className="rad-card profile-card">
          <header className="rad-card-head">
            <h3>
              <UserCog size={17} />
              {t("userSettings.profile")}
            </h3>
            <small>{currentUser?.username ?? "—"}</small>
          </header>

          <label className="rad-field">
            <span className="rad-field-label">{t("common.fullName")}</span>
            <input value={fullName} onChange={(event) => setFullName(event.target.value)} />
          </label>

          <label className="rad-field">
            <span className="rad-field-label">{t("common.email")}</span>
            <input
              type="email"
              value={email}
              onChange={(event) => setEmail(event.target.value)}
              placeholder="ad.soyad@firma.com"
            />
          </label>

          <label className="rad-field">
            <span className="rad-field-label">{t("userSettings.language")}</span>
            <select
              value={isSupportedLanguage(language) ? language : "tr"}
              onChange={(event) => void onChangeLanguage(event.target.value as SupportedLanguage)}
            >
              {SUPPORTED_LANGUAGES.map((code) => (
                <option key={code} value={code}>
                  {LANGUAGE_LABELS[code]}
                </option>
              ))}
            </select>
          </label>

          {profileError ? (
            <p className="net-banner net-banner--bad">
              <Mail size={16} />
              {profileError}
            </p>
          ) : null}

          <div className="profile-card-actions">
            <button
              type="button"
              className="primary-btn"
              onClick={() => void handleSaveProfile()}
              disabled={profileSaving || !profileDirty}
            >
              {profileSaving ? t("userSettings.actions.saving") : t("userSettings.saveProfile")}
            </button>
          </div>
        </section>

        {/* ---- Sifre ----
            Kendi kaydet dugmesi VAR: kimlik bilgileri kaydedilemese bile
            sifre degistirilebilmeli (eski modalde birbirine bagliydi). */}
        <section className="rad-card profile-card">
          <header className="rad-card-head">
            <h3>
              <KeyRound size={17} />
              {t("userSettings.security")}
            </h3>
            <small>{t("userSettings.passwordHint", { min: MIN_PASSWORD_LENGTH })}</small>
          </header>

          <label className="rad-field">
            <span className="rad-field-label">{t("userSettings.currentPassword")}</span>
            <input
              type="password"
              autoComplete="current-password"
              value={currentPassword}
              onChange={(event) => setCurrentPassword(event.target.value)}
            />
          </label>

          <label className="rad-field">
            <span className="rad-field-label">{t("userSettings.newPassword")}</span>
            <input
              type="password"
              autoComplete="new-password"
              value={newPassword}
              onChange={(event) => setNewPassword(event.target.value)}
            />
          </label>

          <label className="rad-field">
            <span className="rad-field-label">{t("userSettings.confirmPassword")}</span>
            <input
              type="password"
              autoComplete="new-password"
              value={repeatPassword}
              onChange={(event) => setRepeatPassword(event.target.value)}
            />
          </label>

          {passwordError ? (
            <p className="net-banner net-banner--bad">
              <ShieldCheck size={16} />
              {passwordError}
            </p>
          ) : null}

          <div className="profile-card-actions">
            <button
              type="button"
              className="primary-btn"
              onClick={() => void handleChangePassword()}
              disabled={passwordSaving}
            >
              {passwordSaving ? t("userSettings.actions.saving") : t("userSettings.savePassword")}
            </button>
          </div>
        </section>
      </div>

      {/* ---- Bildirim tercihleri ----
          Anahtarlar ANINDA kaydedilir (kaydet dugmesi yok); bu yuzden ayri
          bir kart ve tam genislikte. */}
      {notifPrefs ? (
        <section className="rad-card profile-card profile-card--wide">
          <header className="rad-card-head">
            <h3>
              <Mail size={17} />
              {t("userSettings.notifPrefs.title")}
            </h3>
            <small>{t("userSettings.notifPrefs.autoSaveHint")}</small>
          </header>

          <div className="profile-notif-grid">
            {notifRows.map((row) => (
              <div className="notif-prefs-row" key={row.key}>
                <div className="notif-prefs-row-label">
                  <strong>{row.label}</strong>
                  <span>
                    {row.hint}
                    {row.missingText ?? ""}
                  </span>
                </div>
                <button
                  type="button"
                  className={`notif-prefs-toggle ${notifPrefs[row.key] ? "on" : ""}`}
                  onClick={() => void onToggleNotifPref(row.key)}
                  disabled={notifPrefsSaving}
                  aria-label={row.label}
                  aria-pressed={Boolean(notifPrefs[row.key])}
                />
              </div>
            ))}
          </div>
        </section>
      ) : null}
    </section>
  );
}
