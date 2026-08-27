import { Activity, BookOpenCheck, Bot, ClipboardCheck, FileStack, Gauge, LogOut, Menu, ShieldCheck, Target, X } from 'lucide-react'
import { useState } from 'react'
import { NavLink, Outlet, useLocation } from 'react-router-dom'
import { useAuth } from '../features/auth/AuthContext'

const navigation = [
  { to: '/', label: '闭环总览', icon: Gauge },
  { to: '/practice', label: '今日执行', icon: Target },
  { to: '/plan', label: '七日计划', icon: ClipboardCheck },
  { to: '/true-exams', label: '院校真题', icon: BookOpenCheck },
  { to: '/mock-exams', label: '模拟组卷', icon: FileStack },
  { to: '/agent', label: 'Agent 轨迹', icon: Bot },
]

export function AppShell() {
  const { roles, logout } = useAuth()
  const [menuOpen, setMenuOpen] = useState(false)
  const location = useLocation()
  const items = roles.some((role) => role === 'REVIEWER' || role === 'ADMIN')
    ? [...navigation, { to: '/review', label: '候选题审核', icon: ShieldCheck }]
    : navigation

  return (
    <div className="app-shell">
      <button className="mobile-menu" onClick={() => setMenuOpen((value) => !value)} aria-label="打开导航">
        {menuOpen ? <X /> : <Menu />}
      </button>
      <aside className={`side-rail ${menuOpen ? 'side-rail--open' : ''}`}>
        <div className="brand-lockup brand-lockup--rail">
          <span className="brand-mark"><Activity size={20} /></span>
          <div><strong>闭环</strong><small>自动控制复习台</small></div>
        </div>
        <nav aria-label="主导航">
          {items.map(({ to, label, icon: Icon }) => (
            <NavLink key={to} to={to} end={to === '/'} onClick={() => setMenuOpen(false)}>
              <Icon size={18} /><span>{label}</span>
            </NavLink>
          ))}
        </nav>
        <div className="side-rail__footer">
          <div className="system-status"><span />Agent Runtime 在线</div>
          <button onClick={() => void logout()}><LogOut size={17} />退出登录</button>
        </div>
      </aside>
      <main className="workspace" key={location.pathname}>
        <Outlet />
      </main>
    </div>
  )
}
