import React from "react";

import i18n from "../shared/i18n";

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
  /**
   * KAPSAM. `app` (varsayilan) tum ekrani kaplar — kok sarmalayici icin.
   * `page` yalnizca icerik alanini kaplar: bir SAYFA coktugunde sekme
   * seridi, ust bar ve diger sekmeler CALISMAYA DEVAM EDER.
   *
   * NEDEN: 2026-08-12'de hat arizasi detayinda bir render hatasi (hook
   * sirasi) tum uygulamayi dusurdu — operator hicbir sey yapamadi, tek
   * care sayfayi yenilemekti. Bir SCADA arayuzunde tek bir sekmenin
   * kaydi silindi diye butun ekranin olmesi kabul edilemez.
   */
  variant?: "app" | "page";
  /**
   * Bu deger degisince hata durumu SIFIRLANIR (or. aktif sekme anahtari).
   * Boylece kullanici baska bir sekmeye gecip geri geldiginde ekran
   * kendiliginden toparlanir; yeniden yukleme gerekmez.
   */
  resetKey?: string | number;
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

  componentDidUpdate(prevProps: Props): void {
    // Sekme degisti -> yeni icerik icin temiz baslangic. Aksi halde bir kez
    // coken sinir, saglam sayfalari da gostermeyi reddederdi.
    if (this.state.hasError && prevProps.resetKey !== this.props.resetKey) {
      this.setState({ hasError: false, errorMessage: null });
    }
  }

  private handleReload = () => {
    window.location.reload();
  };

  private handleRetry = () => {
    this.setState({ hasError: false, errorMessage: null });
  };

  render() {
    if (this.state.hasError && this.props.variant === "page") {
      // SAYFA KAPSAMI — uygulamanin geri kalani ayakta kalir.
      return (
        <div role="alert" className="page-crash">
          <h2>
            {i18n.t("errors.crashTitle", { defaultValue: "Beklenmeyen bir hata oluştu" })}
          </h2>
          <p>
            {i18n.t("errors.crashPageBody", {
              defaultValue:
                "Bu sayfa açılamadı. Diğer sekmeler çalışmaya devam ediyor; başka bir sekmeye geçip geri dönebilir ya da yeniden deneyebilirsiniz.",
            })}
          </p>
          <div className="page-crash-actions">
            <button type="button" onClick={this.handleRetry}>
              {i18n.t("errors.crashRetry", { defaultValue: "Yeniden dene" })}
            </button>
            <button type="button" onClick={this.handleReload}>
              {i18n.t("errors.crashReload", { defaultValue: "Sayfayı yeniden yükle" })}
            </button>
          </div>
          {/* TEKNIK DETAY — kapali baslar.
              Onceden hata mesaji YALNIZCA konsola yaziliyordu; sahadaki
              operatorden "F12 ac, kirmizi satiri oku" istemek zorunda
              kaliyorduk. Ozet mesaj burada; yigin izi hala ekrana
              basilmaz (operatore anlamsiz, disariya ipucu). */}
          {this.state.errorMessage ? (
            <details className="page-crash-detail">
              <summary>
                {i18n.t("errors.crashDetails", { defaultValue: "Teknik detay" })}
              </summary>
              <code>{this.state.errorMessage}</code>
            </details>
          ) : null}
        </div>
      );
    }
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
          {i18n.t("errors.crashTitle", { defaultValue: "Beklenmeyen bir hata oluştu" })}
        </h1>
        <p style={{ color: "#666", maxWidth: 480, marginBottom: "1.5rem" }}>
          {i18n.t("errors.crashBody", {
            defaultValue:
              "Arayüz beklenmedik bir durum nedeniyle yanıt veremez hale geldi. Sayfayı yeniden yüklemek sorunu çoğunlukla çözer. Sorun devam ederse sistem yöneticisine bildirin.",
          })}
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
          {i18n.t("errors.crashReload", { defaultValue: "Sayfayı yeniden yükle" })}
        </button>
      </div>
    );
  }
}
