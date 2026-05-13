import React from "react";
import ReactDOM from "react-dom/client";
import { App } from "./app/App";
import { ConfirmProvider } from "./components/ConfirmDialog";
import { ErrorBoundary } from "./components/ErrorBoundary";
import { ToastProvider } from "./components/ToastProvider";
import { ProjectSettingsProvider } from "./components/ProjectSettingsProvider";
import { SetupPasswordPage } from "./features/auth/SetupPasswordPage";
import "./shared/i18n";
import "leaflet/dist/leaflet.css";
// Material Symbols self-host — Google Fonts CDN bagimliligi kaldirildi.
// 4G sahada DNS/proxy bloku ikonlari kirsa bile uygulama calismaya devam eder.
import "material-symbols/outlined.css";
import "./styles.css";

// Basit route gate: /setup-password yolunda main App'i hic mount etme,
// dogrudan SetupPasswordPage goster. Auth gerekmedigi icin tek route ve
// minimum bagimlilik. Diger ozel sayfalar eklenirse react-router benzeri
// bir cozume tasinabilir.
const isSetupPasswordRoute = window.location.pathname === "/setup-password";

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <ErrorBoundary>
      <ProjectSettingsProvider>
        <ToastProvider>
          <ConfirmProvider>
            {isSetupPasswordRoute ? <SetupPasswordPage /> : <App />}
          </ConfirmProvider>
        </ToastProvider>
      </ProjectSettingsProvider>
    </ErrorBoundary>
  </React.StrictMode>
);
