import { Check, Circle, Loader2, X } from 'lucide-react';
import { LoadingBlock, ProgressBar } from './ui.jsx';

export const PIPELINE = [
  { id: 'uploaded', label: 'Uploaded' },
  { id: 'validating', label: 'Validating' },
  { id: 'extracting', label: 'Extracting' },
  { id: 'extracted', label: 'Extracted' },
  { id: 'analyzing', label: 'Analyzing' },
  { id: 'analysis_completed', label: 'Analysis Completed' },
  { id: 'generating_policy', label: 'Generating Policy' },
  { id: 'formatting_document', label: 'Formatting Document' },
  { id: 'completed', label: 'Completed' },
];

// Shows where the work is in the pipeline.  `stage` is the stage reported by
// the server; a failed or cancelled job marks the stage it stopped at.
export function StageTracker({ stage, failedAt, steps = PIPELINE }) {
  const stopped = stage === 'failed' || stage === 'cancelled';
  const current = stopped ? failedAt : stage;
  const currentIndex = steps.findIndex((step) => step.id === current);
  return (
    <ol className="stage-tracker" aria-label="Processing stages">
      {steps.map((step, index) => {
        let state = 'pending';
        if (currentIndex === -1) state = 'pending';
        else if (index < currentIndex) state = 'done';
        else if (index === currentIndex) state = stopped ? 'failed' : index === steps.length - 1 || isRestingStage(step.id) ? 'done' : 'active';
        const Icon = state === 'done' ? Check : state === 'failed' ? X : state === 'active' ? Loader2 : Circle;
        return (
          <li key={step.id} className={`stage stage-${state}`} aria-current={index === currentIndex ? 'step' : undefined}>
            <span className="stage-icon">
              <Icon size={14} className={state === 'active' ? 'spin' : ''} aria-hidden="true" />
            </span>
            <span className="stage-label">{step.label}</span>
            <span className="sr-only">
              {state === 'done' ? ' (done)' : state === 'active' ? ' (in progress)' : state === 'failed' ? ' (stopped here)' : ' (not started)'}
            </span>
          </li>
        );
      })}
    </ol>
  );
}

function isRestingStage(id) {
  return ['uploaded', 'extracted', 'analysis_completed', 'completed'].includes(id);
}

export function JobProgress({ job, error, onCancel, onRetry, steps, title }) {
  if (!job) {
    return error ? (
      <p className="form-error" role="alert">
        {error}
      </p>
    ) : (
      <LoadingBlock label="Checking progress…" />
    );
  }
  const failed = job.status === 'failed';
  const cancelled = job.status === 'cancelled';
  const running = job.status === 'queued' || job.status === 'running';
  // The server sets stage to failed/cancelled; the last real stage is the one
  // recorded just before, which the tracker receives as `failedAt`.
  const failedAt = job.result?.failed_stage || guessStoppedStage(job, steps);
  return (
    <section className={`card job-progress ${failed ? 'job-failed' : ''}`} aria-live="polite">
      <div className="job-progress-head">
        <h2>{title}</h2>
        <span className={`badge ${failed ? 'badge-bad' : cancelled ? 'badge-muted' : running ? 'badge-note' : 'badge-ok'}`}>{job.stage_label}</span>
      </div>
      <StageTracker stage={job.stage} failedAt={failedAt} steps={steps} />
      {running && <ProgressBar value={job.progress} label={`${title} progress`} />}
      <p className={failed ? 'form-error' : 'muted'} role={failed ? 'alert' : undefined}>
        {failed ? job.error_message || 'The task failed.' : job.message}
      </p>
      <div className="row-actions">
        {running && onCancel && (
          <button type="button" className="btn" onClick={onCancel} disabled={job.cancel_requested}>
            {job.cancel_requested ? 'Cancelling…' : 'Cancel'}
          </button>
        )}
        {(failed || cancelled) && onRetry && (
          <button type="button" className="btn btn-primary" onClick={onRetry}>
            Retry
          </button>
        )}
      </div>
    </section>
  );
}

function guessStoppedStage(job, steps = PIPELINE) {
  const byType = { extraction: 'extracting', analysis: 'analyzing', generation: 'generating_policy', revision: 'generating_policy' };
  const guess = byType[job.type];
  return steps.some((step) => step.id === guess) ? guess : steps[0]?.id;
}
