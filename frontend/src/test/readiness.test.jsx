import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, expect, it, vi } from 'vitest';
import ReportPage from '../pages/ReportPage.jsx';
import ReportsPage from '../pages/ReportsPage.jsx';
import { report } from './fixtures.js';
import { mockApi, renderPage } from './helpers.jsx';

afterEach(() => vi.unstubAllGlobals());

it('saves one confirmed value for all occurrences without an AI request', async () => {
  const { calls } = mockApi({ 'GET /api/reports/r1': report, 'POST /api/reports/r1/missing-information': report });
  renderPage(<ReportPage />, { path: '/reports/:reportId', route: '/reports/r1' });
  await userEvent.click(await screen.findByRole('tab', { name: /Missing information/ }));
  await userEvent.type(screen.getByLabelText(/list of systems in scope/), 'Approved inventory INV-01');
  await userEvent.click(screen.getByRole('button', { name: 'Save confirmed information' }));
  await waitFor(() => expect(calls.some((c) => c.url.endsWith('/missing-information'))).toBe(true));
  const saved = calls.find((c) => c.method === 'POST');
  expect(JSON.parse(saved.body)).toEqual({ base_version: 1, metadata: {}, replacements: { '[To be confirmed: list of systems in scope]': 'Approved inventory INV-01' } });
  expect(calls.filter((c) => c.method === 'POST')).toHaveLength(1);
});

it('requires professional acknowledgements and rationale before authenticated approval', async () => {
  const ready = { ...report, simulated: false, readiness: { structure_checked: true, ready_for_approval: true, missing_information: [], blocking_issues: [], review_checks: [{ code: 'operational_commitments', message: 'Verify approved deadlines.' }] } };
  const { calls } = mockApi({ 'GET /api/reports/r1': ready, 'POST /api/reports/r1/review': { ...ready, review_status: 'approved' } });
  renderPage(<ReportPage />, { path: '/reports/:reportId', route: '/reports/r1' });
  await userEvent.click(await screen.findByRole('tab', { name: /Review & approval/ }));
  const approve = screen.getByRole('button', { name: 'Approve' });
  expect(approve).toBeDisabled();
  await userEvent.click(screen.getByLabelText('Verify approved deadlines.'));
  expect(approve).toBeDisabled();
  await userEvent.type(screen.getByLabelText(/Review rationale/), 'Confirmed against approved service agreements.');
  expect(approve).toBeEnabled();
  await userEvent.click(approve);
  await waitFor(() => expect(calls.some((c) => c.method === 'POST')).toBe(true));
  expect(JSON.parse(calls.find((c) => c.method === 'POST').body)).toMatchObject({ base_version: 1, acknowledged_checks: ['operational_commitments'], actor: 'Asha Rao' });
});

it('filters report history by organisation and missing information', async () => {
  mockApi({ 'GET /api/reports': { reports: [{ ...report, title: 'Customer continuity', organization: 'Customer A', missing_information_count: 2 }, { ...report, id: 'r2', title: 'Internal configuration', organization: 'Internal', missing_information_count: 0 }] } });
  renderPage(<ReportsPage />);
  await screen.findByText('Customer continuity');
  await userEvent.selectOptions(screen.getByLabelText('Organisation'), 'Customer A');
  expect(screen.getByText('Customer continuity')).toBeInTheDocument();
  expect(screen.queryByText('Internal configuration')).not.toBeInTheDocument();
  await userEvent.click(screen.getByLabelText('Missing information'));
  expect(screen.getByText('1 matching policies')).toBeInTheDocument();
});
