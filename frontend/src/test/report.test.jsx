import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';
import AppLayout from '../layouts/AppLayout.jsx';
import Dashboard from '../pages/Dashboard.jsx';
import ReportPage from '../pages/ReportPage.jsx';
import ReportsPage from '../pages/ReportsPage.jsx';
import { countMatches, splitForDisplay } from '../utils/format.js';
import { job, report } from './fixtures.js';
import { HEALTH, httpError, mockApi, renderPage } from './helpers.jsx';

afterEach(() => vi.unstubAllGlobals());

const openReport = (routes = {}) => {
  const api = mockApi({ 'GET /api/reports/r1': report, ...routes });
  renderPage(<ReportPage />, { path: '/reports/:reportId', route: '/reports/r1' });
  return api;
};

describe('ReportPage preview', () => {
  it('shows the policy title, details, sections and the review notice', async () => {
    openReport();
    expect(await screen.findByRole('heading', { level: 1, name: 'Access Control Policy' })).toBeInTheDocument();
    expect(screen.getByText('Version 1 (latest)')).toBeInTheDocument();
    expect(screen.getByText('Review: Draft')).toBeInTheDocument();
    expect(screen.getByText('Simulated')).toBeInTheDocument();
    const preview = screen.getByRole('article', { name: 'Policy preview' });
    expect(within(preview).getByRole('heading', { name: '1. Purpose' })).toBeInTheDocument();
    expect(within(preview).getByRole('heading', { name: 'Appendix A – Risk Register' })).toBeInTheDocument();
    expect(within(preview).getByText('ACP-001')).toBeInTheDocument();
    expect(within(preview).getByText(/Documented in source material · References:/)).toBeInTheDocument();
    expect(within(preview).getByText(/SIMULATED OUTPUT \(mock mode\)/)).toBeInTheDocument();
    expect(screen.getAllByText(/must be reviewed and approved before adoption/).length).toBeGreaterThan(0);
    // Unknown details are visibly marked, not invented.
    expect(within(preview).getByText('[To be confirmed: policy owner]').tagName).toBe('MARK');
  });

  it('navigates by section and searches within the report', async () => {
    openReport();
    const nav = await screen.findByRole('navigation', { name: 'Jump to section' });
    expect(within(nav).getAllByRole('button').map((button) => button.textContent)).toEqual(['Document control', '1. Purpose', '2. Scope', 'Appendix A – Risk Register']);
    const scrolled = vi.spyOn(window.HTMLElement.prototype, 'scrollIntoView');
    await userEvent.click(within(nav).getByRole('button', { name: '2. Scope' }));
    await waitFor(() => expect(scrolled).toHaveBeenCalled());

    await userEvent.type(screen.getByLabelText(/Search in this report/), 'systems');
    expect(await screen.findByText('4 matches')).toBeInTheDocument();
    expect(document.querySelectorAll('mark.search-hit')).toHaveLength(4); // the count equals what is highlighted
    await userEvent.clear(screen.getByLabelText(/Search in this report/));
    await userEvent.type(screen.getByLabelText(/Search in this report/), 'zzzz');
    expect(await screen.findByText('0 matches')).toBeInTheDocument();
  });

  it('downloads DOCX and PDF for the version being viewed', async () => {
    const api = openReport({
      'GET /api/reports/r1/download': () =>
        new Response(new Blob(['%PDF-1.4'], { type: 'application/pdf' }), {
          status: 200,
          headers: { 'Content-Disposition': 'attachment; filename="Access_Control_Policy_v1.pdf"' },
        }),
    });
    await screen.findByRole('article', { name: 'Policy preview' });
    const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {});
    await userEvent.click(screen.getByRole('button', { name: /Download PDF/ }));
    expect(await screen.findByText('Access_Control_Policy_v1.pdf downloaded.')).toBeInTheDocument();
    expect(click).toHaveBeenCalledTimes(1);
    expect(api.calls.filter((call) => call.url.includes('/download')).map((call) => call.url)).toEqual(['/api/reports/r1/download?format=pdf&version=1']);
    expect(screen.getByRole('button', { name: /Download DOCX/ })).toBeEnabled();
    click.mockRestore();
  });

  it('shows a download error instead of a success message when the server fails', async () => {
    openReport({ 'GET /api/reports/r1/download': httpError(500, 'formatting_failed', 'The PDF document could not be created.') });
    await screen.findByRole('article', { name: 'Policy preview' });
    await userEvent.click(screen.getByRole('button', { name: /Download PDF/ }));
    expect(await screen.findByRole('alert')).toHaveTextContent('The PDF document could not be created.');
    expect(screen.queryByText(/downloaded\./)).not.toBeInTheDocument();
  });

  it('saves an edited section only after confirmation, as a new version', async () => {
    const updated = structuredClone(report);
    updated.current_version = 2;
    updated.version.version_number = 2;
    const api = openReport({ 'PUT /api/reports/r1/sections/purpose': { version: 2, report: updated } });
    await userEvent.click(await screen.findByRole('button', { name: 'Edit section Purpose' }));
    const editor = screen.getByRole('dialog', { name: 'Edit: Purpose' });
    const save = within(editor).getByRole('button', { name: 'Save as new version' });
    expect(save).toBeDisabled(); // nothing changed yet
    await userEvent.type(within(editor).getByLabelText('Section content'), ' Reviewed by security.');
    await userEvent.click(save);

    const confirm = screen.getByRole('dialog', { name: 'Save your edit as a new version?' });
    expect(confirm).toHaveTextContent('Version 1 is kept unchanged');
    expect(api.calls.some((call) => call.method === 'PUT')).toBe(false); // not saved before confirming
    await userEvent.click(within(confirm).getByRole('button', { name: 'Save new version' }));

    expect(await screen.findByText('Saved as version 2. The previous version is kept.')).toBeInTheDocument();
    const put = api.calls.find((call) => call.method === 'PUT');
    expect(JSON.parse(put.body)).toEqual({ markup: 'The purpose of this policy is to control access. Reviewed by security.', note: '', base_version: 1 });
    expect(await screen.findByText('Version 2 (latest)')).toBeInTheDocument();
  });

  it('regenerates selected sections with an optional instruction', async () => {
    const api = openReport({
      'POST /api/reports/r1/sections/regenerate': { job: { id: 'j9' } },
      'GET /api/jobs/j9': job({ id: 'j9', type: 'revision', status: 'completed', stage: 'completed', stage_label: 'Completed', progress: 100, message: 'Version 2 created.', result: { changed: true, version: 2 } }),
    });
    await userEvent.click(await screen.findByRole('button', { name: 'Regenerate section Scope' }));
    const dialog = screen.getByRole('dialog', { name: 'Regenerate sections' });
    expect(within(dialog).getByRole('checkbox', { name: 'Scope' })).toBeChecked();
    expect(within(dialog).getByText(/cannot follow written instructions/)).toBeInTheDocument(); // honest about mock mode
    await userEvent.type(within(dialog).getByLabelText(/Instruction for the revision/), 'Make it shorter.');
    await userEvent.click(within(dialog).getByRole('button', { name: 'Regenerate 1 section' }));
    expect(await screen.findByText('Version 2 created.')).toBeInTheDocument();
    const post = api.calls.find((call) => call.method === 'POST');
    expect(JSON.parse(post.body)).toEqual({ section_ids: ['scope'], instruction: 'Make it shorter.' });
  });

  it('shows quality checks, versions and the review workflow', async () => {
    const approved = { ...structuredClone(report), review_status: 'approved', approved_version: 1, review_events: [{ action: 'approve', actor: 'Asha Rao', comment: '', version_number: 1, created_at: '2026-10-01T11:00:00+00:00' }] };
    const api = openReport({ 'POST /api/reports/r1/review': approved });
    await userEvent.click(await screen.findByRole('tab', { name: /Quality checks/ }));
    expect(screen.getByText(/No blocking issues were found/)).toBeInTheDocument();
    expect(screen.getByText('[To be confirmed: list of systems in scope]')).toBeInTheDocument();

    await userEvent.click(screen.getByRole('tab', { name: /Versions/ }));
    expect(screen.getByRole('table', { name: 'Versions of this policy' })).toHaveTextContent('Initial draft generated from the analysis.');
    expect(screen.getByText(/There is only one version so far/)).toBeInTheDocument();

    await userEvent.click(screen.getByRole('tab', { name: /Review & approval/ }));
    expect(screen.getByText('Human review is required.')).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: 'Approve' }));
    expect(screen.getByRole('alert')).toHaveTextContent("Enter the reviewer's name");
    expect(api.calls.some((call) => call.method === 'POST')).toBe(false);
    await userEvent.type(screen.getByLabelText('Reviewer name'), 'Asha Rao');
    await userEvent.click(screen.getByRole('button', { name: 'Approve' }));
    expect(await screen.findByText('Review: Approved')).toBeInTheDocument();
    expect(JSON.parse(api.calls.find((call) => call.method === 'POST').body)).toEqual({ action: 'approve', actor: 'Asha Rao', comment: '' });
  });

  it('shows generation progress, and a failure with Retry instead of a preview', async () => {
    const failed = { ...report, status: 'failed', current_version: 0, document: null, sections: [], version: null, quality_report: null, error_message: 'Policy generation failed.' };
    mockApi({
      'GET /api/reports/r1': failed,
      'GET /api/jobs/j2': job({ id: 'j2', type: 'generation', status: 'failed', stage: 'failed', stage_label: 'Failed', error_message: 'Policy generation failed. The AI provider is temporarily unavailable.', result: { failed_stage: 'generating_policy' } }),
    });
    renderPage(<ReportPage />, { path: '/reports/:reportId', route: '/reports/r1' });
    expect(await screen.findByRole('alert')).toHaveTextContent('The AI provider is temporarily unavailable.');
    expect(screen.getByRole('button', { name: 'Retry' })).toBeInTheDocument();
    expect(screen.queryByRole('article', { name: 'Policy preview' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /Download/ })).not.toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'change the configuration' })).toHaveAttribute('href', '/analyses/a1/configure');
  });

  it('shows an error state for a report that does not exist', async () => {
    mockApi({ 'GET /api/reports/r1': httpError(404, 'not_found', 'The report was not found. It may have been deleted.') });
    renderPage(<ReportPage />, { path: '/reports/:reportId', route: '/reports/r1' });
    expect(await screen.findByRole('alert')).toHaveTextContent('The report was not found.');
  });
});

describe('ReportsPage (history)', () => {
  const summary = { ...report, quality: { passed: true, score: 100 } };

  it('lists reports with type, sources, version and status, and deletes after confirmation', async () => {
    const { calls } = mockApi({ 'GET /api/reports': { reports: [summary] }, 'DELETE /api/reports/r1': { deleted: true, id: 'r1' } });
    renderPage(<ReportsPage />);
    const row = (await screen.findByRole('link', { name: 'Access Control Policy' })).closest('tr');
    expect(row).toHaveTextContent('security.txt');
    expect(row).toHaveTextContent('v1');
    expect(row).toHaveTextContent('completed');
    expect(within(row).getByRole('button', { name: 'Download Access Control Policy as PDF' })).toBeInTheDocument();
    expect(within(row).getByRole('button', { name: 'Download Access Control Policy as DOCX' })).toBeInTheDocument();

    await userEvent.click(within(row).getByRole('button', { name: 'Delete Access Control Policy' }));
    const dialog = screen.getByRole('dialog', { name: 'Delete this report?' });
    expect(dialog).toHaveTextContent('This cannot be undone');
    await userEvent.click(within(dialog).getByRole('button', { name: 'Cancel' }));
    expect(calls.some((call) => call.method === 'DELETE')).toBe(false);

    await userEvent.click(within(row).getByRole('button', { name: 'Delete Access Control Policy' }));
    await userEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Delete report' }));
    expect(await screen.findByText('No reports yet')).toBeInTheDocument();
  });

  it('shows an empty state with a call to action', async () => {
    mockApi({ 'GET /api/reports': { reports: [] } });
    renderPage(<ReportsPage />);
    expect(await screen.findByText('No reports yet')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Generate a policy' })).toHaveAttribute('href', '/new');
  });
});

describe('Dashboard and layout', () => {
  const dashboard = {
    stats: { files_uploaded: 6, files_processed: 5, analyses_completed: 2, policies_generated: 1, policies_approved: 0, jobs_active: 0, jobs_failed: 1 },
    recent_reports: [{ ...report }],
    recent_analyses: [],
    recent_jobs: [job({ id: 'j5', type: 'extraction', status: 'failed', stage: 'failed', stage_label: 'Failed', error_message: 'OCR is not available on this server.', subject_name: 'scan.png', created_at: '2026-10-01T10:00:00+00:00' })],
  };

  it('shows statistics, recent reports, processing history and system status', async () => {
    mockApi({ 'GET /api/dashboard': dashboard });
    renderPage(<Dashboard />);
    expect(await screen.findByText('Files processed')).toBeInTheDocument();
    expect(screen.getByText('Files processed').previousSibling).toHaveTextContent('5');
    expect(screen.getByText('Policies generated').previousSibling).toHaveTextContent('1');
    expect(screen.getByRole('link', { name: /New policy generation/ })).toHaveAttribute('href', '/new');
    expect(screen.getByRole('link', { name: 'Access Control Policy' })).toHaveAttribute('href', '/reports/r1');
    expect(screen.getByText('File extraction: scan.png')).toBeInTheDocument();
    expect(screen.getByText('OCR is not available on this server.')).toBeInTheDocument();
    expect(screen.getByText(/Mock mode \(no AI model; results are simulated\)/)).toBeInTheDocument();
    expect(screen.getByText(/OCR not installed/)).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: 'How it works' })).toBeInTheDocument();
  });

  it('labels mock mode in the shell and offers keyboard navigation aids', async () => {
    mockApi({});
    renderPage(<AppLayout />);
    expect(await screen.findByText(/Analysis and policies are produced by keyword rules/)).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Skip to main content' })).toHaveAttribute('href', '#main-content');
    const toggle = screen.getByRole('button', { name: 'Open navigation' });
    expect(toggle).toHaveAttribute('aria-expanded', 'false');
    await userEvent.click(toggle);
    expect(screen.getByRole('button', { name: 'Close navigation' })).toHaveAttribute('aria-expanded', 'true');
    expect(screen.getByRole('navigation', { name: 'Primary' })).toHaveClass('sidebar-open');
  });

  it('shows a clear error with retry when the backend is unreachable', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => { throw new TypeError('Failed to fetch'); }));
    renderPage(<AppLayout />);
    expect(await screen.findByRole('alert')).toHaveTextContent('The server could not be reached. Check that the backend is running.');
    expect(screen.getByRole('button', { name: 'Try again' })).toBeInTheDocument();
  });

  it('asks for the access token when the server requires one', async () => {
    const { calls } = mockApi({ 'GET /api/health': { ...HEALTH, auth_required: true } });
    sessionStorage.clear();
    renderPage(<AppLayout />);
    expect(await screen.findByRole('heading', { name: 'Access token required' })).toBeInTheDocument();
    await userEvent.type(screen.getByLabelText('Access token'), 'team-secret');
    await userEvent.click(screen.getByRole('button', { name: 'Continue' }));
    await waitFor(() => expect(calls.some((call) => call.url === '/api/config')).toBe(true));
    sessionStorage.clear();
  });

  it('has responsive breakpoints for tablet and mobile layouts', () => {
    const css = readFileSync(resolve(__dirname, '../styles.css'), 'utf8');
    for (const breakpoint of ['max-width: 1100px', 'max-width: 860px', 'max-width: 520px']) expect(css).toContain(breakpoint);
    expect(css).toMatch(/@media \(max-width: 860px\)[\s\S]*\.sidebar\s*\{[\s\S]*translateX\(-100%\)/);
    expect(css).toContain('prefers-reduced-motion');
  });
});

describe('text helpers', () => {
  it('splits text into placeholders and search hits', () => {
    const parts = splitForDisplay('Owner: [To be confirmed: policy owner] must review access.', 'review');
    expect(parts.map((part) => part.kind)).toEqual(['text', 'placeholder', 'text', 'match', 'text']);
    expect(parts[1].text).toBe('[To be confirmed: policy owner]');
    expect(countMatches('Access access ACCESS', 'access')).toBe(3);
    expect(countMatches('Access', 'a')).toBe(0); // one character is too short to search
  });
});

describe('ReportPage with the current policy structure', () => {
  const table = (columns, rows) => ({ type: 'table', text: '', items: [], statements: [], columns, rows });
  const structured = {
    ...report,
    sections: [
      { id: 'purpose', heading: 'Purpose', origin: 'ai_generated', markup: 'The purpose of this policy is to control access.' },
      { id: 'abbreviations', heading: 'Abbreviations', origin: 'ai_generated', markup: '| Abbreviation | Full Form / Description |\n| MFA | Multi-Factor Authentication |' },
      { id: 'scope', heading: 'Scope', origin: 'ai_generated', markup: 'All staff who use MFA.' },
    ],
    document: {
      ...report.document,
      sections: [
        report.document.sections[0],
        { id: 'abbreviations', number: '2', title: 'Abbreviations', kind: 'section', origin: 'ai_generated', elements: [table(['Abbreviation', 'Full Form / Description'], [['MFA', 'Multi-Factor Authentication'], ['RBAC', 'Role-Based Access Control']])] },
        { ...report.document.sections[1], number: '3' },
        { id: 'roles_responsibilities', number: '4', title: 'Roles and Responsibilities', kind: 'section', origin: 'ai_generated', elements: [table(['Role / Department', 'Responsibilities', 'Basis'], [['IT Manager', 'Approves access requests.', 'Documented']])] },
      ],
    },
  };

  it('shows Abbreviations as a two-column table immediately before Scope, with no Executive Summary', async () => {
    mockApi({ 'GET /api/reports/r1': structured });
    renderPage(<ReportPage />, { path: '/reports/:reportId', route: '/reports/r1' });
    const preview = await screen.findByRole('article', { name: 'Policy preview' });
    const headings = within(preview).getAllByRole('heading').map((heading) => heading.textContent).filter((text) => /^\d+\. /.test(text));
    expect(headings.slice(0, 4)).toEqual(['1. Purpose', '2. Abbreviations', '3. Scope', '4. Roles and Responsibilities']);
    expect(within(preview).queryByText(/Executive Summary/)).not.toBeInTheDocument();
    const abbreviations = within(preview).getByRole('heading', { name: '2. Abbreviations' }).closest('section');
    expect(within(abbreviations).getAllByRole('columnheader').map((cell) => cell.textContent)).toEqual(['Abbreviation', 'Full Form / Description']);
    expect(within(abbreviations).getByRole('row', { name: /RBAC\s*Role-Based Access Control/ })).toBeInTheDocument();
    expect(within(preview).getByRole('columnheader', { name: 'Role / Department' })).toBeInTheDocument();
    // The section can be edited and regenerated like any other.
    expect(screen.getByRole('button', { name: 'Edit section Abbreviations' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Regenerate section Abbreviations' })).toBeInTheDocument();
  });
});
