import { useState } from 'react';
import { Sparkles } from 'lucide-react';
import { titleCase } from '../utils/format.js';
import { Badge } from './ui.jsx';

export const DEFAULT_CONFIG = {
  policy_type: '',
  organization_name: '',
  department: '',
  policy_owner: '',
  approver: '',
  document_version: '1.0',
  effective_date: '',
  review_frequency: 'Annually',
  classification: 'Internal',
  custom_title: '',
  delivery_scope: 'unknown',
  customer_obligations: '', assigned_roles: '', approved_timings: '', approved_tools: '', applicable_obligations: '',
  writing_style: 'formal',
  detail_level: 'standard',
  include_risk_register: false,
  include_control_matrix: false,
  include_gap_analysis: false,
  include_source_references: false,
  output_formats: ['docx', 'pdf'],
};

const REVIEW_OPTIONS = ['Quarterly', 'Every six months', 'Annually', 'Every two years'];
const CLASSIFICATIONS = ['Public', 'Internal', 'Confidential', 'Restricted'];

export function PolicyConfigForm({ config, policyTypes, recommendations, detectedOrganization, initial, onSubmit, submitting }) {
  const [values, setValues] = useState({ ...DEFAULT_CONFIG, ...initial });
  const [errors, setErrors] = useState({});
  const [typeSearch, setTypeSearch] = useState('');
  const [category, setCategory] = useState('all');
  const group = (type) => /continuity|disaster|bcdr/i.test(type.name) ? 'Continuity' : /vendor|supplier/i.test(type.name) ? 'Supplier management' : /ISMS|security|configuration/i.test(type.management_system + ' ' + type.name) ? 'ISMS' : 'Other governance';
  const filteredTypes = policyTypes.filter((type) => type.id === values.policy_type || ((category === 'all' || group(type) === category) && `${type.code} ${type.name} ${type.description}`.toLowerCase().includes(typeSearch.toLowerCase())));
  const set = (name, value) => setValues((current) => ({ ...current, [name]: value }));
  const recommended = recommendations?.[0];
  const selectedType = policyTypes.find((type) => type.id === values.policy_type);

  const submit = (event) => {
    event.preventDefault();
    const next = {};
    if (!values.policy_type) next.policy_type = 'Choose a policy type.';
    if (!values.document_version.trim()) next.document_version = 'Enter a version, for example 1.0.';
    if (!values.output_formats.length) next.output_formats = 'Choose at least one output format.';
    setErrors(next);
    if (Object.keys(next).length) return;
    onSubmit(values);
  };

  const toggleFormat = (format) =>
    set('output_formats', values.output_formats.includes(format) ? values.output_formats.filter((f) => f !== format) : [...values.output_formats, format]);

  return (
    <form onSubmit={submit} noValidate className="config-form">
      <section className="card">
        <h2>Policy type</h2>
        <div className="form-grid"><label className="field"><span>Search policy types</span><input type="search" value={typeSearch} onChange={(e) => setTypeSearch(e.target.value)} placeholder="Search name, code or topic" /></label>
        <label className="field"><span>Policy category</span><select value={category} onChange={(e) => setCategory(e.target.value)}><option value="all">All categories</option>{['ISMS', 'Continuity', 'Supplier management', 'Other governance'].map((g) => <option key={g}>{g}</option>)}</select></label></div>
        {recommended && (
          <div className="recommendation" aria-label="Recommended policy type">
            <div className="recommendation-head">
              <Sparkles size={18} aria-hidden="true" />
              <div>
                <p className="small muted">Recommended for these documents</p>
                <p className="recommendation-name">{recommended.name}</p>
              </div>
              <Badge tone={recommended.fit === 'strong' ? 'ok' : recommended.fit === 'moderate' ? 'note' : 'muted'} label={`${titleCase(recommended.fit)} fit`} />
              {values.policy_type !== recommended.policy_type && (
                <button type="button" className="btn btn-small" onClick={() => set('policy_type', recommended.policy_type)}>
                  Use this type
                </button>
              )}
            </div>
            <ul className="reason-list">
              {recommended.reasons.map((reason) => (
                <li key={reason}>{reason}</li>
              ))}
            </ul>
            {recommendations.length > 1 && (
              <details>
                <summary>Other policy types these documents could support</summary>
                <ul className="alt-list">
                  {recommendations.slice(1).map((item) => (
                    <li key={item.policy_type}>
                      <button type="button" className="link-button" onClick={() => set('policy_type', item.policy_type)}>
                        {item.name}
                      </button>{' '}
                      <span className="muted small">({item.fit} fit)</span>
                      <span className="small"> — {item.reasons[0]}</span>
                    </li>
                  ))}
                </ul>
              </details>
            )}
          </div>
        )}
        <label className="field">
          <span>Policy type to generate</span>
          <select
            value={values.policy_type}
            onChange={(e) => set('policy_type', e.target.value)}
            aria-invalid={Boolean(errors.policy_type)}
            aria-describedby={errors.policy_type ? 'err-policy-type' : undefined}
          >
            <option value="">Select a policy type…</option>
            {filteredTypes.map((type) => (
              <option key={type.id} value={type.id}>
                {type.code ? `${type.code} - ` : ''}{type.name}
                {recommended?.policy_type === type.id ? ' (recommended)' : ''}
              </option>
            ))}
          </select>
        </label>
        {errors.policy_type && (
          <p id="err-policy-type" className="form-error">
            {errors.policy_type}
          </p>
        )}
        {selectedType && <p className="muted small">{selectedType.description}</p>}
      </section>

      <section className="card">
        <h2>Organization and document details</h2>
        <p className="muted small">
          Leave a field empty if you do not know it yet. The policy will show a clearly marked placeholder instead of an invented value.
        </p>
        <div className="form-grid">
          <div>
            <label className="field">
              <span>Organization name</span>
              <input type="text" value={values.organization_name} onChange={(e) => set('organization_name', e.target.value)} maxLength={200} autoComplete="organization" />
            </label>
            {detectedOrganization && values.organization_name !== detectedOrganization && (
              <p className="hint">
                Found in the documents:{' '}
                <button type="button" className="link-button" onClick={() => set('organization_name', detectedOrganization)}>
                  {detectedOrganization}
                </button>
              </p>
            )}
          </div>
          <label className="field">
            <span>Department</span>
            <input type="text" value={values.department} onChange={(e) => set('department', e.target.value)} maxLength={200} />
          </label>
          <label className="field">
            <span>Policy owner</span>
            <input type="text" value={values.policy_owner} onChange={(e) => set('policy_owner', e.target.value)} maxLength={200} placeholder="Role or name" />
          </label>
          <label className="field">
            <span>Approver</span>
            <input type="text" value={values.approver} onChange={(e) => set('approver', e.target.value)} maxLength={200} placeholder="Role or name" />
          </label>
          <div>
            <label className="field">
              <span>Document version</span>
              <input
                type="text"
                value={values.document_version}
                onChange={(e) => set('document_version', e.target.value)}
                maxLength={20}
                aria-invalid={Boolean(errors.document_version)}
                aria-describedby={errors.document_version ? 'err-version' : undefined}
              />
            </label>
            {errors.document_version && (
              <p id="err-version" className="form-error">
                {errors.document_version}
              </p>
            )}
          </div>
          <label className="field">
            <span>Effective date</span>
            <input type="date" value={values.effective_date} onChange={(e) => set('effective_date', e.target.value)} />
          </label>
          <label className="field">
            <span>Review frequency</span>
            <select value={values.review_frequency} onChange={(e) => set('review_frequency', e.target.value)}>
              {REVIEW_OPTIONS.map((option) => (
                <option key={option}>{option}</option>
              ))}
            </select>
          </label>
          <label className="field">
            <span>Classification</span>
            <select value={values.classification} onChange={(e) => set('classification', e.target.value)}>
              {CLASSIFICATIONS.map((option) => (
                <option key={option}>{option}</option>
              ))}
            </select>
          </label>
          <label className="field form-span">
            <span>Document title (optional)</span>
            <input type="text" value={values.custom_title} onChange={(e) => set('custom_title', e.target.value)} maxLength={200} placeholder={selectedType?.name || 'Defaults to the policy type'} />
          </label>
        </div>
      </section>

      <section className="card">
        <h2>Writing style and depth</h2>
        <div className="form-grid">
          <fieldset className="choice-group">
            <legend>Writing style</legend>
            {(config?.writing_styles || []).map((style) => (
              <label key={style.id} className="choice">
                <input type="radio" name="writing_style" value={style.id} checked={values.writing_style === style.id} onChange={() => set('writing_style', style.id)} />
                <span>
                  <strong>{titleCase(style.id)}</strong>
                  <span className="muted small">{style.description}</span>
                </span>
              </label>
            ))}
          </fieldset>
          <fieldset className="choice-group">
            <legend>Level of detail</legend>
            {(config?.detail_levels || []).map((level) => (
              <label key={level.id} className="choice">
                <input type="radio" name="detail_level" value={level.id} checked={values.detail_level === level.id} onChange={() => set('detail_level', level.id)} />
                <span>
                  <strong>{titleCase(level.id)}</strong>
                  <span className="muted small">{level.description}</span>
                </span>
              </label>
            ))}
          </fieldset>
        </div>
      </section>

      <section className="card">
        <h2>Organisation and project context</h2>
        <p>Provide confirmed facts or references to approved records. Leave unknown details blank; the draft must not invent tools, deadlines or legal obligations.</p>
        <label className="field"><span>Delivery scope</span><select value={values.delivery_scope} onChange={(e) => set('delivery_scope', e.target.value)}><option value="unknown">To be confirmed</option><option value="internal">Internally managed services</option><option value="customer">Customer-managed projects</option><option value="both">Both internal and customer-managed</option></select></label>
        <div className="form-grid">{[['customer_obligations', 'Customer contracts, shared responsibilities and recovery requirements'], ['assigned_roles', 'Actual roles, deputies and approval authority'], ['approved_timings', 'Approved response times, RTO/RPO, review frequencies and retention'], ['approved_tools', 'Internal tools and approved customer repositories/processes'], ['applicable_obligations', 'Confirmed regulations, standards and obligations register']].map(([key, label]) => <label className="field" key={key}><span>{label}</span><textarea rows={3} maxLength={key === 'customer_obligations' ? 4000 : 2000} value={values[key]} onChange={(e) => set(key, e.target.value)} /></label>)}</div>
      </section>
      <section className="card">
        <h2>Document output</h2>
        <div className="form-grid">
          <fieldset className="choice-group" aria-describedby={errors.output_formats ? 'err-formats' : undefined}>
            <legend>Output format</legend>
            {[
              ['docx', 'Word document (.docx)', 'Editable in Microsoft Word and compatible editors.'],
              ['pdf', 'PDF document (.pdf)', 'Fixed layout for circulation and review.'],
            ].map(([format, label, help]) => (
              <label key={format} className="choice">
                <input type="checkbox" checked={values.output_formats.includes(format)} onChange={() => toggleFormat(format)} />
                <span>
                  <strong>{label}</strong>
                  <span className="muted small">{help}</span>
                </span>
              </label>
            ))}
            {errors.output_formats && (
              <p id="err-formats" className="form-error">
                {errors.output_formats}
              </p>
            )}
          </fieldset>
        </div>
      </section>

      <div className="form-actions">
        <button type="submit" className="btn btn-primary btn-large" disabled={submitting}>
          {submitting ? 'Starting…' : 'Generate policy'}
        </button>
      </div>
    </form>
  );
}
