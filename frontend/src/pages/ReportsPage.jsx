import { useState } from 'react';
import { Link } from 'react-router-dom';
import { Download, FilePlus2, Trash2 } from 'lucide-react';
import { Badge, ConfirmDialog, EmptyState, ErrorState, LoadingBlock, PageHeader, SimulatedTag } from '../components/ui.jsx';
import { useToast } from '../context/ToastContext.jsx';
import { useAsync } from '../hooks/useAsync.js';
import { api, saveBlob } from '../services/api.js';
import { REVIEW_LABELS, formatDateTime } from '../utils/format.js';

export default function ReportsPage() {
  const toast = useToast();
  const { loading, error, data, reload, setData } = useAsync(() => api.listReports(), []);
  const [deleting, setDeleting] = useState(null);
  const [busy, setBusy] = useState(false);
  const [downloading, setDownloading] = useState(null);

  const download = async (report, format) => {
    setDownloading(`${report.id}-${format}`);
    try {
      const { blob, filename } = await api.downloadReport(report.id, format);
      saveBlob(blob, filename);
    } catch (err) {
      toast.error(err.message);
    } finally {
      setDownloading(null);
    }
  };

  const remove = async () => {
    setBusy(true);
    try {
      await api.deleteReport(deleting.id);
      setData((current) => ({ reports: current.reports.filter((report) => report.id !== deleting.id) }));
      toast.success(`"${deleting.title}" was deleted.`);
      setDeleting(null);
    } catch (err) {
      toast.error(err.message);
    } finally {
      setBusy(false);
    }
  };

  const reports = data?.reports || [];
  return (
    <>
      <PageHeader
        title="Report history"
        subtitle="Every policy generated on this server, with its current version and review status."
        actions={
          <Link to="/new" className="btn btn-primary">
            <FilePlus2 size={16} aria-hidden="true" /> New policy
          </Link>
        }
      />
      {loading && !data && <LoadingBlock label="Loading reports…" />}
      {error && !data && <ErrorState message={error.message} onRetry={reload} />}
      {data && reports.length === 0 && (
        <EmptyState title="No reports yet" action={<Link to="/new" className="btn btn-primary">Generate a policy</Link>}>
          Policies you generate are listed here with their source files and versions.
        </EmptyState>
      )}
      {reports.length > 0 && (
        <div className="card">
          <div className="table-wrap">
            <table className="table">
              <caption className="sr-only">Generated policy reports</caption>
              <thead>
                <tr>
                  <th scope="col">Policy</th>
                  <th scope="col">Type</th>
                  <th scope="col">Source files</th>
                  <th scope="col">Generated</th>
                  <th scope="col">Version</th>
                  <th scope="col">Status</th>
                  <th scope="col">Actions</th>
                </tr>
              </thead>
              <tbody>
                {reports.map((report) => {
                  const ready = report.current_version > 0;
                  return (
                    <tr key={report.id}>
                      <td>
                        <Link to={`/reports/${report.id}`} className="list-title">
                          {report.title}
                        </Link>
                        {report.simulated && (
                          <div>
                            <SimulatedTag />
                          </div>
                        )}
                      </td>
                      <td>{report.policy_type_name}</td>
                      <td className="small">{report.source_files.join(', ')}</td>
                      <td className="nowrap small">{formatDateTime(report.created_at)}</td>
                      <td className="nowrap">{ready ? `v${report.current_version}` : '—'}</td>
                      <td>
                        <div className="badge-row">
                          <Badge value={report.status} />
                          {ready && <Badge value={report.review_status} label={REVIEW_LABELS[report.review_status]} />}
                        </div>
                        {report.status === 'failed' && <p className="form-error small">{report.error_message}</p>}
                      </td>
                      <td>
                        <div className="row-actions nowrap-actions">
                        {ready &&
                          report.output_formats.map((format) => (
                            <button
                              key={format}
                              type="button"
                              className="btn btn-small"
                              onClick={() => download(report, format)}
                              disabled={downloading === `${report.id}-${format}`}
                              aria-label={`Download ${report.title} as ${format.toUpperCase()}`}
                            >
                              <Download size={14} aria-hidden="true" /> {format.toUpperCase()}
                            </button>
                          ))}
                        <button type="button" className="icon-button" onClick={() => setDeleting(report)} aria-label={`Delete ${report.title}`} title="Delete report">
                          <Trash2 size={16} aria-hidden="true" />
                        </button>
                        </div>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </div>
      )}
      {deleting && (
        <ConfirmDialog title="Delete this report?" confirmLabel="Delete report" danger busy={busy} onConfirm={remove} onCancel={() => setDeleting(null)}>
          <p>
            <strong>{deleting.title}</strong> will be deleted with all {deleting.version_count} version(s) and its generated documents. This
            cannot be undone. The uploaded files and the analysis are kept.
          </p>
        </ConfirmDialog>
      )}
    </>
  );
}
