import { useCallback, useEffect, useRef, useState } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { ImportDialog } from '../components/ImportDialog'
import { Drawer, Empty, ErrorBox, PageHeader, Pagination, useToast } from '../components/ui'
import { api, download, errorMessage } from '../lib/api'
import { useAuth } from '../lib/auth'
import { inr, isAccounts, type InvoiceSummary, type Page } from '../lib/types'
import { fmtDate } from './Orders'

const SIZE = 25

const TABS = [
  { key: 'all', label: 'All', params: {} },
  { key: 'review', label: 'Needs review', params: { status: ['review'] } },
  { key: 'issued', label: 'Saved', params: { status: ['issued'] } },
  { key: 'overdue', label: 'Overdue', params: { overdue: true } },
  { key: 'cancelled', label: 'Cancelled', params: { status: ['cancelled'] } },
] as const

const SOURCE: Record<string, string> = { generated: 'Generated', erp: 'Tally/ERP', uploaded: 'Uploaded PDF', excel: 'Excel' }

export function InvoiceStatusBadge({ inv }: { inv: Pick<InvoiceSummary, 'status' | 'overdue' | 'balance_due'> }) {
  if (inv.status === 'review') return <span className="badge bg-amber-50 text-amber-800">Needs review</span>
  if (inv.status === 'cancelled') return <span className="badge bg-red-50 text-red-700">Cancelled</span>
  if (inv.overdue) return <span className="badge bg-red-50 text-red-700">Overdue</span>
  if (Number(inv.balance_due) <= 0) return <span className="badge bg-emerald-50 text-emerald-700">Paid</span>
  return <span className="badge bg-pine-50 text-pine-700">Saved</span>
}

export function Invoices() {
  const { me } = useAuth()
  const canWrite = isAccounts(me?.role)
  const navigate = useNavigate()
  const toast = useToast()
  const [params, setParams] = useSearchParams()
  const tab = params.get('tab') ?? 'all'
  const [q, setQ] = useState('')
  const [page, setPage] = useState(1)
  const [data, setData] = useState<Page<InvoiceSummary>>({ items: [], total: 0, page: 1, size: SIZE })
  const [counts, setCounts] = useState<Record<string, number>>({})
  const [loading, setLoading] = useState(true)
  const [importOpen, setImportOpen] = useState(false)
  const [attachOpen, setAttachOpen] = useState(false)
  const [uploading, setUploading] = useState(false)
  const fileRef = useRef<HTMLInputElement>(null)

  const tabParams = TABS.find((t) => t.key === tab)?.params ?? {}
  const load = useCallback(async () => {
    setLoading(true)
    try {
      const [list, c] = await Promise.all([
        api.get<Page<InvoiceSummary>>('/invoices', { params: { ...tabParams, q: q || undefined, page, size: SIZE }, paramsSerializer: { indexes: null } }),
        api.get<Record<string, number>>('/invoices/counts'),
      ])
      setData(list.data); setCounts(c.data)
    } finally { setLoading(false) }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tab, q, page])
  useEffect(() => { const t = setTimeout(load, 250); return () => clearTimeout(t) }, [load])

  const uploadPdfs = async (files: FileList | null) => {
    if (!files?.length) return
    setUploading(true)
    try {
      const form = new FormData()
      Array.from(files).forEach((f) => form.append('files', f))
      const { data } = await api.post<InvoiceSummary[]>('/invoices/upload', form)
      toast(`${data.length} invoice${data.length > 1 ? 's' : ''} read by AI. Review and confirm each one.`)
      if (data.length === 1) navigate(`/invoices/${data[0].id}`)
      else { setParams({ tab: 'review' }); load() }
    } catch (e) { toast(errorMessage(e), 'error') } finally {
      setUploading(false)
      if (fileRef.current) fileRef.current.value = ''
    }
  }

  const tabCount = (k: string) => k === 'review' ? counts.review : k === 'overdue' ? counts.overdue : undefined

  return (
    <>
      <PageHeader
        title="Invoices"
        subtitle="Invoices generated here, recorded from Tally/ERP, uploaded as PDFs or imported from Excel."
        actions={<>
          <button className="btn-secondary" onClick={() => download('/invoices/export', 'invoices.xlsx')}>Export</button>
          {canWrite && <>
            <button className="btn-secondary" onClick={() => setImportOpen(true)}>Import Excel</button>
            <button className="btn-secondary" onClick={() => setAttachOpen(true)}>Attach PDFs</button>
            <input ref={fileRef} type="file" accept="application/pdf" multiple hidden onChange={(e) => uploadPdfs(e.target.files)} />
            <button className="btn-primary" disabled={uploading} onClick={() => fileRef.current?.click()}>{uploading ? 'Reading…' : 'Upload invoice PDFs'}</button>
          </>}
        </>}
      />

      <div className="mb-3 flex gap-1 overflow-x-auto border-b border-slate-200">
        {TABS.map((t) => {
          const n = tabCount(t.key)
          return (
            <button key={t.key} onClick={() => { setParams(t.key === 'all' ? {} : { tab: t.key }); setPage(1) }}
              className={`-mb-px whitespace-nowrap border-b-2 px-3 py-2 text-sm ${tab === t.key ? 'border-pine-600 font-medium text-pine-700' : 'border-transparent text-slate-500 hover:text-slate-800'}`}>
              {t.label}{n ? <span className={`ml-1.5 rounded-full px-1.5 text-xs ${t.key === 'review' ? 'bg-amber-100 text-amber-800' : 'bg-red-100 text-red-700'}`}>{n}</span> : null}
            </button>
          )
        })}
      </div>

      <div className="card">
        <div className="border-b border-slate-200 p-3">
          <input className="input max-w-xs" placeholder="Search invoice no., customer or file" value={q} onChange={(e) => { setQ(e.target.value); setPage(1) }} />
        </div>
        <div className="overflow-x-auto">
          <table className="w-full min-w-[820px]">
            <thead className="bg-slate-50">
              <tr><th className="th">Invoice</th><th className="th">Customer</th><th className="th">Status</th><th className="th text-right">Total</th>
                <th className="th text-right">Balance</th><th className="th">Due</th><th className="th">Source</th></tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {data.items.map((i) => (
                <tr key={i.id} className="cursor-pointer hover:bg-slate-50" onClick={() => navigate(`/invoices/${i.id}`)}>
                  <td className="td">
                    <div className="whitespace-nowrap font-mono text-xs font-medium text-pine-700">{i.number ?? i.pdf_name ?? 'Uploaded PDF'}</div>
                    <div className="text-xs text-slate-500">{fmtDate(i.invoice_date)}{i.order_number && ` · ${i.order_number}`}</div>
                  </td>
                  <td className="td font-medium text-slate-900">{i.customer_name ?? <span className="text-slate-400">Not matched</span>}</td>
                  <td className="td"><InvoiceStatusBadge inv={i} />{i.sent && <span className="ml-1 text-xs text-slate-500" title="Sent on WhatsApp">✓ sent</span>}</td>
                  <td className="td text-right tabular-nums">{i.status === 'review' ? '—' : inr(i.total)}</td>
                  <td className="td text-right tabular-nums">{i.status === 'issued' ? inr(i.balance_due) : '—'}</td>
                  <td className={`td whitespace-nowrap ${i.overdue ? 'font-medium text-red-600' : 'text-slate-600'}`}>{fmtDate(i.due_date)}</td>
                  <td className="td text-slate-600">{SOURCE[i.source]}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {!loading && data.total === 0 && <Empty title={tab === 'review' ? 'Nothing to review' : 'No invoices here'}>
          Create invoices from approved orders, or upload PDFs from your billing software.</Empty>}
        <Pagination page={page} size={SIZE} total={data.total} onPage={setPage} />
      </div>

      <ImportDialog open={importOpen} onClose={() => setImportOpen(false)} title="Import invoices"
        uploadPath="/invoices/import" templatePath="/invoices/import/template" templateName="invoice_import_template.xlsx"
        columnsHint="One row per invoice. Saved straight away (not sent to buyers). Attach PDFs afterwards with Attach PDFs."
        onDone={load} />
      <AttachPdfs open={attachOpen} onClose={() => setAttachOpen(false)} onDone={load} />
    </>
  )
}

function AttachPdfs({ open, onClose, onDone }: { open: boolean; onClose: () => void; onDone: () => void }) {
  const [files, setFiles] = useState<FileList | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [result, setResult] = useState<{ matched: string[]; unmatched: string[] } | null>(null)
  const close = () => { setFiles(null); setResult(null); setError(null); onClose() }
  const submit = async () => {
    if (!files?.length) return
    setBusy(true); setError(null)
    try {
      const form = new FormData()
      Array.from(files).forEach((f) => form.append('files', f))
      setResult((await api.post('/invoices/attach-pdfs', form)).data)
      onDone()
    } catch (e) { setError(errorMessage(e)) } finally { setBusy(false) }
  }
  return (
    <Drawer open={open} title="Attach PDFs to saved invoices" onClose={close}
      footer={<><button className="btn-secondary" onClick={close}>Close</button>
        <button className="btn-primary" disabled={busy || !files?.length} onClick={submit}>{busy ? 'Uploading…' : 'Attach'}</button></>}>
      <ErrorBox message={error} />
      <p className="mb-3 text-sm text-slate-600">Name each file after its invoice number. For example <span className="font-mono">TI-2026-1234.pdf</span> is attached to invoice <span className="font-mono">TI/2026/1234</span>.</p>
      <input type="file" accept="application/pdf" multiple onChange={(e) => setFiles(e.target.files)} className="text-sm" />
      {result && (
        <div className="mt-4 space-y-2 text-sm">
          <div className="rounded-lg bg-pine-50 p-3 text-pine-800">Attached to {result.matched.length} invoice(s){result.matched.length ? `: ${result.matched.join(', ')}` : ''}</div>
          {result.unmatched.length > 0 && <div className="rounded-lg bg-amber-50 p-3 text-amber-900">No matching invoice for: {result.unmatched.join(', ')}</div>}
        </div>
      )}
    </Drawer>
  )
}
