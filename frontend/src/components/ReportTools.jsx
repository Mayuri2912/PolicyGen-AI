import { useEffect, useState } from 'react';
import { AlertTriangle, CheckCircle2, Info, XCircle } from 'lucide-react';
import { api } from '../services/api.js';
import { REVIEW_LABELS, VERSION_ORIGIN_LABELS, formatDateTime } from '../utils/format.js';
import { Badge, ConfirmDialog, LoadingBlock, Modal } from './ui.jsx';

const MARKUP_HELP = [
  ['### Heading', 'a subheading'],
  ['Plain text', 'a paragraph (blank line between paragraphs)'],
  ['- item', 'a bullet'],
  ['1. step', 'a numbered step'],
  ['>> [documented] Text {REQ-001}', 'a policy statement with its basis and references'],
  ['| A | B |', 'a table row (first row is the header)'],
];

// Edits one section as text.  Saving always creates a new version; the user
// confirms before anything is stored.
export function SectionEditor({ section, baseVersion, onSave, onClose }) {
  const [markup, setMarkup] = useState(section.markup);
  const [note, setNote] = useState('');
  const [confirming, setConfirming] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState(null);
  const changed = markup.trim() !== section.markup.trim();

  const save = async () => {
    setSaving(true);
    setError(null);
    try {
      await onSave({ markup, note, base_version: baseVersion });
      onClose();
    } catch (err) {
      setError(err.message);
      setSaving(false);
      setConfirming(false);
    }
  };

  return (
    <>
      <Modal
        wide
        title={`Edit: ${section.heading}`}
        onClose={onClose}
        footer={
          <>
            <button type="button" className="btn" onClick={onClose}>
              Cancel
            </button>
            <button type="button" className="btn btn-primary" disabled={!changed || !markup.trim()} onClick={() => setConfirming(true)}>
              Save as new version
            </button>
          </>
        }
      >
        <label className="field">
          <span>Section content</span>
          <textarea className="editor" rows={16} value={markup} onChange={(e) => setMarkup(e.target.value)} spellCheck="true" data-autofocus="true" />
        </label>
        <details className="markup-help">
          <summary>Formatting help</summary>
          <dl>
            {MARKUP_HELP.map(([syntax, meaning]) => (
              <div key={syntax}>
                <dt>
                  <code>{syntax}</code>
                </dt>
                <dd>{meaning}</dd>
              </div>
            ))}
          </dl>
        </details>
        <label className="field">
          <span>Change note (optional)</span>
          <input type="text" value={note} onChange={(e) => setNote(e.target.value)} maxLength={300} placeholder="What did you change, and why?" />
        </label>
        {error && (
          <p className="form-error" role="alert">
            {error}
          </p>
        )}
      </Modal>
      {confirming && (
        <ConfirmDialog title="Save your edit as a new version?" confirmLabel="Save new version" busy={saving} onConfirm={save} onCancel={() => setConfirming(false)}>
          <p>
            This creates version {baseVersion + 1}. Version {baseVersion} is kept unchanged and remains available for download and comparison.
          </p>
          <p className="muted small">If the policy is in review or approved, its review status returns to draft for the new version.</p>
        </ConfirmDialog>
      )}
    </>
  );
}

export function RegenerateDialog({ sections, preselected, simulated, onSubmit, onClose }) {
  const [selected, setSelected] = useState(preselected);
  const [instruction, setInstruction] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const toggle = (id) => setSelected((list) => (list.includes(id) ? list.filter((item) => item !== id) : [...list, id]));

  const submit = async () => {
    setBusy(true);
    setError(null);
    try {
      await onSubmit(selected, instruction.trim());
      onClose();
    } catch (err) {
      setError(err.message);
      setBusy(false);
    }
  };

  return (
    <Modal
      title="Regenerate sections"
      onClose={onClose}
      footer={
        <>
          <button type="button" className="btn" onClick={onClose} disabled={busy}>
            Cancel
          </button>
          <button type="button" className="btn btn-primary" onClick={submit} disabled={busy || !selected.length}>
            {busy ? 'Starting…' : `Regenerate ${selected.length} section${selected.length === 1 ? '' : 's'}`}
          </button>
        </>
      }
    >
      <p className="muted small">
        Only the selected sections are rewritten. The result is saved as a new version; the current version is kept.
      </p>
      <fieldset className="choice-group compact">
        <legend>Sections</legend>
        {sections.map((section) => (
          <label key={section.id} className="choice">
            <input type="checkbox" checked={selected.includes(section.id)} onChange={() => toggle(section.id)} />
            <span>{section.heading}</span>
          </label>
        ))}
      </fieldset>
      <label className="field">
        <span>Instruction for the revision (optional)</span>
        <textarea rows={3} value={instruction} onChange={(e) => setInstruction(e.target.value)} maxLength={1500} placeholder="For example: make this shorter, or add a requirement for quarterly reviews of privileged accounts." />
      </label>
      {simulated && (
        <p className="warning-text small">
          Mock mode rebuilds sections from the analysis but cannot follow written instructions. Configure an AI provider API key on the server for AI-assisted editing.
        </p>
      )}
      {error && (
        <p className="form-error" role="alert">
          {error}
        </p>
      )}
    </Modal>
  );
}

const SEVERITY = {
  error: { icon: XCircle, tone: 'bad', label: 'Error' },
  warning: { icon: AlertTriangle, tone: 'warn', label: 'Warning' },
  info: { icon: Info, tone: 'info', label: 'Note' },
};

export function QualityPanel({ report, onJump }) {
  if (!report) return <p className="muted">No quality report is available for this version.</p>;
  return (
    <div>
      <div className="quality-summary">
        {report.passed ? (
          <p className="status status-ok">
            <CheckCircle2 size={18} aria-hidden="true" /> No blocking issues were found by the automatic checks.
          </p>
        ) : (
          <p className="status status-bad">
            <XCircle size={18} aria-hidden="true" /> The automatic checks found issues that should be fixed before approval.
          </p>
        )}
        <div className="badge-row">
          <Badge tone="info" label={`Quality score ${report.score}/100`} />
          <Badge tone="bad" label={`${report.counts.error} errors`} />
          <Badge tone="warn" label={`${report.counts.warning} warnings`} />
          <Badge tone="muted" label={`${report.statement_count ?? 0} policy statements`} />
          {report.correction_rounds > 0 && <Badge tone="note" label={`${report.correction_rounds} automatic correction round`} />}
        </div>
      </div>
      <p className="muted small">
        Checks cover: mandatory sections, empty or repeated content, references to findings, unsupported certification or compliance claims,
        contradictions between sections, unclear responsibilities and unresolved placeholders. They do not replace human review.
      </p>
      {report.issues.length === 0 ? (
        <p>No issues to report.</p>
      ) : (
        <ul className="issue-list">
          {report.issues.map((issue, index) => {
            const { icon: Icon, tone, label } = SEVERITY[issue.severity] || SEVERITY.info;
            return (
              <li key={index} className={`issue issue-${tone}`}>
                <Icon size={16} aria-hidden="true" />
                <div>
                  <p>
                    <strong>{label}:</strong> {issue.message}
                  </p>
                  {issue.section_id && (
                    <button type="button" className="link-button small" onClick={() => onJump(issue.section_id)}>
                      Go to section
                    </button>
                  )}
                </div>
              </li>
            );
          })}
        </ul>
      )}
      {report.placeholders?.length > 0 && (
        <>
          <h3>Placeholders to complete</h3>
          <ul className="placeholder-list">
            {report.placeholders.map((item, index) => (
              <li key={index}>
                <mark className="placeholder">{item.text}</mark>{' '}
                <button type="button" className="link-button small" onClick={() => onJump(item.section_id)}>
                  in {item.section}
                </button>
              </li>
            ))}
          </ul>
        </>
      )}
    </div>
  );
}

export function VersionPanel({ report, viewing, onView }) {
  const versions = [...report.versions].reverse();
  const [from, setFrom] = useState(versions[1]?.version_number ?? versions[0]?.version_number);
  const [to, setTo] = useState(versions[0]?.version_number);
  const [comparison, setComparison] = useState(null);
  const [state, setState] = useState({ loading: false, error: null });

  useEffect(() => {
    setFrom(versions[1]?.version_number ?? versions[0]?.version_number);
    setTo(versions[0]?.version_number);
    setComparison(null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [report.current_version]);

  const compare = async () => {
    setState({ loading: true, error: null });
    try {
      setComparison(await api.compareVersions(report.id, from, to));
      setState({ loading: false, error: null });
    } catch (err) {
      setState({ loading: false, error: err.message });
    }
  };

  return (
    <div>
      <div className="table-wrap">
        <table className="table">
          <caption className="sr-only">Versions of this policy</caption>
          <thead>
            <tr>
              <th scope="col">Version</th>
              <th scope="col">Created</th>
              <th scope="col">Change</th>
              <th scope="col">Note</th>
              <th scope="col">Quality</th>
              <th scope="col">
                <span className="sr-only">Actions</span>
              </th>
            </tr>
          </thead>
          <tbody>
            {versions.map((version) => (
              <tr key={version.version_number} className={version.version_number === viewing ? 'row-current' : ''}>
                <td>
                  v{version.version_number}
                  {version.version_number === report.current_version && <Badge tone="info" label="Latest" />}
                  {version.version_number === report.approved_version && <Badge tone="ok" label="Approved" />}
                </td>
                <td className="nowrap">{formatDateTime(version.created_at)}</td>
                <td>{VERSION_ORIGIN_LABELS[version.origin] || version.origin}</td>
                <td>{version.change_note}</td>
                <td>{version.quality?.score ?? '–'}</td>
                <td>
                  <button type="button" className="btn btn-small" disabled={version.version_number === viewing} onClick={() => onView(version.version_number)}>
                    {version.version_number === viewing ? 'Viewing' : 'View'}
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <h3>Compare versions</h3>
      {versions.length < 2 ? (
        <p className="muted">There is only one version so far. Edit or regenerate a section to create another.</p>
      ) : (
        <>
          <div className="list-toolbar">
            <label className="field field-inline">
              <span>From</span>
              <select value={from} onChange={(e) => setFrom(Number(e.target.value))}>
                {versions.map((v) => (
                  <option key={v.version_number} value={v.version_number}>
                    v{v.version_number}
                  </option>
                ))}
              </select>
            </label>
            <label className="field field-inline">
              <span>To</span>
              <select value={to} onChange={(e) => setTo(Number(e.target.value))}>
                {versions.map((v) => (
                  <option key={v.version_number} value={v.version_number}>
                    v{v.version_number}
                  </option>
                ))}
              </select>
            </label>
            <button type="button" className="btn" onClick={compare} disabled={from === to || state.loading}>
              Compare
            </button>
          </div>
          {state.loading && <LoadingBlock label="Comparing versions…" />}
          {state.error && (
            <p className="form-error" role="alert">
              {state.error}
            </p>
          )}
          {comparison && <Comparison comparison={comparison} />}
        </>
      )}
    </div>
  );
}

function Comparison({ comparison }) {
  const changed = comparison.sections.filter((section) => section.status !== 'unchanged');
  return (
    <div className="comparison" aria-live="polite">
      <p>
        v{comparison.from} → v{comparison.to}: {comparison.counts.changed} changed, {comparison.counts.added} added, {comparison.counts.removed} removed,{' '}
        {comparison.counts.unchanged} unchanged section(s).
      </p>
      {changed.length === 0 && <p className="muted">These versions have identical content.</p>}
      {changed.map((section) => (
        <section key={section.id} className="diff-section">
          <h4>
            {section.heading} <Badge tone={section.status === 'removed' ? 'bad' : section.status === 'added' ? 'ok' : 'note'} label={section.status} />
          </h4>
          <div className="diff">
            {section.diff.map((line, index) => (
              <div key={index} className={`diff-line diff-${line.type}`}>
                <span className="diff-mark" aria-hidden="true">
                  {line.type === 'insert' ? '+' : line.type === 'delete' ? '−' : ' '}
                </span>
                <span className="sr-only">{line.type === 'insert' ? 'Added: ' : line.type === 'delete' ? 'Removed: ' : ''}</span>
                <span>{line.text}</span>
              </div>
            ))}
          </div>
        </section>
      ))}
    </div>
  );
}

const REVIEW_ACTIONS = {
  draft: [
    { action: 'submit', label: 'Submit for review', needsActor: false },
    { action: 'approve', label: 'Approve', needsActor: true, primary: true },
  ],
  in_review: [
    { action: 'approve', label: 'Approve', needsActor: true, primary: true },
    { action: 'request_changes', label: 'Request changes', needsActor: true },
  ],
  changes_requested: [{ action: 'submit', label: 'Resubmit for review', needsActor: false }],
  approved: [{ action: 'reopen', label: 'Reopen as draft', needsActor: false }],
};

export function ReviewPanel({ report, onReview }) {
  const [actor, setActor] = useState('');
  const [comment, setComment] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const actions = REVIEW_ACTIONS[report.review_status] || [];

  const run = async (item) => {
    if (item.needsActor && !actor.trim()) {
      setError("Enter the reviewer's name to record this decision.");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      await onReview({ action: item.action, actor: actor.trim(), comment: comment.trim() });
      setComment('');
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div>
      <div className="notice notice-info">
        <p>
          <strong>Human review is required.</strong> This policy is an AI-assisted draft. An authorized person in your organization must
          check it against the evidence, complete the placeholders and approve it before it is adopted. Approval recorded here is a record
          in this tool only.
        </p>
      </div>
      <p>
        Current status: <Badge value={report.review_status} label={REVIEW_LABELS[report.review_status]} /> for version {report.current_version}
        {report.approved_version ? ` · version ${report.approved_version} is approved` : ''}
      </p>
      <div className="form-grid">
        <label className="field">
          <span>Reviewer name</span>
          <input type="text" value={actor} onChange={(e) => setActor(e.target.value)} maxLength={120} autoComplete="name" />
        </label>
        <label className="field form-span">
          <span>Comment (optional)</span>
          <textarea rows={2} value={comment} onChange={(e) => setComment(e.target.value)} maxLength={2000} />
        </label>
      </div>
      {error && (
        <p className="form-error" role="alert">
          {error}
        </p>
      )}
      <div className="row-actions">
        {actions.map((item) => (
          <button key={item.action} type="button" className={`btn ${item.primary ? 'btn-primary' : ''}`} disabled={busy} onClick={() => run(item)}>
            {item.label}
          </button>
        ))}
      </div>

      <h3>Review history</h3>
      {report.review_events.length === 0 ? (
        <p className="muted">No review activity yet.</p>
      ) : (
        <ul className="timeline">
          {[...report.review_events].reverse().map((event, index) => (
            <li key={index}>
              <strong>{event.action.replace(/_/g, ' ')}</strong> · v{event.version_number} · {event.actor || 'unnamed'} · {formatDateTime(event.created_at)}
              {event.comment && <p className="small">{event.comment}</p>}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
