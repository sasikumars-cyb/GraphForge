# --- Dev stage: Vite dev server with HMR, used by docker/docker-compose.yml ---
# Source is bind-mounted over /app at runtime; node_modules stays inside the
# container via an anonymous volume (see docker-compose.yml) so it's always
# this image's own Linux-built install that runs, never whatever happens to
# be in the host's node_modules.
FROM node:20-alpine AS dev

WORKDIR /app

COPY package.json package-lock.json ./
RUN npm ci

COPY . .

EXPOSE 5173

CMD ["npm", "run", "dev"]

# --- Build stage: compile the SPA to static assets ---
FROM node:20-alpine AS builder

WORKDIR /build

COPY package.json package-lock.json ./
RUN npm ci

COPY . .

# Vite bakes import.meta.env.VITE_* values in at build time, not runtime -
# unset, client.ts's own fallback ("http://localhost:8000/api/v1") ends up
# hardcoded into the bundle, which is wrong for every deployment except a
# developer's own machine. Pass a relative path (e.g. "/api/v1") for any
# same-origin proxy setup (see docker/nginx/nginx.conf's /api/ proxy_pass) -
# leaving this unset preserves the old (dev-only-correct) default.
ARG VITE_API_BASE_URL
ENV VITE_API_BASE_URL=$VITE_API_BASE_URL

RUN npm run build

# --- Runtime stage: serve the static build with Nginx ---
# The Nginx config itself is orchestration, not part of the frontend build —
# docker-compose.yml mounts docker/nginx/nginx.conf into this stage at run
# time, so this Dockerfile stays buildable standalone (`docker build ./frontend`).
FROM nginx:1.27-alpine AS runtime

COPY --from=builder /build/dist /usr/share/nginx/html

EXPOSE 80

HEALTHCHECK --interval=30s --timeout=3s --start-period=5s \
    CMD wget -q --spider http://localhost:80 || exit 1

CMD ["nginx", "-g", "daemon off;"]
