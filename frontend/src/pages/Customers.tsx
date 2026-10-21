import { useCallback, useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { ImportDialog } from '../components/ImportDialog'
import { Drawer, Empty, ErrorBox, Field, PageHeader, Pagination, titleCase, useToast } from '../components/ui'
import { api, download, errorMessage } from '../lib/api'
import { useAuth } from '../lib/auth'
import { inr, isManager, type Customer, type CustomerType, type Page, type PriceList, type User } from '../lib/types'

const TYPES: CustomerType[] = ['distributor', 'retailer', 'dealer', 'other']
const SIZE = 25

type Form = Record<string, string | boolean>

const emptyForm = (): Form => ({
  business_name: '', whatsapp_number: '', contact_person: '', alternate_phone: '', email: '', gstin: '',
  billing_address: '', shipping_address: '', city: '', state: '', pincode: '', customer_type: 'retailer',
  tags: '', price_list_id: '', assigned_user_id: '', credit_limit: '0', credit_days: '0', opening_balance: '0', whatsapp_opt_in: false,
})

const toForm = (c: Customer): Form => ({
  ...emptyForm(),
  ...Object.fromEntries(Object.entries(c).map(([k, v]) => [k, v ?? ''])),
  tags: c.tags.join(', '),
  credit_limit: String(c.credit_limit),
  credit_days: String(c.credit_days),
  opening_balance: String(c.opening_balance ?? '0'),
  whatsapp_opt_in: c.whatsapp_opt_in,
}) as Form

export function Customers() {
  const { me } = useAuth()
  const manager = isManager(me?.role)
  const canEdit = manager || me?.role === 'sales'
  const toast = useToast()
  const navigate = useNavigate()

  const [data, setData] = useState<Page<Customer>>({ items: [], total: 0, page: 1, size: SIZE })
  const [page, setPage] = useState(1)
  const [q, setQ] = useState('')
  const [type, setType] = useState('')
  const [loading, setLoading] = useState(true)
  const [priceLists, setPriceLists] = useState<PriceList[]>([])
  const [salesUsers, setSalesUsers] = useState<User[]>([])

  const [editing, setEditing] = useState<Customer | 'new' | null>(null)
  const [form, setForm] = useState<Form>(emptyForm())
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [importOpen, setImportOpen] = useState(false)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const { data } = await api.get<Page<Customer>>('/customers', {
        params: { page, size: SIZE, q: q || undefined, customer_type: type || undefined },
      })
      setData(data)
    } finally { setLoading(false) }
  }, [page, q, type])

  useEffect(() => { const t = setTimeout(load, 250); return () => clearTimeout(t) }, [load])
  useEffect(() => {
    api.get<PriceList[]>('/price-lists').then((r) => setPriceLists(r.data)).catch(() => {})
    if (manager) api.get<User[]>('/users', { params: { role: 'sales', active: true } }).then((r) => setSalesUsers(r.data)).catch(() => {})
  }, [manager])

  const open = (c: Customer | 'new') => {
    setEditing(c); setError(null)
    setForm(c === 'new' ? emptyForm() : toForm(c))
  }
  const set = (k: string, v: string | boolean) => setForm((f) => ({ ...f, [k]: v }))

  const save = async () => {
    setSaving(true); setError(null)
    const nullable = (v: string | boolean) => (v === '' ? null : v)
    const body: Record<string, unknown> = {
      business_name: form.business_name, whatsapp_number: form.whatsapp_number,
      contact_person: nullable(form.contact_person), alternate_phone: nullable(form.alternate_phone),
      email: nullable(form.email), gstin: nullable(form.gstin), billing_address: nullable(form.billing_address),
      shipping_address: nullable(form.shipping_address), city: nullable(form.city), state: nullable(form.state),
      pincode: nullable(form.pincode), customer_type: form.customer_type,
      tags: String(form.tags).split(',').map((t) => t.trim()).filter(Boolean),
      whatsapp_opt_in: form.whatsapp_opt_in,
    }
    if (manager) {
      Object.assign(body, {
        price_list_id: nullable(form.price_list_id), assigned_user_id: nullable(form.assigned_user_id),
        credit_limit: form.credit_limit || '0', credit_days: Number(form.credit_days || 0),
        opening_balance: form.opening_balance || '0',
      })
    }
    try {
      if (editing === 'new') await api.post('/customers', body)
      else if (editing) await api.patch(`/customers/${editing.id}`, body)
      toast(editing === 'new' ? 'Customer added' : 'Customer updated')
      setEditing(null)
      load()
    } catch (e) { setError(errorMessage(e)) } finally { setSaving(false) }
  }

  const salesName = (id: string | null) => salesUsers.find((u) => u.id === id)?.name ?? (id ? 'Assigned' : '—')

  return (
    <>
      <PageHeader
        title="Customers"
        subtitle="Buyers you trade with. Their WhatsApp number identifies them in every chat."
        actions={<>
          <button className="btn-secondary" onClick={() => download('/customers/export', 'customers.xlsx')}>Export</button>
          {manager && <button className="btn-secondary" onClick={() => setImportOpen(true)}>Import Excel</button>}
          {manager && <button className="btn-primary" onClick={() => open('new')}>Add customer</button>}
        </>}
      />

      <div className="card">
        <div className="flex flex-wrap gap-2 border-b border-slate-200 p-3">
          <input className="input max-w-xs" placeholder="Search name, phone or GSTIN" value={q}
            onChange={(e) => { setQ(e.target.value); setPage(1) }} />
          <select className="input max-w-[180px]" value={type} onChange={(e) => { setType(e.target.value); setPage(1) }}>
            <option value="">All types</option>
            {TYPES.map((t) => <option key={t} value={t}>{titleCase(t)}</option>)}
          </select>
        </div>
        <div className="overflow-x-auto">
          <table className="w-full min-w-[760px]">
            <thead className="bg-slate-50">
              <tr>
                <th className="th">Customer</th><th className="th">WhatsApp</th><th className="th">Type</th>
                <th className="th">Tags</th><th className="th">Credit</th>{manager && <th className="th">Sales</th>}
                <th className="th">Opt-in</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {data.items.map((c) => (
                <tr key={c.id} className={`hover:bg-slate-50 ${canEdit ? 'cursor-pointer' : ''}`} onClick={() => canEdit && open(c)}>
                  <td className="td">
                    <div className="font-medium text-slate-900">{c.business_name}</div>
                    <div className="text-xs text-slate-500">{[c.contact_person, c.city].filter(Boolean).join(' · ')}</div>
                  </td>
                  <td className="td font-mono text-xs">{c.whatsapp_number}</td>
                  <td className="td">{titleCase(c.customer_type)}{c.status !== 'active' && <span className="badge ml-1 bg-slate-100 text-slate-600">{c.status}</span>}</td>
                  <td className="td"><div className="flex flex-wrap gap-1">{c.tags.map((t) => <span key={t} className="badge bg-slate-100 text-slate-600">{t}</span>)}</div></td>
                  <td className="td">{inr(c.credit_limit)}<div className="text-xs text-slate-500">{c.credit_days} days</div></td>
                  {manager && <td className="td">{salesName(c.assigned_user_id)}</td>}
                  <td className="td">{c.opted_out ? <span className="badge bg-red-50 text-red-700">Opted out</span>
                    : c.whatsapp_opt_in ? <span className="badge bg-pine-50 text-pine-700">Yes</span>
                      : <span className="badge bg-slate-100 text-slate-500">No</span>}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {!loading && data.total === 0 && (
          <Empty title="No customers yet">{manager ? 'Add one, or import your list from Excel.' : 'Ask your admin to assign customers to you.'}</Empty>
        )}
        <Pagination page={page} size={SIZE} total={data.total} onPage={setPage} />
      </div>

      <Drawer open={editing !== null} title={editing === 'new' ? 'Add customer' : 'Edit customer'} onClose={() => setEditing(null)}
        footer={<>
          {editing && editing !== 'new' && manager && editing.status !== 'inactive' && (
            <button className="btn-danger mr-auto" onClick={async () => {
              await api.post(`/customers/${editing.id}/deactivate`); toast('Customer deactivated'); setEditing(null); load()
            }}>Deactivate</button>
          )}
          {editing && editing !== 'new' && (
            <button className="btn-secondary" onClick={() => navigate(`/orders?customer=${editing.id}`)}>Orders</button>
          )}
          {editing && editing !== 'new' && (
            <button className="btn-secondary" onClick={() => navigate(`/customers/${editing.id}/ledger`)}>Ledger</button>
          )}
          {editing && editing !== 'new' && (
            <button className="btn-secondary" onClick={async () => {
              try {
                const { data } = await api.post(`/inbox/customers/${editing.id}/conversation`)
                navigate(`/inbox?c=${data.id}`)
              } catch (e) { setError(errorMessage(e)) }
            }}>Open chat</button>
          )}
          <button className="btn-secondary" onClick={() => setEditing(null)}>Cancel</button>
          <button className="btn-primary" disabled={saving} onClick={save}>{saving ? 'Saving…' : 'Save'}</button>
        </>}>
        <ErrorBox message={error} />
        <div className="grid grid-cols-2 gap-3">
          <Field label="Business name *" className="col-span-2"><input className="input" value={String(form.business_name)} onChange={(e) => set('business_name', e.target.value)} /></Field>
          <Field label="WhatsApp number *" hint="10-digit mobile or with +country code"><input className="input" value={String(form.whatsapp_number)} onChange={(e) => set('whatsapp_number', e.target.value)} /></Field>
          <Field label="Contact person"><input className="input" value={String(form.contact_person)} onChange={(e) => set('contact_person', e.target.value)} /></Field>
          <Field label="Alternate phone"><input className="input" value={String(form.alternate_phone)} onChange={(e) => set('alternate_phone', e.target.value)} /></Field>
          <Field label="Email"><input className="input" value={String(form.email)} onChange={(e) => set('email', e.target.value)} /></Field>
          <Field label="GSTIN"><input className="input uppercase" value={String(form.gstin)} onChange={(e) => set('gstin', e.target.value)} /></Field>
          <Field label="Type">
            <select className="input" value={String(form.customer_type)} onChange={(e) => set('customer_type', e.target.value)}>
              {TYPES.map((t) => <option key={t} value={t}>{titleCase(t)}</option>)}
            </select>
          </Field>
          <Field label="Billing address" className="col-span-2"><textarea className="input" rows={2} value={String(form.billing_address)} onChange={(e) => set('billing_address', e.target.value)} /></Field>
          <Field label="Shipping address" className="col-span-2"><textarea className="input" rows={2} value={String(form.shipping_address)} onChange={(e) => set('shipping_address', e.target.value)} /></Field>
          <Field label="City"><input className="input" value={String(form.city)} onChange={(e) => set('city', e.target.value)} /></Field>
          <Field label="State"><input className="input" value={String(form.state)} onChange={(e) => set('state', e.target.value)} /></Field>
          <Field label="Pincode"><input className="input" value={String(form.pincode)} onChange={(e) => set('pincode', e.target.value)} /></Field>
          <Field label="Tags" hint="Comma separated"><input className="input" value={String(form.tags)} onChange={(e) => set('tags', e.target.value)} /></Field>
          {manager && <>
            <Field label="Price list">
              <select className="input" value={String(form.price_list_id)} onChange={(e) => set('price_list_id', e.target.value)}>
                <option value="">Base prices</option>
                {priceLists.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
              </select>
            </Field>
            <Field label="Sales user">
              <select className="input" value={String(form.assigned_user_id)} onChange={(e) => set('assigned_user_id', e.target.value)}>
                <option value="">Not assigned</option>
                {salesUsers.map((u) => <option key={u.id} value={u.id}>{u.name}</option>)}
              </select>
            </Field>
            <Field label="Credit limit (₹)"><input className="input" type="number" min={0} value={String(form.credit_limit)} onChange={(e) => set('credit_limit', e.target.value)} /></Field>
            <Field label="Credit days"><input className="input" type="number" min={0} max={365} value={String(form.credit_days)} onChange={(e) => set('credit_days', e.target.value)} /></Field>
            <Field label="Opening balance (₹)" hint="What they owe you today. Used for the credit check on orders." className="col-span-2">
              <input className="input" type="number" value={String(form.opening_balance)} onChange={(e) => set('opening_balance', e.target.value)} />
            </Field>
          </>}
          <label className="col-span-2 flex items-start gap-2 rounded-lg bg-slate-50 p-3 text-sm">
            <input type="checkbox" className="mt-0.5" checked={Boolean(form.whatsapp_opt_in)} onChange={(e) => set('whatsapp_opt_in', e.target.checked)} />
            <span>Customer has agreed to receive WhatsApp messages from us. Campaigns and reminders are only sent to customers who have opted in.</span>
          </label>
        </div>
      </Drawer>

      <ImportDialog open={importOpen} onClose={() => setImportOpen(false)} title="Import customers"
        uploadPath="/customers/import" templatePath="/customers/import/template" templateName="customer_import_template.xlsx" onDone={load} />
    </>
  )
}
