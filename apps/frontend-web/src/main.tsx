import React from "react";
import ReactDOM from "react-dom/client";
import { App } from "./app/App";
import { ConfirmProvider } from "./components/ConfirmDialog";
import { ErrorBoundary } from "./components/ErrorBoundary";
import { ToastProvider } from "./components/ToastProvider";
import { ProjectSettingsProvider } from "./components/ProjectSettingsProvider";
import "./shared/i18n";
import "leaflet/dist/leaflet.css";
import "./styles.css";

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <ErrorBoundary>
      <ProjectSettingsProvider>
        <ToastProvider>
          <ConfirmProvider>
            <App />
          </ConfirmProvider>
        </ToastProvider>
      </ProjectSettingsProvider>
    </ErrorBoundary>
  </React.StrictMode>
);
