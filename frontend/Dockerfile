FROM node:22-slim

# Development image: `next dev` needs the devDependencies, so NODE_ENV
# must not be "production" here. Step 12's deployment build is separate.
ENV NODE_ENV=development

WORKDIR /app

# Lockfile first: this layer then survives every source edit.
COPY package.json package-lock.json ./
RUN npm ci

COPY . .

EXPOSE 3000
# -H 0.0.0.0 so the dev server is reachable from outside the container.
CMD ["npm", "run", "dev", "--", "-H", "0.0.0.0"]
