import { defineConfig, type Plugin } from 'vite';
import react from '@vitejs/plugin-react';
import electron from 'vite-plugin-electron';
import rendererPlugin from 'vite-plugin-electron-renderer';
import path from 'node:path';
import { RENDERER_CSP } from './shared/csp';

// The packaged app loads dist/index.html from disk, where a response header
// cannot carry a policy, so the build writes it into the page. Development is
// left alone: Vite's client and React refresh need inline scripts and a socket.
const rendererCsp = (): Plugin => ({
  name: 'rom-renderer-csp',
  apply: 'build',
  transformIndexHtml: () => [{
    tag: 'meta',
    attrs: { 'http-equiv': 'Content-Security-Policy', content: RENDERER_CSP },
    injectTo: 'head-prepend',
  }],
});

export default defineConfig({
  plugins: [
    react(),
    rendererCsp(),
    electron([
      {
        entry: 'electron/main.ts',
        vite: {
          build: {
            outDir: 'dist-electron',

            sourcemap: false,
            rollupOptions: {
              external: ['discord-rpc'],
            },
          },
        },
      },
      {
        entry: 'electron/preload.ts',
        onstart(options) {
          options.reload();
        },
        vite: { build: { outDir: 'dist-electron', sourcemap: false } },
      },
    ]),
    rendererPlugin(),
  ],
  resolve: {
    alias: {
      '@shared': path.resolve(__dirname, 'shared'),
    },
  },
  server: {
    port: 5180,
    strictPort: true,
  },
  clearScreen: false,
});
