import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

import pkg from './package.json' with { type: 'json' };

export default defineConfig({
  plugins: [react()],
  // The version badge in the footer reads this, so package.json stays the one
  // place the frontend version is written down.
  define: {
    __APP_VERSION__: JSON.stringify(pkg.version),
  },
  server: {
    port: 3000,
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:1928',
        changeOrigin: true,
      },
      '/ws': {
        target: 'ws://127.0.0.1:1928',
        ws: true,
      }
    }
  },
  build: {
    outDir: 'dist',
    emptyOutDir: true,
  },
  test: {
    // jsdom, because the component tests assert on rendered output and on
    // click behaviour, not just on return values.
    environment: 'jsdom',
    globals: true,
    setupFiles: ['./src/test/setup.js'],
    include: ['src/**/*.test.{js,jsx}'],
    css: false,
  }
});
