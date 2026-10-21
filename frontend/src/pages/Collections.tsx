import { useCallback, useEffect, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { ConfirmProposal, CreateLink, RecordPayment } from '../components/PaymentDialogs'
import { Empty, PageHeader, Pagination, useToast } from '../components/ui'
import { api, download, errorMessage } from '../lib/api'
import { useAuth } from '../lib/auth'
import {
  PAYMENT_MODE_LABEL, PAYMENT_STATUS, canCollect, inr, isPaymentsFull,
  type AgeingRow, type CollectionsDash, type Page, type Payment, type PaymentLinkT,
} from '../lib/types'
import { fmtDate } from './Orders'

const TABS = [
  { key: 'confirm', label: 'To confirm' },
  { key: 'payments', label: 'Payments' },
  { key: 'cheques', label: 'Cheques pending' },
  { key: 'ageing', label: 'Ageing' },
  { key: 'links', label: 'Payment links' },
  { key: 'reminders', label: 'Reminders' },
] as const

export function PaymentStatusBadge({ s }: { s: Payment['status'] }) {
  const [label, cls] = PAYMENT_STATUS[s]
  return <span className={`badge ${cls}`}>{label}</span>
}

function PaymentsTable({ rows, actions }: { rows: Payment[]; actions?: (p: Payment) => React.ReactNode }) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[820px]">
        <thead className="bg-slate-50"><tr><th className="th">Receipt</th><th className="th">Customer</th><th className="th">Mode</th>
          <th className="th text-right">Amount</th><th className="th">Adjusted against</th><th className="th">Status</th>{actions && <th className="th" />}</tr></thead>
        <tbody className="divide-y divide-slate-100">
          {rows.map((p) => (
            <tr key={p.id}>
              <td className="td"><div className="font-mono text-xs font-medium">{p.number}</div><div className="text-xs text-slate-500">{fmtDate(p.payment_date)}</div></td>
              <td className="td font-medium">{p.customer_id ? <Link to={`/customers/${p.customer_id}/ledger`} className="hover:underline">{p.customer_name}</Link> : '—'}</td>
              <td className="td text-slate-600">{PAYMENT_MODE_LABEL[p.mode]}{p.reference && <div className="font-mono text-xs text-slate-500">{p.reference}</div>}
                {p.source === 'ai_proof' && <div className="text-xs text-amber-700">from buyer's message</div>}</td>
              <td className="td text-right font-medium tabular-nums">{inr(p.amount)}</td>
              <td className="td text-xs text-slate-600">
                {p.allocations.map((a) => <div key={a.invoice_id}><Link to={`/invoices/${a.invoice_id}`} className="font-mono text-pine-700">{a.invoice_number}</Link> {inr(a.amount)}</div>)}
                {Number(p.unallocated) > 0 && <div className="text-pine-700">Advance {inr(p.unallocated)}</div>}
              </td>
              <td className="td"><PaymentStatusBadge s={p.status} />
                {p.duplicate_of && <div className="text-xs text-red-600">Duplicate of {p.duplicate_of}</div>}
                {p.status_reason && <div className="text-xs text-slate-500">{p.status_reason}</div>}
                {p.status === 'confirmed' && !p.receipt_sent && p.receipt_note && <div className="max-w-[180px] text-xs text-amber-700" title={p.receipt_note}>Receipt not sent</div>}</td>
              {actions && <td className="td whitespace-nowrap text-right">{actions(p)}</td>}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

export function Collections() {
  const { me } = useAuth()
  const full = isPaymentsFull(me?.role)
  const collect = canCollect(me?.role)
  const canRecord = full || me?.role === 'sales'
  const toast = useToast()
  const [params, setParams] = useSearchParams()
  const tab = params.get('tab') ?? 'confirm'
  const [dash, setDash] = useState<CollectionsDash | null>(null)
  const [payments, setPayments] = useState<Page<Payment>>({ items: [], total: 0, page: 1, size: 25 })
  const [page, setPage] = useState(1)
  const [ageing, setAgeing] = useState<AgeingRow[]>([])
  const [links, setLinks] = useState<PaymentLinkT[]>([])
  const [reminders, setReminders] = useState<{ items: any[]; total: number }>({ items: [], total: 0 })
  const [recordOpen, setRecordOpen] = useState(false)
  const [linkOpen, setLinkOpen] = useState(false)
  const [confirming, setConfirming] = useState<Payment | null>(null)

  const load = useCallback(() => {
    api.get<CollectionsDash>('/collections/dashboard').then((r) => setDash(r.data)).catch(() => {})
    if (tab === 'confirm' || tab === 'payments' || tab === 'cheques') {
      const status = tab === 'confirm' ? ['proposed'] : tab === 'cheques' ? ['pending_clearance'] : undefined
      api.get<Page<Payment>>('/payments', { params: { status, page, size: 25 }, paramsSerializer: { indexes: null } }).then((r) => setPayments(r.data))
    }
    if (tab === 'ageing') api.get<AgeingRow[]>('/collections/ageing').then((r) => setAgeing(r.data))
    if (tab === 'links') api.get<PaymentLinkT[]>('/payment-links').then((r) => setLinks(r.data))
    if (tab === 'reminders') api.get('/collections/reminders').then((r) => setReminders(r.data))
  }, [tab, page])
  useEffect(() => { load() }, [load])

  const act = async (path: string, body: object | null, done: string) => {
    try { await api.post(path, body); toast(done); load() } catch (e) { toast(errorMessage(e), 'error') }
  }
  const runReminders = async () => {
    try {
      const { data } = await api.post('/collections/reminders/run')
      toast(data.customers ? `Reminders: ${data.sent} sent, ${data.skipped} skipped` : 'No reminders due today'); setParams({ tab: 'reminders' }); load()
    } catch (e) { toast(errorMessage(e), 'error') }
  }

  const cards = dash ? [
    { label: 'Total outstanding', value: inr(dash.total_outstanding) },
    { label: 'Overdue', value: inr(dash.overdue), warn: Number(dash.overdue) > 0 },
    { label: 'Collected this month', value: inr(dash.collected_this_month), sub: `Link ${inr(dash.collected_via_link)} · Offline ${inr(dash.collected_offline)}` },
    { label: 'Waiting for you', value: `${dash.to_confirm.count} to confirm`, sub: `${dash.cheques_pending.count} cheque(s) pending · ${inr(dash.cheques_pending.amount)}` },
  ] : []

  return (
    <>
      <PageHeader title="Collections" subtitle="Reminders, payment links, payments and what's still due."
        actions={<>
          {collect && <button className="btn-secondary" onClick={runReminders} title="Runs automatically every morning; never sends the same step twice">Send today's reminders</button>}
          {collect && <button className="btn-secondary" onClick={() => setLinkOpen(true)}>Payment link</button>}
          {canRecord && <button className="btn-primary" onClick={() => setRecordOpen(true)}>Record payment</button>}
        </>} />

      <div className="mb-4 grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        {cards.map((c) => (
          <div key={c.label} className={`card p-4 ${c.warn ? 'border-red-200' : ''}`}>
            <div className="text-sm text-slate-500">{c.label}</div>
            <div className={`mt-1 text-2xl font-semibold ${c.warn ? 'text-red-600' : 'text-slate-900'}`}>{c.value}</div>
            {c.sub && <div className="mt-0.5 text-xs text-slate-500">{c.sub}</div>}
          </div>
        ))}
      </div>

      <div className="mb-3 flex gap-1 overflow-x-auto border-b border-slate-200">
        {TABS.map((t) => {
          const n = t.key === 'confirm' ? dash?.to_confirm.count : t.key === 'cheques' ? dash?.cheques_pending.count : 0
          return (
            <button key={t.key} onClick={() => { setParams({ tab: t.key }); setPage(1) }}
              className={`-mb-px whitespace-nowrap border-b-2 px-3 py-2 text-sm ${tab === t.key ? 'border-pine-600 font-medium text-pine-700' : 'border-transparent text-slate-500 hover:text-slate-800'}`}>
              {t.label}{n ? <span className="ml-1.5 rounded-full bg-amber-100 px-1.5 text-xs text-amber-800">{n}</span> : null}
            </button>
          )
        })}
      </div>

      <div className="card">
        {(tab === 'confirm' || tab === 'payments' || tab === 'cheques') && <>
          {tab === 'confirm' && <p className="border-b border-slate-100 px-4 py-2 text-xs text-slate-500">Payments buyers told us about on WhatsApp (screenshot or message). They count only after Accounts, Admin or Owner confirm.</p>}
          <PaymentsTable rows={payments.items} actions={(p) => <>
            {p.status === 'proposed' && collect && <button className="btn-primary px-2.5 py-1 text-xs" onClick={() => setConfirming(p)}>Review</button>}
            {p.status === 'pending_clearance' && full && <>
              <button className="btn-secondary mr-1 px-2.5 py-1 text-xs" onClick={() => act(`/payments/${p.id}/clear`, null, 'Cheque cleared')}>Cleared</button>
              <button className="btn-danger px-2.5 py-1 text-xs" onClick={() => { const r = window.prompt('Why did it bounce?'); if (r) act(`/payments/${p.id}/bounce`, { reason: r }, 'Marked bounced') }}>Bounced</button></>}
            {p.status === 'confirmed' && p.mode === 'cheque' && full && <button className="text-xs text-red-600 hover:underline" onClick={() => { const r = window.prompt('Why did it bounce?'); if (r) act(`/payments/${p.id}/bounce`, { reason: r }, 'Marked bounced; balance restored') }}>Bounced</button>}
          </>} />
          {payments.total === 0 && <Empty title={tab === 'confirm' ? 'Nothing to confirm' : tab === 'cheques' ? 'No cheques waiting' : 'No payments yet'} />}
          <Pagination page={page} size={25} total={payments.total} onPage={setPage} />
        </>}

        {tab === 'ageing' && <>
          <div className="flex justify-end border-b border-slate-100 p-3"><button className="btn-secondary" onClick={() => download('/collections/ageing/export', 'ageing.xlsx')}>Export</button></div>
          <div className="overflow-x-auto">
            <table className="w-full min-w-[860px]">
              <thead className="bg-slate-50"><tr><th className="th">Customer</th><th className="th text-right">Not due</th><th className="th text-right">0–30</th>
                <th className="th text-right">31–60</th><th className="th text-right">61–90</th><th className="th text-right">90+</th><th className="th text-right">Total</th><th className="th" /></tr></thead>
              <tbody className="divide-y divide-slate-100">
                {ageing.map((r) => (
                  <tr key={r.customer_id}>
                    <td className="td"><Link to={`/customers/${r.customer_id}/ledger`} className="font-medium hover:underline">{r.customer_name}</Link>
                      <div className="text-xs text-slate-500">{r.invoices} invoice(s){r.reminders_paused && ' · reminders paused'}</div></td>
                    {(['not_due', 'd0_30', 'd31_60', 'd61_90', 'd90_plus'] as const).map((b, i) => (
                      <td key={b} className={`td text-right tabular-nums ${Number(r[b]) ? (i >= 2 ? 'font-medium text-red-600' : i === 1 ? 'text-amber-700' : '') : 'text-slate-300'}`}>{Number(r[b]) ? inr(r[b]) : '—'}</td>
                    ))}
                    <td className="td text-right font-semibold tabular-nums">{inr(r.total)}</td>
                    <td className="td text-right">{collect && <button className="text-xs text-pine-700 hover:underline" onClick={async () => {
                      try { await api.post('/payment-links', { customer_id: r.customer_id, send: true }); toast('Payment link sent') } catch (e) { toast(errorMessage(e), 'error') }
                    }}>Send link</button>}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {ageing.length === 0 && <Empty title="Nothing is due" />}
        </>}

        {tab === 'links' && <>
          <div className="overflow-x-auto">
            <table className="w-full min-w-[760px]">
              <thead className="bg-slate-50"><tr><th className="th">Created</th><th className="th">Customer</th><th className="th text-right">Amount</th><th className="th">Link</th><th className="th">Status</th><th className="th" /></tr></thead>
              <tbody className="divide-y divide-slate-100">
                {links.map((l) => (
                  <tr key={l.id}>
                    <td className="td text-slate-600">{fmtDate(l.created_at, true)}<div className="text-xs">{l.purpose === 'reminder' ? 'From reminder' : 'Manual'}</div></td>
                    <td className="td font-medium">{l.customer_name}</td>
                    <td className="td text-right tabular-nums">{inr(l.amount)}</td>
                    <td className="td max-w-[220px] truncate text-xs"><a href={l.short_url ?? '#'} target="_blank" rel="noreferrer" className="text-pine-700 underline">{l.short_url}</a></td>
                    <td className="td"><span className={`badge ${l.status === 'paid' ? 'bg-emerald-50 text-emerald-700' : l.status === 'created' ? 'bg-sky-50 text-sky-700' : 'bg-slate-100 text-slate-500'}`}>{l.status === 'created' ? 'Open' : l.status}</span>
                      {l.gateway === 'simulator' && <div className="text-[10px] text-slate-400">simulator</div>}</td>
                    <td className="td whitespace-nowrap text-right text-xs">{l.status === 'created' && collect && <>
                      <button className="mr-2 text-pine-700 hover:underline" onClick={() => act(`/payment-links/${l.id}/send`, null, 'Sent on WhatsApp')}>Send</button>
                      {l.gateway === 'simulator' && <button className="mr-2 text-pine-700 hover:underline" onClick={() => act(`/payment-links/${l.id}/simulate-paid`, null, 'Marked paid (simulator)')}>Simulate paid</button>}
                      <button className="text-red-600 hover:underline" onClick={() => act(`/payment-links/${l.id}/cancel`, null, 'Link cancelled')}>Cancel</button></>}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {links.length === 0 && <Empty title="No payment links yet" />}
        </>}

        {tab === 'reminders' && <>
          <p className="border-b border-slate-100 px-4 py-2 text-xs text-slate-500">Reminders go out every morning on the days set in <Link to="/payment-settings" className="underline">Payment settings</Link>. Each step is sent once per invoice.</p>
          <div className="overflow-x-auto">
            <table className="w-full min-w-[700px]">
              <thead className="bg-slate-50"><tr><th className="th">Date</th><th className="th">Customer</th><th className="th">Invoice</th><th className="th">Step</th><th className="th">Result</th></tr></thead>
              <tbody className="divide-y divide-slate-100">
                {reminders.items.map((r) => (
                  <tr key={r.id}>
                    <td className="td text-slate-600">{fmtDate(r.run_date)}</td>
                    <td className="td font-medium">{r.customer_name}</td>
                    <td className="td font-mono text-xs"><Link to={`/invoices/${r.invoice_id}`} className="text-pine-700">{r.invoice_number}</Link></td>
                    <td className="td">{r.step}</td>
                    <td className="td text-xs">{r.sent ? <span className="text-emerald-700">Sent on WhatsApp</span> : <span className="text-amber-700">{r.note}</span>}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {reminders.items.length === 0 && <Empty title="No reminders sent yet" />}
        </>}
      </div>

      <RecordPayment open={recordOpen} onClose={() => setRecordOpen(false)} onDone={() => { setParams({ tab: 'payments' }); load() }} />
      <CreateLink open={linkOpen} onClose={() => setLinkOpen(false)} onDone={() => { setParams({ tab: 'links' }); load() }} />
      <ConfirmProposal payment={confirming} onClose={() => setConfirming(null)} onDone={load} />
    </>
  )
}
