import { useMemo, useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import { ArrowLeft, Download, RefreshCw, Search } from 'lucide-react';
import { MissingInformation } from '../components/MissingInformation.jsx';
import { DocumentPreview } from '../components/DocumentPreview.jsx';
import { QualityPanel, RegenerateDialog, ReviewPanel, SectionEditor, VersionPanel } from '../components/ReportTools.jsx';
import { JobProgress } from '../components/StageTracker.jsx';
import { Badge, ErrorState, LoadingBlock, PageHeader, SimulatedTag, TabPanel, Tabs } from '../components/ui.jsx';
import { useSystem } from '../context/SystemContext.jsx';
import { useToast } from '../context/ToastContext.jsx';
import { useAsync } from '../hooks/useAsync.js';
import { useJob } from '../hooks/useJob.js';
import { api, saveBlob } from '../services/api.js';
import { REVIEW_LABELS, countMatches, elementText, formatDateTime } from '../utils/format.js';

const GENERATION_STEPS = [
  { id: 'analysis_completed', label: 'Analysis Completed' },
  { id: 'generating_policy', label: 'Generating Policy' },
  { id: 'formatting_document', label: 'Formatting Document' },
  { id: 'completed', label: 'Completed' },
];
const REVISION_STEPS = GENERATION_STEPS.slice(1);

export default function ReportPage() {
  const { reportId } = useParams();
  const { config, user } = useSystem();
  const toast = useToast();
  const [viewVersion, setViewVersion] = useState(null);
  const [tab, setTab] = useState('document');
  const [search, setSearch] = useState('');
  const [editing, setEditing] = useState(null);
  const [regenerating, setRegenerating] = useState(null);
  const [revisionJobId, setRevisionJobId] = useState(null);
  const [downloading, setDownloading] = useState(null);
  const [followGeneration, setFollowGeneration] = useState(false);

  const { loading, error, data: report, reload, setData } = useAsync(() => api.getReport(reportId, viewVersion), [reportId, viewVersion]);

  // The generation job is followed until the first draft exists, and again
  // while a retry of document formatting is running.
  const generationJobId = report && (!report.document || followGeneration) ? report.job_id : null;
  const {
    job: generationJob,
    error: generationJobError,
    cancel: cancelGeneration,
    retry: retryGenerationJob,
  } = useJob(generationJobId, (job) => {
    if (job.status === 'completed') setFollowGeneration(false);
    reload({ silent: true });
  });
  const { job: revisionJob, cancel: cancelRevision, retry: retryRevision } = useJob(revisionJobId, (job) => {
    if (job.status === 'completed') {
      if (job.result?.changed === false) toast.info(job.message);
      else toast.success(job.message || 'A new version was created.');
      setViewVersion(null);
      reload({ silent: true });
      setRevisionJobId(null);
    } else if (job.status === 'failed') {
      toast.error(job.error_message || 'The revision failed. The policy was left unchanged.');
    }
  });

  const sections = report?.document?.sections || [];
  const matches = useMemo(() => {
    const counts = {};
    let total = 0;
    sections.forEach((section) => {
      const text = `${section.title}\n${section.elements.map(elementText).join('\n')}`;
      const count = countMatches(text, search);
      counts[section.id] = count;
      total += count;
    });
    return { counts, total };
  }, [sections, search]);

  const jump = (sectionId) => {
    setTab('document');
    requestAnimationFrame(() => document.getElementById(`doc-${sectionId}`)?.scrollIntoView({ behavior: 'smooth', block: 'start' }));
  };

  const download = async (format) => {
    setDownloading(format);
    try {
      const { blob, filename } = await api.downloadReport(reportId, format, report.version.version_number);
      saveBlob(blob, filename);
      toast.success(`${filename} downloaded.`);
    } catch (err) {
      toast.error(err.message);
    } finally {
      setDownloading(null);
    }
  };

  const retryGeneration = async () => {
    try {
      if (generationJobId) {
        await retryGenerationJob();
      } else {
        await api.retryJob(report.job_id);
        setFollowGeneration(true);
      }
    } catch (err) {
      toast.error(err.message);
    }
  };

  const back = (
    <Link to="/reports" className="back-link">
      <ArrowLeft size={16} aria-hidden="true" /> Report history
    </Link>
  );

  if (loading && !report) return <LoadingBlock label="Loading report…" />;
  if (error && !report) {
    return (
      <>
        {back}
        <ErrorState title="The report could not be loaded" message={error.message} onRetry={reload} />
      </>
    );
  }

  if (!report.document) {
    return (
      <>
        <PageHeader title={report.title} subtitle={`Step 4 of 4 · ${report.policy_type_name}`} back={back} />
        <JobProgress title="Policy generation" job={generationJob} error={generationJobError} steps={GENERATION_STEPS} onCancel={cancelGeneration} onRetry={retryGeneration} />
        <p className="muted small">
          The analysis is saved. If generation fails you can retry here or{' '}
          <Link to={`/analyses/${report.analysis_id}/configure`}>change the configuration</Link> without repeating the analysis.
        </p>
      </>
    );
  }

  const viewing = report.version.version_number;
  const isLatest = viewing === report.current_version;
  const editable = isLatest && !revisionJobId && report.status === 'completed' && (!user || ['admin', 'editor'].includes(user.role));
  const formats = report.output_formats || ['docx', 'pdf'];
  const tabs = [
    { id: 'document', label: 'Document' },
    { id: 'missing', label: 'Missing information', count: report.readiness?.missing_information.length || 0 },
    { id: 'quality', label: 'Quality checks', count: report.quality_report ? report.quality_report.counts.error + report.quality_report.counts.warning : undefined },
    { id: 'versions', label: 'Versions', count: report.versions.length },
    { id: 'review', label: 'Review & approval' },
  ];

  return (
    <>
      <PageHeader
        title={report.title}
        subtitle={`${report.policy_type_name} · from ${report.source_files.join(', ')} · generated ${formatDateTime(report.created_at)}`}
        back={back}
        actions={
          <>
            {formats.includes('docx') && (
              <button type="button" className="btn btn-primary" onClick={() => download('docx')} disabled={Boolean(downloading)}>
                <Download size={16} aria-hidden="true" /> {downloading === 'docx' ? 'Preparing…' : 'Download DOCX'}
              </button>
            )}
            {formats.includes('pdf') && (
              <button type="button" className="btn btn-primary" onClick={() => download('pdf')} disabled={Boolean(downloading)}>
                <Download size={16} aria-hidden="true" /> {downloading === 'pdf' ? 'Preparing…' : 'Download PDF'}
              </button>
            )}
          </>
        }
      />

      <div className="badge-row">
        {report.simulated && <SimulatedTag />}
        <Badge tone="info" label={`Version ${viewing}${isLatest ? ' (latest)' : ''}`} />
        <Badge value={report.review_status} label={`Review: ${REVIEW_LABELS[report.review_status]}`} />
        {report.readiness && <><Badge tone={report.readiness.structure_checked ? 'ok' : 'bad'} label={report.readiness.structure_checked ? 'Structure checked' : 'Structural issues'} /><Badge tone={report.readiness.missing_information.length ? 'warn' : 'ok'} label={`${report.readiness.missing_information.length} missing information fields`} /><Badge tone="info" label={report.readiness.ready_for_approval ? 'Ready for human approval' : 'Review preparation required'} /></>}
        {report.status === 'failed' && <Badge value="failed" label="Document formatting failed" />}
      </div>

      {report.status === 'failed' && !followGeneration && (
        <div className="notice notice-error" role="alert">
          <p>{report.error_message}</p>
          <button type="button" className="btn btn-small" onClick={retryGeneration}>
            Retry formatting
          </button>
        </div>
      )}
      {followGeneration && generationJob && <JobProgress title="Formatting documents" job={generationJob} steps={GENERATION_STEPS} onRetry={retryGeneration} />}
      <div className="notice notice-warn" role="note">
        <p>{config?.review_notice}</p>
      </div>
      {!isLatest && (
        <div className="notice notice-info">
          <p>
            You are viewing version {viewing}, not the latest. Editing is only possible on the latest version.{' '}
            <button type="button" className="link-button" onClick={() => setViewVersion(null)}>
              Go to version {report.current_version}
            </button>
          </p>
        </div>
      )}
      {revisionJobId && revisionJob && (
        <JobProgress
          title="Revising sections"
          job={revisionJob}
          steps={REVISION_STEPS}
          onCancel={cancelRevision}
          onRetry={() => retryRevision().catch((err) => toast.error(err.message))}
        />
      )}
      {revisionJob && ['failed', 'cancelled'].includes(revisionJob.status) && (
        <button type="button" className="link-button small" onClick={() => setRevisionJobId(null)}>
          Dismiss
        </button>
      )}

      <Tabs tabs={tabs} active={tab} onChange={setTab} label="Report views" />

      {tab === 'document' && (
        <TabPanel id="document">
          <div className="report-layout">
            <aside className="report-nav" aria-label="Sections">
              <label className="field">
                <span>
                  <Search size={14} aria-hidden="true" /> Search in this report
                </span>
                <input type="search" value={search} onChange={(e) => setSearch(e.target.value)} placeholder="Type at least 2 characters" />
              </label>
              <p className="muted small" aria-live="polite">
                {search.trim().length >= 2 ? `${matches.total} match${matches.total === 1 ? '' : 'es'}` : ' '}
              </p>
              <nav aria-label="Jump to section">
                <ul>
                  <li>
                    <button type="button" className="link-button" onClick={() => jump('control')}>
                      Document control
                    </button>
                  </li>
                  {sections.map((section) => (
                    <li key={section.id}>
                      <button type="button" className="link-button" onClick={() => jump(section.id)}>
                        {section.kind === 'appendix' ? `${section.number} – ${section.title}` : `${section.number}. ${section.title}`}
                      </button>
                      {matches.counts[section.id] > 0 && <span className="match-count">{matches.counts[section.id]}</span>}
                    </li>
                  ))}
                </ul>
              </nav>
              {editable && (
                <button type="button" className="btn" onClick={() => setRegenerating([])}>
                  <RefreshCw size={15} aria-hidden="true" /> Regenerate sections…
                </button>
              )}
            </aside>
            <DocumentPreview
              document={report.document}
              search={search}
              scoring={config?.scoring_method}
              editable={editable}
              onEdit={(sectionId) => setEditing(report.sections.find((section) => section.id === sectionId))}
              onRegenerate={(ids) => setRegenerating(ids)}
            />
          </div>
        </TabPanel>
      )}
      {tab === 'missing' && <TabPanel id="missing"><MissingInformation key={report.current_version} report={report} onSaved={() => { setViewVersion(null); reload(); }} onJump={jump} editable={editable} /></TabPanel>}
      {tab === 'quality' && (
        <div className="card">
          <TabPanel id="quality">
            <QualityPanel report={report.quality_report} onJump={jump} />
          </TabPanel>
        </div>
      )}
      {tab === 'versions' && (
        <div className="card">
          <TabPanel id="versions">
            <VersionPanel
              report={report}
              viewing={viewing}
              onView={(number) => {
                setViewVersion(number === report.current_version ? null : number);
                setTab('document');
              }}
            />
          </TabPanel>
        </div>
      )}
      {tab === 'review' && (
        <div className="card">
          <TabPanel id="review">
            <ReviewPanel
              report={report}
              onReview={async (body) => {
                const updated = await api.review(reportId, body);
                setViewVersion(null);
                setData(updated);
                toast.success('Review decision recorded.');
              }}
            />
          </TabPanel>
        </div>
      )}

      {editing && (
        <SectionEditor
          section={editing}
          baseVersion={report.current_version}
          onClose={() => setEditing(null)}
          onSave={async (body) => {
            const response = await api.editSection(reportId, editing.id, body);
            setData(response.report);
            toast.success(`Saved as version ${response.version}. The previous version is kept.`);
          }}
        />
      )}
      {regenerating && (
        <RegenerateDialog
          sections={report.sections}
          preselected={regenerating}
          simulated={report.simulated}
          onClose={() => setRegenerating(null)}
          onSubmit={async (ids, instruction) => {
            const response = await api.regenerateSections(reportId, ids, instruction);
            setRevisionJobId(response.job.id);
          }}
        />
      )}
    </>
  );
}
