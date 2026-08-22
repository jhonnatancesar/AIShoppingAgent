import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'
import { fileURLToPath, URL } from 'node:url'

// TASK-091 (item 1 da V1.2): `npm run dev` roda num servidor separado do
// backend real (uvicorn em :8000); o proxy evita CORS em desenvolvimento
// sem mudar nada do cliente (`credentials: 'include'` continua igual,
// mesma origem aparente do ponto de vista do navegador). Em produção, o
// próprio FastAPI serve o build estático sob o mesmo domínio/porta -- o
// proxy deixa de ser necessário.
export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url)),
    },
  },
  server: {
    proxy: {
      '/api': 'http://127.0.0.1:8000',
    },
  },
})
