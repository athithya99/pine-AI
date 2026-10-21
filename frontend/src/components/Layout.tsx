import { useEffect, useState } from 'react'
import { NavLink, Outlet, useLocation } from 'react-router-dom'
import { api } from '../lib/api'
import { useAuth } from '../lib/auth'
import { ROLE_LABEL, isManager, type Role } from '../lib/types'

type NavItem = { to: string; label: string; roles?: Role[]; soon?: boolean; badge?: 'attention' }

const NAV: { section: string; items: NavItem[] }[] = [
  { section: 'Overview', items: [{ to: '/', label: 'Dashboard' }, { to: '/assistant', label: 'Ask AI' }] },
  {
    section: 'Sales',
    items: [
      { to: '/inbox', label: 'Inbox', badge: 'attention' },
      { to: '/orders', label: 'Orders' },
      { to: '/customers', label: 'Customers' },
      { to: '/campaigns', label: 'Campaigns', roles: ['owner', 'admin'] },
    ],
  },
  {
    section: 'Catalog',
    items: [
      { to: '/products', label: 'Products' },
      { to: '/price-lists', label: 'Price lists' },
      { to: '/schemes', label: 'Schemes' },
    ],
  },
  {
    section: 'Finance',
    items: [
      { to: '/invoices', label: 'Invoices' },
      { to: '/receivables', label: 'Receivables' },
      { to: '/collections', label: 'Collections' },
    ],
  },
  {
    section: 'Settings',
    items: [
      { to: '/users', label: 'Team', roles: ['owner', 'admin'] },
      { to: '/whatsapp', label: 'WhatsApp', roles: ['owner', 'admin'] },
      { to: '/faq', label: 'Bot FAQ', roles: ['owner', 'admin'] },
      { to: '/payment-settings', label: 'Payments', roles: ['owner', 'admin', 'accounts'] },
      { to: '/ai-usage', label: 'AI usage', roles: ['owner', 'admin'] },
      { to: '/settings', label: 'Company' },
    ],
  },
]

export function Layout() {
  const { me, logout } = useAuth()
  const [menuOpen, setMenuOpen] = useState(false)
  const [attention, setAttention] = useState(0)
  const loc = useLocation()
  useEffect(() => {  // chats the bot handed over to a person (FR-M10-05)
    const load = () => api.get<{ count: number }>('/inbox/attention').then((r) => setAttention(r.data.count)).catch(() => {})
    load()
    const t = setInterval(load, 60000)
    return () => clearInterval(t)
  }, [loc.pathname])

  const sidebar = (
    <nav className="flex h-full flex-col">
      <div className="flex items-center gap-2 px-5 py-5">
        <div className="grid h-8 w-8 place-items-center rounded-lg bg-pine-600 text-sm font-bold text-white">P</div>
        <div>
          <div className="text-sm font-semibold leading-tight text-white">Pinesphere</div>
          <div className="text-[11px] leading-tight text-pine-100/70">AI Commerce</div>
        </div>
      </div>
      <div className="flex-1 space-y-5 overflow-y-auto px-3 pb-4">
        {NAV.map((g) => {
          const items = g.items.filter((i) => !i.roles || (me && i.roles.includes(me.role)))
          if (!items.length) return null
          return (
            <div key={g.section}>
              <div className="px-2 pb-1 text-[11px] font-semibold uppercase tracking-wider text-pine-100/50">{g.section}</div>
              {items.map((i) =>
                i.soon ? (
                  <div key={i.to} className="flex items-center justify-between rounded-lg px-2 py-1.5 text-sm text-pine-100/40">
                    {i.label}<span className="text-[10px] uppercase">soon</span>
                  </div>
                ) : (
                  <NavLink
                    key={i.to}
                    to={i.to}
                    end={i.to === '/'}
                    onClick={() => setMenuOpen(false)}
                    className={({ isActive }) =>
                      `block rounded-lg px-2 py-1.5 text-sm ${isActive ? 'bg-white/10 font-medium text-white' : 'text-pine-100/80 hover:bg-white/5 hover:text-white'}`}
                  >
                    <span className="flex items-center justify-between">{i.label}
                      {i.badge === 'attention' && attention > 0 && <span className="rounded-full bg-amber-400 px-1.5 text-[11px] font-semibold text-pine-900" title="Chats the bot handed to a person">{attention}</span>}</span>
                  </NavLink>
                ),
              )}
            </div>
          )
        })}
      </div>
      <div className="border-t border-white/10 px-5 py-4">
        <div className="truncate text-sm font-medium text-white">{me?.name}</div>
        <div className="truncate text-xs text-pine-100/60">{me?.tenant?.name} · {me && ROLE_LABEL[me.role]}</div>
        <button onClick={logout} className="mt-2 text-xs text-pine-100/70 hover:text-white">Sign out</button>
      </div>
    </nav>
  )

  return (
    <div className="min-h-screen md:flex">
      <aside className="hidden w-60 shrink-0 bg-pine-900 md:block md:h-screen md:sticky md:top-0">{sidebar}</aside>
      <header className="flex items-center justify-between bg-pine-900 px-4 py-3 md:hidden">
        <span className="text-sm font-semibold text-white">Pinesphere AI Commerce</span>
        <button className="text-white" onClick={() => setMenuOpen(true)} aria-label="Open menu">☰</button>
      </header>
      {menuOpen && (
        <div className="fixed inset-0 z-40 md:hidden">
          <div className="absolute inset-0 bg-slate-900/40" onClick={() => setMenuOpen(false)} />
          <aside className="relative h-full w-64 bg-pine-900">{sidebar}</aside>
        </div>
      )}
      <main className="min-w-0 flex-1 px-4 py-6 md:px-8">
        <Outlet />
      </main>
    </div>
  )
}

export function RequireManager({ children }: { children: React.ReactNode }) {
  const { me } = useAuth()
  if (!isManager(me?.role)) return <p className="text-sm text-slate-500">You don't have access to this page.</p>
  return <>{children}</>
}
