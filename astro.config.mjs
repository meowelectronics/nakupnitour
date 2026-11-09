// @ts-check
import { defineConfig } from 'astro/config';

// https://astro.build/config
export default defineConfig({
  site: 'https://nakupnitour.cz',
  output: 'static',
  vite: {
    server: {
      proxy: { '/api/branik-offers': 'http://127.0.0.1:8787' },
    },
  },
});
