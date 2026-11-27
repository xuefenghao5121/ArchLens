import { Link, NavLink, Outlet, useLocation } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { api } from '../api/client'
import { useStateQuery } from '../features/shared/useAction'
import { useOperator } from '../features/tasks/useOperator'
import './app.css'

const NAV = [
  { to: '/', label: '研究总览', match: (p: string) => p === '/' || p.startsWith('/decisions') },
  { to: '/flow', label: '流程编排', match: (p: string) => p.startsWith('/flow') || p.startsWith('/layer') || p.startsWith('/layers') },
  { to: '/evidence', label: '证据与回溯', match: (p: string) => p.startsWith('/evidence') },
  { to: '/evolution', label: '演进中心', match: (p: string) => p.startsWith('/evolution') },
  { to: '/agents', label: 'Agent 管理', match: (p: string) => p.startsWith('/agents') },
  { to: '/shelf', label: '技能与工具', match: (p: string) => p.startsWith('/shelf') },
  { to: '/templates', label: '编排配置', match: (p: string) => p.startsWith('/templates') },
]

export default function AppShell() {
  const location = useLocation()
  // 顶栏环境徽章据实显示：服务端不设 WF_ROOT 时连的就是真实 store，不能再写死 sandbox
  const { data, isError } = useStateQuery()
  const { data: runtime } = useQuery({
    queryKey: ['runtime'], queryFn: api.runtime, refetchInterval: 5000,
  })
  const [agent, setAgent] = useOperator()
  const isolated = data?.store_isolated === true
  const badge = !data
    ? (isError ? '后端未连接' : '连接中…')
    : (isolated ? '隔离副本' : '真实 store')

  return (
    <div className="shell">
      <aside className="sidebar">
        <div className="brand">
          <span className="brand-dot" /> OKComputer
          <div className="brand-sub">微架构混合仿真研究工作台</div>
        </div>
        <nav className="nav">
          {NAV.map((n) => (
            <NavLink
              key={n.to}
              to={n.to}
              className={'nav-item' + (n.match(location.pathname) ? ' active' : '')}
            >
              {n.label}
            </NavLink>
          ))}
        </nav>
        <div className="sidebar-foot" title={data?.store_root}>
          <span className="env-dot" /> {data ? `store：${data.store_root}` : '接 wf.py 后端'}
        </div>
      </aside>
      <div className="main">
        <header className="topbar">
          <span className="topbar-title">通用微架构混合仿真 Agent 工作流</span>
          {/* 操作人 / 署名：写操作全部记名；批准、记决定、封板都以它署名 */}
          <label className="topbar-agent" title="写操作以这个身份记入证据图与生命周期历史；批准、记决定、封板都以它署名">
            操作人
            <input
              value={agent}
              placeholder="填你的名字"
              onChange={(e) => setAgent(e.target.value)}
              spellCheck={false}
            />
          </label>
          <span
            className={'topbar-env' + (data && !isolated ? ' real' : '')}
            title={data ? `${data.store_root}\n写操作${isolated ? '落在隔离副本上' : '直接落在真实 store 上'}` : undefined}
          >
            {badge}
          </span>
          {runtime?.active_job && (
            <Link
              className="topbar-job"
              to={`/layer/${encodeURIComponent(runtime.active_job.package_id)}`}
              title="平台后台作业：离开页面也会继续跑，日志写在 platform/runtime/jobs/"
            >
              作业运行中 · {runtime.active_job.layer}
            </Link>
          )}
        </header>
        <main className="content">
          <Outlet />
        </main>
      </div>
    </div>
  )
}
