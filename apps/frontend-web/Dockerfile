# --- Stage 1: build ---
FROM node:20-alpine AS build

WORKDIR /app

# Once package*.json kopyala ki deps cache'lenebilsin.
COPY package.json package-lock.json* ./
RUN npm ci --no-audit --no-fund

COPY . .

# Build sirasinda VITE_API_BASE_URL set edilmezse runtime'da same-origin /api/v1
# kullanilir (api.ts icindeki fallback). Boylece nginx ayni origin'den proxy'ler.
RUN npm run build

# --- Stage 2: nginx (unprivileged) ---
# nginxinc/nginx-unprivileged: master process da non-root (uid 101 = nginx user)
# olarak calisir; standart nginx:alpine'da master root, worker'lar non-root.
# Container escape exploit'lerinde tam non-root acisindan daha guvenli.
# Port 8080: unprivileged uid 101 ile 80 portuna bind edemez; compose mapping
# host:80 -> container:8080 yapildi.
FROM nginxinc/nginx-unprivileged:1.31-alpine

# nginx default config'i kaldir, kendi reverse-proxy konfigumuzu kopyala.
# Unprivileged image'da config dosyalari /etc/nginx/conf.d altinda; ayni yer.
USER root
RUN rm /etc/nginx/conf.d/default.conf
COPY nginx.conf /etc/nginx/conf.d/default.conf
# Guvenlik basliklari snippet'i — nginx.conf'ta `add_header` iceren her
# location bunu include eder (nginx'te add_header miras alinmaz; bkz. dosya
# basindaki aciklama). conf.d ALTINDA DEGIL: conf.d/*.conf otomatik yuklenir
# ve snippet http context'inde tek basina gecersizdir.
COPY security-headers.conf /etc/nginx/security-headers.conf
COPY --from=build /app/dist /usr/share/nginx/html
RUN chown -R nginx:nginx /usr/share/nginx/html /etc/nginx/conf.d \
 && chown nginx:nginx /etc/nginx/security-headers.conf
USER nginx

EXPOSE 8080

# Saglik kontrolu — tum servisler icinde HEALTHCHECK'i olmayan TEK servis
# burasiydi. Kullanicinin gordugu tek yuz bu: nginx ayakta ama SPA servis
# edilmiyorsa (bozuk config, eksik dist, izin hatasi) disaridan "container
# calisiyor" gorunur ve arizanin nerede oldugu anlasilmaz.
#
# `--spider` govdeyi indirmez, yalnizca durum kodunu alir. BusyBox wget
# alpine imajinda hazir gelir; ek paket kurulmuyor.
#
# DURUST SINIR: Docker unhealthy bir container'i YENIDEN BASLATMAZ, yalnizca
# isaretler. Buradaki deger gorunurluk: `docker ps` ve kurulum betiginin
# saglik beklemesi arizayi gorur. nginx cokerse zaten surec biter ve
# `restart: unless-stopped` devreye girer.
HEALTHCHECK --interval=20s --timeout=5s --start-period=10s --retries=3 \
    CMD wget -q --spider http://127.0.0.1:8080/ || exit 1

CMD ["nginx", "-g", "daemon off;"]
