# --- Build stage: compile the SPA to static assets ---
FROM node:20-alpine AS builder

WORKDIR /build

COPY package.json package-lock.json ./
RUN npm ci

COPY . .
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
