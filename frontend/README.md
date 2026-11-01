# Cartograph's frontend

The page of Cartograph, in Next.js. What Cartograph is, how it works and how to run the whole of it: the [root README](../README.md).

## How it runs

It runs in the `frontend` service of the root `docker-compose.yml`, which `docker compose up` starts at http://localhost:3000. The browser calls only this origin: `next.config.ts` rewrites `/api/v1` to `BACKEND_ORIGIN`, the backend's address (`http://backend:8000` in compose). On Vercel, `BACKEND_ORIGIN` is a build variable, and a Vercel build without it fails.

Inside the running service:

    docker compose exec frontend npm test         # vitest
    docker compose exec frontend npx tsc --noEmit
    docker compose exec frontend npm run lint
    docker compose exec frontend npm audit --omit=dev

Packages are installed through the image, from the lockfile. After a dependency change, rebuild the image and recreate the service with `--renew-anon-volumes`, or the container keeps its old `node_modules`.
