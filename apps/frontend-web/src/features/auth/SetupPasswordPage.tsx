import React, { useState } from "react";
import { useTranslation } from "react-i18next";

import { setupPassword } from "../../shared/api";

/**
 * SetupPasswordPage — Davet edilmis kullanici ilk sifresini token ile belirler.
 *
 * URL: `/setup-password?token=<raw-token>`
 *
 * Auth gerekmez (token zaten secret). Token rate-limit 5/dk korumali; 7 gun TTL.
 * Basari sonrasi kullanici login sayfasina yonlendirilir.
 */

export function SetupPasswordPage() {
  const { t } = useTranslation();
  // URLSearchParams ile basit ?token= okuma — router'a bagimlilik yok.
  const params = new URLSearchParams(window.location.search);
  const token = (params.get("token") || "").trim();

  const [pwd, setPwd] = useState("");
  const [confirmPwd, setConfirmPwd] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState(false);
  const [busy, setBusy] = useState(false);

  if (!token) {
    return (
      <PageShell title={t("setupPassword.invalidTitle")}>
        <p style={{ color: "#991b1b" }}>
          {t("setupPassword.invalidBody")}
        </p>
      </PageShell>
    );
  }

  if (success) {
    return (
      <PageShell title={t("setupPassword.doneTitle")}>
        <p style={{ color: "#065f46" }}>
          {t("setupPassword.doneBody")}
        </p>
        <a
          href="/"
          style={{ display: "inline-block", marginTop: "1rem", padding: "0.5rem 1rem", background: "#2563eb", color: "#fff", borderRadius: 6, textDecoration: "none" }}
        >
          {t("setupPassword.goLogin")}
        </a>
      </PageShell>
    );
  }

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    if (pwd.length < 8) {
      setError(t("setupPassword.tooShort"));
      return;
    }
    if (pwd !== confirmPwd) {
      setError(t("setupPassword.mismatch"));
      return;
    }
    setBusy(true);
    try {
      await setupPassword(token, pwd);
      setSuccess(true);
    } catch (err) {
      setError(err instanceof Error ? err.message : t("common.errorOccurred"));
    } finally {
      setBusy(false);
    }
  };

  return (
    <PageShell title={t("setupPassword.title")}>
      <p style={{ color: "#6b7280", marginBottom: "1.5rem", fontSize: "0.875rem" }}>
        {t("setupPassword.intro")}
      </p>
      <form onSubmit={submit}>
        <label style={{ display: "block", marginBottom: "0.75rem" }}>
          <span style={{ display: "block", fontSize: "0.875rem", color: "#374151", marginBottom: 4 }}>
            {t("setupPassword.newPassword")}
          </span>
          <input
            type="password"
            required
            minLength={8}
            autoFocus
            autoComplete="new-password"
            value={pwd}
            onChange={(e) => setPwd(e.target.value)}
            style={{ width: "100%", padding: "0.5rem 0.75rem", border: "1px solid #d1d5db", borderRadius: 6, fontSize: "0.875rem" }}
          />
        </label>
        <label style={{ display: "block", marginBottom: "1rem" }}>
          <span style={{ display: "block", fontSize: "0.875rem", color: "#374151", marginBottom: 4 }}>
            {t("setupPassword.confirmPassword")}
          </span>
          <input
            type="password"
            required
            minLength={8}
            autoComplete="new-password"
            value={confirmPwd}
            onChange={(e) => setConfirmPwd(e.target.value)}
            style={{ width: "100%", padding: "0.5rem 0.75rem", border: "1px solid #d1d5db", borderRadius: 6, fontSize: "0.875rem" }}
          />
        </label>
        {error ? (
          <p role="alert" style={{ color: "#dc2626", fontSize: "0.875rem", marginBottom: "0.75rem" }}>
            {error}
          </p>
        ) : null}
        <button
          type="submit"
          disabled={busy}
          style={{ width: "100%", padding: "0.625rem 1rem", background: busy ? "#9ca3af" : "#2563eb", color: "#fff", border: "none", borderRadius: 6, cursor: busy ? "default" : "pointer", fontSize: "0.875rem", fontWeight: 500 }}
        >
          {busy ? t("setupPassword.submitting") : t("setupPassword.submit")}
        </button>
      </form>
    </PageShell>
  );
}

function PageShell({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div
      style={{
        minHeight: "100vh",
        background: "#f3f4f6",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        padding: "1rem",
        fontFamily: "system-ui, -apple-system, sans-serif",
      }}
    >
      <div style={{ background: "#fff", borderRadius: 8, padding: "2rem", maxWidth: 440, width: "100%", boxShadow: "0 10px 40px rgba(0,0,0,0.1)" }}>
        <h1 style={{ margin: 0, marginBottom: "1rem", fontSize: "1.5rem", color: "#1f2937" }}>{title}</h1>
        {children}
      </div>
    </div>
  );
}
