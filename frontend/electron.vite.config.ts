import { resolve } from 'path'
import { defineConfig } from 'electron-vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

const sourceCommit = process.env['DISTRIBLLM_BUILD_SOURCE_COMMIT'] ?? 'unknown'
const sourceDirty = process.env['DISTRIBLLM_BUILD_SOURCE_DIRTY'] !== 'false'

export default defineConfig({
  main: {
    define: {
      __DISTRIBLLM_SOURCE_COMMIT__: JSON.stringify(sourceCommit),
      __DISTRIBLLM_SOURCE_DIRTY__: JSON.stringify(sourceDirty)
    }
  },
  preload: {},
  renderer: {
    resolve: {
      alias: {
        '@renderer': resolve('src/renderer/src')
      }
    },
    plugins: [tailwindcss(), react()]
  }
})
