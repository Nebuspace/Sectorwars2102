# Multi-stage build. Three named targets:
#   * deps        — npm install, shared by dev and the builder
#   * dev         — runs `vite` dev server with HMR; expects /app source mount
#   * production  — `vite build` static assets, served by nginx:alpine
#
# Pick the target via docker compose `build.target: dev|production` or
# `docker build --target=production`.

# ---------------------------------------------------------------------------
# deps — shared node_modules layer for layer-cache hits
# ---------------------------------------------------------------------------
FROM node:24-slim AS deps

WORKDIR /app
COPY package.json package-lock.json* ./
RUN npm install --legacy-peer-deps --no-audit --no-fund

# ---------------------------------------------------------------------------
# dev — Vite dev server, source volume-mounted by compose at runtime
# ---------------------------------------------------------------------------
FROM deps AS dev

# Source isn't COPY'd in dev — compose volume-mounts ./services/player-client
# over /app so saved edits hot-reload via Vite HMR.
EXPOSE 3000
CMD ["npm", "run", "dev", "--", "--host", "0.0.0.0"]

# ---------------------------------------------------------------------------
# builder — produces the static build artifact for the production runtime
# ---------------------------------------------------------------------------
FROM deps AS builder

ARG VITE_API_URL=/api/v1
ARG VITE_WS_URL=/ws
ENV VITE_API_URL=${VITE_API_URL}
ENV VITE_WS_URL=${VITE_WS_URL}

COPY . .
RUN npm run build

# ---------------------------------------------------------------------------
# production — nginx serving the prebuilt SPA, with HTML5 history-mode routing
# ---------------------------------------------------------------------------
FROM nginx:stable-alpine AS production

# Minimal nginx config: serve static assets, fall back to index.html for any
# unknown path so React Router handles client-side routes (HTML5 history mode).
# nginx-gateway in front handles TLS, CORS, /api/* routing — this is just SPA
# static serving on the upstream port (3000 to match dev port for consistency).
RUN rm /etc/nginx/conf.d/default.conf
COPY <<'EOF' /etc/nginx/conf.d/spa.conf
server {
    listen       3000 default_server;
    listen  [::]:3000 default_server;
    server_name  _;

    root   /usr/share/nginx/html;
    index  index.html;

    # SPA history-mode: any path that doesn't match a file falls back to index.html
    location / {
        try_files $uri $uri/ /index.html;
    }

    # Health endpoint for orchestration probes
    location = /health {
        access_log off;
        return 200 "healthy\n";
        add_header Content-Type text/plain;
    }

    # Cache built static assets aggressively (Vite emits content-hashed names)
    location /assets/ {
        expires 1y;
        add_header Cache-Control "public, immutable";
    }
}
EOF

COPY --from=builder /app/dist /usr/share/nginx/html

EXPOSE 3000

# nginx's default CMD is correct: nginx -g 'daemon off;'
