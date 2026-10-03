import { useEffect, useState } from 'react';
import { NavLink, Outlet, useLocation } from 'react-router-dom';
import { FilePlus2, FileStack, FlaskConical, HelpCircle, LayoutDashboard, Menu, ShieldCheck, X } from 'lucide-react';
import { ErrorState, LoadingBlock } from '../components/ui.jsx';
import { useSystem } from '../context/SystemContext.jsx';

const NAV = [
  { to: '/', label: 'Dashboard', icon: LayoutDashboard, end: true },
  { to: '/new', label: 'New policy', icon: FilePlus2 },
  { to: '/reports', label: 'Report history', icon: FileStack },
  { to: '/help', label: 'Help & privacy', icon: HelpCircle },
];

function TokenGate({ error, onSubmit }) {
  const [token, setToken] = useState('');
  return (
    <form
      className="card token-gate"
      onSubmit={(event) => {
        event.preventDefault();
        if (token.trim()) onSubmit(token);
      }}
    >
      <h1>Access token required</h1>
      <p className="muted">This PolicyGen AI server is protected. Enter the access token provided by its administrator.</p>
      <label className="field">
        <span>Access token</span>
        <input type="password" value={token} onChange={(e) => setToken(e.target.value)} autoComplete="off" />
      </label>
      {error && (
        <p className="form-error" role="alert">
          {error}
        </p>
      )}
      <button type="submit" className="btn btn-primary">
        Continue
      </button>
    </form>
  );
}

export default function AppLayout() {
  const { loading, error, health, config, needsToken, submitToken, reload } = useSystem();
  const [menuOpen, setMenuOpen] = useState(false);
  const location = useLocation();

  useEffect(() => {
    setMenuOpen(false);
    document.getElementById('main-content')?.focus({ preventScroll: true });
  }, [location.pathname]);

  let content;
  if (needsToken) content = <TokenGate error={error} onSubmit={submitToken} />;
  else if (loading && !config) content = <LoadingBlock label="Connecting to the server…" />;
  else if (error && !config) content = <ErrorState title="The server is not available" message={error} onRetry={reload} />;
  else content = <Outlet />;

  const llm = health?.llm;
  return (
    <div className="app-shell">
      <a href="#main-content" className="skip-link">
        Skip to main content
      </a>
      <header className="topbar">
        <button type="button" className="icon-button menu-toggle" onClick={() => setMenuOpen((open) => !open)} aria-expanded={menuOpen} aria-controls="primary-nav" aria-label={menuOpen ? 'Close navigation' : 'Open navigation'}>
          {menuOpen ? <X size={20} aria-hidden="true" /> : <Menu size={20} aria-hidden="true" />}
        </button>
        <NavLink to="/" className="brand">
          <span className="brand-mark">
            <ShieldCheck size={20} aria-hidden="true" />
          </span>
          <span>
            <span className="brand-name">PolicyGen AI</span>
            <span className="brand-tag">Intelligent Policy Generation Agent</span>
          </span>
        </NavLink>
        {llm && (
          <span className={`mode-pill ${llm.simulated ? 'mode-mock' : llm.ready ? 'mode-live' : 'mode-off'}`} title={llm.message}>
            {llm.simulated ? (
              <>
                <FlaskConical size={14} aria-hidden="true" /> Mock mode
              </>
            ) : llm.ready ? (
              <span className="mode-text">
                {llm.display_name || llm.provider}: {llm.model}
              </span>
            ) : (
              <span className="mode-text">{llm.display_name || 'AI provider'} not configured</span>
            )}
          </span>
        )}
      </header>

      <nav id="primary-nav" className={`sidebar ${menuOpen ? 'sidebar-open' : ''}`} aria-label="Primary">
        <ul>
          {NAV.map(({ to, label, icon: Icon, end }) => (
            <li key={to}>
              <NavLink to={to} end={end} className={({ isActive }) => `nav-link ${isActive ? 'nav-active' : ''}`}>
                <Icon size={18} aria-hidden="true" />
                {label}
              </NavLink>
            </li>
          ))}
        </ul>
        <p className="sidebar-foot">
          AI-assisted drafts require review and approval by authorized personnel before adoption.
        </p>
      </nav>
      {menuOpen && <div className="sidebar-scrim" onClick={() => setMenuOpen(false)} aria-hidden="true" />}

      <main id="main-content" className="main" tabIndex={-1}>
        {llm?.simulated && !needsToken && (
          <div className="banner banner-mock" role="note">
            <FlaskConical size={16} aria-hidden="true" />
            <span>
              <strong>Mock mode.</strong> Analysis and policies are produced by keyword rules, not by an AI model, and are labelled as
              simulated. Set an AI provider API key in <code>backend/.env</code> on the server to use a real model.
            </span>
          </div>
        )}
        {llm && !llm.ready && !needsToken && (
          <div className="banner banner-error" role="alert">
            {llm.message}
          </div>
        )}
        {content}
      </main>
    </div>
  );
}
