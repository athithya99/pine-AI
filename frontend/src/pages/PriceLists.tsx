import { useCallback, useEffect, useMemo, useState } from 'react'
import { ImportDialog } from '../components/ImportDialog'
import { Drawer, Empty, ErrorBox, Field, PageHeader, useToast } from '../components/ui'
import { api, errorMessage } from '../lib/api'
import { useAuth } from '../lib/auth'
import { inr, isManager, type Page, type PriceEntry, type PriceList, type Product } from '../lib/types'

export async function loadAllProducts(): Promise<Product[]> {
  const all: Product[] = []
  for (let page = 1; ; page++) {
    const { data } = await api.get<Page<Product>>('/products', { params: { page, size: 200 } })
    all.push(...data.items)
    if (all.length >= data.total || data.items.length === 0) return all
  }
}

export function PriceLists() {
  const { me } = useAuth()
  const manager = isManager(me?.role)
  const toast = useToast()
  const [lists, setLists] = useState<PriceList[]>([])
  const [selected, setSelected] = useState<PriceList | null>(null)
  const [products, setProducts] = useState<Product[]>([])
  const [prices, setPrices] = useState<Record<string, string>>({})
  const [saved, setSaved] = useState<Record<string, string>>({})
  const [filter, setFilter] = useState('')
  const [creating, setCreating] = useState(false)
  const [name, setName] = useState('')
  const [description, setDescription] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [importOpen, setImportOpen] = useState(false)

  const loadLists = useCallback(async () => {
    const { data } = await api.get<PriceList[]>('/price-lists')
    setLists(data)
    setSelected((s) => (s ? data.find((x) => x.id === s.id) ?? data[0] ?? null : data[0] ?? null))
  }, [])

  const loadItems = useCallback(async (pl: PriceList) => {
    const { data } = await api.get<PriceEntry[]>(`/price-lists/${pl.id}/items`)
    const map = Object.fromEntries(data.map((e) => [e.product_id, String(Number(e.price))]))
    setPrices(map); setSaved(map)
  }, [])

  useEffect(() => { loadLists(); loadAllProducts().then(setProducts).catch(() => {}) }, [loadLists])
  useEffect(() => { if (selected) loadItems(selected) }, [selected, loadItems])

  const visible = useMemo(() => {
    const f = filter.toLowerCase()
    return products.filter((p) => !f || p.name.toLowerCase().includes(f) || p.sku.toLowerCase().includes(f))
  }, [products, filter])

  const dirty = products.filter((p) => (prices[p.id] ?? '') !== (saved[p.id] ?? ''))

  const save = async () => {
    if (!selected) return
    setBusy(true)
    try {
      const upserts = dirty.filter((p) => prices[p.id]).map((p) => ({ product_id: p.id, price: prices[p.id] }))
      const removals = dirty.filter((p) => !prices[p.id])
      if (upserts.length) await api.put(`/price-lists/${selected.id}/items`, { items: upserts })
      for (const p of removals) await api.delete(`/price-lists/${selected.id}/items/${p.id}`)
      toast(`Saved ${dirty.length} price${dirty.length === 1 ? '' : 's'}`)
      loadItems(selected)
    } catch (e) { toast(errorMessage(e), 'error') } finally { setBusy(false) }
  }

  const create = async () => {
    setError(null); setBusy(true)
    try {
      const { data } = await api.post<PriceList>('/price-lists', { name, description: description || null })
      setCreating(false); setName(''); setDescription('')
      await loadLists(); setSelected(data)
      toast('Price list created')
    } catch (e) { setError(errorMessage(e)) } finally { setBusy(false) }
  }

  return (
    <>
      <PageHeader title="Price lists" subtitle="Assign a price list to a customer. Products not in the list use their base price."
        actions={manager && <button className="btn-primary" onClick={() => setCreating(true)}>New price list</button>} />

      <div className="grid gap-4 lg:grid-cols-[240px_1fr]">
        <div className="card h-fit p-2">
          {lists.length === 0 && <p className="p-3 text-sm text-slate-500">No price lists yet.</p>}
          {lists.map((l) => (
            <button key={l.id} onClick={() => setSelected(l)}
              className={`block w-full rounded-lg px-3 py-2 text-left text-sm ${selected?.id === l.id ? 'bg-pine-50 font-medium text-pine-700' : 'hover:bg-slate-50'}`}>
              {l.name}
              {l.description && <span className="block truncate text-xs font-normal text-slate-500">{l.description}</span>}
            </button>
          ))}
        </div>

        {selected ? (
          <div className="card">
            <div className="flex flex-wrap items-center gap-2 border-b border-slate-200 p-3">
              <input className="input max-w-xs" placeholder="Filter products" value={filter} onChange={(e) => setFilter(e.target.value)} />
              <div className="ml-auto flex gap-2">
                {manager && <button className="btn-secondary" onClick={() => setImportOpen(true)}>Import Excel</button>}
                {manager && <button className="btn-primary" disabled={!dirty.length || busy} onClick={save}>
                  {dirty.length ? `Save ${dirty.length} change${dirty.length === 1 ? '' : 's'}` : 'Saved'}
                </button>}
              </div>
            </div>
            <div className="overflow-x-auto">
              <table className="w-full min-w-[560px]">
                <thead className="bg-slate-50"><tr><th className="th">Product</th><th className="th">Base price</th><th className="th w-48">{selected.name} price</th></tr></thead>
                <tbody className="divide-y divide-slate-100">
                  {visible.map((p) => (
                    <tr key={p.id}>
                      <td className="td"><div className="font-medium">{p.name}</div><div className="font-mono text-xs text-slate-500">{p.sku}</div></td>
                      <td className="td text-slate-500">{inr(p.base_price)}</td>
                      <td className="td">
                        <input className="input" type="number" min={0} step="0.01" placeholder="Uses base price" disabled={!manager}
                          value={prices[p.id] ?? ''} onChange={(e) => setPrices((x) => ({ ...x, [p.id]: e.target.value }))} />
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            {products.length === 0 && <Empty title="Add products first" />}
          </div>
        ) : <div className="card"><Empty title="Create a price list to get started" /></div>}
      </div>

      <Drawer open={creating} title="New price list" onClose={() => setCreating(false)}
        footer={<><button className="btn-secondary" onClick={() => setCreating(false)}>Cancel</button>
          <button className="btn-primary" disabled={!name || busy} onClick={create}>Create</button></>}>
        <ErrorBox message={error} />
        <div className="space-y-3">
          <Field label="Name *"><input className="input" value={name} onChange={(e) => setName(e.target.value)} placeholder="e.g. Wholesale" /></Field>
          <Field label="Description"><input className="input" value={description} onChange={(e) => setDescription(e.target.value)} /></Field>
        </div>
      </Drawer>

      {selected && (
        <ImportDialog open={importOpen} onClose={() => setImportOpen(false)} title={`Import prices into ${selected.name}`}
          uploadPath={`/price-lists/${selected.id}/import`}
          columnsHint="Upload an .xlsx file with two columns: sku and price (in ₹, before GST). Existing prices are updated." onDone={() => loadItems(selected)} />
      )}
    </>
  )
}
