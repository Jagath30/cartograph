import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // Development only. `next dev` serves its own assets to `localhost` and
  // to the host it was started with, which in the container is 0.0.0.0.
  // The compose file, the smoke check and the API's CORS list all use
  // 127.0.0.1, and without this entry a page opened there is served and
  // never comes alive: its scripts are refused.
  allowedDevOrigins: ["127.0.0.1"],
};

export default nextConfig;
