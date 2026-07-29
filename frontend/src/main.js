import { createApp } from 'vue'
import App from './App.vue'

// 1. 引入 Element Plus 核心样式
import ElementPlus from 'element-plus'
import 'element-plus/dist/index.css'

// 2. 引入 Element Plus 所有图标
import * as ElementPlusIconsVue from '@element-plus/icons-vue'

const app = createApp(App)

// 3. 全局注册所有图标
for (const [key, component] of Object.entries(ElementPlusIconsVue)) {
  app.component(key, component)
}

// 4. 注册 Element Plus
app.use(ElementPlus)

app.mount('#app')