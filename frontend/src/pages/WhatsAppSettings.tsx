import { useCallback, useEffect, useState } from 'react'
import { Drawer, Empty, ErrorBox, Field, PageHeader, useToast } from '../components/ui'
import { api, errorMessage } from '../lib/api'
import { useAuth } from '../lib/auth'
import { isManager, type WaAccount, type WaTemplate } from '../lib/types'

const STATUS_BADGE: Record<string, string> = {
  APPROVED: 'bg-pine-50 text-pine-700', PENDING: 'bg-amber-50 text-amber-700', REJECTED: 'bg-red-50 text-red-700',
  PAUSED: 'bg-slate-100 text-slate-600', DISABLED: 'bg-slate-100 text-slate-600', DRAFT: 'bg-slate-100 text-slate-600',
}

const emptyTpl = { name: '', category: 'UTILITY', language: 'en', header_text: '', body: '', footer: '', buttons: '', example_values: '' }

export function WhatsAppSettings() {
  const { me } = useAuth()
  const manager = isManager(me?.role)
  const toast = useToast()
  const [acc, setAcc] = useState<WaAccount | null>(null)
  const [form, setForm] = useState({ mode: 'simulator', waba_id: '', phone_number_id: '', display_phone_number: '', access_token: '', app_secret: '' })
  const [templates, setTemplates] = useState<WaTemplate[]>([])
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [tplOpen, setTplOpen] = useState(false)
  const [tpl, setTpl] = useState(emptyTpl)
  const [tplError, setTplError] = useState<string | null>(null)

  const load = useCallback(async () => {
    const [a, t] = await Promise.all([api.get<WaAccount>('/whatsapp/account'), api.get<WaTemplate[]>('/whatsapp/templates')])
    setAcc(a.data)
    setTemplates(t.data)
    setForm((f) => ({
      ...f, mode: a.data.mode ?? (a.data.simulator_enabled ? 'simulator' : 'cloud'),
      waba_id: a.data.waba_id ?? '', phone_number_id: a.data.mode === 'cloud' ? a.data.phone_number_id ?? '' : '',
      display_phone_number: a.data.mode === 'cloud' ? a.data.display_phone_number ?? '' : '', access_token: '', app_secret: '',
    }))
  }, [])
  useEffect(() => { load().catch(() => {}) }, [load])

  const save = async () => {
    setBusy(true); setError(null)
    try {
      await api.put('/whatsapp/account', {
        mode: form.mode, waba_id: form.waba_id || null, phone_number_id: form.phone_number_id || null,
        display_phone_number: form.display_phone_number || null,
        access_token: form.access_token || null, app_secret: form.app_secret || null,
      })
      toast('WhatsApp settings saved'); load()
    } catch (e) { setError(errorMessage(e)) } finally { setBusy(false) }
  }

  const test = async () => {
    setBusy(true); setError(null)
    try {
      const { data } = await api.post('/whatsapp/account/test')
      toast(`Connected and subscribed to message webhooks. Meta returned ${data.templates_found} template(s).`)
    } catch (e) { setError(errorMessage(e)) } finally { setBusy(false) }
  }

  const sync = async () => {
    try { const { data } = await api.post<WaTemplate[]>('/whatsapp/templates/sync'); setTemplates(data); toast('Template status updated from Meta') }
    catch (e) { toast(errorMessage(e), 'error') }
  }

  const vars = (tpl.body.match(/\{\{(\d+)\}\}/g) ?? []).filter((v, i, a) => a.indexOf(v) === i).length

  const createTpl = async () => {
    setTplError(null); setBusy(true)
    try {
      await api.post('/whatsapp/templates', {
        name: tpl.name, category: tpl.category, language: tpl.language, body: tpl.body,
        header_text: tpl.header_text || null, footer: tpl.footer || null,
        buttons: tpl.buttons.split(',').map((b) => b.trim()).filter(Boolean).map((text) => ({ type: 'QUICK_REPLY', text })),
        example_values: vars ? tpl.example_values.split(',').map((v) => v.trim()).filter(Boolean) : [],
      })
      toast('Template submitted for approval'); setTplOpen(false); setTpl(emptyTpl); load()
    } catch (e) { setTplError(errorMessage(e)) } finally { setBusy(false) }
  }

  const review = async (t: WaTemplate, decision: 'approve' | 'reject') => {
    await api.post(`/simulator/templates/${t.id}/${decision}`); load()
  }

  const webhookUrl = `${window.location.origin.replace(':5173', ':8000')}${acc?.webhook_url_path ?? '/api/webhooks/whatsapp'}`
  const set = (k: string, v: string) => setForm((f) => ({ ...f, [k]: v }))

  return (
    <>
      <PageHeader title="WhatsApp" subtitle="Connect your WhatsApp Business number and manage message templates." />
      <div className="grid gap-6 xl:grid-cols-[1fr_1fr]">
        <div className="card p-5">
          <div className="mb-4 flex items-center justify-between">
            <h2 className="font-medium">Number</h2>
            {acc?.connected
              ? <span className="badge bg-pine-50 text-pine-700">{acc.mode === 'simulator' ? 'Simulator' : `Live · ${acc.display_phone_number ?? acc.phone_number_id}`}</span>
              : <span className="badge bg-slate-100 text-slate-600">Not connected</span>}
          </div>
          <ErrorBox message={error} />
          <fieldset disabled={!manager} className="space-y-3">
            <div className="grid grid-cols-2 gap-2">
              {acc?.simulator_enabled && (
                <label className={`cursor-pointer rounded-lg border p-3 text-sm ${form.mode === 'simulator' ? 'border-pine-500 bg-pine-50' : 'border-slate-200'}`}>
                  <input type="radio" className="mr-2" checked={form.mode === 'simulator'} onChange={() => set('mode', 'simulator')} />
                  <span className="font-medium">Simulator</span>
                  <span className="mt-1 block text-xs text-slate-500">Test without Meta. Buyer messages are simulated in the inbox.</span>
                </label>
              )}
              <label className={`cursor-pointer rounded-lg border p-3 text-sm ${form.mode === 'cloud' ? 'border-pine-500 bg-pine-50' : 'border-slate-200'}`}>
                <input type="radio" className="mr-2" checked={form.mode === 'cloud'} onChange={() => set('mode', 'cloud')} />
                <span className="font-medium">Live number</span>
                <span className="mt-1 block text-xs text-slate-500">Meta WhatsApp Cloud API.</span>
              </label>
            </div>
            {form.mode === 'cloud' && (
              <>
                <Field label="WhatsApp Business Account ID *"><input className="input" value={form.waba_id} onChange={(e) => set('waba_id', e.target.value.trim())} /></Field>
                <Field label="Phone number ID *"><input className="input" value={form.phone_number_id} onChange={(e) => set('phone_number_id', e.target.value.trim())} /></Field>
                <Field label="Display number"><input className="input" value={form.display_phone_number} onChange={(e) => set('display_phone_number', e.target.value)} placeholder="+91 …" /></Field>
                <Field label="Access token" hint={acc?.access_token_masked ? `Saved (${acc.access_token_masked}). Leave empty to keep it.` : 'From WhatsApp → API Setup in your Meta app'}>
                  <input className="input" type="password" autoComplete="off" value={form.access_token} onChange={(e) => set('access_token', e.target.value)} />
                </Field>
                <Field label="App secret" hint={acc?.app_secret_masked ? `Saved (${acc.app_secret_masked}). Leave empty to keep it.` : 'Meta app → App settings → Basic. Used to verify incoming webhooks.'}>
                  <input className="input" type="password" autoComplete="off" value={form.app_secret} onChange={(e) => set('app_secret', e.target.value)} />
                </Field>
                <div className="rounded-lg bg-slate-50 p-3 text-xs text-slate-600">
                  <div className="font-medium text-slate-700">Webhook for your Meta app</div>
                  <div className="mt-1">Callback URL: <code className="break-all">{webhookUrl}</code> (must be public HTTPS)</div>
                  <div>Verify token: the WHATSAPP_VERIFY_TOKEN value in the server's .env</div>
                  <div>Subscribe to the <code>messages</code> field.</div>
                </div>
              </>
            )}
          </fieldset>
          {manager && (
            <div className="mt-4 flex justify-end gap-2">
              {acc?.mode === 'cloud' && <button className="btn-secondary" disabled={busy} onClick={test}>Test connection</button>}
              <button className="btn-primary" disabled={busy} onClick={save}>Save</button>
            </div>
          )}
        </div>

        <div className="card">
          <div className="flex items-center justify-between border-b border-slate-200 p-4">
            <div>
              <h2 className="font-medium">Message templates</h2>
              <p className="text-xs text-slate-500">Needed to message a buyer more than 24 hours after their last message.</p>
            </div>
            {manager && acc?.connected && (
              <div className="flex gap-2">
                {acc.mode === 'cloud' && <button className="btn-secondary" onClick={sync}>Refresh status</button>}
                <button className="btn-primary" onClick={() => { setTplOpen(true); setTplError(null) }}>New template</button>
              </div>
            )}
          </div>
          <div className="divide-y divide-slate-100">
            {templates.map((t) => (
              <div key={t.id} className="p-4">
                <div className="flex items-center justify-between gap-2">
                  <div className="font-mono text-sm">{t.name} <span className="text-xs text-slate-400">· {t.language} · {t.category.toLowerCase()}</span></div>
                  <span className={`badge ${STATUS_BADGE[t.status]}`}>{t.status.toLowerCase()}</span>
                </div>
                <p className="mt-1 whitespace-pre-wrap text-sm text-slate-600">{t.body}</p>
                {t.buttons && <div className="mt-1 text-xs text-slate-500">Buttons: {t.buttons.map((b) => b.text).join(', ')}</div>}
                {t.rejection_reason && <div className="mt-1 text-xs text-red-600">{t.rejection_reason}</div>}
                {acc?.mode === 'simulator' && manager && t.status === 'PENDING' && (
                  <div className="mt-2 flex gap-2 text-xs">
                    <span className="text-slate-500">Simulator review:</span>
                    <button className="text-pine-700 underline" onClick={() => review(t, 'approve')}>Approve</button>
                    <button className="text-red-600 underline" onClick={() => review(t, 'reject')}>Reject</button>
                  </div>
                )}
              </div>
            ))}
          </div>
          {templates.length === 0 && <Empty title="No templates yet" />}
        </div>
      </div>

      <Drawer open={tplOpen} title="New message template" onClose={() => setTplOpen(false)}
        footer={<><button className="btn-secondary" onClick={() => setTplOpen(false)}>Cancel</button>
          <button className="btn-primary" disabled={busy || !tpl.name || !tpl.body} onClick={createTpl}>Submit for approval</button></>}>
        <ErrorBox message={tplError} />
        <div className="space-y-3">
          <Field label="Name *" hint="Lowercase letters, numbers and underscores, e.g. payment_reminder">
            <input className="input font-mono" value={tpl.name} onChange={(e) => setTpl({ ...tpl, name: e.target.value })} />
          </Field>
          <div className="grid grid-cols-2 gap-3">
            <Field label="Category">
              <select className="input" value={tpl.category} onChange={(e) => setTpl({ ...tpl, category: e.target.value })}>
                <option value="UTILITY">Utility (orders, invoices, reminders)</option>
                <option value="MARKETING">Marketing (offers, launches)</option>
              </select>
            </Field>
            <Field label="Language">
              <select className="input" value={tpl.language} onChange={(e) => setTpl({ ...tpl, language: e.target.value })}>
                <option value="en">English</option><option value="en_US">English (US)</option>
                <option value="ta">Tamil</option><option value="hi">Hindi</option>
              </select>
            </Field>
          </div>
          <Field label="Header (optional)"><input className="input" maxLength={60} value={tpl.header_text} onChange={(e) => setTpl({ ...tpl, header_text: e.target.value })} /></Field>
          <Field label="Body *" hint="Use {{1}}, {{2}} for values filled in when sending, e.g. Hi {{1}}, invoice {{2}} is due.">
            <textarea className="input" rows={4} maxLength={1024} value={tpl.body} onChange={(e) => setTpl({ ...tpl, body: e.target.value })} />
          </Field>
          {vars > 0 && (
            <Field label={`Example values (${vars}) *`} hint="Comma separated; Meta uses these to review the template">
              <input className="input" value={tpl.example_values} onChange={(e) => setTpl({ ...tpl, example_values: e.target.value })} placeholder="Ravi, INV-1024" />
            </Field>
          )}
          <Field label="Footer (optional)"><input className="input" maxLength={60} value={tpl.footer} onChange={(e) => setTpl({ ...tpl, footer: e.target.value })} /></Field>
          <Field label="Quick reply buttons (optional)" hint="Up to 3, comma separated, e.g. Pay now, Talk to us">
            <input className="input" value={tpl.buttons} onChange={(e) => setTpl({ ...tpl, buttons: e.target.value })} />
          </Field>
        </div>
      </Drawer>
    </>
  )
}
