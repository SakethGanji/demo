import path from "path"
import tailwindcss from "@tailwindcss/vite"
import react from "@vitejs/plugin-react-swc"
import { defineConfig } from "vite"

// https://vite.dev/config/
export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "./src"),
    },
  },
  assetsInclude: ["**/*.wasm"],
  build: {
    rollupOptions: {
      output: {
        manualChunks(id) {
          if (!id.includes('node_modules')) return

          // Group by package scope/name automatically
          if (id.includes('react-dom') || id.includes('node_modules/react/')) {
            return 'vendor-react'
          }
          if (id.includes('reactflow') || id.includes('@reactflow/')) {
            return 'vendor-reactflow'
          }
          if (id.includes('@codemirror/') || id.includes('@lezer/')) {
            return 'vendor-codemirror'
          }
          if (id.includes('@radix-ui/')) {
            return 'vendor-radix'
          }
          if (id.includes('@tanstack/')) {
            return 'vendor-tanstack'
          }
          // All other node_modules go to a common vendor chunk
          return 'vendor'
        },
      },
    },
  },
  server: {
    port: 5173,
  },
})