import React from "react";
import ReactDOM from "react-dom/client";
import { App } from "./app/App";
import { ToastProvider } from "./components/ToastProvider";
import "leaflet/dist/leaflet.css";
import "./styles.css";

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <ToastProvider>
      <App />
    </ToastProvider>
  </React.StrictMode>
);
