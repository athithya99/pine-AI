import { useState, type FormEvent } from 'react'
import { Navigate } from 'react-router-dom'
import { ErrorBox } from '../components/ui'
import { errorMessage } from '../lib/api'
import { useAuth } from '../lib/auth'

export function Login() {
  const { me, loginWithPassword, requestOtp, verifyOtp } = useAuth()
  const [mode, setMode] = useState<'password' | 'otp'>('password')
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [mobile, setMobile] = useState('')
  const [code, setCode] = useState('')
  const [otpSent, setOtpSent] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  if (me) return <Navigate to="/" replace />

  const run = async (fn: () => Promise<void>) => {
    setBusy(true); setError(null)
    try { await fn() } catch (e) { setError(errorMessage(e)) } finally { setBusy(false) }
  }

  const submit = (e: FormEvent) => {
    e.preventDefault()
    if (mode === 'password') run(() => loginWithPassword(email, password))
    else if (!otpSent) run(async () => { await requestOtp(mobile); setOtpSent(true) })
    else run(() => verifyOtp(mobile, code))
  }

  return (
    <div className="grid min-h-screen md:grid-cols-2">
      <div className="hidden flex-col justify-between bg-pine-900 p-10 text-white md:flex">
        <div className="flex items-center gap-2">
          <div className="grid h-9 w-9 place-items-center rounded-lg bg-pine-600 font-bold">P</div>
          <span className="font-semibold">Pinesphere AI Commerce</span>
        </div>
        <div>
          <h1 className="text-3xl font-semibold leading-tight">Orders, invoices and payments<br />on WhatsApp — handled by AI.</h1>
          <p className="mt-3 max-w-md text-pine-100/80">Your buyers keep ordering the way they already do. Your team gets clean orders, faster collections and one view of every customer.</p>
        </div>
        <p className="text-xs text-pine-100/50">© Pinesphere Solutions Pvt. Ltd.</p>
      </div>

      <div className="flex items-center justify-center p-6">
        <form onSubmit={submit} className="w-full max-w-sm">
          <h2 className="text-xl font-semibold text-slate-900">Sign in</h2>
          <div className="mt-4 mb-5 grid grid-cols-2 rounded-lg bg-slate-100 p-1 text-sm">
            {(['password', 'otp'] as const).map((m) => (
              <button type="button" key={m} onClick={() => { setMode(m); setError(null) }}
                className={`rounded-md py-1.5 ${mode === m ? 'bg-white font-medium shadow-sm' : 'text-slate-500'}`}>
                {m === 'password' ? 'Email' : 'Mobile OTP'}
              </button>
            ))}
          </div>
          <ErrorBox message={error} />
          {mode === 'password' ? (
            <div className="space-y-3">
              <input className="input" type="email" placeholder="Email" value={email} onChange={(e) => setEmail(e.target.value)} required autoFocus />
              <input className="input" type="password" placeholder="Password" value={password} onChange={(e) => setPassword(e.target.value)} required />
            </div>
          ) : (
            <div className="space-y-3">
              <input className="input" placeholder="Mobile number" value={mobile} onChange={(e) => setMobile(e.target.value)} disabled={otpSent} required />
              {otpSent && (
                <>
                  <input className="input tracking-widest" placeholder="6-digit OTP" value={code} onChange={(e) => setCode(e.target.value)} inputMode="numeric" required autoFocus />
                  <button type="button" className="text-xs text-pine-700" onClick={() => { setOtpSent(false); setCode('') }}>Change number</button>
                </>
              )}
            </div>
          )}
          <button className="btn-primary mt-5 w-full" disabled={busy}>
            {busy ? 'Please wait…' : mode === 'otp' && !otpSent ? 'Send OTP' : 'Sign in'}
          </button>
        </form>
      </div>
    </div>
  )
}
