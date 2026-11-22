import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { PolicyConfigForm } from '../components/PolicyConfigForm.jsx';
import { JobProgress, StageTracker } from '../components/StageTracker.jsx';
import AnalysisPage from '../pages/AnalysisPage.jsx';
import ConfigurePage from '../pages/ConfigurePage.jsx';
import { analysis, job } from './fixtures.js';
import { CONFIG, httpError, mockApi, renderPage } from './helpers.jsx';

afterEach(() => vi.unstubAllGlobals());

const stageState = (label) => screen.getByText(label).closest('li').className;

describe('processing status', () => {
  it('marks earlier stages done, the current one active and later ones pending', () => {
    render(<StageTracker stage="analyzing" />);
    expect(stageState('Extracted')).toContain('stage-done');
    expect(stageState('Analyzing')).toContain('stage-active');
    expect(stageState('Generating Policy')).toContain('stage-pending');
    expect(screen.getByText('Analyzing').closest('li')).toHaveAttribute('aria-current', 'step');
  });

  it('shows the real progress of a running job with a cancel button', async () => {
    const onCancel = vi.fn();
    render(<JobProgress title="AI analysis" job={job()} onCancel={onCancel} onRetry={() => {}} />);
    expect(screen.getByRole('progressbar')).toHaveAttribute('aria-valuenow', '40');
    expect(screen.getByText('Analyzing security.txt.')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Retry' })).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: 'Cancel' }));
    expect(onCancel).toHaveBeenCalled();
  });

  it('never shows a failed job as completed: it shows the error, the failed stage and Retry', async () => {
    const onRetry = vi.fn();
    const failed = job({ status: 'failed', stage: 'failed', stage_label: 'Failed', error_message: 'The AI provider did not respond in time.', result: { failed_stage: 'analyzing' } });
    render(<JobProgress title="AI analysis" job={failed} onCancel={() => {}} onRetry={onRetry} />);
    expect(screen.getByRole('alert')).toHaveTextContent('The AI provider did not respond in time.');
    expect(stageState('Analyzing')).toContain('stage-failed');
    expect(stageState('Completed')).toContain('stage-pending');
    expect(screen.queryByRole('progressbar')).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: 'Retry' }));
    expect(onRetry).toHaveBeenCalled();
  });

  it('shows a loading state before the first status arrives', () => {
    render(<JobProgress title="AI analysis" job={null} />);
    expect(screen.getByText('Checking progress…')).toBeInTheDocument();
  });
});

describe('AnalysisPage', () => {
  it('follows a running analysis and shows the results when it completes', async () => {
    let done = false;
    mockApi({
      'GET /api/analyses/a1': () => (done ? analysis : { id: 'a1', job_id: 'j1', status: 'analyzing', completed: false, source_files: ['security.txt'] }),
      'GET /api/jobs/j1': () => {
        const current = done ? job({ status: 'completed', stage: 'analysis_completed', stage_label: 'Analysis Completed', progress: 100 }) : job();
        done = true;
        return current;
      },
    });
    renderPage(<AnalysisPage />, { path: '/analyses/:analysisId', route: '/analyses/a1' });
    expect(await screen.findByRole('heading', { name: 'Analyzing documents' })).toBeInTheDocument();
    expect(await screen.findByText('Analyzing security.txt.')).toBeInTheDocument();
    expect(await screen.findByRole('heading', { name: 'Analysis results' }, { timeout: 4000 })).toBeInTheDocument();
  });

  it('shows the findings in tabs with source evidence and simulated labelling', async () => {
    mockApi({ 'GET /api/analyses/a1': analysis });
    renderPage(<AnalysisPage />, { path: '/analyses/:analysisId', route: '/analyses/a1' });
    expect(await screen.findByText('Simulated analysis of one file.')).toBeInTheDocument();
    expect(screen.getByText('Simulated')).toBeInTheDocument();
    const tabs = screen.getAllByRole('tab').map((tab) => tab.textContent);
    expect(tabs).toEqual(['Document Summary', 'Requirements2', 'Risks2', 'Controls1', 'Gap Analysis1', 'Recommendations1', 'Missing Information1', 'Source References1']);
    expect(screen.getByText('Minimum password length')).toBeInTheDocument(); // contradiction

    await userEvent.click(screen.getByRole('tab', { name: /Requirements/ }));
    const requirement = screen.getByText('Passwords must be at least 14 characters long.', { selector: 'h3' }).closest('li');
    expect(within(requirement).getByText('REQ-001')).toBeInTheDocument();
    expect(within(requirement).getByText(/Quote found in the source text/)).toBeInTheDocument();
    expect(within(requirement).getByText(/3\. Access Control/)).toBeInTheDocument();

    await userEvent.click(screen.getByRole('tab', { name: /Risks/ }));
    expect(screen.getAllByText('Inferred by analysis').length).toBeGreaterThan(0);
    expect(screen.getByText(/Inferred risks are possibilities to review/)).toBeInTheDocument();
    expect(screen.getByLabelText('Impact 5, likelihood 3: 1 risks, high')).toHaveTextContent('1');

    await userEvent.click(screen.getByRole('tab', { name: /Gap Analysis/ }));
    expect(screen.getByText('Not Evidenced', { selector: '.badge' })).toBeInTheDocument();
    await userEvent.click(screen.getByRole('tab', { name: /Missing Information/ }));
    expect(screen.getByText('Exception and approval process')).toBeInTheDocument();
    await userEvent.click(screen.getByRole('tab', { name: /Source References/ }));
    expect(screen.getByRole('table', { name: 'Source references for findings' })).toBeInTheDocument();
  });

  it('excludes and edits findings through the API and marks user edits', async () => {
    const edited = structuredClone(analysis);
    const { calls } = mockApi({
      'GET /api/analyses/a1': () => edited,
      'PATCH /api/analyses/a1/findings/1': ({ options }) => {
        const patch = JSON.parse(options.body);
        if (patch.excluded !== undefined) edited.requirements[0].excluded = patch.excluded;
        if (patch.data) {
          edited.requirements[0] = { ...edited.requirements[0], ...patch.data, user_edited: true, original: { statement: analysis.requirements[0].statement } };
        }
        return edited.requirements[0];
      },
    });
    renderPage(<AnalysisPage />, { path: '/analyses/:analysisId', route: '/analyses/a1' });
    await userEvent.click(await screen.findByRole('tab', { name: /Requirements/ }));
    const item = () => screen.getByText('REQ-001').closest('li');

    await userEvent.click(within(item()).getByRole('button', { name: /Exclude/ }));
    expect(await within(item()).findByText('Excluded from policy')).toBeInTheDocument();
    expect(JSON.parse(calls.find((call) => call.method === 'PATCH').body)).toEqual({ excluded: true });
    expect(screen.getByRole('tab', { name: /Requirements/ })).toHaveTextContent('Requirements1');

    await userEvent.click(within(item()).getByRole('button', { name: /Edit/ }));
    const dialog = screen.getByRole('dialog', { name: 'Edit REQ-001' });
    const field = within(dialog).getByLabelText('Requirement');
    await userEvent.clear(field);
    await userEvent.type(field, 'Passwords must be at least 16 characters long.');
    await userEvent.click(within(dialog).getByRole('button', { name: 'Save changes' }));
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
    expect(within(item()).getByText('Edited by you')).toBeInTheDocument();
    expect(within(item()).getByRole('button', { name: /Restore original/ })).toBeInTheDocument();
    const patches = calls.filter((call) => call.method === 'PATCH').map((call) => JSON.parse(call.body));
    expect(patches[1]).toEqual({ data: { statement: 'Passwords must be at least 16 characters long.' } });
  });

  it('shows an error state when the analysis cannot be loaded', async () => {
    mockApi({ 'GET /api/analyses/a1': httpError(404, 'not_found', 'The analysis was not found. It may have been deleted.') });
    renderPage(<AnalysisPage />, { path: '/analyses/:analysisId', route: '/analyses/a1' });
    expect(await screen.findByRole('alert')).toHaveTextContent('The analysis was not found. It may have been deleted.');
  });
});

describe('PolicyConfigForm', () => {
  const recommendations = [
    { policy_type: 'access_control', name: 'Access Control Policy', fit: 'strong', reasons: ['7 identified requirement(s) fall in areas this policy covers.'] },
    { policy_type: 'vendor_management', name: 'Vendor Management Policy', fit: 'weak', reasons: ['Related terms appear in the findings: vendor.'] },
  ];
  const setup = (props = {}) => {
    const onSubmit = vi.fn();
    render(
      <PolicyConfigForm config={CONFIG} policyTypes={CONFIG.policy_types} recommendations={recommendations} detectedOrganization="Brightwater Analytics Pvt. Ltd." initial={{ policy_type: 'access_control' }} onSubmit={onSubmit} {...props} />,
    );
    return onSubmit;
  };

  it('shows the recommended policy with its reasons and lets the user choose another', async () => {
    setup();
    expect(screen.getByLabelText('Recommended policy type')).toHaveTextContent('Access Control Policy');
    expect(screen.getByText('7 identified requirement(s) fall in areas this policy covers.')).toBeInTheDocument();
    const select = screen.getByLabelText('Policy type to generate');
    expect(select).toHaveValue('access_control');
    await userEvent.selectOptions(select, 'vendor_management');
    expect(screen.getByText('Third-party rules.')).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: 'Use this type' }));
    expect(select).toHaveValue('access_control');
  });

  it('submits every customization control', async () => {
    const onSubmit = setup();
    await userEvent.click(screen.getByRole('button', { name: 'Brightwater Analytics Pvt. Ltd.' }));
    await userEvent.type(screen.getByLabelText('Policy owner'), 'Head of IT');
    await userEvent.type(screen.getByLabelText('Department'), 'IT');
    await userEvent.clear(screen.getByLabelText('Document version'));
    await userEvent.type(screen.getByLabelText('Document version'), '2.0');
    await userEvent.selectOptions(screen.getByLabelText('Review frequency'), 'Quarterly');
    await userEvent.click(screen.getByRole('radio', { name: /Technical/ }));
    await userEvent.click(screen.getByRole('radio', { name: /Comprehensive/ }));
    expect(screen.queryByRole('checkbox', { name: /Gap analysis/ })).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole('checkbox', { name: /Word document/ }));
    await userEvent.click(screen.getByRole('button', { name: 'Generate policy' }));
    expect(onSubmit).toHaveBeenCalledTimes(1);
    expect(onSubmit.mock.calls[0][0]).toMatchObject({
      policy_type: 'access_control',
      organization_name: 'Brightwater Analytics Pvt. Ltd.',
      policy_owner: 'Head of IT',
      department: 'IT',
      document_version: '2.0',
      review_frequency: 'Quarterly',
      writing_style: 'technical',
      detail_level: 'comprehensive',
      include_gap_analysis: false,
      include_risk_register: false,
      output_formats: ['pdf'],
      effective_date: '',
    });
  });

  it('validates required choices before submitting', async () => {
    const onSubmit = setup({ initial: { policy_type: '' }, recommendations: [] });
    await userEvent.click(screen.getByRole('checkbox', { name: /Word document/ }));
    await userEvent.click(screen.getByRole('checkbox', { name: /PDF document/ }));
    await userEvent.click(screen.getByRole('button', { name: 'Generate policy' }));
    expect(onSubmit).not.toHaveBeenCalled();
    expect(screen.getByText('Choose a policy type.')).toBeInTheDocument();
    expect(screen.getByText('Choose at least one output format.')).toBeInTheDocument();
    expect(screen.getByLabelText('Policy type to generate')).toHaveAttribute('aria-invalid', 'true');
  });
});

describe('ConfigurePage', () => {
  it('loads recommendations, starts generation and opens the report', async () => {
    const { calls } = mockApi({
      'POST /api/policies/recommend': {
        recommended: { policy_type: 'access_control', name: 'Access Control Policy', fit: 'strong', reasons: ['Evidence.'] },
        recommendations: [{ policy_type: 'access_control', name: 'Access Control Policy', fit: 'strong', reasons: ['Evidence.'] }],
        detected_organization_name: 'Brightwater Analytics Pvt. Ltd.',
      },
      'POST /api/policies/generate': { report_id: 'r1', job: { id: 'j2' } },
    });
    renderPage(<ConfigurePage />, { path: '/analyses/:analysisId/configure', route: '/analyses/a1/configure', extraRoutes: [['/reports/:id', 'report page']] });
    expect(await screen.findByLabelText('Organization name')).toHaveValue('Brightwater Analytics Pvt. Ltd.');
    await userEvent.click(screen.getByRole('button', { name: 'Generate policy' }));
    expect(await screen.findByText('report page')).toBeInTheDocument();
    const body = JSON.parse(calls.find((call) => call.url === '/api/policies/generate').body);
    expect(body.analysis_id).toBe('a1');
    expect(body.config.policy_type).toBe('access_control');
  });

  it('explains why configuration is unavailable when the analysis is not ready', async () => {
    mockApi({ 'POST /api/policies/recommend': httpError(409, 'analysis_not_ready', 'The analysis has not completed yet.') });
    renderPage(<ConfigurePage />, { path: '/analyses/:analysisId/configure', route: '/analyses/a1/configure' });
    expect(await screen.findByRole('alert')).toHaveTextContent('The analysis has not completed yet.');
    expect(screen.getByRole('link', { name: /Back to analysis/ })).toHaveAttribute('href', '/analyses/a1');
  });
});

describe('AnalysisPage while the model is slow', () => {
  const pending = { id: 'a1', job_id: 'j1', status: 'analyzing', completed: false, source_files: ['policy.pdf'] };

  it('shows what the model is doing, then a clear failure instead of staying on "Analyzing", and retries the same job', async () => {
    const timedOut = 'AI analysis failed for every document segment. The model vendor/slow:free did not answer within the time limit (600 s).';
    const states = [
      job({ progress: 5, message: 'Analyzing policy.pdf (part 1 of 7). vendor/slow:free is writing its answer (1 min 15 s).' }),
      job({ status: 'failed', stage: 'failed', stage_label: 'Failed', progress: 5, error_message: timedOut, result: { failed_stage: 'analyzing' } }),
    ];
    let retried = false;
    const { calls } = mockApi({
      'GET /api/analyses/a1': () => (retried ? analysis : { ...pending, status: states.length ? 'analyzing' : 'failed' }),
      'GET /api/jobs/j1': () => (retried ? job({ status: 'completed', stage: 'analysis_completed', stage_label: 'Analysis Completed', progress: 100 }) : states.length > 1 ? states.shift() : states[0]),
      'POST /api/jobs/j1/retry': () => {
        retried = true;
        return job({ status: 'queued', stage: 'extracted', stage_label: 'Extracted', progress: 0, message: 'Queued for retry.' });
      },
    });
    renderPage(<AnalysisPage />, { path: '/analyses/:analysisId', route: '/analyses/a1' });

    expect(await screen.findByText(/part 1 of 7\)\. vendor\/slow:free is writing its answer \(1 min 15 s\)\./)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Cancel' })).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: 'Analyzing documents' })).toBeInTheDocument();

    expect(await screen.findByRole('alert', {}, { timeout: 4000 })).toHaveTextContent('did not answer within the time limit (600 s)');
    expect(screen.getByRole('heading', { name: 'Analysis failed' })).toBeInTheDocument();
    expect(screen.queryByRole('heading', { name: 'Analyzing documents' })).not.toBeInTheDocument();
    expect(screen.getByText(/Nothing is running\. Retry continues from the parts/)).toBeInTheDocument();
    expect(stageState('Analyzing')).toContain('stage-failed');
    expect(screen.queryByRole('progressbar')).not.toBeInTheDocument();
    expect(screen.queryByRole('heading', { name: 'Analysis results' })).not.toBeInTheDocument();

    await userEvent.click(screen.getByRole('button', { name: 'Retry' }));
    expect(await screen.findByRole('heading', { name: 'Analysis results' }, { timeout: 4000 })).toBeInTheDocument();
    expect(calls.filter((call) => call.method === 'POST')).toHaveLength(1); // one retry of the same job, no new analysis
  });

  it('sends one cancel request and shows that cancellation is in progress', async () => {
    let cancelling = false;
    const { calls } = mockApi({
      'GET /api/analyses/a1': pending,
      'GET /api/jobs/j1': () => job({ cancel_requested: cancelling, message: cancelling ? 'Cancellation requested.' : 'Analyzing policy.pdf.' }),
      'POST /api/jobs/j1/cancel': () => {
        cancelling = true;
        return job({ cancel_requested: true, message: 'Cancellation requested.' });
      },
    });
    renderPage(<AnalysisPage />, { path: '/analyses/:analysisId', route: '/analyses/a1' });
    await userEvent.click(await screen.findByRole('button', { name: 'Cancel' }));
    const button = await screen.findByRole('button', { name: 'Cancelling…' });
    expect(button).toBeDisabled(); // cannot be pressed twice
    expect(calls.filter((call) => call.url.endsWith('/cancel'))).toHaveLength(1);
  });
});
