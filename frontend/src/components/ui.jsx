import { useEffect, useId, useRef } from 'react';
import { AlertTriangle, Inbox, Loader2, X } from 'lucide-react';
import { splitForDisplay } from '../utils/format.js';

export function Spinner({ label = 'Loading', size = 18 }) {
  return (
    <span className="spinner" role="status">
      <Loader2 size={size} className="spin" aria-hidden="true" />
      <span className="sr-only">{label}</span>
    </span>
  );
}

export function LoadingBlock({ label = 'Loading…' }) {
  return (
    <div className="state-block" role="status">
      <Loader2 size={22} className="spin" aria-hidden="true" />
      <p>{label}</p>
    </div>
  );
}

export function EmptyState({ icon: Icon = Inbox, title, children, action }) {
  return (
    <div className="state-block">
      <Icon size={30} aria-hidden="true" />
      <h3>{title}</h3>
      {children && <p>{children}</p>}
      {action}
    </div>
  );
}

export function ErrorState({ title = 'Something went wrong', message, onRetry }) {
  return (
    <div className="state-block state-error" role="alert">
      <AlertTriangle size={30} aria-hidden="true" />
      <h3>{title}</h3>
      {message && <p>{message}</p>}
      {onRetry && (
        <button type="button" className="btn" onClick={onRetry}>
          Try again
        </button>
      )}
    </div>
  );
}

const BADGE_TONES = {
  completed: 'ok',
  extracted: 'ok',
  analysis_completed: 'ok',
  approved: 'ok',
  compliant: 'ok',
  implemented: 'ok',
  low: 'ok',
  failed: 'bad',
  critical: 'bad',
  not_evidenced: 'bad',
  not_implemented: 'bad',
  cancelled: 'muted',
  unrated: 'muted',
  not_applicable: 'muted',
  unknown: 'muted',
  draft: 'muted',
  high: 'warn',
  partially_addressed: 'warn',
  partially_implemented: 'warn',
  changes_requested: 'warn',
  medium: 'note',
  planned: 'note',
  in_review: 'note',
};

// `plain` shows the label exactly as given (model ids are case-sensitive).
export function Badge({ value, label, tone, plain = false }) {
  const resolved = tone || BADGE_TONES[value] || 'info';
  return <span className={`badge badge-${resolved}${plain ? ' badge-plain' : ''}`}>{label || String(value || '').replace(/_/g, ' ')}</span>;
}

export function ProgressBar({ value, label }) {
  const clamped = Math.max(0, Math.min(100, Math.round(value || 0)));
  return (
    <div className="progress" role="progressbar" aria-valuenow={clamped} aria-valuemin={0} aria-valuemax={100} aria-label={label}>
      <div className="progress-fill" style={{ width: `${clamped}%` }} />
    </div>
  );
}

export function Modal({ title, onClose, children, footer, wide = false }) {
  const titleId = useId();
  const dialogRef = useRef(null);

  useEffect(() => {
    const previous = document.activeElement;
    const dialog = dialogRef.current;
    const focusable = () =>
      dialog ? [...dialog.querySelectorAll('button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])')].filter((el) => !el.disabled) : [];
    (focusable().find((el) => el.dataset.autofocus) || focusable()[1] || focusable()[0])?.focus();

    const onKey = (event) => {
      if (event.key === 'Escape') {
        event.stopPropagation();
        onClose();
      } else if (event.key === 'Tab') {
        // Keep keyboard focus inside the dialog.
        const items = focusable();
        if (!items.length) return;
        const first = items[0];
        const last = items[items.length - 1];
        if (event.shiftKey && document.activeElement === first) {
          event.preventDefault();
          last.focus();
        } else if (!event.shiftKey && document.activeElement === last) {
          event.preventDefault();
          first.focus();
        }
      }
    };
    document.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('keydown', onKey);
      if (previous instanceof HTMLElement) previous.focus();
    };
  }, [onClose]);

  return (
    <div className="modal-backdrop" onMouseDown={(event) => event.target === event.currentTarget && onClose()}>
      <div className={`modal ${wide ? 'modal-wide' : ''}`} role="dialog" aria-modal="true" aria-labelledby={titleId} ref={dialogRef}>
        <header className="modal-header">
          <h2 id={titleId}>{title}</h2>
          <button type="button" className="icon-button" onClick={onClose} aria-label="Close dialog">
            <X size={18} aria-hidden="true" />
          </button>
        </header>
        <div className="modal-body">{children}</div>
        {footer && <footer className="modal-footer">{footer}</footer>}
      </div>
    </div>
  );
}

export function ConfirmDialog({ title, children, confirmLabel = 'Confirm', danger = false, busy = false, onConfirm, onCancel }) {
  return (
    <Modal
      title={title}
      onClose={onCancel}
      footer={
        <>
          <button type="button" className="btn" onClick={onCancel} disabled={busy}>
            Cancel
          </button>
          <button type="button" className={`btn ${danger ? 'btn-danger' : 'btn-primary'}`} onClick={onConfirm} disabled={busy} data-autofocus="true">
            {busy ? 'Working…' : confirmLabel}
          </button>
        </>
      }
    >
      {children}
    </Modal>
  );
}

export function Tabs({ tabs, active, onChange, label }) {
  const onKeyDown = (event) => {
    const index = tabs.findIndex((tab) => tab.id === active);
    let next = null;
    if (event.key === 'ArrowRight') next = tabs[(index + 1) % tabs.length];
    if (event.key === 'ArrowLeft') next = tabs[(index - 1 + tabs.length) % tabs.length];
    if (event.key === 'Home') next = tabs[0];
    if (event.key === 'End') next = tabs[tabs.length - 1];
    if (next) {
      event.preventDefault();
      onChange(next.id);
      document.getElementById(`tab-${next.id}`)?.focus();
    }
  };
  return (
    <div className="tabs" role="tablist" aria-label={label} onKeyDown={onKeyDown}>
      {tabs.map((tab) => (
        <button
          key={tab.id}
          id={`tab-${tab.id}`}
          type="button"
          role="tab"
          aria-selected={tab.id === active}
          aria-controls={`panel-${tab.id}`}
          tabIndex={tab.id === active ? 0 : -1}
          className={`tab ${tab.id === active ? 'tab-active' : ''}`}
          onClick={() => onChange(tab.id)}
        >
          {tab.label}
          {tab.count !== undefined && <span className="tab-count">{tab.count}</span>}
        </button>
      ))}
    </div>
  );
}

export function TabPanel({ id, children }) {
  return (
    <div id={`panel-${id}`} role="tabpanel" aria-labelledby={`tab-${id}`} tabIndex={0} className="tab-panel">
      {children}
    </div>
  );
}

// Renders text with "[To be confirmed: ...]" placeholders and search matches highlighted.
export function RichText({ text, search }) {
  return (
    <>
      {splitForDisplay(text, search).map((part, index) => {
        if (part.kind === 'placeholder') {
          return (
            <mark key={index} className="placeholder">
              {part.text}
            </mark>
          );
        }
        if (part.kind === 'match') {
          return (
            <mark key={index} className={part.inPlaceholder ? 'search-hit placeholder' : 'search-hit'}>
              {part.text}
            </mark>
          );
        }
        return <span key={index}>{part.text}</span>;
      })}
    </>
  );
}

export function SimulatedTag() {
  return (
    <span className="badge badge-sim" title="Produced by rule-based mock mode, not by an AI model">
      Simulated
    </span>
  );
}

export function PageHeader({ title, subtitle, actions, back }) {
  return (
    <div className="page-header">
      <div>
        {back}
        <h1>{title}</h1>
        {subtitle && <p className="page-subtitle">{subtitle}</p>}
      </div>
      {actions && <div className="page-actions">{actions}</div>}
    </div>
  );
}
