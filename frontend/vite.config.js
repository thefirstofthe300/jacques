/// <reference types="vitest/config" />
import { defineConfig } from 'vite'
import { svelte } from '@sveltejs/vite-plugin-svelte'

// https://vite.dev/config/
export default defineConfig({
  plugins: [svelte()],
  build: {
    // Build output lands directly in the Python package tree, sibling to
    // jacques/templates/ (which is removed once the Svelte SPA replaces it).
    outDir: '../jacques/static',
    emptyOutDir: true,
  },
  // Svelte 5 ships separate client/server builds; without this, vitest
  // resolves component imports to the server (SSR) build, which doesn't
  // support mount()/lifecycle functions used by @testing-library/svelte.
  // https://svelte.dev/docs/svelte/testing
  resolve: {
    conditions: ['browser'],
  },
  test: {
    environment: 'jsdom',
  },
})
