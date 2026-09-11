import { useState, type FormEvent } from 'react'
import { Check, Eye, EyeOff, Loader2 } from 'lucide-react'
import { StationBrand, StationThemeButton } from '@/components/station/StationBrand'

const passwordRules = [
  { label: '8 or more characters', check: (value: string) => value.length >= 8 },
  { label: 'Uppercase and lowercase letters', check: (value: string) => /[A-Z]/.test(value) && /[a-z]/.test(value) },
  { label: 'A number', check: (value: string) => /\d/.test(value) },
  { label: 'A special character', check: (value: string) => /[!@#$%^&*(),.?":{}|<>]/.test(value) },
]

export function StationAuth({ onAuthenticated, setup = false }: { onAuthenticated: (token: string) => void; setup?: boolean }) {
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [confirmation, setConfirmation] = useState('')
  const [visible, setVisible] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const valid = !!username.trim() && !!password && (!setup || (username.trim().length >= 3 && passwordRules.every(rule => rule.check(password)) && password === confirmation))

  async function submit(event: FormEvent) {
    event.preventDefault()
    if (busy || !valid) return
    setBusy(true); setError('')
    try {
      const response = await fetch(`/api/v1/auth/${setup ? 'register' : 'login'}`, {
        method: 'POST', credentials: 'same-origin', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ username: username.trim(), password, ...(setup ? { confirmPassword: confirmation } : {}) }),
        signal: AbortSignal.timeout(20000),
      })
      const body = await response.json().catch(() => ({}))
      if (!response.ok) throw new Error(typeof body.error === 'string' ? body.error : 'Your station could not complete sign-in. Please try again.')
      if (typeof body.token !== 'string' || !body.token) throw new Error('Your station returned an incomplete sign-in response. Please try again.')
      onAuthenticated(body.token)
    } catch (cause) {
      setError(cause instanceof Error && cause.name !== 'TimeoutError' && cause.name !== 'TypeError' ? cause.message : 'The station could not be reached. Check your connection and try again.')
    } finally { setBusy(false) }
  }

  return <div className="ms-auth">
    <header className="ms-auth-header"><StationBrand /><StationThemeButton /></header>
    <main className="ms-auth-main">
      <section className="ms-auth-entry" aria-labelledby="ms-auth-title">
        <div className="ms-auth-panel">
          <h1 id="ms-auth-title">{setup ? 'Set up station' : 'Sign in'}</h1>
          <p className="ms-auth-description">{setup ? 'Create the administrator account for this station.' : 'Use the station administrator account.'}</p>
          <form className="ms-auth-form" onSubmit={submit} aria-busy={busy}>
            <div className="ms-auth-field"><label htmlFor="ms-username">Username</label><input id="ms-username" autoComplete="username" autoCapitalize="none" spellCheck={false} required minLength={setup ? 3 : undefined} maxLength={setup ? 50 : undefined} placeholder={setup ? 'Choose a username' : 'Your station username'} value={username} onChange={event => setUsername(event.target.value)} disabled={busy} /></div>
            <div className="ms-auth-field"><label htmlFor="ms-password">Password</label><div className="ms-password-field"><input id="ms-password" type={visible ? 'text' : 'password'} autoComplete={setup ? 'new-password' : 'current-password'} required placeholder={setup ? 'Create a password' : 'Your password'} value={password} onChange={event => setPassword(event.target.value)} disabled={busy} /><button type="button" onClick={() => setVisible(value => !value)} aria-label={visible ? 'Hide password' : 'Show password'} aria-pressed={visible} disabled={busy}>{visible ? <EyeOff /> : <Eye />}</button></div></div>
            {setup && <><ul className="ms-password-rules" aria-label="Password requirements">{passwordRules.map(rule => <li key={rule.label} className={rule.check(password) ? 'is-met' : ''}><Check />{rule.label}</li>)}</ul><div className="ms-auth-field"><label htmlFor="ms-confirmation">Confirm password</label><input id="ms-confirmation" type={visible ? 'text' : 'password'} autoComplete="new-password" required value={confirmation} onChange={event => setConfirmation(event.target.value)} disabled={busy} aria-describedby={confirmation && confirmation !== password ? 'ms-password-mismatch' : undefined} />{confirmation && confirmation !== password && <small id="ms-password-mismatch">Passwords don’t match yet.</small>}</div></>}
            {error && <div className="ms-auth-error" role="alert">{error}</div>}
            <button className="ms-auth-submit" type="submit" disabled={busy || !valid}>{busy ? <><Loader2 className="animate-spin" />{setup ? 'Setting up…' : 'Signing in…'}</> : setup ? 'Create station account' : 'Sign in'}</button>
          </form>
        </div>
      </section>
    </main>
  </div>
}
