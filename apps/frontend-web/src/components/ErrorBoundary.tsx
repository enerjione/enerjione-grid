import React from "react";

/**
 * ErrorBoundary — render path'inde fırlatılan exception'ları yakalar.
 *
 * SCADA UI'da bir component'in render hatası tüm sayfayı beyaza çevirir; operatör
 * sistemi takip edemez. Burada hatayı yakalayıp kullanıcıya "Hata oluştu — yeniden yükle"
 * mesajı + sayfayı reload eden buton gösteriyoruz.
 *
 * Hata detayı sadece server log'una / browser console'una düşer; ekrana sıkıntılı
 * stack trace yazılmaz (operatör için anlamsız + recon ipucu).
 */

interface Props {
  children: React.ReactNode;
}

interface State {
  hasError: boolean;
  errorMessage: string | null;
}

export class ErrorBoundary extends React.Component<Props, State> {
  state: State = { hasError: false, errorMessage: null };

  static getDerivedStateFromError(error: unknown): State {
    const msg = error instanceof Error ? error.message : String(error);
    return { hasError: true, errorMessage: msg };
  }

  componentDidCatch(error: unknown, info: React.ErrorInfo): void {
    // Console'a + opsiyonel Sentry/log endpoint'ine yaz; kullanıcıya gösterilmez.
    // eslint-disable-next-line no-console
    console.error("UI error caught by ErrorBoundary:", error, info);
  }

  private handleReload = () => {
    window.location.reload();
  };

  render() {
    if (!this.state.hasError) return this.props.children;
    return (
      <div
        role="alert"
        style={{
          minHeight: "100vh",
          display: "flex",
          flexDirection: "column",
          alignItems: "center",
          justifyContent: "center",
          padding: "2rem",
          textAlign: "center",
          fontFamily: "system-ui, -apple-system, sans-serif",
        }}
      >
        <h1 style={{ fontSize: "1.5rem", marginBottom: "0.5rem" }}>
          Beklenmeyen bir hata oluştu
        </h1>
        <p style={{ color: "#666", maxWidth: 480, marginBottom: "1.5rem" }}>
          Arayüz beklenmedik bir durum nedeniyle yanıt veremez hale geldi. Sayfayı yeniden
          yüklemek sorunu çoğunlukla çözer. Sorun devam ederse sistem yöneticisine bildirin.
        </p>
        <button
          onClick={this.handleReload}
          style={{
            padding: "0.75rem 1.5rem",
            background: "#2563eb",
            color: "#fff",
            border: "none",
            borderRadius: 6,
            fontSize: "1rem",
            cursor: "pointer",
          }}
        >
          Sayfayı yeniden yükle
        </button>
      </div>
    );
  }
}
