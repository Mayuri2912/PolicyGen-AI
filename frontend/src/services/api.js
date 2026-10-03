// Single place where the frontend talks to the backend.  Every function maps
// to one documented endpoint (see docs/ARCHITECTURE.md).

const BASE = import.meta.env.VITE_API_BASE || '/api';
const TOKEN_KEY = 'policygen_access_token';

export class ApiError extends Error {
  constructor(message, { status = 0, code = 'error', details = null } = {}) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.code = code;
    this.details = details;
  }
}

export function getAccessToken() {
  try {
    return sessionStorage.getItem(TOKEN_KEY) || '';
  } catch {
    return '';
  }
}

export function setAccessToken(token) {
  try {
    if (token) sessionStorage.setItem(TOKEN_KEY, token);
    else sessionStorage.removeItem(TOKEN_KEY);
  } catch {
    // Storage can be unavailable (private mode); the token then lasts for this page only.
  }
}

function authHeaders() {
  const token = getAccessToken();
  return token ? { Authorization: `Bearer ${token}` } : {};
}

async function toError(response) {
  let payload = null;
  try {
    payload = await response.json();
  } catch {
    // Not JSON: fall through to a generic message.
  }
  const error = payload?.error || {};
  const fallback =
    response.status === 429
      ? 'Too many requests. Please wait a moment and try again.'
      : response.status >= 500
        ? 'The server could not complete the request. Please try again.'
        : `Request failed (${response.status}).`;
  return new ApiError(error.message || fallback, {
    status: response.status,
    code: error.code || 'http_error',
    details: error.details || null,
  });
}

async function send(path, { method = 'GET', body, formData } = {}) {
  const headers = { ...authHeaders() };
  let payload;
  if (formData) {
    payload = formData;
  } else if (body !== undefined) {
    headers['Content-Type'] = 'application/json';
    payload = JSON.stringify(body);
  }
  let response;
  try {
    response = await fetch(`${BASE}${path}`, { method, headers, body: payload });
  } catch {
    throw new ApiError('The server could not be reached. Check that the backend is running.', { code: 'network_error' });
  }
  if (!response.ok) throw await toError(response);
  return response;
}

async function json(path, options) {
  const response = await send(path, options);
  return response.json();
}

function query(params) {
  const search = new URLSearchParams();
  Object.entries(params).forEach(([key, value]) => {
    if (value !== undefined && value !== null && value !== '') search.set(key, value);
  });
  const text = search.toString();
  return text ? `?${text}` : '';
}

export const api = {
  // System
  health: () => json('/health'),
  config: () => json('/config'),
  dashboard: () => json('/dashboard'),
  history: (limit = 50) => json(`/history${query({ limit })}`),

  // Files
  uploadFiles: (files) => {
    const formData = new FormData();
    files.forEach((file) => formData.append('files', file, file.name));
    return json('/upload', { method: 'POST', formData });
  },
  listFiles: (ids) => json(`/files${query({ ids: ids?.length ? ids.join(',') : undefined })}`),
  getFile: (id) => json(`/files/${id}`),
  getFileContent: (id) => json(`/files/${id}/content`),
  extractFile: (id) => json(`/extract/${id}`, { method: 'POST' }),
  deleteFile: (id) => json(`/files/${id}`, { method: 'DELETE' }),

  // Analysis
  startAnalysis: (fileIds) => json('/analyses', { method: 'POST', body: { file_ids: fileIds } }),
  listAnalyses: () => json('/analyses'),
  getAnalysis: (id) => json(`/analyses/${id}`),
  updateFinding: (analysisId, findingId, patch) =>
    json(`/analyses/${analysisId}/findings/${findingId}`, { method: 'PATCH', body: patch }),
  deleteAnalysis: (id) => json(`/analyses/${id}`, { method: 'DELETE' }),

  // Policies
  policyTypes: () => json('/policies/types'),
  recommend: (analysisId) => json('/policies/recommend', { method: 'POST', body: { analysis_id: analysisId } }),
  generate: (analysisId, config) => json('/policies/generate', { method: 'POST', body: { analysis_id: analysisId, config } }),

  // Jobs
  getJob: (id) => json(`/jobs/${id}`),
  cancelJob: (id) => json(`/jobs/${id}/cancel`, { method: 'POST' }),
  retryJob: (id) => json(`/jobs/${id}/retry`, { method: 'POST' }),

  // Reports
  listReports: () => json('/reports'),
  getReport: (id, version) => json(`/reports/${id}${query({ version })}`),
  compareVersions: (id, from, to) => json(`/reports/${id}/compare${query({ from, to })}`),
  editSection: (id, sectionId, body) => json(`/reports/${id}/sections/${sectionId}`, { method: 'PUT', body }),
  regenerateSections: (id, sectionIds, instruction) =>
    json(`/reports/${id}/sections/regenerate`, { method: 'POST', body: { section_ids: sectionIds, instruction } }),
  review: (id, body) => json(`/reports/${id}/review`, { method: 'POST', body }),
  deleteReport: (id) => json(`/reports/${id}`, { method: 'DELETE' }),

  // Downloads go through fetch so the optional access token can be sent.
  downloadReport: async (id, format, version) => {
    const response = await send(`/reports/${id}/download${query({ format, version })}`);
    const disposition = response.headers.get('Content-Disposition') || '';
    const match = /filename="?([^";]+)"?/i.exec(disposition);
    return { blob: await response.blob(), filename: match ? match[1] : `policy.${format}` };
  },
};

export function saveBlob(blob, filename) {
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
