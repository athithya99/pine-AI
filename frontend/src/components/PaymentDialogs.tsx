import { useEffect, useMemo, useState } from 'react'
import { api, errorMessage } from '../lib/api'
import { useAuth } from '../lib/auth'
import { PAYMENT_MODE_LABEL, inr, type Customer, type InvoiceSummary, type Page, type Payment, type PaymentLinkT, type PaymentMode } from '../lib/types'
import { AuthedMedia } from '../pages/Inbox'
import { Picker } from '../pages/OrderForm'
import { Drawer, ErrorBox, Field, useToast } from './ui'

const todayIso = () => new Date(Date.now() - new Date().getTimezoneOffset() * 60000).toISOString().slice(0, 10)
const REF_LABEL: Partial<Record<PaymentMode, string>> = { cheque: 'Cheque number *', neft: 'UTR *', rtgs: 'UTR *', upi: 'UPI reference / UTR *' }

type Cust = { id: string; business_name: string }

function CustomerField({ value, onChange, locked }: { value: Cust | null; onChange: (c: Cust | null) => void; locked?: boolean }) {
  const search = useMemo(() => async (q: string) =>
    (await api.get<Page<Customer>>('/customers', { params: { q: q || undefined, size: 10 } })).data.items, [])
  if (value) {
    return (
      <div className="flex items-center justify-between rounded-lg border border-slate-200 px-3 py-2 text-sm">
        <span className="font-medium">{value.business_name}</span>
        {!locked && <button className="text-xs text-slate-500" onClick={() => onChange(null)}>Change</button>}
      </div>
    )
  }
  return <Picker<Customer> placeholder="Search customer" search={search} onPick={onChange}
    render={(c) => <><span className="font-medium">{c.business_name}</span> <span className="font-mono text-xs text-slate-500">{c.whatsapp_number}</span></>} />
}

/** Unpaid invoices of a customer, with optional ticks to pay specific ones. */
function InvoicePicker({ customerId, selected, onChange }: { customerId: string; selected: string[]; onChange: (ids: string[]) => void }) {
  const [rows, setRows] = useState<InvoiceSummary[]>([])
  useEffect(() => {
    api.get<Page<InvoiceSummary>>('/invoices', { params: { customer_id: customerId, status: ['issued'], size: 100 }, paramsSerializer: { indexes: null } })
      .then((r) => setRows(r.data.items.filter((i) => Number(i.balance_due) > 0).reverse())).catch(() => {})
  }, [customerId])
  if (!rows.length) return <p className="text-xs text-slate-500">No unpaid invoices. The amount will be kept as an advance.</p>
  const toggle = (id: string) => onChange(selected.includes(id) ? selected.filter((x) => x !== id) : [...selected, id])
  return (
    <div className="rounded-lg border border-slate-200">
      <div className="border-b border-slate-100 px-3 py-2 text-xs text-slate-500">Adjust against: {selected.length ? 'selected invoices first' : 'oldest unpaid first (default)'}</div>
      {rows.map((i) => (
        <label key={i.id} className="flex items-center justify-between gap-2 px-3 py-1.5 text-sm hover:bg-slate-50">
          <span className="flex items-center gap-2"><input type="checkbox" checked={selected.includes(i.id)} onChange={() => toggle(i.id)} />
            <span className="font-mono text-xs">{i.number}</span>{i.overdue && <span className="badge bg-red-50 text-red-700">overdue</span>}</span>
          <span className="tabular-nums">{inr(i.balance_due)}</span>
        </label>
      ))}
    </div>
  )
}

export function RecordPayment({ open, onClose, onDone, customer }: {
  open: boolean; onClose: () => void; onDone: (p: Payment) => void; customer?: Cust | null
}) {
  const { me } = useAuth()
  const sales = me?.role === 'sales'
  const toast = useToast()
  const [cust, setCust] = useState<Cust | null>(customer ?? null)
  const [mode, setMode] = useState<PaymentMode>('cash')
  const [amount, setAmount] = useState('')
  const [date, setDate] = useState(todayIso())
  const [ref, setRef] = useState('')
  const [bank, setBank] = useState('')
  const [chequeDate, setChequeDate] = useState('')
  const [notes, setNotes] = useState('')
  const [inv, setInv] = useState<string[]>([])
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  useEffect(() => { if (open) { setCust(customer ?? null); setAmount(''); setRef(''); setInv([]); setError(null); setMode('cash') } }, [open, customer])

  const modes: PaymentMode[] = sales ? ['cash', 'cheque'] : ['cash', 'cheque', 'upi', 'neft', 'rtgs', 'other']
  const save = async () => {
    if (!cust) { setError('Pick the customer'); return }
    setBusy(true); setError(null)
    try {
      const { data } = await api.post<Payment>('/payments', {
        customer_id: cust.id, amount, mode, payment_date: date, reference: ref || null, cheque_bank: bank || null,
        cheque_date: chequeDate || null, notes: notes || null, invoice_ids: inv.length ? inv : null,
      })
      toast(data.status === 'pending_clearance' ? `Cheque recorded (${data.number}). Mark it cleared when it clears.`
        : `${data.number} recorded${data.receipt_sent ? ', receipt sent on WhatsApp' : ''}`)
      onDone(data); onClose()
    } catch (e) { setError(errorMessage(e)) } finally { setBusy(false) }
  }
  return (
    <Drawer open={open} onClose={onClose} title="Record payment"
      footer={<><button className="btn-secondary" onClick={onClose}>Cancel</button>
        <button className="btn-primary" disabled={busy || !amount} onClick={save}>{busy ? 'Saving…' : 'Save payment'}</button></>}>
      <ErrorBox message={error} />
      <div className="grid grid-cols-2 gap-3">
        <Field label="Customer *" className="col-span-2"><CustomerField value={cust} onChange={setCust} locked={Boolean(customer)} /></Field>
        <Field label="Mode">
          <select className="input" value={mode} onChange={(e) => setMode(e.target.value as PaymentMode)}>
            {modes.map((m) => <option key={m} value={m}>{PAYMENT_MODE_LABEL[m]}</option>)}
          </select>
        </Field>
        <Field label="Amount (₹) *"><input className="input" type="number" min={1} value={amount} onChange={(e) => setAmount(e.target.value)} autoFocus /></Field>
        <Field label="Date received"><input className="input" type="date" value={date} onChange={(e) => setDate(e.target.value)} /></Field>
        {mode !== 'cash' && <Field label={REF_LABEL[mode] ?? 'Reference'}><input className="input" value={ref} onChange={(e) => setRef(e.target.value)} /></Field>}
        {mode === 'cheque' && <>
          <Field label="Bank"><input className="input" value={bank} onChange={(e) => setBank(e.target.value)} /></Field>
          <Field label="Cheque date"><input className="input" type="date" value={chequeDate} onChange={(e) => setChequeDate(e.target.value)} /></Field>
          <p className="col-span-2 text-xs text-slate-500">Cheques stay "pending clearance" and don't reduce the balance until you mark them cleared.</p>
        </>}
        <Field label="Notes" className="col-span-2"><input className="input" value={notes} onChange={(e) => setNotes(e.target.value)} /></Field>
        {cust && <div className="col-span-2"><InvoicePicker customerId={cust.id} selected={inv} onChange={setInv} /></div>}
      </div>
    </Drawer>
  )
}

export function ConfirmProposal({ payment, onClose, onDone }: { payment: Payment | null; onClose: () => void; onDone: () => void }) {
  const toast = useToast()
  const [cust, setCust] = useState<Cust | null>(null)
  const [mode, setMode] = useState<PaymentMode>('upi')
  const [amount, setAmount] = useState('')
  const [date, setDate] = useState(todayIso())
  const [ref, setRef] = useState('')
  const [reason, setReason] = useState('')
  const [inv, setInv] = useState<string[]>([])
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  useEffect(() => {
    if (!payment) return
    setCust(payment.customer_id ? { id: payment.customer_id, business_name: payment.customer_name ?? '' } : null)
    setMode(payment.mode === 'link' ? 'upi' : payment.mode); setAmount(Number(payment.amount) ? String(Number(payment.amount)) : '')
    setDate(payment.payment_date); setRef(payment.reference ?? ''); setReason(''); setInv([]); setError(null)
  }, [payment])
  if (!payment) return null
  const confirm = async () => {
    if (!cust) { setError('Pick the customer'); return }
    setBusy(true); setError(null)
    try {
      await api.post(`/payments/${payment.id}/confirm`, { customer_id: cust.id, amount, mode, payment_date: date, reference: ref || null, invoice_ids: inv.length ? inv : null })
      toast('Payment confirmed and receipt sent'); onDone(); onClose()
    } catch (e) { setError(errorMessage(e)) } finally { setBusy(false) }
  }
  const reject = async () => {
    setBusy(true); setError(null)
    try { await api.post(`/payments/${payment.id}/reject`, { reason }); toast('Rejected'); onDone(); onClose() }
    catch (e) { setError(errorMessage(e)) } finally { setBusy(false) }
  }
  return (
    <Drawer open onClose={onClose} title="Confirm payment from buyer"
      footer={<>
        <input className="input mr-auto max-w-[200px]" placeholder="Reason to reject" value={reason} onChange={(e) => setReason(e.target.value)} />
        <button className="btn-danger" disabled={busy || reason.trim().length < 2} onClick={reject}>Reject</button>
        <button className="btn-primary" disabled={busy || !amount} onClick={confirm}>Confirm payment</button></>}>
      <ErrorBox message={error} />
      {payment.duplicate_of && <div className="mb-3 rounded-lg bg-red-50 p-2 text-sm text-red-700">This reference is already recorded as {payment.duplicate_of}. Probably a duplicate — reject it.</div>}
      <p className="mb-3 text-sm text-slate-600">Check the buyer's proof against your bank statement before confirming. Nothing is recorded until you confirm.</p>
      {payment.has_proof_media && payment.message_id && <div className="mb-3"><AuthedMedia msg={{ id: payment.message_id, media_mime: 'image/jpeg', media_filename: null }} /></div>}
      <div className="grid grid-cols-2 gap-3">
        <Field label="Customer *" className="col-span-2"><CustomerField value={cust} onChange={setCust} /></Field>
        <Field label="Mode"><select className="input" value={mode} onChange={(e) => setMode(e.target.value as PaymentMode)}>
          {(['upi', 'neft', 'rtgs', 'cheque', 'cash', 'other'] as PaymentMode[]).map((m) => <option key={m} value={m}>{PAYMENT_MODE_LABEL[m]}</option>)}</select></Field>
        <Field label="Amount (₹) *"><input className="input" type="number" value={amount} onChange={(e) => setAmount(e.target.value)} /></Field>
        <Field label="Date"><input className="input" type="date" value={date} onChange={(e) => setDate(e.target.value)} /></Field>
        <Field label={REF_LABEL[mode] ?? 'Reference'}><input className="input" value={ref} onChange={(e) => setRef(e.target.value)} /></Field>
        {cust && <div className="col-span-2"><InvoicePicker customerId={cust.id} selected={inv} onChange={setInv} /></div>}
      </div>
    </Drawer>
  )
}

export function CreateLink({ open, onClose, onDone, customer, invoiceIds }: {
  open: boolean; onClose: () => void; onDone: (l: PaymentLinkT) => void; customer?: Cust | null; invoiceIds?: string[]
}) {
  const toast = useToast()
  const [cust, setCust] = useState<Cust | null>(customer ?? null)
  const [amount, setAmount] = useState('')
  const [inv, setInv] = useState<string[]>(invoiceIds ?? [])
  const [send, setSend] = useState(true)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  useEffect(() => { if (open) { setCust(customer ?? null); setInv(invoiceIds ?? []); setAmount(''); setError(null) } }, [open, customer, invoiceIds])
  const create = async () => {
    if (!cust) { setError('Pick the customer'); return }
    setBusy(true); setError(null)
    try {
      const { data } = await api.post<PaymentLinkT>('/payment-links', { customer_id: cust.id, amount: amount || null, invoice_ids: inv.length ? inv : null, send })
      toast(data.send_note ? `Link created. ${data.send_note}` : send ? 'Payment link sent on WhatsApp' : 'Payment link created', data.send_note ? 'error' : 'ok')
      onDone(data); onClose()
    } catch (e) { setError(errorMessage(e)) } finally { setBusy(false) }
  }
  return (
    <Drawer open={open} onClose={onClose} title="Payment link"
      footer={<><button className="btn-secondary" onClick={onClose}>Cancel</button>
        <button className="btn-primary" disabled={busy} onClick={create}>{send ? 'Create and send' : 'Create link'}</button></>}>
      <ErrorBox message={error} />
      <p className="mb-3 text-sm text-slate-600">The buyer pays by UPI, card or net banking. When they pay, the payment is recorded, matched to invoices and a receipt is sent automatically.</p>
      <div className="space-y-3">
        <Field label="Customer *"><CustomerField value={cust} onChange={setCust} locked={Boolean(customer)} /></Field>
        {cust && <InvoicePicker customerId={cust.id} selected={inv} onChange={setInv} />}
        <Field label="Amount (₹)" hint="Blank = total due on the selected (or all) unpaid invoices"><input className="input" type="number" value={amount} onChange={(e) => setAmount(e.target.value)} /></Field>
        <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={send} onChange={(e) => setSend(e.target.checked)} /> Send the link to the buyer on WhatsApp</label>
      </div>
    </Drawer>
  )
}
