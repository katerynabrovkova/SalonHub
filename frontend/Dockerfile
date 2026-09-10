FROM node:22-bookworm-slim

ENV NODE_ENV=development

WORKDIR /app

# Install dependencies first so the layer is cached until the lockfile changes.
COPY package.json package-lock.json ./
RUN npm ci

COPY . .

# node:22-bookworm-slim already ships a non-root "node" user (uid 1000);
# reuse it rather than creating another (mirrors backend/Dockerfile's intent).
# Pre-create .next so the named volume mounted there inherits node ownership
# (Docker seeds an empty named volume from the image path's perms).
RUN mkdir -p /app/.next && chown -R node:node /app
USER node

EXPOSE 3000

# --hostname 0.0.0.0 is required — plain `next dev` binds to localhost inside
# the container and the published port would never reach it from the host.
CMD ["npx", "next", "dev", "--hostname", "0.0.0.0", "--port", "3000"]
