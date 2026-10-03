import { Pencil, RefreshCw } from 'lucide-react';
import { ORIGIN_LABELS } from '../utils/format.js';
import { Badge, RichText } from './ui.jsx';

function levelClass(likelihood, impact, levels) {
  const score = likelihood * impact;
  for (const band of levels || []) {
    const [min, max] = band.range.split('-').map(Number);
    if (score >= min && score <= max) return band.level;
  }
  return 'unrated';
}

function Element({ element, search, scoring }) {
  switch (element.type) {
    case 'paragraph':
      return (
        <p>
          <RichText text={element.text} search={search} />
        </p>
      );
    case 'subheading':
      return (
        <h4>
          <RichText text={element.text} search={search} />
        </h4>
      );
    case 'note':
      return (
        <p className="doc-note">
          <RichText text={element.text} search={search} />
        </p>
      );
    case 'bullets':
    case 'numbered': {
      const List = element.type === 'bullets' ? 'ul' : 'ol';
      return (
        <List>
          {element.items.map((item, index) => (
            <li key={index}>
              <RichText text={item} search={search} />
            </li>
          ))}
        </List>
      );
    }
    case 'statements':
      return (
        <ul className="statements">
          {element.statements.map((statement) => (
            <li key={statement.id || statement.text}>
              <p>
                <span className="statement-id">{statement.id}</span> <RichText text={statement.text} search={search} />
              </p>
              <p className="statement-basis">
                {statement.basis === 'documented' ? 'Documented in source material' : 'Proposed by this draft'}
                {statement.refs?.length > 0 && (
                  <>
                    {' · References: '}
                    <RichText text={statement.refs.join(', ')} search={search} />
                  </>
                )}
              </p>
            </li>
          ))}
        </ul>
      );
    case 'table':
      return (
        <div className="table-wrap">
          <table className="table doc-table">
            <thead>
              <tr>
                {element.columns.map((column) => (
                  <th key={column} scope="col">
                    {column}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {element.rows.map((row, rowIndex) => (
                <tr key={rowIndex}>
                  {element.columns.map((_, cellIndex) => (
                    <td key={cellIndex}>
                      <RichText text={row[cellIndex] ?? ''} search={search} />
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      );
    case 'risk_matrix':
      return (
        <figure className="risk-matrix">
          <table>
            <caption className="sr-only">Risk matrix</caption>
            <tbody>
              {element.cells.map((row, rowIndex) => (
                <tr key={rowIndex}>
                  <th scope="row">Impact {5 - rowIndex}</th>
                  {row.map((count, columnIndex) => (
                    <td key={columnIndex} className={`cell-${levelClass(columnIndex + 1, 5 - rowIndex, scoring?.levels)}`}>
                      {count || ''}
                    </td>
                  ))}
                </tr>
              ))}
              <tr>
                <td />
                {[1, 2, 3, 4, 5].map((n) => (
                  <th key={n} scope="col">
                    L{n}
                  </th>
                ))}
              </tr>
            </tbody>
          </table>
        </figure>
      );
    default:
      return null;
  }
}

// On-screen rendering of the same document model the DOCX and PDF are built from.
export function DocumentPreview({ document: model, search, scoring, editable, onEdit, onRegenerate }) {
  const { meta } = model;
  return (
    <article className="document" aria-label="Policy preview">
      <header className="doc-cover">
        <p className="doc-org">
          <RichText text={meta.organization} search={search} />
        </p>
        <h2 className="doc-title">{meta.title}</h2>
        {model.notices.map((notice) => (
          <p key={notice} className={`doc-note ${notice.startsWith('SIMULATED') ? 'doc-note-sim' : ''}`}>
            {notice}
          </p>
        ))}
      </header>

      <section id="doc-control" className="doc-section">
        <h3>Document Control</h3>
        <div className="table-wrap">
          <table className="table doc-table">
            <tbody>
              {model.document_control.map(([label, value]) => (
                <tr key={label}>
                  <th scope="row">{label}</th>
                  <td>
                    <RichText text={value} search={search} />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <h3>Version History</h3>
        <div className="table-wrap">
          <table className="table doc-table">
            <thead>
              <tr>
                <th scope="col">Revision</th>
                <th scope="col">Date</th>
                <th scope="col">Change type</th>
                <th scope="col">Change note</th>
              </tr>
            </thead>
            <tbody>
              {model.version_history.map((row) => (
                <tr key={row[0]}>
                  {row.map((cell, index) => (
                    <td key={index}>{cell}</td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>

      {model.sections.map((section) => (
        <section key={section.id} id={`doc-${section.id}`} className="doc-section" data-section={section.id}>
          <div className="doc-section-head">
            <h3>{section.kind === 'appendix' ? `${section.number} – ${section.title}` : `${section.number}. ${section.title}`}</h3>
            <div className="doc-section-tools">
              {section.kind === 'section' && section.origin !== 'ai_generated' && (
                <Badge tone={section.origin === 'user_edited' ? 'edit' : section.origin === 'simulated' ? 'sim' : 'note'} label={ORIGIN_LABELS[section.origin] || section.origin} />
              )}
              {editable && section.kind === 'section' && (
                <>
                  <button type="button" className="btn btn-small" onClick={() => onEdit(section.id)} aria-label={`Edit section ${section.title}`}>
                    <Pencil size={14} aria-hidden="true" /> Edit
                  </button>
                  <button type="button" className="btn btn-small" onClick={() => onRegenerate([section.id])} aria-label={`Regenerate section ${section.title}`}>
                    <RefreshCw size={14} aria-hidden="true" /> Regenerate
                  </button>
                </>
              )}
            </div>
          </div>
          {section.elements.length === 0 && <p className="muted">This section has no content.</p>}
          {section.elements.map((element, index) => (
            <Element key={index} element={element} search={search} scoring={scoring} />
          ))}
        </section>
      ))}
    </article>
  );
}
