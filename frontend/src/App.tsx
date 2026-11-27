import { HashRouter, Navigate, Route, Routes } from 'react-router-dom'
import AppShell from './app/AppShell'
import Dashboard from './features/dashboard/Dashboard'
import Flow from './features/flow/Flow'
import LayerDetail from './features/layers/LayerDetail'
import Evidence from './features/evidence/Evidence'
import Evolution from './features/evolution/Evolution'
import AgentsPage from './features/config/AgentsPage'
import AgentDetail from './features/config/AgentDetail'
import ShelfPage from './features/config/ShelfPage'
import TemplatesPage from './features/config/TemplatesPage'

// 七个入口：研究总览 / 流程编排 / 证据与回溯 / 演进中心 / Agent 管理 / 技能与工具 / 编排配置。
// 层详情（/layer/…）与单个 agent（/agents/…）不进侧栏：从列表点进去。
export default function App() {
  return (
    <HashRouter>
      <Routes>
        <Route element={<AppShell />}>
          <Route path="/" element={<Dashboard />} />
          <Route path="/flow" element={<Flow />} />
          {/* pkg = new 表示「这一层还没有层包」，其余是层包 ID */}
          <Route path="/layer/:pkg" element={<LayerDetail />} />
          <Route path="/evidence" element={<Evidence />} />
          <Route path="/evolution" element={<Evolution />} />
          <Route path="/agents" element={<AgentsPage />} />
          <Route path="/agents/:target" element={<AgentDetail />} />
          <Route path="/shelf" element={<ShelfPage />} />
          <Route path="/templates" element={<TemplatesPage />} />
          {/* 旧入口：层级工作台并入流程编排 → 层详情；决策中心并入层详情与研究总览 */}
          <Route path="/layers" element={<Navigate to="/flow" replace />} />
          <Route path="/decisions" element={<Navigate to="/" replace />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Route>
      </Routes>
    </HashRouter>
  )
}
