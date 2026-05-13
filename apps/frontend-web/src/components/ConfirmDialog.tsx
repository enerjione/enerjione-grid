import React, { createContext, useCallback, useContext, useEffect, useRef, useState } from "react";

/**
 * ConfirmDialog — `window.confirm()` yerine kullanılan stillendirilmiş onay diyaloğu.
 *
 * Kullanım:
 *   const { confirm } = useConfirm();
 *   if (await confirm({ title: "Sil", message: "Emin misin?", danger: true })) {
 *     ...
 *   }
 *
 * window.confirm avantajına göre:
 *   - Stil uygulanmış (uygulama görselliğiyle tutarlı)
 *   - Klavye nav (ESC = iptal, Enter = onay, focus trap modal'da)
 *   - Mobil-friendly (window.confirm bazı OS'larda body'yi taşıyor)
 *   - Promise-based — async/await ile temiz akış
 *   - "danger" flag → kırmızı onay butonu (silme/destructive)
 */

interface ConfirmOptions {
  title?: string;
  message: string;
  confirmText?: string;
  cancelText?: string;
  /** Kırmızı vurgu — destructive aksiyon (sil, sıfırla vb.) */
  danger?: boolean;
}

interface ConfirmContextValue {
  confirm: (opts: ConfirmOptions) => Promise<boolean>;
}

const ConfirmContext = createContext<ConfirmContextValue | null>(null);

interface PendingItem extends ConfirmOptions {
  resolve: (ok: boolean) => void;
}

export function ConfirmProvider({ children }: { children: React.ReactNode }) {
  const [pending, setPending] = useState<PendingItem | null>(null);
  const confirmBtnRef = useRef<HTMLButtonElement | null>(null);

  const confirm = useCallback((opts: ConfirmOptions) => {
    return new Promise<boolean>((resolve) => {
      setPending({ ...opts, resolve });
    });
  }, []);

  const finish = useCallback((ok: boolean) => {
    if (pending) {
      pending.resolve(ok);
      setPending(null);
    }
  }, [pending]);

  // ESC = iptal, Enter = onay
  useEffect(() => {
    if (!pending) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.preventDefault();
        finish(false);
      } else if (e.key === "Enter") {
        e.preventDefault();
        finish(true);
      }
    };
    window.addEventListener("keydown", onKey);
    // Onay butonuna focus — varsayılan davranış olarak Enter direk onayı seçsin
    queueMicrotask(() => confirmBtnRef.current?.focus());
    return () => window.removeEventListener("keydown", onKey);
  }, [pending, finish]);

  return (
    <ConfirmContext.Provider value={{ confirm }}>
      {children}
      {pending && (
        <div
          role="dialog"
          aria-modal="true"
          aria-labelledby="confirm-title"
          aria-describedby="confirm-message"
          style={{
            position: "fixed",
            inset: 0,
            background: "rgba(15, 23, 42, 0.55)",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            zIndex: 9999,
            padding: "1rem",
          }}
          onClick={(e) => {
            // Backdrop tıklama = iptal (sadece dış katman, modal içi değil)
            if (e.target === e.currentTarget) finish(false);
          }}
        >
          <div
            style={{
              background: "#fff",
              borderRadius: 8,
              boxShadow: "0 20px 60px rgba(0,0,0,0.25)",
              maxWidth: 440,
              width: "100%",
              padding: "1.5rem",
              fontFamily: "system-ui, -apple-system, sans-serif",
            }}
          >
            <h2
              id="confirm-title"
              style={{ margin: 0, marginBottom: "0.75rem", fontSize: "1.125rem" }}
            >
              {pending.title ?? "Onay"}
            </h2>
            <p
              id="confirm-message"
              style={{ margin: 0, marginBottom: "1.5rem", color: "#374151", lineHeight: 1.5 }}
            >
              {pending.message}
            </p>
            <div style={{ display: "flex", gap: "0.5rem", justifyContent: "flex-end" }}>
              <button
                type="button"
                onClick={() => finish(false)}
                style={{
                  padding: "0.5rem 1rem",
                  background: "#fff",
                  color: "#374151",
                  border: "1px solid #d1d5db",
                  borderRadius: 6,
                  cursor: "pointer",
                  fontSize: "0.875rem",
                }}
              >
                {pending.cancelText ?? "İptal"}
              </button>
              <button
                ref={confirmBtnRef}
                type="button"
                onClick={() => finish(true)}
                style={{
                  padding: "0.5rem 1rem",
                  background: pending.danger ? "#dc2626" : "#2563eb",
                  color: "#fff",
                  border: "none",
                  borderRadius: 6,
                  cursor: "pointer",
                  fontSize: "0.875rem",
                  fontWeight: 500,
                }}
              >
                {pending.confirmText ?? (pending.danger ? "Sil" : "Onayla")}
              </button>
            </div>
          </div>
        </div>
      )}
    </ConfirmContext.Provider>
  );
}

export function useConfirm(): ConfirmContextValue {
  const ctx = useContext(ConfirmContext);
  if (!ctx) {
    // Provider yoksa native fallback — eski callsite'ler yine çalışır.
    return {
      confirm: async (opts) => window.confirm(opts.message),
    };
  }
  return ctx;
}
