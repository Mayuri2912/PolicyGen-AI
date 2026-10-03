import { useState } from 'react';
import { Link, useNavigate, useParams } from 'react-router-dom';
import { ArrowLeft } from 'lucide-react';
import { PolicyConfigForm } from '../components/PolicyConfigForm.jsx';
import { ErrorState, LoadingBlock, PageHeader } from '../components/ui.jsx';
import { useSystem } from '../context/SystemContext.jsx';
import { useToast } from '../context/ToastContext.jsx';
import { useAsync } from '../hooks/useAsync.js';
import { api } from '../services/api.js';

export default function ConfigurePage() {
  const { analysisId } = useParams();
  const { config } = useSystem();
  const toast = useToast();
  const navigate = useNavigate();
  const [submitting, setSubmitting] = useState(false);
  const { loading, error, data, reload } = useAsync(() => api.recommend(analysisId), [analysisId]);

  const submit = async (values) => {
    setSubmitting(true);
    try {
      const response = await api.generate(analysisId, values);
      navigate(`/reports/${response.report_id}`);
    } catch (err) {
      const fields = Array.isArray(err.details) ? ` (${err.details.map((d) => `${d.field}: ${d.message}`).join('; ')})` : '';
      toast.error(err.message + fields);
      setSubmitting(false);
    }
  };

  const back = (
    <Link to={`/analyses/${analysisId}`} className="back-link">
      <ArrowLeft size={16} aria-hidden="true" /> Back to analysis
    </Link>
  );

  if (loading && !data) return <LoadingBlock label="Preparing policy recommendations…" />;
  if (error) {
    return (
      <>
        {back}
        <ErrorState title="Policy configuration is not available" message={error.message} onRetry={reload} />
      </>
    );
  }

  return (
    <>
      <PageHeader title="Configure the policy" subtitle="Step 3 of 4: choose the policy type and how the document should be written." back={back} />
      <PolicyConfigForm
        config={config}
        policyTypes={config?.policy_types || []}
        recommendations={data.recommendations}
        detectedOrganization={data.detected_organization_name}
        initial={{ policy_type: data.recommended?.policy_type || '', organization_name: data.detected_organization_name || '' }}
        onSubmit={submit}
        submitting={submitting}
      />
    </>
  );
}
