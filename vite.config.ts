import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'
import { viteSingleFile } from 'vite-plugin-singlefile'
import { fileURLToPath } from 'node:url'

// One self-contained HTML file in dist/: open it straight from disk, no server needed.
// Tailwind powers the nteract-elements components in src/components (copied in via the shadcn registry).
export default defineConfig({
  plugins: [react(), tailwindcss(), viteSingleFile()],
  base: './',
  resolve: { alias: { '@': fileURLToPath(new URL('./src', import.meta.url)) } },
  build: { outDir: 'dist', assetsInlineLimit: 100_000_000 },
})
