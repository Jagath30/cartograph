import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // Development only. `next dev` serves its own assets to `localhost` and
  // to the host it was started with, which in the container is 0.0.0.0.
  // The compose file, the smoke check and the API's CORS list all use
  // 127.0.0.1, and without this entry a page opened there is served and
  // never comes alive: its scripts are refused.
  allowedDevOrigins: ["127.0.0.1"],
  // The application is /ask. Temporary (307), not permanent: history and
  // login arrive at step 11, and "/" may become something else.
  async redirects() {
    return [{ source: "/", destination: "/ask", permanent: false }];
  },
};

export default nextConfig;
