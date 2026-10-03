import { useCallback, useState } from 'react';
import { Link, useNavigate, useParams } from 'react-router-dom';
import { ArrowRight, ShieldAlert } from 'lucide-react';
import { FindingList } from '../components/Findings.jsx';
import { BarList, RiskLevelSummary, RiskMatrix } from '../components/RiskCharts.jsx';
import { JobProgress } from '../components/StageTracker.jsx';
import { Badge, EmptyState, ErrorState, LoadingBlock, PageHeader, SimulatedTag, TabPanel, Tabs } from '../components/ui.jsx';
import { useSystem } from '../context/SystemContext.jsx';
import { useToast } from '../context/ToastContext.jsx';
import { useAsync } from '../hooks/useAsync.js';
import { useJob } from '../hooks/useJob.js';
import { api } from '../services/api.js';
import { formatDateTime, providerLabel, titleCase } from '../utils/format.js';

const ANALYSIS_STEPS = [
  { id: 'extracted', label: 'Extracted' },
  { id: 'analyzing', label: 'Analyzing' },
  { id: 'analysis_completed', label: 'Analysis Completed' },
];

function Summary({ analysis }) {
  const { summary } = analysis;
  return (
    <div className="stack">
      <section>
        <h2>Document summary</h2>
        <p>{summary.overall_summary}</p>
        <div className="badge-row">
          {summary.document_category && <Badge tone="info" label={summary.document_category} />}
          {(summary.subject_areas || []).map((area) => (
            <Badge key={area} tone="muted" label={area} />
          ))}
        </div>
      </section>
      <section>
        <h3>Documents analyzed</h3>
        <ul className="plain-list">
          {(summary.document_summaries || []).map((doc) => (
            <li key={doc.file_name} className="doc-summary">
              <strong>{doc.file_name}</strong> {doc.category && <Badge tone="muted" label={doc.category} />}
              <p className="small">{doc.summary}</p>
            </li>
          ))}
        </ul>
      </section>
      <section className="two-col">
        <div>
          <h3>Stakeholders and roles mentioned</h3>
          <p className="small">{summary.stakeholders?.length ? summary.stakeholders.join(', ') : 'None identified.'}</p>
          <h3>Systems mentioned</h3>
          <p className="small">{summary.systems?.length ? summary.systems.join(', ') : 'None identified.'}</p>
          <h3>Organization name found</h3>
          <p className="small">{summary.organization_name || 'Not stated in the documents.'}</p>
        </div>
        <div>
          <h3>Risk overview</h3>
          <RiskLevelSummary summary={analysis.risk_summary} />
          <BarList title="Gap analysis" data={analysis.gap_summary.by_status} labelFor={titleCase} toneFor={(key) => ({ compliant: 'ok', partially_addressed: 'warn', not_evidenced: 'bad' })[key] || 'muted'} />
        </div>
      </section>
      {analysis.contradictions.length > 0 && (
        <section>
          <h3>Contradictions between documents</h3>
          <ul className="finding-list">
            {analysis.contradictions.map((item, index) => (
              <li key={index} className="finding">
                <h3>{item.topic}</h3>
                <p>{item.description}</p>
                <ul className="small">
                  {item.sources.map((source, i) => (
                    <li key={i}>
                      <strong>{source.file_name}</strong>
                      {source.detail ? `: ${source.detail}` : ''}
                    </li>
                  ))}
                </ul>
              </li>
            ))}
          </ul>
        </section>
      )}
      {analysis.security_flags.length > 0 && (
        <section className="notice notice-warn" role="note">
          <p>
            <ShieldAlert size={16} aria-hidden="true" /> <strong>Instruction-like text was found in the uploaded content.</strong> It was treated as document
            text only and was not followed.
          </p>
          <ul className="small">
            {analysis.security_flags.map((flag, index) => (
              <li key={index}>
                <strong>{flag.file_name}</strong> ({flag.type}): “{flag.excerpt}”
              </li>
            ))}
          </ul>
        </section>
      )}
      {analysis.processing?.warnings?.length > 0 && (
        <section className="notice notice-warn">
          <strong>Processing notes</strong>
          <ul className="small">
            {analysis.processing.warnings.map((warning) => (
              <li key={warning}>{warning}</li>
            ))}
          </ul>
        </section>
      )}
    </div>
  );
}

function RisksTab({ analysis, config, onUpdate }) {
  return (
    <div className="stack">
      <div className="three-col">
        <div>
          <h3>Risk matrix</h3>
          <RiskMatrix matrix={analysis.risk_summary.matrix} scoring={analysis.scoring_method} />
        </div>
        <BarList title="By severity" data={analysis.risk_summary.by_level} labelFor={titleCase} toneFor={(key) => ({ critical: 'bad', high: 'warn', medium: 'note', low: 'ok' })[key] || 'muted'} />
        <div>
          <BarList title="By origin" data={analysis.risk_summary.by_origin} labelFor={(key) => (key === 'documented' ? 'Documented in source' : 'Inferred by analysis')} toneFor={(key) => (key === 'documented' ? 'info' : 'note')} />
          <BarList title="By category" data={Object.fromEntries(Object.entries(analysis.risk_summary.by_category).slice(0, 6))} />
        </div>
      </div>
      <p className="muted small">
        Scoring: {analysis.scoring_method.formula}. {analysis.scoring_method.levels.map((band) => `${band.label} ${band.range}`).join(' · ')}. Excluded risks are left
        out of these charts.
      </p>
      <FindingList
        kind="risk"
        items={analysis.risks}
        config={config}
        onUpdate={onUpdate}
        emptyText="No risks were identified."
        filters={{
          label: 'Severity',
          options: ['critical', 'high', 'medium', 'low', 'unrated'].map((value) => ({ value, label: titleCase(value) })),
          test: (item, value) => item.level === value,
        }}
      />
    </div>
  );
}

function MissingTab({ analysis }) {
  return (
    <div className="stack">
      <p className="muted small">
        These are details a policy needs but the documents do not provide. The generated policy marks them with placeholders such as
        <mark className="placeholder">[To be confirmed: policy owner]</mark> instead of inventing values.
      </p>
      {analysis.missing_information.length === 0 ? (
        <EmptyState title="Nothing flagged as missing" />
      ) : (
        <div className="table-wrap">
          <table className="table">
            <thead>
              <tr>
                <th scope="col">Missing information</th>
                <th scope="col">Why it matters</th>
                <th scope="col">Suggested owner</th>
              </tr>
            </thead>
            <tbody>
              {analysis.missing_information.map((item, index) => (
                <tr key={index}>
                  <td>{item.item}</td>
                  <td>{item.why_it_matters}</td>
                  <td>{item.suggested_owner || '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <h3>Assumptions made by the analysis</h3>
      {analysis.assumptions.length ? (
        <ul>
          {analysis.assumptions.map((assumption) => (
            <li key={assumption}>{assumption}</li>
          ))}
        </ul>
      ) : (
        <p className="muted">None recorded.</p>
      )}
    </div>
  );
}

function SourcesTab({ analysis }) {
  return (
    <div className="stack">
      <p className="muted small">
        Every finding with evidence is listed with the file and location it came from. A quote is marked verified when it was found in the
        extracted text of that file.
      </p>
      {analysis.standards_mentioned.length > 0 && (
        <p className="small">
          <strong>Standards and laws mentioned in the documents:</strong>{' '}
          {analysis.standards_mentioned.map((item) => `${item.name} (${item.file_name})`).join('; ')}. Mention is not evidence of compliance.
        </p>
      )}
      <div className="table-wrap">
        <table className="table">
          <caption className="sr-only">Source references for findings</caption>
          <thead>
            <tr>
              <th scope="col">Finding</th>
              <th scope="col">File</th>
              <th scope="col">Location</th>
              <th scope="col">Evidence</th>
              <th scope="col">Verified</th>
            </tr>
          </thead>
          <tbody>
            {analysis.source_references.map((ref, index) => (
              <tr key={index}>
                <td>
                  <span className="ref-id">{ref.ref_id}</span> <span className="small">{ref.title}</span>
                </td>
                <td>{ref.file_name}</td>
                <td>{ref.location || '—'}</td>
                <td className="small">{ref.evidence ? `“${ref.evidence}”` : '—'}</td>
                <td>{ref.evidence ? <Badge tone={ref.evidence_verified ? 'ok' : 'warn'} label={ref.evidence_verified ? 'Yes' : 'No'} /> : '—'}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

export default function AnalysisPage() {
  const { analysisId } = useParams();
  const { config } = useSystem();
  const toast = useToast();
  const navigate = useNavigate();
  const [tab, setTab] = useState('summary');
  const { loading, error, data: analysis, reload, setData } = useAsync(() => api.getAnalysis(analysisId), [analysisId]);

  const pending = analysis && !analysis.completed;
  const { job, error: jobError, cancel, retry: retryJob } = useJob(pending ? analysis.job_id : null, () => reload({ silent: true }));

  const retry = async () => {
    try {
      await retryJob();
    } catch (err) {
      toast.error(err.message);
    }
  };

  const updateFinding = useCallback(
    async (item, patch) => {
      try {
        await api.updateFinding(analysisId, item.id, patch);
        // Reload so the charts, gap summary and control matrix reflect the change.
        setData(await api.getAnalysis(analysisId));
        if (patch.restore) toast.info(`${item.ref_id} restored to the original AI finding.`);
        else if (patch.excluded !== undefined) toast.info(`${item.ref_id} ${patch.excluded ? 'excluded from' : 'included in'} policy generation.`);
        else toast.success(`${item.ref_id} updated.`);
      } catch (err) {
        toast.error(err.message);
        throw err;
      }
    },
    [analysisId, setData, toast],
  );

  if (loading && !analysis) return <LoadingBlock label="Loading analysis…" />;
  if (error) return <ErrorState title="The analysis could not be loaded" message={error.message} onRetry={reload} />;

  if (pending) {
    // The heading follows the real job status, so a stopped analysis never reads as still running.
    const stopped = job?.status === 'failed' || job?.status === 'cancelled';
    const title = job?.status === 'failed' ? 'Analysis failed' : job?.status === 'cancelled' ? 'Analysis cancelled' : 'Analyzing documents';
    return (
      <>
        <PageHeader title={title} subtitle={`Step 2 of 4 · ${analysis.source_files.join(', ')}`} />
        <JobProgress title="AI analysis" job={job} error={jobError} steps={ANALYSIS_STEPS} onCancel={cancel} onRetry={retry} />
        <p className="muted small">
          {stopped
            ? 'Nothing is running. Retry continues from the parts that were already analyzed.'
            : 'You can leave this page; the analysis continues in the background and appears on the dashboard.'}
        </p>
      </>
    );
  }

  const active = (items) => items.filter((item) => !item.excluded).length;
  const tabs = [
    { id: 'summary', label: 'Document Summary' },
    { id: 'requirements', label: 'Requirements', count: active(analysis.requirements) },
    { id: 'risks', label: 'Risks', count: active(analysis.risks) },
    { id: 'controls', label: 'Controls', count: active(analysis.controls) },
    { id: 'gaps', label: 'Gap Analysis', count: active(analysis.gaps) },
    { id: 'recommendations', label: 'Recommendations', count: active(analysis.recommendations) },
    { id: 'missing', label: 'Missing Information', count: analysis.missing_information.length },
    { id: 'sources', label: 'Source References', count: analysis.source_references.length },
  ];
  const categoryOptions = Object.entries(config?.requirement_categories || {}).map(([value, label]) => ({ value, label }));

  return (
    <>
      <PageHeader
        title="Analysis results"
        subtitle={`Step 2 of 4 · ${analysis.source_files.map((file) => file.file_name).join(', ')} · ${formatDateTime(analysis.created_at)}`}
        actions={
          <button type="button" className="btn btn-primary btn-large" onClick={() => navigate(`/analyses/${analysisId}/configure`)}>
            Configure policy <ArrowRight size={18} aria-hidden="true" />
          </button>
        }
      />
      <div className="badge-row">
        {analysis.simulated ? <SimulatedTag /> : <Badge plain tone="info" label={`Analyzed by ${providerLabel(config, analysis.provider)} · ${analysis.model}`} />}
        {analysis.processing?.chunks_failed > 0 && <Badge tone="warn" label={`${analysis.processing.chunks_failed} segment(s) not analyzed`} />}
      </div>
      <p className="notice notice-info small">
        {analysis.disclaimer} Review the findings below; you can edit or exclude any of them before generating the policy.
      </p>

      <Tabs tabs={tabs} active={tab} onChange={setTab} label="Analysis sections" />
      <div className="card">
        <TabPanel id={tab}>
          {tab === 'summary' && <Summary analysis={analysis} />}
          {tab === 'requirements' && (
            <FindingList
              kind="requirement"
              items={analysis.requirements}
              config={config}
              onUpdate={updateFinding}
              emptyText="No requirements were identified in the documents."
              filters={{ label: 'Category', options: categoryOptions, test: (item, value) => item.category === value }}
            />
          )}
          {tab === 'risks' && <RisksTab analysis={analysis} config={config} onUpdate={updateFinding} />}
          {tab === 'controls' && (
            <div className="stack">
              <div className="three-col">
                <BarList title="Existing vs recommended" data={{ existing: analysis.control_summary.existing, recommended: analysis.control_summary.recommended }} labelFor={titleCase} toneFor={(key) => (key === 'existing' ? 'ok' : 'note')} />
                <BarList title="By type" data={analysis.control_summary.by_type} labelFor={titleCase} />
                <BarList title="By function" data={analysis.control_summary.by_function} labelFor={titleCase} />
              </div>
              <FindingList
                kind="control"
                items={analysis.controls}
                config={config}
                onUpdate={updateFinding}
                emptyText="No controls were identified."
                filters={{
                  label: 'Basis',
                  options: [
                    { value: 'existing', label: 'Existing' },
                    { value: 'recommended', label: 'Recommended' },
                  ],
                  test: (item, value) => item.basis === value,
                }}
              />
            </div>
          )}
          {tab === 'gaps' && (
            <div className="stack">
              <p className="muted small">{analysis.disclaimer}</p>
              <FindingList
                kind="gap"
                items={analysis.gaps}
                config={config}
                onUpdate={updateFinding}
                emptyText="No gap analysis is available because no requirements were identified."
                filters={{
                  label: 'Assessment',
                  options: Object.entries(config?.gap_statuses || {}).map(([value, label]) => ({ value, label })),
                  test: (item, value) => item.status === value,
                }}
              />
            </div>
          )}
          {tab === 'recommendations' && (
            <div className="stack">
              <FindingList kind="recommendation" items={analysis.recommendations} config={config} onUpdate={updateFinding} emptyText="No recommendations were produced." />
              <h3>Responsibilities identified</h3>
              <FindingList kind="responsibility" items={analysis.responsibilities} config={config} onUpdate={updateFinding} emptyText="The documents do not assign responsibilities." />
            </div>
          )}
          {tab === 'missing' && <MissingTab analysis={analysis} />}
          {tab === 'sources' && <SourcesTab analysis={analysis} />}
        </TabPanel>
      </div>
      <p className="muted small">
        <Link to="/new">Analyze different files</Link>
      </p>
    </>
  );
}
