import React from "react";
import ReactDOM from "react-dom/client";
import { App } from "./app/App";
import { ToastProvider } from "./components/ToastProvider";
import { ProjectSettingsProvider } from "./components/ProjectSettingsProvider";
import "leaflet/dist/leaflet.css";
import "./styles.css";

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <ProjectSettingsProvider>
      <ToastProvider>
        <App />
      </ToastProvider>
    </ProjectSettingsProvider>
  </React.StrictMode>
);
