export default function ProcessingSummary({ result, onDismiss }) {
  if (!result) return null;
  const columns = Object.entries(result.columns_cleaned || {});
  const profiled = Object.entries(result.columns_profiled || {});

  return (
    <div className="processing-summary">
      <div className="processing-summary-header">
        <h3>Processing complete — {result.upload.filename}</h3>
        <button type="button" onClick={onDismiss} aria-label="Dismiss">
          ×
        </button>
      </div>
      {result.warnings?.length > 0 && (
        <div className="banner" data-testid="upload-warnings">
          {result.warnings.join(" ")}
        </div>
      )}
      <ul className="agent-log">
        {columns.map(([column, summary]) => (
          <li key={column}>
            <strong>{summary.agent_type}</strong>: {column} — {summary.rows_processed} rows processed,{" "}
            {summary.rows_flagged} flagged
            {summary.rows_influenced_by_feedback > 0 && <>, {summary.rows_influenced_by_feedback} reused from feedback</>}
          </li>
        ))}
        {result.columns_unclassified?.length > 0 && (
          <li className="agent-log-unclassified">
            Left untouched (no matching agent): {result.columns_unclassified.join(", ")}
          </li>
        )}
      </ul>

      {profiled.length > 0 && (
        <div className="profiled-columns" data-testid="profiled-columns">
          <h4>Profiled but not transformed</h4>
          <p className="profiled-columns-hint">
            No agent (and no AI classification) could confidently clean these columns — here's what we know about them instead.
          </p>
          <table className="profiled-columns-table">
            <thead>
              <tr>
                <th>Column</th>
                <th>Detected type</th>
                <th>Null %</th>
                <th>Unique</th>
                <th>Dtype</th>
                <th>Min</th>
                <th>Max</th>
              </tr>
            </thead>
            <tbody>
              {profiled.map(([column, profile]) => (
                <tr key={column}>
                  <td>{column}</td>
                  <td>{profile.detected_type ?? <em>none</em>}</td>
                  <td>{profile.null_pct}%</td>
                  <td>{profile.unique_count}</td>
                  <td>{profile.inferred_dtype}</td>
                  <td>{profile.min_value ?? <em>—</em>}</td>
                  <td>{profile.max_value ?? <em>—</em>}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
