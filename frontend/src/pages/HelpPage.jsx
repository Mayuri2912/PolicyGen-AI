import { Link } from 'react-router-dom';
import { PageHeader } from '../components/ui.jsx';
import { useSystem } from '../context/SystemContext.jsx';

export default function HelpPage() {
  const { config, health } = useSystem();
  const privacy = health?.privacy || {};
  return (
    <>
      <PageHeader title="Help and privacy" subtitle="How to use PolicyGen AI, what it does with your documents, and what it cannot do." />

      <section className="card">
        <h2>Using PolicyGen AI</h2>
        <ol className="steps">
          <li>
            <strong>Upload.</strong> On <Link to="/new">New policy</Link>, drag files in or browse for them. Each file is validated and its
            content extracted. A file that fails shows the reason and can be retried or left out.
          </li>
          <li>
            <strong>Analyze.</strong> Select the extracted files and start the analysis. Files analyzed together are compared with each other,
            so contradictions between them are reported.
          </li>
          <li>
            <strong>Review findings.</strong> Check the requirements, risks, controls and gaps. Each one shows the file and location it came
            from. Edit anything that is wrong and exclude anything that should not shape the policy. Your edits are marked and the original
            finding is kept.
          </li>
          <li>
            <strong>Configure.</strong> Accept the recommended policy type or choose another, and fill in the details you know. Unknown
            details become highlighted placeholders in the document.
          </li>
          <li>
            <strong>Preview and refine.</strong> Search the draft, edit a section, or regenerate selected sections. Every change is saved as a
            new version that you can compare with earlier ones.
          </li>
          <li>
            <strong>Review and download.</strong> Record the review decision, then download the policy as Word or PDF.
          </li>
        </ol>
      </section>

      <section className="card">
        <h2>Supported files</h2>
        <div className="table-wrap">
          <table className="table">
            <thead>
              <tr>
                <th scope="col">Format</th>
                <th scope="col">Extension</th>
                <th scope="col">Notes</th>
              </tr>
            </thead>
            <tbody>
              {(config?.file_types || []).map((type) => (
                <tr key={type.extension}>
                  <td>{type.label}</td>
                  <td>.{type.extension}</td>
                  <td className="small">
                    {type.ocr
                      ? health?.ocr?.available
                        ? 'Read with OCR.'
                        : 'Needs OCR, which is not installed on this server.'
                      : ['doc', 'ppt'].includes(type.extension)
                        ? 'Legacy format: text only, without heading styles.'
                        : type.extension === 'pdf'
                          ? 'Text PDFs are read directly; scanned PDFs need OCR.'
                          : ''}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <p className="small">
          Limits: {config?.limits.max_upload_mb} MB per file and {config?.limits.max_files_per_upload} files per upload. Password-protected
          and encrypted files cannot be read; remove the password first. Macros and scripts inside files are never run.
        </p>
      </section>

      <section className="card">
        <h2>Privacy notice</h2>
        <dl className="detail-list wide">
          <dt>Storage</dt>
          <dd>{privacy.storage}</dd>
          <dt>AI provider</dt>
          <dd>{privacy.llm}</dd>
          <dt>OCR</dt>
          <dd>{privacy.ocr}</dd>
          <dt>Source files</dt>
          <dd>{privacy.source_files}</dd>
          <dt>Retention</dt>
          <dd>{privacy.retention}</dd>
          <dt>Logs</dt>
          <dd>{privacy.logs}</dd>
        </dl>
        <p className="small">
          Do not upload documents you are not permitted to share with the configured AI provider. Deleting a report removes its versions and
          generated documents; deleting a file removes the stored upload and its extracted content.
        </p>
      </section>

      <section className="card">
        <h2>What the results mean</h2>
        <ul>
          <li>
            <strong>Documented vs inferred.</strong> A documented finding is stated in your files and comes with a quote. An inferred risk is
            something the analysis deduced; treat it as a prompt for review, not as a confirmed weakness or incident.
          </li>
          <li>
            <strong>Gap statuses.</strong> {config?.assessment_disclaimer}
          </li>
          <li>
            <strong>Risk scores.</strong> {config?.scoring_method.formula}; {config?.scoring_method.levels.map((band) => `${band.label} ${band.range}`).join(', ')}.{' '}
            {config?.scoring_method.note}
          </li>
          <li>
            <strong>Placeholders.</strong> Text such as <mark className="placeholder">[To be confirmed: policy owner]</mark> marks information
            the documents did not provide. Complete these before approval.
          </li>
          <li>
            <strong>Mock mode.</strong> Without an AI provider API key on the server it runs in mock mode: keyword rules stand in for the AI model so
            the workflow can be tested. Mock results are labelled Simulated and should not be relied on.
          </li>
        </ul>
        <p className="notice notice-warn small">{config?.review_notice}</p>
      </section>
    </>
  );
}
