import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { FileDropzone } from '../components/FileDropzone.jsx';
import { FileList, RejectedList } from '../components/FileList.jsx';
import { ConfirmDialog, EmptyState, ErrorState, LoadingBlock, Modal, PageHeader, Spinner } from '../components/ui.jsx';
import { useSystem } from '../context/SystemContext.jsx';
import { useToast } from '../context/ToastContext.jsx';
import { api } from '../services/api.js';
import { FILE_BUSY, precheckFile } from '../utils/format.js';

const POLL_MS = 1200;

function ContentPreview({ file, onClose }) {
  const [state, setState] = useState({ loading: true, error: null, data: null });
  useEffect(() => {
    let active = true;
    api
      .getFileContent(file.id)
      .then((data) => active && setState({ loading: false, error: null, data }))
      .catch((error) => active && setState({ loading: false, error: error.message, data: null }));
    return () => {
      active = false;
    };
  }, [file.id]);

  return (
    <Modal wide title={`Extracted content: ${file.file_name}`} onClose={onClose}>
      {state.loading && <LoadingBlock label="Loading extracted content…" />}
      {state.error && <ErrorState message={state.error} />}
      {state.data && (
        <div className="extracted">
          <p className="muted small">
            Title: {state.data.document_title} · {state.data.characters.toLocaleString()} characters. This is the text the analysis will read.
          </p>
          {state.data.sections.map((section, index) => (
            <section key={index}>
              <h3>{section.heading || 'Untitled section'}</h3>
              <p className="muted small">{section.location}</p>
              <pre>{section.text}{section.truncated ? '\n… (shortened for preview)' : ''}</pre>
            </section>
          ))}
          {state.data.tables.map((table, index) => (
            <section key={`t${index}`}>
              <h3>Table: {table.title || table.location}</h3>
              <p className="muted small">
                {table.location} · {table.row_count} rows{table.truncated ? ' (first rows shown)' : ''}
              </p>
              <div className="table-wrap">
                <table className="table doc-table">
                  <thead>
                    <tr>
                      {table.headers.map((header, i) => (
                        <th key={i} scope="col">
                          {header}
                        </th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {table.rows.map((row, r) => (
                      <tr key={r}>
                        {row.map((cell, c) => (
                          <td key={c}>{cell}</td>
                        ))}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </section>
          ))}
        </div>
      )}
    </Modal>
  );
}

export default function UploadPage() {
  const { config, health } = useSystem();
  const toast = useToast();
  const navigate = useNavigate();
  const [files, setFiles] = useState([]);
  const [selected, setSelected] = useState([]);
  const [rejected, setRejected] = useState([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState(null);
  const [uploading, setUploading] = useState(false);
  const [starting, setStarting] = useState(false);
  const [removing, setRemoving] = useState(null);
  const [removeBusy, setRemoveBusy] = useState(false);
  const [preview, setPreview] = useState(null);
  const autoSelect = useRef(new Set());

  const extensions = useMemo(() => (config?.file_types || []).map((type) => type.extension), [config]);
  const maxBytes = (config?.limits.max_upload_mb || 25) * 1024 * 1024;
  const maxFiles = config?.limits.max_files_per_upload || 10;

  const merge = useCallback((incoming) => {
    setFiles((current) => {
      const byId = new Map(current.map((file) => [file.id, file]));
      incoming.forEach((file) => byId.set(file.id, { ...byId.get(file.id), ...file }));
      return [...byId.values()].sort((a, b) => (a.created_at < b.created_at ? 1 : -1));
    });
    // Files uploaded in this visit are selected as soon as they are extracted.
    const ready = incoming.filter((file) => file.status === 'extracted' && autoSelect.current.has(file.id)).map((file) => file.id);
    if (ready.length) {
      ready.forEach((id) => autoSelect.current.delete(id));
      setSelected((current) => [...new Set([...current, ...ready])]);
    }
  }, []);

  const loadAll = useCallback(async () => {
    setLoading(true);
    setLoadError(null);
    try {
      const { files: list } = await api.listFiles();
      merge(list);
    } catch (error) {
      setLoadError(error.message);
    } finally {
      setLoading(false);
    }
  }, [merge]);

  useEffect(() => {
    loadAll();
  }, [loadAll]);

  // Poll only the files that are still being validated or extracted.
  const busyIds = files.filter((file) => FILE_BUSY.includes(file.status)).map((file) => file.id).join(',');
  useEffect(() => {
    if (!busyIds) return undefined;
    let failures = 0;
    let retryAfter = 0;
    let active = true;
    let timer;
    const poll = async () => {
      try {
        const { files: list } = await api.listFiles(busyIds.split(','));
        if (!active) return;
        merge(list); failures = 0; retryAfter = 0;
      } catch (error) {
        failures += 1; retryAfter = error.retryAfter || 0;
        if ([401, 403, 404].includes(error.status)) return;
      }
      if (active) timer = setTimeout(poll, Math.max(retryAfter * 1000, Math.min(30000, POLL_MS * 2 ** failures)));
    };
    timer = setTimeout(poll, POLL_MS);
    return () => { active = false; clearTimeout(timer); };
  }, [busyIds, merge]);

  const onFiles = async (picked) => {
    const problems = [];
    const accepted = [];
    picked.forEach((file) => {
      const reason = precheckFile(file, { extensions, maxBytes });
      if (reason) problems.push({ name: file.name, reason });
      else accepted.push(file);
    });
    if (accepted.length > maxFiles) {
      accepted.splice(maxFiles).forEach((file) => problems.push({ name: file.name, reason: `Only ${maxFiles} files can be uploaded at a time.` }));
    }
    if (!accepted.length) {
      setRejected((current) => [...current, ...problems]);
      return;
    }
    setUploading(true);
    try {
      const response = await api.uploadFiles(accepted);
      const stored = [];
      response.results.forEach((result) => {
        if (result.accepted && result.file) {
          autoSelect.current.add(result.file.id);
          stored.push({ ...result.file, duplicate: result.duplicate });
        } else {
          problems.push({ name: result.file_name, reason: result.error?.message || 'The file was rejected.' });
        }
      });
      merge(stored);
      if (response.accepted) toast.success(`${response.accepted} file${response.accepted > 1 ? 's' : ''} uploaded. Extraction has started.`);
    } catch (error) {
      toast.error(error.message);
      accepted.forEach((file) => problems.push({ name: file.name, reason: error.message }));
    } finally {
      setUploading(false);
      if (problems.length) setRejected((current) => [...current, ...problems]);
    }
  };

  const remove = async () => {
    setRemoveBusy(true);
    try {
      await api.deleteFile(removing.id);
      setFiles((current) => current.filter((file) => file.id !== removing.id));
      setSelected((current) => current.filter((id) => id !== removing.id));
      toast.success(`${removing.file_name} was removed.`);
      setRemoving(null);
    } catch (error) {
      toast.error(error.message);
    } finally {
      setRemoveBusy(false);
    }
  };

  const retry = async (file) => {
    try {
      const response = await api.extractFile(file.id);
      autoSelect.current.add(file.id);
      merge([response.file]);
    } catch (error) {
      toast.error(error.message);
    }
  };

  const start = async () => {
    setStarting(true);
    try {
      const response = await api.startAnalysis(selected);
      navigate(`/analyses/${response.analysis_id}`);
    } catch (error) {
      toast.error(error.message);
      setStarting(false);
    }
  };

  const busyCount = busyIds ? busyIds.split(',').length : 0;
  const external = health && !health.llm?.simulated;

  return (
    <>
      <PageHeader title="New policy generation" subtitle="Step 1 of 4: upload the documents the policy should be based on." />

      <div className="notice notice-info">
        <p>
          <strong>Where your files go:</strong> {health?.privacy?.storage} {health?.privacy?.llm}
        </p>
      </div>

      <section className="card">
        <h2>Upload files</h2>
        <FileDropzone onFiles={onFiles} disabled={uploading} extensions={extensions} maxMb={config?.limits.max_upload_mb} maxFiles={maxFiles} />
        {uploading && (
          <p className="status status-busy" role="status">
            <Spinner label="Uploading" /> Uploading and validating…
          </p>
        )}
        {!health?.ocr?.available && (
          <p className="muted small">OCR is not installed on this server, so images and scanned PDFs cannot be read. Text-based files work normally.</p>
        )}
        <RejectedList items={rejected} onDismiss={() => setRejected([])} />
      </section>

      <section className="card">
        <div className="card-head">
          <h2>Files</h2>
          <span className="muted small">
            {selected.length} selected{busyCount ? ` · ${busyCount} still processing` : ''}
          </span>
        </div>
        {loading && !files.length && <LoadingBlock label="Loading files…" />}
        {loadError && !files.length && <ErrorState message={loadError} onRetry={loadAll} />}
        {!loading && !loadError && !files.length && (
          <EmptyState title="No files yet">Upload a document above to get started. Accepted file types and upload limits are shown above.</EmptyState>
        )}
        <FileList
          files={files}
          selected={selected}
          onToggle={(id) => setSelected((current) => (current.includes(id) ? current.filter((item) => item !== id) : [...current, id]))}
          onRemove={setRemoving}
          onRetry={retry}
          onPreview={setPreview}
        />
        <div className="form-actions">
          <p className="muted small">
            Select the extracted files to analyze together. Files that failed can be retried or left out.
            {external ? ` Starting the analysis sends their extracted text to the ${health.llm.service_name || 'configured AI provider'}.` : ''}
          </p>
          <button type="button" className="btn btn-primary btn-large" onClick={start} disabled={!selected.length || starting}>
            {starting ? 'Starting…' : `Start analysis${selected.length ? ` (${selected.length} file${selected.length > 1 ? 's' : ''})` : ''}`}
          </button>
        </div>
      </section>

      {removing && (
        <ConfirmDialog title="Remove this file?" confirmLabel="Remove file" danger busy={removeBusy} onConfirm={remove} onCancel={() => setRemoving(null)}>
          <p>
            <strong>{removing.file_name}</strong> and its extracted content will be deleted from the server. Analyses and reports already created
            from it are kept.
          </p>
        </ConfirmDialog>
      )}
      {preview && <ContentPreview file={preview} onClose={() => setPreview(null)} />}
    </>
  );
}
