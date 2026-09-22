// 渲染层入口：挂载 React 根组件并注入全局样式
import { createRoot } from 'react-dom/client'
import App from './App'
import './styles.css'

createRoot(document.getElementById('root')!).render(<App />)
