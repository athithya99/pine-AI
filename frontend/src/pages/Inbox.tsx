import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { Drawer, Empty, ErrorBox, Field, useToast } from '../components/ui'
import { api, errorCode, errorMessage } from '../lib/api'
import { useAuth } from '../lib/auth'
import { isManager, type ChatMessage, type Conversation, type Page, type WaAccount, type WaTemplate } from '../lib/types'

const POLL_MS = 4000

function timeLabel(iso: string | null) {
  if (!iso) return ''
  const d = new Date(iso)
  const today = new Date()
  return d.toDateString() === today.toDateString()
    ? d.toLocaleTimeString('en-IN', { hour: '2-digit', minute: '2-digit' })
    : d.toLocaleDateString('en-IN', { day: 'numeric', month: 'short' })
}

function hoursLeft(iso: string | null) {
  if (!iso) return 0
  return Math.max(0, Math.round((new Date(iso).getTime() - Date.now()) / 36e5))
}

/** Loads a protected file with the auth header and shows it. */
export function AuthedMedia({ msg }: { msg: Pick<ChatMessage, 'id' | 'media_mime' | 'media_filename'> }) {
  const [url, setUrl] = useState<string | null>(null)
  useEffect(() => {
    let revoke: string | null = null
    api.get(`/media/${msg.id}`, { responseType: 'blob' }).then((r) => {
      revoke = URL.createObjectURL(r.data)
      setUrl(revoke)
    }).catch(() => {})
    return () => { if (revoke) URL.revokeObjectURL(revoke) }
  }, [msg.id])
  if (!url) return <div className="h-24 w-40 animate-pulse rounded-lg bg-black/5" />
  const mime = msg.media_mime ?? ''
  if (mime.startsWith('image/')) return <a href={url} target="_blank" rel="noreferrer"><img src={url} alt="" className="max-h-64 rounded-lg" /></a>
  if (mime.startsWith('audio/')) return <audio controls src={url} className="max-w-full" />
  return (
    <a href={url} download={msg.media_filename ?? 'file'} className="flex items-center gap-2 rounded-lg bg-black/5 px-3 py-2 text-sm underline">
      📄 {msg.media_filename ?? 'Document'}
    </a>
  )
}

function Ticks({ m, onRetry }: { m: ChatMessage; onRetry: () => void }) {
  if (m.direction === 'inbound') return null
  if (m.status === 'failed') {
    return (
      <span className="text-red-600" title={m.error_message ?? ''}>
        Failed{m.error_message ? `: ${m.error_message}` : ''} · <button className="underline" onClick={onRetry}>Retry</button>
      </span>
    )
  }
  const map: Record<string, [string, string]> = {
    queued: ['🕓', 'Sending'], sent: ['✓', 'Sent'], delivered: ['✓✓', 'Delivered'], read: ['✓✓', 'Read'],
  }
  const [mark, label] = map[m.status] ?? ['', m.status]
  return <span title={label} className={m.status === 'read' ? 'text-sky-500' : ''}>{mark}</span>
}

function Bubble({ m, onRetry }: { m: ChatMessage; onRetry: () => void }) {
  const out = m.direction === 'outbound'
  const p = m.payload ?? {}
  const buttons: string[] =
    p.interactive?.type === 'button' ? p.interactive.action.buttons.map((b: any) => b.reply.title)
      : m.msg_type === 'template' ? (p._buttons ?? []).map((b: any) => b.text) : []
  const rows: any[] = p.interactive?.type === 'list' ? p.interactive.action.sections.flatMap((s: any) => s.rows) : []
  const who = m.sender === 'bot' ? 'Bot' : m.sender === 'system' ? 'Automated' : null

  return (
    <div className={`flex ${out ? 'justify-end' : 'justify-start'}`}>
      <div className="max-w-[78%]">
        <div className={`rounded-2xl px-3.5 py-2 text-sm shadow-sm ${out ? 'rounded-br-md bg-pine-100 text-slate-900' : 'rounded-bl-md bg-white text-slate-900'}`}>
          {m.msg_type === 'template' && <div className="mb-1 text-[10px] font-semibold uppercase tracking-wide text-pine-700">Template</div>}
          {m.has_media && <div className="mb-1"><AuthedMedia msg={m} /></div>}
          {!m.has_media && ['image', 'audio', 'document', 'video'].includes(m.msg_type) && (
            <div className="text-xs italic text-slate-500">{m.error_message ?? 'Downloading media…'}</div>
          )}
          {m.msg_type === 'audio' && p.voice && <div className="text-xs text-slate-500">🎤 Voice note</div>}
          {p.transcript && <div className="mt-1 rounded-lg bg-slate-50 px-2 py-1 text-xs italic text-slate-600">“{p.transcript}”</div>}
          {(m.msg_type === 'interactive' || m.msg_type === 'button') && m.direction === 'inbound' && (
            <div className="text-[10px] font-semibold uppercase tracking-wide text-slate-400">Tapped</div>
          )}
          {m.text && <div className="whitespace-pre-wrap break-words">{m.text}</div>}
          {m.msg_type === 'unsupported' && <div className="italic text-slate-500">Unsupported message type</div>}
          {m.msg_type === 'location' && p.latitude && (
            <a className="text-xs underline" target="_blank" rel="noreferrer" href={`https://maps.google.com/?q=${p.latitude},${p.longitude}`}>Open in Maps</a>
          )}
          {rows.length > 0 && (
            <div className="mt-2 space-y-1 border-t border-black/10 pt-2 text-xs">
              <div className="font-medium">☰ {p.interactive.action.button}</div>
              {rows.map((r) => <div key={r.id} className="text-slate-600">• {r.title}{r.description && <span className="text-slate-400"> — {r.description}</span>}</div>)}
            </div>
          )}
          <div className="mt-1 flex items-center justify-end gap-1.5 text-[10px] text-slate-400">
            {who && <span>{who} ·</span>}
            {new Date(m.created_at).toLocaleTimeString('en-IN', { hour: '2-digit', minute: '2-digit' })}
            <Ticks m={m} onRetry={onRetry} />
          </div>
        </div>
        {buttons.length > 0 && (
          <div className="mt-1 grid gap-1">
            {buttons.map((b) => <div key={b} className="rounded-xl bg-white py-1.5 text-center text-sm text-sky-600 shadow-sm">{b}</div>)}
          </div>
        )}
      </div>
    </div>
  )
}

function TemplatePicker({ templates, onSend, busy }: {
  templates: WaTemplate[]; busy: boolean; onSend: (t: WaTemplate, params: string[]) => void
}) {
  const [id, setId] = useState('')
  const [params, setParams] = useState<string[]>([])
  const tpl = templates.find((t) => t.id === id)
  useEffect(() => { setParams(Array(tpl?.variable_count ?? 0).fill('')) }, [tpl])
  const preview = tpl ? tpl.body.replace(/\{\{(\d+)\}\}/g, (_, n) => params[Number(n) - 1] || `{{${n}}}`) : ''

  if (!templates.length) {
    return <p className="text-sm text-slate-500">No approved templates yet. Create one in <Link to="/whatsapp" className="text-pine-700 underline">Settings → WhatsApp</Link>.</p>
  }
  return (
    <div className="space-y-2">
      <select className="input" value={id} onChange={(e) => setId(e.target.value)}>
        <option value="">Choose an approved template…</option>
        {templates.map((t) => <option key={t.id} value={t.id}>{t.name} ({t.category.toLowerCase()})</option>)}
      </select>
      {tpl && (
        <>
          {params.map((v, i) => (
            <input key={i} className="input" placeholder={`Value for {{${i + 1}}}`} value={v}
              onChange={(e) => setParams((ps) => ps.map((p, j) => (j === i ? e.target.value : p)))} />
          ))}
          <div className="whitespace-pre-wrap rounded-lg bg-slate-50 p-2 text-xs text-slate-600">{preview}</div>
          <button className="btn-primary w-full" disabled={busy || params.some((p) => !p.trim())} onClick={() => onSend(tpl, params)}>
            Send template
          </button>
        </>
      )}
    </div>
  )
}

function SimulatorDrawer({ open, onClose, defaultNumber, lastButtons, onDone }: {
  open: boolean; onClose: () => void; defaultNumber: string; lastButtons: { id: string; title: string }[]; onDone: () => void
}) {
  const toast = useToast()
  const [number, setNumber] = useState(defaultNumber)
  const [name, setName] = useState('')
  const [text, setText] = useState('')
  const [file, setFile] = useState<File | null>(null)
  const [error, setError] = useState<string | null>(null)
  useEffect(() => { if (open) { setNumber(defaultNumber); setError(null) } }, [open, defaultNumber])

  const run = async (fn: () => Promise<unknown>) => {
    setError(null)
    try { await fn(); toast('Buyer message received'); setText(''); setFile(null); onDone() } catch (e) { setError(errorMessage(e)) }
  }
  const sendText = () => run(() => api.post('/simulator/inbound', { from_number: number, text, profile_name: name || null }))
  const sendFile = () => run(() => {
    const f = new FormData()
    f.append('from_number', number); f.append('file', file!)
    if (text) f.append('caption', text)
    if (name) f.append('profile_name', name)
    return api.post('/simulator/inbound-media', f)
  })
  const tap = (b: { id: string; title: string }) =>
    run(() => api.post('/simulator/inbound', { from_number: number, reply_id: b.id, reply_title: b.title }))

  return (
    <Drawer open={open} title="Simulate a buyer" onClose={onClose}>
      <p className="mb-4 rounded-lg bg-amber-50 p-3 text-sm text-amber-800">
        Test mode. Messages here go through the same pipeline as real WhatsApp webhooks, without a Meta number.
      </p>
      <ErrorBox message={error} />
      <div className="space-y-3">
        <Field label="Buyer's WhatsApp number" hint="Use a new number to see how unknown buyers become leads">
          <input className="input" value={number} onChange={(e) => setNumber(e.target.value)} />
        </Field>
        <Field label="Profile name (optional)"><input className="input" value={name} onChange={(e) => setName(e.target.value)} /></Field>
        <Field label="Message or caption">
          <textarea className="input" rows={3} value={text} onChange={(e) => setText(e.target.value)}
            placeholder="e.g. 20 boxes Tata salt, 10 bags atta" />
        </Field>
        <div className="flex gap-2">
          <button className="btn-primary" disabled={!number || !text.trim() || !!file} onClick={sendText}>Send text</button>
        </div>
        <div className="rounded-lg border border-dashed border-slate-300 p-3">
          <span className="label">Or send a photo, voice note or PDF</span>
          <input type="file" accept="image/*,audio/*,application/pdf" className="input" onChange={(e) => setFile(e.target.files?.[0] ?? null)} />
          <button className="btn-secondary mt-2" disabled={!number || !file} onClick={sendFile}>Send file</button>
        </div>
        {lastButtons.length > 0 && (
          <div>
            <span className="label">Tap a button from your last message</span>
            <div className="flex flex-wrap gap-2">
              {lastButtons.map((b) => <button key={b.id} className="btn-secondary" onClick={() => tap(b)}>{b.title}</button>)}
            </div>
          </div>
        )}
      </div>
    </Drawer>
  )
}

export function Inbox() {
  const { me } = useAuth()
  const toast = useToast()
  const canSend = me?.role !== 'accounts'
  const [params, setParams] = useSearchParams()
  const selectedId = params.get('c')

  const [account, setAccount] = useState<WaAccount | null>(null)
  const [convs, setConvs] = useState<Conversation[]>([])
  const [q, setQ] = useState('')
  const [unreadOnly, setUnreadOnly] = useState(false)
  const [attentionOnly, setAttentionOnly] = useState(false)
  const [current, setCurrent] = useState<Conversation | null>(null)
  const [msgs, setMsgs] = useState<ChatMessage[]>([])
  const [templates, setTemplates] = useState<WaTemplate[]>([])
  const [text, setText] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [showTemplates, setShowTemplates] = useState(false)
  const [simOpen, setSimOpen] = useState(false)
  const bottom = useRef<HTMLDivElement>(null)
  const fileInput = useRef<HTMLInputElement>(null)
  const lastCount = useRef(0)

  const loadConvs = useCallback(async () => {
    const { data } = await api.get<Page<Conversation>>('/inbox/conversations', {
      params: { q: q || undefined, unread: unreadOnly || undefined, attention: attentionOnly || undefined, size: 100 },
    })
    setConvs(data.items)
  }, [q, unreadOnly, attentionOnly])

  const loadThread = useCallback(async () => {
    if (!selectedId) return
    const [c, m] = await Promise.all([
      api.get<Conversation>(`/inbox/conversations/${selectedId}`),
      api.get<ChatMessage[]>(`/inbox/conversations/${selectedId}/messages`),
    ])
    setCurrent(c.data)
    setMsgs(m.data)
  }, [selectedId])

  useEffect(() => {
    api.get<WaAccount>('/whatsapp/account').then((r) => setAccount(r.data)).catch(() => {})
    api.get<WaTemplate[]>('/whatsapp/templates', { params: { approved: true } }).then((r) => setTemplates(r.data)).catch(() => {})
  }, [])
  useEffect(() => { loadConvs().catch(() => {}) }, [loadConvs])
  useEffect(() => { setMsgs([]); setCurrent(null); setError(null); setShowTemplates(false); lastCount.current = 0; loadThread().catch(() => {}) }, [loadThread])
  useEffect(() => {
    const t = setInterval(() => { loadConvs().catch(() => {}); loadThread().catch(() => {}) }, POLL_MS)
    return () => clearInterval(t)
  }, [loadConvs, loadThread])
  useEffect(() => {
    if (msgs.length !== lastCount.current) { bottom.current?.scrollIntoView({ behavior: 'smooth' }); lastCount.current = msgs.length }
  }, [msgs])
  useEffect(() => { if (current && !current.window_open) setShowTemplates(true) }, [current?.id, current?.window_open])

  const refresh = () => { loadThread().catch(() => {}); loadConvs().catch(() => {}) }

  const send = async (body: object) => {
    if (!current) return
    setBusy(true); setError(null)
    try {
      await api.post(`/inbox/conversations/${current.id}/messages`, body)
      setText('')
      refresh()
    } catch (e) {
      setError(errorMessage(e))
      if (errorCode(e) === 'window_closed') setShowTemplates(true)
    } finally { setBusy(false) }
  }

  const sendFile = async (file: File) => {
    if (!current) return
    setBusy(true); setError(null)
    const f = new FormData()
    f.append('file', file)
    if (text.trim()) f.append('caption', text.trim())
    try { await api.post(`/inbox/conversations/${current.id}/media`, f); setText(''); refresh() }
    catch (e) { setError(errorMessage(e)) } finally { setBusy(false) }
  }

  const toggleBot = async () => {
    if (!current) return
    const { data } = await api.patch<Conversation>(`/inbox/conversations/${current.id}`, { bot_enabled: !current.bot_enabled })
    setCurrent(data)
    toast(data.bot_enabled ? 'Bot switched on for this chat' : 'You have taken over this chat')
  }

  const resolve = async () => {
    if (!current) return
    const { data } = await api.patch<Conversation>(`/inbox/conversations/${current.id}`, { bot_enabled: current.bot_enabled, resolved: true })
    setCurrent(data)
    toast('Marked as handled')
  }

  const retry = async (m: ChatMessage) => {
    try { await api.post(`/inbox/messages/${m.id}/retry`); refresh() } catch (e) { toast(errorMessage(e), 'error') }
  }

  const lastButtons = useMemo(() => {
    const last = [...msgs].reverse().find((m) => m.direction === 'outbound')
    const p = last?.payload
    if (p?.interactive?.type === 'button') return p.interactive.action.buttons.map((b: any) => b.reply)
    if (p?.interactive?.type === 'list') return p.interactive.action.sections.flatMap((s: any) => s.rows).map((r: any) => ({ id: r.id, title: r.title }))
    if (last?.msg_type === 'template') return (p?._buttons ?? []).map((b: any) => ({ id: b.payload ?? b.text, title: b.text }))
    return []
  }, [msgs])

  const simulator = account?.mode === 'simulator' && account.simulator_enabled && isManager(me?.role)

  if (account && !account.connected) {
    return (
      <div className="card mx-auto mt-10 max-w-lg p-8 text-center">
        <h1 className="text-lg font-semibold">Connect WhatsApp to start</h1>
        <p className="mt-2 text-sm text-slate-500">Buyer messages will appear here once a WhatsApp number is connected.</p>
        {isManager(me?.role) && <Link to="/whatsapp" className="btn-primary mt-5">Go to WhatsApp settings</Link>}
      </div>
    )
  }

  return (
    <div className="-mx-4 -my-6 flex h-[calc(100vh-52px)] md:-mx-8 md:h-screen">
      {/* conversation list */}
      <div className={`w-full flex-col border-r border-slate-200 bg-white md:flex md:w-80 ${selectedId ? 'hidden' : 'flex'}`}>
        <div className="border-b border-slate-200 p-3">
          <div className="mb-2 flex items-center justify-between">
            <h1 className="text-lg font-semibold">Inbox</h1>
            {simulator && <button className="btn-secondary px-2.5 py-1 text-xs" onClick={() => setSimOpen(true)}>Simulate buyer</button>}
          </div>
          <input className="input" placeholder="Search name or number" value={q} onChange={(e) => setQ(e.target.value)} />
          <div className="mt-2 flex gap-4">
            <label className="flex items-center gap-2 text-xs text-slate-600">
              <input type="checkbox" checked={unreadOnly} onChange={(e) => setUnreadOnly(e.target.checked)} /> Unread only
            </label>
            <label className="flex items-center gap-2 text-xs text-slate-600" title="Chats the bot handed over: buyer asked for a person, a complaint, or a question it couldn't answer">
              <input type="checkbox" checked={attentionOnly} onChange={(e) => setAttentionOnly(e.target.checked)} /> Needs a person
            </label>
          </div>
        </div>
        <div className="flex-1 overflow-y-auto">
          {convs.map((c) => (
            <button key={c.id} onClick={() => setParams({ c: c.id })}
              className={`flex w-full gap-3 border-b border-slate-100 px-3 py-3 text-left hover:bg-slate-50 ${c.id === selectedId ? 'bg-pine-50' : ''}`}>
              <div className="grid h-10 w-10 shrink-0 place-items-center rounded-full bg-pine-100 text-sm font-semibold text-pine-700">
                {c.customer_name.slice(0, 1).toUpperCase()}
              </div>
              <div className="min-w-0 flex-1">
                <div className="flex items-center justify-between gap-2">
                  <span className={`truncate text-sm ${c.unread_count ? 'font-semibold text-slate-900' : 'font-medium text-slate-800'}`}>{c.customer_name}</span>
                  <span className="shrink-0 text-[11px] text-slate-400">{timeLabel(c.last_message_at)}</span>
                </div>
                <div className="flex items-center justify-between gap-2">
                  <span className="truncate text-xs text-slate-500">{c.last_message_preview}</span>
                  {c.unread_count > 0 && <span className="badge bg-pine-600 text-white">{c.unread_count}</span>}
                </div>
                {c.customer_status === 'lead' && <span className="badge mt-1 bg-amber-50 text-amber-700">New lead</span>}
                {c.handover_at && <span className="badge mt-1 bg-amber-100 text-amber-800" title={c.handover_reason ?? ''}>Needs a person</span>}
              </div>
            </button>
          ))}
          {convs.length === 0 && <Empty title="No conversations yet">{simulator ? 'Use “Simulate buyer” to send a test message.' : 'Messages from buyers will show up here.'}</Empty>}
        </div>
      </div>

      {/* thread */}
      <div className={`min-w-0 flex-1 flex-col bg-[#efeae2] ${selectedId ? 'flex' : 'hidden md:flex'}`}>
        {!current ? (
          <div className="grid flex-1 place-items-center text-sm text-slate-500">Select a conversation</div>
        ) : (
          <>
            <div className="flex flex-wrap items-center gap-3 border-b border-slate-200 bg-white px-4 py-2.5">
              <button className="md:hidden" onClick={() => setParams({})} aria-label="Back">←</button>
              <div className="min-w-0 flex-1">
                <div className="truncate font-medium">{current.customer_name}</div>
                <div className="text-xs text-slate-500">{current.whatsapp_number}</div>
              </div>
              {current.opted_out ? <span className="badge bg-red-50 text-red-700">Opted out</span>
                : current.window_open ? <span className="badge bg-pine-50 text-pine-700" title="You can send free-form messages">Window open · {hoursLeft(current.window_closes_at)}h left</span>
                  : <span className="badge bg-amber-50 text-amber-700" title="Only approved templates can be sent">Window closed · templates only</span>}
              {canSend && (
                <button onClick={toggleBot} className={`badge cursor-pointer ${current.bot_enabled ? 'bg-sky-50 text-sky-700' : 'bg-slate-800 text-white'}`}
                  title="Turn off to stop automatic replies while you handle this chat">
                  {current.bot_enabled ? 'Bot on' : 'You are handling'}
                </button>
              )}
              {simulator && <button className="btn-secondary px-2.5 py-1 text-xs" onClick={() => setSimOpen(true)}>Reply as buyer</button>}
            </div>
            {current.handover_at && (
              <div className="flex flex-wrap items-center gap-2 border-b border-amber-200 bg-amber-50 px-4 py-2 text-sm text-amber-900">
                <span className="flex-1">Bot handed this chat to your team: {current.handover_reason}. It stays off until you switch it back on.</span>
                {canSend && <button className="btn-secondary px-2.5 py-1 text-xs" onClick={resolve}>Mark handled</button>}
              </div>
            )}

            <div className="flex-1 space-y-2 overflow-y-auto px-3 py-4 md:px-8">
              {msgs.map((m) => <Bubble key={m.id} m={m} onRetry={() => retry(m)} />)}
              {msgs.length === 0 && <div className="text-center text-sm text-slate-500">No messages yet</div>}
              <div ref={bottom} />
            </div>

            {canSend && !current.opted_out && (
              <div className="border-t border-slate-200 bg-white p-3">
                <ErrorBox message={error} />
                {showTemplates ? (
                  <div>
                    <div className="mb-2 flex items-center justify-between">
                      <span className="text-xs text-slate-500">
                        {current.window_open ? 'Send a template' : 'More than 24 hours since the buyer last wrote. Only approved templates can be sent.'}
                      </span>
                      {current.window_open && <button className="text-xs text-pine-700" onClick={() => setShowTemplates(false)}>Type a message instead</button>}
                    </div>
                    <TemplatePicker key={msgs.length} templates={templates} busy={busy}
                      onSend={(t, p) => send({ kind: 'template', template_id: t.id, template_params: p })} />
                  </div>
                ) : (
                  <div className="flex items-end gap-2">
                    <button className="btn-secondary px-2.5" title="Attach JPG, PNG or PDF" onClick={() => fileInput.current?.click()} disabled={busy}>📎</button>
                    <input ref={fileInput} type="file" className="hidden" accept="image/jpeg,image/png,application/pdf"
                      onChange={(e) => { const f = e.target.files?.[0]; e.target.value = ''; if (f) sendFile(f) }} />
                    <textarea className="input min-h-[42px] flex-1 resize-none" rows={1} placeholder="Type a message" value={text}
                      onChange={(e) => setText(e.target.value)}
                      onKeyDown={(e) => { if (e.key === 'Enter' && !e.shiftKey && text.trim()) { e.preventDefault(); send({ kind: 'text', text }) } }} />
                    <button className="btn-secondary px-2.5" title="Templates" onClick={() => setShowTemplates(true)}>⚡</button>
                    <button className="btn-primary" disabled={busy || !text.trim()} onClick={() => send({ kind: 'text', text })}>Send</button>
                  </div>
                )}
              </div>
            )}
            {current.opted_out && <div className="border-t border-slate-200 bg-white p-3 text-center text-sm text-slate-500">This buyer opted out. They need to send START before you can message them.</div>}
          </>
        )}
      </div>

      {simulator && (
        <SimulatorDrawer open={simOpen} onClose={() => setSimOpen(false)} defaultNumber={current?.whatsapp_number ?? '+919811111111'}
          lastButtons={lastButtons} onDone={refresh} />
      )}
    </div>
  )
}
