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

# --- Stage 2: nginx ---
FROM nginx:1.27-alpine

# nginx default config'i kaldir, kendi reverse-proxy konfigumuzu kopyala.
RUN rm /etc/nginx/conf.d/default.conf
COPY nginx.conf /etc/nginx/conf.d/default.conf

COPY --from=build /app/dist /usr/share/nginx/html

EXPOSE 80

CMD ["nginx", "-g", "daemon off;"]
