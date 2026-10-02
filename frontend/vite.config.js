import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

const releaseSha = process.env.RENDER_GIT_COMMIT || process.env.VITE_RELEASE_SHA || 'local';

export default defineConfig({
  plugins: [react()],
  define: {
    __FLASHIN_RELEASE_SHA__: JSON.stringify(releaseSha),
  },
  server: {
    port: 5173,
  },

});
