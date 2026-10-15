import { useState } from 'react'
import { api, download, errorMessage } from '../lib/api'
import type { ImportResult } from '../lib/types'
import { Drawer, ErrorBox } from './ui'

export function ImportDialog({ open, onClose, title, uploadPath, templatePath, templateName, columnsHint, onDone }: {
  open: boolean
  onClose: () => void
  title: string
  uploadPath: string
  templatePath?: string
  templateName?: string
  columnsHint?: string
  onDone: () => void
}) {
  const [file, setFile] = useState<File | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [result, setResult] = useState<ImportResult | null>(null)

  const close = () => { setFile(null); setResult(null); setError(null); onClose() }

  const submit = async () => {
    if (!file) return
    setBusy(true); setError(null)
    try {
      const form = new FormData()
      form.append('file', file)
      const { data } = await api.post<ImportResult>(uploadPath, form)
      setResult(data)
      onDone()
    } catch (e) {
      setError(errorMessage(e))
    } finally {
      setBusy(false)
    }
  }

  return (
    <Drawer open={open} title={title} onClose={close} footer={
      result
        ? <button className="btn-primary" onClick={close}>Done</button>
        : <>
            <button className="btn-secondary" onClick={close}>Cancel</button>
            <button className="btn-primary" disabled={!file || busy} onClick={submit}>{busy ? 'Importing…' : 'Import'}</button>
          </>
    }>
      <ErrorBox message={error} />
      {!result && (
        <div className="space-y-4">
          {templatePath && (
            <div className="rounded-lg bg-pine-50 p-3 text-sm text-pine-900">
              Use the template so the columns match. Columns marked * are required; the Instructions sheet explains each one.
              <button className="mt-2 block font-medium text-pine-700 underline" onClick={() => download(templatePath, templateName ?? 'template.xlsx')}>
                Download template
              </button>
            </div>
          )}
          {columnsHint && <div className="rounded-lg bg-pine-50 p-3 text-sm text-pine-900">{columnsHint}</div>}
          <input type="file" accept=".xlsx" className="input" onChange={(e) => setFile(e.target.files?.[0] ?? null)} />
          <p className="text-xs text-slate-500">Valid rows are imported. Rows with problems are skipped and listed so you can fix and upload them again.</p>
        </div>
      )}
      {result && (
        <div className="space-y-4">
          <div className="grid grid-cols-3 gap-3 text-center">
            <Stat label="Created" value={result.created} tone="text-pine-700" />
            <Stat label="Updated" value={result.updated} tone="text-slate-800" />
            <Stat label="Skipped" value={result.failed} tone={result.failed ? 'text-red-600' : 'text-slate-800'} />
          </div>
          {result.errors.length > 0 && (
            <div className="card overflow-hidden">
              <table className="w-full">
                <thead className="bg-slate-50"><tr><th className="th w-20">Row</th><th className="th">Problem</th></tr></thead>
                <tbody className="divide-y divide-slate-100">
                  {result.errors.map((e) => (
                    <tr key={e.row}><td className="td font-mono">{e.row}</td><td className="td text-red-700">{e.errors.join('; ')}</td></tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}
    </Drawer>
  )
}

function Stat({ label, value, tone }: { label: string; value: number; tone: string }) {
  return (
    <div className="card p-3">
      <div className={`text-2xl font-semibold ${tone}`}>{value}</div>
      <div className="text-xs text-slate-500">{label}</div>
    </div>
  )
}
