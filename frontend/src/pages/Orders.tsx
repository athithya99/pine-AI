import { useCallback, useEffect, useState } from 'react'
import { Link, useNavigate, useSearchParams } from 'react-router-dom'
import { ImportDialog } from '../components/ImportDialog'
import { Empty, PageHeader, Pagination } from '../components/ui'
import { api, download } from '../lib/api'
import { useAuth } from '../lib/auth'
import {
  ORDER_STATUS_BADGE, ORDER_STATUS_LABEL, inr, isManager,
  type OrderStatus, type OrderSummary, type Page, type User,
} from '../lib/types'

const SIZE = 25

// tabs group statuses the way staff work through them
const TABS: { key: string; label: string; statuses: OrderStatus[] }[] = [
  { key: 'all', label: 'All', statuses: [] },
  { key: 'approval', label: 'Needs approval', statuses: ['on_hold'] },
  { key: 'open', label: 'Open', statuses: ['draft', 'quoted', 'confirmed'] },
  { key: 'to_invoice', label: 'To invoice', statuses: ['approved'] },
  { key: 'to_ship', label: 'To dispatch', statuses: ['invoiced', 'dispatched'] },
  { key: 'done', label: 'Delivered', statuses: ['delivered'] },
  { key: 'closed', label: 'Expired / cancelled', statuses: ['expired', 'cancelled'] },
]

export function StatusBadge({ status }: { status: OrderStatus }) {
  return <span className={`badge ${ORDER_STATUS_BADGE[status]}`}>{ORDER_STATUS_LABEL[status]}</span>
}

export const fmtDate = (iso: string | null, withTime = false) =>
  iso ? new Date(iso).toLocaleString('en-IN', {
    day: 'numeric', month: 'short', ...(withTime ? { hour: 'numeric', minute: '2-digit' } : {}),
  }) : '—'

export function Orders() {
  const { me } = useAuth()
  const manager = isManager(me?.role)
  const canCreate = manager || me?.role === 'sales'
  const navigate = useNavigate()
  const [params, setParams] = useSearchParams()

  const tab = params.get('tab') ?? 'all'
  const customerId = params.get('customer') ?? ''
  const [q, setQ] = useState('')
  const [source, setSource] = useState('')
  const [salesUser, setSalesUser] = useState('')
  const [from, setFrom] = useState('')
  const [to, setTo] = useState('')
  const [page, setPage] = useState(1)
  const [data, setData] = useState<Page<OrderSummary>>({ items: [], total: 0, page: 1, size: SIZE })
  const [counts, setCounts] = useState<Record<string, number>>({})
  const [loading, setLoading] = useState(true)
  const [salesUsers, setSalesUsers] = useState<User[]>([])
  const [importOpen, setImportOpen] = useState(false)

  const statuses = TABS.find((t) => t.key === tab)?.statuses ?? []
  const filters = {
    status: statuses.length ? statuses : undefined, q: q || undefined, source: source || undefined,
    sales_user_id: salesUser || undefined, date_from: from || undefined, date_to: to || undefined,
    customer_id: customerId || undefined,
  }

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const [list, c] = await Promise.all([
        api.get<Page<OrderSummary>>('/orders', { params: { ...filters, page, size: SIZE }, paramsSerializer: { indexes: null } }),
        api.get<Record<string, number>>('/orders/counts'),
      ])
      setData(list.data)
      setCounts(c.data)
    } finally { setLoading(false) }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tab, q, source, salesUser, from, to, customerId, page])

  useEffect(() => { const t = setTimeout(load, 250); return () => clearTimeout(t) }, [load])
  useEffect(() => {
    if (manager) api.get<User[]>('/users', { params: { role: 'sales', active: true } }).then((r) => setSalesUsers(r.data)).catch(() => {})
  }, [manager])

  const tabCount = (statuses: OrderStatus[]) =>
    statuses.length ? statuses.reduce((n, s) => n + (counts[s] ?? 0), 0) : Object.values(counts).reduce((a, b) => a + b, 0)

  const setTab = (key: string) => {
    const next = new URLSearchParams(params)
    if (key === 'all') next.delete('tab'); else next.set('tab', key)
    setParams(next); setPage(1)
  }

  const exportFile = async () => {
    const usp = new URLSearchParams()
    Object.entries(filters).forEach(([k, v]) => {
      if (Array.isArray(v)) v.forEach((x) => usp.append(k, x)); else if (v) usp.set(k, v)
    })
    await download(`/orders/export?${usp.toString()}`, 'orders.xlsx')
  }

  return (
    <>
      <PageHeader
        title="Orders"
        subtitle="Every order from WhatsApp, this panel or Excel, in one flow from quote to delivery."
        actions={<>
          <button className="btn-secondary" onClick={exportFile}>Export</button>
          {canCreate && <button className="btn-secondary" onClick={() => setImportOpen(true)}>Import Excel</button>}
          {canCreate && <button className="btn-primary" onClick={() => navigate('/orders/new')}>New order</button>}
        </>}
      />

      <div className="mb-3 flex gap-1 overflow-x-auto border-b border-slate-200">
        {TABS.map((t) => {
          const n = tabCount(t.statuses)
          const active = tab === t.key
          return (
            <button key={t.key} onClick={() => setTab(t.key)}
              className={`-mb-px whitespace-nowrap border-b-2 px-3 py-2 text-sm ${active ? 'border-pine-600 font-medium text-pine-700' : 'border-transparent text-slate-500 hover:text-slate-800'}`}>
              {t.label}
              {n > 0 && <span className={`ml-1.5 rounded-full px-1.5 text-xs ${t.key === 'approval' ? 'bg-amber-100 text-amber-800' : 'bg-slate-100 text-slate-600'}`}>{n}</span>}
            </button>
          )
        })}
      </div>

      <div className="card">
        <div className="flex flex-wrap items-center gap-2 border-b border-slate-200 p-3">
          <input className="input max-w-xs" placeholder="Search order no., customer or phone" value={q}
            onChange={(e) => { setQ(e.target.value); setPage(1) }} />
          <select className="input max-w-[150px]" value={source} onChange={(e) => { setSource(e.target.value); setPage(1) }}>
            <option value="">All sources</option>
            <option value="whatsapp">WhatsApp</option>
            <option value="panel">Panel</option>
            <option value="excel">Excel</option>
          </select>
          {manager && (
            <select className="input max-w-[170px]" value={salesUser} onChange={(e) => { setSalesUser(e.target.value); setPage(1) }}>
              <option value="">All sales users</option>
              {salesUsers.map((u) => <option key={u.id} value={u.id}>{u.name}</option>)}
            </select>
          )}
          <label className="flex items-center gap-1 text-xs text-slate-500">From
            <input type="date" className="input w-auto" value={from} onChange={(e) => { setFrom(e.target.value); setPage(1) }} />
          </label>
          <label className="flex items-center gap-1 text-xs text-slate-500">To
            <input type="date" className="input w-auto" value={to} onChange={(e) => { setTo(e.target.value); setPage(1) }} />
          </label>
          {customerId && (
            <button className="badge bg-pine-50 text-pine-700" onClick={() => { const n = new URLSearchParams(params); n.delete('customer'); setParams(n) }}>
              One customer ✕
            </button>
          )}
        </div>
        <div className="overflow-x-auto">
          <table className="w-full min-w-[820px]">
            <thead className="bg-slate-50">
              <tr>
                <th className="th">Order</th><th className="th">Customer</th><th className="th">Status</th>
                <th className="th text-right">Total</th><th className="th">Source</th>
                {me?.role !== 'sales' && <th className="th">Sales</th>}
                <th className="th">Date</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {data.items.map((o) => (
                <tr key={o.id} className="cursor-pointer hover:bg-slate-50" onClick={() => navigate(`/orders/${o.id}`)}>
                  <td className="td">
                    <Link to={`/orders/${o.id}`} className="whitespace-nowrap font-mono text-xs font-medium text-pine-700" onClick={(e) => e.stopPropagation()}>{o.number}</Link>
                    <div className="text-xs text-slate-500">{o.line_count} item{o.line_count === 1 ? '' : 's'}</div>
                  </td>
                  <td className="td font-medium text-slate-900">{o.customer_name}</td>
                  <td className="td">
                    <StatusBadge status={o.status} />
                    {o.hold_reasons && <div className="mt-1 max-w-[260px] truncate text-xs text-amber-700" title={o.hold_reasons}>{o.hold_reasons}</div>}
                    {o.status === 'quoted' && o.quote_valid_until && <div className="mt-1 text-xs text-slate-500">valid till {fmtDate(o.quote_valid_until, true)}</div>}
                  </td>
                  <td className="td text-right tabular-nums">{inr(o.grand_total)}</td>
                  <td className="td capitalize text-slate-600">{o.source === 'panel' ? 'Panel' : o.source === 'excel' ? 'Excel' : 'WhatsApp'}</td>
                  {me?.role !== 'sales' && <td className="td text-slate-600">{o.sales_user_name ?? '—'}</td>}
                  <td className="td text-slate-600">{fmtDate(o.created_at, true)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {!loading && data.total === 0 && (
          <Empty title={tab === 'approval' ? 'Nothing waiting for approval' : 'No orders here yet'}>
            {canCreate && tab === 'all' ? 'Create one with New order, or import a batch from Excel.' : null}
          </Empty>
        )}
        <Pagination page={page} size={SIZE} total={data.total} onPage={setPage} />
      </div>

      <ImportDialog open={importOpen} onClose={() => setImportOpen(false)} title="Import orders"
        uploadPath="/orders/import" templatePath="/orders/import/template" templateName="order_import_template.xlsx"
        columnsHint="One row per item. Rows with the same order_ref become one order. Orders are created as drafts; an order with any bad row is skipped."
        onDone={load} />
    </>
  )
}
