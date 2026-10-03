import { fireEvent, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import AppLayout from '../layouts/AppLayout.jsx';
import AnalysisPage from '../pages/AnalysisPage.jsx';
import Dashboard from '../pages/Dashboard.jsx';
import ReportPage from '../pages/ReportPage.jsx';
import UploadPage from '../pages/UploadPage.jsx';
import { providerLabel } from '../utils/format.js';
import { analysis, report } from './fixtures.js';
import { CONFIG, HEALTH, mockApi, renderPage } from './helpers.jsx';

afterEach(() => vi.unstubAllGlobals());

// What the backend reports for each provider.  The UI must show these values,
// not names written into the frontend.
const PROVIDERS = {
  openrouter: { display_name: 'OpenRouter', service_name: 'OpenRouter API', model: 'vendor/Model-X:free' },
  gemini: { display_name: 'Google Gemini', service_name: 'Google Gemini API', model: 'gemini-3.8-flash' },
  anthropic: { display_name: 'Anthropic Claude', service_name: 'Anthropic API', model: 'claude-opus-5-5' },
};
const LABELS = { anthropic: 'Anthropic Claude', gemini: 'Google Gemini', openrouter: 'OpenRouter', mock: 'Mock mode' };

function liveRoutes(provider, extra = {}) {
  const llm = { provider, ...PROVIDERS[provider], simulated: false, ready: true, message: `Documents are sent to the ${PROVIDERS[provider].service_name}.` };
  return {
    'GET /api/health': { ...HEALTH, llm, privacy: { ...HEALTH.privacy, llm: `Text is sent to the ${llm.service_name}.` } },
    'GET /api/config': { ...CONFIG, llm_providers: LABELS },
    ...extra,
  };
}

describe('configured provider and model are displayed', () => {
  it.each(Object.keys(PROVIDERS))('shows %s and its model in the top bar, without the mock banner', async (provider) => {
    mockApi(liveRoutes(provider));
    renderPage(<AppLayout />);
    const { display_name: name, model } = PROVIDERS[provider];
    const pill = await screen.findByText(`${name}: ${model}`);
    expect(pill.closest('.mode-pill')).toHaveClass('mode-live');
    expect(screen.queryByText(/Mock mode/)).not.toBeInTheDocument();
  });

  it('names the provider that is not configured, with the server message, when it has no key', async () => {
    const llm = { provider: 'openrouter', display_name: 'OpenRouter', model: 'openrouter/free', simulated: false, service_name: '', ready: false, message: 'No OpenRouter API key is configured.' };
    mockApi({ 'GET /api/health': { ...HEALTH, status: 'degraded', llm } });
    renderPage(<AppLayout />);
    const pill = await screen.findByText('OpenRouter not configured');
    expect(pill.closest('.mode-pill')).toHaveClass('mode-off');
    expect(screen.getByRole('alert')).toHaveTextContent('No OpenRouter API key is configured.');
    expect(screen.queryByText(/Google Gemini|Mock mode/)).not.toBeInTheDocument(); // no other provider is implied
  });

  it('picks up a provider change on the server when the user returns to the tab', async () => {
    const missing = { provider: 'openrouter', display_name: 'OpenRouter', model: 'openrouter/free', simulated: false, service_name: '', ready: false, message: 'No OpenRouter API key is configured.' };
    let health = { ...HEALTH, status: 'degraded', llm: missing };
    mockApi({ 'GET /api/health': () => health });
    renderPage(<AppLayout />);
    expect(await screen.findByText('OpenRouter not configured')).toBeInTheDocument();

    // The key is added to backend/.env; the server reports the provider as ready.
    health = liveRoutes('openrouter')['GET /api/health'];
    fireEvent.focus(window);
    expect(await screen.findByText('OpenRouter: vendor/Model-X:free')).toBeInTheDocument();
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
  });

  it('names the provider on the dashboard and on the upload page', async () => {
    const stats = { files_uploaded: 0, files_processed: 0, analyses_completed: 0, policies_generated: 0, policies_approved: 0, jobs_active: 0, jobs_failed: 0 };
    mockApi(liveRoutes('openrouter', { 'GET /api/dashboard': { stats, recent_reports: [], recent_analyses: [], recent_jobs: [] } }));
    renderPage(<Dashboard />);
    expect(await screen.findByText('OpenRouter API ready (vendor/Model-X:free)')).toBeInTheDocument();

    vi.unstubAllGlobals();
    mockApi(liveRoutes('openrouter', { 'GET /api/files': { files: [] } }));
    renderPage(<UploadPage />);
    expect(await screen.findByText(/sends their extracted text to the OpenRouter API\./)).toBeInTheDocument();
  });

  it('labels an analysis with the provider and model that produced it', async () => {
    // Made with OpenRouter earlier, viewed while the server runs another provider.
    const made = { ...analysis, simulated: false, provider: 'openrouter', model: 'vendor/Model-X:free' };
    mockApi(liveRoutes('gemini', { 'GET /api/analyses/a1': made }));
    renderPage(<AnalysisPage />, { path: '/analyses/:analysisId', route: '/analyses/a1' });
    const badge = await screen.findByText('Analyzed by OpenRouter · vendor/Model-X:free');
    expect(badge).toHaveClass('badge-plain'); // the model id is shown exactly, not re-capitalised
    expect(screen.queryByText('Simulated')).not.toBeInTheDocument();
  });

  it('labels a report with the provider and model that generated it', async () => {
    const made = { ...report, simulated: false, provider: 'gemini', model: 'gemini-3.8-flash' };
    mockApi(liveRoutes('openrouter', { 'GET /api/reports/r1': made }));
    renderPage(<ReportPage />, { path: '/reports/:reportId', route: '/reports/r1' });
    expect(await screen.findByText('Generated by Google Gemini · gemini-3.8-flash')).toBeInTheDocument();
  });

  it('falls back to the raw provider id when the backend sends no label for it', () => {
    expect(providerLabel({ llm_providers: LABELS }, 'openrouter')).toBe('OpenRouter');
    expect(providerLabel({ llm_providers: LABELS }, 'some-new-provider')).toBe('some-new-provider');
    expect(providerLabel(null, 'gemini')).toBe('gemini');
  });
});
