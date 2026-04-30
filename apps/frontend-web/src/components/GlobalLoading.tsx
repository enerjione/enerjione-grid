type Props = {
  show: boolean;
  message?: string;
};

/** Tam ekran loading overlay — uzun süren API geçişlerinde gösterilir.
 *  Görünür hale geldiğinde sayfanın altında değil, üstüne biner. */
export function GlobalLoading({ show, message }: Props) {
  if (!show) return null;
  return (
    <div className="global-loading-overlay" role="status" aria-live="polite">
      <span className="panel-loading-spinner" aria-hidden="true" />
      <span className="global-loading-text">{message ?? "Yükleniyor…"}</span>
    </div>
  );
}
