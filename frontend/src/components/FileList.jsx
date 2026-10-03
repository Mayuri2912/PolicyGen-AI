import { AlertTriangle, CheckCircle2, Eye, FileText, Loader2, RotateCcw, Trash2 } from 'lucide-react';
import { FILE_BUSY, formatBytes } from '../utils/format.js';
import { Badge } from './ui.jsx';

function StatusCell({ file }) {
  if (file.status === 'failed') {
    return (
      <span className="status status-bad">
        <AlertTriangle size={15} aria-hidden="true" /> Failed
      </span>
    );
  }
  if (file.status === 'extracted') {
    return (
      <span className="status status-ok">
        <CheckCircle2 size={15} aria-hidden="true" /> Extracted
      </span>
    );
  }
  return (
    <span className="status status-busy">
      <Loader2 size={15} className="spin" aria-hidden="true" /> {file.status_label || 'Processing'}
    </span>
  );
}

// Server-side files: validation/extraction status, selection for analysis,
// and per-file actions.
export function FileList({ files, selected, onToggle, onRemove, onRetry, onPreview }) {
  if (!files.length) return null;
  return (
    <div className="table-wrap">
      <table className="table">
        <caption className="sr-only">Uploaded files and their processing status</caption>
        <thead>
          <tr>
            <th scope="col" className="col-check">
              <span className="sr-only">Include in analysis</span>
            </th>
            <th scope="col">File</th>
            <th scope="col">Type</th>
            <th scope="col">Size</th>
            <th scope="col">Status</th>
            <th scope="col">
              <span className="sr-only">Actions</span>
            </th>
          </tr>
        </thead>
        <tbody>
          {files.map((file) => {
            const ready = file.status === 'extracted';
            const busy = FILE_BUSY.includes(file.status);
            return (
              <tr key={file.id}>
                <td className="col-check">
                  <input
                    type="checkbox"
                    checked={selected.includes(file.id)}
                    disabled={!ready}
                    onChange={() => onToggle(file.id)}
                    aria-label={`Include ${file.file_name} in the analysis`}
                  />
                </td>
                <td>
                  <div className="file-name">
                    <FileText size={16} aria-hidden="true" />
                    <span>{file.file_name}</span>
                    {file.duplicate && <Badge tone="note" label="Already uploaded" />}
                  </div>
                  {file.status === 'failed' && <p className="form-error">{file.error_message}</p>}
                  {ready && file.document && (
                    <p className="muted small">
                      {file.document.characters.toLocaleString()} characters · {file.document.sections} sections · {file.document.tables} tables
                    </p>
                  )}
                  {(file.warnings || []).map((warning) => (
                    <p key={warning} className="warning-text small">
                      {warning}
                    </p>
                  ))}
                </td>
                <td>{file.type_label}</td>
                <td className="nowrap">{formatBytes(file.size_bytes)}</td>
                <td className="nowrap">
                  <StatusCell file={file} />
                </td>
                <td>
                  <div className="row-actions nowrap-actions">
                  {ready && (
                    <button type="button" className="icon-button" onClick={() => onPreview(file)} aria-label={`View extracted content of ${file.file_name}`} title="View extracted content">
                      <Eye size={16} aria-hidden="true" />
                    </button>
                  )}
                  {file.status === 'failed' && !file.source_deleted && (
                    <button type="button" className="icon-button" onClick={() => onRetry(file)} aria-label={`Retry extraction of ${file.file_name}`} title="Retry extraction">
                      <RotateCcw size={16} aria-hidden="true" />
                    </button>
                  )}
                  <button type="button" className="icon-button" onClick={() => onRemove(file)} disabled={busy} aria-label={`Remove ${file.file_name}`} title="Remove file">
                    <Trash2 size={16} aria-hidden="true" />
                  </button>
                  </div>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

// Files rejected before or during upload (never stored on the server).
export function RejectedList({ items, onDismiss }) {
  if (!items.length) return null;
  return (
    <div className="notice notice-error" role="alert">
      <div className="notice-head">
        <strong>
          {items.length} file{items.length > 1 ? 's were' : ' was'} not accepted
        </strong>
        <button type="button" className="btn btn-small" onClick={onDismiss}>
          Dismiss
        </button>
      </div>
      <ul>
        {items.map((item, index) => (
          <li key={`${item.name}-${index}`}>
            <strong>{item.name}</strong>: {item.reason}
          </li>
        ))}
      </ul>
    </div>
  );
}
