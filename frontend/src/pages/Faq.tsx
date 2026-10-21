import { useEffect, useState } from 'react'
import { Drawer, Empty, ErrorBox, Field, PageHeader, useToast } from '../components/ui'
import { api, errorMessage } from '../lib/api'
import type { Faq } from '../lib/types'

const EMPTY = { question: '', answer: '', answer_ta: '', answer_hi: '', keywords: '', is_active: true }

export function FaqPage() {
  const toast = useToast()
  const [faqs, setFaqs] = useState<Faq[]>([])
  const [editing, setEditing] = useState<Faq | null>(null)
  const [open, setOpen] = useState(false)
  const [form, setForm] = useState(EMPTY)
  const [error, setError] = useState<string | null>(null)
  const [test, setTest] = useState('')
  const [result, setResult] = useState<{ question: string | null; answer: string | null } | null>(null)

  const load = () => api.get<Faq[]>('/faqs').then((r) => setFaqs(r.data))
  useEffect(() => { load() }, [])

  const edit = (f: Faq | null) => {
    setEditing(f); setError(null)
    setForm(f ? { question: f.question, answer: f.answer, answer_ta: f.answer_ta ?? '', answer_hi: f.answer_hi ?? '', keywords: f.keywords.join(', '), is_active: f.is_active } : EMPTY)
    setOpen(true)
  }
  const save = async () => {
    const body = { ...form, keywords: form.keywords.split(',').map((k) => k.trim()).filter(Boolean), answer_ta: form.answer_ta || null, answer_hi: form.answer_hi || null }
    try {
      if (editing) await api.put(`/faqs/${editing.id}`, body); else await api.post('/faqs', body)
      setOpen(false); load(); toast('Saved')
    } catch (e) { setError(errorMessage(e)) }
  }
  const remove = async (f: Faq) => {
    if (!window.confirm('Delete this answer?')) return
    try { await api.delete(`/faqs/${f.id}`); load() } catch (e) { toast(errorMessage(e), 'error') }
  }
  const tryIt = async () => {
    try { setResult((await api.post('/faqs/test', { text: test })).data) } catch (e) { toast(errorMessage(e), 'error') }
  }

  return (
    <>
      <PageHeader title="Bot FAQ" subtitle="General questions the WhatsApp bot answers for buyers: delivery areas, timings, returns, minimum order…"
        actions={<button className="btn-primary" onClick={() => edit(null)}>Add answer</button>} />
      <div className="grid gap-6 lg:grid-cols-[1fr_320px]">
        <div className="card divide-y divide-slate-100">
          {faqs.length === 0 && <Empty title="No answers yet">Add the questions buyers ask most. When the bot can't answer something, it hands the chat to your team.</Empty>}
          {faqs.map((f) => (
            <div key={f.id} className={`p-4 ${f.is_active ? '' : 'opacity-60'}`}>
              <div className="flex items-start justify-between gap-3">
                <div className="min-w-0">
                  <div className="font-medium">{f.question}</div>
                  <p className="mt-1 whitespace-pre-wrap text-sm text-slate-600">{f.answer}</p>
                  <div className="mt-1.5 flex flex-wrap gap-1.5 text-xs text-slate-500">
                    {f.answer_ta && <span className="badge bg-slate-100">Tamil</span>}
                    {f.answer_hi && <span className="badge bg-slate-100">Hindi</span>}
                    {f.keywords.map((k) => <span key={k} className="badge bg-pine-50 text-pine-700">{k}</span>)}
                    <span>Used {f.times_used}×</span>
                  </div>
                </div>
                <div className="flex shrink-0 gap-3 text-xs"><button className="text-pine-700 hover:underline" onClick={() => edit(f)}>Edit</button>
                  <button className="text-red-600 hover:underline" onClick={() => remove(f)}>Delete</button></div>
              </div>
            </div>
          ))}
        </div>
        <div className="card h-fit p-4">
          <h2 className="font-medium">Try it</h2>
          <p className="mt-0.5 text-xs text-slate-500">Type a buyer's message to see which answer the bot would send.</p>
          <div className="mt-3 flex gap-2"><input className="input" value={test} placeholder="do you deliver to Erode?" onChange={(e) => setTest(e.target.value)} />
            <button className="btn-secondary" disabled={!test.trim()} onClick={tryIt}>Test</button></div>
          {result && <div className="mt-3 rounded-lg bg-slate-50 p-3 text-sm">{result.answer
            ? <><div className="text-xs text-slate-500">Matches "{result.question}"</div><p className="mt-1 whitespace-pre-wrap">{result.answer}</p></>
            : <p className="text-slate-600">No answer matches. The bot would hand this chat to your team.</p>}</div>}
        </div>
      </div>
      <Drawer open={open} title={editing ? 'Edit answer' : 'Add answer'} onClose={() => setOpen(false)}
        footer={<><button className="btn-secondary" onClick={() => setOpen(false)}>Cancel</button><button className="btn-primary" onClick={save}>Save</button></>}>
        <ErrorBox message={error} />
        <div className="space-y-3">
          <Field label="Question"><input className="input" value={form.question} placeholder="What are your delivery areas?" onChange={(e) => setForm({ ...form, question: e.target.value })} /></Field>
          <Field label="Answer (English)"><textarea className="input" rows={3} value={form.answer} onChange={(e) => setForm({ ...form, answer: e.target.value })} /></Field>
          <Field label="Answer in Tamil (optional)" hint="Sent when the buyer writes in Tamil"><textarea className="input" rows={3} value={form.answer_ta} onChange={(e) => setForm({ ...form, answer_ta: e.target.value })} /></Field>
          <Field label="Answer in Hindi (optional)" hint="Sent when the buyer writes in Hindi"><textarea className="input" rows={3} value={form.answer_hi} onChange={(e) => setForm({ ...form, answer_hi: e.target.value })} /></Field>
          <Field label="Other words buyers use" hint="Comma separated, e.g. deliver, delivery, area, town"><input className="input" value={form.keywords} onChange={(e) => setForm({ ...form, keywords: e.target.value })} /></Field>
          <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={form.is_active} onChange={(e) => setForm({ ...form, is_active: e.target.checked })} /> Active</label>
        </div>
      </Drawer>
    </>
  )
}
