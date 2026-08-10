import { createApp } from 'vue'
import { createPinia } from 'pinia'
import App from './App.vue'
import router from './router'
// Element Plus 暗色主题变量
import 'element-plus/theme-chalk/dark/css-vars.css'
// ElMessage / ElMessageBox 通过 JS 调用, unplugin 按需导入不覆盖 JS 用法, 需手动引样式
import 'element-plus/es/components/message/style/css'
import 'element-plus/es/components/message-box/style/css'
// 全局暗色样式
import './styles/main.scss'

// 启用 Element Plus 暗色主题
document.documentElement.classList.add('dark')

const app = createApp(App)
app.use(createPinia())
app.use(router)
app.mount('#app')
