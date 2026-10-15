import { useCallback, useEffect, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { Drawer, ErrorBox, Field, PageHeader, useToast } from '../components/ui'
import { api, errorMessage } from '../lib/api'
import { ORDER_STATUS_LABEL, inr, type OrderAction, type OrderDetail as Order, type OrderEvent } from '../lib/types'
import { useAuth } from '../lib/auth'
import { AuthedMedia } from './Inbox'
import { StatusBadge, fmtDate } from './Orders'

type Dialog = 'approve' | 'reject' | 'cancel' | 'invoice' | 'dispatch' | null

const FLOW = ['quoted', 'confirmed', 'approved', 'invoiced', 'dispatched', 'delivered'] as const

const qty = (v: string | null) => (v === null ? '—' : String(Number(v)))

function Progress({ order }: { order: Order }) {
  if (order.status === 'cancelled' || order.status === 'expired') return null
  const idx = order.status === 'on_hold' ? 1 : order.status === 'draft' ? -1 : FLOW.indexOf(order.status as typeof FLOW[number])
  return (
    <ol className="mb-6 grid grid-cols-6 gap-1 text-[11px]">
      {FLOW.map((s, i) => {
        const done = i <= idx
        const hold = order.status === 'on_hold' && i === 2
        return (
          <li key={s}>
            <div className={`h-1.5 rounded-full ${hold ? 'bg-amber-400' : done ? 'bg-pine-600' : 'bg-slate-200'}`} />
            <div className={`mt-1 ${done ? 'font-medium text-slate-700' : 'text-slate-400'}`}>{hold ? 'Needs approval' : ORDER_STATUS_LABEL[s]}</div>
          </li>
        )
      })}
    </ol>
  )
}

function eventText(e: OrderEvent): string {
  if (e.kind === 'created') return e.remark ?? 'Order created'
  if (e.kind === 'edit') return e.remark ?? 'Order edited'
  if (e.from_status === e.to_status) return e.remark ?? 'Quote re-sent'
  return `${e.from_status ? ORDER_STATUS_LABEL[e.from_status] + ' → ' : ''}${e.to_status ? ORDER_STATUS_LABEL[e.to_status] : ''}`
}

function who(e: OrderEvent): string {
  if (e.actor === 'buyer') return 'Buyer on WhatsApp'
  if (e.actor === 'system') return 'System'
  return e.user_name ?? 'Staff'
}

export function OrderDetail() {
  const { id } = useParams()
  const { me } = useAuth()
  const canWrite = me?.role === 'owner' || me?.role === 'admin' || me?.role === 'sales'
  const navigate = useNavigate()
  const toast = useToast()
  const [order, setOrder] = useState<Order | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [dialog, setDialog] = useState<Dialog>(null)
  const [busy, setBusy] = useState(false)
  const [dlgError, setDlgError] = useState<string | null>(null)
  // dialog fields
  const [remark, setRemark] = useState('')
  const [invoiceNo, setInvoiceNo] = useState('')
  const [invoiceDate, setInvoiceDate] = useState('')
  const [invQty, setInvQty] = useState<Record<string, string>>({})
  const [vehicle, setVehicle] = useState('')
  const [transporter, setTransporter] = useState('')
  const [lr, setLr] = useState('')

  const load = useCallback(() => {
    api.get<Order>(`/orders/${id}`).then((r) => setOrder(r.data)).catch((e) => setError(errorMessage(e)))
  }, [id])
  useEffect(() => { load() }, [load])

  const can = (a: OrderAction) => order?.allowed_actions.includes(a) ?? false

  const run = async (path: string, body: object, done: string) => {
    if (!order) return
    setBusy(true); setDlgError(null)
    try {
      const { data } = await api.post<Order>(`/orders/${order.id}/${path}`, body)
      setOrder(data); setDialog(null); setRemark('')
      const last = [...data.events].reverse().find((e) => e.kind === 'status')
      toast(last?.notify_note ? `${done}. Buyer not messaged: ${last.notify_note.replace(/^Not sent: /, '')}` : done,
        last?.notify_note ? 'error' : 'ok')
    } catch (e) {
      const msg = errorMessage(e)
      if (dialog) setDlgError(msg); else toast(msg, 'error')
      load()
    } finally { setBusy(false) }
  }

  const openDialog = (d: Dialog) => {
    setDlgError(null); setRemark('')
    if (d === 'invoice' && order) {
      setInvoiceNo(''); setInvoiceDate(new Date().toISOString().slice(0, 10))
      setInvQty(Object.fromEntries(order.lines.map((l) => [l.id, String(Number(l.quantity))])))
    }
    if (d === 'dispatch') { setVehicle(''); setTransporter(''); setLr('') }
    setDialog(d)
  }

  const saveAlias = async (lineId: string, text: string, name: string) => {
    if (!order) return
    try {
      const { data } = await api.post<Order>(`/orders/${order.id}/lines/${lineId}/save-alias`)
      setOrder(data)
      toast(`“${text}” will now match ${name}`)
    } catch (e) { toast(errorMessage(e), 'error') }
  }

  const openChat = async () => {
    if (!order) return
    try {
      const { data } = await api.post(`/inbox/customers/${order.customer_id}/conversation`)
      navigate(`/inbox?c=${data.id}`)
    } catch (e) { toast(errorMessage(e), 'error') }
  }

  if (error) return <ErrorBox message={error} />
  if (!order) return <p className="text-sm text-slate-500">Loading…</p>

  const shortLines = order.lines.filter((l) => l.invoiced_qty !== null && Number(l.invoiced_qty) < Number(l.quantity))

  return (
    <>
      <PageHeader
        title={order.number}
        subtitle={`${order.customer_name} · ${order.source === 'panel' ? 'Created in panel' : order.source === 'excel' ? 'Imported from Excel' : 'From WhatsApp'}${order.created_by_name ? ' by ' + order.created_by_name : ''} · ${fmtDate(order.created_at, true)}`}
        actions={<>
          <Link to="/orders" className="btn-secondary">All orders</Link>
          <button className="btn-secondary" onClick={openChat}>Open chat</button>
          {can('edit') && <Link to={`/orders/${order.id}/edit`} className="btn-secondary">Edit items</Link>}
          {can('cancel') && <button className="btn-danger" onClick={() => openDialog('cancel')}>Cancel order</button>}
          {can('send_quote') && <button className={can('confirm') ? 'btn-secondary' : 'btn-primary'} disabled={busy}
            onClick={() => run('send-quote', {}, order.status === 'quoted' ? 'Quote re-sent' : 'Quote sent')}>{order.status === 'quoted' ? 'Re-send quote' : 'Send quote'}</button>}
          {can('confirm') && <button className="btn-primary" disabled={busy} title="Confirm on the buyer's instruction (call, visit)"
            onClick={() => run('confirm', {}, 'Order confirmed')}>Confirm (buyer agreed)</button>}
          {can('reject') && <button className="btn-danger" onClick={() => openDialog('reject')}>Reject</button>}
          {can('approve') && <button className="btn-primary" onClick={() => order.status === 'on_hold' ? openDialog('approve') : run('approve', {}, 'Order approved')}>Approve</button>}
          {can('invoice') && <button className="btn-primary" onClick={() => openDialog('invoice')}>Mark invoiced</button>}
          {can('dispatch') && <button className="btn-primary" onClick={() => openDialog('dispatch')}>Mark dispatched</button>}
          {can('deliver') && <button className="btn-primary" disabled={busy} onClick={() => run('deliver', {}, 'Marked delivered')}>Mark delivered</button>}
        </>}
      />

      <Progress order={order} />

      {order.status === 'on_hold' && (
        <div className="mb-4 rounded-lg border border-amber-200 bg-amber-50 p-3 text-sm text-amber-900">
          <div className="font-medium">Waiting for Owner or Admin approval</div>
          <div className="mt-0.5">{order.hold_reasons}</div>
        </div>
      )}
      {order.status === 'cancelled' && (
        <div className="mb-4 rounded-lg border border-red-200 bg-red-50 p-3 text-sm text-red-800">
          Cancelled {order.cancelled_by_buyer ? 'by the buyer' : ''} {fmtDate(order.cancelled_at, true)}{order.cancel_reason ? ` — ${order.cancel_reason}` : ''}
        </div>
      )}
      {order.status === 'expired' && (
        <div className="mb-4 rounded-lg border border-slate-200 bg-slate-50 p-3 text-sm text-slate-700">
          The quote expired before the buyer confirmed. Send a fresh quote if they still want it.
        </div>
      )}

      <div className="grid gap-6 lg:grid-cols-[1fr_320px]">
        <div className="space-y-6">
          <div className="card">
            <div className="flex items-center justify-between border-b border-slate-200 px-4 py-3">
              <div className="flex items-center gap-2"><StatusBadge status={order.status} />
                {order.revision > 1 && <span className="text-xs text-slate-500">Revision {order.revision}</span>}</div>
              {order.status === 'quoted' && order.quote_valid_until && <span className="text-xs text-slate-500">Quote valid till {fmtDate(order.quote_valid_until, true)}</span>}
            </div>
            <div className="overflow-x-auto">
              <table className="w-full min-w-[640px]">
                <thead className="bg-slate-50">
                  <tr><th className="th">Item</th><th className="th text-right">Qty</th>
                    {order.invoice_number && <th className="th text-right">Invoiced</th>}
                    <th className="th text-right">Rate</th><th className="th text-right">Discount</th><th className="th text-right">GST</th><th className="th text-right">Total</th></tr>
                </thead>
                <tbody className="divide-y divide-slate-100">
                  {order.lines.map((l) => (
                    <tr key={l.id}>
                      <td className="td">
                        <div className="font-medium text-slate-900">{l.name}</div>
                        <div className="flex flex-wrap gap-1 text-xs text-slate-500">
                          <span className="font-mono">{l.sku}</span>
                          {l.scheme_name && <span className="badge bg-pine-50 text-pine-700">{l.scheme_name}</span>}
                          {Number(l.free_qty) > 0 && <span className="badge bg-pine-50 text-pine-700">+{qty(l.free_qty)} free</span>}
                          {l.out_of_stock && <span className="badge bg-red-50 text-red-700">Out of stock</span>}
                          {l.raw_text && <span className="italic">buyer wrote “{l.raw_text}”</span>}
                          {l.match_confidence !== null && (
                            <span className={`badge ${Number(l.match_confidence) >= 0.9 ? 'bg-pine-50 text-pine-700' : 'bg-amber-50 text-amber-800'}`}
                              title="How sure the AI was about this product">AI {Math.round(Number(l.match_confidence) * 100)}%</span>
                          )}
                          {l.raw_text && canWrite && (
                            <button className="text-pine-700 underline decoration-dotted hover:decoration-solid" onClick={() => saveAlias(l.id, l.raw_text!, l.name)}
                              title="Next time this wording matches this product straight away">Save wording as alias</button>
                          )}
                        </div>
                      </td>
                      <td className="td text-right tabular-nums">{qty(l.quantity)} {l.uom}</td>
                      {order.invoice_number && <td className={`td text-right tabular-nums ${l.invoiced_qty !== null && Number(l.invoiced_qty) < Number(l.quantity) ? 'text-amber-700' : ''}`}>{qty(l.invoiced_qty)}</td>}
                      <td className="td text-right tabular-nums">{inr(l.unit_price)}</td>
                      <td className="td text-right tabular-nums text-pine-700">{Number(l.discount) > 0 ? `−${inr(l.discount)}` : '—'}</td>
                      <td className="td text-right tabular-nums text-slate-600">{Number(l.gst_rate)}%</td>
                      <td className="td text-right font-medium tabular-nums">{inr(l.line_total)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <dl className="ml-auto max-w-xs space-y-1 px-4 py-3 text-sm">
              {Number(order.discount_total) > 0 && <div className="flex justify-between"><dt className="text-slate-500">Scheme discount</dt><dd className="tabular-nums text-pine-700">−{inr(order.discount_total)}</dd></div>}
              <div className="flex justify-between"><dt className="text-slate-500">Taxable value</dt><dd className="tabular-nums">{inr(order.taxable_total)}</dd></div>
              <div className="flex justify-between"><dt className="text-slate-500">GST</dt><dd className="tabular-nums">{inr(order.gst_total)}</dd></div>
              <div className="flex justify-between border-t border-slate-200 pt-1 text-base font-semibold"><dt>Total</dt><dd className="tabular-nums">{inr(order.grand_total)}</dd></div>
            </dl>
            {shortLines.length > 0 && <p className="border-t border-slate-100 px-4 py-2 text-xs text-amber-700">Short supply on {shortLines.length} item{shortLines.length > 1 ? 's' : ''}.</p>}
          </div>

          {order.ai_unclear && order.ai_unclear.length > 0 && (
            <div className="card border-amber-200 p-4">
              <h2 className="text-sm font-semibold text-slate-900">Items the AI couldn't add</h2>
              <p className="mb-3 text-xs text-slate-500">The buyer was asked to clarify. Add the right product with Edit items if you know it.</p>
              <ul className="space-y-2 text-sm">
                {order.ai_unclear.map((u, i) => (
                  <li key={i} className="rounded-lg bg-amber-50 px-3 py-2">
                    <span className="font-medium">“{u.text}”</span>{u.quantity ? ` × ${u.quantity}${u.unit ? ' ' + u.unit : ''}` : ''}
                    <span className="text-amber-800"> · {u.reason}</span>
                    {u.candidates.length > 0 && (
                      <div className="mt-0.5 text-xs text-slate-600">Closest: {u.candidates.map((c) => `${c.name} (${Math.round(c.score * 100)}%)`).join(', ')}</div>
                    )}
                  </li>
                ))}
              </ul>
            </div>
          )}

          <div className="card p-4">
            <h2 className="mb-3 text-sm font-semibold text-slate-900">Timeline</h2>
            <ol className="space-y-3">
              {[...order.events].reverse().map((e) => (
                <li key={e.id} className="flex gap-3 text-sm">
                  <div className={`mt-1.5 h-2 w-2 shrink-0 rounded-full ${e.to_status === 'cancelled' ? 'bg-red-400' : e.to_status === 'on_hold' ? 'bg-amber-400' : 'bg-pine-500'}`} />
                  <div className="min-w-0">
                    <div className="text-slate-800"><span className="font-medium">{eventText(e)}</span> <span className="text-slate-500">· {who(e)} · {fmtDate(e.created_at, true)}</span></div>
                    {e.remark && e.kind === 'status' && e.from_status !== e.to_status && <div className="text-slate-600">{e.remark}</div>}
                    {e.message_id && <div className="text-xs text-pine-700">WhatsApp message sent to buyer</div>}
                    {e.notify_note && <div className="text-xs text-amber-700">{e.notify_note}</div>}
                  </div>
                </li>
              ))}
            </ol>
          </div>
        </div>

        <div className="space-y-4">
          {order.source_message && (
            <div className="card p-4 text-sm">
              <h2 className="mb-2 font-semibold text-slate-900">Buyer's message</h2>
              <div className="rounded-2xl rounded-bl-md bg-slate-50 px-3 py-2">
                {order.source_message.has_media && <div className="mb-1"><AuthedMedia msg={order.source_message} /></div>}
                {order.source_message.msg_type === 'audio' && <div className="text-xs text-slate-500">🎤 Voice note</div>}
                {order.source_message.transcript && <div className="italic text-slate-700">“{order.source_message.transcript}”</div>}
                {order.source_message.text && <div className="whitespace-pre-wrap">{order.source_message.text}</div>}
                <div className="mt-1 text-right text-[10px] text-slate-400">{fmtDate(order.source_message.created_at, true)}</div>
              </div>
              <p className="mt-2 text-xs text-slate-500">Read by AI. Check the items, then use Edit items to correct anything.</p>
            </div>
          )}
          <div className="card p-4 text-sm">
            <h2 className="mb-2 font-semibold text-slate-900">Customer</h2>
            <div className="font-medium">{order.customer_name}</div>
            <div className="font-mono text-xs text-slate-500">{order.customer_whatsapp}</div>
            {order.sales_user_name && <div className="mt-1 text-slate-600">Sales: {order.sales_user_name}</div>}
            <dl className="mt-3 space-y-1 border-t border-slate-100 pt-3">
              <div className="flex justify-between"><dt className="text-slate-500">Credit limit</dt><dd>{Number(order.credit.credit_limit) > 0 ? inr(order.credit.credit_limit) : 'Not set'}</dd></div>
              <div className="flex justify-between"><dt className="text-slate-500">Outstanding</dt><dd>{inr(order.credit.outstanding)}</dd></div>
              {order.credit.available !== null && (
                <div className="flex justify-between"><dt className="text-slate-500">Available</dt>
                  <dd className={Number(order.credit.available) < Number(order.grand_total) ? 'font-medium text-amber-700' : ''}>{inr(order.credit.available)}</dd></div>
              )}
            </dl>
          </div>

          {(order.invoice_number || order.dispatched_at || order.approval_remark || order.notes) && (
            <div className="card space-y-2 p-4 text-sm">
              {order.approval_remark && <div><div className="text-xs text-slate-500">Approval remark</div>{order.approval_remark}</div>}
              {order.invoice_number && <div><div className="text-xs text-slate-500">Invoice</div>{order.invoice_number} · {order.invoice_date}</div>}
              {order.dispatched_at && <div><div className="text-xs text-slate-500">Dispatch</div>
                {[order.vehicle_number, order.transporter, order.lr_number && `LR ${order.lr_number}`].filter(Boolean).join(' · ') || fmtDate(order.dispatched_at, true)}</div>}
              {order.notes && <div><div className="text-xs text-slate-500">Notes</div>{order.notes}</div>}
            </div>
          )}
        </div>
      </div>

      <Drawer open={dialog !== null} onClose={() => setDialog(null)}
        title={{ approve: 'Approve order on hold', reject: 'Reject order', cancel: 'Cancel order', invoice: 'Mark as invoiced', dispatch: 'Mark as dispatched' }[dialog ?? 'approve']}
        footer={<>
          <button className="btn-secondary" onClick={() => setDialog(null)}>Close</button>
          {dialog === 'approve' && <button className="btn-primary" disabled={busy || !remark.trim()} onClick={() => run('approve', { remark }, 'Order approved')}>Approve</button>}
          {dialog === 'reject' && <button className="btn-danger" disabled={busy || !remark.trim()} onClick={() => run('reject', { remark }, 'Order rejected')}>Reject order</button>}
          {dialog === 'cancel' && <button className="btn-danger" disabled={busy || remark.trim().length < 2} onClick={() => run('cancel', { reason: remark }, 'Order cancelled')}>Cancel order</button>}
          {dialog === 'invoice' && <button className="btn-primary" disabled={busy || !invoiceNo.trim()} onClick={() => run('invoice', {
            invoice_number: invoiceNo, invoice_date: invoiceDate || null,
            lines: Object.entries(invQty).map(([line_id, v]) => ({ line_id, invoiced_qty: v || '0' })),
          }, 'Marked invoiced')}>Save</button>}
          {dialog === 'dispatch' && <button className="btn-primary" disabled={busy} onClick={() => run('dispatch', {
            vehicle_number: vehicle || null, transporter: transporter || null, lr_number: lr || null,
          }, 'Marked dispatched')}>Save</button>}
        </>}>
        <ErrorBox message={dlgError} />
        {dialog === 'approve' && <>
          <p className="mb-3 rounded-lg bg-amber-50 p-3 text-sm text-amber-900">{order.hold_reasons}</p>
          <Field label="Remark *" hint="Why you're approving despite the hold, e.g. 'Payment promised Friday'">
            <textarea className="input" rows={3} value={remark} onChange={(e) => setRemark(e.target.value)} autoFocus />
          </Field>
        </>}
        {(dialog === 'reject' || dialog === 'cancel') && (
          <Field label={dialog === 'reject' ? 'Remark *' : 'Reason *'} hint="The buyer sees this in the cancellation message">
            <textarea className="input" rows={3} value={remark} onChange={(e) => setRemark(e.target.value)} autoFocus />
          </Field>
        )}
        {dialog === 'invoice' && <div className="space-y-3">
          <div className="grid grid-cols-2 gap-3">
            <Field label="Invoice number *"><input className="input" value={invoiceNo} onChange={(e) => setInvoiceNo(e.target.value)} autoFocus /></Field>
            <Field label="Invoice date"><input className="input" type="date" value={invoiceDate} onChange={(e) => setInvoiceDate(e.target.value)} /></Field>
          </div>
          <p className="text-xs text-slate-500">If you're supplying less than ordered, lower the invoiced quantity. The gap is recorded as short supply and the buyer is told.</p>
          <table className="w-full text-sm">
            <thead><tr><th className="th px-0">Item</th><th className="th px-0 text-right">Ordered</th><th className="th px-0 text-right">Invoiced</th></tr></thead>
            <tbody>
              {order.lines.map((l) => (
                <tr key={l.id}>
                  <td className="py-1.5">{l.name}</td>
                  <td className="py-1.5 text-right tabular-nums">{qty(l.quantity)}</td>
                  <td className="py-1.5 text-right"><input className="input ml-auto w-24 px-2 text-right" type="number" min={0} max={Number(l.quantity)} step="any"
                    value={invQty[l.id] ?? ''} onChange={(e) => setInvQty((q) => ({ ...q, [l.id]: e.target.value }))} /></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>}
        {dialog === 'dispatch' && <div className="grid grid-cols-2 gap-3">
          <Field label="Vehicle number"><input className="input uppercase" value={vehicle} onChange={(e) => setVehicle(e.target.value)} autoFocus /></Field>
          <Field label="LR number"><input className="input" value={lr} onChange={(e) => setLr(e.target.value)} /></Field>
          <Field label="Transporter" className="col-span-2"><input className="input" value={transporter} onChange={(e) => setTransporter(e.target.value)} /></Field>
          <p className="col-span-2 text-xs text-slate-500">All optional. Whatever you fill is included in the buyer's dispatch message.</p>
        </div>}
      </Drawer>
    </>
  )
}
