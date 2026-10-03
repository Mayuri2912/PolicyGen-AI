export function formatBytes(bytes) {
  if (!Number.isFinite(bytes)) return '';
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

export function formatDateTime(value) {
  if (!value) return '';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return '';
  return date.toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' });
}

export function formatDate(value) {
  if (!value) return '';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return '';
  return date.toLocaleDateString(undefined, { dateStyle: 'medium' });
}

export function titleCase(value) {
  return String(value || '')
    .replace(/_/g, ' ')
    .replace(/\b\w/g, (letter) => letter.toUpperCase());
}

export function extensionOf(name) {
  const index = name.lastIndexOf('.');
  return index === -1 ? '' : name.slice(index + 1).toLowerCase();
}

// Client-side pre-check only; the server repeats and extends every check.
export function precheckFile(file, { extensions, maxBytes }) {
  const extension = extensionOf(file.name);
  if (!extension || !extensions.includes(extension)) {
    return `Unsupported file type${extension ? ` (.${extension})` : ''}.`;
  }
  if (file.size === 0) return 'The file is empty.';
  if (file.size > maxBytes) return `Larger than the ${Math.round(maxBytes / (1024 * 1024))} MB limit.`;
  return null;
}

// Display name of an LLM provider id ("openrouter" -> "OpenRouter").  The names
// come from the backend (/api/config); an unknown id is shown as it is.
export function providerLabel(config, providerId) {
  return config?.llm_providers?.[providerId] || providerId || 'AI provider';
}

export const TERMINAL_JOB = ['completed', 'failed', 'cancelled'];
export const FILE_BUSY = ['uploaded', 'validating', 'extracting'];

export const JOB_TYPE_LABELS = {
  extraction: 'File extraction',
  analysis: 'AI analysis',
  generation: 'Policy generation',
  revision: 'Section revision',
};

export const REVIEW_LABELS = {
  draft: 'Draft',
  in_review: 'In review',
  changes_requested: 'Changes requested',
  approved: 'Approved',
};

export const ORIGIN_LABELS = {
  ai_generated: 'AI generated',
  ai_revised: 'AI revised',
  simulated: 'Simulated (mock)',
  user_edited: 'Edited by you',
  system: 'Generated from analysis data',
};

export const VERSION_ORIGIN_LABELS = {
  generated: 'Generated',
  regenerated: 'Sections regenerated',
  ai_revision: 'AI-assisted revision',
  user_edit: 'Edited by user',
};

const PLACEHOLDER = /\[To be confirmed:[^\]]*\]/gi;

// Splits text into parts for display.  Each part has a `kind` (text,
// placeholder or match) and `inPlaceholder`, so a search hit inside a
// "[To be confirmed: ...]" placeholder is still highlighted.
export function splitForDisplay(text, search) {
  const value = String(text ?? '');
  const placeholders = [...value.matchAll(PLACEHOLDER)].map((match) => [match.index, match.index + match[0].length]);
  const matches = [];
  const term = (search || '').trim().toLowerCase();
  if (term.length >= 2) {
    const lowered = value.toLowerCase();
    let from = 0;
    for (;;) {
      const index = lowered.indexOf(term, from);
      if (index === -1) break;
      matches.push([index, index + term.length]);
      from = index + term.length;
    }
  }
  const cuts = [...new Set([0, value.length, ...placeholders.flat(), ...matches.flat()])].sort((a, b) => a - b);
  const inside = (ranges, start, end) => ranges.some(([from, to]) => start >= from && end <= to);
  const parts = [];
  for (let i = 0; i < cuts.length - 1; i += 1) {
    const [start, end] = [cuts[i], cuts[i + 1]];
    const inPlaceholder = inside(placeholders, start, end);
    const kind = inside(matches, start, end) ? 'match' : inPlaceholder ? 'placeholder' : 'text';
    parts.push({ text: value.slice(start, end), kind, inPlaceholder });
  }
  return parts.length ? parts : [{ text: '', kind: 'text', inPlaceholder: false }];
}

export function countMatches(text, search) {
  const term = (search || '').trim().toLowerCase();
  if (term.length < 2) return 0;
  return String(text ?? '').toLowerCase().split(term).length - 1;
}

export function elementText(element) {
  switch (element.type) {
    case 'paragraph':
    case 'subheading':
    case 'note':
      return element.text || '';
    case 'bullets':
    case 'numbered':
      return (element.items || []).join('\n');
    case 'statements':
      return (element.statements || []).map((s) => `${s.id} ${s.text} ${(s.refs || []).join(' ')}`).join('\n');
    case 'table':
      return [...(element.columns || []), ...(element.rows || []).flat()].join('\n');
    default:
      return '';
  }
}
