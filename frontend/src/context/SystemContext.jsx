import { createContext, useCallback, useContext, useEffect, useMemo, useState } from 'react';
import { api, getAccessToken, setAccessToken } from '../services/api.js';

const SystemContext = createContext(null);

// Loads server health (LLM mode, OCR, privacy notice) and reference data
// (limits, file types, policy types) once, and exposes them to every page.
export function SystemProvider({ children }) {
  const [state, setState] = useState({ loading: true, error: null, health: null, config: null, needsToken: false });

  const load = useCallback(async () => {
    setState((current) => ({ ...current, loading: true, error: null }));
    try {
      let health;
      for (let attempt = 0; attempt < 4; attempt += 1) {
        try { health = await api.health(); break; }
        catch (error) {
          if (attempt === 3 || !['network_error', 'timeout', 'server_starting'].includes(error.code)) throw error;
          await new Promise((resolve) => setTimeout(resolve, 2000 * 2 ** attempt));
        }
      }
      if (health.auth_required && !getAccessToken()) {
        setState({ loading: false, error: null, health, config: null, needsToken: true });
        return;
      }
      const config = await api.config();
      const identity = await api.me().catch(() => ({ user: null }));
      setState({ loading: false, error: null, health, config, user: identity.user, needsToken: false });
    } catch (error) {
      if (error.status === 401) {
        setAccessToken('');
        setState((current) => ({ ...current, loading: false, needsToken: true, error: 'Your session expired or access was not accepted. Sign in again.' }));
      } else {
        setState((current) => ({ ...current, loading: false, error: error.message }));
      }
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  // The server re-reads its AI provider settings when backend/.env changes, so
  // the status shown here is refreshed whenever the user comes back to the tab.
  useEffect(() => {
    const refresh = async () => {
      if (document.visibilityState === 'hidden') return;
      try {
        const health = await api.health();
        setState((current) => (current.health ? { ...current, health } : current));
      } catch {
        // Keep what is shown; the next action reports a server that is down.
      }
    };
    window.addEventListener('focus', refresh);
    document.addEventListener('visibilitychange', refresh);
    return () => {
      window.removeEventListener('focus', refresh);
      document.removeEventListener('visibilitychange', refresh);
    };
  }, []);

  const value = useMemo(
    () => ({
      ...state,
      reload: load,
      signOut: async () => { try { await api.logout(); } finally { setAccessToken(''); load(); } },
      submitToken: (token) => {
        setAccessToken(token.trim());
        load();
      },
    }),
    [state, load],
  );

  return <SystemContext.Provider value={value}>{children}</SystemContext.Provider>;
}

export function useSystem() {
  const context = useContext(SystemContext);
  if (!context) throw new Error('useSystem must be used inside SystemProvider');
  return context;
}
