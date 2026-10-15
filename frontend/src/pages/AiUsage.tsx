import { Fragment, useCallback, useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { Empty, PageHeader, Pagination, titleCase } from '../components/ui'
import { api } from '../lib/api'
import type { AiLog, AiStatus, AiUsage as Usage, Page } from '../lib/types'
import { fmtDate } from './Orders'

const SIZE = 50
const USD_TO_INR = 90 // display estimate only

export function AiUsage() {
  const [status, setStatus] = useState<AiStatus | null>(null)
  const [usage, setUsage] = useState<Usage | null>(null)
  const [logs, setLogs] = useState<Page<AiLog>>({ items: [], total: 0, page: 1, size: SIZE })
  const [page, setPage] = useState(1)
  const [errorsOnly, setErrorsOnly] = useState(false)
  const [open, setOpen] = useState<string | null>(null)

  const load = useCallback(() => {
    api.get<Usage>('/ai/usage').then((r) => setUsage(r.data)).catch(() => {})
    api.get<Page<AiLog>>('/ai/logs', { params: { page, size: SIZE, errors_only: errorsOnly || undefined } })
      .then((r) => setLogs(r.data)).catch(() => {})
  }, [page, errorsOnly])
  useEffect(() => { api.get<AiStatus>('/ai/status').then((r) => setStatus(r.data)).catch(() => {}) }, [])
  useEffect(() => { load() }, [load])

  const cost = usage ? Number(usage.cost_usd) : 0
  const cards = usage ? [
    { label: 'AI calls this month', value: usage.calls.toLocaleString('en-IN') },
    { label: 'Orders created by AI', value: usage.orders_created.toLocaleString('en-IN') },
    { label: 'Estimated cost', value: `$${cost.toFixed(2)}`, sub: `≈ ₹${Math.round(cost * USD_TO_INR).toLocaleString('en-IN')}` },
    { label: 'Failed calls', value: usage.errors.toLocaleString('en-IN'), warn: usage.errors > 0 },
  ] : []

  return (
    <>
      <PageHeader title="AI usage" subtitle="Every AI call with what it read, what it understood, tokens, cost and time taken." />

      {status && (
        <div className={`mb-4 rounded-lg border p-3 text-sm ${status.is_mock ? 'border-amber-200 bg-amber-50 text-amber-900' : 'border-slate-200 bg-white text-slate-700'}`}>
          {status.is_mock
            ? <>Running on the <b>development AI</b> (built-in rules). It understands typed orders only, not photos or voice notes. Add the company's API key in the server settings to switch to real AI.</>
            : <>Provider <b>{titleCase(status.provider)}</b> · text: {status.model_fast} · photos: {status.model_smart} · voice notes: {status.voice_notes ? 'on' : 'off'}{!status.configured && <b className="text-red-600"> · API key missing</b>}</>}
          {!status.ai_ordering_enabled && <> · <b>AI ordering is switched off</b> in Settings → Company.</>}
        </div>
      )}

      <div className="mb-4 grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        {cards.map((c) => (
          <div key={c.label} className={`card p-4 ${c.warn ? 'border-amber-300' : ''}`}>
            <div className="text-sm text-slate-500">{c.label}</div>
            <div className="mt-1 text-2xl font-semibold text-slate-900">{c.value}</div>
            {c.sub && <div className="text-xs text-slate-500">{c.sub}</div>}
          </div>
        ))}
      </div>
      {usage && Object.keys(usage.by_intent).length > 0 && (
        <div className="mb-4 flex flex-wrap gap-2 text-xs">
          {Object.entries(usage.by_intent).sort((a, b) => b[1] - a[1]).map(([k, v]) => (
            <span key={k} className="badge bg-slate-100 text-slate-700">{titleCase(k)}: {v}</span>
          ))}
          <span className="badge bg-slate-100 text-slate-500">avg {usage.avg_latency_ms} ms</span>
        </div>
      )}

      <div className="card">
        <div className="flex items-center gap-3 border-b border-slate-200 p-3 text-sm">
          <label className="flex items-center gap-2">
            <input type="checkbox" checked={errorsOnly} onChange={(e) => { setErrorsOnly(e.target.checked); setPage(1) }} /> Failed calls only
          </label>
        </div>
        <div className="overflow-x-auto">
          <table className="w-full min-w-[860px]">
            <thead className="bg-slate-50">
              <tr><th className="th">Time</th><th className="th">Customer</th><th className="th">Message</th><th className="th">Understood as</th>
                <th className="th">Model</th><th className="th text-right">Tokens</th><th className="th text-right">Cost</th><th className="th text-right">Time</th></tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {logs.items.map((l) => (
                <Fragment key={l.id}>
                  <tr className="cursor-pointer hover:bg-slate-50" onClick={() => setOpen(open === l.id ? null : l.id)}>
                    <td className="td whitespace-nowrap text-slate-600">{fmtDate(l.created_at, true)}</td>
                    <td className="td">{l.customer_name ?? '—'}</td>
                    <td className="td max-w-[260px] truncate text-slate-700" title={l.input_text ?? ''}>
                      {l.input_media && <span className="badge mr-1 bg-slate-100 text-slate-600">{l.input_media.startsWith('audio') ? 'voice' : 'photo'}</span>}
                      {l.input_text ?? ''}
                    </td>
                    <td className="td">
                      {l.error ? <span className="badge bg-red-50 text-red-700" title={l.error}>Failed</span>
                        : <span className="badge bg-pine-50 text-pine-700">{l.kind === 'transcribe' ? 'Transcribed' : titleCase(l.intent ?? 'other')}</span>}
                      {l.order_number && l.order_id && <Link to={`/orders/${l.order_id}`} onClick={(e) => e.stopPropagation()} className="ml-1 font-mono text-xs text-pine-700">{l.order_number}</Link>}
                    </td>
                    <td className="td text-xs text-slate-500">{l.model}</td>
                    <td className="td text-right tabular-nums text-slate-600">{(l.input_tokens + l.output_tokens).toLocaleString('en-IN')}</td>
                    <td className="td text-right tabular-nums text-slate-600">{l.cost_usd === null ? '—' : `$${Number(l.cost_usd).toFixed(4)}`}</td>
                    <td className="td text-right tabular-nums text-slate-600">{l.latency_ms} ms</td>
                  </tr>
                  {open === l.id && (
                    <tr className="bg-slate-50">
                      <td colSpan={8} className="px-4 pb-4 pt-1 text-xs">
                        {l.error && <div className="mb-2 rounded bg-red-50 p-2 text-red-700">{l.error}</div>}
                        {l.input_text && <div className="mb-2"><div className="font-semibold text-slate-500">Input</div><div className="whitespace-pre-wrap">{l.input_text}</div></div>}
                        {l.output && <div><div className="font-semibold text-slate-500">AI output</div>
                          <pre className="max-h-64 overflow-auto rounded bg-white p-2 text-[11px]">{JSON.stringify(l.output, null, 2)}</pre></div>}
                      </td>
                    </tr>
                  )}
                </Fragment>
              ))}
            </tbody>
          </table>
        </div>
        {logs.total === 0 && <Empty title="No AI calls yet">They appear here as buyers message you on WhatsApp.</Empty>}
        <Pagination page={page} size={SIZE} total={logs.total} onPage={setPage} />
      </div>
    </>
  )
}
