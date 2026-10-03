import { render } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { vi } from 'vitest';
import { SystemProvider } from '../context/SystemContext.jsx';
import { ToastProvider } from '../context/ToastContext.jsx';

export const CONFIG = {
  limits: { max_upload_mb: 25, max_files_per_upload: 10 },
  file_types: [
    { extension: 'txt', label: 'Plain text', ocr: false },
    { extension: 'pdf', label: 'PDF', ocr: false },
    { extension: 'docx', label: 'Word document', ocr: false },
    { extension: 'png', label: 'PNG image', ocr: true },
  ],
  policy_types: [
    { id: 'access_control', name: 'Access Control Policy', description: 'Rules for access.', sections: [] },
    { id: 'vendor_management', name: 'Vendor Management Policy', description: 'Third-party rules.', sections: [] },
  ],
  writing_styles: [
    { id: 'formal', description: 'Formal register.' },
    { id: 'technical', description: 'Technical audience.' },
    { id: 'simplified', description: 'Plain language.' },
  ],
  detail_levels: [
    { id: 'concise', description: 'Short.' },
    { id: 'standard', description: 'Standard.' },
    { id: 'comprehensive', description: 'Everything.' },
  ],
  requirement_categories: { access_control: 'Access control requirements', organizational: 'Organizational requirements' },
  gap_statuses: { compliant: 'Compliant', partially_addressed: 'Partially Addressed', not_evidenced: 'Not Evidenced', not_applicable: 'Not Applicable' },
  control_statuses: { implemented: 'Implemented', not_implemented: 'Not implemented', unknown: 'Not stated' },
  priorities: ['critical', 'high', 'medium', 'low'],
  stages: {},
  scoring_method: {
    formula: 'score = likelihood x impact',
    note: 'A value of 0 means not assessable.',
    levels: [
      { level: 'low', label: 'Low', range: '1-4' },
      { level: 'medium', label: 'Medium', range: '5-9' },
      { level: 'high', label: 'High', range: '10-15' },
      { level: 'critical', label: 'Critical', range: '16-25' },
    ],
  },
  assessment_disclaimer: 'Statuses are AI-assisted assessments, not a formal audit.',
  review_notice: 'AI-assisted draft. It must be reviewed and approved before adoption.',
};

export const HEALTH = {
  status: 'ok',
  database: 'ok',
  auth_required: false,
  llm: { provider: 'mock', model: 'mock-rule-engine', simulated: true, ready: true, message: 'Mock mode.' },
  ocr: { available: false, engine: null, external: false },
  privacy: { storage: 'Stored on this server.', llm: 'Mock mode is active: no document content leaves this server.', ocr: '', retention: '', source_files: '', logs: '' },
};

// Installs a fetch mock.  `routes` maps "METHOD /api/path" to a response body,
// a function (returning the body), or { status, body }.
export function mockApi(routes) {
  const calls = [];
  const handler = vi.fn(async (url, options = {}) => {
    const method = (options.method || 'GET').toUpperCase();
    const path = String(url).split('?')[0];
    calls.push({ method, url: String(url), body: options.body });
    const defaults = { 'GET /api/health': HEALTH, 'GET /api/config': CONFIG };
    const key = `${method} ${path}`;
    let entry = key in routes ? routes[key] : defaults[key];
    if (entry === undefined) {
      return new Response(JSON.stringify({ error: { code: 'not_found', message: `No mock for ${key}` } }), { status: 404 });
    }
    if (typeof entry === 'function') entry = entry({ url: String(url), options, calls });
    if (entry instanceof Response) return entry;
    const status = entry && entry.__status ? entry.__status : 200;
    const body = entry && entry.__status ? entry.body : entry;
    return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });
  });
  vi.stubGlobal('fetch', handler);
  return { calls, handler };
}

export const httpError = (status, code, message) => ({ __status: status, body: { error: { code, message } } });

export function renderPage(element, { path = '/', route = '/', extraRoutes = [] } = {}) {
  return render(
    <MemoryRouter initialEntries={[route]}>
      <ToastProvider>
        <SystemProvider>
          <Routes>
            <Route path={path} element={element} />
            {extraRoutes.map(([extraPath, text]) => (
              <Route key={extraPath} path={extraPath} element={<p>{text}</p>} />
            ))}
          </Routes>
        </SystemProvider>
      </ToastProvider>
    </MemoryRouter>,
  );
}

export function makeFile(name, content = 'hello', type = 'text/plain') {
  return new File([content], name, { type });
}
