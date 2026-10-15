import { useCallback, useEffect, useState } from 'react'
import { Drawer, ErrorBox, Field, PageHeader, useToast } from '../components/ui'
import { api, errorMessage } from '../lib/api'
import { useAuth } from '../lib/auth'
import { ROLE_LABEL, type Role, type User } from '../lib/types'

const ROLES: Role[] = ['owner', 'admin', 'sales', 'accounts']
const ROLE_HELP: Record<string, string> = {
  owner: 'Everything, including adding other owners',
  admin: 'Everything except changing owner accounts',
  sales: 'Only their assigned customers; can create orders',
  accounts: 'Invoices, payments and ledgers for all customers',
}

export function Users() {
  const { me } = useAuth()
  const toast = useToast()
  const [users, setUsers] = useState<User[]>([])
  const [editing, setEditing] = useState<User | 'new' | null>(null)
  const [form, setForm] = useState({ name: '', email: '', mobile: '', password: '', role: 'sales' as Role })
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  const load = useCallback(() => { api.get<User[]>('/users').then((r) => setUsers(r.data)).catch(() => {}) }, [])
  useEffect(load, [load])

  const open = (u: User | 'new') => {
    setEditing(u); setError(null)
    setForm(u === 'new' ? { name: '', email: '', mobile: '', password: '', role: 'sales' }
      : { name: u.name, email: u.email ?? '', mobile: u.mobile ?? '', password: '', role: u.role })
  }

  const save = async () => {
    setBusy(true); setError(null)
    try {
      if (editing === 'new') {
        await api.post('/users', {
          name: form.name, email: form.email || null, mobile: form.mobile || null,
          password: form.password || null, role: form.role,
        })
        toast('Team member added')
      } else if (editing) {
        await api.patch(`/users/${editing.id}`, { name: form.name, email: form.email || null, mobile: form.mobile || null, role: form.role })
        toast('Team member updated')
      }
      setEditing(null); load()
    } catch (e) { setError(errorMessage(e)) } finally { setBusy(false) }
  }

  const toggleActive = async (u: User) => {
    try { await api.patch(`/users/${u.id}`, { is_active: !u.is_active }); setEditing(null); load() }
    catch (e) { setError(errorMessage(e)) }
  }

  const roles = me?.role === 'owner' ? ROLES : ROLES.filter((r) => r !== 'owner')

  return (
    <>
      <PageHeader title="Team" subtitle="People in your company who use the panel." actions={<button className="btn-primary" onClick={() => open('new')}>Add member</button>} />
      <div className="card overflow-x-auto">
        <table className="w-full min-w-[640px]">
          <thead className="bg-slate-50"><tr><th className="th">Name</th><th className="th">Login</th><th className="th">Role</th><th className="th">Status</th><th className="th">Last login</th></tr></thead>
          <tbody className="divide-y divide-slate-100">
            {users.map((u) => (
              <tr key={u.id} className="cursor-pointer hover:bg-slate-50" onClick={() => open(u)}>
                <td className="td font-medium">{u.name}{u.id === me?.id && <span className="ml-1 text-xs text-slate-400">(you)</span>}</td>
                <td className="td text-xs">{u.email}<div className="text-slate-500">{u.mobile}</div></td>
                <td className="td">{ROLE_LABEL[u.role]}</td>
                <td className="td">{u.is_active ? <span className="badge bg-pine-50 text-pine-700">Active</span> : <span className="badge bg-slate-100 text-slate-500">Inactive</span>}</td>
                <td className="td text-xs text-slate-500">{u.last_login_at ? new Date(u.last_login_at).toLocaleString('en-IN') : 'Never'}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <Drawer open={editing !== null} title={editing === 'new' ? 'Add team member' : 'Edit team member'} onClose={() => setEditing(null)}
        footer={<>
          {editing && editing !== 'new' && editing.id !== me?.id && (
            <button className="btn-danger mr-auto" onClick={() => toggleActive(editing)}>{editing.is_active ? 'Deactivate' : 'Reactivate'}</button>
          )}
          <button className="btn-secondary" onClick={() => setEditing(null)}>Cancel</button>
          <button className="btn-primary" disabled={busy} onClick={save}>Save</button>
        </>}>
        <ErrorBox message={error} />
        <div className="space-y-3">
          <Field label="Name *"><input className="input" value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} /></Field>
          <Field label="Mobile" hint="Used for OTP login"><input className="input" value={form.mobile} onChange={(e) => setForm({ ...form, mobile: e.target.value })} /></Field>
          <Field label="Email"><input className="input" type="email" value={form.email} onChange={(e) => setForm({ ...form, email: e.target.value })} /></Field>
          {editing === 'new' && (
            <Field label="Password" hint="Needed only if they will sign in with email; at least 8 characters">
              <input className="input" type="password" value={form.password} onChange={(e) => setForm({ ...form, password: e.target.value })} />
            </Field>
          )}
          <div>
            <span className="label">Role</span>
            <div className="space-y-2">
              {roles.map((r) => (
                <label key={r} className={`flex cursor-pointer items-start gap-2 rounded-lg border p-3 text-sm ${form.role === r ? 'border-pine-500 bg-pine-50' : 'border-slate-200'}`}>
                  <input type="radio" className="mt-0.5" checked={form.role === r} onChange={() => setForm({ ...form, role: r })} />
                  <span><span className="font-medium">{ROLE_LABEL[r]}</span><span className="block text-xs text-slate-500">{ROLE_HELP[r]}</span></span>
                </label>
              ))}
            </div>
          </div>
        </div>
      </Drawer>
    </>
  )
}
