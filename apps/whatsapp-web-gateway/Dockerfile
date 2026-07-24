FROM node:20-slim

WORKDIR /app

COPY package.json ./
RUN npm install --omit=dev

COPY src ./src

RUN groupadd -r e1 --gid=10001 \
 && useradd -r -g e1 --uid=10001 --home-dir=/app --shell=/sbin/nologin e1 \
 && mkdir -p /data/session \
 && chown -R e1:e1 /app /data/session
USER e1

EXPOSE 8016

HEALTHCHECK --interval=20s --timeout=5s --start-period=15s --retries=3 \
    CMD node -e "require('http').get('http://127.0.0.1:8016/health',r=>process.exit(r.statusCode===200?0:1)).on('error',()=>process.exit(1))"

CMD ["node", "src/index.js"]
