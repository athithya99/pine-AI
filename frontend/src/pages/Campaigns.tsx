import { useCallback, useEffect, useMemo, useState } from 'react'
import { Link, useNavigate, useParams, useSearchParams } from 'react-router-dom'
import { Drawer, Empty, ErrorBox, Field, PageHeader, Pagination, useToast } from '../components/ui'
import { api, errorMessage } from '../lib/api'
import {
  CAMPAIGN_STATUS, inr,
  type Campaign, type CampaignOptions, type CampaignRowT, type CampaignStatus, type CampaignTemplateOpt,
  type CampaignVariable, type CustomerType, type Page, type Product, type RecipientRowT, type Segment, type SegmentPreview,
} from '../lib/types'
import { Picker } from './OrderForm'
import { fmtDate } from './Orders'

const TYPES: [CustomerType, string][] = [['distributor', 'Distributor'], ['retailer', 'Retailer'], ['dealer', 'Dealer'], ['other', 'Other']]

export function CampaignBadge({ s }: { s: CampaignStatus }) {
  const [label, cls] = CAMPAIGN_STATUS[s]
  return <span className={`badge ${cls}`}>{label}</span>
}

// ======================================================================= list
const LIST_TABS: { key: CampaignStatus | 'all'; label: string }[] = [
  { key: 'all', label: 'All' }, { key: 'draft', label: 'Drafts' }, { key: 'scheduled', label: 'Scheduled' },
  { key: 'sending', label: 'Sending' }, { key: 'completed', label: 'Sent' },
]

export function Campaigns() {
  const [params, setParams] = useSearchParams()
  const tab = (params.get('status') ?? 'all') as CampaignStatus | 'all'
  const [page, setPage] = useState(1)
  const [data, setData] = useState<Page<CampaignRowT> | null>(null)

  useEffect(() => {
    api.get<Page<CampaignRowT>>('/campaigns', { params: { status: tab === 'all' ? undefined : tab, page, size: 25 } })
      .then((r) => setData(r.data)).catch(() => {})
  }, [tab, page])

  return (
    <>
      <PageHeader title="Campaigns" subtitle="WhatsApp broadcasts to a segment of buyers, and the orders they bring in."
        actions={<Link to="/campaigns/new" className="btn-primary">New campaign</Link>} />
      <div className="mb-3 flex gap-1 overflow-x-auto border-b border-slate-200">
        {LIST_TABS.map((t) => (
          <button key={t.key} onClick={() => { setParams(t.key === 'all' ? {} : { status: t.key }); setPage(1) }}
            className={`-mb-px whitespace-nowrap border-b-2 px-3 py-2 text-sm ${tab === t.key ? 'border-pine-600 font-medium text-pine-700' : 'border-transparent text-slate-500 hover:text-slate-800'}`}>
            {t.label}
          </button>
        ))}
      </div>
      <div className="card">
        {data && data.items.length === 0 ? (
          <Empty title={tab === 'all' ? 'No campaigns yet' : 'Nothing here'}>
            {tab === 'all' && <>Announce a launch, a scheme or a price change to the buyers who should hear about it. <Link to="/campaigns/new" className="text-pine-700 underline">Create one</Link>.</>}
          </Empty>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full min-w-[820px]">
              <thead className="bg-slate-50"><tr>
                <th className="th">Campaign</th><th className="th">Status</th><th className="th">When</th>
                <th className="th text-right">Buyers</th><th className="th text-right">Sent</th><th className="th text-right">Read</th>
                <th className="th text-right">Orders (7 days)</th></tr></thead>
              <tbody className="divide-y divide-slate-100">
                {data?.items.map((c) => (
                  <tr key={c.id} className="hover:bg-slate-50">
                    <td className="td"><Link to={`/campaigns/${c.id}`} className="font-medium text-slate-900 hover:underline">{c.name}</Link>
                      <div className="font-mono text-xs text-slate-500">{c.template_name ?? 'No template yet'}</div></td>
                    <td className="td"><CampaignBadge s={c.status} /></td>
                    <td className="td text-slate-600">{c.started_at ? fmtDate(c.started_at, true) : c.scheduled_at ? <>Scheduled {fmtDate(c.scheduled_at, true)}</> : <span className="text-slate-400">Created {fmtDate(c.created_at)}</span>}</td>
                    <td className="td text-right tabular-nums">{c.started_at ? c.targeted : '—'}</td>
                    <td className="td text-right tabular-nums">{c.started_at ? c.sent : '—'}</td>
                    <td className="td text-right tabular-nums">{c.started_at ? <>{c.read}{c.sent > 0 && <span className="ml-1 text-xs text-slate-400">{Math.round(100 * c.read / c.sent)}%</span>}</> : '—'}</td>
                    <td className="td text-right tabular-nums">{c.started_at ? <>{c.orders} <span className="text-xs text-slate-500">{inr(c.order_value)}</span></> : '—'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        {data && data.total > data.size && <Pagination page={page} size={data.size} total={data.total} onPage={setPage} />}
      </div>
    </>
  )
}

// ======================================================================= shared bits
function useDebounced<T>(value: T, ms = 400): T {
  const [v, setV] = useState(value)
  useEffect(() => { const t = setTimeout(() => setV(value), ms); return () => clearTimeout(t) }, [value, ms])
  return v
}

function Chips({ options, value, onChange, empty }: {
  options: [string, string][]; value: string[]; onChange: (v: string[]) => void; empty?: string
}) {
  if (options.length === 0) return <p className="text-xs text-slate-400">{empty ?? 'None yet'}</p>
  return (
    <div className="flex max-h-32 flex-wrap gap-1.5 overflow-y-auto">
      {options.map(([k, label]) => {
        const on = value.includes(k)
        return (
          <button key={k} type="button" onClick={() => onChange(on ? value.filter((x) => x !== k) : [...value, k])}
            className={`rounded-full border px-2.5 py-1 text-xs ${on ? 'border-pine-500 bg-pine-50 text-pine-800' : 'border-slate-200 text-slate-600 hover:bg-slate-50'}`}>
            {label}
          </button>
        )
      })}
    </div>
  )
}

/** Like Field, but a <div>: a <label> around several buttons would forward clicks to the first one. */
function Group({ label, children }: { label: string; children: React.ReactNode }) {
  return <div><span className="label">{label}</span>{children}</div>
}

function CampaignMedia({ id, mime }: { id: string; mime: string | null }) {
  const [url, setUrl] = useState<string | null>(null)
  useEffect(() => {
    let revoke: string | null = null
    api.get(`/campaigns/${id}/media`, { responseType: 'blob' }).then((r) => { revoke = URL.createObjectURL(r.data); setUrl(revoke) }).catch(() => {})
    return () => { if (revoke) URL.revokeObjectURL(revoke) }
  }, [id])
  if (!url) return <div className="h-32 animate-pulse rounded-lg bg-black/5" />
  if ((mime ?? '').startsWith('image/')) return <img src={url} alt="" className="max-h-48 w-full rounded-lg object-cover" />
  return <a href={url} target="_blank" rel="noreferrer" className="flex items-center gap-2 rounded-lg bg-black/5 p-3 text-xs">📄 Open PDF</a>
}

/** How the buyer will see it on WhatsApp. */
export function MessagePreview({ tpl, vars, fields, media, orderIndex }: {
  tpl: Pick<CampaignTemplateOpt, 'body' | 'header_format' | 'buttons'> & { header_text?: string | null; footer?: string | null } | null
  vars: CampaignVariable[]; fields: Record<string, string>; media?: React.ReactNode; orderIndex: number | null
}) {
  if (!tpl) return <p className="text-sm text-slate-400">Pick a template to see the message.</p>
  const body = tpl.body.replace(/\{\{(\d+)\}\}/g, (m, n) => {
    const v = vars[Number(n) - 1]
    if (!v || !v.value) return m
    return v.kind === 'field' ? `[${fields[v.value] ?? v.value}]` : v.value
  })
  return (
    <div className="rounded-xl bg-[#e7ddd3] p-3">
      <div className="max-w-[300px] rounded-lg rounded-tl-none bg-white p-2 text-sm shadow-sm">
        {(tpl.header_format === 'IMAGE' || tpl.header_format === 'DOCUMENT') && (
          <div className="mb-2">{media ?? <div className="grid h-28 place-items-center rounded-lg bg-slate-100 text-xs text-slate-400">{tpl.header_format === 'IMAGE' ? '🖼 Image' : '📄 PDF'} attached at send time</div>}</div>
        )}
        {tpl.header_text && <div className="mb-1 font-semibold">{tpl.header_text}</div>}
        <div className="whitespace-pre-wrap">{body}</div>
        {tpl.footer && <div className="mt-1 text-xs text-slate-400">{tpl.footer}</div>}
      </div>
      {(tpl.buttons ?? []).map((b, i) => (
        <div key={i} className="mt-1 max-w-[300px] rounded-lg bg-white py-1.5 text-center text-sm text-sky-600 shadow-sm">
          {b.text}{orderIndex === i && <span className="ml-1 rounded bg-pine-50 px-1 text-[10px] text-pine-700">starts an order</span>}
        </div>
      ))}
    </div>
  )
}

function AudienceBox({ p }: { p: SegmentPreview | null }) {
  if (!p) return <div className="h-24 animate-pulse rounded-lg bg-slate-50" />
  return (
    <div>
      <div className="text-3xl font-semibold tabular-nums">{p.eligible}</div>
      <div className="text-sm text-slate-500">buyer{p.eligible !== 1 ? 's' : ''} will get this message</div>
      <dl className="mt-3 space-y-1 text-xs text-slate-600">
        <div className="flex justify-between"><dt>Match the filters</dt><dd className="tabular-nums">{p.matched}</dd></div>
        <div className="flex justify-between"><dt>Excluded: opted out</dt><dd className="tabular-nums">{p.opted_out}</dd></div>
        <div className="flex justify-between"><dt>Excluded: no WhatsApp opt-in</dt><dd className="tabular-nums">{p.no_opt_in}</dd></div>
        <div className="flex justify-between border-t border-slate-100 pt-1 font-medium text-slate-800"><dt>Estimated Meta charge</dt><dd className="tabular-nums">{inr(p.estimated_cost)}</dd></div>
      </dl>
      <p className="mt-1 text-[11px] text-slate-400">{p.eligible} × ₹{Number(p.rate).toFixed(2)} per message (Company settings)</p>
      {p.eligible > p.capacity_now && (
        <p className="mt-2 rounded-lg bg-amber-50 p-2 text-xs text-amber-800">
          Your number can message {p.daily_limit} buyers per 24 hours ({p.capacity_now} left now). The rest are sent automatically as the limit frees up.
        </p>
      )}
      {p.sample.length > 0 && <p className="mt-2 text-xs text-slate-500">e.g. {p.sample.join(', ')}{p.eligible > p.sample.length ? '…' : ''}</p>}
    </div>
  )
}

// ======================================================================= editor
const EMPTY: { name: string; template_id: string | null; variables: CampaignVariable[]; segment: Segment; order_button_index: number | null; products: { id: string; sku: string; name: string }[] } = {
  name: '', template_id: null, variables: [], segment: {}, order_button_index: null, products: [],
}

export function CampaignForm() {
  const { id } = useParams()
  const nav = useNavigate()
  const toast = useToast()
  const [opts, setOpts] = useState<CampaignOptions | null>(null)
  const [form, setForm] = useState(EMPTY)
  const [existing, setExisting] = useState<Campaign | null>(null)
  const [file, setFile] = useState<File | null>(null)
  const [preview, setPreview] = useState<SegmentPreview | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [lastOrder, setLastOrder] = useState<'any' | 'none' | 'within'>('any')

  useEffect(() => {
    api.get<CampaignOptions>('/campaigns/options').then((r) => setOpts(r.data))
    if (id) api.get<Campaign>(`/campaigns/${id}`).then((r) => {
      const c = r.data
      if (c.status !== 'draft') { nav(`/campaigns/${c.id}`, { replace: true }); return }
      setExisting(c)
      setForm({ name: c.name, template_id: c.template_id, variables: c.variables, segment: c.segment, order_button_index: c.order_button_index, products: c.products })
      setLastOrder(c.segment.no_order_days ? 'none' : c.segment.ordered_within_days ? 'within' : 'any')
    })
  }, [id, nav])

  const seg = form.segment
  const setSeg = (patch: Partial<Segment>) => setForm((f) => ({ ...f, segment: { ...f.segment, ...patch } }))
  const debounced = useDebounced(seg)
  useEffect(() => {
    api.post<SegmentPreview>('/campaigns/preview', debounced).then((r) => setPreview(r.data)).catch(() => setPreview(null))
  }, [debounced])

  const tpl = opts?.templates.find((t) => t.id === form.template_id) ?? null
  const fieldLabels = useMemo(() => Object.fromEntries((opts?.fields ?? []).map((f) => [f.key, f.label])), [opts])
  const needsMedia = tpl?.header_format === 'IMAGE' || tpl?.header_format === 'DOCUMENT'

  const pickTemplate = (tid: string) => {
    const t = opts?.templates.find((x) => x.id === tid)
    const idx = t ? t.buttons.findIndex((b) => /order|buy|book/i.test(b.text)) : -1
    setForm((f) => ({
      ...f, template_id: tid || null, order_button_index: idx >= 0 ? idx : null,
      variables: Array.from({ length: t?.variable_count ?? 0 }, (_, i): CampaignVariable =>
        f.variables[i] ?? (i === 0 ? { kind: 'field', value: 'first_name' } : { kind: 'fixed', value: '' })),
    }))
  }
  const setVar = (i: number, v: Partial<CampaignVariable>) =>
    setForm((f) => ({ ...f, variables: f.variables.map((x, j) => (j === i ? { ...x, ...v } : x)) }))
  const searchProducts = useMemo(() => async (q: string) =>
    (await api.get<Page<Product>>('/products', { params: { q: q || undefined, size: 12 } })).data.items, [])

  const save = async () => {
    setBusy(true); setError(null)
    const body = {
      name: form.name, template_id: form.template_id, variables: form.variables, order_button_index: form.order_button_index,
      product_ids: form.products.map((p) => p.id),
      segment: {
        ...seg, no_order_days: lastOrder === 'none' ? seg.no_order_days || 30 : null,
        ordered_within_days: lastOrder === 'within' ? seg.ordered_within_days || 30 : null,
        min_outstanding: seg.min_outstanding || null, max_outstanding: seg.max_outstanding || null,
      },
    }
    try {
      const { data } = id ? await api.put<Campaign>(`/campaigns/${id}`, body) : await api.post<Campaign>('/campaigns', body)
      if (file && needsMedia) {
        const fd = new FormData(); fd.append('file', file)
        try { await api.post(`/campaigns/${data.id}/media`, fd) } catch (e) { toast(errorMessage(e), 'error') }
      }
      toast('Campaign saved'); nav(`/campaigns/${data.id}`)
    } catch (e) { setError(errorMessage(e)) } finally { setBusy(false) }
  }

  if (!opts) return <div className="h-40 animate-pulse rounded-xl bg-slate-100" />
  const approved = opts.templates.filter((t) => t.status === 'APPROVED')
  const pendingTpls = opts.templates.filter((t) => t.status !== 'APPROVED')

  return (
    <>
      <PageHeader title={id ? 'Edit campaign' : 'New campaign'} subtitle="Pick the message, choose who gets it, then test before sending."
        actions={<><Link to={id ? `/campaigns/${id}` : '/campaigns'} className="btn-secondary">Cancel</Link>
          <button className="btn-primary" disabled={busy || form.name.trim().length < 2} onClick={save}>Save draft</button></>} />
      <ErrorBox message={error} />
      <div className="grid gap-6 lg:grid-cols-[1fr_340px]">
        <div className="space-y-6">
          <div className="card space-y-4 p-5">
            <Field label="Campaign name" hint="Only your team sees this"><input className="input" value={form.name} placeholder="e.g. Diwali scheme – Coimbatore retailers" onChange={(e) => setForm((f) => ({ ...f, name: e.target.value }))} /></Field>
            <Field label="Message template" hint="Campaigns use approved MARKETING templates. Create them under Settings → WhatsApp.">
              <select className="input" value={form.template_id ?? ''} onChange={(e) => pickTemplate(e.target.value)}>
                <option value="">Select a template…</option>
                {approved.map((t) => <option key={t.id} value={t.id}>{t.name} ({t.language})</option>)}
                {pendingTpls.length > 0 && <optgroup label="Waiting for WhatsApp approval">
                  {pendingTpls.map((t) => <option key={t.id} value={t.id} disabled>{t.name} – {t.status.toLowerCase()}</option>)}
                </optgroup>}
              </select>
            </Field>
            {approved.length === 0 && <p className="rounded-lg bg-amber-50 p-3 text-xs text-amber-800">No approved marketing template yet. Add one in <Link to="/whatsapp" className="underline">Settings → WhatsApp</Link> with an "Order now" quick-reply button; Meta usually approves within a few hours.</p>}

            {tpl && tpl.variable_count > 0 && (
              <div>
                <span className="label">Fill the template</span>
                <div className="space-y-2">
                  {form.variables.map((v, i) => (
                    <div key={i} className="grid grid-cols-[40px_170px_1fr] items-center gap-2">
                      <span className="font-mono text-xs text-slate-500">{`{{${i + 1}}}`}</span>
                      <select className="input" value={v.kind} onChange={(e) => setVar(i, { kind: e.target.value as CampaignVariable['kind'], value: e.target.value === 'field' ? 'first_name' : '' })}>
                        <option value="field">Customer field</option><option value="fixed">Same text for all</option>
                      </select>
                      {v.kind === 'field'
                        ? <select className="input" value={v.value} onChange={(e) => setVar(i, { value: e.target.value })}>{opts.fields.map((f) => <option key={f.key} value={f.key}>{f.label}</option>)}</select>
                        : <input className="input" value={v.value} placeholder="e.g. 10% off on Gold Atta till Sunday" onChange={(e) => setVar(i, { value: e.target.value })} />}
                    </div>
                  ))}
                </div>
              </div>
            )}

            {needsMedia && (
              <Field label={tpl?.header_format === 'IMAGE' ? 'Image (JPG or PNG, up to 5 MB)' : 'PDF (up to 16 MB)'}
                hint={existing?.has_media && !file ? `Attached: ${existing.media_filename}. Choose a file to replace it.` : 'Sent as the message header, e.g. a product photo or scheme leaflet'}>
                <input type="file" className="input" accept={tpl?.header_format === 'IMAGE' ? 'image/jpeg,image/png' : 'application/pdf'}
                  onChange={(e) => setFile(e.target.files?.[0] ?? null)} />
              </Field>
            )}

            {tpl && tpl.buttons.length > 0 && (
              <Field label='"Order now" button' hint="When a buyer taps it, the bot replies with the products below and their prices, and takes the order like any WhatsApp order.">
                <select className="input" value={form.order_button_index ?? ''} onChange={(e) => setForm((f) => ({ ...f, order_button_index: e.target.value === '' ? null : Number(e.target.value) }))}>
                  <option value="">No order button</option>
                  {tpl.buttons.map((b, i) => <option key={i} value={i}>{b.text}</option>)}
                </select>
              </Field>
            )}
            {form.order_button_index !== null && (
              <div>
                <span className="label">Products to suggest (up to 10)</span>
                <div className="mb-2 flex flex-wrap gap-1.5">
                  {form.products.map((p) => (
                    <span key={p.id} className="inline-flex items-center gap-1 rounded-full bg-slate-100 px-2.5 py-1 text-xs">
                      <span className="font-mono text-slate-500">{p.sku}</span> {p.name}
                      <button type="button" className="text-slate-400 hover:text-red-600" onClick={() => setForm((f) => ({ ...f, products: f.products.filter((x) => x.id !== p.id) }))}>✕</button>
                    </span>
                  ))}
                </div>
                {form.products.length < 10 && <Picker<Product> placeholder="Add a product from this offer" search={searchProducts}
                  onPick={(p) => setForm((f) => (f.products.some((x) => x.id === p.id) ? f : { ...f, products: [...f.products, { id: p.id, sku: p.sku, name: p.name }] }))}
                  render={(p) => <span><span className="font-mono text-xs text-slate-500">{p.sku}</span> {p.name}</span>} />}
              </div>
            )}
          </div>

          <div className="card space-y-4 p-5">
            <div><h2 className="font-medium">Who gets it</h2><p className="text-xs text-slate-500">Leave a filter empty to include everyone. Only active customers who opted in to WhatsApp receive campaigns.</p></div>
            <Group label="Customer type"><Chips options={TYPES} value={seg.customer_types ?? []} onChange={(v) => setSeg({ customer_types: v as CustomerType[] })} /></Group>
            <Group label="Tags (any of)"><Chips options={opts.tags.map((t) => [t, t])} value={seg.tags ?? []} onChange={(v) => setSeg({ tags: v })} empty="No customer tags yet" /></Group>
            <div className="grid gap-4 sm:grid-cols-2">
              <Group label="City"><Chips options={opts.cities.map((c) => [c, c])} value={seg.cities ?? []} onChange={(v) => setSeg({ cities: v })} /></Group>
              <Group label="State"><Chips options={opts.states.map((c) => [c, c])} value={seg.states ?? []} onChange={(v) => setSeg({ states: v })} /></Group>
            </div>
            <Group label="Sales person"><Chips options={opts.sales_users.map((u) => [u.id, u.name])} value={seg.assigned_user_ids ?? []} onChange={(v) => setSeg({ assigned_user_ids: v })} empty="No sales users" /></Group>
            <div className="grid gap-4 sm:grid-cols-2">
              <Group label="Last order">
                <div className="flex gap-2">
                  <select className="input" value={lastOrder} onChange={(e) => {
                    const v = e.target.value as typeof lastOrder; setLastOrder(v)
                    setSeg({ no_order_days: v === 'none' ? seg.no_order_days || 30 : null, ordered_within_days: v === 'within' ? seg.ordered_within_days || 30 : null })
                  }}>
                    <option value="any">Any time</option><option value="none">No order in last…</option><option value="within">Ordered in last…</option>
                  </select>
                  {lastOrder !== 'any' && <input className="input w-24" type="number" min={1}
                    value={(lastOrder === 'none' ? seg.no_order_days : seg.ordered_within_days) ?? 30}
                    onChange={(e) => setSeg(lastOrder === 'none' ? { no_order_days: Number(e.target.value) || null } : { ordered_within_days: Number(e.target.value) || null })} />}
                  {lastOrder !== 'any' && <span className="self-center text-sm text-slate-500">days</span>}
                </div>
              </Group>
              <Group label="Outstanding amount (₹)">
                <div className="flex items-center gap-2">
                  <input className="input" type="number" placeholder="Min" value={seg.min_outstanding ?? ''} onChange={(e) => setSeg({ min_outstanding: e.target.value || null })} />
                  <span className="text-slate-400">–</span>
                  <input className="input" type="number" placeholder="Max" value={seg.max_outstanding ?? ''} onChange={(e) => setSeg({ max_outstanding: e.target.value || null })} />
                </div>
              </Group>
            </div>
          </div>
        </div>

        <div className="space-y-6 lg:sticky lg:top-4 lg:h-fit">
          <div className="card p-5"><h2 className="mb-3 font-medium">Audience</h2><AudienceBox p={preview} /></div>
          <div className="card p-5"><h2 className="mb-3 font-medium">Preview</h2>
            <MessagePreview tpl={tpl} vars={form.variables} fields={fieldLabels} orderIndex={form.order_button_index}
              media={file && file.type.startsWith('image/') ? <img src={URL.createObjectURL(file)} alt="" className="max-h-48 w-full rounded-lg object-cover" />
                : existing?.has_media ? <CampaignMedia id={existing.id} mime={existing.media_mime} /> : undefined} />
            <p className="mt-2 text-[11px] text-slate-400">[Fields] are filled in for each buyer.</p>
          </div>
        </div>
      </div>
    </>
  )
}

// ======================================================================= detail / report
const VIEWS = [
  ['all', 'All'], ['read', 'Read'], ['replied', 'Replied'], ['tapped', 'Tapped "Order now"'], ['ordered', 'Ordered'],
  ['failed', 'Failed'], ['skipped', 'Excluded'],
] as const
const DELIVERY: Record<string, [string, string]> = {
  pending: ['Waiting', 'bg-slate-100 text-slate-600'], queued: ['Sending', 'bg-slate-100 text-slate-600'],
  sent: ['Sent', 'bg-sky-50 text-sky-700'], delivered: ['Delivered', 'bg-sky-50 text-sky-700'],
  read: ['Read', 'bg-emerald-50 text-emerald-700'], failed: ['Failed', 'bg-red-50 text-red-600'],
  skipped: ['Excluded', 'bg-slate-100 text-slate-500'],
}
const pct = (a: number, b: number) => (b > 0 ? `${Math.round((100 * a) / b)}%` : '')

function SendDrawer({ c, open, onClose, onDone }: { c: Campaign; open: boolean; onClose: () => void; onDone: (c: Campaign) => void }) {
  const [p, setP] = useState<SegmentPreview | null>(null)
  const [when, setWhen] = useState<'now' | 'later'>('now')
  const [at, setAt] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  useEffect(() => {
    if (!open) return
    setError(null); setP(null)
    api.post<SegmentPreview>('/campaigns/preview', c.segment).then((r) => setP(r.data))
  }, [open, c.segment])
  const go = async () => {
    setBusy(true); setError(null)
    try {
      const { data } = when === 'now'
        ? await api.post<Campaign>(`/campaigns/${c.id}/send`, { confirmed_count: p?.eligible ?? 0 })
        : await api.post<Campaign>(`/campaigns/${c.id}/schedule`, { scheduled_at: `${at}:00+05:30` })
      onDone(data)
    } catch (e) {
      setError(errorMessage(e))
      api.post<SegmentPreview>('/campaigns/preview', c.segment).then((r) => setP(r.data))
    } finally { setBusy(false) }
  }
  return (
    <Drawer open={open} title="Send campaign" onClose={onClose}
      footer={<><button className="btn-secondary" onClick={onClose}>Back</button>
        <button className="btn-primary" disabled={busy || !p || p.eligible === 0 || (when === 'later' && !at)} onClick={go}>
          {when === 'now' ? `Send to ${p?.eligible ?? '…'} buyers` : 'Schedule'}</button></>}>
      <ErrorBox message={error} />
      <div className="card mb-4 p-4"><AudienceBox p={p} /></div>
      <div className="space-y-2">
        <label className="flex items-center gap-2 text-sm"><input type="radio" checked={when === 'now'} onChange={() => setWhen('now')} /> Send now</label>
        <label className="flex items-center gap-2 text-sm"><input type="radio" checked={when === 'later'} onChange={() => setWhen('later')} /> Schedule for later</label>
        {when === 'later' && <Field label="Date and time (IST)" hint="The buyer list is taken at that time, so new opt-ins are included and opt-outs excluded.">
          <input type="datetime-local" className="input" value={at} onChange={(e) => setAt(e.target.value)} /></Field>}
      </div>
      <p className="mt-4 text-xs text-slate-500">Meta charges per message delivered. It may decline marketing messages to buyers who've had many recently; those show as failed with Meta's reason and are not retried.</p>
    </Drawer>
  )
}

export function CampaignDetail() {
  const { id } = useParams()
  const nav = useNavigate()
  const toast = useToast()
  const [c, setC] = useState<Campaign | null>(null)
  const [opts, setOpts] = useState<CampaignOptions | null>(null)
  const [view, setView] = useState<string>('all')
  const [page, setPage] = useState(1)
  const [rows, setRows] = useState<Page<RecipientRowT> | null>(null)
  const [testTo, setTestTo] = useState('')
  const [sendOpen, setSendOpen] = useState(false)
  const [preview, setPreview] = useState<SegmentPreview | null>(null)

  const load = useCallback(() => api.get<Campaign>(`/campaigns/${id}`).then((r) => setC(r.data)).catch(() => {}), [id])
  useEffect(() => { load(); api.get<CampaignOptions>('/campaigns/options').then((r) => setOpts(r.data)) }, [load])
  useEffect(() => {
    if (c?.status !== 'sending') return
    const t = setInterval(load, 10000)
    return () => clearInterval(t)
  }, [c?.status, load])
  const started = !!c?.stats
  useEffect(() => {
    if (!started) return
    api.get<Page<RecipientRowT>>(`/campaigns/${id}/recipients`, { params: { view, page, size: 50 } }).then((r) => setRows(r.data))
  }, [id, view, page, started, c?.stats?.sent, c?.stats?.read])
  useEffect(() => {
    if (c && !started && c.status !== 'cancelled') api.post<SegmentPreview>('/campaigns/preview', c.segment).then((r) => setPreview(r.data))
  }, [c, started])

  const act = async (path: string, msg: string) => {
    try { const { data } = await api.post<Campaign>(`/campaigns/${id}/${path}`); setC(data); toast(msg) }
    catch (e) { toast(errorMessage(e), 'error') }
  }
  const test = async () => {
    try { const { data } = await api.post(`/campaigns/${id}/test`, { whatsapp_number: testTo }); toast(data.error ? `${data.message} (${data.error})` : data.message); load() }
    catch (e) { toast(errorMessage(e), 'error') }
  }
  const remove = async () => {
    if (!window.confirm('Delete this draft?')) return
    try { await api.delete(`/campaigns/${id}`); nav('/campaigns') } catch (e) { toast(errorMessage(e), 'error') }
  }
  const duplicate = async () => {
    try { const { data } = await api.post<Campaign>(`/campaigns/${id}/duplicate`); nav(`/campaigns/${data.id}/edit`) }
    catch (e) { toast(errorMessage(e), 'error') }
  }

  if (!c) return <div className="h-40 animate-pulse rounded-xl bg-slate-100" />
  const fieldLabels = Object.fromEntries((opts?.fields ?? []).map((f) => [f.key, f.label]))
  const tpl = c.template_body !== null ? { body: c.template_body, header_format: c.template_header_format, buttons: (c.template_buttons ?? []) as { type: string; text: string }[],
    header_text: opts?.templates.find((t) => t.id === c.template_id)?.header_text, footer: opts?.templates.find((t) => t.id === c.template_id)?.footer } : null
  const s = c.stats

  const tiles = s ? [
    { label: 'Buyers', value: s.targeted, sub: `${s.excluded} excluded` },
    { label: 'Sent', value: s.sent, sub: s.waiting ? `${s.waiting} waiting` : pct(s.sent, s.targeted - s.excluded) + ' of eligible' },
    { label: 'Delivered', value: s.delivered, sub: pct(s.delivered, s.sent) },
    { label: 'Read', value: s.read, sub: pct(s.read, s.sent) },
    { label: 'Failed', value: s.failed, warn: s.failed > 0 },
    { label: 'Replied', value: s.replied, sub: pct(s.replied, s.sent) },
    { label: '"Order now" taps', value: s.order_taps },
    { label: `Orders in ${s.attribution_days} days`, value: s.orders, sub: `${inr(s.order_value)} · ${s.ordering_buyers} buyer${s.ordering_buyers !== 1 ? 's' : ''}`, hi: true },
  ] : []

  return (
    <>
      <PageHeader title={c.name}
        subtitle={c.started_at ? `Sent ${fmtDate(c.started_at, true)}${c.sent_by_name ? ` by ${c.sent_by_name}` : ''}` : c.scheduled_at ? `Scheduled for ${fmtDate(c.scheduled_at, true)}` : `Draft created ${fmtDate(c.created_at)}${c.created_by_name ? ` by ${c.created_by_name}` : ''}`}
        actions={<>
          <CampaignBadge s={c.status} />
          {c.status === 'draft' && <>
            <button className="btn-danger" onClick={remove}>Delete</button>
            <Link to={`/campaigns/${c.id}/edit`} className="btn-secondary">Edit</Link>
            <button className="btn-primary" onClick={() => setSendOpen(true)}>Send…</button></>}
          {c.status === 'scheduled' && <><button className="btn-secondary" onClick={() => act('unschedule', 'Moved back to draft')}>Unschedule</button>
            <button className="btn-danger" onClick={() => act('cancel', 'Campaign cancelled')}>Cancel</button></>}
          {c.status === 'sending' && <button className="btn-danger" onClick={() => window.confirm('Stop sending? Buyers not yet messaged will be skipped.') && act('cancel', 'Campaign stopped')}>Stop sending</button>}
          {c.status !== 'draft' && <button className="btn-secondary" onClick={duplicate}>Duplicate</button>}
        </>} />

      {c.waiting_note && <div className="mb-4 rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-800">{c.waiting_note}</div>}

      {s && (
        <div className="mb-6 grid grid-cols-2 gap-3 sm:grid-cols-4">
          {tiles.map((t) => (
            <div key={t.label} className={`card p-4 ${t.hi ? 'border-pine-200 bg-pine-50/40' : ''}`}>
              <div className="text-xs text-slate-500">{t.label}</div>
              <div className={`mt-1 text-2xl font-semibold tabular-nums ${t.warn ? 'text-red-600' : 'text-slate-900'}`}>{t.value}</div>
              {t.sub && <div className="mt-0.5 text-xs text-slate-500">{t.sub}</div>}
            </div>
          ))}
        </div>
      )}

      <div className="grid gap-6 lg:grid-cols-[1fr_340px]">
        <div className="space-y-6">
          {s ? (
            <div className="card">
              <div className="flex gap-1 overflow-x-auto border-b border-slate-200 px-2">
                {VIEWS.map(([k, label]) => (
                  <button key={k} onClick={() => { setView(k); setPage(1) }}
                    className={`-mb-px whitespace-nowrap border-b-2 px-3 py-2 text-sm ${view === k ? 'border-pine-600 font-medium text-pine-700' : 'border-transparent text-slate-500 hover:text-slate-800'}`}>{label}</button>
                ))}
              </div>
              {rows && rows.items.length === 0 ? <Empty title="No buyers in this view" /> : (
                <div className="overflow-x-auto">
                  <table className="w-full min-w-[640px]">
                    <thead className="bg-slate-50"><tr><th className="th">Buyer</th><th className="th">Status</th><th className="th">Response</th><th className="th text-right">Orders</th></tr></thead>
                    <tbody className="divide-y divide-slate-100">
                      {rows?.items.map((r) => {
                        const [label, cls] = DELIVERY[r.delivery] ?? [r.delivery, 'bg-slate-100 text-slate-600']
                        return (
                          <tr key={r.id}>
                            <td className="td"><Link to={`/customers/${r.customer_id}/ledger`} className="font-medium hover:underline">{r.customer_name}</Link>
                              <div className="font-mono text-xs text-slate-500">{r.whatsapp_number}</div></td>
                            <td className="td"><span className={`badge ${cls}`}>{label}</span>{r.reason && <div className="mt-0.5 max-w-xs text-xs text-slate-500">{r.reason}</div>}</td>
                            <td className="td text-xs text-slate-600">{r.order_tapped ? 'Tapped "Order now"' : r.replied ? 'Replied' : '—'}</td>
                            <td className="td text-right tabular-nums">{r.orders ? <>{r.orders} <span className="text-xs text-slate-500">{inr(r.order_value)}</span></> : '—'}</td>
                          </tr>
                        )
                      })}
                    </tbody>
                  </table>
                </div>
              )}
              {rows && rows.total > rows.size && <Pagination page={page} size={rows.size} total={rows.total} onPage={setPage} />}
            </div>
          ) : c.status !== 'cancelled' && (
            <div className="card p-5">
              <h2 className="font-medium">Test before sending</h2>
              <p className="mt-0.5 text-xs text-slate-500">Sends this exact message to one number. The number must be saved as a customer with WhatsApp opt-in (e.g. your own, tagged "test").</p>
              <div className="mt-3 flex gap-2">
                <input className="input max-w-xs" placeholder="WhatsApp number" value={testTo} onChange={(e) => setTestTo(e.target.value)} />
                <button className="btn-secondary" disabled={testTo.trim().length < 6} onClick={test}>Send test</button>
              </div>
              {c.last_test_at && <p className="mt-2 text-xs text-emerald-700">Last test sent to {c.last_test_to} on {fmtDate(c.last_test_at, true)}</p>}
            </div>
          )}
          {s && (s.failed_reasons.length > 0 || s.excluded_reasons.length > 0) && (
            <div className="grid gap-6 sm:grid-cols-2">
              {s.failed_reasons.length > 0 && <div className="card p-5"><h2 className="mb-2 font-medium">Why messages failed</h2>
                <ul className="space-y-1 text-sm">{s.failed_reasons.map((r) => <li key={r.reason} className="flex justify-between gap-3"><span className="text-slate-600">{r.reason}</span><span className="tabular-nums">{r.count}</span></li>)}</ul>
                <p className="mt-2 text-xs text-slate-400">Not retried automatically.</p></div>}
              {s.excluded_reasons.length > 0 && <div className="card p-5"><h2 className="mb-2 font-medium">Excluded</h2>
                <ul className="space-y-1 text-sm">{s.excluded_reasons.map((r) => <li key={r.reason} className="flex justify-between gap-3"><span className="text-slate-600">{r.reason}</span><span className="tabular-nums">{r.count}</span></li>)}</ul></div>}
            </div>
          )}
        </div>

        <div className="space-y-6">
          {!s && c.status !== 'cancelled' && <div className="card p-5"><h2 className="mb-3 font-medium">Audience right now</h2><AudienceBox p={preview} /></div>}
          <div className="card p-5">
            <h2 className="mb-3 font-medium">Message</h2>
            <MessagePreview tpl={tpl} vars={c.variables} fields={fieldLabels} orderIndex={c.order_button_index}
              media={c.has_media ? <CampaignMedia id={c.id} mime={c.media_mime} /> : undefined} />
            <dl className="mt-3 space-y-1 text-xs text-slate-600">
              <div className="flex justify-between gap-2"><dt>Template</dt><dd className="font-mono">{c.template_name ?? '—'}</dd></div>
              {c.products.length > 0 && <div><dt className="mb-0.5">Suggested on "Order now"</dt><dd className="text-slate-800">{c.products.map((p) => p.name).join(', ')}</dd></div>}
            </dl>
          </div>
        </div>
      </div>

      <SendDrawer c={c} open={sendOpen} onClose={() => setSendOpen(false)} onDone={(d) => { setSendOpen(false); setC(d); toast(d.status === 'scheduled' ? 'Campaign scheduled' : d.status === 'completed' ? 'Campaign sent' : 'Campaign is sending') }} />
    </>
  )
}
