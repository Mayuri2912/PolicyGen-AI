import { useState } from 'react';
import { api } from '../services/api.js';

export function MissingInformation({ report, onSaved, onJump, editable }) {
  const [values, setValues] = useState({});
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const missing = report.readiness?.missing_information || [];
  const save = async (event) => {
    event.preventDefault();
    setBusy(true); setError('');
    const replacements = {}; const metadata = {};
    missing.forEach((item) => {
      const value = values[item.label]?.trim();
      if (!value) return;
      if (item.field) metadata[item.field] = value;
      if (item.sections.some((s) => s !== 'document_control')) replacements[item.placeholder] = value;
    });
    try {
      if (!Object.keys(replacements).length && !Object.keys(metadata).length) throw new Error('Enter at least one confirmed value.');
      await api.completeMissing(report.id, { base_version: report.current_version, replacements, metadata });
      setValues({}); onSaved();
    } catch (err) { setError(err.message); }
    finally { setBusy(false); }
  };
  return <form className="card" onSubmit={save}>
    <h2>Complete missing information</h2>
    <p>Enter approved values or a controlled reference that contains them. A value replaces every occurrence of that exact placeholder in this version. Saving creates a new draft version.</p>
    {!missing.length && <p>No unresolved information was detected. Review the professional checks before approval.</p>}
    <div className="form-grid">{missing.map((item) => <label className="field" key={item.label}>
      <span>{item.label}</span>
      <input type={item.field === 'effective_date' ? 'date' : 'text'} value={values[item.label] || ''} onChange={(e) => setValues({ ...values, [item.label]: e.target.value })} maxLength={item.field ? 200 : 2000} disabled={!editable || busy} />
      <span className="small muted">Used in {item.sections.join(', ').replaceAll('_', ' ')}</span>
      <button type="button" className="btn btn-small" onClick={() => onJump(item.sections[0])}>View location</button>
    </label>)}</div>
    {error && <p role="alert" className="form-error">{error}</p>}
    {missing.length > 0 && <button className="btn btn-primary" disabled={!editable || busy}>{busy ? 'Saving…' : 'Save confirmed information'}</button>}
  </form>;
}
