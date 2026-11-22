import { SignInGate } from '../components/SignInGate.jsx';
import { useState } from 'react';
import { useSystem } from '../context/SystemContext.jsx';
import { useAsync } from '../hooks/useAsync.js';
import { api, setAccessToken } from '../services/api.js';
import { ErrorState, LoadingBlock, PageHeader } from '../components/ui.jsx';

export default function AccountPage() {
  const { user, health, config, reload: reloadSystem } = useSystem();
  const { data, error, loading, reload } = useAsync(() => user?.role === 'admin' && !health?.public_access ? api.accounts() : Promise.resolve({ accounts: [] }), [user?.role, health?.public_access]);
  const [values, setValues] = useState({ email: '', name: '', password: '', role: 'viewer', new_workspace: false });
  const [passwords, setPasswords] = useState({ current_password: '', new_password: '' });
  const [message, setMessage] = useState(''); const [busy, setBusy] = useState(false);
  const set = (key, value) => setValues({ ...values, [key]: value });
  const create = async (event) => {
    event.preventDefault(); setBusy(true); setMessage('');
    try { await api.createAccount(values); setValues({ email: '', name: '', password: '', role: 'viewer', new_workspace: false }); setMessage('Account created. Share its sign-in details through your approved secure channel.'); reload(); }
    catch (err) { setMessage(err.message); } finally { setBusy(false); }
  };
  if (health?.public_access) return <><PageHeader title="System status" subtitle="Policy workspace" /><section className="card"><p>Database: {health.database}. Provider: {health.llm?.service_name}.</p>{health.llm?.key_status && <ul>{health.llm.key_status.map((key) => <li key={key.key_slot}>{key.provider}: {key.available ? 'Ready to try' : key.retry_after_seconds == null ? 'Unavailable' : `Next eligibility check in ${Math.ceil(key.retry_after_seconds / 60)} minute(s)`}</li>)}</ul>}<button className="btn" onClick={reloadSystem}>Refresh status</button></section></>;
  return <><PageHeader title="Account & administration" subtitle="Manage access and view operational information." />
    {!user && <SignInGate onSignedIn={reloadSystem} />}
    {user && <p>Signed in as {user.name} ({user.role}).</p>}
    {user && <form className="card" onSubmit={async (event) => { event.preventDefault(); setBusy(true); setMessage(''); try { const result = await api.changePassword(passwords); setAccessToken(result.token); setPasswords({ current_password: '', new_password: '' }); setMessage('Password changed. Other sessions were signed out.'); } catch (err) { setMessage(err.message); } finally { setBusy(false); } }}><h2>Change your password</h2><div className="form-grid">{[['current_password', 'Current password'], ['new_password', 'New password']].map(([key, label]) => <label className="field" key={key}><span>{label}</span><input type="password" required minLength={12} maxLength={128} autoComplete={key === 'current_password' ? 'current-password' : 'new-password'} value={passwords[key]} onChange={(e) => setPasswords({ ...passwords, [key]: e.target.value })} /></label>)}</div><button className="btn" disabled={busy}>Change password</button></form>}
    {user?.role === 'admin' && <>
      <section className="card"><h2>Workspace accounts</h2>{loading && <LoadingBlock />}{error && <ErrorState message={error.message} onRetry={reload} />}
        <ul>{data?.accounts.map((account) => <li key={account.id}>{account.name} — {account.email} — {account.role} {account.active ? '' : '(disabled)'}
          {account.id !== user.id && account.active && <button className="btn btn-small" disabled={busy} onClick={async () => { setBusy(true); try { await api.disableAccount(account.id); reload(); } catch (err) { setMessage(err.message); } finally { setBusy(false); } }}>Disable access</button>}</li>)}</ul>
      </section>
      <form className="card" onSubmit={create}><h2>Add an account</h2><div className="form-grid">
        <label className="field"><span>Name</span><input required minLength={2} maxLength={100} value={values.name} onChange={(e) => set('name', e.target.value)} /></label>
        <label className="field"><span>Email</span><input type="email" required value={values.email} onChange={(e) => set('email', e.target.value)} /></label>
        <label className="field"><span>Initial password</span><input type="password" autoComplete="new-password" minLength={12} maxLength={128} required value={values.password} onChange={(e) => set('password', e.target.value)} /></label>
        <label className="field"><span>Permission</span><select value={values.role} onChange={(e) => set('role', e.target.value)}>{['viewer', 'editor', 'reviewer', 'admin'].map((role) => <option key={role}>{role}</option>)}</select></label>
        <label><input type="checkbox" checked={values.new_workspace} onChange={(e) => set('new_workspace', e.target.checked)} /> Create an isolated customer workspace (starts empty)</label>
      </div><p className="small">Viewers read and download; editors prepare drafts; reviewers record decisions; administrators manage access and can approve. A new customer workspace should have its own administrator.</p>
        <button className="btn btn-primary" disabled={busy}>Create account</button></form>
      <section className="card"><h2>System status</h2><p>Database: {health?.database}. Provider: {health?.llm?.service_name} / {health?.llm?.model}. OCR: {health?.ocr?.engine}.</p>{health?.llm?.key_status && <><h3>Provider availability</h3><ul>{health.llm.key_status.map((key) => <li key={key.key_slot}>{key.provider} / {key.model}: {key.available ? 'Ready to try' : key.retry_after_seconds == null ? 'Unavailable — check credentials or model access' : `Waiting for quota or recovery; next eligibility check in ${Math.ceil(key.retry_after_seconds / 60)} minute(s)`}</li>)}</ul><button className="btn btn-small" onClick={reloadSystem}>Refresh provider status</button><p className="small">Availability reflects recent API errors and reset times; it is not a remaining-token balance.</p></>}<p>{config?.limits.max_upload_mb} MB per file · {config?.limits.max_files_per_upload} files per upload</p></section>
    </>}{message && <p role="status">{message}</p>}</>;
}
