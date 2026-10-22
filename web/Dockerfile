# syntax=docker/dockerfile:1

# ---- build: compile the SPA -------------------------------------------------
FROM node:22-alpine AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY . .
# Empty = same origin as the page (nginx proxies /api). Set to an absolute URL to call another host.
ARG VITE_API_BASE=""
ENV VITE_API_BASE=${VITE_API_BASE}
RUN npm run build

# ---- dist: just the static files --------------------------------------------
# For a shared nginx image:  COPY --from=<this image or stage> / /usr/share/nginx/html
# Or export locally:         docker build --target dist --output type=local,dest=./dist-out .
FROM scratch AS dist
COPY --from=build /app/dist/ /

# ---- serve (default): standalone nginx with SPA fallback --------------------
FROM nginx:1.27-alpine AS serve
# API_UPSTREAM is resolved at request time, so the container starts even if the API is down.
ENV API_UPSTREAM=http://api:8000 \
    DEMO_USER_ID=demo-user \
    DNS_RESOLVER=127.0.0.11
COPY nginx/default.conf.template /etc/nginx/templates/default.conf.template
COPY --from=build /app/dist/ /usr/share/nginx/html/
EXPOSE 80
HEALTHCHECK --interval=30s --timeout=3s CMD wget -q -O /dev/null http://127.0.0.1/healthz || exit 1
