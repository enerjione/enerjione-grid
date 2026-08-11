/**
 * Profil sayfasi (ust sagdaki kullanici menusu > Profil).
 *
 * ONCEDEN MODALDI ve iki ayri isi TEK "Kaydet" dugmesine bagliyordu:
 * once `PATCH /auth/me` (ad + e-posta), sonra `POST /auth/me/change-password`.
 * Bu duzenin uc somut arizasi vardi:
 *
 *   1. PROFIL HATASI SIFREYI ENGELLIYORDU. Ilk cagri patlarsa ikinci cagri
 *      HIC yapilmiyordu; yalnizca sifresini degistirmek isteyen kullanici
 *      "Profil guncellenemedi" hatasi aliyordu.
 *   2. EKSIK ALAN SESSIZCE GECIYORDU. Kullanici yalnizca "yeni sifre"yi
 *      doldurursa hicbir cagri yapilmiyor, modal "kaydedildi" gibi kapaniyor,
 *      kullanici sifresini degistirdigini SANIYORDU.
 *   3. GERCEK HATA GIZLENIYORDU. Backend'in sebebi ("Mevcut sifre yanlis",
 *      hiz siniri) ekrana hic ulasmiyordu.
 *
 * Bu yuzden sayfa BAGIMSIZ KARTLAR: her birinin kendi kaydet dugmesi ve kendi
 * geri bildirimi var. Bir bolumun hatasi digerini bloklamaz.
 *
 * BU SURUMDE EKLENENLER
 * ---------------------
 * - TELEFON NUMARASI. Modelde ve admin panelinde vardi ama kullanici kendi
 *   kaydinda degistiremiyordu; bildirim tercihleri "SMS: telefon numarasi
 *   eklenmemis" yazip numarayi girebilecegi hicbir yer sunmuyordu.
 * - PROFIL FOTOGRAFI. Tarayicida 192 piksele kucultulup gomulur (bkz.
 *   avatarImage.ts) — sunucuda dosya deposu gerektirmez.
 * - SIFREYI GOSTER. Yazilan sifre gorulemedigi icin yanlis yazim ancak
 *   "mevcut sifre yanlis" hatasindan sonra anlasiliyordu. Ayrica kurallar
 *   YAZARKEN isaretleniyor (bkz. passwordStrength.ts).
 */
import { useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import {
  BellRing,
  Camera,
  Check,
  Eye,
  EyeOff,
  KeyRound,
  Mail,
  Phone,
  ShieldCheck,
  Trash2,
  UserCog,
  X
} from "lucide-react";

import { useToast } from "../../components/ToastProvider";
import { changeMyPassword, updateMyProfile } from "../../shared/api";
import type { SupportedLanguage } from "../../shared/i18n";
import { LANGUAGE_LABELS, SUPPORTED_LANGUAGES, isSupportedLanguage } from "../../shared/i18n";
import type { UserNotificationPreferences, UserRead } from "../../shared/types";
import { AVATAR_ACCEPT, AvatarError, fileToAvatarDataUrl, initialsOf } from "./avatarImage";
import { MIN_PASSWORD_LENGTH, passwordStrength } from "./passwordStrength";
import type { PasswordRuleKey } from "./passwordStrength";

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

const PASSWORD_RULES: PasswordRuleKey[] = ["length", "letter", "digit", "symbol"];

/** Gozu acilip kapanan sifre alani. */
function PasswordField({
  label,
  value,
  onChange,
  autoComplete,
  showLabel,
  hideLabel
}: {
  label: string;
  value: string;
  onChange: (v: string) => void;
  autoComplete: string;
  showLabel: string;
  hideLabel: string;
}) {
  const [gorunur, setGorunur] = useState(false);
  return (
    <div className="rad-field">
      <span className="rad-field-label">{label}</span>
      <div className="pf-secret">
        <input
          type={gorunur ? "text" : "password"}
          autoComplete={autoComplete}
          value={value}
          onChange={(e) => onChange(e.target.value)}
        />
        <button
          type="button"
          className="pf-secret-eye"
          onClick={() => setGorunur((v) => !v)}
          aria-label={gorunur ? hideLabel : showLabel}
          title={gorunur ? hideLabel : showLabel}
        >
          {gorunur ? <EyeOff size={15} strokeWidth={2.1} /> : <Eye size={15} strokeWidth={2.1} />}
        </button>
      </div>
    </div>
  );
}

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
  const [phone, setPhone] = useState("");
  const [avatar, setAvatar] = useState<string | null>(null);
  const [profileSaving, setProfileSaving] = useState(false);
  const [profileError, setProfileError] = useState("");
  const fileRef = useRef<HTMLInputElement | null>(null);

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
    setPhone(currentUser.phone_number ?? "");
    setAvatar(currentUser.avatar_url ?? null);
  }, [currentUser]);

  const profileDirty =
    currentUser !== null &&
    (fullName !== (currentUser.full_name ?? "") ||
      email !== (currentUser.email ?? "") ||
      phone !== (currentUser.phone_number ?? "") ||
      (avatar ?? null) !== (currentUser.avatar_url ?? null));

  const strength = useMemo(() => passwordStrength(newPassword), [newPassword]);

  const handlePickAvatar = async (file: File | undefined) => {
    if (!file) return;
    setProfileError("");
    try {
      setAvatar(
        await fileToAvatarDataUrl(file, {
          tooBig: t("userSettings.avatar.tooBig"),
          notImage: t("userSettings.avatar.notImage"),
          failed: t("userSettings.avatar.failed")
        })
      );
    } catch (err) {
      setProfileError(
        err instanceof AvatarError ? err.message : t("userSettings.avatar.failed")
      );
    } finally {
      // Ayni dosya tekrar secilebilsin (change olayi degismeyen degerde atmaz).
      if (fileRef.current) fileRef.current.value = "";
    }
  };

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
        email: email.trim(),
        // Bos metin backend'de "temizle" demek; ikisi de her kaydetmede
        // gonderilir (bkz. updateMyProfile).
        phone_number: phone.trim() || null,
        avatar_url: avatar
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

  const rolLabel = currentUser
    ? t(`roles.${currentUser.role}`, { defaultValue: currentUser.role })
    : "—";

  return (
    <section className="tab-panel profile-page">
      {/* ---- KIMLIK SERIDI ----
          Fotograf, ad ve rol tek bir seritte: sayfanin "bu benim hesabim"
          diyen kismi. Onceden hicbir yerde kullanicinin kendisi gorunmuyor,
          sayfa iki form kutusundan ibaretti. */}
      <header className="pf-hero">
        <div className="pf-hero-avatar">
          <button
            type="button"
            className="pf-avatar"
            onClick={() => fileRef.current?.click()}
            title={t("userSettings.avatar.change")}
          >
            {avatar ? (
              <img src={avatar} alt="" />
            ) : (
              <span className="pf-avatar-initials">{initialsOf(fullName, "?")}</span>
            )}
            <span className="pf-avatar-overlay">
              <Camera size={18} strokeWidth={2.2} />
            </span>
          </button>
          <input
            ref={fileRef}
            type="file"
            accept={AVATAR_ACCEPT}
            hidden
            onChange={(e) => void handlePickAvatar(e.target.files?.[0])}
          />
          <div className="pf-avatar-actions">
            <button type="button" className="pf-mini-btn" onClick={() => fileRef.current?.click()}>
              <Camera size={13} strokeWidth={2.2} />
              {t("userSettings.avatar.change")}
            </button>
            {avatar ? (
              <button
                type="button"
                className="pf-mini-btn pf-mini-btn--danger"
                onClick={() => setAvatar(null)}
              >
                <Trash2 size={13} strokeWidth={2.2} />
                {t("userSettings.avatar.remove")}
              </button>
            ) : null}
          </div>
        </div>

        <div className="pf-hero-id">
          <h2>{fullName || currentUser?.username || "—"}</h2>
          <div className="pf-hero-meta">
            <span className="pf-chip pf-chip--role">{rolLabel}</span>
            <span className="pf-hero-user">@{currentUser?.username ?? "—"}</span>
            {currentUser?.email ? (
              <span className="pf-hero-item">
                <Mail size={13} strokeWidth={2.2} />
                {currentUser.email}
              </span>
            ) : null}
            <span className={`pf-hero-item${currentUser?.phone_number ? "" : " is-missing"}`}>
              <Phone size={13} strokeWidth={2.2} />
              {currentUser?.phone_number || t("userSettings.phoneMissing")}
            </span>
          </div>
        </div>
      </header>

      <div className="profile-page-columns">
        {/* ---- Kimlik bilgileri ---- */}
        <section className="rad-card profile-card">
          <header className="rad-card-head">
            <h3>
              <UserCog size={17} />
              {t("userSettings.profile")}
            </h3>
            <small>{t("userSettings.profileHint")}</small>
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

          {/* TELEFON: SMS ve WhatsApp bildirimlerinin tek kaynagi. Alan
              olmadigi icin kullanici bu iki kanali hic acamiyordu. */}
          <label className="rad-field">
            <span className="rad-field-label">{t("common.phone")}</span>
            <input
              type="tel"
              value={phone}
              onChange={(event) => setPhone(event.target.value)}
              placeholder="+90 555 123 45 67"
            />
            <small className="rad-field-hint">{t("userSettings.phoneHint")}</small>
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

          <PasswordField
            label={t("userSettings.currentPassword")}
            value={currentPassword}
            onChange={setCurrentPassword}
            autoComplete="current-password"
            showLabel={t("userSettings.showPassword")}
            hideLabel={t("userSettings.hidePassword")}
          />

          <PasswordField
            label={t("userSettings.newPassword")}
            value={newPassword}
            onChange={setNewPassword}
            autoComplete="new-password"
            showLabel={t("userSettings.showPassword")}
            hideLabel={t("userSettings.hidePassword")}
          />

          {/* GUC GOSTERGESI: kurallar YAZARKEN isaretlenir. Once yalnizca
              gonderdikten sonra hata gorunuyordu. */}
          {newPassword ? (
            <div className={`pf-strength pf-strength--${strength.level}`}>
              <div className="pf-strength-bar" aria-hidden="true">
                {[0, 1, 2, 3].map((i) => (
                  <span key={i} className={i < strength.score ? "is-on" : ""} />
                ))}
              </div>
              <span className="pf-strength-label">
                {t(`userSettings.strength.${strength.level}`)}
              </span>
              <ul className="pf-rules">
                {PASSWORD_RULES.map((key) => (
                  <li key={key} className={strength.rules[key] ? "is-ok" : ""}>
                    {strength.rules[key] ? (
                      <Check size={11} strokeWidth={3} />
                    ) : (
                      <X size={11} strokeWidth={3} />
                    )}
                    {t(`userSettings.rules.${key}`, { min: MIN_PASSWORD_LENGTH })}
                  </li>
                ))}
              </ul>
            </div>
          ) : null}

          <PasswordField
            label={t("userSettings.confirmPassword")}
            value={repeatPassword}
            onChange={setRepeatPassword}
            autoComplete="new-password"
            showLabel={t("userSettings.showPassword")}
            hideLabel={t("userSettings.hidePassword")}
          />
          {repeatPassword && newPassword !== repeatPassword ? (
            <p className="pf-inline-warn">{t("userSettings.errors.repeatMismatch")}</p>
          ) : null}

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
              <BellRing size={17} />
              {t("userSettings.notifPrefs.title")}
            </h3>
            <small>{t("userSettings.notifPrefs.autoSaveHint")}</small>
          </header>

          <div className="profile-notif-grid">
            {notifRows.map((row) => (
              <div
                className={`notif-prefs-row${notifPrefs[row.key] ? " is-on" : ""}`}
                key={row.key}
              >
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
