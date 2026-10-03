import { Link } from 'react-router-dom';
import { CheckCircle2, FilePlus2, FileStack, FileText, ScanText, ShieldAlert, XCircle } from 'lucide-react';
import { Badge, EmptyState, ErrorState, LoadingBlock, PageHeader, SimulatedTag } from '../components/ui.jsx';
import { useSystem } from '../context/SystemContext.jsx';
import { useAsync } from '../hooks/useAsync.js';
import { api } from '../services/api.js';
import { JOB_TYPE_LABELS, REVIEW_LABELS, formatDateTime } from '../utils/format.js';

function Stat({ icon: Icon, label, value, hint }) {
  return (
    <div className="card stat">
      <Icon size={22} aria-hidden="true" />
      <div>
        <p className="stat-value">{value}</p>
        <p className="stat-label">{label}</p>
        {hint && <p className="muted small">{hint}</p>}
      </div>
    </div>
  );
}

function StatusLine({ ok, children }) {
  const Icon = ok ? CheckCircle2 : XCircle;
  return (
    <li className={ok ? 'status status-ok' : 'status status-bad'}>
      <Icon size={16} aria-hidden="true" />
      <span>{children}</span>
    </li>
  );
}

export default function Dashboard() {
  const { health, config } = useSystem();
  const { loading, error, data, reload } = useAsync(() => api.dashboard(), []);

  return (
    <>
      <PageHeader
        title="Welcome to PolicyGen AI"
        subtitle="Upload your organization's documents, review what the analysis finds, and generate a policy draft with its evidence attached."
        actions={
          <Link to="/new" className="btn btn-primary btn-large">
            <FilePlus2 size={18} aria-hidden="true" /> New policy generation
          </Link>
        }
      />

      {loading && !data && <LoadingBlock label="Loading dashboard…" />}
      {error && !data && <ErrorState message={error.message} onRetry={reload} />}

      {data && (
        <>
          <section className="stat-grid" aria-label="Summary">
            <Stat icon={FileText} label="Files processed" value={data.stats.files_processed} hint={`${data.stats.files_uploaded} uploaded`} />
            <Stat icon={ScanText} label="Analyses completed" value={data.stats.analyses_completed} />
            <Stat icon={FileStack} label="Policies generated" value={data.stats.policies_generated} hint={`${data.stats.policies_approved} approved`} />
            <Stat icon={ShieldAlert} label="Failed tasks" value={data.stats.jobs_failed} hint={`${data.stats.jobs_active} running now`} />
          </section>

          <div className="two-col">
            <section className="card">
              <div className="card-head">
                <h2>Recent reports</h2>
                <Link to="/reports">View all</Link>
              </div>
              {data.recent_reports.length === 0 ? (
                <EmptyState title="No policies yet" action={<Link to="/new" className="btn">Generate your first policy</Link>}>
                  Generated policies appear here.
                </EmptyState>
              ) : (
                <ul className="plain-list">
                  {data.recent_reports.map((report) => (
                    <li key={report.id} className="list-row">
                      <div>
                        <Link to={`/reports/${report.id}`} className="list-title">
                          {report.title}
                        </Link>
                        <p className="muted small">
                          {report.source_files.join(', ')} · {formatDateTime(report.created_at)}
                        </p>
                      </div>
                      <div className="badge-row">
                        {report.simulated && <SimulatedTag />}
                        <Badge value={report.status} />
                        {report.status === 'completed' && <Badge value={report.review_status} label={REVIEW_LABELS[report.review_status]} />}
                      </div>
                    </li>
                  ))}
                </ul>
              )}
            </section>

            <section className="card">
              <h2>System status</h2>
              <ul className="plain-list status-list">
                <StatusLine ok={health?.database === 'ok'}>Database {health?.database === 'ok' ? 'connected' : 'unavailable'}</StatusLine>
                <StatusLine ok={Boolean(health?.llm?.ready)}>
                  {health?.llm?.simulated ? 'Mock mode (no AI model; results are simulated)' : health?.llm?.ready ? `${health.llm.service_name || 'AI provider'} ready (${health.llm.model})` : `${health?.llm?.display_name || 'AI provider'} not configured`}
                </StatusLine>
                <StatusLine ok={Boolean(health?.ocr?.available)}>
                  {health?.ocr?.available ? `OCR available (${health.ocr.engine})` : 'OCR not installed: images and scanned PDFs cannot be read'}
                </StatusLine>
              </ul>
              <h3>Limits</h3>
              <p className="small">
                {config?.limits.max_upload_mb} MB per file · {config?.limits.max_files_per_upload} files per upload ·{' '}
                {config?.policy_types.length} policy types
              </p>
              <h3>Recent analyses</h3>
              {data.recent_analyses.length === 0 ? (
                <p className="muted small">No analyses yet.</p>
              ) : (
                <ul className="plain-list">
                  {data.recent_analyses.map((analysis) => (
                    <li key={analysis.id} className="list-row">
                      <Link to={`/analyses/${analysis.id}`} className="small">
                        {analysis.source_files.join(', ')}
                      </Link>
                      <Badge value={analysis.status} label={analysis.status_label} />
                    </li>
                  ))}
                </ul>
              )}
            </section>
          </div>

          <section className="card">
            <h2>Processing history</h2>
            {data.recent_jobs.length === 0 ? (
              <p className="muted">Nothing has been processed yet.</p>
            ) : (
              <div className="table-wrap">
                <table className="table">
                  <caption className="sr-only">Most recent background tasks</caption>
                  <thead>
                    <tr>
                      <th scope="col">Task</th>
                      <th scope="col">Stage</th>
                      <th scope="col">Detail</th>
                      <th scope="col">Started</th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.recent_jobs.map((job) => (
                      <tr key={job.id}>
                        <td>
                          {JOB_TYPE_LABELS[job.type] || job.type}
                          {job.subject_name ? `: ${job.subject_name}` : ''}
                        </td>
                        <td>
                          <Badge value={job.stage} label={job.stage_label} />
                        </td>
                        <td className="small">{job.status === 'failed' ? job.error_message : job.message}</td>
                        <td className="nowrap small">{formatDateTime(job.created_at)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </section>

          <section className="card">
            <h2>How it works</h2>
            <ol className="steps">
              <li>
                <strong>Upload</strong> one or more documents: policies, assessments, risk registers, procedures or records.
              </li>
              <li>
                <strong>Review the analysis</strong>: requirements, risks, controls and gaps, each traced to its source. Edit or exclude findings.
              </li>
              <li>
                <strong>Configure</strong> the policy type, organization details, style and optional appendices.
              </li>
              <li>
                <strong>Generate, review and download</strong> the policy as Word and PDF. Have it approved before adoption.
              </li>
            </ol>
            <p>
              <Link to="/help">Read the full instructions and the privacy notice</Link>
            </p>
          </section>
        </>
      )}
    </>
  );
}
