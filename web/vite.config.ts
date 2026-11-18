/// <reference types="vitest/config" />
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// /api is proxied to the FastAPI backend (APP_PORT in .env)
export default defineConfig({
  plugins: [react()],
  server: { proxy: { '/api': 'http://localhost:8700' } },
  test: { environment: 'jsdom', setupFiles: ['./src/test/setup.ts'], css: { modules: { classNameStrategy: 'non-scoped' } } },
})
