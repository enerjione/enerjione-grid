import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from "react";

export type ToastKind = "success" | "error" | "info" | "warning";

type ToastItem = {
  id: number;
  kind: ToastKind;
  title?: string;
  message: string;
  expiresAt: number;
};

type ShowOptions = {
  title?: string;
  durationMs?: number;
};

type ToastApi = {
  show: (kind: ToastKind, message: string, options?: ShowOptions) => void;
  success: (message: string, options?: ShowOptions) => void;
  error: (message: string, options?: ShowOptions) => void;
  info: (message: string, options?: ShowOptions) => void;
  warning: (message: string, options?: ShowOptions) => void;
};

const ToastContext = createContext<ToastApi | null>(null);

const DEFAULT_DURATIONS: Record<ToastKind, number> = {
  success: 3000,
  info: 3500,
  warning: 5000,
  error: 6000
};

const KIND_ICON: Record<ToastKind, string> = {
  success: "check_circle",
  error: "error",
  info: "info",
  warning: "warning"
};

let toastIdCounter = 1;

export function ToastProvider({ children }: { children: ReactNode }) {
  const [items, setItems] = useState<ToastItem[]>([]);
  const timersRef = useRef<Map<number, number>>(new Map());

  const dismiss = useCallback((id: number) => {
    setItems((prev) => prev.filter((item) => item.id !== id));
    const timer = timersRef.current.get(id);
    if (timer) {
      window.clearTimeout(timer);
      timersRef.current.delete(id);
    }
  }, []);

  const show = useCallback(
    (kind: ToastKind, message: string, options?: ShowOptions) => {
      if (!message) return;
      const duration = options?.durationMs ?? DEFAULT_DURATIONS[kind];
      const id = toastIdCounter++;
      const expiresAt = Date.now() + duration;
      setItems((prev) => {
        // Aynı mesajı kısa sürede arka arkaya yutmamak için son 1 sn'deki tekrarı süpür
        const recent = prev.find(
          (item) => item.message === message && item.kind === kind && item.expiresAt - 1000 > Date.now()
        );
        if (recent) return prev;
        return [...prev, { id, kind, title: options?.title, message, expiresAt }];
      });
      const timer = window.setTimeout(() => dismiss(id), duration);
      timersRef.current.set(id, timer);
    },
    [dismiss]
  );

  useEffect(() => {
    return () => {
      timersRef.current.forEach((timer) => window.clearTimeout(timer));
      timersRef.current.clear();
    };
  }, []);

  const api = useMemo<ToastApi>(
    () => ({
      show,
      success: (msg, opts) => show("success", msg, opts),
      error: (msg, opts) => show("error", msg, opts),
      info: (msg, opts) => show("info", msg, opts),
      warning: (msg, opts) => show("warning", msg, opts)
    }),
    [show]
  );

  return (
    <ToastContext.Provider value={api}>
      {children}
      <div className="toast-container" role="status" aria-live="polite">
        {items.map((item) => (
          <div key={item.id} className={`toast toast-${item.kind}`}>
            <span className="material-symbols-outlined toast-icon">{KIND_ICON[item.kind]}</span>
            <div className="toast-body">
              {item.title ? <strong className="toast-title">{item.title}</strong> : null}
              <span className="toast-message">{item.message}</span>
            </div>
            <button
              type="button"
              className="toast-close"
              aria-label="Kapat"
              onClick={() => dismiss(item.id)}
            >
              <span className="material-symbols-outlined">close</span>
            </button>
          </div>
        ))}
      </div>
    </ToastContext.Provider>
  );
}

export function useToast(): ToastApi {
  const ctx = useContext(ToastContext);
  if (!ctx) {
    throw new Error("useToast must be used within ToastProvider");
  }
  return ctx;
}
