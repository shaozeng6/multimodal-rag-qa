import { defineConfig } from 'vite'
import { fileURLToPath, URL } from 'node:url'
import vue from '@vitejs/plugin-vue'
import AutoImport from 'unplugin-auto-import/vite'
import Components from 'unplugin-vue-components/vite'
import { ElementPlusResolver } from 'unplugin-vue-components/resolvers'

// https://vite.dev/config/
export default defineConfig({
  plugins: [
    vue(),
    // 自动导入 Vue / Vue Router / Pinia 等常用 API
    AutoImport({
      imports: ['vue', 'vue-router', 'pinia'],
      resolvers: [ElementPlusResolver()],
      dts: 'src/auto-imports.d.ts',
      eslintrc: { enabled: false },
    }),
    // 自动按需导入 Element Plus 组件
    Components({
      resolvers: [ElementPlusResolver()],
      dts: 'src/components.d.ts',
    }),
  ],
  resolve: {
    alias: {
      // 路径别名 @ -> src
      '@': fileURLToPath(new URL('./src', import.meta.url)),
    },
  },
  server: {
    port: 5173,
    proxy: {
      // 后端接口代理: /api -> http://localhost:8001
      '/api': {
        target: 'http://localhost:8001',
        changeOrigin: true,
      },
      // 上传图片静态目录: /uploads -> 后端(main.py 静态挂载)
      '/uploads': {
        target: 'http://localhost:8001',
        changeOrigin: true,
      },
    },
  },
  css: {
    preprocessorOptions: {
      scss: {
        // 暗色主题下静默 Element Plus 的过时警告
        quietDeps: true,
      },
    },
  },
})
