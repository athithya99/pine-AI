import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { Drawer, ErrorBox, Field, PageHeader, useToast } from '../components/ui'
import { api, errorMessage } from '../lib/api'
import { useAuth } from '../lib/auth'
import { inr, isAccounts, type Customer, type InvoiceDetail as Inv, type Page } from '../lib/types'
import { CreateLink } from '../components/PaymentDialogs'
import { PAYMENT_MODE_LABEL, canCollect, type Payment } from '../lib/types'
import { InvoiceStatusBadge } from './Invoices'
import { Picker } from './OrderForm'
import { fmtDate } from './Orders'

const SOURCE: Record<string, string> = {
  generated: 'Generated here', erp: 'Billed in Tally/ERP', uploaded: 'Uploaded PDF (read by AI)', excel: 'Imported from Excel',
}

/** Shows a protected PDF inline. */
export function PdfFrame({ path, className = '' }: { path: string; className?: string }) {
  const [url, setUrl] = useState<string | null>(null)
  useEffect(() => {
    let u: string | null = null
    api.get(path, { responseType: 'blob' }).then((r) => {
      u = URL.createObjectURL(new Blob([r.data], { type: 'application/pdf' })); setUrl(u)
    }).catch(() => {})
    return () => { if (u) URL.revokeObjectURL(u) }
  }, [path])
  if (!url) return <div className={`animate-pulse rounded-lg bg-slate-100 ${className}`} />
  return <iframe src={url} title="Invoice PDF" className={`rounded-lg border border-slate-200 bg-white ${className}`} />
}

async function openPdf(path: string) {
  const r = await api.get(path, { responseType: 'blob' })
  window.open(URL.createObjectURL(new Blob([r.data], { type: 'application/pdf' })), '_blank')
}

function ReviewForm({ inv, onSaved }: { inv: Inv; onSaved: (i: Inv) => void }) {
  const ai = inv.ai_extracted ?? {}
  const toast = useToast()
  const navigate = useNavigate()
  const [f, setF] = useState({
    invoice_number: ai.invoice_number ?? '', invoice_date: ai.invoice_date ?? '', due_date: ai.due_date ?? '',
    taxable_value: ai.taxable_value ?? '', tax_amount: ai.tax_amount ?? '', total: ai.total ?? '',
  })
  const [customer, setCustomer] = useState<{ id: string; business_name: string } | null>(
    inv.customer_id ? { id: inv.customer_id, business_name: inv.customer_name ?? '' } : null)
  const [send, setSend] = useState(true)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const set = (k: keyof typeof f, v: string) => setF((x) => ({ ...x, [k]: v }))
  const searchCustomers = useMemo(() => async (q: string) =>
    (await api.get<Page<Customer>>('/customers', { params: { q: q || undefined, size: 10 } })).data.items, [])

  const mismatch = f.total && f.taxable_value && f.tax_amount
    && Math.abs(Number(f.taxable_value) + Number(f.tax_amount) - Number(f.total)) > 1

  const confirm = async () => {
    if (!customer) { setError('Pick the customer'); return }
    setBusy(true); setError(null)
    try {
      const { data } = await api.post<Inv>(`/invoices/${inv.id}/confirm`, {
        invoice_number: f.invoice_number, invoice_date: f.invoice_date, due_date: f.due_date || null,
        customer_id: customer.id, total: f.total,
        taxable_value: f.taxable_value || String(Number(f.total) - Number(f.tax_amount || 0)), tax_amount: f.tax_amount || '0',
        send_to_buyer: send,
      })
      toast(data.sent ? `Invoice ${data.number} saved and sent on WhatsApp` : `Invoice ${data.number} saved`)
      onSaved(data)
    } catch (e) { setError(errorMessage(e)) } finally { setBusy(false) }
  }
  const discard = async () => {
    if (!window.confirm('Discard this uploaded PDF?')) return
    await api.delete(`/invoices/${inv.id}`)
    toast('Discarded'); navigate('/invoices?tab=review')
  }

  return (
    <div className="card p-4">
      <h2 className="font-semibold text-slate-900">Check what the AI read</h2>
      <p className="mb-3 text-xs text-slate-500">Compare with the PDF and correct anything wrong. Nothing is shared with the buyer or added to their ledger until you save.</p>
      {ai.error && <div className="mb-3 rounded-lg bg-amber-50 p-2 text-xs text-amber-900">{ai.error}</div>}
      <ErrorBox message={error} />
      <div className="grid grid-cols-2 gap-3">
        <Field label="Customer *" className="col-span-2">
          {customer ? (
            <div className="flex items-center justify-between rounded-lg border border-slate-200 px-3 py-2 text-sm">
              <span className="font-medium">{customer.business_name}</span>
              <button className="text-xs text-slate-500" onClick={() => setCustomer(null)}>Change</button>
            </div>
          ) : (
            <>
              <Picker<Customer> placeholder="Search customer" search={searchCustomers} onPick={setCustomer}
                render={(c) => <><span className="font-medium">{c.business_name}</span> <span className="text-xs text-slate-500">{c.gstin ?? c.whatsapp_number}</span></>} />
              {(ai.customer_name || ai.customer_gstin) && <span className="mt-1 block text-xs text-amber-700">AI read “{ai.customer_name ?? ''}” {ai.customer_gstin ?? ''} but couldn't match a customer.</span>}
            </>
          )}
        </Field>
        <Field label="Invoice number *"><input className="input" value={f.invoice_number} onChange={(e) => set('invoice_number', e.target.value)} /></Field>
        <Field label="Invoice date *"><input className="input" type="date" value={f.invoice_date} onChange={(e) => set('invoice_date', e.target.value)} /></Field>
        <Field label="Taxable value (₹)"><input className="input" type="number" value={f.taxable_value} onChange={(e) => set('taxable_value', e.target.value)} /></Field>
        <Field label="GST amount (₹)"><input className="input" type="number" value={f.tax_amount} onChange={(e) => set('tax_amount', e.target.value)} /></Field>
        <Field label="Total (₹) *"><input className="input" type="number" value={f.total} onChange={(e) => set('total', e.target.value)} /></Field>
        <Field label="Due date" hint="Blank = invoice date + customer's credit days"><input className="input" type="date" value={f.due_date} onChange={(e) => set('due_date', e.target.value)} /></Field>
      </div>
      {mismatch && <p className="mt-2 text-xs text-amber-700">Taxable value + GST doesn't add up to the total. Please check.</p>}
      <label className="mt-3 flex items-center gap-2 text-sm"><input type="checkbox" checked={send} onChange={(e) => setSend(e.target.checked)} /> Send the PDF to the buyer on WhatsApp</label>
      <div className="mt-4 flex justify-between">
        <button className="btn-danger" onClick={discard}>Discard</button>
        <button className="btn-primary" disabled={busy || !f.invoice_number || !f.invoice_date || !f.total} onClick={confirm}>{busy ? 'Saving…' : 'Confirm and save'}</button>
      </div>
    </div>
  )
}

export function InvoiceDetail() {
  const { id } = useParams()
  const { me } = useAuth()
  const canWrite = isAccounts(me?.role)
  const toast = useToast()
  const [inv, setInv] = useState<Inv | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [dialog, setDialog] = useState<'cancel' | 'credit' | null>(null)
  const [reason, setReason] = useState('')
  const [amount, setAmount] = useState('')
  const [busy, setBusy] = useState(false)
  const [dlgError, setDlgError] = useState<string | null>(null)
  const pdfInput = useRef<HTMLInputElement>(null)
  const [payments, setPayments] = useState<Payment[]>([])
  const [linkOpen, setLinkOpen] = useState(false)
  const [paused, setPaused] = useState(false)

  const load = useCallback(() => {
    api.get<Inv>(`/invoices/${id}`).then((r) => { setInv(r.data); setPaused(Boolean(r.data.reminders_paused)) }).catch((e) => setError(errorMessage(e)))
    api.get<Payment[]>(`/invoices/${id}/payments`).then((r) => setPayments(r.data)).catch(() => {})
  }, [id])
  useEffect(() => { load() }, [load])

  if (error) return <ErrorBox message={error} />
  if (!inv) return <p className="text-sm text-slate-500">Loading…</p>

  const sendPdf = async () => {
    try {
      const { data } = await api.post<Inv>(`/invoices/${inv.id}/send`, {})
      setInv(data); toast('Sent on WhatsApp')
    } catch (e) { toast(errorMessage(e), 'error') }
  }
  const attach = async (file?: File) => {
    if (!file) return
    const form = new FormData(); form.append('file', file)
    try { setInv((await api.post<Inv>(`/invoices/${inv.id}/pdf`, form)).data); toast('PDF attached') }
    catch (e) { toast(errorMessage(e), 'error') }
  }
  const submitDialog = async () => {
    setBusy(true); setDlgError(null)
    try {
      if (dialog === 'cancel') {
        setInv((await api.post<Inv>(`/invoices/${inv.id}/cancel`, { reason })).data); toast('Invoice cancelled')
      } else {
        await api.post('/credit-notes', { customer_id: inv.customer_id, invoice_id: inv.id, amount, reason })
        load(); toast('Credit note issued')
      }
      setDialog(null); setReason(''); setAmount('')
    } catch (e) { setDlgError(errorMessage(e)) } finally { setBusy(false) }
  }

  if (inv.status === 'review') {
    return (
      <>
        <PageHeader title="Review uploaded invoice" subtitle={inv.pdf_name ?? ''} actions={<Link to="/invoices?tab=review" className="btn-secondary">Back</Link>} />
        <div className="grid gap-6 lg:grid-cols-[1fr_420px]">
          <PdfFrame path={`/invoices/${inv.id}/pdf`} className="h-[75vh] w-full" />
          {canWrite ? <ReviewForm inv={inv} onSaved={setInv} /> : <p className="text-sm text-slate-500">Accounts, Admin or Owner confirms uploaded invoices.</p>}
        </div>
      </>
    )
  }

  const inter = inv.inter_state
  return (
    <>
      <PageHeader
        title={inv.number ?? 'Invoice'}
        subtitle={`${inv.customer_name ?? ''} · ${SOURCE[inv.source]}${inv.order_number ? ` · order ${inv.order_number}` : ''}`}
        actions={<>
          <Link to="/invoices" className="btn-secondary">All invoices</Link>
          {inv.customer_id && <Link to={`/customers/${inv.customer_id}/ledger`} className="btn-secondary">Customer ledger</Link>}
          {inv.has_pdf && <button className="btn-secondary" onClick={() => openPdf(`/invoices/${inv.id}/pdf`)}>View PDF</button>}
          {canWrite && inv.status === 'issued' && <>
            <input ref={pdfInput} type="file" accept="application/pdf" hidden onChange={(e) => attach(e.target.files?.[0])} />
            {!inv.has_pdf && <button className="btn-secondary" onClick={() => pdfInput.current?.click()}>Attach PDF</button>}
            <button className="btn-secondary" onClick={() => { setDialog('credit'); setDlgError(null) }}>Credit note</button>
            <button className="btn-danger" onClick={() => { setDialog('cancel'); setDlgError(null) }}>Cancel invoice</button>
            {canCollect(me?.role) && Number(inv.balance_due) > 0 && <button className="btn-secondary" onClick={() => setLinkOpen(true)}>Payment link</button>}
            {inv.has_pdf && <button className="btn-primary" onClick={sendPdf}>{inv.sent ? 'Send again' : 'Send on WhatsApp'}</button>}
          </>}
        </>}
      />

      {inv.status === 'cancelled' && <div className="mb-4 rounded-lg border border-red-200 bg-red-50 p-3 text-sm text-red-800">Cancelled {fmtDate(inv.cancelled_at, true)} — {inv.cancel_reason}. The ledger entry was reversed.</div>}
      {inv.send_note && inv.status === 'issued' && <div className="mb-4 rounded-lg border border-amber-200 bg-amber-50 p-3 text-sm text-amber-900">{inv.send_note}</div>}

      <div className="grid gap-6 lg:grid-cols-[1fr_320px]">
        <div className="card">
          <div className="flex flex-wrap items-center justify-between gap-2 border-b border-slate-200 px-4 py-3 text-sm">
            <div className="flex items-center gap-2"><InvoiceStatusBadge inv={inv} />
              <span className="text-slate-500">{inter ? 'Inter-state (IGST)' : 'Intra-state (CGST + SGST)'} · Place of supply {inv.place_of_supply ?? '—'}</span></div>
            {inv.sent && <span className="text-xs text-pine-700">✓ Sent on WhatsApp</span>}
          </div>
          {inv.lines.length > 0 ? (
            <div className="overflow-x-auto">
              <table className="w-full min-w-[640px]">
                <thead className="bg-slate-50"><tr>
                  <th className="th">Item</th><th className="th">HSN</th><th className="th text-right">Qty</th><th className="th text-right">Rate</th>
                  <th className="th text-right">Taxable</th><th className="th text-right">GST</th>
                  {inter ? <th className="th text-right">IGST</th> : <><th className="th text-right">CGST</th><th className="th text-right">SGST</th></>}
                  <th className="th text-right">Total</th></tr></thead>
                <tbody className="divide-y divide-slate-100">
                  {inv.lines.map((l) => (
                    <tr key={l.id}>
                      <td className="td"><div className="font-medium">{l.description}</div>
                        {Number(l.discount) > 0 && <div className="text-xs text-pine-700">Discount {inr(l.discount)}</div>}
                        {Number(l.free_qty) > 0 && <div className="text-xs text-pine-700">+{Number(l.free_qty)} free</div>}</td>
                      <td className="td text-slate-600">{l.hsn_code ?? '—'}</td>
                      <td className="td text-right tabular-nums">{Number(l.quantity)} {l.uom}</td>
                      <td className="td text-right tabular-nums">{inr(l.rate)}</td>
                      <td className="td text-right tabular-nums">{inr(l.taxable_value)}</td>
                      <td className="td text-right tabular-nums">{Number(l.gst_rate)}%</td>
                      {inter ? <td className="td text-right tabular-nums">{inr(l.igst)}</td>
                        : <><td className="td text-right tabular-nums">{inr(l.cgst)}</td><td className="td text-right tabular-nums">{inr(l.sgst)}</td></>}
                      <td className="td text-right font-medium tabular-nums">{inr(l.total)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : <p className="px-4 py-4 text-sm text-slate-500">Item details are on the PDF from your billing software.</p>}
          <dl className="ml-auto max-w-xs space-y-1 px-4 py-3 text-sm">
            <div className="flex justify-between"><dt className="text-slate-500">Taxable value</dt><dd className="tabular-nums">{inr(inv.taxable_value)}</dd></div>
            {inter ? <div className="flex justify-between"><dt className="text-slate-500">IGST</dt><dd className="tabular-nums">{inr(inv.igst)}</dd></div> : <>
              <div className="flex justify-between"><dt className="text-slate-500">CGST</dt><dd className="tabular-nums">{inr(inv.cgst)}</dd></div>
              <div className="flex justify-between"><dt className="text-slate-500">SGST</dt><dd className="tabular-nums">{inr(inv.sgst)}</dd></div></>}
            {Number(inv.round_off) !== 0 && <div className="flex justify-between"><dt className="text-slate-500">Round off</dt><dd className="tabular-nums">{inr(inv.round_off)}</dd></div>}
            <div className="flex justify-between border-t border-slate-200 pt-1 text-base font-semibold"><dt>Total</dt><dd className="tabular-nums">{inr(inv.total)}</dd></div>
          </dl>
          <p className="border-t border-slate-100 px-4 py-2 text-xs text-slate-500">{inv.amount_in_words}</p>
        </div>

        <div className="space-y-4">
          <div className="card space-y-1.5 p-4 text-sm">
            <div className="flex justify-between"><span className="text-slate-500">Invoice date</span><span>{fmtDate(inv.invoice_date)}</span></div>
            <div className="flex justify-between"><span className="text-slate-500">Due date</span><span className={inv.overdue ? 'font-medium text-red-600' : ''}>{fmtDate(inv.due_date)}</span></div>
            <div className="flex justify-between"><span className="text-slate-500">Paid</span><span>{inr(inv.amount_paid)}</span></div>
            <div className="flex justify-between font-medium"><span>Balance due</span><span>{inr(inv.balance_due)}</span></div>
            <div className="border-t border-slate-100 pt-2 text-xs text-slate-500">
              Seller GSTIN {inv.seller_gstin ?? '—'} · Buyer GSTIN {inv.buyer_gstin ?? 'Unregistered'}
            </div>
            {inv.order_id && <Link to={`/orders/${inv.order_id}`} className="block text-xs text-pine-700 underline">Open order {inv.order_number}</Link>}
            {inv.confirmed_by_name && <div className="text-xs text-slate-500">Saved by {inv.confirmed_by_name} · {fmtDate(inv.confirmed_at, true)}</div>}
          </div>
          {inv.status === 'issued' && (
            <div className="card p-4 text-sm">
              <h2 className="mb-2 font-semibold">Payments</h2>
              {payments.length === 0 && <p className="text-xs text-slate-500">No payments adjusted yet.</p>}
              {payments.map((p) => {
                const a = p.allocations.find((x) => x.invoice_id === inv.id)
                return (
                  <div key={p.id} className="flex justify-between border-t border-slate-100 py-1.5 first:border-0">
                    <span><span className="font-mono text-xs">{p.number}</span> · {PAYMENT_MODE_LABEL[p.mode]} · {fmtDate(p.payment_date)}</span>
                    <span className="tabular-nums">{inr(a?.amount ?? p.amount)}</span>
                  </div>
                )
              })}
              {canCollect(me?.role) && Number(inv.balance_due) > 0 && (
                <label className="mt-2 flex items-center gap-2 border-t border-slate-100 pt-2 text-xs text-slate-600">
                  <input type="checkbox" checked={paused} onChange={async (e) => {
                    const v = e.target.checked; setPaused(v)
                    try { await api.put(`/invoices/${inv.id}/reminders`, { paused: v }); toast(v ? 'Reminders paused for this invoice' : 'Reminders on') } catch (err) { toast(errorMessage(err), 'error') }
                  }} /> Pause payment reminders (e.g. disputed)
                </label>
              )}
            </div>
          )}
          {inv.credit_notes.length > 0 && (
            <div className="card p-4 text-sm">
              <h2 className="mb-2 font-semibold">Credit notes</h2>
              {inv.credit_notes.map((c) => (
                <div key={c.id} className="flex justify-between border-t border-slate-100 py-1.5 first:border-0">
                  <span><span className="font-mono text-xs">{c.number}</span> · {c.reason}{c.status === 'cancelled' && <span className="text-red-600"> (cancelled)</span>}</span>
                  <span className="tabular-nums">−{inr(c.amount)}</span>
                </div>
              ))}
            </div>
          )}
          {inv.has_pdf && <PdfFrame path={`/invoices/${inv.id}/pdf`} className="h-96 w-full" />}
        </div>
      </div>

      {inv.customer_id && <CreateLink open={linkOpen} onClose={() => setLinkOpen(false)} onDone={load}
        customer={{ id: inv.customer_id, business_name: inv.customer_name ?? '' }} invoiceIds={[inv.id]} />}
      <Drawer open={dialog !== null} onClose={() => setDialog(null)} title={dialog === 'cancel' ? 'Cancel invoice' : 'Issue credit note'}
        footer={<><button className="btn-secondary" onClick={() => setDialog(null)}>Close</button>
          <button className={dialog === 'cancel' ? 'btn-danger' : 'btn-primary'} disabled={busy || reason.trim().length < 2 || (dialog === 'credit' && !amount)} onClick={submitDialog}>
            {dialog === 'cancel' ? 'Cancel invoice' : 'Issue credit note'}</button></>}>
        <ErrorBox message={dlgError} />
        {dialog === 'cancel' ? (
          <p className="mb-3 text-sm text-slate-600">A saved invoice can't be edited. Cancelling reverses its ledger entry{inv.order_number ? ` and puts order ${inv.order_number} back to Approved so you can invoice it again` : ''}.</p>
        ) : (
          <>
            <p className="mb-3 text-sm text-slate-600">For returns, damages, rate differences or discounts after billing. Reduces what {inv.customer_name} owes.</p>
            <Field label="Amount (₹) *" className="mb-3"><input className="input" type="number" min={1} value={amount} onChange={(e) => setAmount(e.target.value)} autoFocus /></Field>
          </>
        )}
        <Field label="Reason *"><textarea className="input" rows={3} value={reason} onChange={(e) => setReason(e.target.value)} /></Field>
      </Drawer>
    </>
  )
}
