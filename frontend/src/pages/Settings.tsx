import { useEffect, useState } from 'react'
import { ErrorBox, Field, PageHeader, useToast } from '../components/ui'
import { api, errorMessage } from '../lib/api'
import { useAuth } from '../lib/auth'
import { isManager, type AiStatus, type GstRate, type Tenant } from '../lib/types'

const MONTHS = ['January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September', 'October', 'November', 'December']

export function Settings() {
  const { me, reload } = useAuth()
  const manager = isManager(me?.role)
  const toast = useToast()
  const [form, setForm] = useState<Partial<Tenant>>({})
  const [rates, setRates] = useState<GstRate[]>([])
  const [ai, setAi] = useState<AiStatus | null>(null)
  const [newRate, setNewRate] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  const loadRates = () => api.get<GstRate[]>('/gst-rates').then((r) => setRates(r.data)).catch(() => {})
  useEffect(() => {
    api.get<Tenant>('/company').then((r) => setForm(r.data)); loadRates()
    api.get<AiStatus>('/ai/status').then((r) => setAi(r.data)).catch(() => {})
  }, [])

  const set = (k: keyof Tenant, v: string | number) => setForm((f) => ({ ...f, [k]: v }))

  const save = async () => {
    setBusy(true); setError(null)
    const n = (v: unknown) => (v === '' || v === undefined ? null : v)
    try {
      await api.patch('/company', {
        name: form.name, gstin: n(form.gstin), billing_address: n(form.billing_address), state: n(form.state),
        contact_email: n(form.contact_email), contact_phone: n(form.contact_phone), logo_url: n(form.logo_url),
        fy_start_month: form.fy_start_month, order_prefix: form.order_prefix || null,
        quote_validity_hours: form.quote_validity_hours ? Number(form.quote_validity_hours) : null,
        ai_ordering_enabled: form.ai_ordering_enabled, ai_match_threshold: form.ai_match_threshold || null,
      })
      toast('Company details saved'); reload()
    } catch (e) { setError(errorMessage(e)) } finally { setBusy(false) }
  }

  const addRate = async () => {
    try { await api.post('/gst-rates', { rate: newRate }); setNewRate(''); loadRates() }
    catch (e) { toast(errorMessage(e), 'error') }
  }
  const disableRate = async (r: GstRate) => {
    try { await api.post(`/gst-rates/${r.id}/deactivate`); loadRates() }
    catch (e) { toast(errorMessage(e), 'error') }
  }

  const v = (k: keyof Tenant) => String(form[k] ?? '')

  return (
    <>
      <PageHeader title="Company" subtitle="These details appear on invoices and messages sent to your buyers." />
      <div className="grid gap-6 lg:grid-cols-[1fr_320px]">
        <div className="card p-5">
          <ErrorBox message={error} />
          <fieldset disabled={!manager} className="grid grid-cols-2 gap-3">
            <Field label="Company name" className="col-span-2"><input className="input" value={v('name')} onChange={(e) => set('name', e.target.value)} /></Field>
            <Field label="GSTIN"><input className="input uppercase" value={v('gstin')} onChange={(e) => set('gstin', e.target.value)} /></Field>
            <Field label="State"><input className="input" value={v('state')} onChange={(e) => set('state', e.target.value)} /></Field>
            <Field label="Billing address" className="col-span-2"><textarea className="input" rows={2} value={v('billing_address')} onChange={(e) => set('billing_address', e.target.value)} /></Field>
            <Field label="Contact email"><input className="input" value={v('contact_email')} onChange={(e) => set('contact_email', e.target.value)} /></Field>
            <Field label="Contact phone"><input className="input" value={v('contact_phone')} onChange={(e) => set('contact_phone', e.target.value)} /></Field>
            <Field label="Logo URL" className="col-span-2"><input className="input" value={v('logo_url')} onChange={(e) => set('logo_url', e.target.value)} /></Field>
            <Field label="Financial year starts in">
              <select className="input" value={form.fy_start_month ?? 4} onChange={(e) => set('fy_start_month', Number(e.target.value))}>
                {MONTHS.map((m, i) => <option key={m} value={i + 1}>{m}</option>)}
              </select>
            </Field>
            <Field label="Time zone"><input className="input" value={v('timezone')} disabled /></Field>
            <Field label="Order number prefix" hint={`Orders are numbered ${(form.order_prefix || 'ORD').toUpperCase()}/26-27/00001 and restart each financial year`}>
              <input className="input uppercase" maxLength={10} value={v('order_prefix')} onChange={(e) => set('order_prefix', e.target.value)} />
            </Field>
            <Field label="Quote valid for (hours)" hint="After this, an unconfirmed quote expires and the buyer is told">
              <input className="input" type="number" min={1} max={168} value={v('quote_validity_hours')} onChange={(e) => set('quote_validity_hours', Number(e.target.value))} />
            </Field>
          </fieldset>

          <fieldset disabled={!manager} className="mt-5 border-t border-slate-200 pt-4">
            <h2 className="font-medium">AI ordering on WhatsApp</h2>
            <p className="mt-0.5 text-xs text-slate-500">
              {ai ? (ai.is_mock ? 'Running on the development AI: typed orders only. Real AI needs the API key in the server settings.'
                : `AI: ${ai.provider} (${ai.model_fast}, photos with ${ai.model_smart}${ai.voice_notes ? ', voice notes on' : ''})${ai.configured ? '' : ' — API key missing'}`) : ''}
            </p>
            <label className="mt-3 flex items-start gap-2 text-sm">
              <input type="checkbox" className="mt-0.5" checked={Boolean(form.ai_ordering_enabled)}
                onChange={(e) => setForm((f) => ({ ...f, ai_ordering_enabled: e.target.checked }))} />
              <span>Read buyers' WhatsApp messages with AI and reply with a quote. Staff can still switch the bot off for any chat in the Inbox.</span>
            </label>
            <Field label="Match confidence needed" className="mt-3 max-w-xs"
              hint="Items the AI is less sure about are asked back to the buyer instead of being added. Default 0.80.">
              <input className="input" type="number" min={0.5} max={0.99} step={0.01} value={v('ai_match_threshold')}
                onChange={(e) => set('ai_match_threshold', e.target.value)} />
            </Field>
          </fieldset>
          {manager && <div className="mt-4 flex justify-end"><button className="btn-primary" disabled={busy} onClick={save}>Save</button></div>}
        </div>

        <div className="card h-fit p-5">
          <h2 className="font-medium">GST rates</h2>
          <p className="mt-1 text-xs text-slate-500">Products can only use rates listed here. Defaults follow the slabs in force since 22 Sept 2025.</p>
          <ul className="mt-3 divide-y divide-slate-100">
            {rates.map((r) => (
              <li key={r.id} className="flex items-center justify-between py-2 text-sm">
                <span className={r.is_active ? '' : 'text-slate-400 line-through'}>{r.label}</span>
                {manager && r.is_active && <button className="text-xs text-slate-500 hover:text-red-600" onClick={() => disableRate(r)}>Disable</button>}
              </li>
            ))}
          </ul>
          {manager && (
            <div className="mt-3 flex gap-2">
              <input className="input" type="number" min={0} max={100} step="0.01" placeholder="Rate %" value={newRate} onChange={(e) => setNewRate(e.target.value)} />
              <button className="btn-secondary" disabled={!newRate} onClick={addRate}>Add</button>
            </div>
          )}
        </div>
      </div>
    </>
  )
}
