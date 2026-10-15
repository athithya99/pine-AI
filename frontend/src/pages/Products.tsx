import { useCallback, useEffect, useState } from 'react'
import { ImportDialog } from '../components/ImportDialog'
import { Drawer, Empty, ErrorBox, Field, PageHeader, Pagination, STOCK_BADGE, titleCase, useToast } from '../components/ui'
import { api, download, errorMessage } from '../lib/api'
import { useAuth } from '../lib/auth'
import { inr, isManager, type GstRate, type Page, type Product, type StockStatus } from '../lib/types'

const SIZE = 25
const STOCK: StockStatus[] = ['in_stock', 'low', 'out_of_stock']

type Form = Record<string, string>
const emptyForm = (): Form => ({
  sku: '', name: '', brand: '', category: '', uom: 'PCS', pack_size: '', hsn_code: '', gst_rate: '5',
  mrp: '', base_price: '', min_order_qty: '1', aliases: '', stock_status: 'in_stock',
})

export function Products() {
  const { me } = useAuth()
  const manager = isManager(me?.role)
  const toast = useToast()
  const [data, setData] = useState<Page<Product>>({ items: [], total: 0, page: 1, size: SIZE })
  const [page, setPage] = useState(1)
  const [q, setQ] = useState('')
  const [stock, setStock] = useState('')
  const [loading, setLoading] = useState(true)
  const [rates, setRates] = useState<GstRate[]>([])
  const [editing, setEditing] = useState<Product | 'new' | null>(null)
  const [form, setForm] = useState<Form>(emptyForm())
  const [error, setError] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)
  const [importOpen, setImportOpen] = useState(false)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const { data } = await api.get<Page<Product>>('/products', {
        params: { page, size: SIZE, q: q || undefined, stock_status: stock || undefined },
      })
      setData(data)
    } finally { setLoading(false) }
  }, [page, q, stock])

  useEffect(() => { const t = setTimeout(load, 250); return () => clearTimeout(t) }, [load])
  useEffect(() => { api.get<GstRate[]>('/gst-rates').then((r) => setRates(r.data.filter((x) => x.is_active))).catch(() => {}) }, [])

  const open = (p: Product | 'new') => {
    setEditing(p); setError(null)
    setForm(p === 'new' ? emptyForm() : {
      ...emptyForm(),
      ...Object.fromEntries(Object.entries(p).map(([k, v]) => [k, v === null ? '' : String(v)])),
      gst_rate: String(Number(p.gst_rate)),
      min_order_qty: String(Number(p.min_order_qty)),
      aliases: p.aliases.join(', '),
    })
  }
  const set = (k: string, v: string) => setForm((f) => ({ ...f, [k]: v }))

  const save = async () => {
    setSaving(true); setError(null)
    const n = (v: string) => (v === '' ? null : v)
    const body = {
      sku: form.sku, name: form.name, brand: n(form.brand), category: n(form.category), uom: form.uom || 'PCS',
      pack_size: n(form.pack_size), hsn_code: n(form.hsn_code), gst_rate: form.gst_rate, mrp: n(form.mrp),
      base_price: form.base_price, min_order_qty: form.min_order_qty || '1', stock_status: form.stock_status,
      aliases: form.aliases.split(',').map((a) => a.trim()).filter(Boolean),
    }
    try {
      if (editing === 'new') await api.post('/products', body)
      else if (editing) await api.patch(`/products/${editing.id}`, body)
      toast(editing === 'new' ? 'Product added' : 'Product updated')
      setEditing(null); load()
    } catch (e) { setError(errorMessage(e)) } finally { setSaving(false) }
  }

  const quickStock = async (p: Product, s: StockStatus) => {
    try { await api.put(`/products/${p.id}/stock`, { stock_status: s }); load() } catch (e) { toast(errorMessage(e), 'error') }
  }

  return (
    <>
      <PageHeader title="Products" subtitle="Item names and aliases here are what AI ordering matches buyer messages against."
        actions={<>
          <button className="btn-secondary" onClick={() => download('/products/export', 'products.xlsx')}>Export</button>
          {manager && <button className="btn-secondary" onClick={() => setImportOpen(true)}>Import Excel</button>}
          {manager && <button className="btn-primary" onClick={() => open('new')}>Add product</button>}
        </>} />

      <div className="card">
        <div className="flex flex-wrap gap-2 border-b border-slate-200 p-3">
          <input className="input max-w-xs" placeholder="Search name, SKU, brand or alias" value={q} onChange={(e) => { setQ(e.target.value); setPage(1) }} />
          <select className="input max-w-[180px]" value={stock} onChange={(e) => { setStock(e.target.value); setPage(1) }}>
            <option value="">All stock</option>
            {STOCK.map((s) => <option key={s} value={s}>{titleCase(s)}</option>)}
          </select>
        </div>
        <div className="overflow-x-auto">
          <table className="w-full min-w-[760px]">
            <thead className="bg-slate-50"><tr>
              <th className="th">Product</th><th className="th">SKU</th><th className="th">Base price</th>
              <th className="th">GST</th><th className="th">MRP</th><th className="th">Stock</th>
            </tr></thead>
            <tbody className="divide-y divide-slate-100">
              {data.items.map((p) => (
                <tr key={p.id} className="hover:bg-slate-50">
                  <td className={`td ${manager ? 'cursor-pointer' : ''}`} onClick={() => manager && open(p)}>
                    <div className="font-medium text-slate-900">{p.name}</div>
                    <div className="text-xs text-slate-500">{[p.brand, p.category, p.pack_size].filter(Boolean).join(' · ')}</div>
                    {p.aliases.length > 0 && <div className="mt-0.5 text-xs text-slate-400">aka {p.aliases.join(', ')}</div>}
                  </td>
                  <td className="td font-mono text-xs">{p.sku}</td>
                  <td className="td">{inr(p.base_price)}<span className="text-xs text-slate-400"> / {p.uom}</span></td>
                  <td className="td">{Number(p.gst_rate)}%</td>
                  <td className="td">{inr(p.mrp)}</td>
                  <td className="td">
                    {manager ? (
                      <select className={`badge cursor-pointer border-0 ${STOCK_BADGE[p.stock_status]}`} value={p.stock_status}
                        onChange={(e) => quickStock(p, e.target.value as StockStatus)}>
                        {STOCK.map((s) => <option key={s} value={s}>{titleCase(s)}</option>)}
                      </select>
                    ) : <span className={`badge ${STOCK_BADGE[p.stock_status]}`}>{titleCase(p.stock_status)}</span>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {!loading && data.total === 0 && <Empty title="No products yet">{manager && 'Add products one by one or import your catalog from Excel.'}</Empty>}
        <Pagination page={page} size={SIZE} total={data.total} onPage={setPage} />
      </div>

      <Drawer open={editing !== null} title={editing === 'new' ? 'Add product' : 'Edit product'} onClose={() => setEditing(null)}
        footer={<>
          {editing && editing !== 'new' && (
            <button className="btn-danger mr-auto" onClick={async () => {
              await api.patch(`/products/${editing.id}`, { is_active: !editing.is_active })
              toast(editing.is_active ? 'Product deactivated' : 'Product activated'); setEditing(null); load()
            }}>{editing.is_active ? 'Deactivate' : 'Activate'}</button>
          )}
          <button className="btn-secondary" onClick={() => setEditing(null)}>Cancel</button>
          <button className="btn-primary" disabled={saving} onClick={save}>{saving ? 'Saving…' : 'Save'}</button>
        </>}>
        <ErrorBox message={error} />
        <div className="grid grid-cols-2 gap-3">
          <Field label="SKU code *"><input className="input uppercase" value={form.sku} onChange={(e) => set('sku', e.target.value)} /></Field>
          <Field label="Unit"><input className="input uppercase" value={form.uom} onChange={(e) => set('uom', e.target.value)} /></Field>
          <Field label="Product name *" className="col-span-2"><input className="input" value={form.name} onChange={(e) => set('name', e.target.value)} /></Field>
          <Field label="Brand"><input className="input" value={form.brand} onChange={(e) => set('brand', e.target.value)} /></Field>
          <Field label="Category"><input className="input" value={form.category} onChange={(e) => set('category', e.target.value)} /></Field>
          <Field label="Pack size"><input className="input" value={form.pack_size} onChange={(e) => set('pack_size', e.target.value)} placeholder="e.g. 1 kg" /></Field>
          <Field label="HSN code"><input className="input" value={form.hsn_code} onChange={(e) => set('hsn_code', e.target.value)} /></Field>
          <Field label="Base price (₹, before GST) *"><input className="input" type="number" min={0} step="0.01" value={form.base_price} onChange={(e) => set('base_price', e.target.value)} /></Field>
          <Field label="GST rate *">
            <select className="input" value={form.gst_rate} onChange={(e) => set('gst_rate', e.target.value)}>
              {rates.map((r) => <option key={r.id} value={String(Number(r.rate))}>{r.label}</option>)}
            </select>
          </Field>
          <Field label="MRP (₹)"><input className="input" type="number" min={0} step="0.01" value={form.mrp} onChange={(e) => set('mrp', e.target.value)} /></Field>
          <Field label="Minimum order qty"><input className="input" type="number" min={0} step="any" value={form.min_order_qty} onChange={(e) => set('min_order_qty', e.target.value)} /></Field>
          <Field label="Stock status">
            <select className="input" value={form.stock_status} onChange={(e) => set('stock_status', e.target.value)}>
              {STOCK.map((s) => <option key={s} value={s}>{titleCase(s)}</option>)}
            </select>
          </Field>
          <Field label="Aliases" className="col-span-2" hint="Other names buyers use: local names, short forms, common misspellings. Comma separated.">
            <input className="input" value={form.aliases} onChange={(e) => set('aliases', e.target.value)} />
          </Field>
        </div>
      </Drawer>

      <ImportDialog open={importOpen} onClose={() => setImportOpen(false)} title="Import products"
        uploadPath="/products/import" templatePath="/products/import/template" templateName="product_import_template.xlsx" onDone={load} />
    </>
  )
}
