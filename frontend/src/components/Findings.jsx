import { useMemo, useState } from 'react';
import { EyeOff, Pencil, RotateCcw, ShieldCheck, ShieldQuestion, Undo2 } from 'lucide-react';
import { titleCase } from '../utils/format.js';
import { Badge, EmptyState, Modal } from './ui.jsx';

// What each kind of finding shows and which fields the user may edit.
// Editable fields mirror the server's whitelist in ai_analysis/engine.py.
function fieldsFor(kind, config) {
  const categories = Object.entries(config?.requirement_categories || {}).map(([value, label]) => ({ value, label }));
  const scale = [1, 2, 3, 4, 5].map((n) => ({ value: n, label: String(n) }));
  const priorities = (config?.priorities || []).map((p) => ({ value: p, label: titleCase(p) }));
  const options = (map) => Object.entries(map || {}).map(([value, label]) => ({ value, label }));
  return {
    requirement: [
      { name: 'statement', label: 'Requirement', type: 'textarea' },
      { name: 'category', label: 'Category', type: 'select', options: categories },
    ],
    risk: [
      { name: 'title', label: 'Risk title', type: 'text' },
      { name: 'description', label: 'Description', type: 'textarea' },
      { name: 'category', label: 'Category', type: 'text' },
      { name: 'likelihood', label: 'Likelihood (1–5)', type: 'select', options: scale, numeric: true },
      { name: 'impact', label: 'Impact (1–5)', type: 'select', options: scale, numeric: true },
      { name: 'recommended_mitigation', label: 'Recommended mitigation', type: 'textarea' },
    ],
    control: [
      { name: 'name', label: 'Control name', type: 'text' },
      { name: 'description', label: 'Description', type: 'textarea' },
      { name: 'status', label: 'Implementation status', type: 'select', options: options(config?.control_statuses) },
      { name: 'owner', label: 'Responsible role or department', type: 'text' },
      { name: 'recommended_action', label: 'Recommended action', type: 'textarea' },
    ],
    gap: [
      { name: 'status', label: 'Assessment', type: 'select', options: options(config?.gap_statuses) },
      { name: 'identified_gap', label: 'Identified gap', type: 'textarea' },
      { name: 'priority', label: 'Priority', type: 'select', options: priorities },
      { name: 'suggested_action', label: 'Suggested action', type: 'textarea' },
    ],
    responsibility: [
      { name: 'role', label: 'Role', type: 'text' },
      { name: 'responsibility', label: 'Responsibility', type: 'textarea' },
    ],
    recommendation: [
      { name: 'title', label: 'Recommendation', type: 'text' },
      { name: 'description', label: 'Description', type: 'textarea' },
      { name: 'priority', label: 'Priority', type: 'select', options: priorities },
    ],
  }[kind];
}

function Evidence({ source }) {
  if (!source?.file_name) {
    return <p className="muted small">No direct source: this item was inferred or proposed by the analysis.</p>;
  }
  return (
    <div className="evidence">
      <p className="small">
        <strong>Source:</strong> {source.file_name}
        {source.location ? ` — ${source.location}` : ''}
      </p>
      {source.evidence && (
        <blockquote>
          “{source.evidence}”
          <span className={`evidence-flag ${source.evidence_verified ? 'ok' : 'warn'}`}>
            {source.evidence_verified ? (
              <>
                <ShieldCheck size={13} aria-hidden="true" /> Quote found in the source text
              </>
            ) : (
              <>
                <ShieldQuestion size={13} aria-hidden="true" /> Quote not found verbatim in the source text
              </>
            )}
          </span>
        </blockquote>
      )}
    </div>
  );
}

function headline(kind, item) {
  switch (kind) {
    case 'requirement':
      return item.statement;
    case 'risk':
      return item.title;
    case 'control':
      return item.name;
    case 'gap':
      return item.requirement;
    case 'responsibility':
      return `${item.role}: ${item.responsibility}`;
    default:
      return item.title;
  }
}

function Details({ kind, item }) {
  if (kind === 'requirement') {
    return (
      <>
        <div className="badge-row">
          <Badge tone="info" label={item.category_label} />
          <Badge tone={item.basis === 'documented' ? 'ok' : 'note'} label={item.basis === 'documented' ? 'Documented' : 'Inferred'} />
          <Badge tone="muted" label={`Confidence: ${item.confidence}`} />
        </div>
        <Evidence source={item.source} />
      </>
    );
  }
  if (kind === 'risk') {
    return (
      <>
        <div className="badge-row">
          <Badge value={item.level} label={`${titleCase(item.level)}${item.score ? ` (${item.score})` : ''}`} />
          <Badge tone={item.origin === 'documented' ? 'ok' : 'note'} label={item.origin === 'documented' ? 'Documented in source' : 'Inferred by analysis'} />
          <Badge tone="muted" label={`L${item.likelihood || '–'} × I${item.impact || '–'}${item.scoring_basis === 'documented' ? ' (from document)' : item.scoring_basis === 'user' ? ' (set by you)' : ' (estimated)'}`} />
          {item.category && <Badge tone="info" label={item.category} />}
          <Badge tone="muted" label={`Confidence: ${item.confidence}`} />
        </div>
        {item.description && item.description !== item.title && <p>{item.description}</p>}
        {item.origin === 'inferred' && (
          <p className="warning-text small">Inferred risks are possibilities to review. They are not confirmed weaknesses or incidents.</p>
        )}
        <dl className="detail-list">
          <dt>Existing controls</dt>
          <dd>{item.existing_controls?.length ? item.existing_controls.join('; ') : 'None documented'}</dd>
          <dt>Recommended mitigation</dt>
          <dd>{item.recommended_mitigation || '—'}</dd>
          {item.residual_score > 0 && (
            <>
              <dt>Residual risk</dt>
              <dd>
                {titleCase(item.residual_level)} ({item.residual_score})
              </dd>
            </>
          )}
        </dl>
        <Evidence source={item.source} />
      </>
    );
  }
  if (kind === 'control') {
    return (
      <>
        <div className="badge-row">
          <Badge tone={item.basis === 'existing' ? 'ok' : 'note'} label={item.basis === 'existing' ? 'Existing control' : 'Recommended control'} />
          <Badge value={item.status} label={item.status_label} />
          <Badge tone="info" label={`${titleCase(item.control_type)} · ${titleCase(item.control_function)}`} />
        </div>
        {item.objective && (
          <p>
            <strong>Objective:</strong> {item.objective}
          </p>
        )}
        {item.description && item.description !== item.name && <p>{item.description}</p>}
        <dl className="detail-list">
          <dt>Related requirements</dt>
          <dd>{item.related_requirements?.join(', ') || '—'}</dd>
          <dt>Related risks</dt>
          <dd>{item.related_risks?.join(', ') || '—'}</dd>
          <dt>Responsible</dt>
          <dd>{item.owner || 'Not stated'}</dd>
          {item.recommended_action && (
            <>
              <dt>Recommended action</dt>
              <dd>{item.recommended_action}</dd>
            </>
          )}
        </dl>
        <Evidence source={item.source} />
      </>
    );
  }
  if (kind === 'gap') {
    return (
      <>
        <div className="badge-row">
          <Badge value={item.status} label={item.status_label} />
          <Badge value={item.priority} label={`Priority: ${titleCase(item.priority)}`} />
          <Badge tone="muted" label={item.requirement_id} />
        </div>
        <dl className="detail-list">
          <dt>Existing evidence</dt>
          <dd>{item.existing_evidence || 'None found in the documents'}</dd>
          <dt>Identified gap</dt>
          <dd>{item.identified_gap || '—'}</dd>
          <dt>Associated risks</dt>
          <dd>{item.associated_risk_ids?.join(', ') || '—'}</dd>
          <dt>Recommended control</dt>
          <dd>{[item.recommended_control, item.recommended_control_ids?.join(', ')].filter(Boolean).join(' — ') || '—'}</dd>
          <dt>Suggested action</dt>
          <dd>{item.suggested_action || '—'}</dd>
        </dl>
      </>
    );
  }
  if (kind === 'responsibility') {
    return (
      <>
        <div className="badge-row">
          <Badge tone={item.basis === 'documented' ? 'ok' : 'note'} label={item.basis === 'documented' ? 'Documented' : 'Proposed'} />
        </div>
        <Evidence source={item.source} />
      </>
    );
  }
  return (
    <>
      <div className="badge-row">
        <Badge value={item.priority} label={`Priority: ${titleCase(item.priority)}`} />
        {item.related_ids?.length > 0 && <Badge tone="muted" label={item.related_ids.join(', ')} />}
      </div>
      {item.description && <p>{item.description}</p>}
    </>
  );
}

function FindingEditor({ kind, item, config, onSave, onClose }) {
  const fields = fieldsFor(kind, config);
  const [values, setValues] = useState(() => Object.fromEntries(fields.map((f) => [f.name, item[f.name] ?? ''])));
  const [error, setError] = useState(null);
  const [saving, setSaving] = useState(false);

  const submit = async (event) => {
    event.preventDefault();
    const changed = {};
    for (const field of fields) {
      const before = item[field.name] ?? '';
      const after = field.numeric ? Number(values[field.name]) : String(values[field.name]).trim();
      if (after === before) continue;
      if (!field.numeric && after === '') {
        setError(`${field.label} cannot be empty.`);
        return;
      }
      changed[field.name] = after;
    }
    if (!Object.keys(changed).length) {
      onClose();
      return;
    }
    setSaving(true);
    setError(null);
    try {
      await onSave({ data: changed });
      onClose();
    } catch (err) {
      setError(err.message);
      setSaving(false);
    }
  };

  return (
    <Modal
      title={`Edit ${item.ref_id}`}
      onClose={onClose}
      footer={
        <>
          <button type="button" className="btn" onClick={onClose} disabled={saving}>
            Cancel
          </button>
          <button type="submit" form="finding-form" className="btn btn-primary" disabled={saving}>
            {saving ? 'Saving…' : 'Save changes'}
          </button>
        </>
      }
    >
      <form id="finding-form" onSubmit={submit} className="form-grid">
        <p className="muted small form-span">
          Your changes are marked as user edits. The original AI finding is kept and can be restored at any time.
        </p>
        {fields.map((field) => (
          <label key={field.name} className={`field ${field.type === 'textarea' ? 'form-span' : ''}`}>
            <span>{field.label}</span>
            {field.type === 'textarea' ? (
              <textarea rows={3} value={values[field.name]} onChange={(e) => setValues({ ...values, [field.name]: e.target.value })} maxLength={2000} />
            ) : field.type === 'select' ? (
              <select value={values[field.name]} onChange={(e) => setValues({ ...values, [field.name]: e.target.value })}>
                {field.numeric && !values[field.name] && <option value="">Not rated</option>}
                {field.options.map((option) => (
                  <option key={option.value} value={option.value}>
                    {option.label}
                  </option>
                ))}
              </select>
            ) : (
              <input type="text" value={values[field.name]} onChange={(e) => setValues({ ...values, [field.name]: e.target.value })} maxLength={300} />
            )}
          </label>
        ))}
        {error && (
          <p className="form-error form-span" role="alert">
            {error}
          </p>
        )}
      </form>
    </Modal>
  );
}

// A filterable list of findings of one kind, with edit / exclude / restore.
export function FindingList({ kind, items, config, onUpdate, emptyText, filters }) {
  const [editing, setEditing] = useState(null);
  const [filter, setFilter] = useState('all');
  const [query, setQuery] = useState('');

  const visible = useMemo(() => {
    const term = query.trim().toLowerCase();
    return items.filter((item) => {
      if (filter !== 'all' && filters && !filters.test(item, filter)) return false;
      if (!term) return true;
      return JSON.stringify(item).toLowerCase().includes(term);
    });
  }, [items, filter, query, filters]);

  if (!items.length) {
    return <EmptyState title="Nothing here">{emptyText}</EmptyState>;
  }
  const excludedCount = items.filter((item) => item.excluded).length;

  return (
    <div>
      <div className="list-toolbar">
        <label className="field field-inline">
          <span className="sr-only">Search these findings</span>
          <input type="search" placeholder="Search…" value={query} onChange={(e) => setQuery(e.target.value)} />
        </label>
        {filters && (
          <label className="field field-inline">
            <span className="sr-only">{filters.label}</span>
            <select value={filter} onChange={(e) => setFilter(e.target.value)}>
              <option value="all">{filters.label}: all</option>
              {filters.options.map((option) => (
                <option key={option.value} value={option.value}>
                  {option.label}
                </option>
              ))}
            </select>
          </label>
        )}
        <span className="muted small">
          Showing {visible.length} of {items.length}
          {excludedCount > 0 ? ` · ${excludedCount} excluded from policy generation` : ''}
        </span>
      </div>
      <ul className="finding-list">
        {visible.map((item) => (
          <li key={item.id} className={`finding ${item.excluded ? 'finding-excluded' : ''}`}>
            <div className="finding-head">
              <span className="ref-id">{item.ref_id}</span>
              <h3>{headline(kind, item)}</h3>
            </div>
            <div className="badge-row">
              {item.user_edited && <Badge tone="edit" label="Edited by you" />}
              {item.excluded && <Badge tone="muted" label="Excluded from policy" />}
              {item.auto_added && <Badge tone="note" label="Not assessed by the analysis" />}
            </div>
            <Details kind={kind} item={item} />
            {item.user_edited && item.original && (
              <details className="original">
                <summary>Show the original AI finding</summary>
                <p className="small">{headline(kind, item.original)}</p>
              </details>
            )}
            <div className="row-actions">
              <button type="button" className="btn btn-small" onClick={() => setEditing(item)}>
                <Pencil size={14} aria-hidden="true" /> Edit
              </button>
              <button type="button" className="btn btn-small" onClick={() => onUpdate(item, { excluded: !item.excluded })}>
                {item.excluded ? (
                  <>
                    <Undo2 size={14} aria-hidden="true" /> Include
                  </>
                ) : (
                  <>
                    <EyeOff size={14} aria-hidden="true" /> Exclude
                  </>
                )}
              </button>
              {item.user_edited && (
                <button type="button" className="btn btn-small" onClick={() => onUpdate(item, { restore: true })}>
                  <RotateCcw size={14} aria-hidden="true" /> Restore original
                </button>
              )}
            </div>
          </li>
        ))}
      </ul>
      {visible.length === 0 && <p className="muted">No findings match the current filter.</p>}
      {editing && <FindingEditor kind={kind} item={editing} config={config} onClose={() => setEditing(null)} onSave={(patch) => onUpdate(editing, patch)} />}
    </div>
  );
}
