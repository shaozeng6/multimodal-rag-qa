import { createApp } from 'vue';
import { createPinia } from 'pinia';
import App from './App.vue';
import router from './router';
// ElMessage / ElMessageBox 通过 JS 调用, unplugin 按需导入不覆盖 JS 用法, 需手动引样式
import 'element-plus/es/components/message/style/css';
import 'element-plus/es/components/message-box/style/css';
// 全局样式(企业化浅色主题, 见 styles/main.scss token)
import './styles/main.scss';

const app = createApp(App);
app.use(createPinia());
app.use(router);
app.mount('#app');
