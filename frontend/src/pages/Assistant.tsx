import { useEffect, useRef, useState } from 'react'
import { ErrorBox, Field, PageHeader, useToast } from '../components/ui'
import { api, download, errorMessage } from '../lib/api'
import { useAuth } from '../lib/auth'
import { inr, isManager, type AnswerColumn, type AssistantMsg, type AutomationRuleT, type DailySummaryT, type Tenant } from '../lib/types'

const SAMPLES = [
  'Who is above 60 days overdue?', 'Top 10 customers this month', 'Customers with no order in 30 days',
  'How much did we collect this week?', 'Orders pending approval', 'Best selling products last month',
  'Invoices due this week', 'Unanswered chats',
]

function cell(v: any, c: AnswerColumn) {
  if (v === null || v === undefined || v === '') return '—'
  if (c.type === 'money') return inr(v)
  if (c.type === 'date') return new Date(v + 'T00:00:00').toLocaleDateString('en-IN', { day: 'numeric', month: 'short', year: 'numeric' })
  if (c.type === 'datetime') return new Date(v).toLocaleString('en-IN', { day: 'numeric', month: 'short', hour: 'numeric', minute: '2-digit' })
  if (c.type === 'number') return Number(v).toLocaleString('en-IN')
  return String(v)
}

function Answer({ m, onRule }: { m: AssistantMsg; onRule: (m: AssistantMsg) => void }) {
  const { me } = useAuth()
  const [all, setAll] = useState(false)
  const rows = m.rows ?? []
  const shown = all ? rows : rows.slice(0, 10)
  return (
    <div className="space-y-2">
      <div className="flex justify-end"><div className="max-w-[80%] rounded-2xl rounded-br-md bg-pine-600 px-3.5 py-2 text-sm text-white">{m.question}</div></div>
      <div className="max-w-full rounded-2xl rounded-bl-md border border-slate-200 bg-white px-4 py-3 text-sm">
        <p className={m.kind === 'unknown' || m.kind === 'error' ? 'text-slate-600' : 'text-slate-900'}>{m.answer}</p>
        {m.columns && rows.length > 0 && (
          <div className="mt-3 overflow-x-auto rounded-lg border border-slate-100">
            <table className="w-full min-w-[480px] text-xs">
              <thead className="bg-slate-50"><tr>{m.columns.map((c) => <th key={c.key} className={`px-3 py-2 font-semibold text-slate-500 ${['money', 'int', 'number'].includes(c.type) ? 'text-right' : 'text-left'}`}>{c.label}</th>)}</tr></thead>
              <tbody className="divide-y divide-slate-100">
                {shown.map((r, i) => <tr key={i}>{m.columns!.map((c) => <td key={c.key} className={`px-3 py-1.5 ${['money', 'int', 'number'].includes(c.type) ? 'text-right tabular-nums' : ''}`}>{cell(r[c.key], c)}</td>)}</tr>)}
              </tbody>
            </table>
          </div>
        )}
        <div className="mt-2 flex flex-wrap items-center gap-3 text-xs text-slate-500">
          {rows.length > 10 && <button className="text-pine-700 hover:underline" onClick={() => setAll(!all)}>{all ? 'Show less' : `Show all ${rows.length}`}</button>}
          {m.columns && rows.length > 0 && <button className="text-pine-700 hover:underline" onClick={() => download(`/assistant/messages/${m.id}/export`, `${m.tool ?? 'answer'}.xlsx`)}>Export to Excel</button>}
          {m.total_rows > rows.length && <span>First {rows.length} of {m.total_rows} rows</span>}
          <span className="ml-auto">{new Date(m.created_at).toLocaleTimeString('en-IN', { hour: 'numeric', minute: '2-digit' })}</span>
        </div>
        {m.kind === 'rule_proposal' && (
          <div className="mt-3 rounded-lg border border-amber-200 bg-amber-50 p-3">
            <div className="text-xs font-semibold uppercase tracking-wide text-amber-800">New automatic rule</div>
            <p className="mt-1 text-sm text-amber-900">{m.params?.rule?.description}</p>
            {m.rule_id ? <p className="mt-2 text-xs text-emerald-700">✓ Created</p>
              : me?.role === 'owner' && <div className="mt-2 flex gap-2"><button className="btn-primary px-3 py-1 text-xs" onClick={() => onRule(m)}>Confirm and create</button></div>}
          </div>
        )}
      </div>
    </div>
  )
}

const WEEKDAY = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']

function SidePanel() {
  const { me } = useAuth()
  const toast = useToast()
  const owner = me?.role === 'owner'
  const [rules, setRules] = useState<AutomationRuleT[]>([])
  const [sums, setSums] = useState<DailySummaryT[]>([])
  const [company, setCompany] = useState<Tenant | null>(null)
  const load = () => {
    api.get<AutomationRuleT[]>('/assistant/rules').then((r) => setRules(r.data)).catch(() => {})
    api.get<DailySummaryT[]>('/assistant/summaries', { params: { limit: 3 } }).then((r) => setSums(r.data)).catch(() => {})
  }
  useEffect(() => { load(); api.get<Tenant>('/company').then((r) => setCompany(r.data)) }, [])
  const saveSummary = async () => {
    if (!company) return
    try {
      await api.patch('/company', { daily_summary_enabled: company.daily_summary_enabled, daily_summary_time: company.daily_summary_time, daily_summary_to: company.daily_summary_to || null })
      toast('Daily summary settings saved')
    } catch (e) { toast(errorMessage(e), 'error') }
  }
  const sendNow = async () => {
    try { const { data } = await api.post<DailySummaryT>('/assistant/summaries/send-now'); toast(data.sent_at ? `Sent to ${data.sent_to}` : `Saved, not sent: ${data.send_error}`, data.sent_at ? 'ok' : 'error'); load() }
    catch (e) { toast(errorMessage(e), 'error') }
  }
  const toggle = async (r: AutomationRuleT) => {
    try { await api.patch(`/assistant/rules/${r.id}`, { is_active: !r.is_active }); load() } catch (e) { toast(errorMessage(e), 'error') }
  }
  const remove = async (r: AutomationRuleT) => {
    if (!window.confirm('Delete this rule?')) return
    try { await api.delete(`/assistant/rules/${r.id}`); load() } catch (e) { toast(errorMessage(e), 'error') }
  }
  return (
    <div className="space-y-4">
      {company && (
        <div className="card p-4">
          <h2 className="font-medium">Morning summary</h2>
          <p className="mt-0.5 text-xs text-slate-500">Yesterday's orders, collections, new overdue, approvals and unanswered chats, on WhatsApp.</p>
          <fieldset disabled={!owner && me?.role !== 'admin'} className="mt-3 space-y-2">
            <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={company.daily_summary_enabled} onChange={(e) => setCompany({ ...company, daily_summary_enabled: e.target.checked })} /> Send every day</label>
            <div className="grid grid-cols-2 gap-2">
              <Field label="Time (IST)"><input type="time" className="input" value={company.daily_summary_time} onChange={(e) => setCompany({ ...company, daily_summary_time: e.target.value })} /></Field>
              <Field label="WhatsApp number" hint="Blank = owner's mobile"><input className="input" value={company.daily_summary_to ?? ''} onChange={(e) => setCompany({ ...company, daily_summary_to: e.target.value })} /></Field>
            </div>
            <div className="flex justify-end gap-2"><button className="btn-secondary px-3 py-1.5 text-xs" onClick={sendNow}>Send today's now</button><button className="btn-primary px-3 py-1.5 text-xs" onClick={saveSummary}>Save</button></div>
          </fieldset>
          {sums[0] && <div className="mt-3 rounded-lg bg-slate-50 p-3">
            <pre className="whitespace-pre-wrap font-sans text-xs text-slate-700">{sums[0].text}</pre>
            <p className={`mt-1 text-[11px] ${sums[0].sent_at ? 'text-emerald-700' : 'text-amber-700'}`}>{sums[0].sent_at ? `Sent to ${sums[0].sent_to}` : `Not sent: ${sums[0].send_error}`}</p>
          </div>}
        </div>
      )}
      <div className="card p-4">
        <h2 className="font-medium">Automatic rules</h2>
        <p className="mt-0.5 text-xs text-slate-500">Owner can ask, e.g. "remind everyone above 60 days every Monday". A rule is created only after you confirm it.</p>
        {rules.length === 0 ? <p className="mt-3 text-sm text-slate-400">No rules yet.</p> : (
          <ul className="mt-3 space-y-2">
            {rules.map((r) => (
              <li key={r.id} className={`rounded-lg border p-3 text-sm ${r.is_active ? 'border-slate-200' : 'border-slate-100 opacity-60'}`}>
                <p>{r.description}</p>
                <p className="mt-1 text-xs text-slate-500">{r.weekdays.map((d) => WEEKDAY[d]).join(', ')} · {r.last_run_summary ?? 'Not run yet'}</p>
                {owner && <div className="mt-2 flex gap-3 text-xs"><button className="text-pine-700 hover:underline" onClick={() => toggle(r)}>{r.is_active ? 'Pause' : 'Resume'}</button>
                  <button className="text-red-600 hover:underline" onClick={() => remove(r)}>Delete</button></div>}
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  )
}

export function Assistant() {
  const { me } = useAuth()
  const toast = useToast()
  const [msgs, setMsgs] = useState<AssistantMsg[]>([])
  const [q, setQ] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const end = useRef<HTMLDivElement>(null)

  useEffect(() => { api.get<AssistantMsg[]>('/assistant/messages').then((r) => setMsgs(r.data)) }, [])
  useEffect(() => { end.current?.scrollIntoView({ behavior: 'smooth' }) }, [msgs.length])

  const ask = async (question: string) => {
    if (!question.trim() || busy) return
    setBusy(true); setError(null)
    try { const { data } = await api.post<AssistantMsg>('/assistant/ask', { question }); setMsgs((m) => [...m, data]); setQ('') }
    catch (e) { setError(errorMessage(e)) } finally { setBusy(false) }
  }
  const confirmRule = async (m: AssistantMsg) => {
    try {
      const { data } = await api.post<AutomationRuleT>(`/assistant/messages/${m.id}/confirm-rule`)
      setMsgs((all) => all.map((x) => (x.id === m.id ? { ...x, rule_id: data.id } : x)))
      toast('Rule created')
    } catch (e) { toast(errorMessage(e), 'error') }
  }

  return (
    <>
      <PageHeader title="Ask AI" subtitle={me?.role === 'sales' ? 'Questions about your customers, orders and payments. Answers cover only your customers.'
        : 'Ask about your orders, customers, payments, invoices, stock and chats in plain language. Every figure comes from your data.'} />
      <div className="grid gap-6 lg:grid-cols-[1fr_340px]">
        <div className="card flex min-h-[560px] flex-col">
          <div className="flex-1 space-y-5 overflow-y-auto p-4">
            {msgs.length === 0 && (
              <div className="py-8 text-center">
                <p className="font-medium text-slate-700">What would you like to know?</p>
                <div className="mx-auto mt-4 flex max-w-xl flex-wrap justify-center gap-2">
                  {SAMPLES.map((s) => <button key={s} className="rounded-full border border-slate-200 px-3 py-1.5 text-xs text-slate-600 hover:border-pine-500 hover:text-pine-700" onClick={() => ask(s)}>{s}</button>)}
                </div>
              </div>
            )}
            {msgs.map((m) => <Answer key={m.id} m={m} onRule={confirmRule} />)}
            {busy && <div className="h-10 w-40 animate-pulse rounded-2xl bg-slate-100" />}
            <div ref={end} />
          </div>
          <div className="border-t border-slate-200 p-3">
            <ErrorBox message={error} />
            {msgs.length > 0 && <div className="mb-2 flex gap-1.5 overflow-x-auto">
              {SAMPLES.slice(0, 5).map((s) => <button key={s} className="whitespace-nowrap rounded-full border border-slate-200 px-2.5 py-1 text-[11px] text-slate-600 hover:border-pine-500" onClick={() => ask(s)}>{s}</button>)}
            </div>}
            <form className="flex gap-2" onSubmit={(e) => { e.preventDefault(); ask(q) }}>
              <input className="input" placeholder='e.g. "who is above 60 days overdue?"' value={q} onChange={(e) => setQ(e.target.value)} maxLength={1000} />
              <button className="btn-primary" disabled={busy || q.trim().length < 2}>Ask</button>
            </form>
          </div>
        </div>
        {isManager(me?.role) ? <SidePanel /> : <div />}
      </div>
    </>
  )
}
