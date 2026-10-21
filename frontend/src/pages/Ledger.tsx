import { useCallback, useEffect, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { ImportDialog } from '../components/ImportDialog'
import { CreateLink, RecordPayment } from '../components/PaymentDialogs'
import { Drawer, Empty, ErrorBox, Field, PageHeader, useToast } from '../components/ui'
import { api, errorMessage } from '../lib/api'
import { useAuth } from '../lib/auth'
import { canCollect, inr, isAccounts, isPaymentsFull, type BalanceRow, type Statement } from '../lib/types'

const KIND: Record<string, string> = {
  invoice: 'Invoice', invoice_cancelled: 'Invoice cancelled', credit_note: 'Credit note',
  credit_note_cancelled: 'Credit note cancelled', payment: 'Payment', payment_reversed: 'Payment reversed',
}

const fyStart = () => {
  const d = new Date()
  const y = d.getMonth() >= 3 ? d.getFullYear() : d.getFullYear() - 1
  return `${y}-04-01`
}
const iso = (d: Date) => new Date(d.getTime() - d.getTimezoneOffset() * 60000).toISOString().slice(0, 10)
const dmy = (s: string) => new Date(s + 'T00:00:00').toLocaleDateString('en-IN', { day: 'numeric', month: 'short', year: 'numeric' })

export function Ledger() {
  const { id } = useParams()
  const { me } = useAuth()
  const canWrite = isAccounts(me?.role)
  const toast = useToast()
  const [from, setFrom] = useState(fyStart())
  const [to, setTo] = useState(iso(new Date()))
  const [st, setSt] = useState<Statement | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [cnOpen, setCnOpen] = useState(false)
  const [amount, setAmount] = useState('')
  const [reason, setReason] = useState('')
  const [dlgError, setDlgError] = useState<string | null>(null)
  const [payOpen, setPayOpen] = useState(false)
  const [linkOpen, setLinkOpen] = useState(false)

  const load = useCallback(() => {
    api.get<Statement>(`/customers/${id}/ledger`, { params: { date_from: from, date_to: to } })
      .then((r) => { setSt(r.data); setError(null) }).catch((e) => setError(errorMessage(e)))
  }, [id, from, to])
  useEffect(() => { load() }, [load])

  const pdf = async () => {
    const r = await api.get(`/customers/${id}/ledger.pdf`, { params: { date_from: from, date_to: to }, responseType: 'blob' })
    window.open(URL.createObjectURL(new Blob([r.data], { type: 'application/pdf' })), '_blank')
  }
  const send = async () => {
    try { await api.post(`/customers/${id}/ledger/send`, null, { params: { date_from: from, date_to: to } }); toast('Statement sent on WhatsApp') }
    catch (e) { toast(errorMessage(e), 'error') }
  }
  const creditNote = async () => {
    setDlgError(null)
    try {
      await api.post('/credit-notes', { customer_id: id, amount, reason })
      toast('Credit note issued'); setCnOpen(false); setAmount(''); setReason(''); load()
    } catch (e) { setDlgError(errorMessage(e)) }
  }

  return (
    <>
      <PageHeader title={st ? `Ledger · ${st.customer_name}` : 'Ledger'}
        subtitle="Opening balance + invoices − payments − credit notes, with a running balance."
        actions={<>
          <Link to="/receivables" className="btn-secondary">All balances</Link>
          <button className="btn-secondary" onClick={pdf}>Statement PDF</button>
          <button className="btn-secondary" onClick={send}>Send on WhatsApp</button>
          {canWrite && <button className="btn-secondary" onClick={() => setCnOpen(true)}>Credit note</button>}
          {canCollect(me?.role) && Number(st?.closing ?? 0) > 0 && <button className="btn-secondary" onClick={() => setLinkOpen(true)}>Payment link</button>}
          {(isPaymentsFull(me?.role) || me?.role === 'sales') && <button className="btn-primary" onClick={() => setPayOpen(true)}>Record payment</button>}
        </>} />
      {st && <RecordPayment open={payOpen} onClose={() => setPayOpen(false)} onDone={load} customer={{ id: st.customer_id, business_name: st.customer_name }} />}
      {st && <CreateLink open={linkOpen} onClose={() => setLinkOpen(false)} onDone={load} customer={{ id: st.customer_id, business_name: st.customer_name }} />}
      <ErrorBox message={error} />
      <div className="mb-3 flex flex-wrap items-center gap-2 text-sm">
        <label className="flex items-center gap-1 text-slate-500">From <input type="date" className="input w-auto" value={from} onChange={(e) => setFrom(e.target.value)} /></label>
        <label className="flex items-center gap-1 text-slate-500">To <input type="date" className="input w-auto" value={to} onChange={(e) => setTo(e.target.value)} /></label>
      </div>
      {st && (
        <div className="card">
          <div className="overflow-x-auto">
            <table className="w-full min-w-[720px]">
              <thead className="bg-slate-50"><tr><th className="th">Date</th><th className="th">Particulars</th><th className="th">Ref.</th>
                <th className="th text-right">Debit</th><th className="th text-right">Credit</th><th className="th text-right">Balance</th></tr></thead>
              <tbody className="divide-y divide-slate-100">
                <tr className="bg-slate-50/60"><td className="td">{dmy(st.date_from)}</td><td className="td font-medium" colSpan={4}>Opening balance</td>
                  <td className="td text-right font-medium tabular-nums">{inr(st.opening)}</td></tr>
                {st.entries.map((e) => (
                  <tr key={e.id}>
                    <td className="td whitespace-nowrap">{dmy(e.entry_date)}</td>
                    <td className="td"><span className="text-xs text-slate-500">{KIND[e.kind] ?? e.kind}</span><div>{e.narration}</div></td>
                    <td className="td font-mono text-xs">{e.ref_id && e.kind.startsWith('invoice') ? <Link className="text-pine-700" to={`/invoices/${e.ref_id}`}>{e.ref_number}</Link> : e.ref_number}</td>
                    <td className="td text-right tabular-nums">{Number(e.debit) ? inr(e.debit) : ''}</td>
                    <td className="td text-right tabular-nums">{Number(e.credit) ? inr(e.credit) : ''}</td>
                    <td className="td text-right tabular-nums">{inr(e.balance)}</td>
                  </tr>
                ))}
                <tr className="font-semibold"><td className="td" /><td className="td">Closing balance</td><td className="td" />
                  <td className="td text-right tabular-nums">{inr(st.total_debit)}</td><td className="td text-right tabular-nums">{inr(st.total_credit)}</td>
                  <td className="td text-right tabular-nums">{inr(st.closing)}</td></tr>
              </tbody>
            </table>
          </div>
          {st.entries.length === 0 && <Empty title="No entries in this period" />}
        </div>
      )}
      <Drawer open={cnOpen} onClose={() => setCnOpen(false)} title="Issue credit note"
        footer={<><button className="btn-secondary" onClick={() => setCnOpen(false)}>Close</button>
          <button className="btn-primary" disabled={!amount || reason.trim().length < 2} onClick={creditNote}>Issue</button></>}>
        <ErrorBox message={dlgError} />
        <p className="mb-3 text-sm text-slate-600">Not linked to a specific invoice. To link one, open the invoice and use Credit note there.</p>
        <Field label="Amount (₹) *" className="mb-3"><input className="input" type="number" min={1} value={amount} onChange={(e) => setAmount(e.target.value)} /></Field>
        <Field label="Reason *"><textarea className="input" rows={3} value={reason} onChange={(e) => setReason(e.target.value)} /></Field>
      </Drawer>
    </>
  )
}

export function Receivables() {
  const { me } = useAuth()
  const canWrite = isAccounts(me?.role)
  const navigate = useNavigate()
  const [rows, setRows] = useState<BalanceRow[]>([])
  const [q, setQ] = useState('')
  const [onlyDue, setOnlyDue] = useState(true)
  const [importOpen, setImportOpen] = useState(false)
  const load = useCallback(() => {
    api.get<BalanceRow[]>('/ledger/balances', { params: { q: q || undefined, only_due: onlyDue || undefined } }).then((r) => setRows(r.data)).catch(() => {})
  }, [q, onlyDue])
  useEffect(() => { const t = setTimeout(load, 250); return () => clearTimeout(t) }, [load])
  const total = rows.reduce((a, r) => a + Number(r.balance), 0)
  const overdue = rows.reduce((a, r) => a + Number(r.overdue), 0)

  return (
    <>
      <PageHeader title="Receivables" subtitle="What each customer owes you today. Click a customer for the full ledger."
        actions={canWrite ? <button className="btn-secondary" onClick={() => setImportOpen(true)}>Import opening balances</button> : null} />
      <div className="mb-4 grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
        <div className="card p-4"><div className="text-sm text-slate-500">Total receivable</div><div className="mt-1 text-2xl font-semibold">{inr(total)}</div></div>
        <div className={`card p-4 ${overdue > 0 ? 'border-red-200' : ''}`}><div className="text-sm text-slate-500">Overdue invoices</div><div className="mt-1 text-2xl font-semibold text-red-600">{inr(overdue)}</div></div>
      </div>
      <div className="card">
        <div className="flex flex-wrap items-center gap-3 border-b border-slate-200 p-3 text-sm">
          <input className="input max-w-xs" placeholder="Search customer" value={q} onChange={(e) => setQ(e.target.value)} />
          <label className="flex items-center gap-2"><input type="checkbox" checked={onlyDue} onChange={(e) => setOnlyDue(e.target.checked)} /> Only customers who owe</label>
        </div>
        <div className="overflow-x-auto">
          <table className="w-full min-w-[640px]">
            <thead className="bg-slate-50"><tr><th className="th">Customer</th><th className="th text-right">Opening</th>
              <th className="th text-right">Balance</th><th className="th text-right">Overdue</th><th className="th text-right">Credit limit</th></tr></thead>
            <tbody className="divide-y divide-slate-100">
              {rows.map((r) => (
                <tr key={r.customer_id} className="cursor-pointer hover:bg-slate-50" onClick={() => navigate(`/customers/${r.customer_id}/ledger`)}>
                  <td className="td"><div className="font-medium text-slate-900">{r.customer_name}</div><div className="font-mono text-xs text-slate-500">{r.whatsapp_number}</div></td>
                  <td className="td text-right tabular-nums text-slate-600">{inr(r.opening_balance)}</td>
                  <td className={`td text-right font-medium tabular-nums ${Number(r.balance) < 0 ? 'text-pine-700' : ''}`}>{inr(r.balance)}</td>
                  <td className={`td text-right tabular-nums ${Number(r.overdue) > 0 ? 'text-red-600' : 'text-slate-400'}`}>{Number(r.overdue) ? inr(r.overdue) : '—'}</td>
                  <td className="td text-right tabular-nums text-slate-600">{Number(r.credit_limit) ? inr(r.credit_limit) : 'No limit'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {rows.length === 0 && <Empty title={onlyDue ? 'Nobody owes you anything' : 'No customers'} />}
      </div>
      <ImportDialog open={importOpen} onClose={() => setImportOpen(false)} title="Import opening balances"
        uploadPath="/ledger/opening-balances/import" templatePath="/ledger/opening-balances/template" templateName="opening_balances_template.xlsx"
        columnsHint="What each customer owed you at go-live. Matched by WhatsApp number or GSTIN; existing opening balances are replaced."
        onDone={load} />
    </>
  )
}
