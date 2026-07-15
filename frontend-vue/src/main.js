import { createApp } from 'vue'
import { createPinia } from 'pinia'
import ElementPlus from 'element-plus'
import 'element-plus/dist/index.css'
import './styles/theme.css'
import './styles/content.css'
import './styles/overlays.css'
import App from './App.vue'

// 入参: 无
// 方法: 装配 Pinia + ElementPlus 后挂载根组件
// 出参: 无（副作用：挂载 #app）
createApp(App)
  .use(createPinia())
  .use(ElementPlus)
  .mount('#app')
