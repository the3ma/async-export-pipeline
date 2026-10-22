import { defineConfig } from 'vitest/config';
import react from '@vitejs/plugin-react';

// `npm run dev` proxies /api to the FastAPI service and injects the demo user header
// (the production nginx does the same). `npm run dev:mock` serves the MSW worker
// from mock-public/ so the app runs with no backend at all.
export default defineConfig(({ mode }) => ({
  plugins: [react()],
  publicDir: mode === 'mock' ? 'mock-public' : 'public',
  server: {
    port: 5173,
    proxy:
      mode === 'mock'
        ? undefined
        : {
            '/api': {
              target: process.env.API_PROXY_TARGET ?? 'http://localhost:8000',
              changeOrigin: true,
              headers: { 'X-User-Id': 'demo-user' },
            },
          },
  },
  build: {
    sourcemap: true,
    target: 'es2022',
  },
  test: {
    environment: 'jsdom',
    setupFiles: ['./src/test/setup.ts'],
    css: { modules: { classNameStrategy: 'non-scoped' } },
    restoreMocks: true,
  },
}));
