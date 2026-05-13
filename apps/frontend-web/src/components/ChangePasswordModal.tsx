import React, { useState } from "react";

import { API_BASE_URL } from "../shared/api";

/**
 * ChangePasswordModal — Backend must_change_password=true dondurduyse
 * kullaniciya zorla goster. ESC ve backdrop tikla KAPATAMAZ (forceful=true);
 * kullanici sadece "Sifreyi degistir" basari oldugunda kapanir.
 *
 * Davranis:
 *   - current_password + new_password + confirm_password
 *   - Yeni sifre minimum 8 karakter + ayni eski sifre olamaz
 *   - POST /auth/me/change-password
 *   - 204 ise onSuccess() cagrilir (App.tsx session.mustChangePassword=false yapar)
 */

interface Props {
  /** true ise kapatilamaz; sifre degisene kadar app kilitli. */
  forceful?: boolean;
  /** Optional "Iptal" butonu icin (forceful=false durumunda). */
  onClose?: () => void;
  onSuccess: () => void;
  accessToken: string;
}

export function ChangePasswordModal({ forceful = true, onClose, onSuccess, accessToken }: Props) {
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [confirmPwd, setConfirmPwd] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    if (next.length < 8) {
      setError("Yeni sifre en az 8 karakter olmali.");
      return;
    }
    if (next !== confirmPwd) {
      setError("Yeni sifre ve dogrulama eslesmiyor.");
      return;
    }
    if (next === current) {
      setError("Yeni sifre eski ile ayni olamaz.");
      return;
    }
    setBusy(true);
    try {
      const headers: HeadersInit = { "Content-Type": "application/json" };
      if (accessToken) headers["Authorization"] = `Bearer ${accessToken}`;
      const resp = await fetch(`${API_BASE_URL}/auth/me/change-password`, {
        method: "POST",
        headers,
        credentials: "include",
        body: JSON.stringify({ current_password: current, new_password: next }),
      });
      if (!resp.ok) {
        const data = await resp.json().catch(() => ({}));
        setError(data.detail || "Sifre degistirilemedi.");
        return;
      }
      onSuccess();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Beklenmeyen hata.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-labelledby="cpw-title"
      style={{
        position: "fixed",
        inset: 0,
        background: "rgba(15, 23, 42, 0.65)",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        zIndex: 10000,
        padding: "1rem",
      }}
      onClick={(e) => {
        if (!forceful && e.target === e.currentTarget) onClose?.();
      }}
    >
      <form
        onSubmit={submit}
        style={{
          background: "#fff",
          borderRadius: 8,
          maxWidth: 460,
          width: "100%",
          padding: "1.75rem",
          boxShadow: "0 20px 60px rgba(0,0,0,0.25)",
          fontFamily: "system-ui, -apple-system, sans-serif",
        }}
      >
        <h2 id="cpw-title" style={{ margin: 0, marginBottom: "0.5rem", fontSize: "1.25rem" }}>
          Sifre Degistirme {forceful ? "(Zorunlu)" : ""}
        </h2>
        {forceful ? (
          <p style={{ color: "#92400e", background: "#fef3c7", padding: "0.5rem 0.75rem", borderRadius: 6, fontSize: "0.875rem", marginBottom: "1rem" }}>
            Guvenlik gerekcesiyle ilk girisinizde sifrenizi degistirmeniz gerekiyor.
          </p>
        ) : (
          <p style={{ color: "#6b7280", fontSize: "0.875rem", marginBottom: "1rem" }}>
            Mevcut sifrenizi girip yeni bir sifre belirleyin.
          </p>
        )}

        <label style={{ display: "block", marginBottom: "0.75rem" }}>
          <span style={{ display: "block", fontSize: "0.875rem", color: "#374151", marginBottom: 4 }}>
            Mevcut Sifre
          </span>
          <input
            type="password"
            required
            autoFocus
            autoComplete="current-password"
            value={current}
            onChange={(e) => setCurrent(e.target.value)}
            style={{ width: "100%", padding: "0.5rem 0.75rem", border: "1px solid #d1d5db", borderRadius: 6, fontSize: "0.875rem" }}
          />
        </label>

        <label style={{ display: "block", marginBottom: "0.75rem" }}>
          <span style={{ display: "block", fontSize: "0.875rem", color: "#374151", marginBottom: 4 }}>
            Yeni Sifre (min 8 karakter)
          </span>
          <input
            type="password"
            required
            minLength={8}
            autoComplete="new-password"
            value={next}
            onChange={(e) => setNext(e.target.value)}
            style={{ width: "100%", padding: "0.5rem 0.75rem", border: "1px solid #d1d5db", borderRadius: 6, fontSize: "0.875rem" }}
          />
        </label>

        <label style={{ display: "block", marginBottom: "1rem" }}>
          <span style={{ display: "block", fontSize: "0.875rem", color: "#374151", marginBottom: 4 }}>
            Yeni Sifre (Tekrar)
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

        <div style={{ display: "flex", gap: "0.5rem", justifyContent: "flex-end" }}>
          {!forceful && onClose ? (
            <button
              type="button"
              onClick={onClose}
              disabled={busy}
              style={{ padding: "0.5rem 1rem", background: "#fff", color: "#374151", border: "1px solid #d1d5db", borderRadius: 6, cursor: "pointer", fontSize: "0.875rem" }}
            >
              Iptal
            </button>
          ) : null}
          <button
            type="submit"
            disabled={busy}
            style={{ padding: "0.5rem 1rem", background: busy ? "#9ca3af" : "#2563eb", color: "#fff", border: "none", borderRadius: 6, cursor: busy ? "default" : "pointer", fontSize: "0.875rem", fontWeight: 500 }}
          >
            {busy ? "Kaydediliyor..." : "Sifreyi Degistir"}
          </button>
        </div>
      </form>
    </div>
  );
}
