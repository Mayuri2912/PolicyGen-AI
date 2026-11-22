import { useCallback, useEffect, useRef, useState } from 'react';
import { api } from '../services/api.js';
import { TERMINAL_JOB } from '../utils/format.js';

const POLL_MS = 1200;

// Polls a background job until it reaches a terminal status and reports the
// real stage, progress and outcome.  `onFinished` fires once per run; call
// `restart()` after retrying a job to follow it again.
export function useJob(jobId, onFinished) {
  const [job, setJob] = useState(null);
  const [error, setError] = useState(null);
  const [run, setRun] = useState(0);
  const callback = useRef(onFinished);
  callback.current = onFinished;

  useEffect(() => {
    if (!jobId) {
      setJob(null);
      return undefined;
    }
    let active = true;
    let timer;
    let failures = 0;
    let retryAfter = 0;
    setError(null);

    const poll = async () => {
      try {
        const next = await api.getJob(jobId);
        if (!active) return;
        failures = 0; retryAfter = next.result?.next_retry_at ? Math.min(30, Math.max(1, (Date.parse(next.result.next_retry_at) - Date.now()) / 1000)) : 0;
        setJob(next);
        setError(null);
        if (TERMINAL_JOB.includes(next.status)) {
          callback.current?.(next);
          return;
        }
      } catch (err) {
        if (!active) return;
        failures += 1; retryAfter = err.retryAfter || 0;
        setError(err.message);
        if ([401, 403, 404].includes(err.status)) return;
      }
      timer = setTimeout(poll, Math.max(retryAfter * 1000, Math.min(30000, POLL_MS * 2 ** failures)));
    };
    poll();

    return () => {
      active = false;
      clearTimeout(timer);
    };
  }, [jobId, run]);

  const cancel = useCallback(async () => {
    if (jobId) setJob(await api.cancelJob(jobId));
  }, [jobId]);

  const retry = useCallback(async () => {
    if (!jobId) return;
    setJob(await api.retryJob(jobId));
    setRun((value) => value + 1);
  }, [jobId]);

  return { job, error, cancel, retry, running: Boolean(job) && !TERMINAL_JOB.includes(job.status) };
}
