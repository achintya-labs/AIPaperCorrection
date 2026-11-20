import node from "@astrojs/node";
import react from "@astrojs/react";
import { defineConfig } from "astro/config";

export default defineConfig({
  output: "server",
  adapter: node({ mode: "standalone" }),
  integrations: [react()],
  // Astro's own origin check needs the public hostname at BUILD time. We do the same check in
  // src/middleware.ts (see src/lib/csrf.ts), configured at runtime via ALLOWED_HOSTS, so one image fits all.
  security: { checkOrigin: false },
  server: { host: true, port: 4321 },
});
