import { useEffect, useState } from 'react';
import { api, setAccessToken, getAccessToken } from '../services/api.js';

export function SignInGate({ onSignedIn, legacyGate }) {
  const [mode, setMode] = useState('login');
  const [status, setStatus] = useState(null);
  const [values, setValues] = useState({ email: '', password: '', name: '', token: getAccessToken() });
  const [error, setError] = useState(''); const [busy, setBusy] = useState(false);
  useEffect(() => { api.authStatus().then(setStatus).catch((err) => setError(err.message)); }, []);
  const set = (key, value) => setValues({ ...values, [key]: value });
  const submit = async (event) => {
    event.preventDefault(); setBusy(true); setError('');
    try {
      if (mode === 'setup') setAccessToken(values.token.trim());
      const result = mode === 'setup'
        ? await api.setupAccount({ email: values.email, password: values.password, name: values.name })
        : await api.login({ email: values.email, password: values.password });
      setAccessToken(result.token); onSignedIn();
    } catch (err) { if (mode === 'setup') setAccessToken(''); setError(err.message); }
    finally { setBusy(false); }
  };
  return <section className="card token-gate">
    <h1>{mode === 'setup' ? 'Set up your administrator account' : 'Sign in'}</h1>
    <p>Accounts keep customer workspaces separate and record who reviews each policy. Sessions expire after eight hours.</p>
    <form onSubmit={submit}>
      {mode === 'setup' && <><label className="field"><span>Existing administrator access token</span><input type="password" required value={values.token} onChange={(e) => set('token', e.target.value)} autoComplete="off" /></label>
        <label className="field"><span>Name</span><input required minLength={2} maxLength={100} value={values.name} onChange={(e) => set('name', e.target.value)} /></label></>}
      <label className="field"><span>Email</span><input type="email" required maxLength={254} value={values.email} onChange={(e) => set('email', e.target.value)} autoComplete="username" /></label>
      <label className="field"><span>Password (at least 12 characters)</span><input type="password" minLength={12} maxLength={128} required value={values.password} onChange={(e) => set('password', e.target.value)} autoComplete={mode === 'setup' ? 'new-password' : 'current-password'} /></label>
      {error && <p className="form-error" role="alert">{error}</p>}
      <button className="btn btn-primary" disabled={busy}>{busy ? 'Please wait…' : mode === 'setup' ? 'Create administrator account' : 'Sign in'}</button>
    </form>
    {status && !status.accounts_enabled && status.setup_available && <button className="btn" onClick={() => setMode(mode === 'setup' ? 'login' : 'setup')}>{mode === 'setup' ? 'Back to sign in' : 'First-time administrator setup'}</button>}
    {status && !status.accounts_enabled && legacyGate && <details><summary>Existing administrator access</summary>{legacyGate}</details>}
  </section>;
}
