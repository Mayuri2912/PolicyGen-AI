const LEVELS = [
  { id: 'critical', label: 'Critical' },
  { id: 'high', label: 'High' },
  { id: 'medium', label: 'Medium' },
  { id: 'low', label: 'Low' },
  { id: 'unrated', label: 'Unrated' },
];

function levelOf(score, bands) {
  // bands: [{level, range: "1-4"}, ...] from the server's scoring method
  for (const band of bands || []) {
    const [min, max] = band.range.split('-').map(Number);
    if (score >= min && score <= max) return band.level;
  }
  return 'unrated';
}

// 5x5 likelihood/impact heat map.  `matrix` rows are impact 5..1, columns likelihood 1..5.
export function RiskMatrix({ matrix, scoring }) {
  if (!matrix) return null;
  return (
    <figure className="risk-matrix">
      <table>
        <caption className="sr-only">Number of risks by likelihood (columns) and impact (rows)</caption>
        <tbody>
          {matrix.map((row, rowIndex) => {
            const impact = 5 - rowIndex;
            return (
              <tr key={impact}>
                <th scope="row">Impact {impact}</th>
                {row.map((count, columnIndex) => {
                  const likelihood = columnIndex + 1;
                  const level = levelOf(likelihood * impact, scoring?.levels);
                  return (
                    <td key={likelihood} className={`cell-${level}`} aria-label={`Impact ${impact}, likelihood ${likelihood}: ${count} risks, ${level}`}>
                      {count || ''}
                    </td>
                  );
                })}
              </tr>
            );
          })}
          <tr>
            <td />
            {[1, 2, 3, 4, 5].map((n) => (
              <th key={n} scope="col">
                L{n}
              </th>
            ))}
          </tr>
        </tbody>
      </table>
      <figcaption className="muted small">Likelihood (L1–L5) across, impact up. Score = likelihood × impact.</figcaption>
    </figure>
  );
}

export function BarList({ title, data, toneFor, labelFor }) {
  const entries = Object.entries(data || {}).filter(([, count]) => count > 0);
  const max = Math.max(1, ...entries.map(([, count]) => count));
  return (
    <div className="bar-list">
      <h3>{title}</h3>
      {entries.length === 0 && <p className="muted small">No data.</p>}
      <ul>
        {entries.map(([key, count]) => (
          <li key={key}>
            <span className="bar-label">{labelFor ? labelFor(key) : key}</span>
            <span className="bar-track" aria-hidden="true">
              <span className={`bar-fill bar-${toneFor ? toneFor(key) : 'info'}`} style={{ width: `${(count / max) * 100}%` }} />
            </span>
            <span className="bar-count">{count}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}

export function RiskLevelSummary({ summary }) {
  const byLevel = summary?.by_level || {};
  return (
    <div className="level-tiles">
      {LEVELS.map((level) => (
        <div key={level.id} className={`level-tile level-${level.id}`}>
          <span className="level-count">{byLevel[level.id] || 0}</span>
          <span className="level-name">{level.label}</span>
        </div>
      ))}
    </div>
  );
}
