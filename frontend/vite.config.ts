import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    // During development, forward API calls to the backend.
    proxy: { "/api": "http://127.0.0.1:8000" },
  },
});
