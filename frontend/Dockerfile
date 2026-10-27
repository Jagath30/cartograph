FROM node:22-slim

# Development image. NODE_ENV is left unset (item 109, step 12): npm ci
# then installs the devDependencies `next dev` needs, `next dev` sets
# development itself, and `next build` sets production, which its
# prerender of /_global-error needs. Set to development, the build failed.

WORKDIR /app

# npm pinned (step 9). The image's own npm 10.9.8 crashes while resolving
# vitest's peers ("Cannot read properties of null (reading 'edgesOut')");
# 11.20.0 does not. The lockfile is made with this version.
RUN npm install -g npm@11.20.0

# Lockfile first: this layer then survives every source edit.
COPY package.json package-lock.json ./
RUN npm ci

COPY . .

EXPOSE 3000
# -H 0.0.0.0 so the dev server is reachable from outside the container.
CMD ["npm", "run", "dev", "--", "-H", "0.0.0.0"]
