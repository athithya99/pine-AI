import { useEffect, useState } from 'react'
import { useParams } from 'react-router-dom'
import { ErrorBox, Field, PageHeader, useToast } from '../components/ui'
import { api, errorMessage } from '../lib/api'
import { useAuth } from '../lib/auth'
import { inr, isManager, type Tenant } from '../lib/types'

interface Gateway {
  mode: 'simulator' | 'razorpay'; key_id: string | null; key_secret_masked: string | null
  webhook_secret_masked: string | null; webhook_url_path: string; simulator_enabled: boolean
}

const STEP_OPTIONS = [-7, -3, -1, 0, 1, 3, 7, 15, 30, 45, 60]
const stepName = (d: number) => d < 0 ? `${-d} day${d !== -1 ? 's' : ''} before due` : d === 0 ? 'On due date' : `${d} day${d !== 1 ? 's' : ''} after due`

export function PaymentSettings() {
  const { me, reload } = useAuth()
  const manager = isManager(me?.role)
  const toast = useToast()
  const [gw, setGw] = useState<Gateway | null>(null)
  const [mode, setMode] = useState<'simulator' | 'razorpay'>('simulator')
  const [keyId, setKeyId] = useState('')
  const [secret, setSecret] = useState('')
  const [whSecret, setWhSecret] = useState('')
  const [company, setCompany] = useState<Tenant | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    api.get<Gateway>('/payments/gateway').then((r) => { setGw(r.data); setMode(r.data.mode); setKeyId(r.data.key_id ?? '') })
    api.get<Tenant>('/company').then((r) => setCompany(r.data))
  }, [])

  const saveGateway = async () => {
    setError(null)
    try {
      const { data } = await api.put<Gateway>('/payments/gateway', { mode, key_id: keyId || null, key_secret: secret || null, webhook_secret: whSecret || null })
      setGw(data); setSecret(''); setWhSecret(''); toast('Payment gateway saved')
    } catch (e) { setError(errorMessage(e)) }
  }
  const test = async () => {
    try { toast((await api.post('/payments/gateway/test')).data.message) } catch (e) { toast(errorMessage(e), 'error') }
  }
  const saveReminders = async () => {
    if (!company) return
    try {
      await api.patch('/company', { reminders_enabled: company.reminders_enabled, reminder_offsets: company.reminder_offsets, receipt_prefix: company.receipt_prefix })
      toast('Reminder schedule saved'); reload()
    } catch (e) { toast(errorMessage(e), 'error') }
  }
  const toggleStep = (d: number) => company && setCompany({
    ...company, reminder_offsets: company.reminder_offsets.includes(d) ? company.reminder_offsets.filter((x) => x !== d) : [...company.reminder_offsets, d].sort((a, b) => a - b),
  })
  const webhookUrl = gw ? `${window.location.origin.replace(':5173', ':8000')}${gw.webhook_url_path}` : ''

  return (
    <>
      <PageHeader title="Payment settings" subtitle="Your payment gateway for payment links, and the reminder schedule." />
      <div className="grid gap-6 lg:grid-cols-2">
        <div className="card p-5">
          <h2 className="font-medium">Payment gateway</h2>
          <p className="mt-1 text-xs text-slate-500">Links are created on your own gateway account, so money settles directly to you. Pinesphere never holds funds or card details.</p>
          <ErrorBox message={error} />
          <fieldset disabled={!manager} className="mt-4 space-y-3">
            <div className="grid grid-cols-2 gap-2">
              {([['simulator', 'Simulator', 'For testing and demos. Links open a test payment page.'],
                ['razorpay', 'Razorpay', 'Real UPI / card / net banking. Use test keys first.']] as const).map(([k, t, d]) => (
                <button key={k} type="button" onClick={() => setMode(k)}
                  className={`rounded-lg border p-3 text-left text-sm ${mode === k ? 'border-pine-500 bg-pine-50' : 'border-slate-200 hover:bg-slate-50'}`}>
                  <div className="font-medium">{t}</div><div className="text-xs text-slate-500">{d}</div>
                </button>
              ))}
            </div>
            {mode === 'razorpay' && <>
              <Field label="Key ID" hint="Razorpay Dashboard → Account & Settings → API keys (starts with rzp_test_ or rzp_live_)"><input className="input font-mono" value={keyId} onChange={(e) => setKeyId(e.target.value)} /></Field>
              <Field label="Key Secret" hint={gw?.key_secret_masked ? `Saved: ${gw.key_secret_masked}. Leave blank to keep.` : undefined}><input className="input font-mono" type="password" value={secret} onChange={(e) => setSecret(e.target.value)} /></Field>
              <Field label="Webhook secret" hint={gw?.webhook_secret_masked ? `Saved: ${gw.webhook_secret_masked}. Leave blank to keep.` : 'The secret you type when adding the webhook in Razorpay'}><input className="input font-mono" type="password" value={whSecret} onChange={(e) => setWhSecret(e.target.value)} /></Field>
              <div className="rounded-lg bg-slate-50 p-3 text-xs text-slate-600">
                In Razorpay → Webhooks, add this URL with the event <b>payment_link.paid</b>:
                <div className="mt-1 break-all font-mono text-slate-800">{webhookUrl}</div>
                <div className="mt-1">It must be reachable over HTTPS (use your tunnel URL while testing locally).</div>
              </div>
            </>}
          </fieldset>
          {manager && <div className="mt-4 flex justify-end gap-2"><button className="btn-secondary" onClick={test}>Test connection</button><button className="btn-primary" onClick={saveGateway}>Save</button></div>}
        </div>

        {company && (
          <div className="card p-5">
            <h2 className="font-medium">Payment reminders</h2>
            <p className="mt-1 text-xs text-slate-500">Sent every morning on WhatsApp to buyers who opted in, listing unpaid invoices with a payment link. Outside the 24-hour chat window this needs an approved <span className="font-mono">payment_reminder</span> template (name, amount, link).</p>
            <fieldset disabled={!manager} className="mt-4 space-y-3">
              <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={company.reminders_enabled} onChange={(e) => setCompany({ ...company, reminders_enabled: e.target.checked })} /> Send payment reminders</label>
              <div className="flex flex-wrap gap-2">
                {STEP_OPTIONS.map((d) => (
                  <button key={d} type="button" onClick={() => toggleStep(d)}
                    className={`rounded-full border px-3 py-1 text-xs ${company.reminder_offsets.includes(d) ? 'border-pine-500 bg-pine-50 text-pine-800' : 'border-slate-200 text-slate-500'}`}>{stepName(d)}</button>
                ))}
              </div>
              <Field label="Receipt number prefix" className="max-w-[200px]"><input className="input uppercase" maxLength={5} value={company.receipt_prefix} onChange={(e) => setCompany({ ...company, receipt_prefix: e.target.value })} /></Field>
            </fieldset>
            {manager && <div className="mt-4 flex justify-end"><button className="btn-primary" onClick={saveReminders}>Save</button></div>}
          </div>
        )}
      </div>
    </>
  )
}

/** Public test payment page for simulator links (what the buyer sees when they tap the link). */
export function PayPage() {
  const { id } = useParams()
  const [link, setLink] = useState<{ company: string; customer: string; amount: string; status: string } | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [done, setDone] = useState<string | null>(null)
  useEffect(() => { api.get(`/public/pay/${id}`).then((r) => setLink(r.data)).catch((e) => setError(errorMessage(e))) }, [id])
  const pay = async () => {
    try { setDone((await api.post(`/public/pay/${id}`)).data.receipt) } catch (e) { setError(errorMessage(e)) }
  }
  return (
    <div className="grid min-h-screen place-items-center bg-slate-50 px-4">
      <div className="card w-full max-w-sm p-6 text-center">
        <div className="mb-1 text-xs font-semibold uppercase tracking-wide text-amber-700">Test payment page (simulator)</div>
        {error && <p className="text-sm text-red-600">{error}</p>}
        {link && <>
          <div className="text-sm text-slate-500">Pay {link.company}</div>
          <div className="my-2 text-3xl font-semibold">{inr(link.amount)}</div>
          <div className="mb-4 text-xs text-slate-500">for {link.customer}</div>
          {done ? <p className="rounded-lg bg-emerald-50 p-3 text-sm text-emerald-800">Payment successful. Receipt {done}.</p>
            : link.status === 'created' ? <button className="btn-primary w-full" onClick={pay}>Pay with UPI (test)</button>
              : <p className="text-sm text-slate-500">This link is {link.status}.</p>}
        </>}
      </div>
    </div>
  )
}
