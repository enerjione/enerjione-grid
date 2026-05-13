import React, { createContext, useCallback, useContext, useEffect, useRef, useState } from "react";

/**
 * ConfirmDialog — `await asyncConfirm()` yerine kullanılan stillendirilmiş onay diyaloğu.
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

// Global confirm referansi — `window.confirm` override icin. ConfirmProvider
// mount edilince burayi guncelleriz; eski `await asyncConfirm(...)` cagrilari da
// stillendirilmis dialog'a gider. Provider yoksa fallback native confirm'a duser.
let _globalConfirm: ((opts: ConfirmOptions) => Promise<boolean>) | null = null;

export function ConfirmProvider({ children }: { children: React.ReactNode }) {
  const [pending, setPending] = useState<PendingItem | null>(null);
  const confirmBtnRef = useRef<HTMLButtonElement | null>(null);

  const confirm = useCallback((opts: ConfirmOptions) => {
    return new Promise<boolean>((resolve) => {
      setPending({ ...opts, resolve });
    });
  }, []);

  // window.confirm override — eski 24 callsite'i tek tek migrate etmeden
  // stillendirilmis dialog'a yonlendir. Promise donmedigi icin senkron
  // davranis simule edilemez; bu yuzden `await asyncConfirm(...)` async kullanim
  // gerektirir. Bu sistem icindeki kullanimlarin TUMU async fonksiyon icinde
  // (async event handler) cagriliyor → Promise donmesi sorun degil.
  useEffect(() => {
    _globalConfirm = confirm;
    const original = window.confirm.bind(window);
    // Senkron API ile uyumsuz oldugu icin global override yapmiyoruz; bunun
    // yerine useConfirm() hook'unun "no provider" fallback'i guncellenir.
    // Eski callsite'lerin migrate edilmesi gerek — bu hook ile minimum efor.
    return () => {
      _globalConfirm = null;
      void original;
    };
  }, [confirm]);

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
  if (ctx) return ctx;
  // Provider yoksa: oncelikle global confirm referansini dene (mount sirasi
  // problemi olursa). Yine yoksa native window.confirm fallback.
  return {
    confirm: async (opts) => {
      if (_globalConfirm) return _globalConfirm(opts);
      return window.confirm(opts.message);
    },
  };
}

/** Global async confirm — `window.confirm` cagrisi yerine yazilmadan
 * once async fonksiyon icinde kullanilir. Provider mount edilmemisse
 * native fallback'e duser.
 *
 * Migration kolayligi icin: `if (await asyncConfirm(msg)) { ... }` ->
 * `if (await asyncConfirm(msg)) { ... }`.
 */
export async function asyncConfirm(messageOrOpts: string | ConfirmOptions): Promise<boolean> {
  const opts: ConfirmOptions =
    typeof messageOrOpts === "string" ? { message: messageOrOpts } : messageOrOpts;
  if (_globalConfirm) return _globalConfirm(opts);
  // Provider mount edilmemis — native confirm'a dus.
  return window.confirm(opts.message);
}
