import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { PageHeader } from '../components/ui'
import { api } from '../lib/api'
import { useAuth } from '../lib/auth'
import { inr, isManager, type DailySummaryT, type DashboardData } from '../lib/types'

const PERIODS = [
  ['today', 'Today'], ['yesterday', 'Yesterday'], ['last_7_days', 'Last 7 days'], ['this_month', 'This month'],
  ['last_month', 'Last month'], ['last_90_days', 'Last 90 days'], ['this_year', 'This FY'],
] as const

const compact = (v: string | number) => {
  const n = Number(v)
  if (n >= 1e7) return `₹${(n / 1e7).toFixed(1)}Cr`
  if (n >= 1e5) return `₹${(n / 1e5).toFixed(1)}L`
  if (n >= 1e3) return `₹${(n / 1e3).toFixed(1)}K`
  return `₹${Math.round(n)}`
}
const shortDate = (iso: string) => new Date(iso + 'T00:00:00').toLocaleDateString('en-IN', { day: 'numeric', month: 'short' })

/** One series, one hue (brand), bars anchored to the baseline with rounded tops, hover tooltip per bar. */
function DailyBars({ data, field, label }: { data: DashboardData['series']; field: 'order_value' | 'collections'; label: string }) {
  const [hover, setHover] = useState<number | null>(null)
  const values = data.map((d) => Number(d[field]))
  const max = Math.max(...values, 1)
  const W = 560, H = 150, pad = 2, top = 10, left = 52
  const bw = Math.max(2, W / Math.max(values.length, 1) - pad)
  const ticks = [0, max / 2, max]
  const total = values.reduce((a, b) => a + b, 0)
  return (
    <div className="card p-4">
      <div className="mb-2 flex items-baseline justify-between">
        <h3 className="text-sm font-medium text-slate-700">{label} per day</h3>
        <span className="text-xs text-slate-500">Total {inr(total)}</span>
      </div>
      <div className="relative">
        <svg viewBox={`0 0 ${W + left} ${H + top + 22}`} className="w-full" role="img" aria-label={`${label} per day`}>
          {ticks.map((t, i) => {
            const y = top + H - (t / max) * H
            return <g key={i}><line x1={left} x2={W + left} y1={y} y2={y} stroke="#e2e8f0" strokeWidth={1} />
              <text x={left - 6} y={y + 4} textAnchor="end" fontSize={12} fill="#64748b">{compact(t)}</text></g>
          })}
          {values.map((v, i) => {
            const h = (v / max) * H
            const x = left + i * (bw + pad)
            return (
              <g key={i} onMouseEnter={() => setHover(i)} onMouseLeave={() => setHover(null)}>
                <rect x={x - pad / 2} y={top} width={bw + pad} height={H} fill="transparent" />
                {v > 0 && <path d={`M${x},${top + H} v${-Math.max(h - 4, 0)} q0,-4 4,-4 h${Math.max(bw - 8, 0)} q4,0 4,4 v${Math.max(h - 4, 0)} z`}
                  fill={hover === i ? '#185848' : '#1f6f5c'} />}
              </g>
            )
          })}
          {data.length > 0 && <>
            <text x={left} y={top + H + 18} fontSize={12} fill="#64748b">{shortDate(data[0].date)}</text>
            <text x={W + left} y={top + H + 18} fontSize={12} fill="#64748b" textAnchor="end">{shortDate(data[data.length - 1].date)}</text>
          </>}
        </svg>
        {hover !== null && data[hover] && (
          <div className="pointer-events-none absolute top-0 rounded-md bg-slate-900 px-2 py-1 text-xs text-white shadow"
            style={{ left: `min(calc(${((left + hover * (bw + pad)) / (W + left)) * 100}% + 8px), calc(100% - 140px))` }}>
            <div>{shortDate(data[hover].date)}</div>
            <div className="font-medium">{inr(data[hover][field])}{field === 'order_value' ? ` · ${data[hover].orders} order(s)` : ''}</div>
          </div>
        )}
      </div>
    </div>
  )
}

function Tile({ label, value, sub, to, warn }: { label: string; value: string | number; sub?: string; to?: string; warn?: boolean }) {
  const body = <>
    <div className={`text-sm ${warn ? 'text-amber-800' : 'text-slate-500'}`}>{label}</div>
    <div className="mt-1 text-2xl font-semibold tabular-nums text-slate-900">{value}</div>
    {sub && <div className="mt-0.5 text-xs text-slate-500">{sub}</div>}
  </>
  const cls = `card p-4 ${warn ? 'border-amber-300 bg-amber-50' : ''} ${to ? 'hover:border-pine-500' : ''}`
  return to ? <Link to={to} className={cls}>{body}</Link> : <div className={cls}>{body}</div>
}

export function Dashboard() {
  const { me } = useAuth()
  const manager = isManager(me?.role)
  const [period, setPeriod] = useState('this_month')
  const [d, setD] = useState<DashboardData | null>(null)
  const [orders, setOrders] = useState<Record<string, number>>({})
  const [inv, setInv] = useState<Record<string, number>>({})
  const [attention, setAttention] = useState(0)
  const [summary, setSummary] = useState<DailySummaryT | null>(null)

  useEffect(() => {
    api.get<Record<string, number>>('/orders/counts').then((r) => setOrders(r.data)).catch(() => {})
    api.get<Record<string, number>>('/invoices/counts').then((r) => setInv(r.data)).catch(() => {})
    api.get<{ count: number }>('/inbox/attention').then((r) => setAttention(r.data.count)).catch(() => {})
    if (manager) api.get<DailySummaryT[]>('/assistant/summaries', { params: { limit: 1 } }).then((r) => setSummary(r.data[0] ?? null)).catch(() => {})
  }, [manager])
  useEffect(() => { api.get<DashboardData>('/dashboard', { params: { period } }).then((r) => setD(r.data)).catch(() => {}) }, [period])

  const n = (...keys: string[]) => keys.reduce((a, k) => a + (orders[k] ?? 0), 0)
  const todo = [
    { label: 'Orders needing approval', value: n('on_hold'), to: '/orders?tab=approval' },
    { label: 'Chats needing a person', value: attention, to: '/inbox' },
    { label: 'Quotes waiting for buyer', value: n('quoted'), to: '/orders?tab=open' },
    { label: 'Approved, to invoice', value: n('approved'), to: '/orders?tab=to_invoice' },
    { label: 'Uploaded invoices to review', value: inv.review ?? 0, to: '/invoices?tab=review' },
    { label: 'Overdue invoices', value: inv.overdue ?? 0, to: '/invoices?tab=overdue' },
  ].filter((c) => c.value > 0)

  const maxSource = Math.max(...(d?.orders.by_source.map((s) => Number(s.value)) ?? [0]), 1)
  const rate = d?.ai.match_rate

  return (
    <>
      <PageHeader title={`Welcome, ${me?.name?.split(' ')[0] ?? ''}`}
        subtitle={`${me?.tenant?.name ?? ''}${d?.scope === 'own customers' ? ' · your customers' : ''}`}
        actions={<select className="input w-44" value={period} onChange={(e) => setPeriod(e.target.value)} aria-label="Period">
          {PERIODS.map(([k, l]) => <option key={k} value={k}>{l}</option>)}
        </select>} />

      {todo.length > 0 && (
        <div className="mb-6">
          <h2 className="mb-2 text-xs font-semibold uppercase tracking-wide text-slate-500">Needs attention</h2>
          <div className="grid gap-3 sm:grid-cols-3 lg:grid-cols-6">
            {todo.map((c) => <Tile key={c.label} label={c.label} value={c.value} to={c.to} warn />)}
          </div>
        </div>
      )}

      {d && <>
        <div className="mb-4 grid grid-cols-2 gap-3 lg:grid-cols-4">
          <Tile label={`Orders ${d.period.label}`} value={inr(d.orders.value)} sub={`${d.orders.count} order(s) · avg ${inr(d.orders.average)}`} to="/orders" />
          <Tile label={`Collected ${d.period.label}`} value={inr(d.collections.amount)} sub={`${d.collections.count} payment(s)`} to="/collections" />
          <Tile label="Outstanding now" value={inr(d.outstanding.total)} sub={`Overdue ${inr(d.outstanding.overdue)} · ${d.outstanding.overdue_customers} customer(s)`} to="/receivables" />
          <Tile label="AI match rate" value={rate == null ? '—' : `${Math.round(rate * 100)}%`}
            sub={d.ai.whatsapp_orders ? `${d.ai.lines_matched} items matched, ${d.ai.items_asked} asked back · ${d.ai.whatsapp_orders} WhatsApp order(s)` : 'No WhatsApp AI orders in this period'} to="/ai-usage" />
        </div>

        <div className="mb-4 grid gap-4 lg:grid-cols-2">
          <DailyBars data={d.series} field="order_value" label="Order value" />
          <DailyBars data={d.series} field="collections" label="Collections" />
        </div>

        <div className="grid gap-4 lg:grid-cols-3">
          <div className="card p-4">
            <h3 className="mb-3 text-sm font-medium text-slate-700">Orders by source</h3>
            <div className="space-y-3">
              {d.orders.by_source.map((s) => (
                <div key={s.source}>
                  <div className="mb-1 flex justify-between text-xs"><span className="text-slate-700">{s.source}</span>
                    <span className="tabular-nums text-slate-500">{s.orders} · {inr(s.value)}</span></div>
                  <div className="h-2 rounded-full bg-slate-100"><div className="h-2 rounded-full bg-pine-600" style={{ width: `${(Number(s.value) / maxSource) * 100}%` }} /></div>
                </div>
              ))}
            </div>
          </div>
          <div className="card p-4">
            <h3 className="mb-2 text-sm font-medium text-slate-700">Top customers</h3>
            {d.top_customers.length === 0 ? <p className="text-sm text-slate-400">No orders {d.period.label}.</p> : (
              <table className="w-full text-sm"><tbody>
                {d.top_customers.map((c) => <tr key={c.customer_id} className="border-b border-slate-50 last:border-0">
                  <td className="py-1.5 pr-2"><Link to={`/customers/${c.customer_id}/ledger`} className="hover:underline">{c.customer}</Link></td>
                  <td className="py-1.5 text-right tabular-nums text-slate-600">{inr(c.order_value)}</td></tr>)}
              </tbody></table>)}
          </div>
          <div className="card p-4">
            <h3 className="mb-2 text-sm font-medium text-slate-700">Top products</h3>
            {d.top_products.length === 0 ? <p className="text-sm text-slate-400">No orders {d.period.label}.</p> : (
              <table className="w-full text-sm"><tbody>
                {d.top_products.map((p) => <tr key={p.product} className="border-b border-slate-50 last:border-0">
                  <td className="py-1.5 pr-2">{p.product}<span className="ml-1 text-xs text-slate-400">{Number(p.quantity)} {p.uom}</span></td>
                  <td className="py-1.5 text-right tabular-nums text-slate-600">{inr(p.value)}</td></tr>)}
              </tbody></table>)}
          </div>
        </div>
      </>}

      {manager && summary && (
        <div className="card mt-4 p-4">
          <div className="mb-2 flex items-center justify-between">
            <h3 className="text-sm font-medium text-slate-700">Morning summary</h3>
            <Link to="/assistant" className="text-xs text-pine-700 hover:underline">Settings</Link>
          </div>
          <pre className="whitespace-pre-wrap font-sans text-sm text-slate-700">{summary.text}</pre>
          <p className="mt-2 text-xs text-slate-500">{summary.sent_at ? `Sent on WhatsApp to ${summary.sent_to}` : summary.send_error ? `Not sent: ${summary.send_error}` : ''}</p>
        </div>
      )}
    </>
  )
}
