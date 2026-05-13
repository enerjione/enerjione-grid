import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: {
    host: true,
    port: 5173,
    strictPort: true,
    allowedHosts: true
  },
  preview: {
    host: true,
    port: 5173,
    strictPort: true,
    allowedHosts: true
  },
  build: {
    // Production source map'lerini bundle ile dağıtma — gönderirsek orijinal
    // kaynak kodu (component isimleri, secret URL, business logic) deşifre
    // edilebilir. Lokal debug için yine "build:debug" script'i ile sourcemap
    // üretilebilir.
    sourcemap: false,
  },
  esbuild: {
    // Production build'inde console.* ve debugger çağrılarını sil.
    // Debug log'ları (token prefix, internal URL, kullanıcı verisi) prod
    // bundle'a sızmasın. development run'da etkisi yok (vite dev server
    // esbuild build adımını atlar).
    drop: ["console", "debugger"],
  },
});
