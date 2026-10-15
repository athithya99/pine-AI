import { useCallback, useEffect, useMemo, useState } from 'react'
import { Drawer, Empty, ErrorBox, Field, PageHeader, titleCase, useToast } from '../components/ui'
import { api, errorMessage } from '../lib/api'
import { useAuth } from '../lib/auth'
import { isManager, type Product, type Scheme, type SchemeType } from '../lib/types'
import { loadAllProducts } from './PriceLists'

const TYPE_LABEL: Record<SchemeType, string> = {
  percent: '% discount',
  flat_per_unit: '₹ off per unit',
  free_qty: 'Buy X get Y free',
}

const todayIST = () => new Intl.DateTimeFormat('en-CA', { timeZone: 'Asia/Kolkata' }).format(new Date())

type Form = {
  name: string; scheme_type: SchemeType; value: string; min_qty: string; free_qty: string
  start_date: string; end_date: string; product_ids: string[]; target: Scheme['target']; target_values: string
}
const emptyForm = (): Form => ({
  name: '', scheme_type: 'percent', value: '', min_qty: '0', free_qty: '0', start_date: todayIST(),
  end_date: todayIST(), product_ids: [], target: 'all', target_values: '',
})

function describe(s: Scheme) {
  const n = (v: string) => Number(v)
  if (s.scheme_type === 'percent') return `${n(s.value)}% off${n(s.min_qty) ? ` on ${n(s.min_qty)}+ units` : ''}`
  if (s.scheme_type === 'flat_per_unit') return `₹${n(s.value)} off per unit${n(s.min_qty) ? ` on ${n(s.min_qty)}+ units` : ''}`
  return `Buy ${n(s.min_qty)} get ${n(s.free_qty)} free`
}

function status(s: Scheme) {
  const t = todayIST()
  if (!s.is_active) return ['Paused', 'bg-slate-100 text-slate-600']
  if (s.end_date < t) return ['Ended', 'bg-slate-100 text-slate-500']
  if (s.start_date > t) return ['Upcoming', 'bg-blue-50 text-blue-700']
  return ['Running', 'bg-pine-50 text-pine-700']
}

export function Schemes() {
  const { me } = useAuth()
  const manager = isManager(me?.role)
  const toast = useToast()
  const [schemes, setSchemes] = useState<Scheme[]>([])
  const [products, setProducts] = useState<Product[]>([])
  const [editing, setEditing] = useState<Scheme | 'new' | null>(null)
  const [form, setForm] = useState<Form>(emptyForm())
  const [productFilter, setProductFilter] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  const load = useCallback(() => { api.get<Scheme[]>('/schemes').then((r) => setSchemes(r.data)).catch(() => {}) }, [])
  useEffect(() => { load(); loadAllProducts().then(setProducts).catch(() => {}) }, [load])

  const productName = useMemo(() => Object.fromEntries(products.map((p) => [p.id, p.name])), [products])

  const open = (s: Scheme | 'new') => {
    setEditing(s); setError(null); setProductFilter('')
    setForm(s === 'new' ? emptyForm() : {
      name: s.name, scheme_type: s.scheme_type, value: String(Number(s.value)), min_qty: String(Number(s.min_qty)),
      free_qty: String(Number(s.free_qty)), start_date: s.start_date, end_date: s.end_date,
      product_ids: s.product_ids, target: s.target, target_values: s.target_values.join(', '),
    })
  }
  const set = <K extends keyof Form>(k: K, v: Form[K]) => setForm((f) => ({ ...f, [k]: v }))

  const save = async () => {
    setBusy(true); setError(null)
    const body = {
      ...form,
      value: form.value || '0', min_qty: form.min_qty || '0', free_qty: form.free_qty || '0',
      target_values: form.target === 'all' ? [] : form.target_values.split(',').map((t) => t.trim().toLowerCase()).filter(Boolean),
    }
    try {
      if (editing === 'new') await api.post('/schemes', body)
      else if (editing) await api.patch(`/schemes/${editing.id}`, body)
      toast(editing === 'new' ? 'Scheme created' : 'Scheme updated')
      setEditing(null); load()
    } catch (e) { setError(errorMessage(e)) } finally { setBusy(false) }
  }

  const toggleProduct = (id: string) =>
    set('product_ids', form.product_ids.includes(id) ? form.product_ids.filter((x) => x !== id) : [...form.product_ids, id])

  return (
    <>
      <PageHeader title="Schemes" subtitle="Time-bound offers. If several apply to a line, the one giving the buyer the most benefit is used; they never stack."
        actions={manager && <button className="btn-primary" onClick={() => open('new')}>New scheme</button>} />

      <div className="card overflow-x-auto">
        <table className="w-full min-w-[760px]">
          <thead className="bg-slate-50"><tr>
            <th className="th">Scheme</th><th className="th">Offer</th><th className="th">Products</th>
            <th className="th">For</th><th className="th">Dates</th><th className="th">Status</th>
          </tr></thead>
          <tbody className="divide-y divide-slate-100">
            {schemes.map((s) => {
              const [label, cls] = status(s)
              return (
                <tr key={s.id} className={`hover:bg-slate-50 ${manager ? 'cursor-pointer' : ''}`} onClick={() => manager && open(s)}>
                  <td className="td font-medium">{s.name}</td>
                  <td className="td">{describe(s)}</td>
                  <td className="td text-xs text-slate-600">{s.product_ids.map((id) => productName[id] ?? '…').join(', ')}</td>
                  <td className="td">{s.target === 'all' ? 'All customers' : `${titleCase(s.target)}: ${s.target_values.join(', ')}`}</td>
                  <td className="td text-xs">{s.start_date} → {s.end_date}</td>
                  <td className="td"><span className={`badge ${cls}`}>{label}</span></td>
                </tr>
              )
            })}
          </tbody>
        </table>
        {schemes.length === 0 && <Empty title="No schemes yet" />}
      </div>

      <Drawer open={editing !== null} title={editing === 'new' ? 'New scheme' : 'Edit scheme'} onClose={() => setEditing(null)}
        footer={<>
          {editing && editing !== 'new' && (
            <button className="btn-secondary mr-auto" onClick={async () => {
              await api.patch(`/schemes/${editing.id}`, { is_active: !editing.is_active }); setEditing(null); load()
            }}>{editing.is_active ? 'Pause' : 'Resume'}</button>
          )}
          <button className="btn-secondary" onClick={() => setEditing(null)}>Cancel</button>
          <button className="btn-primary" disabled={busy} onClick={save}>Save</button>
        </>}>
        <ErrorBox message={error} />
        <div className="grid grid-cols-2 gap-3">
          <Field label="Name *" className="col-span-2"><input className="input" value={form.name} onChange={(e) => set('name', e.target.value)} /></Field>
          <Field label="Type" className="col-span-2">
            <select className="input" value={form.scheme_type} onChange={(e) => set('scheme_type', e.target.value as SchemeType)}>
              {Object.entries(TYPE_LABEL).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
            </select>
          </Field>
          {form.scheme_type !== 'free_qty' ? <>
            <Field label={form.scheme_type === 'percent' ? 'Discount %' : '₹ off per unit'}>
              <input className="input" type="number" min={0} step="0.01" value={form.value} onChange={(e) => set('value', e.target.value)} />
            </Field>
            <Field label="Minimum quantity" hint="0 = any quantity">
              <input className="input" type="number" min={0} value={form.min_qty} onChange={(e) => set('min_qty', e.target.value)} />
            </Field>
          </> : <>
            <Field label="Buy quantity"><input className="input" type="number" min={1} value={form.min_qty} onChange={(e) => set('min_qty', e.target.value)} /></Field>
            <Field label="Free quantity"><input className="input" type="number" min={1} value={form.free_qty} onChange={(e) => set('free_qty', e.target.value)} /></Field>
          </>}
          <Field label="Start date"><input className="input" type="date" value={form.start_date} onChange={(e) => set('start_date', e.target.value)} /></Field>
          <Field label="End date" hint="Last day the offer applies"><input className="input" type="date" value={form.end_date} onChange={(e) => set('end_date', e.target.value)} /></Field>
          <Field label="Who gets it" className="col-span-2">
            <select className="input" value={form.target} onChange={(e) => set('target', e.target.value as Form['target'])}>
              <option value="all">All customers</option>
              <option value="tags">Customers with tags</option>
              <option value="customer_types">Customer types</option>
            </select>
          </Field>
          {form.target !== 'all' && (
            <Field label={form.target === 'tags' ? 'Tags' : 'Types'} className="col-span-2"
              hint={form.target === 'tags' ? 'Comma separated' : 'distributor, retailer, dealer, other'}>
              <input className="input" value={form.target_values} onChange={(e) => set('target_values', e.target.value)} />
            </Field>
          )}
          <div className="col-span-2">
            <span className="label">Products * ({form.product_ids.length} selected)</span>
            <input className="input mb-2" placeholder="Filter" value={productFilter} onChange={(e) => setProductFilter(e.target.value)} />
            <div className="max-h-56 overflow-y-auto rounded-lg border border-slate-200">
              {products.filter((p) => !productFilter || p.name.toLowerCase().includes(productFilter.toLowerCase()) || p.sku.toLowerCase().includes(productFilter.toLowerCase()))
                .map((p) => (
                  <label key={p.id} className="flex items-center gap-2 border-b border-slate-100 px-3 py-2 text-sm last:border-0">
                    <input type="checkbox" checked={form.product_ids.includes(p.id)} onChange={() => toggleProduct(p.id)} />
                    {p.name} <span className="font-mono text-xs text-slate-400">{p.sku}</span>
                  </label>
                ))}
            </div>
          </div>
        </div>
      </Drawer>
    </>
  )
}
