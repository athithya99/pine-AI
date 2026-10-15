import { useEffect, useMemo, useRef, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { ErrorBox, Field, PageHeader, STOCK_BADGE, titleCase, useToast } from '../components/ui'
import { api, errorMessage } from '../lib/api'
import { inr, type Customer, type OrderDetail, type Page, type Product } from '../lib/types'

type Line = { product: Pick<Product, 'id' | 'sku' | 'name' | 'uom' | 'stock_status'>; quantity: string; raw_text?: string | null }

interface QuoteLine {
  product_id: string; quantity: string; unit_price: string; price_source: string; gross: string; discount: string
  free_qty: string; scheme_name: string | null; gst_rate: string; gst_amount: string; line_total: string
  below_min_qty: boolean
}
interface Quote { lines: QuoteLine[]; taxable_total: string; gst_total: string; grand_total: string; has_out_of_stock: boolean }

const PRICE_SOURCE: Record<string, string> = { customer_price: 'Customer price', price_list: 'Price list', base_price: 'Base price' }

/** Type-ahead picker used for customers and products. */
function Picker<T extends { id: string }>({ placeholder, search, render, onPick, autoFocus }: {
  placeholder: string
  search: (q: string) => Promise<T[]>
  render: (t: T) => React.ReactNode
  onPick: (t: T) => void
  autoFocus?: boolean
}) {
  const [q, setQ] = useState('')
  const [items, setItems] = useState<T[]>([])
  const [open, setOpen] = useState(false)
  const [hi, setHi] = useState(0)
  const box = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!open) return
    const t = setTimeout(() => { search(q).then((r) => { setItems(r); setHi(0) }).catch(() => setItems([])) }, 200)
    return () => clearTimeout(t)
  }, [q, open, search])
  useEffect(() => {
    const close = (e: MouseEvent) => { if (box.current && !box.current.contains(e.target as Node)) setOpen(false) }
    document.addEventListener('mousedown', close)
    return () => document.removeEventListener('mousedown', close)
  }, [])

  const pick = (t: T) => { onPick(t); setQ(''); setOpen(false) }
  return (
    <div ref={box} className="relative">
      <input className="input" placeholder={placeholder} value={q} autoFocus={autoFocus}
        onFocus={() => setOpen(true)} onChange={(e) => { setQ(e.target.value); setOpen(true) }}
        onKeyDown={(e) => {
          if (e.key === 'ArrowDown') { e.preventDefault(); setHi((h) => Math.min(h + 1, items.length - 1)) }
          if (e.key === 'ArrowUp') { e.preventDefault(); setHi((h) => Math.max(h - 1, 0)) }
          if (e.key === 'Enter' && items[hi]) { e.preventDefault(); pick(items[hi]) }
          if (e.key === 'Escape') setOpen(false)
        }} />
      {open && items.length > 0 && (
        <ul className="absolute z-20 mt-1 max-h-72 w-full overflow-y-auto rounded-lg border border-slate-200 bg-white py-1 shadow-lg">
          {items.map((t, i) => (
            <li key={t.id}>
              <button type="button" onMouseDown={(e) => e.preventDefault()} onClick={() => pick(t)}
                className={`block w-full px-3 py-2 text-left text-sm ${i === hi ? 'bg-pine-50' : 'hover:bg-slate-50'}`}>
                {render(t)}
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}

export function OrderForm() {
  const { id } = useParams()
  const editing = Boolean(id)
  const navigate = useNavigate()
  const toast = useToast()

  const [order, setOrder] = useState<OrderDetail | null>(null)
  const [customer, setCustomer] = useState<Pick<Customer, 'id' | 'business_name' | 'whatsapp_number'> & { credit_limit?: string } | null>(null)
  const [lines, setLines] = useState<Line[]>([])
  const [notes, setNotes] = useState('')
  const [quote, setQuote] = useState<Quote | null>(null)
  const [quoteError, setQuoteError] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    if (!id) return
    api.get<OrderDetail>(`/orders/${id}`).then(({ data }) => {
      setOrder(data)
      setCustomer({ id: data.customer_id, business_name: data.customer_name, whatsapp_number: data.customer_whatsapp })
      setLines(data.lines.map((l) => ({
        product: { id: l.product_id, sku: l.sku, name: l.name, uom: l.uom, stock_status: l.out_of_stock ? 'out_of_stock' : 'in_stock' },
        quantity: String(Number(l.quantity)),
        raw_text: l.raw_text,
      })))
      setNotes(data.notes ?? '')
    }).catch((e) => setError(errorMessage(e)))
  }, [id])

  // live price preview from the same M3 rules the server uses
  const validLines = useMemo(() => lines.filter((l) => Number(l.quantity) > 0), [lines])
  useEffect(() => {
    if (!customer || validLines.length === 0) { setQuote(null); return }
    const t = setTimeout(() => {
      api.post<Quote>('/pricing/quote', {
        customer_id: customer.id, lines: validLines.map((l) => ({ product_id: l.product.id, quantity: l.quantity })),
      }).then((r) => { setQuote(r.data); setQuoteError(null) }).catch((e) => setQuoteError(errorMessage(e)))
    }, 300)
    return () => clearTimeout(t)
  }, [customer, validLines])

  const searchCustomers = useMemo(() => async (q: string) =>
    (await api.get<Page<Customer>>('/customers', { params: { q: q || undefined, size: 10 } })).data.items
      .filter((c) => c.status !== 'inactive'), [])
  const searchProducts = useMemo(() => async (q: string) =>
    (await api.get<Page<Product>>('/products', { params: { q: q || undefined, size: 12 } })).data.items, [])

  const addProduct = (p: Product) => {
    setLines((ls) => {
      const i = ls.findIndex((l) => l.product.id === p.id)
      if (i >= 0) return ls.map((l, j) => (j === i ? { ...l, quantity: String(Number(l.quantity || 0) + 1) } : l))
      return [...ls, { product: p, quantity: String(Number(p.min_order_qty) || 1) }]
    })
  }
  const setQty = (i: number, v: string) => setLines((ls) => ls.map((l, j) => (j === i ? { ...l, quantity: v } : l)))
  const remove = (i: number) => setLines((ls) => ls.filter((_, j) => j !== i))
  const priced = (pid: string) => quote?.lines.find((q) => q.product_id === pid)

  const submit = async (action: 'draft' | 'quote' | 'confirm') => {
    if (!customer) { setError('Pick a customer'); return }
    if (validLines.length === 0) { setError('Add at least one product with a quantity'); return }
    setBusy(true); setError(null)
    const body = {
      lines: validLines.map((l) => ({ product_id: l.product.id, quantity: l.quantity, raw_text: l.raw_text ?? null })),
      notes: notes || null,
    }
    try {
      let saved: OrderDetail
      if (editing && order) {
        saved = (await api.put<OrderDetail>(`/orders/${order.id}/lines`, body)).data
        toast(saved.status === 'draft' ? 'Order updated' : 'Order updated and revised quote sent to the buyer')
      } else {
        saved = (await api.post<OrderDetail>('/orders', { ...body, customer_id: customer.id, action })).data
        toast(action === 'quote' ? `Quote ${saved.number} sent` : action === 'confirm'
          ? (saved.status === 'on_hold' ? `${saved.number} confirmed and put on hold for approval` : `${saved.number} confirmed`)
          : `Draft ${saved.number} saved`)
      }
      navigate(`/orders/${saved.id}`)
    } catch (e) { setError(errorMessage(e)) } finally { setBusy(false) }
  }

  const requote = editing && order && order.status !== 'draft'

  return (
    <>
      <PageHeader
        title={editing ? `Edit ${order?.number ?? 'order'}` : 'New order'}
        subtitle={editing ? (requote ? 'Saving sends the revised quote to the buyer to confirm again.' : 'Change items or quantities.')
          : 'Prices follow your price rules: customer price, then price list, then base price, then the best scheme.'}
        actions={<Link to={editing && order ? `/orders/${order.id}` : '/orders'} className="btn-secondary">Back</Link>}
      />
      <ErrorBox message={error} />

      <div className="grid gap-6 lg:grid-cols-[1fr_300px]">
        <div className="space-y-4">
          <div className="card p-4">
            <Field label="Customer">
              {customer ? (
                <div className="flex items-center justify-between rounded-lg border border-slate-200 px-3 py-2">
                  <div>
                    <div className="font-medium text-slate-900">{customer.business_name}</div>
                    <div className="font-mono text-xs text-slate-500">{customer.whatsapp_number}</div>
                  </div>
                  {!editing && <button className="text-xs text-slate-500 hover:text-slate-800" onClick={() => { setCustomer(null); setQuote(null) }}>Change</button>}
                </div>
              ) : (
                <Picker<Customer> placeholder="Search customer by name or phone" autoFocus search={searchCustomers} onPick={setCustomer}
                  render={(c) => <><span className="font-medium">{c.business_name}</span> <span className="font-mono text-xs text-slate-500">{c.whatsapp_number}</span>
                    {c.status === 'lead' && <span className="badge ml-1 bg-slate-100 text-slate-500">lead</span>}</>} />
              )}
            </Field>
          </div>

          <div className="card">
            <div className="border-b border-slate-200 p-4">
              <Picker<Product> placeholder="Add product: type SKU or name, Enter to add" search={searchProducts} onPick={addProduct}
                render={(p) => <div className="flex items-center justify-between gap-2">
                  <span><span className="font-mono text-xs text-slate-500">{p.sku}</span> {p.name}</span>
                  <span className="flex items-center gap-2 text-xs text-slate-500">{inr(p.base_price)}
                    {p.stock_status !== 'in_stock' && <span className={`badge ${STOCK_BADGE[p.stock_status]}`}>{titleCase(p.stock_status)}</span>}</span>
                </div>} />
            </div>
            <div className="overflow-x-auto">
              <table className="w-full min-w-[640px]">
                <thead className="bg-slate-50">
                  <tr><th className="th">Product</th><th className="th w-28">Qty</th><th className="th text-right">Rate</th>
                    <th className="th text-right">Discount</th><th className="th text-right">GST</th><th className="th text-right">Total</th><th className="th w-8" /></tr>
                </thead>
                <tbody className="divide-y divide-slate-100">
                  {lines.map((l, i) => {
                    const p = priced(l.product.id)
                    return (
                      <tr key={l.product.id}>
                        <td className="td">
                          <div className="font-medium text-slate-900">{l.product.name}</div>
                          <div className="flex flex-wrap items-center gap-1 text-xs text-slate-500">
                            <span className="font-mono">{l.product.sku}</span>
                            {l.raw_text && <span className="italic">buyer wrote “{l.raw_text}”</span>}
                            {p && <span>· {PRICE_SOURCE[p.price_source]}</span>}
                            {p?.scheme_name && <span className="badge bg-pine-50 text-pine-700">{p.scheme_name}</span>}
                            {p && Number(p.free_qty) > 0 && <span className="badge bg-pine-50 text-pine-700">+{Number(p.free_qty)} free</span>}
                            {l.product.stock_status === 'out_of_stock' && <span className="badge bg-red-50 text-red-700">Out of stock</span>}
                            {p?.below_min_qty && <span className="badge bg-amber-50 text-amber-700">Below min. qty</span>}
                          </div>
                        </td>
                        <td className="td">
                          <div className="flex items-center gap-1">
                            <input className="input w-20 px-2" type="number" min={0} step="any" value={l.quantity} onChange={(e) => setQty(i, e.target.value)} />
                            <span className="text-xs text-slate-500">{l.product.uom}</span>
                          </div>
                        </td>
                        <td className="td text-right tabular-nums">{p ? inr(p.unit_price) : '…'}</td>
                        <td className="td text-right tabular-nums text-pine-700">{p && Number(p.discount) > 0 ? `−${inr(p.discount)}` : '—'}</td>
                        <td className="td text-right tabular-nums text-slate-600">{p ? `${Number(p.gst_rate)}%` : ''}</td>
                        <td className="td text-right font-medium tabular-nums">{p ? inr(p.line_total) : '…'}</td>
                        <td className="td"><button className="text-slate-400 hover:text-red-600" onClick={() => remove(i)} aria-label="Remove">✕</button></td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            </div>
            {lines.length === 0 && <p className="px-4 py-8 text-center text-sm text-slate-500">No items yet. Search above to add products.</p>}
            {quoteError && <p className="px-4 pb-3 text-sm text-red-600">{quoteError}</p>}
          </div>

          <div className="card p-4">
            <Field label="Notes" hint="Internal note, not sent to the buyer">
              <textarea className="input" rows={2} maxLength={1000} value={notes} onChange={(e) => setNotes(e.target.value)} />
            </Field>
          </div>
        </div>

        <div className="card h-fit p-4 lg:sticky lg:top-6">
          <dl className="space-y-2 text-sm">
            <div className="flex justify-between"><dt className="text-slate-500">Taxable value</dt><dd className="tabular-nums">{inr(quote?.taxable_total ?? 0)}</dd></div>
            <div className="flex justify-between"><dt className="text-slate-500">GST</dt><dd className="tabular-nums">{inr(quote?.gst_total ?? 0)}</dd></div>
            <div className="flex justify-between border-t border-slate-200 pt-2 text-base font-semibold"><dt>Total</dt><dd className="tabular-nums">{inr(quote?.grand_total ?? 0)}</dd></div>
          </dl>
          {quote?.has_out_of_stock && <p className="mt-3 rounded-lg bg-amber-50 p-2 text-xs text-amber-800">Has out-of-stock items. If confirmed, the order goes on hold for approval.</p>}
          <div className="mt-4 flex flex-col gap-2">
            {editing ? (
              <button className="btn-primary" disabled={busy} onClick={() => submit('draft')}>{requote ? 'Save and send revised quote' : 'Save changes'}</button>
            ) : <>
              <button className="btn-primary" disabled={busy} onClick={() => submit('quote')}>Send quote on WhatsApp</button>
              <button className="btn-secondary" disabled={busy} onClick={() => submit('confirm')} title="Use when the buyer has already agreed, e.g. on a call">Confirm now (buyer agreed)</button>
              <button className="btn-secondary" disabled={busy} onClick={() => submit('draft')}>Save as draft</button>
            </>}
          </div>
        </div>
      </div>
    </>
  )
}
