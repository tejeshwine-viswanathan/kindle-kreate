import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";
import { VitePWA } from "vite-plugin-pwa";

const apiTarget = process.env.API_URL ?? "http://localhost:8000";

export default defineConfig({
  plugins: [
    react(),
    tailwindcss(),
    VitePWA({
      registerType: "autoUpdate",
      devOptions: { enabled: true },
      includeAssets: ["apple-touch-icon.png"],
      manifest: {
        name: "Kindle Kreate",
        short_name: "Kindle Kreate",
        description: "Convert PDFs into clean, validated EPUBs for Kindle.",
        theme_color: "#2e6b66",
        background_color: "#fbf7f1",
        display: "standalone",
        start_url: "/",
        icons: [
          { src: "pwa-192x192.png", sizes: "192x192", type: "image/png" },
          { src: "pwa-512x512.png", sizes: "512x512", type: "image/png" },
          { src: "pwa-maskable-512x512.png", sizes: "512x512", type: "image/png", purpose: "maskable" },
        ],
      },
      workbox: {
        // App shell only. API calls, uploads and downloads always go to the network.
        navigateFallbackDenylist: [/^\/api\//, /^\/health/],
        globPatterns: ["**/*.{js,css,html,png,svg,woff2}"],
      },
    }),
  ],
  server: {
    proxy: {
      "/api": apiTarget,
      "/health": apiTarget,
    },
  },
});
