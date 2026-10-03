import { useCallback, useEffect, useRef, useState } from 'react';

// Runs an async loader on mount and whenever `deps` change, with loading and
// error state, and ignores responses that arrive after a newer request.
export function useAsync(loader, deps = []) {
  const [state, setState] = useState({ loading: true, error: null, data: null });
  const sequence = useRef(0);

  // eslint-disable-next-line react-hooks/exhaustive-deps
  const run = useCallback(async ({ silent = false } = {}) => {
    const ticket = ++sequence.current;
    if (!silent) setState((current) => ({ ...current, loading: true, error: null }));
    try {
      const data = await loader();
      if (ticket === sequence.current) setState({ loading: false, error: null, data });
      return data;
    } catch (error) {
      if (ticket === sequence.current) setState((current) => ({ loading: false, error, data: silent ? current.data : null }));
      return null;
    }
  }, deps);

  useEffect(() => {
    run();
    return () => {
      sequence.current += 1;
    };
  }, [run]);

  const setData = useCallback((updater) => {
    setState((current) => ({ ...current, data: typeof updater === 'function' ? updater(current.data) : updater }));
  }, []);

  return { ...state, reload: run, setData };
}
