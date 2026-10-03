import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { FileDropzone } from '../components/FileDropzone.jsx';
import UploadPage from '../pages/UploadPage.jsx';
import { precheckFile } from '../utils/format.js';
import { extractedFile, failedFile } from './fixtures.js';
import { httpError, makeFile, mockApi, renderPage } from './helpers.jsx';

afterEach(() => vi.unstubAllGlobals());

describe('FileDropzone', () => {
  const props = { extensions: ['txt', 'pdf'], maxMb: 25, maxFiles: 10 };

  it('passes files chosen with the Browse button', async () => {
    const onFiles = vi.fn();
    render(<FileDropzone onFiles={onFiles} {...props} />);
    const files = [makeFile('a.txt'), makeFile('b.pdf', '%PDF-', 'application/pdf')];
    await userEvent.upload(screen.getByLabelText('Choose files to upload'), files);
    expect(onFiles).toHaveBeenCalledTimes(1);
    expect(onFiles.mock.calls[0][0].map((file) => file.name)).toEqual(['a.txt', 'b.pdf']);
  });

  it('passes dropped files and highlights while dragging', () => {
    const onFiles = vi.fn();
    render(<FileDropzone onFiles={onFiles} {...props} />);
    const zone = screen.getByTestId('dropzone');
    fireEvent.dragEnter(zone);
    expect(zone).toHaveClass('dropzone-active');
    fireEvent.drop(zone, { dataTransfer: { files: [makeFile('dropped.txt')] } });
    expect(zone).not.toHaveClass('dropzone-active');
    expect(onFiles.mock.calls[0][0][0].name).toBe('dropped.txt');
  });

  it('shows supported formats and limits, and ignores drops while disabled', () => {
    const onFiles = vi.fn();
    render(<FileDropzone onFiles={onFiles} {...props} disabled />);
    expect(screen.getByText(/Supported: TXT, PDF/)).toBeInTheDocument();
    expect(screen.getByText(/Up to 25 MB per file, 10 files per upload/)).toBeInTheDocument();
    fireEvent.drop(screen.getByTestId('dropzone'), { dataTransfer: { files: [makeFile('x.txt')] } });
    expect(onFiles).not.toHaveBeenCalled();
    expect(screen.getByRole('button', { name: 'Browse files' })).toBeDisabled();
  });
});

describe('precheckFile', () => {
  const rules = { extensions: ['txt', 'pdf'], maxBytes: 100 };
  it('accepts supported files and explains rejections', () => {
    expect(precheckFile(makeFile('ok.TXT', 'hi'), rules)).toBeNull();
    expect(precheckFile(makeFile('tool.exe', 'MZ'), rules)).toMatch(/Unsupported file type \(\.exe\)/);
    expect(precheckFile(makeFile('noextension', 'x'), rules)).toMatch(/Unsupported file type/);
    expect(precheckFile(makeFile('empty.txt', ''), rules)).toBe('The file is empty.');
    expect(precheckFile(makeFile('big.txt', 'x'.repeat(200)), rules)).toMatch(/Larger than/);
  });
});

describe('UploadPage', () => {
  it('lists files with their status and enables analysis only for selected, extracted files', async () => {
    mockApi({ 'GET /api/files': { files: [extractedFile, failedFile] } });
    renderPage(<UploadPage />);
    expect(await screen.findByText('security.txt')).toBeInTheDocument();
    expect(screen.getByText('1,200 characters · 4 sections · 0 tables')).toBeInTheDocument();
    expect(screen.getByText('OCR is not available on this server.')).toBeInTheDocument();
    expect(screen.getByText(/no document content leaves this server/)).toBeInTheDocument();

    const start = screen.getByRole('button', { name: /Start analysis/ });
    expect(start).toBeDisabled();
    expect(screen.getByLabelText('Include scan.png in the analysis')).toBeDisabled();
    await userEvent.click(screen.getByLabelText('Include security.txt in the analysis'));
    expect(screen.getByRole('button', { name: 'Start analysis (1 file)' })).toBeEnabled();
    expect(screen.getByRole('button', { name: 'Retry extraction of scan.png' })).toBeInTheDocument();
  });

  it('rejects unsupported files in the browser without uploading them', async () => {
    const { calls } = mockApi({ 'GET /api/files': { files: [] } });
    renderPage(<UploadPage />);
    await screen.findByText('No files yet');
    fireEvent.drop(screen.getByTestId('dropzone'), { dataTransfer: { files: [makeFile('malware.exe', 'MZ', 'application/octet-stream')] } });
    const alert = await screen.findByRole('alert');
    expect(within(alert).getByText(/1 file was not accepted/)).toBeInTheDocument();
    expect(within(alert).getByText(/Unsupported file type \(\.exe\)/)).toBeInTheDocument();
    expect(calls.some((call) => call.method === 'POST')).toBe(false);
    await userEvent.click(within(alert).getByRole('button', { name: 'Dismiss' }));
    expect(screen.queryByText(/was not accepted/)).not.toBeInTheDocument();
  });

  it('uploads accepted files, shows server-side rejections, and selects new files', async () => {
    const { calls } = mockApi({
      'GET /api/files': { files: [] },
      'POST /api/upload': {
        accepted: 1,
        rejected: 1,
        results: [
          { file_name: 'security.txt', accepted: true, duplicate: false, file: extractedFile, error: null },
          { file_name: 'fake.pdf', accepted: false, duplicate: false, file: null, error: { code: 'signature_mismatch', message: 'The file is not a valid PDF (missing PDF header).' } },
        ],
      },
    });
    renderPage(<UploadPage />);
    await screen.findByText('No files yet');
    await userEvent.upload(screen.getByLabelText('Choose files to upload'), [makeFile('security.txt'), makeFile('fake.pdf', 'nope', 'application/pdf')]);

    expect(await screen.findByText(/The file is not a valid PDF/)).toBeInTheDocument();
    expect(await screen.findByRole('button', { name: 'Start analysis (1 file)' })).toBeEnabled();
    const post = calls.find((call) => call.method === 'POST');
    expect(post.url).toBe('/api/upload');
    expect(post.body.getAll('files').map((file) => file.name)).toEqual(['security.txt', 'fake.pdf']);
    expect(await screen.findByText('1 file uploaded. Extraction has started.')).toBeInTheDocument();
  });

  it('starts the analysis with the selected file ids and navigates to it', async () => {
    const { calls } = mockApi({
      'GET /api/files': { files: [extractedFile] },
      'POST /api/analyses': { analysis_id: 'a1', job: { id: 'j1' } },
    });
    renderPage(<UploadPage />, { extraRoutes: [['/analyses/:id', 'analysis page']] });
    await userEvent.click(await screen.findByLabelText('Include security.txt in the analysis'));
    await userEvent.click(screen.getByRole('button', { name: 'Start analysis (1 file)' }));
    expect(await screen.findByText('analysis page')).toBeInTheDocument();
    expect(JSON.parse(calls.find((call) => call.url === '/api/analyses').body)).toEqual({ file_ids: ['f1'] });
  });

  it('asks for confirmation before removing a file', async () => {
    const { calls } = mockApi({ 'GET /api/files': { files: [extractedFile] }, 'DELETE /api/files/f1': { deleted: true, id: 'f1' } });
    renderPage(<UploadPage />);
    await userEvent.click(await screen.findByRole('button', { name: 'Remove security.txt' }));
    const dialog = screen.getByRole('dialog', { name: 'Remove this file?' });
    expect(calls.some((call) => call.method === 'DELETE')).toBe(false);
    await userEvent.click(within(dialog).getByRole('button', { name: 'Remove file' }));
    await waitFor(() => expect(screen.queryByText('security.txt')).not.toBeInTheDocument());
    expect(calls.some((call) => call.method === 'DELETE' && call.url === '/api/files/f1')).toBe(true);
  });

  it('shows an error state with retry when the file list cannot be loaded', async () => {
    mockApi({ 'GET /api/files': httpError(500, 'internal_error', 'An unexpected error occurred. Please try again.') });
    renderPage(<UploadPage />);
    expect(await screen.findByText('An unexpected error occurred. Please try again.')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Try again' })).toBeInTheDocument();
  });
});
